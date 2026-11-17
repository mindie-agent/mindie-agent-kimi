"""[Role] `0.1.26 -> 0.1.27` transition shim for Kimi remote deadline arithmetic (`mindie_harness.process.Deadline`).
[Invariants] `outer_timeout >= inner_wait + 60s` grace, minimum `180s`.
[Timeouts & Locks] Pure arithmetic; zero I/O or locks.
[Side-Effect Policy] Purely functional.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

_H = Path(__file__).resolve().parent
for _c in (_H, _H.parents[1] / "generations" / _H.name / "scripts" if len(_H.parents) >= 2 else _H):
    if (_c / "kimi_launch.py").is_file():
        if sys.path[:1] != [str(_c)]:
            sys.path.insert(0, str(_c))
        break
else:
    _p = _H.parents[1] / "current.json" if len(_H.parents) >= 2 else Path()
    if _p.is_file() and (_g := json.loads(_p.read_text(encoding="utf-8")).get("generation")):
        sys.path.insert(0, str(Path(_g) / "scripts"))
from kimi_launch import (  # noqa: E402,F401
    KIMI_REMOTE_INNER_SECONDS,
    REMOTE_GRACE_SECONDS,
    REMOTE_MIN_BUDGET_SECONDS,
    kimi_remote_inner_seconds,
    remote_budget,
)
