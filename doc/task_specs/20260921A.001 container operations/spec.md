# 容器入口与运行治理

## 任务边界

实施前完整阅读[主任务](../20260921A%20cfb%20trading%20api/spec.md)及其全部 spec 扩展。公共配置、just 命令、日志预算及生命周期以主任务 [spec_03_operations.md](../20260921A%20cfb%20trading%20api/spec_03_operations.md) 为唯一真值；本文件定义本阶段交付及验证。

本阶段在对应 JJ change 内实施以下范围，配置及内部消息优先使用 Pydantic：

- 新建 justfile 与容器内运维入口，迁移 `tools/bridge.py` 的 fetch/build/up/down/status/screenshot/logs/test 职责。
- 以 FastAPI/Uvicorn 取代 `cn_futures_bridge/api.py`、`__main__.py` 的标准库 HTTP 启动链路，迁移诊断及生命周期。
- 引入并锁定所需 Python 依赖，调整 Containerfile、构建上下文及容器测试目标；不升级 Wine 或终端。
- 更新 config.example.toml、配置解析、调用方及测试，移除 api.token，纳入队列和容量预算，提供显式迁移和忽略备份规则；交易状态不引入日历配置或时间规则数据。
- 建立结构化日志、子进程输出收集、轮转、工件登记及清理入口，处理容量和低磁盘空间。
- 更新已有启动 current spec 和 README，仅描述本阶段实际实现；执行器和业务路由仍留在 task spec。

本阶段不接入账户登录、不操作真实交易、不提供八个业务占位路由，不复制 debug 探针为生产调用，不创建新的外部 API 路径管理文件。

停止线：just 可在仅有 just/Podman/基础 shell 的宿主运行；两种镜像可启动空账户终端并提供无鉴权诊断；配置迁移无凭证泄漏，日志和工件预算生效；旧宿主正式入口及旧鉴权实现退出。以容器构建、just check 和少量离线 pytest 完成基础验证。

## 任务规范

### 入口及配置迁移

just 只编排 Podman 和必要的宿主文件复制，不在宿主导入 `cn_futures_bridge`，不要求安装 uv/Python 应用依赖。容器工具入口必须能校验配置和获取锁定安装包，而不启动 Wine、GUI 或网络交易连接。

build/run/up 的镜像选择及已有容器处理按公共契约。构建时校验安装包与固定版本；镜像上下文只加入源码、锁文件、示例和必需产物，实际配置、备份、debug、数据卷和运行日志不得进入镜像。

配置迁移仅识别已知旧 schema，不猜测未知字段含义。迁移先校验、建立专用备份，再原子替换；错误保留原文件和权限，输出中不包含 token 或账号密码。无须迁移时明确返回无变化。直接使用旧 api.token 时报告可执行的迁移命令，不悄悄接受旧鉴权选项。

`tools/bridge.py` 在迁移完成后退出正式入口，避免同时维护两个会解释配置和发布端口的宿主程序；复用逻辑可以进入容器内模块。测试及 README 一同移除旧命令依赖。

### FastAPI 诊断与生命周期

只提供公共规范中的诊断和文档入口；`/cfb/*` 尚未发布，不能返回模拟交易成功。所有诊断均无 Authorization 要求，OpenAPI 不包含安全方案或 Bearer 依赖。

一个 Uvicorn 服务进程拥有 runtime 生命周期；不能在模块 import 或每个请求中启动 GUI。配置验证、离线测试和 OpenAPI 生成不得误启真实终端。

本阶段保持 `stage=terminal_bootstrap`、窗口就绪含义、账户不自动登录及交易布尔值为 false。新增执行器状态字段输出 unavailable、队列深度 0、活动操作 null、未决数 0，能力为 unverified；不把占位元数据说成执行器已实现。

API 存活与 GUI 就绪分离。生命周期失败保留状态及可用诊断，停止时只回收本容器拥有的进程和文件锁。测试 HTTP 的对象与生产入口使用同一 FastAPI 应用，不另写一套测试服务。

### 日志、工件及磁盘预算

实现共用的日志输出、轮转和清理服务。Python 日志和子进程输出均纳入预算，持续消费 stdout/stderr，避免管道填满反向阻塞终端。重命名一个仍被子进程持有的日志文件不算完成轮转。

清理器区分已关闭日志、活动工件、已结束异常证据和保护中的最小请求状态。容量是诊断文件淘汰的唯一触发依据，按生成顺序淘汰最旧可删项；定期检查不按文件年龄删除。失败 CSV 与截图合计默认 300 MiB；符号链接、路径穿越、未知文件和客户端状态目录不进入删除范围。

活动登记支持创建、写入中、完成、释放和保护状态，后续终端执行器使用同一接口；测试用真实临时文件验证清理，不能只断言 mock 的删除方法被调用。孤儿工件只有在确认所属会话退出且不存在未决引用后可回收。

本阶段提供轻量操作数据库目录及预算核算支持，不发明交易恢复状态机；交易阶段登记防重发记录。没有业务执行器时 pause/resume/在线业务测试明确报告 unavailable，不能建立假执行器来返回成功。原生交易状态适配与业务路由由后续阶段承接，本阶段不构造开盘/休盘结果。

每个步骤提供结构化日志，优先关联现有错误文字和 CSV。截图由调用方明确说明诊断需要，未知窗口/焦点/复位异常可拍，普通参数错误、排队满或已有充分文字的失败不自动截图。同一未解除异常不重复拍；不建设复杂截图分类或异常自愈规则。

对终端自写文件做有界只读分类，识别诊断日志与会话/flow 状态。确认可安全轮转的日志登记到同一预算；未识别的目录不批量删除。持续增长而无可用治理办法属于边界缺口，不能宣称已解决全部日志膨胀。

定时、阈值及 `just clean` 共用清理逻辑，不能三个入口各写一套删除规则。没有运行容器时，clean 可启动不含 GUI 的工具容器，但必须取得数据目录排他所有权；取得不到则报忙，不旁路运行实例。

## 公开接口与用户写法

完整 TOML 和命令用法引用主任务运维扩展，不重复定义字段默认值。第一阶段必须实现以下用户主链：

```bash
just init-config
just build
just up vnc
just status
just screenshot
just logs
just clean
just down
just run headless
just down
just test
```

默认示例账户为空，上述启动只显示登录界面。对已有旧配置，先执行 `just migrate-config`；配置会保留账号值，但本阶段不登录。

`GET /healthz` 返回 `{"status":"ok"}`；`/v1/status` 中 `stage` 仍为 `terminal_bootstrap`，`login_state` 仍为 `unverified`，`trading_ready=false`。`/readyz` 是否为 200 仍由本阶段启动状态决定，不能据此发送业务请求。

反例：配置含旧 api.token 但未迁移、已有 config.toml.bak 将被覆盖、变体与运行实例不一致、无权限读取配置均明确失败；不覆盖秘密文件、不自动停止活跃实例。未实现的 `just test-simnow trade` 不得返回空测试通过。

## 测试、验证与阶段过渡

主要入口 `just check` 在 Podman 工具镜像内执行 `uvx ty check`，必须通过；不运行宿主 Python 应用或修改受控源码的检查。依赖版本由 uv.lock 锁定，工具镜像预装检查依赖，运行检查时关闭网络。

`just test` 只保留少量 pytest 冒烟检查：配置拒绝旧 token/错误类型、诊断无鉴权、OpenAPI 和未就绪状态。检查不挂载真实配置、不启动 Wine，不建设完整 GUI 或在线测试套件。

`just build` 构建双镜像。原有标准库 HTTP、token 和宿主 tools/bridge.py 正式入口在本阶段退出；README/current spec 同步实际基础行为。原生执行器与业务路由由后续阶段接入，当前能力为 unverified，不能以占位成功表示已经能交易。
