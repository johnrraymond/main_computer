from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from main_computer.mcel_application_package_browser_catalog import (
    BROWSER_CATALOG_FORMAT,
    BROWSER_CATALOG_SCHEMA,
    InvalidRepositoryPackageCatalog,
    build_repository_browser_catalog_payload,
    check_browser_catalog,
    extract_browser_catalog_payload,
    render_browser_catalog_javascript,
    write_browser_catalog,
)
from main_computer.mcel_application_packages import build_application_package_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = ROOT / "mcel_apps" / "contract-counter"
TOOL = ROOT / "tools" / "mcel_application_package_browser_catalog.py"
GENERATED = ROOT / "runtime" / "build" / "mcel" / "web" / "applications" / "scripts" / "mcel-application-package-catalog.js"


def _copy_repository_package(target_root: Path) -> Path:
    destination = target_root / "mcel_apps" / "contract-counter"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE_PACKAGE, destination)
    return destination


def test_browser_catalog_payload_projects_validated_package_metadata_only() -> None:
    payload = build_repository_browser_catalog_payload(ROOT)

    assert payload["schema"] == BROWSER_CATALOG_SCHEMA
    assert payload["format"] == BROWSER_CATALOG_FORMAT
    assert payload["packageCount"] == 3
    assert {item["appId"] for item in payload["packages"]} == {
        "calculator",
        "contract-counter",
        "contract-workbench",
    }
    assert payload["catalogFingerprint"] == build_application_package_catalog(ROOT).fingerprint

    package = next(item for item in payload["packages"] if item["appId"] == "contract-counter")
    assert package["appId"] == "contract-counter"
    assert package["conformance"]["currentMode"] == "semantic-runtime-proven"
    assert package["runtime"]["document"] == "mcel_apps/contract-counter/src/index.html"
    assert package["runtimeProjection"]["manifestUrl"] == "applications/mcel-packages/contract-counter/mcel.runtime.json"
    assert package["runtimeProjection"]["fingerprint"].startswith("sha256:")

    calculator = next(item for item in payload["packages"] if item["appId"] == "calculator")
    assert calculator["runtime"] == {}
    assert calculator["runtimeProjection"]["mountMode"] == "host-bound"
    assert calculator["runtimeProjection"]["hostRoute"] == "/applications/calculator"
    assert calculator["runtimeProjection"]["rootSelector"] == "#calculator-app"
    assert calculator["runtimeProjection"]["runtimeFacade"] == "MainComputerCalculatorRuntime"
    assert calculator["runtimeProjection"]["documentUrl"] is None
    assert calculator["runtimeProjection"]["scriptUrl"] is None
    assert calculator["runtimeProjection"]["styleUrl"] is None

    assert set(package) == {
        "appId",
        "title",
        "packageRoot",
        "manifest",
        "requirements",
        "blueprint",
        "authoring",
        "contracts",
        "runtime",
        "testsRoot",
        "template",
        "conformance",
        "fingerprint",
        "fingerprintAlgorithm",
        "fileCount",
        "runtimeProjection",
    }


def test_browser_catalog_render_is_deterministic_and_location_independent(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "nested" / "second"
    _copy_repository_package(first)
    _copy_repository_package(second)

    first_js = render_browser_catalog_javascript(build_repository_browser_catalog_payload(first))
    second_js = render_browser_catalog_javascript(build_repository_browser_catalog_payload(second))

    assert first_js == second_js
    assert extract_browser_catalog_payload(first_js) == build_repository_browser_catalog_payload(first)


def test_checked_in_browser_catalog_is_fresh() -> None:
    fresh, destination, expected = check_browser_catalog(ROOT)

    assert destination == GENERATED
    assert fresh is True
    assert GENERATED.read_text(encoding="utf-8") == expected


def test_browser_catalog_refuses_invalid_repository_package_before_write(tmp_path: Path) -> None:
    broken = tmp_path / "mcel_apps" / "broken-app"
    broken.mkdir(parents=True)
    output = tmp_path / "catalog.js"

    with pytest.raises(InvalidRepositoryPackageCatalog):
        write_browser_catalog(tmp_path, output_path=output)

    assert not output.exists()


def test_browser_catalog_check_detects_stale_artifact(tmp_path: Path) -> None:
    _copy_repository_package(tmp_path)
    output = tmp_path / "catalog.js"
    output.write_text("stale\n", encoding="utf-8")

    fresh, _, _ = check_browser_catalog(tmp_path, output_path=output)

    assert fresh is False
    assert output.read_text(encoding="utf-8") == "stale\n"


def test_browser_catalog_cli_check_and_json_output() -> None:
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
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["schema"] == "mcel.application-package-browser-catalog-result.v1"
    assert payload["resultCode"] == "browser_catalog_fresh"
    assert payload["packageCount"] == 3
    assert payload["catalogFingerprint"] == build_application_package_catalog(ROOT).fingerprint


def test_browser_catalog_cli_check_uses_stale_exit_class(tmp_path: Path) -> None:
    _copy_repository_package(tmp_path)
    output = tmp_path / "catalog.js"
    output.write_text("stale\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--repo-root",
            str(tmp_path),
            "--output",
            str(output),
            "--check",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 4
    payload = json.loads(completed.stdout)
    assert payload["resultCode"] == "stale_browser_package_catalog"
    assert output.read_text(encoding="utf-8") == "stale\n"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_browser_catalog_javascript_exposes_data_only_lookup_api() -> None:
    script = f"""
      const catalog = require({json.dumps(str(GENERATED))});
      const record = catalog.getPackage("contract-counter");
      const first = catalog.listPackages();
      first[0].title = "mutated copy";
      console.log(JSON.stringify({{
        schema: catalog.SCHEMA,
        format: catalog.FORMAT,
        packageCount: catalog.packageCount,
        hasCounter: catalog.hasPackage("contract-counter"),
        missing: catalog.getPackage("missing-app"),
        title: record.title,
        titleAfterCopyMutation: catalog.getPackage("contract-counter").title,
        currentMode: record.conformance.currentMode,
        adapterPath: record.contracts.adapter,
        runtimeManifestUrl: record.runtimeProjection.manifestUrl,
        executableKeys: Object.keys(record).filter((key) => typeof record[key] === "function")
      }}));
    """
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload == {
        "schema": BROWSER_CATALOG_SCHEMA,
        "format": BROWSER_CATALOG_FORMAT,
        "packageCount": 3,
        "hasCounter": True,
        "missing": None,
        "title": "Contract Counter",
        "titleAfterCopyMutation": "Contract Counter",
        "currentMode": "semantic-runtime-proven",
        "adapterPath": "mcel_apps/contract-counter/contracts/adapter.js",
        "runtimeManifestUrl": "applications/mcel-packages/contract-counter/mcel.runtime.json",
        "executableKeys": [],
    }


def test_sanity_freshness_check_rejects_stale_browser_package_catalog(tmp_path: Path) -> None:
    from tools.mcel_sanity_check import (
        SanityReport,
        _check_browser_application_package_catalog_freshness,
    )

    _copy_repository_package(tmp_path)
    output, _, _ = write_browser_catalog(tmp_path)
    fresh_report = SanityReport(repo_root=tmp_path)
    _check_browser_application_package_catalog_freshness(fresh_report)
    assert fresh_report.errors == []

    output.write_text(output.read_text(encoding="utf-8") + "\n// stale\n", encoding="utf-8")
    stale_report = SanityReport(repo_root=tmp_path)
    _check_browser_application_package_catalog_freshness(stale_report)

    assert [issue.code for issue in stale_report.errors] == [
        "stale-browser-application-package-catalog"
    ]


def test_browser_shell_loads_package_catalog_before_surface_registry() -> None:
    shell = (ROOT / "main_computer" / "web" / "applications.html").read_text(encoding="utf-8")
    catalog_include = "<!-- @include applications/scripts/mcel-application-package-catalog.js -->"
    surface_include = "<!-- @include applications/scripts/mcel-app-surface-registry.js -->"

    host_bound_include = "<!-- @include applications/scripts/mcel-host-bound-application-runtime.js -->"
    assert catalog_include in shell
    assert host_bound_include in shell
    assert shell.index(catalog_include) < shell.index(host_bound_include)
    assert shell.index(host_bound_include) < shell.index(surface_include)
