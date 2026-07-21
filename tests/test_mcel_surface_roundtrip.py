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
DOC = ROOT / "pretty_docs" / "mcel-surface-roundtrip.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; surface round-trip smoke test cannot run")
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
        const irApi = sandbox.McelSemanticSurfaceIR;
        const layoutApi = sandbox.McelSharedLayoutGrammar;
        const extractorApi = sandbox.McelSurfaceExtractors;
        const roundTripApi = sandbox.McelSurfaceRoundTrip;
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


def test_surface_roundtrip_contract_is_domain_neutral() -> None:
    source = ROUNDTRIP.read_text(encoding="utf-8")

    assert "mcel.surface-roundtrip.v1" in source
    assert "verifyRenderedSurfaceRoundTrip" in source
    assert "verifyHtmlAndSvgAgree" in source
    assert "compareSemanticGraphs" in source
    assert "compareLayoutGrammars" in source

    forbidden_domain_terms = [
        "BIO_HEALTH",
        "SYS_HEALTH",
        "heart_rate",
        "elephant",
        "health_app",
    ]
    for term in forbidden_domain_terms:
        assert term not in source


def test_verify_rendered_html_roundtrip_passes_against_extracted_canonical_models() -> None:
    script = load_api_script(
        f"""
        const html = {json.dumps(HTML_SURFACE)};
        const expected = extractorApi.extractSurfaceBundleFromHtml(html);
        const report = roundTripApi.verifyRenderedSurfaceRoundTrip({{
          expectedSurfaceIr: expected.surfaceIR,
          expectedLayoutGrammar: expected.layoutGrammar,
          renderedText: html,
          surfaceKind: "html"
        }});
        const summary = roundTripApi.summarizeRoundTrip(report);
        process.stdout.write(JSON.stringify({{
          version: roundTripApi.contractVersion,
          valid: report.valid,
          status: report.status,
          summary,
          semanticSame: report.semantic.expectedFingerprint === report.semantic.actualFingerprint,
          layoutSame: report.layout.expectedFingerprint === report.layout.actualFingerprint
        }}));
        """
    )

    result = run_node_json(script)

    assert result["version"] == "mcel.surface-roundtrip.v1"
    assert result["valid"] is True
    assert result["status"] == "pass"
    assert result["summary"]["errorCount"] == 0
    assert result["semanticSame"] is True
    assert result["layoutSame"] is True


def test_verify_rendered_svg_roundtrip_passes_against_extracted_canonical_models() -> None:
    script = load_api_script(
        f"""
        const svg = {json.dumps(SVG_SURFACE)};
        const expected = extractorApi.extractSurfaceBundleFromSvg(svg);
        const report = roundTripApi.verifyRenderedSurfaceRoundTrip({{
          expectedSurfaceIr: expected.surfaceIR,
          expectedLayoutGrammar: expected.layoutGrammar,
          renderedText: svg,
          surfaceKind: "svg"
        }});
        process.stdout.write(JSON.stringify({{
          valid: report.valid,
          status: report.status,
          semanticValid: report.semantic.valid,
          layoutValid: report.layout.valid,
          codes: report.diagnostics.map((item) => item.code).sort()
        }}));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is True
    assert result["status"] == "pass"
    assert result["semanticValid"] is True
    assert result["layoutValid"] is True
    assert result["codes"] == []


def test_html_and_svg_projections_agree_even_when_renderer_labels_differ() -> None:
    script = load_api_script(
        f"""
        const html = {json.dumps(HTML_SURFACE)};
        const svg = {json.dumps(SVG_SURFACE)};
        const report = roundTripApi.verifyHtmlAndSvgAgree(html, svg);
        process.stdout.write(JSON.stringify({{
          valid: report.valid,
          status: report.status,
          semanticValid: report.semantic.valid,
          layoutValid: report.layout.valid,
          htmlRenderer: report.htmlBundle.surfaceIR.surface.renderer,
          svgRenderer: report.svgBundle.surfaceIR.surface.renderer,
          semanticFingerprintSame: report.semantic.expectedFingerprint === report.semantic.actualFingerprint,
          layoutFingerprintSame: report.layout.expectedFingerprint === report.layout.actualFingerprint
        }}));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is True
    assert result["status"] == "pass"
    assert result["semanticValid"] is True
    assert result["layoutValid"] is True
    assert result["htmlRenderer"] == "html"
    assert result["svgRenderer"] == "svg"
    assert result["semanticFingerprintSame"] is True
    assert result["layoutFingerprintSame"] is True


def test_roundtrip_reports_semantic_mismatch_with_first_difference_path() -> None:
    script = load_api_script(
        f"""
        const html = {json.dumps(HTML_SURFACE)};
        const expected = extractorApi.extractSurfaceBundleFromHtml(html);
        const broken = html.replace("Hypothesis B", "Different Hypothesis");
        const report = roundTripApi.verifyRenderedSurfaceRoundTrip({{
          expectedSurfaceIr: expected.surfaceIR,
          expectedLayoutGrammar: expected.layoutGrammar,
          renderedText: broken,
          surfaceKind: "html"
        }});
        process.stdout.write(JSON.stringify({{
          valid: report.valid,
          status: report.status,
          semanticValid: report.semantic.valid,
          layoutValid: report.layout.valid,
          codes: report.diagnostics.map((item) => item.code).sort(),
          diffs: report.semantic.diagnostics[0].detail.diffs
        }}));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert result["status"] == "fail"
    assert result["semanticValid"] is False
    assert result["layoutValid"] is True
    assert "semantic-surface-roundtrip-mismatch" in result["codes"]
    assert any("label" in item["path"] for item in result["diffs"])


def test_roundtrip_reports_layout_mismatch_with_first_difference_path() -> None:
    script = load_api_script(
        f"""
        const html = {json.dumps(HTML_SURFACE)};
        const expected = extractorApi.extractSurfaceBundleFromHtml(html);
        const broken = html.replace('data-layout-anchor-x="620"', 'data-layout-anchor-x="700"');
        const report = roundTripApi.verifyRenderedSurfaceRoundTrip({{
          expectedSurfaceIr: expected.surfaceIR,
          expectedLayoutGrammar: expected.layoutGrammar,
          renderedText: broken,
          surfaceKind: "html"
        }});
        process.stdout.write(JSON.stringify({{
          valid: report.valid,
          status: report.status,
          semanticValid: report.semantic.valid,
          layoutValid: report.layout.valid,
          codes: report.diagnostics.map((item) => item.code).sort(),
          diffs: report.layout.diagnostics[0].detail.diffs
        }}));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert result["status"] == "fail"
    assert result["semanticValid"] is True
    assert result["layoutValid"] is False
    assert "layout-grammar-roundtrip-mismatch" in result["codes"]
    assert any("anchorX" in item["path"] for item in result["diffs"])


def test_roundtrip_rejects_unknown_surface_kind() -> None:
    script = load_api_script(
        f"""
        const report = roundTripApi.verifyRenderedSurfaceRoundTrip({{
          renderedText: {json.dumps(HTML_SURFACE)},
          surfaceKind: "canvas"
        }});
        process.stdout.write(JSON.stringify({{
          valid: report.valid,
          status: report.status,
          codes: report.diagnostics.map((item) => item.code)
        }}));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert result["status"] == "fail"
    assert result["codes"] == ["roundtrip-unsupported-surface-kind"]


def test_roundtrip_documentation_sets_the_boundary() -> None:
    doc = DOC.read_text(encoding="utf-8")

    assert "mcel.surface-roundtrip.v1" in doc
    assert "SemanticSurfaceIR" in doc
    assert "SharedLayoutGrammar" in doc
    assert "SurfaceExtractors" in doc
    assert "does not define a renderer" in doc
    assert "does not define domain vocabulary" in doc
    assert "Health App" not in doc
