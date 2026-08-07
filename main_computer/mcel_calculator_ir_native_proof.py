"""Calculator IR-native proof wrapper for host-bound DSL authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_calculator_host_bound_profile import APP_ID, build_calculator_ir_native_proof_profile
from main_computer.mcel_calculator_parity import run_calculator_browser_parity_probe
from main_computer.mcel_host_bound_ir_native_proof import (
    HostBoundIrNativeProofError,
    run_host_bound_ir_native_intent_proof,
)


CalculatorIrNativeProofError = HostBoundIrNativeProofError
CALCULATOR_IR_NATIVE_PROOF_PROFILE = build_calculator_ir_native_proof_profile(run_calculator_browser_parity_probe)


def run_calculator_ir_native_intent_proof(
    *,
    repo: Path,
    record: Any,
    acceptance: Mapping[str, Any] | None = None,
    observation: Mapping[str, Any] | None = None,
    headed: bool = False,
    node_probe_runner: Any = None,
    browser_probe_runner: Any = None,
) -> dict[str, Any]:
    return run_host_bound_ir_native_intent_proof(
        CALCULATOR_IR_NATIVE_PROOF_PROFILE,
        repo=repo,
        record=record,
        acceptance=acceptance,
        observation=observation,
        headed=headed,
        node_probe_runner=node_probe_runner,
        browser_probe_runner=browser_probe_runner,
    )
