from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import json
import shutil

from main_computer.models import ChatMessage
from main_computer.text_console_clobs import (
    build_text_console_clob_lookup_context,
    enrich_terminal_result_with_clobs,
    response_uses_text_console_clob_evidence,
    save_text_console_clob,
)
from main_computer.text_console import sanitize_text_console_clob_public_answer


def test_large_terminal_stdout_is_saved_as_side_loaded_clob(tmp_path: Path):
    large_stdout = "\n".join(f"generated/path/{index:04d}.py" for index in range(400))
    result = enrich_terminal_result_with_clobs(
        tmp_path,
        {
            "command": "Get-ChildItem main_computer -Recurse",
            "cwd": str(tmp_path),
            "target_id": "repo-root-powershell-terminal",
            "target_display_name": "Repo Terminal",
            "target_os": "windows",
            "target_shell": "powershell",
            "exit_code": 0,
            "stdout": large_stdout,
            "stderr": "",
            "duration_ms": 123,
            "timed_out": False,
        },
        threshold_chars=200,
        inline_excerpt_chars=300,
    )

    clobs = result["text_console_clobs"]
    assert len(clobs) == 1
    assert clobs[0]["stream"] == "stdout"
    assert clobs[0]["clob_id"].startswith("clob-terminal_output-")
    assert "model_context" in result
    thread_text = result["model_context"]["thread_text"]
    assert clobs[0]["clob_id"] in thread_text
    assert "side-loaded clob reference" in thread_text
    assert "generated/path/0000.py" in thread_text
    assert "generated/path/0200.py" not in thread_text

    cache_path = tmp_path / clobs[0]["cache_path"]
    assert cache_path.exists()
    cache_payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache_payload["payload"]["text"] == large_stdout


def test_clob_lookup_context_uses_retrieved_lines_not_full_payload(tmp_path: Path):
    full_text = "\n".join(
        [
            "src/random.py",
            "tests/test_unrelated.py",
            "tests/test_text_console_clob_runtime.py",
            "main_computer/text_console_clobs.py",
            "docs/notes.md",
        ]
    )
    clob = save_text_console_clob(
        tmp_path,
        clob_type="terminal_output",
        text=full_text,
        source={"kind": "terminal_result", "stream": "stdout"},
    )
    thread_messages = [
        ChatMessage(
            role="system",
            content=(
                "Terminal result from an explicitly executed text-console mount.\n"
                "Large terminal output was saved as a side-loaded clob.\n"
                f"clob_id: {clob['clob_id']}\n"
            ),
        )
    ]

    context, metadata = build_text_console_clob_lookup_context(
        tmp_path,
        prompt="Which tests mention text console clob?",
        thread_messages=thread_messages,
        max_chars=1200,
    )

    assert metadata["clob_ids_seen"] == [clob["clob_id"]]
    assert metadata["clob_ids_loaded"] == [clob["clob_id"]]
    assert metadata["result_count"] >= 1
    assert metadata["full_clob_injected"] is False
    assert "tests/test_text_console_clob_runtime.py" in context
    assert "clob-evidence-001" in context
    assert len(context) <= 1200
    assert "docs/notes.md" not in context


def test_clob_lookup_context_makes_runtime_evidence_grounding_explicit(tmp_path: Path):
    full_text = "\n".join(
        [
            "build output line",
            "ERROR critical assertion failed in tests/test_runtime_clob_grounding.py",
            "cleanup finished",
        ]
    )
    clob = save_text_console_clob(
        tmp_path,
        clob_type="terminal_output",
        text=full_text,
        source={"kind": "terminal_result", "stream": "stdout"},
    )
    thread_messages = [
        ChatMessage(
            role="system",
            content=(
                "Terminal result from an explicitly executed text-console mount.\n"
                f"clob_id: {clob['clob_id']}\n"
            ),
        )
    ]

    context, metadata = build_text_console_clob_lookup_context(
        tmp_path,
        prompt="Which runtime clob assertion failed?",
        thread_messages=thread_messages,
        max_chars=1200,
    )

    assert "Grounding requirement" in context
    assert "grounding_evidence:" in context
    assert "evidence_id=clob-evidence-001" in context
    assert metadata["grounding_required"] is True
    assert metadata["evidence_ids"] == ["clob-evidence-001"]
    assert metadata["evidence"][0]["text"] == "ERROR critical assertion failed in tests/test_runtime_clob_grounding.py"

    cited = response_uses_text_console_clob_evidence("The failing line is clob-evidence-001.", metadata)
    quoted = response_uses_text_console_clob_evidence(
        "The failing line is ERROR critical assertion failed in tests/test_runtime_clob_grounding.py.",
        metadata,
    )
    plausible_but_ungrounded = response_uses_text_console_clob_evidence(
        "The failure was a runtime clob assertion.",
        metadata,
    )

    assert cited["ok"] is True
    assert cited["matched_ids"] == ["clob-evidence-001"]
    assert quoted["ok"] is True
    assert quoted["matched_texts"] == ["ERROR critical assertion failed in tests/test_runtime_clob_grounding.py"]
    assert plausible_but_ungrounded["ok"] is False


def test_clob_public_answer_sanitizer_removes_internal_evidence_tags_but_keeps_content():
    response = (
        "Specific examples include:\n"
        "* rag_assisted_thinking.py (evidence_id=clob-evidence-001)\n"
        "* recurrent_thinking.py (evidence_id=clob-evidence-010)\n"
        "The answer is in clob-evidence-011: thinking_models.py"
    )

    public = sanitize_text_console_clob_public_answer(response)

    assert "clob-evidence-" not in public
    assert "evidence_id=" not in public
    assert "rag_assisted_thinking.py" in public
    assert "recurrent_thinking.py" in public
    assert "thinking_models.py" in public


def _copy_action_specs_for_test_repo(repo: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "main_computer" / "action_specs"
    target = repo / "main_computer" / "action_specs"
    target.mkdir(parents=True, exist_ok=True)
    for path in source.glob("*.md"):
        shutil.copy2(path, target / path.name)


def test_terminal_output_clob_followup_reaches_operator_as_bounded_grounding_slice(
    tmp_path: Path,
    monkeypatch,
):
    import main_computer.text_console as prod
    from main_computer.config import MainComputerConfig
    from main_computer.models import ChatResponse

    _copy_action_specs_for_test_repo(tmp_path)
    large_stdout = "\n".join(f"irrelevant/path/{index:04d}.py" for index in range(160))
    large_stdout += "\ncritical/runtime/path.txt contains the answer token"

    terminal_result = enrich_terminal_result_with_clobs(
        tmp_path,
        {
            "command": "Get-ChildItem . -Recurse",
            "cwd": str(tmp_path),
            "target_id": "repo-root-powershell-terminal",
            "target_display_name": "Repo Terminal",
            "target_os": "windows",
            "target_shell": "powershell",
            "exit_code": 0,
            "stdout": large_stdout,
            "stderr": "",
            "duration_ms": 123,
            "timed_out": False,
        },
        threshold_chars=200,
        inline_excerpt_chars=180,
    )
    thread_message = ChatMessage(role="system", content=terminal_result["model_context"]["thread_text"])
    lookup_context, lookup_metadata = build_text_console_clob_lookup_context(
        tmp_path,
        prompt="Which runtime path contains the answer token?",
        thread_messages=[thread_message],
        max_chars=1400,
    )
    conversation_messages = [thread_message, ChatMessage(role="system", content=lookup_context)]
    calls: list[list[ChatMessage]] = []

    class GroundedProvider:
        name = "fake"
        model = "fake-model"

        def chat(self, messages):
            calls.append(messages)
            joined = "\n".join(str(message.content) for message in messages)
            if prod.ACTION_PREFLIGHT_PROMPT in joined:
                return ChatResponse(
                    content=json.dumps(
                        {
                            "needs_mount": False,
                            "needs_edit": False,
                            "needs_answer_only": True,
                            "selected_spec_ids": [],
                            "reason": "The follow-up can be answered from clob lookup evidence.",
                        }
                    ),
                    provider="fake",
                    model="fake-model",
                )
            assert "Grounding requirement" in joined
            assert "evidence_id=clob-evidence-001" in joined
            assert "critical/runtime/path.txt contains the answer token" in joined
            assert "irrelevant/path/0080.py" not in joined
            return ChatResponse(
                content="The answer is: critical/runtime/path.txt contains the answer token.",
                provider="fake",
                model="fake-model",
            )

    class FakeComputer:
        provider = GroundedProvider()

        def context_pack(self, prompt):
            return SimpleNamespace(
                text=(
                    "Deterministic workspace context pack:\n"
                    f"Workspace root: {tmp_path}\n"
                    "Main computer file manifest:\n"
                    "  - main_computer/action_specs/terminal.md\n"
                ),
                evidence=[],
                manifest_chars=64,
            )

        def _web_search_context(self, prompt):
            return {"attempted": False, "results": []}, ""

    monkeypatch.setattr(prod.MainComputer, "build", classmethod(lambda cls, config=None: FakeComputer()))

    response = prod.run_text_console_operator_chat(
        text_console_config=prod.TextConsoleConfig.from_current_directory(
            tmp_path,
            provider="ollama",
            model="fake-model",
            base_url="http://127.0.0.1:11434",
            timeout=120.0,
            think=False,
        ),
        prompt="Which runtime path contains the answer token?",
        base_config=MainComputerConfig(workspace=tmp_path),
        conversation_messages=conversation_messages,
    )
    grounding = response_uses_text_console_clob_evidence(response.content, lookup_metadata)

    assert len(calls) == 2
    assert "clob-evidence-" not in response.content
    assert "evidence_id=" not in response.content
    assert "critical/runtime/path.txt contains the answer token" in response.content
    assert grounding["ok"] is True
    assert grounding["matched_ids"] == []
    assert grounding["matched_texts"] == ["critical/runtime/path.txt contains the answer token"]



def test_clob_grounded_answer_path_bypasses_operator_scaffold_and_thread_bulk(
    tmp_path: Path,
    monkeypatch,
):
    import main_computer.text_console as prod
    from main_computer.config import MainComputerConfig
    from main_computer.models import ChatResponse

    full_text = "\n".join(f"irrelevant/history/{index:04d}.txt" for index in range(250))
    full_text += "\ncritical/runtime/path.txt contains the answer token"
    clob = save_text_console_clob(
        tmp_path,
        clob_type="terminal_output",
        text=full_text,
        source={"kind": "terminal_result", "stream": "stdout"},
    )
    thread_message = ChatMessage(
        role="system",
        content=(
            "Terminal result from an explicitly executed text-console mount.\n"
            f"clob_id: {clob['clob_id']}\n"
            "HISTORIC_BULK_SHOULD_NOT_REACH_CLOB_ANSWER " * 500
        ),
    )
    lookup_context, lookup_metadata = build_text_console_clob_lookup_context(
        tmp_path,
        prompt="Which runtime path contains the answer token?",
        thread_messages=[thread_message],
        max_chars=1400,
    )
    calls: list[list[ChatMessage]] = []

    class GroundedProvider:
        name = "fake"
        model = "fake-model"

        def chat(self, messages):
            calls.append(messages)
            joined = "\n".join(str(message.content) for message in messages)
            assert prod.ACTION_PREFLIGHT_PROMPT not in joined
            assert prod.FINAL_OPERATOR_PROMPT not in joined
            assert "Selected text-console action spec" not in joined
            assert "Main computer file manifest" not in joined
            assert "THREAD_TERMINAL_RESULT_CONTEXT_PROMPT" not in joined
            assert "HISTORIC_BULK_SHOULD_NOT_REACH_CLOB_ANSWER" not in joined
            assert "evidence_id=clob-evidence-001" in joined
            assert "critical/runtime/path.txt contains the answer token" in joined
            assert "irrelevant/history/0120.txt" not in joined
            return ChatResponse(
                content="The answer is in clob-evidence-001: critical/runtime/path.txt contains the answer token.",
                provider="fake",
                model="fake-model",
            )

    class FakeComputer:
        provider = GroundedProvider()

    monkeypatch.setattr(prod.MainComputer, "build", classmethod(lambda cls, config=None: FakeComputer()))

    response = prod.run_text_console_clob_grounded_answer(
        text_console_config=prod.TextConsoleConfig.from_current_directory(
            tmp_path,
            provider="ollama",
            model="fake-model",
            base_url="http://127.0.0.1:11434",
            timeout=120.0,
            think=False,
        ),
        prompt="Which runtime path contains the answer token?",
        clob_lookup_text=lookup_context,
        base_config=MainComputerConfig(workspace=tmp_path),
    )
    grounding = response_uses_text_console_clob_evidence(response.content, lookup_metadata)

    assert len(calls) == 1
    assert len(calls[0]) == 4
    assert response.metadata["text_console_clob_grounded_answer"]["bypassed_operator_preflight"] is True
    assert response.metadata["text_console_clob_grounded_answer"]["bypassed_action_specs"] is True
    assert response.metadata["text_console_clob_grounded_answer"]["bypassed_thread_messages"] is True
    assert response.metadata["text_console_clob_grounded_answer"]["input_chars"] < 3000
    assert "clob-evidence-" not in response.content
    assert "evidence_id=" not in response.content
    assert "critical/runtime/path.txt contains the answer token" in response.content
    assert response.metadata["text_console_clob_grounded_answer"]["public_answer_sanitized"] is True
    assert grounding["ok"] is True
    assert grounding["matched_ids"] == []
    assert grounding["matched_texts"] == ["critical/runtime/path.txt contains the answer token"]


def test_clob_lookup_followup_action_heuristic_keeps_actions_on_operator_path():
    import main_computer.text_console as prod

    assert prod.text_console_prompt_requests_local_action("Which runtime path contains the answer token?") is False
    assert prod.text_console_prompt_requests_local_action("What assertion failed in that output?") is False
    assert prod.text_console_prompt_requests_local_action("Run pytest for that failing test file") is True
    assert prod.text_console_prompt_requests_local_action("Patch the file that failed") is True

def test_text_console_frontend_preserves_terminal_clob_context_for_thread_messages():
    page = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "text.html").read_text(encoding="utf-8")

    assert "text_console_clobs: Array.isArray(result?.text_console_clobs)" in page
    assert "model_context: result?.model_context" in page
    assert "function terminalResultThreadText(result)" in page
    assert "result?.model_context?.thread_text" in page
    assert "function mountThreadExecutionResultText(artifact)" in page
    assert "mountThreadExecutionResultText(artifact)" in page
