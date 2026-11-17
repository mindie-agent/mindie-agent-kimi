"""[Role] `0.1.26 -> 0.1.27` transition shim for Kimi `wire.jsonl` transcript adapter (`kimi_transcript`).
[Invariants] Preserves `load_transcript_adapter` (`identify`, `extract`, `scan`) and fork timestamp boundaries.
[Timeouts & Locks] Bounded scan bytes and 2.0s deadline; zero locks.
[Side-Effect Policy] Read-only over `wire.jsonl` and `state.json`.
"""
from __future__ import annotations
from pathlib import Path
import sys

_H = Path(__file__).resolve().parent
if str(_H) not in sys.path:
    sys.path.insert(0, str(_H))
import kimi_transcript as _impl  # noqa: E402

globals().update({k: v for k, v in _impl.__dict__.items() if k not in ("__name__", "__file__", "__spec__", "__loader__")})
sys.modules[__name__] = _impl
