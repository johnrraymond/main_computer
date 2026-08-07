"""Generic IR-native proof aggregation for host-bound MCEL applications.

A host-bound app proves IR-native authority by binding its authoritative DSL,
generated runtime bindings, host-bound projection, browser catalog entry,
closed capability accounting, retired legacy artifacts, and fresh browser
parity into one intent/scenario report.  App-specific wrappers should supply
only a profile: app identity, parity probe hook, report labels, and compatibility
status text.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


REPORT_SCHEMA = "mcel.host-bound-ir-native-authoritative-proof.v1"
REPORT_VERSION = "mcel-host-bound-ir-native-authoritative-proof-v1"


class HostBoundIrNativeProofError(RuntimeError):
    """Raised when generic host-bound IR-native proof cannot converge."""


@dataclass(frozen=True)
class HostBoundIrNativeProofProfile:
    app_id: str
    run_browser_parity_probe: Callable[[Path, bool, str], Mapping[str, Any]]
    report_schema: str = REPORT_SCHEMA
    report_version: str = REPORT_VERSION
    status: str = "ir-native-authoritative"
    coverage_mode: str = "fresh-browser-dsl-authoritative-host-bound-generated-adapter"
    scenario_prefix: str | None = None
    parity_failure_message: str | None = None
    convergence_failure_message: str | None = None
    acceptance_not_required_status: str = "not-required-for-shadow-proof"
    observation_not_required_status: str = "not-required-for-shadow-proof"
    promotion_eligible: bool = True

    @property
    def authoritative_scenario_prefix(self) -> str:
        return self.scenario_prefix or f"{self.app_id}.authoritative"


def run_host_bound_ir_native_intent_proof(
    profile: HostBoundIrNativeProofProfile,
    *,
    repo: Path,
    record: Any,
    acceptance: Mapping[str, Any] | None = None,
    observation: Mapping[str, Any] | None = None,
    headed: bool = False,
    node_probe_runner: Any = None,
    browser_probe_runner: Callable[[Path, bool, str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate host-bound parity evidence into an IR-native proof report."""

    del node_probe_runner
    if getattr(record, "app_id", profile.app_id) != profile.app_id:
        raise HostBoundIrNativeProofError(
            f"{profile.app_id} proof received a package record for a different application."
        )

    try:
        parity = dict(
            (browser_probe_runner or profile.run_browser_parity_probe)(
                Path(repo),
                headed,
                "ir-native",
            )
        )
    except Exception as exc:
        raise HostBoundIrNativeProofError(str(exc)) from exc

    if parity.get("valid") is not True or parity.get("status") != "pass":
        raise HostBoundIrNativeProofError(
            profile.parity_failure_message
            or f"{profile.app_id} generated-adapter authority evidence did not pass."
        )

    generated = parity.get("generatedBindings") or {}
    runtime_binding_checks = parity.get("runtimeBindingChecks") or {}
    local_provider_free = parity.get("localProviderFree") or {}
    capability_accounting = parity.get("capabilityAccounting") or {}
    authority = parity.get("authority") or {}
    checks = {
        "dslAuthorityCompiled": bool(parity.get("semanticFingerprint")),
        "hostBoundProjectionActive": (parity.get("checks") or {}).get("runtimeProjectionHostBound") is True,
        "browserCatalogHostBound": (parity.get("checks") or {}).get("browserCatalogHostBound") is True,
        "generatedRuntimeBindingsExact": bool(runtime_binding_checks) and all(runtime_binding_checks.values()),
        "allLocalIntentsProviderFree": bool(local_provider_free) and all(local_provider_free.values()),
        "capabilityAccountingClosed": capability_accounting.get("status") == "closed",
        "legacyAdapterRetired": authority.get("legacySemanticAdapterRetired") is True,
        "promoted": authority.get("promotionEligible") is True,
        "freshBrowserParity": authority.get("freshChromiumObservation") is True,
    }

    acceptance_status = _acceptance_status(profile, acceptance or {})
    observation_status = _observation_status(profile, observation or {})
    scenario_count = _scenario_count(parity)
    intent_results = _intent_results(generated, runtime_binding_checks)
    failed_intents = sorted(name for name, entry in intent_results.items() if entry.get("passed") is not True)
    passed = not failed_intents and all(checks.values())

    if not passed:
        failed = failed_intents + [key for key, value in checks.items() if value is not True]
        raise HostBoundIrNativeProofError(
            (profile.convergence_failure_message or f"{profile.app_id} IR proof did not converge")
            + (": " + ", ".join(failed[:12]) if failed else ".")
        )

    return {
        "schema": profile.report_schema,
        "version": profile.report_version,
        "appId": profile.app_id,
        "status": profile.status,
        "passed": True,
        "applicable": True,
        "coverageMode": profile.coverage_mode,
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
            f"{profile.authoritative_scenario_prefix}.{name}": {
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
            "freshChromiumObservation": authority.get("freshChromiumObservation"),
            "browserObservation": {
                "schema": (parity.get("browserObservation") or {}).get("schema"),
                "status": (parity.get("browserObservation") or {}).get("status"),
                "observedIntentCount": (parity.get("browserObservation") or {}).get("observedIntentCount"),
                "checks": (parity.get("browserObservation") or {}).get("checks"),
            },
        },
        "legacyEvidenceRequired": False,
        "promotionEligible": profile.promotion_eligible,
    }


def _scenario_count(parity: Mapping[str, Any]) -> int:
    return int(parity.get("intentCount") or len(parity.get("generatedBindings") or {}))


def _acceptance_status(profile: HostBoundIrNativeProofProfile, report: Mapping[str, Any]) -> dict[str, Any]:
    if not report:
        return {"provided": False, "status": profile.acceptance_not_required_status}
    results = [entry for entry in report.get("results") or [] if entry.get("appId") == profile.app_id]
    return {
        "provided": True,
        "status": report.get("status"),
        "passed": report.get("passed"),
        "appScoped": (report.get("evidenceScope") or {}).get("kind") == "app-scoped",
        "matchingResultCount": len(results),
    }


def _observation_status(profile: HostBoundIrNativeProofProfile, report: Mapping[str, Any]) -> dict[str, Any]:
    if not report:
        return {"provided": False, "status": profile.observation_not_required_status}
    return {
        "provided": True,
        "status": report.get("status"),
        "ok": report.get("ok"),
        "evidenceScope": report.get("evidenceScope"),
        "appId": report.get("appId"),
    }


def _intent_results(
    generated: Mapping[str, Any],
    runtime_binding_checks: Mapping[str, Any],
) -> dict[str, Any]:
    return {
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
