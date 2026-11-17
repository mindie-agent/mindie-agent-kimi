# mindie-agent-kimi 0.1.29: Strict Runtime `/mindie-agent` Skill vs. Install-Time Onboarding Boundary

[中文](mindie-agent-kimi-0.1.29.md)

- **Preserves Native Manual `/mindie-agent` Trigger (`disableModelInvocation: true`)**: Keeps Kimi Code's manual-only `/mindie-agent` session lease gate (`P2.1`) while deterministic `remote` and `components` MCP surfaces remain zero-gate default-on (`P2.7`).
- **Moves Install & Consent Guides Out of Runtime `skills/`**: Relocates `user-notice.md`, `github-authentication.md`, and `workspace-setup.md` from `skills/mindie-agent/references/` to `docs/install/`, and removes all first-use onboarding, `vllm-ascend-workspace` setup, `gh auth login`, and GitHub Star prompts from `skills/mindie-agent/SKILL.md` and its runtime reference files.
