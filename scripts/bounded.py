"""[Role] `0.1.26 -> 0.1.27` transition shim delegating to `kimi_bounded` (`mindie_harness.process`).
[Invariants] Zero `CREATE_NEW_CONSOLE`/`DETACHED_PROCESS`; enforces `CREATE_NO_WINDOW` (`0x08000000`).
[Timeouts & Locks] Delegated to `mindie_harness.process.run_owned` and `Deadline`.
[Side-Effect Policy] Spawns and reaps owned child process trees only when `run` is called.
"""
from __future__ import annotations
import os, sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_H = Path(__file__).resolve().parent
_GEN = _H.parents[1] / "generations" / _H.name / "scripts" if len(_H.parents) >= 2 and _H.parent.name == "launch" else _H
for _c in (_H, _GEN):
    if (_c / "kimi_bounded.py").is_file():
        if sys.path[:1] != [str(_c)]:
            sys.path.insert(0, str(_c))
        break
else:
    raise ModuleNotFoundError("No module named 'kimi_bounded'")
import kimi_bounded as _impl  # noqa: E402
globals().update({k: v for k, v in _impl.__dict__.items() if k not in ("__name__", "__file__", "__spec__", "__loader__")})
sys.modules[__name__] = _impl
