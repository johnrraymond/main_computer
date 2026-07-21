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
DOC = ROOT / "pretty_docs" / "mcel-shared-layout-grammar.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; shared layout grammar smoke test cannot run")
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
        const ridges = sandbox.McelSemanticSurfaceRidges;
        const irApi = sandbox.McelSemanticSurfaceIR;
        const layoutApi = sandbox.McelSharedLayoutGrammar;
        {body}
        """
    )


def test_shared_layout_grammar_contract_is_domain_neutral() -> None:
    source = LAYOUT.read_text(encoding="utf-8")

    assert "mcel.shared-layout-grammar.v1" in source
    assert "SemanticSurfaceIR" in source
    assert "viewport" in source
    assert "routeKind" in source
    assert "fromPort" in source
    assert "toPort" in source

    forbidden_domain_terms = [
        "BIO_HEALTH",
        "SYS_HEALTH",
        "heart_rate",
        "elephant",
        "health_app",
    ]
    for term in forbidden_domain_terms:
        assert term not in source


def test_neutral_demo_layout_grammar_validates() -> None:
    script = load_api_script(
        """
        const result = layoutApi.buildNeutralDemoLayoutGrammar();
        const grammar = result.grammar;
        const report = layoutApi.validateSharedLayoutGrammar(irApi.buildNeutralDemoSurfaceIR(), grammar);
        process.stdout.write(JSON.stringify({
          version: layoutApi.contractVersion,
          valid: report.valid,
          errors: report.errorCount,
          warnings: report.warningCount,
          surfaceId: grammar.surfaceId,
          viewport: grammar.viewport,
          regionIds: grammar.regions.map((region) => region.id),
          nodeIds: grammar.nodes.map((node) => node.id),
          edgeIds: grammar.edges.map((edge) => edge.id),
          controlIds: grammar.controls.map((control) => control.id),
          routeKinds: grammar.edges.map((edge) => edge.routeKind),
          fingerprintStable: layoutApi.layoutFingerprint(grammar) === layoutApi.layoutFingerprint(layoutApi.canonicalizeLayoutGrammar(grammar))
        }));
        """
    )

    result = run_node_json(script)

    assert result["version"] == "mcel.shared-layout-grammar.v1"
    assert result["valid"] is True
    assert result["errors"] == 0
    assert result["warnings"] == 0
    assert result["surfaceId"] == "surface.demo-neutral"
    assert result["viewport"] == {"width": 900, "height": 520, "safeMargin": 24}
    assert result["regionIds"] == ["region.workbench"]
    assert result["nodeIds"] == ["Hypothesis.B", "Observation.A"]
    assert result["edgeIds"] == ["EDGE.observation-supports-hypothesis"]
    assert result["controlIds"] == ["trace_evidence"]
    assert result["routeKinds"] == ["cubic"]
    assert result["fingerprintStable"] is True


def test_layout_grammar_builds_from_surface_ir_and_checks_ports() -> None:
    script = load_api_script(
        """
        const ir = irApi.buildNeutralDemoSurfaceIR();
        const result = layoutApi.buildSharedLayoutGrammar(ir, {
          viewport: {width: 900, height: 520, safeMargin: 24},
          regions: [{id: "region.workbench", role: "workbench", x: 60, y: 80, width: 780, height: 360}],
          nodePorts: {
            "Observation.A": ["east"],
            "Hypothesis.B": ["west"]
          }
        });
        process.stdout.write(JSON.stringify({
          valid: result.valid,
          diagnostics: result.diagnostics.map((item) => item.code),
          edge: result.grammar.edges[0]
        }));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is True
    assert result["diagnostics"] == []
    assert result["edge"]["from"] == "Observation.A"
    assert result["edge"]["to"] == "Hypothesis.B"
    assert result["edge"]["fromPort"] == "east"
    assert result["edge"]["toPort"] == "west"


def test_layout_grammar_catches_missing_or_orphan_layout_records() -> None:
    script = load_api_script(
        """
        const ir = JSON.parse(JSON.stringify(irApi.buildNeutralDemoSurfaceIR()));
        ir.layout.nodes = ir.layout.nodes.filter((node) => node.id !== "Observation.A");
        ir.layout.nodes.push({
          id: "Ghost.Node",
          anchorX: 200,
          anchorY: 200,
          width: 140,
          height: 70,
          region: "region.workbench"
        });
        ir.layout.controls = [];
        const result = layoutApi.buildSharedLayoutGrammar(ir, {
          viewport: {width: 900, height: 520, safeMargin: 24},
          regions: [{id: "region.workbench", role: "workbench", x: 60, y: 80, width: 780, height: 360}]
        });
        process.stdout.write(JSON.stringify({
          valid: result.valid,
          codes: result.diagnostics.map((item) => item.code).sort()
        }));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert "layout-node-missing" in result["codes"]
    assert "layout-node-orphan" in result["codes"]
    assert "layout-control-missing" in result["codes"]


def test_layout_grammar_rejects_center_routes_missing_ports_and_bad_route_kind() -> None:
    script = load_api_script(
        """
        const ir = JSON.parse(JSON.stringify(irApi.buildNeutralDemoSurfaceIR()));
        ir.layout.edges[0].routeKind = "telepathy";
        ir.layout.edges[0].fromPort = "center";
        ir.layout.edges[0].toPort = "";
        const result = layoutApi.buildSharedLayoutGrammar(ir, {
          viewport: {width: 900, height: 520, safeMargin: 24},
          regions: [{id: "region.workbench", role: "workbench", x: 60, y: 80, width: 780, height: 360}]
        });
        process.stdout.write(JSON.stringify({
          valid: result.valid,
          codes: result.diagnostics.map((item) => item.code).sort()
        }));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert "layout-edge-route-kind-invalid" in result["codes"]
    assert "layout-edge-center-from-port-forbidden" in result["codes"]
    assert "layout-edge-to-port-missing" in result["codes"]


def test_layout_grammar_catches_viewport_region_and_collision_failures() -> None:
    script = load_api_script(
        """
        const ir = JSON.parse(JSON.stringify(irApi.buildNeutralDemoSurfaceIR()));
        ir.layout.nodes[0].anchorX = 12;
        ir.layout.nodes[1].anchorX = 12;
        ir.layout.nodes[1].anchorY = ir.layout.nodes[0].anchorY;
        const result = layoutApi.buildSharedLayoutGrammar(ir, {
          viewport: {width: 900, height: 520, safeMargin: 24},
          regions: [{id: "region.workbench", role: "workbench", x: 60, y: 80, width: 780, height: 360}]
        });
        process.stdout.write(JSON.stringify({
          valid: result.valid,
          codes: result.diagnostics.map((item) => item.code).sort()
        }));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert "layout-node-outside-viewport" in result["codes"]
    assert "layout-node-outside-region" in result["codes"]
    assert "layout-node-collision" in result["codes"]


def test_layout_grammar_requires_teleport_flag_for_actual_region_drift() -> None:
    script = load_api_script(
        """
        const ir = JSON.parse(JSON.stringify(irApi.buildNeutralDemoSurfaceIR()));
        ir.graph.regions.push({id: "region.overlay", role: "overlay"});
        ir.graph.nodes[0].actualRegion = "region.overlay";
        ir.graph.nodes[0].teleported = false;
        const result = layoutApi.buildSharedLayoutGrammar(ir, {
          viewport: {width: 900, height: 520, safeMargin: 24},
          regions: [
            {id: "region.workbench", role: "workbench", x: 60, y: 80, width: 780, height: 360},
            {id: "region.overlay", role: "overlay", x: 60, y: 80, width: 780, height: 360}
          ]
        });
        process.stdout.write(JSON.stringify({
          valid: result.valid,
          codes: result.diagnostics.map((item) => item.code).sort()
        }));
        """
    )

    result = run_node_json(script)

    assert result["valid"] is False
    assert "layout-node-actual-region-without-teleport" in result["codes"]


def test_layout_documentation_sets_the_boundary() -> None:
    doc = DOC.read_text(encoding="utf-8")

    assert "mcel.shared-layout-grammar.v1" in doc
    assert "SemanticSurfaceIR" in doc
    assert "shared layout grammar" in doc
    assert "does not define renderer appearance" in doc
    assert "does not define domain vocabulary" in doc
    assert "Health App" not in doc
