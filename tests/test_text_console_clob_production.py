from __future__ import annotations

from pathlib import Path
import json

from main_computer.models import ChatMessage
from main_computer.text_console_clobs import (
    build_text_console_clob_lookup_context,
    enrich_terminal_result_with_clobs,
    save_text_console_clob,
)


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


def test_text_console_frontend_preserves_terminal_clob_context_for_thread_messages():
    page = (Path(__file__).resolve().parents[1] / "main_computer" / "web" / "text.html").read_text(encoding="utf-8")

    assert "text_console_clobs: Array.isArray(result?.text_console_clobs)" in page
    assert "model_context: result?.model_context" in page
    assert "function terminalResultThreadText(result)" in page
    assert "result?.model_context?.thread_text" in page
    assert "function mountThreadExecutionResultText(artifact)" in page
    assert "mountThreadExecutionResultText(artifact)" in page
