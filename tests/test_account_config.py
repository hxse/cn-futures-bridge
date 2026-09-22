"""分组配置与迁移的离线契约，不读取实际配置或启动账户会话。"""

import hashlib
from pathlib import Path
import stat
import tomllib

from pydantic import ValidationError
import pytest
import tomli_w

from cn_futures_bridge.config import ConfigError, Settings, load_settings
from cn_futures_bridge.config_migration import migrate
from cn_futures_bridge.journal import Journal


def example() -> dict:
    return tomllib.loads(Path("config.example.toml").read_text())


def test_selected_account_and_backup_have_separate_validation(tmp_path: Path) -> None:
    data = example()
    data["bridge"]["data_dir"] = str(tmp_path)
    data["accounts"]["sandbox"].update(username="sample", password="sample-password")
    # 备用组尚未补完，不妨碍模拟环境启动；选中它时才验证业务完整性。
    data["accounts"]["live"].update(broker_id="1234", site="待选择", username="pending", password="")
    sandbox = Settings.model_validate(data)
    assert sandbox.request_mode == "sandbox" and sandbox.bridge.environment == "simnow"
    assert sandbox.account is sandbox.accounts.sandbox and sandbox.account.configured
    data["bridge"]["mode"] = "live"
    with pytest.raises(ValidationError):
        Settings.model_validate(data)
    data["accounts"]["live"].update(broker_id="6020", site="二套", password="pending-password")
    live = Settings.model_validate(data)
    assert live.account is live.accounts.live and live.request_mode == "live"
    assert live.bridge.environment == "live" and live.site == "二套"
    data["accounts"]["live"].update(username="", password="")
    assert not Settings.model_validate(data).account.configured
    data["accounts"]["sandbox"]["unexpected"] = "must-reject"
    with pytest.raises(ValidationError) as error:
        Settings.model_validate(data)
    assert error.value.errors()[0]["loc"] == ("accounts", "sandbox", "unexpected")
    del data["accounts"]["sandbox"]["unexpected"]
    data["accounts"]["sandbox"]["broker_id"] = 9999
    with pytest.raises(ValidationError) as error:
        Settings.model_validate(data)
    assert error.value.errors()[0]["loc"] == ("accounts", "sandbox", "broker_id")


def test_inactive_account_does_not_change_runtime_identity(tmp_path: Path) -> None:
    data = example()
    data["bridge"]["data_dir"] = str(tmp_path)
    data["accounts"]["sandbox"].update(username="sample", password="sample-password")
    before = Settings.model_validate(data)
    identity = hashlib.sha256("电信2:sample".encode()).hexdigest()[:24]
    assert before.session_dir == tmp_path / "sessions" / f"simnow-9999-{identity}"
    assert Journal(before).namespace == hashlib.sha256(b"simnow:9999:sample").hexdigest()
    data["accounts"]["live"].update(username="backup", password="backup-password", site="二套")
    after = Settings.model_validate(data)
    assert after.identity_signature == before.identity_signature and after.session_dir == before.session_dir
    assert before.account.username.get_secret_value() == "sample"
    data["accounts"]["sandbox"]["username"] = "changed-active"
    assert Settings.model_validate(data).identity_signature != before.identity_signature
    assert "sample-password" not in before.model_dump_json() and "backup-password" not in after.model_dump_json()


@pytest.mark.parametrize("environment", ["simnow", "live"])
def test_migration_preserves_selected_account_and_backup(tmp_path: Path, environment: str) -> None:
    (tmp_path / "config.example.toml").write_text(Path("config.example.toml").read_text())
    broker, site = ("9999", "电信2") if environment == "simnow" else ("6020", "二套")
    original = tomli_w.dumps({"bridge": {"environment": environment, "data_dir": str(tmp_path)},
        "account": {"broker_id": broker, "site": site, "username": "sample", "password": "secret"},
        "api": {"port": 45180, "token": "old-token"}, "reconnect": {"interval_seconds": 900}}).encode()
    target = tmp_path / "config.toml"
    target.write_bytes(original)
    old_backup = tmp_path / "config.toml.bak"
    old_backup.write_bytes(b"older configuration")
    migrate(tmp_path)
    settings = load_settings(target)
    assert settings.request_mode == ("sandbox" if environment == "simnow" else "live")
    assert settings.account.username.get_secret_value() == "sample" and settings.account.password.get_secret_value() == "secret"
    assert settings.api.port == 45180 and settings.reconnect.interval_seconds == 900
    assert settings.broker_id == broker and settings.site == site
    identity = hashlib.sha256(f"{site}:sample".encode()).hexdigest()[:24]
    assert settings.session_dir == tmp_path / "sessions" / f"{environment}-{broker}-{identity}"
    assert Journal(settings).namespace == hashlib.sha256(f"{environment}:{broker}:sample".encode()).hexdigest()
    backup = list(tmp_path.glob("config.toml.*.bak"))
    assert len(backup) == 1 and backup[0].read_bytes() == original
    assert old_backup.read_bytes() == b"older configuration"
    assert stat.S_IMODE(backup[0].stat().st_mode) == stat.S_IMODE(target.stat().st_mode) == 0o600
    migrated = target.read_bytes()
    migrate(tmp_path)
    assert target.read_bytes() == migrated and list(tmp_path.glob("config.toml.*.bak")) == backup
    result = tomllib.loads(migrated.decode())
    assert "account" not in result and "environment" not in result["bridge"] and "token" not in result["api"]


def test_conflicting_or_invalid_migration_never_replaces_config(tmp_path: Path) -> None:
    (tmp_path / "config.example.toml").write_text(Path("config.example.toml").read_text())
    invalid = (
        {"bridge": {"mode": "sandbox"}, "account": {}},
        {"bridge": {"environment": "simnow"}, "accounts": {}},
        {"bridge": {"environment": "live"}, "account": {"username": "private-name", "password": "private-password"}},
        {"account": {"username": "private-name"}},
    )
    target = tmp_path / "config.toml"
    for data in invalid:
        original = tomli_w.dumps(data).encode()
        target.write_bytes(original)
        with pytest.raises(ConfigError) as error:
            migrate(tmp_path)
        assert "private-name" not in str(error.value) and "private-password" not in str(error.value)
        assert target.read_bytes() == original and not list(tmp_path.glob("*.bak"))


def test_old_inputs_exit_and_default_environment_migrates(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    for data in ({"account": {}}, {"bridge": {"environment": "simnow"}}):
        target.write_text(tomli_w.dumps(data))
        with pytest.raises(ConfigError, match="migrate-config"):
            load_settings(target)
        with pytest.raises(ValidationError):
            Settings.model_validate(data)
    target.write_text('[bridge]\nmode = "simnow"\n')
    with pytest.raises(ConfigError, match="bridge.mode"):
        load_settings(target)
    (tmp_path / "config.example.toml").write_text(Path("config.example.toml").read_text())
    original = b'[account]\nusername = "sample"\npassword = "secret"\n'
    target.write_bytes(original)
    migrate(tmp_path)
    assert load_settings(target).request_mode == "sandbox"
    assert (tmp_path / "config.toml.bak").read_bytes() == original
    # 已分组配置只遗留 api.token 时，仍沿用同一迁移入口并保留两组。
    grouped = tomllib.loads(target.read_text())
    grouped["api"]["token"] = "obsolete-token"
    grouped["accounts"]["live"].update(username="backup", password="backup-password")
    target.write_text(tomli_w.dumps(grouped))
    migrate(tmp_path)
    assert load_settings(target).accounts.live.password.get_secret_value() == "backup-password"
    assert "token" not in tomllib.loads(target.read_text())["api"]
