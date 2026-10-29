"""Component-only switching preserves the native plugin identity and old binding."""
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
from unittest.mock import Mock

import pytest
from support import SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))
import genstate
import updater


def generations(tmp_path):
    path = make_config(tmp_path / 'profile')
    adapter = json.loads(path.read_text())
    configs = []
    for name in ('old', 'candidate'):
        generation = tmp_path / name
        generation.mkdir()
        config = generation / 'adapter.json'
        config.write_text(json.dumps(dict(adapter, domain_configs={})))
        configs.append(dict(generation=str(generation), python=sys.executable,
                            adapter_config=str(config), sha='a' * 40))
    genstate.write_current(configs[0], adapter)
    return adapter, configs


def test_component_switch_preserves_plugin_sha_without_native_install(tmp_path):
    adapter, (old, candidate) = generations(tmp_path)
    install = Mock()
    updater.switch(adapter, old, Path(candidate['generation']), Path(candidate['python']),
                   Path(candidate['adapter_config']), old['sha'], time.monotonic() + 120,
                   lambda *_: {'service': 'absent'}, install, 1, update_native=False)
    assert genstate.read_current(adapter) == candidate
    install.assert_not_called()


def test_busy_work_prevents_component_publication_without_native_mutation(tmp_path):
    adapter, (old, candidate) = generations(tmp_path)
    install = Mock()
    def busy(*_):
        raise updater.Deferred('active RPC')
    with pytest.raises(updater.Deferred, match='active RPC'):
        updater.switch(adapter, old, Path(candidate['generation']), Path(candidate['python']),
                       Path(candidate['adapter_config']), old['sha'], time.monotonic() + 120,
                       busy, install, 1, update_native=False)
    assert genstate.read_current(adapter) == old
    install.assert_not_called()


def component_generations(tmp_path):
    from mindie_knowledge.component_bindings import rebind_commands

    adapter, (old, candidate) = generations(tmp_path)
    profile = tmp_path / "profile/kimi.json"
    commands = {"profiler": [sys.executable, "-m", "mindie_profiler"],
                "sim": ["/user/custom-simulator", "--mode"]}
    value = dict(adapter, component_commands=commands, profile_config=str(profile))
    profile.write_text(json.dumps(value))
    rebound = rebind_commands(value, candidate["python"], {"mindie-profiler": {}})
    selected = dict(value, domain_configs={}, component_source_commands=commands,
                    component_commands=rebound)
    Path(candidate["adapter_config"]).write_text(json.dumps(selected))
    return adapter, old, candidate, profile, commands


def test_component_switch_updates_the_authoritative_profile_and_runs_new_command(tmp_path, monkeypatch):
    import component_tools

    adapter, old, candidate, profile, commands = component_generations(tmp_path)
    updater.switch(adapter, old, Path(candidate["generation"]), Path(candidate["python"]),
                   Path(candidate["adapter_config"]), old["sha"], time.monotonic() + 120,
                   lambda *_: {"service": "absent"}, Mock(), 1, update_native=False)
    active = json.loads(profile.read_text())
    assert active["component_commands"]["sim"] == commands["sim"]
    assert active["component_commands"]["profiler"] == [
        sys.executable, "-I", "-X", "utf8", "-m", "mindie_profiler"]
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", candidate["adapter_config"])
    result = component_tools.execute("profiler", {"argv": ["--help"], "cwd": str(tmp_path)}, "session")
    assert result["structuredContent"]["exit_code"] == 0
    assert "inventory" in result["structuredContent"]["stdout"]


def test_component_command_edit_during_idle_handoff_is_preserved(tmp_path):
    adapter, old, candidate, profile, _commands = component_generations(tmp_path)
    changed = json.loads(profile.read_text())
    changed["component_commands"]["profiler"] = ["/user/replacement-profiler"]
    def idle(*_):
        profile.write_text(json.dumps(changed))
        return {"service": "absent"}
    with pytest.raises(updater.Deferred, match="component commands changed"):
        updater.switch(adapter, old, Path(candidate["generation"]), Path(candidate["python"]),
                       Path(candidate["adapter_config"]), old["sha"], time.monotonic() + 120,
                       idle, Mock(), 1, update_native=False)
    assert json.loads(profile.read_text()) == changed
    assert genstate.read_current(adapter) == old


def test_failed_runtime_pointer_restores_command_binding_without_losing_other_profile_edits(tmp_path):
    adapter, old, candidate, profile, commands = component_generations(tmp_path)
    write_current = updater.write_current
    def publish(value, adapter):
        if value == candidate:
            changed = json.loads(profile.read_text())
            changed["user_option"] = "preserved"
            profile.write_text(json.dumps(changed))
            raise OSError("pointer publication failed")
        return write_current(value, adapter)
    with patch.object(updater, "write_current", side_effect=publish), \
            pytest.raises(OSError, match="pointer publication failed"):
        updater.switch(adapter, old, Path(candidate["generation"]), Path(candidate["python"]),
                       Path(candidate["adapter_config"]), old["sha"], time.monotonic() + 120,
                       lambda *_: {"service": "absent"}, Mock(), 1, update_native=False)
    active = json.loads(profile.read_text())
    assert active["component_commands"] == commands
    assert active["user_option"] == "preserved"
    assert genstate.read_current(adapter) == old


def test_damaged_stable_profile_fails_before_component_selection(tmp_path):
    import component_update

    adapter, (old, _candidate) = generations(tmp_path)
    profile = tmp_path / "profile/kimi.json"
    profile.write_text("{broken")
    adapter["profile_config"] = str(profile)
    with patch("mindie_knowledge.component_runtime.discover") as discover, \
            pytest.raises(json.JSONDecodeError):
        component_update.prepare(adapter, Path(old["generation"]), old["python"],
                                 time.monotonic() + 600)
    discover.assert_not_called()


def test_plugin_candidate_rebinds_a_baseline_component_even_when_pins_do_not_change(tmp_path):
    import component_update
    from mindie_knowledge.component_runtime import read_inventory

    adapter, (old, candidate) = generations(tmp_path)
    profile = tmp_path / "profile/kimi.json"
    profile.write_text(json.dumps(dict(adapter, component_commands={
        "diagnostics": [sys.executable, "-I", "-X", "utf8", "-m", "mindie_diagnostics.cli"]})))
    adapter["profile_config"] = str(profile)
    inventory = read_inventory(sys.executable)
    baseline = {"runtime": {name: row["revision"] for name, row in inventory.items()}}
    python = Path(candidate["generation"]) / "candidate-python"
    with patch("mindie_knowledge.component_runtime.source_baseline", return_value=baseline), \
            patch("mindie_knowledge.component_runtime.discover", return_value={
                "changed": False, "runtime": inventory}), \
            patch("mindie_knowledge.component_runtime.build") as build, \
            patch.object(updater, "probe_runtime"), \
            patch.object(updater, "write_generation_configs",
                         return_value=Path(candidate["adapter_config"])) as configure:
        prepared = component_update.prepare(adapter, Path(candidate["generation"]), python,
                                            time.monotonic() + 600)
    assert prepared[1] == python
    assert configure.call_args.args[1] == python
    build.assert_not_called()
