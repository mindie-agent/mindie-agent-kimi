# Organizer Failure Classification & Process Groups

<a href="organizer-diagnostics.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

When community experience sharing is enabled, `mindie-agent-kimi` invokes an isolated Kimi CLI summary worker to index redacted public material blocks (titles and summaries). This document describes the static classification, outcome receipts and process-group lifecycle guarantees for the worker.

---

## 1. Summary-Worker Protocol & Static Error Categories

- **Shared Wire Protocol**: the shared Core first invokes the worker with `--identity` for a fingerprinted policy identity (model, implementation hashes, limits), then sends one request on stdin and expects exactly one strict outcome envelope (`mindie-summary-outcome/1`) on stdout. Exit zero means the envelope was delivered, not that indexing succeeded; failures and uncertain paid outcomes are returned as explicit envelopes, never as an empty success.
- **Isolated Profile**: the worker runs inside a temporary `KIMI_CODE_HOME` with no plugins, skills, hooks, or MCP servers loaded, and cleans up its temporary directory upon completion. Provider and model come from the user's selected native config; a missing model or host is a configuration failure, never a default or an empty success.
- **Static Categories**: missing configuration, invalid requests, native execution failures, schema-invalid output, output-limit overflows and the adapter's own bounded deadline (300s) map to static shared categories and reason codes; the Core no longer imposes a total worker timeout.
- **Zero Secret Leakage & Billing Honesty**: Kimi's prompt mode reports no usage counters, so one started model call is accounted as `model_calls=1`, `usage_known=false` (billing status unknown), never as zero. The shared Core records only the static failure category, reason code and elapsed time—never copying raw provider stderr, prompts, or environment variables into persisted diagnostics. Failed or uncertain paid requests are never automatically replayed, and no fallback summary is ever substituted.

---

## 2. Unified Process-Group Cleanup

- **Shared Process-Group Ownership**: the worker script and its spawned native Kimi child process remain in the Core-owned process group so that normal completion, timeout, or cancellation reliably closes the entire process tree without leaving orphan processes behind.
- **Verification Scope**: unit tests cover the identity fingerprint, missing profile, missing host, invalid wire input, policy drift, completed-but-unusable answers and a full round trip (stub binary); macOS host acceptance covers the native K3 call, cancellation and zero lingering child processes.
