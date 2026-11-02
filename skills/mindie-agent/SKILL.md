---
name: mindie-agent
description: Explicit /mindie-agent entry for knowledge, domain skills, feedback, distillation and background capture in the current Kimi session. Configured deterministic tools remain available without this entry.
disableModelInvocation: true
---

# MindIE Agent (Unified Entry)

Use `/mindie-agent` to enable domain knowledge and experience capture for the current Kimi session.
`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`, and background `Stop` capture require the user's explicit `/mindie-agent` entry in this session (new chats, forks, and subagents require their own entry).

Remote and component tools are always available without `/mindie-agent`:
- `remote` MCP tools for remote file read/write, command execution, and background job management over SSH.
- `components` MCP tools: `mindie_status`, `diagnostics_status`, and `diagnostics_bundle` (local-only), plus `coordinator`, `debugger`, `profiler`, and `sim` (running `mindie-coordinator`, `mindie-debugger`, `mindie-profiler`, and `mindie-sim` with an `argv` array).

## On-Demand Knowledge Retrieval

- After calling `mindie_entry(op="init", request_nonce=...)`, inspect the relevant code and callers first. When the codebase already explains the issue and supports a fix and verification, proceed directly without querying knowledge.
- Probe missing environment or runtime facts before querying, and query knowledge when a domain contract, hardware constraint, or troubleshooting workflow can inform your next step.
- Reuse complete rules already visible in context. To read graded units (`L1–L4`), call `knowledge_explain(refs=[...], conditions=..., known_refs=[...], token_budget=..., request_nonce=...)` (even for a single unit), copying the exact `mindie://<domain>/units/<id>@<revision>` URIs and supplying a fresh `request_nonce`.
- Once the next step and its prerequisites are clear, stop reading and execute. Re-query if new evidence or verification results change the picture.

## Core Tools & Operational Contracts

1. **Bind & Initialize on Invocation (`request_nonce` on Every Call)**:
   - When `/mindie-agent` is invoked in the current Kimi session, call `mindie_entry` once with `op=init` (plus optional `domain="<domain>"` when activating a registered target domain for distillation/writes) and a fresh `request_nonce`.
   - Every subsequent `knowledge`, `remote`, and `components` MCP call must include a fresh `request_nonce`.
2. **Experience Loop & Unified Contribution Switch**:
   - In an activated session with contribution enabled for the project scope, eligible `Stop` events run automatically in the background.
   - Inspect `first_use` and `reporting_activation` from `mindie_entry`. Reuse saved `disabled`, `later`, and `read-only` settings without prompting. If no choice exists, present [用户须知](references/user-notice.md) once and accept `contribute` or `disabled` (`op=choose` with the user's `owner/repo` and GitHub account, or `/mindie-agent contribute owner/repo ACCOUNT`).
   - If a legacy knowledge-only `contribute` returns `needs_choice`, explain once that contribution now covers both public knowledge PRs and sanitized product fault Issues, and resolve it via `/mindie-agent contribute owner/repo ACCOUNT`.
3. **Graded Knowledge (`L1–L4`), Multi-Domain Routing, `knowledge_distill` & Long-Horizon Execution**:
   - **Discovery (`knowledge_query`)**: Accepts `query`, optional `domain` (default domain or a registered `domain_configs` route such as `"vllm-ascend"` or `"operators"`), `limit` (1–20), `conditions`, `continuation`, `domain_path`, `token_budget` (1–16000), `include_unknown`, `include_archived`, and `request_nonce`. Returns bounded navigation `cards` and deduplicated `foundation_cards` (`L1` premises).
   - **Closure Read (`knowledge_explain`)**: Call `knowledge_explain(refs=[...], conditions=..., known_refs=[...], token_budget=..., request_nonce=...)`. Shared prerequisites expand once in `requires`, requested bodies return untruncated in `units`, and `known_refs` lists only exact `id@revision` units currently visible in active context. Copy the exact returned `ref` (`mindie://<domain>/units/<id>@<revision>`) into `refs` and `feedback_ref` into `knowledge_feedback` (always pass full `mindie://<domain>/...` URIs rather than bare IDs).
   - **Usage Feedback (`knowledge_feedback`)**: Records `feedback_ref` with optional `event_type` (`discovered` | `loaded` | `applied` | `evaluated` with `run_id`, optional `step_id`, and `outcome="supported"|"refuted"|"inconclusive"`) and/or `rating="up"|"down"`.
   - **Distillation (`knowledge_distill`)**: Call `knowledge_distill(inputs, task_state, request_nonce, domain=..., related_refs=[...], proposal=...)` at `task_state="verified_milestone"` or `"completed"`. `observed` or fault/build evidence (`compile_failed`, `infra_failed`, `measurement_invalid`) can be distilled as `single_observation` or `hypothesis`; `reproduced_empirical` requires repeated `verified` evidence (`repetitions >= 2`), and `L4` requires a verified ablation receipt. Pass `related_refs` for delta comparison (`revise`/`merge`/`supersede`), or pass a `mindie-distill-decision/1` `proposal` for deterministic validation and application (`model_calls=0`).
   - **Three-Valued `conditions` (`matched` | `mismatched` | `condition_unknown`)**: Units separate functional `tier` (`L1–L4`) from `evidence_strength` (`hardware_spec`, `reproduced_empirical`, `single_observation`, `hypothesis`). When a unit's required premise or the caller's observed fact is missing or `"unknown"`, status is `condition_unknown`; probe the missing fact before applying the unit.
   - **Diagnosis, Exploration & De-Biased Subagents**: Judge experiments independently on `Performance`, `Correctness/Invariants`, and `Diagnostic Information Gain`. When `assess_stagnation` reports three consecutive unimproved attempts on the same L3 route, call `knowledge_query(query=..., mode="explore", native_subcontext=True, ledger_entries=[...], bottleneck_summary={...}, goal=..., request_nonce=...)` and seed a fresh native subagent only with `exploration_context.subagent_fact_pack.prompt_brief` (`schema_version == "mindie-rsi-subagent-pack/1"`). See [Diagnostic Reasoning](references/diagnostic-reasoning.md) for first-divergence checks, observer controls, and `W1–W7` / `C1–C5` workflows.
   - **Launch-Time `in_flight_runs` Registration & Recovery**: Immediately when `coordinator` or `remote-dev` starts a managed run or background job, atomically update `PROGRESS.md` (`os.replace`) to record its `run_id` (`run_id == execution_id`) or `job_id`, `slot_id`, endpoint, and `Run Manifest` path under `in_flight_runs` before waiting or ending the turn. After any interruption, compaction, or resume, check `in_flight_runs` first rather than submitting a duplicate run. Record condition-scoped hypothesis outcomes in `experiment-ledger.jsonl`, and treat `candidate_for_sequential_pruning` with `pruned_combination_verified=false` as **RETAIN**.
   - **NPU Lease & Profiler Discipline**: Use `npu_count=0` only for steps confirmed to need no device. Device JIT, warmup, performance timing, attribution checks, and `mindie_profiler` runs on the same physical NPU (or same A3 dual-Die package) require an exclusive `coordinator` lease. Pass explicit `--time-window START:END`, `--step-window STEP=START:END`, or `--full-range` and explicit scene/`--clock-domain` annotations to `mindie_profiler`.

## Progressive Reference Loading (Read on Demand)

Load reference files only when needed for the current step:

- **First-use GitHub authentication, enterprise-network token login, or auth troubleshooting**: [GitHub Sign-In](references/github-authentication.md).
- **First-use onboarding and workspace setup**: present [用户须知](references/user-notice.md), then follow [Workspace Setup](references/workspace-setup.md).
- **Detailed tool schemas (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `remote_*`), graded response fields, `PROGRESS.md` + `experiment-ledger.jsonl`, and profiler rules**: [Domain Tooling](references/domain-skills.md).
- **Session lifecycle, multi-domain routing, consent configuration, or recovery**: [Activation Lifecycle](references/activation-lifecycle.md).
- **Sharing scope, local redaction rules, or public Git history questions**: [用户须知](references/user-notice.md).
