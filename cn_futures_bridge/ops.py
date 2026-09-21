"""容器内部运维入口，宿主 just 只负责 Podman 编排。"""

import argparse
from collections import deque
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import tomllib
import urllib.request

import tomli_w
from pydantic import BaseModel

from .artifacts import ArtifactStore
from .config import ConfigError, Settings, load_settings
from .control import request_control
from .errors import BridgeError


class TerminalLock(BaseModel):
    url: str
    sha256: str


def initialize(root: Path) -> None:
    target = root / "config.toml"
    if target.exists():
        return
    with target.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write((root / "config.example.toml").read_bytes())


def migrate(root: Path) -> None:
    target = root / "config.toml"
    original = target.read_bytes()
    data = tomllib.loads(original.decode())
    if "token" not in data.get("api", {}):
        load_settings(target)
        print("配置无需迁移")
        return
    del data["api"]["token"]
    defaults = tomllib.loads((root / "config.example.toml").read_text())
    for name, section in defaults.items():
        data.setdefault(name, section)
    try:
        Settings.model_validate(data)
    except ValueError:
        raise ConfigError("旧配置还包含其他无效字段，未迁移") from None
    backup = root / "config.toml.bak"
    with backup.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor, name = tempfile.mkstemp(prefix="config.toml.", suffix=".tmp", dir=root)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(tomli_w.dumps(data))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
    print("配置已迁移，凭证保留；原配置备份为 config.toml.bak")


def fetch(root: Path) -> None:
    lock = TerminalLock.model_validate(tomllib.loads((root / "terminal.lock.toml").read_text()))
    target = root / "vendor" / "q72-installer.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != lock.sha256:
            raise ValueError("现有安装包与锁定哈希不符，请核对来源")
        print("安装包哈希有效")
        return
    temporary = target.with_suffix(".download")
    start = time.monotonic()
    try:
        with urllib.request.urlopen(lock.url, timeout=30) as response, temporary.open("wb") as output:
            size = 0
            while chunk := response.read(1048576):
                size += len(chunk)
                if time.monotonic() - start > 120 or size > 536870912:
                    raise ValueError("安装包下载超时或超过大小上限")
                output.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != lock.sha256:
            raise ValueError("下载内容与锁定哈希不符")
        temporary.rename(target)
    finally:
        temporary.unlink(missing_ok=True)


def http(settings: Settings, path: str) -> bytes:
    with urllib.request.urlopen(f"http://127.0.0.1:{settings.api.port}{path}", timeout=10) as response:
        data = response.read(33554433)
    if len(data) > 33554432:
        raise ValueError("诊断响应超过限制")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="容器内部运维")
    parser.add_argument("command", choices=("init-config", "migrate-config", "fetch", "layout",
                                           "status", "logs", "screenshot", "clean", "pause", "resume"))
    parser.add_argument("--root", type=Path, default=Path("/workspace"))
    parser.add_argument("--config", type=Path, default=Path("/etc/cn-futures-bridge/config.toml"))
    parser.add_argument("--output", type=Path, default=Path("/tmp/cfb-desktop.png"))
    args = parser.parse_args()
    try:
        if args.command in ("init-config", "migrate-config", "fetch"):
            {"init-config": initialize, "migrate-config": migrate, "fetch": fetch}[args.command](args.root)
            return 0
        settings = load_settings(args.config)
        if args.command == "layout":
            print(settings.api.port, settings.vnc.port, settings.vnc.web_port, settings.bridge.data_dir)
        elif args.command == "status":
            print(http(settings, "/v1/status").decode())
        elif args.command == "logs":
            for path in sorted((settings.bridge.data_dir / "logs").glob("cfb-*.jsonl")):
                if path.is_file() and not path.is_symlink():
                    with path.open() as stream:
                        print("".join(deque(stream, maxlen=100)), end="")
        elif args.command == "screenshot":
            args.output.write_bytes(http(settings, "/v1/desktop/screenshot"))
        elif args.command == "clean" and not (settings.bridge.data_dir / "control.sock").exists():
            with (settings.bridge.data_dir / ".session.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                ArtifactStore(settings).clean()
                print("已检查工件预算")
        else:
            print(json.dumps(request_control(settings.bridge.data_dir / "control.sock", args.command),
                             ensure_ascii=False))
    except (OSError, ValueError, BridgeError):
        # TOML/HTTP/模型异常可能携带敏感输入，不打印原始异常。
        print("操作失败，请检查配置、迁移备份、容器状态和受管日志")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
