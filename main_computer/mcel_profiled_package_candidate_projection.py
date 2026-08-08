"""Generic profiled-package candidate projection for MCEL fixture apps.

Profiled-package apps use an app-specific deterministic projection profile to
materialize generated package files from canonical MCEL IR.  This module owns
the app-agnostic mechanics for compiling DSL, comparing it to a live package
import, applying the deterministic projection profile, writing isolated
candidate packages, checking file/package/runtime fingerprints, and producing
projection reports.

App-specific wrappers should provide only a profile: app identity, source
defaults, generated paths, projection callbacks, live-package import, and
round-trip import.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_application_ir import canonical_json_bytes, compare_application_ir
from main_computer.mcel_application_packages import (
    ApplicationPackageRecord,
    build_application_package_catalog,
    fingerprint_package_files,
)
from main_computer.mcel_application_runtime_projection import build_application_runtime_projection
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT, compile_dsl_application


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = "mcel.profiled-package-candidate-projection-report.v1"
VERSION = "mcel-profiled-package-candidate-projection-v1"


@dataclass(frozen=True)
class ProfiledPackageCandidateProjectionReport:
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
                "candidateDirectory": _display(self.candidate_directory),
                "report": _display(self.report_path) if self.report_path else None,
            }
        return value


@dataclass(frozen=True)
class ProfiledPackageProjectionProfile:
    app_id: str
    projection_profile: str
    project_ir: Callable[[Mapping[str, Any]], tuple[Mapping[str, Any], Mapping[str, bytes]]]
    import_live_ir: Callable[[str, Path], Any]
    import_candidate_ir: Callable[[Path, Path, Mapping[str, Any]], tuple[Mapping[str, Any] | None, list[Mapping[str, Any]]]]
    generated_paths: tuple[str, ...]
    default_dsl_source: Path
    default_fixture_ir: Path | None
    default_live_package_root: Path
    default_candidate_root: Path = DEFAULT_CANDIDATE_ROOT
    profile_module_path: Path | None = None
    report_schema: str = REPORT_SCHEMA
    version: str = VERSION
    report_filename: str = "projection-report.json"
    source_conflict_code: str = "MCEL_PROFILED_PACKAGE_PROJECTION_SOURCE_CONFLICT"
    projection_profile_invalid_code: str = "MCEL_PROFILED_PACKAGE_PROJECTION_PROFILE_INVALID"
    invalid_live_package_code: str = "MCEL_PROFILED_PACKAGE_LIVE_PACKAGE_INVALID"
    file_conflict_code: str = "MCEL_PROFILED_PACKAGE_PROJECTION_FILE_CONFLICT"
    drift_code: str = "MCEL_PROFILED_PACKAGE_CANDIDATE_GENERATED_DRIFT"
    roundtrip_conflict_code: str = "MCEL_PROFILED_PACKAGE_CANDIDATE_ROUNDTRIP_CONFLICT"
    source_conflict_summary: str = "Live package and DSL candidate are not semantically exact."
    drift_summary: str = "Existing candidate projections contain manual drift."
    roundtrip_conflict_summary: str = "Generated candidate package does not import back to candidate semantics."
    live_authority: str = "legacy-explicit-package"
    candidate_authority: str = "none"
    source_metrics: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None
    top_level_flags: Mapping[str, Any] = field(default_factory=dict)
    limitations: Mapping[str, Any] = field(default_factory=dict)


def project_profiled_package_candidate(
    profile: ProfiledPackageProjectionProfile,
    *,
    dsl_source_path: Path | None = None,
    fixture_ir_path: Path | None = None,
    live_package_root: Path | None = None,
    candidate_root: Path | None = None,
    write_candidate: bool = False,
) -> ProfiledPackageCandidateProjectionReport:
    dsl_source_path = dsl_source_path or profile.default_dsl_source
    fixture_ir_path = profile.default_fixture_ir if fixture_ir_path is None else fixture_ir_path
    live_package_root = live_package_root or profile.default_live_package_root
    candidate_root = candidate_root or profile.default_candidate_root

    diagnostics: list[Mapping[str, Any]] = []
    repo = _repository_root_for_package(live_package_root, profile.app_id)
    source_path = _resolve(repo, dsl_source_path)
    fixture_path = _resolve(repo, fixture_ir_path) if fixture_ir_path is not None else None
    package_root = _resolve(repo, live_package_root)
    candidate_base = _resolve(repo, candidate_root)

    dsl = compile_dsl_application(source_path, compare_ir_path=fixture_path, write_candidate=False)
    diagnostics.extend(dsl.diagnostics)
    live = profile.import_live_ir(profile.app_id, repo)
    diagnostics.extend(getattr(live, "diagnostics", ()) or ())
    live_valid = bool(getattr(live, "valid", False))
    live_ir = getattr(live, "normalized_ir", None)
    if not dsl.valid or dsl.normalized_ir is None or not live_valid or live_ir is None:
        return _result(False, "invalid-source", diagnostics, {})

    live_dsl = compare_application_ir(live_ir, dsl.normalized_ir)
    if live_dsl.get("status") != "exact":
        diagnostics.append(
            _diagnostic(
                profile.source_conflict_code,
                profile.source_conflict_summary,
                "$comparison.liveToDsl",
                observed=live_dsl,
            )
        )
        return _result(False, "semantic-conflict", diagnostics, {"comparison": live_dsl})

    try:
        profile_manifest, profile_files = profile.project_ir(dsl.normalized_ir)
        profile_files = dict(profile_files)
    except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
        diagnostics.append(
            _diagnostic(
                profile.projection_profile_invalid_code,
                f"Projection profile is invalid: {exc}",
                "$projectionProfile",
            )
        )
        return _result(False, "invalid-projection-profile", diagnostics, {})

    live_catalog = build_application_package_catalog(repo)
    live_record = next((record for record in live_catalog.packages if record.app_id == profile.app_id), None)
    if live_record is None or not live_record.valid or not live_record.fingerprint:
        diagnostics.append(
            _diagnostic(
                profile.invalid_live_package_code,
                "Live package is not a valid package record.",
                "$package",
            )
        )
        return _result(False, "invalid-live-package", diagnostics, {})

    file_results = _compare_generated_files(profile, live_record, profile_files, diagnostics)
    live_runtime = build_application_runtime_projection(repo, live_catalog, live_record)

    source_binding = str(dsl.source_binding_fingerprint or "").removeprefix("sha256:")
    candidate_directory = candidate_base / profile.app_id / source_binding
    candidate_package = candidate_directory / "package" / "mcel_apps" / profile.app_id
    report_path: Path | None = None
    roundtrip_status = "not-run"
    candidate_package_fingerprint = live_record.fingerprint

    if write_candidate:
        if candidate_directory.exists():
            _assert_no_projection_drift(candidate_directory, profile_files, diagnostics, profile)
        candidate_directory.mkdir(parents=True, exist_ok=True)
        projections = candidate_directory / "projections"
        for relative, content in profile_files.items():
            target = projections / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if candidate_package.exists():
            shutil.rmtree(candidate_package)
        shutil.copytree(package_root, candidate_package, ignore=_copy_ignore)
        for relative, content in live_record.files.items():
            target = candidate_package / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for relative, content in profile_files.items():
            target = candidate_package / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        (candidate_directory / "mcel.application.ir.json").write_bytes(
            canonical_json_bytes(dsl.normalized_ir) + b"\n"
        )
        shutil.copy2(source_path, candidate_directory / "application.dsl.js")

        package_files = _read_files(candidate_package)
        candidate_package_fingerprint = fingerprint_package_files(package_files)
        roundtrip_ir, roundtrip_diagnostics = profile.import_candidate_ir(
            candidate_package,
            repo,
            live_ir,
        )
        diagnostics.extend(roundtrip_diagnostics)
        if roundtrip_ir is not None:
            roundtrip_status = str(compare_application_ir(dsl.normalized_ir, roundtrip_ir).get("status") or "conflicting")
        else:
            roundtrip_status = "invalid"
        if roundtrip_status != "exact":
            diagnostics.append(
                _diagnostic(
                    profile.roundtrip_conflict_code,
                    profile.roundtrip_conflict_summary,
                    "$roundtrip",
                )
            )

    package_exact = candidate_package_fingerprint == live_record.fingerprint
    runtime_exact = True
    files_exact = all(item["status"] == "exact" for item in file_results)
    blocking = [item for item in diagnostics if bool(item.get("blocking", True))]
    exact = files_exact and package_exact and runtime_exact and roundtrip_status in {"not-run", "exact"} and not blocking

    source_metrics = dict(profile.source_metrics(dsl.normalized_ir) if profile.source_metrics else {})
    source = {
        "dsl": _display(source_path),
        "semanticFingerprint": dsl.semantic_fingerprint,
        "sourceBindingFingerprint": dsl.source_binding_fingerprint,
        "authoringFrontend": "mcel.dsl.v1",
        **source_metrics,
    }
    report: dict[str, Any] = {
        "schema": profile.report_schema,
        "version": profile.version,
        "appId": profile.app_id,
        "valid": exact,
        "status": "exact" if exact else "conflicting",
        "projectionProfile": profile.projection_profile,
        "genericPipeline": True,
        **dict(profile.top_level_flags),
        "source": source,
        "projectionProfileBinding": {
            "path": _display(_resolve(repo, profile.profile_module_path)) if profile.profile_module_path else None,
            "profile": profile_manifest,
        },
        "comparison": {
            "liveToDsl": live_dsl,
            "status": "exact" if live_dsl.get("status") == "exact" else "conflicting",
        },
        "projections": file_results,
        "fingerprints": {
            "semantic": dsl.semantic_fingerprint,
            "package": candidate_package_fingerprint,
            "livePackage": live_record.fingerprint,
            "packageStatus": "exact" if package_exact else "conflicting",
            "catalog": live_catalog.fingerprint,
            "runtimeProjection": live_runtime.fingerprint,
            "runtimeProjectionStatus": "exact" if runtime_exact else "conflicting",
        },
        "roundtrip": {"status": roundtrip_status},
        "authority": {
            "liveAuthority": profile.live_authority,
            "candidateAuthority": profile.candidate_authority,
            "liveApplicationChanged": False,
            "candidatePromoted": False,
            "promotionExecuted": False,
            "promotionEligible": False,
            "evidenceReused": False,
        },
        "limitations": dict(profile.limitations),
    }
    if write_candidate:
        report_path = candidate_directory / profile.report_filename
        provisional = ProfiledPackageCandidateProjectionReport(
            exact,
            report["status"],
            tuple(diagnostics),
            report,
            candidate_directory,
            report_path,
        )
        report_path.write_bytes(canonical_json_bytes(provisional.to_dict()) + b"\n")
    return ProfiledPackageCandidateProjectionReport(
        exact,
        report["status"],
        tuple(diagnostics),
        report,
        candidate_directory if write_candidate else None,
        report_path,
    )


def _compare_generated_files(
    profile: ProfiledPackageProjectionProfile,
    live_record: ApplicationPackageRecord,
    generated_files: Mapping[str, bytes],
    diagnostics: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for relative in profile.generated_paths:
        candidate_bytes = generated_files.get(relative)
        live_bytes = live_record.files.get(relative)
        exact = candidate_bytes is not None and live_bytes == candidate_bytes
        results.append(
            {
                "path": relative,
                "status": "exact" if exact else "conflicting",
                "candidateSha256": _sha(candidate_bytes) if candidate_bytes is not None else None,
                "liveSha256": _sha(live_bytes) if live_bytes is not None else None,
            }
        )
        if not exact:
            diagnostics.append(
                _diagnostic(
                    profile.file_conflict_code,
                    f"Generated projection differs from live {relative}.",
                    f"$projections.{relative}",
                )
            )
    return results


def _assert_no_projection_drift(
    candidate_directory: Path,
    files: Mapping[str, bytes],
    diagnostics: list[Mapping[str, Any]],
    profile: ProfiledPackageProjectionProfile,
) -> None:
    projections = candidate_directory / "projections"
    if not projections.exists():
        return
    drift = []
    for relative, expected in files.items():
        path = projections / relative
        if path.is_file() and path.read_bytes() != expected:
            drift.append(relative)
    if drift:
        diagnostics.append(
            _diagnostic(
                profile.drift_code,
                profile.drift_summary,
                "$candidate.projections",
                observed=drift,
            )
        )
        raise RuntimeError(profile.drift_summary + ": " + ", ".join(drift))


def _read_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    }


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {
        name
        for name in names
        if name in {"__pycache__", ".pytest_cache", ".mypy_cache", ".git", "node_modules"}
        or name.endswith((".pyc", ".pyo"))
    }


def _repository_root_for_package(package_root: Path, app_id: str) -> Path:
    path = package_root.resolve() if package_root.is_absolute() else (REPOSITORY_ROOT / package_root).resolve()
    marker = Path("mcel_apps") / app_id
    if path.parts[-2:] == marker.parts:
        return path.parents[1]
    return REPOSITORY_ROOT


def _resolve(repo: Path, path: Path | None) -> Path:
    if path is None:
        raise ValueError("Cannot resolve a missing path.")
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _sha(value: bytes | None) -> str | None:
    return "sha256:" + hashlib.sha256(value).hexdigest() if value is not None else None


def _display(path: Path | None, root: Path = REPOSITORY_ROOT) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _diagnostic(
    code: str,
    summary: str,
    semantic_path: str,
    *,
    observed: Any = None,
) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "problem": summary,
        "semanticPath": semantic_path,
        "observed": observed,
    }


def _result(
    valid: bool,
    status: str,
    diagnostics: list[Mapping[str, Any]],
    report: Mapping[str, Any],
) -> ProfiledPackageCandidateProjectionReport:
    return ProfiledPackageCandidateProjectionReport(valid, status, tuple(diagnostics), report)
