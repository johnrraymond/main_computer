"""Diagnostic-only regression: failures must identify the earliest failing lab phase."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.slh_seh_lab import run_lab
from tools.slh_seh_lab.lab_stage_diagnostics import enable, disable, record, stage, summarize
from tools.slh_seh_lab.service_lifecycle import _managed_auth


def test_stage_journal_flushes_nested_failure_before_unwinding(tmp_path):
    path = tmp_path/'stages.jsonl'
    token = enable(path)
    try:
        with pytest.raises(TimeoutError, match='handshake response'):
            with stage('outer'):
                with stage('inner', transport='seh_relay', endpoint_port=12345):
                    assert path.exists()
                    rows = [json.loads(line) for line in path.read_text().splitlines()]
                    assert rows[-1]['stage'] == 'inner' and rows[-1]['state'] == 'begin'
                    raise TimeoutError('timed out while waiting for handshake response')
    finally:
        disable(token)
    summary = summarize(path)
    assert summary['events'] == 4
    assert summary['last_failure']['stage'] == 'inner'
    assert summary['last_failure']['error_classification'] == 'handshake_response_timeout'
    assert summary['unfinished_stage'] is None
    assert 'message' not in summary['last_failure']


def test_incomplete_stage_identified_without_completing_transaction(tmp_path):
    path = tmp_path/'trace.jsonl'
    token = enable(path)
    try:
        record('pinky.reserve_payment', 'begin')
    finally:
        disable(token)
    assert summarize(path)['unfinished_stage'] == 'pinky.reserve_payment'


def test_managed_websocket_handshake_failure_names_transport_and_port(tmp_path):
    path = tmp_path/'trace.jsonl'
    token = enable(path)
    try:
        with patch('websockets.sync.client.connect', side_effect=TimeoutError('timed out while waiting for handshake response')):
            with pytest.raises(TimeoutError):
                _managed_auth(43210, 'lab-worker', None, expected_pin='0'*64, via_relay=True)
    finally:
        disable(token)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    begin = next(row for row in rows if row['stage'] == 'preflight.worker_websocket.handshake' and row['state'] == 'begin')
    assert begin['transport'] == 'seh_relay' and begin['endpoint_port'] == 43210
    assert summarize(path)['last_failure']['stage'] == 'preflight.worker_websocket.handshake'
    assert all('lab-worker' not in json.dumps(row) for row in rows)


def test_run_smoke_preflight_failure_does_not_launch_hubs(tmp_path):
    path = tmp_path/'lab-stage-diagnostic.jsonl'
    token = enable(path)
    try:
        with patch('tools.slh_seh_lab.service_lifecycle.verify_services', side_effect=TimeoutError('handshake response')), \
             patch('tools.stable_hub_lab.run_lab.check_cluster') as checked:
            with pytest.raises(TimeoutError):
                run_lab.run_smoke(run_lab.DEFAULT_TOPOLOGY, use_existing=False,
                                  report_dir=tmp_path, pinky_integrated=True)
            checked.assert_not_called()
    finally:
        disable(token)
    assert summarize(path)['last_failure']['stage'] == 'preflight.two_service_lifecycle'
    assert not list(tmp_path.glob('smoke-hub*.log'))


def test_cli_reports_preflight_failure_with_stage_file_even_without_hub_logs(tmp_path, capsys):
    with patch('tools.slh_seh_lab.run_lab.validate_topology', return_value={'safe': True}), \
         patch('tools.slh_seh_lab.service_lifecycle.verify_services', side_effect=TimeoutError('timed out while waiting for handshake response')), \
         patch('tools.stable_hub_lab.run_lab.check_cluster') as checked:
        code = run_lab.main(['run', '--pinky-integrated', '--json', '--report-dir', str(tmp_path)])
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'FAIL'
    assert report['hub_log_paths'] == []
    assert report['failure_before_hub_launch'] is True
    assert report['first_failed_lab_stage']['stage'] == 'preflight.two_service_lifecycle'
    assert report['lab_stage_events_recorded'] >= 3
    assert Path(report['lab_stage_trace_path']).is_file()
    assert Path(report['lab_runtime_log_path']).is_file()
    assert all('file' in item and 'function' in item and 'line' in item for item in report['failure_stack'])
    checked.assert_not_called()


def test_verify_local_timeout_preserves_safe_stage_and_failure_stack(tmp_path, capsys):
    def fail_local():
        with stage('preflight.worker_websocket.handshake', transport='direct', endpoint_port=4444):
            raise TimeoutError('timed out while waiting for handshake response: private credential')
    with patch('tools.slh_seh_lab.run_lab.validate_topology', return_value={'safe': True}), \
         patch('tools.slh_seh_lab.run_lab.verify_local', side_effect=fail_local):
        code = run_lab.main(['verify-local', '--json', '--report-dir', str(tmp_path)])
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report['terminal_failed_lab_stage']['stage'] == 'preflight.worker_websocket.handshake'
    assert report['terminal_failed_lab_stage']['transport'] == 'direct'
    assert report['failure_stack'][-1]['function'] == 'fail_local'
    assert Path(report['lab_stage_trace_path']).exists()
    assert report['lab_stage_events_recorded'] >= 3
    assert 'private credential' not in json.dumps(report['failure_stack'])
    assert 'private credential' not in Path(report['lab_stage_trace_path']).read_text()


def test_wrapped_openssl_handshake_close_records_safe_retry_evidence(tmp_path):
    """Windows OpenSSL close is classified without exposing device or private data."""
    import ssl
    from websockets.exceptions import ConnectionClosedError
    from tools.slh_seh_lab.service_lifecycle import _managed_auth, TransientWorkerHandshakeError

    def closed_in_tls(*args, **kwargs):
        try:
            raise ssl.SSLError('[SSL] internal error (_ssl.c:2427)')
        except ssl.SSLError as root:
            raise ConnectionClosedError(None, None) from root

    path = tmp_path / 'lab-stage-diagnostic.jsonl'
    token = enable(path)
    try:
        with patch('websockets.sync.client.connect', side_effect=closed_in_tls):
            with pytest.raises(TransientWorkerHandshakeError):
                _managed_auth(42001, 'confidential-worker-name', object(),
                              expected_pin='sensitive-pin', via_relay=True)
    finally:
        disable(token)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    retry = [row for row in rows if row['state'] == 'transient_transport_close']
    assert len(retry) == 1
    assert retry[0]['transport'] == 'seh_relay'
    assert retry[0]['cause_type'] == 'SSLError'
    assert 'confidential-worker-name' not in path.read_text()
    assert 'sensitive-pin' not in path.read_text()
