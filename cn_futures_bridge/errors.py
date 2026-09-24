"""跨 HTTP 与执行器使用同一错误语义。"""

from typing import Literal

from pydantic import BaseModel, Field
from .results import OrderExecution, OrderIdentity, PriceContext, Verification

Submission = Literal["submitted", "rejected", "unknown"]
Capability = Literal["supported", "unsupported", "unverified"]


class FieldProblem(BaseModel):
    loc: list[str | int]
    type: str
    message: str
    context: PriceContext | None = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[FieldProblem] | None = None


class ErrorResponse(BaseModel):
    request_id: str
    submission_status: Submission | None = None
    order_id: str | None = Field(default=None,
        description="异常前已取得的真实订单编号；非 2xx 不表示未下单，可用 fetch_orders 的 order_sys_id 继续查询。")
    identity: OrderIdentity | None = Field(default=None,
        description="异常前已捕获的完整引用；无订单编号时可用六个字段及原 mode 查询 fetch_orders，禁止根据报错自动重发。")
    error: ErrorDetail
    execution: OrderExecution | None = None
    verification: Verification | None = None


class BridgeError(Exception):
    def __init__(self, code: str, message: str, status: int = 503, *,
                 submission_status: Submission | None = None,
                 order_id: str | None = None, identity: OrderIdentity | None = None,
                 details: list[FieldProblem] | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.submission_status = submission_status
        self.order_id = order_id
        self.identity = identity
        self.details = details

    def response(self, request_id: str) -> ErrorResponse:
        return ErrorResponse(request_id=request_id, submission_status=self.submission_status,
                             order_id=self.order_id, identity=self.identity,
                             error=ErrorDetail(code=self.code, message=str(self), details=self.details))


CAPABILITY_NAMES = (
    "create_market_order", "create_limit_order", "cancel_order", "fetch_orders",
    "fetch_trades", "fetch_positions", "fetch_balance", "fetch_trading_status",
    "limit_order_ioc", "limit_order_fok", "close_today", "close_yesterday",
    "cancel_session_order", "hedge_arbitrage", "hedge_hedge", "invest_unit", "non_cny_balance",
)


def initial_capabilities() -> dict[str, Capability]:
    return {name: "unverified" for name in CAPABILITY_NAMES}


class ReconnectStatus(BaseModel):
    enabled: bool = True
    interval_seconds: int = 600
    state: Literal["idle", "waiting", "reconnecting", "paused", "manual", "disabled"] = "idle"
    attempts: int = 0
    last_failure_at: str | None = None
    last_error: ErrorDetail | None = None
    next_retry_at: str | None = None
    manual_required: bool = False


class ServiceStatus(BaseModel):
    environment: str = "simnow"
    request_mode: Literal["sandbox", "live"] = "sandbox"
    broker_id: str = "9999"
    site: str = "电信2"
    stage: str = "terminal_bootstrap"
    state: str = "starting"
    terminal_version: str | None = None
    terminal_window_visible: bool = False
    account_configured: bool = False
    login_state: str = "unverified"
    trading_ready: bool = False
    automation_enabled: bool = False
    vnc_enabled: bool = False
    error: ErrorDetail | None = None
    executor_state: str = "unavailable"
    queue_depth: int = 0
    active_operation_id: str | None = None
    unresolved_operations: int = 0
    reconnect: ReconnectStatus = Field(default_factory=ReconnectStatus)
    capabilities: dict[str, Capability] = Field(default_factory=initial_capabilities)
