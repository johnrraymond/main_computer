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
        pytest.skip("node is unavailable; SCM layout/style functional smoke test cannot run")

    script_path = tmp_path / "mcel-scm-layout-style-smoke.js"
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


def _valid_layout_style_component_manifest() -> str:
    return """
{
  version: "1.0.0",
  contract: "layout-style.studio.v1",
  owns: {
    state: ["bottomDockExpanded"],
    runtime: ["layoutReport"],
    layout: ["body", "bottomDock"],
    style: ["studioTheme"]
  },
  state: {
    bottomDockExpanded: false
  },
  runtime: {
    layoutReport: null
  },
  transitions: {},
  layoutContract: {
    root: "#studio",
    failClosed: true,
    maxDocumentHeightRatio: 1.6,
    requiredComputed: {
      ".studio-shell": {display: "grid", overflow: "hidden"},
      ".studio-body": {display: "grid"}
    },
    regions: {
      body: {selector: ".studio-body", slot: "body", required: true},
      bottomDock: {selector: "#bottom-dock", slot: "bottomDock", required: true}
    },
    states: {
      bottomDockCollapsed: {
        when: "state.bottomDockExpanded === false",
        selector: "#bottom-dock",
        maxHeight: 80
      }
    }
  },
  styleContract: {
    scope: "sealed",
    owns: ["studioTheme"],
    forbidsGlobalLeakage: true,
    expectedComputed: {
      "#studio": {backgroundColor: "rgb(30, 30, 30)"},
      ".studio-body": {display: "grid"}
    },
    forbiddenComputed: {
      "button": {backgroundColor: "rgb(246, 199, 91)"}
    }
  }
}
"""


def test_scm_layout_and_style_contract_checks_pass_for_declared_observation(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("LayoutStyleStudio", {_valid_layout_style_component_manifest()});
const instance = McelLabScm.createComponentInstance("LayoutStyleStudio");

const layout = McelLabScm.checkLayoutContract(instance, {{
  computed: {{
    ".studio-shell": {{display: "grid", overflow: "hidden"}},
    ".studio-body": {{display: "grid"}}
  }},
  regions: {{
    body: true,
    bottomDock: true
  }},
  rects: {{
    "#bottom-dock": {{height: 64}}
  }},
  documentHeightRatio: 1.1
}});

const style = McelLabScm.checkStyleContract(instance, {{
  computed: {{
    "#studio": {{backgroundColor: "rgb(30, 30, 30)"}},
    ".studio-body": {{display: "grid"}},
    "button": {{backgroundColor: "rgb(45, 45, 48)"}}
  }}
}});

process.stdout.write(JSON.stringify({{
  layoutOk: layout.ok,
  styleOk: style.ok,
  evidencePhases: McelLabScm.exportEvidence(instance).evidence.map((entry) => entry.phase)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["layoutOk"] is True
    assert data["styleOk"] is True
    assert "layout-check" in data["evidencePhases"]
    assert "style-check" in data["evidencePhases"]


def test_scm_layout_contract_records_computed_region_and_ratio_violations(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("LayoutStyleStudio", {_valid_layout_style_component_manifest()});
const instance = McelLabScm.createComponentInstance("LayoutStyleStudio");

const result = McelLabScm.checkLayoutContract(instance, {{
  computed: {{
    ".studio-shell": {{display: "block", overflow: "visible"}},
    ".studio-body": {{display: "block"}}
  }},
  regions: {{
    body: true
  }},
  rects: {{
    "#bottom-dock": {{height: 140}}
  }},
  documentHeightRatio: 2.4
}});

process.stdout.write(JSON.stringify({{
  ok: result.ok,
  codes: result.violations.map((entry) => entry.code),
  evidenceCodes: McelLabScm.exportEvidence(instance).evidence.map((entry) => entry.code).filter(Boolean)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is False
    assert "SCM_LAYOUT_COMPUTED_MISMATCH" in data["codes"]
    assert "SCM_LAYOUT_REGION_MISSING" in data["codes"]
    assert "SCM_LAYOUT_DOCUMENT_HEIGHT_RATIO_EXCEEDED" in data["codes"]
    assert "SCM_LAYOUT_STATE_MAX_HEIGHT_EXCEEDED" in data["codes"]
    assert "SCM_LAYOUT_COMPUTED_MISMATCH" in data["evidenceCodes"]


def test_scm_style_contract_records_expected_and_forbidden_computed_violations(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

McelLabScm.defineComponent("LayoutStyleStudio", {_valid_layout_style_component_manifest()});
const instance = McelLabScm.createComponentInstance("LayoutStyleStudio");

const result = McelLabScm.checkStyleContract(instance, {{
  computed: {{
    "#studio": {{backgroundColor: "rgb(255, 255, 255)"}},
    ".studio-body": {{display: "block"}},
    "button": {{backgroundColor: "rgb(246, 199, 91)"}}
  }},
  globalLeakage: [
    {{selector: "button", property: "backgroundColor", value: "rgb(246, 199, 91)", source: "global"}}
  ]
}});

process.stdout.write(JSON.stringify({{
  ok: result.ok,
  codes: result.violations.map((entry) => entry.code),
  evidenceCodes: McelLabScm.exportEvidence(instance).evidence.map((entry) => entry.code).filter(Boolean)
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["ok"] is False
    assert "SCM_STYLE_COMPUTED_MISMATCH" in data["codes"]
    assert "SCM_STYLE_FORBIDDEN_COMPUTED_MATCH" in data["codes"]
    assert "SCM_STYLE_GLOBAL_LEAKAGE_DETECTED" in data["codes"]
    assert "SCM_STYLE_FORBIDDEN_COMPUTED_MATCH" in data["evidenceCodes"]


def test_scm_rejects_layout_region_outside_declared_layout_ownership(tmp_path: Path) -> None:
    script = f"""
{_scm_bootstrap()}

let code = "";
try {{
  McelLabScm.defineComponent("BadLayoutStudio", {{
    version: "1.0.0",
    contract: "bad.layout.v1",
    owns: {{
      layout: ["body"],
      style: ["studioTheme"]
    }},
    transitions: {{}},
    layoutContract: {{
      root: "#studio",
      regions: {{
        inspector: {{
          selector: ".inspector",
          slot: "inspector",
          required: true
        }}
      }}
    }},
    styleContract: {{
      scope: "sealed",
      owns: ["studioTheme"],
      expectedComputed: {{}},
      forbiddenComputed: {{}}
    }}
  }});
}} catch (error) {{
  code = error.violation && error.violation.code;
}}

process.stdout.write(JSON.stringify({{code}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["code"] == "SCM_LAYOUT_REGION_SLOT_UNOWNED"


def test_mcel_core_facade_exposes_layout_and_style_checks(tmp_path: Path) -> None:
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

process.stdout.write(JSON.stringify({{
  checkLayoutContract: typeof MCEL.checkLayoutContract,
  checkStyleContract: typeof MCEL.checkStyleContract
}}));
"""

    data = _run_node_json(tmp_path, script)

    assert data["checkLayoutContract"] == "function"
    assert data["checkStyleContract"] == "function"
