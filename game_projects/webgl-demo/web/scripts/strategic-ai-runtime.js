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
  const DEFAULT_SEED = 1;
  const SCORE_METRICS = Object.freeze([
    "goalPriority",
    "evidenceSupport",
    "uncertainty",
    "memoryRelevance",
    "observationReliability",
    "captainCooperation",
    "captainEvidenceDiscipline",
    "captainAuthorityResistance",
    "commitmentTrust"
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

  function probability(value, fallback = 0) {
    return finiteNumber(value, fallback, 0, 1);
  }

  function integerValue(value, fallback = 0, minimum = 0) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(minimum, Math.trunc(parsed));
  }

  function roundNumber(value, places = 6) {
    const scale = 10 ** places;
    return Math.round((Number(value) + Number.EPSILON) * scale) / scale;
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

  function hashString(value) {
    const text = String(value);
    let hash = 2166136261;
    for (let index = 0; index < text.length; index += 1) {
      hash ^= text.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function deterministicRank(seed, ...parts) {
    return hashString([integerValue(seed, DEFAULT_SEED), ...parts].join("|"));
  }

  function idSlug(value) {
    return stringValue(value)
      .replace(/[^a-zA-Z0-9._-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "record";
  }

  function propositionKey(proposition) {
    const raw = objectValue(proposition);
    return `${stringValue(raw.predicate)}|${stableStringify(arrayValue(raw.arguments))}`;
  }

  function propositionsMatch(left, right) {
    return propositionKey(left) === propositionKey(right);
  }

  function propositionValuesMatch(left, right) {
    return propositionsMatch(left, right)
      && objectValue(left).value === objectValue(right).value;
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


  function definitionFromProject(project) {
    return objectValue(objectValue(project).metadata).strategicAI || null;
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
        trust: probability(raw.initialTrust, 0.5),
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
    if (!arrayValue(definition.checkpoints).length) errors.push("checkpoints must be a non-empty list");

    const collections = [
      ["sources", "id"],
      ["observationChannels", "id"],
      ["effectTypes", "id"],
      ["actionTypes", "id"],
      ["policyProfiles", "id"],
      ["reportRoutes", "id"],
      ["captainModelProfiles", "id"],
      ["commitmentTypes", "id"],
      ["cooperationProfiles", "id"],
      ["campaignOpportunities", "id"],
      ["actors", "id"],
      ["facts", "id"],
      ["evidence", "id"],
      ["goals", "id"],
      ["checkpoints", "id"]
    ];
    const seen = new Set();
    collections.forEach(([collection, key]) => {
      arrayValue(definition[collection]).forEach((record, index) => {
        const id = stringValue(objectValue(record)[key]);
        if (!id) errors.push(`${collection}[${index}] is missing ${key}`);
        else if (seen.has(id)) errors.push(`duplicate strategic AI id ${id}`);
        else seen.add(id);
      });
    });

    const state = objectValue(definition.stateDefaults);
    if (state.stateVersion !== STATE_VERSION) {
      errors.push(`stateDefaults.stateVersion must be ${STATE_VERSION}`);
    }
    if (!stringValue(state.currentCheckpointId)) {
      errors.push("stateDefaults.currentCheckpointId must be set");
    }
    if (!Number.isInteger(definition.offscreenSimulationBudget)
        || definition.offscreenSimulationBudget < 1) {
      errors.push("offscreenSimulationBudget must be a positive integer");
    }
    if (!arrayValue(definition.offscreenSchedules).length) {
      errors.push("offscreenSchedules must be a non-empty list");
    }
    return {valid: errors.length === 0, errors};
  }

  class StrategicAIDefinitionError extends Error {
    constructor(message, details = []) {
      super(message);
      this.name = "StrategicAIDefinitionError";
      this.details = arrayValue(details).slice();
    }
  }

  class StrategicAIRuntime {
    constructor(definition, options = {}) {
      this.definition = clone(objectValue(definition));
      this.report = validateDefinition(this.definition);
      if (!this.report.valid) {
        throw new StrategicAIDefinitionError(
          "Invalid strategic AI definition",
          this.report.errors
        );
      }

      this.seed = integerValue(options.seed, DEFAULT_SEED, 0);
      this.actionPolicies = clone(objectValue(options.actionPolicies));
      this.policyProfiles = indexById(this.definition.policyProfiles);
      this.sources = indexById(this.definition.sources);
      this.channels = indexById(this.definition.observationChannels);
      this.effects = indexById(this.definition.effectTypes);
      this.actions = indexById(this.definition.actionTypes);
      this.actors = indexById(this.definition.actors);
      this.facts = indexById(this.definition.facts);
      this.evidence = indexById(this.definition.evidence);
      this.goals = indexById(this.definition.goals);
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
        throw new StrategicAIDefinitionError(
          `Strategic AI state version must be ${STATE_VERSION}`
        );
      }
      if (!this.checkpoints.has(stringValue(this.state.currentCheckpointId))) {
        throw new StrategicAIDefinitionError(
          `Unknown strategic AI checkpoint ${stringValue(this.state.currentCheckpointId)}`
        );
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
      this.state.observations = arrayValue(this.state.observations).map((record) => clone(record));
      this.state.beliefs = arrayValue(this.state.beliefs).map((record) => clone(record));
      this.state.memories = arrayValue(this.state.memories).map((record) => clone(record));
      this.state.receipts = arrayValue(this.state.receipts).map((record) => clone(record));
      const canonical = objectValue(this.state.canonicalState);
      this.state.canonicalState = {
        revision: integerValue(canonical.revision, 0, 0),
        factStates: arrayValue(canonical.factStates).map((record) => clone(record)),
        resourceBalances: arrayValue(canonical.resourceBalances).map((record) => clone(record)),
        events: arrayValue(canonical.events).map((record) => clone(record))
      };
      this.state.proposals = arrayValue(this.state.proposals).map((record) => clone(record));
      this.state.outcomes = arrayValue(this.state.outcomes).map((record) => clone(record));
      this.state.reports = arrayValue(this.state.reports).map((record) => clone(record));
      this.state.captainModels = arrayValue(this.state.captainModels).map((record) => clone(record));
      this.state.commitments = arrayValue(this.state.commitments).map((record) => clone(record));
      this.state.cooperationModels = arrayValue(this.state.cooperationModels).map((record) => clone(record));
      this.state.campaignOpportunityStates = arrayValue(this.state.campaignOpportunityStates).map((record) => clone(record));
      this.state.directorReceipts = arrayValue(this.state.directorReceipts).map((record) => clone(record));

      this.refreshStateIndexes();
      this.actors.forEach((_actor, actorId) => {
        if (!this.actorStates.has(actorId)) {
          this.state.actorStates.push({
            actorId,
            activeGoalIds: [],
            beliefIds: [],
            memoryIds: [],
            pendingProposalIds: []
          });
        }
      });
      this.refreshStateIndexes();
    }

    refreshStateIndexes() {
      this.actorStates = indexById(this.state.actorStates, "actorId");
      this.observations = indexById(this.state.observations);
      this.beliefs = indexById(this.state.beliefs);
      this.memories = indexById(this.state.memories);
      this.receipts = indexById(this.state.receipts, "decisionId");
      this.proposals = indexById(this.state.proposals, "proposalId");
      this.outcomes = indexById(this.state.outcomes, "outcomeId");
      this.reports = indexById(this.state.reports, "reportId");
      this.captainModels = indexById(this.state.captainModels, "modelId");
      this.commitments = indexById(this.state.commitments, "commitmentId");
      this.cooperationModels = indexById(this.state.cooperationModels, "modelId");
      this.campaignOpportunityStates = indexById(this.state.campaignOpportunityStates, "opportunityId");
      this.directorReceipts = indexById(this.state.directorReceipts, "directorReceiptId");
    }

    actor(actorId) {
      const id = stringValue(actorId);
      const actor = this.actors.get(id);
      if (!actor) throw new StrategicAIDefinitionError(`Unknown strategic actor ${id}`);
      return actor;
    }

    actorState(actorId) {
      this.actor(actorId);
      const state = this.actorStates.get(stringValue(actorId));
      if (!state) {
        throw new StrategicAIDefinitionError(`Missing strategic actor state ${stringValue(actorId)}`);
      }
      return state;
    }

    checkpoint(checkpointId = null) {
      const id = stringValue(checkpointId || this.state.currentCheckpointId);
      const checkpoint = this.checkpoints.get(id);
      if (!checkpoint) throw new StrategicAIDefinitionError(`Unknown strategic AI checkpoint ${id}`);
      return checkpoint;
    }

    actorBeliefs(actorId) {
      const state = this.actorState(actorId);
      return state.beliefIds
        .map((beliefId) => this.beliefs.get(beliefId))
        .filter(Boolean)
        .map(clone);
    }

    actorMemories(actorId) {
      const state = this.actorState(actorId);
      return state.memoryIds
        .map((memoryId) => this.memories.get(memoryId))
        .filter(Boolean)
        .map(clone);
    }

    ingestObservation(observation) {
      const raw = clone(objectValue(observation));
      const id = stringValue(raw.id);
      const observerId = stringValue(raw.observerId);
      const channelId = stringValue(raw.channelId);
      const sourceId = stringValue(raw.sourceId);
      if (!id) throw new StrategicAIDefinitionError("Observation id is required");
      if (this.observations.has(id)) {
        throw new StrategicAIDefinitionError(`Duplicate strategic observation ${id}`);
      }
      const actor = this.actor(observerId);
      if (!this.channels.has(channelId)) {
        throw new StrategicAIDefinitionError(`Unknown observation channel ${channelId}`);
      }
      if (!arrayValue(actor.observationChannelIds).includes(channelId)) {
        throw new StrategicAIDefinitionError(
          `Actor ${observerId} cannot observe through channel ${channelId}`
        );
      }
      if (!this.sources.has(sourceId)) {
        throw new StrategicAIDefinitionError(`Unknown observation source ${sourceId}`);
      }
      if (!stringValue(objectValue(raw.proposition).predicate)) {
        throw new StrategicAIDefinitionError("Observation proposition predicate is required");
      }

      raw.observerId = observerId;
      raw.channelId = channelId;
      raw.sourceId = sourceId;
      raw.reliability = probability(
        raw.reliability,
        probability(objectValue(this.channels.get(channelId)).defaultReliability, 0.5)
      );
      raw.observedAt = integerValue(raw.observedAt, integerValue(this.checkpoint().worldTime, 0), 0);
      raw.visibility = stringValue(raw.visibility || "private");

      this.state.observations.push(raw);
      this.refreshStateIndexes();
      return clone(raw);
    }

    createBeliefFromObservation(actorId, observation) {
      const keyHash = hashString(
        `${actorId}|${propositionKey(observation.proposition)}|${stableStringify(objectValue(observation.proposition).value)}`
      ).toString(16);
      const baseId = `belief.runtime.${idSlug(actorId)}.${keyHash}`;
      let id = baseId;
      let suffix = 2;
      while (this.beliefs.has(id)) {
        id = `${baseId}.${suffix}`;
        suffix += 1;
      }
      return {
        id,
        holderId: actorId,
        proposition: clone(observation.proposition),
        confidence: roundNumber(probability(observation.reliability) * 0.75),
        basisIds: [stringValue(observation.id)],
        updatedAt: integerValue(observation.observedAt, 0, 0),
        visibility: "private"
      };
    }

    updateBeliefs(actorId, observationIds = null) {
      const id = stringValue(actorId);
      const state = this.actorState(id);
      const requested = observationIds === null
        ? null
        : new Set(arrayValue(observationIds).map(stringValue));
      const actorObservations = this.state.observations.filter((record) => {
        const raw = objectValue(record);
        return stringValue(raw.observerId) === id
          && (requested === null || requested.has(stringValue(raw.id)));
      });
      const changed = [];

      actorObservations
        .slice()
        .sort((left, right) => {
          const timeDelta = integerValue(left.observedAt) - integerValue(right.observedAt);
          if (timeDelta) return timeDelta;
          return stringValue(left.id).localeCompare(stringValue(right.id));
        })
        .forEach((observation) => {
          const observationId = stringValue(observation.id);
          const matching = state.beliefIds
            .map((beliefId) => this.beliefs.get(beliefId))
            .filter((belief) => belief && propositionsMatch(belief.proposition, observation.proposition));
          let hasSupportingBelief = false;

          matching.forEach((belief) => {
            if (arrayValue(belief.basisIds).includes(observationId)) return;
            const reliability = probability(observation.reliability, 0.5);
            const current = probability(belief.confidence, 0.5);
            const supports = propositionValuesMatch(belief.proposition, observation.proposition);
            hasSupportingBelief = hasSupportingBelief || supports;
            const next = supports
              ? current + ((1 - current) * reliability * 0.5)
              : current * (1 - (reliability * 0.75));
            belief.confidence = roundNumber(probability(next));
            belief.basisIds = [...new Set([...arrayValue(belief.basisIds), observationId])];
            belief.updatedAt = Math.max(
              integerValue(belief.updatedAt, 0),
              integerValue(observation.observedAt, 0)
            );
            changed.push(clone(belief));
          });

          if (!hasSupportingBelief) {
            const belief = this.createBeliefFromObservation(id, observation);
            this.state.beliefs.push(belief);
            state.beliefIds.push(belief.id);
            changed.push(clone(belief));
            this.refreshStateIndexes();
          }
        });

      this.refreshStateIndexes();
      return changed;
    }

    relevantSourceIds(context = {}) {
      const ids = new Set(arrayValue(objectValue(context).sourceIds).map(stringValue));
      const proposition = objectValue(objectValue(context).proposition);
      if (stringValue(proposition.predicate)) {
        this.state.observations.forEach((observation) => {
          if (propositionsMatch(observation.proposition, proposition)) ids.add(stringValue(observation.id));
        });
        this.state.beliefs.forEach((belief) => {
          if (propositionsMatch(belief.proposition, proposition)) ids.add(stringValue(belief.id));
        });
      }
      return ids;
    }

    retrieveMemories(actorId, context = {}, limit = 5) {
      const id = stringValue(actorId);
      const checkpoint = this.checkpoint(objectValue(context).checkpointId);
      const now = integerValue(
        objectValue(context).worldTime,
        integerValue(checkpoint.worldTime, 0),
        0
      );
      const relevantIds = this.relevantSourceIds(context);
      const maximum = Math.max(0, integerValue(limit, 5, 0));

      return this.actorMemories(id)
        .map((memory) => {
          const sources = arrayValue(memory.sourceIds).map(stringValue);
          const overlap = sources.filter((sourceId) => relevantIds.has(sourceId)).length;
          const relevance = sources.length ? overlap / sources.length : 0;
          const age = Math.max(0, now - integerValue(memory.recordedAt, 0));
          const recency = 1 / (1 + (age / 1000));
          const score = roundNumber(
            (probability(memory.salience) * 0.55)
            + (probability(relevance) * 0.25)
            + (probability(recency) * 0.20)
          );
          return {...clone(memory), retrievalScore: score};
        })
        .sort((left, right) => (
          right.retrievalScore - left.retrievalScore
          || stringValue(left.id).localeCompare(stringValue(right.id))
        ))
        .slice(0, maximum);
    }

    beliefMetrics(actorId) {
      const groups = new Map();
      this.actorBeliefs(actorId).forEach((belief) => {
        const key = propositionKey(belief.proposition);
        if (!groups.has(key)) groups.set(key, {truthy: 0, falsy: 0});
        const group = groups.get(key);
        if (objectValue(belief.proposition).value === true) {
          group.truthy = Math.max(group.truthy, probability(belief.confidence));
        } else if (objectValue(belief.proposition).value === false) {
          group.falsy = Math.max(group.falsy, probability(belief.confidence));
        }
      });

      let dominantBeliefConfidence = 0;
      let uncertainty = 1;
      let evidenceSupport = 0;
      if (groups.size) {
        uncertainty = 0;
        groups.forEach((group) => {
          const dominant = Math.max(group.truthy, group.falsy);
          const opposing = Math.min(group.truthy, group.falsy);
          const groupUncertainty = group.truthy > 0 && group.falsy > 0
            ? 1 - Math.abs(group.truthy - group.falsy)
            : 1 - dominant;
          const groupSupport = dominant * (1 - (probability(groupUncertainty) * 0.5));
          dominantBeliefConfidence = Math.max(dominantBeliefConfidence, dominant);
          uncertainty = Math.max(uncertainty, probability(groupUncertainty));
          evidenceSupport = Math.max(evidenceSupport, probability(groupSupport));
          if (opposing > dominant) {
            uncertainty = Math.max(uncertainty, 1);
          }
        });
      }

      return {
        dominantBeliefConfidence: roundNumber(dominantBeliefConfidence),
        uncertainty: roundNumber(probability(uncertainty)),
        evidenceSupport: roundNumber(probability(evidenceSupport))
      };
    }

    actorMetrics(actorId, context = {}) {
      const state = this.actorState(actorId);
      const goalPriorities = state.activeGoalIds
        .map((goalId) => this.goals.get(goalId))
        .filter(Boolean)
        .map((goal) => probability(goal.priority));
      const goalPriority = goalPriorities.length
        ? goalPriorities.reduce((total, value) => total + value, 0) / goalPriorities.length
        : 0;

      const actorObservations = this.state.observations.filter(
        (record) => stringValue(record.observerId) === stringValue(actorId)
      );
      const observationReliability = actorObservations.length
        ? actorObservations.reduce((total, record) => total + probability(record.reliability), 0)
          / actorObservations.length
        : 0;

      const memories = this.retrieveMemories(actorId, context, 1);
      const beliefs = this.beliefMetrics(actorId);
      const cooperationModels = this.state.cooperationModels.filter(
        (model) => stringValue(objectValue(model).holderActorId) === stringValue(actorId)
      );
      const commitmentTrust = cooperationModels.length
        ? cooperationModels.reduce(
          (total, model) => total + probability(objectValue(model).trust, 0.5),
          0
        ) / cooperationModels.length
        : 0;
      const metrics = {
        goalPriority: roundNumber(probability(goalPriority)),
        evidenceSupport: beliefs.evidenceSupport,
        uncertainty: beliefs.uncertainty,
        memoryRelevance: memories.length ? probability(memories[0].retrievalScore) : 0,
        observationReliability: roundNumber(probability(observationReliability)),
        captainCooperation: 0,
        captainEvidenceDiscipline: 0,
        captainAuthorityResistance: 0,
        commitmentTrust: roundNumber(probability(commitmentTrust))
      };

      const overrides = objectValue(objectValue(context).metricOverrides);
      SCORE_METRICS.forEach((metric) => {
        if (Object.prototype.hasOwnProperty.call(overrides, metric)) {
          metrics[metric] = roundNumber(probability(overrides[metric]));
        }
      });
      return metrics;
    }

    actorPolicyProfile(actorId) {
      const actor = this.actor(actorId);
      const profileId = stringValue(actor.policyProfileId);
      const profile = this.policyProfiles.get(profileId);
      if (!profile) {
        throw new StrategicAIDefinitionError(
          `Actor ${stringValue(actorId)} references missing policy profile ${profileId}`
        );
      }
      return profile;
    }

    actionPolicy(actorId, actionTypeId, context = {}) {
      const contextPolicies = objectValue(objectValue(context).actionPolicies);
      const profile = this.actorPolicyProfile(actorId);
      const authoredPolicies = objectValue(profile.actionPolicies);
      const policy = objectValue(
        contextPolicies[actionTypeId]
        || this.actionPolicies[actionTypeId]
        || authoredPolicies[actionTypeId]
      );
      if (!Object.keys(policy).length) {
        throw new StrategicAIDefinitionError(
          `Policy profile ${stringValue(profile.id)} has no action policy for ${actionTypeId}`
        );
      }
      return {
        profileId: stringValue(profile.id),
        baseScore: finiteNumber(policy.baseScore, 0),
        weights: objectValue(policy.weights)
      };
    }

    evaluateCandidates(actorId, context = {}) {
      const actor = this.actor(actorId);
      const metrics = this.actorMetrics(actorId, context);
      const availability = objectValue(objectValue(context).availability);
      const rejectionReasons = objectValue(objectValue(context).rejectionReasons);
      const actorAuthorities = new Set(arrayValue(actor.authorityIds).map(stringValue));
      const candidateActions = [];
      const rejections = [];

      arrayValue(actor.candidateActionTypeIds)
        .map(stringValue)
        .sort()
        .forEach((actionTypeId) => {
          const action = this.actions.get(actionTypeId);
          if (!action) {
            rejections.push({actionTypeId, reason: "unknown action type"});
            return;
          }
          const missingAuthority = arrayValue(action.requiredAuthorityIds)
            .map(stringValue)
            .find((authorityId) => !actorAuthorities.has(authorityId));
          if (missingAuthority) {
            rejections.push({
              actionTypeId,
              reason: `missing authority ${missingAuthority}`
            });
            return;
          }
          if (availability[actionTypeId] === false) {
            rejections.push({
              actionTypeId,
              reason: stringValue(rejectionReasons[actionTypeId] || "action unavailable")
            });
            return;
          }

          const policy = this.actionPolicy(actorId, actionTypeId, context);
          const scoreComponents = {baseScore: roundNumber(policy.baseScore)};
          let score = policy.baseScore;
          SCORE_METRICS.forEach((metric) => {
            const weight = finiteNumber(policy.weights[metric], 0);
            const component = metrics[metric] * weight;
            scoreComponents[metric] = roundNumber(component);
            score += component;
          });
          candidateActions.push({
            actionTypeId,
            score: roundNumber(score),
            scoreComponents
          });
        });

      const checkpointId = stringValue(objectValue(context).checkpointId || this.state.currentCheckpointId);
      candidateActions.sort((left, right) => {
        const scoreDelta = right.score - left.score;
        if (Math.abs(scoreDelta) > 1e-9) return scoreDelta;
        return deterministicRank(this.seed, actorId, checkpointId, left.actionTypeId)
          - deterministicRank(this.seed, actorId, checkpointId, right.actionTypeId);
      });
      rejections.sort((left, right) => left.actionTypeId.localeCompare(right.actionTypeId));

      return {
        actorId: stringValue(actorId),
        checkpointId,
        policyProfileId: stringValue(this.actorPolicyProfile(actorId).id),
        canonicalRevision: integerValue(objectValue(this.state.canonicalState).revision, 0, 0),
        metrics,
        candidateActions,
        rejections
      };
    }

    nextDecisionId(actorId, checkpointId) {
      const ordinal = this.state.receipts.length + 1;
      return `decision.runtime.${idSlug(actorId)}.${idSlug(checkpointId)}.${ordinal}`;
    }

    decide(actorId, checkpointId = null, context = {}) {
      const id = stringValue(actorId);
      const checkpoint = this.checkpoint(checkpointId);
      const evaluation = this.evaluateCandidates(id, {
        ...objectValue(context),
        checkpointId: stringValue(checkpoint.id)
      });
      const selected = evaluation.candidateActions[0];
      if (!selected) {
        throw new StrategicAIDefinitionError(
          `No available strategic action for ${id}`,
          evaluation.rejections
        );
      }
      const second = evaluation.candidateActions[1];
      const margin = second ? Math.max(0, selected.score - second.score) : Math.max(0, selected.score);
      const confidence = roundNumber(probability(0.5 + (0.5 * Math.tanh(margin))));
      const actorState = this.actorState(id);
      const action = this.actions.get(selected.actionTypeId);
      const receipt = {
        decisionId: this.nextDecisionId(id, stringValue(checkpoint.id)),
        actorId: id,
        checkpointId: stringValue(checkpoint.id),
        policyProfileId: evaluation.policyProfileId,
        canonicalRevision: evaluation.canonicalRevision,
        activeGoalIds: actorState.activeGoalIds.slice(),
        beliefIds: actorState.beliefIds.slice(),
        candidateActions: clone(evaluation.candidateActions),
        rejections: clone(evaluation.rejections),
        selectedActionTypeId: selected.actionTypeId,
        expectedEffectTypeIds: arrayValue(action.effectTypeIds).map(stringValue),
        confidence,
        randomSeed: this.seed
      };
      this.state.receipts.push(receipt);
      this.refreshStateIndexes();
      return clone(receipt);
    }

    snapshot() {
      return clone(this.state);
    }

    getReceipts() {
      return clone(this.state.receipts);
    }

    canonicalFacts() {
      return clone(this.definition.facts);
    }
  }

  function create(definition, options = {}) {
    return new StrategicAIRuntime(definition, options);
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
    DEFAULT_SEED,
    SCORE_METRICS: SCORE_METRICS.slice(),
    StrategicAIDefinitionError,
    StrategicAIRuntime,
    definitionFromProject,
    defaultCanonicalState,
    defaultCaptainModels,
    defaultCooperationModels,
    defaultOpportunityStates,
    defaultOffscreenStepStates,
    migrateState,
    validateDefinition,
    create
  };

  global.MainComputerStrategicAIRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
