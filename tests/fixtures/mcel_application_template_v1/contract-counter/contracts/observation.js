export const ContractCounterObservation = Object.freeze({
  schema: "mcel.observation-contract.v1",
  appId: "contract-counter",
  currentStatus: "declaration-only",
  observations: Object.freeze([
    Object.freeze({ id: "contract-counter.observe.value", source: "browser-dom", semanticNodeId: "contract-counter.value", property: "textContent" }),
    Object.freeze({ id: "contract-counter.observe.value-visible", source: "browser-geometry", semanticNodeId: "contract-counter.value", property: "visible" }),
    Object.freeze({ id: "contract-counter.observe.receipt", source: "browser-dom", semanticNodeId: "contract-counter.latest-receipt", property: "textContent" })
  ])
});
