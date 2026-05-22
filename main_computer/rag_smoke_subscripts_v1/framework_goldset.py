from __future__ import annotations

from pathlib import Path
from typing import Any

from main_computer.rag_smoke_framework import RECOMMENDED_SMOKE_CONCEPTS, run_recommended_smoke_suite
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
    step_id="framework_goldset",
    version="rag_smoke_assembly_v1",
    description=(
        "Run the existing mini RAG framework gold-set through a separate subscript boundary. "
        "This keeps the current crop intact while proving each concept returns an explicit outcome."
    ),
    requires=(
        StateRequirement("repo_root", "Repository root inventoried by repo_shape."),
        StateRequirement("deterministic_retriever_proven", "Retriever boundary succeeded before this gold-set runs."),
    ),
    provides=(
        StateProvision("framework_goldset_proven", "True when all recommended RAG smoke concepts pass."),
        StateProvision("framework_goldset_trace_path", "Trace emitted by the existing mini framework."),
        StateProvision("framework_goldset_summary_path", "Assembly-specific summary of concept outcomes."),
    ),
    evidence_required=("framework_goldset_trace_path", "framework_goldset_summary_path"),
)


def run(repo_dir: Path, output_dir: Path, state: dict[str, Any]) -> StepResult:
    require_state(state, "repo_root", "deterministic_retriever_proven")
    if Path(state["repo_root"]).resolve() != repo_dir:
        raise ValueError("input repo_root does not match --repo-dir")
    if state.get("deterministic_retriever_proven") is not True:
        raise ValueError("deterministic retriever boundary did not prove its state")

    suite_dir = output_dir / "mini_framework"
    suite_dir.mkdir(parents=True, exist_ok=True)
    outcomes = run_recommended_smoke_suite(suite_dir)
    failed = [outcome.as_dict() for outcome in outcomes if not outcome.ok]
    if failed:
        raise ValueError(f"framework gold-set failures: {failed}")

    expected_ids = [concept.id for concept in RECOMMENDED_SMOKE_CONCEPTS]
    summary = {
        "schema_version": 1,
        "step_id": CONTRACT.step_id,
        "concept_count": len(outcomes),
        "expected_concept_ids": expected_ids,
        "outcomes": [outcome.as_dict() for outcome in outcomes],
    }
    summary_path = output_dir / "framework_goldset_summary.json"
    write_json(summary_path, summary)
    trace_path = suite_dir / "rag_smoke_trace.json"
    if not trace_path.exists():
        raise ValueError(f"expected framework trace was not written: {trace_path}")

    return StepResult(
        step_id=CONTRACT.step_id,
        status="ok",
        provided_state={
            "framework_goldset_proven": True,
            "framework_goldset_trace_path": str(trace_path),
            "framework_goldset_summary_path": str(summary_path),
        },
        evidence={
            "framework_goldset_trace_path": str(trace_path),
            "framework_goldset_summary_path": str(summary_path),
        },
        details={
            "concept_count": len(outcomes),
            "concept_ids": expected_ids,
        },
    )


def main(argv: list[str] | None = None) -> int:
    return step_cli(CONTRACT, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
