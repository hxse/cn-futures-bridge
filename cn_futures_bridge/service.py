"""服务生命周期拥有桌面、运维通道及文件治理。"""

import logging
import threading

from pydantic import JsonValue

from .artifacts import ArtifactStore
from .config import Settings
from .control import ControlServer
from .dispatch import Dispatcher
from .errors import BridgeError, ServiceStatus
from .logging_store import LogStore, configure_logging
from .runtime import Runtime

LOG = logging.getLogger(__name__)


class BridgeService:
    def __init__(self, settings: Settings, *, vnc: bool = False):
        self.settings = settings
        self.runtime = Runtime(settings, vnc=vnc)
        self.logs: LogStore | None = None
        self.artifacts: ArtifactStore | None = None
        self.control_server: ControlServer | None = None
        self.stopping = threading.Event()
        self.maintenance: threading.Thread | None = None
        self.dispatcher: Dispatcher | None = None

    def start(self) -> None:
        self.runtime.acquire()
        try:
            self.logs = configure_logging(self.settings)
            self.runtime.logs = self.logs
            self.artifacts = ArtifactStore(self.settings)
            self.artifacts.clean()
            self.dispatcher = Dispatcher(self.settings, self.runtime)
            self.runtime.start()
            self.dispatcher.start()
            self.control_server = ControlServer(self.settings.bridge.data_dir / "control.sock", self.control)
            self.control_server.start()
            self.maintenance = threading.Thread(target=self._maintain, name="storage-monitor", daemon=True)
            self.maintenance.start()
        except BaseException:
            self.stop()
            raise

    def _maintain(self) -> None:
        while not self.stopping.wait(self.settings.artifacts.cleanup_interval_seconds):
            try:
                if self.artifacts:
                    self.artifacts.clean()
                if self.logs:
                    self.logs.maintain()
                if self.dispatcher:
                    self.dispatcher.journal.maintain()
                if self.logs and self.logs.failed:
                    raise BridgeError("STORAGE_UNAVAILABLE", "日志写入失败，停止终端以避免无记录操作")
            except (OSError, ValueError, BridgeError):
                self.runtime._fail("STORAGE_UNAVAILABLE", "文件治理失败，服务停止接受操作")
                if self.dispatcher:
                    self.dispatcher.stop()
                self.runtime.stop_event.set()
                LOG.error("空间治理失败，已要求终端停止")
                return

    def status(self) -> ServiceStatus:
        base = self.runtime.status()
        return self.dispatcher.status(base) if self.dispatcher else base

    def screenshot(self) -> bytes:
        return self.runtime.screenshot()

    def control(self, command: str) -> dict[str, JsonValue]:
        if command == "status":
            return self.status().model_dump(mode="json")
        if command == "clean" and self.artifacts:
            self.artifacts.clean()
            if self.dispatcher:
                self.dispatcher.journal.maintain()
            return {"cleaned": True}
        if self.dispatcher and command == "pause":
            self.dispatcher.pause()
            return {"paused": True}
        if self.dispatcher and command == "resume":
            reply = self.dispatcher.resume()
            if reply.status >= 400:
                raise BridgeError("SERVICE_NOT_READY", "恢复核对失败，查看服务状态及日志")
            return {"resumed": True}
        raise BridgeError("SERVICE_NOT_READY", "终端执行器尚未启用")

    def stop(self) -> None:
        self.stopping.set()
        if self.control_server:
            self.control_server.stop()
        if self.dispatcher:
            self.dispatcher.stop()
        self.runtime.stop()
        if self.dispatcher:
            self.dispatcher.terminate_after_desktop()
        if self.maintenance and self.maintenance.is_alive():
            self.maintenance.join(timeout=2)
