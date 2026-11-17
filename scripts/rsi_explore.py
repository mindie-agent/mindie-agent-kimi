"""[Role] Kimi forwarder to shared `mindie_harness.rsi.rsi_explore`.

[Invariants] `mode` is `reuse`, `diagnose`, or `explore`; malformed arguments
fail before any lease or knowledge RPC call.
[Timeouts & Locks] Pure in-memory validation (`< 0.1ms`); zero locks or I/O.
[Side-Effect Policy] Read-only argument normalization via `mindie_harness.rsi`.
"""
from __future__ import annotations

from mindie_harness.rsi.rsi_explore import (
    MODES,
    OPTIONAL_KEYS,
    forwarded,
    parse_args,
    schema_properties,
)

__all__ = ["MODES", "OPTIONAL_KEYS", "forwarded", "parse_args", "schema_properties"]
