# 整理器故障与取消证据

中文 | [English](organizer-diagnostics.en.md) · [返回文档索引](README.md)

本文说明 `mindie-agent-kimi` 调用本地 Kimi CLI 整理器（Organizer）时的静态退出分类、进程组生命周期管理与验证边界。完整英文细则见 [`organizer-diagnostics.en.md`](organizer-diagnostics.en.md)。

## 1. 隔离调用与静态错误分类

- 整理器在隔离配置下运行，不加载业务会话的 Hook、MCP 或可变工具集。
- 启动失败、非零退出码、输出格式不合法与调用取消均映射为静态可定位阶段码，不将原始对话或敏感环境变量写入公开诊断。

## 2. 进程组清理保障

- 整理操作拥有独立的进程组与临时目录；取消或异常退出时先回收整个整理进程组，再清理临时目录，并完整保留已发生的模型用量记录。
