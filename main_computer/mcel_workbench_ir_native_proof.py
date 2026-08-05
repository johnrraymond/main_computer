"""IR-native intent, effect, and capability proof for promoted Contract Workbench."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_workbench_candidate_projection import DEFAULT_FIXTURE_IR, GENERATED_PATHS, PROJECTION_PROFILE

APP_ID = "contract-workbench"
OWNERSHIP_SCHEMA = "mcel.generated-file-ownership.v1"


class WorkbenchIrNativeProofError(RuntimeError):
    pass


def run_workbench_ir_native_intent_proof(
    *,
    repo: Path,
    record: Any,
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
    headed: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    del headed
    repo = repo.resolve()
    package = repo / record.package_root
    manifest = _load_json(repo / record.manifest, "Workbench package manifest")
    authoring = manifest.get("authoring") or {}
    if authoring.get("status") != "dsl-authoritative":
        raise WorkbenchIrNativeProofError("Workbench package is not DSL-authoritative.")
    source_ref = str(authoring.get("source") or authoring.get("definition") or "application.js")
    source = package / source_ref
    fixture = repo / DEFAULT_FIXTURE_IR
    compiled = compile_dsl_application(source, compare_ir_path=fixture)
    if not compiled.valid or compiled.normalized_ir is None or compiled.comparison_status != "exact":
        raise WorkbenchIrNativeProofError("Authoritative Workbench DSL does not compile to the canonical IR exactly.")
    ir = compiled.normalized_ir
    ownership = _verify_ownership(package, compiled)

    if acceptance.get("status") != "pass" or acceptance.get("passed") is not True:
        raise WorkbenchIrNativeProofError("Workbench acceptance evidence did not pass.")
    if observation.get("status") != "pass" or observation.get("ok") is not True:
        raise WorkbenchIrNativeProofError("Workbench browser observation did not pass.")
    observed = observation.get("observation") or {}
    scenario_results = {}
    for entry in observed.get("scenarioResults") or []:
        if not isinstance(entry, Mapping) or not entry.get("id"):
            continue
        observed_id = str(entry.get("id"))
        semantic_id = observed_id if observed_id.startswith("scenario:") else f"scenario:{observed_id}"
        scenario_results[semantic_id] = entry
    declared_scenarios = {
        str(entry.get("id")): entry
        for entry in ir.get("scenarios") or []
        if isinstance(entry, Mapping) and entry.get("id")
    }
    if set(scenario_results) != set(declared_scenarios):
        missing = sorted(set(declared_scenarios) - set(scenario_results))
        unexpected = sorted(set(scenario_results) - set(declared_scenarios))
        raise WorkbenchIrNativeProofError(
            "Browser scenario evidence is not exact"
            + (f"; missing={missing}" if missing else "")
            + (f"; unexpected={unexpected}" if unexpected else "")
        )
    failed_scenarios = sorted(key for key, value in scenario_results.items() if value.get("passed") is not True)
    if failed_scenarios:
        raise WorkbenchIrNativeProofError("Workbench browser scenarios failed: " + ", ".join(failed_scenarios))
    observation_coverage = [entry for entry in observed.get("observationCoverage") or [] if isinstance(entry, Mapping)]
    if len(observation_coverage) != 7 or not all(entry.get("passed") is True for entry in observation_coverage):
        raise WorkbenchIrNativeProofError("Workbench browser observation coverage is not 7/7 passing.")

    invariants = {str(item.get("id")) for item in (ir.get("proof") or {}).get("invariants") or [] if isinstance(item, Mapping)}
    effects = [item for item in ir.get("effects") or [] if isinstance(item, Mapping)]
    effects_by_owner: dict[str, list[str]] = {}
    for effect in effects:
        owner = str(((effect.get("owner") or {}).get("ref") or ""))
        effects_by_owner.setdefault(owner, []).append(str(effect.get("id") or ""))

    intent_reports: dict[str, Any] = {}
    failed_intents: list[str] = []
    for intent in ir.get("intents") or []:
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
            "transitionOrCapabilityDeclared": bool(intent.get("transition") or intent.get("capability") or operation_kind in {"cancel", "prohibited", "async"}),
            "writesDeclaredOrProhibited": bool(intent.get("writes") or operation_kind in {"cancel", "prohibited", "async", "async-capability"}),
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

    effect_instances = [
        {
            "effectId": str(effect.get("id") or ""),
            "owner": str(((effect.get("owner") or {}).get("ref") or "")),
            "status": "closed" if intent_reports.get(str(((effect.get("owner") or {}).get("ref") or "")), {}).get("passed") else "open",
        }
        for effect in effects
    ]
    effect_accounting = {
        "schema": "mcel.workbench-ir-native-effect-accounting.v1",
        "status": "closed" if len(effect_instances) == 18 and all(item["status"] == "closed" for item in effect_instances) else "open",
        "declaredEffectCount": len(effects),
        "closedEffectCount": sum(item["status"] == "closed" for item in effect_instances),
        "instances": effect_instances,
    }
    capabilities = [item for item in ir.get("capabilities") or [] if isinstance(item, Mapping)]
    capability_intents = [
        item for item in ir.get("intents") or []
        if str(item.get("operationKind") or "") in {"async", "async-capability", "cancel"}
    ]
    capability_accounting = {
        "schema": "mcel.workbench-ir-native-capability-accounting.v1",
        "status": "closed" if len(capabilities) == 1 and len(capability_intents) == 2 and all(intent_reports.get(str(item.get("id")), {}).get("passed") for item in capability_intents) else "open",
        "declaredCapabilityCount": len(capabilities),
        "declaredCapabilityIntentCount": len(capability_intents),
        "streamedOperationCount": sum(1 for cap in capabilities for op in cap.get("operations") or [] if isinstance(op, Mapping) and op.get("stream") is True),
        "cancellableOperationCount": sum(1 for cap in capabilities for op in cap.get("operations") or [] if isinstance(op, Mapping) and op.get("cancellable") is True),
    }

    passed = (
        not failed_intents
        and len(intent_reports) == 7
        and len(declared_scenarios) == 14
        and effect_accounting["status"] == "closed"
        and capability_accounting["status"] == "closed"
    )
    if not passed:
        raise WorkbenchIrNativeProofError("Workbench IR-native intent/effect/capability proof did not converge.")
    return {
        "schema": "mcel.workbench-ir-native-intent-complete-proof.v1",
        "appId": APP_ID,
        "status": "ir-native",
        "passed": True,
        "applicable": True,
        "coverageMode": "authoritative-dsl-ir-runtime-convergence",
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
        "scenarios": {key: {"passed": True, "intentId": str(((declared_scenarios[key].get("intent") or {}).get("ref") or ""))} for key in sorted(declared_scenarios)},
        "legacyEvidenceRequired": False,
    }


def _verify_ownership(package: Path, compiled: Any) -> dict[str, Any]:
    path = package / "mcel.generated.json"
    value = _load_json(path, "Workbench generated ownership")
    if value.get("schema") != OWNERSHIP_SCHEMA or value.get("appId") != APP_ID:
        raise WorkbenchIrNativeProofError("Workbench generated ownership manifest identity is invalid.")
    authority = value.get("sourceAuthority") or {}
    if authority.get("kind") != "mcel.dsl.v1" or authority.get("semanticFingerprint") != compiled.semantic_fingerprint:
        raise WorkbenchIrNativeProofError("Workbench generated ownership is not bound to the authoritative DSL semantics.")
    expected_paths = sorted(path for path in GENERATED_PATHS if path != "mcel.app.json")
    entries = {str(item.get("path")): item for item in value.get("generatedFiles") or [] if isinstance(item, Mapping)}
    if sorted(entries) != expected_paths:
        raise WorkbenchIrNativeProofError("Workbench generated ownership does not enumerate the exact derived artifact set.")
    files = []
    for relative in expected_paths:
        artifact = package / relative
        actual = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact.is_file() else None
        expected = entries[relative].get("sha256")
        exact = actual == expected
        files.append({"path": relative, "sha256": actual, "expectedSha256": expected, "exact": exact})
        if not exact:
            raise WorkbenchIrNativeProofError(f"Generated Workbench artifact drifted: {relative}")
    return {"schema": OWNERSHIP_SCHEMA, "path": "mcel.generated.json", "exact": True, "generatedFileCount": len(files), "generatedFiles": files, "projectionProfile": PROJECTION_PROFILE}


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkbenchIrNativeProofError(f"Could not load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkbenchIrNativeProofError(f"{label} must be a JSON object.")
    return value
