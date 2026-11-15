"""ORG-SLH PID ownership and shared teardown, independent of Windows UI."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

from main_computer.slh import organization_process as op


def record_for(directory: Path, pid: int, created: float) -> dict:
    return {'schema': op.SCHEMA, 'pid': pid, 'created': created,
            'parent_pid': os.getpid(), 'parent_created': psutil.Process().create_time(),
            'state_dir': str(directory.resolve())}


def test_child_publishes_atomic_identity_and_cleans_only_its_own_record(tmp_path):
    parent = psutil.Process()
    with op.registered_process(tmp_path, parent_pid=parent.pid, parent_created=parent.create_time()) as identity:
        path = tmp_path / op.PID_FILENAME
        assert path.is_file()
        assert json.loads(path.read_text()) == identity
        assert identity['pid'] == os.getpid()
        assert identity['created'] == parent.create_time()
        assert not list(tmp_path.glob('.organization-pid-*.tmp'))
    assert not path.exists()


def test_old_child_does_not_erase_replacement_record(tmp_path):
    parent = psutil.Process()
    path = tmp_path / op.PID_FILENAME
    with op.registered_process(tmp_path, parent_pid=parent.pid, parent_created=parent.create_time()):
        replacement = record_for(tmp_path, 999999, parent.create_time())
        op._write_record(tmp_path, replacement)
    assert op.read_record(tmp_path) == replacement
    path.unlink()


def test_missing_stale_and_recycled_pid_are_safe_noops(tmp_path):
    assert op.stop_recorded_process(tmp_path)['state'] == 'not-running'
    stale = record_for(tmp_path, 999999, 1234.0)
    op._write_record(tmp_path, stale)
    assert op.stop_recorded_process(tmp_path) == {'state': 'not-running', 'pid': 999999}
    assert not (tmp_path / op.PID_FILENAME).exists()
    reused = record_for(tmp_path, os.getpid(), 1234.0)
    op._write_record(tmp_path, reused)
    assert op.stop_recorded_process(tmp_path)['state'] == 'not-running'
    assert psutil.pid_exists(os.getpid())


def test_matching_pid_timestamp_without_module_identity_is_never_killed(tmp_path):
    current = psutil.Process()
    op._write_record(tmp_path, record_for(tmp_path, current.pid, current.create_time()))
    with pytest.raises(RuntimeError, match='unverified process'):
        op.stop_recorded_process(tmp_path)
    assert current.is_running()
    assert (tmp_path / op.PID_FILENAME).exists()


@pytest.mark.parametrize(
    ('scenario', 'expected_state'),
    [
        ('exit_after_denial', 'not-running'),
        ('transient_denial', 'terminated'),
        ('recycled_after_denial', 'not-running'),
        ('persistent_denial', None),
        ('foreign_after_denial', None),
        ('identity_access_denied', None),
    ],
)
def test_stop_cmdline_denial_differential_no_kill_unverified(
        tmp_path, monkeypatch, scenario, expected_state):
    """Exercise actual shutdown code with simulated process handles only."""
    fake_pid = 57321
    fake_created = 1731624544.0

    class FakeProcess:
        pid = fake_pid
        created = fake_created
        alive = True
        cmdline_reads = 0
        terminations = 0
        kills = 0

        def create_time(self):
            if scenario == 'identity_access_denied':
                raise psutil.AccessDenied(self.pid)
            return self.created

        def is_running(self):
            return self.alive

        def status(self):
            return 'running' if self.alive else psutil.STATUS_ZOMBIE

        def cmdline(self):
            self.cmdline_reads += 1
            if self.cmdline_reads == 1 and scenario == 'exit_after_denial':
                self.alive = False
                raise psutil.AccessDenied(self.pid)
            if self.cmdline_reads == 1 and scenario == 'recycled_after_denial':
                self.created += 100
                raise psutil.AccessDenied(self.pid)
            if scenario == 'persistent_denial' or (
                    self.cmdline_reads == 1 and scenario in ('transient_denial', 'foreign_after_denial')):
                raise psutil.AccessDenied(self.pid)
            module = ('other.unrelated_service' if scenario == 'foreign_after_denial'
                      else 'main_computer.slh.organization_service')
            return ['python', '-m', module, '--state-dir', str(tmp_path)]

        def terminate(self):
            self.terminations += 1
            self.alive = False

        def kill(self):
            self.kills += 1
            self.alive = False

    fake = FakeProcess()
    op._write_record(tmp_path, record_for(tmp_path, fake_pid, fake_created))
    monkeypatch.setattr(op.psutil, 'Process', lambda pid: fake)
    monkeypatch.setattr(op.time, 'sleep', lambda seconds: None)

    if expected_state is None:
        expected_error = ('Cannot verify ORG-SLH process identity'
                          if scenario == 'identity_access_denied'
                          else 'unverified process' if scenario == 'foreign_after_denial'
                          else 'Cannot verify ORG-SLH command line')
        with pytest.raises(RuntimeError, match=expected_error):
            op.stop_recorded_process(tmp_path, timeout=0.001)
        assert fake.terminations == fake.kills == 0
        assert (tmp_path / op.PID_FILENAME).exists()
    else:
        assert op.stop_recorded_process(tmp_path, timeout=0.001) == {
            'state': expected_state, 'pid': fake_pid}
        assert fake.terminations == (1 if expected_state == 'terminated' else 0)
        assert fake.kills == 0
        assert not (tmp_path / op.PID_FILENAME).exists()
    assert fake.cmdline_reads <= 4


def test_org_shutdown_failure_is_not_reported_as_gateway_failure():
    script = (Path(__file__).resolve().parents[1] / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    assert 'ORG-SLH shutdown verification failed: {0}. Stop report: {1}' in script
    assert 'Verified SLH process did not terminate. Stop report: $reportPath' in script


def test_invalid_record_fails_closed(tmp_path):
    path = tmp_path / op.PID_FILENAME
    path.write_text('{"pid":123}', encoding='utf-8')
    with pytest.raises(RuntimeError, match='PID record is invalid'):
        op.stop_recorded_process(tmp_path)
    assert path.exists()


def test_recorded_orphan_stops_after_manager_handle_is_lost(tmp_path, monkeypatch):
    # Use an actual disposable child to exercise psutil termination/wait. Only
    # the cmdline ownership predicate is substituted; production requires -m
    # organization_service and the matching --state-dir argument.
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'])
    try:
        proc = psutil.Process(child.pid)
        op._write_record(tmp_path, record_for(tmp_path, child.pid, proc.create_time()))
        monkeypatch.setattr(op, '_belongs_to_org', lambda live, directory: live.pid == child.pid)
        result = op.stop_recorded_process(tmp_path)
        assert result == {'state': 'terminated', 'pid': child.pid}
        assert child.poll() is not None
        assert not (tmp_path / op.PID_FILENAME).exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_cli_stale_pid_stop_is_idempotent(tmp_path):
    op._write_record(tmp_path, record_for(tmp_path, 999999, 1234.0))
    cmd = [sys.executable, '-m', 'main_computer.slh.organization_process',
           'stop', '--state-dir', str(tmp_path)]
    first = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert json.loads(first.stdout) == {'state': 'not-running', 'pid': 999999}
    second = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert json.loads(second.stdout) == {'state': 'not-running', 'pid': None}


def test_launcher_and_controller_share_pid_file_contract():
    from main_computer.slh.organization_controller import OrganizationController
    script = (Path(__file__).resolve().parents[1] / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    assert 'organization-service.pid' in script
    assert 'main_computer.slh.organization_process stop --state-dir $stateDir' in script
    assert 'stop_recorded_process(self.directory)' in __import__('inspect').getsource(OrganizationController)
    assert 'org_service_stop = $orgChildStop' in script
    assert '[string]$orgChildStop.state -eq "failed"' in script
    assert 'verified_creation_date' in script


def test_controller_status_reports_verified_interpreter_behind_launcher(tmp_path, monkeypatch):
    """Exercise the two-PID shape of a Windows venv Python redirector.

    Run real disposable processes on every OS; substitute only the production
    ORG-SLH command-line predicate because the test child sleeps instead of
    binding an actual ORG-SLH endpoint.
    """
    from main_computer.slh.organization_controller import OrganizationController

    current = psutil.Process()
    # Use the base interpreter to avoid introducing an additional Windows venv
    # redirector into the *test* launcher/child relationship.
    base_python = getattr(sys, '_base_executable', sys.executable)
    launcher_code = (
        'import subprocess,sys\n'
        'child=subprocess.Popen([sys._base_executable,"-c","import time;time.sleep(30)"])\n'
        'print(child.pid,flush=True)\n'
        'child.wait()\n'
    )
    launcher = subprocess.Popen([base_python, '-c', launcher_code],
                                stdout=subprocess.PIPE, text=True)
    child_pid = None
    controller = None
    try:
        child_pid = int(launcher.stdout.readline().strip())
        child = psutil.Process(child_pid)
        assert child.ppid() == launcher.pid
        assert child_pid != launcher.pid
        op._write_record(tmp_path, record_for(tmp_path, child_pid, child.create_time()))
        monkeypatch.setattr(op, '_belongs_to_org', lambda proc, state: proc.pid == child_pid)

        class Authority:
            directory = tmp_path
            def initialized(self):
                return True

        controller = OrganizationController(Authority(), automatic_start=False)
        controller._process = launcher
        monkeypatch.setattr(controller, '_listening', lambda config: True)
        status = controller.status()
        assert status['listening']
        assert status['pid'] == child_pid
        assert status['pid'] != launcher.pid

        # A guessed launcher or a record for a different Local Service cannot
        # establish ownership of the PID, despite the live process and state.
        assert op.verified_launched_pid(
            tmp_path, launcher_pid=os.getpid(), controller_pid=current.pid,
            controller_created=current.create_time()) is None
        assert op.verified_launched_pid(
            tmp_path, launcher_pid=launcher.pid, controller_pid=current.pid,
            controller_created=current.create_time() - 10) is None
        assert op.verified_launched_pid(
            tmp_path, launcher_pid=launcher.pid, controller_pid=current.pid,
            controller_created=current.create_time()) == child_pid
    finally:
        if controller is not None:
            controller.close()
        if child_pid is not None and psutil.pid_exists(child_pid):
            try:
                psutil.Process(child_pid).kill()
            except psutil.NoSuchProcess:
                pass
        if launcher.poll() is None:
            launcher.kill()
        launcher.wait(timeout=5)
        if launcher.stdout is not None:
            launcher.stdout.close()
