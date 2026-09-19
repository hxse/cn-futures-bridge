# SimNow 空账号启动阶段

## 交付与边界

交付 Python 3.11+ 的最小启动服务、一份 Containerfile 的 headless/vnc 两个目标、版本锁定清单、config.example.toml 与未跟踪的本地 config.toml、正式启动脚本和离线测试。当前不需要第三方 Python 包。

账号字段留空；即使用户填写 username/password，也只校验为字符串并报告是否完整填写，不自动输入、登录或发送订单。登录状态始终为 unverified，trading_ready 与 automation_enabled 始终为 false；没有资金、持仓或交易成功占位接口。

调试仅支持 environment = "simnow"，其他值直接拒绝。官方普通快期2包自带“上期技术 / 9999”站点，可从完整包提取并只保留该站点。必须保留原生服务器、认证参数与其余运行文件，不使用快期自有模拟替代 SimNow；实际站点连通和登录待账号阶段验证。

## 安装、持久化与进程

- terminal.lock.toml 是安装包 URL、SHA256、版本、包内目录、主程序与模拟站点身份的唯一来源。构建先核对包哈希、真实主程序和模拟站点；不匹配则失败。
- 从固定包提取完整运行目录；启动实际 q7_release.exe，不经自动更新启动器切换版本。记录提取并缩减站点后的全部文件哈希，启动前核对持久目录，不一致时明确失败，不覆盖用户数据。
- Debian slim、32 位 Wine、中文字体、Xvfb、Openbox、截图和桥接代码为公共层；vnc 只增加 x11vnc/noVNC/websockify 及其入口。两个目标使用同一套配置与代码。
- /data 包含 wine、terminal、logs 和会话锁；数据目录可通过 TOML 修改，启动脚本按同一路径挂载。进程用文件锁排他占用同一个数据目录。
- 启动顺序为版本核对、虚拟屏幕、窗口管理器就绪、可选 VNC、Wine 会话引导、首次字体映射、终端登录窗口创建。前缀持久化不跳过每次 Wine 会话引导。各项等待均有期限，失败不循环重启终端。
- 虚拟屏幕禁用屏保。空账号阶段允许 VNC 手工观察，不启用业务自动化、不点击或输入账号。启动时按进程身份及“用户登录”标题确认窗口出现，只报告 window_visible，不宣称已登录或交易就绪；遇到关键进程退出或窗口消失更新状态。
- 镜像以 root 预建权限为 1777 的 /tmp/.X11-unix，运行进程继续使用普通用户；Xvfb 禁止在最后一个探测连接断开后自动重置，避免窗口管理器连接阶段的竞态。镜像启动验证须检查真实套接字和窗口管理器就绪。
- 保留进程日志用于排查 Wine、显示和终端启动；此处不实现或伪装完整快期业务日志解析。截图按请求采集，两镜像都可用。
- 停止时结束本实例进程与专用 Wine 会话；停止并移除容器保留数据卷。切换变体先 down 再 up，不自动抢占其他容器。

## 配置与公开入口

根目录 config.example.toml 为完整示例，config.toml 为实际配置且忽略提交、排除构建上下文；首次 init-config 或 up 仅在文件缺失时复制示例，不覆盖已有内容。配置无环境变量第二套入口，缺节、未知字段、类型或范围错误明确拒绝。

| 配置项 | 约定 |
| --- | --- |
| bridge.environment | 只能为 simnow |
| bridge.data_dir | 独立的绝对目录，默认 /data |
| bridge.startup_timeout_seconds | 15–300 的整数，默认 90 |
| account.username / password | 字符串，可为空；本阶段不执行登录 |
| api.host / port / token | 默认 0.0.0.0、8000、空 token；非空 token 用 Bearer 鉴权 |
| desktop.width / height / dpi | 默认 1280、800、96；范围分别为 800–3840、600–2160、72–192 |
| vnc.port / web_port | 默认 5900、6080，只影响 VNC 变体 |

三个端口必须为互不相同的 1024–65535 整数。启动脚本将端口发布到宿主机 127.0.0.1；远程使用 SSH 转发。VNC 本阶段依赖此本机访问边界，不继承 API token；用户自定义 podman 命令时须保留该绑定方式。配置错误不输出密码原文。

在仓库根目录执行：

```bash
uv run python tools/bridge.py init-config
uv run python -m cn_futures_bridge --config config.toml --check-config
uv run python tools/bridge.py build all
uv run python tools/bridge.py up vnc
uv run python tools/bridge.py status
uv run python tools/bridge.py screenshot
uv run python tools/bridge.py down
uv run python tools/bridge.py up headless
```

默认镜像为 localhost/cn-futures-bridge:0.1.0-headless 与 :0.1.0-vnc，容器名 cn-futures-bridge，数据卷 cn-futures-bridge-data。调试桌面 http://127.0.0.1:6080/vnc.html。fetch 只准备并校验安装包，build 自动调用 fetch；logs 输出容器日志，进程日志保存在卷内 logs/。

| HTTP 入口 | 当前含义 |
| --- | --- |
| GET /healthz | 200 表示 HTTP 服务存活；无认证，仅返回 status=ok |
| GET /v1/status | 当前启动状态；不返回凭证 |
| GET /readyz | 仅窗口可见且启动未失败时为 200，其余 503；不是交易就绪 |
| GET /v1/desktop/screenshot | 返回 image/png；不可截图时返回 503 与明确错误码 |

除 healthz 外，配置了 token 时均要求 Authorization: Bearer。响应禁止缓存。未知 GET 返回 404，POST 返回 405，不调用终端。

启动后状态的核心字段示例：

```json
{"environment":"simnow","stage":"terminal_bootstrap","state":"window_visible",
 "terminal_window_visible":true,"account_configured":false,
 "login_state":"unverified","trading_ready":false,"automation_enabled":false}
```

完整响应另含 terminal_version、vnc_enabled、error。error 为 null 或含 code/message 的对象。state 可为 starting、window_visible、window_missing、failed；错误包括 SESSION_IN_USE、TERMINAL_CHANGED、WINE_INIT_FAILED、WINE_INIT_TIMEOUT、WINDOW_TIMEOUT、PROCESS_EXITED、STARTUP_FAILED 等。无法绑定 HTTP 或取得会话锁时，进程非零退出。

反例：把 environment 改成 live 应在启动任何进程前报错；填好账户不启动自动登录；目录中旧版程序与镜像不符时停止，不自动覆盖；窗口不存在时 healthz 可为 200，但 readyz 必须为 503。

## 验证与停止线

正式离线入口为 `uv run python tools/bridge.py test`。验证空账号、已填写账号仍不登录、凭证不泄露、非法环境与配置、存活和窗口就绪的区分、诊断鉴权、写操作不可用，以及随包文件变化被拒绝、用户数据保留和目录锁释放；只使用本地临时目录和回环 HTTP，不访问外部服务，不启动 Wine。

镜像验证独立于离线入口：build all，依次 up vnc/headless、status、screenshot、down；核对两个镜像可见登录窗口、中文字体、截图、空账号与交易未就绪、停止后的卷复用。比对公共文件与已安装公共依赖一致，VNC 只增加远程桌面依赖。

首次构建需要网络下载官方包和发行版依赖；启动验证不提供账号、不点击登录、不构造报撤单。记录真实启动错误并修复已授权范围内的兼容问题，不能拿空桌面或只有 Wine 进程当作快期启动成功。

启动阶段通过后停止。用户填写账号并发出继续指令后，再验证原生 SimNow 站点及登录，调查快捷键、导入导出与业务日志；华安实盘、完整查询与交易 API、路径延迟比较均由后续范围承接。
