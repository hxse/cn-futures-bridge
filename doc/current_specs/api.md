# CFB HTTP 接口

## 运行与范围

FastAPI 提供三个 POST、五个 GET 业务路由，统一使用 `/cfb` 前缀。方法、名称及常用输入对齐参考 CTP 服务，返回使用 CFB 自己的必要字段，不承诺原始 CTP 结构。参考服务不是运行依赖。

每个实例只支持一个启动时选定的 SimNow 或华安实盘账户。config.toml 可保存 accounts.sandbox/live 两组凭证，bridge.mode 选择其中一组；请求 mode 必须与启动选择一致，否则返回 409/ENVIRONMENT_MISMATCH，不触发登录、切换、入队或幂等重放。sandbox 内部仍对应 simnow；/v1/status 的 environment 保留 simnow/live，request_mode 为 sandbox/live。HTTP 无鉴权，Podman 发布端口仅绑定宿主 127.0.0.1，默认文档入口为 http://127.0.0.1:45173/docs；完整参数、枚举和返回模型由 /openapi.json 提供。

应用仅在容器内运行，使用 `just run` 启动 headless，或 `just run vnc` 启用远程桌面；两者均自动准备配置、构建和启动。配置、生命周期及容量治理见 [bootstrap.md](bootstrap.md)，内部动作、会话和复位见 [terminal_execution.md](terminal_execution.md)。

## 路由与输入

| 方法 | 路径 | 参数 |
| --- | --- | --- |
| POST | /cfb/create_market_order | mode、exchange_id、instrument_id、side、offset、volume、hedge_flag、invest_unit_id |
| POST | /cfb/create_limit_order | 市价请求的公共字段，加 price、time_in_force |
| POST | /cfb/cancel_order | mode、exchange_id、instrument_id、invest_unit_id，以及 by 指定的一组身份字段 |
| GET | /cfb/fetch_orders | mode；可选 exchange_id、instrument_id、order_sys_id，或完整引用组 trading_day/front_id/session_id/order_ref；insert_time_start、insert_time_end；invest_unit_id |
| GET | /cfb/fetch_trades | mode；可选 exchange_id、instrument_id、trade_id、trade_time_start、trade_time_end；invest_unit_id |
| GET | /cfb/fetch_positions | mode；可选 exchange_id、instrument_id；invest_unit_id |
| GET | /cfb/fetch_balance | mode、currency_id |
| GET | /cfb/fetch_trading_status | mode；必填 exchange_id、product_id |

POST 只接受 application/json 对象；GET 使用 query。未知 JSON/query 字段、重复单值 query、重复幂等头或 POST 中夹带 query 均拒绝，不静默忽略条件。

公共默认值：mode=sandbox、hedge_flag=speculation、invest_unit_id=""、time_in_force=GFD、currency_id=CNY。交易所为 SHFE/INE/DCE/CZCE/CFFEX/GFEX；下单、撤单和状态查询必填交易所，其他查询可省略。

实盘必须明确传 mode=live，省略时按 sandbox 校验并报环境不匹配。环境匹配在 HTTP、调度准入和执行器复核；实盘与模拟盘使用同一能力规则，当前限价 GFD 等基础分支不会因 live 被禁用，未核验分支仍返回 501。AI 的实盘交易测试需用户明确授权，该约束不是只读 API 权限。

合约格式为 `^[A-Za-z][A-Za-z0-9]{0,79}$`，保留大小写；品种为 `^[A-Za-z][A-Za-z0-9_]{0,79}$`，同样区分大小写。执行时与终端实际标的及所属交易所精确核对，不能以自动补全或模糊匹配替代；资料未就绪与标的不匹配分别报错。

side 为 buy/sell，offset 为 open/close/close_today/close_yesterday；volume 是 1～2147483647 的严格整数，拒绝 bool、数字字符串和小数。price 为正的有限 JSON number，拒绝数字字符串、NaN、Infinity；执行时核对可取得的报价步长与涨跌停，不自动改价或改手数。

time_in_force 为 GFD/IOC/FOK；hedge_flag 为 speculation/arbitrage/hedge。支持模拟市价、限价 GFD/IOC、投机、开仓或普通平仓。模拟市价以买入涨停价、卖出跌停价的限价 IOC 实现，实际参数由 execution 返回，见 [限价模拟市价](market_orders.md)。FOK、指定平今/平昨和非投机分支仍返回 501/CAPABILITY_NOT_SUPPORTED；不以 GFD 或发送后主动撤单冒充 IOC/FOK。

撤单必须声明 by：exchange_order 要求 order_sys_id；session_order 要求 front_id、session_id、order_ref。字段组不能混用。订单编号保留原始空格及字符串身份，长度 1～20，至少包含一个非空格可见 ASCII 字符；不会转整数、补空格或移除前缀。front_id 为 0～2147483647，session_id 为有符号 32 位整数，order_ref 为 1～12 位数字字符串。当前只启用交易所编号的唯一目标定位；会话方式尚未核验。

trade_id 与 order_sys_id 使用相同标识规则。时间过滤为严格 HH:MM:SS 的闭区间，不支持跨午夜，起点不得大于终点。只查询终端当前快照，不补历史、不接受任意日期。非空投资单元与非人民币资金当前不支持；invest_unit_id 最长 16 个可见 ASCII 字符，currency_id 格式为三个大写字母。

## 提交、独立查询与追踪

所有可执行业务请求进入同一个 FIFO，包括原生状态查询。只读诊断不等待业务队列。HTTP 等待为异步；客户端断开时取消尚未开始的队列项，已经开始的动作仍由执行器完成核对、收尾和持久化，不因 HTTP 退出释放 GUI。

未连接、等待重连或重连中，有效的新业务请求立即返回 503/SERVICE_NOT_READY；不等待数分钟，不保留到恢复后执行。断线时未开始的队列项以未执行结束，已开始的操作保留已知副作用事实；执行中发现真实断线可返回 503/CONNECTION_LOST。参数、环境、能力校验仍先执行；已保存幂等响应仍可读取。请求不改变重连期限。

有有效重连计划时，503 响应附 Retry-After：等待中为距下次尝试秒数、至少 1；正在恢复时使用配置间隔作建议，不保证届时已恢复。人工暂停、关闭恢复或需要人工处理时不附此头。错误正文格式不变。

三个 POST 支持可选 Idempotency-Key，1～128 个非空格可见 ASCII 字符。同账户/环境/券商中，同键同参数重放原响应和逻辑编号，同键不同参数 409，原请求尚在执行 409；不同环境和券商的结果隔离，无键的同参数请求仍是独立指令。未知提交不自动到期或重新发送。

正常本地提交且收尾确认后返回 202，包含 request_id、submission_status、order_id、identity、execution、verification。HTTP 202 本身不是成交确认。

submitted 表示本地动作完成，不代表柜台接受或成交。限价 GFD 通过 CSV 导入；限价 IOC 和模拟市价通过下单板生成手动预埋单。开平仓共用实际发送引用捕获：identity 包含 exchange_id、instrument_id、trading_day、front_id、session_id、order_ref。order_id 来自该完整引用的真实订单，尚未分配时为 null。发送前保存 CSV 基线，发送后默认最多 3 轮、轮间 100 ms 精确回读，已确认时提前结束；全程占用同一 FIFO，只发送一次。

POST 的 execution.kind 为 limit/emulated_market/cancel，price 和 time_in_force 为实际参数；撤单两者为 null。verification 包含 attempts、correlation、orders、trades、positions_before、positions_after 和 error_code。开平仓 source=terminal_csv_and_native，撤单 source=terminal_csv。

- status=observed：精确订单达到约定状态，其成交量与已关联 CSV 成交核对一致；拒单也可为 observed，须读取 orders[].status，不能把 observed 当成交成功。持仓仍是独立前后快照，其他操作也可能改变仓位。
- pending：限定轮数内状态/成交尚未到齐；ambiguous：精确引用或编号出现冲突；unavailable：读取失败，error_code 指明原因，未取得的后仓位为 null。
- correlation=order_ref：用捕获的真实引用和会话精确定位原生订单，取得交易所编号后核对同号 CSV。没有交易所编号的拒单使用该引用对应的原生订单回报；只返回原生成交对象明确关联到该订单的 CSV 成交，并填写其 order_id。
- correlation=order_id：仅用于已有精确编号的撤单，orders 是该编号对应的完整委托状态。已成交和已撤销分别如实返回，活动列表消失不直接判为撤单成功。

通知只清障和记录，不能改变 submission_status 或 verification。引用捕获缺失、重复或错配返回 unknown，不回退到参数候选；已捕获引用及订单号保存到防重发记录，进程中断的错误响应仍保留已知标识。下单前后 CSV 均使用新文件。同幂等键重放原结果，后续 GET 才取得新快照。

升级前保存的幂等响应原样重放，可能保留 snapshot_delta 和缺失的 identity，不重新认领历史请求。按引用查询针对终端现有记录；重启后终端可能不再保存纯本地拒单，此时空列表仅表示当前快照未找到，不能推断未曾提交或失败。

一个结果待确认的市价模拟响应示例：

```json
{"request_id":"cfb-example","submission_status":"submitted","order_id":null,"identity":{"exchange_id":"DCE","instrument_id":"m2701","trading_day":"20260924","front_id":3,"session_id":123,"order_ref":"18"},"execution":{"kind":"emulated_market","price":3618.0,"time_in_force":"IOC"},"verification":{"source":"terminal_csv_and_native","status":"pending","correlation":"order_ref","attempts":3,"orders":[],"trades":[],"positions_before":[],"positions_after":[],"error_code":null}}
```

价格仅为格式示例。positions_after=[] 表示成功读到空持仓，不表示本次报单失败。

### 调用方再次确认订单状态

开仓和平仓使用同一套查询方式，不需要新的确认路由。优先将下单响应的 **order_id 原样传给 GET /cfb/fetch_orders 的 order_sys_id**，同时带原请求的 mode、exchange_id、instrument_id。两个字段名称不同，但值是同一个真实交易所编号；CFB 的 request_id 仅供日志追踪，不能作为订单编号。

以下编号仅展示格式，须替换为实际响应值：

```bash
curl 'http://127.0.0.1:45173/cfb/fetch_orders?mode=sandbox&exchange_id=DCE&instrument_id=m2701&order_sys_id=648294'
```

order_id 为 null 不代表提交失败。若已返回 identity，就完整传入其中的六个字段（exchange_id、instrument_id、trading_day、front_id、session_id、order_ref），另带原 mode，且不传 order_sys_id。两种查询方式互斥；不能只凭 order_ref 定位订单。按引用查询只支持当前终端交易日，缺少成组字段报 422，其他交易日报 501，不用空列表掩盖不支持的历史查询。结果附 identity，source=terminal_csv_and_native；引用存在但 CSV 尚未更新时返回原生事实及 changing。

```bash
curl 'http://127.0.0.1:45173/cfb/fetch_orders?mode=sandbox&exchange_id=DCE&instrument_id=m2701&trading_day=20260924&front_id=3&session_id=123&order_ref=18'
```

使用实际响应里的 identity，不能照抄示例编号。原生编号的定长前置空格在适配边界去除，以对齐 CSV；外部传入的 order_sys_id 保持原样，不擅自改变其身份。

从 orders 数组读取目标订单的 status、filled_volume、remaining_volume，按以下含义确认：

| status | 调用方判断 |
| --- | --- |
| filled | 全部成交 |
| open | 挂单，尚未成交 |
| partially_filled | 部分成交，仍有未成交部分 |
| cancelled | 剩余部分已撤销；是否曾成交仍须读取 filled_volume |
| rejected | 订单被拒绝；可能没有交易所编号 |
| pending / unknown | 尚不能确认最终结果 |

remaining_volume 表示未成交手数，已撤单的剩余量不代表仍有活动挂单。consistency=stable 仅表示连续快照一致，不代表成交；verification.status=observed 仅表示约定状态及相关成交已核对，挂单和拒单也可被观察到。不要将这两个标记或 submission_status=submitted 当作 filled。

orders=[] 只表示当前终端快照未找到，读取失败则报错；两者均不能证明从未提交。仅查询当前交易日，不把编号当作跨账户、跨交易日的全局定位键。未能确认时，可在调用方设置有限次数和间隔再次 GET；查询失败或 HTTP 非 2xx 不触发重新下单。错误响应若保留 order_id 或 identity，也可沿用上述查询方式；两者都缺失时需结合请求日志核对，不能凭参数相似认领其他订单。

同 Idempotency-Key 重试 POST 只重放保存的旧响应，不刷新订单状态；确认最新状态必须调用 GET /cfb/fetch_orders。/docs 和 /openapi.json 的路由描述、查询参数与响应字段说明应直接呈现上述映射、两种查询示例及状态含义。

每个响应携带 X-Request-ID 和 Cache-Control: no-store，头中编号与正文一致。幂等重放保留原编号；CFB 编号不能用作订单编号。后续通过订单、成交确认结果，持仓未变本身不能证明下单失败。

查询返回 request_id、observed_at、source、trading_day，加 orders/trades/positions 数组或 balance 对象。observed_at 是读取时间，trading_day 来自终端且允许 null；缺少可信交易日时写操作不就绪。读取失败不返回旧快照、默认零或空数组。三个 CSV GET 至少读取两次、最多配置轮数，以委托状态/数量、成交记录、持仓数量等业务字段核对；返回最新完整快照及 consistency=stable/changing，浮动盈亏不参与持仓稳定判断。changing 表示读取期间数据仍有变化，不是空数据或读取失败。

订单包含订单号、交易所/合约、买卖/开平、总手数、成交/剩余手数、价格、状态、说明和时间。成交包含成交号、可空订单号、交易身份、手数/价格、时间及可空手续费/平仓盈亏。持仓按终端维度保留总仓、今昨仓、可平量、均价、保证金、盈亏，不能把可平量当总仓或强拆 CTP 行。

资金读取窗口的服务器列，包含 equity、available、margin，及可空 frozen_margin、frozen_commission、commission；values_source=server，source=terminal_text。其余账户表格 source=terminal_csv。字段及可空性以 OpenAPI 的 Pydantic 模型为准。

品种交易状态返回 request_id、exchange_id、product_id、observed_at、is_trading、source=terminal_native。只用快期 Product_Status：3 返回 true，1/2/4/5/6/7 返回 false；集合竞价归 false 不等于禁止所有报撤单。0、未知、断线或读取失败报错，详见 [trading_status.md](trading_status.md)。

## 错误与能力

错误包含 request_id、submission_status、order_id、identity、error，以及可空 execution/verification；error 包含 code、message、可空 details，参数问题 details 项为 loc/type/message。副作用前错误的 submission_status 为 null，明确本次拒绝为 rejected，影响不明为 unknown；已确认提交后的收尾错误保留 submitted、已知标识和已取得的观察结果。

| HTTP | 典型错误 |
| --- | --- |
| 409 | ENVIRONMENT_MISMATCH、ORDER_NOT_FOUND、ORDER_IDENTITY_AMBIGUOUS、MARKET_NOT_TRADING、IDEMPOTENCY_CONFLICT、OPERATION_IN_PROGRESS |
| 422 | INVALID_ARGUMENTS、ORDER_REJECTED |
| 429 | QUEUE_FULL |
| 501 | CAPABILITY_NOT_SUPPORTED |
| 502 | TERMINAL_DATA_INVALID、OPERATION_STATUS_UNKNOWN |
| 503 | SERVICE_NOT_READY、CONNECTION_LOST、GUI_UNRESPONSIVE、GUI_RESET_FAILED、STORAGE_UNAVAILABLE |
| 504 | QUEUE_TIMEOUT、QUERY_TIMEOUT |

`/v1/status` 的 capabilities 区分 supported/unsupported/unverified；基础分支 supported 不代表所有附加模式可用。状态还包含 executor_state、queue_depth、active_operation_id、unresolved_operations。unknown 保留防重发记录；正常 submitted 不因尚未查询成交而成为未决故障。

状态中的 reconnect 报告定时恢复计划，字段见 [bootstrap.md](bootstrap.md)。登录诊断区分 CONNECTION_FAILED、LOGIN_REQUIRES_ATTENTION 和原因不明的 QUERY_TIMEOUT。离线时 /healthz 仍为 200，/readyz 为 503，/v1/status 为 200；fetch_trading_status 报未就绪，不能把断线返回成休盘 false。

## 用户写法与验证边界

```bash
curl 'http://127.0.0.1:45173/cfb/fetch_balance'
curl 'http://127.0.0.1:45173/cfb/fetch_positions?exchange_id=CZCE&instrument_id=RM701'
curl 'http://127.0.0.1:45173/cfb/fetch_orders?exchange_id=CZCE&instrument_id=RM701'
curl 'http://127.0.0.1:45173/cfb/fetch_trading_status?exchange_id=CZCE&product_id=RM'
```

以下价格只展示请求格式：

```bash
curl -X POST 'http://127.0.0.1:45173/cfb/create_limit_order' \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: rm701-open-001' \
  -d '{"exchange_id":"CZCE","instrument_id":"RM701","side":"buy","offset":"open","volume":1,"price":2323}'
```

`just check` 为主要静态检查，`just test` 仅运行少量离线配置/诊断、防重发和 HTTP 契约检查。镜像构建及空账户启动不能证明完整模拟交易闭环。正式 API 的自动登录、非空查询、开平仓/撤单、重连和状态切换仍需在线核验；历史探针耗时不能当作 REST 性能保证。
