"""唯一 TOML 配置模型；校验错误只包含字段名，不回显凭证。"""

from pathlib import Path
import hashlib
import tomllib
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

from .profiles import TerminalProfile, catalog

Positive = Annotated[int, Field(gt=0)]
Port = Annotated[int, Field(ge=1024, le=65535)]
Timeout = Annotated[int, Field(ge=1, le=300000)]


class ConfigError(ValueError):
    pass


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class BridgeConfig(ConfigModel):
    mode: Literal["sandbox", "live"] = "sandbox"
    data_dir: Path = Path("/data")
    startup_timeout_seconds: Annotated[int, Field(ge=15, le=300)] = 90

    @property
    def environment(self) -> Literal["simnow", "live"]:
        # 保持原生 profile、持久化目录及幂等记录的既有身份。
        return "simnow" if self.mode == "sandbox" else "live"

    @field_validator("data_dir", mode="before")
    @classmethod
    def absolute_directory(cls, value: object) -> Path:
        if not isinstance(value, (str, Path)):
            raise ValueError("必须是独立的绝对目录")
        path = Path(value)
        if not path.is_absolute() or path == Path("/") or any(c in str(path) for c in "\r\n\0"):
            raise ValueError("必须是独立的绝对目录")
        return path


class AccountConfig(ConfigModel):
    broker_id: Annotated[str, Field(pattern=r"^[0-9]{0,10}$")] = ""
    site: Annotated[str, Field(max_length=40)] = ""
    username: SecretStr = Field(default=SecretStr(""), repr=False)
    password: SecretStr = Field(default=SecretStr(""), repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.username.get_secret_value() and self.password.get_secret_value())


class AccountsConfig(ConfigModel):
    sandbox: AccountConfig = Field(default_factory=AccountConfig)
    live: AccountConfig = Field(default_factory=AccountConfig)


class ApiConfig(ConfigModel):
    host: Annotated[str, Field(min_length=1)] = "0.0.0.0"
    port: Port = 45173


class DesktopConfig(ConfigModel):
    width: Annotated[int, Field(ge=800, le=3840)] = 1280
    height: Annotated[int, Field(ge=600, le=2160)] = 800
    dpi: Annotated[int, Field(ge=72, le=192)] = 96


class VncConfig(ConfigModel):
    port: Port = 45174
    web_port: Port = 45175


class ReconnectConfig(ConfigModel):
    enabled: bool = True
    interval_seconds: Annotated[int, Field(ge=60, le=86400)] = 600


class ExecutionConfig(ConfigModel):
    queue_capacity: Annotated[int, Field(ge=1, le=1024)] = 32
    queue_timeout_ms: Timeout = 5000
    step_timeout_ms: Timeout = 3000
    poll_interval_ms: Annotated[int, Field(ge=1, le=1000)] = 10
    gui_action_gap_ms: Annotated[int, Field(ge=0, le=300000)] = 10
    idempotency_ttl_hours: Positive = 168
    journal_max_bytes: Positive = 134217728

    @model_validator(mode="after")
    def polling_deadline(self) -> Self:
        if self.poll_interval_ms > self.step_timeout_ms:
            raise ValueError("poll_interval_ms 不得大于 step_timeout_ms")
        return self


class LoggingConfig(ConfigModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    max_file_bytes: Positive = 20971520
    max_total_bytes: Positive = 209715200

    @model_validator(mode="after")
    def budget(self) -> Self:
        if self.max_file_bytes > self.max_total_bytes:
            raise ValueError("单文件上限不得大于日志总预算")
        return self


class ArtifactConfig(ConfigModel):
    max_total_bytes: Positive = 314572800
    cleanup_interval_seconds: Positive = 60
    min_free_bytes: Positive = 536870912


class Settings(ConfigModel):
    bridge: BridgeConfig = Field(default_factory=BridgeConfig)
    accounts: AccountsConfig = Field(default_factory=AccountsConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    desktop: DesktopConfig = Field(default_factory=DesktopConfig)
    vnc: VncConfig = Field(default_factory=VncConfig)
    reconnect: ReconnectConfig = Field(default_factory=ReconnectConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    artifacts: ArtifactConfig = Field(default_factory=ArtifactConfig)

    @property
    def request_mode(self) -> Literal["sandbox", "live"]:
        return self.bridge.mode

    @property
    def account(self) -> AccountConfig:
        return self.accounts.sandbox if self.bridge.mode == "sandbox" else self.accounts.live

    @property
    def broker_id(self) -> str:
        return self.account.broker_id or ("9999" if self.bridge.environment == "simnow" else "")

    @property
    def site(self) -> str:
        return self.account.site or ("电信2" if self.bridge.environment == "simnow" else "")

    @property
    def profile_name(self) -> str:
        names = [name for name, profile in catalog().items()
                 if profile.environment == self.bridge.environment and profile.broker_id == self.broker_id]
        if len(names) != 1:
            raise ValueError("环境与受支持券商不匹配，live 须指定 broker_id")
        return names[0]

    @property
    def profile(self) -> TerminalProfile:
        return catalog()[self.profile_name]

    @property
    def session_dir(self) -> Path:
        identity = self.site + ":" + self.account.username.get_secret_value()
        digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
        return self.bridge.data_dir / "sessions" / f"{self.bridge.environment}-{self.broker_id}-{digest}"

    @property
    def terminal_dir(self) -> Path:
        return self.session_dir / "terminal"

    @property
    def wine_prefix(self) -> Path:
        return self.session_dir / "wine"

    @property
    def identity_signature(self) -> str:
        # 备用配置不参与当前实例的身份；标签不保存密码或账号原文。
        value = self.model_dump_json(exclude={"accounts"})
        value += self.account.model_dump_json(exclude={"password", "username"})
        value += ":" + self.account.username.get_secret_value()
        return hashlib.sha256(value.encode()).hexdigest()

    @model_validator(mode="after")
    def distinct_ports(self) -> Self:
        if len({self.api.port, self.vnc.port, self.vnc.web_port}) != 3:
            raise ValueError("API、VNC 和 noVNC 端口必须互不相同")
        return self

    @model_validator(mode="after")
    def selected_profile(self) -> Self:
        if bool(self.account.username.get_secret_value()) != bool(self.account.password.get_secret_value()):
            raise ValueError(f"accounts.{self.bridge.mode} 的 username/password 必须一起填写或一起留空")
        if self.site not in self.profile.sites:
            raise ValueError("必须明确选择该券商的原生站点，live 不自动选择站点")
        return self


def load_settings(path: Path) -> Settings:
    try:
        data = tomllib.loads(path.read_text())
    except OSError as exc:
        raise ConfigError("无法读取配置文件") from exc
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError("配置文件不是有效的 UTF-8 TOML") from exc
    if isinstance(data.get("api"), dict) and "token" in data["api"]:
        raise ConfigError("api.token 已退出，请先执行 just migrate-config")
    if "account" in data or (isinstance(data.get("bridge"), dict) and "environment" in data["bridge"]):
        raise ConfigError("旧 account/bridge.environment 已退出，请先执行 just migrate-config")
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        fields = [".".join(map(str, item["loc"])) or "配置组合" for item in exc.errors()]
        raise ConfigError("配置字段无效或存在未知字段：" + ", ".join(fields)) from None
