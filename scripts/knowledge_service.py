#!/usr/bin/env python3
"""Reuse shared core config, transcript loader, and service startup."""

from __future__ import annotations

import os
from pathlib import Path

from paths import engine_config_path, resolve_kimi_bin


def prepare_service_env(config=None):
    """Pin MINDIE_KIMI_BIN for service launches whose PATH is not the login
    shell's (a systemd --user unit has no ~/.local/bin; Task Scheduler
    differs). The engine declares environment: [MINDIE_KIMI_BIN], and only
    the current caller's value crosses the launch boundary. The adapter's
    stamped kimi_bin wins; otherwise resolve deterministically. An
    unresolvable binary stays the summary worker's honest configuration
    failure, never a guessed path. Never raises: launch failures stay
    visible downstream."""
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
