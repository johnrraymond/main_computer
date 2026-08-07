"""Calculator runtime parity wrapper for host-bound DSL authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_calculator_host_bound_profile import (
    APP_ID,
    DEFAULT_DSL_SOURCE,
    EXPECTED_INTENTS,
    LOCAL_LANES,
    build_calculator_runtime_parity_profile,
)
from main_computer.mcel_host_bound_runtime_parity import (
    HostBoundRuntimeParityError,
    HostBoundRuntimeParityResult,
    run_host_bound_browser_parity_probe,
    run_host_bound_generated_adapter_parity,
)


CalculatorParityError = HostBoundRuntimeParityError
CalculatorParityResult = HostBoundRuntimeParityResult


def _run_calculator_browser_observation(**kwargs: Any) -> Any:
    from main_computer.mcel_calculator_browser_observation import run_calculator_browser_observation

    return run_calculator_browser_observation(**kwargs)


CALCULATOR_RUNTIME_PARITY_PROFILE = build_calculator_runtime_parity_profile(_run_calculator_browser_observation)


def run_calculator_generated_adapter_parity(
    *,
    repo_root: Path,
    operation_prefix: str = "promoted",
) -> CalculatorParityResult:
    """Prove the generated Calculator adapter is the live semantic authority."""

    return run_host_bound_generated_adapter_parity(
        CALCULATOR_RUNTIME_PARITY_PROFILE,
        repo_root=repo_root,
        operation_prefix=operation_prefix,
    )


def run_calculator_browser_parity_probe(
    repo: Path,
    headed: bool = False,
    operation_prefix: str = "promoted",
) -> Mapping[str, Any]:
    """Profile hook compatible with the generic app authoring probe signature."""

    return run_host_bound_browser_parity_probe(
        CALCULATOR_RUNTIME_PARITY_PROFILE,
        repo=repo,
        headed=headed,
        operation_prefix=operation_prefix,
    )
