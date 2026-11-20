# 插件更新期间的服务连续性

<b>中文</b> | <a href="service-continuity.en.md">English</a> · <a href="README.md">返回文档索引</a>

本文说明 `mindie-agent-kimi` 在执行后台版本更新与回滚期间，如何保障知识服务的连续性、配置指纹一致性与 `Stop` 事件可靠投递。

---

## 1. 更新事务中的服务停止与有界恢复

- **精确停止与原子重绑**：在更新事务获取 `.kimi.json.update.lock` 排他锁后，`ensure_knowledge_binding(..., retire_previous=True, already_locked=True)` 会确认旧代拥有的本地知识服务端点已退役，生成全部 9 个公开知识域的内容寻址 `engines/kimi/<20hex>/<domain>.engine.json`，并原子更新权威 `kimi.json`（`python`、`component_release` 与 `domain_configs`）。
- **按需恢复原则**：仅在本次更新前原本处于运行状态、且由本次更新流程退役的知识服务才会被按需恢复；若更新前服务本就未启动，或任务租约已被显式撤销（`inactive`），则保持未启动状态，绝不在更新阶段隐式激活任务或调用大模型。

---

## 2. `Stop` 采集交接与回滚安全

- **诚实身份与持久化 `Stop` 交接**：对于已开启社区贡献且位于批准 `project_roots` 内的活跃会话，`entry.py:_stop` 诚实区分原生 `turn`（`turn_id` + `last_assistant_message`）与确定性文件长度检查点 `notification`（`event_id`），在 `6.0s` 预算内委托 `run_stop_capture` 投递增量采集引用并永久保持 `inactive` 撤销不复活。
- **安全回滚与状态可见**：若更新切换失败并触发回滚，仅在确认旧插件包与旧代数指针均已恢复后才恢复旧服务；若服务交接本身中断，更新状态会如实保留 `service_handoff` 诊断标记，后续普通知识同步不会掩盖该状态，也不会在定时任务中盲目无限重试拉起。
