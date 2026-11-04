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


def _catalog_error(code, *, category='temporary_network', status=None):
    import release_metadata
    error = release_metadata.CatalogError(code)
    error.code = code
    error.category = category
    error.returncode = 1
    if status is not None:
        error.status = status
    return error


@pytest.mark.parametrize('error,expected', [(ModuleNotFoundError('PRIVATE'), 1),
                                           (urllib.error.URLError('PRIVATE'), 0),
                                           (CommandCancelled('PRIVATE'), 0),
                                           (CommandTimedOut('PRIVATE', category='temporary_network'), 0),
                                           (FileNotFoundError('PRIVATE'), 0),
                                           (_catalog_error('release_http_error'), 0),
                                           (_catalog_error('invalid_release_json'), 0)])
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


def test_catalog_http_error_is_connectivity_not_internal(tmp_path):
    policy, logs = diagnostic_support.reporting_config_path(), Path(os.environ['MINDIE_DIAGNOSTICS_ROOT'])
    configure_reporting(True, config=policy, roots=[str(logs)])
    updater._update_diagnostic('resolve_release', _catalog_error('release_http_error', status=403))
    event = json.loads(next(Path(logs).glob('events/*/*.jsonl')).read_text().splitlines()[-1])
    assert event['attributes']['category'] == 'connectivity'
    assert event['attributes']['error_code'] == 'release_http_error'
    assert event['attributes']['error_type'] == 'CatalogError'
    assert event['attributes']['exit_code'] == 1
    assert event['reportable'] is False


def test_malformed_release_feed_is_not_an_internal_product_fault(tmp_path):
    policy, logs = diagnostic_support.reporting_config_path(), Path(os.environ['MINDIE_DIAGNOSTICS_ROOT'])
    configure_reporting(True, config=policy, roots=[str(logs)])
    updater._update_diagnostic('resolve_release', _catalog_error('invalid_release_json'))
    event = json.loads(next(Path(logs).glob('events/*/*.jsonl')).read_text().splitlines()[-1])
    assert event['attributes']['category'] == 'connectivity'
    assert event['attributes']['error_code'] == 'update_transport_unavailable'
    assert event['reportable'] is False


def test_unclassified_resolve_runtimeerror_remains_internal(tmp_path):
    policy, logs = diagnostic_support.reporting_config_path(), Path(os.environ['MINDIE_DIAGNOSTICS_ROOT'])
    configure_reporting(True, config=policy, roots=[str(logs)])
    error = RuntimeError('command failed (1)')
    error.returncode = 1
    updater._update_diagnostic('resolve_release', error)
    event = json.loads(next(Path(logs).glob('events/*/*.jsonl')).read_text().splitlines()[-1])
    assert event['attributes']['category'] == 'internal'
    assert event['attributes']['error_code'] == 'update_internal_failure'
    assert event['reportable'] is True


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
