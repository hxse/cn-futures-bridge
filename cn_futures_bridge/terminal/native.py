"""常驻 Wine 控制器的有界通信，超时后保持失效状态，禁止继续派发。"""

import hashlib
import logging
import os
from pathlib import Path
import select
import subprocess
import time

from pydantic import BaseModel, Field, JsonValue

from ..config import Settings
from ..errors import BridgeError
from ..logging_store import LogStore

LOG = logging.getLogger(__name__)

HASHES = {
    "q7_release.exe": "77508f5729b74db4bdaa586bc6c354e71fad29ad8d1250cced3c5c7293a7eca9",
    "shinny_future_core.dll": "da4b54b234bf5fd3b86367028f9aad8f0c431cddadfa6a8244652f5aa16ba56f",
}


def decode(value: str) -> str:
    try:
        return bytes.fromhex(value).decode("gb18030")
    except (ValueError, UnicodeError) as exc:
        raise BridgeError("TERMINAL_DATA_INVALID", "原生文字编码无效", 502) from exc


class NativeReply(BaseModel):
    error: int
    done: bool
    data: dict[str, JsonValue] = Field(default_factory=dict)
    export_result: int = 0
    import_completed: int = 0
    restored: int = 0
    dialogs_created: int = 0
    native_ms: float = 0
    columns: int = 0
    selected: int = -1
    selected_count: int = 0
    row_count: int = 0
    message_hex: str = ""
    startup_privacy_count: int = 0
    startup_terms_count: int = 0
    startup_wizard_count: int = 0
    settlement_count: int = 0
    empty_settlement_count: int = 0
    information_close_count: int = 0
    trade_notice_check_count: int = 0
    trade_notice_checked_count: int = 0
    trade_notice_confirm_count: int = 0
    trade_notice_closed_count: int = 0
    order_notice_count: int = 0
    order_notice_kind: int = 0
    order_notice_hex: str = ""


class Window(BaseModel):
    hwnd: int
    parent: int
    root: int
    checked: int
    id: int
    class_name: str
    visible: bool
    enabled: bool
    password: bool
    rect: tuple[int, int, int, int]
    text_hex: str
    items_hex: list[str]

    @property
    def text(self) -> str:
        return decode(self.text_hex)

    @property
    def items(self) -> list[str]:
        return [decode(item) for item in self.items_hex]


class Windows(BaseModel):
    windows: list[Window]
    focus: int
    flags: int
    capture: int = 0
    document_pending: bool = False


class GridBinding(BaseModel):
    valid: bool
    window: Window | None = None
    object_address: int = Field(default=0, alias="object")
    procedure: int = 0
    vtable: int = 0
    columns: int = 0
    filters: list[Window] = Field(default_factory=list)

    @property
    def identity(self) -> tuple[int, ...]:
        window = self.window
        return (() if window is None else (window.hwnd, window.parent, window.root, window.id,
                self.object_address, self.procedure, self.vtable, self.columns))

    def matches(self, identifier: int, columns: int) -> bool:
        return bool(self.valid and self.window and self.window.id == identifier
                    and self.window.class_name == "ListCtrl" and self.columns == columns
                    and self.object_address and self.procedure and self.vtable)

    def filtered(self, name: str | None) -> bool:
        if name is None:
            return True
        windows = [w for w in self.filters if w.class_name == "Button" and w.text.startswith(name)]
        return len(windows) == 1 and windows[0].checked == 1


class GuiState(BaseModel):
    main: int
    main_count: int
    enabled: bool
    focus: int
    flags: int
    capture: int
    menu_owned: bool
    modifiers: int
    dialogs: int
    unknown_dialogs: int
    funds: int
    funds_count: int
    document_pending: bool


class Session(BaseModel):
    connected: bool
    trade_connected: bool
    market_connected: bool
    identity_match: bool
    trading_day: str
    front_id: int
    session_id: int
    status_bound: bool
    login_generation: int

    @property
    def identity(self) -> tuple[int, int, str]:
        return self.front_id, self.session_id, self.trading_day


class InstrumentInfo(BaseModel):
    found: bool
    data_ready: bool
    id_hex: str = ""
    name_hex: str = ""
    exchange: int = 0
    status: int = 0
    tick: float = 0
    lower: float = 0
    upper: float = 0
    limit_min_volume: int = 0
    limit_max_volume: int = 0

    @property
    def name(self) -> str:
        return decode(self.id_hex)


class NativeClient:
    def __init__(self, settings: Settings, logs: LogStore):
        self.settings = settings
        self.logs = logs
        self.process: subprocess.Popen[bytes] | None = None
        self.poisoned = False
        self.buffer = b""
        self.startup_counts = (0, 0, 0, 0, 0)
        self.notice_counts = (0, 0, 0, 0)
        self.order_notice_count = 0
        self.empty_settlement_count = 0
        self.timings: dict[str, tuple[int, float]] = {}
        self.startup_deadline: float | None = None
        self.env = dict(os.environ, DISPLAY=":99", WINEARCH="win32",
                        WINEPREFIX=str(settings.wine_prefix), WINEDEBUG="-all",
                        WINEDLLOVERRIDES="mscoree=")
        self.timeout = settings.execution.step_timeout_ms / 1000 + .5

    def start(self) -> None:
        for name, expected in HASHES.items():
            path = self.settings.terminal_dir / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise BridgeError("SERVICE_NOT_READY", "固定终端或核心 DLL 校验失败")
        artifact_root = self.windows_path(self.settings.bridge.data_dir / "artifacts") + "\\"
        titles = self.settings.profile.titles(self.settings.site)
        self.startup_deadline = time.monotonic() + self.settings.bridge.startup_timeout_seconds
        self.process = subprocess.Popen(
            ["wine", "/opt/bridge/native/cfb-controller.exe", "Z:\\opt\\bridge\\native\\cfb-hook.dll",
             artifact_root, str(self.settings.execution.step_timeout_ms), titles[0], titles[-1],
             str(self.settings.bridge.startup_timeout_seconds * 1000)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=self.env, start_new_session=True)
        assert self.process.stderr is not None
        self.logs.collect("native", self.process.stderr)
        ready = self._read(5)
        import json
        startup = json.loads(ready)
        if not startup.get("ready"):
            code = startup.get("error", "window_missing")
            raise BridgeError("SERVICE_NOT_READY", f"原生控制器启动失败：{code}")

    @staticmethod
    def windows_path(path: Path) -> str:
        if not path.is_absolute() or ".." in path.parts:
            raise BridgeError("STORAGE_UNAVAILABLE", "原生工件路径无效")
        value = "Z:" + str(path).replace("/", "\\")
        if len(value.encode("gb18030")) >= 239:
            raise BridgeError("STORAGE_UNAVAILABLE", "原生工件路径过长")
        return value

    def _read(self, timeout: float) -> bytes:
        process = self.process
        if process is None or process.stdout is None:
            raise BridgeError("SERVICE_NOT_READY", "原生控制器尚未启动")
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([process.stdout], [], [], left)[0]:
                self.poisoned = True
                raise BridgeError("GUI_UNRESPONSIVE", "原生调用超时，执行权保持隔离")
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk or len(self.buffer) + len(chunk) > 1048576:
                self.poisoned = True
                raise BridgeError("GUI_UNRESPONSIVE", "原生控制器退出或输出超过限制")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return line

    def ask(self, command: str) -> NativeReply:
        started = time.perf_counter()
        try:
            return self._ask(command)
        finally:
            name = command.partition(" ")[0]
            count, seconds = self.timings.get(name, (0, 0.0))
            self.timings[name] = count+1, seconds+time.perf_counter()-started

    def _ask(self, command: str) -> NativeReply:
        if self.poisoned:
            raise BridgeError("GUI_UNRESPONSIVE", "原生会话已隔离，须受控重启后恢复")
        process = self.process
        if process is None or process.stdin is None:
            raise BridgeError("SERVICE_NOT_READY", "原生控制器未启动")
        if "\n" in command or "\r" in command:
            raise BridgeError("INVALID_ARGUMENTS", "原生命令包含非法换行", 422)
        if len(command.encode("gb18030")) > 2048:
            raise BridgeError("INVALID_ARGUMENTS", "原生命令超过有界通信限制", 422)
        try:
            process.stdin.write((command + "\n").encode("gb18030"))
            process.stdin.flush()
            timeout = self.timeout
            if command in ("windows", "startup_done", "managed_windows", "gui_state") and self.startup_deadline is not None:
                timeout = max(timeout, self.startup_deadline - time.monotonic() + 1)
            result = NativeReply.model_validate_json(self._read(timeout))
        except (OSError, ValueError) as exc:
            self.poisoned = True
            raise BridgeError("GUI_UNRESPONSIVE", "原生通信失效，暂停执行") from exc
        counts = (result.startup_privacy_count, result.startup_terms_count, result.startup_wizard_count,
                  result.settlement_count, result.information_close_count)
        for name, current, previous in zip(("确认隐私政策", "确认软件使用协议", "跳过快速配置向导", "确认结算单", "关闭保证金监控中心"),
                                          counts, self.startup_counts):
            if current > previous:
                LOG.info("已投递文档窗口处理请求：%s，累计 %s 次", name, current,
                         extra={"step": "document_confirmation", "event": "submitted"})
        self.startup_counts = counts
        if result.empty_settlement_count > self.empty_settlement_count:
            LOG.info("SimNow 全天站点空结算单已核对查询成功及稳定空正文，已投递确认，累计 %s 次", result.empty_settlement_count,
                     extra={"step": "empty_settlement_confirmation", "event": "submitted"})
        self.empty_settlement_count = result.empty_settlement_count
        if result.order_notice_count > self.order_notice_count:
            title = {7: "下单失败", 8: "下单成功", 9: "撤单成功(即时单)",
                     10: "撤单失败(即时单)", 11: "撤单成功", 12: "撤单失败"}.get(result.order_notice_kind, "交易结果通知")
            LOG.info("终端结果通知已确认（仅清障，不推断交易状态）：%s；%s", title, decode(result.order_notice_hex),
                     extra={"step": "order_notice", "event": "acknowledged"})
        self.order_notice_count = result.order_notice_count
        notice_counts = (result.trade_notice_check_count, result.trade_notice_checked_count,
                         result.trade_notice_confirm_count, result.trade_notice_closed_count)
        events = (("请求勾选不再提示成交通知", "checkbox_submitted"),
                  ("已核对不再提示成交通知为勾选状态", "checkbox_verified"),
                  ("已投递成交通知确定请求", "confirmation_submitted"),
                  ("成交通知窗口已关闭", "end"))
        for (message, event), current, previous in zip(events, notice_counts, self.notice_counts):
            if current > previous:
                LOG.info("%s，累计 %s 次", message, current, extra={"step": "trade_notice", "event": event})
        self.notice_counts = notice_counts
        if result.error:
            LOG.error("原生调用失败：动作=%s，代码=%s", command.split(" ", 1)[0], result.error)
        if not result.done or result.error == 90:
            self.poisoned = True
            raise BridgeError("GUI_UNRESPONSIVE", "GUI 调用尚未确认结束，暂停执行")
        if result.error == 71:
            self.poisoned = True
            raise BridgeError("GUI_RESET_FAILED", "报单引用观察入口未确认恢复，隔离执行器")
        if result.error == 72 or (command.split(" ", 1)[0] in ("gui_state", "recover_gui") and result.error in (91, 92)):
            raise BridgeError("GUI_RESET_FAILED", "原生界面恢复条件或目标身份未确认")
        if result.error in (1, 40, 43, 70):
            raise BridgeError("SERVICE_NOT_READY", f"固定版本或必需原生符号未就绪，代码 {result.error}")
        if result.error:
            raise BridgeError("TERMINAL_DATA_INVALID", f"原生适配拒绝操作，代码 {result.error}", 502)
        return result

    def windows(self) -> Windows:
        return Windows.model_validate(self.ask("windows").data)

    def managed_windows(self) -> Windows:
        return Windows.model_validate(self.ask("managed_windows").data)

    def form_windows(self, panel: int) -> Windows | None:
        result = self.ask(f"form_windows {panel}")
        return Windows.model_validate(result.data) if result.data.get("valid") is True else None

    def grid_binding(self, window: int) -> GridBinding:
        return GridBinding.model_validate(self.ask(f"grid_binding {window}").data)

    def gui_state(self) -> GuiState:
        return GuiState.model_validate(self.ask("gui_state").data)

    def complete_startup(self) -> None:
        while Windows.model_validate(self.ask("startup_done").data).document_pending:
            if self.startup_deadline is None or time.monotonic() >= self.startup_deadline:
                raise BridgeError("QUERY_TIMEOUT", "启动文档确认未在期限内完成", 504)
            time.sleep(self.settings.execution.poll_interval_ms / 1000)
        self.startup_deadline = None

    def session(self) -> Session:
        return Session.model_validate(self.ask("session " + self.settings.account.username.get_secret_value()).data)

    def instrument(self, name: str, *, product: bool = False) -> InstrumentInfo:
        command = "product" if product else "instrument"
        return InstrumentInfo.model_validate(self.ask(f"{command} {name}").data)

    def export(self, window: int, path: Path) -> None:
        result = self.ask(f"export {window} {self.windows_path(path)}")
        if result.export_result != 1 or result.dialogs_created or not path.is_file():
            raise BridgeError("TERMINAL_DATA_INVALID", "无弹窗导出未完成或出现异常窗口", 502)

    def import_csv(self, window: int, path: Path) -> str:
        result = self.ask(f"import {window} {self.windows_path(path)}")
        if not result.import_completed or not result.restored or result.dialogs_created:
            self.poisoned = True
            raise BridgeError("OPERATION_STATUS_UNKNOWN", "导入结果或临时拦截恢复无法确认", 502,
                              submission_status="unknown")
        return decode(result.message_hex)

    def select(self, window: int, index: int) -> None:
        result = self.ask(f"select {window} {index}")
        if result.selected_count != 1 or result.selected != index:
            raise BridgeError("ORDER_IDENTITY_AMBIGUOUS", "表格未唯一选中已核对记录", 409)

    def close(self) -> None:
        if self.process and self.process.poll() is None and not self.poisoned:
            self.ask("quit")
            self.process.wait(timeout=3)
