"""构建时验证并提取 Wine 官方 Gecko；运行时不下载或安装 MSI。"""

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
from typing import Literal
import urllib.request

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GeckoLock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    architecture: Literal["x86"]
    url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def official_url(self) -> "GeckoLock":
        expected = (f"https://dl.winehq.org/wine/wine-gecko/{self.version}/"
                    f"wine-gecko-{self.version}-{self.architecture}.msi")
        if self.url != expected:
            raise ValueError("Gecko 下载地址必须对应锁定版本的官方包")
        return self


def prepare(lock: GeckoLock, output: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="cfb-gecko-") as temporary:
        root = Path(temporary)
        package = root / "gecko.msi"
        digest = hashlib.sha256()
        size = 0
        with urllib.request.urlopen(lock.url, timeout=30) as response, package.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 64 * 1024 * 1024:
                    raise ValueError("Gecko 安装包超过构建下载上限")
                digest.update(chunk)
                target.write(chunk)
        if digest.hexdigest() != lock.sha256:
            raise ValueError("Gecko SHA256 不匹配")
        extracted = root / "extracted"
        subprocess.run(["msiextract", "-C", str(extracted), str(package)],
                       check=True, timeout=120, stdout=subprocess.DEVNULL)
        versions = list(extracted.rglob("wine_gecko/VERSION"))
        if len(versions) != 1 or versions[0].read_text().strip() != f"Wine Gecko {lock.version}":
            raise ValueError("Gecko 提取目录或版本不匹配")
        payload = versions[0].parent
        if not (payload / "xul.dll").is_file():
            raise ValueError("Gecko 缺少渲染组件")
        output.mkdir(parents=True, exist_ok=True)
        shutil.copytree(payload, output / f"wine-gecko-{lock.version}-{lock.architecture}")


if __name__ == "__main__":
    with Path(sys.argv[1]).open("rb") as source:
        locked = GeckoLock.model_validate(tomllib.load(source)["wine_gecko"])
    prepare(locked, Path(sys.argv[2]))
