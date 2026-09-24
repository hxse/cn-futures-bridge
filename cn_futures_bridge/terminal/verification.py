"""实际引用建立身份，CSV 核对确定订单及成交；观察不触发任何补单。"""
from collections.abc import Callable
import logging
import time
from typing import Literal

from pydantic import BaseModel, Field

from ..config import ExecutionConfig
from ..errors import BridgeError
from ..models import CancelByExchange, MarketOrder, OrderQuery, PositionQuery
from ..results import Order, OrderIdentity, Position, Trade, Verification
from .csv_data import matches, order_row, position_row, trade_row
from .native import decode
from .orders import ReadTable
from .steps import Steps
from .tracking import TrackedSnapshot

LOG = logging.getLogger(__name__)
TERMINAL = {"filled", "cancelled", "rejected"}
Row = Order | Trade | Position


class AccountSnapshot(BaseModel):
    orders: list[Order] = Field(default_factory=list)
    trades: list[Trade] = Field(default_factory=list)
    positions: list[Position] = Field(default_factory=list)


def same_offset(request: MarketOrder, offset: str) -> bool:
    return offset == request.offset or (request.offset == "close" and offset in ("close_today", "close_yesterday"))


def business_signature(rows: list[Row]) -> list[str]:
    return sorted(row.model_dump_json(exclude={"profit", "margin", "average_price"}
                  if isinstance(row, Position) else set()) for row in rows)


def exact_order(identity: OrderIdentity, tracked: TrackedSnapshot, csv_orders: list[Order]) -> tuple[list[Order], bool]:
    if len(tracked.orders) > 1:
        raise BridgeError("ORDER_IDENTITY_AMBIGUOUS", "完整会话引用对应多张原生订单", 409)
    if not tracked.orders:
        return [], False
    native = tracked.orders[0].order(identity)
    if not native.order_id:
        return [native], native.status == "rejected"
    selected = [row for row in csv_orders if row.exchange_id == identity.exchange_id
                and row.instrument_id == identity.instrument_id and row.order_id == native.order_id]
    if len(selected) > 1:
        raise BridgeError("ORDER_IDENTITY_AMBIGUOUS", "交易所订单编号对应多条 CSV 记录", 409)
    if not selected:
        return [native], False
    row = selected[0]
    if (row.volume != native.volume or row.side != native.side or row.price != native.price
            or (native.offset != "unknown" and row.offset != native.offset)):
        raise BridgeError("TERMINAL_DATA_INVALID", "精确编号对应的 CSV 与原生订单参数冲突", 502)
    return selected, True


class CsvVerifier:
    def __init__(self, read: ReadTable, config: ExecutionConfig, lookup: Callable[[OrderIdentity], TrackedSnapshot]):
        self.read, self.config, self.lookup = read, config, lookup

    def pause(self) -> None:
        time.sleep(self.config.csv_confirmation_interval_ms / 1000)

    def query(self, table: str, request: PositionQuery, steps: Steps) -> tuple[list[Row], Literal["stable", "changing"]]:
        parser = {"orders": order_row, "trades": trade_row, "positions": position_row}[table]
        previous: list[str] | None = None
        latest: list[Row] = []
        for attempt in range(self.config.csv_confirmation_attempts):
            if attempt:
                self.pause()
            latest = [row for row in (parser(r) for r in self.read(table, steps)) if matches(row, request)]
            signature = business_signature(latest)
            LOG.info("CSV 查询核对：表=%s，轮次=%s，记录=%s，稳定=%s", table, attempt+1, len(latest), signature == previous,
                     extra={"request_id": steps.request_id, "step": "verify_snapshot"})
            if signature == previous:
                return latest, "stable"
            previous = signature
        return latest, "changing"

    def query_reference(self, request: OrderQuery, identity: OrderIdentity, steps: Steps
                        ) -> tuple[list[Order], Literal["stable", "changing"]]:
        previous: list[str] | None = None
        latest: list[Order] = []
        for attempt in range(self.config.csv_confirmation_attempts):
            if attempt:
                self.pause()
            tracked = self.lookup(identity)
            rows = [order_row(row) for row in self.read("orders", steps)]
            latest, complete = exact_order(identity, tracked, rows)
            latest = [row for row in latest if matches(row, request)]
            signature = [row.model_dump_json() for row in latest]
            if signature == previous and (complete or not tracked.orders):
                return latest, "stable"
            previous = signature
        return latest, "changing"

    def snapshot(self, request: MarketOrder | CancelByExchange, steps: Steps, *, before: bool = False) -> AccountSnapshot:
        def relevant(row: Order | Trade | Position) -> bool:
            return row.exchange_id == request.exchange_id and row.instrument_id == request.instrument_id
        # 报单身份来自实际引用；前置委托/成交差集已不参与确认。
        orders = [] if before else [order_row(row) for row in self.read("orders", steps)]
        trades = ([trade_row(row) for row in self.read("trades", steps)]
                  if not before and isinstance(request, MarketOrder) else [])
        positions = [position_row(row) for row in self.read("positions", steps)]
        return AccountSnapshot(orders=[row for row in orders if relevant(row)],
                               trades=[row for row in trades if relevant(row)],
                               positions=[row for row in positions if relevant(row)])

    def compare(self, request: MarketOrder | CancelByExchange, before: AccountSnapshot, after: AccountSnapshot,
                steps: Steps, attempt: int, tracked: TrackedSnapshot | None) -> Verification:
        result = Verification(attempts=attempt, positions_before=before.positions, positions_after=after.positions)
        if isinstance(request, CancelByExchange):
            result.correlation = "order_id"
            result.orders = [row for row in after.orders if row.order_id == request.order_sys_id]
            result.status = "observed" if len(result.orders) == 1 and result.orders[0].status in ("cancelled", "filled") else "pending"
            return result
        assert steps.identity is not None and steps.execution is not None and tracked is not None
        result.source = "terminal_csv_and_native"
        result.orders, complete = exact_order(steps.identity, tracked, after.orders)
        if not result.orders:
            return result
        order = result.orders[0]
        if (order.volume != request.volume or order.price != steps.execution.price or order.side != request.side
                or (order.offset != "unknown" and not same_offset(request, order.offset))):
            raise BridgeError("TERMINAL_DATA_INVALID", "绑定订单与请求参数冲突", 502)
        links = {decode(link.trade_id_hex).lstrip(" "): decode(link.order_id_hex).lstrip(" ") or None
                 for link in tracked.trades}
        result.trades = [row.model_copy(update={"order_id": links[row.trade_id]})
                         for row in after.trades if row.trade_id in links]
        if (len(links) != len(tracked.trades) or len({row.trade_id for row in result.trades}) != len(result.trades)
                or any(row.order_id != order.order_id or row.side != request.side or not same_offset(request, row.offset)
                       for row in result.trades)):
            raise BridgeError("TERMINAL_DATA_INVALID", "已绑定成交的编号或交易身份冲突", 502)
        trades_ready = len(result.trades) == len(links) and sum(row.volume for row in result.trades) == order.filled_volume
        final = order.status in TERMINAL or (steps.execution.time_in_force == "GFD" and order.status in ("open", "partially_filled"))
        result.status = "observed" if complete and final and trades_ready else "pending"
        return result

    def observe(self, request: MarketOrder | CancelByExchange, before: AccountSnapshot, steps: Steps) -> Verification:
        is_new = isinstance(request, MarketOrder)
        result = Verification(positions_before=before.positions, correlation="order_ref" if is_new else "order_id",
                              source="terminal_csv_and_native" if is_new else "terminal_csv")
        for attempt in range(1, self.config.csv_confirmation_attempts+1):
            if attempt > 1:
                self.pause()
            try:
                tracked = None
                if is_new:
                    assert steps.identity is not None
                    with steps.step("track_order"):
                        tracked = self.lookup(steps.identity)
                    if len(tracked.orders) == 1:
                        identifier = tracked.orders[0].order(steps.identity).order_id
                        if identifier and identifier != steps.order_id:
                            if steps.order_id:
                                raise BridgeError("ORDER_IDENTITY_AMBIGUOUS", "绑定引用的交易所订单号发生冲突", 409)
                            steps.order_id = identifier
                            steps.save("order_identified")
                after = self.snapshot(request, steps)
                result = self.compare(request, before, after, steps, attempt, tracked)
            except BridgeError as exc:
                if exc.code not in ("TERMINAL_DATA_INVALID", "ORDER_IDENTITY_AMBIGUOUS"):
                    raise
                result.status = "ambiguous" if exc.code == "ORDER_IDENTITY_AMBIGUOUS" else "unavailable"
                result.attempts, result.error_code = attempt, exc.code
                return result
            LOG.info("精确订单确认：%s", result.model_dump_json(),
                     extra={"request_id": steps.request_id, "step": "verify_execution"})
            if result.status == "observed":
                return result
        return result
