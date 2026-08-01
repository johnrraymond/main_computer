export const ContractCounterSurface = Object.freeze({
  schema: "mcel.semantic-surface-ir.v1",
  appId: "contract-counter",
  surfaceId: "contract-counter.surface.primary",
  regions: Object.freeze([
    Object.freeze({ id: "contract-counter.region.shell", role: "application" }),
    Object.freeze({ id: "contract-counter.region.value", role: "result" }),
    Object.freeze({ id: "contract-counter.region.controls", role: "toolbar" }),
    Object.freeze({ id: "contract-counter.region.evidence", role: "status" })
  ]),
  nodes: Object.freeze([
    Object.freeze({ id: "contract-counter.value", kind: "state-value", statePath: "count", regionId: "contract-counter.region.value" }),
    Object.freeze({ id: "contract-counter.increment-control", kind: "control", intentId: "increment", regionId: "contract-counter.region.controls" }),
    Object.freeze({ id: "contract-counter.reset-control", kind: "control", intentId: "reset", regionId: "contract-counter.region.controls" }),
    Object.freeze({ id: "contract-counter.latest-receipt", kind: "operation-evidence", regionId: "contract-counter.region.evidence" })
  ])
});
