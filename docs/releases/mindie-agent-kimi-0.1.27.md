# mindie-agent-kimi 0.1.27

- **确定性工具解除必填 `request_nonce` 门禁（`P2.7`）**：`remote_tools` 与 `component_tools` 切换为可选 `request_nonce`（`require=False`），无 Nonce 时回退至连接级稳定标识 `MINDIE_MCP_CONNECTION_ID` 并支持缺省 `cwd`；同时将已消费 Nonce 重放拦截置于过期判定之前，`knowledge` 表面继续强制严格会话与手动入口授权（`P2.1`）。
- **提示词与命令清理**：精简并润色 `kimi.plugin.json`、`SKILL.md` 与 `commands/*.md`。
