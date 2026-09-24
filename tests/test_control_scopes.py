"""请求内绑定的有效性、失效重定位和实时回读；全部离线。"""
from pathlib import Path

import pytest

from cn_futures_bridge.config import BridgeConfig, Settings
from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.journal import Journal
from cn_futures_bridge.logging_store import LogStore
from cn_futures_bridge.models import Operation
from cn_futures_bridge.terminal.executor import Executor
from cn_futures_bridge.terminal.gui import Gui
from cn_futures_bridge.terminal.native import GridBinding, NativeReply, Window, Windows
from cn_futures_bridge.terminal.order_form import OrderForm


def window(hwnd: int, identifier: int = 0, kind: str = "Static", text: str = "", parent: int = 1) -> Window:
    return Window(hwnd=hwnd, parent=parent, root=1, checked=0, id=identifier, class_name=kind,
                  visible=True, enabled=True, password=False, rect=(0, 0, 100, 20),
                  text_hex=text.encode("gb18030").hex(), items_hex=[])


@pytest.fixture
def executor(tmp_path: Path) -> Executor:
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    return Executor(settings, LogStore(settings), Journal(settings))


def grid(hwnd: int = 3, checked: int = 1) -> GridBinding:
    return GridBinding(valid=True, window=window(hwnd, 4201, "ListCtrl", parent=2), object=100+hwnd,
                       procedure=50, vtable=60, columns=12,
                       filters=[window(4, 4202, "Button", "全部(A)", parent=2).model_copy(update={"checked": checked})])


def test_reuse_checks_current_layout_object_and_filter(executor: Executor, monkeypatch: pytest.MonkeyPatch) -> None:
    gui = executor.gui
    first, replacement = grid(), grid(5)
    gui.bindings["preorders"] = first
    ready: list[bool] = []
    monkeypatch.setattr(gui, "ensure_ready", lambda: ready.append(True))
    monkeypatch.setattr(gui.native, "grid_binding", lambda hwnd: first)
    monkeypatch.setattr(gui, "baseline", lambda: pytest.fail("有效绑定不应读取全窗口树"))
    assert gui.grid("preorders") == first.window and ready == [True]
    assert replacement.window is not None
    snapshot = Windows(windows=[replacement.window], focus=1, flags=0)
    scans: list[bool] = []
    def baseline() -> Windows:
        scans.append(True)
        return snapshot
    monkeypatch.setattr(gui, "baseline", baseline)
    for invalid in (GridBinding(valid=False), first.model_copy(update={"columns": 9}),
                    first.model_copy(update={"object_address": 999}), grid(checked=0),
                    first.model_copy(update={"window": window(3, 4201, "ListCtrl", parent=99)})):
        gui.bindings["preorders"] = first
        monkeypatch.setattr(gui.native, "grid_binding", lambda hwnd: invalid if hwnd == 3 else replacement)
        assert gui.grid("preorders") == replacement.window
        assert gui.bindings["preorders"].identity == replacement.identity
    assert len(scans) == 5


def test_wrong_filter_rechecked_after_shortcut(executor: Executor, monkeypatch: pytest.MonkeyPatch) -> None:
    gui = executor.gui
    value = grid(checked=0)
    assert value.window is not None
    snapshot = Windows(windows=[value.window], focus=1, flags=0)
    keys: list[str] = []
    def key(value: str) -> None:
        keys.append(value)
    monkeypatch.setattr(gui, "baseline", lambda: snapshot)
    monkeypatch.setattr(gui, "activate", lambda: None)
    monkeypatch.setattr(gui, "key", key)
    monkeypatch.setattr(gui.native, "grid_binding", lambda hwnd: value)
    with pytest.raises(BridgeError) as error:
        gui.grid("preorders")
    assert error.value.code == "TERMINAL_DATA_INVALID" and keys == ["F8", "alt+a"]
    assert not gui.bindings


def test_form_reads_new_values_and_relocates_expired_panel(executor: Executor, monkeypatch: pytest.MonkeyPatch) -> None:
    gui = executor.gui
    root = window(1, text=gui.titles[0], parent=0)
    panel = window(2)
    button = window(4, 3324, "Button", "预埋/条件", parent=2)
    control = window(3, 3302, "Edit", "1", parent=2)
    snapshots = iter([
        Windows(windows=[root, panel, button, control], focus=1, flags=0),
        None,
        Windows(windows=[root, panel, button, control.model_copy(update={"text_hex": "2".encode().hex()})], focus=1, flags=0),
    ])
    queries: list[int] = []
    def form_windows(target: int) -> Windows | None:
        queries.append(target)
        return next(snapshots)
    monkeypatch.setattr(gui.native, "form_windows", form_windows)
    monkeypatch.setattr(gui.native, "windows", lambda: pytest.fail("定向字段读取不应扫描全部窗口"))
    form = OrderForm(gui)
    assert form.control(3302, "Edit").text == "1"
    assert form.control(3302, "Edit").text == "2" and queries == [0, 2, 0]


def test_failed_request_clears_bindings_on_entry_and_exit(executor: Executor, monkeypatch: pytest.MonkeyPatch) -> None:
    gui = executor.gui
    gui.bindings["preorders"] = grid();gui.form_panel = 2
    def unavailable():
        assert not gui.bindings and not gui.form_panel
        gui.bindings["preorders"] = grid();gui.form_panel = 2
        raise BridgeError("SERVICE_NOT_READY", "离线会话未就绪")
    monkeypatch.setattr(executor, "validate_session", unavailable)
    response = executor.execute("cfb-offline", Operation(action="fetch_balance", parameters={"mode": "sandbox"}))
    error = response.body["error"]
    assert response.status == 503 and isinstance(error, dict) and error["code"] == "SERVICE_NOT_READY"
    assert not gui.bindings and not gui.form_panel


def test_select_uses_native_focus_check(executor: Executor, monkeypatch: pytest.MonkeyPatch) -> None:
    gui = executor.gui
    monkeypatch.setattr(gui, "activate", lambda: None)
    monkeypatch.setattr(gui, "key", lambda *keys: None)
    monkeypatch.setattr(gui.native, "windows", lambda: pytest.fail("原生选择已核对焦点，无需全窗口重读"))
    monkeypatch.setattr(gui.native, "ask", lambda command: NativeReply(error=0, done=True, selected_count=1, selected=2))
    gui.select("preorders", window(3), 2)
    with pytest.raises(BridgeError) as error:
        gui.select("preorders", window(3), 1)
    assert error.value.code == "ORDER_IDENTITY_AMBIGUOUS"
