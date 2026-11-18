"""Autouse isolation for every MindIE Agent Kimi contract test."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest

ENV_VARS = (
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
    "XDG_DATA_HOME", "XDG_STATE_HOME", "MINDIE_CONFIG_HOME", "MINDIE_DATA_HOME",
    "MINDIE_KIMI_CONFIG", "MINDIE_AGENT_CONFIG", "MINDIE_DIAGNOSTICS_ROOT",
    "MINDIE_DIAGNOSTICS_CONFIG", "KIMI_CODE_HOME", "REMOTE_DEV_AUTH_DIR",
    "UV_CACHE_DIR", "TMPDIR",
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every host/config/data/diagnostics/temp location at one temp root."""
    root = tmp_path / "home"
    root.mkdir()
    temp = tmp_path / "tmp"
    temp.mkdir()
    values = {
        "HOME": root, "USERPROFILE": root,
        "APPDATA": root / "AppData/Roaming", "LOCALAPPDATA": root / "AppData/Local",
        "XDG_CONFIG_HOME": root / ".config", "XDG_DATA_HOME": root / ".local/share",
        "XDG_STATE_HOME": root / ".local/state",
        "MINDIE_CONFIG_HOME": root / ".config/mindie-agent",
        "MINDIE_DATA_HOME": root / ".local/share/mindie-agent",
        "MINDIE_KIMI_CONFIG": root / ".config/mindie-agent/kimi.json",
        "MINDIE_AGENT_CONFIG": root / ".config/mindie-agent/kimi.json",
        "MINDIE_DIAGNOSTICS_ROOT": root / ".local/share/mindie-agent/diagnostics",
        "MINDIE_DIAGNOSTICS_CONFIG": root / ".config/mindie-agent/diagnostics.json",
        "KIMI_CODE_HOME": root / ".kimi-code",
        "REMOTE_DEV_AUTH_DIR": root / ".remote-dev",
        "UV_CACHE_DIR": temp / "uv-cache", "TMPDIR": temp,
    }
    for name in ENV_VARS:
        monkeypatch.setenv(name, str(values.get(name, temp)))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    for name in ("MINDIE_KIMI_BIN", "MINDIE_REDACTOR_EXECUTABLE"):
        monkeypatch.delenv(name, raising=False)
    return root


@pytest.fixture()
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture()
def plugin_env(isolated_home: Path) -> dict[str, str]:
    """Environment for CLI subprocesses under the isolated home (keeps PYTHONPATH)."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    return env
