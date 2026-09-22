"""独立 GUI 进程与有界 JSON IPC，不把 HTTP 的取消传播成 GUI 解锁。"""

import logging
import multiprocessing
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
import time
from typing import Literal
import uuid

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
            LOG.error("执行器启动失败：%s: %s", exc.code, exc)
            executor.fail(exc)
            executor.failure_evidence(exc)
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
                    executor.failure_evidence(exc)
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


class WorkerProcess:
    """同一 GUI owner 的进程和 IPC；退出确认之前不允许创建新代次。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.generation = ""
        self.connection: Connection | None = None
        self.process: BaseProcess | None = None

    def start(self) -> Message:
        if self.process is not None or self.connection is not None:
            raise BridgeError("GUI_UNRESPONSIVE", "旧执行器尚未回收，不能启动新 owner")
        self.generation = uuid.uuid4().hex
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=worker_main, args=(child, self.settings, self.generation), name="cfb-gui")
        try:
            self.process.start()
        finally:
            child.close()
        return self.receive("startup", self.settings.bridge.startup_timeout_seconds+15)

    def command(self, kind: str) -> Command:
        return Command.model_validate({"generation": self.generation, "kind": kind, "request_id": uuid.uuid4().hex})

    def exchange(self, command: Command, timeout: float) -> Message:
        if self.connection is None:
            raise BridgeError("GUI_UNRESPONSIVE", "执行器通信未建立")
        self.connection.send_bytes(command.model_dump_json().encode())
        return self.receive(command.request_id, timeout)

    def receive(self, request_id: str, timeout: float) -> Message:
        connection = self.connection
        if connection is None or not connection.poll(timeout):
            raise BridgeError("GUI_UNRESPONSIVE", "执行器未在期限内响应，旧执行权保留隔离")
        message = Message.model_validate_json(connection.recv_bytes(MAX_REPLY_BYTES))
        if message.generation != self.generation or message.request_id != request_id:
            raise BridgeError("GUI_UNRESPONSIVE", "执行器返回旧代次或错误操作编号")
        return message

    def stop(self) -> None:
        self.exchange(self.command("stop"), self.settings.execution.step_timeout_ms/1000+5)
        if self.process:
            self.process.join(timeout=3)
            if self.process.is_alive():
                raise BridgeError("GUI_UNRESPONSIVE", "旧执行器尚未退出，停止自动重连")
            self.process.close()
            self.process = None
        if self.connection:
            self.connection.close()
            self.connection = None
        LOG.info("GUI 执行器及控制器已退出", extra={"step": "worker_stop", "event": "end"})

    def terminate_after_desktop(self) -> None:
        # 仅在专属 Wine 会话停止之后调用，不以终止 Python 冒充 GUI 执行权释放。
        if self.process and self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=3)
        if self.connection:
            self.connection.close()
