(function (global) {
  "use strict";

  const SCHEMA = "game.strategicAI.v1";
  const DEFINITION_VERSION = "game.strategicAI.definition.v8";
  const STATE_VERSION = "game.strategicAI.state.v8";
  const LEGACY_STATE_VERSIONS = Object.freeze([
    "game.strategicAI.state.v1",
    "game.strategicAI.state.v2",
    "game.strategicAI.state.v3",
    "game.strategicAI.state.v4",
    "game.strategicAI.state.v5",
    "game.strategicAI.state.v6",
    "game.strategicAI.state.v7"
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
    if (!LEGACY_STATE_VERSIONS.includes(stringValue(state.stateVersion))) return state;
    if (!Array.isArray(state.commitments)) state.commitments = [];
    if (!Array.isArray(state.cooperationModels)) {
      state.cooperationModels = defaultCooperationModels(definition);
    }
    if (!Array.isArray(state.campaignOpportunityStates)) {
      state.campaignOpportunityStates = defaultOpportunityStates(definition);
    }
    if (!Array.isArray(state.directorReceipts)) state.directorReceipts = [];
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
    if (!arrayValue(definition.commitmentTypes).length) {
      errors.push("commitmentTypes must be a non-empty list");
    }
    if (!arrayValue(definition.cooperationProfiles).length) {
      errors.push("cooperationProfiles must be a non-empty list");
    }
    if (arrayValue(definition.campaignOpportunities).length < 2) {
      errors.push("campaignOpportunities must contain at least two routes");
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

  class StrategicAICommitmentError extends Error {
    constructor(message, details = []) {
      super(message);
      this.name = "StrategicAICommitmentError";
      this.details = arrayValue(details).slice();
    }
  }

  class StrategicAICommitmentRuntime {
    constructor(definition, options = {}) {
      this.definition = clone(objectValue(definition));
      this.report = validateDefinition(this.definition);
      if (!this.report.valid) {
        throw new StrategicAICommitmentError(
          "Invalid strategic AI commitment definition",
          this.report.errors
        );
      }

      this.actors = indexById(this.definition.actors);
      this.actions = indexById(this.definition.actionTypes);
      this.resources = indexById(this.definition.resources);
      this.channels = indexById(this.definition.observationChannels);
      this.sources = indexById(this.definition.sources);
      this.commitmentTypes = indexById(this.definition.commitmentTypes);
      this.cooperationProfiles = indexById(this.definition.cooperationProfiles);
      this.profileByParties = new Map();
      this.cooperationProfiles.forEach((profile) => {
        const key = `${stringValue(profile.holderActorId)}|${stringValue(profile.subjectActorId)}`;
        this.profileByParties.set(key, profile);
      });

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
        throw new StrategicAICommitmentError(
          `Strategic AI state version must be ${STATE_VERSION}`
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
      this.state.observations = arrayValue(this.state.observations).map(clone);
      this.state.commitments = arrayValue(this.state.commitments).map(clone);
      this.state.cooperationModels = arrayValue(this.state.cooperationModels).map(clone);
      this.state.campaignOpportunityStates = arrayValue(this.state.campaignOpportunityStates).map(clone);
      this.state.directorReceipts = arrayValue(this.state.directorReceipts).map(clone);
      const existingProfiles = new Set(
        this.state.cooperationModels.map((model) => stringValue(objectValue(model).profileId))
      );
      defaultCooperationModels(this.definition).forEach((model) => {
        if (!existingProfiles.has(model.profileId)) this.state.cooperationModels.push(model);
      });
      this.refreshIndexes();
    }

    refreshIndexes() {
      this.observations = indexById(this.state.observations);
      this.commitments = indexById(this.state.commitments, "commitmentId");
      this.cooperationModels = indexById(this.state.cooperationModels, "modelId");
      this.modelByParties = new Map();
      this.state.cooperationModels.forEach((model) => {
        const raw = objectValue(model);
        const key = `${stringValue(raw.holderActorId)}|${stringValue(raw.subjectActorId)}`;
        this.modelByParties.set(key, raw);
      });
    }

    actor(actorId) {
      const id = stringValue(actorId);
      const actor = this.actors.get(id);
      if (!actor) throw new StrategicAICommitmentError(`Unknown strategic actor ${id}`);
      return actor;
    }

    commitmentType(commitmentTypeId) {
      const id = stringValue(commitmentTypeId);
      const type = this.commitmentTypes.get(id);
      if (!type) throw new StrategicAICommitmentError(`Unknown commitment type ${id}`);
      return type;
    }

    resourceQuantity(resourceId) {
      const canonical = objectValue(this.state.canonicalState);
      const balance = arrayValue(canonical.resourceBalances).find(
        (entry) => stringValue(objectValue(entry).resourceId) === stringValue(resourceId)
      );
      return balance ? finiteNumber(objectValue(balance).quantity, 0, 0) : 0;
    }

    nextCommitmentId(commitmentTypeId) {
      return `commitment.runtime.${idSlug(commitmentTypeId)}.${this.state.commitments.length + 1}`;
    }

    createCommitment(commitmentTypeId, promisorActorId, promiseeActorId, options = {}) {
      const type = this.commitmentType(commitmentTypeId);
      const promisorId = stringValue(promisorActorId);
      const promiseeId = stringValue(promiseeActorId);
      const promisor = this.actor(promisorId);
      this.actor(promiseeId);

      if (!arrayValue(type.promisorActorIds).map(stringValue).includes(promisorId)) {
        throw new StrategicAICommitmentError(
          `Actor ${promisorId} cannot make commitment ${type.id}`
        );
      }
      if (!arrayValue(type.promiseeActorIds).map(stringValue).includes(promiseeId)) {
        throw new StrategicAICommitmentError(
          `Actor ${promiseeId} cannot receive commitment ${type.id}`
        );
      }
      const authorities = new Set(arrayValue(promisor.authorityIds).map(stringValue));
      const missingAuthority = arrayValue(type.requiredAuthorityIds)
        .map(stringValue)
        .find((authorityId) => !authorities.has(authorityId));
      if (missingAuthority) {
        throw new StrategicAICommitmentError(
          `Actor ${promisorId} lacks commitment authority ${missingAuthority}`
        );
      }
      const promisedActionId = stringValue(type.promisedActionTypeId);
      if (!this.actions.has(promisedActionId)) {
        throw new StrategicAICommitmentError(
          `Commitment ${type.id} references missing action ${promisedActionId}`
        );
      }
      const resourceId = stringValue(type.resourceId);
      const amount = finiteNumber(type.resourceAmount, 0, 0);
      const available = this.resourceQuantity(resourceId);
      if (amount <= 0 || available < amount) {
        throw new StrategicAICommitmentError(
          `Commitment ${type.id} requires ${amount} of ${resourceId} but has ${available}`
        );
      }

      const commitmentId = stringValue(
        objectValue(options).commitmentId || this.nextCommitmentId(type.id)
      );
      if (this.commitments.has(commitmentId)) {
        throw new StrategicAICommitmentError(`Duplicate strategic commitment ${commitmentId}`);
      }
      const canonicalRevision = integerValue(
        objectValue(objectValue(this.state).canonicalState).revision,
        0,
        0
      );
      const commitment = {
        commitmentId,
        commitmentTypeId: stringValue(type.id),
        promisorActorId: promisorId,
        promiseeActorId: promiseeId,
        status: "pending",
        createdAt: integerValue(objectValue(options).createdAt, canonicalRevision, 0),
        canonicalRevisionCreated: canonicalRevision,
        resolutionOutcomeId: null,
        resolvedAt: null,
        canonicalRevisionResolved: null,
        resolutionReason: "",
        observationIds: []
      };
      this.state.commitments.push(commitment);
      this.refreshIndexes();
      return clone(commitment);
    }

    buildObservations(commitment, status, resolvedAt) {
      const type = this.commitmentType(commitment.commitmentTypeId);
      const templates = status === "kept"
        ? arrayValue(type.keptObservationTemplates)
        : arrayValue(type.brokenObservationTemplates);
      const observations = [];
      templates.forEach((template) => {
        const raw = objectValue(template);
        const observerId = stringValue(raw.observerId);
        const observer = this.actor(observerId);
        const channelId = stringValue(raw.channelId);
        const sourceId = stringValue(raw.sourceId);
        if (!this.channels.has(channelId)) {
          throw new StrategicAICommitmentError(`Missing commitment channel ${channelId}`);
        }
        if (!arrayValue(observer.observationChannelIds).map(stringValue).includes(channelId)) {
          throw new StrategicAICommitmentError(
            `Actor ${observerId} cannot receive commitment channel ${channelId}`
          );
        }
        if (!this.sources.has(sourceId)) {
          throw new StrategicAICommitmentError(`Missing commitment source ${sourceId}`);
        }
        const observationId = (
          `observation.commitment.${idSlug(commitment.commitmentId)}.${idSlug(raw.idSuffix)}`
        );
        if (this.observations.has(observationId)) {
          throw new StrategicAICommitmentError(
            `Duplicate commitment observation ${observationId}`
          );
        }
        observations.push({
          id: observationId,
          observerId,
          proposition: clone(objectValue(raw.proposition)),
          channelId,
          sourceId,
          reliability: probability(raw.reliability, 1),
          observedAt: integerValue(resolvedAt, 0, 0),
          visibility: stringValue(raw.visibility || "private")
        });
      });
      return observations;
    }

    updateCooperationModel(commitment, status, resolvedAt) {
      const key = `${stringValue(commitment.promiseeActorId)}|${stringValue(commitment.promisorActorId)}`;
      const profile = this.profileByParties.get(key);
      const model = this.modelByParties.get(key);
      if (!profile || !model) return null;
      const current = probability(model.trust, 0.5);
      const next = status === "kept"
        ? current + ((1 - current) * probability(profile.keptDelta))
        : current * (1 - probability(profile.brokenDelta));
      model.trust = roundNumber(probability(next));
      model.commitmentIds = [
        ...new Set([
          ...arrayValue(model.commitmentIds).map(stringValue),
          stringValue(commitment.commitmentId)
        ])
      ];
      model.updatedAt = Math.max(
        integerValue(model.updatedAt, 0, 0),
        integerValue(resolvedAt, 0, 0)
      );
      return clone(model);
    }

    evaluateOutcome(outcome, options = {}) {
      const rawOutcome = objectValue(outcome);
      if (stringValue(rawOutcome.status) !== "accepted") return [];
      const actionTypeId = stringValue(rawOutcome.actionTypeId);
      const actorId = stringValue(rawOutcome.actorId);
      const consumedByResource = new Map();
      arrayValue(rawOutcome.consumedResources).forEach((record) => {
        const raw = objectValue(record);
        consumedByResource.set(
          stringValue(raw.resourceId),
          finiteNumber(raw.amount, 0, 0)
        );
      });
      const resolvedAt = integerValue(
        objectValue(options).resolvedAt,
        integerValue(rawOutcome.canonicalRevisionAfter, 0, 0),
        0
      );
      const resolutions = [];

      this.state.commitments
        .filter((commitment) => stringValue(objectValue(commitment).status) === "pending")
        .slice()
        .sort((left, right) => (
          stringValue(left.commitmentId).localeCompare(stringValue(right.commitmentId))
        ))
        .forEach((commitment) => {
          const type = this.commitmentType(commitment.commitmentTypeId);
          const promisedActionId = stringValue(type.promisedActionTypeId);
          const resourceId = stringValue(type.resourceId);
          const requiredAmount = finiteNumber(type.resourceAmount, 0, 0);
          const consumedAmount = finiteNumber(consumedByResource.get(resourceId), 0, 0);

          let status = "";
          let reason = "";
          if (
            actorId === stringValue(commitment.promisorActorId)
            && actionTypeId === promisedActionId
          ) {
            status = "kept";
            reason = "promised-action-committed";
          } else if (consumedAmount >= requiredAmount && requiredAmount > 0) {
            status = "broken";
            reason = "pledged-resource-diverted";
          }
          if (!status) return;

          const observations = this.buildObservations(commitment, status, resolvedAt);
          commitment.status = status;
          commitment.resolutionOutcomeId = stringValue(rawOutcome.outcomeId);
          commitment.resolvedAt = resolvedAt;
          commitment.canonicalRevisionResolved = integerValue(
            rawOutcome.canonicalRevisionAfter,
            0,
            0
          );
          commitment.resolutionReason = reason;
          commitment.observationIds = observations.map((observation) => observation.id);
          this.state.observations.push(...observations);
          const cooperationModel = this.updateCooperationModel(
            commitment,
            status,
            resolvedAt
          );
          resolutions.push({
            commitment: clone(commitment),
            observations: clone(observations),
            cooperationModel
          });
        });

      this.refreshIndexes();
      return clone(resolutions);
    }

    cooperationMetrics(actorId) {
      const holderId = stringValue(actorId);
      const models = this.state.cooperationModels.filter(
        (model) => stringValue(objectValue(model).holderActorId) === holderId
      );
      const trust = models.length
        ? models.reduce(
          (total, model) => total + probability(objectValue(model).trust, 0.5),
          0
        ) / models.length
        : 0;
      return {commitmentTrust: roundNumber(probability(trust))};
    }

    snapshot() {
      return clone(this.state);
    }

    getCommitments() {
      return clone(this.state.commitments);
    }

    getCooperationModels() {
      return clone(this.state.cooperationModels);
    }
  }

  function create(definition, options = {}) {
    return new StrategicAICommitmentRuntime(definition, options);
  }

  const api = {
    SCHEMA,
    DEFINITION_VERSION,
    STATE_VERSION,
    LEGACY_STATE_VERSIONS: LEGACY_STATE_VERSIONS.slice(),
    StrategicAICommitmentError,
    StrategicAICommitmentRuntime,
    defaultCooperationModels,
    defaultOpportunityStates,
    defaultOffscreenStepStates,
    migrateState,
    validateDefinition,
    create
  };

  global.MainComputerStrategicAICommitmentRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
