#!/usr/bin/env bash
# 宿主只编排容器；配置解析与应用逻辑都在工具镜像内。
set -euo pipefail
cfb_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$cfb_root"
cfb_name=${CFB_CONTAINER:-cn-futures-bridge}
cfb_volume=${CFB_VOLUME:-cn-futures-bridge-data}
cfb_tools=localhost/cn-futures-bridge:tools
cfb_prefix=localhost/cn-futures-bridge:0.1.0

build_tools() {
    podman build --layers --target tools -t "$cfb_tools" -f Containerfile .
}
workspace_op() {
    podman run --rm --userns=keep-id --user "$(id -u):$(id -g)" \
        -v "$cfb_root:/workspace:rw" "$cfb_tools" \
        python -m cn_futures_bridge.ops "$1" --root /workspace
}
layout() {
    podman run --rm --network=none -v "$cfb_root/config.toml:/etc/cn-futures-bridge/config.toml:ro" \
        "$cfb_tools" python -m cn_futures_bridge.ops layout
}
managed() {
    [[ $(podman inspect --format '{{index .Config.Labels "cn-futures-bridge.managed"}}' "$cfb_name") == true ]] || {
        echo '同名容器不属于本项目，停止操作' >&2; exit 1;
    }
}
variant() {
    [[ "$1" == vnc || "$1" == headless ]] || { echo '镜像变体只接受 vnc/headless' >&2; exit 2; }
}
start_container() {
    variant "$1"
    workspace_op init-config
    local cfb_api cfb_vnc cfb_web cfb_data
    read -r cfb_api cfb_vnc cfb_web cfb_data < <(layout)
    [[ -n "$cfb_data" ]] || { echo '无法读取配置，请先检查或 migrate-config' >&2; exit 1; }
    local cfb_target="$cfb_prefix-$1"
    if podman container exists "$cfb_name"; then
        managed
        local cfb_existing cfb_wanted
        cfb_existing=$(podman inspect --format '{{.Image}}' "$cfb_name")
        cfb_wanted=$(podman image inspect --format '{{.Id}}' "$cfb_target")
        [[ "$cfb_existing" == "$cfb_wanted" ]] || { echo '镜像不同，请显式 just down 后再 up' >&2; exit 1; }
        [[ $(podman inspect --format '{{.State.Running}}' "$cfb_name") == true ]] || podman start "$cfb_name"
        return
    fi
    local -a cfb_args=(run --detach --name "$cfb_name" --label cn-futures-bridge.managed=true
        --label "cn-futures-bridge.variant=$1" --userns keep-id:uid=1000,gid=1000
        --stop-timeout 20 --shm-size 256m --security-opt no-new-privileges
        --log-driver k8s-file --log-opt max-size=20mb
        --volume "$cfb_root/config.toml:/etc/cn-futures-bridge/config.toml:ro"
        --volume "$cfb_volume:$cfb_data:U" --publish "127.0.0.1:$cfb_api:$cfb_api")
    if [[ "$1" == vnc ]]; then
        cfb_args+=(--publish "127.0.0.1:$cfb_vnc:$cfb_vnc" --publish "127.0.0.1:$cfb_web:$cfb_web")
    fi
    podman "${cfb_args[@]}" "$cfb_target"
    echo "API: http://127.0.0.1:$cfb_api/docs"
}

cfb_command=${1:-help}
shift || true
case "$cfb_command" in
    tools) build_tools ;;
    init-config|migrate-config|fetch) build_tools; workspace_op "$cfb_command" ;;
    check) build_tools; podman run --rm --network=none "$cfb_tools" uvx ty check ;;
    test) build_tools; podman run --rm --network=none "$cfb_tools" python -m pytest -q ;;
    build|run)
        cfb_variant=${1:-all}
        [[ "$cfb_command" != run || "$cfb_variant" != all ]] || cfb_variant=vnc
        build_tools
        workspace_op fetch
        if [[ "$cfb_variant" == all ]]; then cfb_variants=(headless vnc); else variant "$cfb_variant"; cfb_variants=("$cfb_variant"); fi
        for cfb_variant_item in "${cfb_variants[@]}"; do
            podman build --layers --target "$cfb_variant_item" -t "$cfb_prefix-$cfb_variant_item" -f Containerfile .
        done
        [[ "$cfb_command" != run ]] || start_container "$cfb_variant"
        ;;
    up) build_tools; start_container "${1:-vnc}" ;;
    down)
        if podman container exists "$cfb_name"; then managed; podman stop --time 20 "$cfb_name"; podman rm "$cfb_name"; fi
        ;;
    status|logs|pause|resume)
        managed
        podman exec "$cfb_name" python -m cn_futures_bridge.ops "$cfb_command"
        ;;
    screenshot)
        managed
        cfb_output=${1:-debug/desktop.png}
        podman exec "$cfb_name" python -m cn_futures_bridge.ops screenshot --output /tmp/cfb-desktop.png
        mkdir -p -- "$(dirname -- "$cfb_output")"
        podman cp "$cfb_name:/tmp/cfb-desktop.png" "$cfb_output"
        podman exec "$cfb_name" rm -f /tmp/cfb-desktop.png
        ;;
    clean)
        if podman container exists "$cfb_name" && [[ $(podman inspect --format '{{.State.Running}}' "$cfb_name") == true ]]; then
            managed
            podman exec "$cfb_name" python -m cn_futures_bridge.ops clean
        else
            build_tools
            read -r cfb_api cfb_vnc cfb_web cfb_data < <(layout)
            [[ -n "$cfb_data" ]]
            podman run --rm --network=none --userns keep-id:uid=1000,gid=1000 --user 1000:1000 \
                -v "$cfb_root/config.toml:/etc/cn-futures-bridge/config.toml:ro" \
                -v "$cfb_volume:$cfb_data:U" "$cfb_tools" python -m cn_futures_bridge.ops clean
        fi
        ;;
    *) echo '使用 just 查看正式入口' >&2; exit 2 ;;
esac
