"""Nonce-bound tool calls still need this session's manual knowledge grant."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from support import make_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import admission
import entry
import mcp_server


@pytest.mark.parametrize("state", ["missing", "automatic", "other-session", "revoked"])
@pytest.mark.parametrize("name,arguments", [
    ("knowledge_query", {"query": "KDA"}),
    ("knowledge_explain", {"ref": "mindie://vllm-ascend/units/kda@1"}),
    ("knowledge_feedback", {"ref": "mindie://vllm-ascend/units/kda@1", "rating": "up"}),
    ("knowledge_distill", {"inputs": [], "task_state": "in_flight"}),
    ("knowledge_sync", {}),
])
def test_knowledge_denial_precedes_service(tmp_path, monkeypatch, state, name, arguments):
    config = make_config(tmp_path)
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(config))
    gate = admission.gate()
    if state == "automatic":
        gate.associate("session", project_root=str(tmp_path), not_before=1.0)
    elif state == "other-session":
        gate.activate("parent", project_root=str(tmp_path))
    elif state == "revoked":
        gate.activate("session", project_root=str(tmp_path))
        gate.deactivate("session")
    with patch("knowledge_service.ensure_service") as ensure, patch("knowledge_service.existing_service") as existing:
        with pytest.raises(ValueError, match="/mindie-agent"):
            mcp_server.knowledge_call(name, arguments, "session")
        ensure.assert_not_called()
        existing.assert_not_called()


def test_factual_status_is_available_without_knowledge_or_a_skill_origin(tmp_path, monkeypatch):
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
    assert result["knowledge_enabled"] is False
    assert not {"hint", "next", "note", "choices", "required"}.intersection(result)
    assert not admission.gate().path.exists()
