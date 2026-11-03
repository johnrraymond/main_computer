export const TaskManagerLayout = Object.freeze({
  schema: "mcel.layout-grammar.v1",
  surfaceId: "task-manager.surface.primary",
  regions: Object.freeze({
    "task-manager.region.shell": Object.freeze({ direction: "column", alignment: "center", gap: "medium", padding: "large", minInlineSize: 240, maxInlineSize: 640 }),
    "task-manager.region.value": Object.freeze({ inlineSize: "fill", blockSize: "content", textAlignment: "center" }),
    "task-manager.region.controls": Object.freeze({ direction: "row", wrap: true, alignment: "center", gap: "small" }),
    "task-manager.region.evidence": Object.freeze({ inlineSize: "fill", blockSize: "content", scrollOwner: false })
  }),
  constraints: Object.freeze([
    Object.freeze({ id: "task-manager.layout.value-before-controls", relation: "before", first: "task-manager.region.value", second: "task-manager.region.controls" }),
    Object.freeze({ id: "task-manager.layout.controls-before-evidence", relation: "before", first: "task-manager.region.controls", second: "task-manager.region.evidence" }),
    Object.freeze({ id: "task-manager.layout.controls-usable", relation: "minimum-control-size", target: "task-manager.region.controls", inline: 44, block: 44 })
  ])
});
