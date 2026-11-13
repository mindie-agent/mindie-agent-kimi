# mindie-agent-kimi 0.1.21

Component-only update checks (`updater_check.py`) now isolate `component_update`
deferrals, lock contention, and catalog/build failures instead of letting
exceptions escape to the outer `updater` entrypoint (`stage="entry"`).

`component_update.py` filters retired `mindie-bench` entries from older runtime
inventories and profiles during component discovery and classifies remote
release catalog HTTP, transport, and invalid-JSON responses into typed
`CatalogError` diagnostics so transient network failures enter normal retry
backoff without producing false internal fault reports.

Active component overrides advance to `mindie-knowledge 0.13.15` and
`mindie-diagnostics 0.5.10`, and diagnostic maintenance reserves explicit
startup and SQLite settlement grace inside the tail check budget.
