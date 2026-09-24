"""轻量就绪与有界恢复的离线契约；窗口来源由独立原生夹具验证。"""

from itertools import chain, repeat
from pathlib import Path

import pytest

from cn_futures_bridge.config import BridgeConfig, Settings
from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.logging_store import LogStore
from cn_futures_bridge.terminal.gui import Gui
from cn_futures_bridge.terminal.native import GuiState, NativeClient, NativeReply, Window, Windows
from cn_futures_bridge.terminal.order_form import FormState, OrderForm


def healthy() -> GuiState:
    return GuiState(main=1, main_count=1, enabled=True, focus=1, flags=0, capture=0,
                    menu_owned=False, modifiers=0, dialogs=0, unknown_dialogs=0,
                    funds=0, funds_count=0, document_pending=False)


@pytest.fixture
def gui(tmp_path: Path) -> Gui:
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    return Gui(NativeClient(settings, LogStore(settings)), settings)


def feed(gui: Gui, monkeypatch: pytest.MonkeyPatch, states: list[GuiState]) -> list[str]:
    replies = chain(states, repeat(states[-1]))
    commands: list[str] = []
    def answer(command: str) -> NativeReply:
        commands.append(command)
        if command == "gui_state":
            return NativeReply(error=0, done=True, data=next(replies).model_dump(mode="json"))
        assert command.startswith("recover_gui "), command
        return NativeReply(error=0, done=True, data={"action": "posted"})
    monkeypatch.setattr(gui.native, "ask", answer)
    return commands


def test_clean_check_does_not_scan_controls_type_or_sleep(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    commands = feed(gui, monkeypatch, [healthy()])
    monkeypatch.setattr(gui, "_xdo", lambda *args: pytest.fail("正常后台检查不应操作键盘"))
    monkeypatch.setattr("cn_futures_bridge.terminal.gui.time.sleep", lambda delay: pytest.fail("正常检查不应等待"))
    gui.ensure_ready()
    assert commands == ["gui_state"]


@pytest.mark.parametrize("change,target", [
    ({"funds": 2, "funds_count": 1, "dialogs": 1, "enabled": False}, 2),
    ({"flags": 4, "menu_owned": True, "capture": 1}, 1),
])
def test_recovery_waits_for_fact_and_posts_once(gui: Gui, monkeypatch: pytest.MonkeyPatch,
                                             change: dict, target: int) -> None:
    pending = healthy().model_copy(update=change)
    commands = feed(gui, monkeypatch, [pending, pending, healthy()])
    sleeps: list[float] = []
    monkeypatch.setattr("cn_futures_bridge.terminal.gui.time.sleep", sleeps.append)
    gui.ensure_ready()
    assert commands == ["gui_state", f"recover_gui {target}", "gui_state", "gui_state"]
    assert sleeps == [gui.interval]


@pytest.mark.parametrize("change", [
    {"dialogs": 1, "unknown_dialogs": 1}, {"main_count": 2}, {"enabled": False},
    {"flags": 4, "menu_owned": False}, {"capture": 9}, {"flags": 2},
])
def test_unowned_state_blocks_without_recovery(gui: Gui, monkeypatch: pytest.MonkeyPatch, change: dict) -> None:
    commands = feed(gui, monkeypatch, [healthy().model_copy(update=change)])
    with pytest.raises(BridgeError) as error:
        gui.ensure_ready()
    assert error.value.code == "GUI_RESET_FAILED" and commands == ["gui_state"]


def test_empty_title_dialog_is_not_filtered(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Window(hwnd=1, parent=0, root=1, checked=0, id=0, class_name="main", visible=True,
                  enabled=True, password=False, rect=(0, 0, 800, 600), items_hex=[],
                  text_hex="快期2-CTP-上期技术-电信2".encode("gb18030").hex())
    empty = root.model_copy(update={"hwnd": 2, "root": 2, "parent": 1, "class_name": "#32770", "text_hex": ""})
    snapshot = Windows(windows=[root, empty], focus=1, flags=0)
    monkeypatch.setattr(gui.native, "managed_windows", lambda: snapshot)
    commands = feed(gui, monkeypatch, [healthy().model_copy(update={"dialogs": 1, "unknown_dialogs": 1})])
    assert gui.dialogs(snapshot) == [empty]
    with pytest.raises(BridgeError) as error:
        gui.baseline()
    assert error.value.code == "GUI_RESET_FAILED" and commands == ["gui_state"]


def test_stuck_recovery_times_out_without_reposting(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    gui.timeout = .005
    gui.interval = .001
    pending = healthy().model_copy(update={"funds": 2, "funds_count": 1, "dialogs": 1, "enabled": False})
    commands = feed(gui, monkeypatch, [pending])
    with pytest.raises(BridgeError) as error:
        gui.ensure_ready()
    assert error.value.code == "GUI_RESET_FAILED"
    assert commands.count("recover_gui 2") == 1 and commands.count("gui_state") >= 2


def test_modifiers_only_released_for_keyboard_actions(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    pressed = healthy().model_copy(update={"modifiers": 1})
    keys: list[tuple[str, ...]] = []
    def xdo(*args: str) -> str:
        keys.append(args)
        return ""
    monkeypatch.setattr(gui, "_xdo", xdo)
    commands = feed(gui, monkeypatch, [pressed])
    gui.ensure_ready()
    assert not keys and commands == ["gui_state"]
    commands = feed(gui, monkeypatch, [pressed, healthy()])
    gui.ensure_ready(keyboard=True)
    assert len(keys) == 1 and keys[0][0] == "keyup" and commands == ["gui_state", "gui_state"]
    gui.native.poisoned = True
    commands.clear();keys.clear()
    with pytest.raises(BridgeError) as error:
        gui.finish()
    assert error.value.code == "GUI_UNRESPONSIVE" and not commands and not keys


def test_form_restore_skips_same_fields_but_rereads_after_instrument(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    form = OrderForm(gui)
    state = FormState(instrument="m2701", volume="1", price="3400", side="buy", offset="open", time_mode="GFD")
    values = {3301: state.instrument, 3302: state.volume, 3303: state.price}
    writes: list[int] = []
    tabs: list[tuple[str, ...]] = []
    def control(identifier: int, kind: str) -> Window:
        return Window(hwnd=identifier, parent=1, root=1, checked=0, id=identifier, class_name=kind,
                      visible=True, enabled=True, password=False, rect=(0, 0, 1, 1), items_hex=[],
                      text_hex=values[identifier].encode().hex())
    def answer(command: str) -> NativeReply:
        name, raw_id, raw_value = command.split()
        assert name == "set_text"
        identifier = int(raw_id)
        writes.append(identifier)
        values[identifier] = bytes.fromhex(raw_value).decode()
        if identifier == 3301:
            # 合约变化会影响其他控件，不能沿用恢复开始前的字段快照。
            values[3302], values[3303] = "3", "3500"
        return NativeReply(error=0, done=True)
    monkeypatch.setattr(form, "control", control)
    monkeypatch.setattr(form, "focus", lambda window: None)
    monkeypatch.setattr(form, "radio", lambda identifier, label: None)
    monkeypatch.setattr(form, "time_mode", lambda value: None)
    monkeypatch.setattr(form, "snapshot", lambda: state.model_copy(update={
        "instrument": values[3301], "volume": values[3302], "price": values[3303]}))
    monkeypatch.setattr(gui.native, "ask", answer)
    monkeypatch.setattr(gui, "key", lambda *keys: tabs.append(keys))
    form.restore(state)
    assert not writes and not tabs
    # 正常填参仍通过 Tab 提交编辑，不能把文字相同误认为编辑已生效。
    form.edit(3302, state.volume)
    assert writes == [3302] and tabs == [("Tab",)]
    writes.clear();tabs.clear()
    values[3301] = "m2705"
    form.restore(state)
    assert writes == [3301, 3302, 3303] and len(tabs) == 3
    assert form.snapshot() == state


def test_funds_reads_opening_snapshot_then_uses_verified_close(gui: Gui, monkeypatch: pytest.MonkeyPatch) -> None:
    root = Window(hwnd=1, parent=0, root=1, checked=0, id=0, class_name="main", visible=True,
                  enabled=True, password=False, rect=(0, 0, 800, 600), items_hex=[],
                  text_hex="快期2-CTP-上期技术-电信2".encode("gb18030").hex())
    button = root.model_copy(update={"hwnd": 3, "parent": 1, "id": 3009, "class_name": "Button"})
    dialog = root.model_copy(update={"hwnd": 2, "root": 2, "parent": 1, "class_name": "#32770",
                                     "text_hex": "期货资金账户详情".encode("gb18030").hex()})
    body = dialog.model_copy(update={"hwnd": 4, "parent": 2, "id": 7201, "class_name": "Static",
                                    "text_hex": "服务器资金字段".encode("gb18030").hex()})
    monkeypatch.setattr(gui, "baseline", lambda: Windows(windows=[root, button], focus=1, flags=0))
    snapshots = iter([Windows(windows=[root, button, dialog, body], focus=2, flags=0)])
    monkeypatch.setattr(gui.native, "managed_windows", lambda: next(snapshots))
    pending = healthy().model_copy(update={"funds": 2, "funds_count": 1, "dialogs": 1, "enabled": False})
    commands = feed(gui, monkeypatch, [pending, healthy()])
    read_state = gui.native.ask
    monkeypatch.setattr(gui.native, "ask", lambda command: NativeReply(error=0, done=True, data={"focused": True})
                        if command == "focus 3" else read_state(command))
    keys: list[tuple[str, ...]] = []
    monkeypatch.setattr(gui, "activate", lambda title=None: None)
    monkeypatch.setattr(gui, "key", lambda *values: keys.append(values))
    assert gui.balance_text() == body.text
    assert keys == [("space",)]
    assert commands == ["gui_state", "recover_gui 2", "gui_state"]
