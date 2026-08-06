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
VIEW_MODEL = SCRIPTS / "calculator-view-model.js"
CAPABILITIES = SCRIPTS / "calculator-capabilities.js"
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
        const viewModel = require({json.dumps(str(VIEW_MODEL))});
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


def run_node_json_with_calculator_modules(body: str) -> dict:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; Calculator module tests cannot run")
    script = textwrap.dedent(
        f"""
        const core = require({json.dumps(str(CORE))});
        const viewModel = require({json.dumps(str(VIEW_MODEL))});
        const capabilities = require({json.dumps(str(CAPABILITIES))});
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


def test_calculator_core_and_capability_bridge_load_before_runtime_and_are_registered_as_sources() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    blueprints = BLUEPRINTS.read_text(encoding="utf-8")
    core_include = "<!-- @include applications/scripts/calculator-core.js -->"
    view_model_include = "<!-- @include applications/scripts/calculator-view-model.js -->"
    capabilities_include = "<!-- @include applications/scripts/calculator-capabilities.js -->"
    runtime_include = "<!-- @include applications/scripts/calculator.js -->"

    assert CORE.exists()
    assert VIEW_MODEL.exists()
    assert CAPABILITIES.exists()
    assert core_include in shell
    assert view_model_include in shell
    assert capabilities_include in shell
    assert runtime_include in shell
    assert shell.index(core_include) < shell.index(view_model_include) < shell.index(capabilities_include) < shell.index(runtime_include)
    assert "main_computer/web/applications/scripts/calculator-core.js" in blueprints
    assert "main_computer/web/applications/scripts/calculator-view-model.js" in blueprints
    assert "main_computer/web/applications/scripts/calculator-capabilities.js" in blueprints


def test_calculator_runtime_delegates_local_math_to_core_and_capabilities_to_bridge() -> None:
    core_source = CORE.read_text(encoding="utf-8")
    view_model_source = VIEW_MODEL.read_text(encoding="utf-8")
    capabilities_source = CAPABILITIES.read_text(encoding="utf-8")
    runtime_source = CALCULATOR.read_text(encoding="utf-8")

    assert "window.MainComputerCalculatorCore" in runtime_source
    assert "window.MainComputerCalculatorViewModel" in runtime_source
    assert "window.MainComputerCalculatorCapabilities" in runtime_source
    assert "requireCalculatorCore().appendCalculatorDisplayToken" in runtime_source
    assert "requireCalculatorCore().clearCalculatorDisplayExpression" in runtime_source
    assert "requireCalculatorCore().backspaceCalculatorDisplayExpression" in runtime_source
    assert "requireCalculatorCore().insertCalculatorGraphText" in runtime_source
    assert "requireCalculatorCore().clearCalculatorGraphExpression" in runtime_source
    assert "requireCalculatorCore().backspaceCalculatorGraphExpression" in runtime_source
    assert "requireCalculatorCore().evaluateCalculatorArithmeticExpression" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorVisibleResultModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorModeSwitchViewModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorSessionContextSnapshot" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorResultQaContext" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorResultQaPendingViewModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorResultQaAnswerViewModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorResultQaErrorViewModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorGraphRenderModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorMathicsModelViewModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorMathicsModelErrorViewModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorMathicsEvaluationPendingViewModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorMathicsEvaluationViewModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorMathicsEvaluationErrorViewModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorMathicsClearViewModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorAssistedExpressionViewModel" in runtime_source
    assert "requireCalculatorViewModel().buildCalculatorAssistedExpressionErrorViewModel" in runtime_source
    assert "requireCalculatorCapabilities().askArithmeticModel" in runtime_source
    assert "requireCalculatorCapabilities().askGraphModel" in runtime_source
    assert "requireCalculatorCapabilities().askMathicsModel" in runtime_source
    assert "requireCalculatorCapabilities().evaluateMathics" in runtime_source
    assert "requireCalculatorCapabilities().askResultQuestion" in runtime_source
    assert "window.MainComputerCalculatorRuntime" in runtime_source

    assert "function extractCalculatorExpression" in core_source
    assert "function extractCalculatorGraphExpression" in core_source
    assert "function buildCalculatorVisibleResultModel" in view_model_source
    assert "function buildCalculatorModeSwitchViewModel" in view_model_source
    assert "function buildCalculatorSessionContextSnapshot" in view_model_source
    assert "function buildCalculatorResultQaContext" in view_model_source
    assert "function buildCalculatorResultQaPendingViewModel" in view_model_source
    assert "function buildCalculatorResultQaAnswerViewModel" in view_model_source
    assert "function buildCalculatorResultQaErrorViewModel" in view_model_source
    assert "function buildCalculatorGraphRenderModel" in view_model_source
    assert "function buildCalculatorMathicsModelViewModel" in view_model_source
    assert "function buildCalculatorMathicsEvaluationViewModel" in view_model_source
    assert "function buildCalculatorMathicsClearViewModel" in view_model_source
    assert "function buildCalculatorAssistedExpressionViewModel" in view_model_source
    assert "function buildCalculatorAssistedExpressionErrorViewModel" in view_model_source
    assert "function normalizeCalculatorResult" in view_model_source
    assert "function buildCalculatorVisibleResultModel" not in core_source
    assert "function buildCalculatorModeSwitchViewModel" not in core_source
    assert "function buildCalculatorGraphRenderModel" not in core_source
    assert "function normalizeCalculatorResult" not in core_source
    assert "function extractCalculatorExpression" not in runtime_source
    assert "function extractCalculatorGraphExpression" not in runtime_source
    assert "requireCalculatorCore().extractCalculatorExpression" not in runtime_source
    assert "requireCalculatorCore().extractCalculatorGraphExpression" not in runtime_source
    assert "function buildCalculatorVisibleResultModel" not in runtime_source
    assert "function buildCalculatorModeSwitchViewModel" not in runtime_source
    assert "function buildCalculatorSessionContextSnapshot" not in runtime_source
    assert "function buildCalculatorResultQaContext" not in runtime_source
    assert "function buildCalculatorResultQaPendingViewModel" not in runtime_source
    assert "function buildCalculatorResultQaAnswerViewModel" not in runtime_source
    assert "function buildCalculatorResultQaErrorViewModel" not in runtime_source
    assert "function buildCalculatorGraphRenderModel" not in runtime_source
    assert "function buildCalculatorMathicsModelViewModel" not in runtime_source
    assert "function buildCalculatorMathicsEvaluationViewModel" not in runtime_source
    assert "function buildCalculatorMathicsClearViewModel" not in runtime_source
    assert "function buildCalculatorAssistedExpressionViewModel" not in runtime_source
    assert "function buildCalculatorAssistedExpressionErrorViewModel" not in runtime_source
    assert "function normalizeCalculatorResult" not in runtime_source
    assert "allowed_tools" not in runtime_source
    assert "basic_expression" not in runtime_source
    assert "sampleCalculatorGraphExpression" not in runtime_source
    assert "fetch(" not in runtime_source
    assert "/api/chat" not in runtime_source
    assert "/api/applications/calculator/" not in runtime_source

    combined = core_source + "\n" + view_model_source + "\n" + capabilities_source + "\n" + runtime_source
    assert "Function(" not in combined
    assert "eval(" not in combined
    assert "new Function" not in combined


def test_calculator_view_model_owns_mode_switch_contract() -> None:
    result = run_node_json(
        """
        const graphing = viewModel.buildCalculatorModeSwitchViewModel("graphing", {
          expression: "2+2",
          graphExpression: "sin(x)"
        });
        const basic = viewModel.buildCalculatorModeSwitchViewModel("basic", {
          expression: "3*7",
          graphExpression: "cos(x)"
        });
        const invalid = viewModel.buildCalculatorModeSwitchViewModel("unknown", {
          expression: "5",
          graphExpression: "x"
        });
        process.stdout.write(JSON.stringify({graphing, basic, invalid}));
        """
    )

    assert result["graphing"] == {
        "ok": True,
        "mode": "graphing",
        "graphing": True,
        "statusText": "ready",
        "buttons": {"basicActive": False, "graphingActive": True},
        "shell": {"graphingActive": True, "chatDocked": True, "chatActive": False},
        "panels": {
            "basicHidden": False,
            "graphingHidden": False,
            "mathicsHidden": False,
            "chatHidden": False,
        },
        "focusTarget": "graphExpression",
        "shouldDrawGraph": True,
        "shouldMountChat": True,
        "runtimeResult": {
            "ok": True,
            "mode": "graphing",
            "expression": "2+2",
            "graphExpression": "sin(x)",
            "statusText": "ready",
        },
    }
    assert result["basic"] == {
        "ok": True,
        "mode": "basic",
        "graphing": False,
        "statusText": "ready",
        "buttons": {"basicActive": True, "graphingActive": False},
        "shell": {"graphingActive": False, "chatDocked": True, "chatActive": False},
        "panels": {
            "basicHidden": False,
            "graphingHidden": True,
            "mathicsHidden": True,
            "chatHidden": False,
        },
        "focusTarget": "display",
        "shouldDrawGraph": False,
        "shouldMountChat": True,
        "runtimeResult": {
            "ok": True,
            "mode": "basic",
            "expression": "3*7",
            "graphExpression": "cos(x)",
            "statusText": "ready",
        },
    }
    assert result["invalid"]["mode"] == "basic"
    assert result["invalid"]["graphing"] is False



def test_calculator_view_model_owns_session_and_result_qa_context_shapes() -> None:
    result = run_node_json(
        """
        const session = viewModel.buildCalculatorSessionContextSnapshot({
          activeMode: "scientific-graphing",
          arithmeticExpression: "2+2",
          arithmeticResult: "4",
          arithmeticPrompt: "two plus two",
          graphExpression: "sin(x)",
          graphXMin: "-10",
          graphXMax: "10",
          graphYMin: "-5",
          graphYMax: "5",
          graphStatus: "graphed sin(x)",
          mathicsPrompt: "factor",
          mathicsExpression: "Factor[x^2-1]",
          mathicsStatus: "Mathics result ready",
          qaPrompt: "why?",
          qaStatus: "result Q&A answered"
        });
        const qa = viewModel.buildCalculatorResultQaContext({
          arithmeticExpression: "2+2",
          arithmeticResult: "4",
          graphExpression: "sin(x)",
          graphStatus: "graphed sin(x)",
          graphXMin: "-10",
          graphXMax: "10",
          graphYMin: "-5",
          graphYMax: "5",
          mathicsExpression: "Factor[x^2-1]",
          mathicsOutput: "(x - 1)(x + 1)"
        });
        process.stdout.write(JSON.stringify({session, qa}));
        """
    )

    assert result["session"] == {
        "app": "calculator",
        "target_kind": "calculator-session",
        "target_id": "calculator",
        "active_mode": "scientific-graphing",
        "arithmetic": {
            "expression": "2+2",
            "result": "4",
            "prompt": "two plus two",
        },
        "graph": {
            "expression": "sin(x)",
            "x_min": "-10",
            "x_max": "10",
            "y_min": "-5",
            "y_max": "5",
            "status": "graphed sin(x)",
        },
        "mathics": {
            "prompt": "factor",
            "expression": "Factor[x^2-1]",
            "status": "Mathics result ready",
        },
        "qa": {
            "prompt": "why?",
            "status": "result Q&A answered",
        },
        "allowed_tools": ["arithmetic", "scientific-graphing", "mathics", "calculator-qa"],
    }
    assert result["qa"] == {
        "basic_expression": "2+2",
        "basic_result": "4",
        "graph_expression": "sin(x)",
        "graph_status": "graphed sin(x)",
        "graph_range": {
            "x_min": "-10",
            "x_max": "10",
            "y_min": "-5",
            "y_max": "5",
        },
        "mathics_expression": "Factor[x^2-1]",
        "mathics_output": "(x - 1)(x + 1)",
    }


def test_calculator_view_model_owns_result_qa_answer_view_models() -> None:
    result = run_node_json(
        """
        const pending = viewModel.buildCalculatorResultQaPendingViewModel();
        const answer = viewModel.buildCalculatorResultQaAnswerViewModel(
          "Why is the graph flat?",
          {answer: "Because y is constant."}
        );
        const emptyAnswer = viewModel.buildCalculatorResultQaAnswerViewModel("Explain", {});
        const error = viewModel.buildCalculatorResultQaErrorViewModel("Explain", new Error("provider offline"));
        process.stdout.write(JSON.stringify({pending, answer, emptyAnswer, error}));
        """
    )

    assert result["pending"] == {
        "qaStatusText": "asking model about results",
        "answerText": "Asking about the current calculator context...",
        "answerState": "ready",
    }
    assert result["answer"] == {
        "ok": True,
        "question": "Why is the graph flat?",
        "answer": "Because y is constant.",
        "qaStatusText": "result Q&A answered",
        "answerText": "Because y is constant.",
        "answerState": "ready",
        "runtimeResult": {
            "ok": True,
            "question": "Why is the graph flat?",
            "answer": "Because y is constant.",
            "statusText": "ready",
        },
    }
    assert result["emptyAnswer"]["answer"] == "(no answer returned)"
    assert result["emptyAnswer"]["answerText"] == "(no answer returned)"
    assert result["error"] == {
        "ok": False,
        "question": "Explain",
        "qaStatusText": "provider offline",
        "answerText": "provider offline",
        "answerState": "error",
        "runtimeResult": {
            "ok": False,
            "question": "Explain",
            "code": "result-qa-failed",
            "error": "provider offline",
            "statusText": "error",
        },
    }


def test_calculator_view_model_owns_mathics_panel_view_models() -> None:
    result = run_node_json(
        """
        const model = viewModel.buildCalculatorMathicsModelViewModel({expression: "Factor[x^2-1]"});
        const modelError = viewModel.buildCalculatorMathicsModelErrorViewModel(new Error("provider unavailable"));
        const pending = viewModel.buildCalculatorMathicsEvaluationPendingViewModel();
        const evaluation = viewModel.buildCalculatorMathicsEvaluationViewModel("  Factor[x^2-1]  ", {output: "(x - 1)(x + 1)"});
        const emptyEvaluation = viewModel.buildCalculatorMathicsEvaluationViewModel("N[Pi]", {});
        const evaluationError = viewModel.buildCalculatorMathicsEvaluationErrorViewModel("Factor[x]", new Error("Mathics unavailable"));
        const cleared = viewModel.buildCalculatorMathicsClearViewModel();
        process.stdout.write(JSON.stringify({
          model,
          modelError,
          pending,
          evaluation,
          emptyEvaluation,
          evaluationError,
          cleared
        }));
        """
    )

    assert result["model"] == {
        "ok": True,
        "expression": "Factor[x^2-1]",
        "expressionText": "Factor[x^2-1]",
        "modelStatusText": "mathics expression: Factor[x^2-1]",
        "focusExpression": True,
        "runtimeResult": {
            "ok": True,
            "expression": "Factor[x^2-1]",
        },
    }
    assert result["modelError"] == {
        "ok": False,
        "modelStatusText": "provider unavailable",
        "runtimeResult": {
            "ok": False,
            "code": "provider-request-failed",
            "error": "provider unavailable",
        },
    }
    assert result["pending"] == {
        "evaluationStatusText": "evaluating Mathics expression",
        "outputText": "Evaluating...",
        "outputState": "ready",
    }
    assert result["evaluation"] == {
        "ok": True,
        "expression": "Factor[x^2-1]",
        "output": "(x - 1)(x + 1)",
        "evaluationStatusText": "Mathics result ready",
        "outputText": "(x - 1)(x + 1)",
        "outputState": "ready",
        "runtimeResult": {
            "ok": True,
            "expression": "Factor[x^2-1]",
            "output": "(x - 1)(x + 1)",
            "statusText": "ready",
        },
    }
    assert result["emptyEvaluation"]["output"] == "(no result)"
    assert result["emptyEvaluation"]["outputText"] == "(no result)"
    assert result["evaluationError"] == {
        "ok": False,
        "expression": "Factor[x]",
        "evaluationStatusText": "Mathics unavailable",
        "outputText": "Mathics unavailable",
        "outputState": "error",
        "runtimeResult": {
            "ok": False,
            "expression": "Factor[x]",
            "code": "mathics-evaluation-failed",
            "error": "Mathics unavailable",
            "statusText": "error",
        },
    }
    assert result["cleared"] == {
        "expressionText": "",
        "outputText": "Mathics ready.",
        "outputState": "ready",
        "evaluationStatusText": "mathics evaluation ready",
        "focusExpression": True,
    }


def test_calculator_view_model_owns_model_assisted_expression_view_models() -> None:
    result = run_node_json(
        """
        const arithmetic = viewModel.buildCalculatorAssistedExpressionViewModel(
          "arithmetic",
          {content: "Use this calculator expression: 12 x 3"}
        );
        const graph = viewModel.buildCalculatorAssistedExpressionViewModel(
          "graph",
          {content: "f(x) = sin(x) + x^2"}
        );
        const missingGraph = viewModel.buildCalculatorAssistedExpressionViewModel(
          "graph",
          {content: "Please call window.alert(secret)"}
        );
        const arithmeticError = viewModel.buildCalculatorAssistedExpressionErrorViewModel(
          "arithmetic",
          new Error("provider offline")
        );
        process.stdout.write(JSON.stringify({
          arithmetic,
          graph,
          missingGraph,
          arithmeticError
        }));
        """
    )

    assert result["arithmetic"] == {
        "ok": True,
        "kind": "arithmetic",
        "target": "arithmeticExpression",
        "expression": "12 * 3",
        "expressionText": "12 * 3",
        "statusText": "model expression: 12 * 3",
        "runtimeResult": {
            "ok": True,
            "kind": "arithmetic",
            "expression": "12 * 3",
            "statusText": "model expression: 12 * 3",
        },
    }
    assert result["graph"] == {
        "ok": True,
        "kind": "graph",
        "target": "graphExpression",
        "expression": "sin(x)+x^2",
        "expressionText": "sin(x)+x^2",
        "statusText": "f(x): sin(x)+x^2",
        "runtimeResult": {
            "ok": True,
            "kind": "graph",
            "expression": "sin(x)+x^2",
            "statusText": "f(x): sin(x)+x^2",
        },
    }
    assert result["missingGraph"]["ok"] is False
    assert result["missingGraph"]["kind"] == "graph"
    assert result["missingGraph"]["target"] == "graphExpression"
    assert result["missingGraph"]["statusText"] == "no graph expression returned"
    assert result["missingGraph"]["runtimeResult"] == {
        "ok": False,
        "code": "provider-request-failed",
        "error": "no graph expression returned",
    }
    assert result["arithmeticError"] == {
        "ok": False,
        "kind": "arithmetic",
        "target": "arithmeticExpression",
        "statusText": "provider offline",
        "runtimeResult": {
            "ok": False,
            "code": "provider-request-failed",
            "error": "provider offline",
        },
    }


def test_calculator_capability_bridge_owns_provider_and_backend_requests() -> None:
    result = run_node_json_with_calculator_modules(
        """
        const requests = [];
        const fetcher = async (path, options) => {
          requests.push({path, body: JSON.parse(options.body)});
          if (path === "/api/chat") return {ok: true, json: async () => ({content: "2+2"})};
          if (path.endsWith("/mathics/ask")) return {ok: true, json: async () => ({ok: true, expression: "Factor[x^2-1]"})};
          if (path.endsWith("/mathics/evaluate")) return {ok: true, json: async () => ({ok: true, result_text: "(x - 1)(x + 1)"})};
          if (path.endsWith("/qa")) return {ok: true, json: async () => ({ok: true, answer: "The result is 4."})};
          return {ok: false, json: async () => ({error: "unexpected path"})};
        };
        Promise.all([
          capabilities.askArithmeticModel("two plus two", {fetcher}),
          capabilities.askGraphModel("draw sine", {fetcher}),
          capabilities.askMathicsModel("factor", {fetcher}),
          capabilities.evaluateMathics("Factor[x^2-1]", {fetcher}),
          capabilities.askResultQuestion("what happened?", {result: "4"}, {fetcher})
        ]).then((responses) => {
          process.stdout.write(JSON.stringify({
            schema: capabilities.schema,
            responses,
            requests
          }));
        }).catch((error) => {
          process.stdout.write(JSON.stringify({error: error.message, requests}));
        });
        """
    )

    assert result.get("error") is None
    assert result["schema"] == "main-computer-calculator-capabilities-v1"
    assert [request["path"] for request in result["requests"]] == [
        "/api/chat",
        "/api/chat",
        "/api/applications/calculator/mathics/ask",
        "/api/applications/calculator/mathics/evaluate",
        "/api/applications/calculator/qa",
    ]
    assert "Translate this calculator word problem" in result["requests"][0]["body"]["prompt"]
    assert "Translate this graphing calculator request" in result["requests"][1]["body"]["prompt"]
    assert result["responses"][0]["content"] == "2+2"
    assert result["responses"][2]["expression"] == "Factor[x^2-1]"
    assert result["responses"][3]["output"] == "(x - 1)(x + 1)"
    assert result["responses"][4]["answer"] == "The result is 4."



def test_calculator_core_owns_display_entry_state_transitions() -> None:
    result = run_node_json(
        """
        const transitions = {
          enterDigitFromZero: core.appendCalculatorDisplayToken("0", "7"),
          enterOperatorAfterDigit: core.appendCalculatorDisplayToken("7", "+"),
          clear: core.clearCalculatorDisplayExpression(),
          backspaceToDigit: core.backspaceCalculatorDisplayExpression("123"),
          backspaceToZero: core.backspaceCalculatorDisplayExpression("7"),
          backspaceEmpty: core.backspaceCalculatorDisplayExpression("")
        };
        process.stdout.write(JSON.stringify(transitions));
        """
    )

    assert result["enterDigitFromZero"]["expression"] == "7"
    assert result["enterDigitFromZero"]["result"] == "ready"
    assert result["enterDigitFromZero"]["statusText"] == "ready"
    assert result["enterOperatorAfterDigit"]["expression"] == "7+"
    assert result["clear"]["expression"] == "0"
    assert result["backspaceToDigit"]["expression"] == "12"
    assert result["backspaceToZero"]["expression"] == "0"
    assert result["backspaceEmpty"]["expression"] == "0"

def test_calculator_view_model_owns_result_status_view_models() -> None:
    result = run_node_json(
        """
        const success = viewModel.buildCalculatorVisibleResultModel(
          core.evaluateCalculatorArithmeticExpression("2+3*4")
        );
        const invalid = viewModel.buildCalculatorVisibleResultModel(
          core.evaluateCalculatorArithmeticExpression("2+")
        );
        const blank = viewModel.buildCalculatorVisibleResultModel(
          core.evaluateCalculatorArithmeticExpression("")
        );
        process.stdout.write(JSON.stringify({
          success,
          invalid,
          blank,
          classes: {
            success: viewModel.classifyCalculatorRuntimeResult(success.runtimeResult),
            invalid: viewModel.classifyCalculatorRuntimeResult(invalid.runtimeResult),
            blank: viewModel.classifyCalculatorRuntimeResult(blank.runtimeResult)
          },
          messages: {
            success: viewModel.buildCalculatorStatusMessage(success.runtimeResult),
            invalid: viewModel.buildCalculatorStatusMessage(invalid.runtimeResult),
            blank: viewModel.buildCalculatorStatusMessage(blank.runtimeResult)
          },
          normalized: {
            success: viewModel.normalizeCalculatorResult(success.runtimeResult),
            invalid: viewModel.normalizeCalculatorResult(invalid.runtimeResult),
            blank: viewModel.normalizeCalculatorResult(blank.runtimeResult)
          }
        }));
        """
    )

    assert result["success"]["displayExpression"] == "14"
    assert result["success"]["resultText"] == "14"
    assert result["success"]["statusText"] == "ready"
    assert result["success"]["runtimeResult"]["ok"] is True
    assert result["success"]["runtimeResult"]["result"] == "14"
    assert result["success"]["runtimeResult"]["code"] == ""

    assert result["invalid"]["displayExpression"] == "2+"
    assert result["invalid"]["resultText"] == "incomplete expression"
    assert result["invalid"]["statusText"] == "error"
    assert result["invalid"]["runtimeResult"]["ok"] is False
    assert result["invalid"]["runtimeResult"]["code"] == "expression-invalid"
    assert result["invalid"]["runtimeResult"]["parserCode"] == "incomplete-expression"

    assert result["blank"]["displayExpression"] == "0"
    assert result["blank"]["resultText"] == "ready"
    assert result["blank"]["statusText"] == "ready"
    assert result["blank"]["runtimeResult"] == {
        "ok": False,
        "expression": "",
        "result": "ready",
        "code": "expression-required",
        "error": "enter an expression",
    }

    assert result["classes"] == {
        "success": "ready",
        "invalid": "error",
        "blank": "expression-required",
    }
    assert result["messages"] == {
        "success": "14",
        "invalid": "incomplete expression",
        "blank": "ready",
    }
    assert result["normalized"]["success"]["result"] == "14"
    assert result["normalized"]["invalid"]["statusText"] == "error"
    assert result["normalized"]["blank"]["code"] == "expression-required"



def test_calculator_core_owns_graph_expression_edit_state_transitions() -> None:
    result = run_node_json(
        """
        const transitions = {
          insertAtCaret: core.insertCalculatorGraphText("sin()", "x", 4, 4, 0),
          insertTemplate: core.insertCalculatorGraphText("", "sin()", 0, 0, 1),
          replaceSelection: core.insertCalculatorGraphText("sin(x)", "cos", 0, 3, 0),
          clear: core.clearCalculatorGraphExpression(),
          backspaceSelection: core.backspaceCalculatorGraphExpression("sin(x)", 0, 3),
          backspaceCaret: core.backspaceCalculatorGraphExpression("sin(x)", 4, 4),
          backspaceStart: core.backspaceCalculatorGraphExpression("sin(x)", 0, 0)
        };
        process.stdout.write(JSON.stringify(transitions));
        """
    )

    assert result["insertAtCaret"]["expression"] == "sin(x)"
    assert result["insertAtCaret"]["selectionStart"] == 5
    assert result["insertTemplate"]["expression"] == "sin()"
    assert result["insertTemplate"]["selectionStart"] == 4
    assert result["replaceSelection"]["expression"] == "cos(x)"
    assert result["replaceSelection"]["selectionStart"] == 3
    assert result["clear"]["expression"] == ""
    assert result["clear"]["selectionStart"] == 0
    assert result["backspaceSelection"]["expression"] == "(x)"
    assert result["backspaceSelection"]["selectionStart"] == 0
    assert result["backspaceCaret"]["expression"] == "sinx)"
    assert result["backspaceCaret"]["selectionStart"] == 3
    assert result["backspaceStart"]["expression"] == "sin(x)"
    assert result["backspaceStart"]["selectionStart"] == 0


def test_calculator_core_owns_graph_range_validation_and_sampling() -> None:
    result = run_node_json(
        """
        const plot = core.sampleCalculatorGraphExpression("x", {xMin: -1, xMax: 1, yMin: -1, yMax: 1}, 4);
        let invalidRange = "";
        try {
          core.sampleCalculatorGraphExpression("x", {xMin: 1, xMax: -1, yMin: -1, yMax: 1}, 4);
        } catch (error) {
          invalidRange = error.message;
        }
        let invalidExpression = "";
        try {
          core.sampleCalculatorGraphExpression("process.exit()", {xMin: -1, xMax: 1, yMin: -1, yMax: 1}, 4);
        } catch (error) {
          invalidExpression = error.message;
        }
        process.stdout.write(JSON.stringify({plot, invalidRange, invalidExpression}));
        """
    )

    assert result["plot"]["ok"] is True
    assert result["plot"]["expression"] == "x"
    assert result["plot"]["range"] == {"xMin": -1, "xMax": 1, "yMin": -1, "yMax": 1}
    assert result["plot"]["width"] == 4
    assert result["plot"]["finiteCount"] == 5
    assert len(result["plot"]["samples"]) == 5
    assert result["plot"]["samples"][0]["px"] == 0
    assert result["plot"]["samples"][0]["x"] == -1
    assert result["plot"]["samples"][0]["y"] == -1
    assert result["plot"]["samples"][0]["visible"] is True
    assert result["plot"]["samples"][-1]["px"] == 4
    assert result["plot"]["samples"][-1]["x"] == 1
    assert result["plot"]["samples"][-1]["y"] == 1
    assert result["plot"]["samples"][-1]["visible"] is True
    assert result["invalidRange"] == "x min must be less than x max"
    assert "unsupported token" in result["invalidExpression"].lower()


def test_calculator_view_model_owns_graph_render_models() -> None:
    result = run_node_json(
        """
        const success = viewModel.buildCalculatorGraphRenderModel(
          "x",
          {xMin: -1, xMax: 1, yMin: -1, yMax: 1},
          {width: 4, height: 4}
        );
        const invalidRange = viewModel.buildCalculatorGraphRenderModel(
          "x",
          {xMin: 1, xMax: -1, yMin: -1, yMax: 1},
          {width: 4, height: 4}
        );
        const invalidExpression = viewModel.buildCalculatorGraphRenderModel(
          "process.exit()",
          {xMin: -1, xMax: 1, yMin: -1, yMax: 1},
          {width: 4, height: 4}
        );
        process.stdout.write(JSON.stringify({success, invalidRange, invalidExpression}));
        """
    )

    assert result["success"]["ok"] is True
    assert result["success"]["expression"] == "x"
    assert result["success"]["statusText"] == "graphed x | 5 visible samples"
    assert result["success"]["finiteCount"] == 5
    assert result["success"]["range"] == {"xMin": -1, "xMax": 1, "yMin": -1, "yMax": 1}
    assert len(result["success"]["gridLines"]) == 22
    assert result["success"]["axisLines"] == [
        {"x1": 2, "y1": 0, "x2": 2, "y2": 4},
        {"x1": 0, "y1": 2, "x2": 4, "y2": 2},
    ]
    assert len(result["success"]["curveSegments"]) == 1
    assert result["success"]["curveSegments"][0][0] == {"x": 0, "y": 4}
    assert result["success"]["curveSegments"][0][-1] == {"x": 4, "y": 0}
    assert result["success"]["runtimeResult"] == {
        "ok": True,
        "expression": "x",
        "range": {"xMin": -1, "xMax": 1, "yMin": -1, "yMax": 1},
        "finiteCount": 5,
        "statusText": "graphed x | 5 visible samples",
    }

    assert result["invalidRange"]["ok"] is False
    assert result["invalidRange"]["errorLabel"] == "Graph error"
    assert result["invalidRange"]["runtimeResult"]["code"] == "graph-range-invalid"
    assert result["invalidRange"]["runtimeResult"]["statusText"] == "graph error: x min must be less than x max"

    assert result["invalidExpression"]["ok"] is False
    assert result["invalidExpression"]["runtimeResult"]["code"] == "graph-expression-required"
    assert "unsupported token" in result["invalidExpression"]["runtimeResult"]["error"].lower()



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



def test_model_expression_extraction_is_core_domain_logic() -> None:
    result = run_node_json(
        """
        const arithmetic = [
          core.extractCalculatorExpression("Use this: 2 + 3 * 4"),
          core.extractCalculatorExpression("```js\\n7 x 6\\n```"),
          core.extractCalculatorExpression("Please call Math.max(1, 2)")
        ];
        const graph = [
          core.extractCalculatorGraphExpression("f(x) = sin(x) + x^2"),
          core.extractCalculatorGraphExpression("y = sqrt(abs(x))"),
          core.extractCalculatorGraphExpression("window.alert(secret)")
        ];
        process.stdout.write(JSON.stringify({arithmetic, graph}));
        """
    )

    assert result["arithmetic"] == ["2 + 3 * 4", "7 * 6", ""]
    assert result["graph"] == ["sin(x)+x^2", "sqrt(abs(x))", ""]


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
