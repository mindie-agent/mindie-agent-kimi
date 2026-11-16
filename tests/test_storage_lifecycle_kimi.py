"""Cross-platform storage-lifecycle regression for the 0.1.26 updater.

[Role]
Verifies silent legacy reclamation, single-runtime candidate staging,
failed-staging cleanup, external host-pin rebinding and module-size contracts.

[Invariants]
- Current, rollback, bootstrap, live-lease and externally pinned objects survive.
- A failed candidate never leaves an unpublished `launch/<sha>` behind.
- Core modules stay below 800 lines and new modules carry the contract header.

[Timeouts & Locks]
- Every fixture is bounded; the lease holder is an explicit subprocess that is
  terminated in `finally`.

[Side-Effect Policy]
- Uses only the isolated HOME/TMPDIR from `conftest.py` and temporary paths
  below `tmp_path`; no real scheduler or Kimi registry is touched.
"""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from support import SCRIPTS, make_config, write_json

sys.path.insert(0, str(SCRIPTS))

import genstate  # noqa: E402
import host_pin_refresh  # noqa: E402
import organizer  # noqa: E402
import storage_gc  # noqa: E402
import updater  # noqa: E402

OLD = time.time() - 7200
SHA = "a" * 40
CORE_MODULES = (
    "entry.py", "updater.py", "mcp_server.py", "updater_check.py",
    "updater_staging.py", "component_update.py", "organizer.py",
    "native_mcp.py", "updater_schedule.py", "storage_gc.py",
    "host_pin_refresh.py",
)


def _sha(index):
    return f"{index:040x}"


def _adapter(tmp_path):
    adapter = {"state_dir": str(tmp_path / "data" / "kimi"),
               "kimi_home": str(tmp_path / "kimi-home")}
    update = genstate.update_dir(adapter)
    for name in ("generations", "launch", "receipts", "component-runtimes"):
        (update / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return adapter


def _generation(adapter, sha, *, mtime=OLD):
    update = genstate.update_dir(adapter)
    generation = update / "generations" / sha
    generation.mkdir(parents=True)
    (generation / updater.COMPLETE).write_text(sha + "\n")
    (generation / "config").mkdir()
    (generation / "config" / "kimi.adapter.json").write_text("{}\n")
    launch = update / "launch" / sha
    launch.mkdir(parents=True, exist_ok=True)
    (launch / "mindie_launch.py").write_text("# launcher\n")
    (launch / "bounded.py").write_text("# bounded\n")
    receipt = update / "receipts" / f"{sha}.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"sha": sha}) + "\n")
    for path in (generation, launch, receipt):
        os.utime(path, (mtime, mtime))
    return generation


def _write_current(adapter, sha, generation, previous=None):
    update = genstate.update_dir(adapter)
    adapter_config = generation / "config" / "kimi.adapter.json"
    value = {"generation": str(generation), "python": sys.executable,
             "adapter_config": str(adapter_config), "sha": sha}
    write_json(update / "current.json", value)
    if previous is not None:
        write_json(update / "receipts" / f"{sha}.json", {
            "sha": sha,
            "previous": {"sha": previous[0], "generation": str(previous[1]),
                         "python": sys.executable,
                         "adapter_config": str(previous[1] / "config" / "kimi.adapter.json")},
        })
    return value


class LeaseHolder:
    def __init__(self, generation):
        code = (
            "import json, sys\n"
            "sys.path[:0] = json.loads(sys.argv[1])\n"
            "from mindie_knowledge.storage import acquire_runtime_lease\n"
            "lease = acquire_runtime_lease(sys.argv[2])\n"
            "print('held', flush=True)\n"
            "sys.stdin.readline()\n"
        )
        paths = json.dumps([item for item in sys.path if item])
        self.process = subprocess.Popen(
            [sys.executable, "-c", code, paths, str(generation)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )

    def __enter__(self):
        assert self.process.stdout is not None
        assert self.process.stdout.readline().strip() == "held"
        return self

    def __exit__(self, *_exc):
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()


def test_legacy_sweep_reclaims_field_backlog_and_keeps_rollback_bootstrap_lease(tmp_path):
    adapter = _adapter(tmp_path)
    update = genstate.update_dir(adapter)
    shas = [_sha(index) for index in range(12)]
    generations = [_generation(adapter, sha) for sha in shas]
    for index in range(12):
        component = update / "component-runtimes" / f"{index:024x}"
        component.mkdir()
        (component / "runtime").mkdir()
        (component / "source").mkdir()
        (component / "components.json").write_text(json.dumps({
            "generation_key": f"{index:024x}",
            "adapter_source_sha256": "a" * 64,
            "runtime": {},
        }) + "\n")
        os.utime(component, (OLD, OLD))
    _write_current(adapter, shas[-1], generations[-1], previous=(shas[-2], generations[-2]))
    (update / "launch" / "bootstrap").mkdir()
    (update / "launch" / "bootstrap" / "mindie_launch.py").write_text("# bootstrap\n")
    (update / "launch" / "bootstrap" / "bounded.py").write_text("# bounded\n")
    (Path(adapter["state_dir"]) / "bootstrap-venv").mkdir()
    temp_root = tmp_path / "tmp"
    temp_root.mkdir(exist_ok=True)
    dead = []
    for index in range(3):
        path = temp_root / f"mindie-kimi-summary-dead{index}"
        path.mkdir()
        (path / "config.toml").write_text("secret\n")
        os.utime(path, (OLD, OLD))
        dead.append(path)
    fresh = temp_root / "mindie-kimi-summary-fresh"
    fresh.mkdir()
    (fresh / "config.toml").write_text("active\n")

    held = generations[0]
    with LeaseHolder(held):
        os.utime(held, (OLD, OLD))
        result = storage_gc.run(
            adapter, genstate.read_current(adapter), phase="test", budget_seconds=30,
            force=True, data_home=str(tmp_path / "data"), temp_root=str(temp_root),
        )
        assert result["status"] == "completed"
        assert held.is_dir()
        assert (update / "launch" / shas[0]).is_dir()

    for generation in generations[1:-2]:
        assert not generation.exists()
    assert generations[-1].is_dir() and generations[-2].is_dir()
    assert (update / "launch" / "bootstrap").is_dir()
    assert (update / "launch" / shas[-1]).is_dir()
    assert (update / "launch" / shas[-2]).is_dir()
    assert (Path(adapter["state_dir"]) / "bootstrap-venv").is_dir()
    assert not (update / "launch" / shas[1]).exists()
    assert len(list((update / "receipts").iterdir())) <= 10
    assert (update / "receipts" / f"{shas[-1]}.json").is_file()
    assert (update / "receipts" / f"{shas[-2]}.json").is_file()
    assert len(list((update / "component-runtimes").iterdir())) <= 2
    assert all(not path.exists() for path in dead)
    assert fresh.is_dir()

    os.utime(held, (OLD, OLD))
    storage_gc.run(
        adapter, genstate.read_current(adapter), phase="test-2", budget_seconds=30,
        force=True, data_home=str(tmp_path / "data"), temp_root=str(temp_root),
    )
    assert not held.exists()
    assert not (update / "launch" / shas[0]).exists()


def _upgrade_fixture(tmp_path):
    from test_updater import make_remote

    _source, bare, sha = make_remote(tmp_path)
    config_path = make_config(tmp_path / "cfg", sharing=True, roots=[tmp_path / "cfg"])
    adapter = json.loads(config_path.read_text())
    adapter["update_remote"] = str(bare)
    adapter["kimi_home"] = str(tmp_path / "kimi-home")
    adapter["profile_config"] = str(config_path)
    write_json(config_path, adapter)
    os.environ["MINDIE_KIMI_CONFIG"] = str(config_path)
    old = tmp_path / "old-generation"
    (old / "config").mkdir(parents=True)
    old_adapter = old / "config" / "kimi.adapter.json"
    write_json(old_adapter, dict(adapter, python=sys.executable))
    genstate.write_current(
        {"generation": str(old), "python": sys.executable,
         "adapter_config": str(old_adapter), "sha": "f" * 40},
        adapter,
    )
    return adapter, bare, sha, config_path


def test_upgrade_with_current_python_skips_generation_venv_for_merged_runtime(tmp_path):
    adapter, bare, sha, _config = _upgrade_fixture(tmp_path)
    merged = tmp_path / "merged-source"
    merged.mkdir()
    merged_python = tmp_path / "merged-python"
    merged_python.write_text("")
    config = tmp_path / "merged-config.json"
    config.write_text("{}\n")
    built = []
    calls = []

    def prepare(*_args, **_kwargs):
        calls.append(_kwargs)
        return merged, merged_python, config

    def fake_build(generation, _deadline):
        built.append(generation)
        return Path(sys.executable)

    with patch("component_update.prepare", side_effect=prepare), \
            patch.object(updater, "build_host_package") as host_package:
        generation, python, adapter_config = updater.stage_generation(
            sha, str(bare), adapter, time.monotonic() + 120, build=fake_build,
            force=True,
        )
    assert (generation, python, adapter_config) == (merged, merged_python, config)
    assert built == []
    assert calls and calls[0]["force"] is True
    host_package.assert_called_once()
    assert not (genstate.generations_dir(adapter) / sha / ".venv").exists()


def test_upgrade_without_merged_runtime_builds_one_generation_venv(tmp_path):
    adapter, bare, sha, _config = _upgrade_fixture(tmp_path)
    built = []
    config = tmp_path / "generation-config.json"
    config.write_text("{}\n")

    def fake_build(generation, _deadline):
        built.append(generation)
        (generation / ".venv").mkdir()
        return Path(sys.executable)

    with patch("component_update.prepare", return_value=None), \
            patch.object(updater, "probe_runtime"), \
            patch.object(updater, "write_generation_configs", return_value=config), \
            patch.object(updater, "build_host_package"):
        generation, python, adapter_config = updater.stage_generation(
            sha, str(bare), adapter, time.monotonic() + 120, build=fake_build,
        )
    assert generation == genstate.generations_dir(adapter) / sha
    assert python == Path(sys.executable) and adapter_config == config
    assert built == [generation]
    assert (generation / updater.COMPLETE).is_file()


def test_failed_staging_removes_orphan_launch_and_generation(tmp_path):
    adapter, bare, sha, _config = _upgrade_fixture(tmp_path)
    config = tmp_path / "generation-config.json"
    config.write_text("{}\n")

    def fake_build(generation, _deadline):
        return Path(sys.executable)

    def fail_host_package(generation, adapter_value, sha_value, config_file=None):
        launch = genstate.launch_dir(adapter_value) / sha_value
        launch.mkdir(parents=True)
        (launch / "mindie_launch.py").write_text("# partial\n")
        raise OSError("host package build failed")

    with patch("component_update.prepare", return_value=None), \
            patch.object(updater, "probe_runtime"), \
            patch.object(updater, "write_generation_configs", return_value=config), \
            patch.object(updater, "build_host_package", side_effect=fail_host_package), \
            pytest.raises(OSError, match="host package build failed"):
        updater.stage_generation(
            sha, str(bare), adapter, time.monotonic() + 120, build=fake_build,
        )
    assert not (genstate.generations_dir(adapter) / sha).exists()
    assert not (genstate.launch_dir(adapter) / sha).exists()


def test_component_switch_rebinds_host_pins_and_next_gc_reclaims_old_generation(tmp_path):
    config_path = make_config(tmp_path / "cfg", sharing=True, roots=[tmp_path / "cfg"])
    adapter = json.loads(config_path.read_text())
    adapter["kimi_home"] = str(tmp_path / "kimi-home")
    adapter["profile_config"] = str(config_path)
    state = tmp_path / "data" / "kimi"
    adapter["state_dir"] = str(state)
    write_json(config_path, adapter)
    os.environ["MINDIE_KIMI_CONFIG"] = str(config_path)
    update = genstate.update_dir(adapter)
    for name in ("generations", "launch", "receipts"):
        (update / name).mkdir(parents=True, exist_ok=True)

    stale, rollback, current = _sha(1), _sha(2), _sha(3)
    stale_gen = _generation(adapter, stale)
    rollback_gen = _generation(adapter, rollback)
    current_gen = _generation(adapter, current)
    (current_gen / "host-package").mkdir()
    (current_gen / "host-package" / "kimi.plugin.json").write_text("{}\n")
    current_value = _write_current(
        adapter, current, current_gen, previous=(rollback, rollback_gen),
    )
    previous_value = {
        "sha": stale, "python": str(tmp_path / "old-python"),
        "generation": str(stale_gen),
        "adapter_config": str(stale_gen / "config" / "kimi.adapter.json"),
    }
    old_python = tmp_path / "old-python"
    old_python.write_text("")
    old_launcher = str(update / "launch" / stale / "mindie_launch.py")
    new_launcher = str(update / "launch" / current / "mindie_launch.py")

    plist = Path.home() / "Library" / "LaunchAgents" / "agent.mindie.kimi-update.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({
        "Label": "agent.mindie.kimi-update",
        "ProgramArguments": [str(old_python), old_launcher, "--config",
                             str(stale_gen / "config" / "kimi.adapter.json"),
                             "updater", "check"],
        "StartInterval": 300,
    }))

    managed = tmp_path / "kimi-home" / "plugins" / "managed" / "mindie-agent"
    managed.mkdir(parents=True)
    (managed / "mindie-front").write_text(
        "#!/bin/sh\nexec " + str(old_python) + ' "$@"\n')
    write_json(managed / "kimi.plugin.json", {
        "name": "mindie-agent", "version": "0.1.26",
        "mcpServers": {"remote": {
            "command": "./mindie-front",
            "args": [old_launcher, "--config",
                     str(stale_gen / "config" / "kimi.adapter.json"), "mcp", "remote"],
        }},
        "hooks": [{"event": "PreToolUse",
                   "command": f'"{old_python}" "{old_launcher}" "--config" '
                              f'"{stale_gen / "config" / "kimi.adapter.json"}" hook pretool'}],
    })
    installed = tmp_path / "kimi-home" / "plugins" / "installed.json"
    write_json(installed, {"version": 1, "plugins": [{
        "id": "mindie-agent",
        "originalSource": str(stale_gen / "host-package"),
    }]})

    result = host_pin_refresh.refresh_after_switch(
        adapter, previous_value, current_value, time.monotonic() + 30,
    )
    assert result["status"] == "ok"
    assert "launchd-plist" in result["refreshed"]
    assert "managed-front" in result["refreshed"]
    assert "managed-manifest" in result["refreshed"]
    assert "native-record" in result["refreshed"]
    assert plistlib.loads(plist.read_bytes())["ProgramArguments"] == [
        sys.executable, new_launcher, "--config", str(config_path), "updater", "check"]
    assert str(genstate.read_current(adapter)["python"]) in (
        managed / "mindie-front").read_text()
    manifest = json.loads((managed / "kimi.plugin.json").read_text())
    assert manifest["mcpServers"]["remote"]["args"][0] == new_launcher
    assert manifest["mcpServers"]["remote"]["args"][2] == str(
        current_gen / "config" / "kimi.adapter.json")
    assert json.loads(installed.read_text())["plugins"][0]["originalSource"] == str(
        current_gen / "host-package")

    filler = _generation(adapter, _sha(4), mtime=time.time())
    os.utime(stale_gen, (OLD, OLD))
    os.utime(update / "launch" / stale, (OLD, OLD))
    storage_gc.run(
        adapter, genstate.read_current(adapter), phase="test", budget_seconds=30,
        force=True, data_home=str(tmp_path / "data"), temp_root=str(tmp_path / "tmp"),
    )
    assert not stale_gen.exists()
    assert not (update / "launch" / stale).exists()
    assert current_gen.is_dir() and rollback_gen.is_dir() and filler.is_dir()


def test_summary_scratch_uses_owner_marker_and_deterministic_cleanup(tmp_path):
    captured = {}
    original = organizer.summary_scratch.create_root

    def create_root():
        root = original()
        captured["root"] = root
        assert (root / organizer.summary_scratch.OWNER_MARKER).is_file()
        return root

    def run(*_args, **_kwargs):
        assert (captured["root"] / organizer.summary_scratch.OWNER_MARKER).is_file()
        return '{"ok": true}'

    receipt = {}
    with patch.object(organizer.summary_scratch, "create_root", side_effect=create_root), \
            patch.object(organizer, "prepare_isolated_home", side_effect=lambda base: base), \
            patch.object(organizer, "kimi_bin", return_value=sys.executable), \
            patch.object(organizer.bounded, "run", side_effect=run):
        organizer._invoke_native("material", receipt)
    assert receipt["final_message"] == '{"ok": true}'
    assert not captured["root"].exists()


def test_binding_only_component_change_keeps_the_candidate_generation_venv(tmp_path):
    adapter, bare, sha, _config = _upgrade_fixture(tmp_path)
    candidate_python = tmp_path / "candidate-python"
    candidate_python.write_text("")
    config = tmp_path / "candidate-config.json"
    config.write_text("{}\n")
    built = []

    def fake_build(generation, _deadline):
        built.append(generation)
        return candidate_python

    def prepare(_adapter, source, _python, _deadline, *, force=False):
        # Binding-only: the source generation stays the generation even when
        # the caller passes a fresh candidate interpreter.
        return Path(source), Path(sys.executable), config

    with patch("component_update.prepare", side_effect=prepare), \
            patch.object(updater, "probe_runtime"), \
            patch.object(updater, "build_host_package"):
        generation, python, adapter_config = updater.stage_generation(
            sha, str(bare), adapter, time.monotonic() + 120, build=fake_build,
        )
    assert generation == genstate.generations_dir(adapter) / sha
    assert python == candidate_python
    assert adapter_config == config
    assert built == [generation]
    assert (generation / updater.COMPLETE).is_file()


def test_committed_switch_rebinds_an_installed_plist(tmp_path):
    config_path = make_config(tmp_path / "cfg", sharing=True, roots=[tmp_path / "cfg"])
    adapter = json.loads(config_path.read_text())
    adapter["kimi_home"] = str(tmp_path / "kimi-home")
    adapter["profile_config"] = str(config_path)
    adapter["state_dir"] = str(tmp_path / "data" / "kimi")
    write_json(config_path, adapter)
    os.environ["MINDIE_KIMI_CONFIG"] = str(config_path)
    update = genstate.update_dir(adapter)
    old, new = _sha(8), _sha(9)
    old_gen = _generation(adapter, old)
    new_gen = _generation(adapter, new)
    (new_gen / "host-package").mkdir()
    (new_gen / "host-package" / "kimi.plugin.json").write_text("{}\n")
    old_value = {"generation": str(old_gen), "python": sys.executable,
                 "adapter_config": str(old_gen / "config" / "kimi.adapter.json"),
                 "sha": old}
    write_json(update / "current.json", old_value)
    plist = Path.home() / "Library" / "LaunchAgents" / "agent.mindie.kimi-update.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({
        "Label": "agent.mindie.kimi-update",
        "ProgramArguments": [sys.executable,
                             str(update / "launch" / old / "mindie_launch.py"),
                             "--config", str(old_gen / "config" / "kimi.adapter.json"),
                             "updater", "check"],
        "StartInterval": 300,
    }))
    new_adapter = new_gen / "config" / "kimi.adapter.json"
    updater.switch(
        adapter, old_value, new_gen, Path(sys.executable), new_adapter, new,
        time.monotonic() + 180, lambda *_: {"service": "absent"}, Mock(), 1,
        update_native=False,
    )
    assert genstate.read_current(adapter)["sha"] == new
    assert plistlib.loads(plist.read_bytes())["ProgramArguments"] == [
        sys.executable, str(update / "launch" / new / "mindie_launch.py"),
        "--config", str(config_path), "updater", "check"]


def test_core_modules_stay_below_eight_hundred_lines():
    for name in CORE_MODULES:
        path = SCRIPTS / name
        assert path.is_file(), name
        assert len(path.read_text(encoding="utf-8").splitlines()) < 800, name


def test_new_lifecycle_modules_declare_contract_header():
    for name in ("storage_gc.py", "host_pin_refresh.py"):
        head = "\n".join((SCRIPTS / name).read_text(encoding="utf-8").splitlines()[:30])
        for marker in ("[Role]", "[Invariants]", "[Timeouts & Locks]",
                       "[Side-Effect Policy]"):
            assert marker in head, (name, marker)
