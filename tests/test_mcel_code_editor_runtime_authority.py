from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"


def test_code_editor_runtime_scripts_load_after_legacy_studio_without_semantic_adapter() -> None:
    shell = APPLICATIONS_HTML.read_text(encoding="utf-8")
    expected_order = [
        "applications/scripts/code-editor-monaco-adapter.js",
        "applications/scripts/code-editor-mcel-studio.js",
        "applications/scripts/code-editor-core.js",
        "applications/scripts/code-editor-view-model.js",
        "applications/scripts/code-editor-capabilities.js",
        "applications/scripts/code-editor.js",
    ]

    positions = [shell.index(item) for item in expected_order]
    assert positions == sorted(positions)
    assert "applications/scripts/code-editor-semantic-adapter.js" not in shell


def test_code_editor_runtime_is_direct_canonical_facade_not_old_studio_shim() -> None:
    runtime = (SCRIPTS / "code-editor.js").read_text(encoding="utf-8")
    core = (SCRIPTS / "code-editor-core.js").read_text(encoding="utf-8")
    capabilities = (SCRIPTS / "code-editor-capabilities.js").read_text(encoding="utf-8")
    monaco_adapter = (SCRIPTS / "code-editor-monaco-adapter.js").read_text(encoding="utf-8")

    assert "root.MainComputerCodeEditorRuntime = api.createCodeEditorRuntime" in runtime
    assert 'facade: "MainComputerCodeEditorRuntime"' in runtime
    assert "MainComputerMonacoAdapter" in runtime
    assert 'id="code-studio-runtime-monaco" class="mcel-code-editor-authoring-host code-studio-monaco-host"' in runtime
    assert 'data-code-editor-monaco-host="true"' in runtime
    assert "hostIsDirectPane" in runtime
    assert "monacoMounted" in runtime
    assert "preserveExistingCodeStudioSurface" in runtime
    assert "activateLegacyRuntimePane" in runtime
    assert 'runtimePane.dataset.codeEditorRuntimePrimaryPane = "true"' in runtime
    assert "legacy-fidelity" in runtime
    assert "runtimePreviewIsPrimaryEditorHost" in runtime
    assert "monacoDocumentFor(active)" in runtime
    assert "__no-file-open__.md" in runtime
    assert "does not flip" in runtime
    assert "dispose(\"close-file\")" not in runtime
    assert 'const LOCAL_VS_BASE = "/applications/vendor/monaco-editor/min/vs";' in monaco_adapter
    assert "readOnly: options.readOnly === true" in monaco_adapter
    assert "window.__CE_MONACO_MODEL__ = model" in monaco_adapter
    assert "activeSession.host" in monaco_adapter
    assert "MainComputerCodeStudio" not in runtime
    assert "MainComputerCodeStudio" not in core
    assert "MainComputerCodeStudio" not in capabilities


def test_code_editor_core_rejects_paths_outside_workspace() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; Code Editor runtime tests cannot run")

    probe = f"""
const core = require({json.dumps(str(SCRIPTS / "code-editor-core.js"))});
const rejected = [];
for (const path of ["../secrets.txt", "/etc/passwd", "C:/tmp/file.txt", "https://example.test/x"]) {{
  try {{
    core.normalizeProjectPath(path);
  }} catch (error) {{
    rejected.push(error.code);
  }}
}}
const request = core.buildSaveRequest(core.applyDraftEdit(core.applyOpenedFile(core.initialState(), {{
  path: "src/app.js",
  content: "console.log(1);",
  sourceHash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}}), {{
  path: "src/app.js",
  text: "console.log(2);"
}}));
console.log(JSON.stringify({{
  rejected,
  request,
}}));
"""
    completed = subprocess.run([node, "-e", probe], check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["rejected"] == [
        "path-outside-workspace",
        "path-outside-workspace",
        "path-outside-workspace",
        "path-outside-workspace",
    ]
    assert payload["request"]["path"] == "src/app.js"
    assert payload["request"]["explicit_save"] is True
    assert payload["request"]["stale_source_checked"] is True
    assert payload["request"]["write_policy"] == "explicit-save"


def test_code_editor_runtime_executes_basic_open_edit_save_lane_with_receipts() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; Code Editor runtime tests cannot run")

    probe = f"""
globalThis.TextEncoder = TextEncoder;
require({json.dumps(str(SCRIPTS / "code-editor-core.js"))});
require({json.dumps(str(SCRIPTS / "code-editor-view-model.js"))});
require({json.dumps(str(SCRIPTS / "code-editor-capabilities.js"))});
const moduleApi = require({json.dumps(str(SCRIPTS / "code-editor.js"))});
const core = globalThis.MainComputerCodeEditorCore;
const capabilities = {{
  inspectWorkspace: async () => ({{
    repoDir: ".",
    files: [{{path: "src/app.js", name: "app.js", kind: "file", bytes: 15}}],
  }}),
  openFile: async () => ({{
    repoDir: ".",
    path: "src/app.js",
    content: "console.log(1);",
    sourceHash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  }}),
  saveFile: async (request) => ({{
    ok: true,
    savedPath: request.path,
    changedFiles: [request.path],
    handle: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    status: "pass",
    raw: {{request}},
  }}),
  previewAiderPlan: async () => {{ throw new Error("not used"); }},
  applyReviewedPatch: async () => {{ throw new Error("not used"); }},
  sha256Text: async () => "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
}};
const runtime = moduleApi.createCodeEditorRuntime({{
  core,
  viewModel: globalThis.MainComputerCodeEditorViewModel,
  capabilities,
}});
(async () => {{
  await runtime.inspectWorkspace();
  await runtime.openFile({{path: "src/app.js"}});
  runtime.editDraft({{path: "src/app.js", text: "console.log(2);"}});
  const saved = await runtime.saveFile();
  const state = runtime.state();
  console.log(JSON.stringify({{
    facade: runtime.facade,
    activePath: state.activeFile.path,
    activeContent: state.activeFile.content,
    dirty: state.draft.dirty,
    receiptIntents: state.receipts.slice(0, 4).map((receipt) => receipt.intent),
    savedOk: saved.ok,
    mutationAllowed: state.receipts[0].mutationAllowed,
  }}));
}})().catch((error) => {{
  console.error(error.stack || error.message || error);
  process.exit(1);
}});
"""
    completed = subprocess.run([node, "-e", probe], check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload == {
        "facade": "MainComputerCodeEditorRuntime",
        "activePath": "src/app.js",
        "activeContent": "console.log(2);",
        "dirty": False,
        "receiptIntents": ["saveFile", "editDraft", "openFile", "inspectWorkspace"],
        "savedOk": True,
        "mutationAllowed": True,
    }


def test_code_editor_runtime_executes_reviewed_patch_lane_with_review_gate() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; Code Editor runtime tests cannot run")

    probe = f"""
globalThis.TextEncoder = TextEncoder;
require({json.dumps(str(SCRIPTS / "code-editor-core.js"))});
require({json.dumps(str(SCRIPTS / "code-editor-view-model.js"))});
require({json.dumps(str(SCRIPTS / "code-editor-capabilities.js"))});
const moduleApi = require({json.dumps(str(SCRIPTS / "code-editor.js"))});
const core = globalThis.MainComputerCodeEditorCore;
const calls = [];
const capabilities = {{
  inspectWorkspace: async () => ({{repoDir: ".", files: []}}),
  openFile: async () => ({{
    repoDir: ".",
    path: "src/app.js",
    content: "console.log(1);",
    sourceHash: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  }}),
  saveFile: async () => {{ throw new Error("not used"); }},
  previewAiderPlan: async (request) => {{
    calls.push(["preview", request]);
    return {{
      ok: true,
      handle: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      status: "prepared",
      transaction: {{
        project_root: ".",
        changes: request.changes.map((change) => ({{path: change.path, operation: change.operation}})),
      }},
    }};
  }},
  applyReviewedPatch: async (request) => {{
    calls.push(["apply", request]);
    return {{
      ok: true,
      handle: request.handle,
      status: "applied",
      changedFiles: ["src/app.js"],
      receipt: {{transaction_id: "tx-1", files: [{{path: "src/app.js"}}]}},
    }};
  }},
  sha256Text: async () => "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
}};
const runtime = moduleApi.createCodeEditorRuntime({{
  core,
  viewModel: globalThis.MainComputerCodeEditorViewModel,
  capabilities,
}});
(async () => {{
  await runtime.openFile({{path: "src/app.js"}});
  runtime.editDraft({{path: "src/app.js", text: "console.log(3);"}});
  await runtime.previewAiderPlan();
  await runtime.applyReviewedPatch({{reviewed: true, approved: true}});
  const blocked = await runtime.applyReviewedPatch({{handle: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", reviewed: true}});
  const state = runtime.state();
  console.log(JSON.stringify({{
    calls,
    pending: state.pendingPatch,
    receipts: state.receipts.slice(0, 4).map((receipt) => ({{
      intent: receipt.intent,
      ok: receipt.ok,
      mutationAllowed: receipt.mutationAllowed,
      handle: receipt.handle,
    }})),
    blockedOk: blocked.ok,
    lastError: state.lastError,
  }}));
}})().catch((error) => {{
  console.error(error.stack || error.message || error);
  process.exit(1);
}});
"""
    completed = subprocess.run([node, "-e", probe], check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload["calls"][0][0] == "preview"
    assert payload["calls"][0][1]["mutation_allowed"] is False
    assert payload["calls"][0][1]["changes"] == [
        {
            "operation": "modify",
            "path": "src/app.js",
            "replacement_text": "console.log(3);",
            "expected_before_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        }
    ]
    assert payload["calls"][1] == [
        "apply",
        {
            "repo_dir": ".",
            "handle": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "reviewed": True,
            "approved": True,
            "confirmed": False,
            "require_project_manifest": False,
        },
    ]
    assert payload["pending"]["state"] == "applied"
    assert payload["pending"]["reviewed"] is True
    assert payload["pending"]["approved"] is True
    assert payload["receipts"] == [
        {
            "intent": "applyReviewedPatch",
            "ok": False,
            "mutationAllowed": False,
            "handle": "",
        },
        {
            "intent": "applyReviewedPatch",
            "ok": True,
            "mutationAllowed": True,
            "handle": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        },
        {
            "intent": "previewAiderPlan",
            "ok": True,
            "mutationAllowed": False,
            "handle": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        },
        {
            "intent": "editDraft",
            "ok": True,
            "mutationAllowed": False,
            "handle": "",
        },
    ]
    assert payload["blockedOk"] is False
    assert payload["lastError"]["code"] == "reviewed-patch-approval-required"


def test_code_editor_runtime_installs_canonical_surface_over_legacy_host_markup() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; Code Editor runtime tests cannot run")

    probe = f"""
const moduleApi = require({json.dumps(str(SCRIPTS / "code-editor.js"))});
const root = {{
  innerHTML: '<div class="legacy">old host surface</div>',
  querySelector: () => null,
}};
moduleApi.ensureCanonicalSurface(root);
console.log(JSON.stringify({{
  hasSurface: root.innerHTML.includes('data-code-editor-runtime-surface="dsl-native"'),
  hasFacadeOwnedDraft: root.innerHTML.includes('id="code-studio-runtime-draft"'),
  hasMonacoHost: root.innerHTML.includes('id="code-studio-runtime-monaco"')
    && root.innerHTML.includes('data-code-editor-monaco-host="true"'),
  hasMonacoStatus: root.innerHTML.includes('id="code-editor-monaco-status"'),
  hasPermanentMonacoMessage: root.innerHTML.includes('Monaco editor mounts here and remains visible'),
  hasWorkspaceLoader: root.innerHTML.includes('id="file-map-refresh"'),
  hasReviewedApply: root.innerHTML.includes('id="aider-run"'),
  hasStatusWidgets: root.innerHTML.includes('id="code-editor-mcel-surface-status"')
    && root.innerHTML.includes('id="code-editor-mcel-authoring-status"')
    && root.innerHTML.includes('id="code-editor-mcel-preview-status"'),
}}));
"""
    completed = subprocess.run([node, "-e", probe], check=True, capture_output=True, text=True)
    payload = json.loads(completed.stdout)

    assert payload == {
        "hasSurface": True,
        "hasFacadeOwnedDraft": True,
        "hasMonacoHost": True,
        "hasMonacoStatus": True,
        "hasPermanentMonacoMessage": True,
        "hasWorkspaceLoader": True,
        "hasReviewedApply": True,
        "hasStatusWidgets": True,
    }


def test_code_editor_runtime_preserves_authored_code_studio_chrome_for_ui_fidelity() -> None:
    runtime = (SCRIPTS / "code-editor.js").read_text(encoding="utf-8")
    styles = (ROOT / "main_computer/web/applications/styles/code-editor.css").read_text(encoding="utf-8")

    assert "preserveExistingCodeStudioSurface(rootNode)" in runtime
    assert 'rootNode.dataset.codeEditorRuntimeSurfaceMode = "legacy-fidelity"' in runtime
    assert 'rootNode.dataset.codeEditorMode = "legacy-fidelity"' in runtime
    assert 'existingShell.dataset.codeEditorRuntimeSurfaceMode = "legacy-fidelity"' in runtime
    assert "renderPrimaryEditorPreview(active, patch, receipts)" in runtime
    assert "code-studio-monaco-authoring-surface mcel-code-editor-primary-authoring-surface" in runtime
    assert 'runtimePreview.dataset.codeEditorRuntimePrimaryHost = "true"' in runtime
    assert "activateLegacyRuntimePane();" in runtime
    assert 'runtimePane.dataset.codeEditorRuntimeOwnedPane = "true"' in runtime
    assert 'tab.getAttribute("data-code-studio-tab") === "runtime"' in runtime
    assert "#code-editor-app[data-code-editor-runtime-surface-mode=\"legacy-fidelity\"] .code-studio-body" in styles
    assert "Patch 11: legacy-fidelity grid-area repair" in styles
    assert "Patch 12: legacy-fidelity mode separates" in styles
    assert "Patch 14: legacy-fidelity shell direct-child containment" in styles
    assert '#code-editor-app[data-code-editor-runtime-surface-mode="legacy-fidelity"] .code-studio-shell > *' in styles
    assert "grid-auto-columns: 0 !important;" in styles
    assert '#code-editor-app[data-code-editor-mode="legacy-fidelity"][data-code-editor-runtime-surface-mode="legacy-fidelity"] .code-studio-body' in styles
    assert 'grid-template-areas: "activitybar sidebar editor inspector" !important;' in styles
    assert 'grid-template-areas: "activitybar sidebar editor" !important;' in styles
    assert "grid-column: 3 !important;" in styles
    assert "#code-editor-app[data-code-editor-runtime-surface-mode=\"legacy-fidelity\"] .code-studio-inspector" in styles
    assert "#code-editor-app[data-code-editor-runtime-surface-mode=\"legacy-fidelity\"] .mcel-code-editor-primary-authoring-surface" in styles


def test_code_editor_legacy_fidelity_source_workspace_opens_without_old_studio_gate() -> None:
    runtime = (SCRIPTS / "code-editor.js").read_text(encoding="utf-8")

    assert "sourceWorkspaceEditor" in runtime
    assert "#code-studio-source-editor is the authored source workspace, not a draft mirror." in runtime
    assert "function parseAuthoredSourceWorkspace()" in runtime
    assert "openFileFromAuthoredSource(path)" in runtime
    assert "function bindLegacySourceFileClicks()" in runtime
    assert 'ev.stopImmediatePropagation' in runtime
    assert 'ev.target.closest("[data-code-studio-file]")' in runtime
    assert 'source: "authored-source-workspace"' in runtime
    assert "dom.legacySourceMirror" not in runtime
    assert 'dom.sourceWorkspaceEditor.value = active.open ? active.text : ""' not in runtime
    assert "authoredSourceWorkspaceFiles" in runtime
    assert "runtimePaneActive" in runtime
    assert "activePane" in runtime
