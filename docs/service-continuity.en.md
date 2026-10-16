# Service Continuity Across Plugin Updates

<a href="service-continuity.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

This document explains how `mindie-agent-kimi` maintains background knowledge service continuity, configuration fingerprint consistency, and reliable `Stop` event delivery across plugin updates and rollbacks.

---

## 1. Bounded Service Stop & Restoration During Updates

- **Deterministic Stop and Restore**: Under the exclusive operation lock, the update transaction verifies that the exact old loopback service endpoint has exited, switches the native plugin package and `current.json` pointer, and performs one bounded restoration attempt (single spawn, 5-second budget, 3 readiness probes) using the new generation's interpreter and engine config.
- **No Unsolicited Activation**: Only a service that was actively running and stopped by this updater run is restored. If no service was running before the update (or if task leases were revoked), it stays off—the updater never activates tasks, replays transcripts, or calls a model.

---

## 2. Durable `Stop` Handoff & Rollback Safety

- **Durable `Stop` Queueing**: When an active task with contribution enabled finishes, `Stop` durably records its capture reference first and requests a single coalesced service wake if the worker is absent, preventing lost increments around update windows.
- **Explicit Rollback & Handoff Status**: On rollback, the previous service is restored only after both the previous native package and pointer are confirmed restored. Any interrupted handoff remains visible in updater status as `service_handoff` and is never hidden by subsequent feed syncs.
