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
    submit_description = ("通知只清障，不参与交易状态判断。开平仓捕获实际报单引用并返回 identity、真实 order_id；"
                          "按完整引用关联订单及其成交，再有界回读 CSV。撤单使用精确订单号。"
                          "HTTP 202、submitted 和 verification.status=observed 均不等于全部成交，须读取订单 status 和 filled_volume。\n\n"
                          "**再次确认状态**：调用 GET /cfb/fetch_orders，把本次返回的 order_id 原样传给 order_sys_id，"
                          "同时带上原请求的 mode、exchange_id、instrument_id。order_id 是字符串，不能当数字处理。"
                          "若 order_id=null，但 identity 已返回，则将 identity 的六个字段完整作为查询参数，"
                          "并带原 mode；此时不传 order_sys_id。两种查询方式不能混用。\n\n"
                          "示例编号仅展示格式，请替换为实际响应值：\n"
                          "```bash\n"
                          "curl 'http://127.0.0.1:45173/cfb/fetch_orders?mode=sandbox&exchange_id=DCE&instrument_id=m2701&order_sys_id=648294'\n"
                          "```\n\n"
                          "仅查询当前交易日。空结果或读取失败不能证明没有下单；非 2xx 也可能保留已提交事实及标识。"
                          "未知结果不要更换幂等键重发；同幂等键只重放原响应，查询最新状态须调用上述 GET。\n\n")
    orders_description = """查询终端当前交易日的订单快照，包含已成交、已撤单和终端仍保留的拒单。

**按订单编号复查（优先）**：将开仓或平仓响应的 `order_id` 原样传入 `order_sys_id`，
并带原请求的 `mode`、`exchange_id`、`instrument_id`。不要把 `request_id` 当订单编号。

```bash
curl 'http://127.0.0.1:45173/cfb/fetch_orders?mode=sandbox&exchange_id=DCE&instrument_id=m2701&order_sys_id=648294'
```

**尚无订单编号**：若下单响应的 `order_id=null` 且有 `identity`，完整传入其中的
`exchange_id`、`instrument_id`、`trading_day`、`front_id`、`session_id`、`order_ref`，并带原 `mode`。
不能同时传 `order_sys_id`，不能只传 `order_ref`；缺少成组字段返回 422，其他交易日返回 501。

```bash
curl 'http://127.0.0.1:45173/cfb/fetch_orders?mode=sandbox&exchange_id=DCE&instrument_id=m2701&trading_day=20260924&front_id=3&session_id=123&order_ref=18'
```

以上编号仅展示格式，使用实际响应值。完整引用查询返回的 `identity` 对应本次查询，
`source=terminal_csv_and_native`；仅有原生事实而 CSV 未到齐时 `consistency=changing`。

**如何确认**：读取 `orders[]` 中目标订单的 `status`、`filled_volume`、`remaining_volume`：

| status | 含义 |
| --- | --- |
| filled | 全部成交 |
| open | 挂单，尚未成交 |
| partially_filled | 部分成交，仍有未成交部分 |
| cancelled | 剩余部分已撤销；是否曾成交看 filled_volume |
| rejected | 订单被拒绝 |
| pending / unknown | 尚不能确认最终结果 |

`remaining_volume` 表示未成交手数；已撤单的剩余量不代表仍有活动挂单。
`consistency=stable` 只表示连续读取的快照一致，不代表全部成交；`changing` 表示尚未确认快照稳定。
`orders=[]` 只表示当前快照未找到，不代表未曾提交；读取失败会报错，不能当作空订单或下单失败。
拒单可能没有交易所编号，纯本地拒单也可能在终端重启后消失，不提供历史回补。
需要后续确认时再次调用本 GET，不重复下单；持仓差值和界面通知不用于归属本次订单。
"""

    @router.post("/create_market_order", response_model=SubmissionResult, status_code=202,
                 summary="模拟市价开仓或平仓", description=submit_description + "使用限价 IOC 模拟：买入涨停价、卖出跌停价，允许部分成交、剩余撤销，不保证成交；execution 返回实际限价。")
    async def create_market_order(request: Request, order: MarketOrder,
                                  idempotency_key: IdempotencyKey = None) -> JSONResponse:
        return await execute(service, request, "create_market_order", order, idempotency_key)

    @router.post("/create_limit_order", response_model=SubmissionResult, status_code=202,
                 summary="限价开仓或平仓", description=submit_description +
                 "支持 GFD/IOC、投机；FOK 等未核验分支仍报能力错误。\n\n"
                 "**价格处理**：先修正不超过 tick×10⁻⁹ 的浮点尾差，再按买下卖上对齐。"
                 "买价高于涨停或卖价低于跌停时，超界幅度不超过配置比例才自动截断（默认 5%，含边界）。"
                 "买价低于跌停、卖价高于涨停或超界过大返回 422；合约价格资料无效返回 503。"
                 "不改变手数和有效期，不自动追价或补单。\n\n"
                 "execution.requested_price 为原价，price 为实际提交限价，price_adjusted 和 price_adjustments 说明调整。"
                 "例如 tick=1 时买入 3514.35 按 3514 提交；实际成交价仍以成交回报为准。"
                 "价格错误的 error.details[].context 提供请求价、步长、上下限及允许超界比例。"
                 "幂等键仍匹配原始请求，重放不按新边界重新定价。")
    async def create_limit_order(request: Request, order: LimitOrder,
                                 idempotency_key: IdempotencyKey = None) -> JSONResponse:
        return await execute(service, request, "create_limit_order", order, idempotency_key)

    @router.post("/cancel_order", response_model=SubmissionResult, status_code=202,
                 summary="撤销订单未成交部分", description=submit_description + "使用精确交易所订单编号；会话编号方式尚未核验。")
    async def cancel_order(request: Request, order: Annotated[CancelOrder, Body(discriminator="by")],
                           idempotency_key: IdempotencyKey = None) -> JSONResponse:
        return await execute(service, request, "cancel_order", order, idempotency_key)

    @router.get("/fetch_orders", response_model=OrdersResult, summary="查询订单",
                description=orders_description)
    async def fetch_orders(request: Request, query: Annotated[OrderQuery, Query()]) -> JSONResponse:
        return await execute(service, request, "fetch_orders", query)

    @router.get("/fetch_trades", response_model=TradesResult, summary="查询逐笔成交",
                description="重复读取成交 CSV，返回最新明细和 stable/changing 一致性；不能可靠关联的 order_id 为 null。")
    async def fetch_trades(request: Request, query: Annotated[TradeQuery, Query()]) -> JSONResponse:
        return await execute(service, request, "fetch_trades", query)

    @router.get("/fetch_positions", response_model=PositionsResult, summary="查询持仓",
                description="重复读取持仓 CSV，核对数量一致性并返回最新快照；浮动盈亏不参与稳定判断，仓位变化不直接证明本次订单成交。")
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
