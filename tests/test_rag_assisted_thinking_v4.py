from __future__ import annotations

import json
from pathlib import Path

from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers.ollama import OllamaStreamTerminalError
from main_computer.providers.ollama import append_thinking_status, describe_thinking_setting
from main_computer.rag_assisted_thinking_v4 import (
    RagAssistedThinkingV4Policy,
    build_v4_retrieval_queries,
    run_rag_assisted_thinking_v4_request,
)


class StaticJsonProvider:
    name = "static-v4"
    model = "static-json"

    def __init__(self) -> None:
        self.think = None
        self.messages: list[ChatMessage] = []

    def chat(self, messages):
        self.messages = list(messages)
        return ChatResponse(
            content=json.dumps(
                {
                    "ok": True,
                    "action": "answer",
                    "summary": "answered with v4 chunks",
                    "answer": "RAG v4 used retrieved chunk context.",
                    "citations": [],
                    "files": [],
                    "commands": [],
                    "warnings": [],
                }
            ),
            provider=self.name,
            model=self.model,
            metadata={"think": self.think},
        )


class ProviderStreamErrorProvider:
    name = "ollama"
    model = "gemma4"

    def __init__(self) -> None:
        self.repair_calls = 0

    def chat(self, messages):
        self.repair_calls += 1
        raise OllamaStreamTerminalError(
            "Ollama stream error after 5 content chars and 1000 thinking chars: GGML_ASSERT(ctx->mem_buffer != NULL) failed",
            terminal_fault_type="provider_stream_error",
            partial_content="{\"ok\"",
            partial_thinking="x" * 1000,
        )


class MalformedPrimaryThinkingOnlyRepairProvider:
    name = "ollama"
    model = "gemma4"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        if self.calls == 1:
            return ChatResponse(
                content='{"ok": true, "action": "propose_files", "files": [{"path": "new_patch.py", "content": "'
                + ("x" * 6000),
                provider=self.name,
                model=self.model,
            )
        raise OllamaStreamTerminalError(
            "model emitted thinking only for 60s and produced 0 final content chars",
            terminal_fault_type="thinking_only_watchdog",
            partial_content="",
            partial_thinking="t" * 1144,
        )


def test_ollama_waiting_status_mentions_thinking_state() -> None:
    off = describe_thinking_setting(False)
    assert off["thinking_enabled"] is False
    assert append_thinking_status("Ollama request is still waiting.", off).endswith("Thinking: off.")

    low = describe_thinking_setting("low")
    assert low["thinking_enabled"] is True
    assert low["thinking_state"] == "low"
    assert append_thinking_status("Ollama request is still waiting.", low).endswith("Thinking: on (mode: low).")

def test_v4_retrieval_queries_avoid_broad_smoke_expansion() -> None:
    queries = build_v4_retrieval_queries(
        "Analyze the RAG system and the assisted thinking route.",
        ["Analyze the RAG system and the assisted thinking route."],
        object(),
    )

    assert queries
    assert len(queries) <= 8
    joined = " ".join(queries)
    assert "rag_quality_layer_smoke" not in joined
    assert "rag_json_repair_smoke" not in joined
    assert "run_rag_harness" in joined


def test_v4_general_answer_does_not_use_heuristic_fast_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    provider = StaticJsonProvider()
    result = run_rag_assisted_thinking_v4_request(
        prompt="hi",
        repo_dir=repo,
        provider=provider,
        queries=["hi"],
        run_id="v4_no_fast_path",
        policy=RagAssistedThinkingV4Policy(think="low"),
    )

    assert result.ok
    assert result.mode == "rag_assisted_thinking_v4"
    assert provider.messages, "v4 must not bypass the provider with a guessed greeting fast path"

    payload = json.loads(provider.messages[-1].content)
    assert payload["intent"]["request_type"] == "general_answer"
    assert payload["intent"]["rag_required"] is False
    assert payload["rag_context"] == []
    assert "fast_path_general_chat" not in result.as_dict().get("optimizations", {})


def test_v4_read_only_skips_docker_and_sends_chunk_context(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main_computer").mkdir()
    source = "\n".join(
        [
            "def run_rag_assisted_thinking_v2_request():",
            "    return 'control plane'",
            "",
            "def evaluate_retrieval_quality():",
            "    return 'quality'",
            "",
            "API_KEY = 'SHOULD_NOT_LEAK'",
            "",
        ]
        + [f"NOISE_{index} = {index}" for index in range(160)]
    )
    (repo / "main_computer" / "rag_assisted_thinking_v2.py").write_text(source, encoding="utf-8")
    (repo / "README.md").write_text("# RAG notes\n", encoding="utf-8")

    provider = StaticJsonProvider()
    result = run_rag_assisted_thinking_v4_request(
        prompt="Explain the RAG run_rag_assisted_thinking_v2_request decision flow.",
        repo_dir=repo,
        provider=provider,
        queries=["run_rag_assisted_thinking_v2_request"],
        run_id="v4_read_only",
        policy=RagAssistedThinkingV4Policy(
            think="low",
            docker_enabled=True,
            verify_before=True,
            verify_after=True,
            require_docker_success=True,
            max_context_chars=4000,
            max_candidates=6,
            max_chunks=4,
        ),
    )

    assert result.ok
    assert result.mode == "rag_assisted_thinking_v4"
    assert result.as_dict()["mode"] == "rag_assisted_thinking_v4"
    assert result.docker_before is None
    assert any("skipped Docker before verification" in warning for warning in result.warnings)
    assert provider.think == "low"

    payload = json.loads(provider.messages[-1].content)
    assert payload["rag_context"]
    assert all(item.get("context_kind") == "retrieved_chunk" for item in payload["rag_context"])
    assert sum(len(item["content"]) for item in payload["rag_context"]) < len(source)
    assert "SHOULD_NOT_LEAK" not in json.dumps(payload)

    run_json = next((Path(result.output_dir) / "rag_runs").glob("*/run.json"))
    diagnostics = run_json.read_text(encoding="utf-8")
    assert "diagnostics_scrubbed_by_v4" in diagnostics
    assert "SHOULD_NOT_LEAK" not in diagnostics


def test_v4_provider_stream_error_fails_terminal_and_skips_json_repair(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Repo\n", encoding="utf-8")

    provider = ProviderStreamErrorProvider()
    result = run_rag_assisted_thinking_v4_request(
        prompt="Return a JSON answer about this self-contained prompt.",
        repo_dir=repo,
        provider=provider,
        queries=["self-contained"],
        run_id="v4_provider_stream_error",
        policy=RagAssistedThinkingV4Policy(think="low"),
    )

    payload = result.as_dict()
    assert not result.ok
    assert payload["terminal_fault_type"] == "provider_stream_error"
    assert "GGML_ASSERT" in payload["terminal_fault_message"]
    assert payload["partial_content_chars"] == 5
    assert payload["partial_thinking_chars"] == 1000
    assert payload["json_repair_attempted"] is False
    assert payload["json_repair_skipped_reason"] == "JSON repair skipped because source stream ended with provider/runtime error"
    assert provider.repair_calls == 1


def test_v4_preserves_primary_trace_after_thinking_only_json_repair_failure(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    provider = MalformedPrimaryThinkingOnlyRepairProvider()
    result = run_rag_assisted_thinking_v4_request(
        prompt="Return a replacement file payload for new_patch.py.",
        repo_dir=repo,
        provider=provider,
        queries=["new_patch.py"],
        run_id="v4_primary_then_repair_failure",
        policy=RagAssistedThinkingV4Policy(think="low", exact_evidence_required=False),
    )

    payload = result.as_dict()
    output_dir = Path(result.output_dir)
    traces = json.loads((output_dir / "model_call_traces.json").read_text(encoding="utf-8"))

    assert not result.ok
    assert payload["terminal_fault_type"] == "json_repair_failed"
    assert payload["terminal_fault_source"] == "json_repair"
    assert traces["primary"]["content_chars"] > 6000
    assert traces["primary"]["content_path"]
    assert "malformed control-plane JSON" in traces["primary"]["parse_error"]
    assert traces["json_repair"]["content_chars"] == 0
    assert traces["json_repair"]["thinking_chars"] == 1144
    assert traces["json_repair"]["thinking_path"]
    assert "thinking only" in traces["json_repair"]["terminal_error"]
