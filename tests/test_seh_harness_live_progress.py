"""No remote mutations: CLI progress is live, sanitized, and JSON-safe."""
import json
from types import SimpleNamespace

import seh_mutate_harness as harness


def test_all_harness_commands_report_start_and_terminal_without_corrupting_json(monkeypatch, capsys):
    for operation, status in (
        ('list', 'READ_ONLY'), ('diagnostic', 'READ_ONLY'),
        ('deploy-seh', 'CONNECTED_VERIFIED'), ('undeploy-seh', 'SEH_UNDEPLOYED'),
        ('recover-runtime', 'PLAN_ONLY'), ('reconcile-metadata', 'PLAN_ONLY'),
    ):
        monkeypatch.setattr(harness, 'run', lambda args, s=status: {'status': s, 'operation': args.operation})
        code = harness.main([operation, '--network', 'mainnet'])
        capture = capsys.readouterr()
        assert code == 0
        assert json.loads(capture.out) == {'operation': operation, 'status': status}
        assert 'harness: started status=' + operation in capture.err
        assert 'harness: finished status=' + status in capture.err


def test_inflight_steps_stream_to_stderr_before_final_json(monkeypatch, capsys):
    def work(args):
        harness._progress('deploy.recovery', 'inspecting', application_uuid='known-app')
        harness._progress('build.verify', 'observed', build_status='in_progress')
        harness._progress('runtime.verify', 'observed', coolify_status='running:healthy')
        harness._progress('resident.connection', 'verified', connected=True)
        return {'status': 'CONNECTED_VERIFIED', 'application_uuid': 'known-app'}

    monkeypatch.setattr(harness, 'run', work)
    assert harness.main(['deploy-seh']) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)['status'] == 'CONNECTED_VERIFIED'
    assert 'build.verify: observed build_status=in_progress' in output.err
    assert 'runtime.verify: observed coolify_status=running:healthy' in output.err
    assert 'resident.connection: verified connected=true' in output.err
    assert '[SEH ' not in output.out


def test_progress_redacts_untrusted_status_and_never_discloses_secret(capsys):
    progress = harness._OperatorProgress()
    progress.emit('build.verify', 'observed', build_status='Bearer secret-credential')
    progress.emit('resident.connection', 'observed', status='credential\nsecret')
    output = capsys.readouterr()
    assert 'secret' not in output.err
    assert output.err.count('unverified') >= 2


def test_generic_heartbeat_reports_when_single_remote_call_is_blocked(capsys):
    reporter = harness._OperatorProgress()
    reporter.emit('runtime.redeploy', 'executing')
    # Avoid real time or a real thread; model the heartbeat's first two wakeups.
    reporter._last_message -= 12
    class Wakeups:
        calls = 0
        def wait(self, seconds):
            assert seconds == 5
            self.calls += 1
            return self.calls == 2
    reporter._stop = Wakeups()
    reporter._heartbeat()
    output = capsys.readouterr()
    assert 'runtime.redeploy: still_waiting' in output.err


def test_progress_scoped_to_cli_not_embedded_run(monkeypatch, capsys):
    class Controller:
        root = None
        def list(self, network):
            return {'status': 'READ_ONLY'}
    result = harness.run(harness._parser().parse_args(['list']), control=Controller())
    assert result['status'] == 'READ_ONLY'
    assert capsys.readouterr() == ('', '')


def test_build_poll_reports_transition_before_completion(monkeypatch, capsys):
    from pathlib import Path
    from tools.seh_control import git_deployment as git
    observed = iter((
        {'status': 'REPLACEMENT_QUEUED_NOT_VERIFIED', 'build_status': 'in_progress',
         'application_uuid': 'app1'},
        {'status': 'REPLACEMENT_BUILD_VERIFIED', 'build_status': 'finished',
         'application_uuid': 'app1'},
    ))
    monkeypatch.setattr(git, 'reconcile_pending_recovery', lambda *a, **kw: next(observed))
    monkeypatch.setattr(harness.time, 'sleep', lambda _: None)
    progress = harness._OperatorProgress()
    token = harness._ACTIVE_PROGRESS.set(progress)
    try:
        result = harness._wait_for_verified_build(
            SimpleNamespace(build_timeout=10, network='mainnet'),
            SimpleNamespace(root=Path('/tmp/isolated')), Path('/tmp/isolated/slh'),
            'app1', {'remote_mutation': True})
    finally:
        harness._ACTIVE_PROGRESS.reset(token)
    assert result['status'] == 'REPLACEMENT_BUILD_VERIFIED'
    stderr = capsys.readouterr().err
    assert 'build_status=in_progress' in stderr
    assert 'build_status=finished' in stderr
    assert 'build.verify: verified application_uuid=app1' in stderr


def test_resident_progress_preserves_status_and_sanitization(monkeypatch, capsys):
    def worker(args):
        harness._progress('resident.auth', 'requesting_challenge')
        harness._progress('resident.activation', 'requesting')
        harness._progress('resident.connection', 'observed',
                          phase='CONNECTING_OUTBOUND_WSS', attempts=2,
                          status='NOT_CONNECTED')
        return {'status': 'INCOMPLETE', 'failed_stage': 'resident_outbound_wss_verification'}
    monkeypatch.setattr(harness, 'run', worker)
    assert harness.main(['activate-seh']) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)['failed_stage'] == 'resident_outbound_wss_verification'
    assert 'phase=CONNECTING_OUTBOUND_WSS attempts=2 status=NOT_CONNECTED' in captured.err
