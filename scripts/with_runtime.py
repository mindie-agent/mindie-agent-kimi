#!/usr/bin/env python3
"""[Role] Stdlib interpreter trampoline dispatching plugin scripts to the configured runtime Python.
[Invariants] Falls back to sys.executable when unconfigured so offline entry/status still work; strips PYTHONPATH before os.execv.
[Timeouts & Locks] Zero locks, zero waits; replaces current process image immediately via os.execv.
[Side-Effect Policy] Mutates process environment (removes PYTHONPATH) and execs target script.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _config_path() -> Path:
    if override := os.environ.get("MINDIE_KIMI_CONFIG"):
        return Path(override).expanduser()
    root = Path(base) if (base := os.environ.get("XDG_CONFIG_HOME")) else Path.home() / ".config"
    return root / "mindie-agent" / "kimi.json"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("with_runtime.py <script> [args...]")
    script = Path(argv[0])
    if not script.is_absolute():
        script = Path(__file__).resolve().parent / script
    path = _config_path()
    python = sys.executable
    if path.is_file():
        try:
            configured = json.loads(path.read_text(encoding="utf-8")).get("python")
            if isinstance(configured, str) and configured:
                python = configured
        except TimeoutError:
            raise
        except (OSError, ValueError, TypeError):
            pass
    os.environ.pop("PYTHONPATH", None)
    os.execv(python, [python, str(script), *argv[1:]])


if __name__ == "__main__":
    main()
