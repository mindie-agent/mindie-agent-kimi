# 测试边界与故障检测

中文 | [English](test-boundaries.en.md) · [返回文档索引](README.md)

本文定义 `mindie-agent-kimi` 的跨平台 CI 检查顺序、独立故障维度与前后端传输边界测试规范。完整英文细则见 [`test-boundaries.en.md`](test-boundaries.en.md)。

## 1. 分层确定性验证

- 单元测试与契约测试覆盖命令包装、Hook 入队、配置原子读写、整理器进程组清理与 stdio 传输边界。
- 固定夹具测试用于验证确定性协议行为；原生 Kimi 宿主会话、真实模型调用与远端昇腾 NPU 执行作为独立的实机验收边界分别记录，不以单测通过冒充端到端实机验收。
