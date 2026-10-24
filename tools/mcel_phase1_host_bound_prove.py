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
import hashlib
from datetime import datetime, timezone
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
SEMANTIC_WITNESS_SCHEMA = "mcel.host-bound-semantic-execution-witness.v1"
SEMANTIC_WITNESS_ROOT = Path("runtime/reports/mcel-host-bound-semantic/apps")
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


def run_host_bound_proof(*, repo: Path, app_id: str, headed: bool = False, include_native: bool = False) -> dict[str, Any]:
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
    report = {
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
    if include_native:
        report["irNativeEvidence"] = proof
    return report


def _validate_semantic_execution(app_id: str, proof: Mapping[str, Any]) -> tuple[list[str], str]:
    """Require observed results beyond method availability before issuing a witness.

    Calculator uses real host DOM/receipt effects with mocked provider transport.
    Code Editor uses real isolated ViewportServer and independent disk-byte effects.
    Neither authority claims a live external provider was contacted.
    """
    intents = proof.get("intents") or {}
    checks = proof.get("crossCuttingChecks") or {}
    observed = (proof.get("parityEvidence") or {}).get("browserObservation") or {}
    if (not isinstance(intents, dict) or not intents or
            any(not isinstance(value, dict) or value.get("passed") is not True for value in intents.values()) or
            not checks or not all(value is True for value in checks.values()) or
            observed.get("status") != "pass" or observed.get("checks", {}).get("sourceTreeUnchanged") is not True or
            proof.get("effectAccounting", {}).get("status") != "closed"):
        raise HostBoundPhaseOneError("Semantic execution witness lacks complete real browser/intent authority.")
    if app_id == "calculator":
        semantic = observed.get("intentSemanticChecks") or {}
        if (set(semantic) != set(intents) or not all(value is True for value in semantic.values()) or
                len(intents) != 11):
            raise HostBoundPhaseOneError("Calculator's real DOM/receipt effects are not proven for every intent.")
        return sorted(intents), "real-host-dom-and-receipts; provider-transport-mocked"
    if app_id == "code-editor":
        effect = proof.get("browserEffectEvidence") or {}
        required_checks = ("independentBytesAfterSave", "staleSourceRefused", "previewNonMutating",
                           "unapprovedPatchRefused", "reviewedApplyWritesVerified")
        digest_keys = ("saveAfterSha256", "reviewedApplyAfterSha256", "afterReadOnlyPreviewSha256")
        required_routes = ("/api/applications/editor/files", "/api/applications/editor/read",
                           "/api/applications/editor/project/file/save",
                           "/api/applications/editor/project/transaction/prepare",
                           "/api/applications/editor/project/transaction/apply")
        if (len(intents) != 8 or any(checks.get(key) is not True for key in required_checks) or
                any(not isinstance(effect.get(key), str) or len(effect[key]) != 64 or
                    any(ch not in "0123456789abcdef" for ch in effect[key]) for key in digest_keys) or
                not set(required_routes).issubset(set(effect.get("serverRequestPaths") or [])) or
                observed.get("checks", {}).get("realFilesystemEffects") is not True or
                observed.get("checks", {}).get("unapprovedEffectRefused") is not True):
            raise HostBoundPhaseOneError("Code Editor lacks independent real-workspace effect and refusal proof.")
        return sorted(intents), "real-disposable-workspace-byte-effects-and-refusals"
    raise HostBoundPhaseOneError("Only the two enrolled host-bound applications may issue semantic witnesses.")


def write_semantic_witness(repo: Path, app_id: str, phase_one: Mapping[str, Any]) -> dict[str, Any]:
    """Write source-bound native evidence; truth gate must independently admit it."""
    native = phase_one.get("irNativeEvidence") or {}
    names, effect_scope = _validate_semantic_execution(app_id, native)
    if (phase_one.get("passed") is not True or
            phase_one.get("truthStatus") != TRUTH_SCOPE or
            phase_one.get("pendingAcceptanceContracts") != [] or
            len(names) != phase_one.get("intentCoverage", {}).get("declaredIntentCount") or
            phase_one.get("repositoryFingerprint") != build_repository_provenance(repo)["fingerprint"]):
        raise HostBoundPhaseOneError("Semantic witness cannot be tied to complete, current Phase One evidence.")
    directory = repo / SEMANTIC_WITNESS_ROOT / app_id
    directory.mkdir(parents=True, exist_ok=True)
    native_path = directory / "mcel-ir-native-proof.json"
    raw = (json.dumps(native, sort_keys=True, indent=2) + "\n").encode("utf-8")
    native_path.write_bytes(raw)
    witness = {
        "schema": SEMANTIC_WITNESS_SCHEMA,
        "appId": app_id,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "status": "pass", "passed": True,
        "truthStatus": "host-bound-operational-semantics-witnessed",
        "canonicalTruthGateDecides": True,
        "phaseOneTruthStatus": TRUTH_SCOPE,
        "packageFingerprint": phase_one["packageFingerprint"],
        "runtimeProjectionFingerprint": phase_one["runtimeProjectionFingerprint"],
        "semanticFingerprint": phase_one["semanticFingerprint"],
        "repositoryFingerprint": phase_one["repositoryFingerprint"],
        "intentNames": names,
        "declaredIntentCount": len(names),
        "observedIntentCount": len(names),
        "effectEvidenceScope": effect_scope,
        "nativeProofArtifact": {
            "path": native_path.relative_to(repo).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
    }
    (directory / "mcel-semantic-witness.json").write_text(
        json.dumps(witness, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return witness


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--semantic-witness", action="store_true", help="Run fresh effect checks and emit separate audit-admissible evidence, without asserting final truth.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    # Delete any previous witness before attempting new execution; a failed
    # current run must never fall back to an older successful receipt.
    if args.semantic_witness:
        old_witness = args.repo_root.resolve() / SEMANTIC_WITNESS_ROOT / args.app / "mcel-semantic-witness.json"
        old_witness.unlink(missing_ok=True)
    try:
        report = run_host_bound_proof(repo=args.repo_root, app_id=args.app, headed=args.headed,
                                      include_native=args.semantic_witness)
        if args.semantic_witness:
            witness = write_semantic_witness(args.repo_root.resolve(), args.app, report)
            report.pop("irNativeEvidence", None)
            report["semanticWitness"] = {"status": "pass", "intentCount": witness["declaredIntentCount"],
                                          "effectEvidenceScope": witness["effectEvidenceScope"]}
    except (HostBoundPhaseOneError, OSError, ValueError) as exc:
        report = {"schema": SCHEMA, "appId": args.app, "status": "fail", "passed": False, "error": str(exc)}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"MCEL host-bound Phase One proof: {args.app}: {report['status']}")
        if report.get("semanticWitness"):
            print(f"  source-bound operational witness: {report['semanticWitness']['status']}; "
                  f"intents: {report['semanticWitness']['intentCount']}; final truth: canonical audit")
        if report.get("error"):
            print(report["error"])
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
