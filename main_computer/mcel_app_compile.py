"""Generic compilation authority for DSL-authored MCEL applications."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_app_authoring_profiles import get_app_authoring_profile
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT, compile_dsl_application

REPORT_SCHEMA = "mcel.app-compile-report.v1"
REPORT_VERSION = "mcel-app-compile-wave9"


class AppCompileError(RuntimeError):
    """Raised when a package cannot enter the generic DSL compiler."""


@dataclass(frozen=True)
class AppCompileResult:
    valid: bool
    status: str
    report: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self, *, include_ir: bool = False) -> dict[str, Any]:
        value = dict(self.report)
        value["diagnosticCount"] = self.diagnostic_count
        value["diagnostics"] = [dict(item) for item in self.diagnostics]
        if not include_ir:
            value.pop("normalizedIr", None)
        return value


def compile_application(
    *,
    app_id: str,
    repo_root: Path,
    compare_ir_path: Path | None = None,
    write_candidate: bool = False,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    timeout_ms: int = 1000,
) -> AppCompileResult:
    repo = repo_root.resolve()
    catalog = build_application_package_catalog(repo)
    records = [record for record in catalog.packages if record.app_id == app_id]
    if len(records) != 1:
        raise AppCompileError(f"Application {app_id!r} was not discovered exactly once.")
    record = records[0]
    profile = get_app_authoring_profile(app_id)
    manifest_path = repo / record.manifest
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppCompileError(f"Could not load application manifest: {exc}") from exc
    authoring = manifest.get("authoring") or {}
    source_reference = str(authoring.get("source") or "").strip()
    promoted = authoring.get("status") == "dsl-authoritative"
    if source_reference:
        source = (repo / record.package_root / source_reference).resolve()
    elif profile.candidate_source is not None:
        source = (repo / profile.candidate_source).resolve() if not profile.candidate_source.is_absolute() else profile.candidate_source.resolve()
    else:
        raise AppCompileError(f"Application {app_id!r} does not declare an authoritative or candidate DSL source.")
    if not source.is_file():
        raise AppCompileError(f"Application DSL source is missing: {_display(source, repo)}")
    effective_compare = compare_ir_path
    if effective_compare is None and profile.fixture_ir is not None:
        effective_compare = (repo / profile.fixture_ir).resolve() if not profile.fixture_ir.is_absolute() else profile.fixture_ir.resolve()
    compiled = compile_dsl_application(
        source,
        compare_ir_path=effective_compare,
        write_candidate=write_candidate,
        candidate_root=(repo / candidate_root if not candidate_root.is_absolute() else candidate_root),
        timeout_ms=timeout_ms,
    )
    report = {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": app_id,
        "status": compiled.status,
        "valid": compiled.valid,
        "genericPipeline": True,
        "counterSpecificExecutionPathRequired": False,
        "sourceAuthority": "mcel.dsl.v1" if promoted else "legacy-explicit-package",
        "candidateFrontend": profile.authoring_frontend,
        "applicationProfile": profile.profile_id,
        "promotionExecuted": promoted,
        "source": _display(source, repo),
        "semanticFingerprint": compiled.semantic_fingerprint,
        "sourceBindingFingerprint": compiled.source_binding_fingerprint,
        "comparison": compiled.comparison_status,
        "candidate": compiled.to_dict().get("candidate"),
        "normalizedIr": compiled.normalized_ir,
    }
    return AppCompileResult(
        valid=compiled.valid,
        status=compiled.status,
        report=report,
        diagnostics=tuple(compiled.diagnostics),
    )


def _display(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())
