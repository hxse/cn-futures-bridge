"""环境绑定的最小离线契约；不启动终端，不发送任何真实交易。"""

import asyncio
from itertools import chain, repeat
from pathlib import Path
import time

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError
import pytest

from cn_futures_bridge.api import create_app
from cn_futures_bridge.config import AccountConfig, BridgeConfig, Settings
from cn_futures_bridge.journal import Journal
from cn_futures_bridge.logging_store import LogStore
from cn_futures_bridge.models import Operation
from cn_futures_bridge.results import Reply, SubmissionResult
from cn_futures_bridge.service import BridgeService
from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.terminal.executor import Executor
from cn_futures_bridge.terminal.native import Session, Windows


def live_settings(directory: Path) -> Settings:
    return Settings(bridge=BridgeConfig(environment="live", data_dir=directory),
                    account=AccountConfig(broker_id="6020", site="一套", username=SecretStr("sample-account")))


def test_environment_matches_before_readiness_and_live_is_not_read_only(tmp_path: Path) -> None:
    async def verify() -> None:
        for settings, expected in ((Settings(), "sandbox"), (live_settings(tmp_path), "live")):
            app = create_app(BridgeService(settings), manage_lifecycle=False)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://cfb.test") as client:
                opposite = "live" if expected == "sandbox" else "sandbox"
                order = {"exchange_id": "CZCE", "instrument_id": "RM701", "side": "buy", "offset": "open", "volume": 1, "price": 2323}
                for result in (await client.get("/cfb/fetch_balance", params={"mode": opposite}),
                               await client.post("/cfb/create_limit_order", json={**order, "mode": opposite})):
                    assert result.status_code == 409
                    assert result.json()["error"]["code"] == "ENVIRONMENT_MISMATCH"
                    assert result.json()["submission_status"] is None
                for result in (await client.get("/cfb/fetch_positions", params={"mode": expected}),
                               await client.post("/cfb/create_limit_order", json={**order, "mode": expected})):
                    assert result.status_code == 503
                    assert result.json()["error"]["code"] == "SERVICE_NOT_READY"
                if expected == "live":
                    omitted = await client.get("/cfb/fetch_balance")
                    assert omitted.status_code == 409 and omitted.json()["error"]["code"] == "ENVIRONMENT_MISMATCH"
                status = (await client.get("/v1/status")).json()
                assert status["request_mode"] == expected
    asyncio.run(verify())


def test_profiles_and_idempotency_are_isolated(tmp_path: Path) -> None:
    simnow = Settings(bridge=BridgeConfig(data_dir=tmp_path), account=AccountConfig(username=SecretStr("sample-account")))
    live = live_settings(tmp_path)
    assert (simnow.broker_id, simnow.site, simnow.request_mode) == ("9999", "电信2", "sandbox")
    assert live.terminal_dir != simnow.terminal_dir and live.wine_prefix != simnow.wine_prefix
    assert "sample-account" not in str(live.session_dir)
    other = Settings(bridge=live.bridge, account=AccountConfig(broker_id="6020", site="一套", username=SecretStr("second-account")))
    assert other.identity_signature != live.identity_signature
    for account in (AccountConfig(), AccountConfig(broker_id="9999", site="电信2"), AccountConfig(broker_id="6020", site="不存在")):
        with pytest.raises(ValidationError):
            Settings(bridge=live.bridge, account=account)
    params = {"exchange_id": "CZCE", "instrument_id": "RM701", "side": "buy", "offset": "open", "volume": 1, "price": 2323}
    simulation = Operation(action="create_limit_order", parameters=params)
    real = Operation(action=simulation.action, parameters={**params, "mode": "live"})
    sim_journal, live_journal = Journal(simnow), Journal(live)
    assert sim_journal.admit("cfb-sim", simulation, "same-key") is None
    sim_journal.phase("cfb-sim", "importing", effect="unknown")
    assert live_journal.recover() == 0 and live_journal.lookup(real, "same-key") is None
    assert sim_journal.recover() == 1
    assert live_journal.admit("cfb-live", real, "same-key") is None
    result = Reply(request_id="cfb-live", status=202, body=SubmissionResult(request_id="cfb-live").model_dump(mode="json"))
    live_journal.finish(result)
    assert live_journal.lookup(real, "same-key") == result
    pending = sim_journal.lookup(simulation, "same-key")
    assert pending and pending.body["submission_status"] == "unknown"


def test_login_waits_for_identity_and_rejects_new_login(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for changed in (False, True):
        settings = Settings(bridge=BridgeConfig(data_dir=tmp_path / str(changed)),
                            account=AccountConfig(username=SecretStr("sample"), password=SecretStr("sample-password")))
        executor = Executor(settings, LogStore(settings), Journal(settings))
        pending = Session(connected=False, trade_connected=False, market_connected=True, identity_match=False,
                          trading_day="", front_id=0, session_id=0, status_bound=True, login_generation=0)
        ready = pending.model_copy(update={"connected": True, "trade_connected": True, "identity_match": True,
                                          "trading_day": "20260922", "front_id": 1, "session_id": 2,
                                          "login_generation": int(changed)})
        observations = chain([pending], repeat(ready))
        window = Windows(windows=[], focus=0, flags=0)
        completed: list[bool] = []
        monkeypatch.setattr(executor.native, "start", lambda: None)
        monkeypatch.setattr(executor.native, "windows", lambda: window)
        monkeypatch.setattr(executor.native, "session", lambda: next(observations))
        monkeypatch.setattr(executor.native, "complete_startup", lambda: completed.append(True))
        monkeypatch.setattr(executor.gui, "login", lambda: None)
        monkeypatch.setattr(executor.gui, "baseline", lambda: window)
        executor.native.startup_deadline = time.monotonic() + 2
        if changed:
            with pytest.raises(BridgeError) as error:
                executor.start()
            assert error.value.code == "SERVICE_NOT_READY"
            assert executor.identity_lost and not executor.login_established and not completed
        else:
            executor.start()
            assert executor.login_established and executor.session == ready and completed == [True]
