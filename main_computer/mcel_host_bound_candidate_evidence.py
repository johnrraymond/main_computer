"""Generic candidate evidence aggregation for host-bound MCEL applications.

This module coordinates the shared proof stages for a DSL-authored host-bound
app without knowing the app's domain.  App-specific wrappers should supply a
profile: app identity, DSL/package defaults, projection hook, parity hook,
IR-native proof hook, browser probe hook, and compatibility report labels.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_application_ir import canonical_json_bytes
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_evidence_provenance import build_repository_provenance


REPORT_SCHEMA = "mcel.host-bound-candidate-evidence-report.v1"
REPORT_VERSION = "mcel-host-bound-candidate-evidence-v1"
DEFAULT_REPORT_ROOT = Path("runtime/reports/mcel-compiler-candidates")
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class HostBoundCandidateEvidenceError(RuntimeError):
    """Raised when generic host-bound candidate evidence cannot be produced."""


@dataclass(frozen=True)
class HostBoundCandidateEvidenceProfile:
    app_id: str
    default_dsl_source: Path
    default_package_root: Path
    default_candidate_root: Path
    project_candidate: Callable[..., Any]
    run_generated_adapter_parity: Callable[..., Any]
    run_ir_native_proof: Callable[..., Mapping[str, Any]]
    run_browser_parity_probe: Callable[..., Mapping[str, Any]]
    report_schema: str = REPORT_SCHEMA
    report_version: str = REPORT_VERSION
    report_filename: str = "mcel-host-bound-candidate-evidence-report.json"
    report_markdown_filename: str = "mcel-host-bound-candidate-evidence-report.md"
    report_title: str = "Host-bound Candidate Evidence"
    truth_status: str = "fresh-browser-dsl-authoritative-ir-native"
    dsl_invalid_code: str = "MCEL_HOST_BOUND_CANDIDATE_DSL_INVALID"
    projection_invalid_code: str = "MCEL_HOST_BOUND_CANDIDATE_PROJECTION_INVALID"
    stage_failed_code: str = "MCEL_HOST_BOUND_CANDIDATE_STAGE_FAILED"
    live_authority: str = "existing-host-runtime"
    candidate_authority: str = "mcel.dsl.v1"
    live_authority_changed_key: str = "liveAppChanged"
    legacy_semantic_adapter_authority_key: str = "legacySemanticAdapterRemainsLive"
    host_bound_runtime_active: bool = True
    legacy_semantic_adapter_remains_live: bool = False
    candidate_promoted: bool = True
    promotion_eligible: bool = True


@dataclass(frozen=True)
class HostBoundCandidateEvidenceResult:
    valid: bool
    status: str
    report: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]
    output_directory: Path | None = None
    repository_root: Path = REPOSITORY_ROOT

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.report)
        value["diagnosticCount"] = self.diagnostic_count
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        if self.output_directory is not None:
            value.setdefault("artifacts", {})["outputDirectory"] = _display_path(self.output_directory, self.repository_root)
        return value


def run_host_bound_candidate_evidence(
    profile: HostBoundCandidateEvidenceProfile,
    *,
    repo_root: Path = REPOSITORY_ROOT,
    dsl_source_path: Path | None = None,
    fixture_ir_path: Path | None = None,
    candidate_root: Path | None = None,
    report_root: Path = DEFAULT_REPORT_ROOT,
    headed: bool = False,
    write_report: bool = True,
    command_runner: Any = None,
    node_probe_runner: Any = None,
    browser_probe_runner: Any = None,
    operation_prefix: str = "candidate",
) -> HostBoundCandidateEvidenceResult:
    """Aggregate compile/projection/runtime/browser proof for a host-bound app."""

    del fixture_ir_path, command_runner, node_probe_runner
    repo = Path(repo_root).resolve()
    diagnostics: list[Mapping[str, Any]] = []
    package_root = _resolve(repo, profile.default_package_root)
    live_before = _tree_fingerprint(profile, package_root)

    dsl_source = _resolve(repo, dsl_source_path or profile.default_dsl_source)
    compiled = compile_dsl_application(dsl_source, write_candidate=False)
    diagnostics.extend(compiled.diagnostics)
    if not compiled.valid or compiled.normalized_ir is None:
        diagnostics.append(_diagnostic(profile.dsl_invalid_code, f"{profile.app_id} DSL did not compile.", "$source"))
        return _failure(profile, "invalid-dsl", diagnostics, compiled, repo)

    projection = profile.project_candidate(
        dsl_source_path=dsl_source,
        live_package_root=package_root,
        candidate_root=_resolve(repo, candidate_root or profile.default_candidate_root),
        write_candidate=write_report,
    )
    diagnostics.extend(projection.diagnostics)
    if not projection.valid:
        diagnostics.append(_diagnostic(profile.projection_invalid_code, f"{profile.app_id} deterministic projection failed.", "$projection"))
        return _failure(profile, "invalid-projection", diagnostics, compiled, repo)

    parity = profile.run_generated_adapter_parity(repo_root=repo, operation_prefix=operation_prefix)
    diagnostics.extend(parity.diagnostics)
    record = _app_record(repo, profile.app_id)
    ir_proof = profile.run_ir_native_proof(
        repo=repo,
        record=record,
        acceptance={},
        observation={},
        headed=headed,
        browser_probe_runner=browser_probe_runner or profile.run_browser_parity_probe,
    )
    catalog = build_application_package_catalog(repo)
    runtime = build_runtime_projection_set(repo)
    runtime_records = [item for item in runtime.projections if item.app_id == profile.app_id]
    provenance = build_repository_provenance(repo)
    live_after = _tree_fingerprint(profile, package_root)
    fresh_browser = ((ir_proof.get("parityEvidence") or {}).get("freshChromiumObservation") is True)

    stage_checks = {
        "dslCompilation": compiled.valid and compiled.normalized_ir is not None,
        "candidateProjection": projection.valid,
        "packageValidation": record.valid is True,
        "runtimeProjection": len(runtime_records) == 1 and runtime_records[0].mount_mode == "host-bound",
        "generatedAdapterAuthority": parity.valid is True,
        "freshBrowserParity": fresh_browser,
        "irNativeAuthoritativeProof": ir_proof.get("passed") is True,
        "repositoryBinding": bool(provenance.get("fingerprint")),
        "livePackageUnchanged": live_before == live_after,
    }
    for stage, passed in stage_checks.items():
        if not passed:
            diagnostics.append(_diagnostic(profile.stage_failed_code, f"{profile.app_id} candidate evidence stage failed: {stage}.", f"$stages.{stage}"))

    valid = all(stage_checks.values()) and not any(item.get("blocking", True) for item in diagnostics)
    output_directory = _output_directory(repo, report_root, profile.app_id, compiled.source_binding_fingerprint)
    report: dict[str, Any] = {
        "schema": profile.report_schema,
        "genericSchema": REPORT_SCHEMA,
        "version": profile.report_version,
        "genericVersion": REPORT_VERSION,
        "appId": profile.app_id,
        "status": "pass" if valid else "fail",
        "valid": valid,
        "truthStatus": profile.truth_status,
        "candidate": {
            "semanticFingerprint": compiled.semantic_fingerprint,
            "sourceBindingFingerprint": compiled.source_binding_fingerprint,
            "repositoryProvenance": provenance,
            "packageFingerprint": record.fingerprint,
            "catalogFingerprint": catalog.fingerprint,
            "candidateDirectory": _display_path(projection.candidate_directory, repo) if projection.candidate_directory else None,
        },
        "stages": {name: {"status": "pass" if passed else "fail"} for name, passed in stage_checks.items()},
        "authority": _authority_report(profile, live_before != live_after, bool(write_report), fresh_browser),
        "parityEvidence": parity.report,
        "irNativeAuthoritativeProof": ir_proof,
    }

    if write_report:
        output_directory.mkdir(parents=True, exist_ok=True)
        _write_json(output_directory / profile.report_filename, report)
        (output_directory / profile.report_markdown_filename).write_text(_render_markdown(profile, report), encoding="utf-8")
        candidate_ir = output_directory / "mcel.application.ir.json"
        candidate_ir.write_bytes(canonical_json_bytes(compiled.normalized_ir) + b"\n")
        report.setdefault("artifacts", {})["candidateIr"] = _display_path(candidate_ir, repo)

    return HostBoundCandidateEvidenceResult(valid, "pass" if valid else "fail", report, tuple(diagnostics), output_directory if write_report else None, repo)


def _authority_report(
    profile: HostBoundCandidateEvidenceProfile,
    live_changed: bool,
    contracts_generated: bool,
    fresh_browser: bool,
) -> dict[str, Any]:
    authority = {
        "liveAuthority": profile.live_authority,
        "candidateAuthority": profile.candidate_authority,
        "hostBoundRuntimeActive": profile.host_bound_runtime_active,
        "contractsGeneratedInCandidate": contracts_generated,
        "candidatePromoted": profile.candidate_promoted,
        "promotionEligible": profile.promotion_eligible,
        "freshChromiumObservation": fresh_browser,
    }
    authority[profile.live_authority_changed_key] = live_changed
    authority[profile.legacy_semantic_adapter_authority_key] = profile.legacy_semantic_adapter_remains_live
    return authority


def _app_record(repo: Path, app_id: str) -> Any:
    catalog = build_application_package_catalog(repo)
    matches = [item for item in catalog.packages if item.app_id == app_id]
    if len(matches) != 1:
        raise HostBoundCandidateEvidenceError(f"{app_id} package was not discovered exactly once.")
    return matches[0]


def _output_directory(repo: Path, report_root: Path, app_id: str, source_binding_fingerprint: str | None) -> Path:
    root = report_root if Path(report_root).is_absolute() else repo / report_root
    source = str(source_binding_fingerprint or "unknown").removeprefix("sha256:")
    return root / app_id / source


def _tree_fingerprint(profile: HostBoundCandidateEvidenceProfile, tree_root: Path) -> str:
    digest = hashlib.sha256()
    digest.update(f"{profile.app_id}-candidate-evidence-tree-fingerprint-v1\0".encode("utf-8"))
    if not tree_root.exists():
        digest.update(b"@missing")
        return "sha256:" + digest.hexdigest()
    for path in sorted(tree_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name == "__pycache__" or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(tree_root).as_posix()
        content = path.read_bytes()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def _failure(
    profile: HostBoundCandidateEvidenceProfile,
    status: str,
    diagnostics: list[Mapping[str, Any]],
    compiled: Any,
    repo: Path,
) -> HostBoundCandidateEvidenceResult:
    return HostBoundCandidateEvidenceResult(
        False,
        status,
        {
            "schema": profile.report_schema,
            "genericSchema": REPORT_SCHEMA,
            "version": profile.report_version,
            "genericVersion": REPORT_VERSION,
            "appId": profile.app_id,
            "status": status,
            "valid": False,
            "candidate": {
                "semanticFingerprint": getattr(compiled, "semantic_fingerprint", None),
                "sourceBindingFingerprint": getattr(compiled, "source_binding_fingerprint", None),
            },
            "authority": {
                "candidatePromoted": profile.candidate_promoted,
                "promotionEligible": profile.promotion_eligible,
            },
        },
        tuple(diagnostics),
        None,
        repo,
    )


def _diagnostic(code: str, summary: str, semantic_path: str) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "semanticPath": semantic_path,
    }


def _resolve(repo: Path, path: Path | str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else repo / candidate


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _display_path(path: Path | None, repo: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _render_markdown(profile: HostBoundCandidateEvidenceProfile, report: Mapping[str, Any]) -> str:
    lines = [
        f"# {profile.report_title}",
        "",
        f"- App: `{report.get('appId')}`",
        f"- Status: `{report.get('status')}`",
        f"- Truth status: `{report.get('truthStatus')}`",
        f"- Semantic fingerprint: `{(report.get('candidate') or {}).get('semanticFingerprint')}`",
        f"- Promotion eligible: `{str((report.get('authority') or {}).get('promotionEligible')).lower()}`",
        f"- Fresh Chromium observation: `{str((report.get('authority') or {}).get('freshChromiumObservation')).lower()}`",
        "",
        "## Stages",
        "",
    ]
    for name, stage in sorted((report.get("stages") or {}).items()):
        lines.append(f"- `{name}`: `{stage.get('status')}`")
    lines.append("")
    return "\n".join(lines)
