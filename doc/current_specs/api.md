# CFB HTTP 接口

## 运行与范围

FastAPI 提供三个 POST、五个 GET 业务路由，统一使用 `/cfb` 前缀。方法、名称及常用输入对齐参考 CTP 服务，返回使用 CFB 自己的必要字段，不承诺原始 CTP 结构。参考服务不是运行依赖。

每个实例只支持一个启动时选定的 SimNow 或华安实盘账户。simnow 对应请求 mode=sandbox，live 对应 mode=live；不匹配返回 409/ENVIRONMENT_MISMATCH，不触发登录、切换、入队或幂等重放。HTTP 无鉴权，Podman 发布端口仅绑定宿主 127.0.0.1，默认文档入口为 http://127.0.0.1:45173/docs；完整参数、枚举和返回模型由 /openapi.json 提供。

应用仅在容器内运行，使用 `just run` 启动 headless，或 `just run vnc` 启用远程桌面；两者均自动准备配置、构建和启动。配置、生命周期及容量治理见 [bootstrap.md](bootstrap.md)，内部动作、会话和复位见 [terminal_execution.md](terminal_execution.md)。

## 路由与输入

| 方法 | 路径 | 参数 |
| --- | --- | --- |
| POST | /cfb/create_market_order | mode、exchange_id、instrument_id、side、offset、volume、hedge_flag、invest_unit_id |
| POST | /cfb/create_limit_order | 市价请求的公共字段，加 price、time_in_force |
| POST | /cfb/cancel_order | mode、exchange_id、instrument_id、invest_unit_id，以及 by 指定的一组身份字段 |
| GET | /cfb/fetch_orders | mode；可选 exchange_id、instrument_id、order_sys_id、insert_time_start、insert_time_end；invest_unit_id |
| GET | /cfb/fetch_trades | mode；可选 exchange_id、instrument_id、trade_id、trade_time_start、trade_time_end；invest_unit_id |
| GET | /cfb/fetch_positions | mode；可选 exchange_id、instrument_id；invest_unit_id |
| GET | /cfb/fetch_balance | mode、currency_id |
| GET | /cfb/fetch_trading_status | mode；必填 exchange_id、product_id |

POST 只接受 application/json 对象；GET 使用 query。未知 JSON/query 字段、重复单值 query、重复幂等头或 POST 中夹带 query 均拒绝，不静默忽略条件。

公共默认值：mode=sandbox、hedge_flag=speculation、invest_unit_id=""、time_in_force=GFD、currency_id=CNY。交易所为 SHFE/INE/DCE/CZCE/CFFEX/GFEX；下单、撤单和状态查询必填交易所，其他查询可省略。

实盘必须明确传 mode=live，省略时按 sandbox 校验并报环境不匹配。环境匹配在 HTTP、调度准入和执行器复核；实盘与模拟盘使用同一能力规则，当前限价 GFD 等基础分支不会因 live 被禁用，未核验分支仍返回 501。AI 的实盘交易测试需用户明确授权，该约束不是只读 API 权限。

合约格式为 `^[A-Za-z][A-Za-z0-9]{0,79}$`，保留大小写；品种为 `^[A-Za-z][A-Za-z0-9_]{0,79}$`，同样区分大小写。执行时与终端实际标的及所属交易所精确核对，不能以自动补全或模糊匹配替代；资料未就绪与标的不匹配分别报错。

side 为 buy/sell，offset 为 open/close/close_today/close_yesterday；volume 是 1～2147483647 的严格整数，拒绝 bool、数字字符串和小数。price 为正的有限 JSON number，拒绝数字字符串、NaN、Infinity；执行时核对可取得的报价步长与涨跌停，不自动改价或改手数。

time_in_force 为 GFD/IOC/FOK；hedge_flag 为 speculation/arbitrage/hedge。当前基础交易路径为限价 GFD、投机、开仓或普通平仓。原生市价、IOC/FOK、指定平今/平昨和非投机分支尚未核验，返回 501/CAPABILITY_NOT_SUPPORTED；不以对价限价或发送后撤单模拟这些语义。

撤单必须声明 by：exchange_order 要求 order_sys_id；session_order 要求 front_id、session_id、order_ref。字段组不能混用。订单编号保留原始空格及字符串身份，长度 1～20，至少包含一个非空格可见 ASCII 字符；不会转整数、补空格或移除前缀。front_id 为 0～2147483647，session_id 为有符号 32 位整数，order_ref 为 1～12 位数字字符串。当前只启用交易所编号的唯一目标定位；会话方式尚未核验。

trade_id 与 order_sys_id 使用相同标识规则。时间过滤为严格 HH:MM:SS 的闭区间，不支持跨午夜，起点不得大于终点。只查询终端当前快照，不补历史、不接受任意日期。非空投资单元与非人民币资金当前不支持；invest_unit_id 最长 16 个可见 ASCII 字符，currency_id 格式为三个大写字母。

## 提交、独立查询与追踪

所有可执行业务请求进入同一个 FIFO，包括原生状态查询。只读诊断不等待业务队列。HTTP 等待为异步；客户端断开时取消尚未开始的队列项，已经开始的动作仍由执行器完成核对、收尾和持久化，不因 HTTP 退出释放 GUI。

三个 POST 支持可选 Idempotency-Key，1～128 个非空格可见 ASCII 字符。同账户/环境/券商中，同键同参数重放原响应和逻辑编号，同键不同参数 409，原请求尚在执行 409；不同环境和券商的结果隔离，无键的同参数请求仍是独立指令。未知提交不自动到期或重新发送。

正常本地提交且收尾确认后返回 202：

```json
{"request_id":"cfb-123","submission_status":"submitted","order_id":null}
```

submitted 表示本地动作完成，不代表柜台接受或成交。导入回读、快捷键发送、一次订单 CSV 观察之后即收尾，不轮询柜台。可靠订单编号暂不可得时保持 null；不能根据相同价格或相近时间认领订单。

每个响应携带 X-Request-ID 和 Cache-Control: no-store，头中编号与正文一致。幂等重放保留原编号；CFB 编号不能用作订单编号。后续通过订单、成交确认结果，持仓未变本身不能证明下单失败。

查询返回 request_id、observed_at、source、trading_day，加 orders/trades/positions 数组或 balance 对象。observed_at 是读取时间，trading_day 来自终端且允许 null；缺少可信交易日时写操作不就绪。读取失败不返回旧快照、默认零或空数组。

订单包含订单号、交易所/合约、买卖/开平、总手数、成交/剩余手数、价格、状态、说明和时间。成交包含成交号、可空订单号、交易身份、手数/价格、时间及可空手续费/平仓盈亏。持仓按终端维度保留总仓、今昨仓、可平量、均价、保证金、盈亏，不能把可平量当总仓或强拆 CTP 行。

资金读取窗口的服务器列，包含 equity、available、margin，及可空 frozen_margin、frozen_commission、commission；values_source=server，source=terminal_text。其余账户表格 source=terminal_csv。字段及可空性以 OpenAPI 的 Pydantic 模型为准。

品种交易状态返回 request_id、exchange_id、product_id、observed_at、is_trading、source=terminal_native。只用快期 Product_Status：3 返回 true，1/2/4/5/6/7 返回 false；集合竞价归 false 不等于禁止所有报撤单。0、未知、断线或读取失败报错，详见 [trading_status.md](trading_status.md)。

## 错误与能力

错误包含 request_id、submission_status、order_id、error；error 包含 code、message、可空 details，参数问题 details 项为 loc/type/message。副作用前错误的 submission_status 为 null，明确本次拒绝为 rejected，影响不明为 unknown；已确认提交后的收尾错误保留 submitted。

| HTTP | 典型错误 |
| --- | --- |
| 409 | ENVIRONMENT_MISMATCH、ORDER_NOT_FOUND、ORDER_IDENTITY_AMBIGUOUS、IDEMPOTENCY_CONFLICT、OPERATION_IN_PROGRESS |
| 422 | INVALID_ARGUMENTS、ORDER_REJECTED |
| 429 | QUEUE_FULL |
| 501 | CAPABILITY_NOT_SUPPORTED |
| 502 | TERMINAL_DATA_INVALID、OPERATION_STATUS_UNKNOWN |
| 503 | SERVICE_NOT_READY、GUI_UNRESPONSIVE、GUI_RESET_FAILED、STORAGE_UNAVAILABLE |
| 504 | QUEUE_TIMEOUT、QUERY_TIMEOUT |

`/v1/status` 的 capabilities 区分 supported/unsupported/unverified；基础分支 supported 不代表所有附加模式可用。状态还包含 executor_state、queue_depth、active_operation_id、unresolved_operations。unknown 保留防重发记录；正常 submitted 不因尚未查询成交而成为未决故障。

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
