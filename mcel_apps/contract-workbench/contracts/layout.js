export const ContractWorkbenchLayout = Object.freeze({
  schema: "mcel.layout-grammar.v1",
  surfaceId: "contract-workbench.surface.primary",
  regions: Object.freeze({
    "contract-workbench.region.shell": Object.freeze({direction: "column", gap: "medium", padding: "large", minInlineSize: 320, maxInlineSize: 1120}),
    "contract-workbench.region.editor": Object.freeze({direction: "grid", responsiveColumns: Object.freeze({compact: 1, wide: 4}), gap: "small"}),
    "contract-workbench.region.summary": Object.freeze({direction: "row", wrap: true, gap: "small"}),
    "contract-workbench.region.filters": Object.freeze({direction: "grid", responsiveColumns: Object.freeze({compact: 1, wide: 2}), gap: "small"}),
    "contract-workbench.region.collection": Object.freeze({direction: "column", gap: "small", blockSize: "content", scrollOwner: false}),
    "contract-workbench.region.evidence": Object.freeze({direction: "column", gap: "small", blockSize: "content", scrollOwner: false})
  }),
  constraints: Object.freeze([
    Object.freeze({id: "contract-workbench.layout.editor-before-summary", relation: "before", first: "contract-workbench.region.editor", second: "contract-workbench.region.summary"}),
    Object.freeze({id: "contract-workbench.layout.summary-before-filters", relation: "before", first: "contract-workbench.region.summary", second: "contract-workbench.region.filters"}),
    Object.freeze({id: "contract-workbench.layout.filters-before-collection", relation: "before", first: "contract-workbench.region.filters", second: "contract-workbench.region.collection"}),
    Object.freeze({id: "contract-workbench.layout.collection-before-evidence", relation: "before", first: "contract-workbench.region.collection", second: "contract-workbench.region.evidence"}),
    Object.freeze({id: "contract-workbench.layout.controls-usable", relation: "minimum-control-size", target: "contract-workbench.region.shell", inline: 44, block: 44}),
    Object.freeze({id: "contract-workbench.layout.collection-key-order", relation: "canonical-order", target: "contract-workbench.items"}),
    Object.freeze({id: "contract-workbench.layout.no-page-horizontal-overflow", relation: "no-horizontal-overflow", target: "contract-workbench.region.shell"})
  ])
});
