"""重连的离线契约；假时钟、窗口快照与进程替身，不接触账户或交易网络。"""

import asyncio
from pathlib import Path
import subprocess
import threading

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError
import pytest

from cn_futures_bridge.api import create_app
from cn_futures_bridge.config import AccountConfig, AccountsConfig, BridgeConfig, ReconnectConfig, Settings
from cn_futures_bridge.dispatch import Dispatcher, Job
from cn_futures_bridge.errors import BridgeError, ErrorDetail
from cn_futures_bridge.logging_store import LogStore
from cn_futures_bridge.journal import Journal
from cn_futures_bridge.models import Operation
from cn_futures_bridge.reconnect import ReconnectPlan
from cn_futures_bridge.results import Reply, SubmissionResult
from cn_futures_bridge.runtime import Runtime
from cn_futures_bridge.service import BridgeService
from cn_futures_bridge.terminal.gui import Gui
from cn_futures_bridge.terminal.executor import Executor
from cn_futures_bridge.terminal.native import NativeClient, Session, Window, Windows
from cn_futures_bridge.worker import Message, WorkerProcess, WorkerState


def settings_at(path: Path) -> Settings:
    return Settings(bridge=BridgeConfig(data_dir=path), accounts=AccountsConfig(sandbox=AccountConfig(
        username=SecretStr("offline-fixture"), password=SecretStr("offline-fixture-password"))))


def disconnected() -> WorkerState:
    return WorkerState(login_state="disconnected", blocked=True, ownership_clear=True,
                       error=ErrorDetail(code="CONNECTION_LOST", message="连接断开"))


def connected() -> WorkerState:
    return WorkerState(login_state="logged_in", ownership_clear=True, query_ready=True, trading_ready=True)


def test_reconnect_clock_and_failure_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr("cn_futures_bridge.reconnect.monotonic", lambda: clock[0])
    plan = ReconnectPlan(settings_at(tmp_path))
    error = disconnected().error
    plan.observe(error, safe=True)
    assert plan.interval == 600 and plan.retry_after(paused=False) == 600
    clock[0] = 699
    plan.observe(error, safe=True)
    assert not plan.due() and plan.retry_after(paused=False) == 1
    clock[0] = 700
    assert plan.due() and plan.snapshot(paused=True).next_retry_at is None
    assert plan.retry_after(paused=True) is None
    plan.begin()
    assert plan.status.attempts == 1 and not plan.due() and plan.retry_after(paused=False) == 600
    clock[0] = 790
    plan.observe(error, safe=True)
    assert plan.deadline == 1390  # 从失败结束计时，不从上次尝试开始计时。
    plan.begin()
    plan.observe(None, safe=True)
    assert plan.status.state == "idle" and plan.status.attempts == 2 and plan.status.last_error == error
    assert plan.deadline is None and plan.retry_after(paused=False) is None
    for failure, safe in ((ErrorDetail(code="LOGIN_REQUIRES_ATTENTION", message="凭证错误"), True),
                          (error, False), (ErrorDetail(code="QUERY_TIMEOUT", message="原因不明"), True)):
        plan.observe(failure, safe=safe)
        assert plan.status.manual_required and plan.deadline is None
    disabled = ReconnectPlan(settings_at(tmp_path).model_copy(update={"reconnect": ReconnectConfig(enabled=False)}))
    disabled.observe(error, safe=True)
    assert disabled.status.state == "disabled" and disabled.deadline is None
    for interval in (True, "600", 0, 59, 86401):
        with pytest.raises(ValidationError):
            ReconnectConfig.model_validate({"interval_seconds": interval})


def window(hwnd: int, root: int, title: str, cls: str) -> Window:
    return Window(hwnd=hwnd, root=root, parent=0 if hwnd == root else root, checked=0, id=0,
                  class_name=cls, visible=True, enabled=True, password=False,
                  rect=(0, 0, 100, 100), text_hex=title.encode("gb18030").hex(), items_hex=[])


def test_login_error_uses_owned_labels_not_account_text(tmp_path: Path) -> None:
    settings = settings_at(tmp_path)
    gui = Gui(NativeClient(settings, LogStore(settings)), settings)
    login = window(1, 1, "用户登录", "#32770")
    for label, expected in (("连接服务器失败", "CONNECTION_FAILED"), ("密码错误", "LOGIN_REQUIRES_ATTENTION")):
        snapshot = Windows(windows=[login, window(2, 1, label, "Static")], flags=0, focus=1)
        with pytest.raises(BridgeError) as error:
            gui.login_ready(snapshot, 1)
        assert error.value.code == expected and error.value.status == 503
    for control in (window(2, 1, "连接服务器失败", "Edit"), window(2, 9, "连接服务器失败", "Static"),
                    window(2, 1, "历史记录：连接服务器失败", "Static")):
        assert not gui.login_ready(Windows(windows=[login, control], flags=0, focus=1), 1)
    unexpected = Windows(windows=[login, window(3, 3, "未知确认", "#32770")], flags=0, focus=1)
    with pytest.raises(BridgeError) as error:
        gui.login_ready(unexpected, 1)
    assert error.value.code == "GUI_RESET_FAILED"
    assert not gui.login_ready(unexpected.model_copy(update={"document_pending": True}), 1)


def test_disconnect_does_not_hide_identity_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_at(tmp_path)
    executor = Executor(settings, LogStore(settings), Journal(settings))
    executor.login_established = True
    executor.login_generation = 0
    session = Session(connected=False, trade_connected=False, market_connected=True, identity_match=True,
                      trading_day="20260922", front_id=1, session_id=2, status_bound=True, login_generation=0)
    monkeypatch.setattr(executor.native, "session", lambda: session)
    with pytest.raises(BridgeError) as error:
        executor.validate_session()
    assert error.value.code == "CONNECTION_LOST" and executor.disconnected
    session = session.model_copy(update={"identity_match": False})
    with pytest.raises(BridgeError) as error:
        executor.validate_session()
    assert error.value.code == "SERVICE_NOT_READY"


def test_offline_http_clears_queue_and_keeps_recorded_results(tmp_path: Path) -> None:
    service = BridgeService(settings_at(tmp_path))
    dispatcher = Dispatcher(service.settings, service.runtime)
    service.dispatcher = dispatcher
    dispatcher.ready, dispatcher.state = True, connected()
    parameters = {"exchange_id": "CZCE", "instrument_id": "RM701", "side": "buy", "offset": "open", "volume": 1, "price": 2323}
    order = Operation(action="create_limit_order", parameters=parameters)
    previous = Reply(request_id="recorded", status=202, body=SubmissionResult(request_id="recorded").model_dump(mode="json"))
    dispatcher.journal.admit("recorded", order, "existing")
    dispatcher.journal.finish(previous)
    queued = dispatcher.submit("queued", order, "new")
    read = dispatcher.submit("read", Operation(action="fetch_positions", parameters={}), None)
    assert isinstance(queued, Job) and isinstance(read, Job)
    dispatcher.state = disconnected()
    with dispatcher.condition:
        dispatcher._update_recovery()
    for job in (queued, read):
        assert isinstance(job, Job) and not job.started
        assert job.future.result().status == 503 and job.future.result().body["submission_status"] is None
    assert not dispatcher.queue and dispatcher.journal.lookup(order, "new") == queued.future.result()
    assert dispatcher.submit("duplicate", order, "existing") == previous
    deadline = dispatcher.reconnect.deadline
    async def verify() -> None:
        app = create_app(service, manage_lifecycle=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://offline.test") as client:
            for path in ("/cfb/fetch_balance", "/cfb/fetch_trading_status?exchange_id=CZCE&product_id=RM"):
                result = await client.get(path)
                assert result.status_code == 503 and result.json()["error"]["code"] == "SERVICE_NOT_READY"
                assert 1 <= int(result.headers["Retry-After"]) <= 600
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/readyz")).status_code == 503
            state = (await client.get("/v1/status")).json()["reconnect"]
            assert state["state"] == "waiting" and state["interval_seconds"] == 600 and state["next_retry_at"]
            opposite = await client.get("/cfb/fetch_balance?mode=live")
            assert opposite.status_code == 409 and "Retry-After" not in opposite.headers
            dispatcher.pause()
            assert "Retry-After" not in (await client.get("/cfb/fetch_balance")).headers
            assert dispatcher.status(service.runtime.status()).reconnect.state == "paused"
            assert dispatcher.resume().body == {"resumed": True}
            assert dispatcher.reconnect.deadline == deadline and not dispatcher.queue
    asyncio.run(verify())
    assert dispatcher.reconnect.deadline == deadline and dispatcher.reconnect.status.attempts == 0


def test_dispatch_reconnect_is_serial_and_pause_prevents_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("cn_futures_bridge.reconnect.monotonic", lambda: clock[0])
    settings = settings_at(tmp_path)
    dispatcher = Dispatcher(settings, Runtime(settings))
    dispatcher.ready, dispatcher.state = True, disconnected()
    dispatcher._update_recovery()
    dispatcher.pause()
    clock[0] = 600
    calls: list[str] = []
    started = threading.Event()
    def start() -> Message:
        calls.append("new-worker")
        assert dispatcher.owner_busy and not dispatcher.ready
        with pytest.raises(BridgeError) as error:
            dispatcher.submit("during-reconnect", Operation(action="fetch_balance", parameters={}), None)
        assert error.value.code == "SERVICE_NOT_READY"
        dispatcher.stopping = True
        started.set()
        return Message(generation="new", request_id="startup", state=connected())
    monkeypatch.setattr(dispatcher.worker, "stop", lambda: calls.append("old-worker-exited"))
    monkeypatch.setattr(dispatcher.runtime, "restart_terminal", lambda: calls.append("wine-restarted"))
    monkeypatch.setattr(dispatcher.worker, "start", start)
    loop = threading.Thread(target=dispatcher._loop, daemon=True)
    loop.start()
    try:
        assert not started.wait(.15) and calls == []
        dispatcher.resume()
        assert started.wait(2)
    finally:
        dispatcher.stopping = True
        with dispatcher.condition:
            dispatcher.condition.notify_all()
        loop.join(timeout=2)
    assert not loop.is_alive() and calls == ["old-worker-exited", "wine-restarted", "new-worker"]
    assert dispatcher.state.query_ready and dispatcher.reconnect.status.state == "idle"


def test_uncertain_effect_or_owner_blocks_reconnect(tmp_path: Path) -> None:
    settings = settings_at(tmp_path)
    dispatcher = Dispatcher(settings, Runtime(settings))
    dispatcher.ready, dispatcher.state = True, disconnected()
    order = Operation(action="create_limit_order", parameters={"exchange_id": "CZCE", "instrument_id": "RM701",
        "side": "buy", "offset": "open", "volume": 1, "price": 2323})
    dispatcher.journal.admit("uncertain", order, "unknown-key")
    dispatcher.journal.phase("uncertain", "sending", effect="unknown")
    dispatcher.unresolved = dispatcher.journal.recover()
    dispatcher._update_recovery()
    assert dispatcher.unresolved == 1 and dispatcher.reconnect.status.manual_required
    assert dispatcher.reconnect.deadline is None
    replay = dispatcher.submit("same", order, "unknown-key")
    assert isinstance(replay, Reply) and replay.body["submission_status"] == "unknown"
    dispatcher.unresolved = 0
    dispatcher.state.ownership_clear = False
    dispatcher._update_recovery()
    assert dispatcher.reconnect.deadline is None and dispatcher.reconnect.status.manual_required


def test_wine_exit_must_be_confirmed_before_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for failure in (False, True):
        runtime = Runtime(settings_at(tmp_path))
        calls: list[str] = []
        def command(args: list[str], timeout: int = 10) -> subprocess.CompletedProcess[bytes]:
            calls.append(" ".join(args))
            return subprocess.CompletedProcess(args, int(failure and args == ["wineserver", "-w"]), b"", b"")
        monkeypatch.setattr(runtime, "_command", command)
        monkeypatch.setattr(runtime, "_prepare_files", lambda: tmp_path)
        monkeypatch.setattr(runtime, "_initialize_wine", lambda: calls.append("wine-init"))
        monkeypatch.setattr(runtime, "_spawn", lambda *args: calls.append("terminal-start"))
        monkeypatch.setattr(runtime, "_wait", lambda *args: None)
        if failure:
            with pytest.raises(BridgeError) as error:
                runtime.restart_terminal()
            assert error.value.code == "GUI_UNRESPONSIVE"
            assert calls == ["wineserver -k", "wineserver -w"] and runtime.error
        else:
            runtime.restart_terminal()
            assert calls == ["wineserver -k", "wineserver -w", "wine-init", "terminal-start"]
            assert runtime.window_visible and runtime.error is None


def test_worker_exit_is_required_before_new_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    worker = WorkerProcess(settings_at(tmp_path))
    class RunningProcess:
        def join(self, timeout: float) -> None:
            pass
        def is_alive(self) -> bool:
            return True
    monkeypatch.setattr(worker, "process", RunningProcess())
    monkeypatch.setattr(worker, "exchange", lambda *args: Message(generation="old", request_id="stop", state=disconnected()))
    with pytest.raises(BridgeError) as error:
        worker.stop()
    assert error.value.code == "GUI_UNRESPONSIVE" and worker.process is not None
    with pytest.raises(BridgeError) as error:
        worker.start()
    assert error.value.code == "GUI_UNRESPONSIVE"
