# SimNow 容器运行与诊断

项目遵循 [项目原则](project_principles.md)。应用只在 Podman 内运行，宿主 just/bash 负责容器编排；配置、HTTP 和内部运维消息使用 Pydantic，唯一 HTTP 服务为 FastAPI/Uvicorn 单 worker。

## 终端与镜像

`terminal.lock.toml` 锁定普通快期2 2.93.405.1998 的官方包及 SHA256，构建只保留原包的上期技术/9999 模拟站点，不修改其认证参数。WineHQ 11.0 的 32 位包版本锁定 `11.0.0.0~bookworm-1`；启动直接运行 q7_release.exe，先验证持久目录与镜像清单一致。

headless 与 vnc 共用终端、Python 依赖和业务代码；vnc 只增加远程桌面组件。两者都有 Xvfb/Openbox、截图和中文字体，1280×800/96 DPI 为默认设置。先确认显示与窗口管理器，再引导 Wine 会话、启动客户端；持久化前缀不跳过 Wine 会话引导。

数据卷默认 `/data`，包含 terminal、wine、logs、artifacts，操作数据库位于 state 目录。`.session.lock` 排他锁保护整个实例；停止只处理本容器进程，保留数据卷。程序退出或启动失败不自动重登和无限重启。

客户端已有独立探针能力见 [terminal_capabilities.md](terminal_capabilities.md) 和 [trading_status.md](trading_status.md)，不等于对应 REST 已发布。

## 配置与入口

唯一配置为 config.toml/config.example.toml，节包括 bridge、account、api、desktop、vnc、execution、logging、artifacts。未知字段和非法类型拒绝；Pydantic 对象隐藏凭证，错误不回显原值。账户为空可启动；配置账户后启动执行器，尝试一次 SimNow 电信2登录，失败不自动重试。

HTTP 无鉴权，api.token 不再接受；`just migrate-config` 显式迁移并保存 0600 的 config.toml.bak，已有备份不覆盖。凭证、备份、debug 和数据不进入构建上下文。宿主端口只发布到 127.0.0.1。

`just run [vnc|headless]` 构建并启动，`just build` 默认构建两种镜像，`just up` 默认 vnc。镜像与同名现有实例不符时要求显式 down；不会自动停止现有实例。`CFB_CONTAINER`、`CFB_VOLUME` 可为隔离环境选择不同容器和卷名，端口仍由 TOML 指定。

`just status/logs/screenshot/clean` 提供运维；pause/resume 由唯一执行器处理，先等待当前操作退出，再允许人工接管；恢复前检查账户与界面。命令通过容器内运维入口及受管实例的 Unix socket 执行；停止后的 clean 需取得目录排他锁。旧宿主 Python 入口已退出。

## HTTP 诊断

| 路径 | 含义 |
| --- | --- |
| GET /healthz | HTTP 存活，不检测终端 |
| GET /v1/status | 当前 Pydantic 状态对象 |
| GET /readyz | trading_ready 为真返回 200，否则 503 |
| GET /v1/desktop/screenshot | 虚拟屏幕 PNG；不可截图时明确报错 |
| GET /docs、/openapi.json | FastAPI 文档与接口模型 |

响应带 X-Request-ID 和 no-store，不记录请求头或凭证。桌面尚未接入执行器时 stage 为 terminal_bootstrap；接入后为 terminal_execution，状态区分登录、连接、队列、暂停和 blocked。trading_ready 要求真实身份、连接、交易日和执行权就绪；窗口可见不证明交易就绪。内部执行链路及能力边界见 [terminal_execution.md](terminal_execution.md)，业务路由由后续集成阶段接入。

## 容量与生命周期

Python、Wine、桌面和辅助进程输出由有界收集器写为 JSON 行日志，不把子进程 stdout/stderr 绑定到无限追加文件。单文件默认 20 MiB、日志合计 200 MiB；Podman k8s-file 日志另外限制 20 MiB。

终端 logs 目录下已识别的时间戳诊断文件及旧启动进程日志纳入预算。活动文件通过句柄检查保护，不强制截断；无法安全治理的活动写入超限时，服务停止终端并记录不可用状态。不能仅凭后缀递归删除 Wine、账户或 flow 文件。

artifacts 中只管理明确登记的 op 工件目录，记录 active/ended/protected 状态。成功解析后释放临时 CSV；失败资料保留至容量需要淘汰，默认共享 300 MiB，无年龄清理。清理保护活动和未知提交证据，低磁盘或无法满足预算返回 STORAGE_UNAVAILABLE。

## 验证入口

`just check` 构建工具镜像并在容器内执行 `uvx ty check`；`just test` 运行少量离线 pytest。检查不挂载实际账户配置、不启动 Wine、运行时关闭网络。Python 依赖由 uv.lock 锁定，宿主不需要部署应用依赖。完整 GUI、交易和状态变化的在线覆盖不能由类型检查推导。
