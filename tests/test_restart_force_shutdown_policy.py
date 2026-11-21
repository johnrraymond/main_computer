"""Start and stop share explicit force policy; direct API defaults to strict."""
import json
import os
from pathlib import Path
import subprocess
import sys

import psutil
import pytest

from main_computer.slh import organization_process as op


def _record(directory, pid, created):
    parent = psutil.Process()
    return {'schema': op.SCHEMA, 'pid': pid, 'created': created,
            'parent_pid': parent.pid, 'parent_created': parent.create_time(),
            'state_dir': str(directory.resolve())}


def test_force_restart_kills_unverifiable_pid_but_strict_stop_refuses(tmp_path, monkeypatch):
    record = _record(tmp_path, 57321, 1731624544.0)
    op._write_record(tmp_path, record)

    class ForeignProcess:
        pid = 57321
        alive = True
        kills = 0
        def create_time(self): return record['created']
        def is_running(self): return self.alive
        def status(self): return 'running' if self.alive else psutil.STATUS_ZOMBIE
        def cmdline(self): return ['python.exe', '-m', 'unrelated.module']
        def kill(self):
            self.kills += 1
            self.alive = False
    proc = ForeignProcess()
    monkeypatch.setattr(op.psutil, 'Process', lambda pid: proc)
    monkeypatch.setattr(op.time, 'sleep', lambda seconds: None)
    with pytest.raises(RuntimeError, match='unverified process'):
        op.stop_recorded_process(tmp_path)
    assert proc.kills == 0
    result = op.stop_recorded_process(tmp_path, force_unverified=True)
    assert result['state'] == 'terminated-unverified'
    assert result['pid'] == 57321
    assert result['force_kill_attempted'] is True
    assert 'kill attempted without identity proof' in result['warning']
    assert proc.kills == 1
    assert not (tmp_path / op.PID_FILENAME).exists()


def test_force_restart_cannot_kill_pid_warns_and_clears_obsolete_record(tmp_path, monkeypatch):
    record = _record(tmp_path, 57321, 1731624544.0)
    op._write_record(tmp_path, record)

    class AccessDeniedProcess:
        pid = 57321
        def create_time(self): raise psutil.AccessDenied(self.pid)
        def kill(self): raise psutil.AccessDenied(self.pid)
    monkeypatch.setattr(op.psutil, 'Process', lambda pid: AccessDeniedProcess())
    result = op.stop_recorded_process(tmp_path, force_unverified=True)
    assert result['state'] == 'force-termination-failed'
    assert 'AccessDenied' in result['warning']
    assert not (tmp_path / op.PID_FILENAME).exists()


def test_force_restart_handles_invalid_pid_record_without_crashing(tmp_path, monkeypatch):
    (tmp_path / op.PID_FILENAME).write_text('{"pid": "garbage"}', encoding='utf-8')
    monkeypatch.setattr(op.psutil, 'Process', lambda pid: pytest.fail('no PID available'))
    result = op.stop_recorded_process(tmp_path, force_unverified=True)
    assert result['state'] == 'force-termination-failed'
    assert result['pid'] is None
    assert 'no usable process ID' in result['warning']
    assert not (tmp_path / op.PID_FILENAME).exists()


def test_cli_force_mode_kills_only_disposable_test_child(tmp_path):
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    try:
        op._write_record(tmp_path, _record(tmp_path, child.pid, psutil.Process(child.pid).create_time()))
        cmd = [sys.executable, '-m', 'main_computer.slh.organization_process',
               'stop', '--state-dir', str(tmp_path), '--force-unverified']
        run = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert run.returncode == 0, run.stderr
        data = json.loads(run.stdout)
        assert data['state'] == 'terminated-unverified', data
        assert child.wait(timeout=5) != 0
    finally:
        if child.poll() is None: child.kill()
        child.wait(timeout=5)


def test_powershell_start_and_stop_both_use_forced_shutdown():
    ps = (Path(__file__).resolve().parents[1] / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    assert 'Stop-MainComputer $RootPath $true $true | Out-Null' in ps
    assert 'Stop-MainComputer $resolvedRoot ([bool]$NoDocker) $true | Out-Null' in ps
    assert '--state-dir $stateDir --force-unverified' in ps
    assert 'Write-Warning ("FORCED STOP: killing unverified PID' in ps
    assert 'Force-StopUnverifiedPid $candidate $stopResult' in ps
    assert 'if ($slhFailure -and $ForceShutdown)' in ps
    assert 'Write-Warning ("RESTART cleanup raised an error; proceeding with launch anyway:' in ps


def test_forced_shutdown_is_wired_to_both_public_lifecycle_actions():
    ps = (Path(__file__).resolve().parents[1] / 'scripts/main-computer-start-stop.ps1').read_text(encoding='utf-8-sig')
    dispatch = ps.split('switch ($Action) {', 1)[1]
    assert '"start" {' in dispatch
    assert '"stop" {' in dispatch
    assert 'Stop-MainComputer $resolvedRoot ([bool]$NoDocker) $true | Out-Null' in dispatch
    assert 'Stop-MainComputer $RootPath $true $true | Out-Null' in ps
    org = ps.split('function Stop-MainComputerORGService(', 1)[1].split('\nfunction ', 1)[0]
    assert '--state-dir $stateDir --force-unverified' in org
    stop = ps.split('function Stop-MainComputer(', 1)[1].split('\n$resolvedRoot =', 1)[0]
    assert 'Stop-MainComputerORGService $RootPath $launchContext $ForceShutdown' in stop
    assert 'Force-StopUnverifiedPid $candidate $stopResult' in stop
    assert 'Main Computer stop completed with warnings.' in stop
    assert 'force_unverified_policy = $ForceShutdown' in stop
    assert 'force-termination-failed' in stop
    assert 'if ($slhFailure -and $ForceShutdown)' in stop


def test_forced_shutdown_kills_live_recycled_pid_even_when_strict_stop_calls_it_not_running(tmp_path, monkeypatch):
    record = _record(tmp_path, 57321, 1731624544.0)
    op._write_record(tmp_path, record)

    class RecycledProcess:
        pid = 57321
        alive = True
        kills = 0
        def create_time(self): return record['created'] + 100
        def is_running(self): return self.alive
        def status(self): return 'running' if self.alive else psutil.STATUS_ZOMBIE
        def kill(self):
            self.kills += 1
            self.alive = False
    proc = RecycledProcess()
    monkeypatch.setattr(op.psutil, 'Process', lambda pid: proc)
    monkeypatch.setattr(op.psutil, 'pid_exists', lambda pid: proc.alive)
    monkeypatch.setattr(op.time, 'sleep', lambda seconds: None)
    result = op.stop_recorded_process(tmp_path, force_unverified=True)
    assert result['state'] == 'terminated-unverified'
    assert 'recorded PID is live but process identity did not match' in result['warning']
    assert proc.kills == 1
    assert not (tmp_path / op.PID_FILENAME).exists()


def test_forced_shutdown_does_not_kill_nonexistent_recorded_pid(tmp_path, monkeypatch):
    record = _record(tmp_path, 57321, 1731624544.0)
    op._write_record(tmp_path, record)
    monkeypatch.setattr(op.psutil, 'Process', lambda pid: (_ for _ in ()).throw(psutil.NoSuchProcess(pid)))
    monkeypatch.setattr(op.psutil, 'pid_exists', lambda pid: False)
    result = op.stop_recorded_process(tmp_path, force_unverified=True)
    assert result['state'] == 'not-running'
    assert not (tmp_path / op.PID_FILENAME).exists()
