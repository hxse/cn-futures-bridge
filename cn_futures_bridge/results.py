"""CFB 自己的必要返回字段，不伪造 CTP 回报。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, FiniteFloat, Field, JsonValue

from .models import Exchange, Side

ReturnedOffset = Literal["open", "close", "close_today", "close_yesterday", "unknown"]


class ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SubmissionResult(ResultModel):
    request_id: str
    submission_status: Literal["submitted"] = "submitted"
    order_id: str | None = None


class Order(ResultModel):
    order_id: str | None = None
    exchange_id: Exchange
    instrument_id: str
    side: Side
    offset: ReturnedOffset
    volume: int = Field(ge=0)
    filled_volume: int = Field(ge=0)
    remaining_volume: int = Field(ge=0)
    price: FiniteFloat | None = None
    status: Literal["pending", "open", "partially_filled", "filled", "cancelled", "rejected", "unknown"]
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


class Snapshot(ResultModel):
    request_id: str
    observed_at: str
    source: Literal["terminal_csv", "terminal_text"]
    trading_day: str | None = None


class OrdersResult(Snapshot):
    orders: list[Order]


class TradesResult(Snapshot):
    trades: list[Trade]


class PositionsResult(Snapshot):
    positions: list[Position]


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
