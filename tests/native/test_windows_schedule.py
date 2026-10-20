"""Exercise the scheduled GUI entrypoint and its real console descendants."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import updater


@unittest.skipUnless(os.name == "nt", "requires Windows pythonw and console windows")
class WindowsScheduleLaunchTests(unittest.TestCase):
    def test_windowless_entrypoint_preserves_arguments_exit_code_and_hidden_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "中文 & 100% state"
            root.mkdir()
            probe = root / "probe.py"
            output = root / "result.json"
            probe.write_text(
                "import ctypes, json, pathlib, subprocess, sys\n"
                "ctypes.windll.kernel32.GetConsoleWindow.restype = ctypes.c_void_p\n"
                "ctypes.windll.user32.IsWindowVisible.argtypes = [ctypes.c_void_p]\n"
                "window = ctypes.windll.kernel32.GetConsoleWindow()\n"
                "state = {'has_console': bool(window),\n"
                "         'visible': bool(ctypes.windll.user32.IsWindowVisible(window))}\n"
                "if sys.argv[1] == '--child':\n"
                "    print(json.dumps(state))\n"
                "else:\n"
                "    child = subprocess.check_output([sys.executable, __file__, '--child'])\n"
                "    state.update(child=json.loads(child), args=sys.argv[2:])\n"
                "    pathlib.Path(sys.argv[1]).write_text(json.dumps(state), encoding='utf-8')\n"
                "    raise SystemExit(23)\n",
                encoding="utf-8",
            )
            arguments = [str(root / ("long" * 80) / "中文.json"), "100% & !", 'literal "quote"']
            wrapper = updater._schedule_wrapper(
                {"state_dir": str(root)}, [sys.executable, str(probe), str(output), *arguments],
            )
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            result = subprocess.run([str(pythonw), str(wrapper)], capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 23, result.stderr)
            state = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(state["args"], arguments)
            for process in (state, state["child"]):
                self.assertTrue(process["has_console"])
                self.assertFalse(process["visible"])


if __name__ == "__main__":
    unittest.main()
