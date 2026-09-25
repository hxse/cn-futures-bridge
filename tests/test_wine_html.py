"""网页引导的离线契约：已有前缀也修复注册，构建包先验证再提取。"""

import hashlib
from io import BytesIO
from pathlib import Path
import subprocess
import tomllib
from unittest.mock import Mock

import pytest

from cn_futures_bridge.config import BridgeConfig, Settings
from cn_futures_bridge.errors import BridgeError
from cn_futures_bridge.runtime import Runtime
from container import prepare_gecko


@pytest.mark.parametrize("existing", [False, True])
def test_html_registration_covers_existing_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool,
) -> None:
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    runtime = Runtime(settings)
    settings.wine_prefix.mkdir(parents=True)
    marker = settings.wine_prefix / ".bridge-initialized"
    if existing:
        marker.write_text("win32\n")
    boot = Mock(returncode=0)
    boot.poll.return_value = 0
    commands: list[list[str]] = []
    def command(args: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, b"", b"")
    monkeypatch.setattr(runtime, "_spawn", lambda *args: boot)
    monkeypatch.setattr(runtime, "_command", command)
    runtime._initialize_wine()
    expected = [["wine", "regsvr32", "/s", "mshtml.dll"]]
    if not existing:
        expected.insert(0, ["wine", "regedit", "/S", "/opt/bridge/container/fonts.reg"])
    assert commands == expected and marker.read_text() == "win32\n"
    assert runtime.env["WINEDLLOVERRIDES"] == "mscoree="


@pytest.mark.parametrize("timeout", [False, True])
def test_html_registration_failure_blocks_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, timeout: bool,
) -> None:
    settings = Settings(bridge=BridgeConfig(data_dir=tmp_path))
    runtime = Runtime(settings)
    settings.wine_prefix.mkdir(parents=True)
    (settings.wine_prefix / ".bridge-initialized").write_text("win32\n")
    boot = Mock(returncode=0)
    boot.poll.return_value = 0
    monkeypatch.setattr(runtime, "_spawn", lambda *args: boot)
    def fail(args: list[str], seconds: int) -> subprocess.CompletedProcess[bytes]:
        if timeout:
            raise subprocess.TimeoutExpired(args, seconds)
        return subprocess.CompletedProcess(args, 1, b"", b"")
    monkeypatch.setattr(runtime, "_command", fail)
    with pytest.raises(BridgeError) as error:
        runtime._initialize_wine()
    assert error.value.code == "HTML_SETUP_FAILED" and not runtime.window_visible


def test_gecko_package_is_verified_before_extraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = prepare_gecko.GeckoLock.model_validate(tomllib.loads(Path("terminal.lock.toml").read_text())["wine_gecko"])
    package = b"offline package fixture"
    monkeypatch.setattr(prepare_gecko.urllib.request, "urlopen", lambda *args, **kwargs: BytesIO(package))
    extracted: list[bool] = []
    def extract(args: list[str], **kwargs: object) -> None:
        extracted.append(True)
        root = Path(args[2]) / "fixture" / "wine_gecko"
        root.mkdir(parents=True)
        (root / "VERSION").write_text(f"Wine Gecko {lock.version}")
        (root / "xul.dll").write_bytes(b"offline payload")
    monkeypatch.setattr(prepare_gecko.subprocess, "run", extract)
    with pytest.raises(ValueError, match="SHA256"):
        prepare_gecko.prepare(lock, tmp_path / "bad")
    assert not extracted and not (tmp_path / "bad").exists()
    fixture_lock = lock.model_copy(update={"sha256": hashlib.sha256(package).hexdigest()})
    prepare_gecko.prepare(fixture_lock, tmp_path / "valid")
    payload = tmp_path / "valid" / f"wine-gecko-{lock.version}-x86"
    assert extracted == [True] and (payload / "VERSION").read_text() == f"Wine Gecko {lock.version}"
    assert (payload / "xul.dll").read_bytes() == b"offline payload"
