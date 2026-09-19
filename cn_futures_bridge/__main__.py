import argparse
import logging
from pathlib import Path
import signal
import sys

from .api import create_server
from .config import ConfigError, load_settings
from .runtime import Runtime, RuntimeErrorCode


def main() -> int:
    parser = argparse.ArgumentParser(description="SimNow 快期2启动与诊断")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--check-config", action="store_true", help="只校验配置，不启动桌面或连接网络")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.check_config:
        print("配置有效：simnow；账号不会被自动登录")
        return 0
    runtime = Runtime(settings, vnc=Path("/opt/bridge/.vnc-enabled").exists())
    try:
        server = create_server(runtime)
        runtime.start()
    except (OSError, RuntimeErrorCode) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    def stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
