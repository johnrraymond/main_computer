from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from main_computer.mcel_application_runtime_projection import (
    RUNTIME_MANIFEST_NAME,
    RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM,
    build_runtime_projection_set,
    check_runtime_projections,
    write_runtime_projections,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = ROOT / "mcel_apps" / "contract-counter"
TOOL = ROOT / "tools" / "mcel_application_runtime_projection.py"
PROJECTION_ROOT = ROOT / "runtime" / "build" / "mcel" / "web" / "applications" / "mcel-packages"


def _copy_package(target_root: Path) -> None:
    destination = target_root / "mcel_apps" / "contract-counter"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE_PACKAGE, destination)


def test_runtime_projection_contains_only_browser_execution_files() -> None:
    projection_set = build_runtime_projection_set(ROOT)
    assert projection_set.package_count == 2
    projection = next(item for item in projection_set.projections if item.app_id == "contract-counter")

    assert projection.app_id == "contract-counter"
    assert projection.fingerprint_algorithm == RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM
    assert set(projection.files) == {
        RUNTIME_MANIFEST_NAME,
        "contracts/domain.js",
        "contracts/intents.js",
        "contracts/adapter.js",
        "contracts/surface.js",
        "contracts/layout.js",
        "contracts/acceptance.js",
        "contracts/observation.js",
        "src/index.html",
        "src/app.js",
        "src/app.css",
    }
    assert "requirements.md" not in projection.files
    assert projection.manifest["modules"]["acceptance"]["export"] == "ContractCounterAcceptance"
    assert projection.manifest["modules"]["observation"]["export"] == "ContractCounterObservation"
    assert not any(path.startswith("tests/") for path in projection.files)
    assert projection.manifest["source"]["packageFingerprint"] == projection.source_package_fingerprint
    assert projection.manifest["projection"]["fingerprint"] == projection.fingerprint
    assert projection.manifest["modules"]["adapter"]["export"] == "ContractCounterAdapter"


def test_runtime_projection_is_location_independent_and_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "nested" / "second"
    _copy_package(first)
    _copy_package(second)

    first_set = build_runtime_projection_set(first)
    second_set = build_runtime_projection_set(second)

    assert first_set.catalog_fingerprint == second_set.catalog_fingerprint
    assert first_set.projections[0].fingerprint == second_set.projections[0].fingerprint
    assert first_set.projections[0].files == second_set.projections[0].files


def test_checked_in_runtime_projection_is_fresh() -> None:
    fresh, destination, projection_set = check_runtime_projections(ROOT)

    assert fresh is True
    assert destination == PROJECTION_ROOT
    assert projection_set.package_count == 2


def test_projection_check_detects_changed_and_extra_files(tmp_path: Path) -> None:
    _copy_package(tmp_path)
    output, _, changed = write_runtime_projections(tmp_path)
    assert changed is True

    manifest = output / "contract-counter" / RUNTIME_MANIFEST_NAME
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
    assert payload["packageCount"] == 2
    assert payload["changed"] is False


def test_runtime_projection_cli_uses_stale_exit_class(tmp_path: Path) -> None:
    _copy_package(tmp_path)
    output, _, _ = write_runtime_projections(tmp_path)
    (output / "contract-counter" / RUNTIME_MANIFEST_NAME).write_text("{}\n", encoding="utf-8")

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

    _copy_package(tmp_path)
    output, _, _ = write_runtime_projections(tmp_path)
    report = SanityReport(repo_root=tmp_path)
    _check_application_runtime_projection_freshness(report)
    assert report.errors == []

    (output / "contract-counter" / RUNTIME_MANIFEST_NAME).write_text("stale\n", encoding="utf-8")
    stale = SanityReport(repo_root=tmp_path)
    _check_application_runtime_projection_freshness(stale)
    assert [issue.code for issue in stale.errors] == ["stale-application-runtime-projection"]
