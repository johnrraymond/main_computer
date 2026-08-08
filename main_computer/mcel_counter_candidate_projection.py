"""Counter explicit-package candidate projection.

The Counter app supplies generated-contract content and compatibility checks.
Shared explicit-package projection mechanics live in
``main_computer.mcel_explicit_package_candidate_projection``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from main_computer.mcel_counter_compatibility import DEFAULT_DSL_SOURCE, DEFAULT_FIXTURE_IR
from main_computer.mcel_counter_legacy_importer import DEFAULT_COUNTER_ROOT
from main_computer.mcel_counter_generated_contracts import generate_counter_contracts
from main_computer.mcel_counter_reference_fixture_profile import (
    GENERATED_CONTRACTS,
    build_counter_projection_profile,
)
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_explicit_package_candidate_projection import (
    ExplicitPackageCandidateProjectionReport,
    ExplicitPackageProjectionProfile,
    project_explicit_package_candidate,
)


CounterCandidateProjectionReport = ExplicitPackageCandidateProjectionReport


def counter_explicit_package_projection_profile() -> ExplicitPackageProjectionProfile:
    return build_counter_projection_profile(generate_contracts=generate_counter_contracts)


def project_counter_candidate(
    *,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    live_package_root: Path = DEFAULT_COUNTER_ROOT,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    write_candidate: bool = False,
) -> CounterCandidateProjectionReport:
    return project_explicit_package_candidate(
        counter_explicit_package_projection_profile(),
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        live_package_root=live_package_root,
        candidate_root=candidate_root,
        write_candidate=write_candidate,
    )
