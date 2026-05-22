from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from main_computer.models import ChatMessage, ChatResponse
from main_computer.providers import LLMProvider
from main_computer.rag_model_smoke import run_csv_audit_model_smoke
from main_computer.rag_model_smoke_assembly_v1 import ASSEMBLY_VERSION
from main_computer.rag_smoke_subscripts_v1.contract import (
    StateProvision,
    StateRequirement,
    StepContract,
    StepResult,
    require_state,
    step_cli,
)


CONTRACT = StepContract(
    step_id="csv_audit_model_run",
    version=ASSEMBLY_VERSION,
    description=(
        "Run the existing rag_model_smoke CSV-audit scenario through its model-backed "
        "path and emit only the report/evidence needed by the next boundary."
    ),
    requires=(
        StateRequirement("assembly_run_id", "Unique assembly run id for non-conflicting diagnostics."),
        StateRequirement("target_smoke_module", "Existing smoke module selected by target_inventory."),
        StateRequirement("target_scenario", "Existing smoke scenario selected by target_inventory."),
        StateRequirement("target_smoke_ready", "True only when the existing target smoke was found."),
        StateRequirement("provider_mode", "Confirmed provider mode."),
        StateRequirement("provider_ready", "True only when provider_gate permits model execution."),
        StateRequirement("provider_name", "Provider identity confirmed by provider_gate."),
        StateRequirement("provider_model", "Model identity confirmed by provider_gate."),
    ),
    provides=(
        StateProvision("model_smoke_report_path", "Path to model_smoke_report.json."),
        StateProvision("model_smoke_run_json_path", "Path to the underlying RAG harness run.json."),
        StateProvision("model_smoke_final_plan_path", "Path to the underlying final_plan.json."),
        StateProvision("model_smoke_run_id", "Run id emitted by the existing model smoke."),
        StateProvision("model_smoke_ok", "Boolean status from the existing model smoke report."),
        StateProvision("model_smoke_used_model", "True when the underlying RAG run was not no_model."),
        StateProvision("model_smoke_final_plan_type", "Type returned by the model-backed final plan."),
        StateProvision("model_smoke_retrieved_path_count", "Number of retrieved repo paths in the smoke report."),
        StateProvision("model_smoke_baseline_run_id", "No-model baseline run id emitted by the existing smoke."),
        StateProvision("model_smoke_warning_count", "Warning count from the existing smoke report."),
        StateProvision("model_smoke_failure_count", "Failure count from the existing smoke report."),
    ),
    evidence_required=("report_json", "run_json", "final_plan_json"),
)


class ContractFakeAiProvider(LLMProvider):
    """A deterministic provider for assembly tests.

    It still exercises the model-backed branches of run_rag_harness because the
    harness receives a provider and use_model=True. It is intentionally labeled
    fake so callers cannot mistake it for the real local AI smoke.
    """

    name = "contract-fake-ai"
    model = "contract-fake-rag-model"

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages: Sequence[ChatMessage]) -> ChatResponse:
        self.calls += 1
        if self.calls == 1:
            content = {
                "task_type": "standalone_python_script",
                "goal": "Create and verify a standalone CSV audit CLI script.",
                "needs": [
                    "executor tool loop context",
                    "docker executor artifact behavior",
                    "repository-grounded implementation plan",
                ],
                "retrieval_queries": [
                    "standalone python script",
                    "docker executor",
                    "executor tool loop",
                    "artifact outputs",
                    "python csv audit",
                ],
                "candidate_paths": [
                    "main_computer/docker_executor.py",
                    "main_computer/executor_tool_loop.py",
                    "main_computer/viewport_routes_executor.py",
                ],
                "executor_likely_needed": True,
                "risk": "may_need_writes",
            }
        else:
            content = {
                "type": "plan",
                "summary": (
                    "Use the executor-backed workflow to propose a CSV audit script and tests; "
                    "do not claim that files were created or commands were run until the executor boundary proves it."
                ),
                "evidence": [
                    {
                        "path": "main_computer/docker_executor.py",
                        "reason": "Defines the isolated executor workspace and output artifact collection.",
                    },
                    {
                        "path": "main_computer/executor_tool_loop.py",
                        "reason": "Defines how proposed shell commands are reviewed and executed.",
                    },
                ],
                "next_step": {
                    "kind": "proposal",
                    "description": "Ask for approval to run an executor command that writes csv_audit.py and validates fixtures.",
                    "requires_executor": True,
                    "requires_approval": True,
                },
                "open_questions": [],
            }
        return ChatResponse(
            content=json.dumps(content),
            provider=self.name,
            model=self.model,
            metadata={"contract_fake_ai_call": self.calls},
        )


def _run_json_path(report: dict[str, Any]) -> Path:
    output_dir = Path(str(report.get("output_dir") or ""))
    return output_dir / "run.json"


def _final_plan_path(report: dict[str, Any]) -> Path:
    output_dir = Path(str(report.get("output_dir") or ""))
    return output_dir / "final_plan.json"


def run(repo_dir: Path, output_dir: Path, state: dict[str, Any]) -> StepResult:
    require_state(
        state,
        "assembly_run_id",
        "target_smoke_module",
        "target_scenario",
        "target_smoke_ready",
        "provider_mode",
        "provider_ready",
        "provider_name",
        "provider_model",
    )

    if state["target_scenario"] != "csv-audit":
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            details={"error": f"Unsupported target_scenario: {state['target_scenario']!r}"},
        )
    if not state["target_smoke_ready"] or not state["provider_ready"]:
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            details={"target_smoke_ready": state["target_smoke_ready"], "provider_ready": state["provider_ready"]},
        )

    provider_mode = str(state["provider_mode"]).strip().lower()
    provider: LLMProvider | None
    if provider_mode == "fake":
        provider = ContractFakeAiProvider()
    elif provider_mode == "local":
        provider = None
    else:
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            details={"error": f"Unsupported provider_mode: {provider_mode!r}"},
        )

    run_id = str(state["assembly_run_id"]) + "_csv_audit"
    report_obj = run_csv_audit_model_smoke(
        repo_dir=repo_dir,
        output_root=output_dir / "rag_runs",
        provider=provider,
        strict=False,
        run_id=run_id,
        verbose=False,
        stream_model=False,
        dump_json=False,
        run_baseline=True,
    )
    report = report_obj.to_dict()
    report_path = Path(report_obj.report_path)
    run_json_path = _run_json_path(report)
    final_plan_path = _final_plan_path(report)

    try:
        run_json = json.loads(run_json_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            details={"error": f"Missing run.json: {type(exc).__name__}: {exc}", "run_json_path": str(run_json_path)},
        )

    try:
        final_plan = json.loads(final_plan_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            details={"error": f"Missing final_plan.json: {type(exc).__name__}: {exc}", "final_plan_path": str(final_plan_path)},
        )

    model_used = not bool(run_json.get("no_model"))
    return StepResult(
        step_id=CONTRACT.step_id,
        status="ok",
        provided_state={
            "model_smoke_report_path": str(report_path),
            "model_smoke_run_json_path": str(run_json_path),
            "model_smoke_final_plan_path": str(final_plan_path),
            "model_smoke_run_id": str(report.get("run_id") or ""),
            "model_smoke_ok": bool(report.get("ok")),
            "model_smoke_used_model": model_used,
            "model_smoke_final_plan_type": str(report.get("final_plan_type") or ""),
            "model_smoke_retrieved_path_count": int(report.get("retrieved_path_count") or 0),
            "model_smoke_baseline_run_id": str(report.get("baseline_run_id") or ""),
            "model_smoke_warning_count": len(report.get("warnings") or []),
            "model_smoke_failure_count": len(report.get("failures") or []),
        },
        evidence={
            "report_json": str(report_path),
            "run_json": str(run_json_path),
            "final_plan_json": str(final_plan_path),
        },
        details={
            "provider_mode": provider_mode,
            "provider_name": state["provider_name"],
            "provider_model": state["provider_model"],
            "run_no_model": bool(run_json.get("no_model")),
            "final_plan_provider": final_plan.get("provider"),
            "final_plan_model": final_plan.get("model"),
            "final_plan_mode": final_plan.get("mode"),
        },
    )


def main(argv: list[str] | None = None) -> int:
    return step_cli(CONTRACT, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
