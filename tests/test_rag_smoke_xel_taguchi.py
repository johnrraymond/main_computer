from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

from main_computer.rag_smoke_xel_taguchi import (
    DEFAULT_MAX_CHARS_PER_FILE,
    DEFAULT_PROMPT_STYLE,
    DEFAULT_RETENTION_TEST,
    HOLDOUT_UNLOCK,
    CORPUS_PAIRS,
    FACTOR_NAMES,
    RETENTION_TESTS,
    TAGUCHI_L12_10,
    HoldoutLockedError,
    OllamaError,
    load_corpus,
    build_prompt,
    ollama_answer,
    parse_args,
    parse_timeout_seconds,
    preflight_ollama_model,
    render_language,
    resolve_retention_tests,
    run_suite,
    select_corpus_paths,
    taguchi_variants,
    validate_taguchi_table,
)


def test_taguchi_table_balances_ten_language_rules() -> None:
    validate_taguchi_table()

    assert len(FACTOR_NAMES) == 10
    assert len(TAGUCHI_L12_10) == 12
    assert all(len(row) == 10 for row in TAGUCHI_L12_10)

    for column_index in range(10):
        column = [row[column_index] for row in TAGUCHI_L12_10]
        assert column.count(1) == 6
        assert column.count(2) == 6

    signed_columns = [
        [1 if row[column_index] == 1 else -1 for row in TAGUCHI_L12_10]
        for column_index in range(10)
    ]
    for left_index, left in enumerate(signed_columns):
        for right_index, right in enumerate(signed_columns):
            if left_index >= right_index:
                continue
            assert sum(a * b for a, b in zip(left, right, strict=True)) == 0


def test_doubles_are_locked_behind_exact_cli_switch() -> None:
    with pytest.raises(HoldoutLockedError):
        select_corpus_paths("doubles")

    with pytest.raises(HoldoutLockedError):
        select_corpus_paths("doubles", unlock_holdout="wrong")

    unlocked = select_corpus_paths("doubles", unlock_holdout=HOLDOUT_UNLOCK)
    assert len(unlocked) == 10
    assert all(path == pair["double"] for (_, path), pair in zip(unlocked, CORPUS_PAIRS, strict=True))


def test_visible_selection_does_not_load_double_paths() -> None:
    visible = select_corpus_paths("visible")
    double_paths = {pair["double"] for pair in CORPUS_PAIRS}

    assert len(visible) == 10
    assert not {path for _, path in visible} & double_paths


def test_default_and_extra_retention_tests_are_available() -> None:
    assert DEFAULT_RETENTION_TEST == "default"
    assert DEFAULT_RETENTION_TEST in RETENTION_TESTS
    assert {"path", "symbol", "entity", "quote", "contrast"}.issubset(RETENTION_TESTS)

    assert resolve_retention_tests(None) == ["default"]
    assert resolve_retention_tests(["path", "symbol"]) == ["path", "symbol"]
    assert set(resolve_retention_tests(["all"])) == set(RETENTION_TESTS)


def test_cli_defaults_use_small_fast_prompts() -> None:
    args = parse_args([])

    assert args.max_chars_per_file == DEFAULT_MAX_CHARS_PER_FILE
    assert args.max_chars_per_file <= 1500
    assert args.prompt_style == DEFAULT_PROMPT_STYLE == "compact"
    assert args.include_factor_manifest is False
    assert args.log_progress is True
    assert args.log_every == 1


def test_ollama_timeouts_are_configurable_from_cli() -> None:
    args = parse_args(["--ollama-timeout", "600", "--ollama-preflight-timeout", "45"])

    assert args.ollama_timeout == 600.0
    assert args.ollama_preflight_timeout == 45.0


def test_ollama_timeout_zero_disables_timeout() -> None:
    assert parse_timeout_seconds("0") is None

    args = parse_args(["--ollama-timeout", "0", "--ollama-preflight-timeout", "0"])
    assert args.ollama_timeout is None
    assert args.ollama_preflight_timeout is None


def test_rendered_language_uses_robotic_double_colon_records() -> None:
    repo_dir = Path.cwd()
    case = load_corpus(repo_dir, "visible", max_chars_per_file=1200)[0]
    variant = taguchi_variants(max_variants=1)[0]
    rendered = render_language(case, variant)

    assert "!xel::variant::taguchi_l12_01" in rendered
    assert "!xel::factor::delimiter_style" not in rendered
    assert "§0::class::file_path" in rendered
    assert "§0::text::" in rendered
    assert "#body" in rendered


def test_compact_prompt_is_smaller_than_full_prompt() -> None:
    repo_dir = Path.cwd()
    case = load_corpus(repo_dir, "visible", max_chars_per_file=DEFAULT_MAX_CHARS_PER_FILE)[0]
    variant = taguchi_variants(max_variants=1)[0]
    encoded = render_language(case, variant)
    question = RETENTION_TESTS["default"](case)

    compact = build_prompt(encoded, question, prompt_style="compact")
    full = build_prompt(encoded, question, prompt_style="full")

    assert compact.startswith("Use XEL exactly.")
    assert "You are evaluating an experimental compressed language" not in compact
    assert len(compact) < len(full)


def test_run_suite_smokes_default_visible_without_model(tmp_path: Path) -> None:
    results, summary = run_suite(
        repo_dir=Path.cwd(),
        split="visible",
        unlock_holdout=None,
        retention_tests=["default"],
        max_files=2,
        max_variants=3,
        max_chars_per_file=1200,
        provider="oracle",
        ollama_model="unused",
        ollama_url="http://127.0.0.1:11434",
        temperature=0.0,
        ollama_timeout=180.0,
        ollama_preflight_timeout=20.0,
    )

    assert len(results) == 2 * 3
    assert summary["planned_evaluations"] == 6
    assert summary["scored_evaluations"] == 6
    assert summary["passed_evaluations"] == 6
    assert summary["split"] == "visible"
    assert summary["holdout_unlock_required"] == HOLDOUT_UNLOCK
    assert summary["prompt_style"] == DEFAULT_PROMPT_STYLE
    assert summary["max_chars_per_file"] == 1200


def test_run_suite_logs_progress_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    results, summary = run_suite(
        repo_dir=Path.cwd(),
        split="visible",
        unlock_holdout=None,
        retention_tests=["default"],
        max_files=1,
        max_variants=1,
        max_chars_per_file=400,
        provider="oracle",
        ollama_model="unused",
        ollama_url="http://127.0.0.1:11434",
        temperature=0.0,
        ollama_timeout=180.0,
        ollama_preflight_timeout=20.0,
        log_progress=True,
        log_every=1,
    )

    captured = capsys.readouterr()
    assert len(results) == 1
    assert summary["planned_evaluations"] == 1
    assert "[xel-taguchi] start" in captured.err
    assert "[xel-taguchi] eval 1/1" in captured.err
    assert "[xel-taguchi] done total=1" in captured.err


class _FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_ollama_preflight_reports_missing_model_before_eval_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: object, timeout: int) -> _FakeResponse:
        assert timeout == 7.5
        assert getattr(request, "full_url").endswith("/api/tags")
        return _FakeResponse({"models": [{"name": "llama3.2:latest"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(OllamaError) as excinfo:
        preflight_ollama_model(model="llama3.1", url="http://127.0.0.1:11434", timeout=7.5)

    message = str(excinfo.value)
    assert "llama3.1" in message
    assert "llama3.2:latest" in message
    assert "ollama pull llama3.1" in message


def test_ollama_answer_uses_cli_timeout_value(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_urlopen(request: object, timeout: object) -> _FakeResponse:
        seen["timeout"] = timeout
        seen["url"] = getattr(request, "full_url")
        return _FakeResponse({"response": "ok"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    answer = ollama_answer(
        "hello",
        model="llama3.1",
        url="http://127.0.0.1:11434",
        temperature=0.0,
        timeout=612.5,
    )

    assert answer == "ok"
    assert seen["timeout"] == 612.5
    assert str(seen["url"]).endswith("/api/generate")


def test_ollama_http_error_includes_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeHTTPError(urllib.error.HTTPError):
        def read(self) -> bytes:  # type: ignore[override]
            return b'{"error":"model not found"}'

    def fake_urlopen(request: object, timeout: int) -> _FakeResponse:
        raise FakeHTTPError(getattr(request, "full_url"), 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(OllamaError) as excinfo:
        ollama_answer("hello", model="missing-model", url="http://127.0.0.1:11434", temperature=0.0)

    message = str(excinfo.value)
    assert "HTTP 404" in message
    assert "model not found" in message
    assert "/api/generate" in message
