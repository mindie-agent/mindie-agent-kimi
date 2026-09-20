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
        self.assertEqual(events.count("TurnStarted"), 2)
        command = next(
            item for item in self.manifest["hooks"] if item.get("matcher") == "plugin_command"
        )
        self.assertIn("with_runtime.py", command["command"])
        stop = next(item for item in self.manifest["hooks"] if item["event"] == "Stop")
        self.assertNotIn("printf", stop["command"])
        self.assertLessEqual(stop["timeout"], 2)
        for server in self.manifest["mcpServers"].values():
            self.assertEqual(server["command"], "./scripts/with_runtime.py")

    def test_commands_are_not_textual_markers(self):
        init = (ROOT / "commands" / "init.md").read_text()
        self.assertNotIn("MINDIE_AGENT_NATIVE_ENTRY", init)
        self.assertIn("plugin_command", init)
