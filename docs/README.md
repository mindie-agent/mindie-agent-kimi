# MindIE Agent Kimi Code 适配器文档索引

中文 | [English](README.en.md) · [返回仓库首页](../README.md)

本目录汇总 `mindie-agent-kimi`（Kimi Code 原生适配层）的自动更新、框架稳定性、故障诊断、整理器边界、服务连续性、测试边界与 Windows 启动契约文档。所有文档均提供中英双语版本。

## 核心技术文档

| 文档（中文） | English Counterpart | 说明 |
|---|---|---|
| [自动更新机制](update.md) | [Automatic Update](update.en.md) | 后台代数构建、原子指针切换、回滚回执、命令与宿主重载边界 |
| [框架稳定性与验证](framework-stability.md) | [Framework Stability](framework-stability.en.md) | 日常使用契约、组件预检与测试命令、Windows 原子配置读写与验收边界 |
| [故障诊断与本地取消](failure-diagnostics.md) | [Failure Diagnostics](failure-diagnostics.en.md) | 只读状态诊断、取消与 EOF 进程清理边界、远端作业引用保留 |
| [整理器故障与取消证据](organizer-diagnostics.md) | [Organizer Diagnostics](organizer-diagnostics.en.md) | 整理器静态退出码分类、进程组生命周期管理与独立验证记录 |
| [插件更新期间的服务连续性](service-continuity.md) | [Service Continuity](service-continuity.en.md) | 更新事务中的知识服务停止、有界恢复、回滚交接与验证边界 |
| [测试边界与故障检测](test-boundaries.md) | [Test Boundaries](test-boundaries.en.md) | 跨平台 CI 检查顺序、独立故障维度与前后端传输边界测试 |
| [Windows 原生启动契约](windows-launch-contract.md) | [Windows Launch Contract](windows-launch-contract.en.md) | Windows 无 shell MCP 启动、子进程 Job 归属与原生边界验证 |
