# Domain Tooling, Graded Knowledge & NPU Execution Discipline

The `remote`, `components`, and `knowledge` MCP surfaces work out of the box in every Kimi session, subagent, and automation without session activation or a required `request_nonce`. Poll or stop returned `job_id` / `execution_id` handles directly.

## 1. Graded Knowledge (`L1–L4`), Progressive Loading & `knowledge_distill`

- **`knowledge_query` (`graded-bm25`)**:
  - Accepts `query`, optional `domain` (default domain or a registered `domain_configs` route such as `"vllm-ascend"` or `"operators"`), `limit` (`1..20`), `conditions`, `continuation`, `domain_path`, `token_budget` (`1..64000`, default `8000` in standard/reuse mode and `24000` in `mode="explore"`), `include_unknown` (default `false`), and `include_archived` (default `false`).
  - Returns bounded navigation `cards[]`, deduplicated `foundation_cards[]` (matched `L1` premises), compatible `results[]`, and `usage`.
  - Each item includes `id` (`unit-...`), revision-bound `ref` (`mindie://<domain>/units/<id>@<revision>`, where `<revision>` is a positive integer), `unit_ref` (`mindie://<domain>/units/<id>`), `feedback_ref` (equal to `ref` for graded units), `revision`, `match_status` (`matched` | `mismatched` | `condition_unknown`), `missing_conditions`, `tier`, `category`, `evidence_strength`, `domain_path`, `conditions`, `provenance`, `excerpt`, and `inherited_rules`.
- **`knowledge_explain` & `knowledge_feedback`**:
  - Pass revision-bound `ref` URIs in `knowledge_explain(refs=[...], conditions=..., known_refs=[...], token_budget=16000)` (`1..64000`, default `16000` in reuse/diagnose and `24000` in `mode="explore"`; single `ref` reads support legacy task/block navigation). Shared prerequisites expand once in `requires`, requested bodies return untruncated in `units`, and `known_refs` lists only exact `id@revision` revisions currently visible in active context. All `mindie://<domain>/...` URIs in a batch must share the same domain authority.
  - Pass the exact `feedback_ref` to `knowledge_feedback(ref=..., ...)` to record four-stage usage events (`discovered` | `loaded` | `applied` | `evaluated` with `run_id`, optional `step_id`, and `outcome="supported"|"refuted"|"inconclusive"`) and/or an optional `rating="up"|"down"`. Record `id` (`unit_id`) and integer `revision` when citing units.
- **Three-Valued `conditions` (`matched` | `mismatched` | `condition_unknown`)**:
  - Units separate functional `tier` (`L1` axioms & probes, `L2` SOPs, `L3` convergence workflows, `L4` exploration mutators) from `evidence_strength` (`hardware_spec` | `reproduced_empirical` | `single_observation` | `hypothesis`). When a unit's required premise or the caller's observed fact is missing or `"unknown"`, status is `condition_unknown`; probe the missing fact before applying the unit.
- **`knowledge_distill`**:
  - `knowledge_distill(inputs=..., task_state=..., domain=..., related_refs=[...], proposal=...)` distills authorized evidence (`inputs` items with `kind`, `outcome`, `source_locator`, `content`, `evidence_refs`, optional `repetitions >= 1`) at `task_state="verified_milestone"` or `"completed"`.
  - `hardware_spec` requires a `hardware_spec` source; `reproduced_empirical` requires `outcome == "verified"` with `repetitions >= 2`; `L4` requires verified leave-one-out ablation evidence on the target workload. `observed` or fault/build evidence (`compile_failed`, `infra_failed`, `measurement_invalid`) distills into `single_observation` or `hypothesis`.
  - Pass `related_refs` for delta comparison (`revise`/`merge`/`supersede`), or pass a `mindie-distill-decision/1` `proposal` for deterministic validation and application (`model_calls=0`).

## 2. Handle-Addressed Continuity, Exclusive Physical NPU Lease & `mindie_profiler`

- **Authoritative Handle State (`execution_id` & `job_id`)**:
  - `coordinator` (`execution_id`) and `remote-dev` (`job_id`) persist run and background-job state in the profile-shared state root so both the parent session and subagents can inspect, tail, or cancel in-flight work.
  - On recovery or after context compaction, query `coordinator` status or `remote_job_status` / `remote_TaskOutput` on existing handles before launching duplicate runs.
- **Pre-Compile Parallelism, Exclusive Physical NPU Lease & `mindie_profiler`**:
  - Confirmed device-free compilation and log inspection may run concurrently using isolated `MINDIE_SLOT_ID` caches.
  - Any benchmark or profiling run measuring latency, TPOT, TTFT, MFU, or bandwidth must hold an exclusive `coordinator` lease or an equivalent existing verified host reservation on the target physical NPU cards (including dual-Die `Phy-ID` sibling isolation on Ascend A3 910C).
  - Pass explicit `--time-window START:END`, `--step-window STEP=START:END` / `--steps-file`, or `--full-range` (`--unit us|ns|ms`) and explicit scene/`--clock-domain` annotations to `mindie_profiler`. Keep `ideal_hardware_lower_bound_ms`, `model_predicted_ms`, and `measured_device_ms` distinct, and rely on hardware throttle signal bits rather than a static `85°C` threshold.
