"""Thin-plugin contract: five scripts, one manifest version, one verified seed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

KEEP = {"mindie_launch.py", "entry.py", "kimi_transcript.py", "transcript.py", "install_kimi_plugin.py", "setup.py"}
TOTAL_LINE_CEILING = 1500


def _scripts(repo: Path) -> list[Path]:
    return sorted(path for path in (repo / "scripts").glob("*.py"))


def test_only_five_runtime_modules_exist(repo_root: Path) -> None:
    assert {path.name for path in _scripts(repo_root)} == KEEP


def test_total_runtime_lines_within_ceiling(repo_root: Path) -> None:
    total = sum(len(path.read_text(encoding="utf-8").splitlines()) for path in _scripts(repo_root))
    assert total <= TOTAL_LINE_CEILING, f"scripts/*.py holds {total} lines"


def test_manifest_version_and_remote_profile(repo_root: Path) -> None:
    manifest = json.loads((repo_root / "kimi.plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.1.32"
    assert manifest["mcpServers"]["remote"]["env"]["REMOTE_DEV_HARNESS_PROFILE"] == "kimi"
    for surface in ("knowledge", "remote", "components"):
        server = manifest["mcpServers"][surface]
        assert server["command"] == "python3"
        assert server["args"] == ["./scripts/mindie_launch.py", "mcp", surface]
        assert server["cwd"] == "./"


def test_bundled_seed_and_host_adapter(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(repo_root / "scripts"))
    import entry
    import mindie_harness.api as api

    lock = entry.verify_bundled_seed()
    assert lock["schema"] == "mindie-harness-seed/1"
    assert len(lock["sha256"]) == 64
    adapter = entry.KimiHostAdapter()
    assert adapter.host == "kimi"
    assert isinstance(adapter, api.HostAdapter)


def test_release_metadata_succeeds(repo_root: Path, plugin_env: dict[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, str(repo_root / "scripts/setup.py"), "release-metadata",
         "--source", str(repo_root), "--tests", str(repo_root / "tests"), "--revision", "a" * 40],
        cwd=repo_root, env=plugin_env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == "mindie-plugin-release/1"
    assert payload["version"] == "0.1.32"
    assert payload["source"] == {"repository": "mindie-agent/mindie-agent-kimi", "sha": "a" * 40}


def test_skill_frontmatter_is_model_invocable(repo_root: Path) -> None:
    text = (repo_root / "skills/mindie-agent/SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    assert 'name: "mindie-agent"' in frontmatter
    assert 'description: "' in frontmatter
    assert "disable-model-invocation" not in text
    assert "disableModelInvocation" not in text
    assert "/mindie-agent" not in text and "$mindie-agent" not in text
