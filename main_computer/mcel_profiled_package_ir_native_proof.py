"""Generic IR-native proof aggregation for profiled-package MCEL fixtures.

Profiled-package apps prove IR-native authority by binding an authoritative DSL,
generated-file ownership manifest, generated projection artifacts, acceptance
evidence, browser observation evidence, scenario coverage, intent/effect
ownership, and capability accounting into one report.

App-specific wrappers should provide only a profile: app identity, DSL/fixture
defaults, generated artifact inventory, report labels, expected proof counts,
and diagnostic wording.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from main_computer.mcel_dsl_compiler import compile_dsl_application


OWNERSHIP_SCHEMA = "mcel.generated-file-ownership.v1"
REPORT_SCHEMA = "mcel.profiled-package-ir-native-intent-complete-proof.v1"


class ProfiledPackageIrNativeProofError(RuntimeError):
    """Raised when generic profiled-package IR-native proof cannot converge."""


@dataclass(frozen=True)
class ProfiledPackageIrNativeProofProfile:
    app_id: str
    default_dsl_source: Path
    default_fixture_ir: Path
    generated_paths: Sequence[str]
    projection_profile: str
    report_schema: str = REPORT_SCHEMA
    ownership_schema: str = OWNERSHIP_SCHEMA
    effect_accounting_schema: str = "mcel.profiled-package-ir-native-effect-accounting.v1"
    capability_accounting_schema: str = "mcel.profiled-package-ir-native-capability-accounting.v1"
    coverage_mode: str = "authoritative-dsl-ir-runtime-convergence"
    expected_intent_count: int | None = None
    expected_scenario_count: int | None = None
    expected_effect_count: int | None = None
    expected_observation_coverage_count: int | None = None
    expected_capability_count: int | None = None
    expected_capability_intent_count: int | None = None
    capability_operation_kinds: tuple[str, ...] = ("async", "async-capability", "cancel")
    transitionless_operation_kinds: tuple[str, ...] = ("cancel", "prohibited", "async")
    writeless_operation_kinds: tuple[str, ...] = ("cancel", "prohibited", "async", "async-capability")
    not_dsl_authoritative_message: str = "Package is not DSL-authoritative."
    compile_conflict_message: str = "Authoritative DSL does not compile to the canonical IR exactly."
    acceptance_failed_message: str = "Acceptance evidence did not pass."
    observation_failed_message: str = "Browser observation did not pass."
    scenario_mismatch_message: str = "Browser scenario evidence is not exact"
    scenario_failed_message: str = "Browser scenarios failed: "
    observation_coverage_failed_message: str = "Browser observation coverage is not passing."
    proof_failed_message: str = "IR-native intent/effect/capability proof did not converge."


def run_profiled_package_ir_native_intent_proof(
    profile: ProfiledPackageIrNativeProofProfile,
    *,
    repo: Path,
    record: Any,
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
    headed: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Run app-agnostic IR-native proof aggregation for a profiled package."""

    del headed
    repo = repo.resolve()
    package = repo / record.package_root
    manifest = _load_json(repo / record.manifest, f"{profile.app_id} package manifest")
    authoring = manifest.get("authoring") or {}
    if authoring.get("status") != "dsl-authoritative":
        raise ProfiledPackageIrNativeProofError(profile.not_dsl_authoritative_message)

    source_ref = str(authoring.get("source") or authoring.get("definition") or profile.default_dsl_source.name)
    source = package / source_ref
    fixture = _resolve(repo, profile.default_fixture_ir)
    compiled = compile_dsl_application(source, compare_ir_path=fixture)
    if not compiled.valid or compiled.normalized_ir is None or compiled.comparison_status != "exact":
        raise ProfiledPackageIrNativeProofError(profile.compile_conflict_message)
    ir = compiled.normalized_ir
    ownership = _verify_ownership(profile, compiled, record)

    if acceptance.get("status") != "pass" or acceptance.get("passed") is not True:
        raise ProfiledPackageIrNativeProofError(profile.acceptance_failed_message)
    if observation.get("status") != "pass" or observation.get("ok") is not True:
        raise ProfiledPackageIrNativeProofError(profile.observation_failed_message)

    observed = observation.get("observation") or {}
    scenario_results = _observed_scenario_results(observed)
    declared_scenarios = {
        str(entry.get("id")): entry
        for entry in ir.get("scenarios") or []
        if isinstance(entry, Mapping) and entry.get("id")
    }
    if set(scenario_results) != set(declared_scenarios):
        missing = sorted(set(declared_scenarios) - set(scenario_results))
        unexpected = sorted(set(scenario_results) - set(declared_scenarios))
        raise ProfiledPackageIrNativeProofError(
            profile.scenario_mismatch_message
            + (f"; missing={missing}" if missing else "")
            + (f"; unexpected={unexpected}" if unexpected else "")
        )
    failed_scenarios = sorted(key for key, value in scenario_results.items() if value.get("passed") is not True)
    if failed_scenarios:
        raise ProfiledPackageIrNativeProofError(profile.scenario_failed_message + ", ".join(failed_scenarios))

    observation_coverage = [
        entry for entry in observed.get("observationCoverage") or []
        if isinstance(entry, Mapping)
    ]
    expected_coverage = profile.expected_observation_coverage_count
    if expected_coverage is not None and (
        len(observation_coverage) != expected_coverage
        or not all(entry.get("passed") is True for entry in observation_coverage)
    ):
        raise ProfiledPackageIrNativeProofError(profile.observation_coverage_failed_message)

    invariants = {
        str(item.get("id"))
        for item in (ir.get("proof") or {}).get("invariants") or []
        if isinstance(item, Mapping)
    }
    effects = [item for item in ir.get("effects") or [] if isinstance(item, Mapping)]
    effects_by_owner = _effects_by_owner(effects)

    intent_reports, failed_intents = _intent_reports(
        profile,
        ir=ir,
        declared_scenarios=declared_scenarios,
        scenario_results=scenario_results,
        effects_by_owner=effects_by_owner,
        invariants=invariants,
    )
    effect_accounting = _effect_accounting(profile, effects, intent_reports)
    capability_accounting = _capability_accounting(profile, ir, intent_reports)

    passed = (
        not failed_intents
        and _count_matches(len(intent_reports), profile.expected_intent_count)
        and _count_matches(len(declared_scenarios), profile.expected_scenario_count)
        and effect_accounting["status"] == "closed"
        and capability_accounting["status"] == "closed"
    )
    if not passed:
        raise ProfiledPackageIrNativeProofError(profile.proof_failed_message)

    return {
        "schema": profile.report_schema,
        "appId": profile.app_id,
        "status": "ir-native",
        "passed": True,
        "applicable": True,
        "coverageMode": profile.coverage_mode,
        "semanticFingerprint": compiled.semantic_fingerprint,
        "sourceBindingFingerprint": compiled.source_binding_fingerprint,
        "declaredIntentCount": len(intent_reports),
        "coveredIntentCount": len(intent_reports),
        "declaredScenarioCount": len(declared_scenarios),
        "observedScenarioCount": len(scenario_results),
        "failedIntentIds": [],
        "failedScenarioIds": [],
        "missingScenarioIds": [],
        "unexpectedScenarioIds": [],
        "generatedOwnership": ownership,
        "effectAccounting": effect_accounting,
        "capabilityAccounting": capability_accounting,
        "intents": intent_reports,
        "scenarios": {
            key: {
                "passed": True,
                "intentId": str(((declared_scenarios[key].get("intent") or {}).get("ref") or "")),
            }
            for key in sorted(declared_scenarios)
        },
        "legacyEvidenceRequired": False,
    }


def _observed_scenario_results(observed: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    scenario_results: dict[str, Mapping[str, Any]] = {}
    for entry in observed.get("scenarioResults") or []:
        if not isinstance(entry, Mapping) or not entry.get("id"):
            continue
        observed_id = str(entry.get("id"))
        semantic_id = observed_id if observed_id.startswith("scenario:") else f"scenario:{observed_id}"
        scenario_results[semantic_id] = entry
    return scenario_results


def _effects_by_owner(effects: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for effect in effects:
        owner = str(((effect.get("owner") or {}).get("ref") or ""))
        grouped.setdefault(owner, []).append(str(effect.get("id") or ""))
    return grouped


def _intent_reports(
    profile: ProfiledPackageIrNativeProofProfile,
    *,
    ir: Mapping[str, Any],
    declared_scenarios: Mapping[str, Mapping[str, Any]],
    scenario_results: Mapping[str, Mapping[str, Any]],
    effects_by_owner: Mapping[str, Sequence[str]],
    invariants: set[str],
) -> tuple[dict[str, Any], list[str]]:
    intent_reports: dict[str, Any] = {}
    failed_intents: list[str] = []
    for intent in ir.get("intents") or []:
        if not isinstance(intent, Mapping):
            continue
        intent_id = str(intent.get("id") or "")
        scenario_ids = sorted(
            scenario_id for scenario_id, scenario in declared_scenarios.items()
            if str(((scenario.get("intent") or {}).get("ref") or "")) == intent_id
        )
        declared_effects = sorted(str((ref or {}).get("ref") or "") for ref in intent.get("effectRefs") or [])
        owned_effects = sorted(effects_by_owner.get(intent_id, []))
        invariant_ids = sorted(str((ref or {}).get("ref") or "") for ref in intent.get("invariants") or [])
        operation_kind = str(intent.get("operationKind") or "")
        checks = {
            "declaredScenario": bool(scenario_ids),
            "allScenariosPassed": all(scenario_results[item].get("passed") is True for item in scenario_ids),
            "effectOwnershipExact": declared_effects == owned_effects,
            "invariantsResolved": all(item in invariants for item in invariant_ids),
            "transitionOrCapabilityDeclared": bool(
                intent.get("transition")
                or intent.get("capability")
                or operation_kind in profile.transitionless_operation_kinds
            ),
            "writesDeclaredOrProhibited": bool(
                intent.get("writes")
                or operation_kind in profile.writeless_operation_kinds
            ),
        }
        passed = all(checks.values())
        if not passed:
            failed_intents.append(intent_id)
        intent_reports[intent_id] = {
            "operationKind": operation_kind,
            "scenarioIds": scenario_ids,
            "effectIds": declared_effects,
            "invariantIds": invariant_ids,
            "checks": checks,
            "passed": passed,
        }
    return intent_reports, failed_intents


def _effect_accounting(
    profile: ProfiledPackageIrNativeProofProfile,
    effects: Sequence[Mapping[str, Any]],
    intent_reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    effect_instances = [
        {
            "effectId": str(effect.get("id") or ""),
            "owner": str(((effect.get("owner") or {}).get("ref") or "")),
            "status": "closed" if intent_reports.get(str(((effect.get("owner") or {}).get("ref") or "")), {}).get("passed") else "open",
        }
        for effect in effects
    ]
    expected_effect_count = profile.expected_effect_count
    expected_count_met = expected_effect_count is None or len(effect_instances) == expected_effect_count
    closed_count = sum(item["status"] == "closed" for item in effect_instances)
    return {
        "schema": profile.effect_accounting_schema,
        "status": "closed" if expected_count_met and closed_count == len(effect_instances) else "open",
        "declaredEffectCount": len(effects),
        "closedEffectCount": closed_count,
        "instances": effect_instances,
    }


def _capability_accounting(
    profile: ProfiledPackageIrNativeProofProfile,
    ir: Mapping[str, Any],
    intent_reports: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    capabilities = [item for item in ir.get("capabilities") or [] if isinstance(item, Mapping)]
    capability_intents = [
        item for item in ir.get("intents") or []
        if isinstance(item, Mapping) and str(item.get("operationKind") or "") in profile.capability_operation_kinds
    ]
    expected_capabilities_met = _count_matches(len(capabilities), profile.expected_capability_count)
    expected_intents_met = _count_matches(len(capability_intents), profile.expected_capability_intent_count)
    all_intents_passed = all(intent_reports.get(str(item.get("id")), {}).get("passed") for item in capability_intents)
    return {
        "schema": profile.capability_accounting_schema,
        "status": "closed" if expected_capabilities_met and expected_intents_met and all_intents_passed else "open",
        "declaredCapabilityCount": len(capabilities),
        "declaredCapabilityIntentCount": len(capability_intents),
        "streamedOperationCount": sum(
            1
            for cap in capabilities
            for op in cap.get("operations") or []
            if isinstance(op, Mapping) and op.get("stream") is True
        ),
        "cancellableOperationCount": sum(
            1
            for cap in capabilities
            for op in cap.get("operations") or []
            if isinstance(op, Mapping) and op.get("cancellable") is True
        ),
    }


def _verify_ownership(profile: ProfiledPackageIrNativeProofProfile, compiled: Any, record: Any) -> dict[str, Any]:
    raw = record.files.get("mcel.generated.json")
    if raw is None:
        raise ProfiledPackageIrNativeProofError("Virtual generated ownership manifest is missing.")
    value = json.loads(raw.decode("utf-8"))
    if value.get("schema") != profile.ownership_schema or value.get("appId") != profile.app_id:
        raise ProfiledPackageIrNativeProofError("Generated ownership manifest identity is invalid.")
    authority = value.get("sourceAuthority") or {}
    if authority.get("kind") != "mcel.dsl.v1" or authority.get("semanticFingerprint") != compiled.semantic_fingerprint:
        raise ProfiledPackageIrNativeProofError("Generated ownership is not bound to the authoritative DSL semantics.")
    expected_paths = sorted(path for path in profile.generated_paths if path != "mcel.app.json")
    entries = {str(item.get("path")): item for item in value.get("generatedFiles") or [] if isinstance(item, Mapping)}
    if sorted(entries) != expected_paths:
        raise ProfiledPackageIrNativeProofError("Generated ownership does not enumerate the exact derived artifact set.")
    files = []
    for relative in expected_paths:
        content = record.files.get(relative)
        actual = "sha256:" + hashlib.sha256(content).hexdigest() if content is not None else None
        expected = entries[relative].get("sha256")
        exact = actual == expected
        files.append({"path": relative, "sha256": actual, "expectedSha256": expected, "exact": exact})
        if not exact:
            raise ProfiledPackageIrNativeProofError(f"Generated artifact drifted: {relative}")
    return {
        "schema": profile.ownership_schema,
        "path": "mcel.generated.json",
        "exact": True,
        "generatedFileCount": len(files),
        "generatedFiles": files,
        "projectionProfile": profile.projection_profile,
    }


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfiledPackageIrNativeProofError(f"Could not load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProfiledPackageIrNativeProofError(f"{label} must be a JSON object.")
    return value


def _resolve(repo: Path, path: Path) -> Path:
    return path if path.is_absolute() else repo / path


def _count_matches(actual: int, expected: int | None) -> bool:
    return expected is None or actual == expected
