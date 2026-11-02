# mindie-agent-kimi 0.1.13

This release replaces the single-point Kimi Code version locks (`2.1.1`, `2.2.0`,
and `2.3.1`) in `harness-compatibility.json` with a forward-compatible `2.x`
SemVer range (`>=2.1.1 <=2.999.999`, `include_prerelease: true`) so minor,
patch, and prerelease Kimi Code upgrades continue to select compatible plugin
Releases without falling into `no_compatible_release`, and clarifies the remote
container CANN `plog` triage workflow in the `debugger` MCP tool description.

## Runtime and verification

`runtime/active-overrides.txt` selects `mindie-knowledge 0.13.8`
(`9a4325858e6fadb10715275826a7795843cbbdf4`), `remote-dev 0.11.2`
(`85be999811771e03ecc7d78e5e560fac1636e7ef`), and `mindie-diagnostics 0.5.4`
(`5ae643e19223f2181ecdbaa3edaf464137c4805e`). Optional test component pins in
`test-requirements.txt` remain `mindie-profiler 0.5.5`
(`7cdd56b6cc9e0e7f603368393497487dd2dbfe39`), `mindie-sim 0.9.0`
(`f5608b4943f515c401d2f9b12fffb579a1e2571b`), and `mindie-coordinator 0.7.1`
(`d146c55c414bfb92136cf5fb7fa11ae14c673ef0`).
