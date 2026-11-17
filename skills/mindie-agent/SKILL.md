---
name: mindie-agent
description: Unified /mindie-agent entry for Ascend knowledge, domain skills, feedback, distillation, and optional session capture in Kimi Code. Configured deterministic tools remain available without this entry.
disableModelInvocation: true
---

# MindIE Agent (Kimi Code Unified Entry)

## 1. Core Operating Stance

- **Keep the user's task moving**: Knowledge retrieval, onboarding, diagnostics, and local hypothesis tracking are optional helpers. If any helper fails, report the concrete error once and continue with deterministic MCP tools or authorized host-native tools. Reconcile any `unknown` remote write before repeating it.
- **Zero-gate deterministic tools (`P2.7`)**: `remote` (`remote-dev`) and `components` (`mindie_status`, `diagnostics_status`, `diagnostics_bundle`, `coordinator`, `debugger`, `profiler`, `sim`) work in every Kimi session, subagent, and automation without `/mindie-agent`, `context_file`, or a required `request_nonce`; `coordinator` runs are addressed directly by `execution_id`, and `remote-dev` background jobs by `job_id`. Obtain a `coordinator` lease (or verify an existing reservation) before running workloads on physical NPUs, pausing only that device step if ownership cannot be confirmed.
- **Single `/mindie-agent` entry & strict knowledge gate (`P2.1`)**:
  - When the user invokes `/mindie-agent` (optionally followed by a task prompt), call `mindie_entry(op="init", request_nonce=...)` once (with optional `domain="<domain>"`) to activate the current session's knowledge lease and expose `knowledge_*` tools.
  - Subcommand gestures on the single `/mindie-agent` entry (`status`, `deactivate`, `reporting-status`, `sharing-enable`, `sharing-disable`, `sharing-status`, `recover`) map directly to `mindie_entry(op=..., request_nonce=...)`.
  - All `knowledge` surface MCP calls (`mindie_entry`, `knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`) require an active session lease and a fresh `PreToolUse`-bound `request_nonce`.

## 2. Advisory Knowledge & Domain Tooling

- **Inspect code first**: After `mindie_entry(op="init", ...)`, inspect the relevant code and callers first; proceed directly when the codebase already explains the issue.
- **Two-step retrieval (`L1–L4` graded units)**:
  1. `knowledge_query`: pass concise technical terms, optional `domain`, observed `conditions`, `token_budget` (default `8000`, up to `64000`), and a fresh `request_nonce`. Treat `condition_unknown` as unproven—probe missing facts before applying the unit.
  2. `knowledge_explain`: pass exact `mindie://<domain>/units/<id>@<revision>` URIs in `refs=[...]`, observed `conditions`, currently visible `known_refs=[...]`, `token_budget` (default `16000`), and a fresh `request_nonce`.
- **Optional feedback & distillation**: Record `knowledge_feedback` on `feedback_ref` when useful, and call `knowledge_distill` at `task_state="verified_milestone"` or `"completed"` (`reproduced_empirical` requires repeated verified runs; `L4` requires verified leave-one-out ablation evidence).
- **Long-horizon runs & de-biased exploration**:
  - `coordinator` (`execution_id`) and `remote-dev` (`job_id`) persist authoritative run state across parent sessions, subagents, and compactions—inspect existing handles before resubmitting. Pass explicit `--time-window`, `--step-window`, or `--full-range` and `--clock-domain` to `mindie_profiler`.
  - When repeated `L3` attempts stall on a structural bottleneck, query `L4` structural mutators (`mode="explore"`) and optionally spawn a clean subagent seeded with `exploration_context.subagent_fact_pack.prompt_brief`, verifying multi-factor changes on the operator's real interface.

## 3. Non-Intrusive First-Use Onboarding

- Defer optional onboarding when the user is focused on an active task; reuse saved settings (`contribute`, `disabled`, `read-only`, `later`) without prompting again.
- During initial setup or when `needs_onboarding_prompt: true` (or `mindie_entry` reports `first_use` unset / `needs_choice`), briefly reference [用户须知](references/user-notice.md) and ask via `AskUserQuestion` in the user's language:
  1. **Workspace**: Keep the current directory when already inside a repository checkout, or initialize/select `vllm-ascend-workspace` when starting fresh;
  2. **Community Sharing & Sanitized Fault Reporting**: Enable (`setup.py sharing --enable --project-root "$WORKSPACE_ROOT"` or `mindie_entry(op="choose", choice="contribute", ...)`) vs. Keep Local-Only (`setup.py sharing --disable` or `choice="disabled"`);
  3. **Optional GitHub Star**: Ask lightly whether to star `mindie-agent/mindie-agent` (only star on an explicit yes).
- Report local consent state, background reporter health, and external GitHub Issue/PR creation separately.

## 4. Progressive References (Load Only When Needed)

- GitHub sign-in & enterprise network token login: [GitHub Sign-In](references/github-authentication.md)
- Workspace setup details: [Workspace Setup](references/workspace-setup.md)
- Detailed schemas & profiler timing discipline: [Domain Tooling](references/domain-skills.md)
- Diagnostic isolation & scale-up verification (`W1–W7`): [Diagnostic Reasoning](references/diagnostic-reasoning.md)
- Session lifecycle, multi-domain routing & recovery: [Activation Lifecycle](references/activation-lifecycle.md)
