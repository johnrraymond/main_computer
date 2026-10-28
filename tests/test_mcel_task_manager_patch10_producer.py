"""Read-only producer contract tests: no external PowerShell execution."""
from pathlib import Path
from main_computer.task_manager import TaskManagerService


def test_windows_unmeasured_rss_is_not_zero():
    service=TaskManagerService.__new__(TaskManagerService)
    service.repo_root=Path('/temporary/main_computer')
    service._run_powershell_json=lambda _script: [
        {'ProcessId':2836,'Name':'python.exe','CommandLine':'python --task',
         'CreationDate':'2026-10-27T09:12:01.0000000-07:00'}]
    service._is_main_computer_process=lambda **_:True
    rows=service._powershell_process_rows(query='',limit=24,include_all=True)
    assert len(rows)==1
    record=rows[0]
    assert record['memory_rss'] is None
    assert record['memory_human']=='n/a'
    assert record['memory_availability']=='unavailable'
    assert record['status_availability']=='unavailable'
    assert record['created_at']=='2026-10-27T09:12:01.0000000-07:00'
    assert record['pid']==2836


def test_source_fidelity_and_safe_composition():
    scripts=Path(__file__).resolve().parents[1]/'main_computer/web/applications/scripts'
    general=(scripts/'mcel-semantic-collection-workspace.js').read_text()
    for app_name in ('pid','memory_rss','is_main_computer','task-manager','created_at'):
        assert app_name not in general
    contract=(scripts/'mcel-task-manager-process-contract.js').read_text()
    assert 'mcel.semantic-collection-spec.v2' in contract
    assert 'memory_availability' in contract
    assert 'generationFrom:"created_at"' in contract
