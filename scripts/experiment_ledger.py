#!/usr/bin/env python3
"""[Role] Kimi forwarder to shared `mindie_harness.rsi.experiment_ledger`.

[Invariants] Rebinds `sys.modules[__name__]` to `mindie_harness.rsi.experiment_ledger`
so linked-worktree-safe ledger operations use the single shared implementation.
[Timeouts & Locks] Delegated to `mindie_harness.rsi.experiment_ledger`.
[Side-Effect Policy] Delegated to `mindie_harness.rsi.experiment_ledger`.
"""
from __future__ import annotations

import sys
from mindie_harness.rsi import experiment_ledger as _shared

if __name__ == "__main__":
    raise SystemExit(_shared.main())

_alias = __name__
globals().update(
    {k: v for k, v in _shared.__dict__.items() if k not in ("__name__", "__file__", "__spec__", "__loader__")}
)
sys.modules[_alias] = _shared
