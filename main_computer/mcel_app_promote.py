"""Generic promotion and rollback dispatch for MCEL applications."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_app_authoring_profiles import get_app_authoring_profile
from main_computer.mcel_application_packages import build_application_package_catalog

REPORT_SCHEMA = "mcel.app-promotion-dispatch.v1"
REPORT_VERSION = "mcel-app-promote-wave9"


class AppPromotionError(RuntimeError):
    pass


@dataclass(frozen=True)
class AppPromotionResult:
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


def inspect_application_authority(*, app_id: str, repo_root: Path) -> AppPromotionResult:
    repo = repo_root.resolve()
    profile = get_app_authoring_profile(app_id)
    catalog = build_application_package_catalog(repo)
    records = [record for record in catalog.packages if record.app_id == app_id]
    if len(records) != 1:
        raise AppPromotionError(f"Application {app_id!r} was not discovered exactly once.")
    record = records[0]
    try:
        manifest = json.loads((repo / record.manifest).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppPromotionError(f"Could not load application manifest: {exc}") from exc
    authoring = manifest.get("authoring") or {}
    promoted = authoring.get("status") == "dsl-authoritative"
    report = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": app_id,
        "status": "promoted" if promoted else "legacy-authority",
        "valid": True,
        "genericPipeline": True,
        "counterSpecificExecutionPathRequired": False,
        "applicationProfile": profile.profile_id,
        "sourceAuthority": "mcel.dsl.v1" if promoted else "legacy-explicit-package",
        "derivedArtifactAuthority": profile.projection_profile if promoted else None,
        "promotionExecuted": promoted,
        "rollbackAvailable": promoted,
        "promotionSupported": profile.promotion_supported,
        "promotionRehearsalSupported": profile.promotion_rehearsal_supported,
        "portableIrProjectionComplete": profile.portable_ir_projection_complete,
    }
    return AppPromotionResult(True, report["status"], report, ())



def rehearse_application_promotion(*, app_id: str, repo_root: Path, headed: bool = False, **kwargs: Any) -> AppPromotionResult:
    profile = get_app_authoring_profile(app_id)
    if not profile.promotion_rehearsal_supported:
        raise AppPromotionError(f"Application {app_id!r} does not support promotion rehearsal.")
    raw = profile.rehearse_promotion(repo_root=repo_root, headed=headed, **kwargs)
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
        "operation": "rehearse",
        "result": payload,
    }
    return AppPromotionResult(raw.valid, raw.status, report, tuple(raw.diagnostics), raw.output_directory)

def execute_application_promotion(*, app_id: str, repo_root: Path, headed: bool = False, **kwargs: Any) -> AppPromotionResult:
    profile = get_app_authoring_profile(app_id)
    raw = profile.execute_promotion(repo_root=repo_root, headed=headed, **kwargs)
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
        "operation": "execute",
        "result": payload,
    }
    return AppPromotionResult(raw.valid, raw.status, report, tuple(raw.diagnostics), raw.output_directory)


def rollback_application_promotion(transaction: str, *, app_id: str, repo_root: Path, **kwargs: Any) -> AppPromotionResult:
    profile = get_app_authoring_profile(app_id)
    raw = profile.rollback_promotion(transaction, repo_root=repo_root, **kwargs)
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
        "operation": "rollback",
        "result": payload,
    }
    return AppPromotionResult(raw.valid, raw.status, report, tuple(raw.diagnostics), raw.output_directory)
