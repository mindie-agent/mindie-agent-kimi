# 故障诊断与本地取消

中文 | [English](failure-diagnostics.en.md) · [返回文档索引](README.md)

本文说明 `mindie-agent-kimi` 的只读状态诊断、本地子进程取消与 EOF 清理边界，以及远端作业引用保留机制。完整英文细则见 [`failure-diagnostics.en.md`](failure-diagnostics.en.md)。

## 1. 只读状态查询与结构化诊断

- 状态查询命令（`/mindie-agent:status`、`/mindie-agent:reporting-status`）仅执行纯只读检查，绝不隐式启动服务、调用模型或重放失败操作。
- 内部工具故障返回静态阶段、错误分类与本地诊断关联 ID，同时保留原始错误与退出码。

## 2. 本地取消与远端作业分离

- 本地调用取消或标准输入 EOF 会终止当前调用拥有的本地辅助进程树；而通过 `remote-dev` 启动的远端持久后台作业拥有独立生命周期，保留其作业引用供后续查询或显式停止。
