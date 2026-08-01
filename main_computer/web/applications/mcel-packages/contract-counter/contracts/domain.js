export const ContractCounterDomain = Object.freeze({
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
