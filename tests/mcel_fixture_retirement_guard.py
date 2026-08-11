from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FIXTURE_APP_IDS = ("contract-counter", "contract-workbench")
FIXTURE_PYTHON_MODULE_PREFIXES = ("mcel_counter", "mcel_workbench")
FIXTURE_TITLES = ("Contract Counter", "Contract Workbench")
FIXTURE_IDENTIFIERS = FIXTURE_APP_IDS + FIXTURE_PYTHON_MODULE_PREFIXES + FIXTURE_TITLES


@dataclass(frozen=True)
class FixtureReferenceRule:
    """Documents why a remaining test module may mention reference fixture apps."""

    path: str
    reason: str


# Modules in this set had their generic package/catalog/projection invariants
# moved to fixture-neutral harness tests. Keep them free of direct fixture names
# so future work does not reintroduce hidden dependencies on reference apps.
FIXTURE_NEUTRAL_TEST_MODULES = (
    "tests/test_mcel_application_packages.py",
    "tests/test_mcel_application_package_browser_catalog.py",
    "tests/test_mcel_application_runtime_projection.py",
    "tests/test_mcel_app_surface_conformance.py",
    "tests/test_mcel_dsl_authoring_generic_harness.py",
    "tests/test_mcel_counter_promotion.py",
    "tests/test_mcel_counter_promotion_rehearsal.py",
    "tests/test_mcel_workbench_promotion.py",
    "tests/test_mcel_workbench_promotion_rehearsal.py",
    "tests/test_mcel_generic_promotion_dispatch.py",
    "tests/test_mcel_app_authoring_pipeline.py",
    "tests/test_mcel_counter_candidate_evidence.py",
    "tests/test_mcel_counter_candidate_projection.py",
    "tests/test_mcel_counter_ir_native_proof.py",
    "tests/test_mcel_acceptance_runner.py",
    "tests/test_mcel_app_prove.py",
    "tests/test_mcel_application_observation_runner.py",
    "tests/test_mcel_application_operation_observer.py",
    "tests/test_mcel_create_app.py",
    "tests/test_mcel_application_scaffolding_documentation.py",
    "tests/test_mcel_source_tree_dematerialization.py",
    "tests/test_mcel_application_definition.py",
    "tests/test_mcel_application_definition_normalizer.py",
    "tests/test_mcel_application_ir.py",
    "tests/test_mcel_constrained_expression.py",
)


# Every remaining direct fixture reference in top-level tests must be classified
# here. This is a retirement ledger, not a product migration backlog: entries are
# allowed only when the fixture's compatibility/proof/generator behavior is the
# thing under test.
ALLOWED_FIXTURE_REFERENCE_RULES = (
    FixtureReferenceRule(
        "tests/test_mcel_application_browser_scenario_runner.py",
        "browser scenario runner uses fixture acceptance scenarios as compatibility samples",
    ),
    FixtureReferenceRule(
        "tests/test_mcel_application_runtime.py",
        "runtime adapter/collection/conditional behavior still uses workbench fixture surface samples",
    ),
    FixtureReferenceRule(
        "tests/test_mcel_application_runtime_collection.py",
        "collection runtime behavior still uses workbench fixture package modules",
    ),
    FixtureReferenceRule(
        "tests/test_mcel_counter_compatibility.py",
        "counter compatibility module is explicitly fixture-specific",
    ),
    FixtureReferenceRule(
        "tests/test_mcel_documentation_authority.py",
        "documentation authority pins the current fixture-retirement policy text",
    ),
    FixtureReferenceRule(
        "tests/test_mcel_dsl_app_authoring_surface_documentation.py",
        "DSL authoring docs mention fixture retirement and no-backfill policy",
    ),
    FixtureReferenceRule(
        "tests/test_mcel_dsl_compiler.py",
        "DSL compiler smoke still compiles all checked-in apps including fixtures",
    ),
    FixtureReferenceRule(
        "tests/test_mcel_forward_specification_app.py",
        "forward-spec tests intentionally use the workbench fixture specification",
    ),
    FixtureReferenceRule(
        "tests/test_mcel_reference_app_wrapper_guardrails.py",
        "reference wrapper guardrails intentionally inspect fixture wrapper boundaries",
    ),
    FixtureReferenceRule(
        "tests/test_mcel_workbench_portability.py",
        "workbench portability module is explicitly fixture-specific",
    ),
    FixtureReferenceRule(
        "tests/test_mcel_fixture_retirement_boundaries.py",
        "fixture-retirement boundary guard names the final deletion targets",
    ),
)


# Physical deletion targets for retiring the legacy reference fixtures.  Overlay
# artifacts cannot delete files by omission, so these paths document the manual
# cleanup boundary used by tools/mcel_retire_reference_fixtures.py and by the
# final retirement commands.
FIXTURE_DELETION_TARGETS = (
    "mcel_apps/contract-counter",
    "mcel_apps/contract-workbench",
    "main_computer/mcel_counter_candidate_evidence.py",
    "main_computer/mcel_counter_candidate_projection.py",
    "main_computer/mcel_counter_compatibility.py",
    "main_computer/mcel_counter_effect_probe.py",
    "main_computer/mcel_counter_generated_contracts.py",
    "main_computer/mcel_counter_ir_native_proof.py",
    "main_computer/mcel_counter_legacy_fixture.py",
    "main_computer/mcel_counter_legacy_importer.py",
    "main_computer/mcel_counter_legacy_runtime.js",
    "main_computer/mcel_counter_promotion.py",
    "main_computer/mcel_counter_promotion_rehearsal.py",
    "main_computer/mcel_counter_reference_fixture_profile.py",
    "main_computer/mcel_workbench_candidate_evidence.py",
    "main_computer/mcel_workbench_candidate_projection.py",
    "main_computer/mcel_workbench_expression_profile.py",
    "main_computer/mcel_workbench_ir_native_proof.py",
    "main_computer/mcel_workbench_promotion.py",
    "main_computer/mcel_workbench_promotion_rehearsal.py",
    "main_computer/mcel_workbench_reference_fixture_profile.py",
    "main_computer/mcel_projection_profiles/contract_workbench_v1.py",
    "tools/mcel_counter_candidate_evidence.py",
    "tools/mcel_counter_candidate_projection.py",
    "tools/mcel_counter_compatibility.py",
    "tools/mcel_counter_ir_native_proof.py",
    "tools/mcel_counter_legacy_import.py",
    "tools/mcel_counter_promotion.py",
    "tools/mcel_counter_promotion_rehearsal.py",
    "tests/fixtures/mcel_application_ir/contract-counter.ir.json",
    "tests/fixtures/mcel_application_ir/contract-workbench.ir.json",
    "tests/fixtures/mcel_application_template_v1/contract-counter",
    "tests/test_mcel_application_browser_scenario_runner.py",
    "tests/test_mcel_application_runtime_collection.py",
    "tests/test_mcel_counter_compatibility.py",
    "tests/test_mcel_forward_specification_app.py",
    "tests/test_mcel_reference_app_wrapper_guardrails.py",
    "tests/test_mcel_workbench_portability.py",
)


def fixture_deletion_target_paths() -> tuple[str, ...]:
    return FIXTURE_DELETION_TARGETS


def fixture_reference_hits(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    return [identifier for identifier in FIXTURE_IDENTIFIERS if identifier in text]


def discover_test_modules_with_fixture_references(repo_root: Path) -> set[str]:
    tests_root = repo_root / "tests"
    paths: set[str] = set()
    for path in tests_root.glob("test_*.py"):
        if fixture_reference_hits(path):
            paths.add(path.relative_to(repo_root).as_posix())
    return paths


def allowed_fixture_reference_paths() -> set[str]:
    return {rule.path for rule in ALLOWED_FIXTURE_REFERENCE_RULES}


def rule_reasons() -> Iterable[str]:
    return (rule.reason for rule in ALLOWED_FIXTURE_REFERENCE_RULES)
