"""Counter explicit-package candidate projection.

The Counter app supplies generated-contract content and compatibility checks.
Shared explicit-package projection mechanics live in
``main_computer.mcel_explicit_package_candidate_projection``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_counter_compatibility import DEFAULT_DSL_SOURCE, DEFAULT_FIXTURE_IR
from main_computer.mcel_counter_legacy_importer import DEFAULT_COUNTER_ROOT
from main_computer.mcel_counter_reference_fixture_profile import (
    GENERATED_CONTRACTS,
    build_counter_projection_profile,
)
from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_explicit_package_candidate_projection import (
    ExplicitPackageCandidateProjectionReport,
    ExplicitPackageProjectionProfile,
    project_explicit_package_candidate,
)


CounterCandidateProjectionReport = ExplicitPackageCandidateProjectionReport


def counter_explicit_package_projection_profile() -> ExplicitPackageProjectionProfile:
    return build_counter_projection_profile(generate_contracts=generate_counter_contracts)


def project_counter_candidate(
    *,
    dsl_source_path: Path = DEFAULT_DSL_SOURCE,
    fixture_ir_path: Path = DEFAULT_FIXTURE_IR,
    live_package_root: Path = DEFAULT_COUNTER_ROOT,
    candidate_root: Path = DEFAULT_CANDIDATE_ROOT,
    write_candidate: bool = False,
) -> CounterCandidateProjectionReport:
    return project_explicit_package_candidate(
        counter_explicit_package_projection_profile(),
        dsl_source_path=dsl_source_path,
        fixture_ir_path=fixture_ir_path,
        live_package_root=live_package_root,
        candidate_root=candidate_root,
        write_candidate=write_candidate,
    )


def generate_counter_contracts(ir: Mapping[str, Any]) -> dict[str, bytes]:
    _assert_supported_counter_ir(ir)
    files = {
        "contracts/domain.js": _DOMAIN,
        "contracts/intents.js": _INTENTS,
        "contracts/adapter.js": _ADAPTER,
        "contracts/surface.js": _SURFACE,
        "contracts/layout.js": _LAYOUT,
        "contracts/acceptance.js": _ACCEPTANCE,
        "contracts/observation.js": _OBSERVATION,
    }
    return {path: text.encode("utf-8") for path, text in files.items()}


def _assert_supported_counter_ir(ir: Mapping[str, Any]) -> None:
    app = ir.get("application") or {}
    if app.get("appId") != "contract-counter":
        raise ValueError("Wave 4 only supports appId contract-counter.")
    states = {str(item.get("id")): item for item in ir.get("states") or [] if isinstance(item, Mapping)}
    if set(states) != {"state:count", "state:revision"} or any(item.get("authority") != "canonical" for item in states.values()):
        raise ValueError("Counter projection requires canonical count and revision states.")
    intents = {str(item.get("id")): item for item in ir.get("intents") or [] if isinstance(item, Mapping)}
    if set(intents) != {"intent:increment", "intent:reset", "intent:direct-set"}:
        raise ValueError("Counter projection requires increment, reset, and direct-set intents.")
    if intents["intent:direct-set"].get("operationKind") != "prohibited":
        raise ValueError("Counter direct-set must remain prohibited.")
    expected_effects = {
        "effect:increment.count-write", "effect:increment.revision-write",
        "effect:reset.count-write", "effect:reset.revision-write",
    }
    effects = {str(item.get("id")) for item in ir.get("effects") or [] if isinstance(item, Mapping)}
    if effects != expected_effects:
        raise ValueError("Counter projection requires the four canonical-write effects.")
    scenarios = {str(item.get("id")) for item in ir.get("scenarios") or [] if isinstance(item, Mapping)}
    if scenarios != {"scenario:contract-counter.increment", "scenario:contract-counter.reset", "scenario:contract-counter.stale", "scenario:contract-counter.direct-set"}:
        raise ValueError("Counter projection requires all four compatibility scenarios.")




_DOMAIN = '''export const ContractCounterDomain = Object.freeze({
  schema: "mcel.application-domain.v1",
  appId: "contract-counter",
  initialState: Object.freeze({ count: 0, revision: 0 }),
  invariantReads: Object.freeze(["state.count", "state.revision"]),
  invariants: Object.freeze([
    Object.freeze({
      id: "contract-counter.invariant.count-nonnegative",
      check(state) {
        return Number.isInteger(state?.count) && state.count >= 0;
      }
    }),
    Object.freeze({
      id: "contract-counter.invariant.revision-nonnegative",
      check(state) {
        return Number.isInteger(state?.revision) && state.revision >= 0;
      }
    })
  ])
});
'''
_INTENTS = '''export const ContractCounterIntents = Object.freeze({
  increment: Object.freeze({
    id: "increment",
    kind: "mutation",
    risk: "local-state",
    reads: Object.freeze(["state.count", "state.revision"]),
    writes: Object.freeze(["state.count", "state.revision"]),
    effects: Object.freeze(["count plus one", "revision plus one"])
  }),
  reset: Object.freeze({
    id: "reset",
    kind: "mutation",
    risk: "local-state",
    reads: Object.freeze(["state.count", "state.revision"]),
    writes: Object.freeze(["state.count", "state.revision"]),
    effects: Object.freeze(["count zero", "revision plus one"])
  }),
  directSet: Object.freeze({
    id: "direct-set",
    kind: "prohibited",
    risk: "prohibited",
    reason: "Arbitrary canonical assignment bypasses the MCEL application operation authority."
  })
});
'''
_ADAPTER = '''export const ContractCounterAdapter = Object.freeze({
  schema: "mcel.semantic-adapter.v1",
  appId: "contract-counter",
  adapterId: "contract-counter.adapter.v1",
  currentRuntimeStatus: "scm-controlled",
  targetRuntimeStatus: "fullApplicationSemanticReady",

  preflight({ intentId, input, state }) {
    if (intentId === "direct-set") {
      return Object.freeze({ ok: false, code: "INTENT_PROHIBITED" });
    }
    if (!Object.prototype.hasOwnProperty.call({ increment: true, reset: true }, intentId)) {
      return Object.freeze({ ok: false, code: "INTENT_UNKNOWN" });
    }
    if (input?.expectedRevision !== state?.revision) {
      return Object.freeze({ ok: false, code: "REVISION_STALE" });
    }
    return Object.freeze({ ok: true });
  },

  transition({ intentId, state }) {
    if (intentId === "increment") {
      return Object.freeze({ count: state.count + 1, revision: state.revision + 1 });
    }
    if (intentId === "reset") {
      return Object.freeze({ count: 0, revision: state.revision + 1 });
    }
    throw new Error(`Unsupported authorized intent: ${intentId}`);
  },

  validateEffects({ intentId, before, after }) {
    if (intentId === "increment") {
      return after.count === before.count + 1 && after.revision === before.revision + 1;
    }
    if (intentId === "reset") {
      return after.count === 0 && after.revision === before.revision + 1;
    }
    return false;
  }
});
'''
_SURFACE = '''export const ContractCounterSurface = Object.freeze({
  schema: "mcel.semantic-surface-ir.v1",
  appId: "contract-counter",
  surfaceId: "contract-counter.surface.primary",
  regions: Object.freeze([
    Object.freeze({ id: "contract-counter.region.shell", role: "application" }),
    Object.freeze({ id: "contract-counter.region.value", role: "result" }),
    Object.freeze({ id: "contract-counter.region.controls", role: "toolbar" }),
    Object.freeze({ id: "contract-counter.region.evidence", role: "status" })
  ]),
  nodes: Object.freeze([
    Object.freeze({ id: "contract-counter.value", kind: "state-value", statePath: "count", regionId: "contract-counter.region.value" }),
    Object.freeze({ id: "contract-counter.increment-control", kind: "control", intentId: "increment", regionId: "contract-counter.region.controls" }),
    Object.freeze({ id: "contract-counter.reset-control", kind: "control", intentId: "reset", regionId: "contract-counter.region.controls" }),
    Object.freeze({ id: "contract-counter.latest-receipt", kind: "operation-evidence", regionId: "contract-counter.region.evidence" })
  ])
});
'''
_LAYOUT = '''export const ContractCounterLayout = Object.freeze({
  schema: "mcel.layout-grammar.v1",
  surfaceId: "contract-counter.surface.primary",
  regions: Object.freeze({
    "contract-counter.region.shell": Object.freeze({ direction: "column", alignment: "center", gap: "medium", padding: "large", minInlineSize: 240, maxInlineSize: 640 }),
    "contract-counter.region.value": Object.freeze({ inlineSize: "fill", blockSize: "content", textAlignment: "center" }),
    "contract-counter.region.controls": Object.freeze({ direction: "row", wrap: true, alignment: "center", gap: "small" }),
    "contract-counter.region.evidence": Object.freeze({ inlineSize: "fill", blockSize: "content", scrollOwner: false })
  }),
  constraints: Object.freeze([
    Object.freeze({ id: "contract-counter.layout.value-before-controls", relation: "before", first: "contract-counter.region.value", second: "contract-counter.region.controls" }),
    Object.freeze({ id: "contract-counter.layout.controls-before-evidence", relation: "before", first: "contract-counter.region.controls", second: "contract-counter.region.evidence" }),
    Object.freeze({ id: "contract-counter.layout.controls-usable", relation: "minimum-control-size", target: "contract-counter.region.controls", inline: 44, block: 44 })
  ])
});
'''
_ACCEPTANCE = '''export const ContractCounterAcceptance = Object.freeze({
  schema: "mcel.acceptance-suite.v1",
  appId: "contract-counter",
  currentStatus: "package-local-discovery-live",
  scenarios: Object.freeze([
    Object.freeze({ id: "contract-counter.acceptance.increment", given: Object.freeze({ count: 0, revision: 0 }), when: Object.freeze({ intentId: "increment", expectedRevision: 0 }), expect: Object.freeze({ count: 1, revision: 1, operationStatus: "committed" }) }),
    Object.freeze({ id: "contract-counter.acceptance.reset", given: Object.freeze({ count: 4, revision: 4 }), when: Object.freeze({ intentId: "reset", expectedRevision: 4 }), expect: Object.freeze({ count: 0, revision: 5, operationStatus: "committed" }) }),
    Object.freeze({ id: "contract-counter.acceptance.stale", given: Object.freeze({ count: 2, revision: 2 }), when: Object.freeze({ intentId: "increment", expectedRevision: 1 }), expect: Object.freeze({ code: "REVISION_STALE", canonicalStateUnchanged: true }) }),
    Object.freeze({ id: "contract-counter.acceptance.direct-set", given: Object.freeze({ count: 2, revision: 2 }), when: Object.freeze({ intentId: "direct-set", expectedRevision: 2 }), expect: Object.freeze({ code: "INTENT_PROHIBITED", canonicalStateUnchanged: true }) })
  ])
});
'''
_OBSERVATION = '''export const ContractCounterObservation = Object.freeze({
  schema: "mcel.observation-contract.v1",
  appId: "contract-counter",
  currentStatus: "operation-linked",
  observations: Object.freeze([
    Object.freeze({
      id: "contract-counter.observe.value",
      source: "browser-dom",
      semanticNodeId: "contract-counter.value",
      property: "textContent",
      compareToStatePath: "count",
      normalization: "string"
    }),
    Object.freeze({
      id: "contract-counter.observe.value-visible",
      source: "browser-geometry",
      semanticNodeId: "contract-counter.value",
      property: "visible",
      expected: true,
      normalization: "boolean"
    }),
    Object.freeze({
      id: "contract-counter.observe.receipt",
      source: "browser-dom",
      semanticNodeId: "contract-counter.latest-receipt",
      property: "textContent",
      compareToOperationReceipt: true
    })
  ])
});
'''
