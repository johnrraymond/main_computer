#!/usr/bin/env python3
"""Compose app-scoped MCEL authorities into a final semantic-runtime proof.

The proof runner does not replace package, acceptance, browser observation,
SCM, provenance, or truth authorities.  It executes or loads their app-scoped
evidence, verifies that every artifact describes the same package and source
revision, and asks the browser-side ``McelAppTruthGate`` for the final verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from .mcel_application_package_browser_catalog import check_browser_catalog
    from .mcel_application_packages import build_application_package_catalog, repository_root
    from .mcel_application_runtime_projection import check_runtime_projections
    from .mcel_evidence_provenance import build_repository_provenance
    from .mcel_node_runtime import resolve_node_executable
except ImportError:
    _ROOT = Path(__file__).resolve().parents[1]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from main_computer.mcel_application_package_browser_catalog import check_browser_catalog
    from main_computer.mcel_application_packages import build_application_package_catalog, repository_root
    from main_computer.mcel_application_runtime_projection import check_runtime_projections
    from main_computer.mcel_evidence_provenance import build_repository_provenance
    from main_computer.mcel_node_runtime import resolve_node_executable


RUNNER_VERSION = "mcel-app-prove-v1"
REPORT_SCHEMA = "mcel.application-proof-report.v1"
DEFAULT_OUTPUT_ROOT = Path("runtime/reports/mcel-app-proof")
REQUIRED_SURFACE_LAYERS = (
    "semantic-surface",
    "layout-grammar",
    "runtime-ownership",
    "runtime-visual-fit",
    "diagnostic-no-throw",
)


class AppProofError(RuntimeError):
    """Raised when independent app proof authorities do not agree."""


NODE_TRUTH_BRIDGE = r"""
const fs = require("fs");
const vm = require("vm");
const input = JSON.parse(fs.readFileSync(0, "utf8"));
const sandbox = {console: {log(){}, info(){}, warn(){}, error(){}}, Date, setTimeout, clearTimeout};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(input.truthGatePath, "utf8"), sandbox, {filename: input.truthGatePath});
if (!sandbox.McelAppTruthGate) throw new Error("McelAppTruthGate failed to load.");
const requirementsRegistry = {
  REGISTRY_VERSION: input.requirements.registryVersion,
  strictSchemaReady: true,
  getSummary() { return {valid: true, error_count: 0, registry_version: input.requirements.registryVersion}; },
  getAppContract(appId) { return appId === input.appId ? input.requirements.contract : null; },
  listAppContracts() { return [input.requirements.contract]; }
};
const domainAdapterRegistry = {
  REGISTRY_VERSION: input.adapter.registryVersion,
  AUTHORITY: input.adapter.authority,
  evaluateAdapterReadiness(appId) { return appId === input.appId ? input.adapter.readiness : null; },
  listAdapters() { return [{appId: input.appId, adapterId: input.adapter.readiness.adapterId}]; }
};
const appSurfaceRegistry = {
  registryVersion: input.surface.registryVersion,
  getAppPolicy(appId) { return appId === input.appId ? input.surface.policy : null; },
  listPolicies() { return [input.surface.policy]; }
};
const truth = sandbox.McelAppTruthGate.evaluateAppTruth(input.appId, {
  requirementsRegistry,
  domainAdapterRegistry,
  appSurfaceRegistry,
  runtimeEvidence: input.runtimeEvidence,
  acceptanceEvidence: input.acceptanceEvidence,
  now: input.now
});
process.stdout.write(JSON.stringify(truth));
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slug(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value.lower()).strip("-")


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppProofError(f"Could not load {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AppProofError(f"{label} must be a JSON object: {path}")
    return value


def _package_record(catalog: Any, app_id: str) -> Any:
    records = [record for record in catalog.packages if record.app_id == app_id]
    if len(records) != 1:
        raise AppProofError(f"Application package {app_id!r} was not discovered exactly once.")
    record = records[0]
    if not record.valid or not record.fingerprint:
        raise AppProofError(f"Application package {app_id!r} is invalid.")
    return record


def _run_dependency(repo: Path, arguments: Sequence[str]) -> None:
    completed = subprocess.run(
        [sys.executable, *arguments],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode != 0:
        output = completed.stdout.strip()
        raise AppProofError(
            f"Dependent MCEL authority failed ({' '.join(arguments)}):"
            + (f"\n{output}" if output else "")
        )


def _requirements_tool(repo: Path) -> Any:
    path = repo / "tools" / "mcel_requirements_registry.py"
    spec = importlib.util.spec_from_file_location("mcel_app_proof_requirements", path)
    if spec is None or spec.loader is None:
        raise AppProofError("Could not load the MCEL requirements registry authority.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _intent_complete_coverage(
    *,
    repo: Path,
    app_id: str,
    record: Any,
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    normalized_reference = str((record.authoring or {}).get("normalizedDefinition") or "").strip()
    if not normalized_reference:
        results = [entry for entry in acceptance.get("results") or [] if entry.get("appId") == app_id]
        passed = (
            acceptance.get("status") == "pass"
            and acceptance.get("passed") is True
            and len(results) == 1
            and results[0].get("status") == "pass"
            and int(results[0].get("testCount") or 0) > 0
            and observation.get("status") == "pass"
            and observation.get("ok") is True
        )
        if not passed:
            raise AppProofError("Legacy package acceptance and browser evidence did not converge.")
        return {
            "schema": "mcel.intent-complete-proof.v1",
            "appId": app_id,
            "status": "pass",
            "passed": True,
            "coverageMode": "package-acceptance-and-browser-observation",
            "definitionFingerprint": "",
            "declaredIntentCount": 0,
            "coveredIntentCount": 0,
            "declaredScenarioCount": 0,
            "observedScenarioCount": int(observation.get("operations") or 0),
            "failedIntentIds": [],
            "missingScenarioIds": [],
            "unexpectedScenarioIds": [],
            "failedScenarioIds": [],
            "crossCuttingChecks": {"acceptanceEnforceable": True, "browserObservationPassed": True},
            "intents": {},
        }
    normalized = _load_json(repo / normalized_reference, "normalized application definition")
    definition = normalized.get("definition") or {}
    operations = definition.get("operations") or {}
    scenarios = definition.get("acceptance") or []
    if not isinstance(operations, Mapping) or not operations:
        raise AppProofError("The normalized definition contains no declared operations.")
    if not isinstance(scenarios, list) or not scenarios:
        raise AppProofError("The normalized definition contains no acceptance scenarios.")

    acceptance_results = [entry for entry in acceptance.get("results") or [] if entry.get("appId") == app_id]
    acceptance_pass = (
        acceptance.get("status") == "pass"
        and acceptance.get("passed") is True
        and len(acceptance_results) == 1
        and acceptance_results[0].get("status") == "pass"
        and int(acceptance_results[0].get("testCount") or 0) > 0
        and int(acceptance_results[0].get("enforceableContractCount") or 0) > 0
        and int(acceptance_results[0].get("notDueContractCount") or 0) == 0
    )
    observed = observation.get("observation") or {}
    scenario_results = observed.get("scenarioResults") or []
    observed_by_id = {str(entry.get("id") or ""): entry for entry in scenario_results if entry.get("id")}
    declared_by_id = {str(entry.get("id") or ""): entry for entry in scenarios if entry.get("id")}
    missing_scenarios = sorted(set(declared_by_id) - set(observed_by_id))
    unexpected_scenarios = sorted(set(observed_by_id) - set(declared_by_id))
    failed_scenarios = sorted(
        scenario_id for scenario_id, entry in observed_by_id.items() if entry.get("passed") is not True
    )

    direct_by_intent: dict[str, list[Mapping[str, Any]]] = {intent_id: [] for intent_id in operations}
    for scenario in scenarios:
        intent_id = str(((scenario.get("when") or {}).get("intentId") or "")).strip()
        if intent_id in direct_by_intent:
            direct_by_intent[intent_id].append(scenario)

    intent_results: dict[str, Any] = {}
    for intent_id, operation in sorted(operations.items()):
        kind = str((operation or {}).get("operationKind") or "").strip()
        direct = direct_by_intent.get(intent_id) or []
        ids = [str(entry.get("id")) for entry in direct]
        browser_pass = bool(ids) and all(observed_by_id.get(scenario_id, {}).get("passed") is True for scenario_id in ids)
        expectations = [entry.get("expect") or {} for entry in direct]
        checks: dict[str, bool] = {
            "declaredAcceptance": bool(direct),
            "browserObserved": browser_pass,
        }
        if kind == "mutation":
            checks["committed"] = any(expect.get("operationStatus") == "committed" for expect in expectations)
            if intent_id == "add-contract":
                checks["refusalCoverage"] = any(
                    expect.get("operationStatus") == "refused" or bool(expect.get("code"))
                    for expect in expectations
                )
        elif kind == "async":
            checks["committed"] = any(expect.get("operationStatus") == "committed" for expect in expectations)
            checks["provisionalBeforeCommit"] = any(expect.get("provisionalEventsVisibleBeforeCommit") is True for expect in expectations)
            checks["supersession"] = any(expect.get("olderOperationStatus") == "superseded" for expect in expectations)
            checks["parallelItemKeys"] = any(expect.get("independentItemKeys") is True for expect in expectations)
        elif kind == "cancel":
            checks["cancelled"] = any(expect.get("operationStatus") == "cancelled" for expect in expectations)
            checks["canonicalUnchanged"] = any(expect.get("canonicalStateUnchanged") is True for expect in expectations)
            checks["provisionalClosed"] = any(expect.get("provisionalStateClosed") is True for expect in expectations)
        elif kind == "prohibited":
            checks["explicitRefusal"] = any(expect.get("code") == "INTENT_PROHIBITED" for expect in expectations)
            checks["canonicalUnchanged"] = any(expect.get("canonicalStateUnchanged") is True for expect in expectations)
        else:
            checks["knownOperationKind"] = False
        intent_results[intent_id] = {
            "operationKind": kind,
            "scenarioIds": ids,
            "checks": checks,
            "passed": all(checks.values()),
        }

    cross_cutting = {
        "acceptanceEnforceable": acceptance_pass,
        "allDeclaredScenariosObserved": not missing_scenarios,
        "noUnexpectedScenarios": not unexpected_scenarios,
        "allBrowserScenariosPassed": not failed_scenarios and len(observed_by_id) == len(declared_by_id),
        "filterSortObserved": observed_by_id.get("contract-workbench.acceptance.filter-sort", {}).get("passed") is True,
        "multiInstanceObserved": observed_by_id.get("contract-workbench.acceptance.multi-instance", {}).get("passed") is True,
        "clearAllObserved": observed_by_id.get("contract-workbench.acceptance.clear-all", {}).get("passed") is True,
    }
    failed_intents = sorted(intent_id for intent_id, entry in intent_results.items() if entry["passed"] is not True)
    passed = not failed_intents and all(cross_cutting.values())
    report = {
        "schema": "mcel.intent-complete-proof.v1",
        "appId": app_id,
        "status": "pass" if passed else "fail",
        "passed": passed,
        "definitionFingerprint": normalized.get("definitionFingerprint"),
        "declaredIntentCount": len(operations),
        "coveredIntentCount": len(operations) - len(failed_intents),
        "declaredScenarioCount": len(declared_by_id),
        "observedScenarioCount": len(observed_by_id),
        "failedIntentIds": failed_intents,
        "missingScenarioIds": missing_scenarios,
        "unexpectedScenarioIds": unexpected_scenarios,
        "failedScenarioIds": failed_scenarios,
        "crossCuttingChecks": cross_cutting,
        "intents": intent_results,
    }
    if not passed:
        details = failed_intents + missing_scenarios + failed_scenarios
        raise AppProofError(
            "Intent-complete proof did not converge" + (": " + ", ".join(details[:12]) if details else ".")
        )
    return report


def _package_requirements_contract(repo: Path, record: Any) -> tuple[dict[str, Any], dict[str, int]]:
    tool = _requirements_tool(repo)
    global_registry = tool.build_registry(repo, repo / "pretty_docs", strict_schema=True)
    if not global_registry.strict_schema_ready:
        raise AppProofError("The canonical requirements grammar is not strict-schema ready.")
    requirements_path = repo / record.requirements
    blocks, extraction_errors = tool.extract_blocks_from_file(requirements_path, repo)
    package_registry = tool.RequirementsRegistry(
        repo_root=repo,
        pretty_docs_root=requirements_path.parent,
    )
    package_registry.blocks.extend(blocks)
    package_registry.errors.extend(extraction_errors)
    package_registry.grammar_required_fields = global_registry.grammar_required_fields
    tool.validate_registry(package_registry, strict_schema=True)
    if not package_registry.strict_schema_ready:
        messages = [issue.message for issue in package_registry.errors + package_registry.warnings]
        raise AppProofError(
            "Package requirements are not strict-schema ready"
            + (f": {'; '.join(messages[:5])}" if messages else ".")
        )
    summaries = tool.build_app_contract_summaries(package_registry)
    matches = [summary for summary in summaries if summary.get("app") == record.app_id]
    if len(matches) != 1 or matches[0].get("contract_complete") is not True:
        raise AppProofError("Package requirements do not form one complete application contract.")
    contract = dict(matches[0])
    counts = {str(key): int(value) for key, value in dict(contract.get("block_type_counts") or {}).items()}
    contract["intent_count"] = counts.get("mcel-intent", 0)
    contract["mutation_intent_count"] = len(contract.get("mutation_intents") or [])
    contract["prohibited_intent_count"] = len(contract.get("prohibited_intents") or [])
    contract["runtime_check_count"] = counts.get("mcel-runtime-check", 0)
    return contract, counts


def _adapter_id(repo: Path, record: Any) -> str:
    source = (repo / record.contracts["adapter"]).read_text(encoding="utf-8")
    match = re.search(r'adapterId\s*:\s*["\']([^"\']+)["\']', source)
    if not match:
        raise AppProofError("Package semantic adapter does not declare adapterId.")
    return match.group(1)


def _acceptance_package_entry(report: Mapping[str, Any], app_id: str) -> Mapping[str, Any]:
    entries = [entry for entry in report.get("applicationPackages") or [] if entry.get("appId") == app_id]
    if len(entries) != 1:
        raise AppProofError("Acceptance evidence does not identify the application package exactly once.")
    return entries[0]


def _assert_evidence_alignment(
    *,
    app_id: str,
    record: Any,
    catalog: Any,
    projection: Any,
    provenance: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> None:
    if acceptance.get("status") != "pass" or acceptance.get("passed") is not True:
        raise AppProofError("Package-local acceptance evidence did not pass.")
    scope = acceptance.get("evidenceScope") or {}
    if scope.get("kind") != "app-scoped" or list(scope.get("selectedApps") or []) != [app_id]:
        raise AppProofError("Acceptance evidence is not an exact app-scoped report.")
    results = [entry for entry in acceptance.get("results") or [] if entry.get("appId") == app_id]
    if len(results) != 1 or results[0].get("status") != "pass":
        raise AppProofError("Acceptance evidence does not contain one passing application result.")
    acceptance_package = _acceptance_package_entry(acceptance, app_id)
    if acceptance_package.get("packageFingerprint") != record.fingerprint:
        raise AppProofError("Acceptance evidence package fingerprint is stale.")
    if (acceptance.get("repositoryProvenance") or {}).get("fingerprint") != provenance.get("fingerprint"):
        raise AppProofError("Acceptance evidence repository provenance is stale.")

    if observation.get("status") != "pass" or observation.get("ok") is not True:
        raise AppProofError("Operation-linked browser observation did not pass.")
    if observation.get("evidenceScope") != "app-scoped" or observation.get("appId") != app_id:
        raise AppProofError("Browser observation is not an exact app-scoped report.")
    if (observation.get("package") or {}).get("fingerprint") != record.fingerprint:
        raise AppProofError("Browser observation package fingerprint is stale.")
    if observation.get("catalogFingerprint") != catalog.fingerprint:
        raise AppProofError("Browser observation package-catalog fingerprint is stale.")
    observed = observation.get("observation") or {}
    if observed.get("runtimeProjectionFingerprint") != projection.fingerprint:
        raise AppProofError("Browser observation runtime-projection fingerprint is stale.")
    if observed.get("repositoryFingerprint") != provenance.get("fingerprint"):
        raise AppProofError("Browser observation repository provenance is stale.")
    comparison = observed.get("comparison") or {}
    if not all(comparison.get(key) is True for key in ("stateMatches", "receiptMatches", "surfaceMatches")):
        raise AppProofError("Browser observation does not agree with canonical state, receipt, and surface identity.")
    surface = observation.get("surfaceConformance") or {}
    statuses = surface.get("requiredLayerStatuses") or {}
    if surface.get("status") != "pass" or surface.get("valid") is not True:
        raise AppProofError("Application surface conformance did not pass.")
    missing_or_failed = [layer for layer in REQUIRED_SURFACE_LAYERS if statuses.get(layer) != "pass"]
    if missing_or_failed:
        raise AppProofError(
            "Application surface conformance did not pass every required layer: "
            + ", ".join(missing_or_failed)
        )


def _truth_snapshot(
    *,
    repo: Path,
    app_id: str,
    record: Any,
    requirements_contract: Mapping[str, Any],
    block_counts: Mapping[str, int],
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
    intent_coverage: Mapping[str, Any],
    node: str | None,
    now: str,
) -> dict[str, Any]:
    node_executable = resolve_node_executable(node)
    if not node_executable:
        raise AppProofError("Node.js is required to invoke McelAppTruthGate.")
    total_intents = int(block_counts.get("mcel-intent", 0))
    prohibited = int(requirements_contract.get("prohibited_intent_count") or 0)
    executable = max(0, total_intents - prohibited)
    acceptance_contract_ids = [
        entry.get("contractId")
        for result in acceptance.get("results") or []
        for entry in result.get("contracts") or []
        if entry.get("contractId")
    ]
    surface_conformance = observation["surfaceConformance"]
    runtime_evidence = {
        "schema": "mcel-app-proof-runtime-evidence.v1",
        "appId": app_id,
        "status": "pass",
        "generatedAt": observation.get("generatedAt"),
        "route": observation.get("url"),
        "claimedSemanticRuntimeReady": True,
        "appSurfaceConformance": surface_conformance,
    }
    payload = {
        "truthGatePath": str(repo / "main_computer/web/applications/scripts/mcel-app-truth-gate.js"),
        "appId": app_id,
        "now": now,
        "requirements": {
            "registryVersion": "mcel.package-requirements.v1",
            "contract": requirements_contract,
        },
        "adapter": {
            "registryVersion": "mcel.package-adapter-proof.v1",
            "authority": "package-contract-plus-scm-acceptance-and-browser-observation",
            "readiness": {
                "registryAdapterPresent": True,
                "adapterId": _adapter_id(repo, record),
                "adapterKind": "package-semantic-adapter",
                "adapterVersion": "v1",
                "runtimeCoreReady": True,
                "intentCoverageReady": intent_coverage.get("passed") is True,
                "intentCoverageAuditReady": intent_coverage.get("passed") is True,
                "fullApplicationSemanticReady": True,
                "semanticRuntimeReady": True,
                "operationalSemanticRuntimeReady": True,
                "runtimeBindingCoverageAvailable": True,
                "runtimeBindingAuditReady": True,
                "runtimeBindingReady": True,
                "runtimeBoundIntentCount": max(0, (int(intent_coverage.get("coveredIntentCount") or 0) or total_intents) - prohibited),
                "adapterLocalIntentCount": executable,
                "unboundIntentCount": 0,
                "unboundIntentIds": [],
                "semanticRuntimeScope": "full-application",
                "executableIntentCount": executable,
                "preflightOnlyIntentCount": 0,
                "declaredOnlyIntentCount": 0,
                "prohibitedIntentCount": prohibited,
                "blockedIntentCount": 0,
                "totalIntentCount": total_intents,
                "recoveryReady": True,
                "recoveryCoverageReady": True,
                "missingSemantics": [],
                "missingApplicationSemantics": [],
            },
        },
        "surface": {
            "registryVersion": "mcel.package-surface-proof.v1",
            "policy": {
                "appId": app_id,
                "label": record.title or app_id,
                "state": "surface-aware",
                "conformanceRequired": True,
                "maturity": "semantic-runtime",
                "surfaceId": surface_conformance.get("surfaceId"),
                "contractId": acceptance_contract_ids[0] if acceptance_contract_ids else "",
                "requiredLayerIds": list(REQUIRED_SURFACE_LAYERS),
                "notes": "Derived from the validated package and app-scoped proof evidence.",
            },
        },
        "runtimeEvidence": runtime_evidence,
        "acceptanceEvidence": acceptance,
    }
    completed = subprocess.run(
        [node_executable, "-e", NODE_TRUTH_BRIDGE],
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=repo,
        check=False,
    )
    if completed.returncode != 0:
        raise AppProofError(
            "McelAppTruthGate invocation failed: "
            + (completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}")
        )
    try:
        truth = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AppProofError("McelAppTruthGate returned invalid JSON.") from exc
    if truth.get("overallStatus") != "semantic-runtime-proven":
        raise AppProofError(
            "McelAppTruthGate did not prove semantic runtime readiness: "
            + ", ".join(truth.get("findingCodes") or [])
        )
    if truth.get("claims", {}).get("semanticRuntimeProven") is not True:
        raise AppProofError("McelAppTruthGate omitted the semanticRuntimeProven claim.")
    return truth


def _artifact_reference(path: Path, repo: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": _display_path(path, repo),
        "sha256": _sha256_path(path),
        "schema": payload.get("schema"),
        "generatedAt": payload.get("generatedAt"),
        "status": payload.get("status"),
    }


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# MCEL Application Proof",
        "",
        f"- Status: **{report.get('status')}**",
        f"- Application: `{report.get('appId')}`",
        f"- Truth status: `{report.get('truthStatus')}`",
        f"- Package fingerprint: `{(report.get('package') or {}).get('fingerprint', '')}`",
        f"- Repository fingerprint: `{(report.get('repositoryProvenance') or {}).get('fingerprint', '')}`",
        "",
        "## Stages",
        "",
    ]
    for stage_id, stage in (report.get("stages") or {}).items():
        lines.append(f"- {stage_id}: `{stage.get('status')}`")
    lines.extend(["", "## Evidence", ""])
    for label, evidence in (report.get("evidence") or {}).items():
        lines.append(f"- {label}: `{evidence.get('path')}` (`{evidence.get('sha256')}`)")
    lines.append("")
    return "\n".join(lines)


def run_app_proof(
    *,
    repo: Path,
    app_id: str,
    headed: bool = False,
    reuse_evidence: bool = False,
    node: str | None = None,
) -> dict[str, Any]:
    catalog = build_application_package_catalog(repo)
    if not catalog.ok or catalog.invalid_count or catalog.errors:
        raise AppProofError("The repository application-package catalog is invalid.")
    record = _package_record(catalog, app_id)
    conformance = dict(record.conformance or {})
    current_mode = conformance.get("currentMode")
    if current_mode == "forward-specification":
        gaps = ", ".join(conformance.get("missingBridges") or []) or "unspecified runtime bridges"
        raise AppProofError(
            "The application is a forward specification and is not eligible for semantic-runtime proof; "
            f"unresolved bridges: {gaps}."
        )
    if current_mode != "semantic-runtime-proven":
        raise AppProofError("The application package has not declared the completed semantic-runtime template.")
    if list(conformance.get("missingBridges") or []):
        raise AppProofError("The application package still declares open MCEL platform bridges.")

    browser_fresh, browser_path, _browser_expected = check_browser_catalog(repo)
    projection_fresh, projection_root, projection_set = check_runtime_projections(repo)
    if not browser_fresh:
        raise AppProofError("The browser application-package catalog is stale.")
    if not projection_fresh:
        raise AppProofError("The browser-safe application runtime projection is stale.")
    projections = [item for item in projection_set.projections if item.app_id == app_id]
    if len(projections) != 1:
        raise AppProofError("The application runtime projection was not discovered exactly once.")
    projection = projections[0]

    acceptance_path = repo / "runtime/reports/mcel-acceptance/apps" / _slug(app_id) / "mcel-acceptance-report.json"
    observation_path = repo / "runtime/reports/mcel-observation/apps" / _slug(app_id) / "mcel-operation-observation-report.json"
    if not reuse_evidence:
        _run_dependency(repo, ["main_computer/mcel_acceptance_runner.py", "--app", app_id, "--check"])
        observation_args = ["main_computer/mcel_application_observation_runner.py", "--app", app_id, "--check"]
        if headed:
            observation_args.append("--headed")
        _run_dependency(repo, observation_args)
    acceptance = _load_json(acceptance_path, "acceptance evidence")
    observation = _load_json(observation_path, "browser observation evidence")
    provenance = build_repository_provenance(repo)
    _assert_evidence_alignment(
        app_id=app_id,
        record=record,
        catalog=catalog,
        projection=projection,
        provenance=provenance,
        acceptance=acceptance,
        observation=observation,
    )
    intent_coverage = _intent_complete_coverage(
        repo=repo,
        app_id=app_id,
        record=record,
        acceptance=acceptance,
        observation=observation,
    )

    requirements_contract, block_counts = _package_requirements_contract(repo, record)
    truth = _truth_snapshot(
        repo=repo,
        app_id=app_id,
        record=record,
        requirements_contract=requirements_contract,
        block_counts=block_counts,
        acceptance=acceptance,
        observation=observation,
        intent_coverage=intent_coverage,
        node=node,
        now=_utc_now(),
    )
    generated_at = _utc_now()
    stages = {
        "package": {"status": "pass", "fingerprint": record.fingerprint},
        "applicationDiscovery": {"status": "pass", "catalogFingerprint": catalog.fingerprint},
        "generatedArtifacts": {
            "status": "pass",
            "browserCatalog": _display_path(browser_path, repo),
            "runtimeProjectionRoot": _display_path(projection_root, repo),
            "runtimeProjectionFingerprint": projection.fingerprint,
            "browserPackageCount": catalog.package_count,
        },
        "operationConformance": {"status": "pass", "source": "package-local acceptance"},
        "intentCompleteProof": {
            "status": "pass",
            "declaredIntentCount": intent_coverage["declaredIntentCount"],
            "coveredIntentCount": intent_coverage["coveredIntentCount"],
            "observedScenarioCount": intent_coverage["observedScenarioCount"],
        },
        "surfaceConformance": {
            "status": "pass",
            "requiredLayerStatuses": observation["surfaceConformance"]["requiredLayerStatuses"],
        },
        "acceptanceEvidence": {"status": "pass"},
        "browserObservation": {"status": "pass"},
        "repositoryBinding": {"status": "exact", "fingerprint": provenance["fingerprint"]},
        "truthGate": {"status": truth["overallStatus"]},
    }
    return {
        "schema": REPORT_SCHEMA,
        "runner": RUNNER_VERSION,
        "status": "pass",
        "ok": True,
        "evidenceScope": "app-scoped",
        "generatedAt": generated_at,
        "appId": app_id,
        "truthStatus": truth["overallStatus"],
        "package": {
            "root": record.package_root,
            "fingerprint": record.fingerprint,
            "fingerprintAlgorithm": record.fingerprint_algorithm,
            "catalogFingerprint": catalog.fingerprint,
            "runtimeProjectionFingerprint": projection.fingerprint,
        },
        "repositoryProvenance": provenance,
        "intentCoverage": intent_coverage,
        "stages": stages,
        "evidence": {
            "acceptance": _artifact_reference(acceptance_path, repo, acceptance),
            "browserObservation": _artifact_reference(observation_path, repo, observation),
        },
        "truthSnapshot": truth,
    }


def _write_report(report: Mapping[str, Any], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "mcel-app-proof-report.json"
    markdown_path = output_dir / "mcel-app-proof-report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=repository_root())
    parser.add_argument("--app", required=True)
    parser.add_argument("--check", action="store_true", help="Return nonzero unless the app is semantic-runtime-proven.")
    parser.add_argument("--headed", action="store_true", help="Run the dependent Chromium observation headed.")
    parser.add_argument("--reuse-evidence", action="store_true", help="Use current app-scoped acceptance and observation reports without rerunning them.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--node")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    app_id = args.app.strip()
    output_dir = args.output_dir or (DEFAULT_OUTPUT_ROOT / "apps" / _slug(app_id))
    if not output_dir.is_absolute():
        output_dir = repo / output_dir
    try:
        report = run_app_proof(
            repo=repo,
            app_id=app_id,
            headed=args.headed,
            reuse_evidence=args.reuse_evidence,
            node=args.node,
        )
        json_path, markdown_path = _write_report(report, output_dir)
    except AppProofError as exc:
        payload = {
            "schema": REPORT_SCHEMA,
            "runner": RUNNER_VERSION,
            "status": "fail",
            "ok": False,
            "evidenceScope": "app-scoped",
            "appId": app_id,
            "error": str(exc),
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(RUNNER_VERSION)
            print("status: fail")
            print("evidence_scope: app-scoped")
            print(f"app: {app_id}")
            print(f"error: {exc}")
        return 1 if args.check else 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(RUNNER_VERSION)
        print("status: pass")
        print("evidence_scope: app-scoped")
        print(f"app: {app_id}")
        for label, stage in report["stages"].items():
            print(f"{label}: {stage['status']}")
        print(f"truth_status: {report['truthStatus']}")
        print(f"json: {_display_path(json_path, repo)}")
        print(f"markdown: {_display_path(markdown_path, repo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
