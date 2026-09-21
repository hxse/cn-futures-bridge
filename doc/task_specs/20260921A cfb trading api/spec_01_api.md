# 八路由与接口兼容

## 参考及兼容范围

输入参考：`http://127.0.0.1:5123/openapi.json`，2026-09-21，`ccxt-proxy2` 1.0.0，OpenAPI 3.1.0。读取内容 SHA256 为 `944e9d59c691470f0c9974a256a8af284c9265db271c519d7d9b4f29facf9b22`。本文已转写所需路由和参数；该地址不是运行依赖，后续变化不自动改变 CFB 契约。

业务前缀为 `/cfb`，八个方法/后缀、主要请求字段和默认值尽量兼容参考。返回不要求兼容 CTP，统一使用 [spec_02_models.md](spec_02_models.md) 的 CFB 必要字段。账户查询读取终端快照，交易状态读取快期内部品种状态；不声称执行独立 CTP 请求或直接收到原生回调。

业务路由无 HTTP 鉴权；不要求 Authorization，也不添加登录、任务查询或任意桌面输入业务路由。运行边界和诊断入口见 [spec_03_operations.md](spec_03_operations.md)。

## 路由与请求

| 方法 | 路径 | 请求与成功结果 |
| --- | --- | --- |
| POST | `/cfb/create_market_order` | 市价请求；本地提交结果，通常 HTTP 202 |
| POST | `/cfb/create_limit_order` | 限价请求；本地提交结果，通常 HTTP 202 |
| POST | `/cfb/cancel_order` | 判别联合撤单请求；本地撤单提交结果，通常 HTTP 202 |
| GET | `/cfb/fetch_orders` | 订单过滤；`orders` 数组包装 |
| GET | `/cfb/fetch_trades` | 成交过滤；`trades` 数组包装 |
| GET | `/cfb/fetch_positions` | 持仓过滤；`positions` 数组包装 |
| GET | `/cfb/fetch_balance` | 资金过滤；`balance` 单账户对象 |
| GET | `/cfb/fetch_trading_status` | 交易所/品种参数；快期原生状态对应的 is_trading 二值 |

POST 接受 `application/json` 对象；不接受未声明字段。GET 使用 query 参数并拒绝未声明参数及重复的单值参数。不得把错误拼写、未支持的过滤项或交易条件静默忽略。

### 公共字段及校验

| 字段 | 类型及规则 | 默认值 |
| --- | --- | --- |
| `mode` | `sandbox` / `live`；本阶段 `live` 返回 `SERVICE_NOT_ENABLED`，不切换账户、不回退 | `sandbox` |
| `exchange_id` | `SHFE` / `INE` / `DCE` / `CZCE` / `CFFEX` / `GFEX` | 下单、撤单、状态必填；其他按下表 |
| `instrument_id` | `^[A-Za-z][A-Za-z0-9]{0,79}$` 的具体期货合约，保留大小写 | 下单、撤单必填；查询可选 |
| `invest_unit_id` | 最多 16 个可见 ASCII 字符，允许空；非空在能力未支持时明确拒绝 | 空字符串 |
| `side` | `buy` / `sell` | 必填 |
| `offset` | `open` / `close` / `close_today` / `close_yesterday` | 必填 |
| `volume` | 正整数手数，1～2147483647；JSON bool、字符串、小数不当作整数 | 必填 |
| `hedge_flag` | `speculation` / `arbitrage` / `hedge`；需具备对应终端表达和权限验证 | `speculation` |

不接受交易密码、前置地址、认证码、账户选择字段。`SHFE.rb2610`、主连、指数代码不转换为具体合约。校验请求格式后再检查能力、账户及可用的合约资料；合约与交易所必须匹配，不把终端自动补全当作校验。

### 下单

市价请求字段：`mode, exchange_id, instrument_id, side, offset, volume, hedge_flag, invest_unit_id`。

限价请求包含上述全部字段，再增加：

| 字段 | 规则 |
| --- | --- |
| `price` | 必填有限正 JSON 数值，使用合约报价单位；不接受 NaN、Infinity、bool 或数字字符串 |
| `time_in_force` | `GFD` / `IOC` / `FOK`，默认 `GFD` |

价格与手数不自动修正，不自动拆平今/平昨，不将精确平仓替换为全平。可取得的价格步长、限价范围及权限先校验，柜台仍可能拒绝；未知业务限制不凭猜测补默认值。

市价语义与参考相同：真正的 AnyPrice、IOC、任意数量；请求不接受 `price`。限价 GFD 是当日有效，IOC 是立即成交剩余撤销，FOK 是立即全部成交否则全部撤销。缺少原生表达时返回 501，不用对价/涨跌停限价代替市价，不用“发送后再撤单”模拟 IOC/FOK。

下单不等待柜台接受或成交。执行发送前回读核对、本地发送及一次快速订单 CSV 观察后收尾；若本地提交已确认且界面可安全交权，返回 202/submitted。不能刚入队或刚导入就返回 submitted；也不能只凭按键工具退出码推断动作已生效。

快速观察不轮询等待回报：已有可靠订单号则返回，尚未出现则 order_id=null；取不到回报不影响已确认的本地提交事实。已观察到明确拒绝则返回 rejected 及原因；本地动作影响无法确认则 unknown。查询失败不得把已知 submitted 改成 rejected，记录该次观察失败，由调用方后续查询。

### 撤单

公共字段：`mode, exchange_id, instrument_id, invest_unit_id`，再由必填 `by` 决定唯一字段组：

| `by` | 额外必填字段 |
| --- | --- |
| `exchange_order` | `order_sys_id`：1～20 字符，保留空格及字符串身份，至少含一个非空格可见 ASCII 字符 |
| `session_order` | `front_id`：0～2147483647；`session_id`：有符号 32 位整数；`order_ref`：1～12 位数字字符串 |

两种字段不能混用。会话方式必须使用原订单的会话标识；快期无法可靠提供时在点击撤单前返回 501。交易所方式需先验证 CSV 报单编号与订单标识的对应，不以整数转换、补空格或任意剥离字符伪造原生编号。

撤单只针对请求中唯一确定订单的未成交部分，必须在按键前核对目标；不能用“当前选中行”替代身份。完成本地撤单提交、快速观察和收尾后可返回 202/submitted，不等待最终撤单回报。已知全成/拒绝、目标多义或提交未知分别处理，最终是否撤销由 fetch_orders 确认；不实现改单或批量撤单。

### 查询

| 路由后缀 | 除 `mode` 外的 query 字段 |
| --- | --- |
| `fetch_orders` | 可选 `exchange_id, instrument_id, order_sys_id, insert_time_start, insert_time_end`；`invest_unit_id=""` |
| `fetch_trades` | 可选 `exchange_id, instrument_id, trade_id, trade_time_start, trade_time_end`；`invest_unit_id=""` |
| `fetch_positions` | 可选 `exchange_id, instrument_id`；`invest_unit_id=""` |
| `fetch_balance` | `currency_id="CNY"`，格式为三个大写字母；非 CNY 未验证时返回 501，不擅自换汇 |
| `fetch_trading_status` | 必填 `exchange_id, product_id` |

`trade_id` 与 `order_sys_id` 同为保留原始字符串的 1～20 字符标识。时间格式严格为 `HH:MM:SS`；支持单个不跨午夜的闭区间，起点晚于终点返回 422。仅过滤当前已确认交易日，不能将服务器自然日当交易日；不分页、不补历史，不接受任意日期查询。

`product_id` 格式为 `^[A-Za-z][A-Za-z0-9_]{0,79}$`，区分大小写，例如 `rb`、`m_o`；不自动把具体合约转成品种。

查询每次使用新快照。没有记录且读取及表头/范围校验均成功才返回空数组；缺少数据、文件、过滤所需维度或发生超时不能返回空数组。可在已验证完整的快照上做本地过滤；不能以可见区域、错误筛选或部分结果冒充完整数据。

fetch_positions 返回终端能够准确提供的持仓信息，例如方向、总仓、今仓、昨仓和可平量；不要求复刻 ReqQryInvestorPosition 的行划分或原字段。可平量不等于总仓，不为模拟原生维度拆分或猜分摊保证金。查询订单和成交是确认委托的主要入口，不能仅凭仓位未变判断提交失败。

fetch_trading_status 保留必填 exchange_id、product_id，不新增 instrument_id 或第九个业务路由。成功返回快期对应品种是否连续交易，值为 is_trading，source=terminal_native；不保证账户权限、资金或某笔订单一定被接受。每次查询同样排队，按出队时的当前会话读取，不触发登录、下单或网页访问。

### 原生交易状态读取

正式链路为“公共 FIFO → 会话/连接/版本校验 → 已登录快期进程内查找品种并核对身份 → Product_Status → 二值映射 → 响应”。入口、枚举和固定文件指纹见 [原生状态能力](../../current_specs/trading_status.md)；字段以返回模型为准。Instrument_Status 可用于合约适配和验证，Exchange_Status 只用于相应范围或辅助核对，均不能替代请求品种的 Product_Status。

格式校验在入队前完成；出队后确认品种资料已加载，再按终端的 Product_Id、Product_ExchangeID 精确核对请求。GetProductLike 的模糊查找/大小写补全不改变公开输入规则：无匹配或身份不一致返回 422/INVALID_ARGUMENTS，details 指向对应 query 字段；例如 CZCE/rm 查到 RM 仍不符合区分大小写的输入。资料尚未就绪与品种确实不存在分别处理。

只在身份与连接有效、数据属于当前会话时返回布尔值。断线、重启或会话代次变化立即使旧绑定及数据可信标记失效；重新绑定并确认新会话资料已刷新前返回 503/SERVICE_NOT_READY，不能重放断线前的值。读取前后均须确认同一会话和有效连接；单看进程存在、窗口标题或内存里仍有整数不足以放行。

原生值 0 或未知编码返回 502/TERMINAL_DATA_INVALID；模块/符号/版本不匹配、未登录、断线或当前会话数据未就绪返回 503/SERVICE_NOT_READY；读取超时返回 504/QUERY_TIMEOUT，并遵守执行器失联与所有权规则。错误不携带伪造的 false、null 状态或旧的成功结果。

日历、时段表、本机时间、模拟环境时间规则及交易所颜色均不是业务数据源或回退。observed_at 只记录读取时间，不参与开盘判定；不要求状态值数值变化才算刷新，也不能因值相同就认定新会话已刷新。同步只读操作不切页、不重置 GUI、不固定 sleep；内部原生值及阶段进入步骤日志，失败取证遵守按需截图原则。

## 响应、追踪和幂等

下单/撤单返回 request_id、submission_status、order_id；账户查询返回 CFB 公共快照字段及 orders/trades/positions/balance；交易状态使用带来源和观察时间的二值模型。完整字段见 [spec_02_models.md](spec_02_models.md)，不再保留 CTP 原字段全集或原包装。

每次响应带 X-Request-ID，与正文中的 CFB 逻辑 request_id 一致；幂等重放仍关联原请求，内部日志可以另记本次 HTTP 尝试编号。查询来源和观察时间直接在正文说明，不伪称柜台刚完成刷新。响应禁用 HTTP 缓存。

三个 POST 支持可选 Idempotency-Key：1～128 个可见 ASCII 字符。键在同一账户/环境中唯一，指纹包含路由及规范化业务参数；同键同请求复用已持久化结果，同键不同内容返回 409，同键仍执行中返回 409，不重复执行。GET 不参与交易防重发，但每次正常查询仍入队。无键的不同 POST 是独立业务请求，不按内容相同擅自合并；界面准备/复位幂等不能代替交易幂等。

保护期及持久化空间按运维规范；unknown 的防重发标记不自动过期，正常 submitted 是已结束的本地操作，不等待外部订单成交才结束。保护期外的已结束请求不保证重放；错误或断线后先查询订单/成交，不新增第九个业务路由或自动补单系统。

## 错误与 HTTP 状态

错误统一使用 CFB 模型：request_id、submission_status、order_id、error，error 包含 code、message、details。参数错误的 details 为问题数组，其余通常为 null；不复制原服务 detail 包装、CTP 返回码或会话身份字段。最小必要错误分类如下，不预先枚举所有 GUI 异常形态。

| HTTP | code | 语义 |
| --- | --- | --- |
| 422 | `INVALID_ARGUMENTS` | 格式在入队前校验，依赖终端资料的标的身份在执行时核对；details 列明字段问题，无交易副作用 |
| 422 | `ORDER_REJECTED` | 快速观察中已明确识别的拒单，保留原因 |
| 409 | `CANCEL_REJECTED` | 撤单明确被拒绝或目标已经全部成交 |
| 409 | `ORDER_NOT_FOUND` / `ORDER_IDENTITY_AMBIGUOUS` | 无法唯一确定目标，尚未撤单 |
| 409 | `IDEMPOTENCY_CONFLICT` / `OPERATION_IN_PROGRESS` | 键冲突或原请求未完成，不新增副作用 |
| 429 | `QUEUE_FULL` | 请求未获准入 |
| 504 | `QUEUE_TIMEOUT` | 排队超时，尚未执行，必须从队列移除 |
| 504 | `QUERY_TIMEOUT` | 读取未完整完成，不返回部分数组 |
| 501 | `CAPABILITY_NOT_SUPPORTED` | 固定终端无已验证的等价能力，入队或副作用前拒绝 |
| 502 | `TERMINAL_DATA_INVALID` | 文件、字段或快照无法作为有效业务数据 |
| 502 | `OPERATION_STATUS_UNKNOWN` | 已开始有副作用步骤但最终影响无法确认，禁止自动重放 |
| 503 | `SERVICE_NOT_ENABLED` | 请求 live 等未启用环境，不改当前会话 |
| 503 | `SERVICE_NOT_READY` / `GUI_UNRESPONSIVE` | 尚未开始本次副作用，终端或执行器不可用 |
| 503 | `GUI_RESET_FAILED` | 无法证明收尾后可继续操作，暂停；保留已知 submission_status |
| 503 | `STORAGE_UNAVAILABLE` | 无法可靠登记/留存当前操作，发送前拒绝 |

影响无法确认时返回 unknown，不能用 HTTP 错误暗示一定未发送。已确认本地提交但收尾失败时保留 submitted 并返回明确收尾错误，不改写成拒单。导入已发生但残留不明也不得重建重复预埋单。broker 回报尚未来临本身不属于异常，不阻塞正常下一请求。

## 完整调用与结果示例

已确认当前交易日没有订单时：

```http
GET /cfb/fetch_orders?mode=sandbox&exchange_id=CZCE&instrument_id=RM701
```

```json
{"request_id":"cfb-126","observed_at":"2026-09-21T14:00:00+08:00","source":"terminal_csv","trading_day":"20260921","orders":[]}
```

有数据时，编号访问 orders[0].order_id，状态访问 orders[0].status；先判断数组及可空标识，不能用 null 构造撤单请求。只查持仓不足以确认该笔订单，成交使用 fetch_trades 单独查询。

限价请求体：

```json
{"mode":"sandbox","exchange_id":"CZCE","instrument_id":"RM701","side":"buy","offset":"open","volume":1,"price":2323,"time_in_force":"GFD"}
```

提交返回 202/submitted 后，由 fetch_orders 取得已验证的订单编号再撤单；下面的编号只说明输入形状，不是要求撤销历史样本：

```http
POST /cfb/cancel_order
Content-Type: application/json

{"mode":"sandbox","by":"exchange_order","exchange_id":"CZCE","instrument_id":"RM701","order_sys_id":"288660"}
```

发送后结果未知，HTTP 502 示例：

```json
{"request_id":"cfb-125","submission_status":"unknown","order_id":null,"error":{"code":"OPERATION_STATUS_UNKNOWN","message":"提交动作的影响无法确认，请先查询订单与成交","details":null}}
```

品种状态查询及快期当前返回收盘时的 HTTP 200 示例；日期本身不决定状态：

```http
GET /cfb/fetch_trading_status?mode=sandbox&exchange_id=CZCE&product_id=RM
```

```json
{"request_id":"cfb-127","exchange_id":"CZCE","product_id":"RM","observed_at":"2026-09-21T15:50:00+08:00","is_trading":false,"source":"terminal_native"}
```

原生状态未知时返回 HTTP 502，不能将其包装为休盘：

```json
{"request_id":"cfb-128","submission_status":null,"order_id":null,"error":{"code":"TERMINAL_DATA_INVALID","message":"快期返回未知品种交易状态","details":null}}
```

关键反例：市价请求附带 price、volume=0、混合撤单字段、跨午夜过滤区间均为 422；FOK 尚未支持时返回 501 且不发送 GFD。202/submitted 之后暂未查到持仓不能触发自动重发；所有示例进入后续离线契约测试，示例不是本轮在线运行结果。
