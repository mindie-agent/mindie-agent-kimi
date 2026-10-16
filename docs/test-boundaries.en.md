# Test Boundaries & Failure Detection

<a href="test-boundaries.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

`mindie-agent-kimi` runs its cross-platform test suite in a strict fail-fast order so dependency mismatches or native launch regressions are caught immediately without masking OS-specific behavior.

---

## 1. CI Execution Order

Checks execute in the following order within a single job:
1. **Commit Pin & Peer Preflight (`tests/preflight.py`)**: Verifies installed runtime commit pins and the explicitly supplied peer repository checkout (exiting immediately with code `2` if missing).
2. **Installed Runtime Probe**: Runs the unmodified production probe in a clean child interpreter, paired with a negative control that removes a consumed method and verifies rejection.
3. **Real Node.js MCP Launch & Descendant Ownership (`tests/native`)**: Uses Node.js (`shell: false`) to execute the generated MCP manifest and complete real `initialize` and `tools/list` exchanges; on Windows, also verifies descendant cleanup on normal exit and timeout via a watchdog.
4. **Stdio Transport & Backpressure Cases**: Exercises frontend cancellation, `EOF`, unread large responses, and complete large responses on both POSIX and Windows.
5. **Full Component Suite**: Covers consent persistence, project scope, task identity, publication compatibility, update rollback, and diagnostics.

---

## 2. Independent Failure Dimensions

| Verification Boundary | What the Assertion Observes | What May Be Replaced by Fixtures |
|---|---|---|
| **Dependency Compatibility** | Unmodified installed package and consumed API signatures | Missing API only in the rejecting negative control |
| **Manifest Launch** | Node.js actually spawns the selected executable with exact arguments | Argument-echo child for quoting check; a second test runs the real MCP modules |
| **Process Ownership** | Real descendant processes are dead after the leader exits | Nothing in launch, Windows Job assignment, or liveness checks |
| **Output Backpressure** | Complete large response delivery, or bounded exit when the reader closes | The remote tool's synthetic payload |
| **Concurrent Configuration** | All fields survive real OS kernel file-lock contention | Observation wrapper to synchronize contention timing only |
| **Update Rollback** | Pointer/package restoration and cleanup of incomplete generations | Network download/build and native registry HTTP stub in unit tests |
