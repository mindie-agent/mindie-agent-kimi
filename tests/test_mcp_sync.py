"""Explicit MCP feed sync executes the real CLI against a local Git publication."""
import json
from pathlib import Path
import subprocess
import sys
import uuid
from unittest.mock import patch

import pytest
from support import SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))
import admission
import identity
import mcp_server


@pytest.fixture
def local_feed(tmp_path, monkeypatch):
    from mindie_knowledge.publication_contract import make_contract, render_contract

    config = make_config(tmp_path)
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(config))
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "publication-contract.json").write_text(
        render_contract(make_contract("vllm-ascend", "a" * 40)), encoding="utf-8",
    )
    for arguments in (
        ["init", "-q", "-b", "main"],
        ["add", "."],
        ["-c", "user.name=feed", "-c", "user.email=feed@example.invalid",
         "-c", "commit.gpgsign=false", "commit", "-qm", "local publication"],
    ):
        subprocess.run(["git", "-C", str(remote), *arguments],
                       check=True, capture_output=True, timeout=5)
    adapter = json.loads(config.read_text())
    engine_path = Path(adapter["engine_config"])
    engine = json.loads(engine_path.read_text())
    engine.pop("community_config")
    engine["feeds"] = [{
        "repository": "local/knowledge", "domain": "vllm-ascend", "ref": "main",
        "url": str(remote),
    }]
    engine_path.write_text(json.dumps(engine))
    return config, engine_path


def sync():
    args = {"request_nonce": uuid.uuid4().hex}
    identity.publish_nonce_bind(
        "session-sync", args["request_nonce"],
        "mcp__plugin-mindie-agent_knowledge__knowledge_sync", uuid.uuid4().hex, args,
    )
    return mcp_server.handle("knowledge", {
        "id": 1, "method": "tools/call",
        "params": {"name": "knowledge_sync", "arguments": args},
    })["result"]


def test_discovered_sync_uses_real_local_feed_without_a_service(local_feed, tmp_path):
    import mindie_launch
    import knowledge_sync

    request = json.dumps({"params": {"name": "knowledge_sync", "arguments": {}}}).encode()
    child = mindie_launch._call_budget("knowledge", request)
    native = json.loads((SCRIPTS.parent / "kimi.plugin.json").read_text())["mcpServers"]["knowledge"]
    assert child >= knowledge_sync.TIMEOUT + 5 + 5 + 5 + 2.2
    assert native["toolTimeoutMs"] / 1000 >= child + mindie_launch.CALL_LOCK_BUDGET + mindie_launch.OUTPUT_BUDGET
    listed = mcp_server.handle("knowledge", {"id": 1, "method": "tools/list"})
    schema = next(tool["inputSchema"] for tool in listed["result"]["tools"]
                  if tool["name"] == "knowledge_sync")
    assert schema["required"] == ["request_nonce"]
    admission.activate("session-sync", project_root=str(tmp_path))
    with patch("knowledge_service.ensure_service") as ensure, \
            patch("knowledge_service.existing_service") as existing:
        result = sync()
        ensure.assert_not_called()
        existing.assert_not_called()
    assert not result["isError"], result
    payload = result["structuredContent"]
    assert payload["exit_code"] == 0
    assert payload["feeds"][0]["status"] == "synced"
    assert payload["feeds"][0]["entries"] == 0
    repeated = sync()
    assert not repeated["isError"], repeated
    assert repeated["structuredContent"]["feeds"][0]["status"] == "unchanged"


def test_sync_requires_current_manual_lease_before_cli_effects(local_feed, tmp_path):
    _, engine_path = local_feed
    root = Path(json.loads(engine_path.read_text())["root"])
    admission.activate("parent-session", project_root=str(tmp_path))
    with patch("knowledge_sync.execute") as execute:
        result = sync()
        execute.assert_not_called()
    assert result["isError"]
    assert "/mindie-agent" in result["content"][0]["text"]
    assert not (root / "vllm-ascend").exists()


def test_failed_feed_is_tool_error_even_when_cli_exits_zero(local_feed, tmp_path):
    _, engine_path = local_feed
    engine = json.loads(engine_path.read_text())
    engine["feeds"][0]["url"] = str(tmp_path / "absent-remote")
    engine_path.write_text(json.dumps(engine))
    admission.activate("session-sync", project_root=str(tmp_path))
    result = sync()
    assert result["isError"], result
    payload = result["structuredContent"]
    assert payload["exit_code"] == 0
    assert payload["feeds"][0]["status"] == "unavailable"
    assert payload["stdout"]


@pytest.mark.parametrize("late_failure", ["cleanup_status", "receipt_status"])
def test_real_committed_feed_with_late_failure_stays_degraded(local_feed, monkeypatch, late_failure):
    from mindie_knowledge.loop.feed import Feed
    from mindie_knowledge.loop.store import Store
    from updater_check import fold_feed_results

    _, engine_path = local_feed
    engine = json.loads(engine_path.read_text())
    store = Store(engine["root"], engine["domain"])
    feed = Feed(store, engine["feeds"][0])
    try:
        if late_failure == "cleanup_status":
            def cleanup_failed():
                raise OSError("local staging cleanup failed")

            monkeypatch.setattr(feed, "_clear_staging", cleanup_failed)
        else:
            save = store.feed_set

            def receipt_failed(key, value):
                if key == "feed:" + feed.ident:
                    raise OSError("local feed receipt failed")
                return save(key, value)

            monkeypatch.setattr(store, "feed_set", receipt_failed)
        receipt = feed.sync()
        assert receipt["status"] == "synced"
        assert receipt[late_failure] == "failed"
        aggregate, rows, detail = fold_feed_results(json.dumps([receipt]))
        assert aggregate == "degraded"
        assert rows == [receipt]
        assert rows[0]["commit"] == receipt["commit"]
        assert late_failure in detail
    finally:
        store.close()
