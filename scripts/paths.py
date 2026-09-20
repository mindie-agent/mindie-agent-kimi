"""MindIE-owned paths for the Kimi adapter. Never writes ~/.kimi-code."""

from __future__ import annotations

import json
import os
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
IDENTITY = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z"
NONCE = r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}\Z"
PLUGIN_ID = "mindie-agent"
MCP_QUALIFIED_PREFIX = "mcp__plugin-mindie-agent_"


def config_path() -> Path:
    override = os.environ.get("MINDIE_KIMI_CONFIG")
    if override:
        return Path(override).expanduser().absolute()
    return (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
            / "mindie-agent" / "kimi.json").absolute()


def load_adapter_config() -> dict:
    path = config_path()
    if not path.is_file():
        raise FileNotFoundError("MindIE Kimi adapter configuration is missing")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("adapter configuration must be one JSON object")
    return data


def engine_config_path(config=None) -> Path:
    config = config if config is not None else load_adapter_config()
    value = config.get("engine_config")
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("adapter configuration lacks an absolute engine_config")
    return Path(value)


def load_engine_config(config=None) -> dict:
    path = engine_config_path(config)
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not {"root", "domain"} <= set(data):
        raise ValueError("engine configuration requires root and domain")
    if "session_activation" in data:
        raise ValueError("session_activation was removed; use admission_path")
    return data


def admission_path(engine=None) -> Path:
    engine = engine if engine is not None else load_engine_config()
    value = engine.get("admission_path")
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("engine configuration lacks an absolute admission_path")
    path = Path(value)
    if path.suffix.lower() in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
        raise ValueError("admission_path must be a SQLite file, not an adapter config")
    return path


def community_config_path(config=None) -> Path:
    config = config if config is not None else load_adapter_config()
    value = config.get("community_config")
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("adapter configuration lacks an absolute community_config")
    return Path(value)


def default_state_dir() -> Path:
    return (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "mindie-agent"
        / "state"
    )


def state_dir(config=None) -> Path:
    try:
        config = config if config is not None else load_adapter_config()
    except FileNotFoundError:
        return default_state_dir()
    value = config.get("state_dir")
    if isinstance(value, str) and os.path.isabs(value):
        return Path(value)
    engine = load_engine_config(config)
    return Path(engine["root"]) / "kimi-adapter"


def first_use_path() -> Path:
    """Stable first-use state. Lives under state_dir, NOT beside the
    (possibly generation-specific) adapter config, so it survives
    generation switches."""
    return state_dir() / "kimi.first-use.json"


def kimi_home_from_env() -> Path | None:
    value = os.environ.get("KIMI_CODE_HOME")
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser()


def plugin_root_from_env() -> Path:
    value = os.environ.get("KIMI_PLUGIN_ROOT")
    if isinstance(value, str) and value.strip():
        return Path(value)
    return PLUGIN_ROOT


def configured_python(config=None) -> str:
    config = config if config is not None else load_adapter_config()
    python = config.get("python")
    if not isinstance(python, str) or not python:
        raise ValueError("adapter configuration has no runtime interpreter")
    return python
