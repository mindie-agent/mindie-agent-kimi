"""End-to-end pre-0.1.31 (`v0.1.30 -> v0.1.32`) Kimi self-upgrade handoff tests."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys


def test_v0130_transcript_probe_shim(repo_root: Path) -> None:
    from mindie_knowledge.loop import cli

    shim = repo_root / "scripts" / "transcript.py"
    assert shim.is_file()
    adapter = cli.load_transcript_adapter({"transcript_adapter": str(shim)})
    assert callable(adapter.identify) and callable(adapter.read_material)


def test_v0130_launch_dir_current_json_and_updater_cli(repo_root: Path, tmp_path: Path, plugin_env: dict[str, str]) -> None:
    sha = "b" * 40
    state = tmp_path / "legacy-state"
    gen = state / "update" / "generations" / sha
    launch = state / "update" / "launch" / sha
    (gen / "scripts").mkdir(parents=True)
    (gen / "assets").mkdir(parents=True)
    (gen / "config").mkdir(parents=True)
    launch.mkdir(parents=True)
    for src in (repo_root / "scripts").glob("*.py"):
        shutil.copy2(src, gen / "scripts" / src.name)
    for src in (repo_root / "assets").iterdir():
        shutil.copy2(src, gen / "assets" / src.name)
    shutil.copy2(repo_root / "kimi.plugin.json", gen / "kimi.plugin.json")
    shutil.copy2(repo_root / "harness-compatibility.json", gen / "harness-compatibility.json")
    # v0.1.30 build_host_package copies ONLY mindie_launch.py into update/launch/<sha>/
    shutil.copy2(gen / "scripts" / "mindie_launch.py", launch / "mindie_launch.py")

    cfg_path = tmp_path / "cfg" / "kimi.json"
    cfg_path.parent.mkdir(parents=True)
    gen_adapter = gen / "config" / "kimi.adapter.json"
    gen_adapter.write_text(json.dumps({"python": sys.executable}), encoding="utf-8")
    # Simulate v0.1.30 switch: kimi.json still points to a dead old venv, while current.json points to sys.executable
    cfg_path.write_text(json.dumps({"python": "/nonexistent/v0.1.30/python", "state_dir": str(state)}), encoding="utf-8")
    (state / "update" / "current.json").write_text(json.dumps({
        "generation": str(gen),
        "python": sys.executable,
        "adapter_config": str(gen_adapter),
        "sha": sha,
    }), encoding="utf-8")

    spec = importlib.util.spec_from_file_location("kimi_launch_standalone", launch / "mindie_launch.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.HERE == (gen / "scripts").resolve()
    assert mod._active_python(cfg_path) == Path(sys.executable)

    # Also verify component-runtimes committed generation BOTH when generations/<sha> STILL exists AND when absent
    comp_gen = state / "update" / "component-runtimes" / "comp-001"
    shutil.copytree(str(gen), str(comp_gen))
    comp_adapter = comp_gen / "config" / "kimi.adapter.json"
    (state / "update" / "current.json").write_text(json.dumps({
        "generation": str(comp_gen),
        "python": sys.executable,
        "adapter_config": str(comp_adapter),
        "sha": sha,
    }), encoding="utf-8")

    spec_coexist = importlib.util.spec_from_file_location("kimi_launch_coexist", launch / "mindie_launch.py")
    assert spec_coexist is not None and spec_coexist.loader is not None
    mod_coexist = importlib.util.module_from_spec(spec_coexist)
    spec_coexist.loader.exec_module(mod_coexist)
    assert mod_coexist._effective_scripts(cfg_path) == comp_gen / "scripts"
    assert mod_coexist._active_python(cfg_path) == Path(sys.executable)

    shutil.rmtree(str(gen))
    spec2 = importlib.util.spec_from_file_location("kimi_launch_comp", launch / "mindie_launch.py")
    assert spec2 is not None and spec2.loader is not None
    mod2 = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod2)
    assert mod2._effective_scripts(cfg_path) == comp_gen / "scripts"
    assert mod2._active_python(cfg_path) == Path(sys.executable)

    # Legacy launchd / Windows Scheduled Task invokes: mindie_launch.py --config <cfg> updater status|check
    status_proc = subprocess.run(
        [sys.executable, str(launch / "mindie_launch.py"), "--config", str(cfg_path), "updater", "status"],
        env=plugin_env, capture_output=True, text=True, timeout=30,
    )
    assert status_proc.returncode == 0, status_proc.stderr
    assert json.loads(status_proc.stdout)["result"] == "current"
