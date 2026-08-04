from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from main_computer.mcel_counter_compatibility import compare_counter_representations
from main_computer.mcel_counter_legacy_importer import import_counter_legacy_package

ROOT = Path(__file__).resolve().parents[1]
COUNTER_ROOT = ROOT / "mcel_apps" / "contract-counter"
FIXTURE_IR = ROOT / "tests" / "fixtures" / "mcel_application_ir" / "contract-counter.ir.json"
DSL_SOURCE = ROOT / "tests" / "fixtures" / "mcel_dsl" / "contract-counter.application.js"
IMPORT_TOOL = ROOT / "tools" / "mcel_counter_legacy_import.py"
COMPAT_TOOL = ROOT / "tools" / "mcel_counter_compatibility.py"
EXPECTED_SEMANTIC = "sha256:a9dbe6b7ec49978d313f18836b30c3394539c18f29430c3a7553837bc46eb0ef"


def _copy_counter(tmp_path: Path) -> Path:
    destination = tmp_path / "contract-counter"
    shutil.copytree(COUNTER_ROOT, destination)
    return destination


def test_live_counter_import_is_repository_derived_and_semantically_exact() -> None:
    report = import_counter_legacy_package(COUNTER_ROOT)

    assert report.valid is True
    assert report.status == "pass"
    assert report.diagnostics == ()
    assert report.semantic_fingerprint == EXPECTED_SEMANTIC
    assert report.normalized_ir is not None
    assert report.normalized_ir["provenance"]["compiler"]["id"] == "mcel.counter.legacy-importer"
    assert report.normalized_ir["migration"]["state"] == "legacy-compiled"
    assert {item["path"] for item in report.source_files} == {
        "mcel_apps/contract-counter/contracts/acceptance.js",
        "mcel_apps/contract-counter/contracts/domain.js",
        "mcel_apps/contract-counter/contracts/intents.js",
        "mcel_apps/contract-counter/contracts/layout.js",
        "mcel_apps/contract-counter/contracts/observation.js",
        "mcel_apps/contract-counter/contracts/surface.js",
        "mcel_apps/contract-counter/requirements.md",
    }


def test_three_way_compatibility_is_exact_and_not_promotion_eligible() -> None:
    result = compare_counter_representations()
    data = result.to_dict()

    assert result.valid is True
    assert result.status == "exact"
    assert result.diagnostics == ()
    assert data["migrationState"] == "dual-authored"
    assert data["compatibility"] == "exact"
    assert data["liveAuthority"] == "legacy-explicit-package"
    assert data["candidateAuthority"] == "none"
    assert data["promotionEligible"] is False
    assert set(data["semanticFingerprints"].values()) == {EXPECTED_SEMANTIC}
    assert data["sourceHashCompatibility"]["status"] == "exact"
    assert len(data["features"]) >= 20
    assert all(item["status"] == "exact" for item in data["features"])
    assert data["authority"] == {
        "candidatePromoted": False,
        "contractsGenerated": False,
        "evidenceReused": False,
        "liveApplicationChanged": False,
    }


def test_compatibility_report_writes_json_and_markdown(tmp_path: Path) -> None:
    result = compare_counter_representations(write_report=True, report_root=tmp_path / "reports")

    assert result.valid is True
    assert result.json_path and result.json_path.is_file()
    assert result.markdown_path and result.markdown_path.is_file()
    payload = json.loads(result.json_path.read_text(encoding="utf-8"))
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert payload["status"] == "exact"
    assert payload["promotionEligible"] is False
    assert "Contract Counter Application Compatibility Report" in markdown
    assert "`exact`" in markdown



def test_relocated_exact_package_preserves_source_hash_compatibility(tmp_path: Path) -> None:
    package_root = _copy_counter(tmp_path)

    result = compare_counter_representations(package_root=package_root)

    assert result.valid is True
    assert result.report["sourceHashCompatibility"]["status"] == "exact"

def test_live_source_change_invalidates_fixture_source_binding(tmp_path: Path) -> None:
    package_root = _copy_counter(tmp_path)
    requirements = package_root / "requirements.md"
    requirements.write_text(requirements.read_text(encoding="utf-8") + "\n<!-- source-only drift -->\n", encoding="utf-8")

    result = compare_counter_representations(package_root=package_root)

    assert result.valid is False
    assert result.status == "conflicting"
    assert result.report["sourceHashCompatibility"]["status"] == "conflicting"
    assert "MCEL_COUNTER_FIXTURE_SOURCE_BINDING_STALE" in {item["code"] for item in result.diagnostics}


def test_live_semantic_change_fails_closed(tmp_path: Path) -> None:
    package_root = _copy_counter(tmp_path)
    intents = package_root / "contracts" / "intents.js"
    intents.write_text(intents.read_text(encoding="utf-8").replace("count plus one", "count plus two", 1), encoding="utf-8")

    report = import_counter_legacy_package(package_root)

    assert report.valid is False
    assert "MCEL_COUNTER_EFFECT_UNSUPPORTED" in {item["code"] for item in report.diagnostics}


def test_fixture_semantic_drift_is_detected(tmp_path: Path) -> None:
    fixture = json.loads(FIXTURE_IR.read_text(encoding="utf-8"))
    increment = next(item for item in fixture["intents"] if item["id"] == "intent:increment")
    increment["transition"]["steps"][0]["amount"] = 2
    fixture["fingerprints"] = {}
    fixture_path = tmp_path / "counter.ir.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")

    result = compare_counter_representations(fixture_ir_path=fixture_path)

    assert result.valid is False
    assert result.report["comparisons"]["liveToFixture"]["status"] == "conflicting"
    assert any(item["status"] == "conflicting" for item in result.report["features"])


def test_dsl_semantic_drift_is_detected(tmp_path: Path) -> None:
    text = DSL_SOURCE.read_text(encoding="utf-8").replace(
        "count.increment(1), revision.increment(1)",
        "count.increment(2), revision.increment(1)",
        1,
    )
    dsl_path = tmp_path / "contract-counter.application.js"
    dsl_path.write_text(text, encoding="utf-8")

    result = compare_counter_representations(dsl_source_path=dsl_path)

    assert result.valid is False
    assert result.report["comparisons"]["liveToDsl"]["status"] == "conflicting"
    assert any(item["status"] == "conflicting" for item in result.report["features"])


def test_import_and_compatibility_clis_run_without_python_site_packages(tmp_path: Path) -> None:
    import_completed = subprocess.run(
        [sys.executable, "-S", str(IMPORT_TOOL)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert import_completed.returncode == 0, import_completed.stderr
    assert "status: pass" in import_completed.stdout
    assert EXPECTED_SEMANTIC in import_completed.stdout

    compatibility_completed = subprocess.run(
        [
            sys.executable,
            "-S",
            str(COMPAT_TOOL),
            "--write-report",
            "--report-root",
            str(tmp_path / "reports"),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert compatibility_completed.returncode == 0, compatibility_completed.stderr
    assert "status: exact" in compatibility_completed.stdout
    assert "migration_state: dual-authored" in compatibility_completed.stdout
    assert "promotion_eligible: false" in compatibility_completed.stdout
