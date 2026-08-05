from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from main_computer.mcel_node_runtime import resolve_node_executable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
CORE = SCRIPTS / "calculator-core.js"
CALCULATOR = SCRIPTS / "calculator.js"
SHELL = ROOT / "main_computer" / "web" / "applications.html"
BLUEPRINTS = SCRIPTS / "mcel-app-blueprints-core.js"


def run_node_json(body: str) -> dict:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; Calculator deterministic-core tests cannot run")
    script = textwrap.dedent(
        f"""
        const core = require({json.dumps(str(CORE))});
        {body}
        """
    )
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_calculator_core_loads_before_runtime_and_is_registered_as_source() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    blueprints = BLUEPRINTS.read_text(encoding="utf-8")
    core_include = "<!-- @include applications/scripts/calculator-core.js -->"
    runtime_include = "<!-- @include applications/scripts/calculator.js -->"

    assert CORE.exists()
    assert core_include in shell
    assert runtime_include in shell
    assert shell.index(core_include) < shell.index(runtime_include)
    assert "main_computer/web/applications/scripts/calculator-core.js" in blueprints


def test_calculator_runtime_delegates_local_math_to_parser_owned_core() -> None:
    core_source = CORE.read_text(encoding="utf-8")
    runtime_source = CALCULATOR.read_text(encoding="utf-8")

    assert "window.MainComputerCalculatorCore" in runtime_source
    assert "requireCalculatorCore().evaluateCalculatorArithmeticExpression" in runtime_source
    assert "requireCalculatorCore().compileGraphExpression" in runtime_source
    assert "window.MainComputerCalculatorRuntime" in runtime_source

    combined = core_source + "\n" + runtime_source
    assert "Function(" not in combined
    assert "eval(" not in combined
    assert "new Function" not in combined


def test_arithmetic_parser_preserves_calculator_precedence_and_evidence() -> None:
    result = run_node_json(
        """
        const expressions = {
          addition: core.evaluateCalculatorArithmeticExpression("2+2"),
          precedence: core.evaluateCalculatorArithmeticExpression("2+3*4"),
          grouping: core.evaluateCalculatorArithmeticExpression("(2+3)*4"),
          unary: core.evaluateCalculatorArithmeticExpression("-2*3"),
          modulo: core.evaluateCalculatorArithmeticExpression("5%2"),
          multiplyAlias: core.evaluateCalculatorArithmeticExpression("2 x 3"),
          decimal: core.evaluateCalculatorArithmeticExpression(".5+1.25")
        };
        process.stdout.write(JSON.stringify({
          schema: core.schema,
          grammar: core.arithmeticGrammar,
          expressions
        }));
        """
    )

    assert result["schema"] == "main-computer-calculator-core-v1"
    assert result["grammar"] == "calculator-arithmetic-expression-v1"
    expected = {
        "addition": 4,
        "precedence": 14,
        "grouping": 20,
        "unary": -6,
        "modulo": 1,
        "multiplyAlias": 6,
        "decimal": 1.75,
    }
    for name, value in expected.items():
        evaluation = result["expressions"][name]
        assert evaluation["ok"] is True
        assert evaluation["parseStatus"] == "valid"
        assert evaluation["grammar"] == "calculator-arithmetic-expression-v1"
        assert evaluation["parserCode"] == ""
        assert evaluation["tokenCount"] > 0
        assert evaluation["value"] == value

    assert result["expressions"]["multiplyAlias"]["rawExpression"] == "2 x 3"
    assert result["expressions"]["multiplyAlias"]["normalizedExpression"] == "2*3"


def test_arithmetic_parser_rejects_javascript_and_reports_bounded_failures() -> None:
    result = run_node_json(
        """
        const payloads = [
          "globalThis.process.exit()",
          "Math.max(1,2)",
          "constructor.constructor('return process')()",
          "1;2",
          "a=1",
          "import('fs')",
          "2**3",
          "1e3"
        ];
        const rejected = payloads.map((expression) => ({
          expression,
          result: core.evaluateCalculatorArithmeticExpression(expression)
        }));
        const nonFinite = core.evaluateCalculatorArithmeticExpression("1/0");
        const blank = core.evaluateCalculatorArithmeticExpression("");
        process.stdout.write(JSON.stringify({rejected, nonFinite, blank}));
        """
    )

    for entry in result["rejected"]:
        evaluation = entry["result"]
        assert evaluation["ok"] is False
        assert evaluation["parseStatus"] == "invalid"
        assert evaluation["parserCode"] in {
            "unsupported-token",
            "unexpected-token",
            "expected-token",
            "incomplete-expression",
        }
        assert isinstance(evaluation["errorPosition"], int)
        assert evaluation["error"]

    assert result["nonFinite"]["ok"] is False
    assert result["nonFinite"]["parseStatus"] == "valid"
    assert result["nonFinite"]["parserCode"] == "result-not-finite"
    assert result["nonFinite"]["error"] == "result is not finite"

    assert result["blank"]["ok"] is False
    assert result["blank"]["parseStatus"] == "invalid"
    assert result["blank"]["parserCode"] == "expression-required"


def test_graph_parser_remains_deterministic_and_dom_independent() -> None:
    result = run_node_json(
        """
        const first = core.compileGraphExpression("sin(x) + x^2");
        const second = core.compileGraphExpression("sin(x) + x^2");
        const samples = [-2, -1, 0, 1, 2].map((x) => ({
          x,
          first: first(x),
          second: second(x)
        }));
        let unsupported;
        try {
          core.compileGraphExpression("window.alert(x)");
        } catch (error) {
          unsupported = {
            name: error.name,
            code: error.code,
            position: error.position,
            message: error.message
          };
        }
        process.stdout.write(JSON.stringify({
          expression: first.expression,
          grammar: first.grammar,
          parseStatus: first.parseStatus,
          tokenCount: first.tokenCount,
          samples,
          unsupported
        }));
        """
    )

    assert result["expression"] == "sin(x)+x^2"
    assert result["grammar"] == "calculator-graph-expression-v1"
    assert result["parseStatus"] == "valid"
    assert result["tokenCount"] > 0
    assert all(sample["first"] == sample["second"] for sample in result["samples"])
    assert result["unsupported"]["name"] == "CalculatorExpressionError"
    assert result["unsupported"]["code"] == "unsupported-name"
    assert result["unsupported"]["position"] == 0
