from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from main_computer.rag_harness import run_rag_harness
from main_computer.rag_smoke_subscripts_v1.contract import (
    StateProvision,
    StateRequirement,
    StepContract,
    StepResult,
    require_state,
    step_cli,
    write_json,
)

CONTRACT = StepContract(
    step_id="harness_no_model",
    version="rag_smoke_assembly_v1",
    description=(
        "Run the no-model RAG harness in an isolated fixture repo and verify replayable artifacts. "
        "This proves the assembly can exercise the production harness without model-side shortcuts."
    ),
    requires=(
        StateRequirement("repo_root", "Repository root inventoried by repo_shape."),
        StateRequirement("framework_goldset_proven", "Mini framework boundary succeeded."),
        StateRequirement("retriever_trace_path", "Retriever trace artifact from the deterministic boundary."),
    ),
    provides=(
        StateProvision("no_model_harness_proven", "True when run_rag_harness emits expected replayable artifacts."),
        StateProvision("harness_run_json_path", "run.json emitted by run_rag_harness."),
        StateProvision("harness_final_plan_path", "final_plan.json emitted by run_rag_harness."),
        StateProvision("harness_summary_path", "Assembly-specific no-model harness summary."),
    ),
    evidence_required=("harness_run_json_path", "harness_final_plan_path"),
)


def _write_fixture(fixture_repo: Path) -> None:
    (fixture_repo / "main_computer").mkdir(parents=True, exist_ok=True)
    (fixture_repo / "tests").mkdir(parents=True, exist_ok=True)
    (fixture_repo / "README.md").write_text(
        "# Main Computer fixture\nDocker Linux Executor and RAG assembly contracts live here.\n",
        encoding="utf-8",
    )
    (fixture_repo / "main_computer" / "executor_tool_loop.py").write_text(
        "EXECUTOR_TOOL_LOOP_SYSTEM_PROMPT = 'execute_shell docker executor boundary contract'\n",
        encoding="utf-8",
    )
    (fixture_repo / "main_computer" / "viewport_routes_executor.py").write_text(
        "class ViewportExecutorRoutesMixin:\n"
        "    def _handle_executor_run(self):\n"
        "        return 'docker executor boundary'\n",
        encoding="utf-8",
    )
    (fixture_repo / "tests" / "test_executor_tool_loop.py").write_text(
        "def test_loop_contract():\n"
        "    assert 'docker executor'\n",
        encoding="utf-8",
    )


def run(repo_dir: Path, output_dir: Path, state: dict[str, Any]) -> StepResult:
    require_state(state, "repo_root", "framework_goldset_proven", "retriever_trace_path")
    if Path(state["repo_root"]).resolve() != repo_dir:
        raise ValueError("input repo_root does not match --repo-dir")
    if state.get("framework_goldset_proven") is not True:
        raise ValueError("framework gold-set boundary did not prove its state")
    if not Path(str(state["retriever_trace_path"])).exists():
        raise ValueError("retriever trace path from prior state does not exist")

    fixture_repo = output_dir / "fixture_repo"
    _write_fixture(fixture_repo)

    harness_output_root = output_dir / "harness_runs"
    result = run_rag_harness(
        prompt="Inspect the docker executor tool loop and propose the next backend step",
        repo_dir=fixture_repo,
        queries=["docker executor", "tool loop", "executor routes", "boundary contract"],
        output_root=harness_output_root,
        run_id="assembly_v1_no_model",
        max_context_chars=12000,
        use_model=False,
    )
    if not result.ok:
        raise ValueError(f"run_rag_harness returned non-ok status: {result.status}")

    run_dir = Path(result.output_dir)
    required_files = {
        "run": run_dir / "run.json",
        "grounded_prompt": run_dir / "grounded_prompt.txt",
        "context_chunks": run_dir / "context_chunks.json",
        "final_plan": run_dir / "final_plan.json",
    }
    missing = [name for name, path in required_files.items() if not path.exists()]
    if missing:
        raise ValueError(f"missing harness artifacts: {missing}")

    run_payload = json.loads(required_files["run"].read_text(encoding="utf-8"))
    evidence_paths = {
        item.get("path")
        for item in run_payload.get("final_plan", {}).get("evidence", [])
        if isinstance(item, dict)
    }
    expected_evidence = {
        "main_computer/executor_tool_loop.py",
        "main_computer/viewport_routes_executor.py",
    }
    if not evidence_paths.intersection(expected_evidence):
        raise ValueError(f"final plan did not cite expected harness fixture evidence: {sorted(evidence_paths)}")

    summary = {
        "schema_version": 1,
        "step_id": CONTRACT.step_id,
        "harness_status": result.status,
        "run_dir": str(run_dir),
        "evidence_paths": sorted(path for path in evidence_paths if path),
        "required_files": {name: str(path) for name, path in required_files.items()},
    }
    summary_path = output_dir / "harness_no_model_summary.json"
    write_json(summary_path, summary)

    return StepResult(
        step_id=CONTRACT.step_id,
        status="ok",
        provided_state={
            "no_model_harness_proven": True,
            "harness_run_json_path": str(required_files["run"]),
            "harness_final_plan_path": str(required_files["final_plan"]),
            "harness_summary_path": str(summary_path),
        },
        evidence={
            "harness_run_json_path": str(required_files["run"]),
            "harness_final_plan_path": str(required_files["final_plan"]),
            "harness_summary_path": str(summary_path),
        },
        details={
            "harness_status": result.status,
            "evidence_paths": sorted(path for path in evidence_paths if path),
            "run_dir": str(run_dir),
        },
    )


def main(argv: list[str] | None = None) -> int:
    return step_cli(CONTRACT, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
