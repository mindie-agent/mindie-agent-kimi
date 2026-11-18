# Test Boundaries & Failure Detection

<a href="test-boundaries.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

`mindie-agent-kimi` `0.1.31` runs its thin-plugin suite in a strict fail-fast order so line-ending, contract, native-launch, or release-metadata regressions are caught immediately without masking OS-specific behavior.

---

## 1. CI Execution Order

Checks execute in this order within a single job:

1. **Line-Ending & Syntax Gates**: `python .github/scripts/check_lf_eol.py` rejects CRLF; `python -m compileall -q scripts tests` rejects syntax errors.
2. **Thin-Plugin Contract (`tests/test_thin_plugin_contract.py`)**: `scripts/` keeps exactly five files totaling ≤ 1,000 lines, version `0.1.31`, `REMOTE_DEV_HARNESS_PROFILE == "kimi"`, `entry.verify_bundled_seed()`, and a `KimiHostAdapter` satisfying `mindie_harness.api.HostAdapter`.
3. **Release Metadata (`scripts/setup.py release-metadata --source . --tests tests --revision <sha>`)**: `harness_name="kimi-code"` compatibility, AST `code_paths`, `test_ids`, and the `mindie-plugin-release/1` payload.
4. **Real Node.js MCP Launch (`tests/native`)**: `child_process.spawn` (`windowsHide: true`, `shell: false`) starts all three MCP servers with the exact `kimi.plugin.json` arguments and verifies `initialize` (`0.1.31`), `tools/list`, a JSON-RPC `-32700` parse error, and `ping` recovery.
5. **Full Contract Suite (`pytest -o "pythonpath=tests scripts" tests`)**: cold hook fast paths, project-root scope, MCP surface isolation, `setup.py` subcommands, installer resource verification, and `wire.jsonl` parsing.

---

## 2. Independent Failure Dimensions

| Verification Boundary | What the Assertion Observes | What May Be Replaced by Fixtures |
|---|---|---|
| **Thin-Plugin Size & Version** | The real `scripts/` file set, `wc -l` total, and `kimi.plugin.json` version | Nothing |
| **Seed Integrity** | Real `assets/mindie-harness-seed.pyz` SHA-256 and byte size | Nothing |
| **Cold Hook Scope** | Real `mindie_launch.py` child exit code, `{}` stdout, and zero SQLite | Authorized roots use temporary directories |
| **Manifest Launch** | Node.js actually spawns `python3 ./scripts/mindie_launch.py` with exact arguments | Current interpreter via `MINDIE_TEST_PYTHON_CMD` / PATH shim |
| **MCP Surface Isolation** | Exact `tools/list` sets and cross-surface `tools/call` `unknown_operation` | Nothing |
| **Release Metadata** | `mindie-plugin-release/1` payload and AST target existence | Fixed 40-hex `--revision` test value |
