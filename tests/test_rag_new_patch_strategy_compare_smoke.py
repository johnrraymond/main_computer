from __future__ import annotations

import time
from pathlib import Path

from main_computer import rag_new_patch_strategy_compare_smoke as smoke


def test_timeout_classification_distinguishes_no_content_and_partial() -> None:
    assert (
        smoke.classify_generation_timeout(
            first_content_s=None,
            content_chars=0,
            thinking_chars=0,
            source="idle",
        )
        == "no_first_content_timeout"
    )
    assert (
        smoke.classify_generation_timeout(
            first_content_s=None,
            content_chars=0,
            thinking_chars=1200,
            source="idle",
        )
        == "thinking_only_timeout"
    )
    assert (
        smoke.classify_generation_timeout(
            first_content_s=1.0,
            content_chars=12,
            thinking_chars=0,
            source="idle",
        )
        == "content_stall_timeout"
    )
    assert (
        smoke.classify_generation_timeout(
            first_content_s=1.0,
            content_chars=12,
            thinking_chars=0,
            source="total",
        )
        == "total_timeout_after_partial_output"
    )


def test_build_generation_timings_preserves_partial_counts() -> None:
    started = time.monotonic() - 2.0
    timings = smoke.build_generation_timings(
        started=started,
        first_content_s=0.5,
        content="abc",
        thinking="think",
        event_count=3,
        final_event={},
        status="timeout",
        timeout_subtype="content_stall_timeout",
        error="timed out",
    )

    assert timings["status"] == "timeout"
    assert timings["content_chars"] == 3
    assert timings["thinking_chars"] == 5
    assert timings["event_count"] == 3
    assert timings["timeout_subtype"] == "content_stall_timeout"
    assert timings["error"] == "timed out"


def test_preserve_partial_generation_writes_partial_files(tmp_path: Path) -> None:
    exc = smoke.PartialGenerationError(
        "timeout",
        partial_text="print('partial')\n",
        partial_thinking="private scratch",
        timings={
            "status": "timeout",
            "content_chars": 17,
            "thinking_chars": 15,
            "timeout_subtype": "total_timeout_after_partial_output",
        },
    )

    partial_path, timings = smoke.preserve_partial_generation(out_dir=tmp_path, exc=exc)

    assert partial_path == tmp_path / "raw_response.partial.txt"
    assert partial_path.read_text(encoding="utf-8") == "print('partial')\n"
    assert (tmp_path / "thinking.partial.txt").read_text(encoding="utf-8") == "private scratch"
    assert timings["timeout_subtype"] == "total_timeout_after_partial_output"
    assert (tmp_path / "timings.partial.json").exists()


def test_build_generation_timings_records_first_event_and_snapshots() -> None:
    started = time.monotonic() - 1.0
    timings = smoke.build_generation_timings(
        started=started,
        first_event_s=0.25,
        first_content_s=None,
        last_event_s=0.75,
        content="",
        thinking="thought",
        event_count=2,
        final_event={},
        status="timeout",
        timeout_subtype="thinking_stall_timeout",
        heartbeat_snapshots=[{"ok": True, "stdout": "NAME\\nmodel"}],
    )

    assert timings["first_event_s"] == 0.25
    assert timings["first_content_s"] is None
    assert timings["last_event_s"] == 0.75
    assert timings["heartbeat_snapshots"][0]["ok"] is True


def test_no_first_event_timeout_is_reported_by_manual_partial_error(tmp_path: Path) -> None:
    exc = smoke.PartialGenerationError(
        "no stream event",
        partial_text="",
        partial_thinking="",
        timings={
            "status": "timeout",
            "total_wall_s": 60.0,
            "first_event_s": None,
            "first_content_s": None,
            "content_chars": 0,
            "thinking_chars": 0,
            "event_count": 0,
            "timeout_subtype": "no_first_event_timeout",
        },
    )

    partial_path, timings = smoke.preserve_partial_generation(out_dir=tmp_path, exc=exc)

    assert partial_path is None
    assert timings["timeout_subtype"] == "no_first_event_timeout"
    assert (tmp_path / "timings.partial.json").exists()


def test_provider_stream_append_helpers_flush_files(tmp_path: Path) -> None:
    text_path = tmp_path / "raw_response.stream.txt"
    jsonl_path = tmp_path / "provider_stream_events.jsonl"

    smoke.append_text(text_path, "abc")
    smoke.append_text(text_path, "def")
    smoke.append_jsonl(jsonl_path, {"event_index": 1, "response_chars_delta": 3})

    assert text_path.read_text(encoding="utf-8") == "abcdef"
    line = jsonl_path.read_text(encoding="utf-8").strip()
    assert '"event_index": 1' in line
    assert '"response_chars_delta": 3' in line


def test_ollama_ps_snapshot_contains_one_line_when_stdout(monkeypatch) -> None:
    class FakeCompleted:
        returncode = 0
        stdout = "NAME ID SIZE\nmodel abc 1GB\n"
        stderr = ""

    def fake_run(*args, **kwargs):
        return FakeCompleted()

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)

    snapshot = smoke.ollama_ps_snapshot()

    assert snapshot["ok"] is True
    assert snapshot["one_line"] == "NAME ID SIZE | model abc 1GB"


def test_build_ollama_generate_payload_sets_small_context_and_keep_alive() -> None:
    payload = smoke.build_ollama_generate_payload(
        prompt="Return OK",
        model="gemma4:26b",
        temperature=0.1,
        ollama_num_ctx=8192,
        ollama_keep_alive="5m",
    )

    assert payload["model"] == "gemma4:26b"
    assert payload["stream"] is True
    assert payload["options"]["temperature"] == 0.1
    assert payload["options"]["num_ctx"] == 8192
    assert payload["keep_alive"] == "5m"


def test_build_ollama_generate_payload_can_omit_optional_controls() -> None:
    payload = smoke.build_ollama_generate_payload(
        prompt="Return OK",
        model="gemma4:26b",
        temperature=0.0,
        ollama_num_ctx=0,
        ollama_keep_alive="",
    )

    assert "num_ctx" not in payload["options"]
    assert "keep_alive" not in payload


def test_skipped_generation_result_writes_timing_reason(tmp_path: Path) -> None:
    result = smoke.skipped_generation_result(
        suite_dir=tmp_path,
        strategy="direct_code",
        reason="preflight failed",
        preflight={"ok": False, "error": "no first event"},
    )

    assert result.ok is False
    assert result.output_dir == tmp_path / "direct_code"
    assert result.timings["status"] == "skipped"
    assert result.timings["skip_reason"] == "preflight failed"
    assert (tmp_path / "direct_code" / "timings.json").exists()


def test_build_ollama_chat_payload_uses_messages_schema() -> None:
    payload = smoke.build_ollama_chat_payload(
        prompt="Return OK",
        model="gemma4:26b",
        temperature=0.1,
        ollama_num_ctx=8192,
        ollama_keep_alive="5m",
    )

    assert payload["model"] == "gemma4:26b"
    assert payload["stream"] is True
    assert payload["messages"] == [{"role": "user", "content": "Return OK"}]
    assert payload["options"]["num_ctx"] == 8192
    assert payload["keep_alive"] == "5m"
    assert "prompt" not in payload


def test_build_ollama_payload_selects_chat_endpoint() -> None:
    api_path, payload = smoke.build_ollama_payload(
        prompt="Return OK",
        model="gemma4:26b",
        temperature=0.0,
        ollama_num_ctx=8192,
        ollama_keep_alive="",
        ollama_api="chat",
    )

    assert api_path == "/api/chat"
    assert payload["messages"][0]["content"] == "Return OK"
    assert "keep_alive" not in payload


def test_extract_ollama_event_text_supports_generate_and_chat_shapes() -> None:
    assert smoke.extract_ollama_event_text(
        {"response": "hello", "thinking": "think"},
        ollama_api="generate",
    ) == ("hello", "think")

    assert smoke.extract_ollama_event_text(
        {"message": {"content": "hello", "thinking": "think"}},
        ollama_api="chat",
    ) == ("hello", "think")


def test_preserve_partial_generation_records_thinking_only_timeout(tmp_path: Path) -> None:
    exc = smoke.PartialGenerationError(
        "thinking only",
        partial_text="",
        partial_thinking="private scratch that never became final content",
        timings={
            "status": "timeout",
            "content_chars": 0,
            "thinking_chars": 45,
            "event_count": 10,
            "timeout_subtype": "thinking_only_timeout",
        },
    )

    partial_path, timings = smoke.preserve_partial_generation(out_dir=tmp_path, exc=exc)

    assert partial_path is None
    assert timings["timeout_subtype"] == "thinking_only_timeout"
    assert (tmp_path / "thinking.partial.txt").read_text(encoding="utf-8") == "private scratch that never became final content"
    assert (tmp_path / "timings.partial.json").exists()


def test_ollama_generate_stream_signature_exposes_stall_watchdogs() -> None:
    import inspect

    params = inspect.signature(smoke.ollama_generate_stream).parameters

    assert "thinking_only_timeout_s" in params
    assert "thinking_only_min_chars" in params
    assert "content_stall_timeout_s" in params
    assert params["thinking_only_timeout_s"].default == 300.0
    assert params["content_stall_timeout_s"].default == 180.0

