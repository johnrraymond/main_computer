from __future__ import annotations

from pathlib import Path

from mcel_fixture_retirement_guard import (
    ALLOWED_FIXTURE_REFERENCE_RULES,
    FIXTURE_NEUTRAL_TEST_MODULES,
    allowed_fixture_reference_paths,
    fixture_deletion_target_paths,
    fixture_reference_hits,
    rule_reasons,
    discover_test_modules_with_fixture_references,
)


ROOT = Path(__file__).resolve().parents[1]


def test_genericized_package_catalog_projection_tests_remain_fixture_neutral() -> None:
    for relative_path in FIXTURE_NEUTRAL_TEST_MODULES:
        path = ROOT / relative_path
        assert path.exists(), f"{relative_path} must remain a tracked genericized test module"
        assert fixture_reference_hits(path) == [], (
            f"{relative_path} reintroduced direct reference-app coupling"
        )


def test_remaining_fixture_references_are_intentionally_classified() -> None:
    discovered = discover_test_modules_with_fixture_references(ROOT)
    allowed = allowed_fixture_reference_paths()

    # A remaining fixture reference must either be explicitly classified or be
    # physically removed by the final fixture-retirement command.  Do not require
    # every classified legacy module to remain present after that command runs.
    assert discovered <= allowed


def test_fixture_reference_retirement_ledger_is_actionable() -> None:
    assert len(ALLOWED_FIXTURE_REFERENCE_RULES) == len(allowed_fixture_reference_paths())

    for reason in rule_reasons():
        assert "fixture" in reason or "counter" in reason or "workbench" in reason
        assert "generic package/catalog/projection invariant" not in reason


def test_fixture_deletion_targets_are_repo_relative_and_narrow() -> None:
    targets = fixture_deletion_target_paths()

    assert "mcel_apps/contract-counter" in targets
    assert "mcel_apps/contract-workbench" in targets
    assert "mcel_apps/calculator" not in targets
    assert "mcel_apps/code-editor" not in targets
    assert all(path and not path.startswith("/") and ".." not in path.split("/") for path in targets)
