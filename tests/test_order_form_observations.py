"""复用窗口出现的观察，操作后的状态仍须重新核对；全部离线。"""
from decimal import Decimal
from pathlib import Path

import pytest

from cn_futures_bridge.config import BridgeConfig, Settings
from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.logging_store import LogStore
from cn_futures_bridge.models import LimitOrder
from cn_futures_bridge.terminal.gui import Gui
from cn_futures_bridge.terminal.native import NativeClient, NativeReply, Window, Windows
from cn_futures_bridge.terminal.order_form import OrderForm, TimeMode


def window(hwnd: int, kind: str, text: str = "", identifier: int = 0, parent: int = 1,
           root: int = 1, checked: int = 0) -> Window:
    return Window(hwnd=hwnd, parent=parent, root=root, id=identifier, class_name=kind,
                  text_hex=text.encode("gb18030").hex(), checked=checked, visible=True, enabled=True,
                  password=False, rect=(0, 0, 100, 20), items_hex=[])


@pytest.fixture
def form(tmp_path: Path) -> OrderForm:
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    return OrderForm(Gui(NativeClient(settings, LogStore(settings)), settings))


def menu_fixture(form: OrderForm, monkeypatch: pytest.MonkeyPatch, target: TimeMode, *, invalid: str = "",
                 stuck: bool = False):
    visible = [False]
    mode = ["GFD" if target == "FAK" else "FAK"]
    observations: list[bool] = []
    keys: list[tuple[str, ...]] = []
    def read() -> Windows:
        observations.append(visible[0])
        menus = [window(10, "#32768", root=10)] if visible[0] else []
        return Windows(windows=menus, focus=10 if visible[0] else 1, flags=4 if visible[0] else 0)
    def click(*args: str) -> str:
        visible[0] = True
        return ""
    def key(*values: str) -> None:
        keys.append(values)
        if values == ("Escape",):
            visible[0] = False
        elif values[-1] == "Return" and not stuck:
            visible[0] = False
            mode[0] = target
    def reply(command: str) -> NativeReply:
        assert command == "menu 10"
        return NativeReply(error=0, done=True, data={"items": [
            {"index": index, "command_id": 40000+index+int(invalid == "command" and value == target),
             "enabled": not (invalid == "disabled" and value == target),
             "text_hex": (value+":有效期").encode("gb18030").hex()}
            for index, value in enumerate(("GFD", "GIS", "FAK", "FOK"))]})
    monkeypatch.setattr(form, "control", lambda identifier, kind: window(2, "Static", mode[0], 1472))
    monkeypatch.setattr(form.gui, "activate", lambda: None)
    monkeypatch.setattr(form.gui, "_xdo", click)
    monkeypatch.setattr(form.gui, "key", key)
    monkeypatch.setattr(form.gui.native, "windows", read)
    monkeypatch.setattr(form.gui.native, "ask", reply)
    form.gui.timeout = .01
    form.gui.interval = .001
    return observations, keys, mode


@pytest.mark.parametrize("target", ["FAK", "GFD"])
def test_menu_reuses_opening_but_observes_closure(form: OrderForm, monkeypatch: pytest.MonkeyPatch,
                                               target: TimeMode) -> None:
    observations, keys, mode = menu_fixture(form, monkeypatch, target)
    form.time_mode(target)
    assert observations == [True, False] and mode == [target]
    expected = ("Home", "Down", "Down", "Return") if target == "FAK" else ("Home", "Return")
    assert keys == [expected]


@pytest.mark.parametrize("invalid", ["disabled", "command"])
def test_invalid_menu_item_is_not_selected(form: OrderForm, monkeypatch: pytest.MonkeyPatch, invalid: str) -> None:
    observations, keys, mode = menu_fixture(form, monkeypatch, "FAK", invalid=invalid)
    with pytest.raises(BridgeError) as error:
        form.time_mode("FAK")
    assert error.value.code == "TERMINAL_DATA_INVALID"
    assert observations == [True, True] and keys == [("Escape",)] and mode == ["GFD"]


def test_menu_closing_does_not_replace_value_readback(form: OrderForm, monkeypatch: pytest.MonkeyPatch) -> None:
    observations, keys, _ = menu_fixture(form, monkeypatch, "FAK")
    monkeypatch.setattr(form, "control", lambda identifier, kind: window(2, "Static", "GFD", 1472))
    with pytest.raises(BridgeError) as error:
        form.time_mode("FAK")
    assert error.value.code == "TERMINAL_DATA_INVALID"
    assert observations == [True, False] and keys == [("Home", "Down", "Down", "Return")]


def test_unclosed_menu_still_gets_bounded_cleanup(form: OrderForm, monkeypatch: pytest.MonkeyPatch) -> None:
    observations, keys, mode = menu_fixture(form, monkeypatch, "FAK", stuck=True)
    with pytest.raises(BridgeError) as error:
        form.time_mode("FAK")
    assert error.value.code == "QUERY_TIMEOUT"
    assert len(observations) >= 3 and all(observations)
    assert keys == [("Home", "Down", "Down", "Return"), ("Escape",)] and mode == ["GFD"]


def test_selected_radio_needs_no_second_read_without_action(form: OrderForm, monkeypatch: pytest.MonkeyPatch) -> None:
    values = iter([window(2, "Button", "买入", 3310, checked=1)])
    monkeypatch.setattr(form, "control", lambda identifier, kind: next(values))
    monkeypatch.setattr(form, "focus", lambda target: pytest.fail("已选项不应再操作"))
    form.radio(3310, "买入")


@pytest.mark.parametrize("checked", [0, 1])
def test_changed_radio_requires_new_read(form: OrderForm, monkeypatch: pytest.MonkeyPatch, checked: int) -> None:
    values = iter([window(2, "Button", "卖出", 3311), window(2, "Button", "卖出", 3311, checked=checked)])
    keys: list[str] = []
    monkeypatch.setattr(form, "control", lambda identifier, kind: next(values))
    monkeypatch.setattr(form, "focus", lambda target: None)
    monkeypatch.setattr(form.gui, "key", keys.append)
    if checked:
        form.radio(3311, "卖出")
    else:
        with pytest.raises(BridgeError) as error:
            form.radio(3311, "卖出")
        assert error.value.code == "TERMINAL_DATA_INVALID"
    assert keys == ["space"]


def test_preorder_reuses_opening_but_confirmation_rereads(form: OrderForm, monkeypatch: pytest.MonkeyPatch) -> None:
    root = window(1, "main", "快期2-CTP-上期技术-电信2", parent=0)
    dialog = window(2, "#32770", "设置触发条件", root=2)
    button = window(3, "Button", "预埋/条件", 3324)
    labels = {1: "确定", 2: "取消", 1278: "预埋(本地)，手动发出",
              1279: "预埋(本地)，当重新进入交易状态时", 1280: "条件(本地)，当行情满足以下条件时"}
    children = [window(key+10, "Button", value, key, parent=2, root=2, checked=int(key == 1278))
                for key, value in labels.items()]
    # 出现时未读业务参数；确认阶段的新摘要已经变成另一张指令。
    summary = window(4, "Static", "m2705 买 开仓2手,价格:3206", 4220, parent=2, root=2)
    snapshots = iter([Windows(windows=[root, dialog], focus=2, flags=0),
                      Windows(windows=[root, dialog, *children, summary], focus=2, flags=0)])
    keys: list[str] = []
    monkeypatch.setattr(form, "control", lambda identifier, kind: button)
    monkeypatch.setattr(form, "focus", lambda target: None)
    monkeypatch.setattr(form.gui, "key", keys.append)
    monkeypatch.setattr(form.gui, "managed_snapshot", lambda: next(snapshots))
    assert form.open_preorder() == dialog
    request = LimitOrder(exchange_id="DCE", instrument_id="m2701", side="buy", offset="open",
                         volume=1, price=Decimal(3206), time_in_force="IOC")
    with pytest.raises(BridgeError) as error:
        form.confirm_preorder(dialog, request)
    assert error.value.code == "TERMINAL_DATA_INVALID" and keys == ["space"]
