export const ContractWorkbenchAcceptance = Object.freeze({
  schema: "mcel.acceptance-suite.v1",
  appId: "contract-workbench",
  currentStatus: "forward-specification",
  scenarios: Object.freeze([
    Object.freeze({id: "contract-workbench.acceptance.add", when: Object.freeze({intentId: "add-contract", payload: Object.freeze({name: "Steel", quantity: 12, category: "materials"})}), expect: Object.freeze({operationStatus: "committed", itemCountDelta: 1, stableKey: "contract-1"})}),
    Object.freeze({id: "contract-workbench.acceptance.validation", when: Object.freeze({intentId: "add-contract", payload: Object.freeze({name: "", quantity: 0, category: "materials"})}), expect: Object.freeze({operationStatus: "refused", code: "CONTRACT_NAME_REQUIRED", conditionalValidationVisible: true, canonicalStateUnchanged: true})}),
    Object.freeze({id: "contract-workbench.acceptance.remove", when: Object.freeze({intentId: "remove-contract", itemKey: "contract-1"}), expect: Object.freeze({operationStatus: "committed", keyedItemAbsent: true})}),
    Object.freeze({id: "contract-workbench.acceptance.update", when: Object.freeze({intentId: "update-quantity", itemKey: "contract-1", itemField: Object.freeze({quantity: 18})}), expect: Object.freeze({operationStatus: "committed", visibleQuantity: "18"})}),
    Object.freeze({id: "contract-workbench.acceptance.filter-sort", when: Object.freeze({localState: Object.freeze({filterText: "steel", sortMode: "quantity"})}), expect: Object.freeze({canonicalStateUnchanged: true, collectionMatchesDerivedState: true})}),
    Object.freeze({id: "contract-workbench.acceptance.quote", when: Object.freeze({intentId: "request-quote", itemKey: "contract-1"}), expect: Object.freeze({provisionalEventsVisibleBeforeCommit: true, oneCanonicalCommit: true, operationStatus: "committed"})}),
    Object.freeze({id: "contract-workbench.acceptance.cancel", when: Object.freeze({intentId: "cancel-quote", itemKey: "contract-1"}), expect: Object.freeze({operationStatus: "cancelled", canonicalStateUnchanged: true, provisionalStateClosed: true})}),
    Object.freeze({id: "contract-workbench.acceptance.stale", when: Object.freeze({intentId: "add-contract", expectedRevision: 0, actualRevision: 1}), expect: Object.freeze({code: "REVISION_STALE", canonicalStateUnchanged: true})}),
    Object.freeze({id: "contract-workbench.acceptance.duplicate", when: Object.freeze({intentId: "add-contract", reuseOperationId: true}), expect: Object.freeze({code: "OPERATION_DUPLICATE", canonicalStateUnchanged: true})}),
    Object.freeze({id: "contract-workbench.acceptance.prohibited", when: Object.freeze({intentId: "direct-set"}), expect: Object.freeze({code: "INTENT_PROHIBITED", canonicalStateUnchanged: true})}),
    Object.freeze({id: "contract-workbench.acceptance.multi-instance", when: Object.freeze({mountInstances: 2, mutateInstance: 1}), expect: Object.freeze({isolatedCanonicalState: true, isolatedLocalState: true, isolatedReceipts: true})})
  ])
});
