"""Patch 20: opt-in, reversible portable *package-release* promotion.

This does not change browser host routing, manifest authority, registry maturity,
production capabilities or the existing Calculator DSL promotion flow. A release
becomes active only in the portable release slot, after independent certificate,
canonical truth audit and fresh host-bound browser/effect observations pass.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_portable_certification import (
    APP_INTENTS, DEFAULT_CERTIFICATE, canonical_bytes, generated_binding,
    verify_certificate,
)
from main_computer.mcel_evidence_provenance import build_repository_provenance
from main_computer.mcel_standalone_package import build_standalone_package

SCHEMA = "mcel.portable-package-promotion.v1"
HOST_SCHEMA = "mcel.portable-host-equivalence-witness.v1"
CALCULATOR_OBSERVATION_SCHEMA = "mcel.calculator-browser-parity-observation.v1"
GENERIC_HOST_OBSERVATION_SCHEMA = "mcel.host-bound-browser-observation.v1"
SLOT = Path("runtime/build/mcel/portable-promotions")
EXPECTED = {app: sorted(names) for app, names in APP_INTENTS.items()}


class PortablePromotionRefused(RuntimeError):
    pass


def _require(ok: bool, reason: str) -> None:
    if not ok:
        raise PortablePromotionRefused(reason)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PortablePromotionRefused(f"promotion-evidence-unreadable: {path.name}: {exc}") from exc
    _require(isinstance(value, dict), "promotion-evidence-not-object")
    return value


def _root(repo: Path, override: Path | None = None) -> Path:
    repo = repo.resolve()
    destination = (override if override is not None else repo / SLOT).resolve()
    _require(destination.is_relative_to(repo), "promotion-slot-outside-repository")
    return destination


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _audit_gate(repo: Path) -> Mapping[str, Any]:
    # Invoke the same canonical reader/validator as the truth-audit CLI, not a
    # cached prior 'pass' assertion. Do not require existing portable activation.
    from main_computer.mcel_truth_audit import parse_args, run_audit
    args = parse_args(["--check", "--require-portable-certification",
                       "--require-repo-match", "--require-fresh-runtime",
                       "--require-acceptance", "--no-write"])
    report = run_audit(args, repo=repo)
    summary = report.get("summary") or {}
    bindings = summary.get("evidenceBindingStatuses") or {}
    _require(summary.get("status") == "pass", "canonical-truth-audit-failed")
    _require(bindings.get("runtime") == "exact" and bindings.get("acceptance") == "exact",
             "canonical-runtime-acceptance-not-exact")
    _require(summary.get("portableCertificationStatus") == "pass", "portable-certificate-audit-refused")
    _require(set(summary.get("portablePromotionEligibleAppIds") or []) == set(APP_INTENTS),
             "portable-certificate-audit-incomplete")
    return {"status": "pass", "runtimeBinding": "exact", "acceptanceBinding": "exact",
            "portableCertificate": "pass"}


def _certificate_gate(repo: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = repo / DEFAULT_CERTIFICATE
    cert = _read(path)
    result = verify_certificate(cert, repo)
    _require(result.get("passed") is True and set(result.get("eligibleAppIds") or []) == set(APP_INTENTS),
             "portable-repository-certificate-invalid:" + ",".join(result.get("reasons") or []))
    return cert, result


def _intent_result_names(report: Mapping[str, Any]) -> set[str]:
    return {entry.get("intentName") for entry in report.get("intentResults") or []
            if isinstance(entry, dict) and entry.get("status") == "pass"}


def capture_host_witness(repo: Path, app_id: str) -> dict[str, Any]:
    """Fresh *actual host-bound* Chromium proof; never an injected synthetic pass.

    The two host probes deliberately use their own isolated fixtures. This
    attests bounded common intent/effect contracts and common core identity;
    it does not assert general input-by-input UI/behavioral identity.
    """
    _require(app_id in APP_INTENTS, "unsupported-portable-app")
    start = build_repository_provenance(repo)["fingerprint"]
    if app_id == "calculator":
        from main_computer.mcel_calculator_parity import run_calculator_browser_parity_probe
        host = run_calculator_browser_parity_probe(repo=repo, headed=False, operation_prefix="portable-equivalence")
        observed = host.get("browserObservation") or {}
        checks = observed.get("checks") or {}
        # The real Calculator observer carries its app-specific schema in
        # `schema` and the shared host-observer contract in `genericSchema`.
        # Do not replace one with the other or accept a missing binding.
        _require(observed.get("schema") == CALCULATOR_OBSERVATION_SCHEMA and
                 observed.get("genericSchema") == GENERIC_HOST_OBSERVATION_SCHEMA,
                 "calculator-host-observation-schema-mismatch")
        _require(host.get("valid") is True and host.get("status") == "pass",
                 "calculator-host-parity-failed")
        _require(observed.get("valid") is True and observed.get("status") == "pass",
                 "calculator-host-browser-failed")
        _require(_intent_result_names(observed) == set(APP_INTENTS[app_id]),
                 "calculator-host-intent-set-mismatch")
        _require(all(checks.get(name) is True for name in (
            "freshChromiumObservation", "generatedAdapterMounted", "generatedAdapterModuleExecuted",
            "allIntentsPassed", "generatedRuntimeParityStatus", "localProviderFree",
            "capabilityAccountingClosed", "sourceTreeUnchanged")), "calculator-host-effects-incomplete")
        # The browser catalog contains package/runtime metadata, not a
        # top-level semanticFingerprint. Its observer therefore reports an
        # empty fingerprint even though the generated adapter was exercised.
        # The *host parity* report independently compiles the authoritative
        # DSL and exposes that compilation's semantic fingerprint. Bind that
        # value to the portable package's independently compiled fingerprint.
        host_semantic = host.get("semanticFingerprint")
        browser_semantic = observed.get("semanticFingerprint")
        _require(isinstance(host_semantic, str) and host_semantic.startswith("sha256:")
                 and len(host_semantic) == 71
                 and all(c in "0123456789abcdef" for c in host_semantic[7:]),
                 "calculator-host-compiled-semantic-fingerprint-missing")
        _require(browser_semantic in (None, "", host_semantic),
                 "calculator-host-browser-semantic-fingerprint-mismatch")
        observations = {
            "schema": observed.get("schema"),
            "genericSchema": observed.get("genericSchema"), "checks": checks,
            "intents": sorted(_intent_result_names(observed)),
            "effectAccounting": observed.get("effectAccounting"),
            "semanticFingerprint": host_semantic,
            "browserSemanticFingerprint": browser_semantic or None,
        }
    else:
        from main_computer.mcel_application_packages import build_application_package_catalog
        from main_computer.mcel_code_editor_ir_native_proof import run_code_editor_ir_native_intent_proof
        matches = [record for record in build_application_package_catalog(repo).packages if record.app_id == app_id]
        _require(len(matches) == 1, "code-editor-host-record-not-unique")
        host = run_code_editor_ir_native_intent_proof(repo=repo, record=matches[0])
        checks = host.get("crossCuttingChecks") or {}
        _require(host.get("passed") is True and host.get("status") == "ir-native-authoritative",
                 "code-editor-host-parity-failed")
        _require(set(host.get("intents") or {}) == set(APP_INTENTS[app_id]) and
                 all((item or {}).get("passed") is True for item in (host.get("intents") or {}).values()),
                 "code-editor-host-intent-set-mismatch")
        _require(all(checks.get(name) is True for name in (
            "freshBrowserParity", "independentBytesAfterSave", "staleSourceRefused",
            "previewNonMutating", "unapprovedPatchRefused", "reviewedApplyWritesVerified",
            "realGeneratedAdapterMounted")), "code-editor-host-effects-incomplete")
        observed_effects = host.get("browserEffectEvidence") or {}
        _require(observed_effects.get("saveAfterSha256") == hashlib.sha256(b"after();\n").hexdigest()
                 and observed_effects.get("reviewedApplyAfterSha256") == hashlib.sha256(b"reviewed();\n").hexdigest(),
                 "code-editor-host-write-effects-invalid")
        observations = {"schema": host.get("schema"), "checks": checks,
                        "intents": sorted(host["intents"]), "effects": observed_effects,
                        "semanticFingerprint": host.get("semanticFingerprint")}
    _require(build_repository_provenance(repo)["fingerprint"] == start,
             "repository-drift-during-host-proof")
    return {"schema": HOST_SCHEMA, "appId": app_id, "status": "pass",
            "generatedAt": _now(), "repositoryFingerprint": start,
            "coverage": "bounded-common-intent-core-and-host-effect-fixtures",
            "hostObservation": observations}


def verify_witness(witness: Any, app_id: str, source_fingerprint: str) -> bool:
    """Check persisted host proof claims before use, without trusting status alone."""
    if not isinstance(witness, dict) or not all((
        witness.get("schema") == HOST_SCHEMA, witness.get("appId") == app_id,
        witness.get("status") == "pass", witness.get("repositoryFingerprint") == source_fingerprint,
        witness.get("coverage") == "bounded-common-intent-core-and-host-effect-fixtures")):
        return False
    observation = witness.get("hostObservation") or {}
    if observation.get("intents") != EXPECTED.get(app_id):
        return False
    checks = observation.get("checks") or {}
    if app_id == "calculator":
        semantic = observation.get("semanticFingerprint")
        browser_semantic = observation.get("browserSemanticFingerprint")
        return (isinstance(semantic, str) and semantic.startswith("sha256:")
                and len(semantic) == 71
                and all(c in "0123456789abcdef" for c in semantic[7:])
                and browser_semantic in (None, semantic)
                and observation.get("schema") == CALCULATOR_OBSERVATION_SCHEMA and
                observation.get("genericSchema") == GENERIC_HOST_OBSERVATION_SCHEMA and
                all(checks.get(key) is True for key in (
                    "freshChromiumObservation", "generatedAdapterMounted", "generatedAdapterModuleExecuted",
                    "allIntentsPassed", "generatedRuntimeParityStatus", "localProviderFree",
                    "capabilityAccountingClosed", "sourceTreeUnchanged")))
    if app_id == "code-editor":
        effects = observation.get("effects") or {}
        return (observation.get("schema") == "mcel.code-editor-real-effects-ir-proof.v1" and
                all(checks.get(key) is True for key in (
                    "freshBrowserParity", "independentBytesAfterSave", "staleSourceRefused",
                    "previewNonMutating", "unapprovedPatchRefused", "reviewedApplyWritesVerified",
                    "realGeneratedAdapterMounted")) and
                effects.get("saveAfterSha256") == hashlib.sha256(b"after();\n").hexdigest() and
                effects.get("reviewedApplyAfterSha256") == hashlib.sha256(b"reviewed();\n").hexdigest())
    return False


def prerequisites(repo: Path, app_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    _require(app_id in APP_INTENTS, "unsupported-portable-app")
    _audit_gate(repo)
    _certificate, verdict = _certificate_gate(repo)
    return verdict, build_repository_provenance(repo)


@contextlib.contextmanager
def _locked(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".promotion.lock"
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PortablePromotionRefused("portable-promotion-concurrent-or-stale-lock") from exc
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_bytes(_json(payload))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _active_path(root: Path, app: str) -> Path:
    return root / "active" / f"{app}.json"


def _release_path(root: Path, app: str, tx: str) -> Path:
    _require(isinstance(tx, str) and len(tx) == 32 and all(ch in "0123456789abcdef" for ch in tx),
             "invalid-portable-transaction-id")
    return root / "releases" / app / tx


def validate_active(repo: Path, app_id: str, *, slot_root: Path | None = None,
                    require_certificate: bool = True) -> dict[str, Any]:
    """Read-only independent slot audit; never trusts the pointer's status flag."""
    _require(app_id in APP_INTENTS, "unsupported-portable-app")
    root = _root(repo, slot_root)
    pointer_path = _active_path(root, app_id)
    if not pointer_path.exists():
        return {"status": "inactive", "valid": False, "appId": app_id, "reasons": ["no-active-release"]}
    try:
        pointer = _read(pointer_path)
        _require(pointer.get("schema") == SCHEMA and pointer.get("appId") == app_id,
                 "active-release-pointer-invalid")
        directory = _release_path(root, app_id, pointer.get("transactionId"))
        _require(directory.is_dir() and not directory.is_symlink(), "active-release-payload-missing")
        release = _read(directory / "promotion.json")
        _require(release.get("schema") == SCHEMA and release.get("transactionId") == pointer.get("transactionId"),
                 "active-release-record-invalid")
        _require(pointer.get("releaseSha256") == _sha(_json(release)), "active-release-record-tampered")
        current = build_repository_provenance(repo)["fingerprint"]
        _require(release.get("repositoryFingerprint") == current, "active-release-repository-drift")
        _require(release.get("appId") == app_id and release.get("operation") == "portable-package-release",
                 "active-release-scope-invalid")
        witness = release.get("hostWitness")
        _require(verify_witness(witness, app_id, current), "active-release-host-proof-invalid")
        from main_computer.mcel_portable_certification import current_bindings
        binding = current_bindings(repo)[app_id]
        _require(release.get("sourceBindings") == binding, "active-release-package-source-drift")
        _require((witness.get("hostObservation") or {}).get("semanticFingerprint") ==
                 binding["source"].get("semanticFingerprint"),
                 "active-release-host-semantic-binding-mismatch")
        core_name = "calculator-core.js" if app_id == "calculator" else "code-editor-core.js"
        _require(core_name in binding["assetDigests"], "active-release-shared-core-missing")
        shared = repo / "main_computer" / "web" / "applications" / "scripts" / core_name
        _require(shared.is_file() and binding["assetDigests"][core_name] == _sha(shared.read_bytes()),
                 "active-release-shared-host-core-changed")
        package = _read(directory / "package" / "presentation.json")
        _require(generated_binding(package, directory / "package") == binding,
                 "active-release-package-bytes-invalid")
        _require(pointer.get("sourcePackageFingerprint") == binding["source"]["packageFingerprint"],
                 "active-release-fingerprint-invalid")
        if require_certificate:
            _certificate_gate(repo)
            _require(release.get("certificateSha256") == _sha((repo / DEFAULT_CERTIFICATE).read_bytes()),
                     "active-release-certificate-changed")
        return {"status": "active", "valid": True, "appId": app_id,
                "transactionId": pointer["transactionId"],
                "packageFingerprint": pointer["sourcePackageFingerprint"],
                "hostEquivalentScope": witness["coverage"]}
    except (PortablePromotionRefused, KeyError, OSError, ValueError, TypeError) as exc:
        return {"status": "refused", "valid": False, "appId": app_id, "reasons": [str(exc)]}


def promote(repo: Path, app_id: str, *, execute: bool = False,
            slot_root: Path | None = None) -> dict[str, Any]:
    """Stage or activate a package release. Only execute writes to release slot."""
    repo = repo.resolve()
    root = _root(repo, slot_root)
    verdict, provenance = prerequisites(repo, app_id)
    witness = capture_host_witness(repo, app_id)  # must be fresh on each execute
    _require(verify_witness(witness, app_id, provenance["fingerprint"]), "host-witness-unverified")
    # A rehearsal must enforce the same semantic-source binding as activation.
    # Otherwise it can pass even though execute must refuse the package.
    from main_computer.mcel_portable_certification import current_bindings
    source_binding = current_bindings(repo)[app_id]
    _require((witness.get("hostObservation") or {}).get("semanticFingerprint") ==
             source_binding["source"].get("semanticFingerprint"),
             "host-and-portable-semantic-fingerprint-mismatch")
    if not execute:
        return {"schema": SCHEMA, "status": "rehearsed", "valid": True,
                "appId": app_id, "hostWitness": witness, "repositoryFingerprint": provenance["fingerprint"],
                "portableCertificate": verdict["status"], "productionHostRouteChanged": False}
    with _locked(root):
        # Recheck after expensive browser execution and again inside the lock.
        verdict2, provenance2 = prerequisites(repo, app_id)
        _require(provenance2["fingerprint"] == provenance["fingerprint"] and verdict2 == verdict,
                 "source-or-certificate-drift-before-commit")
        transaction = uuid.uuid4().hex
        destination = _release_path(root, app_id, transaction)
        staging = root / "releases" / app_id / (".staging-" + transaction)
        _require(not destination.exists() and not staging.exists(), "promotion-release-collision")
        prior = _active_path(root, app_id)
        old_pointer = _read(prior) if prior.exists() else None
        try:
            staging.mkdir(parents=True)
            manifest = build_standalone_package(repo, app_id, staging, portable=True)
            source_dir = staging / app_id
            source_dir.rename(staging / "package")
            binding = generated_binding(manifest, staging / "package")
            from main_computer.mcel_portable_certification import current_bindings
            _require(binding == current_bindings(repo)[app_id], "staged-package-mismatches-current-source")
            _require((witness.get("hostObservation") or {}).get("semanticFingerprint") ==
                     binding["source"].get("semanticFingerprint"),
                     "host-and-portable-semantic-fingerprint-mismatch")
            core_name = "calculator-core.js" if app_id == "calculator" else "code-editor-core.js"
            _require(core_name in binding["assetDigests"], "staged-shared-core-missing")
            shared = repo / "main_computer" / "web" / "applications" / "scripts" / core_name
            _require(shared.is_file() and binding["assetDigests"][core_name] == _sha(shared.read_bytes()),
                     "staged-shared-host-core-mismatch")
            release = {"schema": SCHEMA, "transactionId": transaction, "appId": app_id,
                       "operation": "portable-package-release", "generatedAt": _now(),
                       "repositoryFingerprint": provenance["fingerprint"],
                       "sourceBindings": binding, "hostWitness": witness,
                       "certificateSha256": _sha((repo / DEFAULT_CERTIFICATE).read_bytes()),
                       "previousPointer": old_pointer, "productionHostRouteChanged": False}
            (staging / "promotion.json").write_bytes(_json(release))
            _require(build_repository_provenance(repo)["fingerprint"] == provenance["fingerprint"],
                     "source-drift-during-release-staging")
            staging.rename(destination)
            pointer = {"schema": SCHEMA, "appId": app_id, "transactionId": transaction,
                       "releaseSha256": _sha(_json(release)),
                       "sourcePackageFingerprint": binding["source"]["packageFingerprint"]}
            _atomic_json(prior, pointer)  # commit point
            return {"schema": SCHEMA, "status": "promoted", "valid": True, "appId": app_id,
                    "transactionId": transaction, "portableRelease": destination.relative_to(repo).as_posix(),
                    "productionHostRouteChanged": False, "hostEquivalentScope": witness["coverage"]}
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def rollback(repo: Path, app_id: str, transaction: str, *, slot_root: Path | None = None) -> dict[str, Any]:
    repo = repo.resolve()
    root = _root(repo, slot_root)
    _require(app_id in APP_INTENTS, "unsupported-portable-app")
    with _locked(root):
        pointer = _read(_active_path(root, app_id))
        _require(pointer.get("transactionId") == transaction, "rollback-not-current-transaction")
        release = _read(_release_path(root, app_id, transaction) / "promotion.json")
        _require(_sha(_json(release)) == pointer.get("releaseSha256"), "rollback-record-changed")
        previous = release.get("previousPointer")
        if previous is None:
            _active_path(root, app_id).unlink()
        else:
            _require(isinstance(previous, dict) and previous.get("appId") == app_id,
                     "rollback-previous-pointer-invalid")
            # Fail closed rather than reactivate a stale package under new source.
            earlier = _release_path(root, app_id, previous.get("transactionId"))
            _require(_sha(_json(_read(earlier / "promotion.json"))) == previous.get("releaseSha256"),
                     "rollback-previous-release-invalid")
            _atomic_json(_active_path(root, app_id), previous)
            if not validate_active(repo, app_id, slot_root=root)["valid"]:
                # Never leave an invalid previous release activated on failure.
                _atomic_json(_active_path(root, app_id), pointer)
                raise PortablePromotionRefused("rollback-previous-release-not-current")
        return {"schema": SCHEMA, "status": "rolled-back", "valid": True, "appId": app_id,
                "transactionId": transaction, "productionHostRouteChanged": False}
