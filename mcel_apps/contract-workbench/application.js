"use strict";

const mcel = typeof McelAppDefinition !== "undefined"
  ? McelAppDefinition
  : require("../../main_computer/web/applications/scripts/mcel-app-definition.js");

const ContractSchema = mcel.schema.object({
  id: mcel.schema.string({minLength: 1}),
  name: mcel.schema.string({minLength: 1}),
  category: mcel.schema.oneOf("materials", "services", "transport"),
  quantity: mcel.schema.integer({minimum: 1}),
  quoteStatus: mcel.schema.oneOf("idle", "running", "quoted", "partial", "failed", "cancelled"),
  quoteAmount: mcel.schema.integer({minimum: 0})
});

const QuoteService = mcel.capability("contract-workbench.quote-service", {
  risk: "external-read-stream",
  description: "Request independently streamed quote reports and reconcile them before one canonical commit.",
  operations: {
    requestQuote: {
      request: mcel.schema.object({
        contractId: mcel.schema.string({minLength: 1}),
        category: mcel.schema.string({minLength: 1}),
        quantity: mcel.schema.integer({minimum: 1})
      }),
      response: mcel.schema.object({
        amount: mcel.schema.integer({minimum: 0}),
        source: mcel.schema.string({minLength: 1})
      }),
      stream: true,
      cancellable: true
    }
  }
});

const ContractWorkbenchApplication = mcel.defineApplication({
  id: "contract-workbench",
  title: "Contract Operations Workbench",

  state: {
    contracts: mcel.state.canonical([], {
      schema: mcel.schema.array(ContractSchema),
      description: "Committed contracts in stable key order."
    }),
    nextContractId: mcel.state.canonical(1, {
      schema: mcel.schema.integer({minimum: 1})
    }),
    revision: mcel.state.canonical(0, {
      schema: mcel.schema.integer({minimum: 0})
    }),

    quoteProgress: mcel.state.provisional({}, {
      schema: mcel.schema.object({}),
      description: "Per-contract external quote progress that is not canonical until reconciliation commits."
    }),

    draftName: mcel.state.local("", {schema: mcel.schema.string()}),
    draftQuantity: mcel.state.local("1", {schema: mcel.schema.string()}),
    draftCategory: mcel.state.local("materials", {
      schema: mcel.schema.oneOf("materials", "services", "transport")
    }),
    filterText: mcel.state.local("", {schema: mcel.schema.string()}),
    sortMode: mcel.state.local("name", {
      schema: mcel.schema.oneOf("name", "quantity", "category")
    }),

    visibleContracts: mcel.state.derived(
      ["contracts", "filterText", "sortMode"],
      ({contracts, filterText, sortMode}) => {
        const normalizedFilter = String(filterText || "").trim().toLowerCase();
        const filtered = contracts.filter((contract) => (
          !normalizedFilter
          || contract.name.toLowerCase().includes(normalizedFilter)
          || contract.category.toLowerCase().includes(normalizedFilter)
        ));
        return [...filtered].sort((left, right) => {
          if (sortMode === "quantity") return left.quantity - right.quantity || left.id.localeCompare(right.id);
          return String(left[sortMode]).localeCompare(String(right[sortMode])) || left.id.localeCompare(right.id);
        });
      },
      {schema: mcel.schema.array(ContractSchema)}
    ),
    totalQuantity: mcel.state.derived(
      ["contracts"],
      ({contracts}) => contracts.reduce((total, contract) => total + contract.quantity, 0),
      {schema: mcel.schema.integer({minimum: 0})}
    ),
    canSubmit: mcel.state.derived(
      ["draftName", "draftQuantity"],
      ({draftName, draftQuantity}) => (
        String(draftName || "").trim().length > 0
        && Number.isInteger(Number(draftQuantity))
        && Number(draftQuantity) > 0
      ),
      {schema: mcel.schema.boolean()}
    )
  },

  capabilities: {
    quotes: QuoteService
  },

  invariants: [
    mcel.invariant("contract-workbench.invariant.contracts-array",
      (state) => Array.isArray(state?.contracts),
      {reads: ["contracts"]}
    ),
    mcel.invariant("contract-workbench.invariant.contract-keys-unique",
      (state) => {
        const ids = (state?.contracts || []).map((contract) => contract.id);
        return ids.length === new Set(ids).size;
      },
      {reads: ["contracts"]}
    ),
    mcel.invariant("contract-workbench.invariant.contract-values-valid",
      (state) => (state?.contracts || []).every((contract) => (
        typeof contract?.id === "string"
        && contract.id.length > 0
        && typeof contract.name === "string"
        && contract.name.length > 0
        && ["materials", "services", "transport"].includes(contract.category)
        && Number.isInteger(contract.quantity)
        && contract.quantity > 0
        && ["idle", "running", "quoted", "partial", "failed", "cancelled"].includes(contract.quoteStatus)
        && Number.isInteger(contract.quoteAmount)
        && contract.quoteAmount >= 0
      )),
      {reads: ["contracts"]}
    ),
    mcel.invariant("contract-workbench.invariant.revision-nonnegative",
      (state) => Number.isInteger(state?.revision) && state.revision >= 0,
      {reads: ["revision"]}
    )
  ],

  operations: {
    "add-contract": mcel.operation.mutation({
      payload: {
        name: mcel.source.nodeValue("contract-workbench.draft-name", {normalize: "trim"}),
        quantity: mcel.source.nodeValue("contract-workbench.draft-quantity", {parse: "integer"}),
        category: mcel.source.nodeValue("contract-workbench.draft-category")
      },
      reads: ["contracts", "nextContractId", "revision"],
      writes: ["contracts", "nextContractId", "revision"],
      preflight({payload}) {
        if (!payload.name) return {ok: false, code: "CONTRACT_NAME_REQUIRED", message: "A contract name is required."};
        if (!Number.isInteger(payload.quantity) || payload.quantity < 1) {
          return {ok: false, code: "CONTRACT_QUANTITY_INVALID", message: "Quantity must be a positive integer."};
        }
        return {ok: true};
      },
      transition({state, payload}) {
        const contract = {
          id: `contract-${state.nextContractId}`,
          name: payload.name,
          category: payload.category,
          quantity: payload.quantity,
          quoteStatus: "idle",
          quoteAmount: 0
        };
        return {
          contracts: [...state.contracts, contract],
          nextContractId: state.nextContractId + 1,
          revision: state.revision + 1
        };
      },
      ensures({before, after}) {
        return after.contracts.length === before.contracts.length + 1
          && after.revision === before.revision + 1;
      }
    }),

    "remove-contract": mcel.operation.mutation({
      payload: {contractId: mcel.source.itemKey()},
      reads: ["contracts", "revision"],
      writes: ["contracts", "revision"],
      preflight({state, payload}) {
        return state.contracts.some((contract) => contract.id === payload.contractId)
          ? {ok: true}
          : {ok: false, code: "CONTRACT_NOT_FOUND"};
      },
      transition({state, payload}) {
        return {
          contracts: state.contracts.filter((contract) => contract.id !== payload.contractId),
          revision: state.revision + 1
        };
      },
      ensures({before, after, payload}) {
        return !after.contracts.some((contract) => contract.id === payload.contractId)
          && after.contracts.length === before.contracts.length - 1
          && after.revision === before.revision + 1;
      }
    }),

    "update-quantity": mcel.operation.mutation({
      payload: {
        contractId: mcel.source.itemKey(),
        quantity: mcel.source.itemField("quantity", {parse: "integer"})
      },
      reads: ["contracts", "revision"],
      writes: ["contracts", "revision"],
      preflight({payload}) {
        return Number.isInteger(payload.quantity) && payload.quantity > 0
          ? {ok: true}
          : {ok: false, code: "CONTRACT_QUANTITY_INVALID"};
      },
      transition({state, payload}) {
        return {
          contracts: state.contracts.map((contract) => (
            contract.id === payload.contractId
              ? {...contract, quantity: payload.quantity}
              : contract
          )),
          revision: state.revision + 1
        };
      },
      ensures({after, payload}) {
        return after.contracts.some((contract) => (
          contract.id === payload.contractId && contract.quantity === payload.quantity
        ));
      }
    }),

    "request-quote": mcel.operation.async({
      risk: "external-read-stream",
      uses: ["quotes"],
      payload: {contractId: mcel.source.itemKey()},
      reads: ["contracts", "revision"],
      writes: ["contracts", "revision"],
      provisionalPath: "quoteProgress",
      concurrency: "latest-per-item-key",
      cancellable: true,
      async *run({state, payload, capabilities, signal}) {
        const contract = state.contracts.find((entry) => entry.id === payload.contractId);
        if (!contract) throw Object.assign(new Error("Contract not found."), {code: "CONTRACT_NOT_FOUND"});
        yield* capabilities.quotes.requestQuote({
          contractId: contract.id,
          category: contract.category,
          quantity: contract.quantity
        }, {signal});
      },
      receive({provisional, event, payload}) {
        const current = provisional[payload.contractId] || {received: 0, expected: 0, reports: [], failures: []};
        const next = {...current};
        if (event.type === "quote.started") next.expected = event.expected || 0;
        if (event.type === "quote.received") {
          next.received += 1;
          next.reports = [...next.reports, event.report];
        }
        if (event.type === "quote.failed") next.failures = [...next.failures, event];
        return {...provisional, [payload.contractId]: next};
      },
      commit({state, provisional, payload}) {
        const progress = provisional[payload.contractId] || {reports: [], failures: []};
        const amounts = progress.reports.map((report) => Number(report.amount || 0));
        const amount = amounts.length ? Math.round(amounts.reduce((sum, value) => sum + value, 0) / amounts.length) : 0;
        const quoteStatus = progress.failures.length ? "partial" : "quoted";
        return {
          contracts: state.contracts.map((contract) => (
            contract.id === payload.contractId
              ? {...contract, quoteStatus, quoteAmount: amount}
              : contract
          )),
          revision: state.revision + 1
        };
      },
      ensures({after, payload}) {
        return after.contracts.some((contract) => (
          contract.id === payload.contractId
          && ["quoted", "partial"].includes(contract.quoteStatus)
        ));
      }
    }),

    "cancel-quote": mcel.operation.cancel({
      payload: {contractId: mcel.source.itemKey()},
      cancels: "request-quote",
      reads: ["quoteProgress"],
      writes: ["quoteProgress"],
      reason: "Cancel the active quote operation for the selected stable item key."
    }),

    "clear-all": mcel.operation.mutation({
      reads: ["contracts", "revision"],
      writes: ["contracts", "revision"],
      transition({state}) {
        return {contracts: [], revision: state.revision + 1};
      },
      ensures({after}) {
        return after.contracts.length === 0;
      }
    }),

    "direct-set": mcel.operation.prohibited({
      reason: "Arbitrary canonical assignment bypasses declared MCEL operations."
    })
  },

  surface: mcel.surface({
    id: "contract-workbench.surface.primary",
    root: "#contract-workbench-app",
    regions: [
      {id: "contract-workbench.region.shell", role: "application"},
      {id: "contract-workbench.region.editor", role: "form"},
      {id: "contract-workbench.region.summary", role: "status"},
      {id: "contract-workbench.region.filters", role: "search"},
      {id: "contract-workbench.region.collection", role: "list"},
      {id: "contract-workbench.region.evidence", role: "status"}
    ],
    nodes: [
      mcel.node.input({id: "contract-workbench.draft-name", regionId: "contract-workbench.region.editor", inputType: "text", localPath: "draftName"}),
      mcel.node.input({id: "contract-workbench.draft-quantity", regionId: "contract-workbench.region.editor", inputType: "number", localPath: "draftQuantity"}),
      mcel.node.input({id: "contract-workbench.draft-category", regionId: "contract-workbench.region.editor", inputType: "select", localPath: "draftCategory"}),
      mcel.node.control({
        id: "contract-workbench.add-control",
        regionId: "contract-workbench.region.editor",
        intentId: "add-contract",
        payload: {
          name: mcel.source.nodeValue("contract-workbench.draft-name", {normalize: "trim"}),
          quantity: mcel.source.nodeValue("contract-workbench.draft-quantity", {parse: "integer"}),
          category: mcel.source.nodeValue("contract-workbench.draft-category")
        }
      }),
      mcel.node.conditional({
        id: "contract-workbench.validation",
        regionId: "contract-workbench.region.editor",
        source: mcel.source.latestReceipt("message"),
        templateId: "contract-workbench.validation-message",
        when: {predicate: "nonempty"},
        content: {property: "textContent"}
      }),
      mcel.node.property({id: "contract-workbench.total-quantity", regionId: "contract-workbench.region.summary", statePath: "totalQuantity", property: "textContent", transform: "string"}),
      mcel.node.property({id: "contract-workbench.visible-count", regionId: "contract-workbench.region.summary", statePath: "visibleContracts.length", property: "textContent", transform: "string"}),
      mcel.node.input({id: "contract-workbench.filter-text", regionId: "contract-workbench.region.filters", inputType: "search", localPath: "filterText"}),
      mcel.node.input({id: "contract-workbench.sort-mode", regionId: "contract-workbench.region.filters", inputType: "select", localPath: "sortMode"}),
      mcel.node.conditional({
        id: "contract-workbench.empty-state",
        regionId: "contract-workbench.region.collection",
        statePath: "visibleContracts",
        templateId: "contract-workbench.empty-state-template",
        when: {predicate: "empty"},
        content: {literal: "No contracts match the current view."}
      }),
      mcel.node.collection({
        id: "contract-workbench.items",
        regionId: "contract-workbench.region.collection",
        statePath: "visibleContracts",
        keyPath: "id",
        templateId: "contract-workbench.item",
        item: {
          fields: {
            name: {selector: "[data-mcel-item-field='name']", itemPath: "name", property: "textContent"},
            category: {selector: "[data-mcel-item-field='category']", itemPath: "category", property: "textContent"},
            quantity: {selector: "[data-mcel-item-field='quantity']", itemPath: "quantity", property: "value", parse: "integer"},
            quoteStatus: {selector: "[data-mcel-item-field='quote-status']", itemPath: "quoteStatus", property: "textContent"},
            quoteAmount: {selector: "[data-mcel-item-field='quote-amount']", itemPath: "quoteAmount", property: "textContent", transform: "currency-integer"}
          },
          controls: {
            update: {
              selector: "[data-mcel-item-intent='update-quantity']",
              intentId: "update-quantity",
              payload: {
                contractId: {fromItemKey: true},
                quantity: {fromItemField: "quantity", property: "value", parse: "integer"}
              }
            },
            remove: {
              selector: "[data-mcel-item-intent='remove-contract']",
              intentId: "remove-contract",
              payload: {contractId: {fromItemKey: true}}
            },
            quote: {
              selector: "[data-mcel-item-intent='request-quote']",
              intentId: "request-quote",
              payload: {contractId: {fromItemKey: true}}
            },
            cancel: {
              selector: "[data-mcel-item-intent='cancel-quote']",
              intentId: "cancel-quote",
              payload: {contractId: {fromItemKey: true}}
            }
          }
        }
      }),
      mcel.node.control({id: "contract-workbench.clear-control", regionId: "contract-workbench.region.collection", intentId: "clear-all"}),
      mcel.node.receipt({id: "contract-workbench.latest-receipt", regionId: "contract-workbench.region.evidence"})
    ]
  }),

  layout: {
    schema: "mcel.layout-grammar.v1",
    surfaceId: "contract-workbench.surface.primary",
    responsiveModes: ["compact", "wide"],
    regions: {
      "contract-workbench.region.shell": {direction: "column", gap: "medium", padding: "large", minInlineSize: 320, maxInlineSize: 1120},
      "contract-workbench.region.editor": {direction: "grid", responsiveColumns: {compact: 1, wide: 4}, gap: "small"},
      "contract-workbench.region.summary": {direction: "row", wrap: true, gap: "small"},
      "contract-workbench.region.filters": {direction: "grid", responsiveColumns: {compact: 1, wide: 2}, gap: "small"},
      "contract-workbench.region.collection": {direction: "column", gap: "small", blockSize: "content", scrollOwner: false},
      "contract-workbench.region.evidence": {direction: "column", gap: "small", blockSize: "content", scrollOwner: false}
    },
    constraints: [
      {id: "contract-workbench.layout.editor-before-summary", relation: "before", first: "contract-workbench.region.editor", second: "contract-workbench.region.summary"},
      {id: "contract-workbench.layout.summary-before-filters", relation: "before", first: "contract-workbench.region.summary", second: "contract-workbench.region.filters"},
      {id: "contract-workbench.layout.filters-before-collection", relation: "before", first: "contract-workbench.region.filters", second: "contract-workbench.region.collection"},
      {id: "contract-workbench.layout.collection-before-evidence", relation: "before", first: "contract-workbench.region.collection", second: "contract-workbench.region.evidence"},
      {id: "contract-workbench.layout.controls-usable", relation: "minimum-control-size", target: "contract-workbench.region.shell", inline: 44, block: 44},
      {id: "contract-workbench.layout.collection-key-order", relation: "canonical-order", target: "contract-workbench.items"},
      {id: "contract-workbench.layout.no-page-horizontal-overflow", relation: "no-horizontal-overflow", target: "contract-workbench.region.shell"}
    ]
  },

  acceptance: [
    mcel.acceptance("contract-workbench.acceptance.add", {
      operationId: "add-contract",
      when: {intentId: "add-contract", payload: {name: "Steel", quantity: 12, category: "materials"}},
      expect: {operationStatus: "committed", itemCountDelta: 1, stableKey: "contract-1"}
    }),
    mcel.acceptance("contract-workbench.acceptance.validation", {
      operationId: "add-contract",
      when: {intentId: "add-contract", payload: {name: "", quantity: 0, category: "materials"}},
      expect: {operationStatus: "refused", code: "CONTRACT_NAME_REQUIRED", conditionalValidationVisible: true, canonicalStateUnchanged: true}
    }),
    mcel.acceptance("contract-workbench.acceptance.remove", {
      operationId: "remove-contract",
      when: {intentId: "remove-contract", itemKey: "contract-1"},
      expect: {operationStatus: "committed", keyedItemAbsent: true}
    }),
    mcel.acceptance("contract-workbench.acceptance.update", {
      operationId: "update-quantity",
      when: {intentId: "update-quantity", itemKey: "contract-1", itemField: {quantity: 18}},
      expect: {operationStatus: "committed", visibleQuantity: "18"}
    }),
    mcel.acceptance("contract-workbench.acceptance.filter-sort", {
      when: {localState: {filterText: "steel", sortMode: "quantity"}},
      expect: {canonicalStateUnchanged: true, collectionMatchesDerivedState: true}
    }),
    mcel.acceptance("contract-workbench.acceptance.quote", {
      operationId: "request-quote",
      when: {intentId: "request-quote", itemKey: "contract-1"},
      expect: {provisionalEventsVisibleBeforeCommit: true, oneCanonicalCommit: true, operationStatus: "committed"}
    }),
    mcel.acceptance("contract-workbench.acceptance.cancel", {
      operationId: "cancel-quote",
      when: {intentId: "cancel-quote", itemKey: "contract-1"},
      expect: {operationStatus: "cancelled", canonicalStateUnchanged: true, provisionalStateClosed: true}
    }),
    mcel.acceptance("contract-workbench.acceptance.stale", {
      operationId: "add-contract",
      when: {intentId: "add-contract", expectedRevision: 0, actualRevision: 1},
      expect: {code: "REVISION_STALE", canonicalStateUnchanged: true}
    }),
    mcel.acceptance("contract-workbench.acceptance.duplicate", {
      operationId: "add-contract",
      when: {intentId: "add-contract", reuseOperationId: true},
      expect: {code: "OPERATION_DUPLICATE", canonicalStateUnchanged: true}
    }),
    mcel.acceptance("contract-workbench.acceptance.prohibited", {
      operationId: "direct-set",
      when: {intentId: "direct-set"},
      expect: {code: "INTENT_PROHIBITED", canonicalStateUnchanged: true}
    }),
    mcel.acceptance("contract-workbench.acceptance.multi-instance", {
      when: {mountInstances: 2, mutateInstance: 1},
      expect: {isolatedCanonicalState: true, isolatedLocalState: true, isolatedReceipts: true}
    })
  ],

  observations: [
    mcel.observe("contract-workbench.observe.total", {kind: "property", nodeId: "contract-workbench.total-quantity", statePath: "totalQuantity", property: "textContent", normalization: "string"}),
    mcel.observe("contract-workbench.observe.validation", {kind: "conditional", nodeId: "contract-workbench.validation", compareToLatestReceiptPath: "message"}),
    mcel.observe("contract-workbench.observe.empty", {kind: "conditional", nodeId: "contract-workbench.empty-state", compareToStatePredicate: {path: "visibleContracts", predicate: "empty"}}),
    mcel.observe("contract-workbench.observe.items", {
      kind: "collection",
      nodeId: "contract-workbench.items",
      statePath: "visibleContracts",
      keyPath: "id",
      requireOrderMatch: true,
      fields: {name: "name", category: "category", quantity: "quantity", quoteStatus: "quoteStatus", quoteAmount: "quoteAmount"},
      requireItemControls: ["update-quantity", "remove-contract", "request-quote", "cancel-quote"]
    }),
    mcel.observe("contract-workbench.observe.progress", {kind: "provisional", nodeId: "contract-workbench.items", compareToProvisionalStatePath: "quoteProgress"}),
    mcel.observe("contract-workbench.observe.receipt", {kind: "receipt", nodeId: "contract-workbench.latest-receipt", compareToOperationReceipt: true}),
    mcel.observe("contract-workbench.observe.multi-instance", {
      kind: "multi-instance",
      minimumInstances: 2,
      requireIsolated: ["state", "local-state", "provisional-state", "operation-ledger", "receipts", "roots"],
      expect: {isolatedState: true, isolatedReceipts: true}
    })
  ],

  multiInstance: {
    required: true,
    minimumInstances: 2,
    isolation: ["canonical-state", "local-state", "provisional-state", "operation-ledger", "receipts", "dom-roots"]
  }
});

if (typeof module !== "undefined" && module.exports) {
  module.exports = ContractWorkbenchApplication;
}
