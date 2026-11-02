# mindie-agent-kimi 0.1.13

`mindie-agent-kimi 0.1.13` introduces forward-compatible `2.x` Kimi Code version matching so host CLI upgrades stay eligible for plugin and component updates, and advances the active runtime and test contracts to the latest Ascend AI Infra component releases (`knowledge 0.13.10`, `remote-dev 0.11.4`, `diagnostics 0.5.5`, `debugger 0.1.0`, `profiler 0.5.6`, `sim 0.9.2`, and `coordinator 0.7.3`).

## What's New (Human-Readable Summary)

- **Forward-Compatible Kimi Code `2.x` Support**: Replaces single-point Kimi Code version locks (`2.1.1`, `2.2.0`, and `2.3.1`) in `harness-compatibility.json` with `>=2.1.1 <=2.999.999` (`include_prerelease: true`) so minor, patch, and prerelease Kimi Code upgrades continue to select compatible plugin Releases without falling into `no_compatible_release`.
- **Upgraded Active Runtime & Component Contracts**:
  - `runtime/active-overrides.txt` selects `mindie-knowledge 0.13.10` (`7936a4fda67f6808d8fa73824363a4d7345766e5`), `remote-dev 0.11.4` (`7c67876d716feb3949e4dbf33eb5e06980ff0d36`), `mindie-diagnostics 0.5.5` (`f2f2f946c172afc99342a84c0c1ed244f6d44e01`), and `mindie-sim 0.9.2` (`ced2dba5741af2d98f3a75b2a9cad77b9b94c40d`).
  - `test-requirements.txt` pins `mindie-debugger 0.1.0` (`e9406b2b178335190d5cb59dff4d6985894497ba`), `mindie-profiler 0.5.6` (`79d97e75bff7351b0472b93c03b072c1cf47f5e7`), `mindie-sim 0.9.2` (`ced2dba5741af2d98f3a75b2a9cad77b9b94c40d`), and `mindie-coordinator 0.7.3` (`79a8b28eef72d57f965a01c6660849f024fbb1c0`).
- **Clearer Remote CANN Log Triage**: Improves the `debugger` MCP tool guidance for remote container `plog` and ACLGraph diagnostics.
