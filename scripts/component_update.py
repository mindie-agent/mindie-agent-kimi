"""[Role] Advance component releases using Kimi's existing generation transaction.
[Invariants] Native plugin definitions are reinstalled only for plugin updates.
Component updates preserve the plugin SHA and all profile authority paths.
[Timeouts & Locks] Builder uses the remaining enclosing budget minus 260s for
probe/configuration/handoff; the existing exclusive operation lock gates commit.
[Side-Effect Policy] Software release reads/builds and idle generation switch;
never authorizes knowledge, reads a corpus or changes contribution consent.
"""
from __future__ import annotations
import json
from pathlib import Path
import time


def prepare(adapter, source, python, deadline, *, force=False):
    import updater_check
    host = updater_check._HOST
    from mindie_knowledge.component_bindings import required_components, rebind_commands
    from mindie_knowledge.component_runtime import build, discover, source_baseline
    from mindie_knowledge.component_releases import Catalog
    from paths import profile_config_path
    current = host.read_current(adapter)
    profile_path = Path(adapter.get("profile_config") or profile_config_path())
    profile = (json.loads(profile_path.read_text())
               if adapter.get("profile_config") or profile_path.is_file() else adapter)
    if not isinstance(profile, dict):
        raise ValueError("Kimi profile configuration must be one object")
    baseline = source_baseline(source)
    selected = discover(current["python"], baseline["runtime"], force=force,
                        catalog=Catalog(budget=min(120, deadline - time.monotonic() - 260)),
                        required_components=required_components(profile))
    needs_binding = bool(profile.get("component_commands")) and (
        rebind_commands(profile, python, selected["runtime"]) != profile["component_commands"])
    if (not selected["changed"] and not needs_binding
            and Path(source).resolve() == Path(current["generation"]).resolve()):
        return None
    pins = {name: row["revision"] for name, row in selected["runtime"].items()}
    if pins == baseline["runtime"] and not needs_binding:
        return None
    generation, python = Path(source), Path(python)
    if pins != baseline["runtime"]:
        artifact = build(source, host.update_dir(adapter) / "component-runtimes", baseline,
                         selected["runtime"], python=python,
                         timeout=min(420, deadline - time.monotonic() - 260))
        generation, python = Path(artifact["source"]), Path(artifact["python"])
    host.probe_runtime(python, deadline, generation)
    config = host.write_generation_configs(generation, python, adapter, components=selected["runtime"])
    return generation, python, config


def check(adapter, deadline, *, force, idle, install, lock_timeout):
    import updater_check
    host = updater_check._HOST
    current = host.read_current(adapter)
    prepared = prepare(adapter, Path(current["generation"]), current["python"], deadline, force=force)
    if prepared is None:
        return dict(status="up_to_date")
    generation, python, config = prepared
    # Retain the native payload for a later plugin rollback, without changing
    # its registered definitions during a component-only update.
    if current.get("sha"):
        host.build_host_package(generation, adapter, current["sha"], config_file=config)
    host.switch(adapter, current, generation, python, config, current.get("sha"), deadline,
                idle, install, lock_timeout, update_native=False)
    return dict(status="active", generation=str(generation), python=str(python))
