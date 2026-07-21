from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RIDGES = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-semantic-surface-ridges.js"
IR = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-semantic-surface-ir.js"
LAYOUT = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-shared-layout-grammar.js"
EXTRACTORS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-surface-extractors.js"
ROUNDTRIP = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-surface-roundtrip.js"
RENDERER_INTERFACE = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-surface-renderer-interface.js"
DEMO = ROOT / "main_computer" / "web" / "applications" / "scripts" / "mcel-neutral-surface-demo.js"
DOC = ROOT / "pretty_docs" / "mcel-neutral-surface-demo.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; neutral surface demo smoke test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_api_script(body: str) -> str:
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
        vm.runInNewContext(fs.readFileSync({json.dumps(str(RENDERER_INTERFACE))}, "utf8"), sandbox, {{filename: "mcel-surface-renderer-interface.js"}});
        vm.runInNewContext(fs.readFileSync({json.dumps(str(DEMO))}, "utf8"), sandbox, {{filename: "mcel-neutral-surface-demo.js"}});
        const irApi = sandbox.McelSemanticSurfaceIR;
        const layoutApi = sandbox.McelSharedLayoutGrammar;
        const extractorApi = sandbox.McelSurfaceExtractors;
        const roundTripApi = sandbox.McelSurfaceRoundTrip;
        const rendererApi = sandbox.McelSurfaceRendererInterface;
        const demoApi = sandbox.McelNeutralSurfaceDemo;
        {body}
        """
    )


def test_neutral_demo_source_and_docs_are_domain_neutral() -> None:
    source = DEMO.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")

    assert "mcel.neutral-surface-demo.v1" in source
    assert "SemanticSurfaceIR" in source
    assert "SharedLayoutGrammar" in source
    assert "renderer interface" in doc
    assert "round-trip verification" in doc

    forbidden_domain_terms = [
        "BIO_HEALTH",
        "SYS_HEALTH",
        "heart_rate",
        "elephant",
        "health_app",
        "Health App",
    ]
    for term in forbidden_domain_terms:
        assert term not in source
        assert term not in doc


def test_neutral_demo_builds_surface_and_layout_fixture() -> None:
    script = load_api_script(
        """
        const surfaceIR = demoApi.createNeutralDemoSurfaceIR();
        const layoutGrammar = demoApi.createNeutralDemoLayoutGrammar();
        const surfaceValidation = irApi.validateSurfaceIR(surfaceIR);
        const layoutValidation = layoutApi.validateSharedLayoutGrammar(surfaceIR, layoutGrammar);
        process.stdout.write(JSON.stringify({
          contractVersion: demoApi.contractVersion,
          surfaceId: surfaceIR.surface.id,
          nodeIds: surfaceIR.graph.nodes.map((node) => node.id).sort(),
          edgeIds: surfaceIR.graph.edges.map((edge) => edge.id).sort(),
          controlIds: surfaceIR.graph.controls.map((control) => control.id).sort(),
          layoutSurfaceId: layoutGrammar.surfaceId,
          surfaceValid: surfaceValidation.valid,
          layoutValid: layoutValidation.valid
        }));
        """
    )

    result = run_node_json(script)

    assert result["contractVersion"] == "mcel.neutral-surface-demo.v1"
    assert result["surfaceId"] == "surface.demo-neutral"
    assert result["layoutSurfaceId"] == "surface.demo-neutral"
    assert result["nodeIds"] == ["Hypothesis.B", "Observation.A"]
    assert result["edgeIds"] == ["EDGE.observation-supports-hypothesis"]
    assert result["controlIds"] == ["trace_evidence"]
    assert result["surfaceValid"] is True
    assert result["layoutValid"] is True


def test_neutral_demo_html_and_svg_outputs_roundtrip_individually() -> None:
    script = load_api_script(
        """
        const surfaceIR = demoApi.createNeutralDemoSurfaceIR();
        const layoutGrammar = demoApi.createNeutralDemoLayoutGrammar();
        const htmlRenderer = demoApi.htmlRenderer();
        const svgRenderer = demoApi.svgRenderer();
        const htmlResult = rendererApi.renderWithRenderer(htmlRenderer, {surfaceIR, layoutGrammar, surfaceKind: "html"});
        const svgResult = rendererApi.renderWithRenderer(svgRenderer, {surfaceIR, layoutGrammar, surfaceKind: "svg"});
        process.stdout.write(JSON.stringify({
          htmlStatus: htmlResult.status,
          svgStatus: svgResult.status,
          htmlRenderer: htmlResult.outputVerification.roundTrip.extractedBundle.surfaceIR.surface.renderer,
          svgRenderer: svgResult.outputVerification.roundTrip.extractedBundle.surfaceIR.surface.renderer,
          htmlProjection: htmlResult.outputVerification.roundTrip.extractedBundle.surfaceIR.surface.projection,
          svgProjection: svgResult.outputVerification.roundTrip.extractedBundle.surfaceIR.surface.projection
        }));
        """
    )

    result = run_node_json(script)

    assert result["htmlStatus"] == "pass"
    assert result["svgStatus"] == "pass"
    assert result["htmlRenderer"] == "mcel.neutral-demo.html-renderer.v1"
    assert result["svgRenderer"] == "mcel.neutral-demo.svg-renderer.v1"
    assert result["htmlProjection"] == "html"
    assert result["svgProjection"] == "svg"


def test_neutral_demo_pair_agreement_proves_full_pathway() -> None:
    script = load_api_script(
        """
        const result = demoApi.verifyNeutralDemoRoundTrip();
        process.stdout.write(JSON.stringify({
          status: result.status,
          htmlStatus: result.htmlResult.status,
          svgStatus: result.svgResult.status,
          agreementStatus: result.agreement.status,
          semanticMatch: result.agreement.agreement.semantic.valid,
          layoutMatch: result.agreement.agreement.layout.valid
        }));
        """
    )

    result = run_node_json(script)

    assert result["status"] == "pass"
    assert result["htmlStatus"] == "pass"
    assert result["svgStatus"] == "pass"
    assert result["agreementStatus"] == "pass"
    assert result["semanticMatch"] is True
    assert result["layoutMatch"] is True


def test_neutral_demo_markup_carries_required_ridges() -> None:
    script = load_api_script(
        """
        const html = demoApi.renderNeutralDemoHtml();
        const svg = demoApi.renderNeutralDemoSvg();
        process.stdout.write(JSON.stringify({
          htmlHasSurface: html.includes('data-mcel-surface-id="surface.demo-neutral"'),
          htmlHasNode: html.includes('data-mcel-node-id="Observation.A"'),
          htmlHasLayout: html.includes('data-layout-anchor-x="280"'),
          htmlHasRenderer: html.includes('data-mcel-renderer="mcel.neutral-demo.html-renderer.v1"'),
          svgHasSurface: svg.includes('data-mcel-surface-id="surface.demo-neutral"'),
          svgHasNode: svg.includes('data-mcel-node-id="Hypothesis.B"'),
          svgHasRoute: svg.includes('data-layout-route-kind="cubic"'),
          svgHasRenderer: svg.includes('data-mcel-renderer="mcel.neutral-demo.svg-renderer.v1"')
        }));
        """
    )

    result = run_node_json(script)

    assert result == {
        "htmlHasSurface": True,
        "htmlHasNode": True,
        "htmlHasLayout": True,
        "htmlHasRenderer": True,
        "svgHasSurface": True,
        "svgHasNode": True,
        "svgHasRoute": True,
        "svgHasRenderer": True,
    }


def test_neutral_demo_rejects_renderer_attribution_mismatch() -> None:
    script = load_api_script(
        """
        const surfaceIR = demoApi.createNeutralDemoSurfaceIR();
        const layoutGrammar = demoApi.createNeutralDemoLayoutGrammar();
        const renderedText = demoApi.renderNeutralDemoHtml();
        const report = rendererApi.verifyRendererOutput({
          profile: {
            id: "mcel.neutral-demo.other-renderer.v1",
            surfaceKinds: ["html"],
            defaultSurfaceKind: "html"
          },
          surfaceKind: "html",
          renderedText,
          expectedSurfaceIr: surfaceIR,
          expectedLayoutGrammar: layoutGrammar
        });
        process.stdout.write(JSON.stringify({
          status: report.status,
          codes: report.diagnostics.map((item) => item.code).sort()
        }));
        """
    )

    result = run_node_json(script)

    assert result["status"] == "fail"
    assert "surface-renderer-attribution-mismatch" in result["codes"]
