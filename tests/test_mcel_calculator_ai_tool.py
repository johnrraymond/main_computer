"""Headless AI calls must use Calculator's real core and its declared MCEL intent."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from main_computer.executor_tool_loop import (
    ExecutorToolLoopConfig, extract_executor_tool_request, run_executor_tool_loop,
)
from main_computer.mcel_calculator_ai_tool import (
    CalculatorAiToolError, execute_calculator_ai_tool,
)
from main_computer.models import ChatResponse

ROOT = Path(__file__).resolve().parents[1]


class Provider:
    name = "mock"
    model = "mock-ai-tool"

    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    def chat(self, messages):
        self.messages.append(list(messages))
        return ChatResponse(content=self.responses.pop(0), provider=self.name, model=self.model)


class ForbiddenExecutor:
    def run(self, *_args, **_kwargs):
        raise AssertionError("calculator.evaluate must not invoke shell/container execution")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required for the authoritative JS core")
@pytest.mark.parametrize("expression,expected", [
    ("4+9", 13), ("(5+2)*3", 21), ("3 m + 40 cm", 3.4),
])
def test_ai_tool_uses_real_calculator_core_with_mcel_source_provenance(expression, expected):
    receipt = execute_calculator_ai_tool({"action": "calculator.evaluate", "expression": expression}, repo_root=ROOT)
    assert receipt["ok"] is True
    assert receipt["evaluation"]["value"] == pytest.approx(expected)
    assert receipt["semanticFingerprint"].startswith("sha256:")
    assert receipt["sourceBindingFingerprint"].startswith("sha256:")
    assert receipt["coreSha256"].startswith("sha256:")
    assert receipt["receiptSha256"].startswith("sha256:")
    assert receipt["intentId"] == "intent:calculator.evaluate-expression"
    assert receipt["executionLane"] == "local-arithmetic"
    assert receipt["effectStatus"] == "none"
    assert receipt["engine"] == "main-computer-calculator-core-v1"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required")
def test_incompatible_units_are_refused_by_existing_engine():
    receipt = execute_calculator_ai_tool({"action": "calculator.evaluate", "expression": "3 m + 2 s"}, repo_root=ROOT)
    assert receipt["ok"] is False
    assert receipt["evaluation"]["parserCode"] == "unit-dimension-mismatch"


@pytest.mark.parametrize("tool_request", [
    {"action": "calculator.evaluate", "expression": ""},
    {"action": "calculator.evaluate", "expression": "1" * 4097},
    {"action": "calculator.evaluate", "expression": "1+2", "command": "rm -rf /"},
    {"action": "calculator.evaluate", "expression": ["1+2"]},
    {"action": "calculator.evaluate", "expression": "1+2", "network": True},
    {"action": "calculator.evaluate", "expression": "1+2", "intent": "mathics"},
])
def test_tool_rejects_unapproved_arguments_before_js_or_shell(tool_request):
    with pytest.raises(CalculatorAiToolError):
        execute_calculator_ai_tool(tool_request, repo_root=ROOT)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required")
def test_model_issued_action_round_trips_through_actual_ai_tool_dispatcher():
    provider = Provider([
        '{"action":"calculator.evaluate","expression":"3 m + 40 cm"}',
        '{"action":"final","content":"3.4 m (from Calculator)"}',
    ])
    result = run_executor_tool_loop(
        provider=provider, prompt="What is 3 m plus 40 cm?", context_text="", 
        executor_backend=ForbiddenExecutor(),
        config=ExecutorToolLoopConfig(max_steps=3, auto_run=False, allow_network=False),
    )
    assert result.ok and result.status == "complete"
    assert result.final_content == "3.4 m (from Calculator)"
    assert any(s.kind == "calculator_result" for s in result.steps)
    assert "calculator_result:" in provider.messages[1][-1].content
    response = json.loads(provider.messages[1][-1].content.split("calculator_result:\n", 1)[1].split("\nReport", 1)[0])
    assert response["evaluation"]["displayValue"] == "3.4 m"
    assert response["semanticFingerprint"].startswith("sha256:")
    assert extract_executor_tool_request('{"action":"calculator.evaluate","expression":"7+8"}') is not None


def test_tool_action_is_refused_if_extra_fields_claim_external_authority():
    provider = Provider(['{"action":"calculator.evaluate","expression":"2+2","command":"python -c 42"}'])
    result = run_executor_tool_loop(
        provider=provider, prompt="Compute", context_text="",
        executor_backend=ForbiddenExecutor(), config=ExecutorToolLoopConfig(max_steps=2),
    )
    assert not result.ok and result.status == "invalid_tool_request"
    assert "only action and expression" in (result.error or "")


def test_missing_semantic_intent_refuses_before_node(monkeypatch):
    from types import SimpleNamespace
    import main_computer.mcel_calculator_ai_tool as bridge
    bad_ir = {"intents": [{"id": "intent:calculator.other"}]}
    fake = SimpleNamespace(valid=True, status="pass", report={
        "sourceAuthority": "mcel.dsl.v1", "appId": "calculator",
        "normalizedIr": bad_ir, "semanticFingerprint": "sha256:" + "a" * 64,
    })
    monkeypatch.setattr(bridge, "compile_application", lambda **_kwargs: fake)
    monkeypatch.setattr(bridge.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Node must not run"))
    with pytest.raises(CalculatorAiToolError, match="not uniquely declared"):
        bridge.execute_calculator_ai_tool({"action": "calculator.evaluate", "expression": "2+2"}, repo_root=ROOT)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node required")
def test_js_code_injection_is_rejected_by_original_calculator_parser():
    result = execute_calculator_ai_tool({
        "action": "calculator.evaluate", "expression": "2+2;process.exit(0)"
    }, repo_root=ROOT)
    assert result["ok"] is False
    assert result["evaluation"]["parserCode"] == "unsupported-token"
