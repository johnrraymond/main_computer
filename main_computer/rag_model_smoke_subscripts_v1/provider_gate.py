from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from main_computer.config import MainComputerConfig
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
    step_id="provider_gate",
    version=ASSEMBLY_VERSION,
    description=(
        "Establish whether this run is using the real local AI provider or the deterministic "
        "contract-test fake provider before the model-backed smoke can run."
    ),
    requires=(
        StateRequirement("provider_mode", "Assembly-selected provider mode: local or fake."),
        StateRequirement("target_smoke_module", "Existing smoke module selected by target_inventory."),
        StateRequirement("target_scenario", "Existing smoke scenario selected by target_inventory."),
        StateRequirement("target_smoke_ready", "True only when the existing smoke source was found."),
    ),
    provides=(
        StateProvision("provider_mode", "Confirmed provider mode."),
        StateProvision("provider_ready", "True when the next model-run step is allowed to execute."),
        StateProvision("provider_name", "Provider identity expected by the smoke run."),
        StateProvision("provider_model", "Model identity expected by the smoke run."),
        StateProvision("provider_is_real_ai", "True for local Ollama mode, false for deterministic fake mode."),
        StateProvision("provider_gate_summary", "Human-readable provider gate summary."),
    ),
    evidence_required=("provider_gate", "mode_policy"),
)


def _ollama_tags(base_url: str, timeout_s: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api/tags"
    with urlopen(url, timeout=max(1.0, min(timeout_s, 5.0))) as response:
        body = response.read().decode("utf-8", errors="replace")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = {"raw": body[:500]}
    return {"url": url, "response": parsed}


def run(repo_dir: Path, output_dir: Path, state: dict[str, Any]) -> StepResult:
    require_state(state, "provider_mode", "target_smoke_module", "target_scenario", "target_smoke_ready")

    mode = str(state["provider_mode"]).strip().lower()
    if mode == "fake":
        return StepResult(
            step_id=CONTRACT.step_id,
            status="ok",
            provided_state={
                "provider_mode": "fake",
                "provider_ready": True,
                "provider_name": "contract-fake-ai",
                "provider_model": "contract-fake-rag-model",
                "provider_is_real_ai": False,
                "provider_gate_summary": "Using deterministic fake provider for assembly contract testing.",
            },
            evidence={
                "provider_gate": {
                    "mode": "fake",
                    "ready": True,
                    "network_required": False,
                    "reason": "fake mode is only for deterministic boundary verification",
                },
                "mode_policy": {
                    "fake_mode_allowed_for_pytest": True,
                    "local_mode_required_for_real_ai_smoke": True,
                },
            },
            details={"next_step_must_still_run_with_use_model_true": True},
        )

    if mode != "local":
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            details={"error": "provider_mode must be 'local' or 'fake'", "provider_mode": mode},
        )

    config = MainComputerConfig.from_env()
    if config.provider != "ollama":
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            evidence={
                "provider_gate": {"mode": "local", "ready": False, "provider": config.provider},
                "mode_policy": {"local_mode_requires_ollama": True},
            },
            details={"error": "rag_model_smoke uses the local Ollama provider for this assembly."},
        )

    try:
        tags = _ollama_tags(config.ollama_base_url, config.ollama_timeout_s)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return StepResult(
            step_id=CONTRACT.step_id,
            status="fail",
            evidence={
                "provider_gate": {
                    "mode": "local",
                    "ready": False,
                    "provider": "ollama",
                    "base_url": config.ollama_base_url,
                    "model": config.model,
                },
                "mode_policy": {
                    "local_mode_requires_reachable_ollama": True,
                    "fake_mode_is_available_for_contract_tests_only": True,
                },
            },
            details={"error": f"{type(exc).__name__}: {exc}"},
        )

    return StepResult(
        step_id=CONTRACT.step_id,
        status="ok",
        provided_state={
            "provider_mode": "local",
            "provider_ready": True,
            "provider_name": "ollama",
            "provider_model": config.model,
            "provider_is_real_ai": True,
            "provider_gate_summary": f"Ollama provider reachable at {config.ollama_base_url}.",
        },
        evidence={
            "provider_gate": {
                "mode": "local",
                "ready": True,
                "provider": "ollama",
                "base_url": config.ollama_base_url,
                "model": config.model,
                "tags_seen": bool(tags.get("response")),
            },
            "mode_policy": {
                "local_mode_requires_reachable_ollama": True,
                "fake_mode_is_available_for_contract_tests_only": True,
            },
        },
        details={"ollama_tags_preview": tags},
    )


def main(argv: list[str] | None = None) -> int:
    return step_cli(CONTRACT, run, argv)


if __name__ == "__main__":
    raise SystemExit(main())
