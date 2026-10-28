"""Exercise discovered component tools through native nonce-bound MCP calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import component_tools
import identity
import mcp_server
import mindie_launch


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    config = tmp_path / "kimi.json"
    value = {
        "python": sys.executable,
        "engine_config": str(tmp_path / "must-not-open-knowledge.json"),
        "state_dir": str(tmp_path / "state"),
    }
    config.write_text(json.dumps(value))
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(config))
    return config


def call(name, arguments, *, bind=True):
    arguments = dict(arguments, request_nonce=uuid.uuid4().hex)
    if name != "mindie_status":
        arguments.setdefault("cwd", str(Path(os.environ["MINDIE_KIMI_CONFIG"]).parent))
    if bind:
        identity.publish_nonce_bind(
            "ordinary-session", arguments["request_nonce"],
            "mcp__plugin-mindie-agent_components__" + name, uuid.uuid4().hex, arguments,
        )
    return mcp_server.handle("components", {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })["result"]


def test_manifest_launch_discovers_schema_and_executes_real_profiler(adapter):
    config = adapter
    manifest = json.loads((ROOT / "kimi.plugin.json").read_text())
    server = manifest["mcpServers"]["components"]
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    process = subprocess.run(
        [sys.executable, *server["args"]], cwd=ROOT, input=json.dumps(request) + "\n",
        text=True, capture_output=True, timeout=8,
    )
    assert process.returncode == 0, process.stderr
    catalog = {tool["name"]: tool for tool in json.loads(process.stdout)["result"]["tools"]}
    tool = catalog["profiler"]
    assert {"argv", "request_nonce"} <= set(tool["inputSchema"]["required"])
    capture = ROOT / "tests/fixtures/profiler/rank_0"
    result = call(tool["name"], {"argv": ["inventory", "--root", str(capture)]})
    assert not result["isError"], result
    inventory = result["structuredContent"]["result"]
    assert inventory["rank_count"] == 1
    assert inventory["ranks"][0]["files"]
    assert not Path(json.loads(config.read_text())["engine_config"]).exists()


def test_components_cannot_execute_without_native_bind(adapter, tmp_path):
    output = tmp_path / "support.zip"
    result = call("diagnostics_bundle", {"root": str(tmp_path), "output": str(output)}, bind=False)
    assert result["isError"]
    assert "PreToolUse" in result["content"][0]["text"]
    assert not output.exists()


def test_real_coordinator_and_local_diagnostics_need_no_knowledge(adapter, tmp_path):
    inventory = call("coordinator", {"argv": ["hosts", "--action", "list"]})
    assert not inventory["isError"], inventory
    assert inventory["structuredContent"]["result"]["outcome"] == "success"
    status = call("diagnostics_status", {})
    assert not status["isError"], status
    assert status["structuredContent"]["result"]["status"] == "not_configured"
    logs = tmp_path / "logs"
    logs.mkdir()
    output = tmp_path / "support.zip"
    bundle = call("diagnostics_bundle", {"root": str(logs), "output": str(output)})
    assert not bundle["isError"], bundle
    assert output.is_file()


def test_real_sim_preserves_output_file_and_failure_details(adapter, tmp_path, monkeypatch):
    # Native hosts need not inherit the virtualenv's PATH; the adapter resolves
    # its installed sibling CLI from the configured interpreter.
    monkeypatch.setenv("PATH", str(tmp_path / "no-path"))
    output = tmp_path / "models.json"
    result = call("sim", {"argv": ["models", "--output", output.name],
                          "max_output_tokens": 65536})
    assert not result["isError"], result
    models = json.loads(output.read_text())["models"]
    assert models and all(model["layers"] > 0 for model in models)
    result = call("sim", {"argv": ["model-info"]})
    assert result["isError"]
    payload = result["structuredContent"]
    assert payload["exit_code"] == 2
    assert payload["stdout"] == ""
    assert "--key" in payload["stderr"]


def test_completed_cli_keeps_receipt_when_local_cleanup_times_out(adapter, tmp_path, monkeypatch):
    import bounded

    cleanup = bounded._kill_tree

    def delayed_cleanup(process, pgid=None):
        cleanup(process, pgid)
        raise TimeoutError("local cleanup receipt timed out")

    monkeypatch.setattr(bounded, "_kill_tree", delayed_cleanup)
    output = tmp_path / "completed.json"
    result = call("sim", {"argv": ["models", "--output", output.name],
                          "max_output_tokens": 65536})
    assert not result["isError"], result
    assert json.loads(output.read_text())["models"]
    payload = result["structuredContent"]
    assert payload["exit_code"] == 0
    assert payload["cleanup_status"] == "failed"
    assert "TimeoutError" in payload["cleanup_error"]


def test_native_working_directory_and_profile_component_override(adapter, tmp_path):
    from support import write_session

    config = adapter
    value = json.loads(config.read_text())
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"component_commands": {
        "sim": [sys.executable, "-I", "-m", "mindie_sim"],
    }}))
    value["component_commands"] = {"sim": [str(tmp_path / "stale-generation-cli")]}
    value["profile_config"] = str(profile)
    config.write_text(json.dumps(value))
    project = tmp_path / "project"
    project.mkdir()
    write_session(Path(os.environ["KIMI_CODE_HOME"]), "ordinary-session", [], cwd=project)
    arguments = {"argv": ["models", "--output", "native-models.json"],
                 "max_output_tokens": 65536}
    result = component_tools.execute("sim", arguments, "ordinary-session")
    assert not result["isError"], result
    assert json.loads((project / "native-models.json").read_text())["models"]
    assert not (tmp_path / "native-models.json").exists()


def test_factual_status_nonce_requires_no_manual_entry(adapter, tmp_path):
    config = adapter
    engine = Path(json.loads(config.read_text())["engine_config"])
    engine.write_text(json.dumps({
        "root": str(tmp_path / "domain"), "domain": "vllm-ascend",
        "admission_path": str(tmp_path / "domain/admission.sqlite3"),
    }))
    result = call("mindie_status", {})
    assert not result["isError"], result
    assert result["structuredContent"]["knowledge_enabled"] is False
    assert not {"hint", "next", "choices"}.intersection(result["structuredContent"])
    assert not (tmp_path / "domain").exists()


def test_launcher_allows_component_inner_deadline_and_serialized_output():
    arguments = {"argv": [], "timeout_ms": 600000, "max_output_tokens": 262144}
    request = json.dumps({"params": {"name": "sim", "arguments": arguments}}).encode()
    budget = mindie_launch._call_budget("components", request)
    assert budget >= 600 + 5 + 5 + 2
    native = json.loads((ROOT / "kimi.plugin.json").read_text())["mcpServers"]["components"]
    assert native["toolTimeoutMs"] / 1000 >= budget + mindie_launch.CALL_LOCK_BUDGET + mindie_launch.OUTPUT_BUDGET
    response = {"content": [{"type": "text", "text": "\0" * (262144 * 4)}],
                "structuredContent": {"stdout": "\0" * (262144 * 4)}}
    assert mindie_launch._response_budget("components", request) > len(json.dumps(response).encode())


def test_missing_component_is_an_explicit_tool_error(adapter, tmp_path):
    config = adapter
    value = json.loads(config.read_text())
    value["component_commands"] = {"sim": [str(tmp_path / "missing")]}
    config.write_text(json.dumps(value))
    result = call("sim", {"argv": ["--help"]})
    assert result["isError"]
    assert "missing" in result["content"][0]["text"]


@pytest.mark.parametrize("mode", ["timeout", "output_limit"])
def test_interrupted_component_keeps_partial_report_without_replay(adapter, tmp_path, mode):
    child = tmp_path / "partial_report.py"
    child.write_text(
        "import json, sys, threading\n"
        "with open('receipts.jsonl', 'a') as receipt:\n"
        "    receipt.write(json.dumps({'phase': 'inventory'}) + '\\n')\n"
        "print('inventory completed', flush=True)\n"
        "print('analysis pending', file=sys.stderr, flush=True)\n"
        "if sys.argv[1] == 'timeout':\n"
        "    threading.Event().wait(30)\n"
        "else:\n"
        "    sys.stdout.write('x' * 131072)\n"
        "    sys.stdout.flush()\n",
        encoding="utf-8", newline="\n",
    )
    value = json.loads(adapter.read_text())
    value["component_commands"] = {"profiler": [sys.executable, str(child)]}
    adapter.write_text(json.dumps(value))
    result = call("profiler", {
        "argv": [mode], "timeout_ms": 800 if mode == "timeout" else 5000,
        "max_output_tokens": 8192,
    })
    assert result["isError"], result
    payload = result["structuredContent"]
    assert payload["status"] == mode
    assert payload["stdout"].startswith("inventory completed")
    assert payload["output_truncated"] == (mode == "output_limit")
    assert len((payload["stdout"] + payload["stderr"]).encode()) <= 8192 * 4
    if mode == "timeout":
        assert "analysis pending" in payload["stderr"]
    assert [json.loads(line) for line in (tmp_path / "receipts.jsonl").read_text().splitlines()] == [
        {"phase": "inventory"},
    ]
