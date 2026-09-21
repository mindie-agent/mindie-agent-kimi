"""Real local frontend processes; no native model or remote execution."""
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
CHILD = r'''
import json,os,subprocess,sys,time
from pathlib import Path
root=Path(__file__).resolve().parents[2]
message=json.loads(sys.stdin.readline())
large=message.get('params',{}).get('arguments',{}).get('large')
grand=None if large else subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
(root/'pids.json').write_text(json.dumps([os.getpid(),grand.pid if grand else None]))
if not large: time.sleep(30)
print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'content':[{'type':'text','text':'x'*220000 if large else 'done'}]}}),flush=True)
'''


@unittest.skipUnless(os.name == "posix", "real POSIX pipe/process ownership")
class FrontControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        generation = self.root / "generation"
        (generation / "scripts").mkdir(parents=True)
        (generation / "scripts/mcp_server.py").write_text(CHILD)
        config = self.root / "adapter.json"
        config.write_text(json.dumps({"state_dir": str(self.root / "state")}))
        update = self.root / "state/update"
        update.mkdir(parents=True)
        (update / "current.json").write_text(json.dumps({
            "generation": str(generation), "python": sys.executable,
            "adapter_config": str(config), "sha": "controlled-front-probe",
        }))
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("MINDIE_", "PYTHONPATH", "REMOTE_DEV_"))}
        self.proc = subprocess.Popen(
            [sys.executable, str(SCRIPTS / "mindie_launch.py"), "--config",
             str(config), "mcp", "remote"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            start_new_session=True)

    def send(self, value):
        self.proc.stdin.write((json.dumps(value) + "\n").encode())
        self.proc.stdin.flush()

    def start_call(self, large=False):
        self.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "remote_job_status",
                              "arguments": {"job_id": "original-job", "large": large}}})
        until = time.monotonic() + 3
        while not (self.root / "pids.json").exists() and time.monotonic() < until:
            time.sleep(.01)
        self.assertTrue((self.root / "pids.json").exists())

    def tearDown(self):
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=2)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            stream.close()
        pidfile = self.root / "pids.json"
        if pidfile.exists():
            for pid in json.loads(pidfile.read_text()):
                if pid is None:
                    continue
                result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                        capture_output=True, text=True)
                state = result.stdout.strip()
                if state and not state.startswith("Z"):
                    os.kill(pid, signal.SIGKILL)
                    self.fail("owned child remains alive")
        self.tmp.cleanup()

    def test_cancel_keeps_ping_responsive_and_preserves_original_job(self):
        self.start_call()
        start = time.monotonic()
        self.send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                   "params": {"requestId": 1}})
        self.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        data = b""
        selector = selectors.DefaultSelector()
        selector.register(self.proc.stdout, selectors.EVENT_READ)
        try:
            while data.count(b"\n") < 2 and time.monotonic() - start < 2:
                if selector.select(.05):
                    data += os.read(self.proc.stdout.fileno(), 8192)
        finally:
            selector.close()
        responses = {x["id"]: x for x in map(json.loads, data.splitlines())}
        self.assertEqual(responses[2]["result"], {})
        self.assertTrue(responses[1]["result"]["isError"])
        self.assertEqual(responses[1]["result"]["structuredContent"], {
            "stage": "cancelled", "remote_outcome": "unconfirmed", "job_id": "original-job"})
        self.assertLess(time.monotonic() - start, 1)
        self.proc.stdin.close()
        self.proc.wait(timeout=3)

    def test_eof_cancels_owned_process_tree(self):
        self.start_call()
        self.proc.stdin.close()
        self.proc.wait(timeout=3)

    def test_eof_with_unread_large_response_is_bounded(self):
        self.start_call(large=True)
        time.sleep(.15)
        self.proc.stdin.close()
        self.proc.wait(timeout=3)
