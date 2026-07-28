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
CALCULATOR_HTML = WEB / "apps" / "calculator.html"
SURFACE_JS = SCRIPTS / "mcel-calculator-surface.js"
REGISTRY_JS = SCRIPTS / "mcel-app-surface-registry.js"
CONFORMANCE_JS = SCRIPTS / "mcel-app-surface-conformance.js"
DOC = ROOT / "pretty_docs" / "mcel-calculator-surface.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; Calculator MCEL surface smoke test cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_surface_stack(body: str, *, include_policy: bool = False) -> str:
    names = [
        "mcel-semantic-surface-ridges.js",
        "mcel-semantic-surface-ir.js",
        "mcel-shared-layout-grammar.js",
        "mcel-surface-extractors.js",
        "mcel-surface-fit-contract.js",
        "mcel-calculator-surface.js",
    ]
    if include_policy:
        names.extend(
            [
                "mcel-app-surface-registry.js",
                "mcel-app-surface-conformance.js",
            ]
        )
    return textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        for (const name of {json.dumps(names)}) {{
          vm.runInNewContext(
            fs.readFileSync({json.dumps(str(SCRIPTS))} + "/" + name, "utf8"),
            sandbox,
            {{filename: name}}
          );
        }}
        const surface = sandbox.McelCalculatorSurface;
        {body}
        """
    )


def healthy_runtime_report() -> dict:
    return {
        "schema": "mcel-self-diagnosis-report-v2",
        "version": "mcel-self-diagnosis-v2",
        "appId": "calculator",
        "verdict": "pass",
        "summary": {
            "critical": 0,
            "warning": 0,
            "info": 0,
            "primarySurface": {
                "expected": "calculator.surface.workspace",
                "usable": True,
                "exactlyOneAuthoritativeSurface": True,
                "host": {
                    "exists": True,
                    "visible": True,
                    "selector": ".calculator-workspace",
                    "width": 1200,
                    "height": 700,
                },
            },
        },
        "findings": [],
        "measurements": {
            "viewport": {"width": 1440, "height": 900},
            "requiredRegions": {},
            "surfaces": {
                "primaryHost": {
                    "exists": True,
                    "visible": True,
                    "selector": ".calculator-workspace",
                    "width": 1200,
                    "height": 700,
                }
            },
            "layoutCollisions": [],
            "contentFitViolations": [],
            "visualIntegrityViolations": [],
        },
        "contract": {
            "primarySurface": {
                "id": "calculator.surface.workspace",
                "minWidth": 420,
                "minHeight": 320,
            }
        },
    }


def test_calculator_surface_is_wired_before_registry_and_diagnostics() -> None:
    assert SURFACE_JS.exists()
    assert DOC.exists()

    shell = APP_SHELL.read_text(encoding="utf-8")
    assert "mcel-calculator-surface.js" in shell
    assert shell.index("mcel-surface-fit-contract.js") < shell.index("mcel-calculator-surface.js")
    assert shell.index("mcel-calculator-surface.js") < shell.index("mcel-app-surface-registry.js")
    assert shell.index("mcel-calculator-surface.js") < shell.index("mcel-self-diagnosis.js")


def test_calculator_static_markup_extracts_as_valid_surface_and_layout() -> None:
    html = CALCULATOR_HTML.read_text(encoding="utf-8")
    script = load_surface_stack(
        f"""
        const html = {json.dumps(html)};
        const bundle = sandbox.McelSurfaceExtractors.extractSurfaceBundleFromHtml(html, {{
          surfaceId: "calculator.surface.workspace"
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
    assert data["surfaceId"] == "calculator.surface.workspace"
    assert data["diagnostics"] == []
    assert data["nodeIds"] == [
        "calculator.node.arithmetic-lane",
        "calculator.node.chat-context",
        "calculator.node.graphing-lane",
        "calculator.node.mathics-lane",
        "calculator.node.mode-state",
        "calculator.node.model-helper-lane",
        "calculator.node.result-question-lane",
    ]
    assert data["regionIds"] == [
        "calculator.region.arithmetic",
        "calculator.region.chat",
        "calculator.region.graphing",
        "calculator.region.mathics",
        "calculator.region.mode-switch",
    ]
    assert len(data["edgeIds"]) == 7
    assert len(data["controlIds"]) == 10


def test_calculator_surface_contract_builds_reusable_ir_and_layout() -> None:
    script = load_surface_stack(
        """
        const records = surface.buildStaticSurfaceRidgeRecords();
        const irResult = sandbox.McelSemanticSurfaceIR.buildSurfaceIRFromRidges(
          records,
          {requireSurface: true}
        );
        const regions = records
          .filter((record) => record["data-mcel-region"])
          .map((record) => ({
            id: record["data-mcel-region"],
            role: record["data-mcel-region-role"],
            x: Number(record["data-layout-x"]),
            y: Number(record["data-layout-y"]),
            width: Number(record["data-layout-region-width"]),
            height: Number(record["data-layout-region-height"])
          }));
        const nodePorts = Object.fromEntries(
          records
            .filter((record) => record["data-mcel-node-id"])
            .map((record) => [
              record["data-mcel-node-id"],
              ["north", "south", "east", "west"]
            ])
        );
        const layoutResult = sandbox.McelSharedLayoutGrammar.buildSharedLayoutGrammar(
          irResult.ir,
          {
            viewport: {width: 1440, height: 900, safeMargin: 16},
            regions,
            nodePorts
          }
        );
        process.stdout.write(JSON.stringify({
          recordCount: records.length,
          irValid: irResult.valid,
          layoutValid: layoutResult.valid,
          nodeCount: irResult.ir.graph.nodes.length,
          edgeCount: irResult.ir.graph.edges.length,
          controlCount: irResult.ir.graph.controls.length,
          diagnostics: layoutResult.diagnostics.map((item) => item.code)
        }));
        """
    )
    data = run_node_json(script)

    assert data == {
        "recordCount": 30,
        "irValid": True,
        "layoutValid": True,
        "nodeCount": 7,
        "edgeCount": 7,
        "controlCount": 10,
        "diagnostics": [],
    }


def test_dynamic_calculator_outputs_remain_content_not_static_layout_nodes() -> None:
    html = CALCULATOR_HTML.read_text(encoding="utf-8")
    for output_id in [
        "calculator-result",
        "calculator-model-status",
        "calculator-qa-answer",
        "calculator-graph-canvas",
        "calculator-graph-status",
        "calculator-mathics-output",
        "calculator-chat-notebook",
    ]:
        tag = html.split(f'id="{output_id}"', 1)[1].split(">", 1)[0]
        assert "data-mcel-fit-policy=" in tag
        assert "data-mcel-node-id=" not in tag

    assert 'id="calculator-graph-canvas"' in html
    graph_tag = html.split('id="calculator-graph-canvas"', 1)[1].split(">", 1)[0]
    assert 'data-mcel-fit-policy="decorative"' in graph_tag


def test_calculator_registry_policy_requires_all_five_conformance_layers() -> None:
    html = CALCULATOR_HTML.read_text(encoding="utf-8")
    report = healthy_runtime_report()
    script = load_surface_stack(
        f"""
        const result = sandbox.McelAppSurfaceConformance.evaluateAppSurfaceConformance({{
          appId: "calculator",
          surfaceHtml: {json.dumps(html)},
          report: {json.dumps(report)}
        }});
        process.stdout.write(JSON.stringify({{
          status: result.status,
          valid: result.valid,
          maturity: result.registryPolicy.maturity,
          requiredLayerIds: result.requiredLayerIds,
          policyFailedLayerIds: result.policyFailedLayerIds,
          policyUnavailableLayerIds: result.policyUnavailableLayerIds
        }}));
        """,
        include_policy=True,
    )
    data = run_node_json(script)

    assert data["status"] == "pass"
    assert data["valid"] is True
    assert data["maturity"] == "semantic-runtime"
    assert data["requiredLayerIds"] == [
        "semantic-surface",
        "layout-grammar",
        "runtime-ownership",
        "runtime-visual-fit",
        "diagnostic-no-throw",
    ]
    assert data["policyFailedLayerIds"] == []
    assert data["policyUnavailableLayerIds"] == []


def test_calculator_surface_contract_is_documented_without_hidden_mutation_claims() -> None:
    script = SURFACE_JS.read_text(encoding="utf-8")
    doc = DOC.read_text(encoding="utf-8")

    assert "mcel.calculator-surface.v1" in script
    assert "calculator.surface.workspace" in script
    assert "filesystem_mutation" in script
    assert "dynamic-output boundary" in doc.lower()
    assert "do not receive static `data-mcel-node-id`" in doc
