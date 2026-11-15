"""Regression witness for recurring ORG-SLH stop/start identity refusal.

This is intentionally *not* a proposed change in stop safety semantics. No
real process except the optional test-owned disposable sleeper is terminated.
"""
from pathlib import Path

from main_computer.slh import organization_process as op
from tools.repro_org_slh_shutdown_identity import (
    REFUSAL, _classification, replay_in_memory, reproduce_disposable_cli,
)


def _record(state: Path):
    return {
        'schema': op.SCHEMA, 'pid': 57321, 'created': 1731624544.0,
        'parent_pid': 10000, 'parent_created': 1731624000.0,
        'state_dir': str(state.resolve()),
    }


def _replay(state: Path, argv):
    return replay_in_memory(state, _record(state), {
        'pid': 57321, 'created': 1731624544.0,
        'is_running': True, 'status': 'running', 'command_line': argv,
    })


def test_actual_shutdown_function_reproduces_exact_startup_refusal_without_kill(tmp_path):
    result = _replay(tmp_path, [
        'python.exe', '-c', 'import time;time.sleep(60)',
    ])
    assert result['exception'] == REFUSAL
    assert result['exact_incident_refusal_reproduced']
    assert result['synthetic_process_terminations'] == 0
    assert result['synthetic_process_kills'] == 0
    assert result['real_process_signals_sent'] == 0


def test_matching_module_but_wrong_state_directory_refuses(tmp_path):
    result = _replay(tmp_path, [
        'python.exe', '-m', 'main_computer.slh.organization_service',
        '--state-dir', str(tmp_path / 'other'),
    ])
    assert result['exception'] == REFUSAL
    assert result['synthetic_process_terminations'] == 0
    assert _classification([
        'python.exe', '-m', 'main_computer.slh.organization_service',
        '--state-dir', str(tmp_path / 'other')], tmp_path)['reason'] == 'STATE_DIR_ARGV_MISMATCH'


def test_exact_identity_allows_only_fake_termination(tmp_path):
    result = _replay(tmp_path, [
        'python.exe', '-m', 'main_computer.slh.organization_service',
        'run', '--state-dir', str(tmp_path),
    ])
    assert result['result']['state'] == 'terminated'
    assert result['synthetic_process_terminations'] == 1
    assert result['synthetic_process_kills'] == 0
    assert result['real_process_signals_sent'] == 0


def test_pid_reuse_is_safe_noop(tmp_path):
    result = replay_in_memory(tmp_path, _record(tmp_path), {
        'pid': 57321, 'created': 1731624600.0,
        'is_running': True, 'status': 'running',
        'command_line': ['python.exe', '-c', 'pass'],
    })
    assert result['result'] == {'state': 'not-running', 'pid': 57321}
    assert result['synthetic_process_terminations'] == 0


def test_real_cli_reproduces_exact_refusal_on_disposable_live_process():
    result = reproduce_disposable_cli()
    assert result['status'] == 'REPRODUCED', result
    assert result['dummy_process_survived_refusal']
    assert result['dummy_pid_record_preserved']


def test_start_boundary_converts_helper_refusal_to_start_abort():
    ps = (Path(__file__).resolve().parents[1] /
          'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    assert 'main_computer.slh.organization_process stop --state-dir $stateDir' in ps
    assert '[string]$orgChildStop.state -eq "failed"' in ps
    assert 'ORG-SLH shutdown verification failed: {0}. Stop report: {1}' in ps


def test_same_call_cli_failure_writes_exact_pid_and_first_cmdline(tmp_path, monkeypatch, capsys):
    import json
    state = tmp_path / 'runtime' / 'state' / 'slh'
    state.mkdir(parents=True)
    record = _record(state)
    op._write_record(state, record)

    class FakeProcess:
        pid = record['pid']
        def create_time(self): return record['created']
        def is_running(self): return True
        def status(self): return 'running'
        def cmdline(self): return ['python.exe', '-m', 'wrong.module', '--state-dir', str(state)]
        def terminate(self): raise AssertionError('unverified must never terminate')
        def kill(self): raise AssertionError('unverified must never kill')

    monkeypatch.setattr(op.psutil, 'Process', lambda pid: FakeProcess())
    assert op.main(['stop', '--state-dir', str(state)]) == 1
    assert 'unverified process' in capsys.readouterr().err
    captures = list((tmp_path / 'runtime' / 'start_stop').glob('org-slh-identity-failure-*.json'))
    assert len(captures) == 1
    capture = json.loads(captures[0].read_text())
    assert capture['trace'][0]['record'] == record
    assert capture['trace'][1]['argv'][2] == 'wrong.module'
    assert capture['trace'][-1]['event'] == 'identity-refused'
    assert (state / op.PID_FILENAME).is_file()
    from tools.repro_org_slh_shutdown_identity import replay_captured_incident
    replay = replay_captured_incident(captures[0])
    assert replay['status'] == 'REPLAYED_SAME_CALL_CAPTURE'
    assert replay['replay']['exact_incident_refusal_reproduced']
    assert replay['replay']['synthetic_process_terminations'] == 0
    assert replay['replay']['real_process_signals_sent'] == 0


def test_valid_cli_stop_never_emits_failure_capture(tmp_path, monkeypatch):
    state = tmp_path / 'runtime' / 'state' / 'slh'
    state.mkdir(parents=True)
    record = _record(state)
    op._write_record(state, record)

    class FakeProcess:
        pid = record['pid']
        alive = True
        def create_time(self): return record['created']
        def is_running(self): return self.alive
        def status(self): return 'running' if self.alive else 'zombie'
        def cmdline(self): return ['python.exe', '-m', 'main_computer.slh.organization_service', '--state-dir', str(state)]
        def terminate(self): self.alive = False
        def kill(self): raise AssertionError('should not kill')

    # Use a stable fake across the initial verification and post-stop check.
    fake = FakeProcess()
    monkeypatch.setattr(op.psutil, 'Process', lambda pid: fake)
    assert op.main(['stop', '--state-dir', str(state)]) == 0
    destination = tmp_path / 'runtime' / 'start_stop'
    assert not destination.exists() or not list(destination.glob('org-slh-identity-failure-*.json'))
    assert not (state / op.PID_FILENAME).exists()


def test_transient_empty_commandline_recovers_without_weakening_identity(tmp_path, monkeypatch):
    """A single empty Windows observation must not abort an otherwise valid stop."""
    state = tmp_path / 'runtime' / 'state' / 'slh'
    state.mkdir(parents=True)
    record = _record(state)
    op._write_record(state, record)

    class FakeProcess:
        pid = record['pid']
        reads = 0
        terminations = 0
        alive = True
        def create_time(self): return record['created']
        def is_running(self): return self.alive
        def status(self): return 'running' if self.alive else 'zombie'
        def cmdline(self):
            self.reads += 1
            return [] if self.reads == 1 else ['python.exe', '-m', 'main_computer.slh.organization_service',
                                               '--state-dir', str(state)]
        def terminate(self):
            self.terminations += 1
            self.alive = False
        def kill(self): raise AssertionError('must not kill')

    fake = FakeProcess()
    monkeypatch.setattr(op.psutil, 'Process', lambda pid: fake)
    assert op.stop_recorded_process(state) == {'state': 'terminated', 'pid': record['pid']}
    assert fake.reads == 2
    assert fake.terminations == 1
    assert not (state / op.PID_FILENAME).exists()


def test_noncanonical_disposable_state_uses_local_diagnostics(tmp_path, monkeypatch):
    import json
    state = tmp_path / 'disposable-test-state'
    state.mkdir()
    record = _record(state)
    op._write_record(state, record)

    class ForeignProcess:
        pid = record['pid']
        def create_time(self): return record['created']
        def is_running(self): return True
        def status(self): return 'running'
        def cmdline(self): return ['python.exe', '-c', 'pass']
        def terminate(self): raise AssertionError('must not terminate')

    monkeypatch.setattr(op.psutil, 'Process', lambda pid: ForeignProcess())
    assert op.main(['stop', '--state-dir', str(state)]) == 1
    captures = list((state / 'identity_diagnostics').glob('org-slh-identity-failure-*.json'))
    assert len(captures) == 1
    assert json.loads(captures[0].read_text())['trace'][-1]['event'] == 'identity-refused'
    assert not (tmp_path / 'start_stop').exists()
