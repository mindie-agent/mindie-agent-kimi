# mindie-agent-kimi 0.1.27

- **Optional `request_nonce` on deterministic tools (`P2.7`)**: Makes `request_nonce` optional (`require=False`) on `remote` and `components`, falls back to connection-stable `MINDIE_MCP_CONNECTION_ID` with omitted-`cwd` support when unbound, checks consumed-nonce replay ahead of expiry, and keeps `knowledge` strictly gated (`P2.1`).
- **Prompt and command cleanup**: Streamlines and polishes `kimi.plugin.json`, `SKILL.md`, and `commands/*.md`.
