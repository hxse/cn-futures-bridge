"""恰好八个业务路由；输入和结果复用领域模型。"""

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from .errors import ErrorResponse
from .http_execution import check_envelope, execute
from .models import (BalanceQuery, CancelOrder, LimitOrder, MarketOrder, OrderQuery,
                     PositionQuery, TradeQuery, TradingStatusQuery)
from .results import (BalanceResult, OrdersResult, PositionsResult, SubmissionResult,
                      TradesResult, TradingStatusResult)
from .service import BridgeService

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128,
    pattern=r"^[!-~]+$", description="同账户内防重复提交；同键同参数重放原请求结果。")]


def business_router(service: BridgeService) -> APIRouter:
    errors: dict[int | str, dict[str, object]] = {
        code: {"model": ErrorResponse} for code in (409, 422, 429, 500, 501, 502, 503, 504)}
    errors[503].update(description="终端未就绪或操作失败；离线请求不保留到重连后执行。",
        headers={"Retry-After": {"description": "有自动重连计划时建议等待的秒数，不保证届时恢复。",
                                  "schema": {"type": "integer", "minimum": 1}}})
    router = APIRouter(prefix="/cfb", tags=["cfb"], dependencies=[Depends(check_envelope)], responses=errors)
    submit_description = ("完成本地提交和界面收尾后返回 202；submitted 不代表柜台接受或成交。"
                          "请独立查询订单与成交，未知结果不要更换幂等键重发。")

    @router.post("/create_market_order", response_model=SubmissionResult, status_code=202,
                 summary="市价开仓或平仓", description=submit_description + "原生市价模式尚未核验，当前返回 501。")
    async def create_market_order(request: Request, order: MarketOrder,
                                  idempotency_key: IdempotencyKey = None) -> JSONResponse:
        return await execute(service, request, "create_market_order", order, idempotency_key)

    @router.post("/create_limit_order", response_model=SubmissionResult, status_code=202,
                 summary="限价开仓或平仓", description=submit_description + "基础分支为 GFD/投机，其他能力看 /v1/status。")
    async def create_limit_order(request: Request, order: LimitOrder,
                                 idempotency_key: IdempotencyKey = None) -> JSONResponse:
        return await execute(service, request, "create_limit_order", order, idempotency_key)

    @router.post("/cancel_order", response_model=SubmissionResult, status_code=202,
                 summary="撤销订单未成交部分", description=submit_description + "使用精确交易所订单编号；会话编号方式尚未核验。")
    async def cancel_order(request: Request, order: Annotated[CancelOrder, Body(discriminator="by")],
                           idempotency_key: IdempotencyKey = None) -> JSONResponse:
        return await execute(service, request, "cancel_order", order, idempotency_key)

    @router.get("/fetch_orders", response_model=OrdersResult, summary="查询订单",
                description="当前终端交易日的完整委托快照，包含已成、已撤和拒单；source=terminal_csv。")
    async def fetch_orders(request: Request, query: Annotated[OrderQuery, Query()]) -> JSONResponse:
        return await execute(service, request, "fetch_orders", query)

    @router.get("/fetch_trades", response_model=TradesResult, summary="查询逐笔成交",
                description="本账户成交明细；不能可靠关联的 order_id 为 null。")
    async def fetch_trades(request: Request, query: Annotated[TradeQuery, Query()]) -> JSONResponse:
        return await execute(service, request, "fetch_trades", query)

    @router.get("/fetch_positions", response_model=PositionsResult, summary="查询持仓",
                description="按终端合约/方向/投保维度返回总仓、今昨仓及可平量；不复制 CTP 行划分。")
    async def fetch_positions(request: Request, query: Annotated[PositionQuery, Query()]) -> JSONResponse:
        return await execute(service, request, "fetch_positions", query)

    @router.get("/fetch_balance", response_model=BalanceResult, summary="查询账户资金",
                description="读取资金详情原生文字中的服务器列，包含权益、可用资金和保证金；source=terminal_text。")
    async def fetch_balance(request: Request, query: Annotated[BalanceQuery, Query()]) -> JSONResponse:
        return await execute(service, request, "fetch_balance", query)

    @router.get("/fetch_trading_status", response_model=TradingStatusResult, summary="查询品种是否连续交易",
                description="读取快期维护的原生品种状态；不使用日历或颜色。集合竞价返回 false，未知/断线报错。")
    async def fetch_trading_status(request: Request, query: Annotated[TradingStatusQuery, Query()]) -> JSONResponse:
        return await execute(service, request, "fetch_trading_status", query)

    return router
