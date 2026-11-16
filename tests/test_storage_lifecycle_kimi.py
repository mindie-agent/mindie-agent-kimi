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

import component_update  # noqa: E402
import capture_config  # noqa: E402
import genstate  # noqa: E402
import host_pin_refresh  # noqa: E402
import organizer  # noqa: E402
import remote_state_guard  # noqa: E402
import storage_gc  # noqa: E402
import updater  # noqa: E402
import updater_staging  # noqa: E402
from mindie_knowledge.storage import is_lease_held  # noqa: E402

OLD = time.time() - 7200
SHA = "a" * 40
CORE_MODULES = (
    "entry.py", "updater.py", "mcp_server.py", "updater_check.py",
    "updater_staging.py", "component_update.py", "organizer.py",
    "native_mcp.py", "updater_schedule.py", "storage_gc.py",
    "host_pin_refresh.py", "remote_state_guard.py",
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

    def fake_build(generation, _deadline, **_kwargs):
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

    def fake_build(generation, _deadline, **_kwargs):
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

    def fake_build(generation, _deadline, **_kwargs):
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

    pins = host_pin_refresh.scheduled_pins(adapter)
    assert str(old_python) in pins
    assert old_launcher in pins
    filler = _generation(adapter, _sha(4), mtime=time.time())
    os.utime(stale_gen, (OLD, OLD))
    os.utime(update / "launch" / stale, (OLD, OLD))
    storage_gc.run(
        adapter, genstate.read_current(adapter), phase="test", budget_seconds=30,
        force=True, data_home=str(tmp_path / "data"), temp_root=str(tmp_path / "tmp"),
    )
    # The replaced scheduler command is still loaded: both its interpreter's
    # generation and its launcher stay protected.
    assert stale_gen.is_dir()
    assert (update / "launch" / stale).is_dir()
    assert current_gen.is_dir() and rollback_gen.is_dir() and filler.is_dir()

    host_pin_refresh.remember_scheduled_pins(
        adapter, [str(tmp_path / "newer-a"), str(tmp_path / "newer-b")])
    pins = host_pin_refresh.scheduled_pins(adapter)
    # The first replaced command is the permanent anchor; newer replacements
    # only fill the bounded recent ring and can never evict it.
    assert pins[:2] == [str(old_python), old_launcher]
    assert str(tmp_path / "newer-a") in pins
    assert str(tmp_path / "newer-b") in pins
    os.utime(stale_gen, (OLD, OLD))
    os.utime(update / "launch" / stale, (OLD, OLD))
    storage_gc.run(
        adapter, genstate.read_current(adapter), phase="test-2", budget_seconds=30,
        force=True, data_home=str(tmp_path / "data"), temp_root=str(tmp_path / "tmp"),
    )
    assert stale_gen.is_dir()
    assert (update / "launch" / stale).is_dir()
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

    def fake_build(generation, _deadline, **_kwargs):
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


def _pin(name, revision):
    repository = "mindie-agent/" + name.removeprefix("mindie-")
    return f"{name} @ git+https://github.com/{repository}@{revision}\n"


_KNOWLEDGE = "mindie-knowledge"


def _knowledge_target(revision):
    return {
        "repository": "mindie-agent/knowledge",
        "version": "0.14.0",
        "revision": revision,
    }


def test_candidate_build_pins_the_installed_component_floor_in_one_pass(tmp_path):
    adapter, bare, sha, _config = _upgrade_fixture(tmp_path)
    target = genstate.generations_dir(adapter) / sha
    (target / "runtime").mkdir(parents=True)
    (target / "runtime-requirements.txt").write_text(_pin(_KNOWLEDGE, "a" * 40))
    (target / "runtime" / "active-overrides.txt").write_text(_pin(_KNOWLEDGE, "a" * 40))
    (target / updater_staging.SOURCE).write_text(sha + "\n")
    config = tmp_path / "candidate-config.json"
    config.write_text("{}\n")
    builds = []

    def build(generation, _deadline, *, component_overrides=None):
        builds.append((generation, component_overrides))
        (generation / ".venv").mkdir()
        return Path(sys.executable)

    installed = {_KNOWLEDGE: _knowledge_target("b" * 40)}
    with patch("mindie_knowledge.component_runtime.read_inventory", return_value=installed), \
            patch("component_update.prepare", return_value=None), \
            patch.object(updater, "probe_runtime"), \
            patch.object(updater, "write_generation_configs", return_value=config), \
            patch.object(updater, "build_host_package"):
        generation, python, adapter_config = updater.stage_generation(
            sha, str(bare), adapter, time.monotonic() + 120, build=build)
    assert generation == target
    assert len(builds) == 1
    assert builds[0][1] == {_KNOWLEDGE: "b" * 40}
    assert python == Path(sys.executable)
    assert adapter_config == config
    assert (target / updater.COMPLETE).is_file()


def test_prepare_none_binds_candidate_components_and_switch_succeeds(tmp_path):
    adapter, bare, sha, config_path = _upgrade_fixture(tmp_path)
    update = genstate.update_dir(adapter)
    target = update / "generations" / sha
    (target / "runtime").mkdir(parents=True)
    (target / "runtime-requirements.txt").write_text(_pin("mindie-profiler", "a" * 40))
    (target / "runtime" / "active-overrides.txt").write_text(
        _pin("mindie-profiler", "a" * 40))
    (target / updater_staging.SOURCE).write_text(sha + "\n")
    candidate_python = target / ".venv" / "bin" / "python"
    candidate_python.parent.mkdir(parents=True)
    candidate_python.write_text("")

    active = genstate.read_current(adapter)
    old_python = tmp_path / "old-python"
    old_python.write_text("")
    # The active generation was itself a component switch: its config still
    # records the pre-rebind source snapshot while the live profile already
    # publishes the bound commands. Copying the stale source snapshot into the
    # plugin candidate makes the switch CAS defer forever.
    old_bound = {"profiler": [
        sys.executable, "-I", "-X", "utf8", "-m", "mindie_profiler"]}
    old_source = {"profiler": [
        str(old_python), "-I", "-X", "utf8", "-m", "mindie_profiler"]}
    active_adapter = json.loads(Path(active["adapter_config"]).read_text())
    active_adapter.update(
        component_commands=old_bound, component_source_commands=old_source)
    write_json(Path(active["adapter_config"]), active_adapter)
    profile = json.loads(config_path.read_text())
    profile.update(
        component_commands=old_bound, component_source_commands=old_source)
    write_json(config_path, profile)
    installed = {"mindie-profiler": {
        "repository": "mindie-agent/profiler",
        "version": "1.0.0",
        "revision": "b" * 40,
    }}
    built = []

    def build(generation, _deadline, *, component_overrides=None):
        built.append(component_overrides)
        return candidate_python

    with patch("mindie_knowledge.component_runtime.read_inventory",
               return_value=installed), \
            patch("mindie_knowledge.component_bindings.required_components",
                  return_value={}), \
            patch("component_update.prepare", return_value=None), \
            patch.object(updater, "probe_runtime"), \
            patch("capture_config.prepare",
                  return_value={"capture_mode": "public-transcript"}), \
            patch("capture_config.prepare_domains", return_value={}), \
            patch.object(updater, "build_host_package"):
        generation, python, gen_adapter = updater.stage_generation(
            sha, str(bare), adapter, time.monotonic() + 120, build=build)
        candidate = json.loads(Path(gen_adapter).read_text())
        published = {"profiler": [
            str(candidate_python), "-I", "-X", "utf8", "-m", "mindie_profiler"]}
        # `prepare()` proved the active interpreter already meets the floor;
        # the candidate config must still bind its own commands and keep the
        # live profile snapshot for the switch CAS.
        assert built == [{"mindie-profiler": "b" * 40}]
        assert candidate["component_source_commands"] == old_bound
        assert candidate["component_source_commands"] != old_source
        assert candidate["component_commands"] == published
        assert candidate["component_commands"] != old_bound

        updater.switch(
            adapter, active, generation, Path(python), gen_adapter, sha,
            time.monotonic() + 120,
            lambda *_: {"service": "absent"}, Mock(), 5, update_native=False,
        )
    assert genstate.read_current(adapter)["sha"] == sha
    assert json.loads(config_path.read_text())["component_commands"] == published


def test_candidate_overrides_only_return_a_differing_floor(tmp_path):
    source = tmp_path / "source"
    (source / "runtime").mkdir(parents=True)
    (source / "runtime-requirements.txt").write_text(_pin(_KNOWLEDGE, "a" * 40))
    (source / "runtime" / "active-overrides.txt").write_text(_pin(_KNOWLEDGE, "a" * 40))
    installed = {_KNOWLEDGE: _knowledge_target("a" * 40)}
    with patch("mindie_knowledge.component_runtime.read_inventory", return_value=installed):
        assert component_update.candidate_overrides(source, sys.executable) is None
    installed[_KNOWLEDGE] = _knowledge_target("b" * 40)
    with patch("mindie_knowledge.component_runtime.read_inventory", return_value=installed):
        assert component_update.candidate_overrides(source, sys.executable) == {
            _KNOWLEDGE: "b" * 40}


def test_candidate_runtime_build_installs_and_verifies_the_merged_floor(tmp_path):
    generation = tmp_path / "generation"
    generation.mkdir()
    (generation / "runtime-requirements.txt").write_text(_pin(_KNOWLEDGE, "a" * 40))
    seen = {}
    calls = []

    def run(argv, stdin="", **kwargs):
        calls.append(list(argv))
        if "venv" in argv:
            venv = Path(argv[-1])
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python").write_text("")
            return ""
        if "install" in argv:
            overlay = Path(argv[argv.index("--overrides") + 1])
            seen["overlay"] = overlay.read_text()
            seen["exists_during_install"] = overlay.is_file()
        return ""

    installed = {_KNOWLEDGE: _knowledge_target("b" * 40)}
    with patch.object(updater.shutil, "which", return_value="/usr/bin/uv"), \
            patch.object(updater, "bounded_run", side_effect=run), \
            patch("mindie_knowledge.component_runtime.read_inventory",
                  return_value=installed) as read:
        python = updater.build_runtime(
            generation, time.monotonic() + 120,
            component_overrides={_KNOWLEDGE: "b" * 40})
    install = next(argv for argv in calls if "install" in argv)
    overlay_arg = Path(install[install.index("--overrides") + 1])
    assert overlay_arg.name.startswith(".component-overrides-")
    assert install[install.index("--overrides") + 1] == str(overlay_arg)
    assert install.count("-r") == 2 and str(overlay_arg) in install
    assert seen["exists_during_install"] is True
    assert seen["overlay"] == _pin(_KNOWLEDGE, "b" * 40)
    assert not overlay_arg.exists()
    assert read.call_args.args[0] == python
    assert python == generation / ".venv" / "bin" / "python"


def test_scheduled_pins_keep_the_anchor_and_a_bounded_replacement_ring(tmp_path):
    adapter = _adapter(tmp_path)
    update = genstate.update_dir(adapter)
    pairs = []
    for index in range(12):
        sha = _sha(100 + index)
        generation = update / "generations" / sha
        generation.mkdir(parents=True)
        python = generation / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("")
        launcher = update / "launch" / sha / "mindie_launch.py"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("# launcher\n")
        pairs.append((str(python), str(launcher)))
    for pair in pairs:
        host_pin_refresh.remember_scheduled_pins(adapter, list(pair))
    pins = host_pin_refresh.scheduled_pins(adapter)
    assert pins[:2] == list(pairs[0])
    assert pairs[-1][0] in pins and pairs[-1][1] in pins
    assert len(pins) <= 2 + 2 * host_pin_refresh.MAX_REPLACED_RECORDS
    stored = json.loads((update / "scheduled-pins.json").read_text())
    assert stored["schema"] == host_pin_refresh.SCHEDULED_PINS_SCHEMA
    assert stored["anchor"]["python"] == pairs[0][0]
    assert stored["anchor"]["launcher"] == pairs[0][1]
    assert len(stored["records"]) == host_pin_refresh.MAX_REPLACED_RECORDS
    assert len(json.dumps(stored)) < 4096


def test_scheduled_pins_migrate_the_legacy_flat_ring_as_anchor(tmp_path):
    adapter = _adapter(tmp_path)
    update = genstate.update_dir(adapter)
    legacy_sha = _sha(130)
    legacy = _generation(adapter, legacy_sha)
    legacy_python = legacy / ".venv" / "bin" / "python"
    legacy_python.parent.mkdir(parents=True)
    legacy_python.write_text("")
    legacy_launcher = update / "launch" / legacy_sha / "mindie_launch.py"
    write_json(update / "scheduled-pins.json", {
        "schema": host_pin_refresh.LEGACY_SCHEDULED_PINS_SCHEMA,
        "pins": [str(legacy_python), str(legacy_launcher)],
        "updated_at": 1.0,
    })
    assert host_pin_refresh.scheduled_pins(adapter) == [
        str(legacy_python), str(legacy_launcher)]
    fresh = tmp_path / "python-fresh"
    fresh.write_text("")
    host_pin_refresh.remember_scheduled_pins(adapter, [str(fresh)])
    pins = host_pin_refresh.scheduled_pins(adapter)
    assert pins[:2] == [str(legacy_python), str(legacy_launcher)]
    assert str(fresh) in pins
    stored = json.loads((update / "scheduled-pins.json").read_text())
    assert stored["anchor"]["python"] == str(legacy_python)
    assert stored["anchor"]["launcher"] == str(legacy_launcher)


def test_storage_gc_strips_windows_verbatim_prefixes():
    assert storage_gc._strip_verbatim(
        "\\\\?\\C:\\Users\\mindie\\runtime", windows=True) == "C:\\Users\\mindie\\runtime"
    assert storage_gc._strip_verbatim(
        "\\\\?\\UNC\\server\\share\\runtime",
        windows=True) == "\\\\server\\share\\runtime"
    assert storage_gc._strip_verbatim(
        "\\\\?\\C:\\runtime", windows=False) == "\\\\?\\C:\\runtime"


def test_keep_paths_deduplicates_equivalent_host_spellings(tmp_path):
    adapter = _adapter(tmp_path)
    sha = _sha(139)
    generation = _generation(adapter, sha)
    python = generation / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    current = _write_current(adapter, sha, generation)
    current["python"] = str(python)
    host_pin_refresh.remember_scheduled_pins(adapter, [str(python) + os.sep])
    kept = [str(path) for path in storage_gc.keep_paths(adapter, current)]
    assert kept.count(str(python)) == 1


def test_three_upgrades_without_scheduler_reload_keep_original_and_latest_runtime(tmp_path):
    adapter = _adapter(tmp_path)
    update = genstate.update_dir(adapter)
    shas = [_sha(140 + index) for index in range(4)]
    generations = [_generation(adapter, sha) for sha in shas]
    pythons = []
    for generation in generations:
        python = generation / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("")
        pythons.append(python)
    values = [
        {"generation": str(generation), "python": str(python),
         "adapter_config": str(generation / "config" / "kimi.adapter.json"),
         "sha": sha}
        for sha, generation, python in zip(shas, generations, pythons)
    ]
    plist = Path.home() / "Library" / "LaunchAgents" / "agent.mindie.kimi-update.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({
        "Label": "agent.mindie.kimi-update",
        "ProgramArguments": [str(pythons[0]),
                             str(update / "launch" / shas[0] / "mindie_launch.py"),
                             "--config", values[0]["adapter_config"],
                             "updater", "check"],
        "StartInterval": 300,
    }))
    for index in (1, 2, 3):
        genstate.write_current(values[index], adapter)
        result = host_pin_refresh.refresh_after_switch(
            adapter, values[index - 1], values[index], time.monotonic() + 30)
        assert result["status"] == "ok", result
        # No `launchctl` runs here: the on-disk plist changes every upgrade
        # while launchd still dispatches the command it loaded at boot.
        assert str(pythons[0]) in host_pin_refresh.scheduled_pins(adapter)

    keep = {str(path) for path in storage_gc.keep_paths(adapter, values[3])}
    assert str(generations[0]) in keep
    assert str(update / "launch" / shas[0]) in keep
    assert str(generations[2]) in keep
    assert str(update / "launch" / shas[2]) in keep
    assert str(generations[3]) in keep
    stored = json.loads((update / "scheduled-pins.json").read_text())
    assert stored["anchor"]["python"] == str(pythons[0])
    assert stored["anchor"]["launcher"] == str(
        update / "launch" / shas[0] / "mindie_launch.py")
    latest = [slot for slot in stored["records"]
              if slot["python"] == str(pythons[2])]
    assert latest and latest[0]["launcher"] == str(
        update / "launch" / shas[2] / "mindie_launch.py")

    for path in (generations[0], update / "launch" / shas[0],
                 generations[1], update / "launch" / shas[1]):
        os.utime(path, (OLD, OLD))
    extra = _generation(adapter, _sha(199))
    os.utime(extra, (OLD, OLD))
    write_json(update / "receipts" / f"{shas[3]}.json", {
        "sha": shas[3], "previous": values[2]})
    storage_gc.run(
        adapter, genstate.read_current(adapter), phase="test", budget_seconds=30,
        force=True, data_home=str(tmp_path / "data"), temp_root=str(tmp_path / "tmp"),
    )
    # The anchor (first replaced command) and the current runtime survive;
    # only the unprotected legacy generation is reclaimed.
    assert generations[0].is_dir()
    assert (update / "launch" / shas[0]).is_dir()
    assert generations[2].is_dir()
    assert (update / "launch" / shas[2]).is_dir()
    assert generations[3].is_dir()
    assert (update / "launch" / shas[3]).is_dir()
    assert not extra.exists()


def test_scheduled_pins_keep_replaced_generation_and_launcher(tmp_path):
    adapter = _adapter(tmp_path)
    update = genstate.update_dir(adapter)
    old, new, rollback = _sha(10), _sha(11), _sha(12)
    stale = _generation(adapter, old)
    current = _generation(adapter, new)
    previous = _generation(adapter, rollback)
    _write_current(adapter, new, current, previous=(rollback, previous))
    stale_python = stale / ".venv" / "bin" / "python"
    stale_python.parent.mkdir(parents=True)
    stale_python.write_text("")
    stale_launcher = update / "launch" / old / "mindie_launch.py"
    host_pin_refresh.remember_scheduled_pins(
        adapter, [str(stale_python), str(stale_launcher)])
    for path in (stale, update / "launch" / old, stale_launcher):
        os.utime(path, (OLD, OLD))
    storage_gc.run(
        adapter, genstate.read_current(adapter), phase="test", budget_seconds=30,
        force=True, data_home=str(tmp_path / "data"), temp_root=str(tmp_path / "tmp"),
    )
    assert stale.is_dir()
    assert (update / "launch" / old).is_dir()
    assert current.is_dir() and previous.is_dir()


def test_storage_gc_holds_leases_and_keep_paths_for_scheduler_runtimes(tmp_path):
    adapter = _adapter(tmp_path)
    update = genstate.update_dir(adapter)
    active_sha, rollback_sha, scheduled_sha = _sha(13), _sha(14), _sha(15)
    active = _generation(adapter, active_sha)
    rollback = _generation(adapter, rollback_sha)
    scheduled = _generation(adapter, scheduled_sha)
    _write_current(adapter, active_sha, active, previous=(rollback_sha, rollback))
    scheduled_python = scheduled / ".venv" / "bin" / "python"
    scheduled_python.parent.mkdir(parents=True)
    scheduled_python.write_text("")
    host_pin_refresh.remember_scheduled_pins(adapter, [str(scheduled_python)])
    observed = {}

    def legacy(**_kwargs):
        observed["leases"] = {
            name: is_lease_held(path) for name, path in (
                ("active", active), ("rollback", rollback), ("scheduled", scheduled))
        }
        return {"status": "completed"}

    def component(root, *, keep_paths, **_kwargs):
        observed["keep"] = {str(path) for path in keep_paths}
        observed["root"] = root
        return {"status": "completed"}

    with patch("mindie_knowledge.storage.sweep_legacy_installations", side_effect=legacy), \
            patch("mindie_knowledge.storage.prune_component_runtimes", side_effect=component):
        result = storage_gc.run(
            adapter, genstate.read_current(adapter), phase="test", budget_seconds=30,
            force=True, data_home=str(tmp_path / "data"), temp_root=str(tmp_path / "tmp"),
        )
    assert result["status"] == "completed"
    assert observed["leases"] == {"active": True, "rollback": True, "scheduled": True}
    assert str(scheduled) in observed["keep"]
    assert str(update / "launch" / scheduled_sha) in observed["keep"]


def _dead_pid():
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=10)
    return process.pid


def _remote_session(root, name, records, *, files=None):
    path = root / name
    path.mkdir(parents=True)
    (path / "pid").write_text(str(_dead_pid()) + "\n")
    for filename, payload in records.items():
        target = path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    for filename in files or ():
        (path / filename).write_text("")
    os.utime(path, (OLD, OLD))
    return path


def test_guarded_session_retention_defers_unresolved_remote_records(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    running = _remote_session(root, "runtime-running", {
        "job.json": {"schema_version": "remote-dev.job.v1", "state": "running"}})
    pre_reply = _remote_session(root, "session-prereply", {
        "job.json": {"schema_version": "remote-dev.job.v1",
                     "authorization": {"token": "tok", "job_id": "job-1"}}})
    uncertain = _remote_session(root, "gate-v1-uncertain", {
        "result.json": {"state": "succeeded", "submission_state": "uncertain"}})
    nested_running = _remote_session(root, "runtime-nested-result-running", {
        "result.json": {"result": {"state": "running"}}})
    submitting = _remote_session(root, "runtime-submitting", {
        "result.json": {"state": "succeeded", "submission_state": "submitting"}})
    terminal = _remote_session(root, "session-terminal", {
        "job.json": {"schema_version": "remote-dev.job.v1", "state": "succeeded"}})
    acknowledged = _remote_session(root, "gate-v1-acknowledged", {
        "result.json": {"state": "failed", "submission_state": "acknowledged"}})

    report = remote_state_guard.guarded_session_retention(
        root, now=time.time(), max_age_seconds=60)
    assert report["status"] == "partial"
    assert set(report["removed"]) == {terminal.name, acknowledged.name}
    for path in (running, pre_reply, uncertain, nested_running, submitting):
        assert path.is_dir()
    assert report["skipped"]["non_terminal_state"] == 2
    assert report["skipped"]["pre_reply_job"] == 1
    assert report["skipped"]["unresolved_submission"] == 2


def test_unresolved_state_inspects_nested_records_and_null_submission(tmp_path):
    def state_for(name, payload):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "record.json").write_text(json.dumps(payload))
        return remote_state_guard.unresolved_state(directory)

    assert state_for("nested-running", {
        "result": {"state": "running"}})["reason"] == "non_terminal_state"
    assert state_for("nested-list-running", [
        {"job": [{"state": "running"}]}])["reason"] == "non_terminal_state"
    assert state_for("nested-uncertain", {
        "payload": {"submission_state": "uncertain"}})["reason"] == "unresolved_submission"
    for index, value in enumerate((None, "", 7, ["acknowledged"], "unknown")):
        assert state_for(f"null-submission-{index}", {
            "submission_state": value})["reason"] == "unresolved_submission"
    assert state_for("nested-terminal", {
        "result": {"state": "succeeded"},
        "payload": {"submission_state": "acknowledged"}})["unresolved"] is False
    # `authorization.state` is request metadata, never an execution outcome.
    assert state_for("authorization-state", {
        "schema_version": "remote-dev.job.v1",
        "state": "succeeded",
        "authorization": {"state": "running", "job_id": "job-1"},
    })["unresolved"] is False
    deep = {"state": "succeeded"}
    for _ in range(14):
        deep = {"result": deep}
    assert state_for("too-deep", deep)["reason"] == "record_too_deep"


def test_unresolved_state_defers_incomplete_oversized_and_invalid_records(tmp_path):
    crowded = tmp_path / "crowded"
    crowded.mkdir()
    for index in range(401):
        (crowded / f"entry-{index}.txt").write_text("")
    assert remote_state_guard.unresolved_state(crowded)["reason"] == "incomplete_walk"

    oversized = tmp_path / "oversized"
    oversized.mkdir()
    (oversized / "job.json").write_text("x" * (512 * 1024 + 1))
    assert remote_state_guard.unresolved_state(oversized)["reason"] == "oversized_record"

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "job.jsonl").write_text('{"state": "succeeded"}\n{not json\n')
    assert remote_state_guard.unresolved_state(invalid)["reason"] == "invalid_record"

    null_job = tmp_path / "null-job"
    null_job.mkdir()
    (null_job / "job.json").write_text(
        '{"schema_version": "remote-dev.job.v1", "state": null}')
    assert remote_state_guard.unresolved_state(null_job)["reason"] == "pre_reply_job"

    assert remote_state_guard.unresolved_state(
        tmp_path / "absent")["reason"] == "unreadable_directory"
    assert remote_state_guard.unresolved_state(tmp_path / "invalid")["unresolved"] is True


def test_guarded_endpoint_retention_skips_an_unresolved_submission(tmp_path):
    endpoint = tmp_path / "endpoint"
    (endpoint / "jobs").mkdir(parents=True)
    record = endpoint / "jobs" / "job-abc.json"
    record.write_text(json.dumps({
        "schema_version": "remote-dev.job.v1",
        "state": "succeeded",
        "submission_state": "uncertain",
    }))
    outcome = remote_state_guard.guarded_endpoint_retention(endpoint)
    assert outcome["status"] == "deferred"
    assert record.is_file()
    assert remote_state_guard.has_unresolved_state(endpoint) is True


def test_storage_gc_remote_phase_defers_unresolved_session_and_endpoint_state(tmp_path):
    adapter = _adapter(tmp_path)
    current_sha = _sha(20)
    current = _generation(adapter, current_sha)
    _write_current(adapter, current_sha, current)
    session = Path(adapter["state_dir"]) / "remote" / "session-a"
    unresolved = _remote_session(session, "runtime-live", {
        "job.json": {"schema_version": "remote-dev.job.v1", "state": "unknown"}})
    terminal = _remote_session(session, "runtime-done", {
        "job.json": {"schema_version": "remote-dev.job.v1", "state": "succeeded"}})
    expired = time.time() - 3 * 24 * 60 * 60
    for path in (unresolved, terminal):
        os.utime(path, (expired, expired))
    endpoint = session / "endpoints" / "id-a"
    (endpoint / "jobs").mkdir(parents=True)
    endpoint_job = endpoint / "jobs" / "job-a.json"
    endpoint_job.write_text(json.dumps({
        "schema_version": "remote-dev.job.v1", "state": "running"}))

    result = storage_gc.run(
        adapter, genstate.read_current(adapter), phase="test", budget_seconds=30,
        force=True, data_home=str(tmp_path / "data"), temp_root=str(tmp_path / "tmp"),
    )
    assert unresolved.is_dir()
    assert not terminal.exists()
    assert endpoint_job.is_file()
    assert result["reports"]["remote"]["deferred_dirs"] >= 1
    assert result["status"] == "partial"


def test_core_modules_stay_below_eight_hundred_lines():
    for name in CORE_MODULES:
        path = SCRIPTS / name
        assert path.is_file(), name
        assert len(path.read_text(encoding="utf-8").splitlines()) < 800, name


def test_new_lifecycle_modules_declare_contract_header():
    for name in ("storage_gc.py", "host_pin_refresh.py", "remote_state_guard.py"):
        head = "\n".join((SCRIPTS / name).read_text(encoding="utf-8").splitlines()[:30])
        for marker in ("[Role]", "[Invariants]", "[Timeouts & Locks]",
                       "[Side-Effect Policy]"):
            assert marker in head, (name, marker)
