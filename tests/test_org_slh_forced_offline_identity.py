"""Windows-compatible offline verification of the ORG-SLH shutdown fix."""
from tools.force_repro_org_slh_shutdown import EXPECTED_ERROR, force_reproduction


def test_live_correct_identity_recovers_from_transient_cmdline():
    report = force_reproduction()
    assert report['status'] == 'FIX_VERIFIED_OFFLINE', report
    assert report['pid_and_creation_time_verified']
    assert not report['production_application_started']
    assert not report['production_pid_record_accessed']
    assert not report['real_org_service_started']
    assert report['comparison'] == {
        'healthy_same_process_passes': True,
        'transient_empty_recovers': True,
        'transient_truncated_recovers': True,
        'access_denied_retries': True,
        'persistent_empty_refused': True,
        'foreign_process_still_refused': True,
    }
    cases = report['cases']
    for fault in ('transient_empty', 'transient_truncated'):
        case = cases[fault]
        assert case['stop_cli_exit_code'] == 0
        assert case['command_line_observations'] == 2
        assert case['synthetic_termination_calls'] == 1
        assert case['synthetic_kill_calls'] == 0
        assert case['pid_record_preserved']
        assert case['real_process_survived']
    for fault in ('persistent_empty', 'persistent_foreign'):
        case = cases[fault]
        assert case['stop_cli_exit_code'] == 1
        assert case['synthetic_termination_calls'] == 0
        assert case['synthetic_kill_calls'] == 0
        assert case['pid_record_preserved']
        assert case['real_process_survived']
    assert cases['persistent_empty']['command_line_observations'] == 4
    assert cases['persistent_foreign']['command_line_observations'] == 1
    assert EXPECTED_ERROR in cases['persistent_foreign']['stop_cli_stderr']
    assert cases['normal']['synthetic_termination_calls'] == 1
    assert cases['transient_access_denied']['command_line_observations'] == 2
    assert all(case['real_process_signals_sent'] == 0 for case in cases.values())
