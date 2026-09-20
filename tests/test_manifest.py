import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "kimi.plugin.json").read_text())

    def test_native_manifest_shape(self):
        self.assertEqual(self.manifest["name"], "mindie-agent")
        self.assertNotIn("sessionStart", self.manifest)
        events = [item["event"] for item in self.manifest["hooks"]]
        self.assertNotIn("UserPromptSubmit", events)
        # No TurnStarted hook: default-off tasks must create no turn store.
        self.assertEqual(events.count("TurnStarted"), 0)
        self.assertFalse(any(item.get("matcher") == "plugin_command" for item in self.manifest["hooks"]))
        stop = next(item for item in self.manifest["hooks"] if item["event"] == "Stop")
        self.assertTrue(stop["command"].startswith("python3 "))
        self.assertNotIn("printf", stop["command"])
        self.assertLessEqual(stop["timeout"], 2)
        for hook in self.manifest["hooks"]:
            self.assertTrue(hook["command"].startswith("python3 "))
            self.assertIn("with_runtime.py", hook["command"])
        for server in self.manifest["mcpServers"].values():
            self.assertEqual(server["command"], "python3")
            self.assertEqual(server["args"][0], "./scripts/with_runtime.py")
            self.assertEqual(server["cwd"], "./")

    def test_commands_are_not_textual_markers(self):
        init = (ROOT / "commands" / "init.md").read_text()
        self.assertNotIn("MINDIE_AGENT_NATIVE_ENTRY", init)
        self.assertIn("mindie_entry", init)
        self.assertIn("request_nonce", init)
        self.assertNotIn("TurnStarted hook binds", init)

    def test_no_updater_shim(self):
        self.assertFalse((ROOT / "scripts" / "update.py").exists())
