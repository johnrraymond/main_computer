"""IR-native intent-complete proof for the Contract Counter reference fixture.

Counter is retained as the small explicit-package MCEL conformance fixture.  The
shared explicit-package proof module owns the IR/native evidence mechanics; this
wrapper keeps the historical Counter entry points and supplies Counter fixture
profile facts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_counter_candidate_evidence import (
    _build_effect_accounting,
    _run_browser_effect_probe,
    _run_counter_effect_probe,
)
from main_computer.mcel_counter_reference_fixture_profile import build_counter_ir_native_proof_profile
from main_computer.mcel_explicit_package_ir_native_proof import (
    ExplicitPackageIrNativeProofError,
    _verify_generated_ownership as _verify_explicit_package_generated_ownership,
    run_explicit_package_ir_native_intent_proof,
    write_explicit_package_ir_native_report,
)


class CounterIrNativeProofError(RuntimeError):
    """Raised when promoted Counter source, ownership, IR, or evidence diverges."""


def _profile():
    return build_counter_ir_native_proof_profile(
        run_node_probe=_run_counter_effect_probe,
        run_browser_probe=_run_browser_effect_probe,
        build_effect_accounting=_build_effect_accounting,
    )


def run_counter_ir_native_intent_proof(
    *,
    repo: Path,
    record: Any,
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
    headed: bool = False,
    node_probe_runner: Callable[[Path, str], Mapping[str, Any]] | None = None,
    browser_probe_runner: Callable[[Path, bool, str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        return run_explicit_package_ir_native_intent_proof(
            _profile(),
            repo=repo,
            record=record,
            acceptance=acceptance,
            observation=observation,
            headed=headed,
            node_probe_runner=node_probe_runner,
            browser_probe_runner=browser_probe_runner,
        )
    except ExplicitPackageIrNativeProofError as exc:
        raise CounterIrNativeProofError(str(exc)) from exc


def write_counter_ir_native_report(report: Mapping[str, Any], output_directory: Path) -> tuple[Path, Path]:
    return write_explicit_package_ir_native_report(
        report,
        output_directory,
        title=_profile().report_title,
    )


def _verify_generated_ownership(
    *,
    package_root: Path,
    record: Any,
    ownership: Mapping[str, Any],
    semantic_fingerprint: str | None,
) -> dict[str, Any]:
    try:
        return _verify_explicit_package_generated_ownership(
            profile=_profile(),
            package_root=package_root,
            record=record,
            ownership=ownership,
            semantic_fingerprint=semantic_fingerprint,
        )
    except ExplicitPackageIrNativeProofError as exc:
        raise CounterIrNativeProofError(str(exc)) from exc
