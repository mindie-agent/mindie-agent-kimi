"""Service-launch binding: the summary worker's native binary crosses into
service managers whose PATH differs from the login shell (systemd --user,
Task Scheduler) via the declared engine service_launch_context."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import SCRIPTS, make_config, write_session

sys.path.insert(0, str(SCRIPTS))
import knowledge_service  # noqa: E402
import paths  # noqa: E402


class KimiBinResolutionTests(unittest.TestCase):
    def setUp(self):
        self._saved = {
            key: os.environ.get(key)
            for key in ("MINDIE_KIMI_BIN", "KIMI_CODE_HOME", "MINDIE_KIMI_CONFIG")
        }
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _home_with_bin(self, tmp: Path) -> Path:
        home = tmp / "kimi-home"
        (home / "bin").mkdir(parents=True)
        binary = home / "bin" / ("kimi.exe" if os.name == "nt" else "kimi")
        binary.write_text("# native binary\n")
        return home

    def test_env_override_wins(self):
        with tempfile.TemporaryDirectory() as raw:
            home = self._home_with_bin(Path(raw))
            os.environ["MINDIE_KIMI_BIN"] = "/opt/kimi-custom"
            self.assertEqual(
                paths.resolve_kimi_bin({"kimi_home": str(home)}), "/opt/kimi-custom"
            )

    def test_kimi_home_bin_beats_path_search(self):
        with tempfile.TemporaryDirectory() as raw:
            home = self._home_with_bin(Path(raw))
            with patch.object(paths.shutil, "which", return_value="/usr/bin/kimi"):
                self.assertEqual(
                    paths.resolve_kimi_bin({"kimi_home": str(home)}),
                    str(home / "bin" / ("kimi.exe" if os.name == "nt" else "kimi")),
                )

    def test_path_search_fallback_and_honest_absence(self):
        with patch.object(paths.shutil, "which", return_value="/usr/bin/kimi"):
            self.assertEqual(paths.resolve_kimi_bin({}), "/usr/bin/kimi")
        with patch.object(paths.shutil, "which", return_value=None):
            self.assertIsNone(paths.resolve_kimi_bin({}))

    def test_prepare_service_env_sets_declared_value(self):
        with tempfile.TemporaryDirectory() as raw:
            home = self._home_with_bin(Path(raw))
            knowledge_service.prepare_service_env({"kimi_home": str(home)})
            expected = str(home / "bin" / ("kimi.exe" if os.name == "nt" else "kimi"))
            self.assertEqual(os.environ.get("MINDIE_KIMI_BIN"), expected)

    def test_prepare_service_env_never_raises_or_guesses(self):
        with tempfile.TemporaryDirectory() as raw:
            os.environ["MINDIE_KIMI_CONFIG"] = str(Path(raw) / "absent.json")
            with patch.object(paths.shutil, "which", return_value=None):
                knowledge_service.prepare_service_env({})
                self.assertNotIn("MINDIE_KIMI_BIN", os.environ)
                knowledge_service.prepare_service_env()
                self.assertNotIn("MINDIE_KIMI_BIN", os.environ)


class GitBinResolutionTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("MINDIE_KIMI_GIT_BIN")
        os.environ.pop("MINDIE_KIMI_GIT_BIN", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("MINDIE_KIMI_GIT_BIN", None)
        else:
            os.environ["MINDIE_KIMI_GIT_BIN"] = self._saved

    def test_env_override_wins(self):
        os.environ["MINDIE_KIMI_GIT_BIN"] = "/opt/git-custom"
        with patch.object(paths.shutil, "which", return_value="/usr/bin/git"):
            self.assertEqual(paths.resolve_git_bin(), "/opt/git-custom")

    def test_path_search_fallback(self):
        with patch.object(paths.shutil, "which", return_value="/usr/bin/git"):
            self.assertEqual(paths.resolve_git_bin(), "/usr/bin/git")

    def test_windows_standard_locations_after_path_search(self):
        class FakePath:
            def __init__(self, _value):
                pass

            def is_file(self):
                return True

        with patch.object(paths.shutil, "which", return_value=None), \
             patch.object(paths.os, "name", "nt"), \
             patch.object(paths, "Path", new=FakePath):
            self.assertEqual(
                paths.resolve_git_bin(), r"C:\Program Files\Git\cmd\git.exe"
            )

    def test_honest_absence(self):
        class FakePath:
            def __init__(self, _value):
                pass

            def is_file(self):
                return False

        with patch.object(paths.shutil, "which", return_value=None):
            if os.name == "nt":
                with patch.object(paths, "Path", new=FakePath):
                    self.assertIsNone(paths.resolve_git_bin())
            else:
                self.assertIsNone(paths.resolve_git_bin())


class LaunchContextDeclarationTests(unittest.TestCase):
    def test_engine_declaration_passes_core_validation(self):
        import capture_config
        from mindie_knowledge.loop import service_context

        prepared = capture_config.prepare(sys.executable, SCRIPTS)
        declaration = prepared["service_launch_context"]
        self.assertEqual(
            declaration, {"paths": {}, "environment": ["MINDIE_KIMI_BIN"]}
        )
        engine = {
            "root": "/tmp/x",
            "domain": "vllm-ascend",
            "transcript_adapter": str(SCRIPTS / "transcript.py"),
            "redactor_executable": prepared["redactor_executable"],
            "service_launch_context": declaration,
        }
        from mindie_knowledge.loop.cli import validate_config

        validate_config(engine)
        self.assertEqual(
            service_context.validate(engine),
            {"paths": {}, "environment": ["MINDIE_KIMI_BIN"]},
        )

    def test_ensure_service_sets_env_before_core_start(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            adapter = json.loads(config.read_text())
            adapter["kimi_bin"] = "/opt/kimi-stamped"
            config.write_text(json.dumps(adapter, indent=2) + "\n")
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            os.environ.pop("MINDIE_KIMI_BIN", None)
            captured = {}

            def fake_ensure(path, **kwargs):
                captured["path"] = path
                captured["env"] = os.environ.get("MINDIE_KIMI_BIN")
                return "connection"

            try:
                with patch(
                    "mindie_knowledge.loop.cli.ensure_service",
                    side_effect=fake_ensure,
                ):
                    result = knowledge_service.ensure_service()
            finally:
                os.environ.pop("MINDIE_KIMI_CONFIG", None)
                os.environ.pop("MINDIE_KIMI_BIN", None)
            self.assertEqual(result, "connection")
            self.assertEqual(captured["env"], "/opt/kimi-stamped")

    def test_bridge_stop_sets_env_before_handoff(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            home = tmp / "kimi-home"
            write_session(home, "ses_env", [{"type": "metadata", "created_at": 1}])
            config = make_config(tmp, sharing=True, roots=[tmp])
            adapter = json.loads(config.read_text())
            adapter["kimi_bin"] = "/opt/kimi-hook"
            config.write_text(json.dumps(adapter, indent=2) + "\n")
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            os.environ["KIMI_CODE_HOME"] = str(home)
            os.environ.pop("MINDIE_KIMI_BIN", None)
            import admission as admission_mod

            admission_mod.activate("ses_env", project_root=str(tmp.resolve()))
            import types

            captured = {}
            fake = types.ModuleType("mindie_knowledge.loop.cli")

            def capture_hook(config_path, payload):
                captured["env"] = os.environ.get("MINDIE_KIMI_BIN")
                return {"stage": "accepted-local", "wake": "not-requested"}

            fake.capture_hook = capture_hook
            import bridge
            import io

            event = {
                "hook_event_name": "Stop",
                "session_id": "ses_env",
                "cwd": str(tmp),
                "stop_hook_active": False,
            }

            class FakeStdin:
                buffer = None

            stdin = FakeStdin()
            stdin.buffer = io.BytesIO(json.dumps(event).encode())
            old_stdin, old_stdout = sys.stdin, sys.stdout
            old_cli = sys.modules.get("mindie_knowledge.loop.cli")
            sys.modules["mindie_knowledge.loop.cli"] = fake
            printed = []
            old_print = bridge._print
            try:
                sys.stdin = stdin
                sys.stdout = io.StringIO()
                bridge._print = printed.append
                rc = bridge.handle_stop()
            finally:
                sys.stdin, sys.stdout = old_stdin, old_stdout
                bridge._print = old_print
                if old_cli is not None:
                    sys.modules["mindie_knowledge.loop.cli"] = old_cli
                else:
                    sys.modules.pop("mindie_knowledge.loop.cli", None)
                os.environ.pop("MINDIE_KIMI_CONFIG", None)
                os.environ.pop("KIMI_CODE_HOME", None)
                os.environ.pop("MINDIE_KIMI_BIN", None)
            self.assertEqual(rc, 0)
            self.assertEqual(printed, [{}])
            self.assertEqual(captured["env"], "/opt/kimi-hook")


if __name__ == "__main__":
    unittest.main()
