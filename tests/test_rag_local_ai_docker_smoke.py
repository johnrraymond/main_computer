from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from typing import Sequence

from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers import LLMProvider
from main_computer.rag_local_ai_docker_smoke import (
    build_end_to_end_cases,
    docker_concept_validation_command,
    docker_validation_command,
    extract_json_object,
    run_local_ai_docker_suite,
)


class FakeLocalAiProvider(LLMProvider):
    name = "fake-local-ai"
    model = "fake-ollama"

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        payload = None
        for message in reversed(messages):
            try:
                candidate = json.loads(message.content)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and "concept_id" in candidate:
                payload = candidate
                break
        if payload is None:
            raise AssertionError("fake provider did not receive the RAG payload")

        required_phrases = payload.get("required_exact_answer_phrases_when_supported") or []
        required_paths = payload.get("required_citation_paths_when_supported") or []
        abstain = bool(payload.get("require_abstain"))

        if abstain:
            answer = "insufficient evidence"
            citations = []
        else:
            answer = " | ".join(str(phrase) for phrase in required_phrases)
            citations = [
                {"path": path, "line_start": 1, "line_end": 1}
                for path in required_paths
            ]

        response = {
            "concept_id": payload["concept_id"],
            "answer": answer,
            "abstained": abstain,
            "confidence": "high" if not abstain else "low",
            "citations": citations,
        }
        return ChatResponse(content=json.dumps(response), provider=self.name, model=self.model)


class BadButJsonLocalAiProvider(LLMProvider):
    name = "bad-json-local-ai"
    model = "tiny-ollama-like"

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        payload = None
        for message in reversed(messages):
            try:
                candidate = json.loads(message.content)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and "concept_id" in candidate:
                payload = candidate
                break
        concept_id = payload["concept_id"] if payload else "unknown"
        response = {
            "concept_id": concept_id,
            "answer": "11",
            "abstained": False,
            "confidence": "high",
            "citations": [{"path": "src/widget_lock.py#3", "line_start": 1, "line_end": 1}],
        }
        return ChatResponse(content=json.dumps(response), provider=self.name, model=self.model)


def fake_docker_runner(command, *args, **kwargs):
    text = " ".join(str(part) for part in command)
    if "version" in text:
        return subprocess.CompletedProcess(command, 0, stdout="25.0.0\n", stderr="")
    if "RAG_E2E_CONCEPT_OK" in text:
        return subprocess.CompletedProcess(command, 0, stdout="RAG_E2E_CONCEPT_OK fake\n", stderr="")
    if "RAG_E2E_TRACE_OK" in text:
        return subprocess.CompletedProcess(command, 0, stdout="RAG_E2E_TRACE_OK\n", stderr="")
    return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")


def test_extract_json_object_accepts_fenced_model_output() -> None:
    parsed = extract_json_object('Here is the result:\n```json\n{"concept_id":"x","ok":true}\n```')
    assert parsed == {"concept_id": "x", "ok": True}


def test_end_to_end_cases_cover_all_recommended_concepts() -> None:
    cases = build_end_to_end_cases()
    assert len(cases) == 14
    assert cases[0].concept_id == "hybrid_retrieval"
    assert cases[-1].concept_id == "retrieval_trace_artifact"
    assert all(case.expected_phrases for case in cases)


def test_docker_validation_commands_check_end_to_end_trace_and_concepts() -> None:
    trace_command = docker_validation_command()
    concept_command = docker_concept_validation_command()
    assert "RAG_E2E_TRACE_OK" in trace_command
    assert "RAG_E2E_CONCEPT_OK" in concept_command
    assert "retrieval_trace_artifact" in trace_command
    assert "verification" in concept_command


def test_recommended_rag_smokes_are_end_to_end_with_fake_local_ai_and_fake_docker(tmp_path: Path) -> None:
    trace = run_local_ai_docker_suite(
        repo_dir=tmp_path,
        output_dir=tmp_path / "rag_local_ai_docker",
        provider=FakeLocalAiProvider(),
        docker_runner=fake_docker_runner,
    )

    assert trace.ok, trace.failures
    assert trace.schema_version == 2
    assert trace.mode == "end_to_end"
    assert trace.concept_count == 14
    assert len(trace.concept_results) == 14
    assert all(result["ok"] is True for result in trace.concept_results)
    assert all(result["docker_validation"]["ok"] is True for result in trace.concept_results)
    assert (tmp_path / "rag_local_ai_docker" / "rag_local_ai_docker_trace.json").exists()


def test_recommended_rag_smokes_repair_tiny_model_grounding_failures(tmp_path: Path) -> None:
    trace = run_local_ai_docker_suite(
        repo_dir=tmp_path,
        output_dir=tmp_path / "rag_local_ai_docker_bad_model",
        provider=BadButJsonLocalAiProvider(),
        docker_runner=fake_docker_runner,
    )

    assert trace.ok, trace.failures
    repaired = [result for result in trace.concept_results if result["local_ai_answer"]["repaired_by_harness"]]
    assert len(repaired) == 14
    assert all(result["verification"]["ok"] for result in repaired)
    assert all(result["docker_validation"]["ok"] for result in repaired)


@unittest.skipUnless(
    os.environ.get("MAIN_COMPUTER_RUN_RAG_LOCAL_AI_DOCKER_TESTS") == "1",
    "set MAIN_COMPUTER_RUN_RAG_LOCAL_AI_DOCKER_TESTS=1 to run real local Ollama + Docker RAG end-to-end smoke tests",
)
def test_recommended_rag_smokes_use_real_local_ai_and_real_docker(tmp_path: Path) -> None:
    repo_dir = Path.cwd()
    output_dir = tmp_path / "rag_local_ai_docker"
    trace = run_local_ai_docker_suite(repo_dir=repo_dir, output_dir=output_dir)

    assert trace.ok, trace.failures
    assert trace.schema_version == 2
    assert trace.mode == "end_to_end"
    assert trace.concept_count == 14
    assert len(trace.concept_results) == 14
    assert all(result["docker_validation"]["ok"] is True for result in trace.concept_results)
    assert (output_dir / "rag_local_ai_docker_trace.json").exists()
