"""[Role] Canonical path and binary resolution authority for the Kimi adapter.
[Invariants] Profile authority paths anchor at profile_config_path() (never per-generation forks); never mutates ~/.kimi.
[Invariants] domain_configs comes from the selected native generation; profile consent remains shared. Values stay absolute engine-config paths for core selection.
[Timeouts & Locks] Pure filesystem metadata and JSON config reads; zero locks or subprocesses.
[Side-Effect Policy] Strictly read-only path and configuration resolution.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
IDENTITY = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z"
NONCE = r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}\Z"
PLUGIN_ID = "mindie-agent"
MCP_QUALIFIED_PREFIX = "mcp__plugin-mindie-agent_"
GIT_WINDOWS_LOCATIONS = (
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
)


def _default_config_path() -> Path:
    return (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mindie-agent" / "kimi.json").absolute()


def config_path() -> Path:
    if override := os.environ.get("MINDIE_KIMI_CONFIG"):
        return Path(override).expanduser().absolute()
    return _default_config_path()


def _read_config_quiet(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def profile_config_path() -> Path:
    path = config_path()
    data = _read_config_quiet(path) if path.is_file() else None
    if data is not None:
        profile = data.get("profile_config")
        if isinstance(profile, str) and os.path.isabs(profile):
            return Path(profile)
        try:
            generations = state_dir(data) / "update" / "generations"
            path.relative_to(generations)
            return _default_config_path()
        except (OSError, ValueError, KeyError, FileNotFoundError):
            pass
    return path


_DOMAIN_REF = re.compile(r"mindie://(?P<domain>[a-z][a-z0-9-]{0,63})/")


def domain_from_ref(ref):
    """Return the canonical authority domain of a mindie reference, if any."""
    match = _DOMAIN_REF.match(ref) if isinstance(ref, str) else None
    return match.group("domain") if match else None


def domain_configs(config=None) -> dict | None:
    """Trusted domain map of the generation selected by the native launcher."""
    if config is None:
        config = load_adapter_config()
    value = config.get("domain_configs")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("adapter configuration domain_configs must be a mapping")
    return value


def load_adapter_config() -> dict:
    path = config_path()
    if not path.is_file():
        raise FileNotFoundError("MindIE Kimi adapter configuration is missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("adapter configuration must be one JSON object")
    return data


def engine_config_path(config=None) -> Path:
    config = config if config is not None else load_adapter_config()
    value = config.get("engine_config")
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("adapter configuration lacks an absolute engine_config")
    return Path(value)


def load_engine_config_at(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not {"root", "domain"} <= set(data):
        raise ValueError("engine configuration requires root and domain")
    if "session_activation" in data:
        raise ValueError("session_activation was removed; use admission_path")
    return data


def load_engine_config(config=None) -> dict:
    return load_engine_config_at(engine_config_path(config))


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
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mindie-agent" / "state"


def state_dir(config=None) -> Path:
    try:
        config = config if config is not None else load_adapter_config()
    except FileNotFoundError:
        return default_state_dir()
    value = config.get("state_dir")
    if isinstance(value, str) and os.path.isabs(value):
        return Path(value)
    return Path(load_engine_config(config)["root"]) / "kimi-adapter"


def first_use_path() -> Path:
    return state_dir() / "kimi.first-use.json"


def kimi_home_from_env() -> Path | None:
    value = os.environ.get("KIMI_CODE_HOME")
    return Path(value).expanduser() if isinstance(value, str) and value.strip() else None


def plugin_root_from_env() -> Path:
    value = os.environ.get("KIMI_PLUGIN_ROOT")
    return Path(value) if isinstance(value, str) and value.strip() else PLUGIN_ROOT


def configured_python(config=None) -> str:
    config = config if config is not None else load_adapter_config()
    python = config.get("python")
    if not isinstance(python, str) or not python:
        raise ValueError("adapter configuration has no runtime interpreter")
    return python


def resolve_kimi_bin(config=None):
    if explicit := os.environ.get("MINDIE_KIMI_BIN"):
        return explicit
    config = config if config is not None else load_adapter_config()
    home = config.get("kimi_home") or os.environ.get("KIMI_CODE_HOME")
    if isinstance(home, str) and home:
        candidate = Path(home) / "bin" / ("kimi.exe" if os.name == "nt" else "kimi")
        if candidate.is_file():
            return str(candidate)
    return shutil.which("kimi")


def resolve_git_bin():
    if explicit := os.environ.get("MINDIE_KIMI_GIT_BIN"):
        return explicit
    if found := shutil.which("git"):
        return found
    if os.name == "nt":
        for candidate in GIT_WINDOWS_LOCATIONS:
            if Path(candidate).is_file():
                return candidate
    return None
