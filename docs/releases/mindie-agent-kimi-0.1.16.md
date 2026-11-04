# mindie-agent-kimi 0.1.16

`mindie-agent-kimi 0.1.16` enforces Windows zero-console-popup guardrails across all background subprocess paths (`subprocess.CREATE_NO_WINDOW`) and upgrades shared runtime components to `mindie-knowledge 0.13.12`, `mindie-diagnostics 0.5.6`, `remote-dev 0.11.5`, and `mindie-coordinator 0.7.4`.

## What's New (Human-Readable Summary)

- **Windows Zero-Console-Popup Subprocess Guardrails**: Enforces `creationflags=subprocess.CREATE_NO_WINDOW` across all background Python subprocesses (`scripts/with_runtime.py`, `scripts/bridge.py`, `scripts/setup.py`, `scripts/component_prepare.py`, `scripts/runtime_update.py`, `scripts/mcp_server.py`) so auto-update checks and component checks never flash a transient Windows `conhost.exe` window.
- **Upgraded Active Runtime & Component Contracts**:
  - `runtime/active-overrides.txt` selects `mindie-knowledge 0.13.12` (`1840bd4e238e718676768489058917818d18cd3c`), `remote-dev 0.11.5` (`131b24533479e9cefc572144adccdcb345f70846`), `mindie-diagnostics 0.5.6` (`9b331956b00737dd6d8c956da3416316f2de1ddf`), and `mindie-sim 0.9.3` (`dbadff04b60937a0722b0a227cdad01beed083cd`).
  - `test-requirements.txt` pins `mindie-debugger 0.1.0` (`e9406b2b178335190d5cb59dff4d6985894497ba`), `mindie-profiler 0.5.6` (`79d97e75bff7351b0472b93c03b072c1cf47f5e7`), `mindie-sim 0.9.3` (`dbadff04b60937a0722b0a227cdad01beed083cd`), and `mindie-coordinator 0.7.4` (`14d7cec7247bdcbea9b9878db4770e3a05a6ea41`).
- **Unified `setup.py sharing` & Native Step 5 Onboarding Popup**: Adds `setup.py sharing --status|--enable|--disable` with fast stdlib status inspection, personal fork resolution/creation, incremental `GIT_CONFIG_COUNT` enterprise network compatibility, and native `AskUserQuestion` onboarding popup.
