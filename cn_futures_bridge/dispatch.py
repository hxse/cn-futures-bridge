"""单账户 FIFO、请求取消、人工接管和独立执行器生命周期。"""

from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass, field
import logging
import multiprocessing
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
import threading
import time
import uuid

from .config import Settings
from .errors import BridgeError, ErrorDetail, ServiceStatus
from .journal import Journal, error_reply
from .models import Operation
from .results import Reply
from .runtime import Runtime
from .terminal.policy import capabilities, validate_capability
from .worker import Command, MAX_REPLY_BYTES, Message, WorkerState, worker_main

LOG = logging.getLogger(__name__)
WRITE_ACTIONS = ("create_market_order", "create_limit_order", "cancel_order")


@dataclass
class Job:
    request_id: str
    operation: Operation
    deadline: float
    future: Future[Reply] = field(default_factory=Future)
    started: bool = False
    queued_at: float = field(default_factory=time.monotonic)


class Dispatcher:
    def __init__(self, settings: Settings, runtime: Runtime):
        self.settings = settings
        self.runtime = runtime
        self.journal = Journal(settings)
        self.unresolved = self.journal.recover()
        self.queue: deque[Job] = deque()
        self.condition = threading.Condition()
        self.active: Job | None = None
        self.paused = False
        self.stopping = False
        self.state = WorkerState(login_state="logging_in" if settings.account.configured else "not_configured")
        self.generation = uuid.uuid4().hex
        self.connection: Connection | None = None
        self.process: BaseProcess | None = None
        self.resume_future: Future[Reply] | None = None
        self.ready = False
        self.failed = False
        self.owner_busy = False
        self.thread = threading.Thread(target=self._run, name="gui-dispatch", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def submit(self, request_id: str, operation: Operation, key: str | None) -> Job | Reply:
        request = operation.request()
        validate_capability(request)
        write = operation.action in WRITE_ACTIONS
        with self.condition:
            replay = self.journal.lookup(operation, key) if write else None
            if replay:
                LOG.info("重放既有逻辑请求 %s", replay.request_id, extra={"request_id": request_id, "event": "replay"})
                return replay
            if self.stopping or self.failed or not self.ready or self.paused or self.state.blocked:
                raise BridgeError("SERVICE_NOT_READY", "执行器未就绪或已经暂停")
            if not self.state.query_ready or (write and not self.state.trading_ready):
                raise BridgeError("SERVICE_NOT_READY", "真实账户、连接或交易日尚未就绪")
            if len(self.queue) >= self.settings.execution.queue_capacity:
                raise BridgeError("QUEUE_FULL", "执行队列已满，请稍后再试", 429)
            if write:
                existing = self.journal.admit(request_id, operation, key)
                if existing:
                    return existing
            job = Job(request_id, operation, time.monotonic()+self.settings.execution.queue_timeout_ms/1000)
            self.queue.append(job)
            LOG.info("请求入队", extra={"request_id": request_id, "action": operation.action, "step": "queue", "event": "start"})
            self.condition.notify_all()
            return job

    def cancel(self, job: Job, *, timeout: bool = False) -> bool:
        with self.condition:
            if job.started or job.future.done():
                return False
            try:
                self.queue.remove(job)
            except ValueError:
                return False
            error = BridgeError("QUEUE_TIMEOUT" if timeout else "REQUEST_CANCELLED",
                                "请求尚未执行，已从队列移除", 504 if timeout else 499)
            self._finish(job, error_reply(job.request_id, error))
            return True

    def _finish(self, job: Job, reply: Reply) -> None:
        if job.operation.action in WRITE_ACTIONS:
            # 父进程再保存 IPC 中的最终响应，队列取消及执行器派发前拒绝同样受保护。
            try:
                self.journal.finish(reply)
                self.unresolved = self.journal.unresolved()
            except (OSError, BridgeError):
                self.failed = True
                self.state.error = ErrorDetail(code="STORAGE_UNAVAILABLE", message="最终操作记录未能保存，已停止派发")
                effect = reply.body.get("submission_status")
                reply = error_reply(job.request_id, BridgeError("STORAGE_UNAVAILABLE", "最终响应未能持久化，已暂停派发",
                    submission_status=effect if isinstance(effect, str) and effect in ("submitted", "rejected", "unknown") else None), blocked=True)
        if not job.future.done():
            job.future.set_result(reply)
        LOG.info("请求结束", extra={"request_id": job.request_id, "action": job.operation.action,
                 "step": "return", "event": "end", "duration_ms": (time.monotonic()-job.queued_at)*1000,
                 "outcome": reply.status})

    def _exchange(self, command: Command, timeout: float) -> Message:
        if self.connection is None:
            raise BridgeError("GUI_UNRESPONSIVE", "执行器通信未建立")
        self.connection.send_bytes(command.model_dump_json().encode())
        return self._receive(command.request_id, timeout)

    def _receive(self, request_id: str, timeout: float) -> Message:
        connection = self.connection
        if connection is None or not connection.poll(timeout):
            raise BridgeError("GUI_UNRESPONSIVE", "执行器未在期限内响应，旧执行权保留隔离")
        message = Message.model_validate_json(connection.recv_bytes(MAX_REPLY_BYTES))
        if message.generation != self.generation or message.request_id != request_id:
            raise BridgeError("GUI_UNRESPONSIVE", "执行器返回旧代次或错误操作编号")
        with self.condition:
            self.state = message.state
        return message

    def _command(self, kind: str) -> Command:
        return Command.model_validate({"generation": self.generation, "kind": kind, "request_id": uuid.uuid4().hex})

    def _run(self) -> None:
        try:
            deadline = time.monotonic()+self.settings.bridge.startup_timeout_seconds+30
            while not self.runtime.status().terminal_window_visible:
                if self.stopping:
                    return
                if self.runtime.status().error or time.monotonic() > deadline:
                    raise BridgeError("SERVICE_NOT_READY", "终端桌面未就绪，执行器未启动")
                time.sleep(.1)
            context = multiprocessing.get_context("spawn")
            self.connection, child = context.Pipe()
            self.process = context.Process(target=worker_main, args=(child, self.settings, self.generation), name="cfb-gui")
            self.process.start();child.close()
            self._receive("startup", self.settings.bridge.startup_timeout_seconds+15)
            with self.condition:
                self.ready = True
                if self.unresolved:
                    self.paused = True
                self.condition.notify_all()
            self._loop()
        except Exception as exc:
            error = exc if isinstance(exc, BridgeError) else BridgeError("GUI_UNRESPONSIVE", "执行器通信失效")
            LOG.error("执行器已隔离：%s", error)
            with self.condition:
                self.failed = True
                self.state.blocked = True
                self.state.error = error.response("internal").error
                if self.active:
                    job = self.active
                    # 保留子进程已持久化的事实；没有结果时恢复操作记录，绝不假设未发送。
                    try:
                        reply = (self.journal.interrupted(job.request_id) if job.operation.action in WRITE_ACTIONS
                                 else error_reply(job.request_id, error, blocked=True))
                    except (OSError, BridgeError):
                        reply = error_reply(job.request_id, BridgeError("STORAGE_UNAVAILABLE", "执行器失联且操作记录不可读",
                            submission_status="unknown" if job.operation.action in WRITE_ACTIONS else None), blocked=True)
                    self._finish(job, reply)
                self.condition.notify_all()
        finally:
            with self.condition:
                while self.queue:
                    job = self.queue.popleft()
                    self._finish(job, error_reply(job.request_id, BridgeError("SERVICE_NOT_READY", "执行器停止，队列项未执行")))
                if self.resume_future and not self.resume_future.done():
                    self.resume_future.set_exception(BridgeError("SERVICE_NOT_READY", "执行器已经停止"))
                self.condition.notify_all()

    def _loop(self) -> None:
        interval = self.settings.execution.step_timeout_ms/1000
        next_probe = time.monotonic()+2
        while True:
            with self.condition:
                if self.stopping or self.failed:
                    break
                for item in list(self.queue):
                    if time.monotonic() > item.deadline:
                        self.cancel(item, timeout=True)
                resume = self.resume_future
                if resume:
                    self.resume_future = None
                if not resume and (self.paused or self.state.blocked or not self.queue):
                    if time.monotonic() < next_probe or self.paused or self.state.blocked:
                        self.condition.wait(.1)
                        continue
                    job = None
                elif not resume:
                    job = self.queue.popleft();job.started = True;self.active = job
                else:
                    job = None
                self.owner_busy = True
            if resume:
                message = self._exchange(self._command("resume"), 4*interval+5)
                reply = message.reply or Reply(request_id=message.request_id, body={"resumed": True})
                with self.condition:
                    if not message.state.blocked:
                        self.paused = False
                    resume.set_result(reply)
                    self.condition.notify_all()
            elif job:
                LOG.info("请求取得执行权", extra={"request_id": job.request_id, "action": job.operation.action,
                    "step": "queue", "event": "end", "duration_ms": (time.monotonic()-job.queued_at)*1000})
                command = Command(generation=self.generation, request_id=job.request_id, kind="execute",
                                  operation=job.operation, deadline=job.deadline)
                message = self._exchange(command, 80*interval+10)
                if message.reply is None:
                    raise BridgeError("GUI_UNRESPONSIVE", "执行器未返回操作结果")
                with self.condition:
                    self._finish(job, message.reply)
                    self.active = None
                    self.condition.notify_all()
            else:
                self._exchange(self._command("probe"), 4*interval+5)
            with self.condition:
                self.owner_busy = False
                self.condition.notify_all()
            next_probe = time.monotonic()+2
        if not self.failed:
            self._exchange(self._command("stop"), interval+5)

    def pause(self) -> None:
        deadline = time.monotonic()+min(25, 4*self.settings.execution.step_timeout_ms/1000+2)
        with self.condition:
            self.paused = True
            while self.active or self.owner_busy or (not self.ready and not self.failed):
                left = deadline-time.monotonic()
                if left <= 0 or self.failed:
                    raise BridgeError("GUI_UNRESPONSIVE", "当前执行权未退出，暂停尚未完成，不能人工操作")
                self.condition.wait(left)
            if self.failed or not self.state.ownership_clear:
                raise BridgeError("GUI_UNRESPONSIVE", "旧执行权不明，需要受控重启")

    def resume(self) -> Reply:
        with self.condition:
            if not self.ready or self.failed or self.active or self.resume_future:
                raise BridgeError("SERVICE_NOT_READY", "当前不能安全恢复执行")
            self.paused = True
            future: Future[Reply] = Future()
            self.resume_future = future
            self.condition.notify_all()
        try:
            return future.result(timeout=25)
        except TimeoutError as exc:
            raise BridgeError("GUI_UNRESPONSIVE", "恢复核对未完成") from exc

    def status(self, base: ServiceStatus) -> ServiceStatus:
        with self.condition:
            state = ("stopping" if self.stopping else "blocked" if self.failed or self.state.blocked
                     else "starting" if not self.ready else "running" if self.active else "paused" if self.paused else "idle")
            available = state in ("idle", "running") and base.error is None
            return base.model_copy(update={"stage": "terminal_execution", "executor_state": state,
                "login_state": self.state.login_state, "queue_depth": len(self.queue),
                "active_operation_id": self.active.request_id if self.active else None,
                "unresolved_operations": self.unresolved, "capabilities": capabilities(),
                "automation_enabled": available and self.state.query_ready,
                "trading_ready": available and self.state.trading_ready,
                "error": base.error or self.state.error})

    def stop(self) -> None:
        with self.condition:
            self.stopping = True
            self.condition.notify_all()
        if self.thread.is_alive():
            self.thread.join(timeout=5)

    def terminate_after_desktop(self) -> None:
        # 先由 Runtime 终止专属 Wine 会话，再释放 Python owner 和 IPC。
        if self.process and self.process.is_alive():
            self.process.terminate();self.process.join(timeout=3)
        if self.connection:
            self.connection.close()
        if self.thread.is_alive():
            self.thread.join(timeout=3)
