"""Crash attribution only; no production services, FDB, or lab mutation."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.slh_seh_lab.lab_exit import (
    ExitSpec, _execute, _native_exit_classification, _local_child_stage_witness,
)


def test_windows_access_violation_classifies_signed_and_unsigned_ntstatus():
    assert _native_exit_classification(3221225477) == 'WINDOWS_ACCESS_VIOLATION'
    assert _native_exit_classification(-1073741819) == 'WINDOWS_ACCESS_VIOLATION'
    assert _native_exit_classification(0xC00000FD) == 'WINDOWS_STACK_OVERFLOW'
    assert _native_exit_classification(1) is None
    assert _native_exit_classification(0) is None


def test_crash_witness_uses_last_incomplete_stage_not_first_expected_negative(tmp_path):
    trace = tmp_path/'child-reports'/'disposable'/'lab-stage-diagnostic.jsonl'
    trace.parent.mkdir(parents=True)
    events = [
        {'stage':'preflight.worker_websocket.tls_pin','state':'begin'},
        {'stage':'preflight.worker_websocket.tls_pin','state':'error'},
        {'stage':'local.two_service_lifecycle','state':'begin'},
        {'stage':'local.real_process_credit_recovery','state':'begin'},
    ]
    trace.write_text(''.join(json.dumps(x)+'\n' for x in events), encoding='utf-8')
    witness = _local_child_stage_witness(tmp_path)
    assert witness['unfinished_lab_stage'] == 'local.real_process_credit_recovery'
    assert witness['lab_stage_events_recorded'] == 4
    assert Path(witness['lab_stage_trace_path']) == trace


def test_exit_child_preserves_crash_stage_and_unbuffered_output(tmp_path):
    # Child intentionally exits without an exception / completion report,
    # like the native-crash loss-of-report failure boundary (without segfaulting).
    script = (
        "import json, pathlib, os, sys; "
        "p=pathlib.Path(sys.argv[-1])/'child-run';p.mkdir(parents=True);"
        "(p/'lab-stage-diagnostic.jsonl').write_text("
        "json.dumps({'stage':'local.real_process_paid_work','state':'begin'})+'\\n');"
        "print('entered paid work', flush=True);os._exit(19)"
    )
    spec = ExitSpec('core_local_acceptance', ('-c', script), 8, 'local', 'synthetic test')
    result = _execute(spec, tmp_path/'case', root=tmp_path, python=sys.executable)
    assert result['status']=='FAIL' and result['exit_code']==19
    assert result['reason']=='NONZERO_EXIT'
    assert result['unfinished_lab_stage']=='local.real_process_paid_work'
    assert 'entered paid work' in Path(result['console_log']).read_text()
    samples = [json.loads(line) for line in Path(result['process_diagnostic_path']).read_text().splitlines()]
    assert samples[0]['state']=='started'
    assert samples[-1]['state']=='finished'
    assert all('command' not in row for row in samples)


def test_subprocess_has_faulthandler_enabled(tmp_path):
    script = 'import faulthandler; print(faulthandler.is_enabled(), flush=True)'
    spec = ExitSpec('diagnostic', ('-c', script), 8, 'pytest', 'synthetic test')
    result = _execute(spec, tmp_path/'case', root=tmp_path, python=sys.executable)
    assert result['exit_code']==0
    assert 'True' in Path(result['console_log']).read_text()


def test_local_checkpoints_preserve_live_sequence_and_shutdown(tmp_path, monkeypatch):
    from tools.slh_seh_lab import run_lab, real_paid_service, service_lifecycle, decision_stress
    from tools.slh_seh_lab.lab_stage_diagnostics import enable, disable
    from tools.slh_seh_lab import credit_recovery_acceptance

    called = []
    def stub(name, value):
        def inner(*args, **kwargs):
            called.append(name)
            return value
        return inner

    monkeypatch.setattr(run_lab, '_verify_four_issue_hardening', stub('hardening', {'pass':True}))
    monkeypatch.setattr(run_lab, '_credit_edge_regressions', stub('edge', {'pass':True,'cases_passed':6}))
    monkeypatch.setattr(service_lifecycle, 'verify_services', stub('services', {'pass':True}))
    monkeypatch.setattr(decision_stress, 'verify_decisions', stub('decisions', {'pass':True}))

    class Server:
        server_port = 8711
        payout_ledger_directory = object()
        worker_session_directory = object()
        def shutdown(self): called.append('shutdown')
        def server_close(self): called.append('close')
    class Thread:
        def join(self, **kwargs): called.append('join')
    def hub():
        called.append('hub_start')
        return Server(), Thread()
    monkeypatch.setattr(run_lab, '_in_memory_hub', hub)
    def paid(*args, **kwargs):
        if kwargs.get('credit_recovery_acceptance'):
            called.append('recovery')
            return {'pass':True, 'credit_recovery':{}}
        called.append('paid')
        return {'pass':True}
    monkeypatch.setattr(real_paid_service, 'verify_real_process_paid', paid)
    monkeypatch.setattr(run_lab, '_local_demo', stub('demo', {'pass':True}))
    monkeypatch.setattr(run_lab, '_mock_coolify_gate', stub('seh', {'status':'PASS'}))
    monkeypatch.setattr(credit_recovery_acceptance, 'accepted', lambda value: True)

    trace=tmp_path/'stages.jsonl'
    token=enable(trace)
    try:
        result=run_lab.verify_local()
    finally:
        disable(token)
    assert result['status']=='PASS'
    assert called==['hardening','edge','services','decisions','hub_start',
                    'paid','recovery','demo','shutdown','close','join','seh']
    begin=[json.loads(line)['stage'] for line in trace.read_text().splitlines()
           if json.loads(line)['state']=='begin']
    assert begin == [
        'local.hardening_regressions', 'local.credit_edge_regressions',
        'local.two_service_lifecycle', 'local.decision_stress',
        'local.in_memory_hub.start', 'local.real_process_paid_work',
        'local.real_process_credit_recovery', 'local.in_memory_hub_paid_demo',
        'local.in_memory_hub.shutdown', 'local.canonical_seh_mock']


def test_local_shutdown_still_runs_when_paid_work_raises(tmp_path, monkeypatch):
    from tools.slh_seh_lab import run_lab, real_paid_service, service_lifecycle, decision_stress
    from tools.slh_seh_lab.lab_stage_diagnostics import enable, disable
    import pytest
    called=[]
    monkeypatch.setattr(run_lab, '_verify_four_issue_hardening', lambda: {'pass':True})
    monkeypatch.setattr(run_lab, '_credit_edge_regressions', lambda: {'pass':True})
    monkeypatch.setattr(service_lifecycle, 'verify_services', lambda: {'pass':True})
    monkeypatch.setattr(decision_stress, 'verify_decisions', lambda: {'pass':True})
    class Server:
        server_port=8711
        payout_ledger_directory=object()
        def shutdown(self): called.append('shutdown')
        def server_close(self): called.append('close')
    class Thread:
        def join(self, **kwargs): called.append('join')
    monkeypatch.setattr(run_lab,'_in_memory_hub',lambda: (Server(),Thread()))
    def crash(*args,**kwargs):
        raise RuntimeError('isolated fake failure')
    monkeypatch.setattr(real_paid_service,'verify_real_process_paid',crash)
    trace=tmp_path/'stages.jsonl'
    token=enable(trace)
    try:
        with pytest.raises(RuntimeError,match='isolated fake failure'):
            run_lab.verify_local()
    finally:
        disable(token)
    assert called == ['shutdown','close','join']
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    assert any(row['stage']=='local.real_process_paid_work' and row['state']=='error' for row in rows)
    assert any(row['stage']=='local.in_memory_hub.shutdown' and row['state']=='ok' for row in rows)
