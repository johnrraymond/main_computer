"""Stage 4 hosted-source repair: test Win32 CIM failures without a Windows host.

The output of a failing subprocess must never be confused with an authoritative
zero-process enumeration. Tests are source-level and have no operational effects.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

from main_computer.task_manager import TaskManagerService, POWERSHELL_PROCESS_TIMEOUT_SECONDS


def service() -> TaskManagerService:
    return TaskManagerService(Path(__file__).resolve().parents[1])


def test_process_cim_uses_longer_bounded_timeout_and_reports_source():
    svc = service()
    diag = {}
    payload = json.dumps([{'ProcessId': 100, 'Name': 'python.exe', 'CommandLine': 'main_computer',
                           'CreationDate': '20261027172830.000000-420'}])
    with mock.patch('main_computer.task_manager.subprocess.run',
                    return_value=subprocess.CompletedProcess([], 0, payload, '')) as run:
        rows = svc._powershell_process_rows(query='', limit=24, include_all=False, diagnostic=diag)
    assert run.call_args.kwargs['timeout'] == POWERSHELL_PROCESS_TIMEOUT_SECONDS
    assert POWERSHELL_PROCESS_TIMEOUT_SECONDS > 1.5
    assert diag['status'] == 'ok'
    assert diag['sourceKind'] == 'win32-cim-creation-date'
    assert diag['discoveredCount'] == diag['matchingCount'] == diag['servedCount'] == 1
    assert rows[0]['pid'] == 100
    assert rows[0]['created_at'] == '20261027172830.000000-420'


def test_cim_timeout_not_silent_zero():
    svc=service(); diag={}
    with mock.patch('main_computer.task_manager.subprocess.run',
                    side_effect=subprocess.TimeoutExpired(['powershell'], 5)):
        rows=svc._powershell_process_rows(query='',limit=24,include_all=False,diagnostic=diag)
    assert rows == []
    assert diag['status'] == 'timeout'
    assert diag['sourceKind'] == 'win32-cim-creation-date'
    assert diag['timeoutSeconds'] == POWERSHELL_PROCESS_TIMEOUT_SECONDS
    assert diag['discoveredCount'] == 0


def test_cim_failure_and_unparsable_output_report_specific_errors():
    svc=service()
    cases = [(1, '', 'failed', 'command-error'),
             (0, '', '', 'empty-output'),
             (0, 'not-json', '', 'invalid-json')]
    for rc, out, err, expected in cases:
        diag={}
        with mock.patch('main_computer.task_manager.subprocess.run',
                        return_value=subprocess.CompletedProcess([], rc, out, err)):
            assert svc._powershell_process_rows(query='',limit=24,include_all=False,
                                                diagnostic=diag) == []
        assert diag['status'] == expected


def test_empty_cim_result_never_treated_as_successful_enumeration():
    diag={}
    with mock.patch('main_computer.task_manager.subprocess.run',
                    return_value=subprocess.CompletedProcess([],0,'[]','')):
        rows=service()._powershell_process_rows(query='',limit=24,include_all=True,diagnostic=diag)
    assert rows == []
    assert diag['status'] == 'empty-cim-result'


def test_filtered_zero_is_distinct_from_failed_cim_query():
    payload=json.dumps([{'ProcessId': 70, 'Name': 'explorer.exe', 'CommandLine': '',
                         'CreationDate': '20261027172830.000000-420'}]);diag={}
    with mock.patch('main_computer.task_manager.subprocess.run',
                    return_value=subprocess.CompletedProcess([],0,payload,'')):
        rows=service()._powershell_process_rows(query='',limit=24,include_all=False,diagnostic=diag)
    assert rows == []
    assert diag['status']=='ok'
    assert diag['discoveredCount']==1
    assert diag['matchingCount']==0
    assert diag['boundedByFilter']


def test_process_diagnostics_do_not_change_default_snapshot_contract():
    svc=service()
    with mock.patch.object(svc,'_process_rows',return_value=[]), \
         mock.patch.object(svc,'_connection_rows',return_value=[]), \
         mock.patch.object(svc,'_server_summary',return_value={'running':False}), \
         mock.patch.object(svc,'_read_schedules',return_value={'schedules':[]}), \
         mock.patch.object(svc,'_hardware_snapshot',return_value={'cpu':{},'gpu':{}}):
        ordinary=svc.snapshot(include_connections=False)
        observed=svc.snapshot(include_connections=False,process_diagnostics=True)
    assert '_mcel_task_process_producer' not in ordinary
    assert observed['_mcel_task_process_producer']=={}
    assert ordinary['processes']==observed['processes']==[]


def test_source_provenance_not_inferred_from_process_name():
    svc=service()
    diag={}
    with mock.patch('main_computer.task_manager.sys.platform','linux'), \
         mock.patch('main_computer.task_manager.psutil',None):
        assert svc._process_rows(query='',limit=5,include_all=False,diagnostic=diag)==[]
    assert diag=={'sourceKind':'psutil-create-time','status':'unavailable'}
