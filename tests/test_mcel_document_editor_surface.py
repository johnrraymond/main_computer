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
DOCUMENT_HTML = WEB / "apps" / "document.html"
SURFACE_JS = SCRIPTS / "mcel-document-editor-surface.js"
REGISTRY_JS = SCRIPTS / "mcel-app-surface-registry.js"
SELF_DIAGNOSIS_JS = SCRIPTS / "mcel-self-diagnosis.js"
DOC = ROOT / "pretty_docs" / "mcel-document-editor-surface.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; Document Editor MCEL surface smoke test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_document_surface_stack(body: str) -> str:
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
          "mcel-app-surface-conformance.js",
          "mcel-document-editor-surface.js"
        ]) {{
          vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPTS))} + "/" + name, "utf8"), sandbox, {{filename: name}});
        }}
        const surface = sandbox.McelDocumentEditorSurface;
        {body}
        """
    )


def healthy_document_runtime_report() -> dict:
    return {
        "schema": "mcel-self-diagnosis-report-v2",
        "version": "mcel-self-diagnosis-v2",
        "contractId": "document-editor.contract.default.app-health",
        "appId": "document",
        "mode": "default",
        "route": "http://localhost:8765/applications/document",
        "verdict": "pass",
        "summary": {
            "critical": 0,
            "warning": 0,
            "info": 0,
            "primarySurface": {
                "expected": "document-editor.surface.primary",
                "usable": True,
                "exactlyOneAuthoritativeSurface": True,
                "host": {
                    "exists": True,
                    "visible": True,
                    "selector": "#document-object-stage",
                    "width": 760,
                    "height": 520,
                },
                "editor": {
                    "exists": True,
                    "visible": True,
                    "selector": "#document-editor",
                    "width": 620,
                    "height": 440,
                },
            },
        },
        "findings": [],
        "measurements": {
            "viewport": {"width": 1440, "height": 900},
            "requiredRegions": {
                "document-editor.region.root": {
                    "exists": True,
                    "visible": True,
                    "selector": "#document-app",
                    "width": 1280,
                    "height": 720,
                },
                "document-editor.region.primary": {
                    "exists": True,
                    "visible": True,
                    "selector": "#document-object-stage",
                    "width": 760,
                    "height": 520,
                },
                "document-editor.region.content": {
                    "exists": True,
                    "visible": True,
                    "selector": "#document-editor",
                    "width": 620,
                    "height": 440,
                },
            },
            "surfaces": {
                "primaryHost": {
                    "exists": True,
                    "visible": True,
                    "selector": "#document-object-stage",
                    "width": 760,
                    "height": 520,
                },
                "primaryEditor": {
                    "exists": True,
                    "visible": True,
                    "selector": "#document-editor",
                    "width": 620,
                    "height": 440,
                },
            },
            "layoutCollisions": [],
            "contentFitViolations": [],
            "visualIntegrityViolations": [],
        },
        "contract": {
            "id": "document-editor.contract.default.app-health",
            "appId": "document",
            "mode": "default",
            "primarySurface": {
                "id": "document-editor.surface.primary",
                "minWidth": 420,
                "minHeight": 320,
            },
        },
    }


def test_document_editor_surface_files_are_wired_without_visible_panel() -> None:
    assert SURFACE_JS.exists()
    assert DOC.exists()

    app_shell = APP_SHELL.read_text(encoding="utf-8")
    assert "mcel-document-editor-surface.js" in app_shell
    assert app_shell.index("mcel-surface-preview-contract.js") < app_shell.index("mcel-document-editor-surface.js")
    assert app_shell.index("mcel-document-editor-surface.js") < app_shell.index("mcel-app-surface-registry.js")

    html = DOCUMENT_HTML.read_text(encoding="utf-8")
    assert 'data-mcel-surface-id="document-editor.surface.primary"' in html
    assert "mcel-preview" not in html.lower()
    assert "mcel-inspector" not in html.lower()


def test_document_editor_static_markup_extracts_as_valid_mcel_surface() -> None:
    html = DOCUMENT_HTML.read_text(encoding="utf-8")
    script = load_document_surface_stack(
        f"""
        const bundle = sandbox.McelSurfaceExtractors.extractSurfaceBundleFromHtml({json.dumps(html)}, {{
          surfaceId: "document-editor.surface.primary"
        }});
        const diagnostics = bundle.diagnostics
          .concat(bundle.validation.surface.diagnostics)
          .concat(bundle.validation.layout.diagnostics)
          .map((item) => item.code);
        process.stdout.write(JSON.stringify({{
          valid: bundle.valid,
          surfaceId: bundle.surfaceIR.surface.id,
          nodeIds: bundle.surfaceIR.graph.nodes.map((node) => node.id).sort(),
          regionIds: bundle.surfaceIR.graph.regions.map((region) => region.id).sort(),
          edgeIds: bundle.surfaceIR.graph.edges.map((edge) => edge.id).sort(),
          controlIds: bundle.surfaceIR.graph.controls.map((control) => control.id).sort(),
          diagnostics
        }}));
        """
    )
    data = run_node_json(script)

    assert data["valid"] is True
    assert data["surfaceId"] == "document-editor.surface.primary"
    assert data["diagnostics"] == []
    assert data["nodeIds"] == [
        "document-editor.node.ai-context",
        "document-editor.node.document-block",
        "document-editor.node.document-content",
        "document-editor.node.document-library",
        "document-editor.node.document-page",
        "document-editor.node.document-session",
        "document-editor.node.export-target",
        "document-editor.node.layout-state",
        "document-editor.node.selected-document",
        "document-editor.node.selected-object",
        "document-editor.node.status-message",
    ]
    assert data["regionIds"] == [
        "document-editor.region.advanced",
        "document-editor.region.companion",
        "document-editor.region.document-content",
        "document-editor.region.document-page",
        "document-editor.region.menu",
        "document-editor.region.navigation",
        "document-editor.region.primary",
        "document-editor.region.status",
        "document-editor.region.toolbar",
    ]
    assert data["edgeIds"] == [
        "document-editor.edge.companion-describes-content",
        "document-editor.edge.content-contains-block",
        "document-editor.edge.content-targets-object",
        "document-editor.edge.export-projects-document",
        "document-editor.edge.library-selects-document",
        "document-editor.edge.page-owns-content",
        "document-editor.edge.session-owns-page",
        "document-editor.edge.toolbar-configures-layout",
    ]
    assert data["controlIds"] == [
        "document-editor.control.ai-apply",
        "document-editor.control.ai-send",
        "document-editor.control.discard-draft",
        "document-editor.control.export-pdf",
        "document-editor.control.format-bold",
        "document-editor.control.insert-scene",
        "document-editor.control.layout-apply",
        "document-editor.control.reload-disk",
        "document-editor.control.toggle-ai",
        "document-editor.control.toggle-library",
    ]


def test_document_editor_surface_contract_builds_reusable_ir_and_layout() -> None:
    script = load_document_surface_stack(
        """
        const records = surface.buildStaticSurfaceRidgeRecords();
        const irResult = sandbox.McelSemanticSurfaceIR.buildSurfaceIRFromRidges(records, {requireSurface: true});
        const regionRecords = records
          .filter((record) => record["data-mcel-region"])
          .map((record) => ({
            id: record["data-mcel-region"],
            role: record["data-mcel-region-role"],
            x: Number(record["data-layout-x"]),
            y: Number(record["data-layout-y"]),
            width: Number(record["data-layout-region-width"]),
            height: Number(record["data-layout-region-height"])
          }));
        const nodePorts = Object.fromEntries(records
          .filter((record) => record["data-mcel-node-id"])
          .map((record) => [record["data-mcel-node-id"], ["north", "south", "east", "west"]]));
        const layoutResult = sandbox.McelSharedLayoutGrammar.buildSharedLayoutGrammar(irResult.ir, {
          viewport: {width: 1440, height: 900, safeMargin: 16},
          regions: regionRecords,
          nodePorts
        });
        process.stdout.write(JSON.stringify({
          recordCount: records.length,
          irValid: irResult.valid,
          layoutValid: layoutResult.valid,
          nodes: irResult.ir.graph.nodes.length,
          edges: irResult.ir.graph.edges.length,
          controls: irResult.ir.graph.controls.length,
          layoutDiagnostics: layoutResult.diagnostics.map((item) => item.code)
        }));
        """
    )
    data = run_node_json(script)

    assert data["recordCount"] == 39
    assert data["irValid"] is True
    assert data["layoutValid"] is True
    assert data["layoutDiagnostics"] == []
    assert data["nodes"] == 11
    assert data["edges"] == 8
    assert data["controls"] == 10


def test_document_editor_is_parked_as_runtime_baseline() -> None:
    report = healthy_document_runtime_report()
    html = DOCUMENT_HTML.read_text(encoding="utf-8")
    script = load_document_surface_stack(
        f"""
        const policy = sandbox.McelAppSurfaceRegistry.getAppPolicy("document");
        const result = sandbox.McelAppSurfaceConformance.evaluateAppSurfaceConformance({{
          appId: "document",
          surfaceId: "document-editor.surface.primary",
          surfaceHtml: {json.dumps(html)},
          report: {json.dumps(report)}
        }});
        process.stdout.write(JSON.stringify({{
          policy,
          status: result.status,
          valid: result.valid,
          requiredLayerIds: result.requiredLayerIds,
          policyFailedLayerIds: result.policyFailedLayerIds,
          policyUnavailableLayerIds: result.policyUnavailableLayerIds
        }}));
        """
    )
    data = run_node_json(script)

    assert data["policy"]["state"] == "surface-aware"
    assert data["policy"]["maturity"] == "runtime-baseline"
    assert data["policy"]["conformanceRequired"] is True
    assert data["policy"]["surfaceId"] == "document-editor.surface.primary"
    assert data["requiredLayerIds"] == [
        "runtime-ownership",
        "runtime-visual-fit",
        "diagnostic-no-throw",
    ]
    assert data["status"] == "pass"
    assert data["valid"] is True
    assert data["policyFailedLayerIds"] == []
    assert data["policyUnavailableLayerIds"] == []


def test_document_self_diagnosis_contract_is_available() -> None:
    source = SELF_DIAGNOSIS_JS.read_text(encoding="utf-8")

    assert 'document: "default"' in source
    assert 'contractId: "document-editor.contract.default.app-health"' in source
    assert 'appId: "document"' in source
    assert 'hostSelector: "#document-object-stage"' in source
    assert 'editorSelector: "#document-editor"' in source
    assert 'document-editor.region.content' in source


def test_document_editor_surface_is_domain_neutral_and_documented() -> None:
    script = SURFACE_JS.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")

    assert "mcel.document-editor-surface.v1" in script
    assert "document-editor.surface.primary" in script
    assert "buildStaticSurfaceRidgeRecords" in script
    assert "extractCurrentSurface" in script
    assert "runtime-baseline" in doc

    forbidden_terms = ["Health", "BIO_HEALTH", "SYS_HEALTH", "patient", "clinical"]
    for text in [script, doc]:
        for term in forbidden_terms:
            assert term not in text


def test_patch24a_document_editor_spec_separates_outline_picker_and_companion() -> None:
    doc = " ".join(DOC.read_text(encoding="utf-8").split())

    required_phrases = [
        "Patch 24a target model",
        "left navigation -> headings for the current document",
        "File selection is a transient task",
        "The left rail MUST represent the structure of the currently loaded document",
        "document-outline-navigation",
        "Open Pretty Doc...",
        "document-editor.region.file-picker-modal",
        "explicit save/discard/cancel decision",
        "docked",
        "expanded",
        "active",
        "overlay",
        "Docking MUST preserve the thread, draft, result, and selection context",
        "MUST NOT be emitted as unbounded static application-surface layout nodes",
        "Only the center primary zone is the permanent width and document-scroll owner",
        "Document scrolling MUST NOT move the outline rail, the companion panel",
        "The outline body owns its own vertical scrollbar",
        "Only its internal conversation/result body scrolls",
        "Patch 24a is specification and contract-test work only",
        "Patch 24b is responsible for implementing those behaviors",
    ]
    for phrase in required_phrases:
        assert phrase in doc
