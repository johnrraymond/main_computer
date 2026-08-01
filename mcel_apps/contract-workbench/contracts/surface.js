export const ContractWorkbenchSurface = Object.freeze({
  schema: "mcel.semantic-surface-ir.v1",
  appId: "contract-workbench",
  surfaceId: "contract-workbench.surface.primary",
  currentRuntimeStatus: "forward-specification",
  regions: Object.freeze([
    Object.freeze({id: "contract-workbench.region.shell", role: "application"}),
    Object.freeze({id: "contract-workbench.region.editor", role: "form"}),
    Object.freeze({id: "contract-workbench.region.summary", role: "status"}),
    Object.freeze({id: "contract-workbench.region.filters", role: "search"}),
    Object.freeze({id: "contract-workbench.region.collection", role: "list"}),
    Object.freeze({id: "contract-workbench.region.evidence", role: "status"})
  ]),
  nodes: Object.freeze([
    Object.freeze({id: "contract-workbench.draft-name", kind: "input", localPath: "draftName", property: "value", inputType: "text", regionId: "contract-workbench.region.editor"}),
    Object.freeze({id: "contract-workbench.draft-quantity", kind: "input", localPath: "draftQuantity", property: "value", inputType: "number", parse: "integer", regionId: "contract-workbench.region.editor"}),
    Object.freeze({id: "contract-workbench.draft-category", kind: "input", localPath: "draftCategory", property: "value", inputType: "select", regionId: "contract-workbench.region.editor"}),
    Object.freeze({
      id: "contract-workbench.add-control",
      kind: "control",
      intentId: "add-contract",
      regionId: "contract-workbench.region.editor",
      payload: Object.freeze({
        name: Object.freeze({fromNode: "contract-workbench.draft-name", property: "value", normalize: "trim"}),
        quantity: Object.freeze({fromNode: "contract-workbench.draft-quantity", property: "value", parse: "integer"}),
        category: Object.freeze({fromNode: "contract-workbench.draft-category", property: "value"})
      })
    }),
    Object.freeze({
      id: "contract-workbench.validation",
      kind: "conditional",
      source: Object.freeze({fromLatestReceipt: "message"}),
      templateId: "contract-workbench.validation-message",
      when: Object.freeze({predicate: "nonempty"}),
      content: Object.freeze({property: "textContent"}),
      regionId: "contract-workbench.region.editor"
    }),
    Object.freeze({id: "contract-workbench.total-quantity", kind: "property", statePath: "totalQuantity", property: "textContent", transform: "string", regionId: "contract-workbench.region.summary"}),
    Object.freeze({id: "contract-workbench.visible-count", kind: "property", statePath: "visibleContracts.length", property: "textContent", transform: "string", regionId: "contract-workbench.region.summary"}),
    Object.freeze({id: "contract-workbench.filter-text", kind: "input", localPath: "filterText", property: "value", inputType: "search", regionId: "contract-workbench.region.filters"}),
    Object.freeze({id: "contract-workbench.sort-mode", kind: "input", localPath: "sortMode", property: "value", inputType: "select", regionId: "contract-workbench.region.filters"}),
    Object.freeze({
      id: "contract-workbench.empty-state",
      kind: "conditional",
      statePath: "visibleContracts",
      templateId: "contract-workbench.empty-state-template",
      when: Object.freeze({predicate: "empty"}),
      content: Object.freeze({literal: "No contracts match the current view."}),
      regionId: "contract-workbench.region.collection"
    }),
    Object.freeze({
      id: "contract-workbench.items",
      kind: "collection",
      statePath: "visibleContracts",
      keyPath: "id",
      templateId: "contract-workbench.item",
      regionId: "contract-workbench.region.collection",
      item: Object.freeze({
        fields: Object.freeze({
          name: Object.freeze({selector: "[data-mcel-item-field='name']", itemPath: "name", property: "textContent"}),
          category: Object.freeze({selector: "[data-mcel-item-field='category']", itemPath: "category", property: "textContent"}),
          quantity: Object.freeze({selector: "[data-mcel-item-field='quantity']", itemPath: "quantity", property: "value", parse: "integer"}),
          quoteStatus: Object.freeze({selector: "[data-mcel-item-field='quote-status']", itemPath: "quoteStatus", property: "textContent"}),
          quoteAmount: Object.freeze({selector: "[data-mcel-item-field='quote-amount']", itemPath: "quoteAmount", property: "textContent", transform: "currency-integer"})
        }),
        controls: Object.freeze({
          update: Object.freeze({selector: "[data-mcel-item-intent='update-quantity']", intentId: "update-quantity", payload: Object.freeze({contractId: Object.freeze({fromItemKey: true}), quantity: Object.freeze({fromItemField: "quantity", property: "value", parse: "integer"})})}),
          remove: Object.freeze({selector: "[data-mcel-item-intent='remove-contract']", intentId: "remove-contract", payload: Object.freeze({contractId: Object.freeze({fromItemKey: true})})}),
          quote: Object.freeze({selector: "[data-mcel-item-intent='request-quote']", intentId: "request-quote", payload: Object.freeze({contractId: Object.freeze({fromItemKey: true})})}),
          cancel: Object.freeze({selector: "[data-mcel-item-intent='cancel-quote']", intentId: "cancel-quote", payload: Object.freeze({contractId: Object.freeze({fromItemKey: true})})})
        })
      })
    }),
    Object.freeze({id: "contract-workbench.clear-control", kind: "control", intentId: "clear-all", regionId: "contract-workbench.region.collection"}),
    Object.freeze({id: "contract-workbench.latest-receipt", kind: "operation-evidence", regionId: "contract-workbench.region.evidence"})
  ])
});
