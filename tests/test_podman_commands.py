"""离线验证重建重启顺序，不启动容器或访问账户。"""

import json
import os
from pathlib import Path
import subprocess
import sys


def test_restart_builds_before_replacing_container(tmp_path: Path) -> None:
    fake = tmp_path / "podman"
    fake.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ["CFB_COMMAND_TEST"])
with (root / "calls").open("a") as stream:
    stream.write(json.dumps(args) + "\\n")
failure = os.environ["CFB_COMMAND_FAILURE"]
if args[0] == "build":
    sys.exit(41 if failure == "build" and "tools" not in args else 0)
if args[:2] == ["container", "exists"]:
    sys.exit(0 if (root / "exists").exists() else 1)
if args[0] == "inspect" and "managed" in args[2]:
    print("false" if failure == "unmanaged" else "true")
elif args[0] == "stop":
    pass
elif args[0] == "rm":
    (root / "exists").unlink()
elif args[0] == "run" and "--rm" in args:
    if "layout" in args:
        if failure == "config": sys.exit(42)
        print("45173 45174 45175 /data identity")
    elif not ("init-config" in args or "fetch" in args): sys.exit(91)
elif args[0] == "run" and "--detach" in args:
    assert not (root / "exists").exists()
    (root / "exists").touch()
else:
    sys.exit(92)
''')
    fake.chmod(0o700)
    script = Path(__file__).resolve().parents[1] / "scripts" / "podman.sh"
    for mode, failure in (("", ""), ("vnc", ""), ("", "build"), ("", "config"), ("", "unmanaged")):
        (tmp_path / "exists").touch()
        (tmp_path / "calls").write_text("")
        env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
                   CFB_COMMAND_TEST=str(tmp_path), CFB_COMMAND_FAILURE=failure)
        result = subprocess.run(["bash", str(script), "restart", mode], env=env,
                                capture_output=True, text=True, timeout=10)
        calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
        if failure:
            assert result.returncode == {"build": 41, "config": 42, "unmanaged": 1}[failure]
            assert not any(call[0] in ("stop", "rm") or "--detach" in call for call in calls)
            assert (tmp_path / "exists").exists()
        else:
            assert result.returncode == 0, result.stderr
            variant = mode or "headless"
            build = next(i for i, call in enumerate(calls) if call[0] == "build" and variant in call)
            stop = next(i for i, call in enumerate(calls) if call[0] == "stop")
            remove = next(i for i, call in enumerate(calls) if call[0] == "rm")
            start = next(i for i, call in enumerate(calls) if "--detach" in call)
            assert build < stop < remove < start
            assert calls[start][-1] == f"localhost/cn-futures-bridge:0.1.0-{variant}"
            assert sum("--detach" in call for call in calls) == 1
            assert not any("volume" in call for call in calls)
