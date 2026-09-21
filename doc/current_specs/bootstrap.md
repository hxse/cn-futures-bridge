# SimNow 容器运行与诊断

项目遵循 [项目原则](project_principles.md)。应用只在 Podman 内运行，宿主 just/bash 负责容器编排；配置、HTTP 和内部运维消息使用 Pydantic，唯一 HTTP 服务为 FastAPI/Uvicorn 单 worker。

## 终端与镜像

`terminal.lock.toml` 锁定普通快期2 2.93.405.1998 的官方包及 SHA256，构建只保留原包的上期技术/9999 模拟站点，不修改其认证参数。WineHQ 11.0 的 32 位包版本锁定 `11.0.0.0~bookworm-1`；启动直接运行 q7_release.exe，先验证持久目录与镜像清单一致。

headless 与 vnc 共用终端、Python 依赖和业务代码；vnc 只增加远程桌面组件。两者都有 Xvfb/Openbox、截图和中文字体，1280×800/96 DPI 为默认设置。先确认显示与窗口管理器，再引导 Wine 会话、启动客户端；持久化前缀不跳过 Wine 会话引导。

数据卷默认 `/data`，包含 terminal、wine、logs、artifacts，操作数据库位于 state 目录。`.session.lock` 排他锁保护整个实例；停止只处理本容器进程，保留数据卷。程序退出或启动失败不自动重登和无限重启。

客户端已有独立探针能力见 [terminal_capabilities.md](terminal_capabilities.md) 和 [trading_status.md](trading_status.md)，历史探针数据不等于正式 API 全链路已经在线核验。

## 配置与入口

唯一配置为 config.toml/config.example.toml，节包括 bridge、account、api、desktop、vnc、execution、logging、artifacts。未知字段和非法类型拒绝；Pydantic 对象隐藏凭证，错误不回显原值。账户为空可启动；配置账户后启动执行器，尝试一次 SimNow 电信2登录，失败不自动重试。

HTTP 无鉴权，api.token 不再接受；`just migrate-config` 显式迁移并保存 0600 的 config.toml.bak，已有备份不覆盖。凭证、备份、debug 和数据不进入构建上下文。宿主端口只发布到 127.0.0.1。

API、VNC、noVNC 的固定默认端口依次为 45173、45174、45175；TOML 的 `api.port`、`vnc.port`、`vnc.web_port` 可覆盖默认值。配置模型、示例、镜像 EXPOSE 和访问示例使用一致的默认值，实际容器监听及宿主映射均使用配置值。相同宿主发布地址和端口冲突时启动失败，不尝试随机端口；项目接受这一单实例限制，不增加全局容器数量锁。

启动入口只有 `just run` 和 `just run vnc`：前者默认 headless，后者启用 VNC，均先准备及校验配置，再利用缓存构建对应镜像并启动。不再提供 `just up` 或 `just run headless`。辅助命令 `just build [all|headless|vnc]` 只构建，默认两种镜像。镜像与同名现有实例不符时要求显式 `just down` 后再运行对应的 run 命令，不自动停止现有实例。`CFB_CONTAINER`、`CFB_VOLUME` 可为隔离环境选择不同容器和卷名，端口仍由 TOML 指定。

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

## 容量与生命周期

Python、Wine、桌面和辅助进程输出由有界收集器写为 JSON 行日志，不把子进程 stdout/stderr 绑定到无限追加文件。单文件默认 20 MiB、日志合计 200 MiB；Podman k8s-file 日志另外限制 20 MiB。

`logging.max_total_bytes` 和 `artifacts.max_total_bytes` 分别控制日志及工件的总容量，300 MiB 为 314572800 字节。桥接日志在写入前检查容量，工件在创建/收尾及导出前后检查容量；后台按 `artifacts.cleanup_interval_seconds`（默认 60 秒）检查。需要腾出空间时按最旧顺序删除可清理文件，不依赖保存天数。

终端 logs 目录下已识别的时间戳诊断文件及旧启动进程日志纳入预算。活动文件通过句柄检查保护，不强制截断；无法安全治理的活动写入超限时，服务停止终端并记录不可用状态。不能仅凭后缀递归删除 Wine、账户或 flow 文件。

artifacts 中只管理明确登记的 op 工件目录，记录 active/ended/protected 状态。成功解析后释放临时 CSV；失败资料保留至容量需要淘汰，默认共享 300 MiB，无年龄清理。清理保护活动和未知提交证据，低磁盘或无法满足预算返回 STORAGE_UNAVAILABLE。

## 验证入口

开发静态检查与容器无关：宿主安装 uv，首次通过 `uv sync --locked` 准备项目 `.venv`，随后 `just check` 直接执行 `uvx ty check`。ty 自动发现本地依赖环境，检查目标保持 Python 3.11，不再固定容器路径 `/opt/venv`。检查不会构建镜像、启动应用或读取账户配置。

`just test` 继续在工具容器中运行少量离线 pytest，关闭网络且不挂载实际账户配置。应用运行及原生 helper 构建仍使用 Podman；静态检查不受该运行边界限制。完整 GUI、交易和状态变化的在线覆盖不能由类型检查推导。
