"""IR-native intent, effect, and capability proof for promoted Contract Workbench."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_profiled_package_ir_native_proof import (
    ProfiledPackageIrNativeProofError,
    run_profiled_package_ir_native_intent_proof,
)
from main_computer.mcel_workbench_reference_fixture_profile import build_workbench_ir_native_proof_profile


class WorkbenchIrNativeProofError(ProfiledPackageIrNativeProofError):
    """Raised when Workbench IR-native proof cannot converge."""


def run_workbench_ir_native_intent_proof(
    *,
    repo: Path,
    record: Any,
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
    headed: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run Workbench IR-native proof through generic profiled-package tooling."""

    try:
        return run_profiled_package_ir_native_intent_proof(
            build_workbench_ir_native_proof_profile(),
            repo=repo,
            record=record,
            acceptance=acceptance,
            observation=observation,
            headed=headed,
            **kwargs,
        )
    except ProfiledPackageIrNativeProofError as exc:
        raise WorkbenchIrNativeProofError(str(exc)) from exc
