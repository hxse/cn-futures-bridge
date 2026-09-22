"""显式迁移配置；先校验、备份，再原子替换，不输出凭证。"""

import os
from pathlib import Path
import tempfile
import tomllib
import uuid

import tomli_w

from .config import ConfigError, Settings, load_settings


def migrate(root: Path) -> None:
    target = root / "config.toml"
    original = target.read_bytes()
    data = tomllib.loads(original.decode())
    bridge = data.get("bridge", {})
    api = data.get("api", {})
    if not isinstance(bridge, dict) or not isinstance(api, dict):
        raise ConfigError("bridge/api 必须是配置表，未迁移")
    old_accounts = "account" in data or "environment" in bridge
    if old_accounts and ("accounts" in data or "mode" in bridge):
        raise ConfigError("新旧账户配置混用，请先核对 account/accounts 和 environment/mode，未迁移")
    if not old_accounts and "token" not in api:
        load_settings(target)
        print("配置无需迁移")
        return
    defaults = tomllib.loads((root / "config.example.toml").read_text())
    if old_accounts:
        environment = bridge.pop("environment", "simnow")
        if environment not in ("simnow", "live"):
            raise ConfigError("旧 bridge.environment 无效，未迁移")
        mode = "sandbox" if environment == "simnow" else "live"
        account = data.pop("account", {})
        if not isinstance(account, dict):
            raise ConfigError("旧 account 必须是配置表，未迁移")
        bridge["mode"] = mode
        data["bridge"] = bridge
        data["accounts"] = defaults["accounts"].copy()
        # 活动组沿用旧字段，不用示例的实盘站点填补原配置遗漏。
        data["accounts"][mode] = account
    api.pop("token", None)
    for name, section in defaults.items():
        data.setdefault(name, section)
    try:
        Settings.model_validate(data)
    except ValueError:
        raise ConfigError("转换后的配置无效，请检查选中账户的凭证、券商、站点及公共字段，未迁移") from None
    replacement = tomli_w.dumps(data)
    backup = root / "config.toml.bak"
    if backup.exists():
        backup = root / f"config.toml.{uuid.uuid4().hex}.bak"
    with backup.open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor, name = tempfile.mkstemp(prefix="config.toml.", suffix=".tmp", dir=root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        if target.read_bytes() != original:
            raise ConfigError("迁移期间 config.toml 已被修改，保留备份并停止替换")
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
    print(f"配置已迁移；原文件备份为 {backup.name}，备用账号可另行填写，重启后生效")
