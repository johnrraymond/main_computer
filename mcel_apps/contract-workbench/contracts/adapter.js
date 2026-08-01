function findContract(state, contractId) {
  return (state?.contracts || []).find((contract) => contract.id === contractId) || null;
}

export const ContractWorkbenchAdapter = Object.freeze({
  schema: "mcel.semantic-adapter.v1",
  appId: "contract-workbench",
  adapterId: "contract-workbench.adapter.v1",
  currentRuntimeStatus: "forward-specification",
  targetRuntimeStatus: "fullApplicationSemanticReady",

  preflight({intentId, input, state}) {
    if (intentId === "direct-set") return Object.freeze({ok: false, code: "INTENT_PROHIBITED"});
    if (input?.expectedRevision !== state?.revision) return Object.freeze({ok: false, code: "REVISION_STALE"});
    const payload = input?.payload || {};
    if (intentId === "add-contract") {
      if (typeof payload.name !== "string" || !payload.name.trim()) {
        return Object.freeze({ok: false, code: "CONTRACT_NAME_REQUIRED", message: "A contract name is required."});
      }
      if (!Number.isInteger(payload.quantity) || payload.quantity < 1) {
        return Object.freeze({ok: false, code: "CONTRACT_QUANTITY_INVALID", message: "Quantity must be a positive integer."});
      }
    }
    if (["remove-contract", "update-quantity", "request-quote", "cancel-quote"].includes(intentId) && !findContract(state, payload.contractId)) {
      return Object.freeze({ok: false, code: "CONTRACT_NOT_FOUND"});
    }
    if (intentId === "update-quantity" && (!Number.isInteger(payload.quantity) || payload.quantity < 1)) {
      return Object.freeze({ok: false, code: "CONTRACT_QUANTITY_INVALID"});
    }
    return Object.freeze({ok: true});
  },

  transition({intentId, input, state}) {
    const payload = input?.payload || {};
    if (intentId === "add-contract") {
      return Object.freeze({
        contracts: Object.freeze([
          ...state.contracts,
          Object.freeze({
            id: `contract-${state.nextContractId}`,
            name: payload.name.trim(),
            category: payload.category,
            quantity: payload.quantity,
            quoteStatus: "idle",
            quoteAmount: 0
          })
        ]),
        nextContractId: state.nextContractId + 1,
        revision: state.revision + 1
      });
    }
    if (intentId === "remove-contract") {
      return Object.freeze({
        ...state,
        contracts: Object.freeze(state.contracts.filter((contract) => contract.id !== payload.contractId)),
        revision: state.revision + 1
      });
    }
    if (intentId === "update-quantity") {
      return Object.freeze({
        ...state,
        contracts: Object.freeze(state.contracts.map((contract) => (
          contract.id === payload.contractId ? Object.freeze({...contract, quantity: payload.quantity}) : contract
        ))),
        revision: state.revision + 1
      });
    }
    if (intentId === "clear-all") {
      return Object.freeze({...state, contracts: Object.freeze([]), revision: state.revision + 1});
    }
    throw Object.assign(new Error(`Intent ${intentId} requires a future MCEL operation runtime.`), {
      code: intentId === "request-quote" ? "MCEL_CAPABILITY_OPERATION_UNSUPPORTED" : "MCEL_OPERATION_CANCELLATION_UNSUPPORTED"
    });
  },

  validateEffects({intentId, before, after, input}) {
    const payload = input?.payload || {};
    if (intentId === "add-contract") {
      return after.contracts.length === before.contracts.length + 1 && after.revision === before.revision + 1;
    }
    if (intentId === "remove-contract") {
      return !after.contracts.some((contract) => contract.id === payload.contractId)
        && after.contracts.length === before.contracts.length - 1
        && after.revision === before.revision + 1;
    }
    if (intentId === "update-quantity") {
      return after.contracts.some((contract) => contract.id === payload.contractId && contract.quantity === payload.quantity)
        && after.revision === before.revision + 1;
    }
    if (intentId === "clear-all") {
      return after.contracts.length === 0 && after.revision === before.revision + 1;
    }
    return false;
  },

  async *runCapabilityOperation({intentId}) {
    if (intentId !== "request-quote") throw Object.assign(new Error("Unsupported capability operation."), {code: "INTENT_UNKNOWN"});
    throw Object.assign(new Error("Capability-backed streaming operations are not implemented by the current MCEL runtime."), {
      code: "MCEL_CAPABILITY_OPERATION_UNSUPPORTED"
    });
  },

  receiveProvisional() {
    throw Object.assign(new Error("Provisional state reconciliation is not implemented by the current MCEL runtime."), {
      code: "MCEL_PROVISIONAL_COMMIT_RUNTIME_UNSUPPORTED"
    });
  },

  commitCapabilityOperation() {
    throw Object.assign(new Error("Capability-backed canonical commit is not implemented by the current MCEL runtime."), {
      code: "MCEL_CAPABILITY_OPERATION_UNSUPPORTED"
    });
  },

  cancelOperation() {
    throw Object.assign(new Error("Operation cancellation is not implemented by the current MCEL runtime."), {
      code: "MCEL_OPERATION_CANCELLATION_UNSUPPORTED"
    });
  }
});
