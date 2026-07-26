from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB / "scripts"
APP_SHELL = ROOT / "main_computer" / "web" / "applications.html"
FILE_EXPLORER_HTML = WEB / "apps" / "file-explorer.html"
CONFORMANCE_JS = SCRIPTS / "mcel-app-surface-conformance.js"
SELF_DIAGNOSIS_JS = SCRIPTS / "mcel-self-diagnosis.js"
COUNTER_JS = SCRIPTS / "mcel-diagnostics-counter-widget.js"
DOC = ROOT / "pretty_docs" / "mcel-app-surface-conformance.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; MCEL app surface conformance smoke test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_conformance_stack(body: str) -> str:
    return textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        for (const name of [
          "mcel-semantic-surface-ridges.js",
          "mcel-semantic-surface-ir.js",
          "mcel-shared-layout-grammar.js",
          "mcel-surface-extractors.js",
          "mcel-app-surface-conformance.js"
        ]) {{
          vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPTS))} + "/" + name, "utf8"), sandbox, {{filename: name}});
        }}
        const conformance = sandbox.McelAppSurfaceConformance;
        {body}
        """
    )


def healthy_file_explorer_report_literal() -> str:
    return json.dumps(
        {
            "schema": "mcel-self-diagnosis-report-v2",
            "version": "mcel-self-diagnosis-v2",
            "contractId": "file-explorer.contract.default.app-health",
            "appId": "file-explorer",
            "mode": "default",
            "route": "http://localhost:8765/applications/file-explorer",
            "timestamp": "2026-07-23T23:30:00.000Z",
            "verdict": "pass",
            "summary": {
                "critical": 0,
                "warning": 0,
                "info": 0,
                "primarySurface": {
                    "expected": "file-explorer.surface.main",
                    "usable": True,
                    "exactlyOneAuthoritativeSurface": True,
                    "host": {
                        "exists": True,
                        "visible": True,
                        "selector": ".file-explorer-main",
                        "width": 760,
                        "height": 520,
                    },
                    "editor": {
                        "exists": True,
                        "visible": True,
                        "selector": ".file-explorer-main",
                        "width": 760,
                        "height": 520,
                    },
                },
            },
            "findings": [],
            "measurements": {
                "viewport": {"width": 1280, "height": 720},
                "requiredRegions": {
                    "file-explorer.region.root": {
                        "exists": True,
                        "visible": True,
                        "selector": "#file-explorer-app",
                        "width": 1280,
                        "height": 720,
                    },
                    "file-explorer.region.main": {
                        "exists": True,
                        "visible": True,
                        "selector": ".file-explorer-main",
                        "width": 760,
                        "height": 520,
                    },
                },
                "surfaces": {
                    "primaryHost": {
                        "exists": True,
                        "visible": True,
                        "selector": ".file-explorer-main",
                        "width": 760,
                        "height": 520,
                    },
                    "primaryEditor": {
                        "exists": True,
                        "visible": True,
                        "selector": ".file-explorer-main",
                        "width": 760,
                        "height": 520,
                    },
                },
                "layoutCollisions": [],
                "contentFitViolations": [],
                "visualIntegrityViolations": [],
            },
            "contract": {
                "id": "file-explorer.contract.default.app-health",
                "appId": "file-explorer",
                "mode": "default",
                "primarySurface": {
                    "id": "file-explorer.surface.main",
                    "minWidth": 420,
                    "minHeight": 320,
                },
            },
        }
    )


def test_app_surface_conformance_is_wired_before_runtime_diagnostics() -> None:
    assert CONFORMANCE_JS.exists()
    assert DOC.exists()

    app_shell = APP_SHELL.read_text(encoding="utf-8")
    assert "mcel-app-surface-conformance.js" in app_shell
    assert app_shell.index("mcel-app-surface-conformance.js") < app_shell.index("mcel-self-diagnosis.js")
    assert app_shell.index("mcel-app-surface-conformance.js") < app_shell.index("mcel-diagnostics-counter-widget.js")


def test_app_surface_conformance_source_defines_the_five_baseline_layers() -> None:
    source = CONFORMANCE_JS.read_text(encoding="utf-8")

    assert "mcel.app-surface-conformance.v1" in source
    assert "evaluateAppSurfaceConformance" in source
    for layer_id in [
        "semantic-surface",
        "layout-grammar",
        "runtime-ownership",
        "runtime-visual-fit",
        "diagnostic-no-throw",
    ]:
        assert layer_id in source


def test_file_explorer_static_surface_and_runtime_report_pass_conformance() -> None:
    html = FILE_EXPLORER_HTML.read_text(encoding="utf-8")
    report = healthy_file_explorer_report_literal()
    script = load_conformance_stack(
        f"""
        const result = conformance.evaluateAppSurfaceConformance({{
          appId: "file-explorer",
          surfaceId: "file-explorer.surface.primary",
          surfaceHtml: {json.dumps(html)},
          report: {report}
        }});
        process.stdout.write(JSON.stringify({{
          status: result.status,
          valid: result.valid,
          surfaceId: result.surfaceId,
          layerStatus: Object.fromEntries(result.layers.map((layer) => [layer.id, layer.status])),
          failedLayerIds: result.failedLayerIds,
          unavailableLayerIds: result.unavailableLayerIds,
          diagnosticCodes: result.diagnosticCodes
        }}));
        """
    )
    data = run_node_json(script)

    assert data["status"] == "pass"
    assert data["valid"] is True
    assert data["surfaceId"] == "file-explorer.surface.primary"
    assert data["failedLayerIds"] == []
    assert data["unavailableLayerIds"] == []
    assert data["diagnosticCodes"] == []
    assert data["layerStatus"] == {
        "semantic-surface": "pass",
        "layout-grammar": "pass",
        "runtime-ownership": "pass",
        "runtime-visual-fit": "pass",
        "diagnostic-no-throw": "pass",
    }



def test_runtime_baseline_policy_does_not_fail_on_unavailable_static_layers() -> None:
    report = {
        "appId": "code-editor",
        "verdict": "pass",
        "summary": {
            "primarySurface": {
                "expected": "code-editor.surface.monaco-selected-file-editor",
                "usable": True,
                "exactlyOneAuthoritativeSurface": True,
                "host": {"exists": True, "visible": True, "selector": "#code-studio-runtime-monaco", "width": 424, "height": 602},
                "editor": {"exists": True, "visible": True, "selector": ".monaco-editor", "width": 424, "height": 602},
            }
        },
        "measurements": {
            "visualIntegrityViolations": [],
            "layoutCollisions": [],
            "contentFitViolations": [],
            "fitContract": {"available": True},
        },
        "findings": [],
    }
    script = load_conformance_stack(
        f"""
        const result = conformance.evaluateAppSurfaceConformance({{
          appId: "code-editor",
          surfaceId: "code-editor.surface.monaco-selected-file-editor",
          surfaceHtml: "",
          registryPolicy: {{
            appId: "code-editor",
            label: "Code Editor",
            state: "surface-aware",
            conformanceRequired: true,
            maturity: "host-workbench",
            surfaceId: "code-editor.surface.monaco-selected-file-editor",
            requiredLayerIds: ["runtime-ownership", "runtime-visual-fit", "diagnostic-no-throw"]
          }},
          report: {json.dumps(report)}
        }});
        process.stdout.write(JSON.stringify({{
          status: result.status,
          valid: result.valid,
          surfaceId: result.surfaceId,
          failedLayerIds: result.failedLayerIds,
          unavailableLayerIds: result.unavailableLayerIds,
          policyFailedLayerIds: result.policyFailedLayerIds,
          policyUnavailableLayerIds: result.policyUnavailableLayerIds,
          layerStatus: Object.fromEntries(result.layers.map((layer) => [layer.id, layer.status]))
        }}));
        """
    )
    data = run_node_json(script)

    assert data["status"] == "pass"
    assert data["valid"] is True
    assert data["surfaceId"] == "code-editor.surface.monaco-selected-file-editor"
    assert data["policyFailedLayerIds"] == []
    assert data["policyUnavailableLayerIds"] == []
    assert data["layerStatus"]["runtime-ownership"] == "pass"
    assert data["layerStatus"]["runtime-visual-fit"] == "pass"
    assert data["layerStatus"]["diagnostic-no-throw"] == "pass"
    assert data["layerStatus"]["semantic-surface"] == "unavailable"
    assert data["layerStatus"]["layout-grammar"] == "unavailable"

def test_conformance_marks_diagnosis_threw_as_failed_no_throw_layer() -> None:
    script = load_conformance_stack(
        """
        const result = conformance.evaluateRuntimeReport({
          appId: "file-explorer",
          verdict: "fail",
          summary: {primarySurface: null},
          findings: [{
            severity: "critical",
            code: "diagnosis-threw",
            finding: "clippedRangeBox is not defined",
            recommendedNextProbe: "console"
          }],
          measurements: {}
        });
        process.stdout.write(JSON.stringify({
          status: result.status,
          valid: result.valid,
          layerStatus: Object.fromEntries(result.layers.map((layer) => [layer.id, layer.status])),
          diagnosticCodes: result.diagnostics.map((item) => item.code).sort()
        }));
        """
    )
    data = run_node_json(script)

    assert data["status"] == "fail"
    assert data["valid"] is False
    assert data["layerStatus"]["diagnostic-no-throw"] == "fail"
    assert "app-surface-conformance-diagnosis-threw" in data["diagnosticCodes"]
    assert "app-surface-conformance-measurements-missing" in data["diagnosticCodes"]


def test_self_diagnosis_attaches_app_surface_conformance_without_replacing_findings() -> None:
    source = SELF_DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert "function getAppSurfaceConformance" in source
    assert "function getAppSurfaceRegistryPolicy" in source
    assert "function harmonizeContractWithAppSurfacePolicy" in source
    assert "function attachAppSurfaceConformance" in source
    assert "semanticSurfaceHtml" in source
    assert "semanticSurfaceId" in source
    assert "const expectedSurfaceId = (requiresStaticSurface && policySurfaceId)" in source
    assert "report = attachAppSurfaceConformance(report, snapshot, options);" in source
    assert "appSurfaceConformance" in source
    assert "buildReportBuckets(report)" in source


def test_diagnostics_counter_copy_payload_includes_app_surface_conformance() -> None:
    source = COUNTER_JS.read_text(encoding="utf-8")

    assert "function attachConformanceFallback" in source
    assert "McelAppSurfaceConformance" in source
    assert "appSurfaceConformance: report?.summary?.appSurfaceConformance" in source
    assert '"appSurfaceConformance"' in source


def test_app_surface_conformance_documentation_is_domain_neutral() -> None:
    text = DOC.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "semantic validity" in lowered
    assert "runtime visual-fit" in lowered
    assert "diagnostic no-throw reliability" in lowered
    assert "file explorer" in lowered
    for forbidden in ["health app", "bio", "patient", "clinical"]:
        assert forbidden not in lowered
