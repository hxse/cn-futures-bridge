"""候选 CSV 只用于定位，发送必须经过选中后的新身份核对。"""
from decimal import Decimal
from pathlib import Path

import pytest

from cn_futures_bridge.config import BridgeConfig, Settings
from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.journal import Journal
from cn_futures_bridge.logging_store import LogStore
from cn_futures_bridge.models import LimitOrder, Operation
from cn_futures_bridge.terminal.csv_data import PREORDER_HEADER
from cn_futures_bridge.terminal.gui import Gui
from cn_futures_bridge.terminal.native import NativeClient, Window
from cn_futures_bridge.terminal.orders import OrderActions
from cn_futures_bridge.terminal.steps import Steps


def row(contract: str = "m2701") -> dict[str, str]:
    return dict(zip(PREORDER_HEADER, ["预埋单(手动)", "未启动", "手动发出", contract,
        "豆粕", "买　", "开仓", "3206", "1", "投机", "13:00:00", ""]))


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    journal = Journal(settings)
    request = LimitOrder(exchange_id="DCE", instrument_id="m2701", side="buy", offset="open", volume=1, price=Decimal(3206))
    journal.admit("cfb-selection", Operation(action="create_limit_order", parameters=request.model_dump(mode="json")), None)
    steps = Steps("cfb-selection", "create_limit_order", journal, tmp_path)
    steps.owned_row = row()
    gui = Gui(NativeClient(settings, LogStore(settings)), settings)
    target = Window(hwnd=3, parent=2, root=1, checked=0, id=4201, class_name="ListCtrl", visible=True,
                    enabled=True, password=False, rect=(0, 0, 100, 20), text_hex="", items_hex=[])
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(gui, "grid", lambda table: target)
    monkeypatch.setattr(gui, "select", lambda table, window, index: events.append(("select", index)))
    monkeypatch.setattr(gui.native, "select", lambda window, index: events.append(("reselect", index)))
    monkeypatch.setattr(gui, "key", lambda key: events.append(("key", key)))
    monkeypatch.setattr(gui.native, "ask", lambda command: pytest.fail("身份未核对前不得进入发送观察"))
    return gui, request, steps, events


def test_candidate_still_requires_fresh_csv_after_selection(setup):
    gui, _, steps, events = setup
    def read(table, context):
        assert events == [("select", 0)]
        events.append(("read", table))
        return [row()]
    actions = OrderActions(gui, read)
    actions.select_local(steps, [row()])
    assert events == [("select", 0), ("read", "preorders"), ("reselect", 0)]


@pytest.mark.parametrize("fresh", [
    [], [row("m2705"), row()], [row(), row()], [{**row(), "报单手数": "2"}],
])
def test_changed_candidate_cannot_send(setup, fresh):
    gui, request, steps, events = setup
    actions = OrderActions(gui, lambda table, context: fresh)
    with pytest.raises(BridgeError) as error:
        actions.send_local(request, steps, [row()])
    assert error.value.code == "ORDER_IDENTITY_AMBIGUOUS"
    assert events == [("select", 0)]


def test_missing_fresh_csv_cannot_send(setup):
    gui, request, steps, events = setup
    def read(table, context):
        raise BridgeError("TERMINAL_DATA_INVALID", "CSV 读取失败", 502)
    with pytest.raises(BridgeError) as error:
        OrderActions(gui, read).send_local(request, steps, [row()])
    assert error.value.code == "TERMINAL_DATA_INVALID"
    assert events == [("select", 0)]


def test_cleanup_reads_its_own_current_candidate(setup):
    gui, _, steps, events = setup
    sent = {**row(), "状态": "已发送"}
    snapshots = iter([[row("m2705"), sent], [row("m2705"), sent], [row("m2705")]])
    def read(table, context):
        events.append(("read", table))
        return next(snapshots)
    OrderActions(gui, read).cleanup(steps)
    assert events == [("read", "preorders"), ("select", 1), ("read", "preorders"),
                      ("reselect", 1), ("key", "alt+x"), ("read", "preorders")]
    assert steps.owned_row is None
