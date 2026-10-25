import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((ROOT / "kimi.plugin.json").read_text(encoding="utf-8"))

    def test_native_manifest_shape(self):
        self.assertEqual(self.manifest["name"], "mindie-agent")
        self.assertNotIn("sessionStart", self.manifest)
        events = [item["event"] for item in self.manifest["hooks"]]
        self.assertNotIn("UserPromptSubmit", events)
        # No TurnStarted hook: the host omits the native turn id from Stop
        # and no asynchronous record can prove it, so Stop is a thin
        # notification; there is no adapter identity cache.
        self.assertEqual(events.count("TurnStarted"), 0)
        self.assertFalse(any(item.get("matcher") == "plugin_command" for item in self.manifest["hooks"]))
        stop = next(item for item in self.manifest["hooks"] if item["event"] == "Stop")
        self.assertTrue(stop["command"].startswith("python3 "))
        self.assertNotIn("printf", stop["command"])
        for hook in self.manifest["hooks"]:
            self.assertTrue(hook["command"].startswith("python3 "))
            self.assertIn("with_runtime.py", hook["command"])
            self.assertEqual(hook["timeout"], 20)
        for server in self.manifest["mcpServers"].values():
            self.assertEqual(server["command"], "python3")
            self.assertEqual(server["args"][0], "./scripts/with_runtime.py")
            self.assertEqual(server["cwd"], "./")
        self.assertEqual(
            self.manifest["mcpServers"]["remote"].get("env"),
            {"REMOTE_DEV_HARNESS_PROFILE": "kimi"},
        )
        self.assertEqual(
            self.manifest["mcpServers"]["remote"].get("toolTimeoutMs"), 660000,
        )
        self.assertNotIn("env", self.manifest["mcpServers"]["knowledge"])
        self.assertNotIn("toolTimeoutMs", self.manifest["mcpServers"]["knowledge"])

    def test_no_updater_shim(self):
        self.assertFalse((ROOT / "scripts" / "update.py").exists())
