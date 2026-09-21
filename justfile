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

# 准备配置、构建并启动；传 vnc 启用远程桌面。
run mode="":
    bash scripts/podman.sh run {{quote(mode)}}

# 构建成功后替换当前容器，保留数据卷；传 vnc 启用远程桌面。
restart mode="":
    bash scripts/podman.sh restart {{quote(mode)}}

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

# 在宿主机进行静态检查。
check:
    uvx ty check

# 少量离线 pytest 冒烟检查。
test:
    bash scripts/podman.sh test
