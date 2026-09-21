"""Adapter status contract: diagnose existing state without changing it."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CONFIG_ENV = "MINDIE_KIMI_CONFIG"


class StatusDiagnosticTests(unittest.TestCase):
    def _status(self, config, session=None):
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        code = "import json,sys;sys.path.insert(0,sys.argv[1]);import entry;print(json.dumps(entry.status_payload(" + repr(session) + ")))"
        env = dict(os.environ, **{CONFIG_ENV: str(config)})
        result = subprocess.run([sys.executable, "-c", code, str(scripts)], env=env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_corrupt_configuration_is_not_first_use(self):
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "adapter.json"
            config.write_text("{")
            result = self._status(config)
            self.assertIsNone(result["configured"])
            self.assertEqual(result["error"], dict(stage="local_settings", type="JSONDecodeError"))
            self.assertNotIn("choices", result)
            self.assertEqual(list(Path(raw).iterdir()), [config])

    def test_paused_and_scoped_without_activation_or_replay(self):
        from mindie_knowledge.loop.activation import Admission
        from mindie_knowledge.loop.store import Store, session_key
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            engine = root / "engine.json"
            admission = Admission(root / "admission.sqlite3")
            lease = admission.activate("task-A", project_root=str(root))
            for _ in range(3):
                admission.finish("task-A", lease["token"], False)
            store = Store(root / "state", "test")
            try:
                own = store.add_capture(root_session=session_key("task-A"), session="task-A", turn="one", transcript=None, summary="private-material")
                store.add_capture(root_session=session_key("task-B"), session="task-B", turn="two", transcript=None, summary="foreign-material")
                store.mark_capture(own["id"], "failed", "RuntimeError: maintenance agent exited 124; category=deadline; elapsed=120.000s")
            finally:
                store.close()
            engine.write_text(json.dumps(dict(root=str(root / "state"), domain="test", admission_path=str(admission.path))))
            adapter = root / "adapter.json"
            adapter.write_text(json.dumps(dict(engine_config=str(engine), community_config=str(root / "community.json"), state_dir=str(root / "adapter-state"))))
            result = self._status(adapter, "task-A")
            self.assertTrue(result["this_session"]["paused"])
            self.assertEqual(result["this_session"]["failures"], 3)
            self.assertEqual([x["id"] for x in result["diagnostics"]["captures"]], [own["id"]])
            self.assertEqual(result["diagnostics"]["captures"][0]["category"], "deadline")
            self.assertNotIn(lease["token"], json.dumps(result))
            self.assertNotIn("foreign-material", json.dumps(result))
            self.assertEqual(admission.inspect("task-A")["failures"], 3)
            self.assertEqual(self._status(adapter)["diagnostics"]["captures"], [])
            scripts = Path(__file__).resolve().parents[1] / "scripts"
            code = "import json,sys;sys.path.insert(0,sys.argv[1]);import entry;print(json.dumps(entry.op_status('task-A')))"
            env = dict(os.environ, **{CONFIG_ENV: str(adapter)})
            env.pop("KIMI_CODE_HOME", None)
            proc = subprocess.run([sys.executable, "-c", code, str(scripts)], env=env,
                                  capture_output=True, text=True, timeout=5)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(json.loads(proc.stdout)["this_session"]["paused"])
            self.assertEqual(admission.inspect("task-A")["failures"], 3)
            admission.deactivate("task-A")
            proc = subprocess.run([sys.executable, "-c", code, str(scripts)], env=env,
                                  capture_output=True, text=True, timeout=5)
            self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
