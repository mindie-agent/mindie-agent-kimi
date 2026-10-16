# Framework Stability & Verification

<a href="framework-stability.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

This document describes the everyday usage guarantees, Windows atomic configuration I/O, and reproducible preflight and test suite execution for `mindie-agent-kimi`.

---

## 1. Everyday Usage & Persistent Preferences

- **Set Once, Persist Everywhere**: Your community contribution choice, approved project scope, and independent fault-reporting preference persist across tasks, forks, restarts, updates, and transient failures. Later sessions require no renewal or manual recovery steps.
- **No Overhead When Sharing Is Off**: With community contribution disabled, the plugin performs no `Stop` transcript collection or organizer invocation, while domain knowledge search and `remote-dev` remote Ascend tools remain available on demand.
- **Corrupt Config Protection**: Temporary service or network issues recover automatically via persisted worker state; corrupted configuration files are reported explicitly as faults and never trigger onboarding prompts.

---

## 2. Atomic Configuration Reads & Writes on Windows

- **Delete-Sharing + Native Atomic Rename**: The shared configuration store combines delete-sharing reads with native Windows atomic rename semantics ([`FILE_RENAME_INFORMATION`](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information)). Concurrent readers always observe either the complete old document or the complete new document.
- **Contention Safety**: If an external program holds a file open without delete sharing, the write fails visibly while preserving the saved document—never triggering onboarding or a retry loop.

---

## 3. Reproducible Preflight & Component Testing

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
- **Explicit Acceptance Boundaries**: Automated CI verifies locked dependencies, real OS file locks, and stdio transport contracts; live host sessions and remote Ascend NPU execution remain separate acceptance gates.
