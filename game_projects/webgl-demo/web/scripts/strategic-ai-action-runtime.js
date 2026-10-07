(function (global) {
  "use strict";

  const SCHEMA = "game.strategicAI.v1";
  const DEFINITION_VERSION = "game.strategicAI.definition.v8";
  const STATE_VERSION = "game.strategicAI.state.v8";
  const LEGACY_STATE_VERSION = "game.strategicAI.state.v1";
  const PREVIOUS_STATE_VERSION = "game.strategicAI.state.v2";
  const COORDINATOR_STATE_VERSION = "game.strategicAI.state.v3";
  const SOCIAL_STATE_VERSION = "game.strategicAI.state.v4";
  const COMMITMENT_STATE_VERSION = "game.strategicAI.state.v5";
  const DIRECTOR_STATE_VERSION = "game.strategicAI.state.v6";
  const COMMUNICATION_STATE_VERSION = "game.strategicAI.state.v7";
  const LEGACY_STATE_VERSIONS = Object.freeze([
    LEGACY_STATE_VERSION,
    PREVIOUS_STATE_VERSION,
    COORDINATOR_STATE_VERSION,
    SOCIAL_STATE_VERSION,
    COMMITMENT_STATE_VERSION,
    DIRECTOR_STATE_VERSION,
    COMMUNICATION_STATE_VERSION
  ]);

  function objectValue(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function arrayValue(value) {
    return Array.isArray(value) ? value : [];
  }

  function clone(value) {
    return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  }

  function stringValue(value) {
    return String(value || "").trim();
  }

  function finiteNumber(value, fallback = 0, minimum = -Infinity, maximum = Infinity) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(maximum, Math.max(minimum, parsed));
  }

  function integerValue(value, fallback = 0, minimum = 0) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(minimum, Math.trunc(parsed));
  }

  function stableStringify(value) {
    if (Array.isArray(value)) {
      return `[${value.map((item) => stableStringify(item)).join(",")}]`;
    }
    if (value && typeof value === "object") {
      const entries = Object.keys(value)
        .sort()
        .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`);
      return `{${entries.join(",")}}`;
    }
    return JSON.stringify(value);
  }

  function valuesEqual(left, right) {
    return stableStringify(left) === stableStringify(right);
  }

  function idSlug(value) {
    return stringValue(value)
      .replace(/[^a-zA-Z0-9._-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "record";
  }

  function indexById(records, key = "id") {
    const result = new Map();
    arrayValue(records).forEach((record) => {
      const raw = objectValue(record);
      const id = stringValue(raw[key]);
      if (id && !result.has(id)) result.set(id, raw);
    });
    return result;
  }


  function defaultOffscreenStepStates(definition) {
    const routes = indexById(objectValue(definition).reportRoutes);
    return arrayValue(objectValue(definition).offscreenSchedules).flatMap((schedule) => (
      arrayValue(objectValue(schedule).steps).map((step) => {
        const raw = objectValue(step);
        let readyAt = integerValue(raw.dueAt, 0, 0);
        if (stringValue(raw.kind) === "report") {
          const route = routes.get(stringValue(raw.routeId));
          readyAt += integerValue(objectValue(route).latency, 0, 0);
        }
        if (
          stringValue(raw.kind) === "actor-turn"
          && raw.deadlineAt !== null
          && raw.deadlineAt !== undefined
        ) {
          readyAt = Math.max(readyAt, integerValue(raw.deadlineAt, readyAt, 0));
        }
        return {
          scheduleId: stringValue(objectValue(schedule).id),
          stepId: stringValue(raw.id),
          status: "pending",
          attempts: 0,
          readyAt,
          completedAt: null,
          resultIds: [],
          reason: ""
        };
      })
    ));
  }


  function defaultCanonicalState(definition) {
    const raw = objectValue(definition);
    return {
      revision: 0,
      factStates: arrayValue(raw.facts).map((fact) => ({
        factId: stringValue(objectValue(fact).id),
        value: clone(objectValue(objectValue(fact).proposition).value)
      })).filter((entry) => entry.factId),
      resourceBalances: arrayValue(raw.resources).map((resource) => ({
        resourceId: stringValue(objectValue(resource).id),
        quantity: finiteNumber(objectValue(resource).capacity, 0, 0)
      })).filter((entry) => entry.resourceId),
      events: []
    };
  }

  function defaultCaptainModels(definition) {
    return arrayValue(objectValue(definition).captainModelProfiles).map((profile) => {
      const raw = objectValue(profile);
      return {
        modelId: `captain-model.runtime.${idSlug(raw.actorId)}`,
        profileId: stringValue(raw.id),
        holderActorId: stringValue(raw.actorId),
        subjectActorId: stringValue(raw.subjectActorId),
        tendencies: clone(objectValue(raw.initialTendencies)),
        observationIds: [],
        reportIds: [],
        updatedAt: 0
      };
    });
  }

  function defaultCooperationModels(definition) {
    return arrayValue(objectValue(definition).cooperationProfiles).map((profile) => {
      const raw = objectValue(profile);
      return {
        modelId: `cooperation-model.runtime.${idSlug(raw.holderActorId)}`,
        profileId: stringValue(raw.id),
        holderActorId: stringValue(raw.holderActorId),
        subjectActorId: stringValue(raw.subjectActorId),
        trust: finiteNumber(raw.initialTrust, 0.5, 0, 1),
        commitmentIds: [],
        updatedAt: 0
      };
    });
  }

  function defaultOpportunityStates(definition) {
    return arrayValue(objectValue(definition).campaignOpportunities).map((opportunity) => ({
      opportunityId: stringValue(objectValue(opportunity).id),
      status: "available",
      activatedAt: null,
      expiresAt: null,
      activationReceiptId: null,
      activationCount: 0
    }));
  }

  function migrateState(value, definition) {
    const state = clone(objectValue(value));
    const previousVersion = stringValue(state.stateVersion);
    if (!LEGACY_STATE_VERSIONS.includes(previousVersion)) return state;

    if (previousVersion === LEGACY_STATE_VERSION) {
      state.canonicalState = defaultCanonicalState(definition);
      state.proposals = [];
      state.outcomes = [];
    }
    if (!state.canonicalState) state.canonicalState = defaultCanonicalState(definition);
    if (!Array.isArray(state.proposals)) state.proposals = [];
    if (!Array.isArray(state.outcomes)) state.outcomes = [];
    if (!Array.isArray(state.reports)) state.reports = [];
    if (!Array.isArray(state.captainModels)) {
      state.captainModels = defaultCaptainModels(definition);
    }
    if (!Array.isArray(state.commitments)) state.commitments = [];
    if (!Array.isArray(state.cooperationModels)) {
      state.cooperationModels = defaultCooperationModels(definition);
    }
    if (!Array.isArray(state.campaignOpportunityStates)) {
      state.campaignOpportunityStates = defaultOpportunityStates(definition);
    }
    if (!Array.isArray(state.directorReceipts)) state.directorReceipts = [];
    arrayValue(state.actorStates).forEach((actorState) => {
      const raw = objectValue(actorState);
      if (!Array.isArray(raw.pendingProposalIds)) raw.pendingProposalIds = [];
    });
    arrayValue(state.receipts).forEach((receipt) => {
      const raw = objectValue(receipt);
      if (!Object.prototype.hasOwnProperty.call(raw, "canonicalRevision")) {
        raw.canonicalRevision = null;
      }
      if (!Object.prototype.hasOwnProperty.call(raw, "policyProfileId")) {
        raw.policyProfileId = null;
      }
    });
    const receiptById = indexById(state.receipts, "decisionId");
    arrayValue(state.proposals).forEach((proposal) => {
      const raw = objectValue(proposal);
      if (!Object.prototype.hasOwnProperty.call(raw, "canonicalRevision")) {
        const receipt = receiptById.get(stringValue(raw.decisionId));
        raw.canonicalRevision = receipt
          ? clone(objectValue(receipt).canonicalRevision)
          : null;
      }
    });
    if (!Array.isArray(state.offscreenStepStates)) {
      state.offscreenStepStates = defaultOffscreenStepStates(definition);
    }
    if (!Array.isArray(state.offscreenSimulationReceipts)) {
      state.offscreenSimulationReceipts = [];
    }
    if (!Number.isInteger(state.offscreenSimulationTime) || state.offscreenSimulationTime < 0) {
      state.offscreenSimulationTime = 0;
    }
    state.stateVersion = STATE_VERSION;
    return state;
  }

  function validateDefinition(value) {
    const definition = objectValue(value);
    const errors = [];
    if (definition.schema !== SCHEMA) errors.push(`schema must be ${SCHEMA}`);
    if (definition.definitionVersion !== DEFINITION_VERSION) {
      errors.push(`definitionVersion must be ${DEFINITION_VERSION}`);
    }
    if (definition.stateVersion !== STATE_VERSION) {
      errors.push(`stateVersion must be ${STATE_VERSION}`);
    }
    if (!arrayValue(definition.policyProfiles).length) errors.push("policyProfiles must be a non-empty list");
    if (!arrayValue(definition.reportRoutes).length) errors.push("reportRoutes must be a non-empty list");
    if (!arrayValue(definition.captainModelProfiles).length) errors.push("captainModelProfiles must be a non-empty list");
    if (!arrayValue(definition.commitmentTypes).length) errors.push("commitmentTypes must be a non-empty list");
    if (!arrayValue(definition.cooperationProfiles).length) errors.push("cooperationProfiles must be a non-empty list");
    if (arrayValue(definition.campaignOpportunities).length < 2) errors.push("campaignOpportunities must contain at least two routes");
    if (!arrayValue(definition.actors).length) errors.push("actors must be a non-empty list");
    if (!arrayValue(definition.actionTypes).length) errors.push("actionTypes must be a non-empty list");
    if (!arrayValue(definition.effectTypes).length) errors.push("effectTypes must be a non-empty list");
    if (!arrayValue(definition.resources).length) errors.push("resources must be a non-empty list");
    if (!Number.isInteger(definition.offscreenSimulationBudget)
        || definition.offscreenSimulationBudget < 1) {
      errors.push("offscreenSimulationBudget must be a positive integer");
    }
    if (!arrayValue(definition.offscreenSchedules).length) {
      errors.push("offscreenSchedules must be a non-empty list");
    }
    return {valid: errors.length === 0, errors};
  }

  class StrategicAIActionError extends Error {
    constructor(message, details = []) {
      super(message);
      this.name = "StrategicAIActionError";
      this.details = arrayValue(details).slice();
    }
  }

  class StrategicAIActionRuntime {
    constructor(definition, options = {}) {
      this.definition = clone(objectValue(definition));
      this.report = validateDefinition(this.definition);
      if (!this.report.valid) {
        throw new StrategicAIActionError("Invalid strategic AI action definition", this.report.errors);
      }

      this.sources = indexById(this.definition.sources);
      this.channels = indexById(this.definition.observationChannels);
      this.effects = indexById(this.definition.effectTypes);
      this.actions = indexById(this.definition.actionTypes);
      this.actors = indexById(this.definition.actors);
      this.facts = indexById(this.definition.facts);
      this.resources = indexById(this.definition.resources);
      this.checkpoints = indexById(this.definition.checkpoints);

      this.state = migrateState(
        options.state === undefined
          ? objectValue(this.definition.stateDefaults)
          : objectValue(options.state),
        this.definition
      );
      this.normalizeState();
    }

    normalizeState() {
      if (this.state.stateVersion !== STATE_VERSION) {
        throw new StrategicAIActionError(`Strategic AI state version must be ${STATE_VERSION}`);
      }
      const checkpointId = stringValue(this.state.currentCheckpointId);
      if (!this.checkpoints.has(checkpointId)) {
        throw new StrategicAIActionError(`Unknown strategic AI checkpoint ${checkpointId}`);
      }

      this.state.offscreenSimulationTime = integerValue(
        this.state.offscreenSimulationTime,
        0,
        0
      );
      this.state.offscreenStepStates = arrayValue(this.state.offscreenStepStates).map(clone);
      this.state.offscreenSimulationReceipts = arrayValue(
        this.state.offscreenSimulationReceipts
      ).map(clone);
      this.state.actorStates = arrayValue(this.state.actorStates).map((record) => ({
        actorId: stringValue(objectValue(record).actorId),
        activeGoalIds: arrayValue(objectValue(record).activeGoalIds).map(stringValue),
        beliefIds: arrayValue(objectValue(record).beliefIds).map(stringValue),
        memoryIds: arrayValue(objectValue(record).memoryIds).map(stringValue),
        pendingProposalIds: arrayValue(objectValue(record).pendingProposalIds).map(stringValue)
      }));
      this.state.observations = arrayValue(this.state.observations).map(clone);
      this.state.beliefs = arrayValue(this.state.beliefs).map(clone);
      this.state.memories = arrayValue(this.state.memories).map(clone);
      this.state.receipts = arrayValue(this.state.receipts).map(clone);
      this.state.proposals = arrayValue(this.state.proposals).map(clone);
      this.state.outcomes = arrayValue(this.state.outcomes).map(clone);
      this.state.reports = arrayValue(this.state.reports).map(clone);
      this.state.captainModels = arrayValue(this.state.captainModels).map(clone);
      this.state.commitments = arrayValue(this.state.commitments).map(clone);
      this.state.cooperationModels = arrayValue(this.state.cooperationModels).map(clone);
      this.state.campaignOpportunityStates = arrayValue(this.state.campaignOpportunityStates).map(clone);
      this.state.directorReceipts = arrayValue(this.state.directorReceipts).map(clone);

      const canonical = objectValue(this.state.canonicalState);
      this.state.canonicalState = {
        revision: integerValue(canonical.revision, 0, 0),
        factStates: arrayValue(canonical.factStates).map(clone),
        resourceBalances: arrayValue(canonical.resourceBalances).map(clone),
        events: arrayValue(canonical.events).map(clone)
      };
      this.refreshIndexes();
    }

    refreshIndexes() {
      this.actorStates = indexById(this.state.actorStates, "actorId");
      this.observations = indexById(this.state.observations);
      this.receipts = indexById(this.state.receipts, "decisionId");
      this.proposals = indexById(this.state.proposals, "proposalId");
      this.outcomes = indexById(this.state.outcomes, "outcomeId");
      this.reports = indexById(this.state.reports, "reportId");
      this.captainModels = indexById(this.state.captainModels, "modelId");
      this.commitments = indexById(this.state.commitments, "commitmentId");
      this.cooperationModels = indexById(this.state.cooperationModels, "modelId");
      this.campaignOpportunityStates = indexById(this.state.campaignOpportunityStates, "opportunityId");
      this.directorReceipts = indexById(this.state.directorReceipts, "directorReceiptId");
      this.outcomesByProposal = new Map();
      this.state.outcomes.forEach((outcome) => {
        const proposalId = stringValue(objectValue(outcome).proposalId);
        if (proposalId && !this.outcomesByProposal.has(proposalId)) {
          this.outcomesByProposal.set(proposalId, outcome);
        }
      });
    }

    actor(actorId) {
      const id = stringValue(actorId);
      const actor = this.actors.get(id);
      if (!actor) throw new StrategicAIActionError(`Unknown strategic actor ${id}`);
      return actor;
    }

    actorState(actorId) {
      const id = stringValue(actorId);
      this.actor(id);
      const actorState = this.actorStates.get(id);
      if (!actorState) throw new StrategicAIActionError(`Missing actor state ${id}`);
      return actorState;
    }

    receipt(decision) {
      const decisionId = typeof decision === "string"
        ? stringValue(decision)
        : stringValue(objectValue(decision).decisionId);
      const stored = this.receipts.get(decisionId);
      if (!stored) throw new StrategicAIActionError(`Unknown decision receipt ${decisionId}`);
      if (typeof decision !== "string" && !valuesEqual(stored, objectValue(decision))) {
        throw new StrategicAIActionError(`Decision receipt ${decisionId} does not match stored state`);
      }
      return stored;
    }

    nextProposalId(actorId, checkpointId) {
      return `proposal.runtime.${idSlug(actorId)}.${idSlug(checkpointId)}.${this.state.proposals.length + 1}`;
    }

    createProposal(decision, parameters = {}, options = {}) {
      const receipt = this.receipt(decision);
      if (!Number.isInteger(receipt.canonicalRevision) || receipt.canonicalRevision < 0) {
        throw new StrategicAIActionError(
          `Decision ${receipt.decisionId} has no canonical revision binding`
        );
      }
      const actor = this.actor(receipt.actorId);
      const existingProposal = this.state.proposals.find(
        (proposal) => stringValue(objectValue(proposal).decisionId) === stringValue(receipt.decisionId)
      );
      if (existingProposal) {
        throw new StrategicAIActionError(
          `Decision ${receipt.decisionId} already has proposal ${existingProposal.proposalId}`
        );
      }
      const action = this.actions.get(stringValue(receipt.selectedActionTypeId));
      if (!action) {
        throw new StrategicAIActionError(
          `Decision ${receipt.decisionId} references unknown action ${receipt.selectedActionTypeId}`
        );
      }
      const proposalId = stringValue(
        objectValue(options).proposalId
        || this.nextProposalId(receipt.actorId, receipt.checkpointId)
      );
      if (this.proposals.has(proposalId)) {
        throw new StrategicAIActionError(`Duplicate action proposal ${proposalId}`);
      }
      const requestedEffects = Array.isArray(objectValue(options).requestedEffects)
        ? clone(objectValue(options).requestedEffects)
        : arrayValue(action.effectTypeIds).map((effectTypeId) => ({
          effectTypeId: stringValue(effectTypeId),
          payload: clone(objectValue(parameters))
        }));
      const proposal = {
        proposalId,
        decisionId: stringValue(receipt.decisionId),
        actorId: stringValue(receipt.actorId),
        checkpointId: stringValue(receipt.checkpointId),
        canonicalRevision: integerValue(receipt.canonicalRevision, 0, 0),
        actionTypeId: stringValue(receipt.selectedActionTypeId),
        locationId: stringValue(objectValue(options).locationId || actor.localDestinationId),
        parameters: clone(objectValue(parameters)),
        requestedEffects,
        createdAt: integerValue(
          objectValue(options).createdAt,
          integerValue(objectValue(this.checkpoints.get(receipt.checkpointId)).worldTime, 0),
          0
        )
      };
      this.state.proposals.push(proposal);
      const actorState = this.actorState(proposal.actorId);
      if (!actorState.pendingProposalIds.includes(proposalId)) {
        actorState.pendingProposalIds.push(proposalId);
      }
      this.refreshIndexes();
      return clone(proposal);
    }

    resolveProposal(value) {
      const raw = typeof value === "string" ? null : objectValue(value);
      const proposalId = typeof value === "string"
        ? stringValue(value)
        : stringValue(raw.proposalId);
      const stored = this.proposals.get(proposalId);
      if (!stored) {
        return {
          proposal: raw || {proposalId},
          stored: null,
          failure: {
            code: "proposal-not-found",
            reason: `Proposal ${proposalId || "<empty>"} is not registered`
          }
        };
      }
      if (raw && !valuesEqual(raw, stored)) {
        return {
          proposal: stored,
          stored,
          failure: {
            code: "proposal-mismatch",
            reason: `Proposal ${proposalId} does not match the registered proposal`
          }
        };
      }
      return {proposal: stored, stored, failure: null};
    }

    canonicalFactValue(factId, canonicalState = this.state.canonicalState) {
      const id = stringValue(factId);
      const factState = arrayValue(objectValue(canonicalState).factStates)
        .find((entry) => stringValue(objectValue(entry).factId) === id);
      if (factState) return clone(objectValue(factState).value);
      const fact = this.facts.get(id);
      return fact ? clone(objectValue(objectValue(fact).proposition).value) : undefined;
    }

    resourceQuantity(resourceId, canonicalState = this.state.canonicalState) {
      const id = stringValue(resourceId);
      const balance = arrayValue(objectValue(canonicalState).resourceBalances)
        .find((entry) => stringValue(objectValue(entry).resourceId) === id);
      return balance ? finiteNumber(objectValue(balance).quantity, 0, 0) : 0;
    }

    validateProposal(value) {
      const resolved = this.resolveProposal(value);
      const proposal = objectValue(resolved.proposal);
      if (resolved.failure) {
        return {valid: false, ...resolved.failure, proposal: clone(proposal)};
      }

      const receipt = this.receipts.get(stringValue(proposal.decisionId));
      if (!receipt) {
        return this.failure("receipt-not-found", `Decision receipt ${proposal.decisionId} is missing`, proposal);
      }
      if (stringValue(receipt.actorId) !== stringValue(proposal.actorId)) {
        return this.failure("actor-mismatch", "Proposal actor does not match its decision receipt", proposal);
      }
      if (stringValue(receipt.checkpointId) !== stringValue(proposal.checkpointId)) {
        return this.failure("checkpoint-mismatch", "Proposal checkpoint does not match its decision receipt", proposal);
      }
      if (stringValue(this.state.currentCheckpointId) !== stringValue(proposal.checkpointId)) {
        return this.failure("checkpoint-stale", "Proposal checkpoint is no longer current", proposal);
      }
      if (!Number.isInteger(receipt.canonicalRevision) || receipt.canonicalRevision < 0) {
        return this.failure(
          "canonical-revision-unbound",
          "Decision receipt has no canonical revision binding",
          proposal
        );
      }
      if (proposal.canonicalRevision !== receipt.canonicalRevision) {
        return this.failure(
          "canonical-revision-mismatch",
          "Proposal canonical revision does not match its decision receipt",
          proposal
        );
      }
      const currentRevision = integerValue(
        objectValue(this.state.canonicalState).revision,
        0,
        0
      );
      if (proposal.canonicalRevision !== currentRevision) {
        return this.failure(
          "canonical-revision-stale",
          `Proposal revision ${proposal.canonicalRevision} does not match current revision ${currentRevision}`,
          proposal
        );
      }
      if (stringValue(receipt.selectedActionTypeId) !== stringValue(proposal.actionTypeId)) {
        return this.failure("action-mismatch", "Proposal action does not match the selected intention", proposal);
      }

      const actor = this.actors.get(stringValue(proposal.actorId));
      if (!actor) return this.failure("actor-not-found", `Actor ${proposal.actorId} is missing`, proposal);
      const action = this.actions.get(stringValue(proposal.actionTypeId));
      if (!action) return this.failure("action-not-found", `Action ${proposal.actionTypeId} is missing`, proposal);

      const expectedEffects = arrayValue(action.effectTypeIds).map(stringValue).sort();
      const receiptEffects = arrayValue(receipt.expectedEffectTypeIds).map(stringValue).sort();
      if (!valuesEqual(expectedEffects, receiptEffects)) {
        return this.failure(
          "decision-effect-mismatch",
          "Decision receipt effects do not match the selected action definition",
          proposal
        );
      }

      const actorAuthorities = new Set(arrayValue(actor.authorityIds).map(stringValue));
      const missingActionAuthority = arrayValue(action.requiredAuthorityIds)
        .map(stringValue)
        .find((authorityId) => !actorAuthorities.has(authorityId));
      if (missingActionAuthority) {
        return this.failure(
          "missing-authority",
          `Actor ${proposal.actorId} lacks authority ${missingActionAuthority}`,
          proposal
        );
      }

      const locationId = stringValue(proposal.locationId);
      if (locationId !== stringValue(actor.localDestinationId)
          || !arrayValue(action.allowedLocationIds).map(stringValue).includes(locationId)) {
        return this.failure(
          "wrong-location",
          `Action ${proposal.actionTypeId} is not allowed at ${locationId}`,
          proposal
        );
      }

      for (const precondition of arrayValue(action.preconditions)) {
        const raw = objectValue(precondition);
        if (stringValue(raw.kind) !== "fact-equals") {
          return this.failure(
            "unsupported-precondition",
            `Unsupported precondition ${stringValue(raw.kind)}`,
            proposal
          );
        }
        const actual = this.canonicalFactValue(raw.factId);
        if (!valuesEqual(actual, raw.expectedValue)) {
          return this.failure(
            "precondition-failed",
            `Fact ${raw.factId} does not equal the required value`,
            proposal
          );
        }
      }

      for (const cost of arrayValue(action.resourceCosts)) {
        const raw = objectValue(cost);
        const amount = finiteNumber(raw.amount, 0, 0);
        const available = this.resourceQuantity(raw.resourceId);
        if (amount <= 0 || available < amount) {
          return this.failure(
            "resource-unavailable",
            `Resource ${raw.resourceId} requires ${amount} but has ${available}`,
            proposal
          );
        }
      }

      const requestedEffects = arrayValue(proposal.requestedEffects);
      const requestedIds = requestedEffects.map(
        (effect) => stringValue(objectValue(effect).effectTypeId)
      ).sort();
      if (!valuesEqual(expectedEffects, requestedIds)) {
        return this.failure(
          "effect-not-allowed",
          "Requested effects do not exactly match the action effect allowlist",
          proposal
        );
      }

      const effectRecords = [];
      for (const requested of requestedEffects) {
        const effectId = stringValue(objectValue(requested).effectTypeId);
        const effect = this.effects.get(effectId);
        if (!effect) {
          return this.failure("effect-not-found", `Effect ${effectId} is missing`, proposal);
        }
        if (effect.protected === true) {
          const required = arrayValue(effect.requiredAuthorityIds).map(stringValue);
          const missing = required.find((authorityId) => !actorAuthorities.has(authorityId));
          if (!required.length || missing) {
            return this.failure(
              "protected-effect-forbidden",
              missing
                ? `Protected effect ${effectId} requires authority ${missing}`
                : `Protected effect ${effectId} has no explicit authority gate`,
              proposal
            );
          }
        }
        effectRecords.push({definition: effect, request: requested});
      }

      return {
        valid: true,
        code: "",
        reason: "",
        proposal: clone(proposal),
        actor: clone(actor),
        action: clone(action),
        effects: clone(effectRecords),
        resourceCosts: clone(arrayValue(action.resourceCosts))
      };
    }

    failure(code, reason, proposal) {
      return {
        valid: false,
        code: stringValue(code),
        reason: stringValue(reason),
        proposal: clone(objectValue(proposal))
      };
    }

    nextOutcomeId(proposalId) {
      return `outcome.runtime.${idSlug(proposalId)}`;
    }

    removePending(proposal) {
      const actorState = this.actorStates.get(stringValue(objectValue(proposal).actorId));
      if (!actorState) return;
      const proposalId = stringValue(objectValue(proposal).proposalId);
      actorState.pendingProposalIds = actorState.pendingProposalIds
        .filter((id) => stringValue(id) !== proposalId);
    }

    rejectedOutcome(proposal, code, reason) {
      const revision = integerValue(objectValue(this.state.canonicalState).revision, 0, 0);
      return {
        outcomeId: this.nextOutcomeId(proposal.proposalId),
        proposalId: stringValue(proposal.proposalId),
        decisionId: stringValue(proposal.decisionId),
        actorId: stringValue(proposal.actorId),
        actionTypeId: stringValue(proposal.actionTypeId),
        status: "rejected",
        rejectionCode: stringValue(code),
        rejectionReason: stringValue(reason),
        committedEffectTypeIds: [],
        consumedResources: [],
        resultingObservationIds: [],
        canonicalRevisionBefore: revision,
        canonicalRevisionAfter: revision
      };
    }

    applyResourceCosts(canonicalState, costs) {
      const consumed = [];
      for (const cost of arrayValue(costs)) {
        const raw = objectValue(cost);
        const resourceId = stringValue(raw.resourceId);
        const amount = finiteNumber(raw.amount, 0, 0);
        const balance = arrayValue(canonicalState.resourceBalances)
          .find((entry) => stringValue(objectValue(entry).resourceId) === resourceId);
        if (!balance || amount <= 0 || finiteNumber(balance.quantity, 0, 0) < amount) {
          throw new StrategicAIActionError(`Resource ${resourceId} became unavailable during commit`);
        }
        balance.quantity = finiteNumber(balance.quantity, 0, 0) - amount;
        if (balance.quantity < 0) {
          throw new StrategicAIActionError(`Resource ${resourceId} would become negative`);
        }
        consumed.push({resourceId, amount});
      }
      return consumed;
    }

    applyEffects(canonicalState, validation) {
      const committedEffectTypeIds = [];
      const proposal = objectValue(validation.proposal);
      arrayValue(validation.effects).forEach((entry, index) => {
        const raw = objectValue(entry);
        const effect = objectValue(raw.definition);
        const request = objectValue(raw.request);
        const effectId = stringValue(effect.id);
        const operation = stringValue(effect.operation);
        if (operation === "append-event") {
          canonicalState.events.push({
            eventId: `event.runtime.${idSlug(proposal.proposalId)}.${index + 1}`,
            effectTypeId: effectId,
            actorId: stringValue(proposal.actorId),
            proposalId: stringValue(proposal.proposalId),
            checkpointId: stringValue(proposal.checkpointId),
            committedAt: integerValue(proposal.createdAt, 0, 0),
            payload: clone(objectValue(request.payload))
          });
        } else if (operation === "set-fact") {
          const factId = stringValue(effect.targetFactId);
          const factState = canonicalState.factStates.find(
            (entryState) => stringValue(objectValue(entryState).factId) === factId
          );
          if (!factState) {
            throw new StrategicAIActionError(`Effect ${effectId} targets missing fact ${factId}`);
          }
          factState.value = clone(effect.value);
        } else {
          throw new StrategicAIActionError(
            `Effect ${effectId} has unsupported operation ${operation}`
          );
        }
        committedEffectTypeIds.push(effectId);
      });
      return committedEffectTypeIds;
    }

    buildResultingObservations(validation, existingObservationIds) {
      const proposal = objectValue(validation.proposal);
      const observations = [];
      arrayValue(objectValue(validation.action).resultObservationTemplates)
        .forEach((template) => {
          const raw = objectValue(template);
          const observationId = `observation.runtime.${idSlug(proposal.proposalId)}.${idSlug(raw.idSuffix)}`;
          if (existingObservationIds.has(observationId)) {
            throw new StrategicAIActionError(`Resulting observation ${observationId} already exists`);
          }
          const actor = this.actors.get(stringValue(raw.observerId));
          if (!actor) {
            throw new StrategicAIActionError(`Resulting observation actor ${raw.observerId} is missing`);
          }
          if (!arrayValue(actor.observationChannelIds).map(stringValue).includes(stringValue(raw.channelId))) {
            throw new StrategicAIActionError(
              `Actor ${raw.observerId} cannot receive result channel ${raw.channelId}`
            );
          }
          if (!this.channels.has(stringValue(raw.channelId))) {
            throw new StrategicAIActionError(`Result channel ${raw.channelId} is missing`);
          }
          if (!this.sources.has(stringValue(raw.sourceId))) {
            throw new StrategicAIActionError(`Result source ${raw.sourceId} is missing`);
          }
          observations.push({
            id: observationId,
            observerId: stringValue(raw.observerId),
            proposition: clone(objectValue(raw.proposition)),
            channelId: stringValue(raw.channelId),
            sourceId: stringValue(raw.sourceId),
            reliability: finiteNumber(raw.reliability, 0, 0, 1),
            observedAt: integerValue(proposal.createdAt, 0, 0),
            visibility: stringValue(raw.visibility)
          });
          existingObservationIds.add(observationId);
        });
      return observations;
    }

    commitProposal(value) {
      const resolved = this.resolveProposal(value);
      const proposal = objectValue(resolved.proposal);
      if (!resolved.stored) {
        throw new StrategicAIActionError(
          resolved.failure ? resolved.failure.reason : "Unregistered action proposal"
        );
      }
      const existing = this.outcomesByProposal.get(stringValue(proposal.proposalId));
      if (existing) return clone(existing);

      const validation = this.validateProposal(value);
      if (!validation.valid) {
        const outcome = this.rejectedOutcome(proposal, validation.code, validation.reason);
        this.state.outcomes.push(outcome);
        this.removePending(proposal);
        this.refreshIndexes();
        return clone(outcome);
      }

      const canonicalBefore = clone(this.state.canonicalState);
      const canonicalCandidate = clone(this.state.canonicalState);
      const observationsCandidate = clone(this.state.observations);
      try {
        const consumedResources = this.applyResourceCosts(
          canonicalCandidate,
          validation.resourceCosts
        );
        const committedEffectTypeIds = this.applyEffects(canonicalCandidate, validation);
        const existingObservationIds = new Set(
          observationsCandidate.map((observation) => stringValue(objectValue(observation).id))
        );
        const resultingObservations = this.buildResultingObservations(
          validation,
          existingObservationIds
        );
        observationsCandidate.push(...resultingObservations);
        canonicalCandidate.revision = integerValue(canonicalBefore.revision, 0, 0) + 1;

        const outcome = {
          outcomeId: this.nextOutcomeId(proposal.proposalId),
          proposalId: stringValue(proposal.proposalId),
          decisionId: stringValue(proposal.decisionId),
          actorId: stringValue(proposal.actorId),
          actionTypeId: stringValue(proposal.actionTypeId),
          status: "accepted",
          rejectionCode: "",
          rejectionReason: "",
          committedEffectTypeIds,
          consumedResources,
          resultingObservationIds: resultingObservations.map((observation) => observation.id),
          canonicalRevisionBefore: integerValue(canonicalBefore.revision, 0, 0),
          canonicalRevisionAfter: integerValue(canonicalCandidate.revision, 0, 0)
        };

        this.state.canonicalState = canonicalCandidate;
        this.state.observations = observationsCandidate;
        this.state.outcomes.push(outcome);
        this.removePending(proposal);
        this.refreshIndexes();
        return clone(outcome);
      } catch (error) {
        const reason = error instanceof Error ? error.message : String(error);
        const outcome = this.rejectedOutcome(proposal, "effect-commit-failed", reason);
        this.state.outcomes.push(outcome);
        this.removePending(proposal);
        this.refreshIndexes();
        return clone(outcome);
      }
    }

    snapshot() {
      return clone(this.state);
    }

    getProposals() {
      return clone(this.state.proposals);
    }

    getOutcomes() {
      return clone(this.state.outcomes);
    }

    getCanonicalState() {
      return clone(this.state.canonicalState);
    }
  }

  function create(definition, options = {}) {
    return new StrategicAIActionRuntime(definition, options);
  }

  const api = {
    SCHEMA,
    DEFINITION_VERSION,
    STATE_VERSION,
    LEGACY_STATE_VERSION,
    PREVIOUS_STATE_VERSION,
    COORDINATOR_STATE_VERSION,
    SOCIAL_STATE_VERSION,
    COMMITMENT_STATE_VERSION,
    DIRECTOR_STATE_VERSION,
    LEGACY_STATE_VERSIONS: LEGACY_STATE_VERSIONS.slice(),
    StrategicAIActionError,
    StrategicAIActionRuntime,
    defaultCanonicalState,
    defaultCaptainModels,
    defaultCooperationModels,
    defaultOpportunityStates,
    defaultOffscreenStepStates,
    migrateState,
    validateDefinition,
    create
  };

  global.MainComputerStrategicAIActionRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
