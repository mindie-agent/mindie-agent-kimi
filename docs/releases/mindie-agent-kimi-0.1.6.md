# mindie-agent-kimi 0.1.6

本版本将 active runtime 的 remote-dev 提升至正式 0.10.8（`4b1c5cab5d36cdaff79d27e4288c6d5f60667c72`），并在 Kimi MCP 适配器中保留共享组件的缺参修复反馈。

直接 SSH 端点需要 host 与 port 同时提供；port=22 是示例。工具与参数描述说明 alias、已注册 resolver 以及省略端点选择字段时的配置默认值。漏传 port 或操作参数时，模型可见文本列出缺少字段和纠正动作，结构化内容包含 `input_repair` 与 `caller/not_sent`。这允许 Agent 修正参数后发起新调用；适配器不自动重放，副作用不确定的错误仍按原合同处理。

旧 0.1.2 更新器使用的 bootstrap 依赖三元组保持不变：knowledge 0.13.3（`38a42f6`）、remote-dev 0.10.6（`0463a13`）、diagnostics 0.5.2（`801a01b`）。候选准备和 CI 通过 runtime-active-overrides.txt 选用 remote-dev 0.10.8 与 diagnostics 0.5.4（`5ae643e`），knowledge 仍为 0.13.3。bootstrap 安装完成不等同于 active runtime 已切换。

验证覆盖真实共享 parser/call_tool 的缺参错误、公开投影、隐私字段过滤及既有更新器回归；三系统 CI 和正式发布流水线验证最终提交。真实模型补齐 port 的发布前实测使用 Codex CLI 与 DSH 原生 MCP 客户端，不冒充 Kimi 完整宿主升级实测。Release 附带精确 tag 的测试、微基准报告和校验和；宿主实际加载状态须单独确认。
