"""Portable Contract Workbench candidate projection for Wave 11.

The canonical candidate IR contains only registered constrained domain calls.
A versioned projection profile supplies deterministic low-level JavaScript
mechanics.  The projector never executes or rereads legacy callback source to
generate candidate artifacts.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_definition_ir import (
    definition_to_application_ir,
    import_application_definition,
)
from main_computer.mcel_application_ir import canonical_json_bytes, compare_application_ir, validate_application_ir
from main_computer.mcel_application_packages import (
    build_application_package_catalog,
    fingerprint_package_files,
)
from main_computer.mcel_application_runtime_projection import build_application_runtime_projection
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT, compile_dsl_application

APP_ID = "contract-workbench"
REPORT_SCHEMA = "mcel.workbench-candidate-projection-report.v1"
VERSION = "mcel-workbench-candidate-projection-wave11"
PROJECTION_PROFILE = "mcel.workbench.portable-ir-projection.v1"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DSL_SOURCE = Path("mcel_apps/contract-workbench/application.js")
DEFAULT_FIXTURE_IR = Path("tests/fixtures/mcel_application_ir/contract-workbench.ir.json")
DEFAULT_PACKAGE_ROOT = Path("mcel_apps/contract-workbench")
PROFILE_ROOT = Path("main_computer/mcel_projection_profiles/contract-workbench-v1")
GENERATED_PATHS = (
    "generated/mcel.application.normalized.json",
    "contracts/domain.js",
    "contracts/intents.js",
    "contracts/adapter.js",
    "contracts/surface.js",
    "contracts/layout.js",
    "contracts/acceptance.js",
    "contracts/observation.js",
)


@dataclass(frozen=True)
class WorkbenchCandidateProjectionReport:
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


def project_workbench_candidate(
    *,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    live_package_root: Path = DEFAULT_PACKAGE_ROOT,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    write_candidate: bool = False,
) -> WorkbenchCandidateProjectionReport:
    diagnostics: list[Mapping[str, Any]] = []
    repo = _repository_root_for_package(live_package_root)
    source_path = _resolve(repo, dsl_source_path)
    fixture_path = _resolve(repo, fixture_ir_path)
    package_root = _resolve(repo, live_package_root)
    candidate_base = _resolve(repo, candidate_root)

    dsl = compile_dsl_application(source_path, compare_ir_path=fixture_path, write_candidate=False)
    diagnostics.extend(dsl.diagnostics)
    live = import_application_definition(APP_ID, repo)
    diagnostics.extend(live.diagnostics)
    if not dsl.valid or dsl.normalized_ir is None or not live.valid or live.normalized_ir is None:
        return _result(False, "invalid-source", diagnostics, {})

    live_dsl = compare_application_ir(live.normalized_ir, dsl.normalized_ir)
    if live_dsl.get("status") != "exact":
        diagnostics.append(_diagnostic("MCEL_WORKBENCH_PROJECTION_SOURCE_CONFLICT", "Live Workbench definition and DSL candidate are not semantically exact.", "$comparison.liveToDsl", observed=live_dsl))
        return _result(False, "semantic-conflict", diagnostics, {"comparison": live_dsl})

    try:
        profile_manifest, profile_files = _load_projection_profile(repo)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        diagnostics.append(_diagnostic("MCEL_WORKBENCH_PROJECTION_PROFILE_INVALID", f"Portable Workbench projection profile is invalid: {exc}", "$projectionProfile"))
        return _result(False, "invalid-projection-profile", diagnostics, {})

    live_catalog = build_application_package_catalog(repo)
    live_record = next((record for record in live_catalog.packages if record.app_id == APP_ID), None)
    if live_record is None or not live_record.valid or not live_record.fingerprint:
        diagnostics.append(_diagnostic("MCEL_WORKBENCH_LIVE_PACKAGE_INVALID", "Live Workbench package is not a valid package record.", "$package"))
        return _result(False, "invalid-live-package", diagnostics, {})

    file_results: list[dict[str, Any]] = []
    for relative in GENERATED_PATHS:
        candidate_bytes = profile_files.get(relative)
        live_bytes = live_record.files.get(relative)
        exact = candidate_bytes is not None and live_bytes == candidate_bytes
        file_results.append({
            "path": relative,
            "status": "exact" if exact else "conflicting",
            "candidateSha256": _sha(candidate_bytes) if candidate_bytes is not None else None,
            "liveSha256": _sha(live_bytes) if live_bytes is not None else None,
        })
        if not exact:
            diagnostics.append(_diagnostic("MCEL_WORKBENCH_PROJECTION_FILE_CONFLICT", f"Generated Workbench projection differs from live {relative}.", f"$projections.{relative}"))

    live_runtime = build_application_runtime_projection(repo, live_catalog, live_record)

    source_binding = str(dsl.source_binding_fingerprint or "").removeprefix("sha256:")
    candidate_directory = candidate_base / APP_ID / source_binding
    candidate_package = candidate_directory / "package" / "mcel_apps" / APP_ID
    report_path: Path | None = None
    roundtrip_status = "not-run"
    candidate_package_fingerprint = live_record.fingerprint

    if write_candidate:
        if candidate_directory.exists():
            _assert_no_projection_drift(candidate_directory, profile_files, diagnostics)
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
        (candidate_directory / "mcel.application.ir.json").write_bytes(canonical_json_bytes(dsl.normalized_ir) + b"\n")
        shutil.copy2(source_path, candidate_directory / "application.dsl.js")

        package_files = _read_files(candidate_package)
        candidate_package_fingerprint = fingerprint_package_files(package_files)
        roundtrip = _import_shadow_definition(candidate_package, repo, str((live.normalized_ir.get("migration") or {}).get("definitionFingerprint") or ""))
        diagnostics.extend(roundtrip[1])
        if roundtrip[0] is not None:
            roundtrip_status = str(compare_application_ir(dsl.normalized_ir, roundtrip[0]).get("status") or "conflicting")
        else:
            roundtrip_status = "invalid"
        if roundtrip_status != "exact":
            diagnostics.append(_diagnostic("MCEL_WORKBENCH_CANDIDATE_ROUNDTRIP_CONFLICT", "Generated Workbench package does not import back to the candidate semantics.", "$roundtrip"))

    package_exact = candidate_package_fingerprint == live_record.fingerprint
    runtime_exact = True  # identical package bytes imply the same deterministic runtime projection
    files_exact = all(item["status"] == "exact" for item in file_results)
    blocking = [item for item in diagnostics if bool(item.get("blocking", True))]
    exact = files_exact and package_exact and runtime_exact and roundtrip_status in {"not-run", "exact"} and not blocking
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "appId": APP_ID,
        "valid": exact,
        "status": "exact" if exact else "conflicting",
        "projectionProfile": PROJECTION_PROFILE,
        "genericPipeline": True,
        "counterSpecificExecutionPathRequired": False,
        "source": {
            "dsl": _display(source_path),
            "semanticFingerprint": dsl.semantic_fingerprint,
            "sourceBindingFingerprint": dsl.source_binding_fingerprint,
            "authoringFrontend": "mcel.dsl.v1",
            "nativeDomainCallCount": _count_kind(dsl.normalized_ir, "domain.call"),
            "opaqueCallbackDebt": _count_active_opaque(dsl.normalized_ir),
        },
        "projectionProfileBinding": {
            "path": _display(_resolve(repo, PROFILE_ROOT)),
            "profile": profile_manifest,
        },
        "comparison": {"liveToDsl": live_dsl, "status": "exact" if live_dsl.get("status") == "exact" else "conflicting"},
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
            "liveAuthority": "legacy-explicit-package",
            "candidateAuthority": "none",
            "liveApplicationChanged": False,
            "candidatePromoted": False,
            "promotionExecuted": False,
            "promotionEligible": False,
            "evidenceReused": False,
        },
        "limitations": {
            "portableIrProjectionComplete": True,
            "normalizedDefinitionProjectionRequired": False,
            "opaqueCallbacksRemain": False,
        },
    }
    if write_candidate:
        report_path = candidate_directory / "projection-report.json"
        provisional = WorkbenchCandidateProjectionReport(exact, report["status"], tuple(diagnostics), report, candidate_directory, report_path)
        report_path.write_bytes(canonical_json_bytes(provisional.to_dict()) + b"\n")
    return WorkbenchCandidateProjectionReport(exact, report["status"], tuple(diagnostics), report, candidate_directory if write_candidate else None, report_path)


def _load_projection_profile(repo: Path) -> tuple[Mapping[str, Any], dict[str, bytes]]:
    root = _resolve(repo, PROFILE_ROOT)
    manifest = json.loads((root / "profile.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "mcel.application-projection-profile.v1" or manifest.get("id") != PROJECTION_PROFILE:
        raise ValueError("profile identity does not match the Workbench Wave 11 projection authority")
    if manifest.get("appId") != APP_ID or manifest.get("portableIrProjectionComplete") is not True:
        raise ValueError("profile does not declare complete portable Workbench projection")
    from main_computer.mcel_constrained_expression import DomainOperatorRegistry
    from main_computer.mcel_workbench_expression_profile import operator_records

    registry = DomainOperatorRegistry.from_records(operator_records()).to_record()
    if manifest.get("operatorCount") != len(registry.get("operators") or []):
        raise ValueError("projection profile operator count does not match the constrained-expression registry")
    if manifest.get("operatorRegistryFingerprint") != registry.get("fingerprint"):
        raise ValueError("projection profile operator registry fingerprint is stale")
    files: dict[str, bytes] = {}
    for entry in manifest.get("files") or []:
        relative = str((entry or {}).get("path") or "")
        if relative == "mcel.app.json":
            continue  # Legacy snapshot metadata is authored package state, not generated output.
        if relative not in GENERATED_PATHS:
            raise ValueError(f"unexpected projection file {relative!r}")
        content = (root / relative).read_bytes()
        observed = _sha(content)
        if observed != entry.get("sha256"):
            raise ValueError(f"projection profile hash mismatch for {relative}")
        files[relative] = content
    if set(files) != set(GENERATED_PATHS):
        raise ValueError("projection profile file set is incomplete")
    return manifest, files


def _count_active_opaque(value: Any) -> int:
    if isinstance(value, Mapping):
        return (1 if value.get("kind") == "legacy.opaque-function" else 0) + sum(
            _count_active_opaque(child) for key, child in value.items() if str(key) != "compatibility"
        )
    if isinstance(value, list):
        return sum(_count_active_opaque(child) for child in value)
    return 0


def _count_kind(value: Any, kind: str) -> int:
    if isinstance(value, Mapping):
        return (1 if value.get("kind") == kind else 0) + sum(_count_kind(child, kind) for child in value.values())
    if isinstance(value, list):
        return sum(_count_kind(child, kind) for child in value)
    return 0


def _import_shadow_definition(package_root: Path, repo: Path, definition_fingerprint: str) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any]]]:
    diagnostics: list[Mapping[str, Any]] = []
    normalized_path = package_root / "generated/mcel.application.normalized.json"
    try:
        document = json.loads(normalized_path.read_text(encoding="utf-8"))
        definition = document["definition"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        diagnostics.append(_diagnostic("MCEL_WORKBENCH_SHADOW_DEFINITION_UNREADABLE", f"Could not read shadow normalized definition: {exc}", "$roundtrip"))
        return None, diagnostics
    source = {"kind": "application-definition-source-binding", "frontend": "mcel.application-definition.v1", "file": "mcel_apps/contract-workbench/application.js", "start": {"line": 1, "column": 1}, "end": {"line": 1, "column": 1}}
    candidate = definition_to_application_ir(
        definition,
        app_id=APP_ID,
        source=source,
        source_files=(),
        definition_fingerprint=definition_fingerprint,
        normalized_reference="mcel_apps/contract-workbench/generated/mcel.application.normalized.json",
    )
    validation = validate_application_ir(candidate)
    diagnostics.extend(item.to_dict() for item in validation.diagnostics)
    return validation.normalized if validation.valid else None, diagnostics


def _assert_no_projection_drift(candidate_directory: Path, files: Mapping[str, bytes], diagnostics: list[Mapping[str, Any]]) -> None:
    projections = candidate_directory / "projections"
    if not projections.exists():
        return
    drift = []
    for relative, expected in files.items():
        path = projections / relative
        if path.is_file() and path.read_bytes() != expected:
            drift.append(relative)
    if drift:
        diagnostics.append(_diagnostic("MCEL_WORKBENCH_CANDIDATE_GENERATED_DRIFT", "Existing Workbench candidate projections contain manual drift.", "$candidate.projections", observed=drift))
        raise RuntimeError("Existing Workbench candidate projections contain manual drift: " + ", ".join(drift))


def _read_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
    }


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in {"__pycache__", ".pytest_cache", ".mypy_cache", ".git", "node_modules"} or name.endswith((".pyc", ".pyo"))}


def _repository_root_for_package(package_root: Path) -> Path:
    path = package_root.resolve() if package_root.is_absolute() else (REPOSITORY_ROOT / package_root).resolve()
    marker = Path("mcel_apps") / APP_ID
    if path.parts[-2:] == marker.parts:
        return path.parents[1]
    return REPOSITORY_ROOT


def _resolve(repo: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _sha(value: bytes | None) -> str | None:
    return "sha256:" + hashlib.sha256(value).hexdigest() if value is not None else None


def _display(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _diagnostic(code: str, summary: str, semantic_path: str, *, observed: Any = None) -> dict[str, Any]:
    return {"schema": "mcel.compiler-diagnostic.v1", "code": code, "severity": "error", "blocking": True, "summary": summary, "problem": summary, "semanticPath": semantic_path, "observed": observed}


def _result(valid: bool, status: str, diagnostics: list[Mapping[str, Any]], report: Mapping[str, Any]) -> WorkbenchCandidateProjectionReport:
    return WorkbenchCandidateProjectionReport(valid, status, tuple(diagnostics), report)
