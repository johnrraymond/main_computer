"""Windows CIM fallback: genuine OS-backed observation, no fabricated success."""
from __future__ import annotations

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
