---
name: mindie-agent
description: Explicit /mindie-agent entry for knowledge, domain skills, feedback, distillation and background capture in the current Kimi session. Configured deterministic tools remain available without this entry.
disableModelInvocation: true
---

# MindIE Agent (Unified Entry)

Use `/mindie-agent` to enable domain knowledge and experience capture for the current Kimi session.
Never invoke this skill from topic matching. The entire knowledge flywheel—including `knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`, and `Stop` background capture—requires the user's explicit `/mindie-agent` entry in this task. New chats, forks, and subagents do not inherit activation. Contribution consent and project scope are additional capture conditions, never activation.

Remote and component tools remain available by default without `/mindie-agent`:

- `remote` MCP tools for remote file read/write, command execution, and background job management over SSH.
- `components` MCP tools: `mindie_status`, `diagnostics_status`, and `diagnostics_bundle` (included in the plugin runtime; local-only without uploading bundles), plus `coordinator`, `profiler`, and `sim` (which run the optional `mindie-coordinator`, `mindie-profiler`, and `mindie-sim` CLIs with an `argv` array when installed or configured in `component_commands`, returning `status`, `exit_code`, `stdout`, `stderr`, and parsed `result` only when `stdout` is JSON). `npu-top` is for human terminal use and is not an MCP tool.

## Retrieve for a missing decision

After the native `mindie_entry(op="init", request_nonce=...)` binding, choose
the next action in the current Kimi session; initialization does not require a
knowledge query.

- When the code and necessary callers explain the violated invariant and a
  concrete fix can be verified, proceed with zero knowledge calls. A filename
  or stack frame alone is not a root cause.
- Use the smallest relevant probe for missing environment or execution facts.
  Retrieve only a missing contract, mechanism or method that could change the
  action; never invent conditions to obtain a match.
- Reuse fully visible rules. Read an actual returned exact ref directly, without
  discovery, using `knowledge_explain(refs=[...], conditions=...,
  known_refs=[...], token_budget=..., request_nonce=...)`, including for one
  graded unit. Copy revision URIs exactly and supply a fresh nonce on each call.
- Pause reading once the next action and prerequisites are known. New evidence
  or failed verification can justify another read. Keep correctness, device and
  acceptance checks; no skip-feedback call or new task state is needed. The
  existing binding and authorized background capture remain in force.

## Finish the verified task

Identify the requested behavior and its acceptance checks from the task and
relevant callers. Once the change satisfies those invariants and the affected
behavior checks pass, give the final response with the result, validation and
any remaining limitation. Let the host emit its normal completion event.

Extend the implementation or repeat validation only for a concrete failing
case, a required acceptance check, or new evidence that challenges the result.
Do not add optional parameter combinations, speculative compatibility paths or
defensive fallbacks after success without that evidence. An offline parser or
code repair does not require an invented hardware condition or an unrelated
live NPU run. Keep any hardware checks the actual task requires.

Knowledge feedback and background capture must not prolong a finished task.
A passing check alone does not fabricate native completion or C6 admission;
report unfinished requirements honestly if a budget or external dependency
prevents completion.

1. **Bind & Initialize on Invocation (`request_nonce` on Every Call)**:
   - When `/mindie-agent` is invoked in the current native Kimi task, call `mindie_entry` **exactly once** with `op=init` (plus optional `domain="<domain>"` when explicitly activating a registered target domain for distillation/writes under its own consent and scope) and a fresh `request_nonce`. Never pass or fabricate a session ID (`${KIMI_SESSION_ID}` in text is not a capability).
   - Every subsequent `knowledge` (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`), `remote`, and `components` MCP call MUST also include a fresh `request_nonce`.
2. **Experience Loop After Manual Session Entry**:
   - In this explicitly enabled session, when the unified contribution choice and approved project scope allow capture, eligible `Stop` events run **automatically in the background**. Later turns retain the grant; reuse the saved contribution choice without asking again.
   - Inspect `first_use` and `reporting_activation` from the entry response. Preserve saved `disabled`, `later`, and `read-only` choices without prompting. If no choice exists, present the [contribution notice](references/user-notice.md) once and accept `contribute` or `disabled`. For a first conversational contribution choice, call `op=choose` only with the public `owner/repo` and GitHub account the user supplied in that reply.
   - If saved legacy `contribute` returns `needs_choice`, explain once that contribution now includes sanitized product-fault reporting. The supported migration entry is `/mindie-agent contribute owner/repo ACCOUNT`; its explicit arguments are the confirmation, so do not ask again. Never turn incomplete configuration or an old knowledge-only approval into a new opt-in. Reuse known destination values and ask only for missing ones.
3. **Graded Knowledge, Multi-Domain Routing, `knowledge_distill`, Long-Horizon Continuation & Profiler Discipline**:
   - Preserve your native Kimi CLI agent identity—do **not** adopt a separate persona or invent a second task state machine (`coordinator` / `remote-dev` already own execution state).
   - **Graded Knowledge, Multi-Domain Routing, Progressive Loading & `knowledge_distill`**: `knowledge_query` accepts `query`, optional `domain` (selecting the default domain or a registered `domain_configs` Engine/Feed such as `"vllm-ascend"` or `"operators"`), `limit` (1–20), `conditions`, `continuation`, `domain_path`, `token_budget` (1–16000), `include_unknown`, `include_archived`, and `request_nonce`. It returns bounded navigation `cards` plus deduplicated `foundation_cards` (and compatible `results[]`); archived or superseded units stay hidden unless `include_archived=true`. **Loading when needed**: use `cards` / `foundation_cards` only to discover a missing reference; an already returned exact reference needs no query. Read graded rules with `knowledge_explain(refs=[...], conditions=<observed facts>, known_refs=[<already fully visible id@revision>], token_budget=<1–16000>, request_nonce=...)`, including a single unit; reserve single `ref` reads for legacy task/block navigation; shared prerequisites expand once in `requires`, primary units return complete text rather than truncated steps, and `known_refs` means only exact revisions already complete in the current context. For graded units, cards/results return `id`, `ref` (`mindie://<domain>/units/<id>@<revision>`, the single-line `id@revision` identity where `<revision>` is a positive integer), `unit_ref` (without revision), and `feedback_ref` (equal to `ref` for graded units); copy the actual returned revision-bound `ref` into `refs` to read a graded closure (which also returns `feedback_ref`, distinct from legacy task/block navigation) and copy the actual returned `feedback_ref` to `knowledge_feedback` (for graded units `ref` and `feedback_ref` share the same revision URI value, whereas legacy material keeps `ref` and `feedback_ref` distinct; never pass a bare `id` or synthesize a ref; both route by the `mindie://<domain>/...` URI authority). **Four-stage usage feedback** records one exact `feedback_ref` with optional `event_type`: `discovered` (query delivery, also recorded as `delivered`), `loaded` (complete explain read), `applied` (the unit changed a decision or action), and `evaluated` (requires `run_id` plus `outcome="supported" | "refuted" | "inconclusive"`); `step_id` binds a stage to a step, `run_id` links the observation to a managed run, and legacy `rating="up" | "down"` is now an optional vote rather than a required field. Use `knowledge_distill(inputs, task_state, request_nonce, domain=..., related_refs=[...], proposal=...)` at `task_state: 'verified_milestone' | 'completed'` (the runtime wire token for the verified milestone is `"verified_milestone"`) on caller-selected, already-authorized evidence (`completed` or `verified_milestone` with `observed` or fault/environment/build evidence [`compile_failed` / `infra_failed` / `measurement_invalid`] is allowed and can yield `single_observation` or `hypothesis` without elevating unverified faults to `hardware_spec` or `reproduced_empirical`, while `reproduced_empirical` requires repeated `verified` evidence and `L4` requires a valid ablation receipt; optional `related_refs` names existing units for delta comparison against current knowledge, and optional `proposal` submits a `mindie-distill-decision/1` foreground decision for deterministic validation with `model_calls=0` instead of a new model call); only `task_state="in_flight"` (or all-`in_flight` / empty inputs) is a zero-call no-op, and a single-turn host `Stop` event is **not** task completion. Graded query options, multi-domain routing, and `knowledge_distill` require the paired `mindie-knowledge 0.13.0` runtime. Graded Git publication (`mindie-publication-contract/2`), independent Feed sync, and revision-bound unit feedback use that runtime; check the selected feed revision and installed runtime identity separately when diagnosing a mismatch.
   - **Three-Valued `conditions` (`matched` | `mismatched` | `condition_unknown`)**: Units separate functional `tier` (`L1–L4`) from `evidence_strength` (`hardware_spec`, `reproduced_empirical`, `single_observation`, `hypothesis`) and evaluate their declared condition keys. `condition_unknown` occurs when either the unit's declared `required` premise or the caller's `observed` fact is `"unknown"`, missing/not supplied, or has an incomparable format, and `unknown == unknown` never matches (an absent requirement or `"any"` is unconstrained `matched`, and historical `required: "unknown"` is never backfilled by later machine versions)—**never** treat `condition_unknown` as a match; verify whichever fact is missing/unknown (e.g., source `repository`/`revision` for an `L2` source SOP, or runtime environment for an NPU rule).
   - **`L1` + `L2` Foundation Baseline**: When knowledge is needed, query or explain with **observed** `conditions` (`soc`, `cann`, `driver`, `framework`, `repository`/`revision`, `dtype`, `input_shape` or workload case, etc.); never guess a condition to force a match. A matched `_common` `L1` hardware/runtime axiom or measurement gate is mounted automatically as part of the task foundation—either as a query result with `route: 'foundation_baseline'` or under a higher-scoring unit's `inherited_rules`—alongside the matched `L2` bottleneck/triage SOPs (`category: sop`). Treat every inherited rule as a condition-bound prerequisite, never let an `L2` SOP override an `L1` axiom or measurement gate, and never apply a unit whose premise is `condition_unknown`.
   - **Decision experience and revision**: L3 workflows contain conditional branches; choose actions from current observations. Hermes may revise any tier. At a finished finding use `task_state="verified_milestone"`, compare related units and preserve counterevidence, human corrections and superseded plans. Keep supported/refuted/inconclusive judgments scoped to actual evidence. See [Diagnostic Reasoning](references/diagnostic-reasoning.md) when selecting probes, revising a conclusion, or running progressive scale-up verification (pruned vs. full weights), same-topology baseline A/B parity, speculative graph/SLA sweeps, and parent/child PR delivery workflows.
   - **Exploration of an unresolved subproblem**: choose a low-cost experiment that distinguishes competing explanations. A first divergence is progress; repeated no-information attempts prompt review rather than a mandatory two-attempt escalation. Native independent context is usable only when available and authorized; removing L3 from an object or inheriting full history is not isolation. Only device-free work uses `npu_count=0`; device JIT, warmup and timing require the actual allocation and lease. L4 admission still needs a verified final-combination ablation receipt; diagnostic findings may return to L2/L3 without a speedup.
   - **Three-Axis Exploration (`Performance`, `Correctness/Invariants`, `Diagnostic Information Gain`)**: In `diagnose` / `explore` mode, state which axis an experiment is meant to move and the observable that distinguishes success. Never trade an unverified correctness invariant for a performance number; when no axis has a clear expected win, prefer the experiment with the largest diagnostic information gain (the one that most sharply tests an unverified assumption) over another near-identical performance run. These axes are independent of the `L1–L4` knowledge tier and of the `reuse` / `diagnose` / `explore` work mode.
   - **Launch-Time `in_flight_runs` Registration & Recovery-First-Query**: Immediately when `coordinator` or `remote-dev` starts a managed execution, atomically update `PROGRESS.md` (`os.replace`) to register its `run_id` (`run_id == execution_id`, never replaced) or `job_id`, `slot_id`, endpoint, and `Run Manifest` path under `in_flight_runs` before waiting or ending the turn. After any interruption, compaction, or resume, query `in_flight_runs` first and never blindly resubmit an active or uncertain run. Maintain `PROGRESS.md` and `experiment-ledger.jsonl` solely as fact references and condition-scoped judgments (`supported` / `refuted` / `inconclusive`), and treat `candidate_for_sequential_pruning` with `pruned_combination_verified=false` as **RETAIN**.
   - **Physical NPU Timing Mutual Exclusion & Profiler Discipline**: Only steps confirmed to need no device may use `npu_count=0` and parallel slot caches. Device JIT and warmup require the actual device lease; performance timing, attribution checks, and `mindie_profiler` runs on the same physical NPU (or same A3 dual-Die package) require an exclusive `coordinator` lease. `mindie_profiler` accepts explicit unlabeled `--time-window START:END`, labeled `--step-window STEP=START:END`, or `--full-range` (never inferring steps from kernel rows), uses explicit scene/clock annotations (`uncertain` / `clock_domain_unknown` otherwise), keeps single-kernel roofline bounds separate from unverified external `msmodeling` predictions, and never infers thermal throttling from a static `85°C` threshold.

## Progressive Context Disclosure (Read Only on Demand)

- **Installation, first-use GitHub authentication, enterprise-network token login or authentication repair**: read [GitHub Sign-In](references/github-authentication.md). Reuse a working login; otherwise guide browser login or token creation/input and verify credential reuse.
- **Normal installation and first-use setup, including when contribution is disabled**: present the existing [user notice](references/user-notice.md), then complete [Workspace Setup](references/workspace-setup.md). Offer creation/initialization, reuse or the current directory; approved creation/reuse includes native workspace registration. Reuse prior choices, and do not repeat this flow during ordinary tasks.

Do **not** load all reference files during ordinary troubleshooting. Read the specific reference file **only** when the current turn requires it:

- **Knowledge & Remote MCP tool usage (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `remote_*`), condition-matched knowledge and diagnostic decision guidance, graded response shapes, long-horizon ledgers, and physical NPU timing / profiler discipline**: read [Domain Tooling](references/domain-skills.md).
- **Preference changes, disabled/declined profile rules, background worker recovery, or hook lifecycle details**: read [Activation Lifecycle](references/activation-lifecycle.md).
- **Sharing scope, local redaction boundaries, or public Git history questions**: read [用户须知](references/user-notice.md).

