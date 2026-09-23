"""少量离线市价契约检查；不启动 Wine，不连接交易账户。"""

import pytest

from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.models import LimitOrder, MarketOrder
from cn_futures_bridge.terminal.csv_data import order_row
from cn_futures_bridge.terminal.market import emulate_market
from cn_futures_bridge.terminal.tracking import ParkedSnapshot, validate_preorder
from cn_futures_bridge.terminal.native import InstrumentInfo
from cn_futures_bridge.terminal.orders import same_parameters


def request() -> MarketOrder:
    return MarketOrder(exchange_id="SHFE", instrument_id="ag2612", side="buy", offset="open", volume=1)


def test_market_emulation_uses_protective_limit_ioc() -> None:
    info = InstrumentInfo(found=True, data_ready=True, status=3, tick=1, lower=100, upper=120,
                          limit_min_volume=1, limit_max_volume=30)
    buy = emulate_market(request(), info)
    sell = emulate_market(request().model_copy(update={"side": "sell"}), info)
    assert buy.price == 120 and sell.price == 100
    assert buy.time_in_force == sell.time_in_force == "IOC"
    for modified, code in (({"status": 2}, "MARKET_NOT_TRADING"),
                           ({"upper": 0}, "SERVICE_NOT_READY"),
                           ({"tick": 0}, "SERVICE_NOT_READY"),
                           ({"limit_max_volume": 0}, "SERVICE_NOT_READY")):
        with pytest.raises(BridgeError) as error:
            emulate_market(request(), info.model_copy(update=modified))
        assert error.value.code == code
    with pytest.raises(BridgeError) as error:
        emulate_market(request().model_copy(update={"volume": 31}), info)
    assert error.value.code == "INVALID_ARGUMENTS"


def test_numeric_limit_ioc_must_match_price_and_conditions() -> None:
    order = LimitOrder.model_validate({**request().model_dump(), "price": 120, "time_in_force": "IOC"})
    row = {"id": 4, "instrument_hex": "ag2612".encode().hex(), "side": 0, "offset": 0, "hedge": 1,
           "volume": 1, "selector": 105, "time_condition": 1, "volume_condition": 1, "price": 120}
    validate_preorder(order, ParkedSnapshot.model_validate({"last_id": 4, "items": [row]}))
    close = order.model_copy(update={"side": "sell", "offset": "close"})
    validate_preorder(close, ParkedSnapshot.model_validate({"last_id": 4, "items": [{**row, "side": 1, "offset": 3}]}))
    for changes in ({"selector": 104}, {"time_condition": 3}, {"volume_condition": 3},
                    {"price": 119}, {"volume": 2}, {"offset": 1}):
        with pytest.raises(BridgeError) as error:
            validate_preorder(order, ParkedSnapshot.model_validate({"last_id": 4, "items": [{**row, **changes}]}))
        assert error.value.code == "TERMINAL_DATA_INVALID"
    with pytest.raises(BridgeError) as error:
        validate_preorder(order, ParkedSnapshot.model_validate({"last_id": 4, "items": [row, row]}))
    assert error.value.code == "OPERATION_STATUS_UNKNOWN"


def test_csv_market_price_is_not_a_numeric_limit() -> None:
    row = {"类型": "预埋单(手动)", "合约": "ag2612", "买卖": "买　", "开平": "开仓",
           "报单价格": "市价/19422", "报单手数": "1", "投保": "投机", "报单编号": "",
           "成交手数": "0", "未成交手数": "0", "挂单状态": "错单", "详细状态": "报单被拒绝",
           "交易所": "SHFE", "报单时间": ""}
    assert same_parameters(row, request())
    limit = LimitOrder.model_validate({**request().model_dump(), "price": 19422})
    assert not same_parameters(row, limit)
    parsed = order_row(row)
    assert parsed.price is None and parsed.status == "rejected" and parsed.order_id is None
    row["报单价格"] = "19422"
    assert not same_parameters(row, request()) and same_parameters(row, limit)
