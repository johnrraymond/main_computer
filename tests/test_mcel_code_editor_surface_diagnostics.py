from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS_HTML = ROOT / "main_computer" / "web" / "applications.html"
RIDGES = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-semantic-surface-ridges.js"
IR = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-semantic-surface-ir.js"
LAYOUT = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-shared-layout-grammar.js"
EXTRACTORS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-surface-extractors.js"
ROUNDTRIP = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-surface-roundtrip.js"
SELF_DIAGNOSIS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-self-diagnosis.js"
CODE_EDITOR_SURFACE_DIAGNOSTICS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-code-editor-surface-diagnostics.js"
DOC = ROOT / "pretty_docs" / "mcel-code-editor-surface-diagnostics.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; code editor surface diagnostics smoke test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_api_script(body: str, *, include_self_diagnosis: bool = False) -> str:
    self_diagnosis_load = (
        f'vm.runInNewContext(fs.readFileSync({json.dumps(str(SELF_DIAGNOSIS))}, "utf8"), sandbox, {{filename: "mcel-self-diagnosis.js"}});'
        if include_self_diagnosis
        else ""
    )
    return textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(RIDGES))}, "utf8"), sandbox, {{filename: "mcel-semantic-surface-ridges.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(IR))}, "utf8"), sandbox, {{filename: "mcel-semantic-surface-ir.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(LAYOUT))}, "utf8"), sandbox, {{filename: "mcel-shared-layout-grammar.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(EXTRACTORS))}, "utf8"), sandbox, {{filename: "mcel-surface-extractors.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(ROUNDTRIP))}, "utf8"), sandbox, {{filename: "mcel-surface-roundtrip.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(CODE_EDITOR_SURFACE_DIAGNOSTICS))}, "utf8"), sandbox, {{filename: "mcel-code-editor-surface-diagnostics.js"}});
        {self_diagnosis_load}
        const surfaceApi = sandbox.McelCodeEditorSurfaceDiagnostics;
        const selfDiagnosisApi = sandbox.McelSelfDiagnosis;
        {body}
        """
    )


HEALTHY_REPORT_JS = """
const healthyReport = {
  appId: "code-editor",
  contractId: "code-editor.contract.authoring.monaco-golden-path",
  verdict: "pass",
  summary: {
    primarySurface: {
      expected: "code-editor.surface.monaco-selected-file-editor",
      usable: true,
      exactlyOneAuthoritativeSurface: true,
      host: {exists: true, visible: true, selector: "#code-studio-runtime-monaco", x: 300, y: 110, width: 702, height: 438},
      editor: {exists: true, visible: true, selector: "div.monaco-editor.no-user-select.showUnused.showDeprecated.vs-dark", x: 300, y: 110, width: 702, height: 438}
    }
  },
  measurements: {
    viewport: {width: 1180, height: 820},
    requiredRegions: {
      "code-editor.region.root": {exists: true, visible: true, selector: "#code-editor-app", x: 0, y: 0, width: 1180, height: 820},
      "#code-editor-app": {exists: true, visible: true, selector: "#code-editor-app", x: 0, y: 0, width: 1180, height: 820},
      "code-editor.region.editor-group": {exists: true, visible: true, selector: ".code-studio-editor-group", x: 300, y: 110, width: 702, height: 438},
      ".code-studio-editor-group": {exists: true, visible: true, selector: ".code-studio-editor-group", x: 300, y: 110, width: 702, height: 438}
    },
    optionalRegions: {
      "code-editor.region.inspector": {exists: true, visible: true, selector: ".code-studio-inspector", x: 1018, y: 110, width: 130, height: 438}
    },
    surfaces: {
      monacoHost: {exists: true, visible: true, selector: "#code-studio-runtime-monaco", x: 300, y: 110, width: 702, height: 438},
      monacoEditor: {exists: true, visible: true, selector: ".monaco-editor", x: 300, y: 110, width: 702, height: 438}
    }
  }
};
"""


def test_code_editor_surface_diagnostics_source_and_docs_are_domain_neutral() -> None:
    source = CODE_EDITOR_SURFACE_DIAGNOSTICS.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")

    assert "mcel.code-editor-surface-diagnostics.v1" in source
    assert "evaluateCodeEditorSurfacePathway" in source
    assert "summarizeForDiagnosis" in source
    assert "SemanticSurfaceIR" in doc
    assert "SharedLayoutGrammar" in doc
    assert "Health App" not in source
    assert "BIO_HEALTH" not in source
    assert "SYS_HEALTH" not in source


def test_applications_loads_surface_diagnostics_before_self_diagnosis() -> None:
    html = APPLICATIONS_HTML.read_text(encoding="utf-8")
    required_order = [
        "mcel-semantic-surface-ridges.js",
        "mcel-semantic-surface-ir.js",
        "mcel-shared-layout-grammar.js",
        "mcel-surface-extractors.js",
        "mcel-surface-roundtrip.js",
        "mcel-code-editor-surface-diagnostics.js",
        "mcel-self-diagnosis.js",
    ]
    positions = [html.index(name) for name in required_order]
    assert positions == sorted(positions)


def test_healthy_code_editor_report_builds_extractable_surface_pathway() -> None:
    payload = run_node_json(load_api_script(
        HEALTHY_REPORT_JS
        + """
        const result = surfaceApi.evaluateCodeEditorSurfacePathway(healthyReport);
        process.stdout.write(JSON.stringify({
          status: result.status,
          valid: result.valid,
          semanticRidgesPresent: result.semanticRidgesPresent,
          surfaceIrBuildable: result.surfaceIrBuildable,
          surfaceIrValid: result.surfaceIrValid,
          layoutGrammarPresent: result.layoutGrammarPresent,
          layoutGrammarValid: result.layoutGrammarValid,
          extractable: result.extractable,
          roundTripStatus: result.roundTripStatus,
          codes: result.diagnostics.map((item) => item.code),
          nodeIds: result.surfaceIR.graph.nodes.map((node) => node.id).sort(),
          regionIds: result.surfaceIR.graph.regions.map((region) => region.id).sort(),
          hasSemanticFingerprint: Boolean(result.fingerprints.semantic),
          hasLayoutFingerprint: Boolean(result.fingerprints.layout)
        }));
        """
    ))

    assert payload["status"] == "pass"
    assert payload["valid"] is True
    assert payload["semanticRidgesPresent"] is True
    assert payload["surfaceIrBuildable"] is True
    assert payload["surfaceIrValid"] is True
    assert payload["layoutGrammarPresent"] is True
    assert payload["layoutGrammarValid"] is True
    assert payload["extractable"] is True
    assert payload["roundTripStatus"] == "pass"
    assert payload["codes"] == []
    assert "code-editor.node.monaco-selected-file-editor" in payload["nodeIds"]
    assert "code-editor.node.supporting-context-feedback" in payload["nodeIds"]
    assert "code-editor.region.editor-group" in payload["regionIds"]
    assert payload["hasSemanticFingerprint"] is True
    assert payload["hasLayoutFingerprint"] is True


def test_code_editor_surface_pathway_fails_specific_predicate_for_invisible_editor() -> None:
    payload = run_node_json(load_api_script(
        HEALTHY_REPORT_JS
        + """
        const broken = JSON.parse(JSON.stringify(healthyReport));
        broken.summary.primarySurface.editor.visible = false;
        broken.summary.primarySurface.editor.height = 0;
        broken.measurements.surfaces.monacoEditor.visible = false;
        broken.measurements.surfaces.monacoEditor.height = 0;
        const result = surfaceApi.evaluateCodeEditorSurfacePathway(broken);
        process.stdout.write(JSON.stringify({
          valid: result.valid,
          status: result.status,
          codes: result.diagnostics.map((item) => item.code)
        }));
        """
    ))

    assert payload["valid"] is False
    assert payload["status"] == "fail"
    assert "code-editor-primary-editor-not-layout-usable" in payload["codes"]


def test_self_diagnosis_report_can_embed_mcel_surface_pathway_summary() -> None:
    payload = run_node_json(load_api_script(
        HEALTHY_REPORT_JS
        + """
        const report = {
          appId: "code-editor",
          contractId: healthyReport.contractId,
          summary: JSON.parse(JSON.stringify(healthyReport.summary)),
          measurements: JSON.parse(JSON.stringify(healthyReport.measurements)),
          findings: []
        };
        const enriched = selfDiagnosisApi._private.attachMcelSurfacePathway(report, healthyReport);
        process.stdout.write(JSON.stringify({
          hasTopLevel: Boolean(enriched.mcelSurfacePathway),
          hasSummary: Boolean(enriched.summary.mcelSurfacePathway),
          status: enriched.mcelSurfacePathway.status,
          roundTripStatus: enriched.mcelSurfacePathway.roundTripStatus,
          valid: enriched.mcelSurfacePathway.valid,
          codes: enriched.mcelSurfacePathway.diagnosticCodes
        }));
        """,
        include_self_diagnosis=True,
    ))

    assert payload["hasTopLevel"] is True
    assert payload["hasSummary"] is True
    assert payload["status"] == "pass"
    assert payload["roundTripStatus"] == "pass"
    assert payload["valid"] is True
    assert payload["codes"] == []


def test_self_diagnosis_without_surface_diagnostics_degrades_without_findings() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; code editor surface diagnostics smoke test cannot run")

    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(fs.readFileSync({json.dumps(str(SELF_DIAGNOSIS))}, "utf8"), sandbox, {{filename: "mcel-self-diagnosis.js"}});
        const api = sandbox.McelSelfDiagnosis;
        const report = {{appId: "code-editor", summary: {{}}, measurements: {{}}, findings: []}};
        const enriched = api._private.attachMcelSurfacePathway(report, {{}});
        process.stdout.write(JSON.stringify({{
          status: enriched.mcelSurfacePathway.status,
          code: enriched.mcelSurfacePathway.diagnosticCodes[0],
          findingCount: enriched.findings.length
        }}));
        """
    )
    payload = run_node_json(script)

    assert payload["status"] == "unavailable"
    assert payload["code"] == "code-editor-surface-diagnostics-api-unavailable"
    assert payload["findingCount"] == 0
