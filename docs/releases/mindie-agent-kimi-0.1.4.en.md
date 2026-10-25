# mindie-agent-kimi 0.1.4

Pins remote-dev `1fefcc38`; diagnostics uses a `0.5.2` bootstrap floor, while component_prepare selects and preserves the installed `0.5.3` for activation. Large command/TaskOutput results use the core bounded head/tail and full refs; incremental pages remain cursor-lossless. Host packages retain `toolTimeoutMs=660000` and `REMOTE_DEV_HARNESS_PROFILE=kimi`, bound before discovery.

The immutable 0.1.2 controller's first hop has no `--overrides`, so the release uses a resolvable 0.5.2 bootstrap floor; its real component_prepare reads the current runtime inventory and selects the installed 0.5.3/new knowledge, so the prepared generation is not downgraded. A private fixture using actual 0.1.2 build_runtime + actual component_prepare verified 0.5.3/1c465a18 is preserved, knowledge advances, and core is `1fefcc38`. Full host activation/loaded-tool readback is not yet claimed. The 0.1.3 controller has overrides.

Candidate/private-feed checks do not claim formal GitHub distribution is complete.
