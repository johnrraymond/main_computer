export const TaskManagerSurface = Object.freeze({
  schema: "mcel.semantic-surface-ir.v1",
  appId: "task-manager",
  surfaceId: "task-manager.surface.primary",
  regions: Object.freeze([
    Object.freeze({ id: "task-manager.region.shell", role: "application" }),
    Object.freeze({ id: "task-manager.region.value", role: "result" }),
    Object.freeze({ id: "task-manager.region.controls", role: "toolbar" }),
    Object.freeze({ id: "task-manager.region.evidence", role: "status" })
  ]),
  nodes: Object.freeze([
    Object.freeze({ id: "task-manager.value", kind: "state-value", statePath: "count", regionId: "task-manager.region.value" }),
    Object.freeze({ id: "task-manager.increment-control", kind: "control", intentId: "increment", regionId: "task-manager.region.controls" }),
    Object.freeze({ id: "task-manager.reset-control", kind: "control", intentId: "reset", regionId: "task-manager.region.controls" }),
    Object.freeze({ id: "task-manager.latest-receipt", kind: "operation-evidence", regionId: "task-manager.region.evidence" })
  ])
});
