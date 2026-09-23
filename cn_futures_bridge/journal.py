"""最小防重发记录；未知副作用永不过期，恢复只记录而不重放。"""

from contextlib import contextmanager
import fcntl
import hashlib
from pathlib import Path
import sqlite3
import time
from typing import Iterator

from .config import Settings
from .errors import BridgeError
from .models import Operation
from .results import OrderIdentity, Reply


def error_reply(request_id: str, error: BridgeError, *, blocked: bool = False) -> Reply:
    return Reply(request_id=request_id, status=error.status,
                 body=error.response(request_id).model_dump(mode="json"), blocked=blocked)


class Journal:
    def __init__(self, settings: Settings):
        self.path = settings.bridge.data_dir / "state" / "operations.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.maximum = settings.execution.journal_max_bytes
        self.ttl = settings.execution.idempotency_ttl_hours * 3600
        identity = f"{settings.bridge.environment}:{settings.broker_id}:" + settings.account.username.get_secret_value()
        self.namespace = hashlib.sha256(identity.encode()).hexdigest()
        with self._db() as db:
            db.execute("PRAGMA auto_vacuum=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                request_id TEXT PRIMARY KEY, namespace TEXT NOT NULL, action TEXT NOT NULL,
                fingerprint TEXT NOT NULL, key_hash TEXT, phase TEXT NOT NULL,
                effect TEXT, session TEXT, artifact TEXT, reply TEXT,
                created REAL NOT NULL, updated REAL NOT NULL,
                UNIQUE(namespace, key_hash))""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(operations)")}
            for name in ("identity", "order_id"):
                if name not in columns:
                    db.execute(f"ALTER TABLE operations ADD COLUMN {name} TEXT")

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        # 同进程线程及独立执行器共用 flock，checkpoint 也在同一个临界区。
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            db = sqlite3.connect(self.path, timeout=3)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=FULL")
                db.execute(f"PRAGMA max_page_count={max(1, (self.maximum-65536)//8192)}")
                yield db
                db.commit()
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                total = sum(p.stat().st_size for p in self.path.parent.glob("operations.sqlite3*") if p.is_file())
                if total > self.maximum:
                    raise BridgeError("STORAGE_UNAVAILABLE", "防重发数据库容量不足")
            except sqlite3.Error as exc:
                db.rollback()
                raise BridgeError("STORAGE_UNAVAILABLE", "无法可靠保存防重发记录") from exc
            finally:
                db.close()

    @staticmethod
    def fingerprint(operation: Operation) -> str:
        import json
        canonical = {"action": operation.action, "parameters": operation.request().model_dump(mode="json")}
        return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _lookup(self, db: sqlite3.Connection, operation: Operation, key: str | None) -> Reply | None:
        if key is None:
            return None
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        row = db.execute("SELECT request_id,fingerprint,reply FROM operations WHERE namespace=? AND key_hash=?",
                         (self.namespace, key_hash)).fetchone()
        if row is None:
            return None
        request_id, fingerprint, reply = row
        if fingerprint != self.fingerprint(operation):
            return error_reply(request_id, BridgeError("IDEMPOTENCY_CONFLICT", "同一幂等键的参数不同", 409))
        if reply is not None:
            return Reply.model_validate_json(reply)
        return error_reply(request_id, BridgeError("OPERATION_IN_PROGRESS", "该逻辑请求尚在执行，请勿更换键重发", 409))

    def lookup(self, operation: Operation, key: str | None) -> Reply | None:
        if key is None:
            return None
        with self._db() as db:
            self._prune(db)
            return self._lookup(db, operation, key)

    def admit(self, request_id: str, operation: Operation, key: str | None) -> Reply | None:
        with self._db() as db:
            self._prune(db)
            existing = self._lookup(db, operation, key)
            if existing:
                return existing
            now = time.time()
            key_hash = hashlib.sha256(key.encode()).hexdigest() if key else None
            db.execute("""INSERT INTO operations
                       (request_id,namespace,action,fingerprint,key_hash,phase,effect,session,artifact,reply,created,updated)
                       VALUES(?,?,?,?,?,'queued',NULL,NULL,NULL,NULL,?,?)""",
                       (request_id, self.namespace, operation.action, self.fingerprint(operation), key_hash, now, now))
        return None

    def phase(self, request_id: str, phase: str, *, effect: str | None = None,
              session: str | None = None, artifact: Path | None = None,
              identity: OrderIdentity | None = None, order_id: str | None = None) -> None:
        with self._db() as db:
            result = db.execute("""UPDATE operations SET phase=?,effect=COALESCE(?,effect),
                session=COALESCE(?,session),artifact=COALESCE(?,artifact),
                identity=COALESCE(?,identity),order_id=COALESCE(?,order_id),updated=? WHERE request_id=?""",
                (phase, effect, session, str(artifact) if artifact else None,
                 identity.model_dump_json() if identity else None, order_id, time.time(), request_id))
            if result.rowcount != 1:
                raise BridgeError("STORAGE_UNAVAILABLE", "缺少提交前操作记录")

    def finish(self, reply: Reply) -> None:
        with self._db() as db:
            db.execute("UPDATE operations SET phase='finished',effect=?,reply=?,updated=? WHERE request_id=?",
                       (reply.body.get("submission_status"), reply.model_dump_json(), time.time(), reply.request_id))

    def interrupted(self, request_id: str) -> Reply:
        with self._db() as db:
            row = db.execute("SELECT effect,reply,identity,order_id FROM operations WHERE request_id=?", (request_id,)).fetchone()
            if row and row[1]:
                return Reply.model_validate_json(row[1])
            effect = row[0] if row and row[0] in ("submitted", "rejected") else "unknown"
            return error_reply(request_id, BridgeError("OPERATION_STATUS_UNKNOWN",
                "执行器失联，已保留提交事实；请核对订单、成交及日志", 502, submission_status=effect,
                identity=OrderIdentity.model_validate_json(row[2]) if row and row[2] else None,
                order_id=row[3] if row else None), blocked=True)

    def recover(self) -> int:
        with self._db() as db:
            rows = db.execute("SELECT request_id,phase,effect,identity,order_id FROM operations WHERE reply IS NULL AND namespace=?",
                              (self.namespace,)).fetchall()
            for request_id, phase, effect, identity, order_id in rows:
                safe = phase in ("queued", "started") and effect is None
                known = effect if effect in ("submitted", "rejected") else "unknown"
                error = BridgeError("REQUEST_INTERRUPTED" if safe else "OPERATION_STATUS_UNKNOWN",
                                    "上次进程中断；未自动重放，请核对日志和账户", 503,
                                    submission_status=None if safe else known, order_id=order_id,
                                    identity=OrderIdentity.model_validate_json(identity) if identity else None)
                reply = error_reply(request_id, error)
                db.execute("UPDATE operations SET phase='finished',effect=?,reply=?,updated=? WHERE request_id=?",
                           (None if safe else known, reply.model_dump_json(), time.time(), request_id))
            self._prune(db)
            return int(db.execute("SELECT COUNT(*) FROM operations WHERE effect='unknown' AND namespace=?",
                                  (self.namespace,)).fetchone()[0])

    def unresolved(self) -> int:
        with self._db() as db:
            return int(db.execute("SELECT COUNT(*) FROM operations WHERE effect='unknown' AND namespace=?",
                                  (self.namespace,)).fetchone()[0])

    def _prune(self, db: sqlite3.Connection) -> None:
        db.execute("DELETE FROM operations WHERE phase='finished' AND (effect IS NULL OR effect!='unknown') AND updated<?",
                   (time.time()-self.ttl,))

    def maintain(self) -> None:
        with self._db() as db:
            self._prune(db)
