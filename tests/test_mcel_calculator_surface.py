from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from main_computer.mcel_application_virtual_assets import read_virtual_mcel_browser_asset


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "main_computer" / "web" / "applications"
SCRIPTS = WEB / "scripts"
APP_SHELL = ROOT / "main_computer" / "web" / "applications.html"
CALCULATOR_HTML = WEB / "apps" / "calculator.html"


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


def load_surface_stack(body: str) -> str:
    names = [
        "mcel-semantic-surface-ridges.js",
        "mcel-semantic-surface-ir.js",
        "mcel-shared-layout-grammar.js",
        "mcel-surface-extractors.js",
        "mcel-surface-fit-contract.js",
    ]
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
        {body}
        """
    )


def test_calculator_legacy_static_surface_module_is_retired_from_the_viewport() -> None:
    shell = APP_SHELL.read_text(encoding="utf-8")
    assert not (SCRIPTS / "mcel-calculator-surface.js").exists()
    assert "mcel-calculator-surface.js" not in shell


def test_calculator_static_markup_still_extracts_as_valid_surface_and_layout() -> None:
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


def test_calculator_generated_surface_contract_replaces_static_surface_module() -> None:
    surface = read_virtual_mcel_browser_asset(
        ROOT,
        "applications/mcel-packages/calculator/contracts/surface.js",
    ).decode("utf-8")
    assert "export const CalculatorSurface" in surface
    assert '"route": "/applications/calculator"' in surface
    assert '"rootSelector": "#calculator-app"' in surface
    assert '"presentationAuthority": "existing-host-html"' in surface
    assert "surface:calculator.workspace" in surface


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
