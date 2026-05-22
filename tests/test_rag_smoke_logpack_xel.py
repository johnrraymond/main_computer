from __future__ import annotations

import json

from main_computer.rag_smoke_logpack_compact_ollama import (
    compress_compact_logpack,
    make_xel_cue,
)


def test_xel_cue_prefers_ai_relevant_semantic_anchors() -> None:
    cue = make_xel_cue(
        '"ERROR Traceback RuntimeError in main_computer/runtime/worker.py while calling Ollama model gemma4:26b"'
    )

    assert "traceback" in cue
    assert "runtime error" in cue
    assert "path" in cue
    assert "ollama" in cue


def test_compact_logpack_emits_xel_directives_with_cues() -> None:
    line = {
        "level": "ERROR",
        "component": "main_computer/runtime/worker.py",
        "message": "Traceback RuntimeError while calling Ollama model gemma4:26b",
        "detail": "main_computer_test/runtime/aider.log repeated failure marker",
    }
    raw_log = "\n".join(json.dumps(line, sort_keys=True) for _ in range(8)) + "\n"

    compressed, stats = compress_compact_logpack(
        raw_log,
        max_defs=12,
        pass_count=2,
        batch_defs=6,
        max_candidate_len=220,
        min_json_schema_count=99,
        max_schemas=4,
        max_line_prefixes=50,
        max_ngram_tokens=10_000,
        ngram_min_n=2,
        ngram_max_n=8,
        actual_score_limit=200,
        min_pass_gain_chars=0,
        debug_header=False,
        minimal_rules=False,
    )

    assert stats["xel_defs"] > 0
    assert "!xel " in compressed
    assert " cue=" in compressed
    assert " text=" in compressed
    assert "!lex " not in compressed
    assert "attention cue" in compressed
