"""Scheduler dispatch follows command capabilities and preserves native readback."""

import json
from pathlib import Path
import sys

import pytest

from support import SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))

import genstate
import updater
import updater_schedule


@pytest.fixture
def schedule_profile(tmp_path, monkeypatch):
    config = make_config(tmp_path / "config")
    monkeypatch.setenv("MINDIE_KIMI_CONFIG", str(config))
    adapter = json.loads(config.read_text())
    generation = tmp_path / "generation"
    generation.mkdir()
    genstate.write_current({
        "generation": str(generation), "python": sys.executable,
        "adapter_config": str(config), "sha": None,
    }, adapter)
    launcher = genstate.launch_dir(adapter) / "bootstrap" / "mindie_launch.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("# test committed launcher\n")
    launcher.with_name("bounded.py").write_text("# test launcher dependency\n")
    return config, adapter, launcher


@pytest.mark.parametrize("available", [(), ("schtasks",), ("powershell",)])
def test_missing_scheduler_capabilities_do_not_register_or_report_unscheduled(
    schedule_profile, monkeypatch, capsys, available,
):
    config, _, _ = schedule_profile
    monkeypatch.setattr(
        updater_schedule.shutil, "which",
        lambda name: str(Path("/scheduler") / name) if name in available else None,
    )
    calls = []
    monkeypatch.setattr(updater, "bounded_run", lambda *args, **kwargs: calls.append(args))
    monkeypatch.setattr(updater, "_native_run", lambda *args: calls.append(args))

    assert updater.install_schedule(config) == 1
    assert updater.uninstall_schedule() == 1
    assert [json.loads(line)["result"] for line in capsys.readouterr().out.splitlines()] == [
        "unsupported-platform", "unsupported-platform",
    ]
    assert calls == []


def test_task_scheduler_capabilities_drive_registration_and_verified_removal(
    schedule_profile, tmp_path, monkeypatch, capsys,
):
    config, adapter, launcher = schedule_profile
    python = tmp_path / "python.exe"
    python.with_name("pythonw.exe").touch()
    monkeypatch.setattr(updater_schedule.shutil, "which",
                        lambda name: f"/scheduler/{name}" if name in {"schtasks", "powershell"} else None)
    monkeypatch.setattr(updater, "schedule_command",
                        lambda *_: [str(python), str(launcher), "--config", str(config), "updater", "check"])
    calls = []
    wrapper = genstate.update_dir(adapter) / "MindIEKimiUpdate.pyw"

    def run(argv, *_args, **_kwargs):
        calls.append(argv)
        return wrapper.with_suffix(".xml").read_text(encoding="utf-16") if argv[0] == "powershell" else ""

    monkeypatch.setattr(updater, "bounded_run", run)
    assert updater.install_schedule(config) == 0
    installed = json.loads(capsys.readouterr().out)
    assert installed["verified"] is True
    assert installed["action"] == str(wrapper)
    assert calls[0][:2] == ["schtasks", "/Create"]

    present = [True]

    def native(argv, timeout):
        calls.append(argv)
        if "Unregister-ScheduledTask" in argv[-1]:
            present[0] = False
            return 0, "", ""
        return 0, "1" if present[0] else "0", ""

    monkeypatch.setattr(updater, "_native_run", native)
    assert updater.uninstall_schedule() == 0
    assert json.loads(capsys.readouterr().out)["result"] == "unscheduled"
    assert present == [False]
    assert "Get-ScheduledTask" in calls[-1][-1]


def test_launchctl_capability_removes_only_after_absence_readback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(updater_schedule.shutil, "which",
                        lambda name: "/scheduler/launchctl" if name == "launchctl" else None)
    monkeypatch.setattr(updater_schedule.os, "getuid", lambda: 1000, raising=False)
    plist = tmp_path / "agent.mindie.kimi-update.plist"
    plist.write_text("registered")
    loaded, calls = [True], []

    def native(argv, timeout):
        calls.append(argv)
        assert plist.exists()
        if argv[1] == "bootout":
            loaded[0] = False
            return 0, "", ""
        return (0 if loaded[0] else 113), "", ""

    monkeypatch.setattr(updater, "_native_run", native)
    assert updater.uninstall_schedule(plist_path=plist) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "unscheduled"
    assert [argv[1] for argv in calls] == ["print", "bootout", "print"]
    assert not plist.exists()
