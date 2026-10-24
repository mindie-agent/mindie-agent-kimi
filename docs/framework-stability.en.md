# Framework Stability & Verification

<a href="framework-stability.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

This document describes persistent preference management, cross-platform atomic configuration replacement, and reproducible preflight, test, and benchmark execution for `mindie-agent-kimi`.

---

## 1. Everyday Usage & Persistent Preferences

- **Unified Contribution Switch & Per-Session Knowledge Entry**: Your contribution choice and approved project scope live in `mindie-consent.json` and persist across tasks, forks, restarts, updates, and transient failures without re-prompting. Community knowledge sharing and automatic tool fault reporting share this single persistent switch: explicitly selecting `contribute` enables scoped public knowledge sharing and starts the background fault-reporting worker, while `disabled` turns both off. While deterministic tools (`remote-dev` and factual `mindie_entry(op="status")`) are available by default in every session, the knowledge flywheel (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`, and `Stop` transcript capture) is enabled per session by explicitly invoking `/mindie-agent` once. Legacy knowledge-only `contribute` records lacking the unified consent revision (`choice_revision` / marked `needs_choice`) do not automatically enable fault uploads on upgrade until explicitly re-confirmed once.
- **No Overhead When Inactive or Sharing Is Off**: In sessions where `/mindie-agent` has not been invoked—or when contribution is disabled—the plugin performs no `Stop` transcript collection or organizer invocation, while `remote-dev` remote Ascend tools remain available by default and domain knowledge search activates on demand via `/mindie-agent`.
- **Three-State Separation & Corrupt Config Protection**: Saved local consent configuration, a running background worker, and an actually created external GitHub Issue or PR are tracked and reported separately. Corrupted configuration files are surfaced explicitly as faults and never trigger onboarding prompts.

---

## 2. Atomic Configuration Replacement

- **Standard `os.replace()` Atomic Writes**: Shared configuration and state updates write to a temporary file in the target directory and commit via Python's standard `os.replace()` atomic replacement (following `v0.0.2` Iron Rule 3, without custom Win32 ACL or `ctypes` rename wrappers). Concurrent readers always observe either the complete old document or the complete new document.
- **Contention Safety**: If an external process locks the target configuration file and prevents replacement, the write fails visibly while preserving the existing file—never resetting user settings or entering a retry loop.

---

## 3. Reproducible Preflight, Testing & Benchmarks

From the repository root, using a Python interpreter linked against SQLite `>= 3.43.0` (with FTS5 and `contentless_delete` enabled) and `runtime-requirements.txt` installed, run:

```sh
export MINDIE_PEER_CC_REPO=/path/to/mindie-agent-cc
# Node.js is required for the real MCP launch contract tests
python3 tests/preflight.py
python3 -m unittest discover -s tests -p test_runtime_contract.py
python3 -m unittest discover -s tests/native -v
python3 -m unittest discover -s tests
```

- **Preflight Gate (`tests/preflight.py`)**: Verifies that the peer repository commit and installed runtime dependency pins match before starting the test suite; missing dependencies fail explicitly rather than skipping.
- **Release Benchmark (`benchmarks/release.json`)**: Defines the `public-transcript-parsing` microbenchmark (`public_transcript_parse`, `us/op`), executed via `benchmarks/release.py` to measure `wire.jsonl` public transcript extraction and non-public reasoning filtering latency, published alongside formal releases in `benchmark-results.json`, `benchmark-results.md`.
- **Explicit Acceptance Boundaries**: Automated CI and CPU microbenchmarks verify locked dependencies, OS file locks, stdio transport contracts, and local parser latency; live host sessions and standing Ascend A2/A3 NPU execution remain separate acceptance gates.
