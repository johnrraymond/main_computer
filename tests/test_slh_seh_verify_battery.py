"""The acceptance battery must never promote partial evidence to certification."""
import json

import pytest

from tools.slh_seh_lab.verify_battery import _evaluate, main


def test_smoke_lanes_can_pass_without_claiming_full_exit():
    lanes = [{'name': name, 'status': 'PASS'} for name in (
        'core_local_acceptance', 'security_negative_regressions',
        'strict_integrated_smoke', 'paid_transport_smoke')]
    passed, evidence = _evaluate('lab-exit',
        {'status': 'NOT_ACCEPTED', 'profile': 'smoke', 'lanes': lanes}, 1, '')
    assert passed
    assert evidence['full_exit_accepted'] is False
    lanes[-1]['status'] = 'NOT_RUN'
    assert not _evaluate('lab-exit',
        {'status': 'NOT_ACCEPTED', 'profile': 'smoke', 'lanes': lanes}, 1, '')[0]


def test_three_hub_fdb_ledger_requires_every_cycle_and_real_reserve_proof():
    result = {'status': 'PASS',
              'pinky_repeatability': {'pass': True, 'cycles_requested': 3,
                                      'cycles_completed': 3},
              'hub_cluster': {'ok': True, 'ids': ['a', 'b', 'c']},
              'pinky_internal_reserve_sweep': 'CONFIRMED_IN_FDB'}
    assert _evaluate('fdb', result, 0, '')[0]
    result['pinky_repeatability']['cycles_completed'] = 2
    assert not _evaluate('fdb', result, 0, '')[0]
    result['pinky_repeatability']['cycles_completed'] = 3
    result['pinky_internal_reserve_sweep'] = 'NOT_VERIFIED'
    assert not _evaluate('fdb', result, 0, '')[0]



def test_fdb_failure_preserves_individual_reconciliation_gates():
    result = {'status': 'FAIL', 'pinky_repeatability': {
        'pass': False, 'cycles_requested': 3, 'cycles_completed': 1,
        'gates': {'all_individual_cycles_passed': False,
                  'all_requested_cycles_completed': False},
        'cycles': [{'cycle': 1, 'pass': False, 'status': 'FAIL',
                    'gates': {'actual_fdb_reserve_credit': True,
                              'buyer_balance_consistent': False}}]},
        'hub_cluster': {'ok': True, 'ids': ['a', 'b', 'c']}}
    passed, detail = _evaluate('fdb', result, 1, '')
    assert not passed
    assert detail['reconciliation_failed_gates'] == [
        'all_individual_cycles_passed', 'all_requested_cycles_completed']
    assert detail['reconciliation_cycle_failures'] == [{
        'cycle': 1, 'status': 'FAIL',
        'failed_gates': ['buyer_balance_consistent']}]

def test_mainnet_readonly_inspect_needs_topology_and_both_hubs():
    good = ('=== Hub inspection ===\nnetwork:              mainnet\n'
            'status:               accepted\n'
            '  mainneta-hub1 coolify-a deployed\n'
            '  mainnetc-hub1 coolify-c deployed\n'
            'verification:         verified\n')
    assert _evaluate('inspect', None, 0, good)[0]
    assert not _evaluate('inspect', None, 0, good.replace('verified', 'not verified'))[0]
    assert not _evaluate('inspect', None, 0, good.replace('mainnetc-hub1', 'not-present'))[0]
    assert not _evaluate('inspect', None, 1, good)[0]


def test_auth_and_worker_gates_cannot_be_skipped():
    good = {'status': 'PASS', 'pass': True, 'gates': {'auth': True, 'paid': True}}
    assert _evaluate('lab', good, 0, '')[0]
    good['gates']['paid'] = False
    assert not _evaluate('lab', good, 0, '')[0]
    good['gates'] = {}
    assert not _evaluate('lab', good, 0, '')[0]


def test_plan_stays_nonmutating_and_lists_unimplemented_gates(capsys):
    assert main(['plan', '--with-fdb', '--live-readonly']) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload['production_mutations'] is False
    assert len(payload['steps']) == 13
    assert all('--execute-mutations' not in (spec['command'] or [])
               for spec in payload['steps'])
    assert payload['unresolved_exit_gates']['public_mainnet_paid_job_receipt']['status'] == 'NOT_TESTED'
    assert payload['unresolved_exit_gates']['cross_hub_owner_handoff']['status'] == 'NOT_IMPLEMENTED'


def test_only_fdb_lane_requires_explicit_opt_in():
    with pytest.raises(SystemExit) as exc:
        main(['plan', '--only', 'three_hub_fdb_pinky_cycle'])
    assert exc.value.code != 0


def test_failed_paid_lane_names_all_failed_gates_without_promoting_payment():
    result = {'status': 'FAIL', 'pass': False,
              'gates': {'paid_job': True, 'wallet_authorized_claim': False},
              'last_stage': 'paid_job_in_flight',
              'failure': {'stage': 'paid_job_in_flight', 'exception_class': 'RuntimeError'}}
    passed, detail = _evaluate('lab', result, 1, '')
    assert not passed
    assert detail['failed_gates'] == ['wallet_authorized_claim']
    assert detail['last_stage'] == 'paid_job_in_flight'


def test_failed_regression_reports_failed_test_case():
    passed, detail = _evaluate('pytest', None, 1,
        '....F\nFAILED tests/test_slh_example.py::test_recovery - AssertionError\n4 passed, 1 failed in 3s\n')
    assert not passed
    assert 'test_recovery' in detail['failed_cases'][0]


def test_resident_probe_preserves_stage_and_safe_cli_failure(monkeypatch):
    from tools.slh_seh_lab.verify_battery import _resident_status
    from main_computer.slh import authenticator_store
    from tools import slh_authenticator_cli

    class Store:
        def list(self):
            return {'identities': [{'device_id': 'admin-pc'}]}
    monkeypatch.setattr(authenticator_store, 'LocalIdentityStore', Store)
    def refuse(port, method, path, body=None, token=None):
        raise RuntimeError('SLH returned 403 (UNAUTHORIZED)')
    monkeypatch.setattr(slh_authenticator_cli, '_request', refuse)
    ok, detail = _resident_status(admin_device='admin-pc', local_port=8897)
    assert not ok
    assert detail['reason'] == 'RESIDENT_PROBE_ERROR'
    assert detail['stage'] == 'auth_challenge'
    assert detail['safe_error'] == 'SLH returned 403 (UNAUTHORIZED)'


def _supervisor_policy_state(*, enabled=False, fresh=True):
    from datetime import datetime, timedelta, timezone
    return {'ok': True, 'state': 'supervising',
            'service': {'state': 'supervising', 'pid': 12345},
            'slh_supervised': enabled,
            'children': {'app': {'state': 'running'}},
            'updated_at': (datetime.now(timezone.utc) -
                           timedelta(seconds=0 if fresh else 600)).isoformat()}


def test_resident_missing_listener_is_skipped_only_under_fresh_explicit_disabled_policy(monkeypatch):
    from main_computer import service_supervisor
    from tools.slh_seh_lab import verify_battery as battery

    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state',
                        lambda root: _supervisor_policy_state(enabled=False))
    def refused(*args, **kwargs):
        raise ConnectionRefusedError('No resident listener')
    monkeypatch.setattr(battery.socket, 'create_connection', refused)
    policy = battery._resident_policy_skip(local_port=8897)
    assert policy['reason'] == 'RESIDENT_DISABLED_BY_POLICY'
    assert policy['resident_verified'] is False
    assert policy['policy_source'] == 'fresh_supervisor_state'
    assert battery._resident_policy_skip(local_port=8898) is None


def test_resident_enabled_or_stale_supervisor_never_licenses_skip(monkeypatch):
    from main_computer import service_supervisor
    from tools.slh_seh_lab import verify_battery as battery

    def refused(*args, **kwargs):
        raise ConnectionRefusedError('No resident listener')
    monkeypatch.setattr(battery.socket, 'create_connection', refused)
    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state',
                        lambda root: _supervisor_policy_state(enabled=True))
    assert battery._resident_policy_skip(local_port=8897) is None
    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state',
                        lambda root: _supervisor_policy_state(enabled=False, fresh=False))
    assert battery._resident_policy_skip(local_port=8897) is None
    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state',
                        lambda root: {'ok': False, 'state': 'missing'})
    assert battery._resident_policy_skip(local_port=8897) is None


def test_resident_present_must_be_authenticated_even_when_unsupervised(monkeypatch):
    from main_computer import service_supervisor
    from tools.slh_seh_lab import verify_battery as battery

    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state',
                        lambda root: _supervisor_policy_state(enabled=False))
    class Reachable:
        def __enter__(self): return self
        def __exit__(self, *args): return None
    monkeypatch.setattr(battery.socket, 'create_connection',
                        lambda *a, **k: Reachable())
    assert battery._resident_policy_skip(local_port=8897) is None


def test_resident_policy_skip_does_not_call_authenticator_or_mark_pass(monkeypatch, tmp_path):
    from tools.slh_seh_lab import verify_battery as battery
    monkeypatch.setattr(battery, '_resident_policy_skip',
                        lambda **kwargs: {'reason': 'RESIDENT_DISABLED_BY_POLICY',
                                          'resident_verified': False})
    def unexpected(**kwargs):
        raise AssertionError('Authentication should not run for a policy skip')
    monkeypatch.setattr(battery, '_resident_status', unexpected)
    row = battery._run_step('resident_slh_connection_readonly', None, 10, 'resident',
                            tmp_path, admin_device='admin-pc', local_port=8897)
    assert row['status'] == 'SKIPPED_BY_POLICY'
    assert row['exit_code'] is None
    assert row['evidence']['resident_verified'] is False
    assert json.loads((tmp_path / 'resident_slh_connection_readonly.log').read_text()) == row['evidence']


def test_strict_resident_check_fails_even_when_policy_disabled(monkeypatch, tmp_path):
    from tools.slh_seh_lab import verify_battery as battery
    monkeypatch.setattr(battery, '_resident_policy_skip',
                        lambda **kwargs: {'reason': 'RESIDENT_DISABLED_BY_POLICY'})
    monkeypatch.setattr(battery, '_resident_status',
                        lambda **kwargs: (False, {'reason': 'RESIDENT_PROBE_ERROR',
                                                  'stage': 'auth_challenge'}))
    row = battery._run_step('resident_slh_connection_readonly', None, 10, 'resident',
                           tmp_path, admin_device='admin-pc', local_port=8897,
                           require_resident_slh=True)
    assert row['status'] == 'FAIL'
    assert row['exit_code'] == 1


def test_policy_skip_is_neutral_to_exit_code_but_not_counted_as_a_pass(monkeypatch, capsys, tmp_path):
    from tools.slh_seh_lab import verify_battery as battery
    monkeypatch.setattr(battery, '_specs', lambda **kwargs: [
        ('resident_slh_connection_readonly', None, 30, 'resident')])
    def fake_step(*args, **kwargs):
        return {'name': 'resident_slh_connection_readonly', 'status': 'SKIPPED_BY_POLICY',
                'kind': 'resident', 'exit_code': None, 'duration_seconds': 0.01,
                'log': '', 'evidence': {'reason': 'RESIDENT_DISABLED_BY_POLICY',
                                        'resident_verified': False}}
    monkeypatch.setattr(battery, '_run_step', fake_step)
    assert battery.main(['run', '--live-readonly', '--report-dir', str(tmp_path)]) == 0
    reports = list(tmp_path.glob('*/report.json'))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report['status'] == 'SCOPE_SKIPPED_BY_POLICY'
    assert report['passes'] == 0
    assert report['skipped_by_policy'] == 1
    assert report['failures'] == 0
    assert report['full_exit_accepted'] is False
    assert '0 passed, 1 policy-skipped' in capsys.readouterr().out


def test_actual_lane_failure_is_not_masked_by_a_resident_policy_skip(monkeypatch, tmp_path):
    from tools.slh_seh_lab import verify_battery as battery
    monkeypatch.setattr(battery, '_specs', lambda **kwargs: [
        ('outbound_relay_and_recovery', [], 1, 'pytest'),
        ('resident_slh_connection_readonly', None, 30, 'resident')])
    def fake_step(name, *args, **kwargs):
        return {'name': name, 'status': ('SKIPPED_BY_POLICY' if name.startswith('resident') else 'FAIL'),
                'kind': '', 'exit_code': None, 'duration_seconds': 0,
                'log': '', 'evidence': {'reason': 'TEST'}}
    monkeypatch.setattr(battery, '_run_step', fake_step)
    assert battery.main(['run', '--live-readonly', '--report-dir', str(tmp_path)]) == 1
    report = json.loads(next(tmp_path.glob('*/report.json')).read_text())
    assert report['status'] == 'BATTERY_FAILED'
    assert report['failures'] == 1
    assert report['skipped_by_policy'] == 1


def test_degraded_unrelated_child_does_not_erase_explicit_disabled_slh_policy(monkeypatch):
    from main_computer import service_supervisor
    from tools.slh_seh_lab import verify_battery as battery

    state = _supervisor_policy_state(enabled=False)
    state['ok'] = False
    state['state'] = 'degraded'
    state['children']['executor'] = {'state': 'exited'}
    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state', lambda root: state)
    def refused(*args, **kwargs):
        raise ConnectionRefusedError('Port 8897 is not listening')
    monkeypatch.setattr(battery.socket, 'create_connection', refused)
    skip = battery._resident_policy_skip(local_port=8897)
    assert skip['reason'] == 'RESIDENT_DISABLED_BY_POLICY'
    assert skip['policy_source'] == 'fresh_supervisor_state'


def test_stale_disabled_policy_requires_matching_live_supervisor_claim(monkeypatch):
    from main_computer import service_supervisor as supervisor
    from tools.slh_seh_lab import verify_battery as battery

    state = _supervisor_policy_state(enabled=False, fresh=False)
    monkeypatch.setattr(supervisor, 'load_service_supervisor_state', lambda root: state)
    monkeypatch.setattr(battery, '_resident_live_supervisor_claim', lambda **kwargs: False)
    assert battery._resident_policy_skip(local_port=8897) is None
    skip, decision = battery._resident_policy_decision(local_port=8897)
    assert skip is None
    assert decision['reason'] == 'STALE_SUPERVISOR_POLICY_UNVERIFIED'

    monkeypatch.setattr(battery, '_resident_live_supervisor_claim', lambda **kwargs: True)
    def refused(*args, **kwargs):
        raise ConnectionRefusedError('No resident listener')
    monkeypatch.setattr(battery.socket, 'create_connection', refused)
    skip = battery._resident_policy_skip(local_port=8897)
    assert skip['policy_source'] == 'verified_live_supervisor_pid_claim'


def test_stale_supervisor_claim_checks_pid_root_and_live_command(monkeypatch, tmp_path):
    from main_computer import service_supervisor as supervisor
    from tools.slh_seh_lab import verify_battery as battery

    monkeypatch.setattr(battery, 'ROOT', tmp_path)
    entry = {'service': supervisor.SERVICE_NAME, 'pid': 12345,
             'root': str(tmp_path),
             'command_line': 'python -m main_computer.service_supervisor --root . supervise'}
    (tmp_path / supervisor.SERVICE_SUPERVISOR_PID_FILENAME).write_text(json.dumps(entry))
    service = {'pid': 12345}
    monkeypatch.setattr(supervisor, '_read_process_command_line_default',
                        lambda pid: entry['command_line'])
    assert battery._resident_live_supervisor_claim(service=service)
    assert not battery._resident_live_supervisor_claim(service={'pid': 99999})
    entry['root'] = str(tmp_path / 'another-checkout')
    (tmp_path / supervisor.SERVICE_SUPERVISOR_PID_FILENAME).write_text(json.dumps(entry))
    assert not battery._resident_live_supervisor_claim(service=service)


def test_policy_refusal_reason_is_in_failed_resident_report(monkeypatch, tmp_path):
    from tools.slh_seh_lab import verify_battery as battery

    monkeypatch.setattr(battery, '_resident_policy_skip', lambda **kwargs: None)
    monkeypatch.setattr(battery, '_resident_policy_decision',
                        lambda **kwargs: (None, {'policy_check': 'NOT_ELIGIBLE',
                                                  'reason': 'STALE_SUPERVISOR_POLICY_UNVERIFIED'}))
    monkeypatch.setattr(battery, '_resident_status',
                        lambda **kwargs: (False, {'reason': 'RESIDENT_PROBE_ERROR'}))
    row = battery._run_step('resident_slh_connection_readonly', None, 30, 'resident',
                            tmp_path, admin_device='admin-pc', local_port=8897)
    assert row['status'] == 'FAIL'
    assert row['evidence']['policy_skip_decision']['reason'] == 'STALE_SUPERVISOR_POLICY_UNVERIFIED'


def test_windows_indeterminate_socket_and_actual_10061_http_refusal_policy_skip(monkeypatch, tmp_path):
    """Regression: actual Windows report had exactly this contradictory pair."""
    from main_computer import service_supervisor
    from tools.slh_seh_lab import verify_battery as battery
    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state',
                        lambda root: _supervisor_policy_state(enabled=False))
    def indeterminate(*args, **kwargs):
        raise TimeoutError('socket preflight inconclusive')
    monkeypatch.setattr(battery.socket, 'create_connection', indeterminate)
    monkeypatch.setattr(battery, '_resident_status', lambda **kwargs: (False, {
        'reason': 'RESIDENT_PROBE_ERROR', 'error_class': 'RuntimeError',
        'stage': 'auth_challenge',
        'safe_error': 'SLH is unreachable at 127.0.0.1:8897: [WinError 10061] No connection could be made because the target machine actively refused it'}))
    row = battery._run_step('resident_slh_connection_readonly', None, 30, 'resident',
                            tmp_path, admin_device='admin-pc', local_port=8897)
    assert row['status'] == 'SKIPPED_BY_POLICY'
    assert row['exit_code'] is None
    assert row['evidence']['listener'] == 'http_challenge_connection_refused'
    assert row['evidence']['resident_verified'] is False


def test_windows_bare_wsaeconnrefused_is_a_verified_absent_listener(monkeypatch):
    from main_computer import service_supervisor
    from tools.slh_seh_lab import verify_battery as battery
    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state',
                        lambda root: _supervisor_policy_state(enabled=False))
    def winrefused(*args, **kwargs):
        raise OSError(10061, 'Windows socket refusal')
    monkeypatch.setattr(battery.socket, 'create_connection', winrefused)
    skip, decision = battery._resident_policy_decision(local_port=8897)
    assert skip['listener'] == 'connection_refused'
    assert decision['policy_check'] == 'VERIFIED'


def test_refusal_evidence_cannot_override_present_listener_or_enabled_policy(monkeypatch, tmp_path):
    from main_computer import service_supervisor
    from tools.slh_seh_lab import verify_battery as battery
    refused = {'reason': 'RESIDENT_PROBE_ERROR', 'error_class': 'RuntimeError',
               'stage': 'auth_challenge',
               'safe_error': 'SLH is unreachable at 127.0.0.1:8897: [WinError 10061] test'}
    monkeypatch.setattr(battery, '_resident_status', lambda **kwargs: (False, refused.copy()))
    class Reachable:
        def __enter__(self): return self
        def __exit__(self, *args): return None
    monkeypatch.setattr(battery.socket, 'create_connection', lambda *a, **k: Reachable())
    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state',
                        lambda root: _supervisor_policy_state(enabled=False))
    row = battery._run_step('resident_slh_connection_readonly', None, 30, 'resident',
                            tmp_path, admin_device='admin-pc', local_port=8897)
    assert row['status'] == 'FAIL'
    assert row['evidence']['policy_skip_decision']['reason'] == 'RESIDENT_LISTENER_PRESENT'

    def indeterminate(*args, **kwargs): raise TimeoutError()
    monkeypatch.setattr(battery.socket, 'create_connection', indeterminate)
    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state',
                        lambda root: _supervisor_policy_state(enabled=True))
    row = battery._run_step('resident_slh_connection_readonly', None, 30, 'resident',
                            tmp_path, admin_device='admin-pc', local_port=8897)
    assert row['status'] == 'FAIL'
    assert row['evidence']['policy_skip_decision']['reason'] == 'SLH_NOT_EXPLICITLY_DISABLED'


def test_refusal_fallback_requires_exact_stage_and_endpoint(monkeypatch, tmp_path):
    from main_computer import service_supervisor
    from tools.slh_seh_lab import verify_battery as battery
    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state',
                        lambda root: _supervisor_policy_state(enabled=False))
    monkeypatch.setattr(battery.socket, 'create_connection',
                        lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))
    observed = {'reason': 'RESIDENT_PROBE_ERROR', 'error_class': 'RuntimeError',
                'stage': 'auth_challenge',
                'safe_error': 'SLH is unreachable at 127.0.0.1:8898: [WinError 10061] test'}
    for detail in (observed,
                   {**observed, 'stage': 'read_seh_status',
                    'safe_error': observed['safe_error'].replace('8898', '8897')},
                   {**observed, 'safe_error': 'SLH returned 401 (UNAUTHORIZED)'},
                   {**observed, 'safe_error': 'SLH is unreachable at 127.0.0.1:8897: timed out'}):
        monkeypatch.setattr(battery, '_resident_status',
                            lambda detail=detail, **kwargs: (False, detail.copy()))
        row = battery._run_step('resident_slh_connection_readonly', None, 30, 'resident',
                                tmp_path, admin_device='admin-pc', local_port=8897)
        assert row['status'] == 'FAIL'
        assert row['evidence']['policy_skip_decision']['reason'] == 'RESIDENT_LISTENER_UNDETERMINED'


def test_required_resident_does_not_use_http_refusal_policy_skip(monkeypatch, tmp_path):
    from main_computer import service_supervisor
    from tools.slh_seh_lab import verify_battery as battery
    monkeypatch.setattr(service_supervisor, 'load_service_supervisor_state',
                        lambda root: _supervisor_policy_state(enabled=False))
    monkeypatch.setattr(battery, '_resident_status', lambda **kwargs: (False, {
        'reason': 'RESIDENT_PROBE_ERROR', 'error_class': 'RuntimeError',
        'stage': 'auth_challenge',
        'safe_error': 'SLH is unreachable at 127.0.0.1:8897: [WinError 10061] refused'}))
    row = battery._run_step('resident_slh_connection_readonly', None, 30, 'resident',
                            tmp_path, admin_device='admin-pc', local_port=8897,
                            require_resident_slh=True)
    assert row['status'] == 'FAIL'
    assert row['exit_code'] == 1
