FROM ghcr.io/astral-sh/uv:0.9.18 AS uv-bin

FROM docker.io/library/debian:bookworm-slim@sha256:f3034a6ec3c1205360777c4aae76234998866ad18806ae62b63a3f84ccad782b AS toolchain
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv-bin /uv /uvx /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON_DOWNLOADS=never PYTHONDONTWRITEBYTECODE=1
WORKDIR /opt/bridge

FROM toolchain AS dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM toolchain AS capture-build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev libx11-dev libpng-dev \
    && rm -rf /var/lib/apt/lists/*
COPY container/capture.c /tmp/capture.c
RUN gcc -std=c11 -Wall -Wextra -Werror -O2 -s /tmp/capture.c -o /usr/local/bin/cfb-capture -lX11 -lpng

FROM toolchain AS tools
RUN apt-get update && apt-get install -y --no-install-recommends xvfb x11-xserver-utils libpng16-16 \
    && rm -rf /var/lib/apt/lists/*
ENV UV_TOOL_BIN_DIR=/usr/local/bin
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen \
    && /opt/venv/bin/python -c 'import importlib.metadata,subprocess; subprocess.run(["uv","tool","install","ty=="+importlib.metadata.version("ty")],check=True)'
COPY cn_futures_bridge ./cn_futures_bridge
COPY container ./container
COPY tests ./tests
COPY scripts ./scripts
COPY config.example.toml terminal.lock.toml ./
COPY --from=capture-build /usr/local/bin/cfb-capture /usr/local/bin/
ENV PATH="/opt/venv/bin:${PATH}" UV_OFFLINE=1

FROM toolchain AS native-build
RUN apt-get update && apt-get install -y --no-install-recommends gcc-mingw-w64-i686-posix \
    && rm -rf /var/lib/apt/lists/*
COPY native ./native
RUN mkdir /native && i686-w64-mingw32-gcc -Wall -Wextra -Werror -Wno-unused-parameter -O2 -static-libgcc -shared \
    native/hook.c native/import_scope.c native/common.c native/query.c native/gui.c native/startup.c native/market.c native/receipt.c native/tracking.c native/readiness.c \
    -o /native/cfb-hook.dll -Wl,--kill-at \
    && i686-w64-mingw32-gcc -Wall -Wextra -Werror -Wno-unused-parameter -O2 -static-libgcc -municode \
    native/controller.c -o /native/cfb-controller.exe

FROM docker.io/library/debian:bookworm-slim@sha256:f3034a6ec3c1205360777c4aae76234998866ad18806ae62b63a3f84ccad782b AS vnc-assets
# 只提取 noVNC 的浏览器静态资源，避免安装它的 Node/OpenStack 依赖。
RUN apt-get update && cd /tmp && apt-get download novnc=1:1.3.0-1 \
    && dpkg-deb --extract /tmp/novnc*.deb /opt/novnc-assets

FROM docker.io/library/debian:bookworm-slim@sha256:f3034a6ec3c1205360777c4aae76234998866ad18806ae62b63a3f84ccad782b AS payload
RUN apt-get update && apt-get install -y --no-install-recommends python3 innoextract \
    && rm -rf /var/lib/apt/lists/*
COPY vendor/q72-installer.exe /tmp/installer.exe
COPY terminal.lock.toml container/prepare_terminal.py /tmp/
RUN python3 -c 'import hashlib,tomllib; p="/tmp/installer.exe"; expected=tomllib.load(open("/tmp/terminal.lock.toml","rb"))["sha256"]; assert hashlib.sha256(open(p,"rb").read()).hexdigest()==expected, "installer SHA256 mismatch"' \
    && innoextract --silent --extract --output-dir /tmp/extracted /tmp/installer.exe \
    && python3 /tmp/prepare_terminal.py /tmp/extracted /opt/terminal /tmp/terminal.lock.toml

FROM docker.io/library/debian:bookworm-slim@sha256:f3034a6ec3c1205360777c4aae76234998866ad18806ae62b63a3f84ccad782b AS runtime
ENV DEBIAN_FRONTEND=noninteractive
RUN dpkg --add-architecture i386 && apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 tini libfontconfig1:i386 libfreetype6:i386 libxrender1:i386 libgnutls30:i386 \
       xvfb xauth x11-utils xdotool libpng16-16 openbox fonts-wqy-microhei locales tzdata ca-certificates \
    && python3 -c "from pathlib import Path; import hashlib,urllib.request; key=urllib.request.urlopen('https://dl.winehq.org/wine-builds/winehq.key', timeout=30).read(); assert hashlib.sha256(key).hexdigest() == 'd965d646defe94b3dfba6d5b4406900ac6c81065428bf9d9303ad7a72ee8d1b8', 'WineHQ signing key changed'; Path('/usr/share/keyrings/winehq.asc').write_bytes(key); Path('/etc/apt/sources.list.d/winehq.list').write_text('deb [arch=i386 signed-by=/usr/share/keyrings/winehq.asc] https://dl.winehq.org/wine-builds/debian bookworm main\\n')" \
    && apt-get -o Acquire::Retries=1 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::https::Timeout=30 -o Acquire::Retries=1 install -y --no-install-recommends \
       wine-stable:i386=11.0.0.0~bookworm-1 wine-stable-i386:i386=11.0.0.0~bookworm-1 \
    && /opt/wine-stable/bin/wine --version \
    && sed -i 's/^# zh_CN.UTF-8 UTF-8/zh_CN.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && useradd --create-home --uid 1000 bridge \
    && mkdir -p /data /etc/cn-futures-bridge && chown bridge:bridge /data \
    && rm -rf /var/lib/apt/lists/*
ENV LANG=zh_CN.UTF-8 LC_ALL=zh_CN.UTF-8 TZ=Asia/Shanghai PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/opt/wine-stable/bin:${PATH}"
COPY --from=capture-build /usr/local/bin/cfb-capture /usr/local/bin/
RUN install -d -o root -g root -m 1777 /tmp/.X11-unix
WORKDIR /opt/bridge
COPY --from=dependencies /opt/venv /opt/venv
COPY --from=native-build /native /opt/bridge/native
ENV PATH="/opt/venv/bin:${PATH}"
COPY --from=payload /opt/terminal /opt/terminal
COPY cn_futures_bridge ./cn_futures_bridge
COPY pyproject.toml terminal.lock.toml ./
COPY container/fonts.reg container/openbox.xml ./container/
COPY config.example.toml /etc/cn-futures-bridge/config.toml
EXPOSE 45173
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-m", "cn_futures_bridge", "--config", "/etc/cn-futures-bridge/config.toml"]

FROM runtime AS headless
USER bridge

FROM headless AS vnc
USER root
RUN apt-get update && apt-get install -y --no-install-recommends --no-upgrade x11vnc websockify \
    && touch /opt/bridge/.vnc-enabled && rm -rf /var/lib/apt/lists/*
COPY --from=vnc-assets /opt/novnc-assets/usr/share/novnc /usr/share/novnc
COPY --from=vnc-assets /opt/novnc-assets/usr/share/doc/novnc /usr/share/doc/novnc
USER bridge
EXPOSE 45174 45175
