"""Release selection uses the actual host version and shared verified catalog."""

import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from support import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import release_metadata
import updater
from mindie_knowledge.releases_io import ReleaseFetchError


class ReleaseSelectionTests(unittest.TestCase):
    def test_native_version_selects_only_compatible_formal_release_then_verifies_source(self):
        declaration = release_metadata.metadata(SCRIPTS.parent, "a" * 40)
        records = [dict(
            id=1, tag_name="v" + declaration["version"],
            draft=False, prerelease=False, metadata=declaration,
        )]
        version_result = subprocess.CompletedProcess(["kimi", "--version"], 0, "2.2.0\n", "")
        with patch.object(release_metadata.subprocess, "run", return_value=version_result) as native, \
             patch.object(release_metadata, "fetch_releases", return_value=records), \
             patch.object(release_metadata, "verify_release_source", return_value=declaration) as verify:
            result = release_metadata.select_candidate("/selected/kimi", "0.0.1")
            self.assertEqual(result["candidate"]["metadata"], declaration)
            self.assertEqual(result["harness_version"], "2.2.0")
            self.assertEqual(native.call_args.args[0], ["/selected/kimi", "--version"])
            self.assertEqual(
                native.call_args.kwargs.get("creationflags"),
                getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(verify.call_args.args[0], declaration)
            for compatible in ("2.3.1\n", "2.3.2\n", "2.4.0-rc.1\n", "2.99.0\n"):
                version_result.stdout = compatible
                result = release_metadata.select_candidate("/selected/kimi", "0.0.1")
                self.assertEqual(result["candidate"]["metadata"], declaration)
            verify_calls = verify.call_count
            for incompatible in ("2.1.0\n", "3.0.0-alpha.1\n", "3.0.0\n"):
                version_result.stdout = incompatible
                result = release_metadata.select_candidate("/selected/kimi", "0.0.1")
                self.assertEqual(result["status"], "no_compatible_release")
            self.assertEqual(verify.call_count, verify_calls)

    def test_source_verification_failure_stops_selection(self):
        declaration = release_metadata.metadata(SCRIPTS.parent, "a" * 40)
        records = [dict(
            id=1, tag_name="v" + declaration["version"],
            draft=False, prerelease=False, metadata=declaration,
        )]
        with patch.object(release_metadata.subprocess, "run", return_value=(
                subprocess.CompletedProcess([], 0, "2.1.1\n", ""))), \
             patch.object(release_metadata, "fetch_releases", return_value=records), \
             patch.object(release_metadata, "verify_release_source",
                          side_effect=RuntimeError("release_source_declaration_mismatch")):
            with self.assertRaisesRegex(RuntimeError, "release_source_declaration_mismatch"):
                release_metadata.select_candidate("/selected/kimi", "0.0.1")

    def test_catalog_child_reserves_cleanup_inside_existing_check_deadline(self):
        current = dict(generation=str(SCRIPTS.parent))
        with patch("paths.resolve_kimi_bin", return_value="/selected/kimi"), \
             patch.object(updater.time, "monotonic", return_value=100), \
             patch.object(updater, "bounded_run", return_value=json.dumps(
                 dict(status="up_to_date", candidate=None))) as run:
            updater._release_catalog({}, current, deadline=125 + updater.RESERVE)
        self.assertEqual(run.call_args.kwargs["timeout"], 23)
        self.assertEqual(Path(run.call_args.args[0][1]).name, "release_metadata.py")

    def test_select_main_emits_static_fetch_error_without_traceback(self):
        err = ReleaseFetchError("release_http_error", status=403)
        stdout = io.StringIO()
        with patch.object(release_metadata, "select_candidate", side_effect=err), \
             patch.object(sys, "argv", ["release_metadata.py", "select",
                                       "--kimi-bin", "/kimi", "--installed-version", "0.1.0"]), \
             patch("sys.stdout", stdout):
            with self.assertRaises(SystemExit) as ctx:
                release_metadata.main()
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(json.loads(stdout.getvalue()), {
            "error": {"code": "release_http_error", "status": 403, "type": "ReleaseFetchError"},
        })

    def test_typed_catalog_error_rebuilds_http_malformed_and_host_misses(self):
        http = RuntimeError("command failed (1)")
        http.returncode = 1
        http.mindie_output = json.dumps({
            "error": {"type": "ReleaseFetchError", "code": "release_http_error", "status": 403},
        })
        rebuilt = release_metadata.typed_catalog_error(http)
        self.assertIsInstance(rebuilt, release_metadata.CatalogError)
        self.assertEqual(rebuilt.code, "release_http_error")
        self.assertEqual(rebuilt.category, "temporary_network")
        self.assertEqual(rebuilt.returncode, 1)

        feed = RuntimeError("command failed (1)")
        feed.mindie_output = json.dumps({
            "error": {"type": "ReleaseFetchError", "code": "invalid_release_json"},
        })
        rebuilt = release_metadata.typed_catalog_error(feed)
        self.assertEqual(rebuilt.code, "invalid_release_json")
        self.assertEqual(rebuilt.category, "temporary_network")

        limited = RuntimeError("command failed (1)")
        limited.mindie_output = json.dumps({
            "error": {"type": "ReleaseFetchError", "code": "release_http_error", "status": 429},
        })
        self.assertEqual(release_metadata.typed_catalog_error(limited).category, "rate_limited")

        host = RuntimeError("command failed (1)")
        host.mindie_output = json.dumps({"error": {"type": "CalledProcessError"}})
        self.assertIsInstance(release_metadata.typed_catalog_error(host), FileNotFoundError)

        plain = RuntimeError("command failed (1)")
        self.assertIs(release_metadata.typed_catalog_error(plain), plain)

    def test_release_catalog_preserves_child_fetch_code(self):
        child = RuntimeError("command failed (1)")
        child.returncode = 1
        child.mindie_output = json.dumps({
            "error": {"type": "ReleaseFetchError", "code": "release_http_error", "status": 403},
        })
        current = dict(generation=str(SCRIPTS.parent))
        with patch("paths.resolve_kimi_bin", return_value="/selected/kimi"), \
             patch.object(updater.time, "monotonic", return_value=100), \
             patch.object(updater, "bounded_run", side_effect=child):
            with self.assertRaises(release_metadata.CatalogError) as ctx:
                updater._release_catalog({}, current, deadline=125 + updater.RESERVE)
        self.assertEqual(ctx.exception.code, "release_http_error")
        self.assertEqual(ctx.exception.category, "temporary_network")

    def test_release_catalog_rejects_unusable_result(self):
        current = dict(generation=str(SCRIPTS.parent))
        with patch("paths.resolve_kimi_bin", return_value="/selected/kimi"), \
             patch.object(updater.time, "monotonic", return_value=100), \
             patch.object(updater, "bounded_run", return_value="[]"):
            with self.assertRaisesRegex(updater.CheckFailed, "unusable"):
                updater._release_catalog({}, current, deadline=125 + updater.RESERVE)

    def test_resolve_release_rejects_malformed_candidate(self):
        with patch.object(updater, "_release_catalog", return_value={"candidate": {"tag_name": "v1"}}):
            with self.assertRaisesRegex(updater.CheckFailed, "candidate is unusable"):
                updater.resolve_release("remote", {}, {}, deadline=1)

    def test_missing_kimi_bin_is_a_local_prerequisite(self):
        current = dict(generation=str(SCRIPTS.parent))
        with patch("paths.resolve_kimi_bin", return_value=None), \
             patch.object(updater.time, "monotonic", return_value=100):
            with self.assertRaises(FileNotFoundError):
                updater._release_catalog({}, current, deadline=125 + updater.RESERVE)


if __name__ == "__main__":
    unittest.main()

