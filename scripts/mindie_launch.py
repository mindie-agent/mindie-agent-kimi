#!/usr/bin/env python3
"""[Role] `0.1.26 -> 0.1.27` transition launcher shim delegating to `kimi_launch` (`mindie_harness`).
[Invariants] Resolves strictly from own generation (`_H` or `generations/<sha>/scripts`), never `PYTHONPATH` or `current.json`.
[Timeouts & Locks] Shared `operation.lock` across hook/MCP dispatch.
[Side-Effect Policy] Launches `bridge.py`, `mcp_server.py`, or `updater.py` in the committed generation.
"""
from __future__ import annotations
import os, sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_SELF = Path(__file__).resolve()
_H = _SELF.parent
_GEN = _H.parents[1] / "generations" / _H.name / "scripts" if len(_H.parents) >= 2 and _H.parent.name == "launch" else _H
for _c in (_H, _GEN):
    if (_c / "kimi_launch.py").is_file() and (_c / "diagnostic_support.py").is_file():
        if sys.path[:1] != [str(_c)]:
            sys.path.insert(0, str(_c))
        break
else:
    raise ModuleNotFoundError("No module named 'diagnostic_support'")
import kimi_launch as _impl  # noqa: E402
_impl.diagnostic_support._LAUNCH_SHIM_DIR = str(_H if (_H / "diagnostic-build.json").is_file() else (_c if (_c / "diagnostic-build.json").is_file() else _H))
sys.modules.setdefault("mindie_launch", _impl)
if __name__ == "__main__":
    raise SystemExit(_impl.main(launcher_file=_SELF))
globals().update({k: v for k, v in _impl.__dict__.items() if k not in ("__name__", "__file__", "__spec__", "__loader__")})
sys.modules[__name__] = _impl
