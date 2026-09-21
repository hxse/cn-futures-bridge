"""容器内部的运维通道，不增加公开 HTTP 业务路由。"""

from collections.abc import Callable
import json
from pathlib import Path
import socket
import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from .errors import BridgeError


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: Literal["status", "pause", "resume", "clean"]


class ControlServer:
    def __init__(self, path: Path, handler: Callable[[str], dict[str, JsonValue]]):
        self.path = path
        self.handler = handler
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._serve, name="control", daemon=True)

    def start(self) -> None:
        self.path.unlink(missing_ok=True)
        self.socket.bind(str(self.path))
        self.path.chmod(0o600)
        self.socket.listen(4)
        self.socket.settimeout(.5)
        self.thread.start()

    def _serve(self) -> None:
        while not self.stop_event.is_set():
            try:
                client, _ = self.socket.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with client:
                client.settimeout(5)
                try:
                    data = bytearray()
                    while b"\n" not in data and len(data) <= 1024:
                        chunk = client.recv(1024)
                        if not chunk:
                            break
                        data.extend(chunk)
                    request = ControlRequest.model_validate_json(bytes(data))
                    result: dict[str, JsonValue] = {"ok": True, "data": self.handler(request.command)}
                except BridgeError as exc:
                    result = {"ok": False, "code": exc.code, "message": str(exc)}
                except (OSError, ValueError):
                    result = {"ok": False, "code": "INVALID_ARGUMENTS", "message": "内部命令无效"}
                try:
                    client.sendall((json.dumps(result, ensure_ascii=False) + "\n").encode())
                except OSError:
                    pass

    def stop(self) -> None:
        self.stop_event.set()
        self.socket.close()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        self.path.unlink(missing_ok=True)


def request_control(path: Path, command: str) -> dict[str, JsonValue]:
    request = ControlRequest.model_validate({"command": command})
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(30)
        client.connect(str(path))
        client.sendall((request.model_dump_json() + "\n").encode())
        data = bytearray()
        while b"\n" not in data and len(data) < 1048576:
            chunk = client.recv(8192)
            if not chunk:
                break
            data.extend(chunk)
    result: dict[str, JsonValue] = json.loads(data)
    if not result.get("ok"):
        raise BridgeError(str(result.get("code")), str(result.get("message")))
    return result
