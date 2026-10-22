"""Test-suite host isolation.

[Role]
Captures the real user home at import time and redirects every test case's
host-visible home, XDG, Kimi, and diagnostics environment into pytest's
`tmp_path` so no test can write a LaunchAgent, service, or diagnostic under the
developer's real profile.

[Invariants]
- `_REAL_HOME` is the only host home retained; `tests/support.py` uses it to
  resolve the pre-installed Gitleaks cache.
- Every test starts with isolated `HOME`, `USERPROFILE`, `XDG_CONFIG_HOME`,
  `XDG_DATA_HOME`, `KIMI_CODE_HOME`, and MindIE diagnostics paths.
- Failure recorders opened by in-process hook tests are closed on teardown.

[Timeouts & Locks]
- Pure environment setup/teardown; acquires no locks and starts no subprocess.

[Side-Effect Policy]
- Creates only directories under the pytest `tmp_path`; never writes the real
  home or user LaunchAgents.
"""

from __future__ import annotations

import os
from pathlib import Path
import site

import pytest

_REAL_HOME = Path.home()
_PYTHON_USER_BASE = os.environ.get("PYTHONUSERBASE") or site.getuserbase()
os.environ["MINDIE_TEST_REAL_HOME"] = str(_REAL_HOME)

_ISOLATED_PATHS = {
    "HOME": "home",
    "USERPROFILE": "home",
    "XDG_CONFIG_HOME": "config",
    "XDG_DATA_HOME": "data",
    "KIMI_CODE_HOME": "kimi-home",
    "MINDIE_DIAGNOSTICS_ROOT": "diag-root",
    "MINDIE_DIAGNOSTICS_CONFIG": "diagnostics.json",
}


@pytest.fixture(autouse=True)
def _isolate_host_environment(tmp_path, monkeypatch):
    for name, relative in _ISOLATED_PATHS.items():
        value = tmp_path / relative
        if name != "MINDIE_DIAGNOSTICS_CONFIG":
            value.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("PYTHONUSERBASE", _PYTHON_USER_BASE)
    yield
    _close_failure_recorders()


def _close_failure_recorders():
    try:
        import mindie_diagnostics.logging as diagnostic_logging
    except ImportError:
        return
    recorders = list(diagnostic_logging._FAILURE_RECORDERS.values())
    diagnostic_logging._FAILURE_RECORDERS.clear()
    for recorder in recorders:
        try:
            recorder.close()
        except Exception:
            pass
