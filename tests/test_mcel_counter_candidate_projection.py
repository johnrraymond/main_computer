from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from main_computer.mcel_counter_candidate_projection import (
    GENERATED_CONTRACTS,
    generate_counter_contracts,
    project_counter_candidate,
)

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "mcel_counter_candidate_projection.py"
DSL = ROOT / "tests" / "fixtures" / "mcel_dsl" / "contract-counter.application.js"
FIXTURE = ROOT / "tests" / "fixtures" / "mcel_application_ir" / "contract-counter.ir.json"
LIVE = ROOT / "mcel_apps" / "contract-counter"
EXPECTED_SEMANTIC = "sha256:a9dbe6b7ec49978d313f18836b30c3394539c18f29430c3a7553837bc46eb0ef"
EXPECTED_PACKAGE = "sha256:1ab625e72e54349c8abae27ca30cf75ccca1d7895fd172a81d93f6863e6f92b6"
EXPECTED_RUNTIME = "sha256:8641d80e0cf1caede1b8402c37c37206a941ff48bc0394d27ccabf93ed4c5037"


def _live_hashes() -> dict[str, str]:
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in LIVE.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    }


def test_counter_projection_is_exact_and_roundtrips(tmp_path: Path) -> None:
    report = project_counter_candidate(candidate_root=tmp_path, write_candidate=True)
    data = report.to_dict()

    assert report.valid is True
    assert report.status == "exact"
    assert report.diagnostics == ()
    assert len(data["projections"]) == 7
    assert all(item["status"] == "exact" for item in data["projections"])
    assert data["roundtrip"] == {"status": "exact", "semanticFingerprint": EXPECTED_SEMANTIC}
    assert data["fingerprints"]["package"] == {"candidate": EXPECTED_PACKAGE, "live": EXPECTED_PACKAGE, "status": "exact"}
    assert data["fingerprints"]["runtimeProjection"] == {"candidate": EXPECTED_RUNTIME, "live": EXPECTED_RUNTIME, "status": "exact"}
    assert data["authority"] == {
        "liveApplicationChanged": False,
        "contractsGeneratedInCandidate": True,
        "candidatePromoted": False,
        "evidenceReused": False,
        "promotionEligible": False,
    }


def test_generated_contracts_are_byte_exact_with_live_package() -> None:
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    generated = generate_counter_contracts(ir)

    assert set(generated) == set(GENERATED_CONTRACTS)
    for relative, content in generated.items():
        assert content == (LIVE / relative).read_bytes(), relative


def test_candidate_stages_projections_shadow_package_manifest_and_report(tmp_path: Path) -> None:
    report = project_counter_candidate(candidate_root=tmp_path, write_candidate=True)
    assert report.candidate_directory is not None
    root = report.candidate_directory

    for relative in GENERATED_CONTRACTS:
        assert (root / "projections" / relative).read_bytes() == (LIVE / relative).read_bytes()
        assert (root / "package" / "mcel_apps" / "contract-counter" / relative).read_bytes() == (LIVE / relative).read_bytes()
    manifest = json.loads((root / "projections" / "mcel.runtime.json").read_text(encoding="utf-8"))
    assert manifest["projection"]["fingerprint"] == EXPECTED_RUNTIME
    assert manifest["source"]["packageFingerprint"] == EXPECTED_PACKAGE
    persisted = json.loads((root / "projection-report.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "exact"
    assert persisted["authority"]["candidatePromoted"] is False


def test_projection_does_not_modify_live_package(tmp_path: Path) -> None:
    before = _live_hashes()
    report = project_counter_candidate(candidate_root=tmp_path, write_candidate=True)
    assert report.valid is True
    assert _live_hashes() == before


def test_existing_generated_drift_fails_closed(tmp_path: Path) -> None:
    first = project_counter_candidate(candidate_root=tmp_path, write_candidate=True)
    assert first.valid is True and first.candidate_directory
    drifted = first.candidate_directory / "projections" / "contracts" / "domain.js"
    drifted.write_text("// manual drift\n", encoding="utf-8")

    second = project_counter_candidate(candidate_root=tmp_path, write_candidate=True)
    assert second.valid is False
    assert second.status == "conflicting"
    assert any(item.get("code") == "MCEL_COUNTER_CANDIDATE_GENERATED_DRIFT" for item in second.diagnostics)
    assert drifted.read_text(encoding="utf-8") == "// manual drift\n"


def test_unsupported_counter_ir_is_rejected() -> None:
    ir = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ir["intents"] = [item for item in ir["intents"] if item["id"] != "intent:direct-set"]
    try:
        generate_counter_contracts(ir)
    except ValueError as exc:
        assert "direct-set" in str(exc)
    else:
        raise AssertionError("unsupported IR was accepted")


def test_cli_runs_without_python_site_packages(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-S", str(TOOL), "--write-candidate", "--candidate-root", str(tmp_path)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "status: exact" in completed.stdout
    assert "files_exact: 7/7" in completed.stdout
    assert "roundtrip: exact" in completed.stdout


def test_report_only_mode_does_not_write_candidate(tmp_path: Path) -> None:
    report = project_counter_candidate(candidate_root=tmp_path, write_candidate=False)
    assert report.valid is True
    assert report.candidate_directory is None
    assert list(tmp_path.iterdir()) == []
