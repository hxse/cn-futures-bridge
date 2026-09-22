"""管理专用虚拟桌面与终端；不提供业务报撤单入口。"""

import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
from collections.abc import Callable
from typing import TextIO

from pydantic import BaseModel, Field
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from .config import Settings
from .errors import BridgeError, ErrorDetail, ServiceStatus
from .logging_store import LogStore

LOG = logging.getLogger(__name__)


class TerminalManifest(BaseModel):
    version: str | None = None
    executable: str = "q7_release.exe"
    files: dict[str, str] = Field(default_factory=dict)
    environment: str
    broker_id: str
    broker_name: str
    sites: list[str]


class Runtime:
    def __init__(self, settings: Settings, vnc: bool = False):
        self.settings = settings
        self.vnc = vnc
        self.processes: dict[str, subprocess.Popen[bytes]] = {}
        self.state = "starting"
        self.error: ErrorDetail | None = None
        self.window_visible = False
        self.manifest = TerminalManifest(environment=settings.bridge.environment, broker_id=settings.broker_id,
                                         broker_name=settings.profile.broker_name, sites=list(settings.profile.sites))
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.screenshot_lock = threading.Lock()
        self.session_lock: TextIO | None = None
        self.worker: threading.Thread | None = None
        self.logs: LogStore | None = None
        self.env = dict(os.environ, DISPLAY=":99", WINEARCH="win32",
                        WINEPREFIX=str(settings.wine_prefix),
                        WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=")

    def acquire(self) -> None:
        self.settings.bridge.data_dir.mkdir(parents=True, exist_ok=True)
        self.session_lock = (self.settings.bridge.data_dir / ".session.lock").open("a")
        try:
            fcntl.flock(self.session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.session_lock.close()
            self.session_lock = None
            raise BridgeError("SESSION_IN_USE", "持久化目录已被另一实例使用") from exc
    def start(self) -> None:
        if self.session_lock is None:
            self.acquire()
        self.worker = threading.Thread(target=self._run, name="desktop-runtime", daemon=True)
        self.worker.start()

    def _spawn(self, name: str, command: list[str], cwd: Path | None = None) -> subprocess.Popen[bytes]:
        if self.logs is None:
            raise BridgeError("STORAGE_UNAVAILABLE", "日志收集器未启动")
        process = subprocess.Popen(command, cwd=cwd, env=self.env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        self.processes[name] = process
        assert process.stdout is not None
        self.logs.collect(name, process.stdout)
        return process

    def _command(self, command: list[str], timeout: int = 10) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(command, env=self.env, capture_output=True, timeout=timeout,
                              check=False)

    def _wait(self, predicate: Callable[[], bool], timeout: int, code: str, message: str) -> None:
        deadline = time.monotonic() + timeout
        while not self.stop_event.is_set():
            if predicate():
                return
            if time.monotonic() >= deadline:
                raise BridgeError(code, message)
            self.stop_event.wait(0.25)
        raise BridgeError("STOPPING", "启动过程已停止")

    def _prepare_files(self, seed: Path = Path("/opt/terminal")) -> Path:
        seed = seed / self.settings.profile_name
        self.manifest = TerminalManifest.model_validate_json((seed / "bridge-manifest.json").read_bytes())
        if (self.manifest.environment != self.settings.bridge.environment
                or self.manifest.broker_id != self.settings.broker_id
                or self.manifest.broker_name != self.settings.profile.broker_name
                or self.settings.site not in self.manifest.sites):
            raise BridgeError("TERMINAL_CHANGED", "终端种子的环境、券商或站点不匹配")
        self.settings.session_dir.mkdir(parents=True, exist_ok=True)
        target = self.settings.terminal_dir
        if not target.exists():
            with tempfile.TemporaryDirectory(dir=self.settings.session_dir) as temporary:
                staged = Path(temporary) / "terminal"
                shutil.copytree(seed, staged)
                staged.rename(target)
        # 检查全部随包文件，持久化目录不能静默继承不同版本的程序或站点。
        for relative, expected in self.manifest.files.items():
            path = target / relative
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise BridgeError("TERMINAL_CHANGED", f"终端文件与镜像不一致：{relative}")
        return target

    def _initialize_wine(self) -> None:
        marker = self.settings.wine_prefix / ".bridge-initialized"
        # 前缀可以持久化，Wine 的系统会话每次容器启动仍须先完成引导。
        process = self._spawn("wineboot", ["wineboot", "--init"])
        self._wait(lambda: process.poll() is not None, self.settings.bridge.startup_timeout_seconds,
                   "WINE_INIT_TIMEOUT", "Wine 初始化超时，查看 logs/wineboot.log")
        if process.returncode != 0:
            raise BridgeError("WINE_INIT_FAILED", "Wine 初始化失败，查看 logs/wineboot.log")
        if marker.exists():
            return
        result = self._command(["wine", "regedit", "/S", "/opt/bridge/container/fonts.reg"], 30)
        if result.returncode != 0:
            raise BridgeError("FONT_SETUP_FAILED", "Wine 字体映射初始化失败")
        marker.write_text("win32\n")

    def _has_window(self, *, login_only: bool = False) -> bool:
        process = self.processes.get("terminal")
        if process is None or process.poll() is not None:
            return False
        result = self._command(["xdotool", "search", "--all", "--onlyvisible", "--pid",
                                str(process.pid), "--name",
                                "^用户登录$" if login_only else "^用户登录$|快期"], 5)
        return result.returncode == 0 and bool(result.stdout.strip())

    def _run(self) -> None:
        try:
            target = self._prepare_files()
            size = f"{self.settings.desktop.width}x{self.settings.desktop.height}x24"
            self._spawn("xvfb", ["Xvfb", ":99", "-screen", "0", size, "-dpi",
                                  str(self.settings.desktop.dpi), "-s", "0", "-noreset",
                                  "-nolisten", "tcp", "-ac", "-extension", "GLX"])
            self._wait(lambda: self._command(["xdpyinfo"]).returncode == 0,
                       15, "DISPLAY_TIMEOUT", "虚拟屏幕启动超时")
            self._spawn("openbox", ["openbox", "--config-file", "/opt/bridge/container/openbox.xml"])
            # 持久化 Wine 再次启动很快，必须等窗口管理器接管屏幕后再创建快期窗口。
            self._wait(lambda: b"window id # 0x" in self._command(
                ["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"]).stdout,
                15, "WINDOW_MANAGER_TIMEOUT", "窗口管理器未就绪")
            if self.vnc:
                self._start_vnc()
            self._initialize_wine()
            self._spawn("terminal", ["wine", str(target / self.manifest.executable)], target)
            self._wait(lambda: self._has_window(login_only=True), self.settings.bridge.startup_timeout_seconds,
                       "WINDOW_TIMEOUT", "未观察到快期窗口，查看截图与 logs/terminal.log")
            with self.lock:
                self.state = "window_visible"
                self.window_visible = True
            LOG.info("快期窗口已出现；账号登录和交易能力尚未验证")
            while not self.stop_event.wait(1):
                for name in ("xvfb", "openbox", "terminal", "vnc", "novnc"):
                    process = self.processes.get(name)
                    if process is not None and process.poll() is not None:
                        raise BridgeError("PROCESS_EXITED", f"{name} 进程退出，查看对应日志")
                visible = self._has_window()
                with self.lock:
                    self.window_visible = visible
                    self.state = "window_visible" if visible else "window_missing"
        except BridgeError as exc:
            if not self.stop_event.is_set():
                self._fail(exc.code, str(exc))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self._fail("STARTUP_FAILED", f"启动失败：{type(exc).__name__}；查看容器日志")
            LOG.exception("桌面启动异常")
        finally:
            # 启动失败时保留现有桌面和 VNC，便于查看错误窗口；不自动重启终端。
            if self.stop_event.is_set():
                self._stop_processes()

    def _start_vnc(self) -> None:
        self._spawn("vnc", ["x11vnc", "-display", ":99", "-forever", "-shared",
                            "-nopw", "-rfbport", str(self.settings.vnc.port), "-noxdamage"])
        self._spawn("novnc", ["websockify", "--web=/usr/share/novnc/",
                              str(self.settings.vnc.web_port), f"127.0.0.1:{self.settings.vnc.port}"])

    def _fail(self, code: str, message: str) -> None:
        with self.lock:
            self.state = "failed"
            self.error = ErrorDetail(code=code, message=message)
        LOG.error("%s: %s", code, message)

    def status(self) -> ServiceStatus:
        with self.lock:
            return ServiceStatus(environment=self.settings.bridge.environment,
                                 request_mode=self.settings.request_mode, broker_id=self.settings.broker_id,
                                 site=self.settings.site,
                                 state=self.state, terminal_version=self.manifest.version,
                                 terminal_window_visible=self.window_visible,
                                 account_configured=self.settings.account.configured,
                                 vnc_enabled=self.vnc, error=self.error)

    def screenshot(self) -> bytes:
        with self.screenshot_lock, tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "desktop.png"
            try:
                result = self._command(["cfb-capture", str(path)], 5)
            except (OSError, subprocess.SubprocessError) as exc:
                raise BridgeError("SCREENSHOT_UNAVAILABLE", "截图进程失败或超时") from exc
            if result.returncode != 0 or not path.exists():
                raise BridgeError("SCREENSHOT_UNAVAILABLE", "虚拟桌面当前不能截图")
            return path.read_bytes()

    def _stop_processes(self) -> None:
        for process in reversed(list(self.processes.values())):
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
                except ProcessLookupError:
                    pass
        if self.settings.wine_prefix.exists():
            self._command(["wineserver", "-k"], 5)

    def stop(self) -> None:
        self.stop_event.set()
        if self.worker is not None:
            self.worker.join(timeout=self.settings.bridge.startup_timeout_seconds + 10)
        self._stop_processes()
        if self.session_lock is not None:
            self.session_lock.close()
            self.session_lock = None
