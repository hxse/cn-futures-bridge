"""已识别文档的启动等待契约；不连接终端或账户。"""

from pathlib import Path
import time

import pytest

from cn_futures_bridge.config import BridgeConfig, Settings
from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.logging_store import LogStore
from cn_futures_bridge.terminal.gui import Gui
from cn_futures_bridge.terminal.native import NativeClient, NativeReply, Window, Windows


def desktop(title: str = "", *, pending: bool = False) -> Windows:
    main = Window(hwnd=1, parent=0, root=1, checked=0, id=0, class_name="main",
                  visible=True, enabled=not title, password=False, rect=(0, 0, 800, 600),
                  text_hex="快期2-CTP-上期技术-电信2".encode("gb18030").hex(), items_hex=[])
    windows = [main]
    if title:
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
    monkeypatch.setattr(native, "settlement_windows", lambda: next(snapshots))
    assert gui.baseline() == ready
    # 结算单晚于启动结束到达时，仍通过同一基线入口处理。
    native.startup_deadline = None
    snapshots = iter([pending, ready])
    assert gui.baseline() == ready
    monkeypatch.setattr(native, "settlement_windows", lambda: desktop("确认下单"))
    with pytest.raises(BridgeError) as error:
        gui.baseline()
    assert error.value.code == "GUI_RESET_FAILED"
    native.startup_deadline = time.monotonic() - 1
    monkeypatch.setattr(native, "settlement_windows", lambda: pending)
    with pytest.raises(BridgeError) as error:
        gui.baseline()
    assert error.value.code == "QUERY_TIMEOUT"


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
