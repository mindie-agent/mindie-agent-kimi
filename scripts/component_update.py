"""[Role] Advance component releases using Kimi's existing generation transaction.
[Invariants] Native plugin definitions are reinstalled only for plugin updates.
Component updates preserve the plugin SHA and all profile authority paths.
[Timeouts & Locks] Builder uses the remaining enclosing budget minus 260s for
probe/configuration/handoff; the existing exclusive operation lock gates commit.
[Side-Effect Policy] Software release reads/builds and idle generation switch;
never authorizes knowledge, reads a corpus or changes contribution consent.
"""
from __future__ import annotations
from pathlib import Path
import time


def prepare(adapter, source, python, deadline, *, force=False):
    import updater_check
    host = updater_check._HOST
    from mindie_knowledge.component_runtime import build, discover, source_baseline
    from mindie_knowledge.component_releases import Catalog
    current = host.read_current(adapter)
    baseline = source_baseline(source)
    selected = discover(current["python"], baseline["runtime"], force=force,
                        catalog=Catalog(budget=min(120, deadline - time.monotonic() - 260)))
    if not selected["changed"] and Path(source).resolve() == Path(current["generation"]).resolve():
        return None
    pins = {name: row["revision"] for name, row in selected["runtime"].items()}
    if pins == baseline["runtime"]:
        return None
    artifact = build(source, host.update_dir(adapter) / "component-runtimes", baseline,
                     selected["runtime"], python=python,
                     timeout=min(420, deadline - time.monotonic() - 260))
    generation, python = Path(artifact["source"]), Path(artifact["python"])
    host.probe_runtime(python, deadline, generation)
    config = host.write_generation_configs(generation, python, adapter)
    return generation, python, config


def check(adapter, deadline, *, force, idle, install, lock_timeout):
    import updater_check
    host = updater_check._HOST
    current = host.read_current(adapter)
    prepared = prepare(adapter, Path(current["generation"]), current["python"], deadline, force=force)
    if prepared is None or str(prepared[0]) == current["generation"]:
        return dict(status="up_to_date")
    generation, python, config = prepared
    # Retain the native payload for a later plugin rollback, without changing
    # its registered definitions during a component-only update.
    if current.get("sha"):
        host.build_host_package(generation, adapter, current["sha"], config_file=config)
    host.switch(adapter, current, generation, python, config, current.get("sha"), deadline,
                idle, install, lock_timeout, update_native=False)
    return dict(status="active", generation=str(generation), python=str(python))
