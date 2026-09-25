"""Behavioral FTS probe checks. The unsupported double is not an old or Windows SQLite."""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import setup

CAPABILITY = "FTS5 contentless_delete=1 (SQLite >=3.43.0)"


def _exec(source, connect, namespace=None):
    real = sqlite3.connect
    sqlite3.connect = connect
    namespace = {} if namespace is None else namespace
    try:
        exec(source, namespace)
    finally:
        sqlite3.connect = real
    return namespace


class _Recording:
    def __init__(self, raw, name, closed):
        self._raw = raw
        self._name = name
        self._closed = closed

    def execute(self, *args, **kwargs):
        return self._raw.execute(*args, **kwargs)

    def close(self):
        self._closed.append(self._name)
        return self._raw.close()


def _recording_connect(seen, closed):
    real = sqlite3.connect

    def connect(name, *args, **kwargs):
        seen.append(name)
        return _Recording(real(name, *args, **kwargs), name, closed)

    return connect


class _Unsupported:
    def __init__(self):
        self.closed = False

    def execute(self, _sql, *_args):
        raise sqlite3.OperationalError("no such module: fts5")

    def close(self):
        self.closed = True


def _child_env():
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(source, cwd):
    return subprocess.run(
        [sys.executable, "-c", source],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=15,
        env=_child_env(),
    )


class FtsProbeTests(unittest.TestCase):
    def test_supported_ddl_succeeds_and_closes(self):
        seen, closed = [], []
        namespace = _exec(
            setup._FTS_PROBE,
            _recording_connect(seen, closed),
            {"missing": ["import-already-failed"]},
        )
        self.assertEqual(namespace["missing"], ["import-already-failed"])
        self.assertEqual(seen, [":memory:"])
        self.assertEqual(closed, [":memory:"])
        script = setup.probe_script("/probe/transcript.py")
        child = "missing = []\n" + script[script.rindex("import sqlite3\n") :]
        with tempfile.TemporaryDirectory() as tmp:
            completed = _run(child, tmp)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout.strip(), "OK")
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_unsupported_error_names_actual_version_and_closes(self):
        fake = _Unsupported()

        def connect(name, *args, **kwargs):
            self.assertEqual(name, ":memory:")
            return fake

        namespace = {"missing": ["x" * 500]}
        _exec(setup._FTS_PROBE, connect, namespace)
        self.assertTrue(fake.closed)
        text = namespace["missing"][0]
        self.assertTrue(text.startswith("sqlite " + sqlite3.sqlite_version + " lacks "))
        self.assertIn(CAPABILITY, text[:200])
        self.assertIn(sqlite3.sqlite_version, text[:200])
        self.assertIn("OperationalError", text)
        shown = ("MISSING: " + "; ".join(namespace["missing"])).strip()[:300]
        self.assertIn(sqlite3.sqlite_version, shown)
        self.assertIn(CAPABILITY, shown)

    def test_exit1_after_ok_is_rejected_before_writes(self):
        fault = "print('OK', flush=True)\nraise SystemExit(1)\n"
        continued = []
        with tempfile.TemporaryDirectory() as tmp:
            direct = _run(fault, tmp)
            self.assertEqual(direct.returncode, 1)
            self.assertEqual(direct.stdout, "OK\n")
            with patch.object(setup, "PROBE_SCRIPT", fault):
                with self.assertRaises(SystemExit) as caught:
                    setup.probe_runtime(sys.executable)
                    continued.append("write")
        self.assertEqual(continued, [])
        self.assertIn("knowledge runtime probe failed", str(caught.exception.code))


if __name__ == "__main__":
    unittest.main()
