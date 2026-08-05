"""Generic second-application portability authority for MCEL authoring profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_app_authoring_profiles import get_app_authoring_profile
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT

REPORT_SCHEMA = "mcel.app-portability-report.v1"
REPORT_VERSION = "mcel-app-portability-wave11"


class AppPortabilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppPortabilityResult:
    valid: bool
    status: str
    report: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]
    output_directory: Path | None = None

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.report)
        value["diagnosticCount"] = self.diagnostic_count
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        return value


def prove_application_portability(
    *,
    app_id: str,
    repo_root: Path,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    headed: bool = False,
    write_report: bool = False,
    **kwargs: Any,
) -> AppPortabilityResult:
    repo = repo_root.resolve()
    profile = get_app_authoring_profile(app_id)
    if profile.run_candidate_evidence is None:
        raise AppPortabilityError(f"Application {app_id!r} does not provide isolated candidate evidence mechanics.")
    records = [record for record in build_application_package_catalog(repo).packages if record.app_id == app_id]
    if len(records) != 1:
        raise AppPortabilityError(f"Application {app_id!r} was not discovered exactly once.")
    raw = profile.run_candidate_evidence(
        repo_root=repo,
        dsl_source_path=profile.candidate_source,
        fixture_ir_path=profile.fixture_ir,
        candidate_root=(repo / candidate_root if not candidate_root.is_absolute() else candidate_root),
        headed=headed,
        write_report=write_report,
        **kwargs,
    )
    payload = raw.to_dict()
    candidate = payload.get("candidate") or {}
    authority = payload.get("authority") or {}
    migration_debt = payload.get("migrationDebt") or {}
    valid = bool(raw.valid)
    report = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": app_id,
        "status": "pass" if valid else "fail",
        "valid": valid,
        "genericPipeline": True,
        "counterSpecificExecutionPathRequired": False,
        "applicationProfile": profile.profile_id,
        "authoringFrontend": profile.authoring_frontend,
        "projectionProfile": profile.projection_profile,
        "semanticCompatibility": "exact" if valid else "conflicting",
        "candidateTruthStatus": payload.get("truthStatus"),
        "semanticFingerprint": candidate.get("semanticFingerprint"),
        "sourceBindingFingerprint": candidate.get("sourceBindingFingerprint"),
        "liveAuthority": authority.get("liveAuthority") or "legacy-explicit-package",
        "promotionExecuted": bool(authority.get("promotionExecuted")),
        "candidatePromoted": bool(authority.get("candidatePromoted")),
        "promotionEligible": False,
        "evidenceReused": bool(authority.get("evidenceReused")),
        "portableIrProjectionComplete": profile.portable_ir_projection_complete,
        "migrationDebt": migration_debt,
        "candidateEvidence": payload,
    }
    return AppPortabilityResult(valid, report["status"], report, tuple(raw.diagnostics), raw.output_directory)
