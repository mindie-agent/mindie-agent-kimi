# mindie-agent-kimi 0.1.4

Pins remote-dev `1fefcc38` and diagnostics `0.5.3`. Large command/TaskOutput results use the core bounded head/tail and full refs; incremental pages remain cursor-lossless. Host packages retain `toolTimeoutMs=660000` and `REMOTE_DEV_HARNESS_PROFILE=kimi`, bound before discovery.

The immutable 0.1.2 controller's first hop has no `--overrides`, so it cannot resolve the 0.5.3 direct URL against knowledge's 801a01b transitive URL. That is a documented old-controller boundary, not seamless auto-update. Recovery is a one-time candidate `scripts/setup.py` (overrides) path; a private venv verified final diagnostics 0.5.3 and remote-dev `1fefcc38`. The 0.1.3 controller has overrides and follows a different path.

Candidate/private-feed checks do not claim formal GitHub distribution is complete.
