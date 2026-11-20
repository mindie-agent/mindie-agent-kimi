"""Cold hooks, strict MCP surface separation, 9-domain binding, migration, and installer helpers."""
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
EXPECTED_DOMAINS = {
    "vllm-ascend", "operators", "xllm", "mindie-sd", "infer-core",
    "infer-distributed", "infer-observability", "infer-ops-compat", "agent-engineering",
}


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
    before = set(_sqlite_files(isolated_home))
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(json.dumps({"cwd": candidate}).encode())))
    started = time.perf_counter()
    assert launcher.main(["hook", "stop"]) == 0
    elapsed = time.perf_counter() - started
    assert elapsed < HOOK_BUDGET, f"{state} cold Stop fast path took {elapsed:.4f}s"
    assert capfd.readouterr().out == "{}\n"
    assert set(_sqlite_files(isolated_home)) == before, "fast Stop path opened SQLite"
    assert not [p for p in _sqlite_files(isolated_home) if "admission" in p.name]


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
    assert MCPStdioServer(router, server_name=f"mindie-{surface}", server_version=entry.VERSION).serve(stdin=stream, stdout=out) == 0
    replies = {payload["id"]: payload for payload in map(json.loads, out.getvalue().splitlines()) if payload}
    names = [item["name"] for item in replies[2]["result"]["tools"]]
    return replies, names


def test_knowledge_surface_is_exclusive() -> None:
    replies, names = _serve("knowledge", "mindie_status")
    assert names == KNOWLEDGE
    assert not [name for name in names if name.startswith("remote_") or name in COMPONENTS]
    assert replies[1]["result"]["serverInfo"]["version"] == "0.1.33"
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


def test_setup_init_provisions_all_nine_domains_and_root_override(
    isolated_home: Path, plugin_env: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_root = isolated_home / "custom-kb-root"
    summary = _json_run([SETUP, "init", "--root", str(custom_root)], env=plugin_env)
    assert summary["status"] == "initialized"
    engine_path = Path(summary["engine_config"])
    engine = json.loads(engine_path.read_text(encoding="utf-8"))
    assert engine["domain"] == "vllm-ascend"
    assert Path(engine["root"]).resolve() == custom_root.resolve()
    assert engine["transcript_adapter"].endswith("kimi_transcript.py")
    assert engine["community_config"].endswith("mindie-community.json")
    assert Path(summary["native_package"]).resolve() == REPO.resolve()

    cfg_path = Path(summary["config_path"])
    cfg_doc = json.loads(cfg_path.read_text(encoding="utf-8"))
    domain_configs = cfg_doc["domain_configs"]
    assert set(domain_configs.keys()) == EXPECTED_DOMAINS
    for dom, dom_cfg_raw in domain_configs.items():
        dom_path = Path(dom_cfg_raw)
        assert dom_path.is_file()
        assert "engines" in dom_path.parts and "kimi" in dom_path.parts
        dom_doc = json.loads(dom_path.read_text(encoding="utf-8"))
        assert dom_doc["domain"] == dom
        assert Path(dom_doc["root"]).resolve() == custom_root.resolve()
        assert dom_doc["transcript_adapter"].endswith("kimi_transcript.py")
        assert isinstance(dom_doc.get("community_config"), str) and dom_doc["community_config"]

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
    cfg_doc = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg_doc["component_commands"] = {"sim": ["/custom/python", "-m", "mindie_sim"]}
    cfg_path.write_text(json.dumps(cfg_doc), encoding="utf-8")
    _json_run([SETUP, "init"], env=plugin_env)
    assert json.loads(cfg_path.read_text(encoding="utf-8"))["component_commands"]["sim"][0] == "/custom/python"

    # Non-configured return statuses ("skipped" / "deferred") must fail fast instead of printing initialized
    setup_mod = _load("setup")
    import mindie_harness.runtime.knowledge_binding as kb_mod
    from mindie_harness.paths import ProfileRef

    monkeypatch.setattr(kb_mod, "ensure_knowledge_binding", lambda *a, **kw: {"status": "skipped", "reason": "knowledge_not_installed"})
    profile = ProfileRef.resolve("kimi", plugin_root=REPO, config_path=isolated_home / "fail-cfg" / "kimi.json")
    with pytest.raises(SystemExit):
        setup_mod._init(profile, [])
    assert not profile.config_path.exists()


def test_stop_preserves_native_turn_id_and_deterministic_fallback(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _load("entry")
    from mindie_harness.paths import ProfileRef

    captured: list[dict] = []
    monkeypatch.setattr(entry, "run_stop_capture", lambda _prof, payload, **_kw: captured.append(dict(payload)) or {"stage": "inert"})
    profile = ProfileRef.resolve("kimi", plugin_root=REPO, config_path=isolated_home / "cfg" / "kimi.json")
    cwd = isolated_home / "work" / "app"
    cwd.mkdir(parents=True)

    # 1. Native turn_id (even without explicit identity_kind) -> identity_kind="turn" + last_assistant_message preserved
    turn_event = {"session_id": "ses_turn", "turn_id": "turn_42", "last_assistant_message": "final answer", "cwd": str(cwd)}
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(json.dumps(turn_event).encode())))
    entry._stop(profile)
    assert captured[-1]["identity_kind"] == "turn"
    assert captured[-1]["turn_id"] == "turn_42"
    assert captured[-1]["last_assistant_message"] == "final answer"
    assert "event_id" not in captured[-1]

    # 2. Absent turn_id -> notification with deterministic (repeatable) event_id across retries
    notif_event = {"session_id": "ses_notif", "cwd": str(cwd)}
    for _ in range(2):
        monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(json.dumps(notif_event).encode())))
        entry._stop(profile)
    assert captured[-1]["identity_kind"] == "notification"
    assert captured[-2]["event_id"] == captured[-1]["event_id"]
    assert len(str(captured[-1]["event_id"])) == 32


def test_stop_honors_inactive_session_revocation_permanence(
    isolated_home: Path, plugin_env: dict[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mindie_harness.paths import ProfileRef
    from mindie_knowledge.loop.activation import Admission

    project = isolated_home / "work" / "app"
    project.mkdir(parents=True)
    summary = _json_run([SETUP, "init"], env=plugin_env)
    _json_run([SETUP, "sharing", "--local-only", "--project-root", str(project)], env=plugin_env)
    engine_doc = json.loads(Path(summary["engine_config"]).read_text(encoding="utf-8"))
    admission = Admission(Path(engine_doc["admission_path"]))
    admission.activate("ses_revoked", project_root=str(project.resolve()), not_before=0.0)
    admission.deactivate("ses_revoked")
    assert admission.inspect("ses_revoked")["status"] == "inactive"

    entry = _load("entry")
    profile = ProfileRef.resolve("kimi", plugin_root=REPO, config_path=Path(summary["config_path"]))
    event_bytes = json.dumps({"hook_event_name": "Stop", "session_id": "ses_revoked", "cwd": str(project)}).encode()
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(event_bytes)))
    outcome = entry._stop(profile)
    assert admission.inspect("ses_revoked")["status"] == "inactive"
    if isinstance(outcome, dict):
        assert outcome.get("stage") == "inert"


def test_validated_kimi_json_precedence_and_seed_stdin_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = _load("mindie_launch")
    state = tmp_path / "state"
    stale_gen = state / "update" / "generations" / ("c" * 40)
    (stale_gen / "scripts").mkdir(parents=True)
    (stale_gen / "scripts" / "entry.py").write_text("# stale\n", encoding="utf-8")
    stale_py = tmp_path / "stale-venv" / ("python.exe" if os.name == "nt" else "python3")
    stale_py.parent.mkdir(parents=True)
    stale_py.write_bytes(b"MZstale")
    if os.name != "nt":
        stale_py.chmod(0o755)
    stale_adapter = stale_gen / "kimi.adapter.json"
    stale_adapter.write_text("{}", encoding="utf-8")
    (state / "update").mkdir(parents=True, exist_ok=True)
    (state / "update" / "current.json").write_text(json.dumps({
        "generation": str(stale_gen), "python": str(stale_py), "adapter_config": str(stale_adapter),
    }), encoding="utf-8")

    new_gen = tmp_path / "generations" / "gen-new"
    new_gen.mkdir(parents=True)
    new_py = new_gen / ("python.exe" if os.name == "nt" else "python3")
    new_py.write_bytes(b"MZnew")
    if os.name != "nt":
        new_py.chmod(0o755)
    (new_gen / "runtime.json").write_text(json.dumps({
        "schema": "mindie-candidate-check/1", "python": str(new_py), "validation": {"status": "validated"},
    }), encoding="utf-8")
    cfg_path = tmp_path / "cfg" / "kimi.json"
    cfg_path.parent.mkdir(parents=True)

    # 1. Validated component_release in kimi.json wins over stale current.json
    cfg_path.write_text(json.dumps({"python": str(new_py), "component_release": str(new_gen), "state_dir": str(state)}), encoding="utf-8")
    assert launcher._resolve_target(cfg_path) == (new_py, launcher.HERE, cfg_path)

    # 2. Unusable component_release receipt still honors valid top-level kimi.json["python"] before stale current.json
    cfg_path.write_text(json.dumps({"python": str(new_py), "component_release": str(tmp_path / "gone-gen"), "state_dir": str(state)}), encoding="utf-8")
    assert launcher._resolve_target(cfg_path) == (new_py, launcher.HERE, cfg_path)

    # 3. Missing python in kimi.json falls back to legacy current.json
    cfg_path.write_text(json.dumps({"python": str(tmp_path / "missing-py"), "state_dir": str(state)}), encoding="utf-8")
    assert launcher._active_python(cfg_path) == stale_py
    assert launcher._effective_scripts(cfg_path) == stale_gen / "scripts"

    # 4. Cold seed provisioning isolates stdin=subprocess.DEVNULL so MCP client stdin is untouched
    seen_kwargs: dict[str, object] = {}

    def _fake_seed_run(argv, **kwargs):
        seen_kwargs.update(kwargs)
        cfg_path.write_text(json.dumps({"python": str(new_py)}), encoding="utf-8")
        return subprocess.CompletedProcess(argv, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(launcher.subprocess, "run", _fake_seed_run)
    assert launcher._provision(cfg_path)[0] == new_py
    assert seen_kwargs.get("stdin") == subprocess.DEVNULL
    assert seen_kwargs.get("capture_output") is True


def test_windowsapps_stub_rejected_in_python_discovery(tmp_path: Path) -> None:
    launcher = _load("mindie_launch")
    installer = _load("install_kimi_plugin")

    win_apps = tmp_path / "Microsoft" / "WindowsApps"
    zero_dir = tmp_path / "zero-bin"
    real_dir = tmp_path / "real-python"
    for folder in (win_apps, zero_dir, real_dir):
        folder.mkdir(parents=True)
    (win_apps / "python.exe").write_bytes(b"MZreparse-stub")
    (win_apps / "python3.exe").write_bytes(b"")
    (zero_dir / "python.exe").write_bytes(b"")
    real_py = real_dir / "python.exe"
    real_py.write_bytes(b"MZreal-cpython-313")

    fake_path = os.pathsep.join([str(win_apps), str(zero_dir), str(real_dir)])
    assert launcher._valid_python(win_apps / "python.exe", os_name="nt") is None
    assert launcher._valid_python(zero_dir / "python.exe", os_name="nt") is None
    assert launcher._valid_python(real_py, os_name="nt") == real_py
    assert launcher._discover_python(path_env=fake_path, os_name="nt", allow_uv=False) == real_py
    assert installer._find_python(fake_path, os_name="nt", allow_uv=False) == str(real_py)


def test_mindie_kimi_update_schedule_migration_cleanup(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_mod = _load("setup")
    from mindie_harness.paths import ProfileRef

    monkeypatch.delenv("MINDIE_KIMI_SKIP_SCHTASKS", raising=False)
    monkeypatch.setattr(setup_mod, "_NO_WIN", 0x08000000)
    profile = ProfileRef.resolve("kimi", plugin_root=REPO, config_path=isolated_home / "cfg" / "kimi.json")
    upd_dir = profile.state_dir / "update"
    upd_dir.mkdir(parents=True)
    wrapper = upd_dir / "MindIEKimiUpdate.pyw"
    task_xml = upd_dir / "MindIEKimiUpdate.xml"
    wrapper.write_text("# legacy\n", encoding="utf-8")
    task_xml.write_text("<Task/>", encoding="utf-8")

    # 1. Delete failure / Access Denied MUST preserve wrapper & XML files intact
    monkeypatch.setattr(
        setup_mod.subprocess, "run",
        lambda argv, **_kw: subprocess.CompletedProcess(
            argv, returncode=0 if "/Query" in argv else 1, stdout=b"", stderr=b"ERROR: Access is denied.",
        ),
    )
    denied = setup_mod._retire_legacy_schedule(profile, os_name="nt", platform_name="win32")
    assert denied["status"] == "failed"
    assert wrapper.exists() and task_xml.exists()

    # 2. Confirmed deletion unlinks wrapper & XML files with CREATE_NO_WINDOW
    calls: list[tuple[list[str], int]] = []

    def _fake_retire(argv, **kwargs):
        calls.append((list(argv), int(kwargs.get("creationflags") or 0)))
        return subprocess.CompletedProcess(argv, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(setup_mod.subprocess, "run", _fake_retire)
    retired = setup_mod._retire_legacy_schedule(profile, os_name="nt", platform_name="win32")
    assert retired["status"] == "retired"
    assert calls == [
        (["schtasks", "/Query", "/TN", "MindIEKimiUpdate"], 0x08000000),
        (["schtasks", "/Delete", "/TN", "MindIEKimiUpdate", "/F"], 0x08000000),
    ]
    assert not wrapper.exists() and not task_xml.exists()

    # 3. subprocess.TimeoutExpired must propagate explicitly
    def _raise_timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, timeout=15.0)

    monkeypatch.setattr(setup_mod.subprocess, "run", _raise_timeout)
    with pytest.raises(subprocess.TimeoutExpired):
        setup_mod._retire_legacy_schedule(profile, os_name="nt", platform_name="win32")


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


def _valid_report(version: str = "0.1.33") -> dict:
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
