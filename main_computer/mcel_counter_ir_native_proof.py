"""IR-native intent-complete proof for the promoted Contract Counter.

Wave 8 binds the authoritative ``mcel.dsl.v1`` source to canonical application
IR, generated-file ownership, fresh Node and Chromium operation evidence, and
closed effect accounting.  It replaces the former ``legacy-evidence``
classification without changing Counter semantics or truth status.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_counter_candidate_evidence import (
    CounterCandidateEvidenceError,
    _build_effect_accounting,
    _run_browser_effect_probe,
    _run_counter_effect_probe,
)
from main_computer.mcel_application_packages import build_application_package_catalog
from main_computer.mcel_application_runtime_projection import build_runtime_projection_set
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_evidence_provenance import build_repository_provenance

REPORT_SCHEMA = "mcel.ir-native-intent-complete-proof.v1"
REPORT_VERSION = "mcel-counter-ir-native-proof-wave8"
OPERATION_PREFIX = "ir-native"


class CounterIrNativeProofError(RuntimeError):
    """Raised when promoted Counter source, ownership, IR, or evidence diverges."""


def run_counter_ir_native_intent_proof(
    *,
    repo: Path,
    record: Any,
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
    headed: bool = False,
    node_probe_runner: Callable[[Path, str], Mapping[str, Any]] | None = None,
    browser_probe_runner: Callable[[Path, bool, str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    package_root = (repo / record.package_root).resolve()
    package_manifest = _load_json(repo / record.manifest, "application package manifest")
    authoring_manifest = package_manifest.get("authoring") or {}
    authoring = dict(record.authoring or {})
    if authoring_manifest.get("status") != "dsl-authoritative":
        raise CounterIrNativeProofError("The application is not declared dsl-authoritative.")
    if record.app_id != "contract-counter":
        raise CounterIrNativeProofError("Wave 8 currently supports only contract-counter.")

    source_reference = str(authoring_manifest.get("source") or "").strip()
    ownership_reference = str(authoring_manifest.get("ownership") or "").strip()
    if source_reference != "application.js" or ownership_reference != "mcel.generated.json":
        raise CounterIrNativeProofError(
            "Promoted Counter authoring must bind application.js and mcel.generated.json exactly."
        )
    source_path = repo / str(authoring.get("source") or "")
    ownership_relative = Path(str(authoring.get("ownership") or "")).relative_to(Path(record.package_root)).as_posix()
    ownership_bytes = record.files.get(ownership_relative)
    if not source_path.is_file() or ownership_bytes is None:
        raise CounterIrNativeProofError("Authoritative DSL source or virtual generated ownership manifest is missing.")

    compiled = compile_dsl_application(source_path)
    if not compiled.valid or compiled.normalized_ir is None:
        detail = ", ".join(str(item.get("code")) for item in compiled.diagnostics[:5])
        raise CounterIrNativeProofError(
            "Authoritative DSL did not compile to valid canonical IR" + (f": {detail}" if detail else ".")
        )
    if compiled.app_id != record.app_id:
        raise CounterIrNativeProofError("Authoritative DSL app identity does not match the package manifest.")

    evidence_binding = _verify_evidence_alignment(
        repo=repo,
        record=record,
        acceptance=acceptance,
        observation=observation,
    )

    ownership = json.loads(ownership_bytes.decode("utf-8"))
    ownership_result = _verify_generated_ownership(
        package_root=package_root,
        record=record,
        ownership=ownership,
        semantic_fingerprint=compiled.semantic_fingerprint,
    )

    try:
        node_probe = dict(
            (node_probe_runner or _run_counter_effect_probe)(repo, OPERATION_PREFIX)
        )
        browser_probe = dict(
            (browser_probe_runner or _run_browser_effect_probe)(repo, headed, OPERATION_PREFIX)
        )
    except CounterCandidateEvidenceError as exc:
        raise CounterIrNativeProofError(str(exc)) from exc

    effect_accounting = _build_effect_accounting(
        ir=compiled.normalized_ir,
        acceptance=acceptance,
        observation=observation,
        node_probe=node_probe,
        browser_probe=browser_probe,
        operation_prefix=OPERATION_PREFIX,
    )
    if effect_accounting.get("status") != "closed" or effect_accounting.get("valid") is not True:
        codes = [str(item.get("code")) for item in effect_accounting.get("diagnostics") or []]
        raise CounterIrNativeProofError(
            "IR-native effect accounting did not close" + (f": {', '.join(codes[:8])}" if codes else ".")
        )

    scenario_results = _prove_scenarios(
        ir=compiled.normalized_ir,
        node_probe=node_probe,
        browser_probe=browser_probe,
    )
    intent_results = _prove_intents(
        ir=compiled.normalized_ir,
        scenario_results=scenario_results,
        effect_accounting=effect_accounting,
    )
    failed_scenarios = sorted(key for key, item in scenario_results.items() if item.get("passed") is not True)
    failed_intents = sorted(key for key, item in intent_results.items() if item.get("passed") is not True)

    acceptance_pass = acceptance.get("status") == "pass" and acceptance.get("passed") is True
    observation_pass = observation.get("status") == "pass" and observation.get("ok") is True
    invariants = {
        str(item.get("id")): item
        for item in ((compiled.normalized_ir.get("proof") or {}).get("invariants") or [])
        if isinstance(item, Mapping) and item.get("id")
    }
    referenced_invariants = {
        str(ref.get("ref"))
        for intent in compiled.normalized_ir.get("intents") or []
        if isinstance(intent, Mapping)
        for ref in intent.get("invariants") or []
        if isinstance(ref, Mapping) and ref.get("ref")
    }
    cross_cutting = {
        "authoritativeDslCompiled": True,
        "semanticFingerprintBound": ownership_result["semanticFingerprintExact"],
        "generatedOwnershipExact": ownership_result["exact"],
        "acceptancePassed": acceptance_pass,
        "browserObservationPassed": observation_pass,
        "effectAccountingClosed": True,
        "allDeclaredScenariosEvidenced": not failed_scenarios,
        "allDeclaredIntentsCovered": not failed_intents,
        "allDeclaredInvariantsReferenced": bool(invariants) and set(invariants) == referenced_invariants,
        "legacyEvidenceEliminated": True,
    }
    passed = all(cross_cutting.values())
    if not passed:
        details = failed_intents + failed_scenarios + [key for key, value in cross_cutting.items() if value is not True]
        raise CounterIrNativeProofError(
            "IR-native intent-complete proof did not converge"
            + (f": {', '.join(details[:12])}" if details else ".")
        )

    return {
        "schema": REPORT_SCHEMA,
        "version": REPORT_VERSION,
        "appId": record.app_id,
        "status": "ir-native",
        "passed": True,
        "applicable": True,
        "coverageMode": "authoritative-dsl-ir-runtime-convergence",
        "legacyEvidenceRequired": False,
        "semanticFingerprint": compiled.semantic_fingerprint,
        "definitionFingerprint": compiled.semantic_fingerprint,
        "declaredIntentCount": len(intent_results),
        "coveredIntentCount": len(intent_results),
        "declaredScenarioCount": len(scenario_results),
        "observedScenarioCount": len(scenario_results),
        "failedIntentIds": [],
        "missingScenarioIds": [],
        "unexpectedScenarioIds": [],
        "failedScenarioIds": [],
        "crossCuttingChecks": cross_cutting,
        "intents": intent_results,
        "scenarios": scenario_results,
        "sourceAuthority": {
            "kind": "mcel.dsl.v1",
            "path": _display_path(source_path, repo),
            "sha256": _sha256_prefixed(source_path),
            "semanticFingerprint": compiled.semantic_fingerprint,
            "currentSourceBindingFingerprint": compiled.source_binding_fingerprint,
            "promotedCandidateSourceBindingFingerprint": ownership_result["promotedCandidateSourceBindingFingerprint"],
        },
        "generatedOwnership": ownership_result,
        "effectAccounting": effect_accounting,
        "evidenceBindings": {
            **evidence_binding,
            "acceptanceGeneratedAt": acceptance.get("generatedAt"),
            "browserObservationGeneratedAt": observation.get("generatedAt"),
            "nodeProbeSchema": node_probe.get("schema"),
            "browserProbeSchema": browser_probe.get("schema"),
        },
    }


def _verify_evidence_alignment(
    *,
    repo: Path,
    record: Any,
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    catalog = build_application_package_catalog(repo)
    current = [item for item in catalog.packages if item.app_id == record.app_id]
    if len(current) != 1 or current[0].fingerprint != record.fingerprint:
        raise CounterIrNativeProofError("Counter package catalog binding is stale.")
    projections = [
        item for item in build_runtime_projection_set(repo).projections
        if item.app_id == record.app_id
    ]
    if len(projections) != 1:
        raise CounterIrNativeProofError("Counter runtime projection was not discovered exactly once.")
    projection = projections[0]
    provenance = build_repository_provenance(repo)

    scope = acceptance.get("evidenceScope") or {}
    results = [item for item in acceptance.get("results") or [] if item.get("appId") == record.app_id]
    packages = [item for item in acceptance.get("applicationPackages") or [] if item.get("appId") == record.app_id]
    if (
        acceptance.get("status") != "pass"
        or acceptance.get("passed") is not True
        or scope.get("kind") != "app-scoped"
        or list(scope.get("selectedApps") or []) != [record.app_id]
        or len(results) != 1
        or results[0].get("status") != "pass"
        or len(packages) != 1
        or packages[0].get("packageFingerprint") != record.fingerprint
        or (acceptance.get("repositoryProvenance") or {}).get("fingerprint") != provenance.get("fingerprint")
    ):
        raise CounterIrNativeProofError("Acceptance evidence is not exactly bound to the promoted Counter package and repository.")

    observed = observation.get("observation") or {}
    comparison = observed.get("comparison") or {}
    surface = observation.get("surfaceConformance") or {}
    if (
        observation.get("status") != "pass"
        or observation.get("ok") is not True
        or observation.get("evidenceScope") != "app-scoped"
        or observation.get("appId") != record.app_id
        or (observation.get("package") or {}).get("fingerprint") != record.fingerprint
        or observation.get("catalogFingerprint") != catalog.fingerprint
        or (observation.get("repositoryProvenance") or {}).get("fingerprint") != provenance.get("fingerprint")
        or observed.get("runtimeProjectionFingerprint") != projection.fingerprint
        or observed.get("repositoryFingerprint") != provenance.get("fingerprint")
        or not all(comparison.get(key) is True for key in ("stateMatches", "receiptMatches", "surfaceMatches"))
        or surface.get("status") != "pass"
        or surface.get("valid") is not True
    ):
        raise CounterIrNativeProofError("Browser observation is not exactly bound to the promoted Counter package, projection, and repository.")
    return {
        "packageFingerprint": record.fingerprint,
        "catalogFingerprint": catalog.fingerprint,
        "runtimeProjectionFingerprint": projection.fingerprint,
        "repositoryFingerprint": provenance.get("fingerprint"),
        "status": "exact",
    }


def write_counter_ir_native_report(report: Mapping[str, Any], output_directory: Path) -> tuple[Path, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    json_path = output_directory / "mcel-ir-native-intent-proof.json"
    markdown_path = output_directory / "mcel-ir-native-intent-proof.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Contract Counter IR-Native Intent-Complete Proof",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Semantic fingerprint: `{report.get('semanticFingerprint')}`",
        f"- Intent coverage: `{report.get('coveredIntentCount')} / {report.get('declaredIntentCount')}`",
        f"- Scenario evidence: `{report.get('observedScenarioCount')} / {report.get('declaredScenarioCount')}`",
        f"- Effect accounting: `{(report.get('effectAccounting') or {}).get('status')}`",
        f"- Generated ownership: `{'exact' if (report.get('generatedOwnership') or {}).get('exact') else 'conflicting'}`",
        f"- Legacy evidence required: `{str(bool(report.get('legacyEvidenceRequired'))).lower()}`",
        "",
        "## Intents",
        "",
    ]
    for intent_id, entry in sorted((report.get("intents") or {}).items()):
        lines.append(f"- `{intent_id}`: `{'pass' if entry.get('passed') else 'fail'}`")
    lines.extend(["", "## Scenarios", ""])
    for scenario_id, entry in sorted((report.get("scenarios") or {}).items()):
        lines.append(f"- `{scenario_id}`: `{'pass' if entry.get('passed') else 'fail'}`")
    lines.append("")
    return "\n".join(lines)


def _verify_generated_ownership(
    *,
    package_root: Path,
    record: Any,
    ownership: Mapping[str, Any],
    semantic_fingerprint: str | None,
) -> dict[str, Any]:
    if ownership.get("schema") != "mcel.generated-file-ownership.v1":
        raise CounterIrNativeProofError("Generated ownership manifest has the wrong schema.")
    if ownership.get("appId") != record.app_id:
        raise CounterIrNativeProofError("Generated ownership manifest has the wrong app identity.")
    if ownership.get("generatedArtifactsAreDerived") is not True or ownership.get("manualEditsProhibited") is not True:
        raise CounterIrNativeProofError("Generated ownership does not prohibit manual edits to derived artifacts.")
    source_authority = ownership.get("sourceAuthority") or {}
    semantic_exact = source_authority.get("semanticFingerprint") == semantic_fingerprint
    if (
        source_authority.get("kind") != "mcel.dsl.v1"
        or source_authority.get("path") != "application.js"
        or not semantic_exact
    ):
        raise CounterIrNativeProofError("Generated ownership is not bound to the authoritative DSL semantics.")

    package_prefix = Path(record.package_root)
    expected_paths = {
        Path(str(path)).relative_to(package_prefix).as_posix()
        for path in (record.contracts or {}).values()
    }
    generated = ownership.get("generatedFiles") or []
    if not isinstance(generated, list):
        raise CounterIrNativeProofError("Generated ownership files must be an array.")
    observed_paths: set[str] = set()
    files: list[dict[str, Any]] = []
    for entry in generated:
        if not isinstance(entry, Mapping):
            raise CounterIrNativeProofError("Generated ownership contains a non-object file entry.")
        relative = str(entry.get("path") or "")
        if not relative or relative in observed_paths or relative.startswith("/") or ".." in Path(relative).parts:
            raise CounterIrNativeProofError("Generated ownership contains an unsafe or duplicate path.")
        observed_paths.add(relative)
        content = record.files.get(relative)
        actual = "sha256:" + hashlib.sha256(content).hexdigest() if content is not None else None
        expected = str(entry.get("sha256") or "")
        exact = actual == expected and entry.get("generator") == "mcel.counter.explicit-projection.v1"
        files.append({"path": relative, "sha256": actual, "expectedSha256": expected, "exact": exact})
    if observed_paths != expected_paths:
        raise CounterIrNativeProofError("Generated ownership does not cover exactly the declared contract files.")
    if not all(item["exact"] for item in files):
        drifted = [item["path"] for item in files if not item["exact"]]
        raise CounterIrNativeProofError("Generated contract ownership drift: " + ", ".join(drifted))
    return {
        "schema": ownership.get("schema"),
        "path": "mcel.generated.json",
        "sha256": "sha256:" + hashlib.sha256(record.files["mcel.generated.json"]).hexdigest(),
        "exact": True,
        "semanticFingerprintExact": semantic_exact,
        "generatedFileCount": len(files),
        "generatedFiles": files,
        "promotedCandidateSourceBindingFingerprint": source_authority.get("sourceBindingFingerprint"),
    }


def _prove_scenarios(
    *,
    ir: Mapping[str, Any],
    node_probe: Mapping[str, Any],
    browser_probe: Mapping[str, Any],
) -> dict[str, Any]:
    node_ops = {str(item.get("operationId")): item for item in node_probe.get("operations") or []}
    browser_ops = {str(item.get("operationId")): item for item in browser_probe.get("operations") or []}
    results: dict[str, Any] = {}
    for scenario in ir.get("scenarios") or []:
        if not isinstance(scenario, Mapping) or not scenario.get("id"):
            continue
        scenario_id = str(scenario["id"])
        intent_id = str(((scenario.get("intent") or {}).get("ref") or ""))
        suffix = intent_id.removeprefix("intent:")
        claims = list(scenario.get("steps") or [])
        refused = any(
            isinstance(claim, Mapping) and claim.get("kind") == "claim.receipt-disposition"
            for claim in claims
        )
        operation = "stale" if suffix == "increment" and refused else suffix
        node = node_ops.get(f"{OPERATION_PREFIX}-{operation}") or {}
        browser = browser_ops.get(f"{OPERATION_PREFIX}-browser-{operation}") or {}
        claim_results = [_prove_claim(claim, node=node, browser=browser) for claim in claims]
        passed = bool(claim_results) and all(item.get("passed") is True for item in claim_results)
        results[scenario_id] = {
            "intentId": intent_id,
            "operationId": f"{OPERATION_PREFIX}-{operation}",
            "browserOperationId": f"{OPERATION_PREFIX}-browser-{operation}",
            "claimCount": len(claim_results),
            "claims": claim_results,
            "passed": passed,
        }
    return results


def _prove_claim(claim: Any, *, node: Mapping[str, Any], browser: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(claim, Mapping):
        return {"kind": "invalid", "passed": False}
    kind = str(claim.get("kind") or "")
    if kind == "claim.equal":
        state_ref = str((((claim.get("actual") or {}).get("state") or {}).get("ref") or ""))
        state_name = state_ref.removeprefix("state:")
        expected_expr = claim.get("expected") or {}
        expected = expected_expr.get("value") if isinstance(expected_expr, Mapping) else None
        node_after = node.get("after") or {}
        browser_after = browser.get("after") or {}
        passed = node_after.get(state_name) == expected and browser_after.get(state_name) == expected
        return {"kind": kind, "state": state_ref, "expected": expected, "passed": passed}
    if kind == "claim.exists":
        observation = browser.get("observation") or {}
        comparison = observation.get("comparison") or {}
        passed = observation.get("status") == "pass" and comparison.get("surfaceMatches") is True
        return {"kind": kind, "target": (claim.get("target") or {}).get("ref"), "passed": passed}
    if kind == "claim.receipt-disposition":
        expected = str(claim.get("expected") or "")
        abstract_code = str(claim.get("code") or "")
        runtime_code = {"REVISION_STALE": "SCM_STALE_REVISION"}.get(abstract_code, abstract_code)
        node_result = node.get("result") or {}
        browser_result = browser.get("result") or {}
        passed = (
            expected == "refused"
            and node_result.get("status") == "refused"
            and node_result.get("code") == runtime_code
            and browser_result.get("status") == "refused"
            and browser_result.get("code") == runtime_code
            and node.get("before") == node.get("after")
            and browser.get("before") == browser.get("after")
        )
        return {
            "kind": kind,
            "expected": expected,
            "abstractCode": abstract_code,
            "runtimeCode": runtime_code,
            "passed": passed,
        }
    return {"kind": kind or "unknown", "passed": False}


def _prove_intents(
    *,
    ir: Mapping[str, Any],
    scenario_results: Mapping[str, Mapping[str, Any]],
    effect_accounting: Mapping[str, Any],
) -> dict[str, Any]:
    effects = {
        str(item.get("id")): item
        for item in ir.get("effects") or []
        if isinstance(item, Mapping) and item.get("id")
    }
    invariants = {
        str(item.get("id"))
        for item in ((ir.get("proof") or {}).get("invariants") or [])
        if isinstance(item, Mapping) and item.get("id")
    }
    instances = effect_accounting.get("instances") or []
    results: dict[str, Any] = {}
    for intent in ir.get("intents") or []:
        if not isinstance(intent, Mapping) or not intent.get("id"):
            continue
        intent_id = str(intent["id"])
        operation_kind = str(intent.get("operationKind") or "")
        scenario_ids = sorted(
            key for key, item in scenario_results.items() if item.get("intentId") == intent_id
        )
        effect_ids = sorted(
            str(ref.get("ref")) for ref in intent.get("effectRefs") or [] if isinstance(ref, Mapping)
        )
        owned_effect_ids = sorted(
            effect_id
            for effect_id, effect in effects.items()
            if str(((effect.get("owner") or {}).get("ref") or "")) == intent_id
        )
        invariant_ids = sorted(
            str(ref.get("ref")) for ref in intent.get("invariants") or [] if isinstance(ref, Mapping)
        )
        checks = {
            "declaredScenario": bool(scenario_ids),
            "allScenariosPassed": bool(scenario_ids) and all(scenario_results[key].get("passed") is True for key in scenario_ids),
            "effectOwnershipExact": effect_ids == owned_effect_ids,
            "invariantsResolved": all(item in invariants for item in invariant_ids),
        }
        if operation_kind == "mutation":
            completed = {
                str(item.get("effectId"))
                for item in instances
                if item.get("owner") == intent_id and item.get("disposition") == "completed" and item.get("status") == "closed"
            }
            checks.update(
                {
                    "transitionDeclared": isinstance(intent.get("transition"), Mapping),
                    "writesDeclared": bool(intent.get("writes")),
                    "effectsCompleted": set(effect_ids) == completed,
                }
            )
        elif operation_kind == "prohibited":
            checks.update(
                {
                    "noTransition": not intent.get("transition"),
                    "noWrites": not intent.get("writes"),
                    "noEffects": not effect_ids and not owned_effect_ids,
                    "reasonDeclared": bool(intent.get("reasonCode")),
                    "canonicalWriteAbsent": effect_accounting.get("directSetCanonicalWriteObserved") is False,
                }
            )
        else:
            checks["knownOperationKind"] = False
        results[intent_id] = {
            "operationKind": operation_kind,
            "scenarioIds": scenario_ids,
            "effectIds": effect_ids,
            "invariantIds": invariant_ids,
            "checks": checks,
            "passed": all(checks.values()),
        }
    return results


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CounterIrNativeProofError(f"Could not load {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CounterIrNativeProofError(f"{label} must be a JSON object.")
    return value


def _sha256_prefixed(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())
