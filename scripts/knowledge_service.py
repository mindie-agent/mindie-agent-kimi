#!/usr/bin/env python3
"""Reuse shared core config, transcript loader, and service startup."""

from __future__ import annotations

import os
from pathlib import Path

from paths import engine_config_path, resolve_git_bin, resolve_kimi_bin


def prepare_service_env(config=None):
    """Pin MINDIE_KIMI_BIN and MINDIE_GIT_BIN for service launches whose
    PATH is not the login shell's (a systemd --user unit has no
    ~/.local/bin; Task Scheduler differs). The engine declares both names
    in service_launch_context.environment, and only the current caller's
    values cross the launch boundary. Each adapter stamp wins for its
    name; otherwise resolve deterministically, with an explicit
    MINDIE_GIT_BIN/MINDIE_KIMI_GIT_BIN override taking precedence.
    Unresolvable tools stay unset: the component and the summary worker
    keep their own honest configuration failures, never a guessed path.
    Never raises: launch failures stay visible downstream."""
    from paths import load_adapter_config

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

    path = str(Path(engine_file or engine_config_path()).resolve())
    return config_at(path)


def load_transcript(engine_file=None):
    from mindie_knowledge.loop.cli import load_transcript_adapter

    return load_transcript_adapter(engine_config(engine_file))


def existing_service(engine_file=None):
    """Probe a live connection. Never spawn."""
    from mindie_knowledge.loop.cli import connect, rpc

    config = engine_config(engine_file)
    connection = connect(config)
    rpc(connection, "status", timeout=0.4)
    return connection


def ensure_service(engine_file=None):
    from mindie_knowledge.loop.cli import ensure_service as core_ensure

    path = str(Path(engine_file or engine_config_path()).resolve())
    prepare_service_env()
    return core_ensure(path)
