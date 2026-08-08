"""Contract Counter compatibility wrapper for generic explicit-package MCEL tooling."""

from __future__ import annotations

from pathlib import Path

from main_computer.mcel_counter_legacy_importer import DEFAULT_COUNTER_ROOT
from main_computer.mcel_counter_reference_fixture_profile import (
    COMPATIBILITY_REPORT_ROOT as DEFAULT_REPORT_ROOT,
    COMPATIBILITY_REPORT_SCHEMA,
    COMPATIBILITY_VERSION,
    DEFAULT_DSL_SOURCE,
    DEFAULT_FIXTURE_IR,
    build_counter_compatibility_profile,
)
from main_computer.mcel_explicit_package_compatibility import (
    ExplicitPackageCompatibilityReport,
    compare_explicit_package_representations,
)

CounterCompatibilityReport = ExplicitPackageCompatibilityReport


def compare_counter_representations(
    *,
    package_root: Path = DEFAULT_COUNTER_ROOT,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    write_report: bool = False,
    report_root: Path = DEFAULT_REPORT_ROOT,
) -> CounterCompatibilityReport:
    """Compare live Counter package, fixture IR, and DSL through generic MCEL compatibility."""

    return compare_explicit_package_representations(
        build_counter_compatibility_profile(),
        package_root=package_root,
        fixture_ir_path=fixture_ir_path,
        dsl_source_path=dsl_source_path,
        write_report=write_report,
        report_root=report_root,
    )


__all__ = [
    "COMPATIBILITY_REPORT_SCHEMA",
    "COMPATIBILITY_VERSION",
    "CounterCompatibilityReport",
    "DEFAULT_COUNTER_ROOT",
    "DEFAULT_DSL_SOURCE",
    "DEFAULT_FIXTURE_IR",
    "DEFAULT_REPORT_ROOT",
    "compare_counter_representations",
]
