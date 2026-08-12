from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from main_computer.mcel_application_runtime_projection import (
    RUNTIME_MANIFEST_NAME,
    RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM,
    build_runtime_projection_set,
    check_runtime_projections,
    is_runtime_projectable_record,
    write_runtime_projections,
)
from main_computer.mcel_application_packages import build_application_package_catalog

from mcel_dsl_authoring_harness import (
    copy_reference_runtime_projectable_package,
    expected_application_package_ids,
)


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "mcel_application_runtime_projection.py"
PROJECTION_ROOT = ROOT / "runtime" / "build" / "mcel" / "web" / "applications" / "mcel-packages"


def _copy_package(target_root: Path) -> str:
    case, _destination = copy_reference_runtime_projectable_package(ROOT, target_root)
    return case.app_id


def _files_with_location_sensitive_fingerprints_scrubbed(files: dict[str, bytes]) -> dict[str, bytes]:
    """Return projection files with temp-root-derived manifest fingerprints scrubbed.

    After retiring the self-contained reference fixtures, temporary projection
    tests can use host-bound DSL packages such as Calculator. Their generated
    package/source fingerprints legitimately depend on the copied source path,
    but the projected runtime contracts must remain structurally identical.
    """

    scrubbed = dict(files)
    manifest = json.loads(scrubbed[RUNTIME_MANIFEST_NAME].decode("utf-8"))
    manifest["projection"]["fingerprint"] = "<projection-fingerprint>"
    manifest["source"]["catalogFingerprint"] = "<catalog-fingerprint>"
    manifest["source"]["packageFingerprint"] = "<package-fingerprint>"
    scrubbed[RUNTIME_MANIFEST_NAME] = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")
    return scrubbed


def test_runtime_projection_contains_only_browser_execution_files() -> None:
    projection_set = build_runtime_projection_set(ROOT)
    package_catalog = build_application_package_catalog(ROOT)
    expected_ids = {
        str(record.app_id)
        for record in package_catalog.packages
        if record.valid and is_runtime_projectable_record(ROOT, record)
    }

    assert projection_set.package_count == len(expected_ids)
    assert {item.app_id for item in projection_set.projections} == expected_ids

    projection = next((item for item in projection_set.projections if item.document_url), None)
    if projection is None:
        projection = next(iter(projection_set.projections))
        assert projection.fingerprint_algorithm == RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM
        assert RUNTIME_MANIFEST_NAME in projection.files
        assert "requirements.md" not in projection.files
        assert not any(path.startswith("tests/") for path in projection.files)
        assert projection.manifest["source"]["packageFingerprint"] == projection.source_package_fingerprint
        assert projection.manifest["projection"]["fingerprint"] == projection.fingerprint
        return

    record = next(item for item in package_catalog.packages if item.app_id == projection.app_id)
    expected_files = {RUNTIME_MANIFEST_NAME}
    expected_files.update(
        f"contracts/{key}.js"
        for key in ("domain", "intents", "adapter", "surface", "layout", "acceptance", "observation")
        if record.contracts.get(key)
    )
    expected_files.update({"src/index.html", "src/app.js", "src/app.css"})

    assert projection.fingerprint_algorithm == RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM
    assert set(projection.files) == expected_files
    assert "requirements.md" not in projection.files
    assert not any(path.startswith("tests/") for path in projection.files)
    assert projection.manifest["source"]["packageFingerprint"] == projection.source_package_fingerprint
    assert projection.manifest["projection"]["fingerprint"] == projection.fingerprint
    assert projection.manifest["modules"]["adapter"]["path"] == "contracts/adapter.js"
    assert projection.manifest["modules"]["acceptance"]["path"] == "contracts/acceptance.js"
    assert projection.manifest["modules"]["observation"]["path"] == "contracts/observation.js"


def test_calculator_runtime_projection_is_host_bound_and_contains_no_copied_presentation() -> None:
    projection_set = build_runtime_projection_set(ROOT)
    projection = next(item for item in projection_set.projections if item.app_id == "calculator")

    assert projection.mount_mode == "host-bound"
    assert projection.host_route == "/applications/calculator"
    assert projection.root_selector == "#calculator-app"
    assert projection.runtime_facade == "MainComputerCalculatorRuntime"
    assert projection.document_url is None
    assert projection.script_url is None
    assert projection.style_url is None
    assert set(projection.files) == {
        RUNTIME_MANIFEST_NAME,
        "contracts/domain.js",
        "contracts/intents.js",
        "contracts/adapter.js",
        "contracts/surface.js",
        "contracts/layout.js",
        "contracts/surface-bundle.json",
        "contracts/acceptance.js",
        "contracts/observation.js",
    }
    assert projection.surface_bundle_url == "applications/mcel-packages/calculator/contracts/surface-bundle.json"
    assert projection.manifest["surfaceBundle"] == {
        "path": "contracts/surface-bundle.json",
        "url": "applications/mcel-packages/calculator/contracts/surface-bundle.json",
        "schema": "mcel.application-surface-bundle.v1",
        "surfaceId": "calculator.surface.workspace",
        "contractId": "calculator.contract.default.app-health",
    }
    assert projection.surface_bundle is not None
    assert projection.surface_bundle["semanticSurface"]["id"] == "calculator.semantic-surface.semantic-runtime-workspace"
    assert projection.surface_bundle["layoutGrammar"]["id"] == "calculator.layout.semantic-runtime-workspace"
    assert projection.manifest["runtime"] == {
        "mode": "host-bound",
        "route": "/applications/calculator",
        "rootSelector": "#calculator-app",
        "facade": "MainComputerCalculatorRuntime",
    }
    assert not any(path.startswith("src/") for path in projection.files)


def test_code_editor_runtime_projection_is_host_bound_and_contains_no_copied_presentation() -> None:
    projection_set = build_runtime_projection_set(ROOT)
    projection = next(item for item in projection_set.projections if item.app_id == "code-editor")

    assert projection.mount_mode == "host-bound"
    assert projection.host_route == "/applications/code-editor"
    assert projection.root_selector == "#code-editor-app"
    assert projection.runtime_facade == "MainComputerCodeEditorRuntime"
    assert projection.document_url is None
    assert projection.script_url is None
    assert projection.style_url is None
    assert set(projection.files) == {
        RUNTIME_MANIFEST_NAME,
        "contracts/domain.js",
        "contracts/intents.js",
        "contracts/adapter.js",
        "contracts/surface.js",
        "contracts/layout.js",
        "contracts/surface-bundle.json",
        "contracts/acceptance.js",
        "contracts/observation.js",
    }
    assert projection.surface_bundle_url == "applications/mcel-packages/code-editor/contracts/surface-bundle.json"
    assert projection.manifest["surfaceBundle"] == {
        "path": "contracts/surface-bundle.json",
        "url": "applications/mcel-packages/code-editor/contracts/surface-bundle.json",
        "schema": "mcel.application-surface-bundle.v1",
        "surfaceId": "code-editor.surface.monaco-selected-file-editor",
        "contractId": "code-editor.contract.authoring.monaco-golden-path",
    }
    assert projection.surface_bundle is not None
    assert projection.surface_bundle["semanticSurface"]["id"] == "code-editor.semantic-surface.legacy-fidelity"
    assert projection.surface_bundle["layoutGrammar"]["id"] == "code-editor.layout.legacy-fidelity-workbench"
    assert projection.manifest["runtime"] == {
        "mode": "host-bound",
        "route": "/applications/code-editor",
        "rootSelector": "#code-editor-app",
        "facade": "MainComputerCodeEditorRuntime",
    }
    adapter = projection.files["contracts/adapter.js"].decode("utf-8")
    assert "globalThis.MainComputerCodeEditorRuntime" in adapter
    assert "MainComputerCodeStudio" not in adapter
    assert not any(path.startswith("src/") for path in projection.files)


def test_runtime_projection_is_location_independent_and_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "nested" / "second"
    _copy_package(first)
    _copy_package(second)

    first_set = build_runtime_projection_set(first)
    second_set = build_runtime_projection_set(second)

    first_projection = first_set.projections[0]
    second_projection = second_set.projections[0]

    assert first_projection.app_id == second_projection.app_id
    assert first_projection.fingerprint_algorithm == second_projection.fingerprint_algorithm
    assert first_projection.mount_mode == second_projection.mount_mode
    assert first_projection.files.keys() == second_projection.files.keys()
    assert _files_with_location_sensitive_fingerprints_scrubbed(first_projection.files) == (
        _files_with_location_sensitive_fingerprints_scrubbed(second_projection.files)
    )


def test_checked_in_runtime_projection_is_fresh() -> None:
    fresh, destination, projection_set = check_runtime_projections(ROOT)

    assert fresh is True
    assert destination == PROJECTION_ROOT
    assert projection_set.package_count == len(expected_application_package_ids(ROOT))


def test_projection_check_detects_changed_and_extra_files(tmp_path: Path) -> None:
    app_id = _copy_package(tmp_path)
    output, _, changed = write_runtime_projections(tmp_path)
    assert changed is True

    manifest = output / app_id / RUNTIME_MANIFEST_NAME
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    (output / "stale.txt").write_text("stale", encoding="utf-8")

    fresh, _, _ = check_runtime_projections(tmp_path)
    assert fresh is False


def test_runtime_projection_cli_check_and_json() -> None:
    completed = subprocess.run(
        [sys.executable, str(TOOL), "--repo-root", str(ROOT), "--check", "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["resultCode"] == "runtime_projection_fresh"
    assert payload["packageCount"] == len(expected_application_package_ids(ROOT))
    assert payload["changed"] is False


def test_runtime_projection_cli_uses_stale_exit_class(tmp_path: Path) -> None:
    app_id = _copy_package(tmp_path)
    output, _, _ = write_runtime_projections(tmp_path)
    (output / app_id / RUNTIME_MANIFEST_NAME).write_text("{}\n", encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, str(TOOL), "--repo-root", str(tmp_path), "--check", "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 4
    assert json.loads(completed.stdout)["resultCode"] == "stale_runtime_projection"


def test_sanity_check_rejects_stale_runtime_projection(tmp_path: Path) -> None:
    from tools.mcel_sanity_check import SanityReport, _check_application_runtime_projection_freshness

    app_id = _copy_package(tmp_path)
    output, _, _ = write_runtime_projections(tmp_path)
    report = SanityReport(repo_root=tmp_path)
    _check_application_runtime_projection_freshness(report)
    assert report.errors == []

    (output / app_id / RUNTIME_MANIFEST_NAME).write_text("stale\n", encoding="utf-8")
    stale = SanityReport(repo_root=tmp_path)
    _check_application_runtime_projection_freshness(stale)
    assert [issue.code for issue in stale.errors] == ["stale-application-runtime-projection"]
