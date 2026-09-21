"""只验证持久化防重发的最小契约，不启动交易终端。"""

from pathlib import Path

from cn_futures_bridge.config import BridgeConfig, Settings
from cn_futures_bridge.journal import Journal
from cn_futures_bridge.models import Operation
from cn_futures_bridge.results import Reply, SubmissionResult


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
