"""Opt-in, secret-free diagnostics for real smoke/FDB Pinky laboratory."""
from __future__ import annotations

import json
import time

import pytest

from tools.slh_seh_lab.paid_cycle import PaidCycleDiagnostics
from main_computer.stable_hub import StableHubRequestHandler
from main_computer.stable_hub_worker_sessions import _diagnose_fdb_store_operation


def test_paid_cycle_timeline_records_completed_and_failed_stages_without_payload(tmp_path, capsys):
    path = tmp_path / 'paid-cycle-diagnostic.jsonl'
    trace = PaidCycleDiagnostics(path, 'http://127.0.0.1:8872/private?token=secret')
    trace.request_id = 'slh-paid-diagnostic'
    with trace.stage('paid_work_post'):
        pass
    with pytest.raises(TimeoutError):
        with trace.stage('paid_work_replay'):
            raise TimeoutError('private text must not appear')
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(x['stage'], x['status']) for x in rows] == [
        ('paid_work_post', 'begin'), ('paid_work_post', 'ok'),
        ('paid_work_replay', 'begin'), ('paid_work_replay', 'error'),
    ]
    assert all(x['request_id'] == 'slh-paid-diagnostic' for x in rows)
    assert rows[-1]['error_type'] == 'TimeoutError'
    assert rows[-1]['duration_ms'] >= 0
    assert 'secret' not in path.read_text()
    assert 'private text' not in capsys.readouterr().err


def test_paid_cycle_unwritable_diagnostics_never_changes_execution(tmp_path):
    trace = PaidCycleDiagnostics(tmp_path / 'missing-directory' / 'events.jsonl', 'http://127.0.0.1:8872')
    with trace.stage('fund_disposable_requester'):
        pass


def test_work_request_logging_is_opt_in_and_sanitized(monkeypatch):
    handler = object.__new__(StableHubRequestHandler)
    messages = []
    monkeypatch.setattr(handler, '_log_event', messages.append)
    handler._work_diagnostic_enabled = False
    StableHubRequestHandler._work_diagnostic_stage(handler, 'no_logging')
    assert not messages
    handler._work_diagnostic_enabled = True
    handler._work_diagnostic_started = time.monotonic()
    handler._work_diagnostic_previous = handler._work_diagnostic_started
    handler._work_diagnostic_request_id = 'test-request'
    StableHubRequestHandler._work_diagnostic_stage(handler, 'market_select.begin', attempt=2)
    assert len(messages) == 1
    assert messages[0].startswith('work_request_diagnostic ')
    row = json.loads(messages[0].split(' ', 1)[1])
    assert row['stage'] == 'market_select.begin'
    assert row['request_id'] == 'test-request'
    assert row['attempt'] == 2
    assert row['step_ms'] >= 0
    assert 'work' not in row
    assert 'token' not in row


def test_fdb_diagnostic_only_on_slow_or_failed_and_shows_error_code(monkeypatch, capsys):
    monkeypatch.delenv('MAIN_COMPUTER_STABLE_HUB_FDB_DIAGNOSTICS', raising=False)
    _diagnose_fdb_store_operation('main-computer-hub-smoke', 'read_meta', time.monotonic()-2)
    assert capsys.readouterr().err == ''
    monkeypatch.setenv('MAIN_COMPUTER_STABLE_HUB_FDB_DIAGNOSTICS', '1')
    _diagnose_fdb_store_operation('main-computer-hub-smoke', 'read_meta', time.monotonic())
    assert capsys.readouterr().err == ''

    class FdbTimeout(Exception):
        code = 1031

    _diagnose_fdb_store_operation('main-computer-hub-smoke', 'atomic', time.monotonic(),
                                  error=FdbTimeout('never log sensitive data'))
    msg = capsys.readouterr().err
    assert msg.startswith('stable-hub-fdb-diagnostic ')
    event = json.loads(msg.split(' ', 1)[1])
    assert event['fdb_code'] == 1031
    assert event['status'] == 'error'
    assert event['operation'] == 'atomic'
    assert 'never log sensitive data' not in msg
