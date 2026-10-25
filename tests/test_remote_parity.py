"""Remote MCP parity for the Kimi Code plugin."""

import base64
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))

PNG_1X1 = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlKkAAAAASUVORK5CYII='
)


def test_remote_content_keeps_image_block_and_structured_content():
    import mcp_server

    result = mcp_server.remote_result('remote_ReadMediaFile', {
        'text': 'Remote image read shot.png (image/png, 67 bytes).',
        'result': {'outcome': 'success', 'status': 'ok',
                   'extra': {'mime_type': 'image/png'}},
        'content': [{
            'type': 'image', 'mimeType': 'image/png',
            'data': base64.b64encode(PNG_1X1).decode('ascii'),
        }],
    })
    assert result['isError'] is False
    image = [block for block in result['content'] if block.get('type') == 'image']
    assert len(image) == 1
    assert base64.b64decode(image[0]['data']) == PNG_1X1
    assert result['structuredContent']['extra']['mime_type'] == 'image/png'


def test_remote_text_page_over_256k_survives():
    import mcp_server

    page = ('kimi-page-' * 30000)[:300 * 1024]
    result = mcp_server.remote_result('remote_Read', {
        'text': page, 'result': {'outcome': 'success', 'status': 'ok'},
    })
    assert result['content'][0]['text'] == page
    assert len(page.encode()) > 256 * 1024


def test_real_bounded_child_keeps_page_above_local_cap():
    import bounded

    page = ('kimi-bounded-' * 30000)[:300 * 1024]
    script = "import sys; sys.stdout.write(('kimi-bounded-' * 30000)[:300 * 1024])"
    with tempfile.TemporaryDirectory() as directory:
        script_path = Path(directory) / 'generate_page.py'
        script_path.write_text(script, encoding='utf-8')
        output = bounded.run(
            [sys.executable, str(script_path)], '', timeout=10,
            max_output=bounded.REMOTE_MAX_OUTPUT,
        )
    assert output == page
    assert len(output) > 256 * 1024


def test_nonzero_native_bash_is_output_not_helper_error():
    import mcp_server

    result = mcp_server.remote_result('remote_Bash', {
        'text': 'exit_code: 42\n[output]\nno',
        'result': {'outcome': 'failed', 'status': 'failed', 'state': 'failed',
                   'exit_code': 42},
    })
    assert result['isError'] is False
    assert result['structuredContent']['exit_code'] == 42
    assert 'exit_code: 42' in result['content'][0]['text']


def test_task_output_finds_nested_exit_code():
    import mcp_server

    result = mcp_server.remote_result('remote_TaskOutput', {
        'text': 'status: failed\nexit_code: 9',
        'result': {
            'outcome': 'failed', 'status': 'failed',
            'job': {'remote_status': {'exit_code': 9}},
        },
    })
    assert result['isError'] is False
    assert result['structuredContent']['job']['remote_status']['exit_code'] == 9


def test_helper_failure_stays_an_error():
    import mcp_server

    result = mcp_server.remote_result('remote_Bash', {
        'text': 'connection failed',
        'result': {'outcome': 'failed', 'status': 'connection_unavailable',
                   'error_details': {'submission_state': 'not_sent'}},
    })
    assert result['isError'] is True
    assert result['structuredContent']['error_details']['submission_state'] == 'not_sent'


def test_failed_command_without_observed_exit_code_stays_an_error():
    import mcp_server

    result = mcp_server.remote_result('remote_Bash', {
        'text': 'Remote command failed.',
        'result': {'outcome': 'failed', 'status': 'failed', 'state': 'failed'},
    })
    assert result['isError'] is True
    assert 'exit_code' not in result['structuredContent']


def test_uncertain_command_with_exit_code_stays_an_error():
    import mcp_server

    result = mcp_server.remote_result('remote_Bash', {
        'text': 'Remote outcome unconfirmed.',
        'result': {
            'outcome': 'failed', 'status': 'failed', 'state': 'failed',
            'exit_code': 7,
            'error_details': {'category': 'rpc_timeout', 'submission_state': 'uncertain'},
            'unknown': ['reply lost'],
        },
    })
    assert result['isError'] is True
    assert result['structuredContent']['exit_code'] == 7
    assert result['structuredContent']['error_details']['submission_state'] == 'uncertain'


def test_nested_operation_completed_survives_local_recording_failure():
    import mcp_server

    result = mcp_server.remote_result('remote_Bash', {
        'text': 'Remote command succeeded; local recording failed.',
        'result': {
            'outcome': 'failed', 'status': 'local_recording_failed',
            'execution_outcome': 'success', 'state': 'succeeded', 'exit_code': 0,
            'error_details': {'category': 'local_state', 'operation_completed': True},
        },
    })
    assert result['isError'] is False
    assert result['structuredContent']['execution_outcome'] == 'success'


def test_remote_budget_covers_native_inner_wait_plus_grace():
    import mindie_launch

    cases = [
        ('remote_Bash', {'timeout': 300}, 300.0),
        ('remote_Read', {}, 300.0),
        ('remote_Glob', {}, 300.0),
        ('remote_Grep', {}, 300.0),
        ('remote_TaskOutput', {}, 0.0),
        ('remote_bash', {'yield_time_ms': 120000}, 120.0),
    ]
    for name, args, inner in cases:
        outer = mindie_launch.remote_budget(name, args)
        assert outer >= inner + mindie_launch.REMOTE_GRACE_SECONDS, name
        assert outer >= mindie_launch.REMOTE_MIN_BUDGET_SECONDS, name
        if inner:
            assert outer > inner, name
    assert mindie_launch.remote_budget('remote_Bash', {'timeout': 300}) == 360.0
    assert mindie_launch.remote_budget(
        'remote_Bash', {'timeout': 300, 'run_in_background': True}) == 180.0


def _dispatch_fixture(tmp_path):
    import mindie_launch

    state = tmp_path / 'state'
    generation = tmp_path / 'generation'
    (generation / 'scripts').mkdir(parents=True)
    (generation / 'scripts' / 'mcp_server.py').touch()
    config = tmp_path / 'kimi.json'
    config.write_text(json.dumps({'state_dir': str(state)}))
    (state / 'update').mkdir(parents=True)
    (state / 'update' / 'current.json').write_text(json.dumps({
        'generation': str(generation), 'python': sys.executable,
        'adapter_config': str(config), 'sha': 'a' * 40,
    }))
    return mindie_launch, config, state


def test_dispatch_uses_remote_budget_and_remote_output_cap(tmp_path):
    mindie_launch, config, _state = _dispatch_fixture(tmp_path)
    response = {'jsonrpc': '2.0', 'id': 'bash-1', 'result': {'content': []}}
    raw = json.dumps({
        'jsonrpc': '2.0', 'id': 'bash-1', 'method': 'tools/call',
        'params': {'name': 'remote_Bash', 'arguments': {
            'command': 'sleep 1', 'timeout': 300,
        }},
    }).encode()
    with patch.dict(os.environ, {'MINDIE_KIMI_CONFIG': str(config)}), \
            patch.object(mindie_launch.bounded, 'run',
                         return_value=json.dumps(response)) as runner:
        assert mindie_launch._dispatch('remote', raw, 'bash-1') == response
    kwargs = runner.call_args.kwargs
    assert kwargs['timeout'] == 360.0
    assert kwargs['max_output'] == mindie_launch.REMOTE_MAX_OUTPUT
    assert kwargs['max_output'] >= 32 * 1024 * 1024
    assert kwargs['max_output'] > 256 * 1024


def test_knowledge_dispatch_keeps_local_budget(tmp_path):
    mindie_launch, config, _state = _dispatch_fixture(tmp_path)
    response = {'jsonrpc': '2.0', 'id': 'q', 'result': {'content': []}}
    raw = json.dumps({
        'jsonrpc': '2.0', 'id': 'q', 'method': 'tools/call',
        'params': {'name': 'knowledge_query', 'arguments': {'query': 'x'}},
    }).encode()
    with patch.dict(os.environ, {'MINDIE_KIMI_CONFIG': str(config)}), \
            patch.object(mindie_launch.bounded, 'run',
                         return_value=json.dumps(response)) as runner:
        mindie_launch._dispatch('knowledge', raw, 'q')
    assert runner.call_args.kwargs['timeout'] == mindie_launch.CALL_CHILD_BUDGET
    assert runner.call_args.kwargs['max_output'] == mindie_launch.KNOWLEDGE_MAX_OUTPUT


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__]))
