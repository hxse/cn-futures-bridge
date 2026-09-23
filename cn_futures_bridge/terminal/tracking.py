"""实际发送引用、精确订单和成交关系；不按相似参数或快照差集认领订单。"""
from decimal import Decimal

from pydantic import BaseModel, Field, FiniteFloat

from ..errors import BridgeError
from ..models import LimitOrder
from ..results import Order, OrderIdentity
from .csv_data import order_row
from .native import NativeClient, Session, decode
from .policy import EXCHANGE_NUMBERS


class ParkedOrder(BaseModel):
    id: int = Field(gt=0)
    instrument_hex: str
    side: int
    offset: int
    hedge: int
    volume: int
    selector: int
    time_condition: int
    volume_condition: int
    price: FiniteFloat


class ParkedSnapshot(BaseModel):
    last_id: int = Field(ge=0)
    items: list[ParkedOrder]


def parked_snapshot(native: NativeClient, after: int = -1) -> ParkedSnapshot:
    return ParkedSnapshot.model_validate(native.ask(f"parked {after}").data)


def validate_preorder(request: LimitOrder, snapshot: ParkedSnapshot) -> int:
    if len(snapshot.items) != 1:
        raise BridgeError("OPERATION_STATUS_UNKNOWN", "不能唯一核对本次原生预埋记录", 502)
    row = snapshot.items[0]
    if (decode(row.instrument_hex) != request.instrument_id or row.volume != request.volume
            or row.side != (0 if request.side == "buy" else 1)
            or row.offset != (0 if request.offset == "open" else 3) or row.hedge != 1
            or Decimal(str(row.price)) != request.price or row.selector != 105
            or row.time_condition != (1 if request.time_in_force == "IOC" else 3) or row.volume_condition != 1):
        raise BridgeError("TERMINAL_DATA_INVALID", "原生预埋参数与本次请求不符", 502)
    return row.id


class Receipt(BaseModel):
    armed: bool
    active: int
    send_count: int
    count: int
    broken: bool
    valid: bool
    parked_id: int
    direction: int
    offset: int
    volume: int
    price: FiniteFloat
    time_condition: int
    selector: int
    instrument_hex: str
    order_ref_hex: str
    source_hex: str

    def identity(self, request: LimitOrder, parked_id: int, session: Session) -> OrderIdentity:
        if (self.armed or self.active or self.broken or not self.valid or self.send_count != 1 or self.count != 1
                or self.parked_id != parked_id or decode(self.source_hex) != f"p{parked_id}"
                or decode(self.instrument_hex) != request.instrument_id or self.volume != request.volume
                or self.direction != (0 if request.side == "buy" else 1)
                or self.offset != (0 if request.offset == "open" else 3)
                or Decimal(str(self.price)) != request.price or self.selector != 105
                or self.time_condition != (1 if request.time_in_force == "IOC" else 3)):
            raise BridgeError("OPERATION_STATUS_UNKNOWN", "实际发送引用缺失、重复或与本次参数不符；禁止重发", 502,
                              submission_status="unknown")
        return OrderIdentity(exchange_id=request.exchange_id, instrument_id=request.instrument_id,
                             trading_day=session.trading_day, front_id=session.front_id,
                             session_id=session.session_id, order_ref=decode(self.order_ref_hex))


class NativeOrder(BaseModel):
    front_id: int
    session_id: int
    exchange: int
    side: int
    offset: int
    hedge: int
    volume: int = Field(ge=0)
    filled: int = Field(ge=0)
    remaining: int = Field(ge=0)
    price: FiniteFloat
    instrument_hex: str
    order_ref_hex: str
    order_id_hex: str
    message_hex: str
    time_hex: str

    def order(self, identity: OrderIdentity) -> Order:
        if (self.front_id != identity.front_id or self.session_id != identity.session_id
                or self.exchange != EXCHANGE_NUMBERS[identity.exchange_id]
                or decode(self.instrument_hex) != identity.instrument_id
                or decode(self.order_ref_hex) != identity.order_ref or self.side not in (0, 1)):
            raise BridgeError("TERMINAL_DATA_INVALID", "原生订单不符合完整引用", 502)
        # 已核验普通投机开仓/平仓；未覆盖的内部开平编码不转换为 CTP 枚举。
        offset = {0: "开仓", 3: "平仓"}.get(self.offset, "未知")
        return order_row({"交易所": identity.exchange_id, "合约": identity.instrument_id,
            "买卖": "买" if self.side == 0 else "卖", "开平": offset,
            "报单编号": decode(self.order_id_hex).lstrip(" "), "报单手数": str(self.volume),
            "成交手数": str(self.filled), "未成交手数": str(self.remaining), "报单价格": str(self.price),
            "挂单状态": "", "详细状态": decode(self.message_hex), "报单时间": decode(self.time_hex)})


class TradeLink(BaseModel):
    trade_id_hex: str
    order_id_hex: str


class TrackedSnapshot(BaseModel):
    orders: list[NativeOrder]
    trades: list[TradeLink]


def track(native: NativeClient, identity: OrderIdentity) -> TrackedSnapshot:
    return TrackedSnapshot.model_validate(native.ask(
        f"track {identity.front_id} {identity.session_id} {identity.order_ref} "
        f"{EXCHANGE_NUMBERS[identity.exchange_id]} {identity.instrument_id}").data)
