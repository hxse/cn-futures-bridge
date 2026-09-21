"""固定安装包的受控券商目录，构建与运行使用同一份锁文件。"""

from functools import lru_cache
from pathlib import Path
import tomllib
from typing import Literal

from pydantic import BaseModel, ConfigDict


class TerminalProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    environment: Literal["simnow", "live"]
    broker_id: str
    broker_name: str
    sites: tuple[str, ...]
    title_names: tuple[str, ...]

    def titles(self, site: str) -> tuple[str, ...]:
        return tuple(f"快期2-CTP-{name}-{site}" for name in self.title_names)


@lru_cache(maxsize=1)
def catalog() -> dict[str, TerminalProfile]:
    path = Path(__file__).resolve().parent.parent / "terminal.lock.toml"
    data = tomllib.loads(path.read_text())
    return {name: TerminalProfile.model_validate(value) for name, value in data["profiles"].items()}
