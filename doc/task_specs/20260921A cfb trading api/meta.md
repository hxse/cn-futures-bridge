# 20260921A CFB 交易服务与可靠终端执行

- 概括：以无弹窗 CSV 主链提供八个兼容 CTP 命名的 FastAPI 业务接口，统一单账户执行、日志留存及容器入口。
- 级别：三星；涉及交易副作用、接口语义兼容、结果未知恢复和跨进程生命周期。
- 影响范围：HTTP 服务、终端适配、运行配置、请求与操作记录、日志清理、Podman/just 入口及验证。
- 主任务：无。
- 前置任务：20260919A terminal bridge foundation。
- 相关 current spec：[project_principles.md](../../current_specs/project_principles.md)、[bootstrap.md](../../current_specs/bootstrap.md)、[terminal_capabilities.md](../../current_specs/terminal_capabilities.md)、[trading_status.md](../../current_specs/trading_status.md)。
