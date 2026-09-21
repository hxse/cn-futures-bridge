# 八路由兼容与集成验证

## 任务边界

实施前完整阅读[主任务](../20260921A%20cfb%20trading%20api/spec.md)及全部扩展，并复用[终端执行前置](../20260921A.002%20terminal%20execution/spec.md)的模型、调度器、操作记录与错误。主任务的接口和模型扩展是公开契约唯一真值，本文件不重新定义一套字段。

交付范围：发布八个 `/cfb` 路由、参考输入与 CFB 简洁返回绑定、快速提交和独立查询、快期原生品种交易状态、错误和追踪头、全路由排队、幂等/取消/复位适配，以及双镜像与端到端验证。

现有 FastAPI 服务保持唯一，业务 router 复用其生命周期；不新增第二个服务器、账户队列、日志清理器或交易状态表。运行配置、包版本和文件治理继续使用前置实现，不借集成阶段改变公开参数或静默降低验证条件。

按实际交付同步相关 current spec 和必要的 README/guide；不把未实现能力、临时运行统计或文档整理过程写成现行功能。current spec 只描述当前已经接入的链路；历史探针样本不等于正式 REST 集成已完成在线核验。

停止线：八个方法/路径及主要输入可对照参考，必要返回与实现一致；HTTP 快速提交及独立查询复用前置链路；每路由排队、界面复位、最小防重发、容量清理及原生状态映射/失败语义符合规范，两镜像等价。本轮以 just check 和少量离线 pytest 为主要验收；未运行的真实交易、状态切换及重连验证明确保留，不用类型检查替代在线证据。未实现的能力不能被长期占位错误替代，确不支持的交易分支仍须能力判定证据。

## 任务规范

### 路由及共享模型

`/cfb` 下仅提供主任务定义的三个 POST、五个 GET，方法和后缀精确匹配参考。不为批量、异步任务、登录、停止/恢复或任意 GUI 输入增加业务路由。

请求使用共享领域模型及统一规则；FastAPI 绑定 JSON/query 位置、错误列表及参数文档，不能把另一套默认值、枚举或类型转换放在路由里。JSON bool/数字字符串、重复 query、未知字段、错误撤单联合分支等在调用调度器之前拒绝。

mode=live、未支持能力、终端未就绪和队列不可准入分别映射主任务错误，不能以空数组或统一 500 掩盖。CFB request_id 用于逻辑追踪；账户快照交易日和品种状态均来自终端，observed_at 只标记观察时间，不按该时间推算开盘。

复用前置 CFB 必要模型，不复制 CTP 原字段全集、数字枚举或返回包装。成交价和委托价、资金服务器/本地值、总仓与可平量不混用；数值为有限 JSON number，编号保持字符串。balance 为单账户对象，其他数组和字段按主任务模型定义。

### HTTP 生命周期与副作用

八个业务路由的每次可执行请求均进入同一调度器，包括 CSV 读取和原生状态查询；路由不直接按键、调用核心 DLL、导入文件或复位 GUI。参数错误和同键结果重放不再次执行，不能为 GET 单独建立可能交叉操作的快捷通道。

接口挂起等待结果时，FastAPI 仍能响应健康和已有内存状态；业务等待不能用阻塞调用占住事件循环。请求协程取消与执行器生命周期分离，不能由路由 finally 释放仍运行的 GUI 所有权。

HTTP 客户端断开时，尚未开始的排队项按前置取消规则移除；已经执行的操作继续核对并持久化。对重试相同 Idempotency-Key 的调用方，返回进行中或原已保存结果，而非再次启动导入/发送。

正常提交返回 202/submitted：发送前核对、完成本地动作、快速读一次订单 CSV 并确认收尾可交权后返回，不循环等待柜台接受/成交或最终撤单。订单号暂无则 null；明确拒绝返回 rejected，本地影响不明返回 unknown。不能仅入队就返回 submitted，也不能因已提交后观察或复位出错而伪称没有提交。

fetch_trading_status 保持必填 exchange_id、product_id，绑定前置执行器的原生品种读取，返回严格 boolean 的 is_trading 和 source=terminal_native。正常非连续交易可以返回 false，未知编码、标的无效、断线、版本不匹配及超时按主任务分别报错；不把错误降级为空值、false 或旧成功结果。不新增合约状态参数/路由，不以交易所状态、GUI 颜色、时间规则或探测订单代替品种状态。查询不改变界面，仍按共同队列顺序执行。

### OpenAPI 与兼容说明

OpenAPI 准确声明八个方法及主要输入、撤单 discriminator、202 提交结果、自定义查询/错误模型、原生品种状态二值结果和可选 Idempotency-Key。没有 securitySchemes 或 Security 依赖，也不声明完整 CTP 返回兼容。

文档明确：CFB request_id 是自己的追踪编号；submitted 仅为本地提交，后续查订单/成交，仓位不单独证明某笔下单结果；observed_at 是观察时间，source 区分终端 CSV、控件文本及原生状态；市价/IOC/FOK 不降级模拟。字段及能力说明由同一模型和判定来源提供，不另维护虚假的已支持清单。

对照以主任务已转写的参考契约为准。默认测试不访问本机 5123 服务或其他网络；参考服务日后改动不自动重写本项目接口。测试比较业务路径、参数和返回语义，不要求内部模型类名和 operationId 与另一个项目相同。

### 运行与可观测性衔接

每个请求从 HTTP、排队、准备、执行到复位/返回保持同一逻辑追踪关系。观察来源和时间由真实执行产生，幂等重放保留原提交结果，不伪称新提交或重新查到柜台回报；后续查询中的异步拒单不倒改原 202 响应。

步骤日志和容量治理复用前置服务。异常优先日志/现有 CSV，只有未知窗口、焦点、卡住或复位失败等必要情况截图；不为普通失败每次截图，不按文件年龄删除。用户断开、序列化失败、清理或 pause/down 不得提前释放执行权或抹掉已知副作用。

本轮不新增在线测试套件或性能压测。将来核验接口性能时从真实 HTTP 入口计时，不能以探针或领域函数的耗时代替 API 端到端耗时。

## 公开接口与用户写法

完整请求和响应见主任务 [spec_01_api.md](../20260921A%20cfb%20trading%20api/spec_01_api.md)，字段集合见 [spec_02_models.md](../20260921A%20cfb%20trading%20api/spec_02_models.md)。以下是无需鉴权的真实最终调用写法：

集成版本的启动入口统一为 `just run`（默认 headless）和 `just run vnc`，均准备配置、构建对应镜像并启动；移除 `just up` 与显式 `just run headless` 写法。`just build` 仅作为独立构建辅助入口保留，镜像冲突仍要求先显式 down。

固定默认端口为 API 45173、VNC 45174、noVNC 45175，通过 TOML 保留覆盖能力。监听、宿主映射及提示由同一配置决定；相同发布端口被占用则启动失败，不自动寻找其他端口，不增加全局容器单例锁。

```bash
curl 'http://127.0.0.1:45173/cfb/fetch_balance?mode=sandbox&currency_id=CNY'
curl 'http://127.0.0.1:45173/cfb/fetch_positions?mode=sandbox&exchange_id=CZCE&instrument_id=RM701'
curl 'http://127.0.0.1:45173/cfb/fetch_trading_status?mode=sandbox&exchange_id=SHFE&product_id=rb'
```

单手限价下单示例，价格仅用于展示格式：

```bash
curl --request POST 'http://127.0.0.1:45173/cfb/create_limit_order' \
  --header 'Content-Type: application/json' \
  --header 'Idempotency-Key: rm701-open-001' \
  --data '{"mode":"sandbox","exchange_id":"CZCE","instrument_id":"RM701","side":"buy","offset":"open","volume":1,"price":2323,"time_in_force":"GFD"}'
```

该 POST 正常返回 202，例如：

```json
{"request_id":"cfb-123","submission_status":"submitted","order_id":null}
```

随后通过 orders、trades 查询委托和成交，positions、balance 作为账户结果核对。原生品种查询使用 `GET /cfb/fetch_trading_status?mode=sandbox&exchange_id=SHFE&product_id=rb`；终端原生值为连续交易时返回下例，时间字段不决定布尔值：

```json
{"request_id":"cfb-124","exchange_id":"SHFE","product_id":"rb","observed_at":"2026-09-21T14:00:00+08:00","is_trading":true,"source":"terminal_native"}
```

同键改价格返回 409 且无第二次下单；市价附带 price 返回 422；本地提交影响未知返回 502/unknown，不自动重发。暂未见持仓变化不能认定失败。读取成功且确为空才能返回空数组；原生状态未知返回 502/TERMINAL_DATA_INVALID，未登录或断线返回 503/SERVICE_NOT_READY，不能误判休盘。

## 测试、验证与阶段过渡

主要入口 `just check` 在宿主直接执行 `uvx ty check`，覆盖路由、共享 Pydantic 模型、异步等待及错误返回。开发环境首次使用 `uv sync --locked` 准备本地 `.venv`；ty 自动发现依赖，目标 Python 3.11，静态检查不依赖 Podman，也不受应用仅在容器运行的限制。`just test` 保留前置容器内少量 pytest，只增加最小 HTTP 契约检查：八个路径和方法、撤单 discriminator、无鉴权、主要合法参数能够到达就绪判断，以及未知/重复参数、bool 手数、错误交易条件和 live 在副作用前拒绝。

不新增完整并发矩阵、在线交易套件或压测。输入校验、FIFO、幂等键、防重发、客户端断开和界面所有权仍必须按规范实现；没有运行的 GUI 与真实交易范围准确说明，不通过削弱生产语义来满足检查。

通过 `just build` 构建 headless 与 vnc，使用相同业务层和原生 helper。必要的空账户启动检查使用隔离数据，不能覆盖用户已运行实例；不要求本轮等待特定交易时段或发出真实模拟委托。

启动入口调整以 shell 语法、just 命令展开及不启动真实容器的参数编排检查验证：两种 run 分别选择 headless/vnc，配置校验先于业务镜像构建，旧 up 入口退出。仅修改编排时不为验证而登录账户或运行交易终端。

市价、IOC/FOK、显式平仓等尚未核验分支继续标记 unverified，副作用前返回明确的能力错误，不宣布为永久不支持，也不以近似交易替代。自动登录、资金及非空快照、真实开平/撤单、重连与状态切换的完整在线覆盖由后续实测确认。

同步 current spec、README 和必要用户说明，移除业务尚未接入的过时表述；只写已实现行为及重要验证边界，不写本次测试统计或实施过程。主任务不重复执行相同验收。
