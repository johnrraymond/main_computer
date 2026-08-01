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


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "mcel_application_packages.py"
REFERENCE_PACKAGE = ROOT / "mcel_apps" / "contract-counter"


def _copy_package(repo: Path, directory_name: str = "contract-counter") -> Path:
    packages = repo / "mcel_apps"
    packages.mkdir(parents=True, exist_ok=True)
    destination = packages / directory_name
    shutil.copytree(REFERENCE_PACKAGE, destination)
    return destination


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _error_codes(record: object) -> set[str]:
    return {issue.code for issue in record.errors}  # type: ignore[attr-defined]


def test_repository_catalog_discovers_checked_in_contract_counter() -> None:
    catalog = build_application_package_catalog(ROOT)

    assert catalog.ok is True
    assert catalog.package_count == 1
    assert catalog.valid_count == 1
    assert catalog.invalid_count == 0

    record = catalog.packages[0]
    assert record.valid is True
    assert record.app_id == "contract-counter"
    assert record.package_root == "mcel_apps/contract-counter"
    assert record.manifest == "mcel_apps/contract-counter/mcel.app.json"
    assert record.requirements == "mcel_apps/contract-counter/requirements.md"
    assert record.blueprint == "mcel_apps/contract-counter/blueprint.json"
    assert record.contracts["adapter"] == "mcel_apps/contract-counter/contracts/adapter.js"
    assert record.runtime["document"] == "mcel_apps/contract-counter/src/index.html"
    assert record.tests_root == "mcel_apps/contract-counter/tests"
    assert record.fingerprint is not None and record.fingerprint.startswith("sha256:")
    assert record.fingerprint_algorithm == PACKAGE_FINGERPRINT_ALGORITHM
    assert record.conformance["currentMode"] == "structural-only"
    assert record.conformance["targetMode"] == "semantic-runtime-proven"
    assert "application-package-discovery" not in record.conformance["missingBridges"]


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

    app_source = package / "src" / "app.js"
    app_source.write_text(app_source.read_text(encoding="utf-8") + "\n// fingerprint change\n", encoding="utf-8")
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
    package = _copy_package(tmp_path, "renamed-counter")
    manifest = json.loads((package / "mcel.app.json").read_text(encoding="utf-8"))
    manifest["appId"] = "other-counter"
    _write_json(package / "mcel.app.json", manifest)
    blueprint = json.loads((package / "blueprint.json").read_text(encoding="utf-8"))
    blueprint["appId"] = "third-counter"
    _write_json(package / "blueprint.json", blueprint)

    catalog = build_application_package_catalog(tmp_path)
    codes = _error_codes(catalog.packages[0])

    assert catalog.ok is False
    assert "app-id-mismatch" in codes
    assert "package-directory-app-id-mismatch" in codes
    assert "manifest-blueprint-app-id-mismatch" in codes


def test_repository_catalog_refuses_duplicate_declared_application_ids(tmp_path: Path) -> None:
    first = _copy_package(tmp_path, "first-counter")
    second = _copy_package(tmp_path, "second-counter")
    for package in (first, second):
        manifest = json.loads((package / "mcel.app.json").read_text(encoding="utf-8"))
        manifest["appId"] = "shared-counter"
        _write_json(package / "mcel.app.json", manifest)
        blueprint = json.loads((package / "blueprint.json").read_text(encoding="utf-8"))
        blueprint["appId"] = "shared-counter"
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
    target = package / "contracts" / "escaped.js"
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
    link = packages_root / "contract-counter"
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
    assert payload["packageCount"] == 1
    assert payload["packages"][0]["appId"] == "contract-counter"


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
    assert "packages: 1" in completed.stdout
    assert "contract-counter" in completed.stdout
    assert "package: valid" in completed.stdout
    assert "current conformance: structural-only" in completed.stdout


def test_repository_catalog_ignores_generated_python_cache_files(tmp_path: Path) -> None:
    package = _copy_package(tmp_path)
    cache = package / "tests" / "__pycache__"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "test_package.cpython-313.pyc").write_bytes(b"\x00generated-cache")

    catalog = build_application_package_catalog(tmp_path)

    assert catalog.ok is True
    assert catalog.packages[0].valid is True
    assert catalog.packages[0].file_count == 18
