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
                "changed": False, "installed": inventory, "runtime": inventory}), \
            patch("mindie_knowledge.component_runtime.read_inventory", return_value=inventory), \
            patch("mindie_knowledge.component_runtime.build") as build, \
            patch.object(updater, "probe_runtime"), \
            patch.object(updater, "write_generation_configs",
                         return_value=Path(candidate["adapter_config"])) as configure:
        prepared = component_update.prepare(adapter, Path(candidate["generation"]), python,
                                            time.monotonic() + 600)
    assert prepared[1] == python
    assert configure.call_args.args[1] == python
    build.assert_not_called()


@pytest.mark.parametrize("already_active", [False, True])
def test_bootstrap_interpreter_is_rebuilt_even_when_targets_equal_source_pins(tmp_path, already_active):
    import component_update
    from mindie_knowledge.component_runtime import read_inventory

    adapter, (old, candidate) = generations(tmp_path)
    target = read_inventory(sys.executable)
    # Actual bootstrap inventory observed after the 0.1.8 -> 0.1.9 native hop.
    bootstrap = {
        "mindie-knowledge": {"repository": "mindie-agent/knowledge", "version": "0.13.3",
                             "revision": "38a42f6f778cfa555d8918fdcacd12cee4a181f5"},
        "remote-dev": {"repository": "mindie-agent/remote-dev", "version": "0.10.6",
                       "revision": "0463a13fce9618ec9a51904869d61acb0cdb8538"},
        "mindie-diagnostics": {"repository": "mindie-agent/diagnostics", "version": "0.5.2",
                               "revision": "801a01b2789aed7f58e971a909205a7d40b61077"},
    }
    baseline = {"runtime": {name: row["revision"] for name, row in target.items()}}
    source = Path(old["generation"] if already_active else candidate["generation"])
    python = old["python"] if already_active else str(source / "candidate-python")
    artifact = {"source": str(tmp_path / "rebuilt/source"),
                "python": str(tmp_path / "rebuilt/runtime/python")}
    with patch("mindie_knowledge.component_runtime.source_baseline", return_value=baseline), \
            patch("mindie_knowledge.component_runtime.discover", return_value={
                "changed": already_active, "installed": bootstrap if already_active else target,
                "runtime": target}), \
            patch("mindie_knowledge.component_runtime.read_inventory", return_value=bootstrap), \
            patch("mindie_knowledge.component_runtime.build", return_value=artifact) as build, \
            patch.object(updater, "probe_runtime"), \
            patch.object(updater, "write_generation_configs",
                         return_value=Path(candidate["adapter_config"])) as configure:
        prepared = component_update.prepare(adapter, source, python, time.monotonic() + 600)
    assert prepared[:2] == (Path(artifact["source"]), Path(artifact["python"]))
    assert build.call_args.args[3] == target
    assert configure.call_args.kwargs["components"] == target


def test_component_check_does_not_report_active_after_restore_failure(tmp_path):
    import component_update

    adapter, old, candidate, _profile, _commands = component_generations(tmp_path)
    prepared = (
        Path(candidate["generation"]),
        Path(candidate["python"]),
        Path(candidate["adapter_config"]),
    )

    def restore(adapter, final, deadline):
        updater._record(adapter, service_handoff={
            "status": "failed",
            "error": "RuntimeError",
            "sha": (final or {}).get("sha"),
        })

    with patch.object(component_update, "prepare", return_value=prepared), \
            patch.object(updater, "_restore_stopped_service", side_effect=restore), \
            patch.object(updater, "build_host_package"):
        result = component_update.check(
            adapter, time.monotonic() + 120, force=False,
            idle=lambda *_: {"service": "stopped", "idle": True},
            install=Mock(), lock_timeout=1,
        )
    assert result["status"] == "failed"
    assert result["service_handoff"]["error"] == "RuntimeError"
    assert result["generation"] == candidate["generation"]


def test_component_check_reports_degraded_when_restore_stays_pending(tmp_path):
    import component_update

    adapter, old, candidate, _profile, _commands = component_generations(tmp_path)
    prepared = (
        Path(candidate["generation"]),
        Path(candidate["python"]),
        Path(candidate["adapter_config"]),
    )

    def restore(adapter, final, deadline):
        updater._record(adapter, service_handoff={
            "status": "pending",
            "error": "interrupted-stop-or-restore-needs-attention",
            "sha": (final or {}).get("sha"),
        })

    with patch.object(component_update, "prepare", return_value=prepared), \
            patch.object(updater, "_restore_stopped_service", side_effect=restore), \
            patch.object(updater, "build_host_package"):
        result = component_update.check(
            adapter, time.monotonic() + 120, force=False,
            idle=lambda *_: {"service": "stopped", "idle": True},
            install=Mock(), lock_timeout=1,
        )
    assert result["status"] == "degraded"
    assert result["service_handoff"]["status"] == "pending"


def test_component_check_reports_active_after_successful_restore(tmp_path):
    import component_update

    adapter, old, candidate, _profile, _commands = component_generations(tmp_path)
    prepared = (
        Path(candidate["generation"]),
        Path(candidate["python"]),
        Path(candidate["adapter_config"]),
    )

    def restore(adapter, final, deadline):
        updater._record(adapter, service_handoff={
            "status": "restored",
            "sha": (final or {}).get("sha"),
        })

    with patch.object(component_update, "prepare", return_value=prepared), \
            patch.object(updater, "_restore_stopped_service", side_effect=restore), \
            patch.object(updater, "build_host_package"):
        result = component_update.check(
            adapter, time.monotonic() + 120, force=False,
            idle=lambda *_: {"service": "stopped", "idle": True},
            install=Mock(), lock_timeout=1,
        )
    assert result == dict(
        status="active",
        generation=candidate["generation"],
        python=candidate["python"],
    )


def test_component_prepare_uses_reserve_not_hardcoded_260(tmp_path, monkeypatch):
    import component_update

    adapter, (old, _candidate) = generations(tmp_path)
    start = 10_000.0
    monkeypatch.setattr(time, "monotonic", lambda: start)
    captured = {}

    class Catalog:
        def __init__(self, budget=None, **_kwargs):
            captured["budget"] = budget

    inventory = {"mindie-knowledge": {"revision": "a" * 40}}
    with patch("mindie_knowledge.component_releases.Catalog", Catalog), \
            patch("mindie_knowledge.component_runtime.source_baseline",
                  return_value={"runtime": inventory}), \
            patch("mindie_knowledge.component_runtime.discover", return_value={
                "changed": False, "installed": inventory, "runtime": inventory,
            }) as discover, \
            patch("mindie_knowledge.component_runtime.read_inventory", return_value=inventory):
        assert component_update.prepare(
            adapter, Path(old["generation"]), old["python"], start + 200,
        ) is None
    # 200s remaining minus RESERVE 113s leaves 87s; catalog cap is 120.
    assert captured["budget"] == 87
    assert captured["budget"] > 0
    discover.assert_called_once()


def test_component_prepare_exhausted_reserve_raises_timeout(tmp_path, monkeypatch):
    import component_update

    adapter, (old, _candidate) = generations(tmp_path)
    start = 10_000.0
    monkeypatch.setattr(time, "monotonic", lambda: start)
    with patch("mindie_knowledge.component_runtime.source_baseline",
               return_value={"runtime": {}}), \
            patch("mindie_knowledge.component_runtime.discover") as discover, \
            pytest.raises(TimeoutError, match="budget exhausted"):
        component_update.prepare(
            adapter, Path(old["generation"]), old["python"], start + 50,
        )
    discover.assert_not_called()


def test_component_prepare_filters_retired_mindie_bench_and_types_catalog_errors(tmp_path):
    import urllib.error
    import component_update

    adapter, (old, _candidate) = generations(tmp_path)
    profile = tmp_path / "profile/kimi.json"
    profile.write_text(json.dumps(dict(adapter, component_commands={
        "bench": [sys.executable, "-m", "mindie_bench"],
    })))
    adapter["profile_config"] = str(profile)
    active = {"mindie-knowledge": {"repository": "mindie-agent/knowledge",
                                   "version": "0.13.15", "revision": "a" * 40}}
    legacy = dict(active, **{
        "mindie-bench": {"repository": "mindie-agent/mindie-bench",
                         "version": "0.1.0", "revision": "b" * 40},
    })
    artifact = {"source": str(tmp_path / "rebuilt/source"),
                "python": str(tmp_path / "rebuilt/runtime/python")}

    def fake_discover(python, baseline, *, required_components=None, **_kwargs):
        import mindie_knowledge.component_runtime as cr
        inv = cr.read_inventory(python)
        assert "mindie-bench" not in inv
        assert "mindie-bench" not in (required_components or {})
        return {"changed": False, "installed": inv, "runtime": active}

    with patch("mindie_knowledge.component_runtime.source_baseline",
               return_value={"runtime": {"mindie-knowledge": "a" * 40}}), \
            patch("mindie_knowledge.component_runtime.read_inventory", return_value=legacy), \
            patch("mindie_knowledge.component_runtime.discover", side_effect=fake_discover), \
            patch("mindie_knowledge.component_runtime.build", return_value=artifact) as build, \
            patch.object(updater, "probe_runtime"), \
            patch.object(updater, "write_generation_configs",
                         return_value=Path(old["adapter_config"])):
        prepared = component_update.prepare(
            adapter, Path(old["generation"]), old["python"], time.monotonic() + 600,
        )
    assert prepared is not None
    build.assert_called_once()

    typed = component_update.typed_catalog_error(urllib.error.URLError("offline"))
    assert isinstance(typed, component_update.CatalogError)
    assert typed.code == "release_transport_unavailable"
    assert typed.category == "temporary_network"


def test_updater_check_isolates_component_update_failure_and_deferral(tmp_path, capsys):
    import component_update

    adapter, (old, _candidate) = generations(tmp_path)
    recorded = []
    with patch.object(updater, "resolve_release",
                      return_value={"status": "up_to_date", "candidate": None}), \
            patch.object(component_update, "check",
                         side_effect=ValueError("component release artifacts are incomplete: mindie-sim")), \
            patch.object(updater, "_update_diagnostic",
                         side_effect=lambda stage, exc: recorded.append((stage, type(exc).__name__)) or {"recorded": True, "logging_failed": False}):
        rc = updater._check_once(
            adapter, time.monotonic() + 300, force=False,
            build=Mock(), idle=Mock(), install=Mock(), lock_timeout=1,
        )
    assert rc == 1
    assert recorded == [("component_update", "ValueError")]
    payload = json.loads(capsys.readouterr().out)
    assert payload["result"] == "degraded"
    assert payload["components"] == {"status": "failed", "error_type": "ValueError"}

    with patch.object(updater, "resolve_release",
                      return_value={"status": "up_to_date", "candidate": None}), \
            patch.object(component_update, "check",
                         side_effect=updater.Deferred("service busy")), \
            patch.object(updater, "repair_host_package", return_value=None):
        rc_def = updater._check_once(
            adapter, time.monotonic() + 300, force=False,
            build=Mock(), idle=Mock(), install=Mock(), lock_timeout=1,
        )
    assert rc_def == 0
    payload_def = json.loads(capsys.readouterr().out)
    assert payload_def["components"] == {"status": "deferred", "reason": "service busy"}
