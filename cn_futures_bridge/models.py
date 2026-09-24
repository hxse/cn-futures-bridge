"""八个业务动作共用的 Pydantic 输入，不在路由或执行器复制校验。"""

from decimal import Decimal
import math
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, WithJsonSchema, field_serializer, model_validator

Exchange = Literal["SHFE", "INE", "DCE", "CZCE", "CFFEX", "GFEX"]
Side = Literal["buy", "sell"]
Offset = Literal["open", "close", "close_today", "close_yesterday"]
Hedge = Literal["speculation", "arbitrage", "hedge"]
Instrument = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9]{0,79}$")]
Product = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,79}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=20, pattern=r"^[ -~]*[!-~][ -~]*$")]
InvestUnit = Annotated[str, Field(max_length=16, pattern=r"^[ -~]*$")]
TimeText = Annotated[str, Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$")]


def price_value(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("价格必须是有限 JSON 数值")
    try:
        if not math.isfinite(float(value)):
            raise ValueError("价格必须是有限 JSON 数值")
    except OverflowError as exc:
        raise ValueError("价格超出有限 JSON 数值范围") from exc
    result = Decimal(str(value))
    if result <= 0:
        raise ValueError("价格必须大于零")
    return result


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    mode: Literal["sandbox", "live"] = Field(default="sandbox", description="必须匹配启动环境；实盘须显式传 live，不触发账户切换。")


class MarketOrder(RequestModel):
    exchange_id: Exchange
    instrument_id: Instrument
    side: Side
    offset: Offset
    volume: Annotated[int, Field(ge=1, le=2147483647)]
    hedge_flag: Hedge = "speculation"
    invest_unit_id: InvestUnit = ""


class LimitOrder(MarketOrder):
    price: Annotated[Decimal, BeforeValidator(price_value), WithJsonSchema({"type": "number", "exclusiveMinimum": 0})] = Field(
        description="目标限价。CFB 修正极小浮点尾差、按买下卖上对齐 tick；允许范围内的买价超涨停/卖价低于跌停自动截断。实际价及原因见 execution。")
    time_in_force: Literal["GFD", "IOC", "FOK"] = "GFD"

    @field_serializer("price")
    def serialize_price(self, value: Decimal) -> float:
        return float(value)


class CancelBase(RequestModel):
    exchange_id: Exchange
    instrument_id: Instrument
    invest_unit_id: InvestUnit = ""


class CancelByExchange(CancelBase):
    by: Literal["exchange_order"]
    order_sys_id: Identifier


class CancelBySession(CancelBase):
    by: Literal["session_order"]
    front_id: Annotated[int, Field(ge=0, le=2147483647)]
    session_id: Annotated[int, Field(ge=-2147483648, le=2147483647)]
    order_ref: Annotated[str, Field(pattern=r"^[0-9]{1,12}$")]


CancelOrder = Annotated[CancelByExchange | CancelBySession, Field(discriminator="by")]


class PositionQuery(RequestModel):
    exchange_id: Exchange | None = None
    instrument_id: Instrument | None = None
    invest_unit_id: InvestUnit = ""


class OrderQuery(PositionQuery):
    order_sys_id: Identifier | None = Field(default=None,
        description="填下单响应的 order_id，保持字符串原样；建议同时带原交易所和合约。不能与完整引用字段组混用。",
        examples=["648294"])
    trading_day: Annotated[str, Field(pattern=r"^[0-9]{8}$")] | None = Field(default=None,
        description="填 identity.trading_day；只支持当前终端交易日。须同时传前置、会话、报单引用、交易所和合约。",
        examples=["20260924"])
    front_id: Annotated[int, Field(strict=False, ge=0, le=2147483647)] | None = Field(default=None,
        description="填 identity.front_id；仅用于完整引用查询，不能单独定位订单。", examples=[3])
    session_id: Annotated[int, Field(strict=False, ge=-2147483648, le=2147483647)] | None = Field(default=None,
        description="填 identity.session_id，允许负数；与其他完整引用字段一起传入。", examples=[123])
    order_ref: Annotated[str, Field(pattern=r"^[0-9]{1,12}$")] | None = Field(default=None,
        description="填 identity.order_ref，保留字符串及前导零；不是交易所订单号，必须与其余身份字段一起传入。",
        examples=["18"])
    insert_time_start: TimeText | None = None
    insert_time_end: TimeText | None = None

    @model_validator(mode="after")
    def ordered_interval(self) -> Self:
        reference = (self.trading_day, self.front_id, self.session_id, self.order_ref)
        if any(value is not None for value in reference):
            if any(value is None for value in reference) or not self.exchange_id or not self.instrument_id:
                raise ValueError("按引用查询须完整提供交易日、前置、会话、报单引用、交易所和合约")
            if self.order_sys_id is not None:
                raise ValueError("报单引用与交易所订单号不能混用")
        if self.insert_time_start and self.insert_time_end and self.insert_time_start > self.insert_time_end:
            raise ValueError("时间过滤起点不得晚于终点，不支持跨午夜区间")
        return self


class TradeQuery(PositionQuery):
    trade_id: Identifier | None = None
    trade_time_start: TimeText | None = None
    trade_time_end: TimeText | None = None

    @model_validator(mode="after")
    def ordered_interval(self) -> Self:
        if self.trade_time_start and self.trade_time_end and self.trade_time_start > self.trade_time_end:
            raise ValueError("时间过滤起点不得晚于终点，不支持跨午夜区间")
        return self


class BalanceQuery(RequestModel):
    currency_id: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "CNY"


class TradingStatusQuery(RequestModel):
    exchange_id: Exchange
    product_id: Product


Action = Literal["create_market_order", "create_limit_order", "cancel_order", "fetch_orders",
                 "fetch_trades", "fetch_positions", "fetch_balance", "fetch_trading_status"]


class Operation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Action
    parameters: dict[str, object]

    def request(self) -> RequestModel:
        models: dict[str, type[RequestModel]] = {
            "create_market_order": MarketOrder, "create_limit_order": LimitOrder,
            "fetch_orders": OrderQuery, "fetch_trades": TradeQuery,
            "fetch_positions": PositionQuery, "fetch_balance": BalanceQuery,
            "fetch_trading_status": TradingStatusQuery,
        }
        if self.action == "cancel_order":
            from pydantic import TypeAdapter
            return TypeAdapter(CancelOrder).validate_python(self.parameters)
        return models[self.action].model_validate(self.parameters)
