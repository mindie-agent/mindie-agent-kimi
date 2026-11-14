"""Updater resolver and component transport failures must use shared fault eligibility."""
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import time
from unittest.mock import Mock, patch
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import component_update
import genstate
import updater
import diagnostic_support
from bounded import (
    CommandCancelled,
    CommandTimedOut,
    classify_transport_exception,
    classify_transport_text,
)
from mindie_diagnostics.ingestion import ingest
from mindie_diagnostics.integration import configure_reporting
from mindie_diagnostics.outbox import Outbox
from support import make_config


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


def _ssl_eof_error(secret='PRIVATE-HOST'):
    return ssl.SSLError(1, f'[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol ({secret})')


def _ssl_cert_error(secret='PRIVATE-CA'):
    err = ssl.SSLCertVerificationError(1, f'[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed ({secret})')
    err.reason = 'CERTIFICATE_VERIFY_FAILED'
    return err


def test_component_update_ssl_eof_is_connectivity_and_never_enqueues_bug_issue(tmp_path, capsys):
    policy, logs = diagnostic_support.reporting_config_path(), Path(os.environ['MINDIE_DIAGNOSTICS_ROOT'])
    configure_reporting(True, config=policy, roots=[str(logs)])

    cfg_path = make_config(tmp_path / 'profile')
    adapter = json.loads(cfg_path.read_text(encoding='utf-8'))
    gen = tmp_path / 'gen'
    gen.mkdir()
    gen_cfg = gen / 'adapter.json'
    gen_cfg.write_text(json.dumps(dict(adapter, domain_configs={})), encoding='utf-8')
    genstate.write_current(
        {'generation': str(gen), 'python': sys.executable, 'adapter_config': str(gen_cfg), 'sha': 'a' * 40},
        adapter,
    )

    with patch.object(updater, 'resolve_release', return_value={'status': 'up_to_date', 'candidate': None}), \
            patch('mindie_knowledge.component_runtime.source_baseline',
                  return_value={'runtime': {'mindie-knowledge': 'a' * 40}}), \
            patch('mindie_knowledge.component_runtime.discover', side_effect=_ssl_eof_error()):
        rc = updater._check_once(
            adapter, time.monotonic() + 300, force=False,
            build=Mock(), idle=Mock(), install=Mock(), lock_timeout=1,
        )
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out['result'] == 'degraded'
    assert out['failure_class'] == 'temporary_network'
    assert out['error'] == 'temporary network failure'
    assert 'PRIVATE-HOST' not in json.dumps(out)

    queue = Outbox(tmp_path / 'queue.db')
    assert ingest(logs, queue, config=policy)['enqueued'] == 0
    event = json.loads(next(Path(logs).glob('events/*/*.jsonl')).read_text(encoding='utf-8').splitlines()[-1])
    assert event['attributes']['stage'] == 'component_update'
    assert event['attributes']['category'] == 'connectivity'
    assert event['attributes']['error_code'] == 'release_transport_unavailable'
    assert event['reportable'] is False


def test_direct_ssl_and_http_transport_exceptions_never_masquerade_as_internal(tmp_path):
    policy, logs = diagnostic_support.reporting_config_path(), Path(os.environ['MINDIE_DIAGNOSTICS_ROOT'])
    configure_reporting(True, config=policy, roots=[str(logs)])
    queue = Outbox(tmp_path / 'queue.db')

    for exc in (
        _ssl_eof_error('DIRECT-SSL'),
        http.client.IncompleteRead(b'part', 64),
        socket.gaierror(-2, 'Name or service not known'),
    ):
        rec = updater._remember_failure({'state_dir': str(tmp_path)}, 'c' * 40, exc)
        assert rec['failure_class'] == 'temporary_network'
        assert rec['error'] == 'temporary network failure'
        updater._update_diagnostic('component_update', exc)

    assert ingest(logs, queue, config=policy)['enqueued'] == 0
    lines = [
        json.loads(line)
        for path in sorted(Path(logs).glob('events/*/*.jsonl'))
        for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    assert len(lines) == 3
    for event in lines:
        assert event['attributes']['category'] == 'connectivity'
        assert event['attributes']['error_code'] == 'update_transport_unavailable'
        assert event['reportable'] is False
    assert lines[0]['attributes']['error_type'] == 'SSLError'
    assert lines[0]['attributes']['errno'] == 1


@pytest.mark.parametrize('exc_factory', [
    lambda: _ssl_cert_error('DIRECT-CERT'),
    lambda: urllib.error.URLError(_ssl_cert_error('WRAPPED-CERT')),
])
def test_ssl_certificate_verification_failure_is_actionable_credentials_not_internal(tmp_path, exc_factory):
    policy, logs = diagnostic_support.reporting_config_path(), Path(os.environ['MINDIE_DIAGNOSTICS_ROOT'])
    configure_reporting(True, config=policy, roots=[str(logs)])

    typed = component_update.typed_catalog_error(exc_factory())
    assert isinstance(typed, component_update.CatalogError)
    assert typed.code == 'release_certificate_error'
    assert typed.category == 'certificate'

    rec = updater._remember_failure({'state_dir': str(tmp_path)}, 'd' * 40, typed)
    assert rec['failure_class'] == 'certificate'
    assert rec['error'] == 'certificate verification failed'

    updater._update_diagnostic('component_update', typed)
    queue = Outbox(tmp_path / 'queue.db')
    assert ingest(logs, queue, config=policy)['enqueued'] == 0
    event = json.loads(next(Path(logs).glob('events/*/*.jsonl')).read_text(encoding='utf-8').splitlines()[-1])
    assert event['attributes']['category'] == 'credentials'
    assert event['attributes']['error_code'] == 'update_credentials_required'
    assert event['attributes']['reason_code'] == 'release_certificate_error'
    assert event['reportable'] is False


def test_local_invalid_url_and_timeout_are_not_masked_as_transport(tmp_path):
    policy, logs = diagnostic_support.reporting_config_path(), Path(os.environ['MINDIE_DIAGNOSTICS_ROOT'])
    configure_reporting(True, config=policy, roots=[str(logs)])

    bad_url = http.client.InvalidURL('bad host')
    assert classify_transport_exception(bad_url) is None
    assert component_update.typed_catalog_error(bad_url) is bad_url
    updater._update_diagnostic('component_update', bad_url)

    queue = Outbox(tmp_path / 'queue.db')
    assert ingest(logs, queue, config=policy)['enqueued'] == 1
    event = json.loads(next(Path(logs).glob('events/*/*.jsonl')).read_text(encoding='utf-8').splitlines()[-1])
    assert event['attributes']['category'] == 'internal'
    assert event['attributes']['error_code'] == 'update_internal_failure'
    assert event['reportable'] is True

    timeout_exc = TimeoutError('component release discovery budget exhausted')
    assert classify_transport_exception(timeout_exc) is None
    assert component_update.typed_catalog_error(timeout_exc) is timeout_exc
    with patch('mindie_knowledge.component_runtime.discover', side_effect=timeout_exc), \
            pytest.raises(TimeoutError, match='budget exhausted'):
        component_update._discover_catalog(sys.executable, {})


def test_subprocess_tls_reset_and_certificate_phrases_classify_accurately():
    for reset_stderr in (
        'ssl.SSLEOFError: [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1006)',
        'fatal: unable to access https://github.com/org/repo.git/: OpenSSL SSL_connect: SSL_ERROR_SYSCALL in connection to github.com:443',
        'curl: (35) Recv failure',
        'error: peer closed connection without sending TLS close_notify',
        'fatal: schannel: failed to receive handshake, SSL/TLS connection failed',
    ):
        assert classify_transport_text(reset_stderr) == ('temporary_network', None)

    for cert_stderr in (
        'error: invalid peer certificate: UnknownIssuer',
        'fatal: schannel: next InitializeSecurityContext failed: SEC_E_UNTRUSTED_ROOT (0x80090325)',
    ):
        assert classify_transport_text(cert_stderr) == ('certificate', None)
