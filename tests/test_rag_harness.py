from __future__ import annotations

import json
from pathlib import Path

from main_computer.activity import ActivityBus
from main_computer.rag_harness import parse_json_object, run_rag_harness


def test_rag_harness_no_model_writes_replayable_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main_computer").mkdir()
    (repo / "tests").mkdir()
    (repo / "main_computer" / "viewport_routes_executor.py").write_text(
        "class ViewportExecutorRoutesMixin:\n"
        "    def _handle_executor_run(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    (repo / "main_computer" / "executor_tool_loop.py").write_text(
        "EXECUTOR_TOOL_LOOP_SYSTEM_PROMPT = 'execute_shell docker executor'\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_executor_tool_loop.py").write_text("def test_loop(): pass\n", encoding="utf-8")
    (repo / "README.md").write_text("# Main Computer\nDocker Linux Executor\n", encoding="utf-8")

    result = run_rag_harness(
        prompt="Inspect the docker executor tool loop and propose the next backend step",
        repo_dir=repo,
        queries=["docker executor", "tool loop", "executor routes"],
        max_context_chars=12000,
        run_id="rag_test",
    )

    assert result.ok
    assert result.no_model
    assert result.task_decomposition["task_type"] == "executor_backend_review"
    assert result.retrieval.chunks
    assert result.final_plan["type"] == "plan"
    assert Path(result.output_dir, "run.json").exists()
    assert Path(result.output_dir, "grounded_prompt.txt").exists()
    assert Path(result.output_dir, "context_chunks.json").exists()
    assert Path(result.output_dir, "final_plan.json").exists()

    run = json.loads(Path(result.output_dir, "run.json").read_text(encoding="utf-8"))
    assert run["retrieval"]["used_chars"] <= run["retrieval"]["context_budget_chars"]
    evidence_paths = {item["path"] for item in run["final_plan"]["evidence"]}
    assert "main_computer/executor_tool_loop.py" in evidence_paths or "main_computer/viewport_routes_executor.py" in evidence_paths


def test_parse_json_object_accepts_fenced_or_prefixed_json() -> None:
    parsed = parse_json_object("Here:\n```json\n{\"type\":\"plan\"}\n```")
    assert parsed == {"type": "plan"}


def test_rag_harness_emits_activity_monitor_events(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main_computer").mkdir()
    (repo / "main_computer" / "rag_harness.py").write_text("def run_rag_harness(): pass\n", encoding="utf-8")
    (repo / "README.md").write_text("# Main Computer\nRAG activity monitor context\n", encoding="utf-8")

    bus = ActivityBus(repo)
    result = run_rag_harness(
        prompt="Push RAG activity into the activity monitor",
        repo_dir=repo,
        queries=["rag activity monitor"],
        run_id="rag_activity_test",
        activity_bus=bus,
    )

    assert result.ok
    rag_events = bus.events(filter_id="rag", limit=80)
    assert any(event["title"] == "RAG run started" for event in rag_events)
    assert any(event["data"].get("step") == "retrieval" and event["status"] == "completed" for event in rag_events)
    assert any(event["title"] == "RAG run completed" for event in rag_events)
    assert all(event["data"].get("raw_thinking") is None for event in rag_events)

    retrieval_events = [
        event
        for event in rag_events
        if event["data"].get("step") == "retrieval" and event["status"] == "completed"
    ]
    assert retrieval_events
    summary = retrieval_events[0]["data"]["summary"]
    assert summary["chunk_count"] >= 1
    assert "main_computer/rag_harness.py" in summary["top_paths"] or summary["chunk_paths"]
