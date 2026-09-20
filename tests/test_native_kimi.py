import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from support import ROOT


def _configured_kimi_binary():
    explicit = os.environ.get("MINDIE_KIMI_BIN")
    if isinstance(explicit, str) and explicit.strip():
        path = Path(explicit).expanduser()
        return str(path) if path.is_file() else None
    return shutil.which("kimi")


class NativeKimiTests(unittest.TestCase):
    def test_native_binary_has_plugin_manifest_support(self):
        kimi = _configured_kimi_binary()
        if not kimi:
            self.skipTest(
                "native kimi binary is absent (set MINDIE_KIMI_BIN or install on PATH)"
            )
        data = Path(kimi).read_bytes()
        self.assertIn(b"kimi.plugin.json", data)
        self.assertIn(b"KIMI_PLUGIN_ROOT", data)

    def test_plugin_install_is_not_a_cli_subcommand(self):
        kimi = _configured_kimi_binary()
        if not kimi:
            self.skipTest(
                "native kimi binary is absent (set MINDIE_KIMI_BIN or install on PATH)"
            )
        help_text = subprocess.run(
            [kimi, "--help"], capture_output=True, text=True, timeout=10
        ).stdout
        self.assertNotIn("plugins install", help_text.lower())
        manifest = json.loads((ROOT / "kimi.plugin.json").read_text())
        knowledge = manifest["mcpServers"]["knowledge"]
        self.assertEqual(knowledge["command"], "python3")
        self.assertEqual(knowledge["args"][0], "./scripts/with_runtime.py")
