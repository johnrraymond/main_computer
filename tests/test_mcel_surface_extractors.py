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
DOC = ROOT / "pretty_docs" / "mcel-surface-extractors.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; surface extractor smoke test cannot run")
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
        const ridges = sandbox.McelSemanticSurfaceRidges;
        const irApi = sandbox.McelSemanticSurfaceIR;
        const layoutApi = sandbox.McelSharedLayoutGrammar;
        const extractorApi = sandbox.McelSurfaceExtractors;
        {body}
        """
    )


HTML_SURFACE = """
<section
  data-mcel-surface-id="surface.demo-neutral"
  data-mcel-surface-kind="semantic-surface"
  data-mcel-surface-role="authoring-preview"
  data-mcel-surface-contract="mcel.semantic-surface-ir.v1"
  data-mcel-authoritative="true"
  data-mcel-renderer="html"
  data-mcel-projection="html"
  data-layout-viewport-width="900"
  data-layout-viewport-height="520"
  data-layout-safe-margin="24"
>
  <section data-mcel-region="region.workbench" data-mcel-region-role="workbench" data-layout-x="60" data-layout-y="80" data-layout-width="780" data-layout-height="360"></section>
  <article data-mcel-node-id="Observation.A" data-mcel-node-type="observation" data-mcel-node-label="Observation A" data-mcel-source="fixture" data-mcel-provenance="fixture:observation-a" data-mcel-symbol="○" data-mcel-home-region="region.workbench" data-mcel-actual-region="region.workbench" data-layout-anchor-x="280" data-layout-anchor-y="240" data-layout-width="180" data-layout-height="80" data-layout-region="region.workbench" data-layout-z="2"></article>
  <article data-mcel-node-id="Hypothesis.B" data-mcel-node-type="hypothesis" data-mcel-node-label="Hypothesis B" data-mcel-source="fixture" data-mcel-provenance="fixture:hypothesis-b" data-mcel-symbol="?" data-mcel-home-region="region.workbench" data-mcel-actual-region="region.workbench" data-layout-anchor-x="620" data-layout-anchor-y="240" data-layout-width="180" data-layout-height="80" data-layout-region="region.workbench" data-layout-z="2"></article>
  <i data-mcel-edge-id="EDGE.observation-supports-hypothesis" data-mcel-edge-kind="SUPPORTS" data-mcel-from="Observation.A" data-mcel-to="Hypothesis.B" data-mcel-relation="evidence_for" data-mcel-causal-link="false" data-mcel-allowed-inferences="support,comparison" data-mcel-forbidden-inferences="identity,direct_causality" data-layout-route-kind="cubic" data-layout-from-port="east" data-layout-to-port="west" data-layout-z="1"></i>
  <button data-mcel-control="trace_evidence" data-mcel-control-action="trace" data-mcel-reveals="SUPPORTS" data-layout-anchor-x="460" data-layout-anchor-y="360" data-layout-width="140" data-layout-height="38" data-layout-z="3">Trace</button>
</section>
"""

SVG_SURFACE = """
<svg
  xmlns="http://www.w3.org/2000/svg"
  width="900"
  height="520"
  data-mcel-surface-id="surface.demo-neutral"
  data-mcel-surface-kind="semantic-surface"
  data-mcel-surface-role="authoring-preview"
  data-mcel-surface-contract="mcel.semantic-surface-ir.v1"
  data-mcel-authoritative="true"
  data-mcel-renderer="svg"
  data-mcel-projection="svg"
  data-layout-safe-margin="24"
>
  <rect data-mcel-region="region.workbench" data-mcel-region-role="workbench" x="60" y="80" width="780" height="360"></rect>
  <g data-mcel-node-id="Observation.A" data-mcel-node-type="observation" data-mcel-node-label="Observation A" data-mcel-source="fixture" data-mcel-provenance="fixture:observation-a" data-mcel-symbol="○" data-mcel-home-region="region.workbench" data-mcel-actual-region="region.workbench" data-layout-anchor-x="280" data-layout-anchor-y="240" data-layout-width="180" data-layout-height="80" data-layout-region="region.workbench" data-layout-z="2"></g>
  <g data-mcel-node-id="Hypothesis.B" data-mcel-node-type="hypothesis" data-mcel-node-label="Hypothesis B" data-mcel-source="fixture" data-mcel-provenance="fixture:hypothesis-b" data-mcel-symbol="?" data-mcel-home-region="region.workbench" data-mcel-actual-region="region.workbench" data-layout-anchor-x="620" data-layout-anchor-y="240" data-layout-width="180" data-layout-height="80" data-layout-region="region.workbench" data-layout-z="2"></g>
  <path data-mcel-edge-id="EDGE.observation-supports-hypothesis" data-mcel-edge-kind="SUPPORTS" data-mcel-from="Observation.A" data-mcel-to="Hypothesis.B" data-mcel-relation="evidence_for" data-mcel-causal-link="false" data-mcel-allowed-inferences="support,comparison" data-mcel-forbidden-inferences="identity,direct_causality" data-layout-route-kind="cubic" data-layout-from-port="east" data-layout-to-port="west" data-layout-z="1"></path>
  <g data-mcel-control="trace_evidence" data-mcel-control-action="trace" data-mcel-reveals="SUPPORTS" data-layout-anchor-x="460" data-layout-anchor-y="360" data-layout-width="140" data-layout-height="38" data-layout-z="3"></g>
</svg>
"""


def test_surface_extractors_contract_is_domain_neutral() -> None:
    source = EXTRACTORS.read_text(encoding="utf-8")

    assert "mcel.surface-extractors.v1" in source
    assert "extractSurfaceBundleFromHtml" in source
    assert "extractSurfaceBundleFromSvg" in source
    assert "extractLayoutGrammarFromHtml" in source
    assert "extractLayoutGrammarFromSvg" in source

    forbidden_domain_terms = [
        "BIO_HEALTH",
        "SYS_HEALTH",
        "heart_rate",
        "elephant",
        "health_app",
    ]
    for term in forbidden_domain_terms:
        assert term not in source


def test_html_extraction_recovers_semantic_surface_and_layout() -> None:
    script = load_api_script(
        f"""
        const html = {json.dumps(HTML_SURFACE)};
        const bundle = extractorApi.extractSurfaceBundleFromHtml(html);
        process.stdout.write(JSON.stringify({{
          version: extractorApi.contractVersion,
          valid: bundle.valid,
          surfaceValid: bundle.validation.surface.valid,
          layoutValid: bundle.validation.layout.valid,
          selectedSurface: bundle.extraction.selectedSurface,
          nodeIds: bundle.surfaceIR.graph.nodes.map((node) => node.id),
          edgeIds: bundle.surfaceIR.graph.edges.map((edge) => edge.id),
          regionIds: bundle.surfaceIR.graph.regions.map((region) => region.id),
          controlIds: bundle.surfaceIR.graph.controls.map((control) => control.id),
          viewport: bundle.layoutGrammar.viewport,
          layoutNodeIds: bundle.layoutGrammar.nodes.map((node) => node.id),
          layoutEdgeRouteKinds: bundle.layoutGrammar.edges.map((edge) => edge.routeKind)
        }}));
        """
    )

    result = run_node_json(script)

    assert result["version"] == "mcel.surface-extractors.v1"
    assert result["valid"] is True
    assert result["surfaceValid"] is True
    assert result["layoutValid"] is True
    assert result["selectedSurface"]["id"] == "surface.demo-neutral"
    assert result["selectedSurface"]["authoritative"] is True
    assert result["nodeIds"] == ["Hypothesis.B", "Observation.A"]
    assert result["edgeIds"] == ["EDGE.observation-supports-hypothesis"]
    assert result["regionIds"] == ["region.workbench"]
    assert result["controlIds"] == ["trace_evidence"]
    assert result["viewport"] == {"width": 900, "height": 520, "safeMargin": 24}
    assert result["layoutNodeIds"] == ["Hypothesis.B", "Observation.A"]
    assert result["layoutEdgeRouteKinds"] == ["cubic"]


def test_svg_extraction_recovers_equivalent_semantic_surface_and_layout() -> None:
    script = load_api_script(
        f"""
        const html = {json.dumps(HTML_SURFACE)};
        const svg = {json.dumps(SVG_SURFACE)};
        const htmlBundle = extractorApi.extractSurfaceBundleFromHtml(html);
        const svgBundle = extractorApi.extractSurfaceBundleFromSvg(svg);
        process.stdout.write(JSON.stringify({{
          htmlValid: htmlBundle.valid,
          svgValid: svgBundle.valid,
          sameSemantic: JSON.stringify(htmlBundle.surfaceIR.graph) === JSON.stringify(svgBundle.surfaceIR.graph),
          sameLayout: layoutApi.layoutFingerprint(htmlBundle.layoutGrammar) === layoutApi.layoutFingerprint(svgBundle.layoutGrammar),
          svgProjection: svgBundle.surfaceIR.surface.projection,
          svgViewport: svgBundle.layoutGrammar.viewport
        }}));
        """
    )

    result = run_node_json(script)

    assert result["htmlValid"] is True
    assert result["svgValid"] is True
    assert result["sameSemantic"] is True
    assert result["sameLayout"] is True
    assert result["svgProjection"] == "svg"
    assert result["svgViewport"] == {"width": 900, "height": 520, "safeMargin": 24}


def test_authoritative_surface_extraction_ignores_hidden_non_authoritative_surfaces() -> None:
    noisy = f"""
    <main>
      <section data-mcel-surface-id="surface.hidden-proof" data-mcel-surface-kind="semantic-surface" hidden>
        <article data-mcel-node-id="Noise.X" data-mcel-node-type="noise" data-mcel-source="hidden" data-mcel-provenance="hidden:noise"></article>
      </section>
      {HTML_SURFACE}
    </main>
    """
    script = load_api_script(
        f"""
        const noisy = {json.dumps(noisy)};
        const bundle = extractorApi.extractSurfaceBundleFromHtml(noisy);
        process.stdout.write(JSON.stringify({{
          valid: bundle.valid,
          surfaceId: bundle.surfaceIR.surface.id,
          nodeIds: bundle.surfaceIR.graph.nodes.map((node) => node.id),
          selectedSurface: bundle.extraction.selectedSurface
        }}));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is True
    assert result["surfaceId"] == "surface.demo-neutral"
    assert result["selectedSurface"]["authoritative"] is True
    assert result["nodeIds"] == ["Hypothesis.B", "Observation.A"]


def test_missing_required_ridge_fields_surface_as_structured_diagnostics() -> None:
    broken = """
    <section data-mcel-surface-id="surface.broken" data-mcel-surface-kind="semantic-surface" data-mcel-authoritative="true" data-layout-viewport-width="500" data-layout-viewport-height="300">
      <section data-mcel-region="region.workbench" data-mcel-region-role="workbench"></section>
      <article data-mcel-node-id="Observation.A" data-mcel-node-type="observation" data-mcel-home-region="region.workbench" data-layout-anchor-x="120" data-layout-anchor-y="120" data-layout-width="120" data-layout-height="60" data-layout-region="region.workbench"></article>
    </section>
    """
    script = load_api_script(
        f"""
        const broken = {json.dumps(broken)};
        const bundle = extractorApi.extractSurfaceBundleFromHtml(broken);
        process.stdout.write(JSON.stringify({{
          valid: bundle.valid,
          surfaceErrorCodes: bundle.validation.surface.diagnostics.map((item) => item.code),
          allCodes: bundle.diagnostics.map((item) => item.code)
        }}));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert "missing-node-source" in result["surfaceErrorCodes"]
    assert "missing-node-provenance" in result["surfaceErrorCodes"]


def test_duplicate_ids_are_rejected_after_extraction() -> None:
    duplicate = HTML_SURFACE.replace(
        "</section>",
        '<article data-mcel-node-id="Observation.A" data-mcel-node-type="observation" data-mcel-source="fixture" data-mcel-provenance="fixture:duplicate" data-mcel-home-region="region.workbench" data-layout-anchor-x="460" data-layout-anchor-y="140" data-layout-width="120" data-layout-height="60" data-layout-region="region.workbench"></article></section>',
        1,
    )
    script = load_api_script(
        f"""
        const duplicate = {json.dumps(duplicate)};
        const bundle = extractorApi.extractSurfaceBundleFromHtml(duplicate);
        process.stdout.write(JSON.stringify({{
          valid: bundle.valid,
          codes: bundle.validation.surface.diagnostics.map((item) => item.code)
        }}));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert "duplicate-node-id" in result["codes"]


def test_requested_missing_surface_reports_selection_diagnostic() -> None:
    script = load_api_script(
        f"""
        const html = {json.dumps(HTML_SURFACE)};
        const bundle = extractorApi.extractSurfaceBundleFromHtml(html, {{surfaceId: "surface.not-present"}});
        process.stdout.write(JSON.stringify({{
          valid: bundle.valid,
          selectedSurface: bundle.extraction.selectedSurface,
          extractionCodes: bundle.extraction.diagnostics.map((item) => item.code),
          allCodes: bundle.diagnostics.map((item) => item.code)
        }}));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert result["selectedSurface"] is None
    assert "surface-id-not-found" in result["extractionCodes"]
