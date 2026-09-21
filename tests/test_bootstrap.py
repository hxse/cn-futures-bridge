"""最小离线检查，不启动桌面、不连接账户。"""

import asyncio
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest

from cn_futures_bridge.api import create_app
from cn_futures_bridge.config import ConfigError, load_settings
from cn_futures_bridge.service import BridgeService


def test_diagnostics_have_no_authentication() -> None:
    settings = load_settings(Path("config.example.toml"))
    service = BridgeService(settings)
    async def verify() -> None:
        transport = ASGITransport(app=create_app(service, manage_lifecycle=False))
        async with AsyncClient(transport=transport, base_url="http://cfb.test") as client:
            assert (await client.get("/healthz")).json() == {"status": "ok"}
            assert (await client.get("/v1/status")).status_code == 200
            assert (await client.get("/readyz")).status_code == 503
            schema = (await client.get("/openapi.json")).json()
            assert "securitySchemes" not in schema.get("components", {})
    asyncio.run(verify())


def test_configuration_rejects_legacy_token_and_bool_port(tmp_path: Path) -> None:
    example = Path("config.example.toml").read_text()
    target = tmp_path / "config.toml"
    target.write_text(example.replace('port = 8000', 'port = true'))
    with pytest.raises(ConfigError, match="api.port"):
        load_settings(target)
    target.write_text(example.replace('[api]', '[api]\ntoken = "private-secret"'))
    with pytest.raises(ConfigError, match="migrate-config") as error:
        load_settings(target)
    assert "private-secret" not in str(error.value)
