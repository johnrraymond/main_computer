from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"


def _script(relative_path: str) -> str:
    return (SCRIPTS / relative_path).read_text(encoding="utf-8")


def _run_node_json(tmp_path: Path, script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; SCM serialization/repair functional smoke test cannot run")

    script_path = tmp_path / "mcel-scm-serialization-repair-smoke.js"
    script_path.write_text(script, encoding="utf-8")

    result = subprocess.run(
        [node, str(script_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _scm_bootstrap() -> str:
    return f"""
const window = {{}};
{_script("mcel-scm.js")}
McelLabScm.clearDefinitions();
"""


def _valid_serialization_repair_manifest() -> str:
    return """
{
  version: "1.0.0",
  contract: "serialization-repair.studio.v1",
  owns: {
    source: ["workspace"],
    runtime: ["serializedOutput", "workbench.shell", "editor.chrome", "validationReport"],
    state: ["dirty", "drafts", "activeFileId"]
  },
  source: {
    workspace: {
      manifest: {id: "main", title: "Main"},
      files: [{id: "src", text: "console.log('ok');"}]
    }
  },
  runtime: {
    serializedOutput: "",
    workbench: {shell: {mounted: false, damaged: true}},
    editor: {chrome: {generated: true, serialize: "omit"}},
    validationReport: null
  },
  state: {
    dirty: false,
    drafts: {},
    activeFileId: "src"
  },
  transitions: {},
  serializationContract: {
    sourceOwns: ["source.workspace"],
    runtimeOnly: [
      "runtime.serializedOutput",
      "runtime.workbench.shell",
      "runtime.editor.chrome",
      "runtime.validationReport"
    ],
    commitRequiredFor: ["source.workspace"],
    dirtyState: {
      blockedBy: ["state.dirty", "state.drafts"]
    },
    failIfRuntimeLeaks: true,
    runtimeLeakMarkers: ["data-mc-runtime", "data-mc-generated", "editor.chrome"],
    output: {
      format: "clean-source-json",
      includeRuntime: false,
      includeEditorChrome: false,
      writeTo: "runtime.serializedOutput"
    }
  },
  repairContract: {
    allowed: ["runtime.workbench.shell", "runtime.editor.chrome", "runtime.validationReport"],
    forbidden: ["source.workspace", "state.dirty", "state.drafts"],
    strategies: {
      rebuildWorkbenchShell: {
        reads: ["source.workspace", "state.activeFileId", "runtime.workbench.shell", "runtime.editor.chrome"],
        writes: ["runtime.workbench.shell", "runtime.editor.chrome", "runtime.validationReport"],
        apply(ctx) {
          const workspace = ctx.get("source.workspace");
          ctx.set("runtime.workbench.shell", {
            mounted: true,
            damaged: false,
            title: workspace.manifest.title
          });
          ctx.set("runtime.editor.chrome", {
            generated: true,
            serialize: "omit",
            repaired: true
          });
          ctx.set("runtime.validationReport", {
            kind: "repair-report",
            ok: true
          });
          return "rebuilt";
        },
        post(ctx) {
          return ctx.get("runtime.workbench.shell.damaged") === false;
        }
      }
    }
  }
}
"""


def test_scm_serializes_clean_source_and_omits_runtime(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("SerializableStudio", {_valid_serialization_repair_manifest()});
const instance = McelLabScm.createComponentInstance("SerializableStudio");
const result = McelLabScm.serializeComponent(instance);
const packet = McelLabScm.exportEvidence(instance);

console.log(JSON.stringify({{
  ok: result.ok,
  serializedType: typeof result.serialized,
  hasSourceWorkspace: Boolean(result.source.workspace),
  serializedHasFiles: result.serialized.includes("files"),
  serializedMentionsRuntime: result.serialized.includes("workbench"),
  runtimeOutputMatches: instance.runtime.serializedOutput === result.serialized,
  evidencePhases: packet.evidence.map((entry) => entry.phase)
}}));
"""
    data = _run_node_json(tmp_path, script)

    assert data["ok"] is True
    assert data["serializedType"] == "string"
    assert data["hasSourceWorkspace"] is True
    assert data["serializedHasFiles"] is True
    assert data["serializedMentionsRuntime"] is False
    assert data["runtimeOutputMatches"] is True
    assert "serialize-start" in data["evidencePhases"]
    assert "serialize-commit" in data["evidencePhases"]


def test_scm_blocks_serialization_when_dirty_state_is_present(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("DirtyStudio", {_valid_serialization_repair_manifest()});
const instance = McelLabScm.createComponentInstance("DirtyStudio", {{
  state: {{
    dirty: true,
    drafts: {{"src": "changed"}}
  }}
}});

let violation = null;
try {{
  McelLabScm.serializeComponent(instance);
}} catch (error) {{
  violation = error.violation;
}}

console.log(JSON.stringify({{
  code: violation && violation.code,
  blockedBy: violation && violation.blockedBy,
  evidenceCodes: McelLabScm.exportEvidence(instance).evidence.map((entry) => entry.code).filter(Boolean)
}}));
"""
    data = _run_node_json(tmp_path, script)

    assert data["code"] == "SCM_SERIALIZATION_DIRTY_STATE_BLOCKED"
    assert {entry["path"] for entry in data["blockedBy"]} == {"state.dirty", "state.drafts"}
    assert "SCM_SERIALIZATION_DIRTY_STATE_BLOCKED" in data["evidenceCodes"]


def test_scm_detects_runtime_leakage_in_source(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("LeakyStudio", {_valid_serialization_repair_manifest()});
const instance = McelLabScm.createComponentInstance("LeakyStudio", {{
  source: {{
    workspace: {{
      manifest: {{id: "main", title: "Main"}},
      files: [
        {{id: "src", text: "<div data-mc-runtime='true'>runtime chrome</div>"}}
      ]
    }}
  }}
}});

let violation = null;
try {{
  McelLabScm.serializeComponent(instance);
}} catch (error) {{
  violation = error.violation;
}}

console.log(JSON.stringify({{
  code: violation && violation.code,
  leakCount: violation && violation.leaks && violation.leaks.length,
  marker: violation && violation.leaks && violation.leaks[0] && violation.leaks[0].marker
}}));
"""
    data = _run_node_json(tmp_path, script)

    assert data["code"] == "SCM_SERIALIZATION_RUNTIME_LEAK_DETECTED"
    assert data["leakCount"] == 1
    assert data["marker"] == "data-mc-runtime"


def test_scm_repairs_only_allowed_runtime_paths_and_preserves_source(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("RepairableStudio", {_valid_serialization_repair_manifest()});
const instance = McelLabScm.createComponentInstance("RepairableStudio");
const sourceBefore = JSON.stringify(instance.source);
const result = McelLabScm.repairComponent(instance, "rebuildWorkbenchShell");
const sourceAfter = JSON.stringify(instance.source);

console.log(JSON.stringify({{
  ok: result.ok,
  shellMounted: instance.runtime.workbench.shell.mounted,
  shellDamaged: instance.runtime.workbench.shell.damaged,
  chromeSerialize: instance.runtime.editor.chrome.serialize,
  sourceUnchanged: sourceBefore === sourceAfter,
  reportKind: instance.runtime.validationReport.kind,
  evidencePhases: McelLabScm.exportEvidence(instance).evidence.map((entry) => entry.phase)
}}));
"""
    data = _run_node_json(tmp_path, script)

    assert data["ok"] is True
    assert data["shellMounted"] is True
    assert data["shellDamaged"] is False
    assert data["chromeSerialize"] == "omit"
    assert data["sourceUnchanged"] is True
    assert data["reportKind"] == "repair-report"
    assert "repair-start" in data["evidencePhases"]
    assert "repair-commit" in data["evidencePhases"]


def test_scm_rejects_repair_strategy_that_writes_source(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

const manifest = {_valid_serialization_repair_manifest()};
manifest.repairContract.allowed = ["runtime.workbench.shell"];
manifest.repairContract.strategies.rebuildWorkbenchShell.writes = ["source.workspace.files"];

const validation = McelLabScm.validateComponentManifest("BadRepairStudio", manifest);
console.log(JSON.stringify({{
  ok: validation.ok,
  codes: validation.issues.map((issue) => issue.code)
}}));
"""
    data = _run_node_json(tmp_path, script)

    assert data["ok"] is False
    assert "SCM_REPAIR_REBUILDWORKBENCHSHELLWRITES_PATH_INVALID" in data["codes"]


def test_mcel_core_facade_exposes_serialization_and_repair(tmp_path: Path) -> None:
    script = f"""
const window = {{}};
var McelLabContract = {{
  contractVersion: "mcel.facade.test",
  defaultSource: "<main data-mc='component'></main>",
  attributes: {{sourceIndex: "data-mc-source-index"}}
}};
var McelLabEngine = {{}};
var McelLabEditor = {{}};
var McelLabStyleLaw = {{}};
var McelLabLayoutLaw = {{}};
var McelLabChromeLaw = {{}};
var McelLabBrowserObserver = {{}};
var McelLabPlatformSpine = {{}};
var McelLabWorkbench = {{}};
var McelLabBrowserRunner = {{}};
var McelLabCommandSurface = {{}};
var McelLabGraph = {{}};
var McelLabOpsRunner = {{}};
var McelLabAcidTests = {{}};
var McelLabSupervisor = {{}};
var McelLabLawRegistry = {{}};
{_script("mcel-scm.js")}
{_script("mcel-core.js")}
console.log(JSON.stringify({{
  serializeComponent: typeof MCEL.serializeComponent,
  createRepairContext: typeof MCEL.createRepairContext,
  repairComponent: typeof MCEL.repairComponent
}}));
"""
    data = _run_node_json(tmp_path, script)

    assert data["serializeComponent"] == "function"
    assert data["createRepairContext"] == "function"
    assert data["repairComponent"] == "function"
