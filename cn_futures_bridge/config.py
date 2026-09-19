"""从 TOML 读取唯一运行配置，不打印账号和口令。"""

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Account:
    username: str = field(repr=False)
    password: str = field(repr=False)


@dataclass(frozen=True)
class Settings:
    environment: str
    data_dir: Path
    startup_timeout_seconds: int
    account: Account
    api_host: str
    api_port: int
    api_token: str = field(repr=False)
    width: int
    height: int
    dpi: int
    vnc_port: int
    web_port: int


def load_settings(path: Path) -> Settings:
    try:
        with path.open("rb") as stream:
            data = tomllib.load(stream)
    except OSError as exc:
        raise ConfigError(f"无法读取配置文件：{path}") from exc
    except tomllib.TOMLDecodeError as exc:
        # 解析器错误可能带出包含密码的原始行。
        raise ConfigError("配置文件不是有效的 TOML") from exc
    schema = {
        "bridge": {"environment", "data_dir", "startup_timeout_seconds"},
        "account": {"username", "password"},
        "api": {"host", "port", "token"},
        "desktop": {"width", "height", "dpi"},
        "vnc": {"port", "web_port"},
    }
    if set(data) != set(schema):
        raise ConfigError("配置必须且只能包含 bridge/account/api/desktop/vnc 五节")
    for section, keys in schema.items():
        if not isinstance(data[section], dict) or set(data[section]) != keys:
            raise ConfigError(f"配置节 {section} 的字段不完整或存在未知字段")

    def string(section: str, key: str, allow_empty: bool = False) -> str:
        value = data[section][key]
        if not isinstance(value, str) or (not allow_empty and not value.strip()):
            raise ConfigError(f"{section}.{key} 必须是{'可为空的' if allow_empty else '非空'}字符串")
        return value

    def number(section: str, key: str, low: int, high: int) -> int:
        value = data[section][key]
        if type(value) is not int or not low <= value <= high:
            raise ConfigError(f"{section}.{key} 必须是 {low} 到 {high} 之间的整数")
        return value

    environment = string("bridge", "environment")
    if environment != "simnow":
        raise ConfigError("本阶段 bridge.environment 只支持 simnow")
    data_dir = Path(string("bridge", "data_dir"))
    if not data_dir.is_absolute() or data_dir == Path("/"):
        raise ConfigError("bridge.data_dir 必须是独立的绝对目录")
    settings = Settings(
        environment=environment,
        data_dir=data_dir,
        startup_timeout_seconds=number("bridge", "startup_timeout_seconds", 15, 300),
        account=Account(string("account", "username", True), string("account", "password", True)),
        api_host=string("api", "host"),
        api_port=number("api", "port", 1024, 65535),
        api_token=string("api", "token", True),
        width=number("desktop", "width", 800, 3840),
        height=number("desktop", "height", 600, 2160),
        dpi=number("desktop", "dpi", 72, 192),
        vnc_port=number("vnc", "port", 1024, 65535),
        web_port=number("vnc", "web_port", 1024, 65535),
    )
    if len({settings.api_port, settings.vnc_port, settings.web_port}) != 3:
        raise ConfigError("API、VNC 和 noVNC 端口必须互不相同")
    return settings
