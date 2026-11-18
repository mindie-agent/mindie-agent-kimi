---
name: "mindie-agent"
description: "Ascend NPU, vLLM-Ascend, xLLM, MindIE-SD, and AscendC/Triton engineering workflow with default-enabled L1-L4 graded knowledge, remote NPU development, profiling, Roofline simulation, CANN fault triage, and cluster lease coordination."
---

# MindIE Agent Runtime Workflow

## 1. Default-Enabled MCP Tooling

- **Ready out of the box**: `remote` (`remote-dev`), `components` (`mindie_status`, `mindie_doctor`, `diagnostics_status`, `diagnostics_bundle`, `coordinator`, `debugger`, `profiler`, `sim`, `npu_top`), and `knowledge` (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`) work over MCP by default in every Kimi session, subagent, and automation without session activation, `context_file`, or `request_nonce`. Never pass or invent native session identity.
- **Readiness check**: call `mindie_doctor` to report runtime/harness health, contribution tier, diagnostics policy, and remote-dev configuration before deep troubleshooting.

## 2. Non-Blocking Task Continuity Rule

- **MindIE tools are accelerators, never roadblocks.** This covers `remote-dev` (`remote_*`), `coordinator`, `knowledge_*`, and `mindie_doctor`.
- If any tool fails, **report the concrete error once** and continue **immediately** via native `ssh` / `scp`, an authorized terminal, or task-local scripts. Do not retry the same failing helper blindly, do not stall awaiting a tool that is unavailable, and do not ask the user to fix tooling before continuing the engineering work.
- **Reconcile uncertain remote writes before repeating.** When a remote mutation may have been applied but its result was lost, inspect the remote state (`remote_TaskOutput`, `remote_TaskList`, a native `ssh` probe) before re-running the write.
- A missing optional component is a degraded capability, not a task blocker: continue with the remaining surfaces and the native tools that are available.

## 3. Code-First Investigation, Knowledge & NPU Discipline

- **Inspect code first**: read target source files and callers before querying knowledge; proceed directly when the codebase already explains the issue.
- **Two-step `L1–L4` retrieval**:
  1. `knowledge_query`: pass concise technical terms, optional `domain`, observed `conditions`, and `token_budget` (default `8000`, up to `64000`). Treat `condition_unknown` as unproven—probe missing facts before applying the unit.
  2. `knowledge_explain`: batch-read exact `mindie://<domain>/units/<id>@<revision>` URIs in `refs=[...]` with observed `conditions`, visible `known_refs=[...]`, and `token_budget` (default `16000`).
- **Feedback, distillation & NPU execution**: record `knowledge_feedback` on `feedback_ref` when useful, and call `knowledge_distill` at `task_state="verified_milestone"` or `"completed"` (`reproduced_empirical` requires repeated verified runs; `L4` requires verified leave-one-out ablation evidence). Acquire or verify a `coordinator` lease before running workloads on physical NPUs, inspect live `coordinator` (`execution_id`) and `remote-dev` (`job_id`) handles before resubmitting, and pass explicit `--time-window`, `--step-window`, or `--full-range` plus `--clock-domain` to `profiler`.
- **De-biased exploration**: when repeated `L3` attempts stall on a structural bottleneck, query `L4` structural mutators (`mode="explore"`) and optionally spawn a clean subagent seeded with `exploration_context.subagent_fact_pack.prompt_brief`.

## 4. Progressive References (Load Only When Needed)

- Tool schemas, `L1–L4` distillation & profiler timing discipline: [Domain Tooling](references/domain-skills.md)
- Diagnostic isolation & scale-up verification (`W1–W7`): [Diagnostic Reasoning](references/diagnostic-reasoning.md)
- Default session lifecycle, multi-domain routing & `Stop` capture: [Activation Lifecycle](references/activation-lifecycle.md)
