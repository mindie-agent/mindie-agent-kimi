# Activation Lifecycle (Kimi)

- **Session Identity & Manual Activation**: Native identity is `session_id` on hook stdin (resumed sessions keep the same ID; `/fork` creates a new ID with its own independent boundary). `knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`, and background `Stop` capture require an active manual-entry lease (`entry_enabled == 1`) granted by `/mindie-agent` in the current session. Deterministic `remote-dev` and `components` tools require only the `PreToolUse` `request_nonce` binding.
- **Entry Operations (`mindie_entry`)**:
  - `op="init"`, `op="choose"`, `op="deactivate"`, and `op="recover"` consume the verified `/mindie-agent` turn origin once with a fresh `request_nonce`.
  - `op="status"` (`mindie_entry(op="status", request_nonce=...)`) returns read-only `status_facts(session)` (`configured`, `knowledge_enabled`, `this_session`, `diagnostics`, `reporting`).
- **Unified Contribution & Fault-Reporting Switch**:
  - `mindie-consent.json` (`mindie-consent/1`) stores the persistent choice (`contribute` or `disabled`) governing both public knowledge sharing and sanitized product fault reporting, while `mindie-community.json` (`mindie-community-config/1`) stores project scope and repository bindings.
  - When enabling contribution (`contribute`) for a target public knowledge repository `owner/repo` (e.g., `mindie-agent/knowledge-vllm-ascend`), ensure the personal fork exists (`gh repo fork owner/repo --clone=false`) and bind `--community-fork ACCOUNT/repo`.
  - Keep three states distinct when reporting status: (1) local consent config written, (2) background organizer/reporter worker running, and (3) external GitHub Issue or knowledge PR created (inspectable via `/mindie-agent:reporting-status`).
- **Multi-Domain Registration, Target Activation & `deactivate`**:
  - Register an additional domain route with `python -m mindie_knowledge.loop.domain_routes register --adapter-config <path> --domain <domain> --engine-config <path>`.
  - Distilling or contributing to a registered target `domain` requires that domain's own consent and `project_root` scope plus `mindie_entry(op="init", domain="<domain>", request_nonce=...)`.
  - `/mindie-agent:deactivate` (`mindie_entry(op="deactivate", request_nonce=...)`) revokes the current `session_id` across the default domain and all registered `domain_configs`, returning `status="partial"` with per-domain `domains[]` details if any domain fails during revocation.
- **`Stop` Capture vs. Long-Horizon Task Completion**: An eligible `Stop` event hands off the completed turn's public transcript slice in the background. It does not mean a multi-step engineering task or remote benchmark is finished; check remote job status (`remote_job_status`), `Run Manifest` artifacts, and `PROGRESS.md` / `experiment-ledger.jsonl` before concluding a long-horizon task.
