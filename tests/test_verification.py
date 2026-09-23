"""精确引用、成交关联、延迟与缺失证据；全部离线。"""
from decimal import Decimal
from pathlib import Path

import pytest

from cn_futures_bridge.config import BridgeConfig, ExecutionConfig, Settings
from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.journal import Journal
from cn_futures_bridge.models import CancelByExchange, LimitOrder, Operation, PositionQuery
from cn_futures_bridge.results import OrderExecution, OrderIdentity
from cn_futures_bridge.terminal.csv_data import order_row, position_row, trade_row
from cn_futures_bridge.terminal.native import Session
from cn_futures_bridge.terminal.steps import Steps
from cn_futures_bridge.terminal.tracking import Receipt, TrackedSnapshot
from cn_futures_bridge.terminal.verification import AccountSnapshot, CsvVerifier, exact_order


def hexed(value: str) -> str:
    return value.encode("gb18030").hex()


def identity() -> OrderIdentity:
    return OrderIdentity(exchange_id="DCE", instrument_id="m2701", trading_day="20260924",
                         front_id=3, session_id=-12, order_ref="18")


def request(volume: int = 1) -> LimitOrder:
    return LimitOrder(exchange_id="DCE", instrument_id="m2701", side="buy", offset="open",
                      volume=volume, price=Decimal(3618), time_in_force="IOC")


def order(identifier: str, *, filled: int = 0, total: int = 1, state: str = "未成交") -> dict[str, str]:
    return {"交易所": "DCE", "合约": "m2701", "买卖": "买　", "开平": "开仓",
            "报单编号": identifier, "报单手数": str(total), "成交手数": str(filled),
            "未成交手数": str(total-filled), "报单价格": "3618", "挂单状态": state,
            "详细状态": "全部成交报单已提交" if filled == total else "报单已提交", "报单时间": "10:09:15"}


def tracked(identifier: str = "124", *, filled: int = 0, message: str = "报单已提交",
            trade_id: str | None = None) -> TrackedSnapshot:
    row = {"front_id": 3, "session_id": -12, "exchange": 2, "side": 0, "offset": 0, "hedge": 1,
           "volume": 1, "filled": filled, "remaining": 1-filled, "price": 3618,
           "instrument_hex": hexed("m2701"), "order_ref_hex": hexed("18"),
           "order_id_hex": hexed("      "+identifier if identifier else ""),
           "message_hex": hexed(message), "time_hex": hexed("10:09:15")}
    links = [{"trade_id_hex": hexed("    "+trade_id), "order_id_hex": hexed("      "+identifier)}] if trade_id else []
    return TrackedSnapshot.model_validate({"orders": [row], "trades": links})


def position(profit: str, volume: str = "1") -> dict[str, str]:
    return {"交易所": "DCE", "合约": "m2701", "买卖": "买　", "投保": "投机", "总持仓": volume,
            "今仓": volume, "昨仓": "0", "可平量": volume, "持仓均价": "3412", "持仓盈亏": profit}


def steps(tmp_path: Path) -> Steps:
    journal = Journal(Settings(bridge=BridgeConfig(data_dir=tmp_path)))
    journal.admit("cfb-test", Operation(action="create_limit_order", parameters=request().model_dump(mode="json")), None)
    value = Steps("cfb-test", "create_limit_order", journal, tmp_path)
    value.execution = OrderExecution(kind="limit", price=3618, time_in_force="IOC")
    value.identity = identity()
    return value


def test_actual_receipt_requires_exactly_one_matching_send() -> None:
    data = {"armed": False, "active": 0, "send_count": 1, "count": 1, "broken": False, "valid": True,
            "parked_id": 1, "direction": 0, "offset": 0, "volume": 1, "price": 3618,
            "time_condition": 1, "selector": 105, "instrument_hex": hexed("m2701"),
            "order_ref_hex": hexed("18"), "source_hex": hexed("p1")}
    session = Session(connected=True, trade_connected=True, market_connected=True, identity_match=True,
                      trading_day="20260924", front_id=3, session_id=-12, status_bound=True, login_generation=0)
    assert Receipt.model_validate(data).identity(request(), 1, session) == identity()
    for change in ({"count": 0}, {"count": 2}, {"send_count": 2}, {"broken": True},
                   {"parked_id": 2}, {"volume": 2}, {"source_hex": hexed("a0")}):
        with pytest.raises(BridgeError) as error:
            Receipt.model_validate({**data, **change}).identity(request(), 1, session)
        assert error.value.code == "OPERATION_STATUS_UNKNOWN"


def test_same_parameters_are_not_an_order_identity() -> None:
    rows = [order_row(order("123", filled=1)), order_row(order("124"))]
    selected, ready = exact_order(identity(), tracked(), rows)
    assert ready and [row.order_id for row in selected] == ["124"]
    missing, ready = exact_order(identity(), TrackedSnapshot(orders=[], trades=[]), rows)
    assert missing == [] and not ready
    mismatch = tracked().model_copy(deep=True)
    mismatch.orders[0].session_id = 99
    with pytest.raises(BridgeError) as error:
        exact_order(identity(), mismatch, rows)
    assert error.value.code == "TERMINAL_DATA_INVALID"


def test_confirmation_waits_for_exact_csv_and_related_trade(tmp_path: Path) -> None:
    trade = {"交易所": "DCE", "合约": "m2701", "买卖": "买", "开平": "开仓", "成交编号": "201",
             "成交价格": "3412", "成交手数": "1", "成交时间": "10:09:15"}
    rounds = iter([[], [order("124", filled=1)]])
    def read(table: str, context: Steps) -> list[dict[str, str]]:
        return next(rounds) if table == "orders" else [trade] if table == "trades" else []
    verifier = CsvVerifier(read, ExecutionConfig(csv_confirmation_interval_ms=10),
                           lambda key: tracked(filled=1, trade_id="201"))
    context = steps(tmp_path)
    result = verifier.observe(request(), AccountSnapshot(), context)
    assert result.status == "observed" and result.attempts == 2 and context.order_id == "124"
    assert result.correlation == "order_ref" and result.orders[0].status == "filled"
    assert result.trades[0].order_id == "124" and result.positions_after == []
    after = AccountSnapshot(orders=[order_row(order("124", filled=1))], trades=[trade_row({**trade, "成交编号": "202"})])
    delayed = verifier.compare(request(), AccountSnapshot(), after, context, 1, tracked(filled=1, trade_id="201"))
    assert delayed.status == "pending" and delayed.trades == []


def test_rejection_without_exchange_number_is_still_bound(tmp_path: Path) -> None:
    rejected = tracked("", message="下单失败: CTP:平仓量超过持仓量")
    verifier = CsvVerifier(lambda table, context: [], ExecutionConfig(), lambda key: rejected)
    result = verifier.observe(request(), AccountSnapshot(), steps(tmp_path))
    assert result.status == "observed" and result.orders[0].status == "rejected"
    assert result.orders[0].order_id is None and result.correlation == "order_ref"


def test_cancel_keeps_exact_target_and_does_not_claim_other_trades(tmp_path: Path) -> None:
    verifier = CsvVerifier(lambda table, context: [], ExecutionConfig(), lambda key: tracked())
    cancel = CancelByExchange(by="exchange_order", exchange_id="DCE", instrument_id="m2701", order_sys_id="126")
    context = steps(tmp_path)
    result = verifier.compare(cancel, AccountSnapshot(), AccountSnapshot(), context, 1, None)
    assert result.status == "pending" and result.correlation == "order_id"
    result = verifier.compare(cancel, AccountSnapshot(), AccountSnapshot(orders=[order_row(order("126", filled=1))]),
                              context, 2, None)
    assert result.status == "observed" and result.orders[0].status == "filled"


def test_query_stability_and_missing_csv(tmp_path: Path) -> None:
    context = steps(tmp_path)
    values = iter([[position("10")], [position("20")]])
    verifier = CsvVerifier(lambda table, context: next(values), ExecutionConfig(csv_confirmation_interval_ms=10),
                           lambda key: tracked())
    rows, consistency = verifier.query("positions", PositionQuery(), context)
    assert consistency == "stable" and rows[0].model_dump()["profit"] == 20
    def broken(table: str, context: Steps) -> list[dict[str, str]]:
        raise BridgeError("TERMINAL_DATA_INVALID", "CSV 不完整", 502)
    verifier = CsvVerifier(broken, ExecutionConfig(), lambda key: tracked())
    with pytest.raises(BridgeError) as error:
        verifier.query("positions", PositionQuery(), context)
    assert error.value.code == "TERMINAL_DATA_INVALID"
    result = verifier.observe(request(), AccountSnapshot(), context)
    assert result.status == "unavailable" and result.positions_after is None
    for row in ({**position("10"), "可平量": "2"}, {**position("10"), "昨仓": "1"}):
        with pytest.raises(BridgeError) as error:
            position_row(row)
        assert error.value.code == "TERMINAL_DATA_INVALID"
