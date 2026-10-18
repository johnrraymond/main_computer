#!/usr/bin/env python3
"""Fresh Phase One proof for DSL-authoritative host-bound MCEL applications.

Host-bound apps do not mount through mcel-package-host.html.  Their proof uses
package-scoped acceptance plus the existing generic IR-native proof authority,
which performs fresh, generated-adapter browser parity against the real host
runtime.  This is a deliberately distinct proof scope from legacy package-host
surface/truth-gate proof: do not relabel one as the other.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main_computer.mcel_app_ir_native_proof import (  # noqa: E402
    AppIrNativeProofError,
    run_app_ir_native_intent_proof,
)
from main_computer.mcel_application_packages import build_application_package_catalog  # noqa: E402
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set  # noqa: E402
from main_computer.mcel_dsl_compiler import compile_dsl_application  # noqa: E402
from main_computer.mcel_evidence_provenance import build_repository_provenance  # noqa: E402

SCHEMA = "mcel.phase-one-host-bound-proof.v1"
TRUTH_SCOPE = "host-bound-ir-native-authoritative"


class HostBoundPhaseOneError(RuntimeError):
    pass


def _load_acceptance(repo: Path, app_id: str) -> dict[str, Any]:
    command = [
        sys.executable, str(repo / "main_computer/mcel_acceptance_runner.py"),
        "--repo-root", str(repo), "--app", app_id, "--check", "--json",
    ]
    result = subprocess.run(command, cwd=repo, capture_output=True, text=True, check=False)
    if result.returncode:
        raise HostBoundPhaseOneError(
            "Fresh package-scoped acceptance failed: "
            + (result.stderr.strip() or result.stdout.strip())[-2500:]
        )
    try:
        report = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HostBoundPhaseOneError("Acceptance authority did not return JSON evidence.") from exc
    if not isinstance(report, dict):
        raise HostBoundPhaseOneError("Acceptance authority did not return a report object.")
    return report


def run_host_bound_proof(*, repo: Path, app_id: str, headed: bool = False) -> dict[str, Any]:
    repo = repo.resolve()
    catalog = build_application_package_catalog(repo)
    if not catalog.ok or catalog.errors or catalog.invalid_count:
        raise HostBoundPhaseOneError("Package catalog is invalid.")
    records = [record for record in catalog.packages if record.app_id == app_id]
    if len(records) != 1 or not records[0].valid:
        raise HostBoundPhaseOneError(f"Valid application package {app_id!r} not found exactly once.")
    record = records[0]
    projections = [entry for entry in build_runtime_projection_set(repo).projections if entry.app_id == app_id]
    if len(projections) != 1 or projections[0].mount_mode != "host-bound":
        raise HostBoundPhaseOneError("Application does not have exactly one host-bound runtime projection.")
    projection = projections[0]
    manifest = json.loads((repo / record.manifest).read_text(encoding="utf-8"))
    if (manifest.get("authoring") or {}).get("status") != "dsl-authoritative":
        raise HostBoundPhaseOneError("Application is not declared DSL-authoritative.")
    source = str((record.authoring or {}).get("source") or "")
    if not source:
        raise HostBoundPhaseOneError("DSL source path is missing from application package.")
    compiled = compile_dsl_application(repo / source, write_candidate=False)
    if not compiled.valid or not compiled.semantic_fingerprint:
        raise HostBoundPhaseOneError("DSL source failed canonical compilation.")

    acceptance = _load_acceptance(repo, app_id)
    scope = acceptance.get("evidenceScope") or {}
    entries = [item for item in acceptance.get("results") or [] if item.get("appId") == app_id]
    package_entries = [item for item in acceptance.get("applicationPackages") or [] if item.get("appId") == app_id]
    if (acceptance.get("status") != "pass" or acceptance.get("passed") is not True
            or scope.get("kind") != "app-scoped" or scope.get("selectedApps") != [app_id]
            or len(entries) != 1 or entries[0].get("status") != "pass"
            or len(package_entries) != 1 or package_entries[0].get("packageFingerprint") != record.fingerprint):
        raise HostBoundPhaseOneError("Acceptance is not passing, exact app-scoped package evidence.")
    provenance_before = build_repository_provenance(repo)
    if (acceptance.get("repositoryProvenance") or {}).get("fingerprint") != provenance_before["fingerprint"]:
        raise HostBoundPhaseOneError("Acceptance evidence does not bind to current repository provenance.")

    try:
        proof = run_app_ir_native_intent_proof(
            app_id=app_id, repo=repo, record=record, acceptance=acceptance,
            observation={}, headed=headed,
        )
    except AppIrNativeProofError as exc:
        raise HostBoundPhaseOneError(f"Fresh host-bound browser/IR proof failed: {exc}") from exc

    checks = proof.get("crossCuttingChecks") or {}
    observed = (proof.get("parityEvidence") or {}).get("browserObservation") or {}
    expected_intents = proof.get("declaredIntentCount")
    if (proof.get("appId") != app_id
            or proof.get("passed") is not True or proof.get("status") != "ir-native-authoritative"
            or proof.get("semanticFingerprint") != compiled.semantic_fingerprint
            or not isinstance(expected_intents, int) or expected_intents < 1
            or proof.get("coveredIntentCount") != expected_intents
            or proof.get("observedScenarioCount") != proof.get("declaredScenarioCount")
            or proof.get("effectAccounting", {}).get("status") != "closed"
            or not checks or not all(value is True for value in checks.values())
            or (proof.get("parityEvidence") or {}).get("freshChromiumObservation") is not True
            or observed.get("status") != "pass"
            or not (observed.get("checks") or {}).get("sourceTreeUnchanged") is True):
        raise HostBoundPhaseOneError("Host-bound IR-native proof does not establish complete, fresh application authority.")
    provenance_after = build_repository_provenance(repo)
    if provenance_after["fingerprint"] != provenance_before["fingerprint"]:
        raise HostBoundPhaseOneError("Authoritative source changed during browser proof.")
    acceptance_contracts = {
        str(item.get("contractId")): {
            "status": item.get("status"),
            "enforceable": item.get("enforceable") is True,
            "executed": item.get("executed") is True,
            "summary": item.get("summary") or {},
            "bindingId": item.get("bindingId"),
        }
        for item in entries[0].get("contracts") or []
        if item.get("contractId")
    }
    pending_contracts = sorted(
        str(contract.get("contractId"))
        for contract in entries[0].get("contracts") or []
        if contract.get("status") == "not-due" and contract.get("contractId")
    )
    return {
        "schema": SCHEMA,
        "appId": app_id,
        "status": "pass",
        "passed": True,
        "truthStatus": TRUTH_SCOPE,
        "fullSemanticRuntimeProven": False,
        "pendingAcceptanceContracts": pending_contracts,
        "acceptanceContracts": acceptance_contracts,
        "proofScope": "package-scoped acceptance + host-bound generated-adapter browser parity + IR-native intent proof; not the package-host truth gate",
        "semanticFingerprint": compiled.semantic_fingerprint,
        "packageFingerprint": record.fingerprint,
        "runtimeProjectionFingerprint": projection.fingerprint,
        "repositoryFingerprint": provenance_after["fingerprint"],
        "intentCoverage": {
            "passed": True,
            "declaredIntentCount": expected_intents,
            "coveredIntentCount": proof["coveredIntentCount"],
            "declaredScenarioCount": proof["declaredScenarioCount"],
            "observedScenarioCount": proof["observedScenarioCount"],
        },
        "evidence": {
            "acceptance": {"status": "pass", "scope": "app-scoped"},
            "browserObservation": {"status": "pass", "fresh": True, "runtime": "host-bound"},
            "irNative": {"status": proof["status"], "schema": proof["schema"]},
            "effects": {"status": "closed"},
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run_host_bound_proof(repo=args.repo_root, app_id=args.app, headed=args.headed)
    except (HostBoundPhaseOneError, OSError, ValueError) as exc:
        report = {"schema": SCHEMA, "appId": args.app, "status": "fail", "passed": False, "error": str(exc)}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"MCEL host-bound Phase One proof: {args.app}: {report['status']}")
        if report.get("error"):
            print(report["error"])
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
