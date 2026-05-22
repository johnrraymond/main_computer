from __future__ import annotations

import json
from pathlib import Path

from main_computer import rag_new_patch_latest_output_compile_smoke as smoke


def test_unique_output_path_does_not_overwrite_existing_file(tmp_path: Path) -> None:
    first = tmp_path / "ai_repaired_new_patch_candidate.py"
    second = tmp_path / "ai_repaired_new_patch_candidate_2.py"
    first.write_text("old", encoding="utf-8")

    assert smoke.unique_output_path(tmp_path, "ai_repaired_new_patch_candidate.py") == second


def test_build_repair_prompt_contains_compile_error_and_candidate_path(tmp_path: Path) -> None:
    candidate = tmp_path / "proposed_new_patch.py"
    candidate.write_text('if __name__ == "__main__":\n    main"\n', encoding="utf-8")
    latest = smoke.LatestRun(run_dir=tmp_path, proposed_file=candidate, master_results=None)
    compile_result = smoke.CompileResult(
        returncode=1,
        stdout="",
        stderr='SyntaxError: unterminated string literal (detected at line 2)',
    )

    prompt = smoke.build_repair_prompt(
        latest=latest,
        candidate_path=candidate,
        compile_result=compile_result,
        candidate_source=candidate.read_text(encoding="utf-8"),
        max_source_chars=1000,
    )

    assert "Fix the candidate so it is syntactically valid Python" in prompt
    assert "unterminated string literal" in prompt
    assert "Source excerpt around failing line" in prompt
    assert '>> 2:     main"' in prompt
    assert str(candidate) in prompt
    assert "Do not overwrite or modify the selected candidate file" in prompt


def test_extract_repaired_code_from_route_diagnostics_result_json(tmp_path: Path) -> None:
    repo = tmp_path
    run_id = "repair_test"
    output_dir = repo / "diagnostics_output" / "rag_assisted_thinking_v4_routes" / run_id
    output_dir.mkdir(parents=True)
    (output_dir / "result.json").write_text(
        json.dumps(
            {
                "repair_payload": {
                    "files": [
                        {
                            "path": "new_patch.py",
                            "content": "print('fixed')\n",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    code, searched = smoke.extract_repaired_code(repo, run_id, {"payload": {"ok": True}})

    assert code == "print('fixed')\n"
    assert any("result.json" in item for item in searched)


def test_extract_repaired_code_from_response_payload_when_no_diagnostics(tmp_path: Path) -> None:
    response = {
        "payload": {
            "files": [
                {
                    "path": "new_patch.py",
                    "content": "print('from response')\n",
                }
            ]
        }
    }

    code, searched = smoke.extract_repaired_code(tmp_path, "missing_run", response)

    assert code == "print('from response')\n"
    assert searched[-1] == "route response payload"


def test_sha256_original_stability_check_can_detect_changes(tmp_path: Path) -> None:
    path = tmp_path / "proposed_new_patch.py"
    path.write_text("one\n", encoding="utf-8")
    before = smoke.sha256_file(path)
    path.write_text("two\n", encoding="utf-8")
    after = smoke.sha256_file(path)

    assert before != after


def test_load_smoke_output_dir_requires_explicit_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "debug_assets" / "rag_new_patch_recreation_tester" / "rag_new_patch_001" / "20260101_010101"
    run_dir.mkdir(parents=True)
    proposed = run_dir / "proposed_new_patch.py"
    proposed.write_text("print('candidate')\n", encoding="utf-8")
    (run_dir / "master_results.json").write_text("{}", encoding="utf-8")

    latest = smoke.load_smoke_output_dir(tmp_path, run_dir.relative_to(tmp_path))

    assert latest.run_dir == run_dir.resolve()
    assert latest.proposed_file == proposed
    assert latest.master_results == run_dir / "master_results.json"


def test_make_repair_run_dir_creates_child_directory_without_reusing_existing(tmp_path: Path) -> None:
    first = smoke.make_repair_run_dir(tmp_path, "repair_run")
    second = smoke.make_repair_run_dir(tmp_path, "repair_run")

    assert first == tmp_path / "compile_repair_smoke_runs" / "repair_run"
    assert second == tmp_path / "compile_repair_smoke_runs" / "repair_run_1"
    assert first.is_dir()
    assert second.is_dir()


def test_find_route_output_dir_does_not_glob_stale_repair_runs(tmp_path: Path) -> None:
    repo = tmp_path
    stale = repo / "diagnostics_output" / "rag_assisted_thinking_v4_routes" / "prefix_repair_test_suffix"
    stale.mkdir(parents=True)

    assert smoke.find_route_output_dir(repo, "repair_test", {"ok": True}) is None


def test_append_session_log_delta_writes_provider_raw(tmp_path: Path) -> None:
    run_id = "repair_raw_test"
    session_dir = tmp_path / "diagnostics_output" / "chat_console_ai_sessions" / run_id
    session_dir.mkdir(parents=True)
    session_log = session_dir / "session.log"
    session_log.write_text("thinking_delta content_chars=0 thinking_chars=12\n", encoding="utf-8")

    provider_raw = tmp_path / "provider.raw"
    state: dict[str, int | bool] = {"offset": 0, "header_written": False}

    copied = smoke.append_session_log_delta(
        repo_root=tmp_path,
        run_id=run_id,
        provider_raw_path=provider_raw,
        state=state,
    )

    assert copied > 0
    text = provider_raw.read_text(encoding="utf-8")
    assert "route session log raw stream" in text
    assert "thinking_delta" in text

    session_log.write_text(
        "thinking_delta content_chars=0 thinking_chars=12\ncontent_delta content_chars=9 thinking_chars=12\n",
        encoding="utf-8",
    )
    copied_again = smoke.append_session_log_delta(
        repo_root=tmp_path,
        run_id=run_id,
        provider_raw_path=provider_raw,
        state=state,
    )

    assert copied_again > 0
    assert "content_delta" in provider_raw.read_text(encoding="utf-8")


def test_append_raw_creates_render_raw(tmp_path: Path) -> None:
    render_raw = tmp_path / "render.raw"

    smoke.append_raw(render_raw, smoke.raw_header("render snapshot") + "elapsed_s=10.0\n")

    text = render_raw.read_text(encoding="utf-8")
    assert "render snapshot" in text
    assert "elapsed_s=10.0" in text


def test_compile_failure_context_extracts_failing_line_excerpt(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.py"
    source = 'def main():\n    pass\n\nif __name__ == "__main__":\n    main"\n'
    candidate.write_text(source, encoding="utf-8")
    result = smoke.CompileResult(
        returncode=1,
        stdout="",
        stderr=(
            '  File "candidate.py", line 5\n'
            '    main"\n'
            '        ^\n'
            'SyntaxError: unterminated string literal (detected at line 5)\n'
        ),
    )

    context = smoke.compile_failure_context(
        candidate_path=candidate,
        candidate_source=source,
        compile_result=result,
    )

    assert context["line_number"] == 5
    assert "unterminated string literal" in context["error_summary"]
    assert '>> 5:     main"' in context["source_excerpt"]


def test_resolve_candidate_path_accepts_relative_to_output_dir(tmp_path: Path) -> None:
    repo = tmp_path
    run_dir = repo / "debug_assets" / "rag_new_patch_recreation_tester" / "rag_new_patch_001" / "20260101_010101"
    repair_dir = run_dir / "compile_repair_smoke_runs" / "repair_1"
    repair_dir.mkdir(parents=True)
    candidate = repair_dir / "ai_repaired_new_patch_candidate.py"
    candidate.write_text("print('candidate')\n", encoding="utf-8")

    resolved = smoke.resolve_candidate_path(
        repo,
        run_dir,
        "compile_repair_smoke_runs/repair_1/ai_repaired_new_patch_candidate.py",
    )

    assert resolved == candidate.resolve()


def test_resolve_candidate_path_defaults_to_proposed_new_patch(tmp_path: Path) -> None:
    repo = tmp_path
    run_dir = repo / "debug_assets" / "rag_new_patch_recreation_tester" / "rag_new_patch_001" / "20260101_010101"
    run_dir.mkdir(parents=True)
    proposed = run_dir / "proposed_new_patch.py"
    proposed.write_text("print('candidate')\n", encoding="utf-8")

    assert smoke.resolve_candidate_path(repo, run_dir, None) == proposed.resolve()

