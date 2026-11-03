export const TaskManagerAcceptance = Object.freeze({
  schema: "mcel.acceptance-suite.v1",
  appId: "task-manager",
  currentStatus: "package-local-discovery-live",
  scenarios: Object.freeze([
    Object.freeze({ id: "task-manager.acceptance.increment", given: Object.freeze({ count: 0, revision: 0 }), when: Object.freeze({ intentId: "increment", expectedRevision: 0 }), expect: Object.freeze({ count: 1, revision: 1, operationStatus: "committed" }) }),
    Object.freeze({ id: "task-manager.acceptance.reset", given: Object.freeze({ count: 4, revision: 4 }), when: Object.freeze({ intentId: "reset", expectedRevision: 4 }), expect: Object.freeze({ count: 0, revision: 5, operationStatus: "committed" }) }),
    Object.freeze({ id: "task-manager.acceptance.stale", given: Object.freeze({ count: 2, revision: 2 }), when: Object.freeze({ intentId: "increment", expectedRevision: 1 }), expect: Object.freeze({ code: "REVISION_STALE", canonicalStateUnchanged: true }) }),
    Object.freeze({ id: "task-manager.acceptance.direct-set", given: Object.freeze({ count: 2, revision: 2 }), when: Object.freeze({ intentId: "direct-set", expectedRevision: 2 }), expect: Object.freeze({ code: "INTENT_PROHIBITED", canonicalStateUnchanged: true }) })
  ])
});
