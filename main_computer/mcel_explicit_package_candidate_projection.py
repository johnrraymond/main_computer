"""Generic explicit-package candidate projection for DSL-authored MCEL apps.

An explicit MCEL package materializes deterministic generated contracts inside
the application package.  This module owns the app-agnostic mechanics for
compiling the DSL, comparing it to the live package import, generating a
candidate package, checking package/runtime fingerprints, and writing isolated
candidate artifacts.

App-specific wrappers should provide only a profile: app identity, source
defaults, generated contract paths, an IR-to-files projector, and a package
importer/round-trip authority.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from main_computer.mcel_application_ir import canonical_json_bytes, compare_application_ir
from main_computer.mcel_application_packages import (
    CATALOG_FINGERPRINT_ALGORITHM,
    ApplicationPackageRecord,
    build_application_package_catalog,
    fingerprint_package_files,
)
from main_computer.mcel_application_runtime_projection import (
    RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM,
    build_application_runtime_projection,
)
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT, compile_dsl_application


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = "mcel.explicit-package-candidate-projection-report.v1"
VERSION = "mcel-explicit-package-candidate-projection-v1"


@dataclass(frozen=True)
class ExplicitPackageProjectionProfile:
    app_id: str
    projection_profile: str
    generate_contracts: Callable[[Mapping[str, Any]], Mapping[str, bytes]]
    import_package: Callable[[Path], Any]
    generated_contracts: tuple[str, ...]
    default_dsl_source: Path
    default_fixture_ir: Path | None
    default_live_package_root: Path
    default_candidate_root: Path = DEFAULT_CANDIDATE_ROOT
    runtime_files: tuple[str, ...] = ("src/index.html", "src/app.js", "src/app.css")
    report_schema: str = REPORT_SCHEMA
    version: str = VERSION
    source_conflict_code: str = "MCEL_EXPLICIT_PACKAGE_PROJECTION_SOURCE_CONFLICT"
    unsupported_ir_code: str = "MCEL_EXPLICIT_PACKAGE_PROJECTION_UNSUPPORTED_IR"
    invalid_live_package_code: str = "MCEL_EXPLICIT_PACKAGE_LIVE_PACKAGE_RECORD_INVALID"
    file_conflict_code: str = "MCEL_EXPLICIT_PACKAGE_PROJECTION_FILE_CONFLICT"
    package_fingerprint_conflict_code: str = "MCEL_EXPLICIT_PACKAGE_CANDIDATE_PACKAGE_FINGERPRINT_CONFLICT"
    runtime_fingerprint_conflict_code: str = "MCEL_EXPLICIT_PACKAGE_CANDIDATE_RUNTIME_FINGERPRINT_CONFLICT"
    drift_code: str = "MCEL_EXPLICIT_PACKAGE_CANDIDATE_GENERATED_DRIFT"
    roundtrip_conflict_code: str = "MCEL_EXPLICIT_PACKAGE_CANDIDATE_ROUNDTRIP_CONFLICT"
    source_conflict_summary: str = "DSL and live package semantics must be exact before projection."
    unsupported_ir_status: str = "unsupported-ir"
    source_conflict_status: str = "semantic-conflict"
    invalid_live_package_status: str = "invalid-live-package"
    drift_summary: str = "Existing candidate-generated files differ from the deterministic projection."
    roundtrip_conflict_summary: str = "Generated candidate package does not import back to canonical semantics."
    contracts_generated_authority_key: str = "contractsGeneratedInCandidate"
    promotion_eligible: bool = False
    live_application_changed: bool = False
    evidence_reused: bool = False
    candidate_promoted: bool = False
    shadow_generated_runtime_manifest: str = "mcel.runtime.json"
    report_filename: str = "projection-report.json"


@dataclass(frozen=True)
class ExplicitPackageCandidateProjectionReport:
    valid: bool
    status: str
    diagnostics: tuple[Mapping[str, Any], ...]
    report: Mapping[str, Any]
    candidate_directory: Path | None = None
    report_path: Path | None = None

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.report)
        value["diagnosticCount"] = self.diagnostic_count
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        if self.candidate_directory:
            value["artifacts"] = {
                "candidateDirectory": _display_path(self.candidate_directory),
                "report": _display_path(self.report_path) if self.report_path else None,
            }
        return value


def project_explicit_package_candidate(
    profile: ExplicitPackageProjectionProfile,
    *,
    dsl_source_path: Path | None = None,
    fixture_ir_path: Path | None = None,
    live_package_root: Path | None = None,
    candidate_root: Path | None = None,
    write_candidate: bool = False,
) -> ExplicitPackageCandidateProjectionReport:
    dsl_source_path = dsl_source_path or profile.default_dsl_source
    fixture_ir_path = profile.default_fixture_ir if fixture_ir_path is None else fixture_ir_path
    live_package_root = live_package_root or profile.default_live_package_root
    candidate_root = candidate_root or profile.default_candidate_root

    diagnostics: list[Mapping[str, Any]] = []
    dsl = compile_dsl_application(dsl_source_path, compare_ir_path=fixture_ir_path, write_candidate=False)
    diagnostics.extend(dsl.diagnostics)
    live = profile.import_package(live_package_root)
    diagnostics.extend(live.diagnostics)
    if not dsl.valid or dsl.normalized_ir is None or not live.valid or live.normalized_ir is None:
        return _result(False, "invalid-source", diagnostics, profile=profile, dsl=dsl, live=live)

    comparison = compare_application_ir(dsl.normalized_ir, live.normalized_ir)
    if comparison.get("status") != "exact":
        diagnostics.append(_diagnostic(profile.source_conflict_code, profile.source_conflict_summary, "$comparison", observed=comparison))
        return _result(False, profile.source_conflict_status, diagnostics, profile=profile, dsl=dsl, live=live, comparison=comparison)

    try:
        generated = dict(profile.generate_contracts(dsl.normalized_ir))
    except ValueError as exc:
        diagnostics.append(_diagnostic(profile.unsupported_ir_code, str(exc), "$ir"))
        return _result(False, profile.unsupported_ir_status, diagnostics, profile=profile, dsl=dsl, live=live, comparison=comparison)

    live_root = live_package_root.resolve()
    projection_repo = _repository_root_for_package(live_root, app_id=profile.app_id)
    catalog = build_application_package_catalog(projection_repo)
    live_record = next((item for item in catalog.packages if item.app_id == profile.app_id), None)
    if live_record is None or not live_record.valid or not live_record.fingerprint:
        diagnostics.append(_diagnostic(profile.invalid_live_package_code, "The live package record is unavailable or invalid.", "$package"))
        return _result(False, profile.invalid_live_package_status, diagnostics, profile=profile, dsl=dsl, live=live, comparison=comparison)

    live_files = dict(live_record.files)
    file_results: list[dict[str, Any]] = []
    for relative in profile.generated_contracts:
        candidate_bytes = generated[relative]
        live_bytes = live_files.get(relative)
        exact = live_bytes == candidate_bytes
        file_results.append({
            "path": relative,
            "status": "exact" if exact else "conflicting",
            "byteStatus": "exact" if exact else "different",
            "candidateSha256": _sha(candidate_bytes),
            "liveSha256": _sha(live_bytes) if live_bytes is not None else None,
        })
        if not exact:
            diagnostics.append(_diagnostic(profile.file_conflict_code, f"Generated projection differs from live {relative}.", f"$projections.{relative}", observed=file_results[-1]))

    candidate_package_files = dict(live_files)
    candidate_package_files.update(generated)
    candidate_package_fingerprint = fingerprint_package_files(candidate_package_files)

    candidate_catalog_fingerprint = _candidate_catalog_fingerprint(catalog.packages, profile.app_id, candidate_package_fingerprint)
    candidate_runtime_fingerprint = _runtime_fingerprint(
        generated=generated,
        live_root=live_root,
        generated_contracts=profile.generated_contracts,
        runtime_files=profile.runtime_files,
        package_fingerprint=candidate_package_fingerprint,
        catalog_fingerprint=candidate_catalog_fingerprint,
    )
    live_projection = build_application_runtime_projection(projection_repo, catalog, live_record)
    manifest = _candidate_runtime_manifest(
        live_projection.manifest,
        package_fingerprint=candidate_package_fingerprint,
        catalog_fingerprint=candidate_catalog_fingerprint,
        runtime_fingerprint=candidate_runtime_fingerprint,
    )
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")

    package_exact = candidate_package_fingerprint == live_record.fingerprint
    runtime_exact = candidate_runtime_fingerprint == live_projection.fingerprint
    if not package_exact:
        diagnostics.append(_diagnostic(profile.package_fingerprint_conflict_code, "Candidate package fingerprint differs from the live package.", "$fingerprints.package"))
    if not runtime_exact:
        diagnostics.append(_diagnostic(profile.runtime_fingerprint_conflict_code, "Candidate runtime projection fingerprint differs from the live projection.", "$fingerprints.runtime"))

    source_binding = str(dsl.source_binding_fingerprint or "").removeprefix("sha256:")
    candidate_directory = candidate_root.resolve() / profile.app_id / source_binding
    expected_outputs = {**generated, profile.shadow_generated_runtime_manifest: manifest_bytes}
    drift = _existing_generated_drift(candidate_directory / "projections", expected_outputs)
    if drift:
        diagnostics.append(_diagnostic(profile.drift_code, profile.drift_summary, "$candidate.projections", observed=drift))

    roundtrip_status = "not-run"
    roundtrip_fingerprint: str | None = None
    report_path: Path | None = None
    if write_candidate and not drift:
        projections_root = candidate_directory / "projections"
        shadow_root = candidate_directory / "package" / "mcel_apps" / live_root.name
        _write_outputs(projections_root, expected_outputs)
        _write_shadow_package(live_root, shadow_root, generated)
        roundtrip = profile.import_package(shadow_root)
        diagnostics.extend(roundtrip.diagnostics)
        roundtrip_fingerprint = roundtrip.semantic_fingerprint
        if roundtrip.valid and roundtrip.normalized_ir is not None:
            roundtrip_comparison = compare_application_ir(dsl.normalized_ir, roundtrip.normalized_ir)
            roundtrip_status = str(roundtrip_comparison.get("status") or "conflicting")
        else:
            roundtrip_status = "invalid"
        if roundtrip_status != "exact":
            diagnostics.append(_diagnostic(profile.roundtrip_conflict_code, profile.roundtrip_conflict_summary, "$roundtrip"))

    all_files_exact = all(item["status"] == "exact" for item in file_results)
    exact = all_files_exact and package_exact and runtime_exact and (roundtrip_status in {"not-run", "exact"}) and not any(bool(item.get("blocking", True)) for item in diagnostics)
    status = "exact" if exact else "conflicting"
    authority = {
        "liveApplicationChanged": profile.live_application_changed,
        profile.contracts_generated_authority_key: bool(write_candidate and not drift),
        "candidatePromoted": profile.candidate_promoted,
        "evidenceReused": profile.evidence_reused,
        "promotionEligible": profile.promotion_eligible,
    }
    report: dict[str, Any] = {
        "schema": profile.report_schema,
        "version": profile.version,
        "appId": profile.app_id,
        "valid": exact,
        "status": status,
        "projectionProfile": profile.projection_profile,
        "source": {
            "dsl": _display_path(dsl_source_path.resolve()),
            "semanticFingerprint": dsl.semantic_fingerprint,
            "sourceBindingFingerprint": dsl.source_binding_fingerprint,
        },
        "comparison": comparison,
        "projections": file_results,
        "roundtrip": {"status": roundtrip_status, "semanticFingerprint": roundtrip_fingerprint},
        "fingerprints": {
            "package": {"candidate": candidate_package_fingerprint, "live": live_record.fingerprint, "status": "exact" if package_exact else "conflicting"},
            "catalog": {"candidate": candidate_catalog_fingerprint, "live": catalog.fingerprint, "status": "exact" if candidate_catalog_fingerprint == catalog.fingerprint else "conflicting"},
            "runtimeProjection": {"candidate": candidate_runtime_fingerprint, "live": live_projection.fingerprint, "status": "exact" if runtime_exact else "conflicting"},
        },
        "ownership": {
            "generated": list(profile.generated_contracts) + [profile.shadow_generated_runtime_manifest],
            "shadowCopiedFromLive": sorted(set(candidate_package_files) - set(profile.generated_contracts)),
        },
        "authority": authority,
    }
    result = ExplicitPackageCandidateProjectionReport(exact, status, tuple(diagnostics), report, candidate_directory if write_candidate else None, None)
    if write_candidate and not drift:
        report_path = candidate_directory / profile.report_filename
        result = ExplicitPackageCandidateProjectionReport(exact, status, tuple(diagnostics), report, candidate_directory, report_path)
        report_path.write_bytes(canonical_json_bytes(result.to_dict()) + b"\n")
    return result


def _candidate_catalog_fingerprint(records: tuple[ApplicationPackageRecord, ...], app_id: str, package_fingerprint: str) -> str:
    items: list[tuple[str, bytes]] = []
    for record in records:
        fingerprint = package_fingerprint if record.app_id == app_id else record.fingerprint
        payload = {
            "directoryName": record.directory_name,
            "appId": record.app_id,
            "packageRoot": record.package_root,
            "fingerprint": fingerprint,
            "valid": record.valid,
            "errors": [issue.to_dict() for issue in record.errors],
        }
        items.append((record.package_root, json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")))
    return _framed_hash(CATALOG_FINGERPRINT_ALGORITHM, items)


def _repository_root_for_package(package_root: Path, *, app_id: str) -> Path:
    package_root = package_root.resolve()
    if package_root.name == app_id and package_root.parent.name == "mcel_apps":
        return package_root.parent.parent
    for parent in package_root.parents:
        candidate = parent / "mcel_apps" / app_id
        if candidate.resolve() == package_root:
            return parent
    return REPOSITORY_ROOT


def _runtime_fingerprint(
    *,
    generated: Mapping[str, bytes],
    live_root: Path,
    generated_contracts: Sequence[str],
    runtime_files: Sequence[str],
    package_fingerprint: str,
    catalog_fingerprint: str,
) -> str:
    files: dict[str, bytes] = {}
    for relative in generated_contracts:
        files[relative] = generated[relative]
    for relative in runtime_files:
        files[relative] = (live_root / relative).read_bytes()
    inputs = dict(files)
    inputs["@source-package-fingerprint"] = package_fingerprint.encode("utf-8")
    inputs["@catalog-fingerprint"] = catalog_fingerprint.encode("utf-8")
    return _framed_hash(RUNTIME_PROJECTION_FINGERPRINT_ALGORITHM, [(path, inputs[path]) for path in sorted(inputs)])


def _candidate_runtime_manifest(live_manifest: Mapping[str, Any], *, package_fingerprint: str, catalog_fingerprint: str, runtime_fingerprint: str) -> dict[str, Any]:
    manifest = copy.deepcopy(dict(live_manifest))
    manifest["source"]["packageFingerprint"] = package_fingerprint
    manifest["source"]["catalogFingerprint"] = catalog_fingerprint
    manifest["projection"]["fingerprint"] = runtime_fingerprint
    return manifest


def _framed_hash(marker: str, items: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    digest.update(marker.encode("utf-8"))
    digest.update(b"\0")
    for name, content in items:
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _existing_generated_drift(root: Path, expected: Mapping[str, bytes]) -> list[str]:
    if not root.exists():
        return []
    drift: list[str] = []
    for relative, content in expected.items():
        path = root / relative
        if path.exists() and path.read_bytes() != content:
            drift.append(relative)
    return sorted(drift)


def _write_outputs(root: Path, files: Mapping[str, bytes]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _write_shadow_package(live_root: Path, shadow_root: Path, generated: Mapping[str, bytes]) -> None:
    if shadow_root.exists():
        shutil.rmtree(shadow_root)
    shutil.copytree(live_root, shadow_root, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", "*.pyo"))
    for relative, content in generated.items():
        path = shadow_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _result(
    valid: bool,
    status: str,
    diagnostics: list[Mapping[str, Any]],
    *,
    profile: ExplicitPackageProjectionProfile,
    dsl: Any,
    live: Any,
    comparison: Mapping[str, Any] | None = None,
) -> ExplicitPackageCandidateProjectionReport:
    return ExplicitPackageCandidateProjectionReport(valid, status, tuple(diagnostics), {
        "schema": profile.report_schema,
        "version": profile.version,
        "appId": profile.app_id,
        "valid": valid,
        "status": status,
        "projectionProfile": profile.projection_profile,
        "comparison": comparison,
        "authority": {
            "liveApplicationChanged": profile.live_application_changed,
            profile.contracts_generated_authority_key: False,
            "candidatePromoted": profile.candidate_promoted,
            "evidenceReused": profile.evidence_reused,
            "promotionEligible": profile.promotion_eligible,
        },
    })


def _diagnostic(code: str, summary: str, semantic_path: str, *, observed: Any = None) -> dict[str, Any]:
    item = {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "semanticPath": semantic_path,
    }
    if observed is not None:
        item["observed"] = observed
    return item


def _sha(content: bytes | None) -> str | None:
    return "sha256:" + hashlib.sha256(content).hexdigest() if content is not None else None


def _display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()
