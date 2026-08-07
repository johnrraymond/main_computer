"""Generic host-bound candidate projection for DSL-authored MCEL apps.

A host-bound app keeps its durable HTML/CSS/runtime surface in the live
repository while MCEL owns the semantic declaration and deterministic generated
contract artifacts.  This module owns the shared mechanics for compiling the DSL,
running an app projection profile, writing isolated candidate artifacts, checking
candidate drift, and building a projection report.

App-specific wrappers should only supply a small profile: app identity, source
defaults, report labels, and the deterministic IR projector.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import shutil

from main_computer.mcel_application_ir import canonical_json_bytes
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT, compile_dsl_application


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = "mcel.host-bound-candidate-projection-report.v1"
VERSION = "mcel-host-bound-candidate-projection-v1"


@dataclass(frozen=True)
class HostBoundProjectionProfile:
    app_id: str
    projection_profile: str
    project_ir: Callable[[Mapping[str, Any]], Any]
    default_dsl_source: Path
    default_package_root: Path
    report_schema: str = REPORT_SCHEMA
    version: str = VERSION
    unsupported_ir_code: str = "MCEL_HOST_BOUND_PROJECTION_UNSUPPORTED_IR"
    drift_code: str = "MCEL_HOST_BOUND_PROJECTION_DRIFT"
    drift_summary: str = "Existing host-bound candidate files differ from deterministic projection."
    live_app_changed: bool = True
    host_bound_runtime_active: bool = True
    legacy_semantic_adapter_remains_live: bool = False
    candidate_promoted: bool = True
    promotion_eligible: bool = True
    generated_artifacts_are_derived: bool = True
    legacy_live_app_changed_key: str | None = None


@dataclass(frozen=True)
class HostBoundCandidateProjectionReport:
    valid: bool
    status: str
    diagnostics: tuple[Mapping[str, Any], ...]
    report: Mapping[str, Any]
    candidate_directory: Path | None = None
    report_path: Path | None = None
    repository_root: Path = REPOSITORY_ROOT

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.report)
        value["diagnosticCount"] = self.diagnostic_count
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        if self.candidate_directory is not None:
            value["artifacts"] = {
                "candidateDirectory": _display(self.candidate_directory, self.repository_root),
                "report": _display(self.report_path, self.repository_root) if self.report_path else None,
            }
        return value


def project_host_bound_candidate(
    profile: HostBoundProjectionProfile,
    *,
    dsl_source_path: Path | None = None,
    fixture_ir_path: Path | None = None,
    live_package_root: Path | None = None,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    write_candidate: bool = False,
) -> HostBoundCandidateProjectionReport:
    """Compile and project a host-bound MCEL app into isolated candidate files."""

    repo = _repository_root_for_source(dsl_source_path or profile.default_dsl_source)
    source = _resolve(repo, dsl_source_path or profile.default_dsl_source)
    package_root = _resolve(repo, live_package_root or profile.default_package_root)
    output_root = _resolve(repo, candidate_root)
    fixture = _resolve(repo, fixture_ir_path) if fixture_ir_path is not None else None

    compiled = compile_dsl_application(source, compare_ir_path=fixture, write_candidate=False)
    diagnostics = list(compiled.diagnostics)
    if not compiled.valid or compiled.normalized_ir is None:
        return _result(profile, False, "invalid-source", diagnostics, compiled, repo)

    try:
        projection = profile.project_ir(compiled.normalized_ir)
    except ValueError as exc:
        diagnostics.append(_diagnostic(profile.unsupported_ir_code, str(exc), "$ir"))
        return _result(profile, False, "unsupported-ir", diagnostics, compiled, repo)

    source_binding = str(compiled.source_binding_fingerprint or "").removeprefix("sha256:")
    candidate_directory = output_root / profile.app_id / source_binding
    projection_root = candidate_directory / "projections"
    shadow_root = candidate_directory / "package" / "mcel_apps" / profile.app_id

    drift = _existing_drift(projection_root, projection.files)
    if drift:
        diagnostics.append(_diagnostic(
            profile.drift_code,
            profile.drift_summary,
            "$candidate.projections",
            observed=drift,
        ))

    if write_candidate and not drift:
        _write_files(projection_root, projection.files)
        _write_shadow_package(package_root, shadow_root, projection.files)

    blocking = [item for item in diagnostics if bool(item.get("blocking", True))]
    valid = not blocking and not drift
    status = "pass" if valid else "conflicting"
    host = _host_bound_projection_info(compiled.normalized_ir, profile)
    report = {
        "schema": profile.report_schema,
        "version": profile.version,
        "appId": profile.app_id,
        "valid": valid,
        "status": status,
        "projectionProfile": projection.profile_id,
        "source": {
            "dsl": _display(source, repo),
            "semanticFingerprint": compiled.semantic_fingerprint,
            "sourceBindingFingerprint": compiled.source_binding_fingerprint,
        },
        "projection": {
            "fileCount": len(projection.files),
            "files": [
                {
                    "path": path,
                    "sha256": projection.file_hashes[path],
                }
                for path in sorted(projection.files)
            ],
            "intentCount": len(compiled.normalized_ir.get("intents") or []),
            "capabilityCount": len(compiled.normalized_ir.get("capabilities") or []),
            **host,
        },
        "authority": _authority_report(
            profile,
            contracts_generated=bool(write_candidate and not drift),
            include_legacy_adapter=True,
        ),
    }
    result = HostBoundCandidateProjectionReport(
        valid,
        status,
        tuple(diagnostics),
        report,
        candidate_directory if write_candidate else None,
        None,
        repo,
    )
    if write_candidate and not drift:
        report_path = candidate_directory / "projection-report.json"
        result = HostBoundCandidateProjectionReport(
            valid,
            status,
            tuple(diagnostics),
            report,
            candidate_directory,
            report_path,
            repo,
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(canonical_json_bytes(result.to_dict()) + b"\n")
    return result


def _authority_report(
    profile: HostBoundProjectionProfile,
    *,
    contracts_generated: bool,
    include_legacy_adapter: bool,
) -> dict[str, Any]:
    authority: dict[str, Any] = {
        "liveAppChanged": profile.live_app_changed,
        "hostBoundRuntimeActive": profile.host_bound_runtime_active,
        "contractsGeneratedInCandidate": contracts_generated,
        "candidatePromoted": profile.candidate_promoted,
        "promotionEligible": profile.promotion_eligible,
    }
    if include_legacy_adapter:
        authority["legacySemanticAdapterRemainsLive"] = profile.legacy_semantic_adapter_remains_live
        authority["generatedArtifactsAreDerived"] = profile.generated_artifacts_are_derived
    if profile.legacy_live_app_changed_key:
        authority[profile.legacy_live_app_changed_key] = profile.live_app_changed
    return authority


def _host_bound_projection_info(
    application_ir: Mapping[str, Any],
    profile: HostBoundProjectionProfile,
) -> dict[str, Any]:
    surfaces = [
        item for item in application_ir.get("surfaces") or []
        if isinstance(item, Mapping)
    ]
    preferred_id = f"surface:{profile.app_id}.workspace"
    surface = next((item for item in surfaces if item.get("id") == preferred_id), None)
    if surface is None and surfaces:
        surface = surfaces[0]
    surface = surface or {}
    return {
        "hostRoute": str(surface.get("route") or ""),
        "rootSelector": str(surface.get("root") or ""),
        "presentationAuthority": str(surface.get("presentationAuthority") or ""),
        "hostBoundRuntimeActive": profile.host_bound_runtime_active,
    }


def _write_shadow_package(
    package_root: Path,
    shadow_root: Path,
    generated: Mapping[str, bytes],
) -> None:
    if shadow_root.exists():
        shutil.rmtree(shadow_root)
    shutil.copytree(
        package_root,
        shadow_root,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc", "*.pyo"),
    )
    for relative, content in generated.items():
        target = shadow_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _write_files(root: Path, files: Mapping[str, bytes]) -> None:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _existing_drift(root: Path, expected: Mapping[str, bytes]) -> list[str]:
    if not root.exists():
        return []
    actual = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    paths = sorted(set(actual) | set(expected))
    return [
        path for path in paths
        if actual.get(path) != expected.get(path)
    ]


def _repository_root_for_source(path: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        for parent in candidate.resolve().parents:
            if (parent / "mcel_apps").is_dir() and (parent / "main_computer").is_dir():
                return parent
    return REPOSITORY_ROOT


def _resolve(repo: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def _diagnostic(
    code: str,
    summary: str,
    semantic_path: str,
    *,
    observed: Any = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
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


def _result(
    profile: HostBoundProjectionProfile,
    valid: bool,
    status: str,
    diagnostics: list[Mapping[str, Any]],
    compiled: Any,
    repo: Path,
) -> HostBoundCandidateProjectionReport:
    return HostBoundCandidateProjectionReport(
        valid,
        status,
        tuple(diagnostics),
        {
            "schema": profile.report_schema,
            "version": profile.version,
            "appId": profile.app_id,
            "valid": valid,
            "status": status,
            "projectionProfile": profile.projection_profile,
            "source": {
                "semanticFingerprint": getattr(compiled, "semantic_fingerprint", None),
                "sourceBindingFingerprint": getattr(compiled, "source_binding_fingerprint", None),
            },
            "authority": _authority_report(
                profile,
                contracts_generated=False,
                include_legacy_adapter=False,
            ),
        },
        repository_root=repo,
    )


def _display(path: Path | None, repo: Path = REPOSITORY_ROOT) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
