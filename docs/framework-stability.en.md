# Framework Stability & Verification

<a href="framework-stability.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

This document describes persistent preference management, cross-platform atomic configuration replacement, and reproducible contract, test, and benchmark execution for `mindie-agent-kimi`.

---

## 1. Everyday Usage & Persistent Preferences

- **Unified Contribution Switch & Default-Enabled Knowledge**: Your contribution choice and approved project scope live in `mindie-consent.json` and persist across tasks, forks, restarts, updates, and transient failures without re-prompting. Community knowledge sharing and automatic tool fault reporting share this single persistent switch: explicitly selecting `contribute` enables scoped public knowledge sharing and starts the background fault-reporting worker, while `disabled` turns both off. Deterministic tools (`remote-dev` and factual `mindie_entry(op="status")`) and `knowledge_*` tools (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`) are available by default in every session without manual activation. Legacy knowledge-only `contribute` records lacking the unified consent revision (`choice_revision` / marked `needs_choice`) do not automatically enable fault uploads on upgrade until explicitly re-confirmed once.
- **Zero Overhead When Sharing Is Off or Out of Scope**: When contribution is disabled or the session workspace lies outside authorized `project_roots`, `Stop` exits immediately before opening SQLite and performs no transcript collection or organizer invocation, while `remote-dev` and `knowledge_*` tools remain available by default.
- **Three-State Separation & Corrupt Config Protection**: Saved local consent configuration, a running background worker, and an actually created external GitHub Issue or PR are tracked and reported separately. Corrupted configuration files are surfaced explicitly as faults and never trigger onboarding prompts.

---

## 2. Atomic Configuration Replacement

- **Standard `os.replace()` Atomic Writes**: Shared configuration and state updates write to a temporary file in the target directory and commit via Python's standard `os.replace()` atomic replacement (following `v0.0.2` Iron Rule 3, without custom Win32 ACL or `ctypes` rename wrappers). Concurrent readers always observe either the complete old document or the complete new document.
- **Contention Safety**: If an external process locks the target configuration file and prevents replacement, the write fails visibly while preserving the existing file—never resetting user settings or entering a retry loop.

---

## 3. Reproducible Contract, Testing & Benchmarks

From the repository root, with a Python interpreter linked against SQLite `>= 3.43.0` (FTS5 and `contentless_delete`) and `runtime-requirements.txt` installed, run (Node.js is required for the native MCP launch tests):

```sh
python3 .github/scripts/check_lf_eol.py
python3 -m pytest -q -o "pythonpath=tests scripts" tests
python3 -m unittest discover -s tests/native -v
python3 scripts/setup.py release-metadata --source . --tests tests --revision "$(git rev-parse HEAD)"
```

- **Thin-Plugin Gates**: `tests/test_thin_plugin_contract.py` verifies that `scripts/` keeps exactly five runtime files, totals ≤ 1,000 lines, reports plugin version `0.1.31`, verifies the bundled seed, and validates release metadata through `scripts/setup.py release-metadata`.
- **Release Benchmark (`benchmarks/release.json`)**: Defines the `public-transcript-parsing` microbenchmark (`public_transcript_parse`, `us/op`), executed via `benchmarks/release.py` to measure `wire.jsonl` public transcript extraction and non-public reasoning filtering latency, published alongside formal releases in `benchmark-results.json`, `benchmark-results.md`.
- **Explicit Acceptance Boundaries**: Automated CI and CPU microbenchmarks verify locked dependencies, OS file locks, stdio transport contracts, and local parser latency; live host sessions and standing Ascend A2/A3 NPU execution remain separate acceptance gates.
