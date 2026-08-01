export const ContractCounterLayout = Object.freeze({
  schema: "mcel.layout-grammar.v1",
  surfaceId: "contract-counter.surface.primary",
  regions: Object.freeze({
    "contract-counter.region.shell": Object.freeze({ direction: "column", alignment: "center", gap: "medium", padding: "large", minInlineSize: 240, maxInlineSize: 640 }),
    "contract-counter.region.value": Object.freeze({ inlineSize: "fill", blockSize: "content", textAlignment: "center" }),
    "contract-counter.region.controls": Object.freeze({ direction: "row", wrap: true, alignment: "center", gap: "small" }),
    "contract-counter.region.evidence": Object.freeze({ inlineSize: "fill", blockSize: "content", scrollOwner: false })
  }),
  constraints: Object.freeze([
    Object.freeze({ id: "contract-counter.layout.value-before-controls", relation: "before", first: "contract-counter.region.value", second: "contract-counter.region.controls" }),
    Object.freeze({ id: "contract-counter.layout.controls-before-evidence", relation: "before", first: "contract-counter.region.controls", second: "contract-counter.region.evidence" }),
    Object.freeze({ id: "contract-counter.layout.controls-usable", relation: "minimum-control-size", target: "contract-counter.region.controls", inline: 44, block: 44 })
  ])
});
