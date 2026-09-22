"""单调时钟驱动的重连计划；仅调度线程修改，不操作 GUI。"""

from datetime import datetime, timedelta, timezone
import logging
from math import ceil
from time import monotonic

from .config import Settings
from .errors import ErrorDetail, ReconnectStatus

LOG = logging.getLogger(__name__)
CONNECTION_ERRORS = {"CONNECTION_FAILED", "CONNECTION_LOST"}


class ReconnectPlan:
    def __init__(self, settings: Settings):
        self.enabled = settings.reconnect.enabled and settings.account.configured
        self.interval = settings.reconnect.interval_seconds
        self.status = ReconnectStatus(enabled=self.enabled, interval_seconds=self.interval,
                                      state="idle" if self.enabled else "disabled")
        self.deadline: float | None = None
        self.observed: tuple[ErrorDetail | None, bool] | None = None
        self.started_at: float | None = None

    def observe(self, error: ErrorDetail | None, *, safe: bool) -> None:
        observation = (error, safe)
        if self.observed == observation and self.started_at is None:
            return
        self.observed = observation
        if self.started_at is not None:
            LOG.info("重连尝试结束", extra={"step": "reconnect", "event": "end",
                     "duration_ms": (monotonic()-self.started_at)*1000,
                     "outcome": "ok" if error is None and safe else "error"})
            self.started_at = None
        self.deadline = None
        self.status.next_retry_at = None
        self.status.manual_required = not safe or error is not None
        if error is None and safe:
            self.status.state = "idle" if self.enabled else "disabled"
            return
        if error is not None:
            self.status.last_error = error
            self.status.last_failure_at = datetime.now(timezone.utc).isoformat()
        if self.enabled and safe and error and error.code in CONNECTION_ERRORS:
            self.status.manual_required = False
            self.status.state = "waiting"
            self.deadline = monotonic() + self.interval
            self.status.next_retry_at = (datetime.now(timezone.utc) + timedelta(seconds=self.interval)).isoformat()
            LOG.warning("连接未就绪：%s；第 %s 次恢复结束后，下次尝试 %s", error.code,
                        self.status.attempts, self.status.next_retry_at,
                        extra={"step": "reconnect", "event": "scheduled"})
        else:
            self.status.state = "manual" if self.enabled else "disabled"

    def due(self) -> bool:
        return self.deadline is not None and monotonic() >= self.deadline

    def begin(self) -> None:
        self.deadline = None
        self.status.next_retry_at = None
        self.status.state = "reconnecting"
        self.status.attempts += 1
        self.started_at = monotonic()
        LOG.info("开始第 %s 次连接恢复", self.status.attempts,
                 extra={"step": "reconnect", "event": "start"})

    def retry_after(self, *, paused: bool) -> int | None:
        if paused or self.status.manual_required:
            return None
        if self.status.state == "reconnecting":
            return self.interval
        return max(1, ceil(self.deadline-monotonic())) if self.deadline is not None else None

    def snapshot(self, *, paused: bool) -> ReconnectStatus:
        value = self.status.model_copy(deep=True)
        if paused:
            value.state = "paused"
            value.next_retry_at = None
        return value
