from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from main_computer.rag_model_smoke_assembly_v1 import ASSEMBLY_VERSION
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
    step_id="report_contract",
    version=ASSEMBLY_VERSION,
    description=(
        "Validate the evidence emitted by the existing model smoke and reject runs that "
        "silently downgraded to no-model or skipped the model-backed steps."
    ),
    requires=(
        StateRequirement("provider_mode", "Confirmed provider mode."),
        StateRequirement("provider_is_real_ai", "Whether provider_gate used real local AI."),
        StateRequirement("model_smoke_report_path", "Path to model_smoke_report.json."),
        StateRequirement("model_smoke_run_json_path", "Path to the underlying RAG harness run.json."),
        StateRequirement("model_smoke_final_plan_path", "Path to the underlying final_plan.json."),
        StateRequirement("model_smoke_ok", "Status from the existing model smoke report."),
        StateRequirement("model_smoke_used_model", "True when the run did not fall back to no_model."),
        StateRequirement("model_smoke_retrieved_path_count", "Retrieved path count from the smoke report."),
        StateRequirement("model_smoke_baseline_run_id", "No-model baseline run id emitted by the existing smoke."),
    ),
    provides=(
        StateProvision("ai_smoke_contract_proven", "True when all anti-cheat report checks pass."),
        StateProvision("ai_smoke_real_provider_run", "True only when provider_mode was local."),
        StateProvision("model_call_count", "Count of model-backed RAG steps found in run.json."),
        StateProvision("anti_cheat_checks", "Named checks that must pass before the assembly can pass."),
        StateProvision("assertion_report_path", "Path to this boundary's assertion report."),
    ),
    evidence_required=("assertion_report", "model_steps", "anti_cheat_checks"),
)


def _load_json(path: str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def _model_step_count(run_json: dict[str, Any]) -> int:
    count = 0
    for step in run_json.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("kind") in {"task_decomposition", "grounded_plan"}:
            output = step.get("output")
            if isinstance(output, dict) and output.get("mode") == "model":
                count += 1
    return count


def run(repo_dir: Path, output_dir: Path, state: dict[str, Any]) -> StepResult:
    require_state(
        state,
        "provider_mode",
        "provider_is_real_ai",
        "model_smoke_report_path",
        "model_smoke_run_json_path",
        "model_smoke_final_plan_path",
        "model_smoke_ok",
        "model_smoke_used_model",
        "model_smoke_retrieved_path_count",
        "model_smoke_baseline_run_id",
    )

    report = _load_json(str(state["model_smoke_report_path"]))
    run_json = _load_json(str(state["model_smoke_run_json_path"]))
    final_plan = _load_json(str(state["model_smoke_final_plan_path"]))

    checks = {
        "existing_smoke_report_ok": bool(report.get("ok")) is True,
        "assembly_state_report_ok": bool(state["model_smoke_ok"]) is True,
        "rag_run_completed": run_json.get("status") == "complete" and bool(run_json.get("ok")) is True,
        "model_backed_not_no_model": bool(run_json.get("no_model")) is False and bool(state["model_smoke_used_model"]) is True,
        "model_steps_present": _model_step_count(run_json) >= 2,
        "final_plan_has_model_identity": bool(final_plan.get("provider")) and bool(final_plan.get("model")),
        "retrieval_happened": int(state["model_smoke_retrieved_path_count"]) > 0,
        "baseline_was_recorded": bool(str(state["model_smoke_baseline_run_id"]).strip()),
        "no_failures_in_report": not bool(report.get("failures")),
    }
    failures = [name for name, passed in checks.items() if not passed]

    assertion_report = {
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "provider_mode": state["provider_mode"],
        "provider_is_real_ai": bool(state["provider_is_real_ai"]),
        "report_path": state["model_smoke_report_path"],
        "run_json_path": state["model_smoke_run_json_path"],
        "final_plan_path": state["model_smoke_final_plan_path"],
        "model_step_count": _model_step_count(run_json),
        "final_plan_provider": final_plan.get("provider"),
        "final_plan_model": final_plan.get("model"),
        "final_plan_mode": final_plan.get("mode"),
    }
    assertion_report_path = output_dir / "assertion_report.json"
    write_json(assertion_report_path, assertion_report)

    if failures:
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            evidence={
                "assertion_report": str(assertion_report_path),
                "model_steps": assertion_report["model_step_count"],
                "anti_cheat_checks": checks,
            },
            details={"failures": failures},
        )

    return StepResult(
        step_id=CONTRACT.step_id,
        status="ok",
        provided_state={
            "ai_smoke_contract_proven": True,
            "ai_smoke_real_provider_run": str(state["provider_mode"]) == "local",
            "model_call_count": assertion_report["model_step_count"],
            "anti_cheat_checks": sorted(checks),
            "assertion_report_path": str(assertion_report_path),
        },
        evidence={
            "assertion_report": str(assertion_report_path),
            "model_steps": assertion_report["model_step_count"],
            "anti_cheat_checks": checks,
        },
        details={
            "provider_mode": state["provider_mode"],
            "provider_is_real_ai": bool(state["provider_is_real_ai"]),
            "fake_mode_warning": (
                "This was deterministic contract-test mode, not a live local-AI smoke."
                if str(state["provider_mode"]) == "fake"
                else ""
            ),
        },
    )


def main(argv: list[str] | None = None) -> int:
    return step_cli(CONTRACT, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
