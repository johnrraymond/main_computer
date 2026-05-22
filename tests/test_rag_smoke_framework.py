from __future__ import annotations

import json
from pathlib import Path

from main_computer.rag_smoke_framework import (
    MiniRagFramework,
    RECOMMENDED_SMOKE_CONCEPTS,
    build_repo_map,
    run_recommended_smoke_suite,
)


def test_recommended_rag_smoke_suite_runs_all_14_concepts(tmp_path: Path) -> None:
    outcomes = run_recommended_smoke_suite(tmp_path)

    assert len(RECOMMENDED_SMOKE_CONCEPTS) == 14
    assert len(outcomes) == 14
    assert {outcome.name for outcome in outcomes} == {concept.name for concept in RECOMMENDED_SMOKE_CONCEPTS}
    assert all(outcome.ok for outcome in outcomes), [outcome.as_dict() for outcome in outcomes if not outcome.ok]

    trace_path = tmp_path / "rag_smoke_trace.json"
    assert trace_path.exists()
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    assert trace["schema_version"] == 1
    assert trace["selected_context"][0] == "src/widget_lock.py"


def test_smoke_framework_exposes_implementation_descriptions() -> None:
    assert all(concept.description for concept in RECOMMENDED_SMOKE_CONCEPTS)
    assert all(concept.implementation_hint for concept in RECOMMENDED_SMOKE_CONCEPTS)

    ids = [concept.id for concept in RECOMMENDED_SMOKE_CONCEPTS]
    assert ids == [
        "hybrid_retrieval",
        "contextual_chunk_enrichment",
        "parent_child_neighbor_expansion",
        "score_threshold_abstention",
        "query_rewrite_multi_query",
        "crag_retrieval_evaluator",
        "self_rag_critique_loop",
        "raptor_tree_hierarchy",
        "graphrag_local_global",
        "repo_map_ast_symbols",
        "file_caps_compaction",
        "retrieved_prompt_injection_guard",
        "precision_recall_goldset",
        "retrieval_trace_artifact",
    ]


def test_shared_retriever_supports_abstention_and_traceable_scores() -> None:
    framework = MiniRagFramework.from_texts(
        {
            "docs/noise.md": "This document repeats refund refund but never names the requested policy.",
            "docs/policy.md": "RefundPolicyV9 is governed by the private beta refund workflow.",
        }
    )

    results = framework.retrieve("RefundPolicyV9 refund workflow", mode="hybrid")
    evaluation = framework.evaluate_retrieval("RefundPolicyV9 refund workflow", results, min_top_score=5)

    assert results[0].chunk.path == "docs/policy.md"
    assert results[0].lexical_score > 0
    assert results[0].semantic_score > 0
    assert "lexical:" in " ".join(results[0].reasons)
    assert evaluation.sufficient


def test_repo_map_indexes_definitions_imports_and_calls() -> None:
    repo_map = build_repo_map(
        {
            "main_computer/example.py": (
                "from main_computer.helpers import normalize_user_id\n"
                "class UserService:\n"
                "    def load(self, raw):\n"
                "        return normalize_user_id(raw)\n"
            )
        }
    )

    assert repo_map.definitions["UserService"] == "main_computer/example.py"
    assert repo_map.definitions["load"] == "main_computer/example.py"
    assert "main_computer.helpers.normalize_user_id" in repo_map.imports["main_computer/example.py"]
    assert "normalize_user_id" in repo_map.calls["main_computer/example.py:load"]
    assert repo_map.signatures["load"] == "def load(self, raw)"
