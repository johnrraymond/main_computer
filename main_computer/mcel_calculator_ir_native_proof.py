"""IR-native proof for the host-bound Calculator DSL authority.

This proof establishes that the authoritative DSL, generated adapter,
host-bound projection, stable runtime facade, and explicit capability lanes
converge. Fresh Chromium generated-adapter observation is required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_calculator_parity import (
    APP_ID,
    CalculatorParityError,
    run_calculator_generated_adapter_parity,
)


REPORT_SCHEMA = "mcel.calculator-ir-native-authoritative-proof.v1"
REPORT_VERSION = "mcel-calculator-ir-native-authoritative-proof-v1"


class CalculatorIrNativeProofError(RuntimeError):
    """Raised when Calculator IR proof cannot converge."""


def run_calculator_ir_native_intent_proof(
    *,
    repo: Path,
    record: Any,
    acceptance: Mapping[str, Any] | None = None,
    observation: Mapping[str, Any] | None = None,
    headed: bool = False,
    node_probe_runner: Any = None,
    browser_probe_runner: Any = None,
) -> dict[str, Any]:
    del headed, node_probe_runner
    if getattr(record, "app_id", APP_ID) != APP_ID:
        raise CalculatorIrNativeProofError("Calculator proof received a non-Calculator package record.")

    try:
        parity = dict(
            (browser_probe_runner or _run_default_parity_probe)(
                repo,
                False,
                "ir-native",
            )
        )
    except Exception as exc:
        raise CalculatorIrNativeProofError(str(exc)) from exc

    if parity.get("valid") is not True or parity.get("status") != "pass":
        raise CalculatorIrNativeProofError("Calculator generated-adapter authority evidence did not pass.")

    generated = parity.get("generatedBindings") or {}
    runtime_binding_checks = parity.get("runtimeBindingChecks") or {}
    local_provider_free = parity.get("localProviderFree") or {}
    capability_accounting = parity.get("capabilityAccounting") or {}
    checks = {
        "dslAuthorityCompiled": bool(parity.get("semanticFingerprint")),
        "hostBoundProjectionActive": (parity.get("checks") or {}).get("runtimeProjectionHostBound") is True,
        "browserCatalogHostBound": (parity.get("checks") or {}).get("browserCatalogHostBound") is True,
        "generatedRuntimeBindingsExact": bool(runtime_binding_checks) and all(runtime_binding_checks.values()),
        "allLocalIntentsProviderFree": bool(local_provider_free) and all(local_provider_free.values()),
        "capabilityAccountingClosed": capability_accounting.get("status") == "closed",
        "legacyAdapterRetired": (parity.get("authority") or {}).get("legacySemanticAdapterRetired") is True,
        "promoted": (parity.get("authority") or {}).get("promotionEligible") is True,
        "freshBrowserParity": (parity.get("authority") or {}).get("freshChromiumObservation") is True,
    }

    acceptance_status = _acceptance_status(acceptance or {})
    observation_status = _observation_status(observation or {})
    scenario_count = _scenario_count(parity)
    intent_results = {
        str(name): {
            "passed": bool(runtime_binding_checks.get(name)),
            "runtimeMethod": (generated.get(name) or {}).get("runtimeMethod"),
            "lane": (generated.get(name) or {}).get("lane"),
            "risk": (generated.get(name) or {}).get("risk"),
            "effectRefs": list((generated.get(name) or {}).get("effectRefs") or []),
            "checks": {"runtimeBinding": bool(runtime_binding_checks.get(name))},
        }
        for name in sorted(generated)
    }
    failed_intents = sorted(name for name, entry in intent_results.items() if entry.get("passed") is not True)
    passed = not failed_intents and all(checks.values())

    if not passed:
        failed = failed_intents + [key for key, value in checks.items() if value is not True]
        raise CalculatorIrNativeProofError(
            "Calculator IR proof did not converge"
            + (": " + ", ".join(failed[:12]) if failed else ".")
        )

    return {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": APP_ID,
        "status": "ir-native-authoritative",
        "passed": True,
        "applicable": True,
        "coverageMode": "fresh-browser-dsl-authoritative-host-bound-generated-adapter",
        "semanticFingerprint": parity.get("semanticFingerprint"),
        "sourceBindingFingerprint": parity.get("sourceBindingFingerprint"),
        "declaredIntentCount": len(intent_results),
        "coveredIntentCount": len(intent_results),
        "declaredScenarioCount": scenario_count,
        "observedScenarioCount": scenario_count,
        "failedIntentIds": [],
        "failedScenarioIds": [],
        "missingScenarioIds": [],
        "unexpectedScenarioIds": [],
        "crossCuttingChecks": checks,
        "acceptanceBinding": acceptance_status,
        "observationBinding": observation_status,
        "intents": intent_results,
        "scenarios": {
            f"calculator.authoritative.{name}": {
                "passed": True,
                "intentId": (generated.get(name) or {}).get("intentId"),
                "evidence": "generated-adapter runtime authority",
            }
            for name in sorted(generated)
        },
        "effectAccounting": capability_accounting,
        "parityEvidence": {
            "schema": parity.get("schema"),
            "status": parity.get("status"),
            "projectionProfile": parity.get("projectionProfile"),
            "freshChromiumObservation": (parity.get("authority") or {}).get("freshChromiumObservation"),
            "browserObservation": {
                "schema": (parity.get("browserObservation") or {}).get("schema"),
                "status": (parity.get("browserObservation") or {}).get("status"),
                "observedIntentCount": (parity.get("browserObservation") or {}).get("observedIntentCount"),
                "checks": (parity.get("browserObservation") or {}).get("checks"),
            },
        },
        "legacyEvidenceRequired": False,
        "promotionEligible": True,
    }


def _run_default_parity_probe(repo: Path, headed: bool, operation_prefix: str) -> Mapping[str, Any]:
    from main_computer.mcel_calculator_parity import run_calculator_browser_parity_probe

    return run_calculator_browser_parity_probe(repo, headed, operation_prefix)


def _scenario_count(parity: Mapping[str, Any]) -> int:
    return int(parity.get("intentCount") or len(parity.get("generatedBindings") or {}))


def _acceptance_status(report: Mapping[str, Any]) -> dict[str, Any]:
    if not report:
        return {"provided": False, "status": "not-required-for-shadow-proof"}
    results = [entry for entry in report.get("results") or [] if entry.get("appId") == APP_ID]
    return {
        "provided": True,
        "status": report.get("status"),
        "passed": report.get("passed"),
        "appScoped": (report.get("evidenceScope") or {}).get("kind") == "app-scoped",
        "matchingResultCount": len(results),
    }


def _observation_status(report: Mapping[str, Any]) -> dict[str, Any]:
    if not report:
        return {"provided": False, "status": "not-required-for-shadow-proof"}
    return {
        "provided": True,
        "status": report.get("status"),
        "ok": report.get("ok"),
        "evidenceScope": report.get("evidenceScope"),
        "appId": report.get("appId"),
    }
