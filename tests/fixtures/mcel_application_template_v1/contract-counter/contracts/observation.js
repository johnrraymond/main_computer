export const ContractCounterObservation = Object.freeze({
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
