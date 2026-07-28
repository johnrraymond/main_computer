from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from main_computer import mcel_truth_audit as audit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "main_computer" / "mcel_truth_audit.py"
DOC = ROOT / "pretty_docs" / "mcel-repository-truth-audit.md"
TRUTH_GATE_DOC = ROOT / "pretty_docs" / "mcel-app-truth-gate.md"


def fake_truth(
    app_id: str = "demo",
    *,
    maturity: str = "runtime-baseline",
    conformance_required: bool = True,
    blocking_code: str = "",
    specified: bool = True,
    runtime_core_ready: bool = True,
    full_semantic_ready: bool = False,
    runtime_present: bool = True,
    runtime_fresh: bool = True,
    runtime_status: str = "pass",
    diagnosis_completed: bool = True,
    policy_passed: bool = True,
    runtime_proven: bool = True,
    acceptance_count: int = 1,
    acceptance_present: bool = True,
    acceptance_passed: bool = True,
    semantic_proven: bool = False,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    if blocking_code:
        findings.append(
            {
                "code": blocking_code,
                "severity": "error",
                "blocking": True,
                "message": f"{blocking_code} happened",
                "detail": {"appId": app_id},
            }
        )
    return {
        "schema": "mcel-app-truth-snapshot-v1",
        "contractVersion": "mcel.app-truth-gate.v1",
        "appId": app_id,
        "generatedAt": "2026-07-27T12:00:00.000Z",
        "overallStatus": "blocked" if blocking_code else "runtime-proven",
        "requirements": {
            "present": specified,
            "schemaValid": specified,
            "contractComplete": specified,
            "acceptanceContractCount": acceptance_count,
        },
        "adapter": {
            "registered": runtime_core_ready,
            "runtimeCoreReady": runtime_core_ready,
            "fullApplicationSemanticReady": full_semantic_ready,
        },
        "surface": {
            "registered": True,
            "conformanceRequired": conformance_required,
            "maturity": maturity,
            "registryState": "surface-aware" if conformance_required else "legacy",
        },
        "evidence": {
            "runtime": {
                "present": runtime_present,
                "fresh": runtime_fresh,
                "freshness": "fresh" if runtime_fresh else "stale",
                "status": runtime_status,
                "diagnosisCompleted": diagnosis_completed,
                "policyPassed": policy_passed,
            },
            "acceptance": {
                "present": acceptance_present,
                "passed": acceptance_passed,
                "status": "pass" if acceptance_passed else "missing",
            },
        },
        "claims": {
            "specified": specified,
            "implementationPresent": True,
            "partiallyImplemented": not full_semantic_ready,
            "runtimeSurfaceProven": runtime_proven,
            "acceptanceProven": acceptance_passed,
            "semanticRuntimeProven": semantic_proven,
            "verificationComplete": runtime_proven and acceptance_passed,
        },
        "findings": findings,
        "findingCodes": [item["code"] for item in findings],
    }


def fake_snapshot(*apps: dict[str, Any]) -> dict[str, Any]:
    ordered = sorted(apps, key=lambda item: item["appId"])
    return {
        "schema": "mcel-app-truth-snapshot-v1",
        "contractVersion": "mcel.app-truth-gate.v1",
        "generatedAt": "2026-07-27T12:00:00.000Z",
        "appCount": len(ordered),
        "appIds": [item["appId"] for item in ordered],
        "statusCounts": {},
        "findingCounts": {},
        "apps": ordered,
    }


def build_report(
    *apps: dict[str, Any],
    check: bool = True,
    require_fresh_runtime: bool = False,
    require_acceptance: bool = False,
    loader_diagnostics: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return audit.build_audit_report(
        truth_snapshot=fake_snapshot(*apps),
        loader_diagnostics=loader_diagnostics or [],
        authorities={
            "requirementsRegistry": True,
            "domainAdapterRegistry": True,
            "appSurfaceRegistry": True,
            "appTruthGate": True,
        },
        source_inventory_data={"coreAuthorities": [], "domainAdapters": []},
        runtime_metadata={
            "present": False,
            "path": "runtime.json",
            "repositoryBinding": {"status": "absent", "exact": False},
        },
        acceptance_metadata={
            "present": False,
            "path": "acceptance.json",
            "repositoryBinding": {"status": "absent", "exact": False},
        },
        repository_provenance={
            "schema": "mcel-repository-provenance-v2",
            "algorithm": "sha256-source-path-content-v2",
            "fingerprint": "current",
            "fileCount": 1,
            "totalBytes": 1,
            "scope": "snapshot-source-roots-v2",
            "selectionMethod": "snapshot-source-roots",
            "sourceRoots": ["main_computer", "tests"],
        },
        check=check,
        require_fresh_runtime=require_fresh_runtime,
        require_acceptance=require_acceptance,
        require_repo_match=False,
        max_evidence_age_hours=168,
    )


def test_truth_audit_cli_and_contract_are_documented() -> None:
    assert SCRIPT.exists()
    assert DOC.exists()

    source = SCRIPT.read_text(encoding="utf-8")
    documentation = DOC.read_text(encoding="utf-8")

    assert "mcel-repository-truth-audit-v1" in source
    assert "McelAppTruthGate" in source
    assert "--check" in source
    assert "--require-fresh-runtime" in source
    assert "--require-acceptance" in source
    assert "--release-gate" in source
    assert "--latest-runtime-evidence" in source
    assert "--require-repo-match" in source
    assert "playwright_bundled_node_candidates" in source
    assert "resolve_node_executable" in source

    assert "python main_computer/mcel_truth_audit.py --check" in documentation
    assert "Playwright-bundled Node" in documentation
    assert "Legacy → runtime baseline" in documentation
    assert "Runtime baseline → semantic runtime" in documentation
    assert "blocking: true" in documentation
    assert "--release-gate" in documentation
    assert "repository fingerprint" in documentation.lower()
    assert "snapshot-source-roots" in documentation
    assert "git-tracked-and-unignored" in documentation

    truth_gate_doc = TRUTH_GATE_DOC.read_text(encoding="utf-8")
    assert "## Patch 26 repository audit consumer" in truth_gate_doc
    assert "mcel_truth_audit.py --check" in truth_gate_doc


def _make_executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)
    return path


def test_node_resolution_prefers_explicit_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    explicit = _make_executable(tmp_path / "custom-node")
    monkeypatch.setattr(audit.shutil, "which", lambda value: str(explicit) if value == str(explicit) else None)
    monkeypatch.setattr(
        audit,
        "playwright_bundled_node_candidates",
        lambda: (_make_executable(tmp_path / "playwright" / "driver" / "node"),),
    )

    assert audit.resolve_node_executable(str(explicit)) == str(explicit.resolve())


def test_invalid_explicit_node_does_not_silently_fall_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_node = _make_executable(tmp_path / "system-node")
    monkeypatch.setattr(audit.shutil, "which", lambda value: str(system_node) if value == "node" else None)

    with pytest.raises(audit.McelTruthAuditError, match="Explicit Node.js executable"):
        audit.resolve_node_executable(str(tmp_path / "missing-node"))


def test_node_resolution_uses_system_path_before_playwright(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_node = _make_executable(tmp_path / "system-node")
    monkeypatch.setattr(audit.shutil, "which", lambda value: str(system_node) if value == "node" else None)

    def unexpected_playwright_lookup() -> tuple[Path, ...]:
        raise AssertionError("Playwright lookup should not run when system Node exists.")

    monkeypatch.setattr(audit, "playwright_bundled_node_candidates", unexpected_playwright_lookup)

    assert audit.resolve_node_executable() == str(system_node.resolve())


def test_node_resolution_falls_back_to_playwright_bundled_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundled_node = _make_executable(tmp_path / "playwright" / "driver" / "node.exe")
    monkeypatch.setattr(audit.shutil, "which", lambda _value: None)
    monkeypatch.setattr(
        audit,
        "playwright_bundled_node_candidates",
        lambda: (tmp_path / "missing-node", bundled_node),
    )

    assert audit.resolve_node_executable() == str(bundled_node.resolve())


def test_default_check_uses_truth_gate_blocking_findings_only() -> None:
    legacy = fake_truth(
        "legacy-app",
        maturity="legacy",
        conformance_required=False,
        specified=False,
        runtime_core_ready=False,
        runtime_present=False,
        runtime_fresh=False,
        runtime_status="missing",
        diagnosis_completed=False,
        policy_passed=False,
        runtime_proven=False,
        acceptance_count=0,
        acceptance_present=False,
        acceptance_passed=True,
    )
    report = build_report(legacy)

    assert report["summary"]["status"] == "pass"
    assert report["summary"]["blockingAppCount"] == 0
    assert report["apps"][0]["declaredLevel"] == "legacy"
    assert report["apps"][0]["enforcement"]["reasonCodes"] == []


def test_truth_gate_blocking_finding_fails_check_without_reclassification() -> None:
    truth = fake_truth(
        blocking_code="semantic-readiness-overclaimed",
        full_semantic_ready=False,
        semantic_proven=False,
    )
    report = build_report(truth)

    app = report["apps"][0]
    assert report["summary"]["status"] == "fail"
    assert app["blockingFindingCodes"] == ["semantic-readiness-overclaimed"]
    assert app["enforcement"]["reasonCodes"] == ["semantic-readiness-overclaimed"]
    assert app["enforcement"]["reasons"][0]["source"] == "mcel-app-truth-gate"


def test_opt_in_runtime_and_acceptance_policies_have_separate_codes() -> None:
    truth = fake_truth(
        runtime_present=False,
        runtime_fresh=False,
        runtime_status="missing",
        diagnosis_completed=False,
        policy_passed=False,
        runtime_proven=False,
        acceptance_present=False,
        acceptance_passed=False,
    )
    report = build_report(
        truth,
        require_fresh_runtime=True,
        require_acceptance=True,
    )

    codes = report["apps"][0]["enforcement"]["reasonCodes"]
    assert codes == [
        "audit-required-acceptance-proof-missing",
        "audit-required-runtime-proof-missing",
    ]
    assert report["apps"][0]["blockingFindingCodes"] == []
    assert report["summary"]["status"] == "fail"


def test_failed_acceptance_evidence_is_not_duplicated_as_missing() -> None:
    truth = fake_truth(
        blocking_code="acceptance-test-failed",
        acceptance_present=True,
        acceptance_passed=False,
    )
    truth["evidence"]["acceptance"]["status"] = "fail"

    report = build_report(
        truth,
        require_acceptance=True,
    )

    app = report["apps"][0]
    assert app["blockingFindingCodes"] == ["acceptance-test-failed"]
    assert app["enforcement"]["reasonCodes"] == ["acceptance-test-failed"]
    assert "audit-required-acceptance-proof-missing" not in app["enforcement"]["reasonCodes"]


def test_promotion_rules_are_explicit_and_advisory() -> None:
    legacy_ready = fake_truth(
        "legacy-ready",
        maturity="legacy",
        conformance_required=False,
        full_semantic_ready=False,
        runtime_proven=False,
        acceptance_count=0,
    )
    baseline_ready = fake_truth(
        "baseline-ready",
        maturity="runtime-baseline",
        full_semantic_ready=True,
        semantic_proven=True,
    )
    semantic = fake_truth(
        "semantic",
        maturity="semantic-runtime",
        full_semantic_ready=True,
        semantic_proven=True,
    )
    report = build_report(semantic, legacy_ready, baseline_ready)

    apps = {item["appId"]: item for item in report["apps"]}
    assert apps["legacy-ready"]["promotion"]["nextLevel"] == "runtime-baseline"
    assert apps["legacy-ready"]["promotion"]["eligible"] is True
    assert apps["baseline-ready"]["promotion"]["nextLevel"] == "semantic-runtime"
    assert apps["baseline-ready"]["promotion"]["eligible"] is True
    assert apps["semantic"]["promotion"]["nextLevel"] is None
    assert apps["semantic"]["promotion"]["currentLevelHealthy"] is True
    assert report["summary"]["promotionReadyAppIds"] == [
        "baseline-ready",
        "legacy-ready",
    ]


def test_loader_failure_is_an_audit_integrity_failure() -> None:
    report = build_report(
        fake_truth(),
        loader_diagnostics=[
            {
                "kind": "domain-adapter",
                "path": "broken-adapter.js",
                "status": "failed",
                "error": {"name": "Error", "message": "boom"},
            }
        ],
    )

    assert report["summary"]["status"] == "fail"
    assert report["summary"]["blockingReasonCount"] == 1
    assert report["auditLevelReasons"][0]["code"] == "audit-authority-load-failed"


def test_markdown_separates_truth_enforcement_and_promotion() -> None:
    report = build_report(fake_truth())
    markdown = audit.render_markdown(report)

    assert "# MCEL repository truth audit" in markdown
    assert "## App truth" in markdown
    assert "## Blocking reasons" in markdown
    assert "## Promotion readiness" in markdown
    assert "McelAppTruthGate" in markdown
    assert "Legacy and unenrolled apps are non-blocking" in markdown


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_real_repository_audit_loads_canonical_registries(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--now",
            "2026-07-27T12:00:00Z",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 0, completed.stderr
    assert "apps: 21" in completed.stdout
    assert "blocking_apps: 0" in completed.stdout

    report_path = tmp_path / "mcel-repository-truth-audit.json"
    markdown_path = tmp_path / "mcel-repository-truth-audit.md"
    assert report_path.exists()
    assert markdown_path.exists()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == "mcel-repository-truth-audit-v1"
    assert report["summary"]["appCount"] == 21
    assert report["summary"]["declaredLevelCounts"] == {
        "legacy": 16,
        "runtime-baseline": 1,
        "semantic-runtime": 4,
    }
    assert report["authorities"] == {
        "requirementsRegistry": True,
        "domainAdapterRegistry": True,
        "appSurfaceRegistry": True,
        "appTruthGate": True,
    }
    core_paths = [
        item["path"] for item in report["sourceInventory"]["coreAuthorities"]
    ]
    assert "main_computer/web/applications/scripts/mcel-semantic-adapter-toolkit.js" in core_paths

    adapter_paths = [
        item["path"] for item in report["sourceInventory"]["domainAdapters"]
    ]
    assert "main_computer/web/applications/scripts/code-editor-semantic-adapter.js" in adapter_paths
    assert "main_computer/web/applications/scripts/git-tools-semantic-adapter.js" in adapter_paths


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is unavailable")
def test_check_can_require_fresh_runtime_without_calling_missing_evidence_broken() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--now",
            "2026-07-27T12:00:00Z",
            "--no-write",
            "--check",
            "--require-fresh-runtime",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 1
    assert "status: fail" in completed.stdout
    assert "blocking_apps: 5" in completed.stdout


def test_explicit_missing_evidence_is_an_operator_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--runtime-evidence",
            str(missing),
            "--no-write",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )

    assert completed.returncode == 2
    assert "runtime evidence file does not exist" in completed.stderr


def test_latest_runtime_evidence_selects_newest_schema_matching_report(tmp_path: Path) -> None:
    root = tmp_path / "runtime" / "reports" / "flog"
    root.mkdir(parents=True)
    older = root / "older.json"
    newer = root / "nested" / "newer.json"
    newer.parent.mkdir()
    ignored = root / "not-flog.json"

    older.write_text(
        json.dumps(
            {
                "schema": "mcel-runtime-flog-report-v2",
                "generatedAt": "2026-07-27T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    newer.write_text(
        json.dumps(
            {
                "schema": "mcel-runtime-flog-report-v2",
                "generatedAt": "2026-07-27T11:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    ignored.write_text(
        json.dumps(
            {
                "schema": "mcel-repository-truth-audit-v1",
                "generatedAt": "2026-07-27T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    selected = audit.discover_latest_evidence_path(
        repo=tmp_path,
        search_root=Path("runtime/reports/flog"),
        label="runtime",
    )

    assert selected == newer


def test_mismatched_repository_evidence_is_an_integrity_failure() -> None:
    truth = fake_truth()
    report = audit.build_audit_report(
        truth_snapshot=fake_snapshot(truth),
        loader_diagnostics=[],
        authorities={},
        source_inventory_data={},
        runtime_metadata={
            "present": True,
            "path": "runtime.json",
            "repositoryBinding": {
                "status": "mismatch",
                "exact": False,
                "currentFingerprint": "current",
                "evidenceFingerprint": "old",
            },
        },
        acceptance_metadata={
            "present": False,
            "path": "acceptance.json",
            "repositoryBinding": {"status": "absent", "exact": False},
        },
        repository_provenance={"fingerprint": "current"},
        check=True,
        require_fresh_runtime=False,
        require_acceptance=False,
        require_repo_match=False,
        max_evidence_age_hours=168,
    )

    assert report["summary"]["status"] == "fail"
    assert report["auditLevelReasons"][0]["code"] == "audit-evidence-repository-mismatch"


def test_required_repository_match_rejects_unbound_evidence() -> None:
    truth = fake_truth()
    report = audit.build_audit_report(
        truth_snapshot=fake_snapshot(truth),
        loader_diagnostics=[],
        authorities={},
        source_inventory_data={},
        runtime_metadata={
            "present": True,
            "path": "runtime.json",
            "repositoryBinding": {"status": "unbound", "exact": False},
        },
        acceptance_metadata={
            "present": False,
            "path": "acceptance.json",
            "repositoryBinding": {"status": "absent", "exact": False},
        },
        repository_provenance={"fingerprint": "current"},
        check=True,
        require_fresh_runtime=False,
        require_acceptance=False,
        require_repo_match=True,
        max_evidence_age_hours=168,
    )

    assert report["summary"]["status"] == "fail"
    assert report["auditLevelReasons"][0]["code"] == "audit-evidence-repository-unbound"


def test_mismatched_evidence_is_not_forwarded_to_truth_gate() -> None:
    evidence = audit.EvidenceInput(
        value={"schema": "mcel-runtime-flog-report-v2"},
        metadata={"present": True},
    )

    assert audit.truth_eligible_evidence(evidence, {"status": "mismatch"}) is None
    assert audit.truth_eligible_evidence(evidence, {"status": "exact"}) == evidence.value


def test_release_gate_enables_complete_evidence_policy() -> None:
    args = audit.parse_args(["--release-gate", "--no-write"])
    assert args.release_gate is True
    assert args.latest_runtime_evidence is False
    assert args.latest_acceptance_evidence is False

    source = SCRIPT.read_text(encoding="utf-8")
    assert "release_gate = bool" in source
    assert "require_repo_match = bool" in source
