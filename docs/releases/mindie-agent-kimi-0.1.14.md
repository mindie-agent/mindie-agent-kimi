# mindie-agent-kimi 0.1.14

`mindie-agent-kimi 0.1.14` unifies the Recursive Self-Improvement (`rsi_loop` / `experiment_ledger`) state machine onto `mindie-knowledge 0.13.11`, raises default graded knowledge retrieval token budgets (`explore` `24000`, `query` `8000`, `explain` `16000`, ceiling `64000`), fixes stale replaced service owner retirement during `service replace`, and upgrades `mindie-sim` to `0.9.3`.

## What's New (Human-Readable Summary)

- **Single-Source RSI Loop & Experiment Ledger (`mindie-knowledge 0.13.11`)**: Exposes `rsi_loop` (`orient` / `propose` / `verdict`) and `experiment_ledger` in the knowledge MCP server backed by a single authoritative `ExperimentLedger`, surfacing `active_rsi_session` directly in `knowledge_explore`.
- **Higher Graded Retrieval Token Budgets**: Raises default `token_budget` to `24000` for `knowledge_explore`, `8000` for `knowledge_query`, and `16000` for `knowledge_explain` (ceiling `64000`).
- **Upgraded Active Runtime & Component Contracts**:
  - `runtime/active-overrides.txt` selects `mindie-knowledge 0.13.11` (`1392a5c326605225fc6d89334842ae7700c0dce8`), `remote-dev 0.11.4` (`7c67876d716feb3949e4dbf33eb5e06980ff0d36`), `mindie-diagnostics 0.5.5` (`f2f2f946c172afc99342a84c0c1ed244f6d44e01`), and `mindie-sim 0.9.3` (`dbadff04b60937a0722b0a227cdad01beed083cd`).
  - `test-requirements.txt` pins `mindie-debugger 0.1.0` (`e9406b2b178335190d5cb59dff4d6985894497ba`), `mindie-profiler 0.5.6` (`79d97e75bff7351b0472b93c03b072c1cf47f5e7`), `mindie-sim 0.9.3` (`dbadff04b60937a0722b0a227cdad01beed083cd`), and `mindie-coordinator 0.7.3` (`79a8b28eef72d57f965a01c6660849f024fbb1c0`).
