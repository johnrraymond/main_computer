from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"


def _script(relative_path: str) -> str:
    return (SCRIPTS / relative_path).read_text(encoding="utf-8")


def _run_node_json(tmp_path: Path, script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; CodeStudio SCM manifest smoke test cannot run")

    script_path = tmp_path / "code-studio-scm-manifest-smoke.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        [node, str(script_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _core_dependency_stubs() -> str:
    return """
const window = {};
var McelLabContract = {
  contractVersion: "mcel.facade.test",
  defaultSource: "<main data-mc='component'></main>",
  attributes: {sourceIndex: "data-mc-source-index"}
};
var McelLabEngine = {};
var McelLabEditor = {};
var McelLabStyleLaw = {};
var McelLabLayoutLaw = {};
var McelLabChromeLaw = {};
var McelLabBrowserObserver = {};
var McelLabPlatformSpine = {};
var McelLabWorkbench = {};
var McelLabBrowserRunner = {};
var McelLabCommandSurface = {};
var McelLabGraph = {};
var McelLabOpsRunner = {};
var McelLabAcidTests = {};
var McelLabSupervisor = {};
var McelLabLawRegistry = {};
"""


def _mcel_with_code_studio_manifest() -> str:
    return f"""
{_core_dependency_stubs()}
{_script("mcel-scm.js")}
{_script("mcel-core.js")}
MCEL.scm.clearDefinitions();
{_script("code-editor-scm-manifest.js")}
"""


def test_code_studio_scm_manifest_is_loaded_after_mcel_core() -> None:
    markup = APPLICATIONS_HTML.read_text(encoding="utf-8")

    assert '<!-- @include applications/scripts/code-editor-scm-manifest.js -->' in markup
    include_order = [
        "applications/scripts/mcel-scm.js",
        "applications/scripts/mcel-core.js",
        "applications/scripts/code-editor-scm-manifest.js",
    ]
    positions = [markup.index(include) for include in include_order]

    assert positions == sorted(positions)


def test_code_studio_scm_manifest_registers_through_core_facade(tmp_path: Path) -> None:
    script = f"""
{_mcel_with_code_studio_manifest()}

const definition = MCEL.componentDefinition("CodeStudio");
const validation = MCEL.validateComponentManifest("CodeStudio", McelCodeStudioScm.manifest);

process.stdout.write(JSON.stringify({{
  registered: Boolean(definition),
  componentName: McelCodeStudioScm.componentName,
  definitionName: definition && definition.name,
  version: definition && definition.version,
  contract: definition && definition.contract,
  owns: definition && definition.owns,
  transitions: definition ? Object.keys(definition.transitions) : [],
  effects: definition ? Object.keys(definition.effects) : [],
  children: definition ? Object.keys(definition.children) : [],
  childComponents: definition && {{
    activitybar: definition.children.activitybar.component,
    explorer: definition.children.explorer.component,
    editor: definition.children.editor.component,
    inspector: definition.children.inspector.component,
    bottomDock: definition.children.bottomDock.component
  }},
  childOutputs: definition && {{
    explorerOpenFile: definition.children.explorer.outputs.openFile,
    editorCommitDraft: definition.children.editor.outputs.commitDraft,
    dockToggle: definition.children.bottomDock.outputs.toggleDock
  }},
  validation
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["registered"] is True
    assert data["componentName"] == "CodeStudio"
    assert data["definitionName"] == "CodeStudio"
    assert data["version"] == "2.4.0"
    assert data["contract"] == "mcel.scm.code-studio.v1"
    assert data["validation"]["ok"] is True
    assert data["owns"]["source"] == ["workspace.manifest", "workspace.files"]
    assert "activitybar" in data["owns"]["layout"]
    assert data["owns"]["style"] == ["codeStudioTheme"]
    assert data["owns"]["effects"] == [
        "loadWorkspace",
        "loadFile",
        "saveFile",
        "runValidation",
    ]
    assert data["transitions"] == [
        "openFile",
        "selectPanel",
        "editDraft",
        "commitDraft",
        "toggleBottomDock",
        "serializeWorkspace",
    ]
    assert data["effects"] == [
        "loadWorkspace",
        "loadFile",
        "saveFile",
        "runValidation",
    ]
    assert data["children"] == [
        "activitybar",
        "explorer",
        "editor",
        "inspector",
        "bottomDock",
    ]
    assert data["childComponents"] == {
        "activitybar": "ActivityBar",
        "explorer": "FileExplorer",
        "editor": "SourceEditor",
        "inspector": "ContractInspector",
        "bottomDock": "AssistantDock",
    }
    assert data["childOutputs"] == {
        "explorerOpenFile": "transition.openFile",
        "editorCommitDraft": "transition.commitDraft",
        "dockToggle": "transition.toggleBottomDock",
    }


def test_code_studio_scm_transitions_update_only_declared_paths(tmp_path: Path) -> None:
    script = f"""
{_mcel_with_code_studio_manifest()}

const instance = McelCodeStudioScm.createDefaultInstance({{id: "code-studio-test-instance"}});
MCEL.transition(instance, "openFile", {{fileId: "test-app"}});
MCEL.transition(instance, "editDraft", {{text: "test('studio works', () => expect(true).toBe(true));"}});
MCEL.transition(instance, "commitDraft");
MCEL.transition(instance, "serializeWorkspace");

const committedFile = instance.source.workspace.files.find((file) => file.id === "test-app");
const packet = MCEL.exportScmEvidence(instance);

process.stdout.write(JSON.stringify({{
  activeFileId: instance.state.activeFileId,
  openTabs: instance.state.openTabs,
  dirty: instance.state.dirty,
  drafts: instance.state.drafts,
  committedText: committedFile && committedFile.text,
  serializedOutput: instance.runtime.serializedOutput,
  loadedFilePath: instance.runtime.loadedFile && instance.runtime.loadedFile.path,
  evidencePhases: packet.evidence.map((entry) => entry.phase),
  transitionEvidence: packet.evidence.filter((entry) => entry.phase === "transition").map((entry) => entry.transitionName)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["activeFileId"] == "test-app"
    assert data["openTabs"] == ["src-app", "test-app"]
    assert data["dirty"] is False
    assert data["drafts"] == {}
    assert data["committedText"] == "test('studio works', () => expect(true).toBe(true));"
    assert '"files"' in data["serializedOutput"]
    assert data["loadedFilePath"] == "tests/app.test.js"
    assert data["evidencePhases"][0] == "create-instance"
    assert data["transitionEvidence"] == [
        "openFile",
        "editDraft",
        "commitDraft",
        "serializeWorkspace",
    ]


def test_code_studio_scm_child_context_reads_emits_and_blocks_undeclared_mutation(tmp_path: Path) -> None:
    script = f"""
{_mcel_with_code_studio_manifest()}

const instance = McelCodeStudioScm.createDefaultInstance({{id: "code-studio-child-instance"}});
const explorer = MCEL.createChildContext(instance, "explorer");
const editor = MCEL.createChildContext(instance, "editor");

const explorerFiles = explorer.get("files").map((file) => file.id);
explorer.emit("openFile", {{fileId: "test-app"}});
editor.set("state.drafts.test-app", "draft from child context");

let blocked = null;
try {{
  explorer.set("state.activeFileId", "src-app");
}} catch (error) {{
  blocked = error.violation;
}}

const packet = MCEL.exportScmEvidence(instance);

process.stdout.write(JSON.stringify({{
  explorerFiles,
  activeFileId: instance.state.activeFileId,
  draft: instance.state.drafts["test-app"],
  blocked,
  evidencePhases: packet.evidence.map((entry) => entry.phase),
  childOutputTargets: packet.evidence
    .filter((entry) => entry.phase === "child-output")
    .map((entry) => entry.childName + ":" + entry.outputName + ":" + entry.transitionName),
  childMutations: packet.evidence
    .filter((entry) => entry.phase === "child-mutation")
    .map((entry) => entry.childName + ":" + entry.path + ":" + String(Boolean(entry.ok)))
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["explorerFiles"] == ["src-app", "test-app", "mcel-contract"]
    assert data["activeFileId"] == "test-app"
    assert data["draft"] == "draft from child context"
    assert data["blocked"]["kind"] == "mcel-scm-violation"
    assert data["blocked"]["code"] == "SCM_CHILD_UNDECLARED_MUTATION"
    assert data["blocked"]["childName"] == "explorer"
    assert data["blocked"]["path"] == "state.activeFileId"
    assert "child-output" in data["evidencePhases"]
    assert "child-mutation" in data["evidencePhases"]
    assert "explorer:openFile:openFile" in data["childOutputTargets"]
    assert "editor:state.drafts.test-app:true" in data["childMutations"]


def test_code_studio_scm_child_outputs_require_declared_transitions(tmp_path: Path) -> None:
    script = f"""
{_mcel_with_code_studio_manifest()}

const manifest = {{
  ...McelCodeStudioScm.manifest,
  transitions: {{
    ...McelCodeStudioScm.manifest.transitions
  }}
}};
delete manifest.transitions.openFile;

const validation = MCEL.validateComponentManifest("CodeStudioBrokenChildMap", manifest);

process.stdout.write(JSON.stringify({{
  ok: validation.ok,
  issueCodes: validation.issues.map((issue) => issue.code),
  childIssues: validation.issues
    .filter((issue) => issue.code === "SCM_CHILD_OUTPUT_TARGET_MISSING")
    .map((issue) => issue.childName + ":" + issue.outputName + ":" + issue.target)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is False
    assert "SCM_CHILD_OUTPUT_TARGET_MISSING" in data["issueCodes"]
    assert "explorer:openFile:transition.openFile" in data["childIssues"]


def test_code_studio_structured_route_manifest_registers_through_core_facade(tmp_path: Path) -> None:
    script = f"""
{_mcel_with_code_studio_manifest()}

const routeDefinition = MCEL.routeDefinition("workspace.file");
const validation = MCEL.validateRouteManifest("workspace.file", McelCodeStudioScm.routeManifest);

process.stdout.write(JSON.stringify({{
  registered: Boolean(routeDefinition),
  routeName: McelCodeStudioScm.routeName,
  routeVersion: routeDefinition && routeDefinition.version,
  routeContract: routeDefinition && routeDefinition.contract,
  displayPath: routeDefinition && routeDefinition.displayPath,
  segments: routeDefinition && routeDefinition.segments,
  mountComponent: routeDefinition && routeDefinition.mounts.component,
  mountInputs: routeDefinition && routeDefinition.mounts.inputs,
  dataLoaders: routeDefinition ? Object.keys(routeDefinition.data) : [],
  onLeaveBlockedBy: routeDefinition && routeDefinition.lifecycle.onLeave.blockedBy,
  validation,
  hasPathString: Object.prototype.hasOwnProperty.call(McelCodeStudioScm.routeManifest, "path")
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["registered"] is True
    assert data["routeName"] == "workspace.file"
    assert data["routeVersion"] == "1.1.0"
    assert data["routeContract"] == "mcel.scm.route.workspace-file.v1"
    assert data["displayPath"] == "workspace/{workspaceId}/file/{fileId}"
    assert data["segments"] == [
        {"literal": "workspace"},
        {"param": "workspaceId", "type": "id", "required": True},
        {"literal": "file"},
        {"param": "fileId", "type": "id", "required": True},
    ]
    assert data["mountComponent"] == "CodeStudio"
    assert data["mountInputs"] == {
        "workspaceId": "route.params.workspaceId",
        "activeFileId": "route.params.fileId",
        "selectedPanel": "route.query.panel",
    }
    assert data["dataLoaders"] == ["loadWorkspace", "loadFile"]
    assert data["onLeaveBlockedBy"] == ["component.state.dirty"]
    assert data["validation"]["ok"] is True
    assert data["hasPathString"] is False


def test_code_studio_structured_route_enter_and_dirty_leave_block(tmp_path: Path) -> None:
    script = f"""
{_mcel_with_code_studio_manifest()}

const componentInstance = McelCodeStudioScm.createDefaultInstance({{
  id: "code-studio-route-component",
  state: {{
    ...McelCodeStudioScm.defaultState(),
    dirty: true
  }}
}});
const routeInstance = McelCodeStudioScm.createDefaultRouteInstance({{
  id: "workspace-file-route-test",
  componentInstance
}});

const enterResult = MCEL.enterRoute(routeInstance, {{
  params: {{
    workspaceId: "workspace-main",
    fileId: "test-app"
  }},
  query: {{
    panel: "debug",
    line: "7"
  }}
}});

const blocked = MCEL.leaveRoute(routeInstance);
const allowed = MCEL.leaveRoute(routeInstance, {{resolution: "discardDraft"}});
const packet = MCEL.exportRouteEvidence(routeInstance);

process.stdout.write(JSON.stringify({{
  enterResult,
  blocked,
  allowed,
  evidencePhases: packet.evidence.map((entry) => entry.phase),
  blockedEvidence: packet.evidence.filter((entry) => entry.code === "SCM_ROUTE_LEAVE_BLOCKED")
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["enterResult"]["ok"] is True
    assert data["enterResult"]["params"] == {
        "workspaceId": "workspace-main",
        "fileId": "test-app",
    }
    assert data["enterResult"]["query"] == {
        "panel": "debug",
        "line": 7,
    }
    assert data["enterResult"]["mountInputs"] == {
        "workspaceId": "workspace-main",
        "activeFileId": "test-app",
        "selectedPanel": "debug",
    }
    assert data["blocked"]["ok"] is False
    assert data["blocked"]["blocked"] is True
    assert data["blocked"]["blockers"] == ["component.state.dirty"]
    assert data["allowed"]["ok"] is True
    assert "route-enter" in data["evidencePhases"]
    assert "route-leave" in data["evidencePhases"]
    assert data["blockedEvidence"][0]["severity"] == "user-action-required"



def test_code_studio_scm_effects_and_route_loaders_run_through_declared_contexts(tmp_path: Path) -> None:
    script = f"""
{_mcel_with_code_studio_manifest()}

const instance = McelCodeStudioScm.createDefaultInstance({{mcel: MCEL}});
const loadFile = MCEL.runEffect(instance, "loadFile", {{fileId: "test-app"}});
const validate = MCEL.runEffect(instance, "runValidation");

const route = McelCodeStudioScm.createDefaultRouteInstance({{
  mcel: MCEL,
  componentInstance: instance
}});
MCEL.enterRoute(route, {{
  params: {{
    workspaceId: "workspace-main",
    fileId: "src-app"
  }},
  query: {{}}
}});
const routeLoad = MCEL.runRouteLoader(route, "loadFile");
const effectPacket = MCEL.exportScmEvidence(instance);
const routePacket = MCEL.exportRouteEvidence(route);

process.stdout.write(JSON.stringify({{
  loadFileOk: loadFile.ok,
  loadedFileId: instance.runtime.loadedFile && instance.runtime.loadedFile.id,
  validationOk: validate.ok,
  validationKind: instance.runtime.validationReport && instance.runtime.validationReport.kind,
  routeLoadOk: routeLoad.ok,
  routeActiveFile: route.data.activeFile,
  effectPhases: effectPacket.evidence.map((entry) => entry.phase),
  routePhases: routePacket.evidence.map((entry) => entry.phase)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["loadFileOk"] is True
    assert data["loadedFileId"] == "test-app"
    assert data["validationOk"] is True
    assert data["validationKind"] == "workspace-validation-report"
    assert data["routeLoadOk"] is True
    assert data["routeActiveFile"] == {
        "workspaceId": "workspace-main",
        "fileId": "src-app",
        "loaded": True,
    }
    assert "effect-start" in data["effectPhases"]
    assert "effect-commit" in data["effectPhases"]
    assert "route-loader-start" in data["routePhases"]
    assert "route-loader-commit" in data["routePhases"]

def test_code_studio_scm_manifest_does_not_rewrite_existing_studio_behavior() -> None:
    script = _script("code-editor-scm-manifest.js")

    assert "document.querySelector" not in script
    assert "addEventListener" not in script
    assert "innerHTML" not in script
    assert re.search(r"\bregister\(\);", script), "manifest should register itself without editing the UI"


def test_code_studio_scm_manifest_declares_layout_and_style_contracts(tmp_path: Path) -> None:
    script = f"""
{_mcel_with_code_studio_manifest()}

const definition = MCEL.componentDefinition("CodeStudio");
process.stdout.write(JSON.stringify({{
  version: definition && definition.version,
  layoutRoot: definition && definition.layoutContract && definition.layoutContract.root,
  layoutSlots: Object.keys(definition.layoutContract.regions || {{}}),
  styleScope: definition && definition.styleContract && definition.styleContract.scope,
  styleOwns: definition && definition.styleContract && definition.styleContract.owns,
  hasForbiddenButton: Boolean(definition.styleContract.forbiddenComputed.button)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["version"] == "2.4.0"
    assert data["layoutRoot"] == "#code-editor-app"
    assert data["layoutSlots"] == [
        "activitybar",
        "sidebar",
        "editorGroup",
        "inspector",
        "bottomDock",
        "statusbar",
    ]
    assert data["styleScope"] == "sealed"
    assert data["styleOwns"] == ["codeStudioTheme"]
    assert data["hasForbiddenButton"] is True


def test_code_studio_scm_layout_and_style_checks_produce_evidence(tmp_path: Path) -> None:
    script = f"""
{_mcel_with_code_studio_manifest()}

const instance = McelCodeStudioScm.createDefaultInstance({{mcel: MCEL}});
const layoutPass = MCEL.checkLayoutContract(instance, {{
  computed: {{
    ".code-studio-shell": {{display: "grid", overflow: "hidden"}},
    ".code-studio-body": {{display: "grid"}}
  }},
  regions: {{
    activitybar: true,
    sidebar: true,
    editorGroup: true,
    inspector: true,
    bottomDock: true,
    statusbar: true
  }},
  rects: {{
    "#code-studio-bottom-panel": {{height: 72}}
  }},
  documentHeightRatio: 1.1
}});
const styleFail = MCEL.checkStyleContract(instance, {{
  computed: {{
    "#code-editor-app": {{backgroundColor: "rgb(30, 30, 30)"}},
    ".code-studio-body": {{display: "grid"}},
    ".code-studio-titlebar button": {{
      backgroundColor: "rgb(246, 199, 91)",
      color: "rgb(220, 220, 220)"
    }},
    "button": {{backgroundColor: "rgb(246, 199, 91)"}}
  }}
}});
const packet = MCEL.exportScmEvidence(instance);

process.stdout.write(JSON.stringify({{
  layoutOk: layoutPass.ok,
  styleOk: styleFail.ok,
  styleCodes: styleFail.violations.map((entry) => entry.code),
  phases: packet.evidence.map((entry) => entry.phase),
  evidenceCodes: packet.evidence.map((entry) => entry.code).filter(Boolean)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["layoutOk"] is True
    assert data["styleOk"] is False
    assert "SCM_STYLE_COMPUTED_MISMATCH" in data["styleCodes"]
    assert "SCM_STYLE_FORBIDDEN_COMPUTED_MATCH" in data["styleCodes"]
    assert "layout-check" in data["phases"]
    assert "style-check" in data["phases"]
    assert "SCM_STYLE_FORBIDDEN_COMPUTED_MATCH" in data["evidenceCodes"]


def test_code_studio_scm_layout_check_catches_known_grid_failure_shape(tmp_path: Path) -> None:
    script = f"""
{_mcel_with_code_studio_manifest()}

const instance = McelCodeStudioScm.createDefaultInstance({{mcel: MCEL}});
const result = MCEL.checkLayoutContract(instance, {{
  computed: {{
    ".code-studio-shell": {{display: "grid", overflow: "hidden"}},
    ".code-studio-body": {{display: "block"}}
  }},
  regions: {{
    activitybar: true,
    sidebar: true,
    editorGroup: true,
    inspector: true,
    bottomDock: true,
    statusbar: true
  }},
  rects: {{
    "#code-studio-bottom-panel": {{height: 130}}
  }},
  documentHeightRatio: 15.64
}});

process.stdout.write(JSON.stringify({{
  ok: result.ok,
  codes: result.violations.map((entry) => entry.code),
  bodyViolation: result.violations.find((entry) => entry.selector === ".code-studio-body")
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is False
    assert "SCM_LAYOUT_COMPUTED_MISMATCH" in data["codes"]
    assert "SCM_LAYOUT_DOCUMENT_HEIGHT_RATIO_EXCEEDED" in data["codes"]
    assert "SCM_LAYOUT_STATE_MAX_HEIGHT_EXCEEDED" in data["codes"]
    assert data["bodyViolation"]["actual"] == "block"
    assert data["bodyViolation"]["expected"] == "grid"
