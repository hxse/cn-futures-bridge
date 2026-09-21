"""独立 GUI 进程与有界 JSON IPC，不把 HTTP 的取消传播成 GUI 解锁。"""

import logging
from multiprocessing.connection import Connection
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .config import Settings
from .errors import BridgeError, ErrorDetail
from .journal import Journal, error_reply
from .logging_store import configure_logging
from .models import Operation
from .results import Reply
from .terminal.executor import Executor

MAX_REPLY_BYTES = 8 * 1024 * 1024
LOG = logging.getLogger(__name__)


class WorkerState(BaseModel):
    login_state: str = "logging_in"
    blocked: bool = False
    trading_ready: bool = False
    query_ready: bool = False
    ownership_clear: bool = False
    error: ErrorDetail | None = None


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation: str
    request_id: str
    kind: Literal["execute", "probe", "resume", "stop"]
    operation: Operation | None = None
    deadline: float = 0


class Message(BaseModel):
    generation: str
    request_id: str
    state: WorkerState
    reply: Reply | None = None


def snapshot(executor: Executor) -> WorkerState:
    session = executor.session
    ready = (session is not None and session.connected and session.identity_match
             and not executor.disconnected and not executor.blocked and not executor.native.poisoned)
    return WorkerState(login_state=executor.login_state, blocked=executor.blocked,
                       query_ready=ready, trading_ready=ready and bool(session and session.trading_day),
                       ownership_clear=not executor.native.poisoned,
                       error=executor.error)


def send(connection: Connection, generation: str, request_id: str, executor: Executor,
         reply: Reply | None = None) -> None:
    message = Message(generation=generation, request_id=request_id, state=snapshot(executor), reply=reply)
    data = message.model_dump_json().encode()
    if len(data) > MAX_REPLY_BYTES:
        message.reply = error_reply(request_id, BridgeError("TERMINAL_DATA_INVALID", "查询结果超过 IPC 容量限制", 502))
        data = message.model_dump_json().encode()
    connection.send_bytes(data)


def worker_main(connection: Connection, settings: Settings, generation: str) -> None:
    logs = configure_logging(settings)
    executor = Executor(settings, logs, Journal(settings))
    try:
        try:
            executor.start()
        except BridgeError as exc:
            executor.fail(exc)
        except Exception as exc:
            LOG.error("执行器启动失败：%s", type(exc).__name__)
            executor.fail(BridgeError("SERVICE_NOT_READY", "执行器启动失败，未自动重试"))
        send(connection, generation, "startup", executor)
        while True:
            command = Command.model_validate_json(connection.recv_bytes(65536))
            if command.generation != generation:
                raise BridgeError("SERVICE_NOT_READY", "IPC 会话代次不符")
            if command.kind == "stop":
                executor.native.close()
                send(connection, generation, command.request_id, executor)
                return
            reply = None
            try:
                if logs.failed:
                    raise BridgeError("STORAGE_UNAVAILABLE", "执行器日志不可写，暂停操作")
                if command.kind == "resume":
                    executor.resume()
                elif command.kind == "probe" and not executor.blocked and settings.account.configured:
                    executor.validate_session()
                    executor.gui.baseline()
                elif command.kind == "execute":
                    if time.monotonic() > command.deadline:
                        raise BridgeError("QUEUE_TIMEOUT", "请求派发期限已过，尚未执行", 504)
                    if command.operation is None:
                        raise BridgeError("INVALID_ARGUMENTS", "IPC 缺少业务参数", 422)
                    reply = executor.execute(command.request_id, command.operation)
            except BridgeError as exc:
                if command.kind != "execute" or exc.code == "STORAGE_UNAVAILABLE":
                    executor.fail(exc)
                reply = error_reply(command.request_id, exc, blocked=executor.blocked)
            send(connection, generation, command.request_id, executor, reply)
    except (EOFError, OSError, ValueError, BridgeError):
        LOG.error("执行器 IPC 结束；不重放任何操作")
    finally:
        # 原生调用超时仍由控制器占有；停止整个 Wine 会话前不得重启第二个 owner。
        if not executor.native.poisoned:
            try:
                executor.native.close()
            except Exception:
                LOG.error("原生控制器未能正常退出")
        connection.close()
