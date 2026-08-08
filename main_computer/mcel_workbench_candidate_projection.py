"""Workbench profiled-package candidate projection wrapper.

Workbench supplies a deterministic portable-IR projection profile.  Shared
profiled-package projection mechanics live in
``main_computer.mcel_profiled_package_candidate_projection``.
"""

from __future__ import annotations

from pathlib import Path

from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_profiled_package_candidate_projection import (
    ProfiledPackageCandidateProjectionReport,
    ProfiledPackageProjectionProfile,
    project_profiled_package_candidate,
)
from main_computer.mcel_workbench_reference_fixture_profile import (
    APP_ID,
    DEFAULT_DSL_SOURCE,
    DEFAULT_FIXTURE_IR,
    DEFAULT_PACKAGE_ROOT,
    GENERATED_PATHS,
    PROFILE_MODULE_PATH,
    PROJECTION_PROFILE,
    build_workbench_projection_profile,
)


WorkbenchCandidateProjectionReport = ProfiledPackageCandidateProjectionReport


def workbench_profiled_package_projection_profile() -> ProfiledPackageProjectionProfile:
    return build_workbench_projection_profile()


def project_workbench_candidate(
    *,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    live_package_root: Path = DEFAULT_PACKAGE_ROOT,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    write_candidate: bool = False,
) -> WorkbenchCandidateProjectionReport:
    return project_profiled_package_candidate(
        workbench_profiled_package_projection_profile(),
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        live_package_root=live_package_root,
        candidate_root=candidate_root,
        write_candidate=write_candidate,
    )
