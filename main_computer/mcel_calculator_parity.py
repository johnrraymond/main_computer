"""Calculator generated-adapter parity evidence for the host-bound shadow DSL.

This authority does not promote Calculator. It proves that the generated DSL
adapter and the legacy semantic adapter name the same stable runtime facade
methods, that host-bound runtime projection is active, and that local Calculator
intents remain provider-free. When requested through the profile hook it also
delegates to the fresh browser parity observation runner.
"""

from __future__ import annotations

import hashlib
import json
import re
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
REPORT_SCHEMA = "mcel.calculator-generated-adapter-parity.v1"
LOCAL_LANES = frozenset({"local-ui", "local-arithmetic", "local-graph"})
CAPABILITY_LANES = frozenset({"model-arithmetic", "model-graph", "model-mathics", "mathics", "model-result-qa", "result-qa"})
DEFAULT_DSL_SOURCE = Path("mcel_apps/calculator/application.js")
LEGACY_ADAPTER = Path("main_computer/web/applications/scripts/calculator-semantic-adapter.js")


class CalculatorParityError(RuntimeError):
    """Raised when Calculator shadow parity evidence cannot converge."""


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
    operation_prefix: str = "candidate",
) -> CalculatorParityResult:
    """Compare generated Calculator adapter bindings with the live semantic facade."""

    repo = Path(repo_root).resolve()
    diagnostics: list[Mapping[str, Any]] = []
    dsl_source = repo / DEFAULT_DSL_SOURCE
    legacy_path = repo / LEGACY_ADAPTER

    compiled = compile_dsl_application(dsl_source, write_candidate=False)
    diagnostics.extend(compiled.diagnostics)
    if not compiled.valid or compiled.normalized_ir is None:
        diagnostics.append(_diagnostic("MCEL_CALCULATOR_PARITY_DSL_INVALID", "Calculator DSL did not compile.", "$source"))
        return _result(False, "fail", diagnostics, {})

    projection = project_calculator_ir(compiled.normalized_ir)
    generated = _generated_bindings(compiled.normalized_ir)
    legacy = _legacy_bindings(legacy_path)
    catalog = build_application_package_catalog(repo)
    runtime = build_runtime_projection_set(repo)
    browser = build_repository_browser_catalog_payload(repo)

    package_records = [item for item in catalog.packages if item.app_id == APP_ID]
    runtime_records = [item for item in runtime.projections if item.app_id == APP_ID]
    browser_records = [item for item in browser.get("packages") or [] if item.get("appId") == APP_ID]

    parity = _compare_bindings(generated, legacy)
    local_provider_free = {
        name: (
            binding.get("lane") in LOCAL_LANES
            and not binding.get("effectRefs")
            and binding.get("risk") == "read-only"
        )
        for name, binding in generated.items()
        if binding.get("lane") in LOCAL_LANES
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
        "legacyIntentSetExact": set(legacy) == set(EXPECTED_INTENTS),
        "generatedLegacyBindingsMatch": all(item.get("match") is True for item in parity.values()),
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
            diagnostics.append(_diagnostic("MCEL_CALCULATOR_PARITY_CHECK_FAILED", f"Calculator parity check failed: {key}.", f"$checks.{key}"))

    valid = not diagnostics and all(checks.values())
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "version": "mcel-calculator-generated-adapter-parity-v1",
        "appId": APP_ID,
        "status": "pass" if valid else "fail",
        "valid": valid,
        "operationPrefix": operation_prefix,
        "generatedAt": _utc_now(),
        "coverageMode": "host-bound-generated-adapter-vs-legacy-semantic-adapter",
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
        "legacyBindings": legacy,
        "bindingParity": parity,
        "localProviderFree": local_provider_free,
        "capabilityAccounting": capability_accounting,
        "projection": {
            "fileCount": len(projection.files),
            "files": projected_files,
            "generatedArtifactsAreDerived": True,
            "publishedAsSecondCalculator": False,
        },
        "authority": {
            "liveCalculatorChanged": False,
            "legacySemanticAdapterRemainsLive": True,
            "candidatePromoted": False,
            "promotionEligible": False,
            "freshChromiumObservation": False,
        },
    }
    return CalculatorParityResult(valid, "pass" if valid else "fail", report, tuple(diagnostics))


def run_calculator_browser_parity_probe(
    repo: Path,
    headed: bool = False,
    operation_prefix: str = "candidate",
) -> Mapping[str, Any]:
    """Profile hook compatible with the generic app authoring probe signature.

    This is now a fresh Chromium parity observation. It loads the real
    Calculator route, exercises the host-bound generated adapter and the legacy
    semantic adapter through the same runtime facade, and fails closed when the
    browser evidence cannot be produced.
    """

    from main_computer.mcel_calculator_browser_observation import run_calculator_browser_observation

    static = run_calculator_generated_adapter_parity(repo_root=repo, operation_prefix=operation_prefix)
    if not static.valid:
        raise CalculatorParityError("; ".join(str(item.get("summary")) for item in static.diagnostics) or "Calculator parity failed.")
    browser = run_calculator_browser_observation(
        repo_root=repo,
        headed=headed,
        operation_prefix=operation_prefix,
        require_browser=True,
    )
    if not browser.valid:
        raise CalculatorParityError("; ".join(str(item.get("summary")) for item in browser.diagnostics) or "Calculator browser parity failed.")
    report = dict(static.report)
    report["schema"] = "mcel.calculator-browser-parity-probe.v2"
    report["coverageMode"] = "fresh-browser-host-bound-generated-adapter-vs-legacy-semantic-adapter"
    report["browserObservation"] = dict(browser.report)
    report.setdefault("authority", {})["freshChromiumObservation"] = True
    report.setdefault("checks", {})["freshBrowserParity"] = True
    return report


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


def _legacy_bindings(path: Path) -> dict[str, dict[str, Any]]:
    source = path.read_text(encoding="utf-8")
    marker = "const INTENT_DEFINITIONS = Object.freeze(["
    start = source.find(marker)
    if start < 0:
        raise CalculatorParityError("Legacy Calculator semantic adapter no longer declares INTENT_DEFINITIONS.")
    body_start = source.find("[", start)
    body_end = source.find("]);", body_start)
    if body_start < 0 or body_end < 0:
        raise CalculatorParityError("Could not isolate Calculator INTENT_DEFINITIONS.")
    body = source[body_start:body_end]
    records: dict[str, dict[str, Any]] = {}
    for match in re.finditer(r"Object\.freeze\(\{(?P<body>.*?)\}\)", body, flags=re.S):
        raw = match.group("body")
        intent_id = _string_property(raw, "id")
        if not intent_id:
            continue
        records[intent_id] = {
            "sourceName": intent_id,
            "runtimeMethod": _string_property(raw, "runtimeMethod"),
            "executionBinding": _string_property(raw, "executionBinding"),
            "lane": _string_property(raw, "lane"),
            "risk": _string_property(raw, "risk"),
            "mutates": _bool_property(raw, "mutates"),
        }
    if set(records) != set(EXPECTED_INTENTS):
        raise CalculatorParityError(
            "Legacy Calculator semantic adapter intent set drifted: "
            f"missing={sorted(set(EXPECTED_INTENTS) - set(records))}, extra={sorted(set(records) - set(EXPECTED_INTENTS))}"
        )
    return records


def _compare_bindings(generated: Mapping[str, Mapping[str, Any]], legacy: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(set(generated) | set(legacy)):
        gen = generated.get(name) or {}
        old = legacy.get(name) or {}
        risk_match = gen.get("risk") == old.get("risk")
        if gen.get("lane") in CAPABILITY_LANES:
            # The shadow DSL intentionally upgrades provider-backed lanes to
            # explicit external-read capability risk while the legacy adapter
            # still labels them non-mutating/read-only. Parity here is the
            # stable runtime binding; risk sharpening is recorded, not a drift.
            risk_match = gen.get("risk") == "external-read" and old.get("mutates") is False
        checks = {
            "runtimeMethod": gen.get("runtimeMethod") == old.get("runtimeMethod") == EXPECTED_INTENTS.get(name),
            "executionBinding": gen.get("executionBinding") == old.get("executionBinding"),
            "lane": gen.get("lane") == old.get("lane"),
            "riskCompatible": risk_match,
            "legacyNonMutating": old.get("mutates") is False,
        }
        out[name] = {
            "match": all(checks.values()),
            "checks": checks,
            "generated": {key: gen.get(key) for key in ("intentId", "runtimeMethod", "executionBinding", "lane", "risk")},
            "legacy": {key: old.get(key) for key in ("runtimeMethod", "executionBinding", "lane", "risk", "mutates")},
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


def _string_property(raw: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*:\s*[\"']([^\"']+)[\"']", raw)
    return match.group(1) if match else ""


def _bool_property(raw: str, name: str) -> bool | None:
    match = re.search(rf"\b{re.escape(name)}\s*:\s*(true|false)\b", raw)
    if not match:
        return None
    return match.group(1) == "true"


def _result(valid: bool, status: str, diagnostics: list[Mapping[str, Any]], extra: Mapping[str, Any]) -> CalculatorParityResult:
    report = {
        "schema": REPORT_SCHEMA,
        "version": "mcel-calculator-generated-adapter-parity-v1",
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
