"""只验证持久化防重发的最小契约，不启动交易终端。"""

from pathlib import Path
import sqlite3

from cn_futures_bridge.config import BridgeConfig, Settings
from cn_futures_bridge.journal import Journal
from cn_futures_bridge.models import Operation
from cn_futures_bridge.results import OrderExecution, OrderIdentity, Reply, SubmissionResult
from cn_futures_bridge.terminal.steps import Steps


def test_replay_conflict_and_interrupted_import(tmp_path: Path) -> None:
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    operation = Operation(action="create_limit_order", parameters={
        "exchange_id": "CZCE", "instrument_id": "RM701", "side": "buy",
        "offset": "open", "volume": 1, "price": 2323})
    journal = Journal(settings)
    assert journal.admit("cfb-first", operation, "one") is None
    pending = journal.lookup(operation, "one")
    assert pending and pending.status == 409 and pending.request_id == "cfb-first"
    result = Reply(request_id="cfb-first", status=202,
                   body=SubmissionResult(request_id="cfb-first").model_dump(mode="json"))
    journal.finish(result)
    assert Journal(settings).lookup(operation, "one") == result
    changed = Operation(action=operation.action, parameters={**operation.parameters, "volume": 2})
    conflict = journal.lookup(changed, "one")
    assert conflict and conflict.status == 409
    assert conflict.body["error"] == {"code": "IDEMPOTENCY_CONFLICT", "message": "同一幂等键的参数不同", "details": None}
    assert journal.admit("cfb-interrupted", operation, "two") is None
    journal.phase("cfb-interrupted", "importing", effect="unknown")
    restarted = Journal(settings)
    assert restarted.recover() == 1
    interrupted = restarted.lookup(operation, "two")
    assert interrupted and interrupted.body["submission_status"] == "unknown"
    assert interrupted.request_id == "cfb-interrupted"
    assert restarted.admit("cfb-new-request", operation, "two") == interrupted


def test_captured_identity_survives_worker_and_service_restart(tmp_path: Path) -> None:
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    operation = Operation(action="create_market_order", parameters={
        "exchange_id": "DCE", "instrument_id": "m2701", "side": "buy", "offset": "open", "volume": 1})
    identity = OrderIdentity(exchange_id="DCE", instrument_id="m2701", trading_day="20260924",
                             front_id=3, session_id=-12, order_ref="18")
    journal = Journal(settings)
    journal.admit("cfb-captured", operation, "identity-key")
    journal.phase("cfb-captured", "order_identified", effect="unknown", identity=identity, order_id="599159")
    interrupted = journal.interrupted("cfb-captured")
    assert interrupted.body["identity"] == identity.model_dump(mode="json")
    assert interrupted.body["order_id"] == "599159"
    assert Journal(settings).recover() == 1
    replay = Journal(settings).lookup(operation, "identity-key")
    assert replay and replay.body["identity"] == interrupted.body["identity"]
    assert replay.body["order_id"] == "599159" and replay.body["submission_status"] == "unknown"


def test_normalized_price_survives_interruption_and_legacy_migration(tmp_path: Path) -> None:
    settings=Settings(bridge=BridgeConfig(data_dir=tmp_path))
    operation=Operation(action='create_limit_order',parameters={'exchange_id':'DCE','instrument_id':'m2701',
        'side':'buy','offset':'open','volume':1,'price':3514.35})
    journal=Journal(settings)
    journal.admit('legacy',operation,'old')
    old=Reply(request_id='legacy',status=202,body={'request_id':'legacy','submission_status':'submitted',
        'execution':{'kind':'limit','price':3514,'time_in_force':'GFD'}})
    journal.finish(old)
    # 恢复为新增 execution 列之前的真实表结构，旧响应不添加臆测的调价字段。
    with sqlite3.connect(journal.path) as db:
        db.execute('ALTER TABLE operations DROP COLUMN execution')
    journal=Journal(settings)
    assert journal.lookup(operation,'old')==old
    journal.admit('pending',operation,'new')
    steps=Steps('pending','create_limit_order',journal,tmp_path)
    steps.execution=OrderExecution(kind='limit',price=3514,time_in_force='GFD',requested_price=3514.35,
                                    price_adjusted=True,price_adjustments=['tick_floor'])
    steps.save('importing',effect='unknown')
    expected=steps.execution.model_dump(mode='json')
    assert Journal(settings).interrupted('pending').body['execution']==expected
    restarted=Journal(settings)
    assert restarted.recover()==1
    replay=restarted.lookup(operation,'new')
    assert replay and replay.body['execution']==expected and replay.body['submission_status']=='unknown'
