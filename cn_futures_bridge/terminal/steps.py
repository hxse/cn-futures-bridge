"""串行操作的步骤、耗时与提交前持久化。"""

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from pathlib import Path
import time

from ..errors import BridgeError, Submission
from ..journal import Journal
from .order_form import FormState
from .native import Session, Window
from ..results import OrderExecution, OrderIdentity

LOG = logging.getLogger(__name__)


class Steps:
    def __init__(self, request_id: str, action: str, journal: Journal, directory: Path | None):
        self.request_id = request_id
        self.action = action
        self.journal = journal
        self.directory = directory
        self.writes = action in ("create_market_order", "create_limit_order", "cancel_order")
        self.effect: Submission | None = None
        self.owned_row: dict[str, str] | None = None
        self.form_state: FormState | None = None
        self.market_dialog: Window | None = None
        self.execution: OrderExecution | None = None
        self.session: Session | None = None
        self.parked_id: int | None = None
        self.identity: OrderIdentity | None = None
        self.order_id: str | None = None
        self.capture_armed = False
        self.counter = 0

    def path(self, table: str) -> Path:
        assert self.directory is not None
        self.counter += 1
        return self.directory / f"{self.counter:03d}-{table}.csv"

    def save(self, phase: str, *, effect: Submission | None = None, session: str | None = None) -> None:
        if effect:
            self.effect = effect
        if self.writes:
            self.journal.phase(self.request_id, phase, effect=effect, session=session, artifact=self.directory,
                               identity=self.identity, order_id=self.order_id)

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        start = time.monotonic()
        extra = {"request_id": self.request_id, "action": self.action, "step": name, "event": "start"}
        LOG.info("步骤开始", extra=extra)
        try:
            yield
        except BaseException as exc:
            LOG.error("步骤失败：%s", str(exc) if isinstance(exc, BridgeError) else type(exc).__name__,
                      extra={**extra, "event": "error", "outcome": "error",
                             "duration_ms": (time.monotonic()-start)*1000,
                             "error_code": getattr(exc, "code", "INTERNAL_ERROR")})
            raise
        else:
            LOG.info("步骤完成", extra={**extra, "event": "end", "outcome": "ok",
                                    "duration_ms": (time.monotonic()-start)*1000})
