"""Calculator generated-adapter evidence for the host-bound DSL authority.

This authority is post-promotion: the generated DSL adapter is the semantic
adapter.  The evidence proves that the generated bindings cover the eleven
stable Calculator runtime methods, that host-bound runtime projection is active,
and that local Calculator intents remain provider-free.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_application_package_browser_catalog import build_repository_browser_catalog_payload
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_projection_profiles.calculator_shadow_v1 import EXPECTED_INTENTS, project_calculator_ir


APP_ID = "calculator"
REPORT_SCHEMA = "mcel.calculator-generated-adapter-authority.v1"
LOCAL_LANES = frozenset({"local-ui", "local-arithmetic", "local-graph"})
CAPABILITY_LANES = frozenset({"model-arithmetic", "model-graph", "model-mathics", "mathics", "model-result-qa", "result-qa"})
DEFAULT_DSL_SOURCE = Path("mcel_apps/calculator/application.js")


class CalculatorParityError(RuntimeError):
    """Raised when Calculator generated-adapter evidence cannot converge."""


@dataclass(frozen=True)
class CalculatorParityResult:
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


def run_calculator_generated_adapter_parity(
    *,
    repo_root: Path,
    operation_prefix: str = "promoted",
) -> CalculatorParityResult:
    """Prove the generated Calculator adapter is the live semantic authority."""

    repo = Path(repo_root).resolve()
    diagnostics: list[Mapping[str, Any]] = []
    dsl_source = repo / DEFAULT_DSL_SOURCE

    compiled = compile_dsl_application(dsl_source, write_candidate=False)
    diagnostics.extend(compiled.diagnostics)
    if not compiled.valid or compiled.normalized_ir is None:
        diagnostics.append(_diagnostic("MCEL_CALCULATOR_AUTHORITY_DSL_INVALID", "Calculator DSL did not compile.", "$source"))
        return _result(False, "fail", diagnostics, {})

    projection = project_calculator_ir(compiled.normalized_ir)
    generated = _generated_bindings(compiled.normalized_ir)
    catalog = build_application_package_catalog(repo)
    runtime = build_runtime_projection_set(repo)
    browser = build_repository_browser_catalog_payload(repo)

    package_records = [item for item in catalog.packages if item.app_id == APP_ID]
    runtime_records = [item for item in runtime.projections if item.app_id == APP_ID]
    browser_records = [item for item in browser.get("packages") or [] if item.get("appId") == APP_ID]
    manifest = _manifest(repo)

    local_provider_free = {
        name: (
            binding.get("lane") in LOCAL_LANES
            and not binding.get("effectRefs")
            and binding.get("risk") == "read-only"
        )
        for name, binding in generated.items()
        if binding.get("lane") in LOCAL_LANES
    }
    runtime_binding_checks = {
        name: binding.get("runtimeMethod") == EXPECTED_INTENTS.get(name)
        for name, binding in generated.items()
    }
    capability_accounting = _capability_accounting(compiled.normalized_ir, generated)
    projected_files = [
        {
            "path": path,
            "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
        }
        for path, content in sorted(projection.files.items())
    ]

    checks = {
        "dslCompiled": compiled.valid and compiled.normalized_ir is not None,
        "manifestAuthoritative": (manifest.get("authoring") or {}).get("status") == "dsl-authoritative",
        "legacyAdapterRetired": not (repo / "main_computer/web/applications/scripts/calculator-semantic-adapter.js").exists(),
        "legacySurfaceRetired": not (repo / "main_computer/web/applications/scripts/mcel-calculator-surface.js").exists(),
        "packageDiscoveredOnce": len(package_records) == 1 and bool(package_records and package_records[0].valid),
        "runtimeProjectionHostBound": (
            len(runtime_records) == 1
            and runtime_records[0].mount_mode == "host-bound"
            and runtime_records[0].host_route == "/applications/calculator"
            and runtime_records[0].root_selector == "#calculator-app"
            and runtime_records[0].runtime_facade == "MainComputerCalculatorRuntime"
            and runtime_records[0].document_url is None
            and runtime_records[0].script_url is None
            and runtime_records[0].style_url is None
        ),
        "browserCatalogHostBound": (
            len(browser_records) == 1
            and (browser_records[0].get("runtimeProjection") or {}).get("mountMode") == "host-bound"
            and (browser_records[0].get("runtimeProjection") or {}).get("hostRoute") == "/applications/calculator"
        ),
        "generatedIntentSetExact": set(generated) == set(EXPECTED_INTENTS),
        "generatedRuntimeBindingsExact": bool(runtime_binding_checks) and all(runtime_binding_checks.values()),
        "localIntentsProviderFree": bool(local_provider_free) and all(local_provider_free.values()),
        "capabilityAccountingClosed": capability_accounting.get("status") == "closed",
        "projectionFileSetExact": sorted(projection.files) == [
            "contracts/acceptance.js",
            "contracts/adapter.js",
            "contracts/domain.js",
            "contracts/intents.js",
            "contracts/layout.js",
            "contracts/observation.js",
            "contracts/surface.js",
            "generated/mcel.application.normalized.json",
        ],
    }
    for key, passed in checks.items():
        if not passed:
            diagnostics.append(_diagnostic("MCEL_CALCULATOR_AUTHORITY_CHECK_FAILED", f"Calculator authority check failed: {key}.", f"$checks.{key}"))

    valid = not diagnostics and all(checks.values())
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "version": "mcel-calculator-generated-adapter-authority-v1",
        "appId": APP_ID,
        "status": "pass" if valid else "fail",
        "valid": valid,
        "operationPrefix": operation_prefix,
        "generatedAt": _utc_now(),
        "coverageMode": "host-bound-generated-adapter-authority",
        "semanticFingerprint": compiled.semantic_fingerprint,
        "sourceBindingFingerprint": compiled.source_binding_fingerprint,
        "projectionProfile": projection.profile_id,
        "host": {
            "route": "/applications/calculator",
            "rootSelector": "#calculator-app",
            "runtimeFacade": "MainComputerCalculatorRuntime",
            "presentationAuthority": "existing-host-html",
        },
        "checks": checks,
        "intentCount": len(generated),
        "generatedBindings": generated,
        "runtimeBindingChecks": runtime_binding_checks,
        "localProviderFree": local_provider_free,
        "capabilityAccounting": capability_accounting,
        "projection": {
            "fileCount": len(projection.files),
            "files": projected_files,
            "generatedArtifactsAreDerived": True,
            "publishedAsSecondCalculator": False,
        },
        "authority": {
            "liveCalculatorChanged": True,
            "legacySemanticAdapterRemainsLive": False,
            "legacySemanticAdapterRetired": True,
            "candidatePromoted": True,
            "promotionEligible": True,
            "freshChromiumObservation": False,
        },
    }
    return CalculatorParityResult(valid, "pass" if valid else "fail", report, tuple(diagnostics))


def run_calculator_browser_parity_probe(
    repo: Path,
    headed: bool = False,
    operation_prefix: str = "promoted",
) -> Mapping[str, Any]:
    """Profile hook compatible with the generic app authoring probe signature."""

    from main_computer.mcel_calculator_browser_observation import run_calculator_browser_observation

    static = run_calculator_generated_adapter_parity(repo_root=repo, operation_prefix=operation_prefix)
    if not static.valid:
        raise CalculatorParityError("; ".join(str(item.get("summary")) for item in static.diagnostics) or "Calculator authority evidence failed.")
    browser = run_calculator_browser_observation(
        repo_root=repo,
        headed=headed,
        operation_prefix=operation_prefix,
        require_browser=True,
    )
    if not browser.valid:
        raise CalculatorParityError("; ".join(str(item.get("summary")) for item in browser.diagnostics) or "Calculator browser observation failed.")
    report = dict(static.report)
    report["schema"] = "mcel.calculator-browser-authority-probe.v1"
    report["coverageMode"] = "fresh-browser-host-bound-generated-adapter-authority"
    report["browserObservation"] = dict(browser.report)
    report.setdefault("authority", {})["freshChromiumObservation"] = True
    report.setdefault("checks", {})["freshBrowserAuthority"] = True
    return report


def _manifest(repo: Path) -> Mapping[str, Any]:
    try:
        value = __import__("json").loads((repo / "mcel_apps/calculator/mcel.app.json").read_text(encoding="utf-8"))
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


def _capability_accounting(ir: Mapping[str, Any], generated: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    effects = [item for item in ir.get("effects") or [] if isinstance(item, Mapping)]
    capabilities = [item for item in ir.get("capabilities") or [] if isinstance(item, Mapping)]
    by_owner = {str(((effect.get("owner") or {}).get("ref") or "")): str(effect.get("id") or "") for effect in effects}
    capability_intents = {
        name: binding
        for name, binding in generated.items()
        if binding.get("lane") in CAPABILITY_LANES
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
        "schema": "mcel.calculator-capability-accounting.v1",
        "status": "closed" if closed and len(capabilities) == 3 and len(effects) == 5 and len(capability_intents) == 5 else "open",
        "declaredCapabilityCount": len(capabilities),
        "declaredEffectCount": len(effects),
        "capabilityIntentCount": len(capability_intents),
        "closedIntentEffectCount": sum(1 for item in instances if item["status"] == "closed"),
        "instances": instances,
    }


def _result(valid: bool, status: str, diagnostics: list[Mapping[str, Any]], extra: Mapping[str, Any]) -> CalculatorParityResult:
    report = {
        "schema": REPORT_SCHEMA,
        "version": "mcel-calculator-generated-adapter-authority-v1",
        "appId": APP_ID,
        "status": status,
        "valid": valid,
        **dict(extra),
    }
    return CalculatorParityResult(valid, status, report, tuple(diagnostics))


def _diagnostic(code: str, summary: str, semantic_path: str) -> dict[str, Any]:
    return {
        "schema": "mcel.compiler-diagnostic.v1",
        "code": code,
        "severity": "error",
        "blocking": True,
        "summary": summary,
        "semanticPath": semantic_path,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
