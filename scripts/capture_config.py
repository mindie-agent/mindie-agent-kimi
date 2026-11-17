"""[Role] `0.1.26 -> 0.1.27` transition shim for Kimi capture & domain-catalog wiring (`kimi_capture`).
[Invariants] Verifies absolute gitleaks redactor binary and declares `service_launch_context`.
[Timeouts & Locks] 90s bounded child execution via `mindie_harness.process` (`bounded.run`).
[Side-Effect Policy] Installs verified transcript redactor and prepares domain configs.
"""
from __future__ import annotations
from pathlib import Path
import sys

_H = Path(__file__).resolve().parent
if str(_H) not in sys.path:
    sys.path.insert(0, str(_H))
import kimi_capture as _impl  # noqa: E402

globals().update({k: v for k, v in _impl.__dict__.items() if k not in ("__name__", "__file__", "__spec__", "__loader__")})
sys.modules[__name__] = _impl
