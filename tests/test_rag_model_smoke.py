from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

import pytest

from main_computer.executor_tool_loop import ExecutorToolLoopResult, ExecutorToolLoopStep
from main_computer.models import ChatMessage, ChatResponse
from main_computer.rag_model_smoke import (
    BROKEN_CODE_REPAIR_PROMPT,
    BROKEN_CODE_REPAIR_QUERIES,
    CSV_AUDIT_SCRIPT_PROMPT,
    _executor_context_from_rag_result,
    parse_args,
    run_broken_code_repair_docker_smoke,
    run_csv_audit_model_smoke,
    validate_broken_code_repair_docker_smoke,
    validate_csv_audit_model_smoke,
)
from main_computer.rag_harness import run_rag_harness


class FakeModelProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.calls = 0
        self.fallback = False

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.calls += 1
        if self.calls == 1:
            content = json.dumps(
                {
                    "task_type": "standalone_python_script",
                    "goal": "Create and verify a standalone CSV audit CLI script.",
                    "needs": [
                        "executor tool loop context",
                        "docker executor artifact behavior",
                        "route context",
                    ],
                    "retrieval_queries": [
                        "standalone python script",
                        "docker executor",
                        "executor tool loop",
                        "artifact outputs",
                    ],
                    "candidate_paths": [
                        "main_computer/docker_executor.py",
                        "main_computer/executor_tool_loop.py",
                        "main_computer/viewport_routes_executor.py",
                    ],
                    "executor_likely_needed": True,
                    "risk": "may_need_writes",
                }
            )
        else:
            content = json.dumps(
                {
                    "type": "plan",
                    "summary": "Create the CSV audit script in executor workspace, test with fixtures, and publish outputs as artifacts.",
                    "evidence": [
                        {
                            "path": "main_computer/docker_executor.py",
                            "reason": "Defines /inputs and /outputs mounts and artifact collection.",
                        },
                        {
                            "path": "main_computer/executor_tool_loop.py",
                            "reason": "Defines the AI-to-executor tool request flow.",
                        },
                    ],
                    "next_step": {
                        "kind": "proposal",
                        "description": "Ask for approval to draft csv_audit.py in executor workspace and run fixture tests.",
                        "requires_executor": True,
                        "requires_approval": True,
                    },
                    "open_questions": [],
                }
            )
        return ChatResponse(content=content, provider=self.name, model=self.model)


def _write_fixture_repo(repo: Path) -> None:
    (repo / "main_computer").mkdir()
    (repo / "tests").mkdir()
    (repo / "main_computer" / "docker_executor.py").write_text(
        "class DockerExecutor:\n"
        "    inputs_root = '/inputs'\n"
        "    outputs_root = '/outputs'\n"
        "    def run(self): pass\n",
        encoding="utf-8",
    )
    (repo / "main_computer" / "executor_tool_loop.py").write_text(
        "EXECUTOR_TOOL_LOOP_SYSTEM_PROMPT = 'execute_shell artifacts approval'\n",
        encoding="utf-8",
    )
    (repo / "main_computer" / "viewport_routes_executor.py").write_text(
        "class ViewportExecutorRoutesMixin:\n"
        "    def _handle_executor_ai(self): pass\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_executor_tool_loop.py").write_text("def test_loop(): pass\n", encoding="utf-8")
    (repo / "README.md").write_text("# Main Computer\nDocker Linux Executor\n", encoding="utf-8")


def test_csv_audit_model_smoke_accepts_structured_model_plan(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fixture_repo(repo)
    provider = FakeModelProvider()

    report = run_csv_audit_model_smoke(
        repo_dir=repo,
        output_root=tmp_path / "runs",
        provider=provider,
        run_id="csv_model_smoke",
        strict=True,
    )

    assert report.ok
    assert report.run_id == "csv_model_smoke"
    assert report.baseline_run_id == "csv_model_smoke_baseline"
    assert report.final_plan_type == "plan"
    assert not report.failures
    assert not report.warnings
    assert "main_computer/docker_executor.py" in report.retrieved_paths
    assert Path(report.output_dir, "model_smoke_report.json").exists()


def test_csv_audit_model_smoke_validation_warns_on_ungrounded_plan(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fixture_repo(repo)

    result = run_rag_harness(
        prompt=CSV_AUDIT_SCRIPT_PROMPT,
        repo_dir=repo,
        queries=["docker executor", "executor tool loop"],
        output_root=tmp_path / "runs",
        provider=FakeModelProvider(),
        use_model=True,
        run_id="weak_validation",
    )
    weak_plan = dict(result.final_plan)
    weak_plan["evidence"] = []
    weak_plan["next_step"] = {"kind": "proposal", "requires_executor": False, "requires_approval": False}
    object.__setattr__(result, "final_plan", weak_plan)

    report = validate_csv_audit_model_smoke(result, strict=False)

    assert report.ok
    assert report.warnings
    assert any("evidence is empty" in item for item in report.warnings)
    assert any("requires_executor=true" in item for item in report.warnings)


def test_csv_audit_model_smoke_verbose_enables_streaming_provider(tmp_path: Path, capsys) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fixture_repo(repo)
    provider = FakeModelProvider()

    report = run_csv_audit_model_smoke(
        repo_dir=repo,
        output_root=tmp_path / "runs",
        provider=provider,
        run_id="csv_model_smoke_verbose",
        strict=True,
        run_baseline=False,
        verbose=True,
        stream_model=True,
    )

    captured = capsys.readouterr()
    assert report.ok
    assert provider.fallback is True
    assert "[rag-model-smoke] enabled provider fallback/streaming" in captured.err
    assert "[rag-model-smoke] model-backed step 02 task_decomposition" in captured.err
    assert "[rag-model-smoke] validation report:" in captured.err
    assert "full result raw JSON" not in captured.err
    assert "class DockerExecutor" not in captured.err


def test_rag_model_smoke_cli_defaults_to_csv_verbose_streaming() -> None:
    args = parse_args([])

    assert args.scenario == "csv-audit"
    assert args.quiet is False
    assert args.no_stream is False
    assert args.dump_json is False


def test_rag_model_smoke_dump_json_is_opt_in() -> None:
    args = parse_args(["--dump-json"])

    assert args.dump_json is True


def test_broken_code_repair_executor_context_is_concise(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fixture_repo(repo)

    result = run_rag_harness(
        prompt=BROKEN_CODE_REPAIR_PROMPT,
        repo_dir=repo,
        queries=BROKEN_CODE_REPAIR_QUERIES,
        output_root=tmp_path / "runs",
        provider=FakeModelProvider(),
        use_model=True,
        run_id="broken_code_repair_context",
    )

    context = _executor_context_from_rag_result(result)

    assert "Retrieved path summary:" in context
    assert "Do not import main_computer" in context
    assert "class DockerExecutor" not in context
    assert "EXECUTOR_TOOL_LOOP_SYSTEM_PROMPT" not in context


def test_broken_code_repair_validation_requires_docker_failure_then_pass(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fixture_repo(repo)

    result = run_rag_harness(
        prompt=BROKEN_CODE_REPAIR_PROMPT,
        repo_dir=repo,
        queries=BROKEN_CODE_REPAIR_QUERIES,
        output_root=tmp_path / "runs",
        provider=FakeModelProvider(),
        use_model=True,
        run_id="broken_code_repair_validation",
    )
    executor_result = ExecutorToolLoopResult(
        ok=True,
        status="complete",
        provider="fake",
        model="fake-tool-model",
        final_content="Initial tests failed, repair was applied, final tests passed.",
        steps=[
            ExecutorToolLoopStep(index=1, kind="model", content='{"action":"execute_shell"}'),
            ExecutorToolLoopStep(
                index=1,
                kind="command_output",
                executor_result={
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "INITIAL_FAILURE_CONFIRMED\nFINAL_REPAIR_PASSED\n",
                    "stderr": "",
                    "artifacts": [{"relative_path": "repair_report.json", "name": "repair_report.json"}],
                },
            ),
        ],
    )

    report = validate_broken_code_repair_docker_smoke(
        result,
        executor_result=executor_result,
        docker_status={"enabled": True, "docker_available": True},
        strict=True,
    )

    assert report.ok
    assert report.scenario == "broken_code_repair_docker"
    assert report.executor_status == "complete"
    assert report.docker_available is True
    assert "repair_report.json" in report.artifact_paths
    assert not report.failures


def test_broken_code_repair_validation_fails_without_docker_execution(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_fixture_repo(repo)

    result = run_rag_harness(
        prompt=BROKEN_CODE_REPAIR_PROMPT,
        repo_dir=repo,
        queries=BROKEN_CODE_REPAIR_QUERIES,
        output_root=tmp_path / "runs",
        provider=FakeModelProvider(),
        use_model=True,
        run_id="broken_code_repair_no_docker",
    )

    report = validate_broken_code_repair_docker_smoke(
        result,
        executor_result=None,
        docker_status={"enabled": True, "docker_available": False, "docker_error": "docker unavailable"},
        strict=False,
    )

    assert not report.ok
    assert any("Docker is required" in item for item in report.failures)
    assert any("Executor tool loop did not run" in item for item in report.failures)


def test_rag_model_smoke_cli_accepts_broken_code_repair_scenario() -> None:
    args = parse_args(["--scenario", "broken-code-repair", "--max-executor-steps", "6"])

    assert args.scenario == "broken-code-repair"
    assert args.max_executor_steps == 6


def test_broken_code_repair_docker_smoke_uses_real_docker_when_enabled(tmp_path: Path) -> None:
    if os.environ.get("MAIN_COMPUTER_RUN_DOCKER_SMOKE") != "1":
        pytest.skip("Set MAIN_COMPUTER_RUN_DOCKER_SMOKE=1 to run the live Docker/model repair smoke.")

    report = run_broken_code_repair_docker_smoke(
        repo_dir=Path.cwd(),
        output_root=tmp_path / "runs",
        run_id="broken_code_repair_live",
        strict=True,
        max_executor_steps=4,
        verbose=True,
        stream_model=True,
    )

    assert report.ok
    assert report.docker_available is True
    assert report.executor_status == "complete"
    assert "repair_report.json" in report.artifact_paths
