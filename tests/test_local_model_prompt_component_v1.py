from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

import pytest

from main_computer.local_model_prompt_component_v1 import (
    COMPONENT_VERSION,
    CONTRACT,
    run_from_state,
    run_local_model_prompt_call,
)
from main_computer.models import ChatMessage, ChatResponse


class FakeLocalPromptProvider:
    name = "fake-local-ollama"
    model = "fake-gemma4:prompt-component"

    def __init__(self) -> None:
        self.calls: list[list[ChatMessage]] = []

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        captured = list(messages)
        self.calls.append(captured)
        prompt = captured[-1].content
        return ChatResponse(
            content=f"LOCAL_MODEL_COMPONENT_OK: {prompt}",
            provider=self.name,
            model=self.model,
            metadata={"fake": True},
        )


def test_local_model_prompt_component_contract_is_single_purpose() -> None:
    contract = CONTRACT.as_dict()

    assert contract["step_id"] == "local_model_prompt_call"
    assert contract["version"] == COMPONENT_VERSION
    assert [item["key"] for item in contract["requires"]] == ["prompt_text"]
    assert "local_model_response_text" in {item["key"] for item in contract["provides"]}
    assert "local_model_provider" in {item["key"] for item in contract["provides"]}
    assert "local_model_model" in {item["key"] for item in contract["provides"]}
    assert set(contract["evidence_required"]) == {
        "provider_identity",
        "model_response",
        "trace_json",
    }
    assert "retrieval" in contract["description"].lower()
    assert "executor" in contract["description"].lower()


def test_local_model_prompt_component_calls_provider_once_and_emits_raw_result(tmp_path: Path) -> None:
    provider = FakeLocalPromptProvider()

    result = run_local_model_prompt_call(
        prompt_text="Return the smoke token.",
        system_prompt="You are only checking model connectivity.",
        output_dir=tmp_path,
        provider=provider,
        run_id="unit-local-model-prompt",
    )

    assert result.ok, result.as_dict()
    assert len(provider.calls) == 1
    assert [message.role for message in provider.calls[0]] == ["system", "user"]
    assert provider.calls[0][-1].content == "Return the smoke token."

    state = result.provided_state
    assert state["local_model_call_ok"] is True
    assert state["local_model_provider"] == "fake-local-ollama"
    assert state["local_model_model"] == "fake-gemma4:prompt-component"
    assert state["local_model_response_text"] == "LOCAL_MODEL_COMPONENT_OK: Return the smoke token."
    assert state["local_model_response_chars"] == len(state["local_model_response_text"])
    assert state["local_model_message_count"] == 2
    assert result.details["single_model_call_boundary"] is True
    assert result.details["retrieval_performed"] is False
    assert result.details["rag_performed"] is False
    assert result.details["executor_performed"] is False

    trace_path = Path(state["local_model_trace_path"])
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["ok"] is True
    assert trace["schema_version"] == 1
    assert trace["component_version"] == COMPONENT_VERSION
    assert trace["step_id"] == "local_model_prompt_call"
    assert trace["run_id"] == "unit-local-model-prompt"
    assert trace["response_text"] == state["local_model_response_text"]
    assert trace["message_count"] == 2


def test_local_model_prompt_component_accepts_prompt_from_state(tmp_path: Path) -> None:
    provider = FakeLocalPromptProvider()

    result = run_from_state(
        output_dir=tmp_path,
        state={
            "assembly_run_id": "assembly-state-run",
            "prompt_text": "State supplied prompt.",
        },
        provider=provider,
    )

    assert result.ok
    assert len(provider.calls) == 1
    assert provider.calls[0][-1].content == "State supplied prompt."
    trace = json.loads(Path(result.provided_state["local_model_trace_path"]).read_text(encoding="utf-8"))
    assert trace["run_id"] == "assembly-state-run"


def test_local_model_prompt_component_rejects_empty_prompt(tmp_path: Path) -> None:
    provider = FakeLocalPromptProvider()

    result = run_local_model_prompt_call(prompt_text="   ", output_dir=tmp_path, provider=provider)

    assert not result.ok
    assert result.status == "fail"
    assert provider.calls == []
    assert "prompt_text must be non-empty" in result.details["error"]


@pytest.mark.skipif(
    os.environ.get("MAIN_COMPUTER_RUN_LOCAL_MODEL_PROMPT_COMPONENT_TESTS") != "1",
    reason="set MAIN_COMPUTER_RUN_LOCAL_MODEL_PROMPT_COMPONENT_TESTS=1 to call the live local Ollama model",
)
def test_local_model_prompt_component_calls_live_local_ollama(tmp_path: Path) -> None:
    result = run_local_model_prompt_call(
        prompt_text="Reply with a short confirmation that the local AI call worked.",
        output_dir=tmp_path,
        run_id="live-local-model-prompt",
    )

    assert result.ok, result.as_dict()
    assert result.provided_state["local_model_provider"] == "ollama"
    assert result.provided_state["local_model_response_chars"] > 0
    assert Path(result.provided_state["local_model_trace_path"]).exists()
