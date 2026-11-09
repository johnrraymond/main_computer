"""Pure diagnostic regressions; no FDB cluster or production deployments."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from main_computer.stable_hub_worker_sessions import FoundationDbStableWorkerSessionStore
from tools.slh_seh_lab.paid_cycle import PaidCycleDiagnostics, _await_paid_terminal
from tools.slh_seh_lab.verify_battery import _run_child_with_evidence, _run_step


def test_battery_timeout_retains_partial_stdout_stderr_and_runtime_evidence(tmp_path):
    script = ("import sys,time;print('tests/test_foo.py::test_stalled ',flush=True);"
              "print('worker entered handshake',file=sys.stderr,flush=True);time.sleep(15)")
    row = _run_step('seh_deployment_and_trust_controls',
                    [sys.executable, '-u', '-c', script], 2.5, 'pytest', tmp_path,
                    admin_device=None, local_port=8897)
    assert row['status'] == 'FAIL'
    evidence = row['evidence']
    assert evidence['reason'] == 'TIMEOUT'
    assert evidence['timed_out'] is True
    assert evidence['last_pytest_node'] == 'tests/test_foo.py::test_stalled'
    assert 'test_stalled' in Path(row['log']).read_text()
    assert 'worker entered handshake' in Path(row['log']).read_text()
    events = [json.loads(x) for x in Path(evidence['activity_journal']).read_text().splitlines()]
    assert [event['phase'] for event in events][-2:] == ['timeout_before_termination', 'timed_out']
    assert all('command' not in event for event in events)


def test_battery_non_pytest_json_stdout_is_preserved(tmp_path):
    row = _run_step('fake_paid_lab',
                    [sys.executable, '-c', "import json;print(json.dumps({'status':'PASS','pass':True,'gates':{'paid':True}}))"],
                    5, 'lab', tmp_path, admin_device=None, local_port=8897)
    assert row['status'] == 'PASS'
    assert row['evidence']['timed_out'] is False
    assert 'fake_paid_lab.stdout.log' in row['evidence']['stdout_log']


def test_paid_poll_summary_proves_nonterminal_last_state(tmp_path):
    class Store:
        def load(self):
            return {'accepted_sessions': {'sess-test': {'status': 'accepted'}}}
    trace_path = tmp_path/'poll.jsonl'
    trace = PaidCycleDiagnostics(trace_path, 'http://127.0.0.1:8728')
    trace.request_id = 'test-id'
    with pytest.raises(RuntimeError, match='Timed out waiting'):
        _await_paid_terminal(Store(), 'sess-test', trace, seconds=.02)
    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    summary = next(row for row in rows if row['stage']=='paid_terminal_poll_summary')
    assert summary['last_status']=='accepted'
    assert summary['reads'] >= 1
    assert summary['max_read_ms'] >= 0
    assert rows[-1]['status'] == 'observed'


def test_paid_poll_summary_proves_terminal_success(tmp_path):
    class Store:
        n = 0
        def load(self):
            self.n+=1
            status = 'running' if self.n == 1 else 'succeeded'
            return {'accepted_sessions': {'sess-test': {'status': status}}}
    trace = PaidCycleDiagnostics(tmp_path/'poll.jsonl', 'http://127.0.0.1:8728')
    result = _await_paid_terminal(Store(), 'sess-test', trace, seconds=1)
    assert result['status'] == 'succeeded'
    rows = [json.loads(line) for line in (tmp_path/'poll.jsonl').read_text().splitlines()]
    assert rows[-1]['reads'] == 2
    assert rows[-1]['last_status'] == 'succeeded'


def test_atomic_fdb_timeout_emits_attempt_phase_without_body(monkeypatch, capsys):
    monkeypatch.setenv('MAIN_COMPUTER_STABLE_HUB_FDB_DIAGNOSTICS', '1')
    class Timeout(Exception):
        code=1031
    class FDB:
        @staticmethod
        def transactional(fn):
            return fn
    store = FoundationDbStableWorkerSessionStore(cluster_file='dummy', namespace='main-computer-hub-smoke')
    store._opened = True
    store._fdb = FDB()
    store._db = object()
    def action():
        store._transaction_local.fdb_phase = 'read_meta'
        raise Timeout('do not expose this text')
    with pytest.raises(Timeout):
        store.atomic(action)
    rows = [json.loads(line.split(' ',1)[1]) for line in capsys.readouterr().err.splitlines()
            if line.startswith('stable-hub-fdb-diagnostic ')]
    atomic = next(r for r in rows if r['operation']=='atomic')
    assert atomic['fdb_code']==1031
    assert atomic['last_phase']=='read_meta'
    assert atomic['callbacks_started']==1
    assert atomic['callbacks_completed']==0
    assert atomic['configured_timeout_ms'] >= 1
    assert 'do not expose this text' not in str(atomic)


def test_atomic_fdb_counts_callback_retries(monkeypatch, capsys):
    monkeypatch.setenv('MAIN_COMPUTER_STABLE_HUB_FDB_DIAGNOSTICS', '1')
    class Conflict(Exception):
        code=1020
    class FDB:
        @staticmethod
        def transactional(fn):
            def wrapper(database):
                try:
                    return fn(database)
                except Conflict:
                    return fn(database)
            return wrapper
    store = FoundationDbStableWorkerSessionStore(cluster_file='dummy', namespace='main-computer-hub-smoke')
    store._opened = True
    store._fdb = FDB()
    store._db = object()
    calls = []
    def action():
        calls.append(1)
        if len(calls)==1:
            raise Conflict()
        return 'ok'
    assert store.atomic(action)=='ok'
    rows = [json.loads(line.split(' ',1)[1]) for line in capsys.readouterr().err.splitlines()
            if line.startswith('stable-hub-fdb-diagnostic ')]
    atomic = next(r for r in rows if r['operation']=='atomic')
    assert atomic['callbacks_started'] == 2
    assert atomic['callbacks_completed'] == 1
    assert atomic['last_phase'] == 'callback_completed_commit_not_confirmed'


def test_worker_terminal_diagnostic_begin_and_error_sanitized(monkeypatch):
    from main_computer.stable_hub import _emit_worker_terminal_fdb_diagnostic
    messages = []
    monkeypatch.delenv('MAIN_COMPUTER_STABLE_HUB_FDB_DIAGNOSTICS', raising=False)
    _emit_worker_terminal_fdb_diagnostic(
        messages.append, phase='begin', request_id='r1', session_id='s1',
        message_type='worker.work.result', hub_id='smoke-hub3')
    assert not messages
    monkeypatch.setenv('MAIN_COMPUTER_STABLE_HUB_FDB_DIAGNOSTICS', '1')
    _emit_worker_terminal_fdb_diagnostic(
        messages.append, phase='begin', request_id='r1', session_id='s1',
        message_type='worker.work.result', hub_id='smoke-hub3')
    _emit_worker_terminal_fdb_diagnostic(
        messages.append, phase='error', request_id='r1', session_id='s1',
        message_type='worker.work.result', hub_id='smoke-hub3',
        elapsed_ms=20031.0, error_type='FDBError', fdb_code=1031)
    assert len(messages) == 2
    rows = [json.loads(x.split(' ', 1)[1]) for x in messages]
    assert [x['phase'] for x in rows] == ['begin', 'error']
    assert rows[-1]['fdb_code']==1031
    assert rows[-1]['duration_ms']==20031.0
    assert not any(k in rows[-1] for k in ('result', 'wallet', 'token', 'key', 'work'))


def test_paid_poll_read_exception_retains_last_phase_without_secret(tmp_path):
    class FdbFailure(RuntimeError):
        pass
    class Store:
        def load(self):
            raise FdbFailure('private-store-value-never-log')
    trace = PaidCycleDiagnostics(tmp_path/'poll.jsonl', 'http://127.0.0.1:8728')
    with pytest.raises(FdbFailure):
        _await_paid_terminal(Store(), 'sess-test', trace, seconds=1)
    content = (tmp_path/'poll.jsonl').read_text()
    assert 'private-store-value-never-log' not in content
    rows = [json.loads(line) for line in content.splitlines()]
    assert any(r['stage'] == 'paid_terminal_ledger_read' and r['error_type']=='FdbFailure'
               for r in rows)
    assert rows[-1]['read_error_count'] == 1
    assert rows[-1]['reads'] == 1
