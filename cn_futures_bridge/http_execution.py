"""HTTP 等待与终端所有权解耦；断开仅取消尚未开始的排队项。"""

import asyncio
import logging
import threading
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .dispatch import Job
from .errors import BridgeError, FieldProblem
from .models import Action, Operation, RequestModel
from .results import Reply
from .service import BridgeService
from .terminal.policy import validate_capability

LOG = logging.getLogger(__name__)


def invalid(location: list[str | int], message: str) -> BridgeError:
    return BridgeError("INVALID_ARGUMENTS", "请求参数无效", 422,
                       details=[FieldProblem(loc=location, type="value_error", message=message)])


async def check_envelope(request: Request) -> None:
    names = list(request.query_params.keys())
    for name in names:
        if len(request.query_params.getlist(name)) != 1:
            raise invalid(["query", name], "单值参数不能重复")
    if request.method == "POST":
        if names:
            raise invalid(["query", names[0]], "POST 参数必须放在 JSON 对象中")
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise invalid(["header", "Content-Type"], "需要 application/json")
        if len(request.headers.getlist("idempotency-key")) > 1:
            raise invalid(["header", "Idempotency-Key"], "幂等键不能重复")


def response(request: Request, reply: Reply) -> JSONResponse:
    request.state.request_id = reply.request_id
    request.state.submission_status = reply.body.get("submission_status")
    request.state.order_id = reply.body.get("order_id")
    return JSONResponse(status_code=reply.status, content=reply.body)


async def execute(service: BridgeService, request: Request, action: Action,
                  parameters: RequestModel, key: str | None = None) -> JSONResponse:
    LOG.info("已校验的业务参数：%s", parameters.model_dump_json(), extra={
        "request_id": str(request.state.request_id), "action": action, "step": "validate",
        "event": "end", "outcome": "ok"})
    validate_capability(parameters)
    dispatcher = service.dispatcher
    if dispatcher is None:
        raise BridgeError("SERVICE_NOT_READY", "终端执行器尚未启动")
    operation = Operation(action=action, parameters=parameters.model_dump(mode="json"))
    cancelled = threading.Event()
    admission = asyncio.create_task(run_in_threadpool(dispatcher.submit, str(request.state.request_id),
                                                     operation, key, cancelled))
    job: Job | None = None
    try:
        while not admission.done():
            done, _ = await asyncio.wait({admission}, timeout=.05)
            if not done and await request.is_disconnected():
                cancelled.set()
                admission.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                error = BridgeError("REQUEST_CANCELLED", "HTTP 在准入期间断开，尚未开始的操作将取消", 499)
                return JSONResponse(status_code=499, content=error.response(str(request.state.request_id)).model_dump())
        item = await asyncio.shield(admission)
        if isinstance(item, Reply):
            return response(request, item)
        job = item
        request.state.operation_job = job
        future = asyncio.wrap_future(job.future)
        while not future.done():
            done, _ = await asyncio.wait({future}, timeout=.05)
            if done:
                break
            if await request.is_disconnected():
                cancelled.set()
                removed = await run_in_threadpool(dispatcher.cancel, job)
                # 已开始的操作继续由 owner 收尾并保存；连接已经不存在，无需等待 HTTP 返回。
                error = BridgeError("REQUEST_CANCELLED", "HTTP 调用已断开；已开始的操作仍由执行器记录结果", 499,
                    submission_status="unknown" if not removed and action.startswith(("create_", "cancel_")) else None)
                return JSONResponse(status_code=499, content=error.response(job.request_id).model_dump(mode="json"))
            if not job.started and time.monotonic() > job.deadline:
                await run_in_threadpool(dispatcher.cancel, job, timeout=True)
        return response(request, future.result())
    except asyncio.CancelledError:
        cancelled.set()
        # 包括线程准入尚未返回的窗口；Event 同时阻止之后才入队的项被派发。
        admission.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        raise
