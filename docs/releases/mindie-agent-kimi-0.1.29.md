# mindie-agent-kimi 0.1.29：隔离运行时 `/mindie-agent` Skill 与安装期引导文档边界

[English](mindie-agent-kimi-0.1.29.en.md)

- **保持原生手动 `/mindie-agent` 门禁（`disableModelInvocation: true`）**：维持 Kimi Code 单一 `/mindie-agent` 手动入口与按会话知识租约准入（`P2.1`），`remote` 与 `components` 确定性 MCP 工具继续默认常驻零门禁可用（`P2.7`）。
- **彻底剥离运行时 `SKILL.md` 中的安装与首次配置内容**：将 `user-notice.md`、`github-authentication.md` 与 `workspace-setup.md` 从 `skills/mindie-agent/references/` 迁移至 `docs/install/`，并清理 `skills/mindie-agent/SKILL.md` 及其运行时参考文档中的首次配置提问、`vllm-ascend-workspace` 克隆、`gh auth login` 与 GitHub Star 提示，避免运行期会话浪费上下文。
