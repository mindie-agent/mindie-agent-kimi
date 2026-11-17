"""[Role] `0.1.26 -> 0.1.27` transition shim delegating to `kimi_paths` (`mindie_harness`).
[Invariants] Resolves strictly from own generation (`_H` or `generations/<sha>/scripts`).
[Timeouts & Locks] Pure path/config resolution; zero locks or subprocesses.
[Side-Effect Policy] Read-only.
"""
from __future__ import annotations
import sys
from pathlib import Path

_H = Path(__file__).resolve().parent
_GEN = _H.parents[1] / "generations" / _H.name / "scripts" if len(_H.parents) >= 2 and _H.parent.name == "launch" else _H
for _c in (_H, _GEN):
    if (_c / "kimi_paths.py").is_file():
        if sys.path[:1] != [str(_c)]:
            sys.path.insert(0, str(_c))
        break
else:
    raise ModuleNotFoundError("No module named 'kimi_paths'")
import kimi_paths as _impl  # noqa: E402
globals().update({k: v for k, v in _impl.__dict__.items() if k not in ("__name__", "__file__", "__spec__", "__loader__")})
sys.modules[__name__] = _impl
