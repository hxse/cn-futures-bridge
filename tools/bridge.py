"""项目正式入口：准备安装包、构建、启动与停止专用容器。"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cn_futures_bridge.config import ConfigError, load_settings

NAME = "cn-futures-bridge"


def run(*command: str) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def fetch() -> None:
    with (ROOT / "terminal.lock.toml").open("rb") as stream:
        lock = tomllib.load(stream)
    target = ROOT / "vendor" / "q72-installer.exe"
    target.parent.mkdir(exist_ok=True)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != lock["sha256"]:
            raise ValueError("已有安装包与 terminal.lock.toml 不匹配；请保留文件并核对来源")
        print("安装包校验通过")
        return
    temporary = target.with_suffix(".download")
    try:
        # curl 限制完整下载时间；安装包始终只从锁定的官方 HTTPS URL 获取。
        run("curl", "--fail", "--location", "--proto", "=https", "--connect-timeout", "15",
            "--max-time", "120", "--output", str(temporary), lock["url"])
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != lock["sha256"]:
            raise ValueError("官方安装包内容与锁定哈希不符，停止构建")
        temporary.rename(target)
    finally:
        temporary.unlink(missing_ok=True)


def build(variant: str) -> None:
    fetch()
    variants = ("headless", "vnc") if variant == "all" else (variant,)
    for item in variants:
        run("podman", "build", "--layers", "--target", item,
            "-t", f"localhost/{NAME}:0.1.0-{item}", "-f", "Containerfile", ".")


def config_file() -> Path:
    target = ROOT / "config.toml"
    if not target.exists():
        shutil.copyfile(ROOT / "config.example.toml", target)
        target.chmod(0o600)
    return target


def up(variant: str) -> None:
    path = config_file()
    settings = load_settings(path)
    command = ["podman", "run", "--detach", "--name", NAME,
               "--label", "cn-futures-bridge.managed=true",
               "--userns", "keep-id:uid=1000,gid=1000",
               "--stop-timeout", "20", "--shm-size", "256m",
               "--security-opt", "no-new-privileges",
               "--volume", f"{path}:/etc/cn-futures-bridge/config.toml:ro,Z",
               "--volume", f"cn-futures-bridge-data:{settings.data_dir}:U",
               "--publish", f"127.0.0.1:{settings.api_port}:{settings.api_port}"]
    if variant == "vnc":
        command += ["--publish", f"127.0.0.1:{settings.vnc_port}:{settings.vnc_port}",
                    "--publish", f"127.0.0.1:{settings.web_port}:{settings.web_port}"]
    command.append(f"localhost/{NAME}:0.1.0-{variant}")
    run(*command)
    print(f"启动状态：http://127.0.0.1:{settings.api_port}/v1/status")
    if variant == "vnc":
        print(f"桌面：http://127.0.0.1:{settings.web_port}/vnc.html")


def down() -> None:
    result = subprocess.run(["podman", "inspect", NAME], capture_output=True, text=True, check=True)
    labels = json.loads(result.stdout)[0]["Config"].get("Labels", {})
    if labels.get("cn-futures-bridge.managed") != "true":
        raise ValueError("同名容器不属于本项目启动脚本，不自动删除")
    run("podman", "stop", "--time", "20", NAME)
    run("podman", "rm", NAME)


def request(path: str) -> bytes:
    settings = load_settings(config_file())
    headers = {"Authorization": f"Bearer {settings.api_token}"} if settings.api_token else {}
    url = f"http://127.0.0.1:{settings.api_port}{path}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="cn-futures-bridge 启动工具")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="下载并校验锁定安装包")
    commands.add_parser("init-config", help="仅在缺失时创建本地 config.toml")
    build_parser = commands.add_parser("build")
    build_parser.add_argument("variant", choices=("headless", "vnc", "all"), default="all", nargs="?")
    up_parser = commands.add_parser("up")
    up_parser.add_argument("variant", choices=("headless", "vnc"), default="vnc", nargs="?")
    commands.add_parser("down", help="停止并删除本项目容器，保留数据卷")
    commands.add_parser("status")
    commands.add_parser("logs")
    shot_parser = commands.add_parser("screenshot")
    shot_parser.add_argument("output", type=Path, default=ROOT / "debug" / "desktop.png", nargs="?")
    commands.add_parser("test", help="离线测试，不启动终端或访问外部网络")
    args = parser.parse_args()
    try:
        if args.command == "fetch":
            fetch()
        elif args.command == "init-config":
            print(config_file())
        elif args.command == "build":
            build(args.variant)
        elif args.command == "up":
            up(args.variant)
        elif args.command == "down":
            down()
        elif args.command == "status":
            print(request("/v1/status").decode())
        elif args.command == "logs":
            run("podman", "logs", "--tail", "100", NAME)
        elif args.command == "screenshot":
            body = request("/v1/desktop/screenshot")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(body)
            print(args.output)
        elif args.command == "test":
            run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v")
    except (OSError, ValueError, ConfigError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
