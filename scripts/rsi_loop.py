#!/usr/bin/env python3
"""[Role] Kimi forwarder to shared `mindie_harness.rsi.rsi_loop`.

[Invariants] Rebinds `sys.modules[__name__]` to `mindie_harness.rsi.rsi_loop`
so state transitions and monkeypatches operate on the single shared authority.
[Timeouts & Locks] Delegated to `mindie_harness.rsi.rsi_loop`.
[Side-Effect Policy] Delegated to `mindie_harness.rsi.rsi_loop`.
"""
from __future__ import annotations

import sys
from mindie_harness.rsi import rsi_loop as _shared

if __name__ == "__main__":
    raise SystemExit(_shared.main())

_alias = __name__
globals().update(
    {k: v for k, v in _shared.__dict__.items() if k not in ("__name__", "__file__", "__spec__", "__loader__")}
)
sys.modules[_alias] = _shared
