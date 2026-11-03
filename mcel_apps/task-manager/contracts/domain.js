export const TaskManagerDomain = Object.freeze({
  schema: "mcel.application-domain.v1",
  appId: "task-manager",
  initialState: Object.freeze({ count: 0, revision: 0 }),
  invariantReads: Object.freeze(["state.count", "state.revision"]),
  invariants: Object.freeze([
    Object.freeze({
      id: "task-manager.invariant.count-nonnegative",
      check(state) {
        return Number.isInteger(state?.count) && state.count >= 0;
      }
    }),
    Object.freeze({
      id: "task-manager.invariant.revision-nonnegative",
      check(state) {
        return Number.isInteger(state?.revision) && state.revision >= 0;
      }
    })
  ])
});
