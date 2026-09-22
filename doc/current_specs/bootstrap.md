# 单账户容器运行与诊断

项目遵循 [项目原则](project_principles.md)。应用只在 Podman 内运行，宿主 just/bash 负责容器编排；配置、HTTP 和内部运维消息使用 Pydantic，唯一 HTTP 服务为 FastAPI/Uvicorn 单 worker。

## 终端与镜像

`terminal.lock.toml` 锁定普通快期2 2.93.405.1998 的官方包及 SHA256，profiles 目录声明上期技术/9999 和 H华安期货/6020。构建分别生成只含目标券商的 simnow/huaan 种子，不修改原生站点和认证参数。Wine 使用 WineHQ 11.0 的 32 位预编译包，`wine-stable:i386` 与 `wine-stable-i386:i386` 均锁定 `11.0.0.0~bookworm-1`，不从源码编译。启动直接运行 q7_release.exe，先验证种子的环境、券商和全部程序文件哈希。

headless 与 vnc 共用终端、Python 依赖和业务代码；vnc 只增加远程桌面组件。两者都有 Xvfb/Openbox、截图和中文字体，1280×800/96 DPI 为默认设置。Xvfb 关闭 GLX，保留二维 X11。先确认显示与窗口管理器，再引导 Wine 会话、启动客户端；持久化前缀不跳过 Wine 会话引导。

Wine 保留官方包及其必要依赖，Mono/MSHTML 仍由运行环境禁用。优化优先考虑运行内存和构建耗时，不为缩小镜像引入完整 Wine 源码编译，也不强行删除 dpkg 依赖或任意 Wine 服务。原生桥接和轻量截图工具仍在独立构建阶段编译，运行镜像只携带产物。

诊断和失败证据统一使用 `cfb-capture`，读取 X11 根窗口并通过 libpng 保存 RGB PNG，保留超时和原有容量治理；不安装 scrot。Openbox 仍依赖 Imlib2，因此其共用图像库继续保留。中文字体仍使用完整文泉驿微米黑及原有映射，不裁剪字库。

数据卷默认 `/data`，共享 logs、artifacts、state；terminal/wine 位于 sessions 下按环境、券商、站点和账户摘要隔离。摘要目录不显示凭证原文，旧 /data/terminal 和 /data/wine 保留但不自动迁移。`.session.lock` 排他锁保护整个实例；停止只处理本容器进程，保留数据卷。已识别连接故障按受控流程定时恢复；进程崩溃、桌面启动失败及未知异常不盲目循环重启。

客户端已有独立探针能力见 [terminal_capabilities.md](terminal_capabilities.md) 和 [trading_status.md](trading_status.md)，历史探针数据不等于正式 API 全链路已经在线核验。

## 配置与入口

唯一配置为 config.toml/config.example.toml，节包括 bridge、accounts.sandbox、accounts.live，以及公共 api、desktop、vnc、execution、reconnect、logging、artifacts。bridge.mode 是唯一启动选择，默认 sandbox，只接受 sandbox/live，与 API mode 一致。两组账号可同时保存，每次启动只登录选中组。

Pydantic 对所有分组严格校验字段名、类型和字段基本格式，只对选中组检查凭证配对及环境、券商、站点组合。选中组账号密码均空可启动登录界面，不安排重连；仅填写一项时报错。备用组可为空或待补齐，选中时再做业务完整性校验。sandbox 的空 broker_id/site 仍解析为 9999/电信2；live 必须明确填写 6020 和“一套”或“二套”。错误不回显凭证，日志过滤覆盖两组账号密码。

Settings.account 为派生的当前账号，不能作为配置字段输入；内部 bridge.environment 从 mode 映射为 simnow/live，用于原生 profile、session 目录和幂等 namespace。此重构不改变原数据身份或创建替代目录，业务和重连共用同一个当前账号。

```toml
[bridge]
mode = "sandbox"

[accounts.sandbox]
broker_id = "9999"
site = "电信2"
username = ""
password = ""

[accounts.live]
broker_id = "6020"
site = "一套"
username = ""
password = ""
```

reconnect.enabled 默认 true；interval_seconds 为严格整数 60～86400，默认 600，计时从本次失败结束开始。可省略整个配置节；设为 false 关闭自动重连，900 表示每 15 分钟。单次登录沿用 bridge.startup_timeout_seconds。已识别连接故障由同一调度器恢复：确认旧 worker 和专属 Wine 退出，再引导新终端会话；HTTP、Xvfb/Openbox、VNC 不重启，不重建镜像。具体限制见 [terminal_execution.md](terminal_execution.md)。

实例只使用启动时的配置，不通过 API 或重连切换。容器身份摘要只包含公共配置和当前账户身份，排除备用组和密码；备用组变化不影响当前实例复用。重复 run 遇到 mode、当前账户、站点或公共配置变化时要求 restart，不静默复用旧登录。密码仅在启动时读取，修改当前密码也须 restart。CLI 配置错误输出到 stderr。

正常加载拒绝旧 [account]、bridge.environment 和 api.token，并提示 just migrate-config。迁移把旧账户放到原环境对应组，simnow 映射 sandbox，旧环境省略时沿用原 simnow 默认值；另一组使用示例的空凭证配置，公共自定义值保留。旧 account/environment 与新 accounts/mode 结构混用时拒绝；转换后须通过新模型校验，失败不改写。首次备份为 config.toml.bak，已有备份时改用 config.toml.<唯一编号>.bak；完整原文含注释保存在备份中，不猜测注释中的备用凭证。原子替换前核对源文件未变化，备份及新文件均为 0600；重复迁移新格式只验证，不改写或增加备份。

HTTP 无鉴权。凭证、备份、debug 和数据不进入版本控制或构建上下文。宿主端口只发布到 127.0.0.1。

API、VNC、noVNC 的固定默认端口依次为 45173、45174、45175；TOML 的 `api.port`、`vnc.port`、`vnc.web_port` 可覆盖默认值。配置模型、示例、镜像 EXPOSE 和访问示例使用一致的默认值，实际容器监听及宿主映射均使用配置值。相同宿主发布地址和端口冲突时启动失败，不尝试随机端口；项目接受这一单实例限制，不增加全局容器数量锁。

首次启动使用 `just run` 和 `just run vnc`：前者默认 headless，后者启用 VNC，均先准备及校验配置，再利用缓存构建对应镜像并启动。不再提供 `just up` 或 `just run headless`。辅助命令 `just build [all|headless|vnc]` 只构建，默认两种镜像。run 遇到镜像或启动配置与现有实例不符时报错，不自动停止现有实例。`CFB_CONTAINER`、`CFB_VOLUME` 可为隔离环境选择不同容器和卷名，端口仍由 TOML 指定。

显式重建重启使用 `just restart` 或 `just restart vnc`，变体选择与 run 一致。先校验配置并利用缓存构建选定镜像，停止前再次校验配置；成功后停止、删除本项目同名容器，再沿同一启动链路创建实例。构建或配置失败不停止旧容器，不删除数据卷，不操作非受管容器。此入口可用于代码升级和已编辑配置的环境/账户切换；不会同时运行新旧实例。

`just status/logs/screenshot/clean` 提供运维；pause/resume 由唯一执行器处理，先等待当前操作退出，再允许人工接管；恢复前检查账户与界面。命令通过容器内运维入口及受管实例的 Unix socket 执行；停止后的 clean 需取得目录排他锁。旧宿主 Python 入口已退出。

## HTTP 诊断

| 路径 | 含义 |
| --- | --- |
| GET /healthz | HTTP 存活，不检测终端 |
| GET /v1/status | 当前 Pydantic 状态对象 |
| GET /readyz | trading_ready 为真返回 200，否则 503 |
| GET /v1/desktop/screenshot | 虚拟屏幕 PNG；不可截图时明确报错 |
| GET /docs、/openapi.json | FastAPI 文档与接口模型 |

响应带 X-Request-ID 和 no-store，不记录请求头或凭证。桌面尚未接入执行器时 stage 为 terminal_bootstrap；接入后为 terminal_execution，状态区分登录、连接、队列、暂停和 blocked。trading_ready 要求真实身份、连接、交易日和执行权就绪；窗口可见不证明交易就绪。内部执行链路及能力边界见 [terminal_execution.md](terminal_execution.md)，八个业务路由已由 [api.md](api.md) 定义并接入同一 FastAPI 服务。

状态对象的 environment/request_mode/broker_id/site 描述当前启动绑定；不显示账号密码，也不通过状态查询重新登录。实盘具有与适配范围一致的交易能力，不设强制只读模式。

reconnect 对象包含 enabled、interval_seconds、state、attempts、last_failure_at、last_error、next_retry_at、manual_required。state 为 idle/waiting/reconnecting/paused/manual/disabled；无账户时禁用。attempts 是本实例的自动恢复尝试次数，不含首次启动；最近失败与次数在恢复成功后保留。时间为 UTC ISO8601 或 null，定时执行使用单调时钟。暂停/停止时 next_retry_at 为 null，不代表清除原计时；恢复时期限已过可立即尝试。断线期间 healthz 仍 200，readyz 为 503，status 仍可读。

## 容量与生命周期

Python、Wine、桌面和辅助进程输出由有界收集器写为 JSON 行日志，不把子进程 stdout/stderr 绑定到无限追加文件。单文件默认 20 MiB、日志合计 200 MiB；Podman k8s-file 日志另外限制 20 MiB。

`logging.max_total_bytes` 和 `artifacts.max_total_bytes` 分别控制日志及工件的总容量，300 MiB 为 314572800 字节。桥接日志在写入前检查容量，工件在创建/收尾及导出前后检查容量；后台按 `artifacts.cleanup_interval_seconds`（默认 60 秒）检查。需要腾出空间时按最旧顺序删除可清理文件，不依赖保存天数。

终端 logs 目录下已识别的时间戳诊断文件及旧启动进程日志纳入预算。活动文件通过句柄检查保护，不强制截断；无法安全治理的活动写入超限时，服务停止终端并记录不可用状态。不能仅凭后缀递归删除 Wine、账户或 flow 文件。

artifacts 中只管理明确登记的 op 工件目录，记录 active/ended/protected 状态。成功解析后释放临时 CSV；失败资料保留至容量需要淘汰，默认共享 300 MiB，无年龄清理。清理保护活动和未知提交证据，低磁盘或无法满足预算返回 STORAGE_UNAVAILABLE。

## 验证入口

开发静态检查与容器无关：宿主安装 uv，首次通过 `uv sync --locked` 准备项目 `.venv`，随后 `just check` 直接执行 `uvx ty check`。ty 自动发现本地依赖环境，检查目标保持 Python 3.11，不再固定容器路径 `/opt/venv`。检查不会构建镜像、启动应用或读取账户配置。

`just test` 继续在工具容器中运行少量离线 pytest，关闭网络且不挂载实际账户配置。截图检查使用独立 Xvfb 验证完整 PNG、颜色、尺寸及显示不可用错误，不启动 Wine 或账户会话。应用运行及原生 helper 构建仍使用 Podman；静态检查不受该运行边界限制。完整 GUI、交易和状态变化的在线覆盖不能由类型检查推导。
