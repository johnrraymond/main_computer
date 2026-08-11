from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from main_computer.mcel_application_package_browser_catalog import (
    build_repository_browser_catalog_payload,
    render_browser_catalog_javascript,
)


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB / "scripts"
APP_SHELL = ROOT / "main_computer" / "web" / "applications.html"
FILE_EXPLORER_HTML = WEB / "apps" / "file-explorer.html"
CODE_EDITOR_HTML = WEB / "apps" / "code-editor.html"
CONFORMANCE_JS = SCRIPTS / "mcel-app-surface-conformance.js"
REGISTRY_JS = SCRIPTS / "mcel-app-surface-registry.js"
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
          "mcel-app-surface-registry.js",
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


def healthy_code_editor_report_literal() -> str:
    return json.dumps(
        {
            "schema": "mcel-self-diagnosis-report-v2",
            "version": "mcel-self-diagnosis-v2",
            "contractId": "code-editor.contract.authoring.monaco-golden-path",
            "appId": "code-editor",
            "mode": "authoring",
            "route": "http://localhost:8765/applications/code-editor",
            "timestamp": "2026-08-10T13:00:00.000Z",
            "verdict": "pass",
            "summary": {
                "critical": 0,
                "warning": 0,
                "info": 0,
                "primarySurface": {
                    "expected": "code-editor.surface.monaco-selected-file-editor",
                    "usable": True,
                    "exactlyOneAuthoritativeSurface": True,
                    "host": {
                        "exists": True,
                        "visible": True,
                        "selector": "#code-studio-runtime-monaco",
                        "width": 702,
                        "height": 438,
                    },
                    "editor": {
                        "exists": True,
                        "visible": True,
                        "selector": ".monaco-editor",
                        "width": 702,
                        "height": 438,
                    },
                },
            },
            "findings": [],
            "measurements": {
                "viewport": {"width": 1180, "height": 820},
                "requiredRegions": {
                    "code-editor.region.root": {
                        "exists": True,
                        "visible": True,
                        "selector": "#code-editor-app",
                        "width": 1180,
                        "height": 820,
                    },
                    "code-editor.region.editor-group": {
                        "exists": True,
                        "visible": True,
                        "selector": ".code-studio-editor-group",
                        "width": 702,
                        "height": 438,
                    },
                },
                "optionalRegions": {
                    "code-editor.region.inspector": {
                        "exists": True,
                        "visible": True,
                        "selector": ".code-studio-inspector",
                        "width": 130,
                        "height": 438,
                    }
                },
                "surfaces": {
                    "primaryHost": {
                        "exists": True,
                        "visible": True,
                        "selector": "#code-studio-runtime-monaco",
                        "width": 702,
                        "height": 438,
                    },
                    "primaryEditor": {
                        "exists": True,
                        "visible": True,
                        "selector": ".monaco-editor",
                        "width": 702,
                        "height": 438,
                    },
                    "monacoHost": {
                        "exists": True,
                        "visible": True,
                        "selector": "#code-studio-runtime-monaco",
                        "width": 702,
                        "height": 438,
                    },
                    "monacoEditor": {
                        "exists": True,
                        "visible": True,
                        "selector": ".monaco-editor",
                        "width": 702,
                        "height": 438,
                    },
                },
                "forbiddenRegions": [
                    {
                        "id": "source-pane",
                        "selector": "[data-code-studio-pane='source']",
                        "box": {
                            "exists": True,
                            "visible": False,
                            "selector": "[data-code-studio-pane='source']",
                            "width": 0,
                            "height": 0,
                        },
                    },
                    {
                        "id": "serialized-pane",
                        "selector": "[data-code-studio-pane='serialized']",
                        "box": {
                            "exists": True,
                            "visible": False,
                            "selector": "[data-code-studio-pane='serialized']",
                            "width": 0,
                            "height": 0,
                        },
                    },
                    {
                        "id": "contract-pane",
                        "selector": "[data-code-studio-pane='contract']",
                        "box": {
                            "exists": True,
                            "visible": False,
                            "selector": "[data-code-studio-pane='contract']",
                            "width": 0,
                            "height": 0,
                        },
                    },
                    {
                        "id": "proof-dock",
                        "selector": "#code-studio-bottom-panel",
                        "box": {
                            "exists": True,
                            "visible": False,
                            "selector": "#code-studio-bottom-panel",
                            "width": 0,
                            "height": 0,
                        },
                    },
                ],
                "ownerChain": [
                    {
                        "exists": True,
                        "visible": True,
                        "selector": "#code-studio-runtime-monaco",
                        "width": 702,
                        "height": 438,
                    },
                    {
                        "exists": True,
                        "visible": True,
                        "selector": ".code-studio-editor-group",
                        "width": 702,
                        "height": 438,
                    },
                    {
                        "exists": True,
                        "visible": True,
                        "selector": ".code-studio-body",
                        "width": 1100,
                        "height": 520,
                        "gridTemplateColumns": "48px 220px 702px 130px",
                    },
                    {
                        "exists": True,
                        "visible": True,
                        "selector": ".code-studio-shell",
                        "width": 1180,
                        "height": 820,
                        "gridTemplateColumns": "1180px",
                    },
                ],
                "layoutCollisions": [],
                "contentFitViolations": [],
                "visualIntegrityViolations": [],
            },
            "contract": {
                "id": "code-editor.contract.authoring.monaco-golden-path",
                "appId": "code-editor",
                "mode": "authoring",
                "primarySurface": {
                    "id": "code-editor.surface.monaco-selected-file-editor",
                    "minWidth": 360,
                    "minHeight": 240,
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

def test_code_editor_declared_surface_bundle_passes_static_layers() -> None:
    bundle = build_repository_browser_catalog_payload(ROOT)["surfaceBundles"]["code-editor"]
    html = CODE_EDITOR_HTML.read_text(encoding="utf-8")
    report = healthy_code_editor_report_literal()
    script = load_conformance_stack(
        f"""
        const result = conformance.evaluateAppSurfaceConformance({{
          appId: "code-editor",
          surfaceId: "code-editor.surface.monaco-selected-file-editor",
          surfaceBundle: {json.dumps(bundle)},
          surfaceHtml: {json.dumps(html)},
          registryPolicy: {{
            appId: "code-editor",
            label: "Code Editor",
            state: "surface-aware",
            conformanceRequired: true,
            maturity: "semantic-runtime",
            surfaceId: "code-editor.surface.monaco-selected-file-editor",
            requiredLayerIds: ["semantic-surface", "layout-grammar", "runtime-ownership", "runtime-visual-fit", "diagnostic-no-throw"]
          }},
          report: {report}
        }});
        process.stdout.write(JSON.stringify({{
          status: result.status,
          valid: result.valid,
          failedLayerIds: result.failedLayerIds,
          unavailableLayerIds: result.unavailableLayerIds,
          policyFailedLayerIds: result.policyFailedLayerIds,
          policyUnavailableLayerIds: result.policyUnavailableLayerIds,
          layerStatus: Object.fromEntries(result.layers.map((layer) => [layer.id, layer.status])),
          layerFinding: Object.fromEntries(result.layers.map((layer) => [layer.id, layer.finding])),
          semanticDetail: result.layers.find((layer) => layer.id === "semantic-surface").detail,
          layoutDetail: result.layers.find((layer) => layer.id === "layout-grammar").detail,
          diagnosticCodes: result.diagnosticCodes
        }}));
        """
    )
    data = run_node_json(script)

    assert data["status"] == "pass"
    assert data["valid"] is True
    assert data["failedLayerIds"] == []
    assert data["unavailableLayerIds"] == []
    assert data["policyFailedLayerIds"] == []
    assert data["policyUnavailableLayerIds"] == []
    assert data["layerStatus"]["semantic-surface"] == "pass"
    assert data["layerStatus"]["layout-grammar"] == "pass"
    assert data["layerFinding"]["semantic-surface"].startswith("Declared MCEL semantic surface")
    assert data["semanticDetail"]["regionCount"] >= 10
    assert data["semanticDetail"]["controlCount"] >= 8
    assert data["semanticDetail"]["primaryRegionId"] == "primary-editor"
    assert data["layoutDetail"]["regionCount"] >= 10
    assert data["layoutDetail"]["constraintCount"] >= 6
    assert "app-surface-conformance-surface-bundle-unavailable" not in data["diagnosticCodes"]


def test_code_editor_app_surface_conformance_resolves_browser_catalog_surface_bundle() -> None:
    catalog_js = render_browser_catalog_javascript(build_repository_browser_catalog_payload(ROOT))
    report = healthy_code_editor_report_literal()
    script = load_conformance_stack(
        f"""
        vm.runInNewContext({json.dumps(catalog_js)}, sandbox, {{filename: "mcel-application-package-catalog.js"}});
        const result = conformance.evaluateAppSurfaceConformance({{
          appId: "code-editor",
          surfaceId: "code-editor.surface.monaco-selected-file-editor",
          report: {report}
        }});
        process.stdout.write(JSON.stringify({{
          status: result.status,
          valid: result.valid,
          surfaceBundleCount: sandbox.McelApplicationPackages.surfaceBundleCount,
          requiredLayerIds: result.requiredLayerIds,
          policyFailedLayerIds: result.policyFailedLayerIds,
          policyUnavailableLayerIds: result.policyUnavailableLayerIds,
          unavailableLayerIds: result.unavailableLayerIds,
          layerStatus: Object.fromEntries(result.layers.map((layer) => [layer.id, layer.status])),
          semanticFinding: result.layers.find((layer) => layer.id === "semantic-surface").finding,
          layoutFinding: result.layers.find((layer) => layer.id === "layout-grammar").finding,
          diagnosticCodes: result.diagnosticCodes
        }}));
        """
    )
    data = run_node_json(script)

    assert data["surfaceBundleCount"] == 2
    assert data["status"] == "pass"
    assert data["valid"] is True
    assert data["requiredLayerIds"] == [
        "semantic-surface",
        "layout-grammar",
        "runtime-ownership",
        "runtime-visual-fit",
        "diagnostic-no-throw",
    ]
    assert data["policyFailedLayerIds"] == []
    assert data["policyUnavailableLayerIds"] == []
    assert data["unavailableLayerIds"] == []
    assert data["layerStatus"]["semantic-surface"] == "pass"
    assert data["layerStatus"]["layout-grammar"] == "pass"
    assert data["semanticFinding"].startswith("Declared MCEL semantic surface")
    assert data["layoutFinding"].startswith("Declared MCEL layout grammar")
    assert "app-surface-conformance-surface-bundle-unavailable" not in data["diagnosticCodes"]



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
    assert "function attachDslAuthoringDeclarationFindings" in source
    assert "function auditDslAuthoringDeclarationCoverage" in source
    assert "listDslAuthoredSemanticRuntimePackages" in source
    assert "dsl-semantic-surface-missing" in source
    assert "dsl-layout-grammar-missing" in source
    assert "semanticSurfaceHtml" in source
    assert "semanticSurfaceId" in source
    assert "const expectedSurfaceId = (requiresStaticSurface && policySurfaceId)" in source
    assert "report = attachAppSurfaceConformance(report, snapshot, options);" in source
    assert "appSurfaceConformance" in source
    assert "buildReportBuckets(report)" in source


def test_dsl_authoring_declaration_gaps_warn_without_failing_widget_counts() -> None:
    catalog_js = render_browser_catalog_javascript(build_repository_browser_catalog_payload(ROOT))
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext({json.dumps(catalog_js)}, sandbox, {{filename: "mcel-application-package-catalog.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(SELF_DIAGNOSIS_JS))}, "utf8"), sandbox, {{filename: "mcel-self-diagnosis.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(COUNTER_JS))}, "utf8"), sandbox, {{filename: "mcel-diagnostics-counter-widget.js"}});

        const audit = sandbox.McelSelfDiagnosis.auditDslAuthoringDeclarationCoverage();
        const greenApp = audit.apps.find((app) => app.status === "green");
        const warningApp = audit.apps.find((app) => app.status === "warning");

        const greenReport = sandbox.McelSelfDiagnosis._private.attachDslAuthoringDeclarationFindings({{
          appId: greenApp?.appId || "",
          verdict: "pass",
          summary: {{critical: 0, warning: 0, info: 0}},
          findings: []
        }});
        const greenCounts = sandbox.MCELDiagnosticsCounterWidget._private.summarizeReport(
          greenReport,
          sandbox.MCELDiagnosticsCounterWidget._private.createIssueHistory("2026-08-10T22:00:00.000Z")
        );

        const warningReport = warningApp
          ? sandbox.McelSelfDiagnosis.diagnose(warningApp.appId, {{silent: true}})
          : null;
        const warningCounts = warningReport
          ? sandbox.MCELDiagnosticsCounterWidget._private.summarizeReport(
              warningReport,
              sandbox.MCELDiagnosticsCounterWidget._private.createIssueHistory("2026-08-10T22:00:00.000Z")
            )
          : null;

        const coverage = Object.fromEntries(audit.apps.map((app) => [app.appId, {{
          status: app.status,
          warningCount: app.warningCount,
          semanticSurfaceDeclared: app.semanticSurfaceDeclared,
          layoutGrammarDeclared: app.layoutGrammarDeclared,
          codes: app.findings.map((finding) => finding.code)
        }}]));

        process.stdout.write(JSON.stringify({{
          greenAppId: greenApp?.appId || "",
          greenVerdict: greenReport.verdict,
          greenSummary: greenReport.summary,
          greenCodes: greenReport.findings.map((finding) => finding.code),
          greenSeverities: greenReport.findings.map((finding) => finding.severity),
          greenCounts,
          warningAppId: warningApp?.appId || "",
          warningVerdict: warningReport?.verdict || "",
          warningCodes: warningReport?.findings.map((finding) => finding.code) || [],
          warningCounts,
          audit: {{
            appCount: audit.appCount,
            warningCount: audit.warningCount,
            greenCount: audit.greenCount,
            coverage
          }}
        }}));
        """
    )
    data = run_node_json(script)

    assert data["greenAppId"]
    assert data["greenVerdict"] == "pass"
    assert data["greenSummary"]["critical"] == 0
    assert data["greenSummary"]["warning"] == 0
    assert data["greenCodes"] == []
    assert data["greenSeverities"] == []
    assert data["greenCounts"]["errors"] == 0
    assert data["greenCounts"]["warnings"] == 0

    coverage = data["audit"]["coverage"]
    assert data["audit"]["appCount"] == len(coverage)
    assert data["audit"]["warningCount"] == sum(item["warningCount"] for item in coverage.values())
    assert data["audit"]["greenCount"] == sum(1 for item in coverage.values() if item["status"] == "green")

    for item in coverage.values():
        if item["status"] == "green":
            assert item["warningCount"] == 0
            assert item["semanticSurfaceDeclared"] is True
            assert item["layoutGrammarDeclared"] is True
            assert item["codes"] == []
        else:
            assert item["status"] == "warning"
            assert item["warningCount"] == 2
            assert item["semanticSurfaceDeclared"] is False
            assert item["layoutGrammarDeclared"] is False
            assert item["codes"] == [
                "dsl-semantic-surface-missing",
                "dsl-layout-grammar-missing",
            ]

    if data["warningAppId"]:
        assert set(data["warningCodes"]) >= {
            "dsl-semantic-surface-missing",
            "dsl-layout-grammar-missing",
        }
        assert data["warningCounts"]["errors"] == 0
        assert data["warningCounts"]["warnings"] >= 2


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
