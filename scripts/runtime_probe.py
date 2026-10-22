#!/usr/bin/env python3
"""Pinned runtime probe templates for `scripts/setup.py`.

[Role]
Owns the inline Python/FTS5 probe source used to verify that a bootstrap or
candidate interpreter can import the pinned core modules, load the transcript
adapter, and use SQLite FTS5 `contentless_delete=1`. `setup.py` re-exports the
templates and helpers for existing callers and tests.

[Invariants]
- `probe_script(parser)` always appends the FTS5 probe and the `OK`/`MISSING`
  tail, and never interpolates unquoted parser paths.
- The module list is the exact set required by the adapter runtime contract.

[Timeouts & Locks]
- Pure string construction only; the caller bounds subprocess execution (15s
  in `setup.probe_runtime`).

[Side-Effect Policy]
- No file, network, or process writes; the generated probe uses an in-memory
  SQLite database only.
"""

from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

PROBE_MODULES = (
    "mindie_knowledge.loop.cli",
    "mindie_knowledge.loop.activation",
    "mindie_knowledge.loop.engine",
    "remote_dev.mcp.tools",
)
_PROBE_TEMPLATE = """
import importlib
import os
import sys
missing = []
if sys.version_info < ((3, 12) if os.name == "nt" else (3, 11)):
    missing.append("Python 3.12+ on Windows or 3.11+ on POSIX is required; prefer Python 3.13")
for name in {modules!r}:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f"{{name}} ({{type(exc).__name__}}: {{exc}})")
if not missing:
    from mindie_knowledge.loop.engine import Engine
    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop import cli
    from mindie_knowledge.loop import locks
    from mindie_knowledge.loop import settings as _settings
    if not callable(getattr(Engine, "stop_if_idle", None)):
        missing.append("core engine lacks the stop_if_idle API")
    if not callable(getattr(cli, "load_transcript_adapter", None)):
        missing.append("core cli lacks load_transcript_adapter")
    if not callable(getattr(locks, "lock_held", None)):
        missing.append("core locks lacks lock_held")
    if not callable(getattr(_settings, "CommunityWriteContext", None)):
        missing.append("core settings lack the CommunityWriteContext API")
    for method in ("activate", "deactivate", "active_lease", "claim", "inspect", "check"):
        if not callable(getattr(Admission, method, None)):
            missing.append("core Admission lacks " + method)
if not missing:
    try:
        module = cli.load_transcript_adapter({{"transcript_adapter": {parser!r}}})
        if module is None:
            missing.append("transcript adapter did not load")
    except Exception as exc:
        missing.append(f"transcript adapter load failed ({{type(exc).__name__}}: {{exc}})")
"""

_FTS_PROBE = r"""
import sqlite3
_fts = None
try:
    _fts = sqlite3.connect(":memory:")
    _fts.execute("CREATE VIRTUAL TABLE probe USING fts5(body, content='', contentless_delete=1)")
    _fts.execute("INSERT INTO probe(rowid, body) VALUES (1, 'alpha')")
    if _fts.execute("SELECT rowid FROM probe WHERE probe MATCH 'alpha'").fetchall() != [(1,)]:
        raise RuntimeError("insert MATCH failed")
    _fts.execute("UPDATE probe SET body='beta' WHERE rowid=1")
    if _fts.execute("SELECT rowid FROM probe WHERE probe MATCH 'beta'").fetchall() != [(1,)]:
        raise RuntimeError("update MATCH failed")
    if _fts.execute("SELECT rowid FROM probe WHERE probe MATCH 'alpha'").fetchall():
        raise RuntimeError("stale MATCH survived update")
    _fts.execute("DELETE FROM probe WHERE rowid=1")
    if _fts.execute("SELECT rowid FROM probe WHERE probe MATCH 'beta'").fetchall():
        raise RuntimeError("delete MATCH failed")
except Exception as exc:
    missing.insert(0, (
        "sqlite " + sqlite3.sqlite_version
        + " lacks FTS5 contentless_delete=1 (SQLite >=3.43.0): "
        + type(exc).__name__ + ": " + str(exc)[:160]
    ))
finally:
    if _fts is not None:
        _fts.close()
"""

_PROBE_TAIL = """
print("MISSING: " + "; ".join(missing) if missing else "OK")
"""


def probe_script(parser: str) -> str:
    return _PROBE_TEMPLATE.format(modules=list(PROBE_MODULES), parser=parser) + _FTS_PROBE + _PROBE_TAIL


PROBE_SCRIPT = probe_script(str((PLUGIN_ROOT / "scripts" / "transcript.py").resolve()))
