#!/usr/bin/env python3
"""Reuse shared core config, transcript loader, and service startup."""

from __future__ import annotations

from pathlib import Path

from paths import engine_config_path


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
    return core_ensure(path)
