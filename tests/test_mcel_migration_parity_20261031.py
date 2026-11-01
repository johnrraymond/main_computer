"""Regressions for the 2026-10-31 MCEL application parity migration.

These are non-Windows source/runtime checks, not live host acceptance tests.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"


def test_document_tabs_retain_independent_dirty_drafts_and_approve_close() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node unavailable")
    code = f"""
const assert = require('node:assert/strict');
const core = require({json.dumps(str(SCRIPTS / 'code-editor-core.js'))});
const h = 'a'.repeat(64);
let s = core.initialState();
s = core.applyOpenedFile(s, {{path:'src/first.js',content:'one();',sourceHash:h}});
s = core.applyDraftEdit(s, {{text:'one(1);'}});
s = core.applyOpenedFile(s, {{path:'src/second.js',content:'two();',sourceHash:h}});
s = core.applyDraftEdit(s, {{text:'two(2);'}});
assert.equal(s.documents.length, 2);
assert.equal(s.documents[0].draft.text, 'one(1);');
assert.equal(s.documents[1].draft.text, 'two(2);');
s = core.activateDocument(s,'src/first.js');
assert.equal(s.draft.text, 'one(1);');
assert.equal(s.draft.dirty, true);
assert.throws(()=>core.closeFile(s), e=>e.code==='dirty-document-close-refused');
assert.throws(()=>core.applyWorkspaceInspection(s,{{repoDir:'another-repo',files:[]}}),
  e=>e.code==='repo-change-open-documents-refused');
s = core.closeFile(s,{{discard:true}});
assert.equal(s.documents.length, 1);
assert.equal(s.activeFile.path, 'src/second.js');
assert.equal(s.draft.text, 'two(2);');
assert.equal(s.draft.dirty, true);
s = core.discardDraft(s);
assert.equal(s.draft.dirty,false);
assert.equal(s.documents[0].draft.text,'two();');
s = core.closeFile(s);
assert.equal(s.activeFile,null);
assert.equal(s.documents.length,0);
assert.equal(Object.isFrozen(s),true);
console.log('PASS document-tabs');
"""
    result = subprocess.run([node, "-e", code], capture_output=True, text=True, cwd=ROOT, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "PASS document-tabs" in result.stdout


def test_document_tabs_and_aider_transaction_controls_exist_in_both_surfaces() -> None:
    runtime = (SCRIPTS / "code-editor.js").read_text(encoding="utf-8")
    assert "renderDocumentTabs" in runtime and "bindDocumentTabs" in runtime
    assert "core.activateDocument(state, path)" in runtime
    assert "#code-editor-transaction-preview" in runtime
    assert "#code-editor-transaction-apply" in runtime
    assert 'id="code-editor-transaction-preview"' in runtime
    assert 'id="code-editor-transaction-apply"' in runtime
    assert 'bindButton("#aider-run"' not in runtime
    assert "Discard unsaved changes" in runtime
    aider = (SCRIPTS / "code-editor-aider-actions.js").read_text(encoding="utf-8")
    assert 'sendAiderAction("/api/applications/aider/prepare"' in aider
    assert 'sendAiderAction("/api/applications/aider/run"' in aider
    assert "aiderArchiveList" in aider


def test_file_hierarchy_uses_scoped_adapter_and_never_mutates() -> None:
    bridge = (SCRIPTS / "mcel-file-explorer-candidate-bridge.js").read_text(encoding="utf-8")
    assert "renderHierarchy" in bridge
    assert 'setAttribute("role","tree")' in bridge
    assert 'setAttribute("aria-expanded"' in bridge
    assert 'execute("listDirectory",{rootId:cachedRoot,relativePath:path})' in bridge
    assert 'execute("previewEntry",{rootId:cachedRoot,relativePath:path,entry})' in bridge
    assert "adapter.executeIntent(intent,params)" in bridge
    assert "/api/applications/file-explorer/delete" not in bridge


def test_task_manager_reuses_host_guarded_operations_and_existing_auto_refresh() -> None:
    bridge = (SCRIPTS / "mcel-task-manager-notebook-bridge.js").read_text(encoding="utf-8")
    task = (SCRIPTS / "task-manager.js").read_text(encoding="utf-8")
    host = (SCRIPTS / "terminal.js").read_text(encoding="utf-8")
    assert "installOperationsPanel" in bridge
    assert "global.runTaskAction(action" in bridge
    assert 'observed.has(pid)' in bridge
    assert 'global.confirm(message)' in bridge
    assert '"auto-refresh": root.querySelector("#task-auto-refresh")' in bridge
    assert 'taskAutoRefresh.addEventListener("change", scheduleTaskManagerAutoRefresh)' in task
    assert 'window.setInterval(() =>' in host
    assert '}, 5000);' in host
    for name in ("server_start", "server_shutdown", "server_restart", "terminate_pid", "kill_pid"):
        assert name in bridge


def test_windows_live_acceptance_refuses_non_windows_without_host_io(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("Run live smoke on the Windows host instead")
    from tools.mcel_migrations_windows_smoke import run
    output = tmp_path / "report.json"
    result = run("http://127.0.0.1:8765", output=output)
    assert result["overall"] == "REFUSED_NON_WINDOWS"
    assert not output.exists()
