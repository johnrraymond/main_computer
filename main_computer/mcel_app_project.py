"""Generic isolated projection authority for DSL-authored MCEL applications."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_app_authoring_profiles import get_app_authoring_profile
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT

REPORT_SCHEMA = "mcel.app-projection-report.v1"
REPORT_VERSION = "mcel-app-project-wave9"


class AppProjectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppProjectionResult:
    valid: bool
    status: str
    report: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.report)
        value["diagnosticCount"] = self.diagnostic_count
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        return value


def project_application(
    *,
    app_id: str,
    repo_root: Path,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    write_candidate: bool = False,
) -> AppProjectionResult:
    repo = repo_root.resolve()
    profile = get_app_authoring_profile(app_id)
    catalog = build_application_package_catalog(repo)
    records = [record for record in catalog.packages if record.app_id == app_id]
    if len(records) != 1:
        raise AppProjectionError(f"Application {app_id!r} was not discovered exactly once.")
    record = records[0]
    source_reference = str(record.authoring.get("source") or "").strip()
    if source_reference:
        source = repo / source_reference
    elif profile.candidate_source is not None:
        source = repo / profile.candidate_source if not profile.candidate_source.is_absolute() else profile.candidate_source
    else:
        source = repo / record.package_root / "application.js"
    package_root = repo / record.package_root
    if profile.fixture_ir is None:
        raise AppProjectionError(f"Application {app_id!r} has no compatibility IR binding.")
    raw = profile.project_candidate(
        dsl_source_path=source,
        fixture_ir_path=repo / profile.fixture_ir if not profile.fixture_ir.is_absolute() else profile.fixture_ir,
        live_package_root=package_root,
        candidate_root=repo / candidate_root if not candidate_root.is_absolute() else candidate_root,
        write_candidate=write_candidate,
    )
    payload = raw.to_dict()
    report = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": app_id,
        "status": raw.status,
        "valid": raw.valid,
        "genericPipeline": True,
        "counterSpecificExecutionPathRequired": False,
        "applicationProfile": profile.profile_id,
        "projectionProfile": profile.projection_profile,
        "candidateFrontend": profile.authoring_frontend,
        "portableIrProjectionComplete": profile.portable_ir_projection_complete,
        "semanticFingerprint": (payload.get("source") or {}).get("semanticFingerprint"),
        "projection": payload,
    }
    return AppProjectionResult(raw.valid, raw.status, report, tuple(raw.diagnostics))
