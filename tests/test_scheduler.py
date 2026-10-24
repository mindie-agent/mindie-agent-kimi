import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from support import SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))

import genstate  # noqa: E402
import updater  # noqa: E402

SHA = "421e22bf1fbf524a074d84ae3e95c3323860becd"
STALE = "# stale bootstrap launcher\n"
CURRENT = "# current version launcher\n"


class SchedulerLauncherTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config_path = make_config(self.tmp / "cfg")
        self.adapter = json.loads(self.config_path.read_text())
        os.environ["MINDIE_KIMI_CONFIG"] = str(self.config_path)
        self.gen = self.tmp / "generation"
        self.gen.mkdir()
        (self.gen / "scripts").mkdir()

    def tearDown(self):
        os.environ.pop("MINDIE_KIMI_CONFIG", None)
        self._tmp.cleanup()

    def write_current(self, sha):
        genstate.write_current(
            {
                "generation": str(self.gen),
                "python": sys.executable,
                "adapter_config": str(self.config_path),
                "sha": sha,
            },
            self.adapter,
        )

    def write_launch(self, identity, launcher_text, bounded=True):
        directory = genstate.launch_dir(self.adapter) / identity
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "mindie_launch.py").write_text(launcher_text)
        if bounded:
            (directory / "bounded.py").write_text("# bounded\n")
        return directory / "mindie_launch.py"

    def install(self):
        captured = []

        def fake_run(cmd, *_a, **_k):
            captured.append(list(cmd))
            return ""

        home = self.tmp / "fake-home"
        home.mkdir(exist_ok=True)
        with mock.patch.object(updater, "bounded_run", side_effect=fake_run), \
             mock.patch.object(updater.sys, "platform", "darwin"), \
             mock.patch.object(updater.os, "getuid", return_value=1000, create=True), \
             mock.patch("pathlib.Path.home", return_value=home):
            code = updater.install_schedule(self.config_path)
        plist = home / "Library" / "LaunchAgents" / "agent.mindie.kimi-update.plist"
        payload = plistlib.loads(plist.read_bytes()) if plist.is_file() else None
        return code, captured, payload

    def test_versioned_launcher_selected_over_stale_bootstrap(self):
        self.write_current(SHA)
        bootstrap = self.write_launch("bootstrap", STALE)
        current = self.write_launch(SHA, CURRENT)
        code, captured, payload = self.install()
        self.assertEqual(code, 0)
        self.assertEqual(payload["ProgramArguments"][1], str(current))
        self.assertIn("--config", payload["ProgramArguments"])
        self.assertIn("PATH", payload.get("EnvironmentVariables", {}))
        self.assertTrue(any("launchctl" in cmd[0] for cmd in captured))
        self.assertEqual(bootstrap.read_text(), STALE)

    def test_existing_wrapper_dispatches_committed_launcher_with_full_budget(self):
        from types import SimpleNamespace
        import updater_schedule
        self.write_current(SHA)
        launcher = self.write_launch(SHA, CURRENT)
        wrapper = genstate.update_dir(self.adapter) / "MindIEKimiUpdate.pyw"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("# legacy 300-second action\n")
        with mock.patch.object(updater, "bounded_run") as native:
            self.assertTrue(updater_schedule.refresh_existing_wrapper(
                self.adapter, genstate.read_current(self.adapter)))
            self.assertFalse(updater_schedule.refresh_existing_wrapper(
                self.adapter, genstate.read_current(self.adapter)))
        native.assert_not_called()
        process = mock.Mock(return_value=SimpleNamespace(returncode=7))
        windows = SimpleNamespace(
            STARTUPINFO=lambda: SimpleNamespace(dwFlags=0, wShowWindow=0),
            STARTF_USESHOWWINDOW=1, SW_HIDE=0, CREATE_NEW_CONSOLE=16, DEVNULL=-3,
            run=process)
        with mock.patch.dict(sys.modules, {"subprocess": windows}), self.assertRaises(SystemExit) as exit:
            exec(compile(wrapper.read_text(), str(wrapper), "exec"), {})
        self.assertEqual(exit.exception.code, 7)
        self.assertEqual(process.call_args.args[0], [sys.executable, str(launcher),
                         "--config", str(self.config_path), "updater", "check"])
        self.assertGreaterEqual(process.call_args.kwargs["timeout"], updater.CHECK_BUDGET + 20)

    def test_unscheduled_profile_stays_unscheduled_during_checks(self):
        import updater_schedule
        self.write_current(SHA)
        self.assertFalse(updater_schedule.refresh_existing_wrapper(
            self.adapter, genstate.read_current(self.adapter)))
        self.assertFalse((genstate.update_dir(self.adapter) / "MindIEKimiUpdate.pyw").exists())

    def test_missing_current_launcher_does_not_use_bootstrap(self):
        self.write_current(SHA)
        bootstrap = self.write_launch("bootstrap", STALE)
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertIn(SHA, str(raised.exception))
        self.assertNotIn("bootstrap", str(raised.exception).split(SHA)[-1])
        self.assertEqual(bootstrap.read_text(), STALE)

    def test_fresh_sha_none_selects_bootstrap(self):
        self.write_current(None)
        launcher = self.write_launch("bootstrap", "# bootstrap\n")
        code, _captured, payload = self.install()
        self.assertEqual(code, 0)
        self.assertEqual(payload["ProgramArguments"][1], str(launcher))

    def test_missing_bounded_fails(self):
        self.write_current(SHA)
        self.write_launch(SHA, CURRENT, bounded=False)
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertIn("no launcher available", str(raised.exception))


class WindowsScheduleTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.adapter = {"state_dir": str(self.tmp / "中文 & 100% state")}
        self.python = self.tmp / "runtime with spaces" / "python.exe"
        self.python.parent.mkdir()
        self.pythonw = self.python.with_name("pythonw.exe")
        self.pythonw.touch()
        self.command = [str(self.python), str(self.tmp / "long launcher path" / "mindie_launch.py"),
                        "--config", str(self.tmp / "100% 中文 & kimi.json"), "updater", "check"]

    def tearDown(self):
        self._tmp.cleanup()

    @property
    def wrapper(self):
        return genstate.update_dir(self.adapter) / "MindIEKimiUpdate.pyw"

    def _fake_scheduler(self, calls, query_xml=None):
        def fake_run(argv, stdin, **kw):
            calls.append(list(argv))
            if argv[0] == "powershell":
                if query_xml is not None:
                    return query_xml
                return self.wrapper.with_suffix(".xml").read_text(encoding="utf-16")
            return ""

        return fake_run

    def test_registration_references_wrapper_and_verifies_readback(self):
        calls = []
        with mock.patch.object(
            updater, "bounded_run", side_effect=self._fake_scheduler(calls)
        ):
            payload = updater._register_windows_schedule(self.adapter, self.command)
        create = next(argv for argv in calls if "/Create" in argv)
        # /F overwrite heals an existing (e.g. truncated) registration.
        self.assertIn("/F", create)
        self.assertNotIn("/TR", create)
        definition = ET.parse(create[create.index("/XML") + 1])
        action = definition.find("./{*}Actions/{*}Exec")
        self.assertEqual(action.findtext("{*}Command"), str(self.pythonw))
        self.assertEqual(action.findtext("{*}Arguments"), subprocess.list2cmdline([str(self.wrapper)]))
        self.assertEqual(definition.findtext("./{*}Triggers/{*}TimeTrigger/{*}Repetition/{*}Interval"), "PT5M")
        self.assertEqual(payload["verified"], True)
        self.assertEqual(payload["action"], str(self.wrapper))

    def test_long_interpreter_and_config_paths_survive_registration(self):
        self.command[0] = str(self.tmp / ("a" * 70) / ("b" * 70) / "python.exe")
        self.pythonw = Path(self.command[0]).with_name("pythonw.exe")
        self.pythonw.parent.mkdir(parents=True)
        self.pythonw.touch()
        self.command[3] = str(self.tmp / ("c" * 70) / ("d" * 70) / "中文.json")
        self.test_registration_references_wrapper_and_verifies_readback()

    def test_missing_pythonw_does_not_replace_existing_task(self):
        self.pythonw.unlink()
        with mock.patch.object(updater, "bounded_run") as run, self.assertRaises(SystemExit):
            updater._register_windows_schedule(self.adapter, self.command)
        run.assert_not_called()

    def test_truncated_readback_is_a_loud_failure(self):
        truncated = (
            '<Task><Actions><Exec><Command>"updater" "che</Command>'
            "</Exec></Actions></Task>"
        )
        calls = []
        with mock.patch.object(
            updater, "bounded_run",
            side_effect=self._fake_scheduler(calls, query_xml=truncated),
        ), self.assertRaises(SystemExit) as caught:
            updater._register_windows_schedule(self.adapter, self.command)
        self.assertIn("not trusted", str(caught.exception))

    def test_console_python_readback_is_rejected_even_with_correct_wrapper(self):
        task = ET.Element("Task")
        action = ET.SubElement(ET.SubElement(task, "Actions"), "Exec")
        ET.SubElement(action, "Command").text = str(self.python)
        ET.SubElement(action, "Arguments").text = subprocess.list2cmdline([str(self.wrapper)])
        with mock.patch.object(updater, "bounded_run", side_effect=self._fake_scheduler(
                [], query_xml=ET.tostring(task, encoding="unicode"))), self.assertRaises(SystemExit):
            updater._register_windows_schedule(self.adapter, self.command)


if __name__ == "__main__":
    unittest.main()
