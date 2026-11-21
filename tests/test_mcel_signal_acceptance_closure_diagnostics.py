"""Regression checks for signal-integrated full acceptance diagnostics.

These are harness-only checks. They neither enable production signals nor
replace application behavior with a test success.
"""
from __future__ import annotations

from pathlib import Path

from tools import mcel_task_manager_full_functional_acceptance as task


def test_separate_module_lanes_report_every_completed_test(monkeypatch):
    seen = []
    def fake_subprocess(command, *, timeout, environment):
        seen.append((command[-1], timeout, environment))
        return {
            'status': 'PASS', 'passedTests': 3 if len(seen) == 1 else 4,
            'seconds': 2.0, 'outputTail': '3 passed', 'exitCode': 0,
            'command': command,
        }
    monkeypatch.setattr(task, 'subprocess_lane', fake_subprocess)
    result = task.module_lanes(('tests/first.py', 'tests/second.py'), timeout=120,
                               environment={'MCEL_SIGNAL_SCHEDULER_TASK_MANAGER': 'certified'})
    assert result['status'] == 'PASS'
    assert result['passedTests'] == 7
    assert [lane['module'] for lane in result['lanes']] == ['tests/first.py','tests/second.py']
    assert all(item[1] == 120 for item in seen)
    assert all(item[2]['MCEL_SIGNAL_SCHEDULER_TASK_MANAGER'] == 'certified' for item in seen)


def test_module_timeout_is_visible_and_cannot_turn_into_success(monkeypatch):
    calls = iter([
        {'status': 'PASS', 'passedTests': 17, 'seconds': 30.0, 'outputTail': '17 passed', 'exitCode': 0},
        {'status': 'TIMEOUT', 'passedTests': 0, 'seconds': 120.0, 'outputTail': 'still running', 'exitCode': None},
    ])
    def fake_subprocess(command, *, timeout, environment):
        return {**next(calls), 'command': command}
    monkeypatch.setattr(task,'subprocess_lane',fake_subprocess)
    result=task.module_lanes(('tests/a.py','tests/hung.py'), timeout=120,
                             environment={'MCEL_SIGNAL_SCHEDULER_TASK_MANAGER':'certified'})
    assert result['status']=='TIMEOUT'
    assert result['passedTests']==17
    assert result['lanes'][1]['module']=='tests/hung.py'
    assert 'tests/hung.py' in result['outputTail']


def test_empty_module_does_not_count_as_pass(monkeypatch):
    def fake_subprocess(command, *, timeout, environment):
        return {'status':'PASS','passedTests':0,'seconds':0.1,
                'outputTail':'nothing ran','exitCode':0,'command':command}
    monkeypatch.setattr(task,'subprocess_lane',fake_subprocess)
    result=task.module_lanes(('tests/empty.py',),timeout=30,environment={})
    assert result['status']=='FAIL'
    assert result['lanes'][0]['reason']=='EMPTY_OR_SKIPPED_TEST_MODULE'


def test_full_acceptance_keeps_all_original_test_modules():
    assert len(task.FULL_TESTS)==8
    assert len(task.SMOKE_TESTS)==6
    assert all(Path(item.split('::')[0]).is_file() for item in task.FULL_TESTS)
