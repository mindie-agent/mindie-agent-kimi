#!/usr/bin/env python3
"""Stdlib-only trampoline: exec the configured interpreter before MindIE imports.

Plugin hooks and MCP stdio must not import mindie_knowledge or remote_dev
under system python3. Windows uses this file as the command with python args.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _config_path() -> Path:
    override = os.environ.get("MINDIE_KIMI_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "mindie-agent" / "kimi.json"


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("with_runtime.py <script> [args...]")
    here = Path(__file__).resolve().parent
    script = Path(argv[0])
    if not script.is_absolute():
        script = here / script
    rest = argv[1:]
    path = _config_path()
    try:
        python = json.loads(path.read_text())["python"]
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise SystemExit(f"MindIE configured interpreter unavailable: {exc}"[:400])
    if not isinstance(python, str) or not python:
        raise SystemExit("adapter configuration has no runtime interpreter")
    os.execv(python, [python, str(script), *rest])


if __name__ == "__main__":
    main()
