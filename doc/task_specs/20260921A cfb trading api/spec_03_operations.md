# 配置、容器入口与运行治理

## 运行与部署边界

应用只支持在 Podman 镜像内运行。宿主需要 just、Podman 和基础 shell 工具，不需要 Python/uv/Wine 业务环境；配置校验、依赖安装、原生 helper 编译、离线测试及在线探针均通过容器完成。

保留固定 Wine 11.0、32 位终端、字体与显示设置。原生 helper 在独立构建阶段编译后复制到公共 runtime，编译器不进入运行镜像；headless 与 vnc 共用相同 FastAPI、执行器、依赖和配置，仅远程桌面组件不同。

FastAPI/Uvicorn 固定一个服务 worker，不使用自动 reload。生命周期负责启动/停止执行器、日志收集器及既有终端 runtime；失败状态可以诊断，不能自动重复初始化交易。数据目录继续通过操作系统文件锁排他使用。

HTTP 服务没有 Bearer/token 鉴权，也不从配置自动恢复鉴权。容器监听配置的 API 地址，宿主发布始终显式为 `127.0.0.1:<port>:<port>`，VNC/noVNC 也仅回环发布；不使用 host 网络或裸 `-p port:port`。业务调用来自上层代理，但不在本项目实现该代理。

## 配置唯一来源

正式文件仍是 `config.toml` 与同 schema 的 `config.example.toml`。以下是最终完整示例；省略新增治理节时使用这里的默认值，未知节/字段和非法值报错，不静默忽略。

```toml
[bridge]
environment = "simnow"
data_dir = "/data"
startup_timeout_seconds = 90

[account]
username = ""
password = ""

[api]
host = "0.0.0.0"
port = 8000

[desktop]
width = 1280
height = 800
dpi = 96

[vnc]
port = 5900
web_port = 6080

[execution]
queue_capacity = 32
queue_timeout_ms = 5000
step_timeout_ms = 3000
poll_interval_ms = 10
gui_action_gap_ms = 10
idempotency_ttl_hours = 168
journal_max_bytes = 134217728

[logging]
level = "INFO"
max_file_bytes = 20971520
max_total_bytes = 209715200

[artifacts]
max_total_bytes = 314572800
cleanup_interval_seconds = 60
min_free_bytes = 536870912
```

所有计数/毫秒/字节参数为正整数，bool 不接受为数字。`gui_action_gap_ms` 允许 0，其他等待期限不得为 0；`queue_capacity` 范围 1～1024，轮询间隔 1～1000 ms，期限不超过 300000 ms。`poll_interval_ms` 不大于阶段超时；日志单文件上限不大于总量；三个空间预算必须为有限正值。日志等级限 `DEBUG/INFO/WARNING/ERROR`。现有端口、尺寸及 SimNow 校验继续保留，不借此修改账户用途。

queue_timeout_ms 约束排队，超时未执行则取消；step_timeout_ms 约束本地动作、单次 CSV/原生状态读取及复位，不设置柜台回报等待期限。gui_action_gap_ms 用于需要界面响应的动作间最小间隔，不强制套用到已经同步完成的 CSV 读写或原生状态查询；就绪检查仍然必需。

交易状态复用已登录快期及同一常驻原生控制器，不增加时间规则配置、日历数据文件或请求时联网更新。固定主程序与核心 DLL 的身份、状态符号及映射由受控适配版本管理，不允许外部请求指定 DLL、函数或内存地址。

日志和工件配置没有按天/小时到期删除字段。cleanup_interval_seconds 只控制检查容量的频率；idempotency_ttl_hours 是独立的业务防重发保护期，不是诊断文件清理时间。

有账户配置时，执行器阶段在启动时尝试一次自动登录 SimNow 并核对实际账户、站点、连接及交易日；无账户或登录失败停留在可诊断状态，不接收交易。登录不能在每个 HTTP 请求中重复执行，不在 API 参数中接收账号密码。

`api.token` 正式退出。`just migrate-config` 提供一次性迁移：识别旧 schema，移除该键、补充新节默认值，原子写入并保存 0600 的 `config.toml.bak`；已有备份不覆盖，失败保持原文件。新旧配置值不输出到日志。旧键未迁移时明确报配置错误，不能悄悄保留一套鉴权链路。

实际配置及其备份加入忽略规则并排除镜像上下文；更新 `config.example.toml`、配置解析、启动调用方与相关测试必须在同一阶段完成。只验证配置的入口不得启动终端或登录。

## just 命令契约

| 命令 | 行为 |
| --- | --- |
| `just` | 列出命令及用途，不启动交易环境 |
| `just init-config` | 缺失时从示例创建本地配置，0600，已有文件不覆盖 |
| `just migrate-config` | 上述显式配置迁移，不启动服务 |
| `just fetch` | 容器内获取并校验锁定安装包，已有有效文件复用 |
| `just build [variant]` | variant 为 all/vnc/headless，默认 all；构建前确保锁定包可用 |
| `just up [variant]` | 默认 vnc，使用已有镜像启动；缺失配置时初始化，配置无效则停止 |
| `just run [variant]` | 默认 vnc，依次准备配置、构建对应镜像、启动，一行完成 |
| `just down` | 停止并移除本项目容器，保留数据卷及未决操作记录 |
| `just status` | 读取容器与服务状态，区分 HTTP 存活、登录就绪、可执行本地动作和暂停；不保证柜台接受 |
| `just logs` | 查看脱敏日志，保持与请求追踪编号可关联 |
| `just screenshot [output]` | 默认 `debug/desktop.png`，容器截图并导出本地文件 |
| `just pause` | 暂停派发并等待当前原子操作退出；超时报告未暂停成功，不允许人工抢占 |
| `just resume` | 确认旧执行权退出、身份及界面基线安全后恢复；不自动对账、补单或重放未知请求 |
| `just clean` | 通过容器内清理器执行保留规则，不清空数据卷或全部日志 |
| `just check` | 容器内执行 `uvx ty check`，本轮主要代码检查入口 |
| `just test` | 容器内少量离线 pytest 冒烟检查，网络关闭，不挂载真实账户配置、不登录或交易 |

运行中的同名受管容器可由 up 返回现状；不属于本项目的同名容器报错。变体或镜像不匹配时要求显式 down 后再 up，不能为满足 run 自动杀掉可能正在下单的实例。down 不回滚已提交交易，不重放队列；未结束操作持久化为可恢复状态。

just 是唯一正式用户入口。`tools/bridge.py` 原宿主运行职责迁移后退出，不保留并行的 `uv run python tools/bridge.py` 工作流；可复用其中逻辑作为容器内模块，但不能要求宿主导入应用配置模块。阶段交付同步 README，避免旧命令仍被推荐。

所有正式入口由本任务实施；Pydantic 配置、FastAPI、uv 依赖锁、类型检查及工具镜像保持同一来源。

## 诊断及人工接管

保留 `/healthz`、`/readyz`、`/v1/status`、`/v1/desktop/screenshot`；增加 FastAPI 自带 `/docs`、`/openapi.json`，不计入八个业务路由。四个既有诊断路径保持用途与原字段，移除鉴权。`/healthz` 只表示 HTTP 存活，不探测 GUI。

`/v1/status` 保留现有字段，并增加 `executor_state`、`queue_depth`、`active_operation_id`、`unresolved_operations`、`capabilities`。`executor_state` 为 `unavailable/starting/idle/running/paused/blocked/stopping`；活动编号就是 CFB 逻辑 request_id，没有活动时为 null。未决数只统计本地动作影响不明的 unknown，不把尚未查成交的 submitted 算成故障；能力为 supported/unsupported/unverified，不能把未验证写 supported。

capabilities 固定包含八个业务路由后缀及 `limit_order_ioc, limit_order_fok, close_today, close_yesterday, cancel_session_order, hedge_arbitrage, hedge_hedge, invest_unit, non_cny_balance`。状态表示当前版本和运行配置的适配能力，不能保证任意合约、时段或账户权限都允许操作；执行时仍须核对具体前置条件。复合路由的 supported 只表示其基础分支可用，附加分支独立判断。

运行治理阶段仍使用 stage=terminal_bootstrap 及原启动就绪含义；执行器接入后为 terminal_execution，login_state 为 not_configured/logging_in/logged_in/failed/disconnected。trading_ready 表示身份、连接、必要交易日、执行器、存储及基础本地提交能力就绪；暂停、执行器故障或界面不能确认安全时为 false，不因订单未成交而变 false。automation_enabled 表示执行器已启用且允许派发，不由窗口可见推导；原生交易状态查询必须有已核对的登录、连接及当前会话数据。

执行器阶段的 `/readyz` 只有 trading_ready=true 才返回 200，否则 503，状态正文与 `/v1/status` 相同；不把当前市场休盘等同于服务故障。原窗口就绪语义随 stage 明确退出，测试与用户说明同时迁移。

pause/resume/clean 通过容器内部控制通道进入拥有者，不新增 HTTP 业务路由，也不让独立 `podman exec` 程序直接操作 GUI 或删除活动文件。VNC 连接可用于观看，人工输入只允许在暂停成功后进行。

## 分步日志与空间治理

每条结构化日志为一行 JSON，至少包含 timestamp、level、request_id、action、step、event、duration_ms、outcome、error_code；未知值为 null。步骤区分校验、入队、界面准备、账户核对、导入、回读、定位、发送、单次订单观察、收尾和返回，每步 start/end/error 可关联。记录排队、本地执行、复位及 HTTP 总耗时，真实柜台/成交延迟属于独立查询或验证指标。

原生交易状态步骤记录会话/连接与版本校验、实际品种及交易所、原生状态值、映射结果和耗时；未知或失效数据保留错误原因。读取成功无需截图或生成 CSV，不因每次查询创建独立无上限文件。

CFB 逻辑请求编号与幂等键分开；日志不记录密码、鉴权头、完整配置或登录键盘输入。只记录允许的业务参数，账户身份脱敏。复用幂等结果时保留原逻辑编号，并记录本次 HTTP 尝试与它的关联。

异常先使用现成的文字、CSV 和步骤记录，只有未知窗口、焦点异常、GUI 卡住或复位失败等确需视觉证据时截图。参数校验、排队拒绝和已有充分错误文字的情况默认不截图；同一未解除异常不因每次新请求重复拍图。截图失败记录原因，不改写已知提交结果。第一版不增加复杂异常分类、自动点击未知弹窗或自动修复规则。

INFO 记录每个业务步骤的结果，DEBUG 可记录细粒度按键和轮询详情；正常轮询在 INFO 汇总次数及总耗时，不以每 10 ms 一条 INFO 制造洪水。可异步批量写详细日志，但发送前操作状态必须已持久化，不能以日志最终会写出代替提交前记录。

终端、Wine、桌面及 helper 的 stdout/stderr 必须持续被收集并写入受管轮转文件，不能依赖子进程长期持有一个无限追加文件描述符。收集缓冲有上限，错误须上报；轮转和清理不能堵塞子进程输出导致 GUI 假死。

| 数据 | 生命周期及上限 |
| --- | --- |
| 桥接及已登记的进程/诊断日志 | 默认单文件 20 MiB、合计 200 MiB；只按容量轮转及淘汰最旧已关闭文件，无保留天数 |
| 成功操作的临时 CSV | 完成解析、核对及必要操作摘要登记后删除；不能在仍由终端写入时删除 |
| 失败 CSV、必要截图及取证 | 共享默认 300 MiB（314572800 字节）硬上限，按生成顺序先淘汰最旧的已结束工件；无时间到期规则 |
| 最小操作数据库及幂等记录 | 默认预算 128 MiB（含 SQLite/WAL）；已结束请求保留 168 小时防重发保护，unknown 不自动过期；不保存整套自动交易恢复状态机 |
| Podman 容器输出日志 | 使用可设上限的明确日志驱动，默认单容器 20 MiB；不能遗漏应用日志之外的第二份输出 |

应用日志和工件位于 `/data/logs`、`/data/artifacts`；操作数据库位于 `/data/state/operations.sqlite3`。清理器只管理明确登记的路径，不递归清空 /data，不删除 Wine 前缀、终端会话/flow、账户配置或锁文件。终端自写日志须先分类其用途、活动句柄与安全轮转规则；不能仅按 `.log` 后缀认定可删。

每次生成工件先校验容量，必要时先淘汰旧的已结束工件；单文件超出预算停止并报告。活动文件与确需保护的最小未知提交证据不能被普通清理误删，且它们也计入 300 MiB 总预算。无法安全腾出空间就明确报错，不扩大预算或删除防重发依据。容量未触发时，不能仅因文件存放三天或七天而删除。

达到硬预算或磁盘低于 min_free_bytes 时先做有界清理，仍不足则 `STORAGE_UNAVAILABLE`。未决操作保护预算不能靠自动删记录解除；若终端自写日志无法安全轮转且继续增长，须保存操作状态并受控停止其写入进程，保留明确错误，不承诺仍能正常交易。

清理在启动、周期以及接近容量阈值时触发，按文件活跃登记协调；不能与导入、导出、截图或数据库事务争抢同一文件。SQL 记录删除、WAL checkpoint 及文件回收均须计入预算，不能只删行却不回收磁盘。可清理的记录不能仍是恢复或幂等有效期内的唯一依据。

## 生命周期及验证口径

启动按“配置/目录排他锁 → 日志及预算校验 → 桌面与终端 → 执行器/helper → 可选自动登录 → 身份和数据校验”推进，API 可以提前提供不依赖 GUI 的诊断。停止先拒绝新请求、撤销未执行队列项，再完成或标记当前操作、停止所属进程；不停止共享宿主进程，不自动撤销历史订单。

执行器所有权失败、登录失败、空间不足和人工接管不触发自动重新发单。重启将不完整动作标为 unknown 并保留防重发记录；恢复前确认旧调用已终止、身份和界面基线安全。不自动补单、自动对账修复或清空未知请求；后续结果由调用方查询及必要人工核对。

这里的配置和命令是后续最终契约；各实施阶段只运行自己已引入的验证范围。本轮不新增在线测试套件；`just check` 和少量离线 pytest 不能被描述为真实成交或全部 GUI 行为验证。
