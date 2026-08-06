from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CALCULATOR_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "calculator.js"
CAPABILITIES_JS = ROOT / "main_computer" / "web" / "applications" / "scripts" / "calculator-capabilities.js"
CALCULATOR_HTML = ROOT / "main_computer" / "web" / "applications" / "apps" / "calculator.html"
CALCULATOR_ROUTES = ROOT / "main_computer" / "viewport_routes_calculator.py"


def _javascript_function(source: str, name: str) -> str:
    match = re.search(rf"\b(?:async\s+)?function\s+{re.escape(name)}\s*\(", source)
    assert match, f"missing JavaScript function: {name}"

    paren = source.find("(", match.start())
    assert paren >= 0, f"missing parameter list for JavaScript function: {name}"
    paren_depth = 0
    quote = ""
    escaped = False
    index = paren
    while index < len(source):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in {'"', "'", "`"}:
            quote = char
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth -= 1
            if paren_depth == 0:
                break
        index += 1
    opening = source.find("{", index + 1)
    assert opening >= 0, f"missing body for JavaScript function: {name}"

    depth = 0
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    index = opening
    while index < len(source):
        char = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if line_comment:
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and following == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and following == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and following == "*":
            block_comment = True
            index += 2
            continue
        if char in {'"', "'", "`"}:
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[match.start(): index + 1]
        index += 1

    raise AssertionError(f"unterminated JavaScript function: {name}")


def test_calculator_basic_evaluate_and_graph_are_local_only() -> None:
    source = CALCULATOR_JS.read_text(encoding="utf-8")
    arithmetic = _javascript_function(source, "evaluateCalculatorArithmeticExpression")
    calculate = _javascript_function(source, "calculateExpression")
    graph = _javascript_function(source, "drawCalculatorGraph")

    assert "evaluateCalculatorArithmeticExpression" in calculate
    assert "sampleCalculatorGraphExpression" in graph
    assert "calculatorGraphCanvas.getContext" in graph

    for body in (arithmetic, calculate, graph):
        lowered = body.lower()
        assert "fetch(" not in lowered
        assert "/api/" not in lowered
        assert "provider" not in lowered
        assert ".chat(" not in lowered
        assert "git" not in lowered
        assert "revision" not in lowered
        assert "checkpoint" not in lowered


def test_calculator_model_and_mathics_actions_have_non_mutating_boundaries() -> None:
    source = CALCULATOR_JS.read_text(encoding="utf-8")
    capabilities = CAPABILITIES_JS.read_text(encoding="utf-8")
    html = CALCULATOR_HTML.read_text(encoding="utf-8")
    routes = CALCULATOR_ROUTES.read_text(encoding="utf-8")

    ask_model = _javascript_function(source, "askCalculatorModel")
    ask_mathics = _javascript_function(source, "askCalculatorMathicsModel")
    evaluate_mathics = _javascript_function(source, "evaluateCalculatorMathics")

    assert "requireCalculatorCapabilities().askArithmeticModel" in ask_model
    assert "requireCalculatorCapabilities().askMathicsModel" in ask_mathics
    assert "requireCalculatorCapabilities().evaluateMathics" in evaluate_mathics
    assert "/api/chat" in capabilities
    assert "/api/applications/calculator/mathics/ask" in capabilities
    assert "/api/applications/calculator/mathics/evaluate" in capabilities
    assert "/api/applications/calculator/qa" in capabilities
    assert "fetch(" not in source

    forbidden_frontend_tokens = (
        "/api/applications/files",
        "/api/applications/git",
        "/api/git",
        "writefile",
        "commit",
        "checkpoint",
        "revision",
        "/terminal",
    )
    for body in (ask_model, ask_mathics, evaluate_mathics, capabilities):
        lowered = body.lower()
        for token in forbidden_frontend_tokens:
            assert token not in lowered

    lowered_source = source.lower()
    lowered_routes = routes.lower()
    for token in ("git commit", "git push", "revision checkpoint", "write_text(", "write_bytes("):
        assert token not in lowered_source
        assert token not in lowered_routes

    assert "evaluate_mathics_expression" in routes
    assert "subprocess" not in lowered_routes
    assert "terminal" not in lowered_routes
    assert "Mathics" in html
    assert "terminal execution" not in html.lower()
