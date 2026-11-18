"""Default-enabled knowledge tools work without /mindie-agent or request_nonce, while respecting explicit deactivation."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from support import make_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import admission
import entry
import mcp_server


@pytest.mark.parametrize("name,arguments", [
    ("knowledge_query", {"query": "KDA"}),
    ("knowledge_explain", {"ref": "mindie://vllm-ascend/units/kda@1"}),
    ("knowledge_feedback", {"ref": "mindie://vllm-ascend/units/kda@1", "rating": "up"}),
    ("knowledge_distill", {"inputs": [{
        "kind": "authorized_transcript",
        "outcome": "verified",
        "source_locator": "fixture@abcdef1:probe.txt",
        "content": "verified fixture",
        "evidence_refs": ["fixture-observation"],
    }], "task_state": "completed"}),
    ("knowledge_sync", {}),
])
def test_deactivated_session_denial_precedes_service(tmp_path, monkeypatch, name, arguments):
    config = make_config(tmp_path)
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(config))
    gate = admission.gate()
    gate.activate("session", project_root=str(tmp_path))
    gate.deactivate("session")
    with patch("knowledge_service.ensure_service") as ensure, \
            patch("knowledge_service.existing_service") as existing, \
            patch("knowledge_sync.execute") as sync_exec:
        with pytest.raises(ValueError, match="deactivated"):
            mcp_server.knowledge_call(name, arguments, "session")
        ensure.assert_not_called()
        existing.assert_not_called()
        sync_exec.assert_not_called()


def test_default_enabled_knowledge_tools_need_no_nonce_or_prior_activation(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(config))
    listed = mcp_server.handle("knowledge", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    names = {item["name"] for item in listed}
    assert names == {
        "mindie_entry",
        "knowledge_query",
        "knowledge_explain",
        "knowledge_feedback",
        "knowledge_distill",
        "knowledge_sync",
    }
    for item in listed:
        assert "request_nonce" not in item["inputSchema"].get("required", [])

    captured_rpc = []

    def fake_rpc(_conn, method, payload, *, timeout=None):
        captured_rpc.append((method, payload))
        return {"results": [], "ok": True}

    with patch("knowledge_service.existing_service", return_value={"url": "http://127.0.0.1:1", "token": "t"}), \
            patch("mindie_knowledge.loop.cli.rpc", fake_rpc):
        resp = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "knowledge_query", "arguments": {"query": "AscendC"}},
        })["result"]
    assert resp["isError"] is False
    assert captured_rpc[0][0] == "query"
    assert not admission.gate().path.exists()

    with patch("knowledge_service.existing_service", return_value={"url": "http://127.0.0.1:1", "token": "t"}), \
            patch("mindie_knowledge.loop.cli.distill_selected", return_value={"status": "noop", "model_calls": 0}) as distill:
        distill_resp = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "knowledge_distill",
                "arguments": {
                    "inputs": [{
                        "kind": "authorized_transcript",
                        "outcome": "verified",
                        "source_locator": "fixture@abcdef1:probe.txt",
                        "content": "verified fixture",
                        "evidence_refs": ["fixture-observation"],
                    }],
                    "task_state": "completed",
                },
            },
            "_meta": {"session_id": "ses_meta_hint"},
        })["result"]
    assert distill_resp["isError"] is False
    lease = admission.knowledge_lease("ses_meta_hint")
    assert lease is not None
    assert lease["activated_at"] == 0.0
    assert distill.call_args.args[1]["_session_id"] == "ses_meta_hint"
    assert distill.call_args.args[1]["_activation"] == lease["token"]


def test_factual_status_is_available_without_creating_admission_sqlite(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(config))
    with patch.object(entry, "require_current_plugin_command") as origin, \
            patch("knowledge_service.ensure_service") as ensure, \
            patch("knowledge_service.existing_service") as existing:
        result = entry.op_status("ordinary-session")
        origin.assert_not_called()
        ensure.assert_not_called()
        existing.assert_not_called()
    assert result["configured"]
    assert result["knowledge_enabled"] is True
    assert result["this_session"]["bound"] is False
    assert result["this_session"]["enabled"] is True
    assert not {"hint", "next", "note", "choices", "required"}.intersection(result)
    assert not admission.gate().path.exists()


def test_whitelist_fresh_deactivate_and_busy_bind_fallback(tmp_path, monkeypatch):
    import sqlite3

    config = make_config(tmp_path)
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(config))

    # 1. Unlisted tool names are rejected before touching knowledge_service
    with patch("knowledge_service.ensure_service") as ensure, \
            patch("knowledge_service.existing_service") as existing:
        rejected = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "knowledge_stop", "arguments": {}},
        })["result"]
        assert rejected["isError"] is True
        assert "unknown knowledge tool" in rejected["content"][0]["text"]
        ensure.assert_not_called()
        existing.assert_not_called()

    # 2. Fresh-session deactivate persists a durable disabled row without prior lease
    assert not admission.gate().path.exists()
    assert admission.deactivate("fresh-session", project_root=tmp_path) is True
    assert admission.is_deactivated("fresh-session") is True
    assert admission.deactivate("fresh-session", project_root=tmp_path) is False
    with pytest.raises(ValueError, match="deactivated"):
        mcp_server.knowledge_call("knowledge_query", {"query": "Ascend"}, "fresh-session")

    # 3. Busy optional bind store falls back cleanly to _CONNECTION_SESSION_ID instead of crashing
    with patch.object(mcp_server, "claim_nonce", side_effect=sqlite3.OperationalError("database is locked")), \
            patch("knowledge_service.existing_service", return_value={"url": "http://127.0.0.1:1", "token": "t"}), \
            patch("mindie_knowledge.loop.cli.rpc", return_value={"results": []}):
        fallback_resp = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {"name": "knowledge_query", "arguments": {"query": "Ascend", "request_nonce": "nonce-busy-1"}},
        })["result"]
    assert fallback_resp["isError"] is False
