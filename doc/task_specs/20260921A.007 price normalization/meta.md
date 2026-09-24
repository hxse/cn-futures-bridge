# 20260921A.007 限价归一化与价格边界

- 概括：按合约价位和涨跌停分类调整或拒绝限价，保留请求价、实际价与调价原因。
- 级别：三星；涉及报单参数、价格约束、幂等及异常恢复中的公开事实。
- 影响范围：价格校验、限价 GFD/IOC、执行结果、配置、操作记录、OpenAPI 与相关测试。
- 主任务：20260921A。
- 前置任务：20260921A.006。
- 相关 current spec：doc/current_specs/api.md、doc/current_specs/market_orders.md、doc/current_specs/terminal_execution.md、doc/current_specs/bootstrap.md。
