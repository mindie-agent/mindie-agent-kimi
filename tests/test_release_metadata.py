"""Release selection uses the actual host version and shared verified catalog."""

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
            self.assertEqual(verify.call_args.args[0], declaration)
            version_result.stdout = "2.1.2\n"
            result = release_metadata.select_candidate("/selected/kimi", "0.0.1")
            self.assertEqual(result["status"], "no_compatible_release")
            self.assertEqual(verify.call_count, 1)

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


if __name__ == "__main__":
    unittest.main()
