# 20260921A.001 容器入口与运行治理

- 概括：建立 just 容器入口，迁移 FastAPI 诊断和无鉴权配置，统一日志轮转及工件空间治理。
- 级别：二星；涉及启动、配置、镜像、诊断和文件生命周期，交易执行尚不启用。
- 影响范围：justfile、Containerfile、Python 依赖、配置解析、HTTP 诊断、日志/清理器、用户说明和离线测试。
- 主任务：[20260921A](../20260921A%20cfb%20trading%20api/spec.md)。
- 前置任务：20260921A cfb trading api。
- 相关 current spec：[bootstrap.md](../../current_specs/bootstrap.md)、[project_principles.md](../../current_specs/project_principles.md)。
