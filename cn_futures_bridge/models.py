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
    price: Annotated[Decimal, BeforeValidator(price_value), WithJsonSchema({"type": "number", "exclusiveMinimum": 0})]
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
    order_sys_id: Identifier | None = None
    insert_time_start: TimeText | None = None
    insert_time_end: TimeText | None = None

    @model_validator(mode="after")
    def ordered_interval(self) -> Self:
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
