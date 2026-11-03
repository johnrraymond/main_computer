"""Stable Task Manager acceptance regression tests; migrated without weakening negative gates."""
from __future__ import annotations

from datetime import datetime,timezone

from pathlib import Path

from types import SimpleNamespace

from unittest import mock

import pytest

from main_computer.task_manager import TaskManagerService

ROOT = Path(__file__).resolve().parents[1]

def test_windows_cim_json_millisecond_date_becomes_utc_iso():
    svc=TaskManagerService(ROOT)
    ms=1793595519217
    expected=datetime.fromtimestamp(ms/1000,timezone.utc).isoformat(timespec='milliseconds')
    assert svc._normalize_windows_creation_date(f'/Date({ms})/')==expected
    assert svc._normalize_windows_creation_date(f'/Date({ms}-0800)/')==expected
    assert datetime.fromisoformat(expected).utcoffset() is not None
    assert svc._normalize_windows_creation_date('garbage')==''
    assert svc._normalize_windows_creation_date('2026-11-01T01:00:00')==''

def test_windows_process_rows_do_not_expose_raw_powershell_date():
    svc=TaskManagerService(ROOT)
    ms=1793595519217
    output=[{'ProcessId':98765432,'Name':'python.exe','CommandLine':'python --test',
             'CreationDate':f'/Date({ms})/'}]
    with mock.patch.object(svc,'_run_powershell_json',return_value=output):
        rows=svc._powershell_process_rows(query='',include_all=True,limit=100)
    assert len(rows)==1
    assert rows[0]['pid']==98765432
    assert rows[0]['created_at'].startswith('2026-')
    assert '/Date' not in rows[0]['created_at']

def test_invalid_cim_generation_does_not_claim_valid_target_and_falls_back():
    svc=TaskManagerService(ROOT)
    with mock.patch('main_computer.task_manager.sys.platform','win32'), \
         mock.patch.object(svc,'_powershell_process_rows',return_value=[{'pid': 98765432,'created_at':''}]), \
         mock.patch.object(svc,'_psutil_process_rows',return_value=[{'pid':98765432,'created_at':'2026-11-01T01:00:00+00:00'}]) as fallback:
        rows=svc._process_rows(query='98765432',limit=100,include_all=True)
    fallback.assert_called_once()
    assert rows[0]['created_at']

def test_timestamp_millisecond_precision_accepted_only_for_matching_birth(monkeypatch):
    svc=TaskManagerService(ROOT)
    ms=1793595519217
    expected=svc._normalize_windows_creation_date(f'/Date({ms})/')

    class Child:
        def __init__(self,offset):
            self.offset=offset;self.terminated=False
        def create_time(self):return ms/1000+self.offset
        def terminate(self):self.terminated=True
        def wait(self,timeout=None):return 0

    child=Child(0.0004)
    fake=SimpleNamespace(Process=lambda _pid:child,NoSuchProcess=OSError,
                         AccessDenied=PermissionError,ZombieProcess=RuntimeError)
    with mock.patch('main_computer.task_manager.psutil',fake):
        result=svc._process_action(action='terminate_pid',pid=98765432,confirm=True,
                                   expected_created_at=expected)
        assert result['ok'] and child.terminated
        assert result['verified_created_at']==expected
        child.offset=0.020
        with pytest.raises(ValueError,match='Process generation mismatch'):
            svc._process_action(action='terminate_pid',pid=98765432,confirm=True,
                                expected_created_at=expected)
        with pytest.raises(ValueError,match='Invalid process generation timestamp'):
            svc._process_action(action='terminate_pid',pid=98765432,confirm=True,
                                expected_created_at='/Date(1793595519217)/')

def test_isolated_effects_verify_same_pid_and_generation_at_reported_precision():
    from tools.mcel_task_manager_acceptance.lab import _verified_host_generation
    milliseconds=1793595519217
    os_epoch=milliseconds/1000+0.0004
    iso=datetime.fromtimestamp(milliseconds/1000,timezone.utc).isoformat(timespec='milliseconds')
    assert _verified_host_generation({'pid':77,'created_at':iso},pid=77,observed_epoch=os_epoch)==iso
    assert _verified_host_generation({'pid':78,'created_at':iso},pid=77,observed_epoch=os_epoch) is None
    assert _verified_host_generation({'pid':77,'created_at':iso},pid=77,observed_epoch=os_epoch+0.02) is None
    assert _verified_host_generation({'pid':77,'created_at':f'/Date({milliseconds})/'},pid=77,observed_epoch=os_epoch) is None
    assert _verified_host_generation({'pid':77,'created_at':'2026-11-01T00:00:00'},pid=77,observed_epoch=os_epoch) is None

import os

from datetime import datetime

from pathlib import Path

from unittest import mock

from main_computer.task_manager import TaskManagerService

ROOT = Path(__file__).resolve().parents[1]

def _cim_failure(*, query, limit, include_all, diagnostic=None):
    if diagnostic is not None:
        diagnostic.update(sourceKind='win32-cim-creation-date', status='timeout', timeoutSeconds=5.0)
    return []

def test_win32_cim_timeout_falls_back_to_genuine_psutil_pid_generation():
    service = TaskManagerService(ROOT)
    diagnostic = {}
    with mock.patch('main_computer.task_manager.sys.platform', 'win32'), \
            mock.patch.object(service, '_powershell_process_rows', side_effect=_cim_failure):
        rows = service._process_rows(query=str(os.getpid()), limit=100, include_all=True,
                                     diagnostic=diagnostic)
    current = next(row for row in rows if row['pid'] == os.getpid())
    datetime.fromisoformat(current['created_at'])
    assert current['created_at'].endswith('+00:00')
    assert current['is_current_process'] is True
    assert diagnostic['sourceKind'] == 'psutil-create-time-fallback'
    assert diagnostic['status'] == 'ok'
    assert diagnostic['primaryStatus'] == 'timeout'
    assert diagnostic['fallbackUsed'] is True
    assert diagnostic['discoveredCount'] > 0

def test_win32_successful_cim_does_not_use_backup():
    service = TaskManagerService(ROOT)
    with mock.patch('main_computer.task_manager.sys.platform', 'win32'), \
            mock.patch.object(service, '_powershell_process_rows',
                              return_value=[{'pid': 42, 'created_at': '2026-11-01T01:00:00+00:00'}]), \
            mock.patch.object(service, '_psutil_process_rows') as fallback:
        rows = service._process_rows(query='', limit=5, include_all=True)
    assert rows[0]['pid'] == 42
    fallback.assert_not_called()

def test_win32_failure_without_psutil_is_not_success():
    service = TaskManagerService(ROOT)
    diagnostic = {}
    with mock.patch('main_computer.task_manager.sys.platform', 'win32'), \
            mock.patch('main_computer.task_manager.psutil', None), \
            mock.patch.object(service, '_powershell_process_rows', side_effect=_cim_failure):
        rows = service._process_rows(query='', limit=100, include_all=True,
                                     diagnostic=diagnostic)
    assert rows == []
    assert diagnostic['status'] == 'timeout'
    assert diagnostic['fallbackUsed'] is False
    assert diagnostic['fallbackReason'] == 'psutil-unavailable'

def test_win32_failed_psutil_fallback_does_not_forge_observations():
    service = TaskManagerService(ROOT)
    diagnostic = {}
    with mock.patch('main_computer.task_manager.sys.platform', 'win32'), \
            mock.patch.object(service, '_powershell_process_rows', side_effect=_cim_failure), \
            mock.patch.object(service, '_psutil_process_rows', return_value=[]) as backup:
        rows = service._process_rows(query=str(os.getpid()), limit=100, include_all=True,
                                     diagnostic=diagnostic)
    assert rows == []
    backup.assert_called_once()
    assert diagnostic['fallbackUsed'] is True
    assert diagnostic.get('status') != 'ok'

def test_win32_snapshot_preserves_public_shape_and_exposes_opt_in_provenance():
    service = TaskManagerService(ROOT)
    with mock.patch('main_computer.task_manager.sys.platform', 'win32'), \
            mock.patch.object(service, '_powershell_process_rows', side_effect=_cim_failure), \
            mock.patch.object(service, '_connection_rows', return_value=[]), \
            mock.patch.object(service, '_server_summary', return_value={'running': False}), \
            mock.patch.object(service, '_read_schedules', return_value={'schedules': []}), \
            mock.patch.object(service, '_hardware_snapshot', return_value={'cpu': {}, 'gpu': {}}):
        ordinary = service.snapshot(query=str(os.getpid()), limit=100, include_all=True,
                                    include_connections=False)
        shadow = service.snapshot(query=str(os.getpid()), limit=100, include_all=True,
                                  include_connections=False, process_diagnostics=True)
    assert ordinary['processes'] and shadow['processes']
    assert '_mcel_task_process_producer' not in ordinary
    diag = shadow['_mcel_task_process_producer']
    assert diag['sourceKind'] == 'psutil-create-time-fallback'
    assert diag['status'] == 'ok'

def test_numeric_query_with_unrelated_cim_match_rechecks_os_pid():
    service = TaskManagerService(ROOT)
    pid = os.getpid()
    # Real Win32 CIM query semantics include command-line substring matches.
    # This is not proof the requested PID was actually enumerated.
    wrong = [{'pid': pid + 10_000_000, 'created_at': '2026-11-01T01:00:00+00:00'}]
    diagnostic = {}
    with mock.patch('main_computer.task_manager.sys.platform', 'win32'), \
            mock.patch.object(service, '_powershell_process_rows', return_value=wrong):
        rows = service._process_rows(query=str(pid), limit=100, include_all=True,
                                     diagnostic=diagnostic)
    assert any(row['pid'] == pid and row['created_at'] for row in rows)
    assert diagnostic['primaryMissingRequestedPid'] is True
    assert diagnostic['sourceKind'] == 'psutil-create-time-fallback'

