"""唯一 TOML 配置模型；校验错误只包含字段名，不回显凭证。"""

from pathlib import Path
import tomllib
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator, model_validator

Positive = Annotated[int, Field(gt=0)]
Port = Annotated[int, Field(ge=1024, le=65535)]
Timeout = Annotated[int, Field(ge=1, le=300000)]


class ConfigError(ValueError):
    pass


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class BridgeConfig(ConfigModel):
    environment: Literal["simnow"] = "simnow"
    data_dir: Path = Path("/data")
    startup_timeout_seconds: Annotated[int, Field(ge=15, le=300)] = 90

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
    username: SecretStr = Field(default=SecretStr(""), repr=False)
    password: SecretStr = Field(default=SecretStr(""), repr=False)

    @property
    def configured(self) -> bool:
        return bool(self.username.get_secret_value() and self.password.get_secret_value())


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
    account: AccountConfig = Field(default_factory=AccountConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    desktop: DesktopConfig = Field(default_factory=DesktopConfig)
    vnc: VncConfig = Field(default_factory=VncConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    artifacts: ArtifactConfig = Field(default_factory=ArtifactConfig)

    @model_validator(mode="after")
    def distinct_ports(self) -> Self:
        if len({self.api.port, self.vnc.port, self.vnc.web_port}) != 3:
            raise ValueError("API、VNC 和 noVNC 端口必须互不相同")
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
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        fields = [".".join(map(str, item["loc"])) or "配置组合" for item in exc.errors()]
        raise ConfigError("配置字段无效或存在未知字段：" + ", ".join(fields)) from None
