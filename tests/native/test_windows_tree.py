"""Real Win32 cleanup; the outer watchdog keeps old-code failures bounded."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == "nt", "Win32 Job mechanism; POSIX group tests are separate")
class WindowsTreeTests(unittest.TestCase):
    def test_descendants_remain_owned_after_leader_exit(self):
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        for inherited in (False, True):
            with self.subTest(inherited_pipes=inherited), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                child = root / "child.py"
                child.write_text("import time; time.sleep(30)\n", encoding="utf-8")
                parent = root / "parent.py"
                pidfile = root / "child.pid"
                redirect = "" if inherited else ", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL"
                parent.write_text("import pathlib, subprocess, sys\n"
                                  f"p=subprocess.Popen([sys.executable,sys.argv[1]]{redirect})\n"
                                  "pathlib.Path(sys.argv[2]).write_text(str(p.pid))\n", encoding="utf-8")
                runner = root / "runner.py"
                scripts = Path(__file__).resolve().parents[2] / "scripts"
                runner.write_text("import json, sys\n"
                                  f"sys.path.insert(0,{str(scripts)!r})\n"
                                  "import bounded\n"
                                  "timeout=False\n"
                                  "try:\n"
                                  " bounded.run([sys.executable,*sys.argv[1:]],timeout=0.3)\n"
                                  "except bounded.CommandTimedOut:\n"
                                  " timeout=True\n"
                                  "print(json.dumps({'timeout':timeout}))\n", encoding="utf-8")
                process = subprocess.Popen([sys.executable,str(runner),str(parent),str(child),str(pidfile)],
                                           stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                handle = None
                started = time.monotonic()
                try:
                    deadline = started + 3
                    while not pidfile.exists() and process.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(pidfile.exists(), "real descendant did not start")
                    handle = kernel.OpenProcess(0x100001,False,int(pidfile.read_text()))
                    stdout,stderr = process.communicate(timeout=4)
                    self.assertEqual(process.returncode,0,stderr.decode("utf-8","replace"))
                    self.assertEqual(json.loads(stdout)["timeout"],inherited)
                    if handle:
                        self.assertEqual(kernel.WaitForSingleObject(handle,1000),0,"descendant survived")
                    self.assertLess(time.monotonic()-started,3)
                finally:
                    if handle:
                        if kernel.WaitForSingleObject(handle,0)==258:
                            kernel.TerminateProcess(handle,1)
                        kernel.CloseHandle(handle)
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=3)


if __name__ == "__main__":
    unittest.main()
