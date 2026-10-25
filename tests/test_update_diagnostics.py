"""Updater resolver failures must use shared fault eligibility."""
import json
import os
from pathlib import Path
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import updater
import diagnostic_support
from bounded import CommandCancelled, CommandTimedOut
from mindie_diagnostics.ingestion import ingest
from mindie_diagnostics.integration import configure_reporting
from mindie_diagnostics.outbox import Outbox


@pytest.mark.parametrize('error,expected', [(ModuleNotFoundError('PRIVATE'), 1),
                                           (urllib.error.URLError('PRIVATE'), 0),
                                           (CommandCancelled('PRIVATE'), 0),
                                           (CommandTimedOut('PRIVATE', category='temporary_network'), 0)])
def test_resolver_failure_is_visible_and_only_product_fault_is_queued(tmp_path, error, expected):
    policy, logs = diagnostic_support.reporting_config_path(), Path(os.environ['MINDIE_DIAGNOSTICS_ROOT'])
    configure_reporting(True, config=policy, roots=[str(logs)])
    ref = updater._update_diagnostic('resolve_release', error)
    assert ref['recorded']
    queue = Outbox(tmp_path / 'queue.db')
    assert ingest(logs, queue, config=policy)['enqueued'] == expected
    events = ''.join(path.read_text() for path in logs.glob('events/*/*.jsonl'))
    assert 'PRIVATE' not in events
    assert json.loads(events.splitlines()[-1])['reportable'] is bool(expected)


def test_unclassified_command_failure_keeps_only_bounded_reason(tmp_path):
    error = RuntimeError('command failed (1)')
    error.returncode = 1
    error.reason = 'fatal: https://<redacted>@example.invalid/repo failed; token=LOCAL-CANARY'
    record = updater._remember_failure({'state_dir': str(tmp_path)}, 'a' * 40, error)
    assert 'command failed (1)' in record['error']
    assert 'fatal:' in record['error']
    assert 'LOCAL-CANARY' not in json.dumps(record)
    assert len(record['error']) <= 500


def test_classified_failure_keeps_static_message_not_stderr(tmp_path):
    error = RuntimeError('command failed (1)')
    error.category = 'temporary_network'
    error.reason = 'PRIVATE-SECRET'
    record = updater._remember_failure({'state_dir': str(tmp_path)}, 'b' * 40, error)
    assert record['error'] == 'temporary network failure'
    assert record['failure_class'] == 'temporary_network'
    assert 'PRIVATE-SECRET' not in json.dumps(record)
