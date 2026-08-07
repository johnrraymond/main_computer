"""Generic runtime parity and generated-adapter authority for host-bound MCEL apps.

A host-bound app keeps its durable HTML/CSS/runtime surface in the live
repository while MCEL owns the semantic declaration and generated contract
authority.  This module owns the shared static authority checks and optional
fresh-browser probe orchestration.  App-specific wrappers should provide only a
profile: app identity, source defaults, expected host surface, intent bindings,
local lanes, retired artifacts, projection hook, and browser observation hook.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_dsl_compiler import compile_dsl_application


REPORT_SCHEMA = "mcel.host-bound-generated-adapter-authority.v1"
REPORT_VERSION = "mcel-host-bound-generated-adapter-authority-v1"
BROWSER_PROBE_SCHEMA = "mcel.host-bound-browser-authority-probe.v1"


class HostBoundRuntimeParityError(RuntimeError):
    """Raised when host-bound runtime parity evidence cannot converge."""


@dataclass(frozen=True)
class HostBoundRetiredArtifact:
    check_key: str
    path: Path


@dataclass(frozen=True)
class HostBoundRuntimeParityProfile:
    app_id: str
    default_dsl_source: Path
    project_ir: Callable[[Mapping[str, Any]], Any]
    expected_intents: Mapping[str, str]
    local_lanes: frozenset[str]
    route: str
    root_selector: str
    runtime_facade: str
    report_schema: str = REPORT_SCHEMA
    report_version: str = REPORT_VERSION
    browser_probe_schema: str = BROWSER_PROBE_SCHEMA
    manifest_path: Path | None = None
    presentation_authority: str = "existing-host-html"
    projection_expected_files: tuple[str, ...] = (
        "contracts/acceptance.js",
        "contracts/adapter.js",
        "contracts/domain.js",
        "contracts/intents.js",
        "contracts/layout.js",
        "contracts/observation.js",
        "contracts/surface.js",
        "generated/mcel.application.normalized.json",
    )
    retired_artifacts: tuple[HostBoundRetiredArtifact, ...] = ()
    capability_accounting_schema: str = "mcel.host-bound-capability-accounting.v1"
    dsl_invalid_code: str = "MCEL_HOST_BOUND_AUTHORITY_DSL_INVALID"
    check_failed_code: str = "MCEL_HOST_BOUND_AUTHORITY_CHECK_FAILED"
    authority_live_changed_key: str = "liveAppChanged"
    legacy_semantic_adapter_live_key: str = "legacySemanticAdapterRemainsLive"
    legacy_semantic_adapter_retired_key: str = "legacySemanticAdapterRetired"
    live_changed: bool = True
    legacy_semantic_adapter_remains_live: bool = False
    legacy_semantic_adapter_retired: bool = True
    candidate_promoted: bool = True
    promotion_eligible: bool = True
    published_as_second_app: bool = False
    browser_observation_runner: Callable[..., Any] | None = None
    browser_probe_error: type[Exception] = HostBoundRuntimeParityError
    extra_authority: Mapping[str, Any] = field(default_factory=dict)

    @property
    def manifest(self) -> Path:
        return self.manifest_path or Path(f"mcel_apps/{self.app_id}/mcel.app.json")

    @property
    def capability_lanes(self) -> frozenset[str]:
        return frozenset(
            lane
            for lane in _declared_lanes_hint(self.expected_intents)
            if lane not in self.local_lanes
        )


@dataclass(frozen=True)
class HostBoundRuntimeParityResult:
    valid: bool
    status: str
    report: Mapping[str, Any]
    diagnostics: tuple[Mapping[str, Any], ...]

    @property
    def diagnostic_count(self) -> int:
        return len(self.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.report)
        payload["diagnosticCount"] = self.diagnostic_count
        payload["diagnostics"] = [dict(item) for item in self.diagnostics]
        return payload


def run_host_bound_generated_adapter_parity(
    profile: HostBoundRuntimeParityProfile,
    *,
    repo_root: Path,
    operation_prefix: str = "promoted",
) -> HostBoundRuntimeParityResult:
    """Prove a host-bound generated adapter is the live semantic authority."""

    repo = Path(repo_root).resolve()
    diagnostics: list[Mapping[str, Any]] = []
    dsl_source = _resolve(repo, profile.default_dsl_source)

    compiled = compile_dsl_application(dsl_source, write_candidate=False)
    diagnostics.extend(compiled.diagnostics)
    if not compiled.valid or compiled.normalized_ir is None:
        diagnostics.append(_diagnostic(profile.dsl_invalid_code, f"{profile.app_id} DSL did not compile.", "$source"))
        return _result(profile, False, "fail", diagnostics, {})

    projection = profile.project_ir(compiled.normalized_ir)
    generated = _generated_bindings(compiled.normalized_ir)
    catalog = build_application_package_catalog(repo)
    runtime = build_runtime_projection_set(repo)
    browser = build_repository_browser_catalog_payload(repo)

    package_records = [item for item in catalog.packages if item.app_id == profile.app_id]
    runtime_records = [item for item in runtime.projections if item.app_id == profile.app_id]
    browser_records = [item for item in browser.get("packages") or [] if item.get("appId") == profile.app_id]
    manifest = _manifest(repo, profile.manifest)

    local_provider_free = {
        name: (
            binding.get("lane") in profile.local_lanes
            and not binding.get("effectRefs")
            and binding.get("risk") == "read-only"
        )
        for name, binding in generated.items()
        if binding.get("lane") in profile.local_lanes
    }
    runtime_binding_checks = {
        name: binding.get("runtimeMethod") == profile.expected_intents.get(name)
        for name, binding in generated.items()
    }
    capability_accounting = _capability_accounting(profile, compiled.normalized_ir, generated)
    projected_files = [
        {
            "path": path,
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        }
        for path, content in sorted(projection.files.items())
    ]

    checks: dict[str, bool] = {
        "dslCompiled": compiled.valid and compiled.normalized_ir is not None,
        "manifestAuthoritative": (manifest.get("authoring") or {}).get("status") == "dsl-authoritative",
        "packageDiscoveredOnce": len(package_records) == 1 and bool(package_records and package_records[0].valid),
        "runtimeProjectionHostBound": (
            len(runtime_records) == 1
            and runtime_records[0].mount_mode == "host-bound"
            and runtime_records[0].host_route == profile.route
            and runtime_records[0].root_selector == profile.root_selector
            and runtime_records[0].runtime_facade == profile.runtime_facade
            and runtime_records[0].document_url is None
            and runtime_records[0].script_url is None
            and runtime_records[0].style_url is None
        ),
        "browserCatalogHostBound": (
            len(browser_records) == 1
            and (browser_records[0].get("runtimeProjection") or {}).get("mountMode") == "host-bound"
            and (browser_records[0].get("runtimeProjection") or {}).get("hostRoute") == profile.route
        ),
        "generatedIntentSetExact": set(generated) == set(profile.expected_intents),
        "generatedRuntimeBindingsExact": bool(runtime_binding_checks) and all(runtime_binding_checks.values()),
        "localIntentsProviderFree": bool(local_provider_free) and all(local_provider_free.values()),
        "capabilityAccountingClosed": capability_accounting.get("status") == "closed",
        "projectionFileSetExact": tuple(sorted(projection.files)) == tuple(sorted(profile.projection_expected_files)),
    }
    for retired in profile.retired_artifacts:
        checks[retired.check_key] = not _resolve(repo, retired.path).exists()

    for key, passed in checks.items():
        if not passed:
            diagnostics.append(_diagnostic(profile.check_failed_code, f"{profile.app_id} authority check failed: {key}.", f"$checks.{key}"))

    valid = not diagnostics and all(checks.values())
    report: dict[str, Any] = {
        "schema": profile.report_schema,
        "genericSchema": REPORT_SCHEMA,
        "version": profile.report_version,
        "genericVersion": REPORT_VERSION,
        "appId": profile.app_id,
        "status": "pass" if valid else "fail",
        "valid": valid,
        "operationPrefix": operation_prefix,
        "generatedAt": _utc_now(),
        "coverageMode": "host-bound-generated-adapter-authority",
        "semanticFingerprint": compiled.semantic_fingerprint,
        "sourceBindingFingerprint": compiled.source_binding_fingerprint,
        "projectionProfile": getattr(projection, "profile_id", ""),
        "host": {
            "route": profile.route,
            "rootSelector": profile.root_selector,
            "runtimeFacade": profile.runtime_facade,
            "presentationAuthority": profile.presentation_authority,
        },
        "checks": checks,
        "intentCount": len(generated),
        "generatedBindings": generated,
        "runtimeBindingChecks": runtime_binding_checks,
        "localProviderFree": local_provider_free,
        "capabilityAccounting": capability_accounting,
        "projection": {
            "fileCount": len(getattr(projection, "files", {})),
            "files": projected_files,
            "generatedArtifactsAreDerived": True,
            "publishedAsSecondCalculator": profile.published_as_second_app,
            "publishedAsSecondApp": profile.published_as_second_app,
        },
        "authority": _authority(profile),
    }
    return HostBoundRuntimeParityResult(valid, "pass" if valid else "fail", report, tuple(diagnostics))


def run_host_bound_browser_parity_probe(
    profile: HostBoundRuntimeParityProfile,
    *,
    repo: Path,
    headed: bool = False,
    operation_prefix: str = "promoted",
) -> Mapping[str, Any]:
    """Run static authority parity plus a fresh browser observation."""

    static = run_host_bound_generated_adapter_parity(
        profile,
        repo_root=repo,
        operation_prefix=operation_prefix,
    )
    if not static.valid:
        raise profile.browser_probe_error(
            "; ".join(str(item.get("summary")) for item in static.diagnostics)
            or f"{profile.app_id} authority evidence failed."
        )

    if profile.browser_observation_runner is None:
        raise profile.browser_probe_error(f"{profile.app_id} has no browser observation runner.")

    browser = profile.browser_observation_runner(
        repo_root=repo,
        headed=headed,
        operation_prefix=operation_prefix,
        require_browser=True,
    )
    if not browser.valid:
        raise profile.browser_probe_error(
            "; ".join(str(item.get("summary")) for item in browser.diagnostics)
            or f"{profile.app_id} browser observation failed."
        )

    report = dict(static.report)
    report["schema"] = profile.browser_probe_schema
    report["coverageMode"] = "fresh-browser-host-bound-generated-adapter-authority"
    report["browserObservation"] = dict(browser.report)
    report.setdefault("authority", {})["freshChromiumObservation"] = True
    report.setdefault("checks", {})["freshBrowserAuthority"] = True
    return report


def _authority(profile: HostBoundRuntimeParityProfile) -> dict[str, Any]:
    authority: dict[str, Any] = {
        "candidatePromoted": profile.candidate_promoted,
        "promotionEligible": profile.promotion_eligible,
        "freshChromiumObservation": False,
        **dict(profile.extra_authority),
    }
    authority[profile.authority_live_changed_key] = profile.live_changed
    authority[profile.legacy_semantic_adapter_live_key] = profile.legacy_semantic_adapter_remains_live
    authority[profile.legacy_semantic_adapter_retired_key] = profile.legacy_semantic_adapter_retired
    return authority


def _manifest(repo: Path, manifest_path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(_resolve(repo, manifest_path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, Mapping) else {}


def _generated_bindings(ir: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for intent in ir.get("intents") or []:
        if not isinstance(intent, Mapping):
            continue
        source = str(intent.get("sourceName") or "")
        if not source:
            continue
        out[source] = {
            "intentId": str(intent.get("id") or ""),
            "sourceName": source,
            "runtimeMethod": str(intent.get("runtimeMethod") or ""),
            "executionBinding": str(intent.get("executionBinding") or ""),
            "lane": str(intent.get("lane") or ""),
            "risk": str(intent.get("risk") or ""),
            "operationKind": str(intent.get("operationKind") or ""),
            "effectRefs": [str((ref or {}).get("ref") or "") for ref in intent.get("effectRefs") or [] if isinstance(ref, Mapping)],
        }
    return out


def _capability_accounting(
    profile: HostBoundRuntimeParityProfile,
    ir: Mapping[str, Any],
    generated: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    effects = [item for item in ir.get("effects") or [] if isinstance(item, Mapping)]
    capabilities = [item for item in ir.get("capabilities") or [] if isinstance(item, Mapping)]
    by_owner = {str(((effect.get("owner") or {}).get("ref") or "")): str(effect.get("id") or "") for effect in effects}
    capability_intents = {
        name: binding
        for name, binding in generated.items()
        if binding.get("lane") not in profile.local_lanes
    }
    instances = []
    closed = True
    for name, binding in sorted(capability_intents.items()):
        intent_id = str(binding.get("intentId") or "")
        owned = by_owner.get(intent_id)
        refs = list(binding.get("effectRefs") or [])
        exact = bool(owned) and refs == [owned]
        closed = closed and exact
        instances.append({
            "sourceName": name,
            "intentId": intent_id,
            "effectId": owned,
            "effectRefs": refs,
            "status": "closed" if exact else "open",
        })
    return {
        "schema": profile.capability_accounting_schema,
        "genericSchema": "mcel.host-bound-capability-accounting.v1",
        "status": "closed" if closed and len(capability_intents) == len(effects) else "open",
        "declaredCapabilityCount": len(capabilities),
        "declaredEffectCount": len(effects),
        "capabilityIntentCount": len(capability_intents),
        "closedIntentEffectCount": sum(1 for item in instances if item["status"] == "closed"),
        "instances": instances,
    }


def _result(
    profile: HostBoundRuntimeParityProfile,
    valid: bool,
    status: str,
    diagnostics: list[Mapping[str, Any]],
    extra: Mapping[str, Any],
) -> HostBoundRuntimeParityResult:
    report = {
        "schema": profile.report_schema,
        "genericSchema": REPORT_SCHEMA,
        "version": profile.report_version,
        "genericVersion": REPORT_VERSION,
        "appId": profile.app_id,
        "status": status,
        "valid": valid,
        **dict(extra),
    }
    return HostBoundRuntimeParityResult(valid, status, report, tuple(diagnostics))


def _diagnostic(code: str, summary: str, semantic_path: str) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "semanticPath": semantic_path,
    }


def _resolve(repo: Path, value: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else repo / path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _declared_lanes_hint(_: Mapping[str, str]) -> frozenset[str]:
    """Compatibility hook for callers that inspect the profile property only.

    Runtime capability accounting is derived from the compiled IR rather than
    this hint.  The method remains to keep the profile a stable generic object.
    """

    return frozenset()
