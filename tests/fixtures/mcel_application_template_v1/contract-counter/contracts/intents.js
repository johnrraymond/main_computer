export const ContractCounterIntents = Object.freeze({
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
