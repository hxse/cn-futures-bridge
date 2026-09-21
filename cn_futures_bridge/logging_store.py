"""有界 JSON 日志与进程输出收集，不让子进程持有无限追加日志。"""

from datetime import datetime, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import IO
import uuid

from .config import Settings
from .terminal_logs import closed_logs


class LogStore(logging.Handler):
    def __init__(self, settings: Settings):
        super().__init__()
        self.root = settings.bridge.data_dir / "logs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.maximum = settings.logging.max_total_bytes
        self.file_maximum = settings.logging.max_file_bytes
        self.secrets = tuple(value for value in (
            settings.account.username.get_secret_value(), settings.account.password.get_secret_value()) if value)
        self.current = self._new_path()
        self.terminal_logs = settings.terminal_dir / "logs"
        self.mutex = threading.RLock()
        self.failed = False
        self.pumps: list[threading.Thread] = []

    def _new_path(self) -> Path:
        return self.root / f"cfb-{time.time_ns()}-{uuid.uuid4().hex[:8]}.jsonl"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            for secret in self.secrets:
                message = message.replace(secret, "[redacted]")
            item = {"timestamp": datetime.now(timezone.utc).isoformat(), "level": record.levelname,
                    "message": message[:16384], "logger": record.name}
            for key in ("request_id", "action", "step", "event", "duration_ms", "outcome", "error_code"):
                item[key] = getattr(record, key, None)
            body = (json.dumps(item, ensure_ascii=False) + "\n").encode()
            with self.mutex, (self.root / ".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if len(body) > self.file_maximum:
                    raise OSError("日志记录超过单文件预算")
                if self.current.exists() and self.current.stat().st_size + len(body) > self.file_maximum:
                    self.current = self._new_path()
                files = sorted(p for p in self.root.glob("cfb-*.jsonl") if p.is_file() and not p.is_symlink())
                external, active = closed_logs(self.root, self.terminal_logs)
                files = sorted(files + external, key=lambda p: p.stat().st_mtime_ns)
                total = sum(p.stat().st_size for p in files)
                total += sum(p.stat().st_size for p in active)
                for path in files:
                    if total + len(body) <= self.maximum:
                        break
                    if path == self.current:
                        self.current = self._new_path()
                    total -= path.stat().st_size
                    path.unlink()
                if total + len(body) > self.maximum:
                    raise OSError("日志总预算耗尽")
                with self.current.open("ab") as stream:
                    stream.write(body)
        except (OSError, ValueError):
            self.failed = True

    def collect(self, name: str, stream: IO[bytes]) -> None:
        def consume() -> None:
            buffer = b""
            try:
                while chunk := os.read(stream.fileno(), 8192):
                    buffer += chunk
                    while b"\n" in buffer or len(buffer) >= 16384:
                        if b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                        else:
                            line, buffer = buffer[:16384], buffer[16384:]
                        logging.getLogger(f"process.{name}").info(line.decode("utf-8", "replace"))
                if buffer:
                    logging.getLogger(f"process.{name}").info(buffer.decode("utf-8", "replace"))
            except (OSError, ValueError):
                self.failed = True
            finally:
                stream.close()
        worker = threading.Thread(target=consume, name=f"log-{name}", daemon=True)
        self.pumps.append(worker)
        worker.start()

    def maintain(self) -> None:
        _, active = closed_logs(self.root, self.terminal_logs)
        if any(path.stat().st_size > self.file_maximum for path in active):
            self.failed = True
        # 周期性写入同时执行总预算淘汰，所有日志共用同一锁。
        logging.getLogger(__name__).info("日志容量检查", extra={"event": "maintenance"})


def configure_logging(settings: Settings) -> LogStore:
    sink = LogStore(settings)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(sink)
    root.setLevel(settings.logging.level)
    return sink
