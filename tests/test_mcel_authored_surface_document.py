from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"


def run_node(script: str) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not available on PATH")
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def load_script(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


def test_authored_surface_document_module_exists_and_is_domain_neutral() -> None:
    script = load_script("mcel-authored-surface-document.js")
    assert "mcel.authored-surface-document.v1" in script
    assert "analyzeText" in script
    assert "detectDocumentKind" in script
    assert "Health" not in script
    assert "BIO_HEALTH" not in script


def test_authored_surface_document_node_api_accepts_plain_text_as_not_applicable() -> None:
    script = f"""
    const fs = require("fs");
    const vm = require("vm");
    const sandbox = {{console}};
    sandbox.window = sandbox;
    for (const name of [
      "mcel-semantic-surface-ridges.js",
      "mcel-semantic-surface-ir.js",
      "mcel-shared-layout-grammar.js",
      "mcel-surface-extractors.js",
      "mcel-authored-surface-document.js"
    ]) {{
      vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPTS))} + "/" + name, "utf8"), sandbox, {{filename: name}});
    }}
    const report = sandbox.McelAuthoredSurfaceDocument.analyzeText("const x = 1;");
    process.stdout.write(JSON.stringify({{
      status: report.status,
      applicable: report.applicable,
      containsSurfaceRidges: report.containsSurfaceRidges,
      valid: report.valid
    }}));
    """
    data = run_node(script)
    assert data == {
        "status": "not-applicable",
        "applicable": False,
        "containsSurfaceRidges": False,
        "valid": True,
    }


def test_authored_surface_document_node_api_builds_surface_from_html_ridges() -> None:
    html = """
    <main data-mcel-surface-id="demo.surface" data-mcel-surface-kind="semantic-surface" data-mcel-authoritative="true" data-layout-viewport-width="640" data-layout-viewport-height="360">
      <section data-mcel-region="region.workbench" data-mcel-region-role="workbench" data-layout-x="40" data-layout-y="40" data-layout-region-width="520" data-layout-region-height="240"></section>
      <article data-mcel-node-id="Observation.A" data-mcel-node-type="observation" data-mcel-source="demo" data-mcel-provenance="fixture" data-mcel-home-region="region.workbench" data-layout-anchor-x="120" data-layout-anchor-y="120" data-layout-width="120" data-layout-height="80"></article>
      <article data-mcel-node-id="Hypothesis.B" data-mcel-node-type="hypothesis" data-mcel-source="demo" data-mcel-provenance="fixture" data-mcel-home-region="region.workbench" data-layout-anchor-x="340" data-layout-anchor-y="120" data-layout-width="120" data-layout-height="80"></article>
      <i data-mcel-edge-id="edge.supports" data-mcel-edge-kind="SUPPORTS" data-mcel-from="Observation.A" data-mcel-to="Hypothesis.B" data-mcel-relation="evidence_for" data-layout-route-kind="cubic" data-layout-from-port="east" data-layout-to-port="west"></i>
    </main>
    """
    script = f"""
    const fs = require("fs");
    const vm = require("vm");
    const sandbox = {{console}};
    sandbox.window = sandbox;
    for (const name of [
      "mcel-semantic-surface-ridges.js",
      "mcel-semantic-surface-ir.js",
      "mcel-shared-layout-grammar.js",
      "mcel-surface-extractors.js",
      "mcel-authored-surface-document.js"
    ]) {{
      vm.runInNewContext(fs.readFileSync({json.dumps(str(SCRIPTS))} + "/" + name, "utf8"), sandbox, {{filename: name}});
    }}
    const report = sandbox.McelAuthoredSurfaceDocument.analyzeText({json.dumps(html)});
    process.stdout.write(JSON.stringify({{
      status: report.status,
      applicable: report.applicable,
      containsSurfaceRidges: report.containsSurfaceRidges,
      surfaceIrBuildable: report.surfaceIrBuildable,
      layoutGrammarBuildable: report.layoutGrammarBuildable,
      surfaceId: report.surfaceIR && report.surfaceIR.surface && report.surfaceIR.surface.id
    }}));
    """
    data = run_node(script)
    assert data["status"] == "pass"
    assert data["applicable"] is True
    assert data["containsSurfaceRidges"] is True
    assert data["surfaceIrBuildable"] is True
    assert data["layoutGrammarBuildable"] is True
    assert data["surfaceId"] == "demo.surface"
