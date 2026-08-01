export const ContractWorkbenchIntents = Object.freeze({
  "add-contract": Object.freeze({
    id: "add-contract",
    kind: "mutation",
    risk: "local-state",
    payload: Object.freeze({name: "trimmed-string", quantity: "positive-integer", category: "contract-category"}),
    reads: Object.freeze(["state.contracts", "state.nextContractId", "state.revision"]),
    writes: Object.freeze(["state.contracts", "state.nextContractId", "state.revision"]),
    effects: Object.freeze(["append one stable keyed contract", "advance next id", "revision plus one"])
  }),
  "remove-contract": Object.freeze({
    id: "remove-contract",
    kind: "mutation",
    risk: "local-state",
    payload: Object.freeze({contractId: "collection-item-key"}),
    reads: Object.freeze(["state.contracts", "state.revision"]),
    writes: Object.freeze(["state.contracts", "state.revision"]),
    effects: Object.freeze(["remove exactly one keyed contract", "revision plus one"])
  }),
  "update-quantity": Object.freeze({
    id: "update-quantity",
    kind: "mutation",
    risk: "local-state",
    payload: Object.freeze({contractId: "collection-item-key", quantity: "positive-integer-item-field"}),
    reads: Object.freeze(["state.contracts", "state.revision"]),
    writes: Object.freeze(["state.contracts", "state.revision"]),
    effects: Object.freeze(["replace one keyed item quantity", "revision plus one"])
  }),
  "request-quote": Object.freeze({
    id: "request-quote",
    kind: "async-capability",
    risk: "external-read-stream",
    payload: Object.freeze({contractId: "collection-item-key"}),
    uses: Object.freeze(["quotes.requestQuote"]),
    reads: Object.freeze(["state.contracts", "state.revision"]),
    writes: Object.freeze(["provisional.quoteProgress", "state.contracts", "state.revision"]),
    concurrency: "latest-per-item-key",
    cancellable: true,
    effects: Object.freeze(["stream provisional quote events", "reconcile reports", "commit one quote result", "revision plus one"])
  }),
  "cancel-quote": Object.freeze({
    id: "cancel-quote",
    kind: "cancel-operation",
    risk: "external-operation-control",
    payload: Object.freeze({contractId: "collection-item-key"}),
    cancels: "request-quote",
    reads: Object.freeze(["provisional.quoteProgress"]),
    writes: Object.freeze(["provisional.quoteProgress"]),
    effects: Object.freeze(["abort matching in-flight quote", "no canonical contract mutation"])
  }),
  "clear-all": Object.freeze({
    id: "clear-all",
    kind: "mutation",
    risk: "local-state",
    reads: Object.freeze(["state.contracts", "state.revision"]),
    writes: Object.freeze(["state.contracts", "state.revision"]),
    effects: Object.freeze(["contracts empty", "revision plus one"])
  }),
  "direct-set": Object.freeze({
    id: "direct-set",
    kind: "prohibited",
    risk: "prohibited",
    reason: "Arbitrary canonical assignment bypasses the MCEL operation authority."
  })
});
