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
DOC = ROOT / "pretty_docs" / "mcel-surface-renderer-interface.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; renderer interface smoke test cannot run")
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
        const irApi = sandbox.McelSemanticSurfaceIR;
        const layoutApi = sandbox.McelSharedLayoutGrammar;
        const rendererApi = sandbox.McelSurfaceRendererInterface;
        const roundTripApi = sandbox.McelSurfaceRoundTrip;

        function escapeAttr(value) {{
          return String(value ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }}

        function surfaceAttrs(surface, layout, profile, projection) {{
          return [
            `data-mcel-surface-id="${{escapeAttr(surface.id)}}"`,
            `data-mcel-surface-kind="${{escapeAttr(surface.kind)}}"`,
            `data-mcel-surface-role="${{escapeAttr(surface.role)}}"`,
            `data-mcel-surface-contract="${{escapeAttr(surface.contract)}}"`,
            `data-mcel-authoritative="true"`,
            `data-mcel-renderer="${{escapeAttr(profile.id)}}"`,
            `data-mcel-projection="${{escapeAttr(projection)}}"`,
            `data-layout-viewport-width="${{escapeAttr(layout.viewport.width)}}"`,
            `data-layout-viewport-height="${{escapeAttr(layout.viewport.height)}}"`,
            `data-layout-safe-margin="${{escapeAttr(layout.viewport.safeMargin)}}"`
          ].join(" ");
        }}

        function renderRegion(region) {{
          return `<section data-mcel-region="${{escapeAttr(region.id)}}" data-mcel-region-role="${{escapeAttr(region.role)}}" data-layout-x="${{escapeAttr(region.x)}}" data-layout-y="${{escapeAttr(region.y)}}" data-layout-region-width="${{escapeAttr(region.width)}}" data-layout-region-height="${{escapeAttr(region.height)}}"></section>`;
        }}

        function nodeLayout(layout, grammar) {{
          const ports = grammar.nodePorts[layout.id] || [];
          return [
            `data-layout-anchor-x="${{escapeAttr(layout.anchorX)}}"`,
            `data-layout-anchor-y="${{escapeAttr(layout.anchorY)}}"`,
            `data-layout-width="${{escapeAttr(layout.width)}}"`,
            `data-layout-height="${{escapeAttr(layout.height)}}"`,
            `data-layout-region="${{escapeAttr(layout.region)}}"`,
            `data-layout-ports="${{escapeAttr(ports.join(","))}}"`
          ].join(" ");
        }}

        function renderNode(node, grammar) {{
          const layout = grammar.nodes.find((item) => item.id === node.id);
          return `<article data-mcel-node-id="${{escapeAttr(node.id)}}" data-mcel-node-type="${{escapeAttr(node.type)}}" data-mcel-node-label="${{escapeAttr(node.label)}}" data-mcel-source="${{escapeAttr(node.source)}}" data-mcel-provenance="${{escapeAttr(node.provenance)}}" data-mcel-symbol="${{escapeAttr(node.symbol)}}" data-mcel-home-region="${{escapeAttr(node.homeRegion)}}" data-mcel-actual-region="${{escapeAttr(node.actualRegion)}}" data-mcel-teleported="${{escapeAttr(node.teleported)}}" ${{nodeLayout(layout, grammar)}}></article>`;
        }}

        function renderEdge(edge, grammar) {{
          const layout = grammar.edges.find((item) => item.id === edge.id);
          return `<i data-mcel-edge-id="${{escapeAttr(edge.id)}}" data-mcel-edge-kind="${{escapeAttr(edge.kind)}}" data-mcel-from="${{escapeAttr(edge.from)}}" data-mcel-to="${{escapeAttr(edge.to)}}" data-mcel-relation="${{escapeAttr(edge.relation)}}" data-mcel-causal-link="${{escapeAttr(edge.causalLink)}}" data-mcel-allowed-inferences="${{escapeAttr((edge.allowedInferences || []).join(","))}}" data-mcel-forbidden-inferences="${{escapeAttr((edge.forbiddenInferences || []).join(","))}}" data-layout-route-kind="${{escapeAttr(layout.routeKind)}}" data-layout-from-port="${{escapeAttr(layout.fromPort)}}" data-layout-to-port="${{escapeAttr(layout.toPort)}}"></i>`;
        }}

        function renderControl(control, grammar) {{
          const layout = grammar.controls.find((item) => item.id === control.id);
          return `<button data-mcel-control="${{escapeAttr(control.id)}}" data-mcel-control-action="${{escapeAttr(control.action)}}" data-mcel-reveals="${{escapeAttr(control.reveals)}}" data-layout-anchor-x="${{escapeAttr(layout.anchorX)}}" data-layout-anchor-y="${{escapeAttr(layout.anchorY)}}" data-layout-width="${{escapeAttr(layout.width)}}" data-layout-height="${{escapeAttr(layout.height)}}"></button>`;
        }}

        function renderNeutralHtml(request) {{
          const ir = request.surfaceIR;
          const grammar = request.layoutGrammar.grammar || request.layoutGrammar;
          const profile = request.profile;
          return `<section ${{surfaceAttrs(ir.surface, grammar, profile, "html")}}>${{grammar.regions.map(renderRegion).join("")}}${{ir.graph.nodes.map((node) => renderNode(node, grammar)).join("")}}${{ir.graph.edges.map((edge) => renderEdge(edge, grammar)).join("")}}${{ir.graph.controls.map((control) => renderControl(control, grammar)).join("")}}</section>`;
        }}

        function renderNeutralSvg(request) {{
          const ir = request.surfaceIR;
          const grammar = request.layoutGrammar.grammar || request.layoutGrammar;
          const profile = request.profile;
          return `<svg xmlns="http://www.w3.org/2000/svg" width="${{grammar.viewport.width}}" height="${{grammar.viewport.height}}" ${{surfaceAttrs(ir.surface, grammar, profile, "svg")}}>${{grammar.regions.map(renderRegion).join("")}}${{ir.graph.nodes.map((node) => renderNode(node, grammar)).join("")}}${{ir.graph.edges.map((edge) => renderEdge(edge, grammar)).join("")}}${{ir.graph.controls.map((control) => renderControl(control, grammar)).join("")}}</svg>`;
        }}

        function neutralInputs() {{
          return {{
            surfaceIR: irApi.buildNeutralDemoSurfaceIR(),
            layoutGrammar: layoutApi.buildNeutralDemoLayoutGrammar()
          }};
        }}

        {body}
        """
    )


def test_renderer_profile_validation_requires_stable_identity_and_supported_kind() -> None:
    script = load_api_script(
        """
        const valid = rendererApi.validateRendererProfile({
          id: "renderer.neutral-html",
          surfaceKinds: ["html"],
          capabilities: ["semantic-ridges", "layout-ridges"]
        });
        const invalid = rendererApi.validateRendererProfile({
          id: "",
          surfaceKinds: ["canvas"],
          output: {emitsSemanticRidges: false}
        });
        process.stdout.write(JSON.stringify({
          contractVersion: rendererApi.contractVersion,
          validStatus: valid.status,
          invalidStatus: invalid.status,
          invalidCodes: invalid.diagnostics.map((item) => item.code).sort(),
          fingerprintStable: rendererApi.rendererProfileFingerprint(valid.profile) === rendererApi.rendererProfileFingerprint(valid.profile)
        }));
        """
    )
    result = run_node_json(script)

    assert result["contractVersion"] == "mcel.surface-renderer-interface.v1"
    assert result["validStatus"] == "pass"
    assert result["invalidStatus"] == "fail"
    assert "surface-renderer-profile-missing-id" in result["invalidCodes"]
    assert "surface-renderer-profile-unsupported-surface-kind" in result["invalidCodes"]
    assert "surface-renderer-profile-does-not-emit-semantic-ridges" in result["invalidCodes"]
    assert result["fingerprintStable"] is True


def test_renderer_implementation_requires_render_function() -> None:
    script = load_api_script(
        """
        const result = rendererApi.validateRendererImplementation({
          profile: {id: "renderer.no-render", surfaceKinds: ["html"]}
        });
        process.stdout.write(JSON.stringify({
          status: result.status,
          codes: result.diagnostics.map((item) => item.code)
        }));
        """
    )
    result = run_node_json(script)

    assert result["status"] == "fail"
    assert "surface-renderer-implementation-missing-render-function" in result["codes"]


def test_html_renderer_output_roundtrips_against_surface_ir_and_layout() -> None:
    script = load_api_script(
        """
        const inputs = neutralInputs();
        const renderer = {
          profile: {id: "renderer.neutral-html", surfaceKinds: ["html"], defaultSurfaceKind: "html"},
          render: renderNeutralHtml
        };
        const result = rendererApi.renderWithRenderer(renderer, inputs);
        process.stdout.write(JSON.stringify({
          status: result.status,
          verificationStatus: result.outputVerification.status,
          renderer: result.outputVerification.roundTrip.extractedBundle.surfaceIR.surface.renderer,
          projection: result.outputVerification.roundTrip.extractedBundle.surfaceIR.surface.projection,
          summary: roundTripApi.summarizeRoundTrip(result.outputVerification.roundTrip)
        }));
        """
    )
    result = run_node_json(script)

    assert result["status"] == "pass"
    assert result["verificationStatus"] == "pass"
    assert result["renderer"] == "renderer.neutral-html"
    assert result["projection"] == "html"
    assert result["summary"]["valid"] is True


def test_svg_renderer_output_roundtrips_against_surface_ir_and_layout() -> None:
    script = load_api_script(
        """
        const inputs = neutralInputs();
        const renderer = {
          profile: {id: "renderer.neutral-svg", surfaceKinds: ["svg"], defaultSurfaceKind: "svg"},
          render: renderNeutralSvg
        };
        const result = rendererApi.renderWithRenderer(renderer, Object.assign({surfaceKind: "svg"}, inputs));
        process.stdout.write(JSON.stringify({
          status: result.status,
          verificationStatus: result.outputVerification.status,
          renderer: result.outputVerification.roundTrip.extractedBundle.surfaceIR.surface.renderer,
          projection: result.outputVerification.roundTrip.extractedBundle.surfaceIR.surface.projection
        }));
        """
    )
    result = run_node_json(script)

    assert result["status"] == "pass"
    assert result["verificationStatus"] == "pass"
    assert result["renderer"] == "renderer.neutral-svg"
    assert result["projection"] == "svg"


def test_renderer_output_fails_when_required_ridges_are_missing() -> None:
    script = load_api_script(
        """
        const inputs = neutralInputs();
        const renderer = {
          profile: {id: "renderer.bad", surfaceKinds: ["html"], defaultSurfaceKind: "html"},
          render() {
            return "<section><article>Observation A</article></section>";
          }
        };
        const result = rendererApi.renderWithRenderer(renderer, inputs);
        process.stdout.write(JSON.stringify({
          status: result.status,
          codes: result.diagnostics.map((item) => item.code).sort()
        }));
        """
    )
    result = run_node_json(script)

    assert result["status"] == "fail"
    assert "surface-renderer-output-roundtrip-failed" in result["codes"]


def test_html_and_svg_renderer_pair_agree_through_roundtrip_extraction() -> None:
    script = load_api_script(
        """
        const inputs = neutralInputs();
        const htmlRenderer = {
          profile: {id: "renderer.neutral-html", surfaceKinds: ["html"], defaultSurfaceKind: "html"},
          render: renderNeutralHtml
        };
        const svgRenderer = {
          profile: {id: "renderer.neutral-svg", surfaceKinds: ["svg"], defaultSurfaceKind: "svg"},
          render: renderNeutralSvg
        };
        const htmlResult = rendererApi.renderWithRenderer(htmlRenderer, inputs);
        const svgResult = rendererApi.renderWithRenderer(svgRenderer, Object.assign({surfaceKind: "svg"}, inputs));
        const agreement = rendererApi.verifyRendererPairAgreement(htmlResult, svgResult);
        process.stdout.write(JSON.stringify({
          htmlStatus: htmlResult.status,
          svgStatus: svgResult.status,
          agreementStatus: agreement.status
        }));
        """
    )
    result = run_node_json(script)

    assert result["htmlStatus"] == "pass"
    assert result["svgStatus"] == "pass"
    assert result["agreementStatus"] == "pass"


def test_renderer_interface_docs_exist_and_name_the_contract() -> None:
    text = DOC.read_text(encoding="utf-8")
    assert "mcel.surface-renderer-interface.v1" in text
    assert "SemanticSurfaceIR" in text
    assert "SharedLayoutGrammar" in text
    assert "round-trip" in text
