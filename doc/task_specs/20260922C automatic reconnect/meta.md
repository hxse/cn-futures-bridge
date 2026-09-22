# 20260922C 自动重连与账户配置

- 概括：分组保存模拟与实盘账户，固定选择一个账户，并默认每 10 分钟通过唯一执行器恢复连接。
- 级别：三星；涉及进程所有权、会话更新及交易请求的失败语义。
- 影响范围：配置及迁移、登录识别、终端生命周期、调度器、HTTP 状态和文档。
- 主任务：无。
- 前置任务：20260922B settlement confirmation。
- 相关 current spec：doc/current_specs/terminal_execution.md、doc/current_specs/bootstrap.md、doc/current_specs/api.md。
- 替代范围：旧单组账户配置及连接故障后只能人工重启的恢复策略；未知窗口、身份异常及交易结果不明仍保留人工核对。
