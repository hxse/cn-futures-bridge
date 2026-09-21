"""唯一 FastAPI 应用；业务与诊断共用服务生命周期。"""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import RequestResponseEndpoint

from .errors import BridgeError, ErrorDetail, FieldProblem, ServiceStatus
from .service import BridgeService

LOG = logging.getLogger(__name__)


def request_id(request: Request) -> str:
    return str(request.state.request_id)


def create_app(service: BridgeService, *, manage_lifecycle: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if manage_lifecycle:
            await run_in_threadpool(service.start)
        try:
            yield
        finally:
            if manage_lifecycle:
                await run_in_threadpool(service.stop)

    app = FastAPI(title="cn-futures-bridge", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def trace(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = "cfb-" + uuid.uuid4().hex
        start = time.monotonic()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        LOG.info("HTTP 请求结束", extra={"request_id": request_id(request), "action": request.method,
                 "step": "response", "event": "end", "duration_ms": (time.monotonic()-start)*1000,
                 "outcome": response.status_code})
        return response

    @app.exception_handler(BridgeError)
    async def bridge_error(request: Request, exc: BridgeError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.response(request_id(request)).model_dump())

    @app.exception_handler(RequestValidationError)
    async def invalid_arguments(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [FieldProblem(loc=list(error["loc"]), type=error["type"], message=error["msg"])
                    for error in exc.errors()]
        error = BridgeError("INVALID_ARGUMENTS", "请求参数无效", 422, details=problems)
        return JSONResponse(status_code=422, content=error.response(request_id(request)).model_dump())

    @app.get("/healthz", tags=["diagnostics"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/status", response_model=ServiceStatus, tags=["diagnostics"])
    def status() -> ServiceStatus:
        return service.status()

    @app.get("/readyz", response_model=ServiceStatus, tags=["diagnostics"])
    def ready() -> JSONResponse:
        value = service.status()
        available = value.state == "window_visible" and value.terminal_window_visible
        return JSONResponse(status_code=200 if available else 503, content=value.model_dump())

    @app.get("/v1/desktop/screenshot", tags=["diagnostics"], response_class=Response)
    def screenshot() -> Response:
        return Response(service.screenshot(), media_type="image/png")

    return app
