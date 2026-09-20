#!/usr/bin/env python3
"""Stdlib trampoline: configured interpreter, or this python if unconfigured.

Unconfigured entry/status must still run offline. Sharing stays off and no
service/model is started by this launcher.
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
    python = sys.executable
    if path.is_file():
        try:
            configured = json.loads(path.read_text()).get("python")
        except (OSError, ValueError, TypeError):
            configured = None
        if isinstance(configured, str) and configured:
            python = configured
    os.execv(python, [python, str(script), *rest])


if __name__ == "__main__":
    main()
