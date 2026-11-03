export const TaskManagerObservation = Object.freeze({
  schema: "mcel.observation-contract.v1",
  appId: "task-manager",
  currentStatus: "operation-linked",
  observations: Object.freeze([
    Object.freeze({
      id: "task-manager.observe.value",
      source: "browser-dom",
      semanticNodeId: "task-manager.value",
      property: "textContent",
      compareToStatePath: "count",
      normalization: "string"
    }),
    Object.freeze({
      id: "task-manager.observe.value-visible",
      source: "browser-geometry",
      semanticNodeId: "task-manager.value",
      property: "visible",
      expected: true,
      normalization: "boolean"
    }),
    Object.freeze({
      id: "task-manager.observe.receipt",
      source: "browser-dom",
      semanticNodeId: "task-manager.latest-receipt",
      property: "textContent",
      compareToOperationReceipt: true
    })
  ])
});
