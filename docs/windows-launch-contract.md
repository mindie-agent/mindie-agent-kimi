# Windows 原生启动契约

中文 | [English](windows-launch-contract.en.md) · [返回文档索引](README.md)

本文定义 `mindie-agent-kimi` 在原生 Windows 环境下的无 Shell MCP 启动、子进程 Job Object 归属与路径解析契约。完整英文细则见 [`windows-launch-contract.en.md`](windows-launch-contract.en.md)。

## 1. 无 Shell 直接启动与 Job 归属

- Windows 下 MCP 与 Hook 入口直接通过固定的 Python 可执行文件启动，不经过 `cmd.exe` 或 PowerShell 字符串拼接，防止路径空格、引号转义与命令行注入问题。
- 辅助子进程纳入带 `KILL_ON_JOB_CLOSE` 的 Windows Job Object 管理，确保父进程退出或取消时可靠回收全部后代进程。
