# 20260921A.002 终端执行器与可靠交易闭环

- 概括：将 SimNow 登录、CSV 主链、键盘、原生交易状态和必要 GUI 操作纳入唯一执行器，提供快速提交、界面复位、独立查询及轻量防重发。
- 级别：三星；涉及 GUI 所有权、不可逆发送、私有终端入口和提交结果判定。
- 影响范围：终端适配、原生 helper、进程通信、队列、领域模型、操作数据库、运行就绪及模拟验证入口。
- 主任务：[20260921A](../20260921A%20cfb%20trading%20api/spec.md)。
- 前置任务：[20260921A.001 container operations](../20260921A.001%20container%20operations/spec.md)。
- 相关 current spec：[project_principles.md](../../current_specs/project_principles.md)、[bootstrap.md](../../current_specs/bootstrap.md)、[terminal_capabilities.md](../../current_specs/terminal_capabilities.md)、[trading_status.md](../../current_specs/trading_status.md)。
