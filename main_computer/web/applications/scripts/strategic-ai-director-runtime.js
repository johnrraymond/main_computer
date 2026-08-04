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
  const OPPORTUNITY_PREDICATE = "predicate.campaign.opportunity-window-active";

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
    if (arrayValue(definition.campaignOpportunities).length < 2) {
      errors.push("campaignOpportunities must contain at least two authored routes");
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

  class StrategicAIDirectorError extends Error {
    constructor(message, details = []) {
      super(message);
      this.name = "StrategicAIDirectorError";
      this.details = arrayValue(details).slice();
    }
  }

  class StrategicAIDirectorRuntime {
    constructor(definition, options = {}) {
      this.definition = clone(objectValue(definition));
      this.report = validateDefinition(this.definition);
      if (!this.report.valid) {
        throw new StrategicAIDirectorError(
          "Invalid strategic AI director definition",
          this.report.errors
        );
      }

      this.opportunities = indexById(this.definition.campaignOpportunities);
      this.actors = indexById(this.definition.actors);
      this.channels = indexById(this.definition.observationChannels);
      this.sources = indexById(this.definition.sources);
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
        throw new StrategicAIDirectorError(
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
      this.state.campaignOpportunityStates = arrayValue(
        this.state.campaignOpportunityStates
      ).map(clone);
      this.state.directorReceipts = arrayValue(this.state.directorReceipts).map(clone);

      const existing = new Set(
        this.state.campaignOpportunityStates.map(
          (record) => stringValue(objectValue(record).opportunityId)
        )
      );
      defaultOpportunityStates(this.definition).forEach((record) => {
        if (!existing.has(record.opportunityId)) {
          this.state.campaignOpportunityStates.push(record);
        }
      });
      this.refreshIndexes();

      this.opportunities.forEach((_opportunity, opportunityId) => {
        if (!this.opportunityStates.has(opportunityId)) {
          throw new StrategicAIDirectorError(
            `Missing campaign opportunity state ${opportunityId}`
          );
        }
      });
    }

    refreshIndexes() {
      this.observations = indexById(this.state.observations);
      this.opportunityStates = indexById(
        this.state.campaignOpportunityStates,
        "opportunityId"
      );
      this.directorReceipts = indexById(
        this.state.directorReceipts,
        "directorReceiptId"
      );
    }

    opportunity(opportunityId) {
      const id = stringValue(opportunityId);
      const opportunity = this.opportunities.get(id);
      if (!opportunity) {
        throw new StrategicAIDirectorError(`Unknown campaign opportunity ${id}`);
      }
      return opportunity;
    }

    opportunityState(opportunityId) {
      const id = stringValue(opportunityId);
      this.opportunity(id);
      const state = this.opportunityStates.get(id);
      if (!state) {
        throw new StrategicAIDirectorError(`Missing campaign opportunity state ${id}`);
      }
      return state;
    }

    checkpoint(checkpointId = null) {
      const id = stringValue(checkpointId || this.state.currentCheckpointId);
      const checkpoint = this.checkpoints.get(id);
      if (!checkpoint) {
        throw new StrategicAIDirectorError(`Unknown strategic checkpoint ${id}`);
      }
      return checkpoint;
    }

    currentRevision() {
      return integerValue(
        objectValue(objectValue(this.state).canonicalState).revision,
        0,
        0
      );
    }

    nextReceiptId(operation, opportunityId) {
      return (
        `director-receipt.runtime.${idSlug(operation)}.`
        + `${idSlug(opportunityId)}.${this.state.directorReceipts.length + 1}`
      );
    }

    validateOpportunityBindings(opportunity) {
      const channelId = stringValue(opportunity.channelId);
      const sourceId = stringValue(opportunity.sourceId);
      const channel = this.channels.get(channelId);
      const source = this.sources.get(sourceId);
      if (!channel) {
        throw new StrategicAIDirectorError(
          `Campaign opportunity ${opportunity.id} uses missing channel ${channelId}`
        );
      }
      if (!source || stringValue(source.kind) !== "system") {
        throw new StrategicAIDirectorError(
          `Campaign opportunity ${opportunity.id} must use a system source`
        );
      }
      arrayValue(opportunity.observerIds).map(stringValue).forEach((actorId) => {
        const actor = this.actors.get(actorId);
        if (!actor) {
          throw new StrategicAIDirectorError(
            `Campaign opportunity ${opportunity.id} references missing observer ${actorId}`
          );
        }
        if (!arrayValue(actor.observationChannelIds).map(stringValue).includes(channelId)) {
          throw new StrategicAIDirectorError(
            `Actor ${actorId} cannot receive campaign channel ${channelId}`
          );
        }
      });
    }

    buildObservations(opportunity, receiptId, active, observedAt) {
      this.validateOpportunityBindings(opportunity);
      return arrayValue(opportunity.observerIds)
        .map(stringValue)
        .sort()
        .map((actorId) => {
          const observationId = (
            `observation.director.${idSlug(receiptId)}.${idSlug(actorId)}`
          );
          if (this.observations.has(observationId)) {
            throw new StrategicAIDirectorError(
              `Duplicate director observation ${observationId}`
            );
          }
          return {
            id: observationId,
            observerId: actorId,
            proposition: {
              predicate: OPPORTUNITY_PREDICATE,
              arguments: [
                stringValue(opportunity.id),
                stringValue(opportunity.routeSystemId)
              ],
              value: Boolean(active)
            },
            channelId: stringValue(opportunity.channelId),
            sourceId: stringValue(opportunity.sourceId),
            reliability: probability(opportunity.reliability, 1),
            observedAt: integerValue(observedAt, 0, 0),
            visibility: stringValue(opportunity.visibility || "faction")
          };
        });
    }

    selectOpportunityForRoute(routeSystemId, checkpointId) {
      const routeId = stringValue(routeSystemId);
      const candidates = arrayValue(this.definition.campaignOpportunities)
        .filter((opportunity) => (
          stringValue(objectValue(opportunity).routeSystemId) === routeId
          && arrayValue(objectValue(opportunity).checkpointIds)
            .map(stringValue)
            .includes(stringValue(checkpointId))
        ))
        .sort((left, right) => stringValue(left.id).localeCompare(stringValue(right.id)));
      if (!candidates.length) {
        throw new StrategicAIDirectorError(
          `No authored campaign opportunity for ${routeId} at ${checkpointId}`
        );
      }
      if (candidates.length > 1) {
        throw new StrategicAIDirectorError(
          `Ambiguous campaign opportunity for ${routeId} at ${checkpointId}`
        );
      }
      return candidates[0];
    }

    activateRoute(routeSystemId, options = {}) {
      const checkpoint = this.checkpoint(objectValue(options).checkpointId);
      const opportunity = this.selectOpportunityForRoute(
        routeSystemId,
        checkpoint.id
      );
      const opportunityState = this.opportunityState(opportunity.id);
      if (stringValue(opportunityState.status) !== "available") {
        throw new StrategicAIDirectorError(
          `Campaign opportunity ${opportunity.id} is ${opportunityState.status}`
        );
      }

      const selectedAt = integerValue(
        objectValue(options).selectedAt,
        integerValue(checkpoint.worldTime, 0, 0),
        0
      );
      const canonicalRevision = this.currentRevision();
      if (
        Object.prototype.hasOwnProperty.call(objectValue(options), "canonicalRevision")
        && integerValue(objectValue(options).canonicalRevision, -1, -1)
          !== canonicalRevision
      ) {
        throw new StrategicAIDirectorError(
          `Campaign director canonical revision is stale`
        );
      }

      const receiptId = stringValue(
        objectValue(options).directorReceiptId
        || this.nextReceiptId("activate", opportunity.id)
      );
      if (this.directorReceipts.has(receiptId)) {
        throw new StrategicAIDirectorError(`Duplicate director receipt ${receiptId}`);
      }
      const expiresAt = selectedAt + integerValue(opportunity.windowDuration, 1, 1);
      const observations = this.buildObservations(
        opportunity,
        receiptId,
        true,
        selectedAt
      );
      const receipt = {
        directorReceiptId: receiptId,
        operation: "activate",
        opportunityId: stringValue(opportunity.id),
        routeSystemId: stringValue(opportunity.routeSystemId),
        checkpointId: stringValue(checkpoint.id),
        selectedAt,
        canonicalRevision,
        previousStatus: "available",
        nextStatus: "active",
        observationIds: observations.map((observation) => observation.id),
        expiresAt,
        reason: stringValue(objectValue(options).reason || "authored-route-selected")
      };

      opportunityState.status = "active";
      opportunityState.activatedAt = selectedAt;
      opportunityState.expiresAt = expiresAt;
      opportunityState.activationReceiptId = receiptId;
      opportunityState.activationCount = integerValue(
        opportunityState.activationCount,
        0,
        0
      ) + 1;
      this.state.observations.push(...observations);
      this.state.directorReceipts.push(receipt);
      this.refreshIndexes();
      return {
        opportunity: clone(opportunity),
        opportunityState: clone(opportunityState),
        receipt: clone(receipt),
        observations: clone(observations)
      };
    }

    transitionActiveOpportunity(opportunityId, operation, selectedAt, reason) {
      const opportunity = this.opportunity(opportunityId);
      const opportunityState = this.opportunityState(opportunity.id);
      if (stringValue(opportunityState.status) !== "active") {
        throw new StrategicAIDirectorError(
          `Campaign opportunity ${opportunity.id} is not active`
        );
      }
      const checkpoint = this.checkpoint();
      const receiptId = this.nextReceiptId(operation, opportunity.id);
      const observations = this.buildObservations(
        opportunity,
        receiptId,
        false,
        selectedAt
      );
      const nextStatus = operation === "deactivate" ? "available" : "closed";
      const receipt = {
        directorReceiptId: receiptId,
        operation,
        opportunityId: stringValue(opportunity.id),
        routeSystemId: stringValue(opportunity.routeSystemId),
        checkpointId: stringValue(checkpoint.id),
        selectedAt,
        canonicalRevision: this.currentRevision(),
        previousStatus: "active",
        nextStatus,
        observationIds: observations.map((observation) => observation.id),
        expiresAt: null,
        reason
      };

      opportunityState.status = nextStatus;
      opportunityState.activatedAt = null;
      opportunityState.expiresAt = null;
      opportunityState.activationReceiptId = null;
      this.state.observations.push(...observations);
      this.state.directorReceipts.push(receipt);
      this.refreshIndexes();
      return {
        opportunity: clone(opportunity),
        opportunityState: clone(opportunityState),
        receipt: clone(receipt),
        observations: clone(observations)
      };
    }

    deactivateOpportunity(opportunityId, options = {}) {
      const selectedAt = integerValue(
        objectValue(options).selectedAt,
        integerValue(this.checkpoint().worldTime, 0, 0),
        0
      );
      return this.transitionActiveOpportunity(
        opportunityId,
        "deactivate",
        selectedAt,
        stringValue(objectValue(options).reason || "route-selection-reversed")
      );
    }

    expireOpportunities(worldTime) {
      const now = integerValue(worldTime, 0, 0);
      const expired = [];
      this.state.campaignOpportunityStates
        .filter((state) => (
          stringValue(objectValue(state).status) === "active"
          && integerValue(objectValue(state).expiresAt, Number.MAX_SAFE_INTEGER, 0)
            <= now
        ))
        .slice()
        .sort((left, right) => (
          stringValue(left.opportunityId).localeCompare(stringValue(right.opportunityId))
        ))
        .forEach((state) => {
          expired.push(
            this.transitionActiveOpportunity(
              state.opportunityId,
              "expire",
              now,
              "authored-window-expired"
            )
          );
        });
      return clone(expired);
    }

    snapshot() {
      return clone(this.state);
    }

    getOpportunityStates() {
      return clone(this.state.campaignOpportunityStates);
    }

    getDirectorReceipts() {
      return clone(this.state.directorReceipts);
    }
  }

  function create(definition, options = {}) {
    return new StrategicAIDirectorRuntime(definition, options);
  }

  const api = {
    SCHEMA,
    DEFINITION_VERSION,
    STATE_VERSION,
    LEGACY_STATE_VERSIONS: LEGACY_STATE_VERSIONS.slice(),
    OPPORTUNITY_PREDICATE,
    StrategicAIDirectorError,
    StrategicAIDirectorRuntime,
    defaultOpportunityStates,
    defaultOffscreenStepStates,
    migrateState,
    validateDefinition,
    create
  };

  global.MainComputerStrategicAIDirectorRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
