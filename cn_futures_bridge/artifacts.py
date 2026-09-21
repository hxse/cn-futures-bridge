"""只清理明确登记且已结束的工件，容量预算跨进程协调。"""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import shutil
import time
from typing import Iterator, Literal
import uuid

from pydantic import BaseModel, ConfigDict

from .config import Settings
from .errors import BridgeError


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    created_ns: int
    state: Literal["active", "ended", "protected"] = "active"


class ArtifactStore:
    def __init__(self, settings: Settings):
        self.root = settings.bridge.data_dir / "artifacts"
        self.root.mkdir(parents=True, exist_ok=True)
        self.maximum = settings.artifacts.max_total_bytes
        self.minimum_free = settings.artifacts.min_free_bytes

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with (self.root / ".lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def _entries(self) -> list[tuple[Path, ArtifactRecord, int]]:
        entries: list[tuple[Path, ArtifactRecord, int]] = []
        for path in self.root.glob("op-*"):
            if path.is_symlink() or not path.is_dir():
                continue
            marker = path / "record.json"
            if not marker.is_file() or marker.is_symlink():
                continue
            record = ArtifactRecord.model_validate_json(marker.read_bytes())
            if record.name != path.name or any(p.is_symlink() for p in path.iterdir()):
                raise BridgeError("STORAGE_UNAVAILABLE", "工件登记或路径身份不一致")
            size = sum(p.stat().st_size for p in path.iterdir() if p.is_file())
            entries.append((path, record, size))
        return sorted(entries, key=lambda entry: entry[1].created_ns)

    def _ensure(self, reserve: int = 0) -> None:
        entries = self._entries()
        total = sum(size for _, _, size in entries)
        free = shutil.disk_usage(self.root).free
        for path, record, size in entries:
            if total + reserve <= self.maximum and free - reserve >= self.minimum_free:
                break
            if record.state == "ended":
                shutil.rmtree(path)
                total -= size
                free += size
        if total + reserve > self.maximum or free - reserve < self.minimum_free:
            raise BridgeError("STORAGE_UNAVAILABLE", "工件容量或可用磁盘不足，活动及保护记录未删除")

    def clean(self) -> None:
        with self._locked():
            self._ensure()

    def create(self) -> Path:
        with self._locked():
            self._ensure(min(1048576, self.maximum))
            path = self.root / f"op-{uuid.uuid4().hex}"
            path.mkdir(mode=0o700)
            self._write(path, ArtifactRecord(name=path.name, created_ns=time.time_ns()))
            return path

    def _write(self, path: Path, record: ArtifactRecord) -> None:
        temporary = path / "record.tmp"
        temporary.write_text(record.model_dump_json())
        os.replace(temporary, path / "record.json")

    def finish(self, path: Path, *, keep: bool, protect: bool = False) -> None:
        if path.parent != self.root or path.is_symlink():
            raise BridgeError("STORAGE_UNAVAILABLE", "工件路径不属于登记目录")
        with self._locked():
            record = ArtifactRecord.model_validate_json((path / "record.json").read_bytes())
            if record.name != path.name:
                raise BridgeError("STORAGE_UNAVAILABLE", "工件身份错误")
            if keep or protect:
                record.state = "protected" if protect else "ended"
                self._write(path, record)
            else:
                shutil.rmtree(path)
            self._ensure()
