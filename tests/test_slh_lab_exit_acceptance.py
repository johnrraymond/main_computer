"""Acceptance cannot be inferred from partial paid success or missing lanes."""
import copy
import json
from pathlib import Path
import pytest

from tools.slh_seh_lab.integrated_stress import StressProfile
from tools.slh_seh_lab.lab_exit import (
    UNIMPLEMENTED_REQUIRED, ExitSpec, exit_specs, judge, run_exit,
)


def _stress_spec():
    return ExitSpec('windows_stress_01', ('-m','tools.slh_seh_lab.run_lab','stress-integrated',
                    '--workers','64','--requests','64','--concurrency','8','--seed','1729','--strict-acceptance','--capacity-probe'), 300,'stress','lab')


def _passing_stress():
    return {'status':'PASS','pass':True,'integrated_stress':{
        'pass':True,'profile':{'paid_requests':64,'workers':64,'concurrency':8,'seed':1729,'strict_acceptance':True},
        'paid':{'submitted':64,'accepted':64,'succeeded':64,'rejected':0,'failed':0},
        'gates':{'all_paid_requests_accepted':True,'all_paid_requests_succeeded':True,
                 'capacity_65th_rejected_without_disruption':True,
                 'capacity_existing_workers_retained':True},
        'events':[{'event':'complete','result':'PASS'},
                  {'event':'causal_fleet_checkpoint','missing_workers':[],
                   'unexpected_workers':[],'dead_worker_clients':[]}]}}


# These six capabilities must remain required even if their current test lanes pass.
EXPECTED_MISSING_CAPABILITIES = {
    'crash_during_paid_execution',
    'persistent_three_hub_crash_restart',
    'cross_hub_owner_handoff',
    'representative_compute_output',
    'credential_rotation_live_connections',
    'pinky_promise_economic_cycle',
}


def test_all_six_exit_blockers_are_declared_and_pinky_price_is_fixed():
    assert set(UNIMPLEMENTED_REQUIRED) == EXPECTED_MISSING_CAPABILITIES
    reason = UNIMPLEMENTED_REQUIRED['pinky_promise_economic_cycle']
    assert '0.1-credit' in reason
    assert 'organization wallet' in reason
    assert 'Reserve settlement' in reason


def test_rejects_partial_acceptance_even_with_generic_pass():
    spec = _stress_spec()
    report = _passing_stress()
    assert judge(spec, report, returncode=0)[0]
    for key,value in [('accepted',32),('rejected',32),('succeeded',63),('failed',1)]:
        altered=copy.deepcopy(report)
        altered['integrated_stress']['paid'][key] = value
        assert not judge(spec,altered,returncode=0)[0], key


def test_missing_capacity_witness_or_dead_worker_fails():
    spec=_stress_spec()
    report=_passing_stress()
    del report['integrated_stress']['gates']['capacity_65th_rejected_without_disruption']
    assert not judge(spec,report,returncode=0)[0]
    report=_passing_stress()
    report['integrated_stress']['events'][1]['dead_worker_clients']=['worker-a']
    assert not judge(spec,report,returncode=0)[0]


def test_soak_must_retain_same_connection_for_full_duration():
    spec=ExitSpec('soak',('--transport','seh','--cycles','16','--soak-seconds','3600'),4000,'paid-soak','lab')
    report={'status':'PASS','pass':True,'cycles_requested':16,'soak_seconds_required':3600,
            'paths':{'seh':{'pass':True,'cycles_completed':100,'worker_survived':True,
                            'tls_io_mode':'serialized','soak_seconds_required':3600,'elapsed_seconds':3600,
                            'admin_session_refreshes':5}}}
    assert judge(spec,report,returncode=0)[0]
    report['paths']['seh']['elapsed_seconds']=3599
    assert not judge(spec,report,returncode=0)[0]
    report['paths']['seh']['elapsed_seconds']=3600
    report['paths']['seh']['admin_session_refreshes']=0
    assert judge(spec,report,returncode=0) == (False,'SUSTAINED_DURATION_NOT_PROVEN')


def test_full_profile_refuses_weakened_thresholds():
    with pytest.raises(ValueError,match='ten'):
        exit_specs(profile='full',repeats=9)
    with pytest.raises(ValueError,match='3600'):
        exit_specs(profile='full',repeats=10,soak_seconds=3599)
    specs=exit_specs(profile='full',repeats=10,soak_seconds=3600)
    assert sum(s.kind=='stress' for s in specs)==12
    assert any('--capacity-probe' in s.command for s in specs)
    assert any(s.kind=='three-hub' for s in specs)
    assert any(s.kind=='paid-soak' for s in specs)


def test_smoke_profile_does_not_create_acceptance(tmp_path,monkeypatch):
    from tools.slh_seh_lab import run_lab
    monkeypatch.setattr(run_lab, 'validate_topology', lambda path: {'network_kind':'smoke'})
    def all_pass(spec, path, *, root, python):
        return {'name':spec.name,'status':'PASS','reason':'SIMULATED_TEST_DOUBLE'}
    report=run_exit(profile='smoke',report_dir=tmp_path,executor=all_pass)
    assert report['status']=='NOT_ACCEPTED'
    assert not report['accepted']
    assert len(report['lanes'])==4
    assert json.loads((tmp_path/'exit-report.json').read_text())['status']=='NOT_ACCEPTED'


def test_full_profile_remains_unaccepted_without_missing_lanes(tmp_path,monkeypatch):
    from tools.slh_seh_lab import run_lab
    monkeypatch.setattr(run_lab, 'validate_topology', lambda path: {'network_kind':'smoke'})
    def all_pass(spec, path, *, root, python):
        return {'name':spec.name,'status':'PASS','reason':'SIMULATED_TEST_DOUBLE'}
    report=run_exit(profile='full',report_dir=tmp_path,executor=all_pass)
    assert report['status']=='NOT_ACCEPTED'
    assert 'crash_during_paid_execution' in report['required_gaps']
    assert 'representative_compute_output' in report['required_gaps']
    assert all(v['status']=='BLOCKED_UNIMPLEMENTED' for v in report['required_gaps'].values())
    assert EXPECTED_MISSING_CAPABILITIES <= set(report['required_gaps'])
    assert report['required_gaps']['pinky_promise_economic_cycle'] == {
        'status': 'BLOCKED_UNIMPLEMENTED',
        'reason': UNIMPLEMENTED_REQUIRED['pinky_promise_economic_cycle'],
    }
    recorded = json.loads((tmp_path/'exit-report.json').read_text())
    assert recorded['status'] == 'NOT_ACCEPTED'
    assert recorded['accepted'] is False
    assert recorded['required_gaps']['pinky_promise_economic_cycle'] == report['required_gaps']['pinky_promise_economic_cycle']


def test_pinky_promise_alone_blocks_forged_successful_full_exit(tmp_path, monkeypatch):
    # Prove this gate remains decisive after the five older blockers are cleared.
    from tools.slh_seh_lab import lab_exit, run_lab
    monkeypatch.setattr(run_lab, 'validate_topology', lambda path: {'network_kind': 'smoke'})
    monkeypatch.setattr(lab_exit.platform, 'system', lambda: 'Windows')
    monkeypatch.setattr(lab_exit, 'UNIMPLEMENTED_REQUIRED', {
        'pinky_promise_economic_cycle': UNIMPLEMENTED_REQUIRED['pinky_promise_economic_cycle'],
    })

    def forged_pass(spec, path, *, root, python):
        return {
            'name': spec.name,
            'status': 'PASS',
            'reason': 'FORGED_SUCCESS',
            'pinky_promise_economic_cycle': {'status': 'PASS'},
        }

    report = run_exit(profile='full', report_dir=tmp_path, executor=forged_pass)
    assert all(lane['status'] == 'PASS' for lane in report['lanes'])
    assert len(report['lanes']) == len(exit_specs(profile='full'))
    assert report['required_gaps'] == {
        'pinky_promise_economic_cycle': {
            'status': 'BLOCKED_UNIMPLEMENTED',
            'reason': UNIMPLEMENTED_REQUIRED['pinky_promise_economic_cycle'],
        },
    }
    assert report['status'] == 'NOT_ACCEPTED'
    assert report['accepted'] is False
    persisted = json.loads((tmp_path / 'exit-report.json').read_text())
    assert persisted['status'] == 'NOT_ACCEPTED'
    assert persisted['accepted'] is False
    assert persisted['required_gaps'] == report['required_gaps']



def test_full_exit_keeps_six_blockers_after_early_failure_on_non_windows(tmp_path, monkeypatch):
    from tools.slh_seh_lab import lab_exit, run_lab
    monkeypatch.setattr(run_lab, 'validate_topology', lambda path: {'network_kind': 'smoke'})
    monkeypatch.setattr(lab_exit.platform, 'system', lambda: 'Linux')

    def fail_first(spec, path, *, root, python):
        return {'name': spec.name, 'status': 'FAIL', 'reason': 'INTENTIONAL'}

    report = run_exit(profile='full', report_dir=tmp_path, executor=fail_first)
    assert report['lanes'][0]['status'] == 'FAIL'
    assert all(lane['status'] == 'NOT_RUN' for lane in report['lanes'][1:])
    assert EXPECTED_MISSING_CAPABILITIES <= set(report['required_gaps'])
    assert report['required_gaps']['required_windows_validation']['status'] == 'BLOCKED_UNIMPLEMENTED'
    assert report['status'] == 'NOT_ACCEPTED'
    recorded = json.loads((tmp_path / 'exit-report.json').read_text())
    assert recorded['required_gaps'] == report['required_gaps']
    assert recorded['accepted'] is False


def test_first_failure_preserved_and_remaining_lanes_not_run(tmp_path,monkeypatch):
    from tools.slh_seh_lab import run_lab
    monkeypatch.setattr(run_lab, 'validate_topology', lambda path: {'network_kind':'smoke'})
    def fail_first(spec,path,*,root,python):
        return {'name':spec.name,'status':'FAIL','reason':'INTENTIONAL'}
    report=run_exit(profile='smoke',report_dir=tmp_path,executor=fail_first)
    assert len(report['lanes'])==4
    assert report['lanes'][0]['reason']=='INTENTIONAL'
    assert all(x['status']=='NOT_RUN' for x in report['lanes'][1:])


def test_stress_profile_flags_opt_in():
    default=StressProfile()
    assert not default.strict_acceptance
    assert not default.capacity_probe
    assert StressProfile(64,64,8,1729,True,True,True).capacity_probe
