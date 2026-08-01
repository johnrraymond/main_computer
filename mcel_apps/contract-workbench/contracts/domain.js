export const ContractWorkbenchDomain = Object.freeze({
  schema: "mcel.application-domain.v1",
  appId: "contract-workbench",
  currentRuntimeStatus: "forward-specification",
  initialState: Object.freeze({
    contracts: Object.freeze([]),
    nextContractId: 1,
    revision: 0
  }),
  rendererLocalState: Object.freeze({
    draftName: "",
    draftQuantity: "1",
    draftCategory: "materials",
    filterText: "",
    sortMode: "name"
  }),
  provisionalState: Object.freeze({quoteProgress: Object.freeze({})}),
  derivedState: Object.freeze([
    Object.freeze({id: "visibleContracts", reads: Object.freeze(["contracts", "filterText", "sortMode"])}),
    Object.freeze({id: "totalQuantity", reads: Object.freeze(["contracts"])}),
    Object.freeze({id: "canSubmit", reads: Object.freeze(["draftName", "draftQuantity"])})
  ]),
  invariantReads: Object.freeze(["state.contracts", "state.nextContractId", "state.revision"]),
  invariants: Object.freeze([
    Object.freeze({
      id: "contract-workbench.invariant.contracts-array",
      check(state) {
        return Array.isArray(state?.contracts);
      }
    }),
    Object.freeze({
      id: "contract-workbench.invariant.contract-keys-unique",
      check(state) {
        const ids = (state?.contracts || []).map((contract) => contract.id);
        return ids.length === new Set(ids).size;
      }
    }),
    Object.freeze({
      id: "contract-workbench.invariant.contract-values-valid",
      check(state) {
        return (state?.contracts || []).every((contract) => (
          typeof contract.id === "string"
          && contract.id.length > 0
          && typeof contract.name === "string"
          && contract.name.length > 0
          && ["materials", "services", "transport"].includes(contract.category)
          && Number.isInteger(contract.quantity)
          && contract.quantity > 0
          && ["idle", "running", "quoted", "partial", "failed", "cancelled"].includes(contract.quoteStatus)
          && Number.isInteger(contract.quoteAmount)
          && contract.quoteAmount >= 0
        ));
      }
    }),
    Object.freeze({
      id: "contract-workbench.invariant-revision-nonnegative",
      check(state) {
        return Number.isInteger(state?.revision) && state.revision >= 0;
      }
    })
  ])
});
