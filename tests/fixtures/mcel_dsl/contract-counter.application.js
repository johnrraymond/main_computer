"use strict";

const mcel = require("@mcel/app");

module.exports = mcel.defineApp(
  {
    id: "contract-counter",
    title: "Contract Counter",
    semanticVersion: "1",
    targetTruthStatus: "semantic-runtime-proven",
    requirements: "mcel_apps/contract-counter/requirements.md"
  },
  (dsl) => {
    const {field, state, intent, invariant, surface, layout, prove} = dsl;

    const count = state.canonical(
      "count",
      field.integer().minimum(0),
      {initial: 0}
    );
    const revision = state.canonical(
      "revision",
      field.integer().minimum(0),
      {initial: 0}
    );

    const countNonnegative = invariant("count-nonnegative", {
      check: ({read, expr}) => expr.greaterThanOrEqual(read(count), 0)
    });
    const revisionNonnegative = invariant("revision-nonnegative", {
      check: ({read, expr}) => expr.greaterThanOrEqual(read(revision), 0)
    });

    const increment = intent.mutation("increment", {
      reads: [count, revision],
      invariants: [countNonnegative, revisionNonnegative],
      change: () => [count.increment(1), revision.increment(1)]
    });

    const reset = intent.mutation("reset", {
      reads: [count, revision],
      invariants: [countNonnegative, revisionNonnegative],
      change: () => [count.set(0), revision.increment(1)]
    });

    const directSet = intent.prohibited("direct-set", {
      sourceName: "directSet",
      reasonCode: "MCEL_CANONICAL_ASSIGNMENT_BYPASSES_OPERATION_AUTHORITY"
    });

    const primary = surface.define("primary", {
      id: "contract-counter.primary",
      sourceName: "ContractCounterSurface",
      root: surface.region("shell", {
        children: [
          surface.text("value", {
            id: "counter.value",
            value: count
          }),
          surface.action("increment", {
            id: "counter.increment",
            intent: increment
          }),
          surface.action("reset", {
            id: "counter.reset",
            intent: reset
          }),
          surface.receipt("receipt", {
            id: "counter.receipt"
          })
        ]
      })
    });

    const primaryLayout = layout.define("primary", {
      id: "contract-counter.primary",
      surface: primary,
      orderedChildren: [
        primary.node("value"),
        primary.node("increment"),
        primary.node("reset"),
        primary.node("receipt")
      ]
    });

    const directSetScenario = prove
      .scenario("contract-counter.direct-set", {intent: directSet})
      .expect(prove.receiptDisposition("refused", "INTENT_PROHIBITED"));

    const incrementScenario = prove
      .scenario("contract-counter.increment", {intent: increment})
      .expect(
        prove.canonical(count).equals(1),
        prove.canonical(revision).equals(1),
        prove.visible(primary.node("value")).exists()
      );

    const resetScenario = prove
      .scenario("contract-counter.reset", {intent: reset})
      .expect(prove.canonical(count).equals(0));

    const staleScenario = prove
      .scenario("contract-counter.stale", {intent: increment})
      .expect(prove.receiptDisposition("refused", "REVISION_STALE"));

    return {
      models: [],
      states: [count, revision],
      derivations: [],
      capabilities: [],
      invariants: [countNonnegative, revisionNonnegative],
      intents: [increment, reset, directSet],
      surfaces: [primary],
      layouts: [primaryLayout],
      scenarios: [directSetScenario, incrementScenario, resetScenario, staleScenario],
      proof: prove.config({
        invariants: [countNonnegative, revisionNonnegative],
        requiredAuthorities: [
          "canonical-state",
          "visible-surface",
          "operation-receipt"
        ],
        targetTruthStatus: "semantic-runtime-proven"
      })
    };
  }
);
