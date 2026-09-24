# 限价归一化与价格边界

## 任务边界

为 create_limit_order 的 GFD/IOC 普通投机开仓、平仓统一实现价格分类处理；新增配置、结果和错误中的价格事实，同步 current spec、示例配置与自动 API 文档。请求字段、八个路由数量/名称、买卖开平、数量、有效期、发送方式、订单身份和确认语义保持不变。

替换 orders 中的旧取余及可选边界校验，唯一价格逻辑放入终端价格模块，GFD、IOC 和模拟市价共用其严格校验。模拟市价仍以真实涨跌停价＋IOC 执行，不调用显式限价自动修正规则，也不增加新行情或校验路由。

本任务维护请求模型说明、执行结果模型、价格错误详情、执行器、操作记录、配置及相关测试。不修改账户凭证、原生交易函数、GUI 快捷键、CSV 确认轮数或等待间隔，不扩展 FOK、平今/平昨等既有未核验能力。

停止线为离线价格/集成契约通过、镜像构建完成，以及具备条件的 SimNow 小额验证与测试副作用清理。遇到终端或交易环境不可用时如实说明在线限制；未知结果不得重发或伪造验收通过。

## 任务规范

### 一次价格决策

1. HTTP 沿用正的有限 JSON number 输入，拒绝 bool、字符串、null、零、负数、NaN/Infinity。价格不按固定小数位截断。
2. 在 FIFO 执行权内核对账户、会话及准确合约，取得本次 tick、lower、upper。三者必须有限且大于零，lower ≤ upper，区间内至少存在一个合法正价格；否则 503/SERVICE_NOT_READY。不能把 0 或缺失资料当作无限制。
3. 价格计算采用十进制值和精确整数价位运算，不依赖全局 Decimal 精度做取余。极大有限输入不得引发未捕获异常或落入 502。
4. 若请求价与最近的 tick 整数倍不同，且差不超过 tick × 10⁻⁹，吸附到该价，记录 float_noise；否则保留原数值进入下一步。
5. 对去除尾差后的价格，买价低于 lower 或卖价高于 upper 返回 422/INVALID_ARGUMENTS。买价高于 upper，只有 price ≤ upper × (1+r) 才截到 upper；卖价低于 lower，只有 price ≥ lower × (1-r) 才截到 lower。边界包含等号；阈值比较也容纳不超过 tick × 10⁻⁹ 的尾差，并记录 float_noise，例如 3614 × 1.05 的浮点结果。超出该容差的额外越界返回 422。r 为配置比例，判断必须早于方向取整，不能先取整绕过幅度限制。
6. 买价向下、卖价向上对齐到 tick 的整数倍，记录 tick_floor/tick_ceil；已对齐不重复记录。若截断值本身不在网格，仍按此方向对齐。最终必须为正、整跳并在闭区间内，否则 422，不反向抬买价或压卖价凑成有效指令；实际价不能在当前终端数值传输中准确表示时同样拒绝，不二次舍入。
7. 除定义内的浮点尾差修正，最终买价不高于修正前目标价，最终卖价不低于修正前目标价。数量和有效期不改变，不做追价、重算或自动补发。

归一化在持仓基线和导入/填参之前完成，输入拒绝不创建本地预埋单。严格终端校验仍在实际导入/填参入口执行，只核验最终价，不再次归一化。原生、CSV、引用捕获及订单回报必须核对这个最终价；原始请求保持不变。

模拟市价仍要求连续交易以及有效资料，选定的原生涨跌停价必须可用于该价格网格；资料异常返回 503，不改成其他价或其他有效期。

### 记录与错误

幂等指纹使用原始请求。不同原价即使归一化后相同，也不能共用同一幂等键；重放已有响应时不按新报价、配置或规则重新计算。旧的完整响应原样重放，不补造历史调价信息。

执行决策只有一份：最终价供执行副本、execution 和确认逻辑使用；日志记录请求价、实际价、原因以及本次 tick/边界/阈值。必须保留现有详细步骤和容量治理。

SQLite 在已有导入/发送前阶段同步保存 execution，扩展现有记录，不新增另一套日志或操作数据库。请求中断和恢复响应携带已经持久化的调价信息；尚未决定或尚未持久化时为 null，不臆测实际价格。副作用前拒绝的 submission_status 仍为 null；已知提交、未知结果及执行权隔离规则保持。

价格错误沿用 error.code、message、details。details 的 FieldProblem 增加可空 context，用结构化字段说明请求价、本次有效资料及允许超界比例；无关错误的 context 为 null。缺失或非有限资料用 null 表示，不能在 JSON 中输出 NaN/Infinity。成交价仍从成交回报读取，不以提交限价替代。

## 公开接口与用户写法

路由和请求字段不变，自动处理为默认行为，无新增请求开关。以下配置可省略，使用默认值：

```toml
[execution]
price_max_deviation_ratio = 0.05
```

该值为有限数，允许 0 ≤ r < 1；0 关闭越界截断，仍允许尾差修正和方向对齐。比例相对于当时的涨停/跌停边界，不是滑点指标，也不替代交易所涨跌停。浮点尾差阈值固定，不添加配置项。

OrderExecution 保留 kind、price、time_in_force，新增 requested_price、price_adjusted、price_adjustments。price 为实际提交限价；requested_price 为显式限价收到的原价。price_adjustments 按处理顺序记录 float_noise、upper_limit、lower_limit、tick_floor、tick_ceil；price_adjusted 表示实际价发生改变。未调整时 false/[]；市价模拟和撤单没有请求价，使用 null/false/[]。

FieldProblem.context 的字段为 requested_price、price_tick、lower_limit、upper_limit、max_deviation_ratio；字段均允许 null，错误只返回确实取得的资料。

以下合约和数值只作格式与规则示例，运行时使用快期真实资料。设 tick=1、lower=3206、upper=3614：

```bash
curl -X POST http://127.0.0.1:45173/cfb/create_limit_order \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: price-example-001' \
  -d '{"mode":"sandbox","exchange_id":"DCE","instrument_id":"m2701","side":"buy","offset":"open","volume":1,"price":3514.35,"time_in_force":"GFD"}'
```

响应中的 execution 为：

```json
{"kind":"limit","price":3514.0,"time_in_force":"GFD","requested_price":3514.35,"price_adjusted":true,"price_adjustments":["tick_floor"]}
```

| 输入场景 | 结果 |
| --- | --- |
| 3486.0000000000005，任一方向 | 3486，float_noise |
| 3514.35，buy / sell | 3514 / 3515，tick_floor / tick_ceil |
| buy 3675 | 3614，upper_limit |
| sell 3190 | 3206，lower_limit |
| buy 3190 / sell 3675 | 422，错误方向越界，不提交 |
| buy 3794.7 / 3794.71 | 前者恰好 5% 可截断，后者超过阈值拒绝 |
| sell 3045.7 / 3045.69 | 前者恰好 5% 可截断，后者超过阈值拒绝 |
| 0、负数、错误类型、非有限数、巨大越界价格 | 422，不提交 |
| tick=0、边界缺失/颠倒、区间无合法价 | 503，不提交 |

HTTP 202、submitted、observed 不等于成交；继续使用返回的订单号或完整 identity 查询真实状态。

## 测试、验证与阶段过渡

必须通过 just check、just test、just build。默认 pytest 离线，不读取实际账户配置；新增测试集中覆盖价格数学、字段契约及执行链路，不为简单路径建立大量重复用例。

覆盖整数及 0.2/0.5/0.005 步长、双向取整、尾差阈值内外、涨跌停及 5% 边界、错误方向、大数/极小数、缺失/非有限/颠倒资料和区间无合法价；改变全局 Decimal 精度仍不能引发取余异常。验证配置默认、自定义、零比例及非法值。

执行器离线证明 GFD/IOC 均使用同一个实际价，CSV/原生引用/订单确认核对实际价；无效价格在持仓 CSV 和交易动作之前拒绝，确认失败不重发，GUI 收尾仍执行。HTTP 测试覆盖输入结构不变、价格处理说明、响应模型及错误 context；SQLite 检查原参数冲突、原响应重放、旧表迁移及中断时实际价保存。

在线通过独立 debug 探针，仅在环境、账户绑定、就绪和交易状态均明确的 SimNow 中执行，单笔一手。验证买卖取整、尾差修正、允许截断以及拒绝请求，核对真实预埋/委托价格、订单身份与结果，结束只撤销或平掉本次操作。实际市场规则不满足静态例子时使用当前资料选择等价输入，不修改真实终端元数据。

上线直接替换旧的显式限价拒绝规则，不设置新旧双轨；新配置有默认值，旧 config.toml 无需改动。SQLite 增列可向后读取已存记录，旧响应保持原样；新镜像经受控重启使用，不热换执行中的模块。实现后同步有效 current spec；本任务文档不记录实际测试统计。
