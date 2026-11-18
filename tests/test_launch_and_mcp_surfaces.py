"""Cold hooks, strict MCP surface separation, setup delegation, and installer helpers."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import types

import pytest

REPO = Path(__file__).resolve().parents[1]
LAUNCH = REPO / "scripts/mindie_launch.py"
SETUP = REPO / "scripts/setup.py"
SLOW = 0.30
HOOK_BUDGET = 0.03
KNOWLEDGE = ["knowledge_query", "knowledge_explain", "knowledge_feedback", "knowledge_distill", "knowledge_sync"]
REMOTE = [
    "remote_Read", "remote_Write", "remote_Edit", "remote_Bash", "remote_Glob", "remote_Grep",
    "remote_ReadMediaFile", "remote_TaskOutput", "remote_TaskStop", "remote_TaskList",
    "remote_auth", "remote_probe", "remote_artifact_pull", "remote_artifact_push",
]
COMPONENTS = ["mindie_status", "mindie_doctor", "profiler", "sim", "coordinator", "npu_top",
              "debugger", "rsi_loop", "experiment_ledger", "diagnostics_status", "diagnostics_bundle"]


def _run(argv: list[object], *, stdin: str | None = None, env: dict[str, str], timeout: float = 60.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *map(str, argv)], input=stdin, capture_output=True, text=True,
        env=env, timeout=timeout, cwd=str(REPO),
    )


def _json_run(argv: list[object], *, env: dict[str, str]) -> dict:
    result = _run(argv, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _sqlite_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file() and path.suffix in {".sqlite3", ".sqlite", ".db"}]


def _load(name: str):
    if str(REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _stop(env: dict[str, str], cwd: str) -> subprocess.CompletedProcess:
    return _run([LAUNCH, "hook", "stop"], stdin=json.dumps({"cwd": cwd}), env=env)


@pytest.mark.parametrize("state", ["unconfigured", "disabled", "out-of-scope", "sibling"])
def test_stop_fast_paths_exit_empty_without_sqlite(isolated_home: Path, plugin_env: dict[str, str], state: str) -> None:
    project = isolated_home / "work" / "app"
    project.mkdir(parents=True)
    if state != "unconfigured":
        _run([SETUP, "init"], env=plugin_env)
        if state == "disabled":
            _run([SETUP, "sharing", "--disable"], env=plugin_env)
        else:
            _run([SETUP, "sharing", "--local-only", "--project-root", project], env=plugin_env)
    candidate = str(project if state in {"disabled", "sibling"} else isolated_home / "work" / "outside")
    if state == "sibling":
        candidate = str(isolated_home / "work" / "app-other")
    before = set(_sqlite_files(isolated_home))
    started = time.perf_counter()
    result = _stop(plugin_env, candidate)
    elapsed = time.perf_counter() - started
    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}\n"
    assert elapsed < SLOW, f"{state} hook stop took {elapsed:.3f}s"
    assert set(_sqlite_files(isolated_home)) == before, "fast Stop path opened SQLite"


@pytest.mark.parametrize("state", ["unconfigured", "disabled", "out-of-scope", "sibling"])
def test_stop_in_process_budget(isolated_home: Path, plugin_env: dict[str, str], state: str,
                                monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture) -> None:
    project = isolated_home / "work" / "app"
    project.mkdir(parents=True)
    if state != "unconfigured":
        _run([SETUP, "init"], env=plugin_env)
        _run([SETUP, "sharing", "--local-only", "--project-root", project], env=plugin_env)
        if state == "disabled":
            _run([SETUP, "sharing", "--disable"], env=plugin_env)
    candidate = str(project if state == "disabled" else isolated_home / "work" / "app-other")
    launcher = _load("mindie_launch")
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(json.dumps({"cwd": candidate}).encode())))
    started = time.perf_counter()
    assert launcher.main(["hook", "stop"]) == 0
    elapsed = time.perf_counter() - started
    assert elapsed < HOOK_BUDGET, f"{state} cold Stop fast path took {elapsed:.4f}s"
    assert capfd.readouterr().out == "{}\n"
    assert _sqlite_files(isolated_home) == [], "fast Stop path opened SQLite"


def test_pretool_never_reads_stdin(plugin_env: dict[str, str]) -> None:
    process = subprocess.Popen(
        [sys.executable, str(LAUNCH), "hook", "pretool"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=plugin_env, cwd=str(REPO),
    )
    try:
        line = _readline_with_budget(process, 10.0)
        assert line == b"{}\n"
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.wait(timeout=10)


def _readline_with_budget(process: subprocess.Popen, budget: float) -> bytes:
    box: dict[str, bytes] = {}
    reader = threading.Thread(target=lambda: box.setdefault("line", process.stdout.readline()), daemon=True)
    reader.start()
    reader.join(budget)
    if reader.is_alive():
        process.kill()
        raise AssertionError("hook pretool blocked on stdin")
    return box.get("line", b"")


def test_session_start_enqueues_update_check(isolated_home: Path, plugin_env: dict[str, str]) -> None:
    result = _run([LAUNCH, "hook", "session-start"], env=plugin_env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "{}\n"
    marker = Path(os.environ["MINDIE_DATA_HOME"]) / "state" / "kimi" / "pending-update-check.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["schema"] == "mindie-pending-update/1"
    assert payload["host"] == "kimi" and payload["reason"] == "session_start"


def _serve(surface: str, tool: str) -> tuple[dict[int, dict], list[str]]:
    entry = _load("entry")
    from mindie_harness.mcp.router import MCPRouter
    from mindie_harness.mcp.server import MCPStdioServer
    from mindie_harness.paths import ProfileRef

    config = Path(os.environ["MINDIE_CONFIG_HOME"]) / "kimi.json"
    profile = ProfileRef.resolve("kimi", plugin_root=REPO, config_path=config)
    adapter = entry.KimiHostAdapter(config_path=profile.config_path)
    tools = adapter.mcp_tools(surface, profile)
    router = MCPRouter(profile, surface=surface, tools=tools,
                       handler=lambda ctx, args: adapter.dispatch_tool(ctx, args, profile),
                       session_extractor=adapter.extract_session_identity)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": tool, "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "ping"},
    ]
    stream = io.BytesIO(b"".join(json.dumps(item).encode() + b"\n" for item in requests))
    out = io.BytesIO()
    assert MCPStdioServer(router, server_name=f"mindie-{surface}", server_version="0.1.31").serve(stdin=stream, stdout=out) == 0
    replies = {payload["id"]: payload for payload in map(json.loads, out.getvalue().splitlines()) if payload}
    names = [item["name"] for item in replies[2]["result"]["tools"]]
    return replies, names


def test_knowledge_surface_is_exclusive() -> None:
    replies, names = _serve("knowledge", "mindie_status")
    assert names == KNOWLEDGE
    assert not [name for name in names if name.startswith("remote_") or name in COMPONENTS]
    assert replies[1]["result"]["serverInfo"]["version"] == "0.1.31"
    excluded = replies[3]["result"]
    assert excluded["isError"] is True
    assert excluded["structuredContent"]["reason"] == "unknown_operation"
    assert replies[4]["result"] == {}


def test_remote_surface_is_exclusive() -> None:
    replies, names = _serve("remote", "knowledge_query")
    assert names == REMOTE
    assert replies[3]["result"]["structuredContent"]["reason"] == "unknown_operation"
    assert replies[4]["result"] == {}


def test_components_surface_is_exclusive() -> None:
    replies, names = _serve("components", "knowledge_query")
    for expected in ("mindie_doctor", "mindie_status", "diagnostics_status", "profiler", "sim", "coordinator", "debugger"):
        assert expected in names
    assert not [name for name in names if name.startswith("knowledge_")]
    assert replies[3]["result"]["structuredContent"]["reason"] == "unknown_operation"


def test_setup_init_and_sharing_lifecycle(isolated_home: Path, plugin_env: dict[str, str]) -> None:
    summary = _json_run([SETUP, "init"], env=plugin_env)
    assert summary["status"] == "initialized"
    engine = json.loads(Path(summary["engine_config"]).read_text(encoding="utf-8"))
    assert engine["domain"] == "vllm-ascend"
    assert engine["transcript_adapter"].endswith("kimi_transcript.py")
    assert Path(summary["native_package"]).resolve() == REPO.resolve()
    project = isolated_home / "work" / "project"
    project.mkdir(parents=True)
    configured = _json_run([SETUP, "sharing", "--local-only", "--project-root", str(project)], env=plugin_env)
    assert configured["tier"] == "local-only" and configured["enabled"] is True
    status = _json_run([SETUP, "sharing", "--status"], env=plugin_env)
    assert status["tier"] == "local-only"
    assert status["project_roots"] == [str(project.resolve())]
    assert _json_run([SETUP, "sharing", "--disable"], env=plugin_env)["tier"] == "disabled"
    assert _json_run([SETUP, "sharing", "--enable", "--project-root", str(project)], env=plugin_env)["tier"] == "contribute"
    assert _json_run([SETUP, "sharing", "--status"], env=plugin_env)["tier"] == "contribute"
    assert _run([SETUP, "sharing", "--enable", "--disable"], env=plugin_env).returncode != 0
    cfg_path = Path(summary["config_path"])
    cfg_doc = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg_doc["component_commands"] = {"sim": ["/custom/python", "-m", "mindie_sim"]}
    cfg_path.write_text(json.dumps(cfg_doc), encoding="utf-8")
    _json_run([SETUP, "init"], env=plugin_env)
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["component_commands"]["sim"][0] == "/custom/python"


def test_setup_doctor_reports_readiness(plugin_env: dict[str, str]) -> None:
    _run([SETUP, "init"], env=plugin_env)
    result = _run([SETUP, "doctor", "--timeout", "10"], env=plugin_env)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["isError"] is False
    body = payload["structuredContent"]
    assert body["schema"] == "mindie-doctor/1"
    assert body["overall_status"] in {"ok", "attention_needed", "degraded"}
    assert "runtime" in body and "packages" in body["runtime"]


def _valid_report(version: str = "0.1.31") -> dict:
    servers = [{"name": name, "enabled": True} for name in ("knowledge", "remote", "components")]
    record = {"mcpServerCount": 3, "enabledMcpServerCount": 3, "hookCount": 2}
    return {
        "install": {"body": {"data": {"id": "mindie-agent", "mcpServers": servers, "diagnostics": [], **record}}},
        "after": {"body": {"data": {"plugins": [{"id": "mindie-agent", "version": version, "enabled": True,
                                                 "state": "ok", **record}]}}},
    }


def test_installer_resource_verification() -> None:
    installer = _load("install_kimi_plugin")
    manifest = json.loads((REPO / "kimi.plugin.json").read_text(encoding="utf-8"))
    expected = installer.expected_native_resources(REPO, manifest)
    assert expected == {"mcp": ["knowledge", "remote", "components"], "mcp_count": 3, "hooks": 2}
    installer.verify_install_report(_valid_report(), REPO, manifest)
    broken = _valid_report()
    broken["install"]["body"]["data"]["mcpServers"] = [{"name": "knowledge", "enabled": True}]
    with pytest.raises(SystemExit):
        installer.verify_install_report(broken, REPO, manifest)
    stale = _valid_report(version="0.1.29")
    with pytest.raises(SystemExit):
        installer.verify_install_report(stale, REPO, manifest)


def test_sharing_roots_accepts_legacy_and_rejects_corrupt_community(isolated_home: Path) -> None:
    launcher = _load("mindie_launch")
    config = isolated_home / "cfg" / "kimi.json"
    config.parent.mkdir(parents=True)
    root = isolated_home / "work" / "app"
    root.mkdir(parents=True)
    (config.parent / "mindie-consent.json").write_text(
        json.dumps({"enabled": True, "project_roots": [str(root)]}), encoding="utf-8")
    assert launcher._sharing_roots(config) == [os.path.normcase(str(root.resolve()))]
    (config.parent / "mindie-community.json").write_text("{corrupt", encoding="utf-8")
    assert launcher._sharing_roots(config) == []


def test_wire_path_and_windows_which_kimi(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _load("entry")
    installer = _load("install_kimi_plugin")
    from mindie_harness.paths import ProfileRef

    good = isolated_home / ".kimi-code" / "sessions" / "b1" / "ses_1"
    bad = isolated_home / ".kimi-code" / "sessions" / "b2" / "ses_1"
    for folder, wd in ((good, isolated_home / "work" / "app"), (bad, isolated_home / "work" / "foreign")):
        (folder / "agents" / "main").mkdir(parents=True)
        (folder / "agents" / "main" / "wire.jsonl").write_text("{}\n", encoding="utf-8")
        (folder / "state.json").write_text(json.dumps({"workDir": str(wd)}), encoding="utf-8")
    monkeypatch.delenv("KIMI_CODE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("USERPROFILE", str(isolated_home))
    profile = ProfileRef.resolve("kimi", plugin_root=REPO, config_path=isolated_home / "cfg" / "kimi.json")
    found = entry._wire_path(profile, "ses_1", str(isolated_home / "work" / "app"))
    assert found == str((good / "agents" / "main" / "wire.jsonl").resolve())

    win_stub = isolated_home / "WindowsApps"
    win_bin = isolated_home / "bin"
    win_stub.mkdir(); win_bin.mkdir()
    (win_stub / "kimi.exe").write_bytes(b"")
    (win_bin / "kimi.exe").write_bytes(b"MZreal")
    monkeypatch.delenv("MINDIE_KIMI_BIN", raising=False)
    monkeypatch.setenv("PATH", f"{win_stub}{os.pathsep}{win_bin}")
    assert installer._which_kimi("nt") == str(win_bin / "kimi.exe")
