# CFB 必要返回模型与数据来源

## 返回原则

仅路由数量、方法、名称和主要输入尽量兼容参考服务。返回采用 CFB 自己的 snake_case 字段，不复制 CTP 原始结构、不保留大批无来源的占位字段。字段如下冻结，未知可选值用 null，实际的零和空数组不能与读取失败混淆。

request_id 为非空 CFB 逻辑请求标识，不是 CTP nRequestID。相同 Idempotency-Key 的同内容请求复用原逻辑标识和已保存结果；内部可另记 HTTP 尝试编号。响应头 X-Request-ID 与响应中的 request_id 一致。

## 提交返回

三个 POST 正常完成本地提交并确认界面可以交权后返回 HTTP 202，固定包含：

| 字段 | 类型及含义 |
| --- | --- |
| request_id | string，日志及防重发记录的逻辑标识 |
| submission_status | `submitted`，本地提交动作已完成，不表示柜台接受或成交 |
| order_id | string 或 null，已可靠取得的目标/新增订单编号；不能用追踪编号代替 |

```json
{"request_id":"cfb-123","submission_status":"submitted","order_id":null}
```

submitted 需要实际本地动作完成的证据，例如已核对的本次预埋记录变为已发送；仅入队、导入成功、xdotool 返回 0 都不足以证明。快速订单快照尚无回报时仍可正常返回 submitted，不进行循环等待。

拒绝和异常使用下文错误模型。submission_status 为 rejected 表示已经观察到明确的本次提交拒绝；unknown 表示提交或本地导入的影响无法确认；尚未开始副作用的校验、排队或能力错误为 null。若提交已确认但界面收尾失败，错误响应仍保留 submitted，不能抹掉已知副作用。

## 查询公共包装

四类账户数据查询固定包含 request_id、observed_at、source、trading_day，以及对应的数据字段。observed_at 为 RFC3339 快照读取时间，不是柜台数据刷新时间；source 为 `terminal_csv` 或 `terminal_text`；trading_day 为终端确认的 YYYYMMDD 或 null，不能按宿主日期补造。

| 路由 | 数据字段 | 形状 |
| --- | --- | --- |
| fetch_orders | orders | 订单数组 |
| fetch_trades | trades | 本账户逐笔成交数组 |
| fetch_positions | positions | 终端可可靠区分的持仓数组 |
| fetch_balance | balance | 当前单账户、指定币种的资金对象 |

数据字段本身不为 null。有效快照确认无订单/成交/持仓时才返回空数组；资金读取失败不返回空对象或全零资金。接口不承诺 CTP 行划分和字段全集。

### 订单

| 字段 | 类型及含义 |
| --- | --- |
| order_id | string 或 null，终端可可靠定位的订单编号 |
| exchange_id, instrument_id | string，已核对的交易所和具体合约 |
| side | `buy` / `sell` |
| offset | `open` / `close` / `close_today` / `close_yesterday` / `unknown` |
| volume, filled_volume, remaining_volume | integer，原始、累计成交及剩余未成交手数 |
| price | number 或 null，委托价格；无价格信息时不当作零限价 |
| status | `pending` / `open` / `partially_filled` / `filled` / `cancelled` / `rejected` / `unknown` |
| status_message | string 或 null，已取得的终端详细说明 |
| order_time | string 或 null，终端原报单时间，保留其 HH:MM:SS 口径 |

只有证明编号可用于目标定位时才填 order_id；字符串原样保留，不将其转整数或伪造前导空格。当前支持的交易所编号撤单中，可把该值传入请求的 order_sys_id；这一对应关系须验证。没有可信对应时不能假定可撤。

状态结合显示状态与详细说明判断：本地待处理行为 pending；活跃未成交为 open；部分成交仍有效为 partially_filled；部分成交后撤销为 cancelled 并保留数量。终端拒单即使显示“已撤单”，仍应根据明确拒绝原因标记 rejected；无法区分时 unknown，不猜测。

### 成交

字段为 trade_id（string）、order_id（string|null）、exchange_id、instrument_id（string）、side（buy/sell）、offset（与订单相同枚举）、volume（integer）、price（number）、trade_time（string|null）、commission、close_profit（number|null）。只返回实际成交；无法证明所属订单时 order_id 为 null，不靠相同价格和时间硬配。

### 持仓

字段为 exchange_id、instrument_id（string）、direction（long/short）、hedge_flag（speculation/arbitrage/hedge/unknown）、volume（integer）、today_volume、yesterday_volume、available_volume（integer|null）、average_price、margin、profit（number|null）。

volume 是总持仓，available_volume 是可平量，两者不能混用。按终端已能区分的合约、方向、投保维度保留记录，不为仿 ReqQryInvestorPosition 人为拆今昨仓行，也不把保证金按手数猜分摊。未取得的可选明细为 null；身份或总持仓无法确认则报读取错误。

### 资金

字段为 currency_id（string）、equity、available、margin、frozen_margin、frozen_commission、commission（number|null），以及 values_source（server/local）。默认使用资金详情的服务器列；只在有完整、明确来源的情况下提供同一来源的数据，不能把服务器和本地演算值混成一行。基础权益、可用资金及占用保证金须可读取才视为该能力通过验证；其他可选字段缺失为 null。

equity 表示当前动态权益，available 表示可用资金，margin 表示占用保证金，其余分别为冻结保证金、冻结手续费和手续费。映射须核对原字段实际口径，不能用静态权益替代动态权益或把冻结金额混入占用保证金。

## 交易状态返回

fetch_trading_status 成功时固定包含 request_id、exchange_id、product_id、observed_at、is_trading、source。exchange_id 和 product_id 为经终端资料精确核对的请求身份；observed_at 是执行器读取时间，格式为 RFC3339，不代表柜台刚刷新状态；source 固定为 terminal_native。

is_trading 为严格 boolean，仅 true/false，不接受 null 或第三种成功状态；按 [原生状态语义](../../current_specs/trading_status.md) 映射，含义是是否连续交易。原生未知、会话失效或读取失败使用统一错误响应，不返回默认 false。原始编码保留在内部日志，成功正文不暴露原生状态枚举、日盘/夜盘分类或 reason 字段。

```json
{"request_id":"cfb-124","exchange_id":"CZCE","product_id":"RM","observed_at":"2026-09-21T15:50:00+08:00","is_trading":false,"source":"terminal_native"}
```

该值来自当前已登录终端维护的数据，不用时间推算，也不保证账户权限、资金或委托接受。未实施的旧日历返回字段不保留兼容包装；完整查询前置条件和失败语义见 [spec_01_api.md](spec_01_api.md)。

## 错误返回

统一错误对象包含 request_id、submission_status、order_id、error。前三项遵循上述含义，其中 submission_status 对未开始副作用或查询错误为 null；error 包含 code、message、details，details 为参数校验问题数组或 null。details 每项仅包含 loc、type、message，不回显凭证或完整请求。

```json
{"request_id":"cfb-125","submission_status":"unknown","order_id":null,"error":{"code":"OPERATION_STATUS_UNKNOWN","message":"提交动作的影响无法确认，请先查询订单与成交","details":null}}
```

没有明确订单身份就保持 null；request_id 用于查日志，不冒充订单号。清理失败、响应发送失败不能将已经确认的 submitted 改成 rejected。异步拒单在稍后的 fetch_orders 中体现，不修改已经返回或幂等重放的本地提交结果。

执行中的幂等重试尚无结果时 submission_status 也可为 null；是否未执行由 QUEUE_TIMEOUT 等明确错误码说明，不能仅凭 null 推断没有副作用。

## 编码、数值及示例访问

CSV 保留中文列名及业务值，按当前固定客户端的 GB18030 读写；API 统一输出 UTF-8 JSON。已有实测未发现中文方块或乱码，保留当前文泉驿字体及字体映射，不增加字体专项；编码失败明确报错，不能改用替换字符后继续交易。

金额/价格内部精确处理，输出有限 JSON number 或 null；手数保持 integer。显示缺失符号不转成零，原始 CTP 数字枚举不作为 CFB 外部返回。必需身份、数量或过滤依据缺失时返回错误，可选字段缺失才为 null。

```json
{"request_id":"cfb-126","observed_at":"2026-09-21T14:00:00+08:00","source":"terminal_csv","trading_day":"20260921","orders":[]}
```

非空结果使用 orders[].order_id/status、trades[].trade_id/volume、positions[].volume/available_volume、balance.available/margin。先判断记录和可空字段，不能以一次没有持仓变化推断订单失败；确认提交后的状态优先查询订单和成交。
