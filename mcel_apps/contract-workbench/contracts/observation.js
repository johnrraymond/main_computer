export const ContractWorkbenchObservation = Object.freeze({
  schema: "mcel.observation-contract.v1",
  appId: "contract-workbench",
  currentStatus: "forward-specification",
  observations: Object.freeze([
    Object.freeze({id: "contract-workbench.observe.total", kind: "property", source: "browser-dom", semanticNodeId: "contract-workbench.total-quantity", property: "textContent", compareToStatePath: "totalQuantity", normalization: "string"}),
    Object.freeze({id: "contract-workbench.observe.validation", kind: "conditional", source: "browser-dom", semanticNodeId: "contract-workbench.validation", compareToLatestReceiptPath: "message"}),
    Object.freeze({id: "contract-workbench.observe.empty", kind: "conditional", source: "browser-dom", semanticNodeId: "contract-workbench.empty-state", compareToStatePredicate: Object.freeze({path: "visibleContracts", predicate: "empty"})}),
    Object.freeze({
      id: "contract-workbench.observe.items",
      kind: "collection",
      source: "browser-dom",
      semanticNodeId: "contract-workbench.items",
      compareToStatePath: "visibleContracts",
      keyPath: "id",
      requireOrderMatch: true,
      fields: Object.freeze({name: "name", category: "category", quantity: "quantity", quoteStatus: "quoteStatus", quoteAmount: "quoteAmount"}),
      requireItemControls: Object.freeze(["update-quantity", "remove-contract", "request-quote", "cancel-quote"])
    }),
    Object.freeze({id: "contract-workbench.observe.progress", kind: "provisional", source: "browser-dom", semanticNodeId: "contract-workbench.items", compareToProvisionalStatePath: "quoteProgress"}),
    Object.freeze({id: "contract-workbench.observe.receipt", kind: "receipt", source: "browser-dom", semanticNodeId: "contract-workbench.latest-receipt", compareToOperationReceipt: true}),
    Object.freeze({id: "contract-workbench.observe.multi-instance", kind: "multi-instance", source: "browser-dom", minimumInstances: 2, requireIsolated: Object.freeze(["state", "local-state", "provisional-state", "operation-ledger", "receipts", "roots"])})
  ])
});
