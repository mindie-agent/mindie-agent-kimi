#!/usr/bin/env python3
"""Stdlib trampoline into the configured interpreter."""
import os
import sys
from pathlib import Path

os.execv(
    sys.executable,
    [sys.executable, str(Path(__file__).with_name("with_runtime.py")), "mcp_server.py", "knowledge"],
)
