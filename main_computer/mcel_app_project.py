"""Generic isolated projection authority for DSL-authored MCEL applications."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_app_authoring_profiles import (
    AppAuthoringProfileError,
    get_app_authoring_profile,
)
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_host_bound_candidate_projection import (
    HostBoundProjectionProfile,
    project_host_bound_candidate,
)
from main_computer.mcel_projection_profiles.host_bound_policy import (
    policy_for_app,
    project_with_policy,
)

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
    catalog = build_application_package_catalog(repo)
    records = [record for record in catalog.packages if record.app_id == app_id]
    if len(records) != 1:
        raise AppProjectionError(f"Application {app_id!r} was not discovered exactly once.")
    record = records[0]
    package_root = (repo / record.package_root).resolve()
    try:
        manifest = json.loads((repo / record.manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppProjectionError(f"Could not read package manifest for {app_id!r}: {exc}") from exc
    authoring = manifest.get("authoring") or {}
    projection_meta = manifest.get("projection") or {}
    source_reference = str(authoring.get("source") or "").strip()

    if (authoring.get("status") == "dsl-authoritative"
            and projection_meta.get("mountMode") == "host-bound"):
        if not source_reference:
            raise AppProjectionError(f"Application {app_id!r} has no authoritative DSL source.")
        source = (package_root / source_reference).resolve()
        if not source.is_relative_to(package_root) or not source.is_file():
            raise AppProjectionError(f"Application {app_id!r} DSL source is missing or escapes its package.")
        policy = policy_for_app(app_id)
        promotion = manifest.get("promotion") or {}
        profile = HostBoundProjectionProfile(
            app_id=app_id,
            projection_profile=policy.profile_id,
            project_ir=lambda ir: project_with_policy(ir, expected_app_id=app_id),
            default_dsl_source=source,
            default_package_root=package_root,
            report_schema=policy.report_schema,
            version=policy.report_version,
            live_app_changed=bool(projection_meta.get("liveRuntimeChanged")),
            host_bound_runtime_active=bool(projection_meta.get("hostBoundRuntimeActive")),
            legacy_semantic_adapter_remains_live=not bool(projection_meta.get("legacySemanticAdapterRetired")),
            candidate_promoted=bool(promotion.get("promotionExecuted")),
            promotion_eligible=bool(promotion.get("promotionEligible")),
            legacy_live_app_changed_key=policy.legacy_live_app_changed_key,
        )
        raw = project_host_bound_candidate(
            profile,
            dsl_source_path=source,
            fixture_ir_path=None,
            live_package_root=package_root,
            candidate_root=repo / candidate_root if not candidate_root.is_absolute() else candidate_root,
            write_candidate=write_candidate,
        )
        authoring_profile = None
        try:
            authoring_profile = get_app_authoring_profile(app_id)
        except AppAuthoringProfileError:
            pass
        application_profile = authoring_profile.profile_id if authoring_profile else None
        projection_profile = policy.profile_id
        frontend = "mcel.dsl.v1"
        portable = raw.valid
    else:
        # Legacy/fixture projectors are outside the generic host-bound contract.
        profile = get_app_authoring_profile(app_id)
        if source_reference:
            source = (package_root / source_reference).resolve()
            if not source.is_relative_to(package_root):
                raise AppProjectionError("DSL source escapes its package root.")
        elif profile.candidate_source is not None:
            source = ((repo / profile.candidate_source).resolve() if not profile.candidate_source.is_absolute()
                      else profile.candidate_source.resolve())
        else:
            source = package_root / "application.js"
        fixture_ir_path = None
        if profile.fixture_ir is not None:
            fixture_ir_path = repo / profile.fixture_ir if not profile.fixture_ir.is_absolute() else profile.fixture_ir
        raw = profile.project_candidate(
            dsl_source_path=source,
            fixture_ir_path=fixture_ir_path,
            live_package_root=package_root,
            candidate_root=repo / candidate_root if not candidate_root.is_absolute() else candidate_root,
            write_candidate=write_candidate,
        )
        application_profile = profile.profile_id
        projection_profile = profile.projection_profile
        frontend = profile.authoring_frontend
        portable = profile.portable_ir_projection_complete
    payload = raw.to_dict()
    report = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": app_id,
        "status": raw.status,
        "valid": raw.valid,
        "genericPipeline": True,
        "counterSpecificExecutionPathRequired": False,
        "applicationProfile": application_profile,
        "projectionProfile": projection_profile,
        "candidateFrontend": frontend,
        "portableIrProjectionComplete": portable,
        "semanticFingerprint": (payload.get("source") or {}).get("semanticFingerprint"),
        "projection": payload,
    }
    return AppProjectionResult(raw.valid, raw.status, report, tuple(raw.diagnostics))
