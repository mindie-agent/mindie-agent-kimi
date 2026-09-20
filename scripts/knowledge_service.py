#!/usr/bin/env python3
"""Start the shared knowledge Engine with Kimi admission and transcript parser.

Core CLI still has a Codex-flavored MCP identity path. This adapter owns
native dispatch and loads `transcript_adapter` plus `admission_path`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from paths import load_engine_config, engine_config_path, configured_python, config_path


def load_transcript(path):
    location = Path(path)
    if not location.is_file():
        raise ValueError("transcript_adapter is not a readable module path")
    spec = importlib.util.spec_from_file_location("mindie_kimi_transcript", location)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ValueError("transcript adapter could not be loaded")
    sys.modules["mindie_kimi_transcript"] = module
    spec.loader.exec_module(module)
    for name in ("FileIdentity", "identify", "read_material"):
        if not hasattr(module, name):
            raise ValueError(f"transcript adapter missing {name}")
    return module


def connection_path(config):
    return Path(config["root"]) / config["domain"] / "connection.json"


def serve(engine_file):
    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop.engine import Engine
    from mindie_knowledge.loop.feed import Feed
    from mindie_knowledge.loop.store import Store
    from mindie_knowledge.loop.transport import Service

    config = json.loads(Path(engine_file).read_text())
    store = Store(config["root"], config["domain"])
    admission = Admission(config["admission_path"]) if config.get("admission_path") else None
    parser = None
    if config.get("transcript_adapter"):
        parser = load_transcript(config["transcript_adapter"])
    engine = Engine(
        store,
        agent_command=config.get("agent_command"),
        settings_path=config.get("community_config"),
        admission=admission,
        transcript_adapter=parser,
    )
    feeds = [Feed(store, item) for item in config.get("feeds", [])]
    Service(
        engine,
        connection_path=connection_path(config),
        admission=admission,
        feeds=feeds,
    ).serve()


def existing_service(engine_file=None):
    """Probe a live connection. Never spawn (Stop must not orphan a child)."""
    from mindie_knowledge.loop.cli import connect, rpc

    engine_file = str(Path(engine_file or engine_config_path()).resolve())
    config = json.loads(Path(engine_file).read_text())
    connection = connect(config)
    rpc(connection, "status", timeout=0.4)
    return connection


def ensure_service(engine_file=None):
    from mindie_knowledge.loop.cli import connect, rpc
    from mindie_knowledge.loop.locks import StartInProgress, StartLock
    from mindie_knowledge.loop.process import terminate_tree

    engine_file = str(Path(engine_file or engine_config_path()).resolve())
    config = json.loads(Path(engine_file).read_text())
    deadline = time.monotonic() + 5.0

    def probe():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("knowledge startup deadline exceeded")
        connection = connect(config)
        rpc(connection, "status", timeout=min(0.5, remaining))
        return connection

    try:
        return probe()
    except (OSError, ValueError):
        pass
    lock = StartLock(connection_path(config).with_name("start.lock"))
    acquired = False
    process = None
    ready = False
    try:
        try:
            lock.acquire()
            acquired = True
        except StartInProgress:
            pass
        if acquired:
            try:
                return probe()
            except (OSError, ValueError):
                pass
            spawn = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.name != "nt":
                spawn["start_new_session"] = True
            process = subprocess.Popen(
                [
                    configured_python(),
                    str(HERE / "knowledge_service.py"),
                    "serve",
                    "--config",
                    engine_file,
                ],
                **spawn,
            )
        for _ in range(3):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))
            if process is not None and process.poll() is not None:
                raise RuntimeError("knowledge service exited during startup; no retry")
            try:
                connection = probe()
                ready = True
                return connection
            except (OSError, ValueError):
                pass
        raise RuntimeError("knowledge service unavailable after bounded readiness probes")
    finally:
        if process is not None and not ready:
            terminate_tree(process)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        if acquired:
            lock.release()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["serve"])
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    serve(args.config)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"mindie-kimi: {exc}", file=sys.stderr)
        raise SystemExit(2)
