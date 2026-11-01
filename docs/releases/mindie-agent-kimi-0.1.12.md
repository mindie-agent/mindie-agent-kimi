# mindie-agent-kimi 0.1.12

This maintenance release synchronizes the optional component test pins and the
native `sim` and `profiler` MCP tool descriptions with `mindie-sim 0.9.0` and
`mindie-profiler 0.5.5`, streamlines plugin skill and tool prompt wording, and
includes enterprise-network transport hardening and Windows/LF install fixes.

## Runtime and verification

`runtime/active-overrides.txt` selects `mindie-knowledge 0.13.8`
(`9a4325858e6fadb10715275826a7795843cbbdf4`), `remote-dev 0.11.2`
(`85be999811771e03ecc7d78e5e560fac1636e7ef`), and `mindie-diagnostics 0.5.4`
(`5ae643e19223f2181ecdbaa3edaf464137c4805e`). Optional test component pins in
`test-requirements.txt` are updated to `mindie-profiler 0.5.5`
(`7cdd56b6cc9e0e7f603368393497487dd2dbfe39`), `mindie-sim 0.9.0`
(`f5608b4943f515c401d2f9b12fffb579a1e2571b`), and `mindie-coordinator 0.7.1`
(`d146c55c414bfb92136cf5fb7fa11ae14c673ef0`), while `scripts/component_tools.py`
exposes `mindie-sim 0.9.0` and `mindie-profiler 0.5.5` capabilities (`compose`,
`--model`, `--hardware`, `--concurrency`, `--async-scheduling`, `predict`,
`efficiency`, `sla`, `sweep --mode pd`, `skew`, `camodel`, and `kernels`).
