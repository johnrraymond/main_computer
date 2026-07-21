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
DOC = ROOT / "pretty_docs" / "mcel-semantic-surface-ir.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; semantic surface IR smoke test cannot run")
    completed = subprocess.run(
        ["node", "-e", script],
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
        const ridges = sandbox.McelSemanticSurfaceRidges;
        const irApi = sandbox.McelSemanticSurfaceIR;
        {body}
        """
    )


def test_semantic_surface_ir_contract_is_domain_neutral() -> None:
    source = IR.read_text(encoding="utf-8")

    assert "mcel.semantic-surface-ir.v1" in source
    assert "SurfaceIR" in source
    assert "graph" in source
    assert "nodes" in source
    assert "edges" in source
    assert "regions" in source
    assert "controls" in source

    forbidden_domain_terms = [
        "BIO_HEALTH",
        "SYS_HEALTH",
        "heart_rate",
        "elephant",
        "health_app",
    ]
    for term in forbidden_domain_terms:
        assert term not in source


def test_neutral_demo_ir_validates_and_canonicalizes() -> None:
    script = load_api_script(
        """
        const ir = irApi.buildNeutralDemoSurfaceIR();
        const report = irApi.validateSurfaceIR(ir);
        process.stdout.write(JSON.stringify({
          version: irApi.contractVersion,
          ridgeVersion: irApi.ridgeContractVersion,
          valid: report.valid,
          errors: report.errorCount,
          warnings: report.warningCount,
          surface: ir.surface,
          nodeIds: ir.graph.nodes.map((node) => node.id),
          edgeIds: ir.graph.edges.map((edge) => edge.id),
          regionIds: ir.graph.regions.map((region) => region.id),
          controlIds: ir.graph.controls.map((control) => control.id),
          layoutNodeCount: ir.layout.nodes.length,
          layoutEdgeCount: ir.layout.edges.length,
          layoutControlCount: ir.layout.controls.length
        }));
        """
    )

    result = run_node_json(script)

    assert result["version"] == "mcel.semantic-surface-ir.v1"
    assert result["ridgeVersion"] == "mcel.semantic-surface-ridges.v1"
    assert result["valid"] is True
    assert result["errors"] == 0
    assert result["warnings"] == 0
    assert result["surface"]["id"] == "surface.demo-neutral"
    assert result["nodeIds"] == ["Hypothesis.B", "Observation.A"]
    assert result["edgeIds"] == ["EDGE.observation-supports-hypothesis"]
    assert result["regionIds"] == ["region.workbench"]
    assert result["controlIds"] == ["trace_evidence"]
    assert result["layoutNodeCount"] == 2
    assert result["layoutEdgeCount"] == 1
    assert result["layoutControlCount"] == 1


def test_ir_builds_from_existing_ridge_fixture() -> None:
    script = load_api_script(
        """
        const records = ridges.buildNeutralDemoRidgeRecords();
        const result = irApi.buildSurfaceIRFromRidges(records);
        const report = irApi.validateSurfaceIR(result.ir);
        process.stdout.write(JSON.stringify({
          valid: result.valid,
          reportValid: report.valid,
          diagnostics: result.diagnostics.map((item) => item.code),
          surfaceId: result.ir.surface.id,
          nodeCount: result.ir.graph.nodes.length,
          edgeCount: result.ir.graph.edges.length,
          regionCount: result.ir.graph.regions.length,
          controlCount: result.ir.graph.controls.length,
          fingerprintStable: irApi.semanticFingerprint(result.ir) === irApi.semanticFingerprint(irApi.canonicalizeSurfaceIR(result.ir))
        }));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is True
    assert result["reportValid"] is True
    assert result["diagnostics"] == []
    assert result["surfaceId"] == "surface.demo-neutral"
    assert result["nodeCount"] == 2
    assert result["edgeCount"] == 1
    assert result["regionCount"] == 1
    assert result["controlCount"] == 1
    assert result["fingerprintStable"] is True


def test_ir_round_trip_preserves_canonical_fingerprint() -> None:
    script = load_api_script(
        """
        const ir = irApi.buildNeutralDemoSurfaceIR();
        const exported = irApi.exportSurfaceIRAsRidgeRecords(ir);
        const rebuilt = irApi.buildSurfaceIRFromRidges(exported).ir;
        process.stdout.write(JSON.stringify({
          exportedCount: exported.length,
          sameFingerprint: irApi.semanticFingerprint(ir) === irApi.semanticFingerprint(rebuilt),
          ridgeReportValid: ridges.validateSurfaceRidges(exported).valid
        }));
        """
    )

    result = run_node_json(script)

    assert result["exportedCount"] == 6
    assert result["sameFingerprint"] is True
    assert result["ridgeReportValid"] is True


def test_ir_validator_catches_broken_references_and_duplicate_ids() -> None:
    script = load_api_script(
        """
        const ir = JSON.parse(JSON.stringify(irApi.buildNeutralDemoSurfaceIR()));
        ir.graph.nodes.push(Object.assign({}, ir.graph.nodes[0]));
        ir.graph.edges[0].to = "Missing.Node";
        ir.layout.nodes[0].width = -4;
        const report = irApi.validateSurfaceIR(ir);
        process.stdout.write(JSON.stringify({
          valid: report.valid,
          codes: report.diagnostics.map((item) => item.code).sort()
        }));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert "duplicate-node-id" in result["codes"]
    assert "edge-to-node-missing" in result["codes"]
    assert "layout-node-width-invalid" in result["codes"]


def test_ir_documentation_sets_the_boundary() -> None:
    doc = DOC.read_text(encoding="utf-8")

    assert "mcel.semantic-surface-ir.v1" in doc
    assert "SemanticSurfaceIR" in doc
    assert "does not define a domain vocabulary" in doc
    assert "does not define renderer behavior" in doc
    assert "does not define the full layout grammar" in doc
    assert "Health App" not in doc
