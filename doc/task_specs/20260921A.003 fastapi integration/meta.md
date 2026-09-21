# 20260921A.003 八路由兼容与集成验证

- 概括：将已验证的终端执行链路绑定到八个 FastAPI 路由，对齐参考契约并提供双镜像及最小契约检查。
- 级别：三星；涉及交易成功语义、HTTP 取消与副作用、跨模块恢复及公开返回模型。
- 影响范围：业务 router、共享模型绑定、错误和响应头、OpenAPI、current spec/使用说明与最小集成检查。
- 主任务：[20260921A](../20260921A%20cfb%20trading%20api/spec.md)。
- 前置任务：[20260921A.002 terminal execution](../20260921A.002%20terminal%20execution/spec.md)；继承其运行治理前置。
- 相关 current spec：[project_principles.md](../../current_specs/project_principles.md)、[bootstrap.md](../../current_specs/bootstrap.md)、[terminal_capabilities.md](../../current_specs/terminal_capabilities.md)、[trading_status.md](../../current_specs/trading_status.md)。
