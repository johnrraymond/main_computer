"""Calculator host-bound candidate projection wrapper.

The shared projection engine owns mechanics; the Calculator host-bound profile
owns the app-specific defaults and compatibility labels.
"""

from __future__ import annotations

from pathlib import Path

from main_computer.mcel_calculator_host_bound_profile import (
    DEFAULT_CANDIDATE_ROOT,
    DEFAULT_DSL_SOURCE,
    DEFAULT_PACKAGE_ROOT,
    PROJECTION_PROFILE,
    build_calculator_projection_profile,
)
from main_computer.mcel_host_bound_candidate_projection import (
    HostBoundCandidateProjectionReport,
    project_host_bound_candidate,
)


CalculatorCandidateProjectionReport = HostBoundCandidateProjectionReport
_CALCULATOR_HOST_BOUND_PROFILE = build_calculator_projection_profile()


def project_calculator_candidate(
    *,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path | None = None,
    live_package_root: Path = DEFAULT_PACKAGE_ROOT,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    write_candidate: bool = False,
) -> CalculatorCandidateProjectionReport:
    """Compile and project Calculator without copying its live HTML/runtime."""

    return project_host_bound_candidate(
        _CALCULATOR_HOST_BOUND_PROFILE,
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        live_package_root=live_package_root,
        candidate_root=candidate_root,
        write_candidate=write_candidate,
    )
