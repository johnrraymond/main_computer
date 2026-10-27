"""Guarded read-only Task Manager lab integration contracts."""
from pathlib import Path
import json

import pytest

from tools import mcel_task_manager_notebook_lab_acceptance as lab


def test_original_lab_witness_is_bitwise_derived_from_independent_ir():
    source = lab.ROOT / 'tools/fixtures/mcel_acid_tab_ir.json'
    embedded = (lab.SCRIPTS / 'mcel-task-manager-tab-witness.js').read_text(encoding='utf-8')
    assert embedded.startswith('/* Pinned independent IR witness.')
    assert json.loads(embedded.split('window.McelTaskManagerTabWitness = ', 1)[1].rstrip(' ;\n')) == json.loads(source.read_text())


def test_host_wiring_opt_in_only_and_existing_operations_preserved():
    app = (lab.ROOT / 'main_computer/web/applications.html').read_text()
    entry = (lab.SCRIPTS / 'task-manager.js').read_text()
    refresh = (lab.SCRIPTS / 'terminal.js').read_text()
    bridge = (lab.SCRIPTS / 'mcel-task-manager-notebook-bridge.js').read_text()
    assert app.index('mcel-task-manager-notebook-bridge.js') < app.index('applications/scripts/task-manager.js')
    assert app.index('mcel-view-transition.js') < app.index('mcel-tab-workspace.js')
    assert 'McelTaskManagerNotebookLab.optInHost(taskManagerApp)' in entry
    assert 'McelTaskManagerNotebookLab?.publish(taskManagerSnapshotCache, "available")' in refresh
    assert 'McelTaskManagerNotebookLab?.publish(null, "failed")' in refresh
    assert 'mcel_task_notebook' in bridge and '=== "lab"' in bridge
    assert 'fetch(' not in bridge and 'setInterval(' not in bridge and 'localStorage' not in bridge
    assert 'server_shutdown' in entry and 'runTaskAction(' in entry
    assert 'setTaskNotebookTab(button.dataset.taskTab' in entry
    router = (lab.SCRIPTS / 'app-routing.js').read_text(encoding='utf-8')
    assert 'taskManagerLabRouteSuffix()' in router
    assert 'const routeTarget = nextPath + taskManagerLabRouteSuffix()' in router
    assert 'window.McelTaskManagerNotebookLab?.unmount(taskManagerApp)' in router


def test_browser_lab_mount_and_exact_rollback(tmp_path):
    pytest.importorskip('playwright.sync_api')
    result = lab.run(tmp_path)
    assert result['status'] == 'PASS', result['errors']
    assert result['operatorActionsExecuted'] is False
    assert len(result['cases']) == 4
    for case in result['cases']:
        assert case['status'] == 'PASS', case
        assert case['rollback']['exactLegacy'] and case['rollback']['exactControls']
        assert not case['requests'] and not case['pageErrors']
        assert (tmp_path / case['screenshot']).stat().st_size > 1000
