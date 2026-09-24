"""日志预算跨 writer 共享，正常写入不枚举进程句柄；全部离线。"""

import json
import logging
import os
from pathlib import Path

import pytest

from cn_futures_bridge.config import BridgeConfig, LoggingConfig, Settings
from cn_futures_bridge.logging_store import LogStore


def settings(root: Path) -> Settings:
    return Settings(bridge=BridgeConfig(data_dir=root),
                    logging=LoggingConfig(max_file_bytes=512, max_total_bytes=1024))


def record(message: str = "容量检查") -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, __file__, 0, message, (), None)


def test_healthy_budget_does_not_inspect_handles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sink = LogStore(settings(tmp_path))
    (sink.root / "terminal.log").write_bytes(b"terminal")
    monkeypatch.setattr("cn_futures_bridge.logging_store.closed_logs",
                        lambda candidates: pytest.fail("预算内写入不得扫描进程句柄"))
    sink.emit(record())
    assert not sink.failed
    assert json.loads(sink.current.read_text())["message"] == "容量检查"


def test_writers_rotate_and_share_actual_budget(tmp_path: Path) -> None:
    first, second = LogStore(settings(tmp_path)), LogStore(settings(tmp_path))
    initial = first.current
    for number in range(12):
        writer = first if number % 2 == 0 else second
        writer.emit(record(f"line-{number}"))
        files = list(writer.root.glob("cfb-*.jsonl"))
        assert not first.failed and not second.failed
        assert sum(path.stat().st_size for path in files) <= writer.maximum
        assert all(path.stat().st_size <= writer.file_maximum for path in files)
        assert json.loads(writer.current.read_text().splitlines()[-1])["message"] == f"line-{number}"
    assert first.current != initial and not initial.exists()


def test_cleanup_preserves_open_terminal_file(tmp_path: Path) -> None:
    sink = LogStore(settings(tmp_path))
    closed = sink.root / "wineboot.log"
    closed.write_bytes(b"x" * 650)
    os.utime(closed, (1, 1))
    active = sink.root / "terminal.log"
    with active.open("wb") as stream:
        stream.write(b"y" * 400)
        stream.flush()
        sink.emit(record())
        assert not sink.failed and not closed.exists()
        assert active.read_bytes() == b"y" * 400
        assert active.stat().st_size + sink.current.stat().st_size <= sink.maximum


def test_external_growth_exhausts_budget_without_truncating_active_file(tmp_path: Path) -> None:
    sink = LogStore(settings(tmp_path))
    sink.emit(record())
    sink.terminal_logs.mkdir(parents=True)
    active = sink.terminal_logs / "2026.09.24-12.00.log"
    with active.open("wb") as stream:
        stream.write(b"z" * 1024)
        stream.flush()
        sink.emit(record())
        assert sink.failed and active.read_bytes() == b"z" * 1024
        assert not list(sink.root.glob("cfb-*.jsonl"))


def test_maintenance_checks_active_file_limit_and_io_failure_is_visible(tmp_path: Path) -> None:
    sink = LogStore(settings(tmp_path))
    active = sink.root / "terminal.log"
    with active.open("wb") as stream:
        stream.write(b"x" * 513)
        stream.flush()
        sink.maintain()
        assert sink.failed and active.stat().st_size == 513
    broken = LogStore(settings(tmp_path / "broken"))
    (broken.root / ".lock").mkdir()
    broken.emit(record())
    assert broken.failed and not broken.current.exists()
