"""Temporary-network retry for the Kimi updater. No native install."""

import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from support import SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))

import bounded  # noqa: E402
import entry  # noqa: E402
import genstate  # noqa: E402
import updater  # noqa: E402

SECRET = "SENTINEL_SECRET_http://user:password@evil.example/secret"
SHA = "b" * 40
CURRENT = "a" * 40


def _script(body):
    return "import sys\n" + body


NET = _script(
    f"sys.stdout.write({SECRET!r} + '\\n')\n"
    "sys.stdout.write('ERROR: Could not fetch URL\\n')\n"
    "sys.stdout.write('NewConnectionError: connection reset by peer\\n')\n"
    f"sys.stderr.write({SECRET!r} + '\\n')\n"
    "raise SystemExit(1)\n"
)
RATE = _script(
    "sys.stderr.write('The requested URL returned error: 403\\n')\n"
    "sys.stderr.write('secondary rate limit\\nRetry-After: 12\\n')\n"
    f"sys.stderr.write({SECRET!r} + '\\n')\n"
    "raise SystemExit(128)\n"
)
AUTH = _script(
    f"sys.stderr.write('Authentication failed for {SECRET}\\n')\n"
    "sys.stderr.write('The requested URL returned error: 403\\n')\n"
    "raise SystemExit(128)\n"
)
PERM = _script(
    "sys.stderr.write('The requested URL returned error: 403\\n')\n"
    "raise SystemExit(128)\n"
)
CERT = _script(
    "sys.stderr.write('SSL: CERTIFICATE_VERIFY_FAILED\\n')\n"
    f"sys.stderr.write({SECRET!r} + '\\n')\n"
    "raise SystemExit(1)\n"
)
HANG = _script(
    "import time\n"
    f"sys.stderr.write({SECRET!r} + '\\n')\n"
    "sys.stderr.flush()\n"
    "time.sleep(30)\n"
)


def _public(exc):
    return json.dumps({
        "error": str(exc),
        "category": getattr(exc, "category", None),
        "retry_after": getattr(exc, "retry_after", None),
    })


def _run(body, *, transport, timeout=5):
    return bounded.run(
        [sys.executable, "-c", body], "", timeout=timeout, transport=transport
    )


class Clock:
    def __init__(self):
        self.now = 1_700_000_000.0

    def __call__(self):
        return self.now


class ClassificationTests(unittest.TestCase):
    def assertClean(self, exc):
        blob = _public(exc)
        self.assertNotIn("SENTINEL", blob)
        self.assertNotIn("password", blob)
        self.assertNotIn("evil.example", blob)
        self.assertNotIn("http://", blob)

    def test_subprocess_classification(self):
        with self.assertRaises(RuntimeError) as caught:
            _run(NET, transport=True)
        self.assertClean(caught.exception)
        self.assertEqual(caught.exception.category, "temporary_network")
        self.assertIn("command failed", str(caught.exception))

        with self.assertRaises(RuntimeError) as caught:
            _run(RATE, transport=True)
        self.assertClean(caught.exception)
        self.assertEqual(caught.exception.category, "rate_limited")
        self.assertEqual(caught.exception.retry_after, 12.0)

        with self.assertRaises(RuntimeError) as caught:
            _run(AUTH, transport=True)
        self.assertClean(caught.exception)
        self.assertEqual(caught.exception.category, "authentication")

        with self.assertRaises(RuntimeError) as caught:
            _run(PERM, transport=True)
        self.assertEqual(caught.exception.category, "permission")

        with self.assertRaises(RuntimeError) as caught:
            _run(CERT, transport=True)
        self.assertClean(caught.exception)
        self.assertEqual(caught.exception.category, "certificate")

        with self.assertRaises(RuntimeError) as caught:
            _run("import sys\nraise SystemExit(128)\n", transport=True)
        self.assertIsNone(getattr(caught.exception, "category", None))

        with self.assertRaises(RuntimeError) as caught:
            _run(NET, transport=False)
        self.assertIsNone(getattr(caught.exception, "category", None))

        with self.assertRaises(RuntimeError) as caught:
            _run(
                _script(
                    "sys.stderr.write(\"fatal: unable to access 'https://example.invalid/r.git/': "
                    "Proxy CONNECT aborted\\n\")\nraise SystemExit(128)\n"
                ),
                transport=True,
            )
        self.assertClean(caught.exception)
        self.assertEqual(caught.exception.category, "temporary_network")
        with self.assertRaises(RuntimeError) as caught:
            _run(
                _script(
                    "sys.stdout.write('ERROR: NewConnectionError: connection refused\\n')\n"
                    "sys.stdout.write('ERROR: Could not find a version that satisfies the requirement example\\n')\n"
                    "sys.stdout.write('ERROR: No matching distribution found for example\\n')\n"
                    "raise SystemExit(1)\n"
                ),
                transport=True,
            )
        self.assertEqual(caught.exception.category, "temporary_network")
        with self.assertRaises(RuntimeError) as caught:
            _run(
                _script(
                    "sys.stdout.write('A' * 70000)\n"
                    "sys.stderr.write('fatal: failed to connect to server\\n')\n"
                    "raise SystemExit(128)\n"
                ),
                transport=True,
            )
        self.assertEqual(caught.exception.category, "temporary_network")
        self.assertNotIn("AAAA", str(caught.exception))
        with self.assertRaises(RuntimeError) as caught:
            _run(
                _script("sys.stdout.write('ERROR: THESE PACKAGES DO NOT MATCH THE HASHES\\n')\nraise SystemExit(1)\n"),
                transport=True,
            )
        self.assertEqual(caught.exception.category, "bad_content")
        with self.assertRaises(RuntimeError) as caught:
            _run(
                _script("sys.stdout.write('ERROR: ResolutionImpossible: conflicting dependencies\\n')\nraise SystemExit(1)\n"),
                transport=True,
            )
        self.assertEqual(caught.exception.category, "resolver")

    def test_timeout_classification_follows_the_phase(self):
        with self.assertRaises(bounded.CommandTimedOut) as caught:
            _run(HANG, transport=True, timeout=0.4)
        self.assertClean(caught.exception)
        self.assertEqual(caught.exception.category, "temporary_network")
        self.assertIsInstance(caught.exception, RuntimeError)

        with self.assertRaises(bounded.CommandTimedOut) as plain:
            _run(HANG, transport=False, timeout=0.4)
        self.assertIsNone(plain.exception.category)


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config_path = make_config(root / "cfg")
        self.adapter = json.loads(self.config_path.read_text())
        os.environ["MINDIE_KIMI_CONFIG"] = str(self.config_path)
        generation = root / "generation"
        generation.mkdir()
        genstate.write_current({
            "generation": str(generation),
            "python": sys.executable,
            "adapter_config": str(self.config_path),
            "sha": CURRENT,
        }, self.adapter)
        launcher = genstate.launch_dir(self.adapter) / CURRENT / "mindie_launch.py"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("# retained launcher\n")
        self.launcher = launcher
        self.calls = {"stage": 0, "switch": 0, "resolve": 0}
        self.clock = Clock()
        self._time = patch("updater.time.time", self.clock)
        self._time.start()
        self._feed = updater._feed_sync
        self._log = updater._logging_maintenance
        self._resolve = updater.resolve_main
        self._stage = updater.stage_generation
        self._switch = updater.switch
        updater._feed_sync = lambda *args, **kwargs: True
        updater._logging_maintenance = lambda *args, **kwargs: None

    def tearDown(self):
        updater._feed_sync = self._feed
        updater._logging_maintenance = self._log
        updater.resolve_main = self._resolve
        updater.stage_generation = self._stage
        updater.switch = self._switch
        self._time.stop()
        os.environ.pop("MINDIE_KIMI_CONFIG", None)
        self.tmp.cleanup()

    def _check(self):
        return updater.check(self.adapter)

    def test_same_sha_retries_past_three_without_recover(self):
        def resolve(remote, deadline):
            self.calls["resolve"] += 1
            return SHA

        def stage(sha, remote, adapter, deadline, build=None):
            self.calls["stage"] += 1
            if self.calls["stage"] <= 4:
                _run(NET, transport=True)
            return Path(adapter["state_dir"]), Path(sys.executable), self.config_path

        def switch(*args, **kwargs):
            self.calls["switch"] += 1
            genstate.write_current({
                "generation": str(Path(self.adapter["state_dir"])),
                "python": sys.executable,
                "adapter_config": str(self.config_path),
                "sha": SHA,
            }, self.adapter)

        updater.resolve_main = resolve
        updater.stage_generation = stage
        updater.switch = switch
        self.assertEqual(self._check(), 1)
        failed = genstate.read_json(genstate.failed_path(self.adapter))
        self.assertEqual(failed["failure_class"], "temporary_network")
        self.assertEqual(failed["count"], 1)
        self.assertGreater(failed["next_retry_at"], self.clock.now)
        self.assertEqual(failed["first_failure_at"], self.clock.now)
        self.assertNotIn("SENTINEL", json.dumps(failed))
        view = entry._updater_view()
        self.assertEqual(view["recovery"], "automatic")
        self.assertEqual(view["next_retry_at"], failed["next_retry_at"])
        argv = shlex.split(view["command"])
        self.assertTrue(Path(argv[1]).is_absolute())
        self.assertEqual(argv[-2:], ["updater", "status"])
        self.assertNotIn("scripts/updater.py", view["command"])
        before = genstate.failed_path(self.adapter).read_bytes()
        self.assertEqual(entry._updater_view()["recovery"], "automatic")
        self.assertEqual(genstate.failed_path(self.adapter).read_bytes(), before)

        self.assertEqual(self._check(), 0)
        self.assertEqual(self.calls["stage"], 1)
        self.assertEqual(
            genstate.read_status(self.adapter)["result"], "retry-waiting"
        )

        for expected in (2, 3, 4):
            self.clock.now += 8 * 3600
            self.assertEqual(self._check(), 1)
            failed = genstate.read_json(genstate.failed_path(self.adapter))
            self.assertEqual(failed["count"], expected)
            self.assertNotEqual(
                genstate.read_status(self.adapter)["result"], "suppressed-known-failed"
            )
        self.clock.now += 8 * 3600
        self.assertEqual(self._check(), 0)
        self.assertEqual(self.calls["switch"], 1)
        self.assertEqual(self.calls["stage"], 5)
        failed = genstate.read_json(genstate.failed_path(self.adapter))
        self.assertNotIn("next_retry_at", failed)
        self.assertNotIn("error", failed)
        self.assertNotIn("failure_class", failed)
        self.assertEqual(failed["count"], 4)
        self.assertIn("first_failure_at", failed)
        self.assertNotIn("SENTINEL", json.dumps(failed))
        self.assertNotEqual(entry._updater_view().get("recovery"), "manual")

    def test_legacy_record_stays_suppressed(self):
        def stage(*args, **kwargs):
            raise AssertionError("staged")

        updater.resolve_main = lambda remote, deadline: SHA
        updater.stage_generation = stage
        genstate.atomic_write(genstate.failed_path(self.adapter), {
            "sha": SHA, "error": "boom", "at": 1,
        })
        self.clock.now += 8 * 3600
        self.assertEqual(self._check(), 0)
        failed = genstate.read_json(genstate.failed_path(self.adapter))
        self.assertEqual(failed["error"], "boom")
        self.assertNotIn("failure_class", failed)
        self.assertEqual(
            genstate.read_status(self.adapter)["result"], "suppressed-known-failed"
        )
        view = entry._updater_view()
        self.assertEqual(view["recovery"], "manual")
        self.assertEqual(shlex.split(view["command"])[-1], "recover")
        self.assertNotIn("scripts/updater.py", view["command"])

    def test_certificate_retries_after_backoff_without_quarantine(self):
        def stage(sha, remote, adapter, deadline, build=None):
            self.calls["stage"] += 1
            _run(CERT, transport=True)

        updater.resolve_main = lambda remote, deadline: SHA
        updater.stage_generation = stage
        self.assertEqual(self._check(), 1)
        failed = genstate.read_json(genstate.failed_path(self.adapter))
        self.assertEqual(failed["failure_class"], "certificate")
        self.assertNotIn("quarantined", failed)
        self.assertGreater(failed["next_retry_at"], self.clock.now)
        self.assertNotIn("SENTINEL", json.dumps(failed))
        self.assertEqual(entry._updater_view()["recovery"], "action_required")
        self.assertEqual(self._check(), 0)
        self.assertEqual(self.calls["stage"], 1)
        self.clock.now = failed["next_retry_at"] + 1
        self.assertEqual(self._check(), 1)
        self.assertEqual(self.calls["stage"], 2)

    def test_resolve_success_resets_consecutive_counter(self):
        genstate.write_status({
            "resolve_wait": True,
            "resolve_failures": 5,
            "resolve_failure_at": 10,
            "next_retry_at": self.clock.now + 600,
            "error": "temporary network failure",
        }, self.adapter)
        updater._clear_resolve_wait(self.adapter)
        status = genstate.read_status(self.adapter)
        self.assertEqual(status.get("resolve_failures"), 0)
        self.assertEqual(status.get("resolve_failures_seen"), 5)
        self.assertEqual(status.get("resolve_failure_at"), 10)
        self.assertNotIn("next_retry_at", status)
        err = RuntimeError("temporary network failure")
        err.category = "temporary_network"
        updater._note_resolve_failure(self.adapter, err, CURRENT)
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["resolve_failures"], 1)
        self.assertLess(status["next_retry_at"] - self.clock.now, 400)

    def test_history_sha_is_not_an_active_failure(self):
        genstate.atomic_write(genstate.failed_path(self.adapter), {
            "sha": SHA,
            "count": 4,
            "first_failure_at": 10,
            "history": [{"at": 10, "class": "temporary_network"}],
        })
        view = entry._updater_view()
        self.assertEqual(view["recovery"], "recovered")
        self.assertNotIn("failed_sha", view)
        stored = genstate.read_json(genstate.failed_path(self.adapter))
        self.assertEqual(stored["sha"], SHA)
        self.assertEqual(stored["history"][0]["class"], "temporary_network")

    def test_fifo_metadata_is_unavailable_immediately(self):
        path = genstate.failed_path(self.adapter)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        os.mkfifo(path)
        started = time.monotonic()
        view = entry._updater_view()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(view, {"status": "unavailable"})

    def test_unknown_switch_timeout_stays_guarded(self):
        def stage(sha, remote, adapter, deadline, build=None):
            self.calls["stage"] += 1
            return Path(adapter["state_dir"]), Path(sys.executable), self.config_path

        def switch(*args, **kwargs):
            self.calls["switch"] += 1
            raise bounded.CommandTimedOut("command timed out after 5s")

        updater.resolve_main = lambda remote, deadline: SHA
        updater.stage_generation = stage
        updater.switch = switch
        self.assertEqual(self._check(), 1)
        failed = genstate.read_json(genstate.failed_path(self.adapter))
        self.assertNotIn("failure_class", failed)
        self.assertNotIn("next_retry_at", failed)
        self.assertNotIn("SENTINEL", json.dumps(failed))
        self.clock.now += 8 * 3600
        self.assertEqual(self._check(), 0)
        self.assertEqual(self.calls["switch"], 1)
        self.assertEqual(
            genstate.read_status(self.adapter)["result"], "suppressed-known-failed"
        )

    def test_resolve_failure_does_not_busy_loop(self):
        def resolve(remote, deadline):
            self.calls["resolve"] += 1
            _run(NET, transport=True)

        updater.resolve_main = resolve
        self.assertEqual(self._check(), 1)
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["resolve_failure_class"], "temporary_network")
        self.assertGreater(status["next_retry_at"], self.clock.now)
        self.assertNotIn("SENTINEL", json.dumps(status))
        self.assertEqual(self._check(), 0)
        self.assertEqual(self.calls["resolve"], 1)
        self.clock.now = status["next_retry_at"] + 1
        self.assertEqual(self._check(), 1)
        self.assertEqual(self.calls["resolve"], 2)
        self.assertEqual(genstate.read_status(self.adapter)["resolve_failures"], 2)


if __name__ == "__main__":
    unittest.main()
