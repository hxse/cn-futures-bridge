"""已识别文档的启动等待契约；不连接终端或账户。"""

from pathlib import Path
import time

import pytest

from cn_futures_bridge.config import BridgeConfig, Settings
from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.logging_store import LogStore
from cn_futures_bridge.terminal.gui import Gui
from cn_futures_bridge.terminal.native import GuiState, NativeClient, NativeReply, Window, Windows


def unknown_state() -> GuiState:
    return GuiState(main=1, main_count=1, enabled=False, focus=2, flags=0, capture=0,
                    menu_owned=False, modifiers=0, dialogs=1, unknown_dialogs=1,
                    funds=0, funds_count=0, document_pending=False)


def desktop(title: str | None = None, *, pending: bool = False) -> Windows:
    main = Window(hwnd=1, parent=0, root=1, checked=0, id=0, class_name="main",
                  visible=True, enabled=title is None, password=False, rect=(0, 0, 800, 600),
                  text_hex="快期2-CTP-上期技术-电信2".encode("gb18030").hex(), items_hex=[])
    windows = [main]
    if title is not None:
        windows.append(main.model_copy(update={"hwnd": 2, "root": 2, "parent": 1,
                       "class_name": "#32770", "enabled": True,
                       "text_hex": title.encode("gb18030").hex()}))
    return Windows(windows=windows, focus=1, flags=0, document_pending=pending)


def test_startup_waits_for_document_but_rejects_unknown_dialog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    native = NativeClient(settings, LogStore(settings))
    gui = Gui(native, settings)
    pending, ready = desktop("确认结算单", pending=True), desktop()
    snapshots = iter([pending, ready])
    native.startup_deadline = time.monotonic() + 2
    monkeypatch.setattr(native, "managed_windows", lambda: next(snapshots))
    assert gui.baseline() == ready
    # 结算单晚于启动结束到达时，仍通过同一基线入口处理。
    native.startup_deadline = None
    snapshots = iter([pending, ready])
    assert gui.baseline() == ready
    monkeypatch.setattr(native, "managed_windows", lambda: desktop("确认下单"))
    monkeypatch.setattr(native, "gui_state", unknown_state)
    with pytest.raises(BridgeError) as error:
        gui.baseline()
    assert error.value.code == "GUI_RESET_FAILED"
    native.startup_deadline = time.monotonic() - 1
    monkeypatch.setattr(native, "managed_windows", lambda: pending)
    with pytest.raises(BridgeError) as error:
        gui.baseline()
    assert error.value.code == "QUERY_TIMEOUT"


@pytest.mark.parametrize("title", ["", "保证金监控中心"])
def test_late_information_window_waits_for_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, title: str,
) -> None:
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    native = NativeClient(settings, LogStore(settings))
    gui = Gui(native, settings)
    pending, ready = desktop(title, pending=True), desktop()
    snapshots = iter([pending, pending, ready])
    commands: list[str] = []
    def answer(command: str) -> NativeReply:
        commands.append(command)
        return NativeReply(error=0, done=True, information_close_count=1,
                           data=next(snapshots).model_dump(mode="json"))
    monkeypatch.setattr(native, "ask", answer)
    assert native.startup_deadline is None and gui.baseline() == ready
    assert commands == ["managed_windows"] * 3
    # 相同标题但不满足原生模板的窗口不能被基线当作已经处理。
    monkeypatch.setattr(native, "managed_windows", lambda: desktop(title))
    monkeypatch.setattr(native, "gui_state", unknown_state)
    with pytest.raises(BridgeError) as error:
        gui.baseline()
    assert error.value.code == "GUI_RESET_FAILED"


def test_startup_completion_waits_for_native_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    native = NativeClient(settings, LogStore(settings))
    pending, ready = desktop("确认结算单", pending=True), desktop()
    snapshots = iter([pending, ready])
    commands: list[str] = []
    def answer(command: str) -> NativeReply:
        commands.append(command)
        return NativeReply(error=0, done=True, data=next(snapshots).model_dump(mode="json"))
    native.startup_deadline = time.monotonic() + 2
    monkeypatch.setattr(native, "ask", answer)
    native.complete_startup()
    assert commands == ["startup_done", "startup_done"] and native.startup_deadline is None
    native.startup_deadline = time.monotonic() - 1
    monkeypatch.setattr(native, "ask", lambda command: NativeReply(error=0, done=True, data=pending.model_dump(mode="json")))
    with pytest.raises(BridgeError) as error:
        native.complete_startup()
    assert error.value.code == "QUERY_TIMEOUT" and native.startup_deadline is not None


def test_trade_notice_waits_for_checkbox_and_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    native = NativeClient(settings, LogStore(settings))
    gui = Gui(native, settings)
    pending, ready = desktop("(2/2) 成交通知", pending=True), desktop()
    replies = iter([
        NativeReply(error=0, done=True, trade_notice_check_count=1, data=pending.model_dump(mode="json")),
        NativeReply(error=0, done=True, trade_notice_check_count=1, trade_notice_checked_count=1,
                    trade_notice_confirm_count=1, data=pending.model_dump(mode="json")),
        NativeReply(error=0, done=True, trade_notice_check_count=1, trade_notice_checked_count=1,
                    trade_notice_confirm_count=1, trade_notice_closed_count=1, data=ready.model_dump(mode="json")),
    ])
    commands: list[str] = []
    def answer(command: str) -> NativeReply:
        commands.append(command)
        return next(replies)
    monkeypatch.setattr(native, "ask", answer)
    assert gui.baseline() == ready and commands == ["managed_windows"] * 3
    monkeypatch.setattr(native, "managed_windows", lambda: desktop("确认下单"))
    monkeypatch.setattr(native, "gui_state", unknown_state)
    with pytest.raises(BridgeError) as error:
        gui.baseline()
    assert error.value.code == "GUI_RESET_FAILED"
