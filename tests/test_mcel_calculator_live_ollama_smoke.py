"""Fail-closed contracts for a live Ollama → MCEL Calculator validation run."""
from __future__ import annotations

from pathlib import Path

import pytest

from main_computer.models import ChatResponse
from tools.mcel_calculator_live_ollama_smoke import run_live_calculator_smoke

ROOT = Path(__file__).resolve().parents[1]


class ScriptedModel:
    name = "mock"
    model = "simulated-only-for-smoke-gate-regressions"

    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.contexts = []

    def chat(self, messages):
        self.contexts.append(list(messages))
        return ChatResponse(content=self.responses.pop(0), model=self.model, provider=self.name)


TOOL = '{"action":"calculator.evaluate","expression":"3 m + 40 cm"}'
FINAL = '{"action":"final","content":"The Calculator reports 3.4 m."}'


def test_verified_model_tool_result_roundtrip_uses_actual_core():
    model = ScriptedModel(TOOL, FINAL)
    report = run_live_calculator_smoke(provider=model, repo_root=ROOT)
    assert report["passed"] is True
    assert report["stages"] == {
        "modelSelection": "pass", "calculatorReceipt": "pass", "modelFinal": "pass",
    }
    assert report["toolCalls"] == 1
    assert report["modelCalls"] == 2
    assert report["semanticFingerprint"].startswith("sha256:")
    assert "calculator_result:" in model.contexts[1][-1].content


@pytest.mark.parametrize("first_response", [
    '{"action":"final","content":"3.4 m"}',
    "I can calculate this myself: 3.4 m.",
    '{"action":"execute_shell","command":"echo 3.4","cwd":"/workspace"}',
])
def test_model_must_actually_invoke_calculator(first_response):
    report = run_live_calculator_smoke(provider=ScriptedModel(first_response), repo_root=ROOT)
    assert not report["passed"]
    assert report["failureStage"] == "model_selection"


def test_incorrect_tool_arguments_are_rejected():
    model = ScriptedModel('{"action":"calculator.evaluate","expression":"2+2"}', FINAL)
    report = run_live_calculator_smoke(provider=model, repo_root=ROOT)
    assert report["failureStage"] == "model_selection"
    assert not report["passed"]


@pytest.mark.parametrize("final", [
    '{"action":"final","content":"3.5 m"}',
    '{"action":"final","content":"Calculation complete."}',
])
def test_model_must_use_actual_calculator_result(final):
    report = run_live_calculator_smoke(provider=ScriptedModel(TOOL, final), repo_root=ROOT)
    assert not report["passed"]
    assert report["stages"]["calculatorReceipt"] == "pass"
    assert report["failureStage"] == "model_final"


def test_provider_failure_is_reported_without_success():
    class FailedProvider:
        name = "ollama"
        model = "unavailable"

        def chat(self, messages):
            raise ConnectionError("offline")

    report = run_live_calculator_smoke(provider=FailedProvider(), repo_root=ROOT)
    assert not report["passed"] and report["failureStage"] == "provider_or_tool_loop"


def test_receipt_tampering_is_rejected(monkeypatch):
    from tools import mcel_calculator_live_ollama_smoke as smoke
    from main_computer.executor_tool_loop import ExecutorToolLoopResult, ExecutorToolLoopStep
    fake = ExecutorToolLoopResult(
        ok=True, status="complete", provider="mock", model="mock", final_content="3.4 m",
        steps=[ExecutorToolLoopStep(index=1, kind="calculator_result",
            tool_request={"action": "calculator.evaluate", "expression": "3 m + 40 cm"},
            executor_result={"schema": "mcel.calculator.ai-tool-receipt.v1", "ok": True,
                             "evaluation": {"ok": True, "displayValue": "3.4 m"}})],
    )
    monkeypatch.setattr(smoke, "run_executor_tool_loop", lambda **kwargs: fake)
    report = smoke.run_live_calculator_smoke(provider=ScriptedModel(), repo_root=ROOT)
    assert not report["passed"] and report["failureStage"] == "calculator_receipt"


def test_plain_model_answer_after_tool_does_not_satisfy_final_json_contract():
    report = run_live_calculator_smoke(provider=ScriptedModel(TOOL, "3.4 m"), repo_root=ROOT)
    assert not report["passed"] and report["failureStage"] == "model_final"
