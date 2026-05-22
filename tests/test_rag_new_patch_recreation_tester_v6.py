from __future__ import annotations

from pathlib import Path

from main_computer import rag_new_patch_recreation_tester_v6 as harness


def test_diagnostics_lookup_prefers_v4_and_globs_run_id(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    v3 = repo / "diagnostics_output" / "rag_assisted_thinking_v3_routes" / "prefix_run123_suffix"
    v4 = repo / "diagnostics_output" / "rag_assisted_thinking_v4_routes" / "timestamp_run123_expanded"
    v3.mkdir(parents=True)
    v4.mkdir(parents=True)

    assert harness.diagnostics_dir(repo, "run123") == v4


def test_diagnostics_lookup_falls_back_to_v3(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    v3 = repo / "diagnostics_output" / "rag_assisted_thinking_v3_routes" / "prefix_run123_suffix"
    v3.mkdir(parents=True)

    assert harness.diagnostics_dir(repo, "run123") == v3


def test_terminal_fault_event_preserves_provider_error_over_post_timeout() -> None:
    event_payload = {
        "event": {"message": "POST thread did not return"},
        "data": {
            "error": "Ollama stream error after 5022 content chars and 2970 thinking chars: GGML_ASSERT(ctx->mem_buffer != NULL) failed",
            "terminal_fault_type": "provider_stream_error",
            "content_chars": 5022,
            "thinking_chars": 2970,
        },
    }

    fault = harness._terminal_fault_from_event(event_payload)

    assert fault["terminal_fault_type"] == "provider_stream_error"
    assert "GGML_ASSERT" in fault["terminal_fault_message"]
    assert fault["partial_content_chars"] == 5022
    assert fault["partial_thinking_chars"] == 2970


def test_print_event_model_stream_uses_counter_deltas(capsys) -> None:
    counters: dict[str, int] = {}
    first = {"ts": "1", "source": "local-ai", "status": "running", "title": "Model text transmitted", "message": "prefix", "data": {"rag_type": "model_stream", "stream_event_type": "content_delta", "content_chars": 10, "thinking_chars": 3}}
    second = {"ts": "2", "source": "local-ai", "status": "running", "title": "Model text transmitted", "message": "same prefix plus more", "data": {"rag_type": "model_stream", "stream_event_type": "content_delta", "content_chars": 14, "thinking_chars": 3}}

    harness.print_event(first, previous_counters=counters)
    harness.print_event(second, previous_counters=counters)

    out = capsys.readouterr().out
    assert "content_chars=10 thinking_chars=3 (+10 content chars, +3 thinking chars)" in out
    assert "content_chars=14 thinking_chars=3 (+4 content chars, +0 thinking chars)" in out
    assert "same prefix plus more" not in out


def test_stream_observer_stops_on_stream_error_before_summary(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    done = harness.threading.Event()
    faults: harness.queue.Queue[dict[str, object]] = harness.queue.Queue()
    event = {
        "ts": "2026-05-06T00:00:00Z",
        "source": "local-ai",
        "status": "failed",
        "title": "Model stream error",
        "message": "GGML_ASSERT",
        "data": {
            "run_id": "run1",
            "rag_type": "model_stream",
            "stream_event_type": "stream_error",
            "error": "GGML_ASSERT(ctx->mem_buffer != NULL) failed",
            "terminal_fault_type": "provider_stream_error",
            "content_chars": 5,
            "thinking_chars": 7,
        },
    }

    def fake_get_json(base_url: str, path: str, query=None, timeout_s: float = 10.0):
        calls.append(path)
        if path == harness.RUN_RESULT_ROUTE:
            return {"ok": True, "running": True}
        return {"events": [event]}

    monkeypatch.setattr(harness, "get_json", fake_get_json)
    monkeypatch.setattr(harness, "print_ollama_snapshot", lambda *args, **kwargs: {"ok": True})

    harness.stream_observer(
        base_url="http://test",
        run_id="run1",
        thread_id="thread1",
        done=done,
        out_dir=tmp_path,
        heartbeat_every=10,
        poll_interval_s=0.01,
        ollama_ps_every=0,
        quiet=True,
        expected_models=["gemma4"],
        allow_any_loaded_models=True,
        stop_unexpected_models_flag=False,
        ollama_stop_timeout_s=1.0,
        terminal_faults=faults,
    )

    assert done.is_set()
    assert not faults.empty()
    assert harness._terminal_fault_from_event(faults.get())["terminal_fault_type"] == "provider_stream_error"


def test_primary_output_preserved_after_repair_failure_in_summary(tmp_path: Path) -> None:
    diag = tmp_path / "diag"
    diag.mkdir()
    primary_text = "x" * 6000
    (diag / "primary_partial_response.txt").write_text(primary_text, encoding="utf-8")
    (diag / "json_repair_partial_thinking.txt").write_text("t" * 1144, encoding="utf-8")
    diags = {
        "output_dir": str(diag),
        "model_call_traces.json": {
            "primary": {
                "content_chars": 6000,
                "content_preview": primary_text[:1200],
                "content_path": str(diag / "primary_partial_response.txt"),
                "parse_error": "AI response contained an unterminated JSON object.",
            },
            "json_repair": {
                "thinking_chars": 1144,
                "thinking_path": str(diag / "json_repair_partial_thinking.txt"),
                "terminal_error": "model emitted thinking only for 60s and produced 0 final content chars",
            },
        },
    }

    primary, repair = harness.collect_model_call_traces(diags)
    fault = harness.choose_terminal_fault(
        response={"ok": False, "status": "abstain"},
        diags=diags,
        observed_fault=None,
        primary_trace=primary,
        repair_trace=repair,
        json_repair_attempted=True,
    )
    partials = harness.compatibility_partials(primary, repair)

    assert primary.content_chars == 6000
    assert primary.content_path
    assert repair.thinking_chars == 1144
    assert partials["partial_content_chars"] == primary.content_chars
    assert partials["partial_response_preview"] == primary.content_preview
    assert fault["terminal_fault_type"] == "json_repair_failed"
    assert fault["terminal_fault_source"] == "json_repair"


def test_repair_trace_does_not_overwrite_primary_preview(tmp_path: Path) -> None:
    primary = harness.ModelCallTrace(name="primary", content_chars=12, content_preview="primary text")
    repair = harness.ModelCallTrace(name="json_repair", content_chars=0, content_preview="")

    partials = harness.compatibility_partials(primary, repair)

    assert partials["partial_response_preview"] == "primary text"
    assert partials["partial_content_chars"] == 12


def test_provider_stream_error_skips_repair_fault_classification() -> None:
    event_payload = {
        "event": {"message": "GGML_ASSERT"},
        "data": {
            "error": "Ollama stream error: GGML_ASSERT(ctx->mem_buffer != NULL) failed",
            "terminal_fault_type": "provider_stream_error",
            "content_chars": 5,
            "thinking_chars": 7,
        },
    }
    primary = harness.ModelCallTrace(name="primary")
    repair = harness.ModelCallTrace(name="json_repair")

    fault = harness.choose_terminal_fault(
        response={"ok": False},
        diags={},
        observed_fault=event_payload,
        primary_trace=primary,
        repair_trace=repair,
        json_repair_attempted=False,
    )

    assert fault["terminal_fault_type"] == "provider_stream_error"
    assert fault["terminal_fault_source"] == "primary"
