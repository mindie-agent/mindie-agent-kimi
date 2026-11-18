#!/usr/bin/env python3
"""[Role] Legacy pre-0.1.31 `transcript.py` import shim forwarding to `kimi_transcript`.
[Invariants] Preserves `updater_staging.probe_runtime` (`cli.load_transcript_adapter`) and legacy `kimi.engine.json` `transcript_adapter` lookups during `v0.1.30 -> v0.1.32` self-upgrade.
[Timeouts & Locks] Pure module re-export; zero locks, subprocesses, or network I/O.
[Side-Effect Policy] Strictly read-only.
"""
from __future__ import annotations

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from kimi_transcript import *  # noqa: E402,F401,F403
