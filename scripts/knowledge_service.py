#!/usr/bin/env python3
"""[Role] Adapter helper for preparing service launch environment, loading transcript adapter, and starting knowledge service.
[Invariants] Pins MINDIE_KIMI_BIN and MINDIE_GIT_BIN deterministically from stamped adapter config or environment without guessing paths.
[Timeouts & Locks] existing_service status probe bounded at 2.0s; ensure_service delegates to shared core startup lock.
[Side-Effect Policy] Sets MINDIE_KIMI_BIN / MINDIE_GIT_BIN in os.environ and spawns local knowledge service when ensure_service() is called.
"""

from __future__ import annotations

import os
from pathlib import Path

from paths import engine_config_path, load_adapter_config, resolve_git_bin, resolve_kimi_bin


def prepare_service_env(config=None):
    from diagnostic_support import bind_environment

    bind_environment()
    try:
        if config is None:
            try:
                config = load_adapter_config()
            except Exception:
                config = {}
        value = config.get("kimi_bin")
        if not (isinstance(value, str) and value):
            value = resolve_kimi_bin(config)
    except Exception:
        value = None
    if value:
        os.environ["MINDIE_KIMI_BIN"] = value
    try:
        git = os.environ.get("MINDIE_GIT_BIN")
        if not git:
            git = config.get("git_bin") if isinstance(config, dict) else None
            if not (isinstance(git, str) and git):
                git = resolve_git_bin()
    except Exception:
        git = None
    if isinstance(git, str) and git:
        os.environ["MINDIE_GIT_BIN"] = git


def engine_config(engine_file=None):
    from mindie_knowledge.loop.cli import config_at

    return config_at(str(Path(engine_file or engine_config_path()).resolve()))


def load_transcript(engine_file=None):
    from mindie_knowledge.loop.cli import load_transcript_adapter

    return load_transcript_adapter(engine_config(engine_file))


def existing_service(engine_file=None):
    from mindie_knowledge.loop.cli import connect, rpc

    connection = connect(engine_config(engine_file))
    rpc(connection, "status", timeout=2.0)
    return connection


def ensure_service(engine_file=None):
    from mindie_knowledge.loop.cli import ensure_service as core_ensure

    path = str(Path(engine_file or engine_config_path()).resolve())
    prepare_service_env()
    return core_ensure(path)
