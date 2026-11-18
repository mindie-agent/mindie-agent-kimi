# mindie-agent-kimi 0.1.30：全量 MCP 工具静默默认常驻、零 `request_nonce` 门禁与快速 `Stop` 采集

[English](mindie-agent-kimi-0.1.30.en.md)

- **`knowledge_*` 与确定性工具开箱默认常驻（零 `request_nonce` 必填门禁）**：`knowledge` MCP 服务在启动阶段即默认暴露全部 `L1–L4` 知识工具（`knowledge_query`、`knowledge_explain`、`knowledge_feedback`、`knowledge_distill`、`knowledge_sync`），取消必填 `request_nonce` 与前置会话激活门禁；调用时优先解析可选绑定提示并平滑回退至连接级会话标识，按需自动签发默认会话租约（`not_before=0.0`），同时保留重放拦截与本地回执诚实（`isError=False` + `receipt_warning`）。
- **静默语义触发 `SKILL.md` 与零 SQLite 快速 `Stop` 采集短路**：移除 `skills/mindie-agent/SKILL.md` 中的 `disableModelInvocation` 限制并精简为纯运行时工作流指南；`Stop` Hook（`scripts/bridge.py`）在打开 SQLite 前优先校验 `mindie-consent.json` 与 `mindie-community.json` 授权目录范围，未开启共享或不在授权工作区时 `< 100ms` 零 SQLite 开销快速返回，已开启且命中范围时自动补齐会话租约并投递后台脱敏采集。
