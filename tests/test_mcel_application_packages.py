from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from main_computer.mcel_application_packages import (
    CATALOG_SCHEMA,
    PACKAGE_FINGERPRINT_ALGORITHM,
    build_application_package_catalog,
)

from mcel_dsl_authoring_harness import (
    copy_reference_self_contained_runtime_package,
    discover_valid_application_package_cases,
    expected_application_package_ids,
)


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "mcel_application_packages.py"


def _copy_package(repo: Path, directory_name: str | None = None) -> Path:
    _case, destination = copy_reference_self_contained_runtime_package(
        ROOT,
        repo,
        directory_name=directory_name,
    )
    return destination


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _error_codes(record: object) -> set[str]:
    return {issue.code for issue in record.errors}  # type: ignore[attr-defined]


def test_repository_catalog_discovers_checked_in_packages_without_fixture_names() -> None:
    catalog = build_application_package_catalog(ROOT)
    expected_ids = expected_application_package_ids(ROOT)

    assert catalog.ok is True
    assert catalog.package_count == len(expected_ids)
    assert catalog.valid_count == len(expected_ids)
    assert catalog.invalid_count == 0
    assert {record.app_id for record in catalog.packages} == expected_ids

    for record in catalog.packages:
        assert record.valid is True
        assert record.app_id
        assert record.package_root == f"mcel_apps/{record.directory_name}"
        assert record.manifest == f"{record.package_root}/mcel.app.json"
        assert record.requirements == f"{record.package_root}/requirements.md"
        assert record.blueprint == f"{record.package_root}/blueprint.json"
        assert record.contracts["adapter"] == f"{record.package_root}/contracts/adapter.js"
        if record.runtime:
            assert record.runtime["document"] == f"{record.package_root}/src/index.html"
        assert record.tests_root == f"{record.package_root}/tests"
        assert record.acceptance_bindings == f"{record.package_root}/tests/mcel_acceptance_bindings.json"
        assert record.fingerprint is not None and record.fingerprint.startswith("sha256:")
        assert record.fingerprint_algorithm == PACKAGE_FINGERPRINT_ALGORITHM
        assert record.conformance["currentMode"] == "semantic-runtime-proven"
        assert record.conformance["targetMode"] == "semantic-runtime-proven"
        assert "application-package-discovery" not in record.conformance["missingBridges"]


def test_repository_catalog_materializes_calculator_authoritative_contracts_in_memory() -> None:
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == "calculator")

    assert record.valid is True
    assert record.conformance["currentMode"] == "semantic-runtime-proven"
    assert record.conformance["shadow"] is False
    assert record.runtime == {}
    assert record.files["contracts/domain.js"]
    assert record.files["contracts/adapter.js"]
    assert record.files["contracts/surface-bundle.json"]
    assert record.contracts["surfaceBundle"] == "mcel_apps/calculator/contracts/surface-bundle.json"
    surface_bundle = json.loads(record.files["contracts/surface-bundle.json"].decode("utf-8"))
    assert surface_bundle["appId"] == "calculator"
    assert surface_bundle["semanticSurface"]["id"] == "calculator.semantic-surface.semantic-runtime-workspace"
    assert surface_bundle["layoutGrammar"]["id"] == "calculator.layout.semantic-runtime-workspace"
    assert record.files["generated/mcel.application.normalized.json"]
    assert record.files["mcel.generated.json"]
    assert not (ROOT / "mcel_apps/calculator/contracts").exists()
    assert not (ROOT / "mcel_apps/calculator/generated").exists()


def test_repository_catalog_materializes_code_editor_authoritative_contracts_in_memory() -> None:
    catalog = build_application_package_catalog(ROOT)
    record = next(item for item in catalog.packages if item.app_id == "code-editor")

    assert record.valid is True
    assert record.conformance["currentMode"] == "semantic-runtime-proven"
    assert record.conformance["shadow"] is False
    assert record.runtime == {}
    assert record.files["contracts/domain.js"]
    assert record.files["contracts/adapter.js"]
    assert record.files["generated/mcel.application.normalized.json"]
    assert record.files["mcel.generated.json"]
    adapter = record.files["contracts/adapter.js"].decode("utf-8")
    assert "globalThis.MainComputerCodeEditorRuntime" in adapter
    assert "MainComputerCodeStudio" not in adapter
    assert not (ROOT / "mcel_apps/code-editor/contracts").exists()
    assert not (ROOT / "mcel_apps/code-editor/generated").exists()


def test_repository_catalog_is_deterministic_and_location_independent(tmp_path: Path) -> None:
    first_repo = tmp_path / "first"
    second_repo = tmp_path / "nested" / "second"
    _copy_package(first_repo)
    _copy_package(second_repo)

    first = build_application_package_catalog(first_repo).to_dict()
    second = build_application_package_catalog(second_repo).to_dict()

    assert first == second
    assert first["repositoryRoot"] == "."
    assert first["packagesRoot"] == "mcel_apps"


def test_repository_catalog_fingerprint_changes_with_package_contents(tmp_path: Path) -> None:
    package = _copy_package(tmp_path)
    before = build_application_package_catalog(tmp_path)

    requirements = package / "requirements.md"
    requirements.write_text(
        requirements.read_text(encoding="utf-8") + "\n<!-- fingerprint change -->\n",
        encoding="utf-8",
    )
    after = build_application_package_catalog(tmp_path)

    assert before.ok is True
    assert after.ok is True
    assert before.packages[0].fingerprint != after.packages[0].fingerprint
    assert before.fingerprint != after.fingerprint


def test_repository_catalog_refuses_missing_manifest_candidate(tmp_path: Path) -> None:
    candidate = tmp_path / "mcel_apps" / "unfinished-app"
    candidate.mkdir(parents=True)
    (candidate / "README.md").write_text("unfinished\n", encoding="utf-8")

    catalog = build_application_package_catalog(tmp_path)

    assert catalog.ok is False
    assert catalog.invalid_count == 1
    assert "missing-required-file" in _error_codes(catalog.packages[0])


def test_repository_catalog_refuses_directory_manifest_and_blueprint_identity_mismatch(tmp_path: Path) -> None:
    package = _copy_package(tmp_path, "renamed-package")
    manifest = json.loads((package / "mcel.app.json").read_text(encoding="utf-8"))
    manifest["appId"] = "other-app"
    _write_json(package / "mcel.app.json", manifest)
    blueprint = json.loads((package / "blueprint.json").read_text(encoding="utf-8"))
    blueprint["appId"] = "third-app"
    _write_json(package / "blueprint.json", blueprint)

    catalog = build_application_package_catalog(tmp_path)
    codes = _error_codes(catalog.packages[0])

    assert catalog.ok is False
    assert "app-id-mismatch" in codes
    assert "package-directory-app-id-mismatch" in codes
    assert "manifest-blueprint-app-id-mismatch" in codes


def test_repository_catalog_refuses_duplicate_declared_application_ids(tmp_path: Path) -> None:
    first = _copy_package(tmp_path, "first-package")
    second = _copy_package(tmp_path, "second-package")
    for package in (first, second):
        manifest = json.loads((package / "mcel.app.json").read_text(encoding="utf-8"))
        manifest["appId"] = "shared-app"
        _write_json(package / "mcel.app.json", manifest)
        blueprint = json.loads((package / "blueprint.json").read_text(encoding="utf-8"))
        blueprint["appId"] = "shared-app"
        _write_json(package / "blueprint.json", blueprint)

    catalog = build_application_package_catalog(tmp_path)

    assert catalog.ok is False
    assert catalog.invalid_count == 2
    assert all("duplicate-application-id" in _error_codes(record) for record in catalog.packages)


def test_repository_catalog_refuses_unsafe_manifest_reference(tmp_path: Path) -> None:
    package = _copy_package(tmp_path)
    manifest = json.loads((package / "mcel.app.json").read_text(encoding="utf-8"))
    manifest["requirements"] = "../outside.md"
    _write_json(package / "mcel.app.json", manifest)

    catalog = build_application_package_catalog(tmp_path)

    assert catalog.ok is False
    assert "unsafe-manifest-reference" in _error_codes(catalog.packages[0])
    assert catalog.packages[0].requirements is None


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_repository_catalog_refuses_internal_symlink_escape(tmp_path: Path) -> None:
    package = _copy_package(tmp_path)
    outside = tmp_path / "outside.js"
    outside.write_text("export const escaped = true;\n", encoding="utf-8")
    contracts = package / "contracts"
    contracts.mkdir(exist_ok=True)
    target = contracts / "escaped.js"
    try:
        target.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    catalog = build_application_package_catalog(tmp_path)

    assert catalog.ok is False
    assert "symlink-package-entry" in _error_codes(catalog.packages[0])
    assert catalog.packages[0].fingerprint is None


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_repository_catalog_refuses_symlink_package_root(tmp_path: Path) -> None:
    outside_repo = tmp_path / "outside"
    outside_package = _copy_package(outside_repo)
    packages_root = tmp_path / "repo" / "mcel_apps"
    packages_root.mkdir(parents=True)
    link = packages_root / outside_package.name
    try:
        link.symlink_to(outside_package, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    catalog = build_application_package_catalog(tmp_path / "repo")

    assert catalog.ok is False
    assert "symlink-package-root" in _error_codes(catalog.packages[0])


def test_repository_catalog_ignores_non_directory_entries_with_warning(tmp_path: Path) -> None:
    _copy_package(tmp_path)
    (tmp_path / "mcel_apps" / "README.md").write_text("catalog note\n", encoding="utf-8")

    catalog = build_application_package_catalog(tmp_path)

    assert catalog.ok is True
    assert catalog.package_count == 1
    assert [warning.code for warning in catalog.warnings] == ["ignored-non-package-entry"]


def test_repository_catalog_missing_packages_root_is_invalid(tmp_path: Path) -> None:
    catalog = build_application_package_catalog(tmp_path)

    assert catalog.ok is False
    assert catalog.package_count == 0
    assert [issue.code for issue in catalog.errors] == ["missing-packages-root"]


def test_repository_catalog_cli_json_is_machine_readable() -> None:
    completed = subprocess.run(
        [sys.executable, str(TOOL), "--repo-root", str(ROOT), "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["schema"] == CATALOG_SCHEMA
    assert payload["format"] == "mcel-application-packages-v1"
    assert payload["ok"] is True
    expected_ids = expected_application_package_ids(ROOT)
    assert payload["packageCount"] == len(expected_ids)
    assert {item["appId"] for item in payload["packages"]} == expected_ids


def test_repository_catalog_cli_returns_invalid_catalog_exit_class(tmp_path: Path) -> None:
    candidate = tmp_path / "mcel_apps" / "broken-app"
    candidate.mkdir(parents=True)

    completed = subprocess.run(
        [sys.executable, str(TOOL), "--repo-root", str(tmp_path), "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 3
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["invalidCount"] == 1


def test_repository_catalog_human_report_has_fast_readout() -> None:
    completed = subprocess.run(
        [sys.executable, str(TOOL), "--repo-root", str(ROOT)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.startswith("mcel-application-packages-v1\n")
    expected_ids = expected_application_package_ids(ROOT)
    assert f"packages: {len(expected_ids)}" in completed.stdout
    for app_id in expected_ids:
        assert app_id in completed.stdout
    assert "package: valid" in completed.stdout
    assert "current conformance: semantic-runtime-proven" in completed.stdout


def test_repository_catalog_ignores_generated_python_cache_files(tmp_path: Path) -> None:
    package = _copy_package(tmp_path)
    before = build_application_package_catalog(tmp_path)
    cache = package / "tests" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "test_package.cpython-313.pyc").write_bytes(b"\x00generated-cache")

    catalog = build_application_package_catalog(tmp_path)

    assert catalog.ok is True
    assert catalog.packages[0].valid is True
    assert catalog.packages[0].file_count == before.packages[0].file_count


def test_repository_catalog_refuses_package_acceptance_selector_escape(tmp_path: Path) -> None:
    package = _copy_package(tmp_path)
    bindings_path = package / "tests" / "mcel_acceptance_bindings.json"
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    bindings["bindings"][0]["selectors"] = ["../outside.py"]
    _write_json(bindings_path, bindings)

    catalog = build_application_package_catalog(tmp_path)

    assert catalog.ok is False
    assert "unsafe-package-acceptance-selector" in _error_codes(catalog.packages[0])


def test_repository_catalog_refuses_package_acceptance_app_identity_mismatch(tmp_path: Path) -> None:
    package = _copy_package(tmp_path)
    bindings_path = package / "tests" / "mcel_acceptance_bindings.json"
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    bindings["appId"] = "other-app"
    _write_json(bindings_path, bindings)

    catalog = build_application_package_catalog(tmp_path)

    assert catalog.ok is False
    assert "acceptance-binding-app-id-mismatch" in _error_codes(catalog.packages[0])


def test_repository_catalog_refuses_acceptance_binding_file_outside_tests_root(tmp_path: Path) -> None:
    package = _copy_package(tmp_path)
    manifest_path = package / "mcel.app.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tests"]["acceptanceBindings"] = "mcel_acceptance_bindings.json"
    _write_json(manifest_path, manifest)
    shutil.copy2(package / "tests" / "mcel_acceptance_bindings.json", package / "mcel_acceptance_bindings.json")

    catalog = build_application_package_catalog(tmp_path)

    assert catalog.ok is False
    assert "package-acceptance-bindings-outside-tests-root" in _error_codes(catalog.packages[0])
