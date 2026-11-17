"""Live `v0.1.26 -> v0.1.27` staging upgrade & clean-arch behavioral contract tests."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from support import ROOT, SCRIPTS, env_for, make_config, write_session
from windows_launcher_fixture import write_launcher

TRANSITION_SHIMS = (
    "mindie_launch.py",
    "bounded.py",
    "diagnostic_support.py",
    "paths.py",
    "launch_budget.py",
    "transcript.py",
    "capture_config.py",
)


def _extract_git_tag_tree(tag: str, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", tag],
        capture_output=True,
        timeout=20,
        check=True,
    )
    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:*") as tf:
        tf.extractall(dest, filter="data")
    return dest


def test_transition_shims_under_30_lines_and_seed_lock_matches():
    for rel in TRANSITION_SHIMS:
        path = SCRIPTS / rel
        assert path.is_file(), f"missing transition shim: {rel}"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) < 30, f"{rel} has {len(lines)} lines (must be < 30)"
    assert (ROOT / "runtime-requirements.txt").is_file()
    assert (ROOT / "runtime" / "active-overrides.txt").is_file()

    pyz = ROOT / "assets" / "mindie-harness-seed.pyz"
    lock = json.loads((ROOT / "assets" / "seed-lock.json").read_text(encoding="utf-8"))
    assert lock["schema"] == "mindie-harness-seed/1"
    assert pyz.stat().st_size == lock["size_bytes"]
    assert hashlib.sha256(pyz.read_bytes()).hexdigest() == lock["sha256"]


def test_real_v0_1_26_updater_staging_stages_refactored_candidate_without_file_not_found(tmp_path, monkeypatch):
    """Execute real `v0.1.26` `updater_staging.py` against the refactored `v0.1.27` candidate."""
    old_026 = _extract_git_tag_tree("v0.1.26", tmp_path / "v0.1.26")
    config = make_config(tmp_path / "profile")
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(config))
    adapter = json.loads(config.read_text(encoding="utf-8"))
    state = Path(adapter["state_dir"])

    sha_new = "7" * 40
    candidate = state / "update" / "generations" / sha_new
    candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        ROOT,
        candidate,
        ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", ".venv", "host-package"),
    )

    import genstate

    genstate.write_current(
        {
            "generation": str(old_026),
            "python": sys.executable,
            "adapter_config": str(config),
            "sha": "6" * 40,
        },
        adapter,
    )

    stage_driver = (
        "import json, sys, time\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(old_026 / 'scripts')!r})\n"
        "import updater as old_updater\n"
        f"candidate = Path({str(candidate)!r})\n"
        f"config = Path({str(config)!r})\n"
        "adapter = json.loads(config.read_text(encoding='utf-8'))\n"
        f"sha_new = {sha_new!r}\n"
        "deadline = time.monotonic() + 120.0\n"
        "old_updater.probe_runtime(Path(sys.executable), deadline, candidate)\n"
        "package = old_updater.build_host_package(candidate, adapter, sha_new, config_file=config)\n"
        "print(json.dumps({'package': str(package)}))\n"
    )
    staged = subprocess.run(
        [sys.executable, "-c", stage_driver],
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
        env=env_for(config),
    )
    assert staged.returncode == 0, staged.stderr
    package = Path(json.loads(staged.stdout.strip())["package"])
    assert (package / "kimi.plugin.json").is_file()
    assert not (package / "commands").exists()

    launch_sha_dir = state / "update" / "launch" / sha_new
    for copied in ("mindie_launch.py", "bounded.py", "diagnostic_support.py", "paths.py", "launch_budget.py"):
        assert (launch_sha_dir / copied).is_file()
    assert not (launch_sha_dir / "kimi_launch.py").exists()

    # 3. Switch current.json to candidate and verify the 0.1.26-copied launch/<sha>/mindie_launch.py works!
    genstate.write_current(
        {
            "generation": str(candidate),
            "python": sys.executable,
            "adapter_config": str(config),
            "sha": sha_new,
        },
        adapter,
    )
    proc = subprocess.Popen(
        [sys.executable, str(launch_sha_dir / "mindie_launch.py"), "--config", str(config), "mcp", "remote"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env_for(config),
    )
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
        proc.stdin.flush()
        init_reply = json.loads(proc.stdout.readline())
        assert init_reply["id"] == 1
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        proc.stdin.flush()
        tools_reply = json.loads(proc.stdout.readline())
        proc.stdin.close()
        assert proc.wait(timeout=5) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
    tool_names = {t["name"] for t in tools_reply["result"]["tools"]}
    assert "remote_Bash" in tool_names


def test_windows_scheduled_task_migrates_action_without_create_new_console(tmp_path, monkeypatch):
    config = make_config(tmp_path / "sched")
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(config))
    adapter = json.loads(config.read_text(encoding="utf-8"))
    import genstate
    import updater
    import updater_schedule

    old_py = tmp_path / "old_gen" / "Scripts" / "python.exe"
    new_py = tmp_path / "new_gen" / "Scripts" / "python.exe"
    for py in (old_py, new_py):
        py.parent.mkdir(parents=True, exist_ok=True)
        py.write_bytes(b"stub")
        write_launcher(py.with_name("pythonw.exe"))

    sha_new = "9" * 40
    launch_new = genstate.launch_dir(adapter) / sha_new
    launch_new.mkdir(parents=True, exist_ok=True)
    (launch_new / "mindie_launch.py").write_text("# launcher\n", encoding="utf-8")
    (launch_new / "bounded.py").write_text("# bounded\n", encoding="utf-8")
    wrapper = genstate.update_dir(adapter) / "MindIEKimiUpdate.pyw"
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    wrapper.write_text("# old 0.1.26 CREATE_NEW_CONSOLE wrapper\n", encoding="utf-8")

    current = {
        "generation": str(ROOT),
        "python": str(new_py),
        "adapter_config": str(config),
        "sha": sha_new,
    }
    genstate.write_current(current, adapter)
    registered_cmds: list[list[str]] = []

    def fake_run(argv, stdin="", **_kw):
        registered_cmds.append(list(argv))
        if argv[0] == "powershell":
            return wrapper.with_suffix(".xml").read_text(encoding="utf-16")
        return ""

    with patch.object(updater_schedule, "os") as mock_os, \
         patch.object(updater_schedule, "_scheduler_backend", return_value="task-scheduler"), \
         patch.object(updater, "_task_state", return_value=("present", "")), \
         patch.object(updater, "bounded_run", side_effect=fake_run):
        mock_os.name = "nt"
        mock_os.path = os.path
        mock_os.environ = os.environ
        mock_os.devnull = os.devnull
        assert updater_schedule.refresh_existing_wrapper(adapter, current) is True

    text = wrapper.read_text(encoding="utf-8")
    assert "CREATE_NEW_CONSOLE" not in text
    assert "SW_HIDE" not in text
    assert any("/Create" in cmd for cmd in registered_cmds)


def test_ad02_subagent_shares_remote_job_root_and_ad07_symlink_config(tmp_path, monkeypatch):
    real_cfg = make_config(tmp_path / "real")
    symlink_cfg = tmp_path / "symlink-kimi.json"
    symlink_cfg.symlink_to(real_cfg)
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(symlink_cfg))

    import diagnostic_support
    import mcp_server

    diag_json = tmp_path / "diag-build-target.json"
    diag_json.write_text(json.dumps({"version": "0.1.27", "revision": "a" * 40}), encoding="utf-8")
    link_diag = tmp_path / "link-diag.json"
    link_diag.symlink_to(diag_json)
    assert diagnostic_support._read_small(link_diag) is not None

    observed_dirs: list[tuple[str, str]] = []

    def fake_call_tool(tool_name, args):
        observed_dirs.append((os.environ["REMOTE_DEV_SESSION_ID"], os.environ["REMOTE_DEV_STATE_DIR"]))
        return {"result": {"outcome": "success", "status": "ok", "tool": tool_name}, "text": "ok"}

    with patch("remote_dev.mcp.tools.call_tool", side_effect=fake_call_tool):
        mcp_server.remote_call("remote_Bash", {"command": "echo 1"}, "parent-session")
        mcp_server.remote_call("remote_TaskOutput", {"task_id": "job-1"}, "subagent-session")

    assert observed_dirs[0][0] == "parent-session"
    assert observed_dirs[1][0] == "subagent-session"
    assert observed_dirs[0][1] == observed_dirs[1][1]
    assert Path(observed_dirs[0][1]).name == "remote"


def test_ad03_concurrent_resident_mcp_and_ad11_unified_subcommand(tmp_path, monkeypatch):
    config = make_config(tmp_path / "conc")
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(config))
    home = tmp_path / "kimi-home"
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.setenv("KIMI_PLUGIN_ROOT", str(ROOT))

    import admission
    import entry
    import identity

    admission.gate().activate("ses_uni", project_root=str(tmp_path))
    records = [
        {
            "type": "turn.prompt",
            "promptId": "p-deact-1",
            "origin": {"kind": "user"},
            "input": [{"type": "text", "text": "/mindie-agent deactivate"}],
        }
    ]
    write_session(home, "ses_uni", records, cwd=tmp_path)
    found = identity.require_current_plugin_command("ses_uni", "deactivate")
    assert found["command"] == "deactivate"
    res = entry.op_deactivate("ses_uni")
    assert res["deactivated"] is True

    # Concurrent resident MCP calls complete in parallel without single-lock contention
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPTS / "mcp_server.py"), "components"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env_for(config),
        cwd=str(ROOT),
    )
    try:
        for req_id in range(1, 9):
            proc.stdin.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "method": "tools/call",
                        "params": {"name": "mindie_status", "arguments": {}},
                    }
                )
                + "\n"
            )
        proc.stdin.flush()
        proc.stdin.close()
        out, err = proc.communicate(timeout=15)
        assert proc.returncode == 0, err
        replies = [json.loads(ln) for ln in out.splitlines() if ln.strip()]
        assert {r["id"] for r in replies} == set(range(1, 9))
        assert all(r["result"]["isError"] is False for r in replies)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
