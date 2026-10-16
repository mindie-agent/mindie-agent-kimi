# 插件更新期间的服务连续性

中文 | [English](service-continuity.en.md) · [返回文档索引](README.md)

本文说明 `mindie-agent-kimi` 在执行版本切换与回滚期间，如何保障后台知识服务的连续性、配置指纹一致性与安全交接。完整英文细则见 [`service-continuity.en.md`](service-continuity.en.md)。

## 1. 精确服务归属与平滑交接

- 知识服务复用要求引擎配置指纹、活动安装绑定与工作器健康状态完全一致。
- 更新切换前先停止旧代数拥有的服务实例，确认新代数服务就绪后再对外提供服务；若交接失败，仅在旧包与旧指针均确认恢复后才恢复旧服务，否則保留明确的 `service_handoff` 诊断状态。
