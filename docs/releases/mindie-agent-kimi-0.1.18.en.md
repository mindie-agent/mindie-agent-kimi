# mindie-agent-kimi 0.1.18

[中文](mindie-agent-kimi-0.1.18.md)

This maintenance release repairs an observed Windows race between ordinary configuration reads and settings migration.

- Settings migration reuses the pinned shared runtime's atomic replacement mechanism. Short readers can close their handles within its existing five-second budget. Persistent sharing conflicts and real permission errors still fail explicitly, preserving saved choices.
- Disabling contribution and migrating settings paths continue to serialize through the same community-settings write boundary. Migration preserves an already saved disabled state.
- The caller explicitly forwards its main-only environment key. The shared publisher verifies availability before long tests, validates all assets through the private draft ID, and creates the exact source tag only before complete immutable publication.
- Components continue to follow the latest formal releases while preserving installed version floors. This independent release does not alter the historical product combination or other repository versions.

Regression coverage includes a real Windows read-handle replacement conflict and concurrent disable/migration. Final platform CI, release benchmarks and old-client upgrade outcomes belong to the shipped reports. No new NPU or model-performance measurements are claimed.
