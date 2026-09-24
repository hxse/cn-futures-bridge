"""CFB 自己的必要返回字段，不伪造 CTP 回报。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, FiniteFloat, Field, JsonValue

from .models import Exchange, Instrument, Side

ReturnedOffset = Literal["open", "close", "close_today", "close_yesterday", "unknown"]


class ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class OrderIdentity(ResultModel):
    """当前账户的完整订单引用；六个字段一起传给 GET /cfb/fetch_orders，另带原请求 mode。"""

    exchange_id: Exchange
    instrument_id: Instrument
    trading_day: str = Field(pattern=r"^[0-9]{8}$", description="订单所属终端交易日；按引用查询目前只支持当前交易日。")
    front_id: int = Field(ge=0, le=2147483647, description="实际提交订单的前置编号，与会话和引用共同定位。")
    session_id: int = Field(ge=-2147483648, le=2147483647, description="实际提交订单的会话编号，可能为负数。")
    order_ref: str = Field(pattern=r"^[0-9]{1,12}$", description="实际发送时捕获的报单引用；不是 order_id，不能单独定位。")


class Order(ResultModel):
    order_id: str | None = Field(default=None,
        description="真实交易所订单编号；再次查询时原样传给 fetch_orders 的 order_sys_id。尚未取得或拒单未分配时为 null。")
    exchange_id: Exchange
    instrument_id: str
    side: Side
    offset: ReturnedOffset
    volume: int = Field(ge=0)
    filled_volume: int = Field(ge=0, description="已成交手数；已撤单也可能曾部分成交。")
    remaining_volume: int = Field(ge=0, description="未成交手数；已撤销或拒绝后不表示仍有活动挂单。")
    price: FiniteFloat | None = None
    status: Literal["pending", "open", "partially_filled", "filled", "cancelled", "rejected", "unknown"] = Field(
        description="filled=全部成交；open=挂单；partially_filled=部分成交；cancelled=剩余撤销；rejected=拒单；pending/unknown=尚不能确认终态。")
    status_message: str | None = None
    order_time: str | None = None


class Trade(ResultModel):
    trade_id: str
    order_id: str | None = None
    exchange_id: Exchange
    instrument_id: str
    side: Side
    offset: ReturnedOffset
    volume: int = Field(gt=0)
    price: FiniteFloat
    trade_time: str | None = None
    commission: FiniteFloat | None = None
    close_profit: FiniteFloat | None = None


class Position(ResultModel):
    exchange_id: Exchange
    instrument_id: str
    direction: Literal["long", "short"]
    hedge_flag: Literal["speculation", "arbitrage", "hedge", "unknown"]
    volume: int = Field(ge=0)
    today_volume: int | None = None
    yesterday_volume: int | None = None
    available_volume: int | None = None
    average_price: FiniteFloat | None = None
    margin: FiniteFloat | None = None
    profit: FiniteFloat | None = None


class Balance(ResultModel):
    currency_id: str = "CNY"
    equity: FiniteFloat
    available: FiniteFloat
    margin: FiniteFloat
    frozen_margin: FiniteFloat | None = None
    frozen_commission: FiniteFloat | None = None
    commission: FiniteFloat | None = None
    values_source: Literal["server", "local"] = "server"


PriceAdjustmentReason = Literal["float_noise", "upper_limit", "lower_limit", "tick_floor", "tick_ceil"]


class PriceContext(ResultModel):
    requested_price: FiniteFloat | None = None
    price_tick: FiniteFloat | None = None
    lower_limit: FiniteFloat | None = None
    upper_limit: FiniteFloat | None = None
    max_deviation_ratio: FiniteFloat | None = None


class OrderExecution(ResultModel):
    kind: Literal["limit", "emulated_market", "cancel"]
    price: FiniteFloat | None = Field(default=None, description="实际采用的提交限价；不是成交价。")
    time_in_force: Literal["GFD", "IOC"] | None = None
    requested_price: FiniteFloat | None = Field(default=None, description="显式限价收到的原始价格；模拟市价和撤单为 null。")
    price_adjusted: bool = Field(default=False, description="实际限价是否因价格处理而改变。")
    price_adjustments: list[PriceAdjustmentReason] = Field(default_factory=list,
        description="按处理顺序记录尾差、边界截断或方向取整；未调整时为空。")


class Verification(ResultModel):
    source: Literal["terminal_csv", "terminal_csv_and_native"] = "terminal_csv"
    status: Literal["observed", "pending", "ambiguous", "unavailable"] = Field(default="pending",
        description="observed=已核对约定订单状态及对应成交，不等于全部成交，须读 orders[].status；pending=尚未到齐；ambiguous=身份冲突；unavailable=读取不可用。")
    correlation: Literal["order_ref", "order_id", "snapshot_delta"] = Field(
        default="order_ref", description="新请求按引用/订单号关联；snapshot_delta 仅描述升级前保存的幂等响应。")
    attempts: int = Field(default=0, ge=0)
    orders: list[Order] = Field(default_factory=list, description="按实际报单引用或交易所订单号精确关联的委托。")
    trades: list[Trade] = Field(default_factory=list, description="新委托只返回原生成交对象确认属于该订单的 CSV 成交；撤单不认领其他成交。")
    positions_before: list[Position] = Field(default_factory=list)
    positions_after: list[Position] | None = None
    error_code: str | None = None


class SubmissionResult(ResultModel):
    request_id: str = Field(description="CFB 请求追踪编号，用于日志与幂等记录，不是订单编号，不能传给 order_sys_id。")
    submission_status: Literal["submitted"] = Field(default="submitted",
        description="仅表示本地提交，不代表柜台接受或成交；后续用 GET /cfb/fetch_orders 查询。")
    order_id: str | None = Field(default=None,
        description="已确认归属本次请求的真实交易所订单编号。用户可原样传给 GET /cfb/fetch_orders 的 order_sys_id，并带原 mode、交易所和合约；未取得时为 null。",
        examples=["648294", None])
    identity: OrderIdentity | None = Field(default=None,
        description="开平仓实际发送的完整引用。order_id 为 null 时，可把这六个字段及原 mode 传给 fetch_orders，不传 order_sys_id；缺失不能推断未提交。")
    execution: OrderExecution | None = None
    verification: Verification | None = None


class Snapshot(ResultModel):
    request_id: str
    observed_at: str
    source: Literal["terminal_csv", "terminal_text", "terminal_csv_and_native"]
    trading_day: str | None = None


class OrdersResult(Snapshot):
    orders: list[Order] = Field(description="当前快照中符合条件的订单；空数组仅表示未找到，不能推断从未提交或下单失败。")
    consistency: Literal["stable", "changing"] = Field(default="stable",
        description="stable=连续读取快照一致，不等于全部成交；changing=尚未确认快照稳定，可再次 GET 查询。")
    identity: OrderIdentity | None = Field(default=None,
        description="按完整引用查询时回显该身份；按 order_sys_id 查询时为 null，不影响编号查询结果。")


class TradesResult(Snapshot):
    trades: list[Trade]
    consistency: Literal["stable", "changing"] = "stable"


class PositionsResult(Snapshot):
    positions: list[Position]
    consistency: Literal["stable", "changing"] = "stable"


class BalanceResult(Snapshot):
    balance: Balance


class TradingStatusResult(ResultModel):
    request_id: str
    exchange_id: Exchange
    product_id: str
    observed_at: str
    is_trading: bool
    source: Literal["terminal_native"] = "terminal_native"


class Reply(ResultModel):
    request_id: str
    status: int = 200
    body: dict[str, JsonValue]
    blocked: bool = False
