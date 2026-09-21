set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
    @just --list

# 只在缺失时创建配置。
init-config:
    bash scripts/podman.sh init-config

migrate-config:
    bash scripts/podman.sh migrate-config

fetch:
    bash scripts/podman.sh fetch

build variant="all":
    bash scripts/podman.sh build {{quote(variant)}}

up variant="vnc":
    bash scripts/podman.sh up {{quote(variant)}}

run variant="vnc":
    bash scripts/podman.sh run {{quote(variant)}}

down:
    bash scripts/podman.sh down

status:
    bash scripts/podman.sh status

logs:
    bash scripts/podman.sh logs

screenshot output="debug/desktop.png":
    bash scripts/podman.sh screenshot {{quote(output)}}

pause:
    bash scripts/podman.sh pause

resume:
    bash scripts/podman.sh resume

clean:
    bash scripts/podman.sh clean

# 工具镜像内执行 uvx ty check，不挂载真实配置。
check:
    bash scripts/podman.sh check

# 少量离线 pytest 冒烟检查。
test:
    bash scripts/podman.sh test
