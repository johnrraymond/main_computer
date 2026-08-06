from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from main_computer.mcel_application_virtual_assets import read_virtual_mcel_browser_asset
from main_computer.mcel_node_runtime import resolve_node_executable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer/web/applications/scripts"
SHELL = ROOT / "main_computer/web/applications.html"


def run_node_json(script: str) -> dict:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; Calculator generated-adapter tests cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_calculator_legacy_semantic_adapter_is_retired_from_the_viewport() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    assert not (SCRIPTS / "calculator-semantic-adapter.js").exists()
    assert "calculator-semantic-adapter.js" not in shell


def test_calculator_generated_adapter_declares_all_authoritative_runtime_bindings() -> None:
    adapter = read_virtual_mcel_browser_asset(
        ROOT,
        "applications/mcel-packages/calculator/contracts/adapter.js",
    ).decode("utf-8")

    assert "calculator.dsl-authoritative-adapter.v1" in adapter
    assert "MainComputerCalculatorRuntime" in adapter
    for method in [
        "switchMode",
        "enterToken",
        "clearExpression",
        "evaluateExpression",
        "drawGraph",
        "resetGraph",
        "askModelForExpression",
        "askModelForGraphExpression",
        "askModelForMathicsExpression",
        "evaluateMathics",
        "askResultQuestion",
    ]:
        assert f'"runtimeMethod": "{method}"' in adapter


def test_calculator_generated_adapter_invokes_stable_runtime_facade_methods() -> None:
    adapter = read_virtual_mcel_browser_asset(
        ROOT,
        "applications/mcel-packages/calculator/contracts/adapter.js",
    ).decode("utf-8")
    # Convert the ES module into a small CommonJS-evaluable script for this
    # contract smoke test.
    runnable = adapter.replace("export const CalculatorAdapter =", "globalThis.CalculatorAdapter =")
    script = textwrap.dedent(
        f"""
        const calls = [];
        globalThis.MainComputerCalculatorRuntime = {{
          switchMode(payload) {{ calls.push(["switchMode", payload]); return {{ok: true}}; }},
          enterToken(payload) {{ calls.push(["enterToken", payload]); return {{ok: true}}; }},
          clearExpression(payload) {{ calls.push(["clearExpression", payload]); return {{ok: true}}; }},
          evaluateExpression(payload) {{ calls.push(["evaluateExpression", payload]); return {{ok: true, value: 14}}; }},
          drawGraph(payload) {{ calls.push(["drawGraph", payload]); return {{ok: true}}; }},
          resetGraph(payload) {{ calls.push(["resetGraph", payload]); return {{ok: true}}; }},
          askModelForExpression(payload) {{ calls.push(["askModelForExpression", payload]); return Promise.resolve({{ok: true}}); }},
          askModelForGraphExpression(payload) {{ calls.push(["askModelForGraphExpression", payload]); return Promise.resolve({{ok: true}}); }},
          askModelForMathicsExpression(payload) {{ calls.push(["askModelForMathicsExpression", payload]); return Promise.resolve({{ok: true}}); }},
          evaluateMathics(payload) {{ calls.push(["evaluateMathics", payload]); return Promise.resolve({{ok: true}}); }},
          askResultQuestion(payload) {{ calls.push(["askResultQuestion", payload]); return Promise.resolve({{ok: true}}); }}
        }};
        {runnable}
        Promise.resolve(globalThis.CalculatorAdapter.invoke("evaluateExpression", {{expression: "2+3*4"}}))
          .then((result) => process.stdout.write(JSON.stringify({{result, calls}})))
          .catch((error) => {{ console.error(error && error.stack || error); process.exit(1); }});
        """
    )
    result = run_node_json(script)

    assert result["result"] == {"ok": True, "value": 14}
    assert result["calls"] == [["evaluateExpression", {"expression": "2+3*4"}]]
