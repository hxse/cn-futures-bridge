"""容器服务入口；只有真实启动才加载桌面。"""

import argparse
from pathlib import Path
import sys

import uvicorn

from .api import create_app
from .config import ConfigError, load_settings
from .service import BridgeService


def main() -> int:
    parser = argparse.ArgumentParser(description="单账户期货终端桥接服务")
    parser.add_argument("--config", type=Path, default=Path("/etc/cn-futures-bridge/config.toml"))
    parser.add_argument("--check-config", action="store_true", help="只校验配置，不启动桌面")
    args = parser.parse_args()
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.check_config:
        print(f"配置有效：{settings.bridge.environment} / {settings.broker_id} / {settings.site}")
        return 0
    service = BridgeService(settings, vnc=Path("/opt/bridge/.vnc-enabled").exists())
    uvicorn.run(create_app(service), host=settings.api.host, port=settings.api.port,
                workers=1, access_log=False, log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
