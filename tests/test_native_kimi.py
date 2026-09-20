import json
import shutil
import subprocess
import unittest
from pathlib import Path

from support import ROOT


class NativeKimiTests(unittest.TestCase):
    def test_installed_binary_is_0_42_0_and_has_plugin_manifest_support(self):
        kimi = shutil.which("kimi")
        self.assertIsNotNone(kimi)
        version = subprocess.run(
            [kimi, "--version"], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(version.stdout.strip(), "0.42.0")

        def has(pattern):
            found = subprocess.run(
                ["rg", "-a", "-l", pattern, kimi],
                capture_output=True,
                timeout=30,
            )
            return found.returncode == 0

        self.assertTrue(has("kimi.plugin.json"))
        self.assertTrue(has("KIMI_PLUGIN_ROOT"))

    def test_plugin_install_is_not_a_cli_subcommand(self):
        kimi = shutil.which("kimi")
        help_text = subprocess.run(
            [kimi, "--help"], capture_output=True, text=True, timeout=10
        ).stdout
        self.assertNotIn("plugins install", help_text.lower())
        manifest = json.loads((ROOT / "kimi.plugin.json").read_text())
        knowledge = manifest["mcpServers"]["knowledge"]
        self.assertEqual(knowledge["command"], "python3")
        self.assertEqual(knowledge["args"][0], "./scripts/with_runtime.py")
