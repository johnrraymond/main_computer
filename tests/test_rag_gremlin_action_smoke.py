from __future__ import annotations

import json

from main_computer import rag_gremlin_action_smoke as smoke
from main_computer import rag_gremlin_pyramid_atom_smoke as base


def test_provider_generate_payload_defaults_to_non_thinking_when_unspecified() -> None:
    payload, metadata = smoke.prepare_ollama_generate_payload(
        {"model": "example-model", "prompt": "prompt", "stream": True}
    )

    assert payload["think"] is False
    assert metadata["thinking_state"] == "off"
    assert metadata["think_source"] == "default_non_thinking"
    assert metadata["think_default_applied"] is True


def test_provider_generate_payload_preserves_explicit_thinking() -> None:
    payload, metadata = smoke.prepare_ollama_generate_payload(
        {"model": "example-model", "prompt": "prompt", "stream": True},
        think=True,
    )

    assert payload["think"] is True
    assert metadata["thinking_state"] == "on"
    assert metadata["think_source"] == "explicit_argument"
    assert metadata["think_default_applied"] is False


def test_empty_generated_action_source_is_invalid() -> None:
    ok, issues = smoke.validate_action_gremlin_source("", "empty_gremlin.py")

    assert not ok
    assert "missing top-level def main()" in issues


def test_invalid_python_source_is_rejected() -> None:
    ok, issues = smoke.validate_action_gremlin_source("def main(:\n    pass\n", "bad_gremlin.py")

    assert not ok
    assert any(issue.startswith("syntax error:") for issue in issues)


def test_valid_python_source_defining_top_level_main_is_accepted() -> None:
    source = "\n".join([
        "def main():",
        "    return None",
        "",
    ])
    ok, issues = smoke.validate_action_gremlin_source(source, "valid_gremlin.py")

    assert ok, issues


def test_old_action_contract_is_rejected() -> None:
    source = "\n".join([
        "def main():",
        "    def action(inputs):",
        "        return {}",
        "    return action",
        "",
    ])
    ok, issues = smoke.validate_generated_gremlin_source(source, "old_contract.py")

    assert not ok
    assert "disallowed action() contract" in issues


def test_empty_ai_pyramid_fails_without_fallback(tmp_path) -> None:
    log = base.Logger(tmp_path / "verbose.log", quiet=True)

    try:
        smoke.parse_ai_pyramid_or_fail("", tmp_path, log)
    except RuntimeError as exc:
        assert "AI pyramid produced no grep words" in str(exc)
    else:
        raise AssertionError("empty AI pyramid should fail")

    parse_error = json.loads((tmp_path / "02_parse_error.json").read_text(encoding="utf-8"))
    assert parse_error["ok"] is False
    assert parse_error["reason"] == "AI pyramid produced no grep words"
    assert not (tmp_path / "02_parse_fallback_pyramid.txt").exists()


def test_gremlin_generation_file_index_records_saved_artifacts(tmp_path) -> None:
    (tmp_path / "11_gremlin_generator_request.txt").write_text("request", encoding="utf-8")
    (tmp_path / "11_gremlin_generator_raw_response.jsonl").write_text('{"response": ""}\n', encoding="utf-8")
    (tmp_path / "11_gremlin_source.py").write_text("def main():\n    pass\n", encoding="utf-8")
    validation = {"ok": False, "issues": ["missing top-level def main()"]}

    payload = smoke.write_gremlin_generation_files(
        tmp_path,
        {"model": "example-model"},
        validation,
    )

    assert payload["files"]["request"]["exists"] is True
    assert payload["files"]["raw_response"]["size_bytes"] == len('{"response": ""}\n')
    assert payload["files"]["active_source"]["exists"] is True
    assert payload["files"]["post_payload"]["exists"] is False
    assert ("validation" + "_fallback") not in payload["files"]
    assert payload["gremlin_info"]["model"] == "example-model"
    assert payload["validation"] == validation
    assert (tmp_path / "11_gremlin_generation_files.json").exists()


def test_int_env_default_uses_default_for_missing_or_invalid() -> None:
    import os

    previous = os.environ.get("MAIN_COMPUTER_TEST_TIMEOUT_VALUE")
    os.environ.pop("MAIN_COMPUTER_TEST_TIMEOUT_VALUE", None)
    assert smoke.int_env_default("MAIN_COMPUTER_TEST_TIMEOUT_VALUE", 17) == 17

    try:
        os.environ["MAIN_COMPUTER_TEST_TIMEOUT_VALUE"] = "not-an-int"
        assert smoke.int_env_default("MAIN_COMPUTER_TEST_TIMEOUT_VALUE", 17) == 17

        os.environ["MAIN_COMPUTER_TEST_TIMEOUT_VALUE"] = "42"
        assert smoke.int_env_default("MAIN_COMPUTER_TEST_TIMEOUT_VALUE", 17) == 42
    finally:
        if previous is None:
            os.environ.pop("MAIN_COMPUTER_TEST_TIMEOUT_VALUE", None)
        else:
            os.environ["MAIN_COMPUTER_TEST_TIMEOUT_VALUE"] = previous

def test_default_gremlin_generation_timeout_disables_http_read_timeout_by_default() -> None:
    assert smoke.DEFAULT_GREMLIN_TIMEOUT_S == 0


def test_start_here_lists_generator_error_and_omits_missing_generation_artifacts(tmp_path) -> None:
    for name in [
        "01_ai_pyramid_text.txt",
        "05_word_hit_summary.json",
        "07_top_atoms.json",
        "08_selected_atom_buffer.txt",
        "10_symbol_context.txt",
        "11_gremlin_generator_request.txt",
        "11_gremlin_generator_post_payload.json",
        "11_gremlin_generator_error.json",
        "11_gremlin_generation_files.json",
        "11_gremlin_validation.json",
        "11_gremlin_source.py",
        "12_action_inputs.json",
        "12_action_driver.py",
        "12_action_driver_result.json",
        "12_gremlin_output.json",
        "12_gremlin_stdout.txt",
        "12_gremlin_stderr.txt",
        "12_gremlin_output_files.json",
        "12_action_result.json",
        "13_changed_files_verification.json",
        "14_patch_zip.json",
        "15_new_patch_dry_run.json",
    ]:
        (tmp_path / name).write_text("x", encoding="utf-8")

    start_here = smoke.make_start_here(
        out_dir=tmp_path,
        prompt="change something",
        ai_pyramid="chat",
        word_summary=[],
        selected_atoms=[],
        symbols_found=[],
        candidate_files=[],
        gremlin_info={
            "model": "example-model",
            "source_path": str(tmp_path / "11_gremlin_source.py"),
        },
        verification={"ok": False, "changed_file_count": 0, "no_change_reason": "needs_more_evidence"},
        patch_zip=None,
        new_patch_result={"ok": None},
    )

    assert "- generator error: `11_gremlin_generator_error.json`" in start_here
    assert "- `11_gremlin_generator_error.json`" in start_here
    assert "generator raw response" not in start_here
    assert "- `11_gremlin_generator_raw_response.jsonl`" not in start_here
    assert "generator info" not in start_here
    assert "- `11_gremlin_generator_info.json`" not in start_here
    assert ("validation " + "fallback") not in start_here


def test_start_here_surfaces_gremlin_mode_and_error(tmp_path) -> None:
    (tmp_path / "11_gremlin_generator_request.txt").write_text("request", encoding="utf-8")
    (tmp_path / "11_gremlin_generator_error.json").write_text('{"error": "TimeoutError: timed out"}', encoding="utf-8")
    (tmp_path / "11_gremlin_source.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (tmp_path / "11_gremlin_generation_files.json").write_text("{}", encoding="utf-8")
    (tmp_path / "11_gremlin_validation.json").write_text('{"ok": false}', encoding="utf-8")

    start_here = smoke.make_start_here(
        out_dir=tmp_path,
        prompt="change something",
        ai_pyramid="chat",
        word_summary=[],
        selected_atoms=[],
        symbols_found=[],
        candidate_files=[],
        gremlin_info={
            "ok": False,
            "mode": "ollama_failed",
            "model": "example-model",
            "error": "TimeoutError: timed out",
            "source_path": str(tmp_path / "11_gremlin_source.py"),
        },
        verification={"ok": False, "changed_file_count": 0, "no_change_reason": "needs_more_evidence"},
        patch_zip=None,
        new_patch_result={"ok": None},
    )

    assert "- active source: `" in start_here
    assert "- model: `example-model`" in start_here
    assert "- mode: `ollama_failed`" in start_here
    assert "- generation_ok: `False`" in start_here
    assert "- error: `TimeoutError: timed out`" in start_here


def test_ollama_generate_response_summary_exposes_empty_response_metadata() -> None:
    summary = base.summarize_ollama_generate_response(
        {
            "model": "gemma4:26b",
            "response": "",
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": 128,
            "eval_count": 0,
        },
        106.26,
    )

    assert summary["ok"] is True
    assert summary["response_chars"] == 0
    assert summary["response_empty"] is True
    assert summary["response_repr"] == "''"
    assert summary["done"] is True
    assert summary["done_reason"] == "stop"
    assert summary["prompt_eval_count"] == 128
    assert summary["eval_count"] == 0
    assert "response" in summary["keys"]


def test_parse_pyramid_orders_distal_terms_before_trunk_terms() -> None:
    parsed = base.parse_pyramid(
        "\n".join([
            "trunk",
            "  branch",
            "    twig",
            "      leafalpha",
            "      leafbeta",
            "    sideleaf",
            "  otherbranch",
            "    otherleaf",
            "    finial",
        ])
    )

    assert parsed["unique_terms"][:5] == ["leafalpha", "leafbeta", "sideleaf", "otherleaf", "finial"]
    assert parsed["unique_terms"][-1] == "trunk"
    assert parsed["term_scores"]["leafalpha"] > parsed["term_scores"]["twig"] > parsed["term_scores"]["branch"] > parsed["term_scores"]["trunk"]
    assert parsed["term_is_leaf"]["leafalpha"] is True
    assert parsed["term_is_leaf"]["trunk"] is False


def test_evidence_buffer_skips_oversized_atoms_and_keeps_later_small_atoms() -> None:
    atoms = [
        {
            "category": "same_line",
            "score": 100,
            "connected_words": ["rare"],
            "connected_word_count": 1,
            "layer_score_sum": 5,
            "path": "main_computer/large.py",
            "line_start": 1,
            "line_end": 1,
            "text": "x" * 500,
        },
        {
            "category": "same_line",
            "score": 90,
            "connected_words": ["rare", "small"],
            "connected_word_count": 2,
            "layer_score_sum": 9,
            "path": "main_computer/small.py",
            "line_start": 2,
            "line_end": 2,
            "text": "2:small",
        },
    ]

    evidence, selected, used = base.fill_evidence_buffer(
        atoms,
        max_chars=220,
        category_limits={"same_line": 220},
    )

    assert "large.py" not in evidence
    assert "small.py" in evidence
    assert [atom["path"] for atom in selected] == ["main_computer/small.py"]
    assert used["same_line"] > 0


def test_no_deterministic_stop_button_cheat_strings() -> None:
    source = smoke._THIS_FILE.read_text(encoding="utf-8")
    forbidden = [
        "lexical_action_gremlin_source",
        "deterministic_lexical",
        "RED_STYLE_LINES",
        "built stop button red replacement",
        "deterministic lexical gremlin only recognized stop-button-red requests",
        "if (cell.type === " + '"ai"' + " && cell.status === " + '"running"' + ") controls.append(chatConsoleButton(",
        "_stop_button_red_replacement",
        "_build_stop_button_red_change",
        "_prompt_wants_stop_button_red",
    ]

    for text in forbidden:
        assert text not in source


def test_default_source_scan_excludes_runner_and_tests() -> None:
    repo = smoke._THIS_FILE.parents[1]
    source_files = smoke.iter_action_source_files(repo, ["main_computer", "tests"], include_runner=False)
    rels = {path.relative_to(repo).as_posix() for path in source_files}

    assert "main_computer/rag_gremlin_action_smoke.py" not in rels
    assert "main_computer/rag_gremlin_pyramid_atom_smoke.py" not in rels
    assert "tests/test_rag_gremlin_action_smoke.py" not in rels


def test_top_atoms_for_prompt_skips_oversized_atoms_and_uses_later_small_atoms() -> None:
    atoms = [
        {
            "path": "main_computer/large.py",
            "line_start": 1,
            "line_end": 1,
            "category": "block_window",
            "connected_words": ["rare"],
            "connected_word_count": 1,
            "layer_score_sum": 5,
            "score": 100,
            "text": "x" * 500,
        },
        {
            "path": "main_computer/small.py",
            "line_start": 2,
            "line_end": 2,
            "category": "same_line",
            "connected_words": ["rare", "small"],
            "connected_word_count": 2,
            "layer_score_sum": 9,
            "score": 90,
            "text": "2:small",
        },
    ]

    packed = smoke.top_atoms_for_prompt(atoms, text_limit=260)

    assert [atom["path"] for atom in packed] == ["main_computer/small.py"]


def test_second_prompt_budget() -> None:
    duplicate_text = "same atom text\n" * 10
    atoms = []
    for idx in range(20):
        atoms.append({
            "path": f"main_computer/file_{idx % 2}.py",
            "line_start": idx + 1,
            "line_end": idx + 1,
            "category": "same_line" if idx % 3 else "nearby_window",
            "connected_words": ["alpha", "beta"],
            "connected_word_count": 2,
            "layer_score_sum": 10,
            "score": 100 - idx,
            "text": duplicate_text if idx < 4 else f"small {idx}",
        })

    prompt, stats = smoke.build_gremlin_generator_prompt(
        user_prompt="change something carefully",
        repo_name="repo",
        selected_atoms=atoms,
        symbol_context="symbol context\n" * 300,
        candidate_files=["main_computer/file_0.py", "main_computer/file_1.py"],
    )

    assert len(prompt) <= smoke.DEFAULT_SECOND_PROMPT_CHAR_LIMIT
    assert "Return only raw Python source code." in prompt
    assert "Do not return JSON." in prompt
    assert '"gremlin_source"' not in prompt
    assert prompt.count(duplicate_text.strip()) <= 1
    assert stats["selected_atom_count_for_ai"] <= 3
    assert stats["symbol_context_chars"] <= smoke.DEFAULT_SYMBOL_CONTEXT_CHAR_LIMIT
    assert stats["second_prompt_chars"] == len(prompt)


def test_strip_code_fence_accepts_python_source() -> None:
    source = smoke.strip_code_fence("```python\ndef main():\n    pass\n```")

    assert source == "def main():\n    pass\n"


def test_raw_python_empty_source_is_rejected_by_validator() -> None:
    ok, issues = smoke.validate_action_gremlin_source("", "generated.py")

    assert not ok
    assert "missing top-level def main()" in issues


def test_gremlin_driver_prints_compact_summary_not_full_payload(tmp_path) -> None:
    driver = smoke.build_gremlin_driver("gremlin.py", tmp_path)

    assert 'print(json.dumps(summary' in driver
    assert 'module.main()' in driver
    assert 'module.main(' in driver
    assert 'print(json.dumps(output' not in driver
