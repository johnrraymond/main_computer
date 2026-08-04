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

  function resolveCoordinatorApi() {
    if (global.MainComputerStrategicAICoordinator) {
      return global.MainComputerStrategicAICoordinator;
    }
    if (typeof module !== "undefined" && module.exports) {
      return require("./strategic-ai-coordinator.js");
    }
    return null;
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

  function migrateState(value, definition) {
    const state = clone(objectValue(value));
    if (!LEGACY_STATE_VERSIONS.includes(stringValue(state.stateVersion))) return state;
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
    if (!Number.isInteger(definition.offscreenSimulationBudget)
        || definition.offscreenSimulationBudget < 1) {
      errors.push("offscreenSimulationBudget must be a positive integer");
    }
    if (!arrayValue(definition.offscreenSchedules).length) {
      errors.push("offscreenSchedules must be a non-empty list");
    }
    return {valid: errors.length === 0, errors};
  }

  class StrategicAIOffscreenError extends Error {
    constructor(message, code = "offscreen-invalid", details = []) {
      super(message);
      this.name = "StrategicAIOffscreenError";
      this.code = stringValue(code || "offscreen-invalid");
      this.details = arrayValue(details).slice();
    }
  }

  class StrategicAIOffscreenRuntime {
    constructor(definition, options = {}) {
      this.definition = clone(objectValue(definition));
      this.report = validateDefinition(this.definition);
      if (!this.report.valid) {
        throw new StrategicAIOffscreenError(
          "Invalid strategic AI off-screen definition",
          "definition-invalid",
          this.report.errors
        );
      }

      this.seed = integerValue(options.seed, 1, 0);
      this.coordinatorApi = objectValue(options.coordinatorApi);
      if (!Object.keys(this.coordinatorApi).length) {
        this.coordinatorApi = resolveCoordinatorApi();
      }
      if (!this.coordinatorApi
          || this.coordinatorApi.DEFINITION_VERSION !== DEFINITION_VERSION
          || this.coordinatorApi.STATE_VERSION !== STATE_VERSION) {
        throw new StrategicAIOffscreenError(
          "Strategic AI coordinator v8 must be loaded before off-screen simulation",
          "coordinator-unavailable"
        );
      }

      this.schedules = indexById(this.definition.offscreenSchedules);
      this.actors = indexById(this.definition.actors);
      this.actions = indexById(this.definition.actionTypes);
      this.effects = indexById(this.definition.effectTypes);
      this.routes = indexById(this.definition.reportRoutes);
      this.commitmentTypes = indexById(this.definition.commitmentTypes);
      this.opportunities = indexById(this.definition.campaignOpportunities);
      this.intents = indexById(this.definition.communicativeIntents);
      this.validateSchedules();

      this.state = migrateState(
        options.state === undefined
          ? objectValue(this.definition.stateDefaults)
          : objectValue(options.state),
        this.definition
      );
      this.normalizeState();
    }

    validateSchedules() {
      const errors = [];
      const seenSteps = new Set();
      arrayValue(this.definition.offscreenSchedules).forEach((schedule) => {
        const rawSchedule = objectValue(schedule);
        const scheduleId = stringValue(rawSchedule.id);
        const systemId = stringValue(rawSchedule.systemId);
        arrayValue(rawSchedule.steps).forEach((step) => {
          const raw = objectValue(step);
          const stepId = stringValue(raw.id);
          if (seenSteps.has(stepId)) errors.push(`duplicate off-screen step ${stepId}`);
          seenSteps.add(stepId);
          const kind = stringValue(raw.kind);

          if (kind === "actor-turn") {
            const actor = this.actors.get(stringValue(raw.actorId));
            if (!actor) {
              errors.push(`off-screen step ${stepId} references missing actor`);
              return;
            }
            if (stringValue(actor.systemId) !== systemId) {
              errors.push(`off-screen step ${stepId} actor is outside schedule system`);
            }
            const actorActions = new Set(arrayValue(actor.candidateActionTypeIds).map(stringValue));
            const authorities = new Set(arrayValue(actor.authorityIds).map(stringValue));
            arrayValue(raw.allowedActionTypeIds).map(stringValue).forEach((actionId) => {
              const action = this.actions.get(actionId);
              if (!action || !actorActions.has(actionId)) {
                errors.push(`off-screen step ${stepId} does not authorize actor action ${actionId}`);
                return;
              }
              const protectedEffects = arrayValue(action.effectTypeIds)
                .map((effectId) => this.effects.get(stringValue(effectId)))
                .filter((effect) => effect && objectValue(effect).protected === true);
              if (protectedEffects.length) {
                if (!Number.isInteger(raw.deadlineAt) || raw.deadlineAt < 0) {
                  errors.push(`protected off-screen step ${stepId} requires an explicit deadline`);
                }
                const required = new Set(arrayValue(action.requiredAuthorityIds).map(stringValue));
                protectedEffects.forEach((effect) => {
                  arrayValue(effect.requiredAuthorityIds).map(stringValue)
                    .forEach((authorityId) => required.add(authorityId));
                });
                const missing = [...required].filter((authorityId) => !authorities.has(authorityId));
                if (missing.length) {
                  errors.push(
                    `protected off-screen step ${stepId} lacks authorities ${missing.sort().join(",")}`
                  );
                }
              }
            });
          } else if (kind === "report") {
            const route = this.routes.get(stringValue(raw.routeId));
            const sender = this.actors.get(stringValue(raw.senderActorId));
            if (!route) errors.push(`off-screen step ${stepId} references missing report route`);
            if (!sender || stringValue(sender.systemId) !== systemId) {
              errors.push(`off-screen step ${stepId} report sender is outside schedule system`);
            }
          } else if (kind === "commitment") {
            const promisor = this.actors.get(stringValue(raw.promisorActorId));
            if (!this.commitmentTypes.has(stringValue(raw.commitmentTypeId))) {
              errors.push(`off-screen step ${stepId} references missing commitment type`);
            }
            if (!promisor || stringValue(promisor.systemId) !== systemId) {
              errors.push(`off-screen step ${stepId} promisor is outside schedule system`);
            }
          } else if (kind === "director") {
            if (stringValue(raw.operation) === "activate-route"
                && stringValue(raw.routeSystemId) !== systemId) {
              errors.push(`off-screen step ${stepId} activates a different system`);
            }
            if (stringValue(raw.operation) === "deactivate-opportunity"
                && !this.opportunities.has(stringValue(raw.opportunityId))) {
              errors.push(`off-screen step ${stepId} references missing opportunity`);
            }
          } else if (kind === "communication") {
            const speaker = this.actors.get(stringValue(raw.speakerActorId));
            if (!this.intents.has(stringValue(raw.intentId))) {
              errors.push(`off-screen step ${stepId} references missing communication intent`);
            }
            if (!speaker || stringValue(speaker.systemId) !== systemId) {
              errors.push(`off-screen step ${stepId} speaker is outside schedule system`);
            }
          } else {
            errors.push(`off-screen step ${stepId} has unsupported kind ${kind}`);
          }
        });
      });
      if (errors.length) {
        throw new StrategicAIOffscreenError(
          "Invalid authored off-screen schedules",
          "schedule-invalid",
          errors
        );
      }
    }

    normalizeState() {
      if (this.state.stateVersion !== STATE_VERSION) {
        throw new StrategicAIOffscreenError(
          `Strategic AI state version must be ${STATE_VERSION}`,
          "state-version-invalid"
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

      const existing = new Set(
        this.state.offscreenStepStates.map(
          (record) => `${stringValue(record.scheduleId)}|${stringValue(record.stepId)}`
        )
      );
      defaultOffscreenStepStates(this.definition).forEach((record) => {
        const key = `${record.scheduleId}|${record.stepId}`;
        if (!existing.has(key)) this.state.offscreenStepStates.push(record);
      });
      this.refreshIndexes();
    }

    refreshIndexes() {
      this.stepStates = new Map();
      this.state.offscreenStepStates.forEach((record) => {
        const raw = objectValue(record);
        this.stepStates.set(
          `${stringValue(raw.scheduleId)}|${stringValue(raw.stepId)}`,
          raw
        );
      });
    }

    stepState(scheduleId, stepId) {
      const key = `${stringValue(scheduleId)}|${stringValue(stepId)}`;
      const record = this.stepStates.get(key);
      if (!record) {
        throw new StrategicAIOffscreenError(
          `Missing off-screen step state ${key}`,
          "step-state-missing"
        );
      }
      return record;
    }

    nextReceiptId() {
      return `offscreen-receipt.runtime.${this.state.offscreenSimulationReceipts.length + 1}`;
    }

    coordinator() {
      return this.coordinatorApi.create(this.definition, {
        state: this.state,
        seed: this.seed
      });
    }

    commitmentIdFromStep(scheduleId, stepId) {
      if (!stepId) return "";
      const state = this.stepState(scheduleId, stepId);
      return stringValue(arrayValue(state.resultIds)[0]);
    }

    executeStep(schedule, step, completedAt) {
      const raw = objectValue(step);
      const kind = stringValue(raw.kind);
      const coordinator = this.coordinator();
      let resultIds = [];
      let rejected = false;
      let reason = "completed";

      if (kind === "actor-turn") {
        const actor = this.actors.get(stringValue(raw.actorId));
        const allowed = new Set(arrayValue(raw.allowedActionTypeIds).map(stringValue));
        const availability = {};
        const rejectionReasons = {};
        arrayValue(actor.candidateActionTypeIds).map(stringValue).forEach((actionId) => {
          availability[actionId] = allowed.has(actionId);
          if (!allowed.has(actionId)) {
            rejectionReasons[actionId] = "off-screen schedule does not authorize action";
          }
        });
        const turn = coordinator.runTurn(raw.actorId, {
          decisionContext: {availability, rejectionReasons},
          proposalOptions: {createdAt: completedAt}
        });
        resultIds = [
          turn.turnId,
          objectValue(turn.decision).decisionId,
          objectValue(turn.proposal).proposalId,
          objectValue(turn.outcome).outcomeId
        ].map(stringValue).filter(Boolean);
        rejected = stringValue(objectValue(turn.outcome).status) !== "accepted";
        reason = rejected
          ? stringValue(objectValue(turn.outcome).rejectionCode || "action-rejected")
          : "verified-action-completed";
      } else if (kind === "report") {
        const delivery = coordinator.deliverReport(
          raw.routeId,
          raw.senderActorId,
          raw.recipientActorId,
          raw.sourceObservationId,
          {sentAt: integerValue(raw.dueAt, 0, 0)}
        );
        resultIds = [
          objectValue(delivery.report).reportId,
          objectValue(delivery.observation).id
        ].map(stringValue).filter(Boolean);
        reason = "authored-report-delivered";
      } else if (kind === "commitment") {
        const commitment = coordinator.createCommitment(
          raw.commitmentTypeId,
          raw.promisorActorId,
          raw.promiseeActorId,
          {createdAt: completedAt}
        );
        resultIds = [stringValue(commitment.commitmentId)].filter(Boolean);
        reason = "typed-commitment-created";
      } else if (kind === "director") {
        const operation = stringValue(raw.operation);
        if (operation === "activate-route") {
          const transition = coordinator.activateCampaignRoute(raw.routeSystemId, {
            selectedAt: completedAt,
            canonicalRevision: integerValue(
              objectValue(this.state.canonicalState).revision,
              0,
              0
            ),
            reason: "off-screen-authored-route"
          });
          resultIds = [
            objectValue(transition.receipt).directorReceiptId,
            ...arrayValue(transition.observationIds)
          ].map(stringValue).filter(Boolean);
        } else if (operation === "deactivate-opportunity") {
          const transition = coordinator.deactivateCampaignOpportunity(
            raw.opportunityId,
            {selectedAt: completedAt, reason: "off-screen-authored-reversal"}
          );
          resultIds = [
            objectValue(transition.receipt).directorReceiptId,
            ...arrayValue(transition.observationIds)
          ].map(stringValue).filter(Boolean);
        } else {
          const transitions = coordinator.expireCampaignOpportunities(completedAt);
          resultIds = transitions.flatMap((transition) => [
            objectValue(transition.receipt).directorReceiptId,
            ...arrayValue(transition.observationIds)
          ]).map(stringValue).filter(Boolean);
        }
        reason = "director-boundary-completed";
      } else if (kind === "communication") {
        const commitmentId = this.commitmentIdFromStep(
          stringValue(schedule.id),
          stringValue(raw.commitmentStepId)
        );
        const communication = coordinator.performCommunication(
          raw.intentId,
          raw.speakerActorId,
          raw.audienceActorIds,
          commitmentId ? {commitmentId} : {}
        );
        resultIds = [stringValue(communication.communicationId)].filter(Boolean);
        reason = "knowledge-safe-communication-rendered";
      }

      this.state = coordinator.snapshot();
      this.refreshIndexes();
      return {resultIds: [...new Set(resultIds)], rejected, reason};
    }

    simulateUntil(targetTime, options = {}) {
      const toTime = integerValue(targetTime, -1, -1);
      const fromTime = integerValue(this.state.offscreenSimulationTime, 0, 0);
      if (toTime < fromTime) {
        throw new StrategicAIOffscreenError(
          "Off-screen simulation cannot move backward",
          "time-reversal"
        );
      }
      const activeSystemId = stringValue(objectValue(options).activeSystemId);
      if (!activeSystemId) {
        throw new StrategicAIOffscreenError(
          "Active system id is required",
          "active-system-required"
        );
      }
      const declaredBudget = integerValue(
        this.definition.offscreenSimulationBudget,
        1,
        1
      );
      const requestedBudget = Object.prototype.hasOwnProperty.call(
        objectValue(options),
        "budget"
      )
        ? integerValue(objectValue(options).budget, 0, 0)
        : declaredBudget;
      const budget = Math.min(declaredBudget, requestedBudget);
      let remaining = budget;
      const processedStepIds = [];
      const deferredStepIds = [];
      const skippedScheduleIds = [];
      const canonicalBefore = integerValue(
        objectValue(this.state.canonicalState).revision,
        0,
        0
      );

      const candidates = [];
      arrayValue(this.definition.offscreenSchedules)
        .slice()
        .sort((left, right) => stringValue(left.id).localeCompare(stringValue(right.id)))
        .forEach((schedule) => {
          if (stringValue(schedule.systemId) === activeSystemId) {
            skippedScheduleIds.push(stringValue(schedule.id));
            return;
          }
          arrayValue(schedule.steps).forEach((step) => {
            const state = this.stepState(schedule.id, step.id);
            if (stringValue(state.status) !== "pending") return;
            if (integerValue(state.readyAt, 0, 0) > toTime) return;
            candidates.push({schedule, step, state});
          });
        });

      candidates.sort((left, right) => {
        const timeDelta = integerValue(left.state.readyAt) - integerValue(right.state.readyAt);
        if (timeDelta) return timeDelta;
        const scheduleDelta = stringValue(left.schedule.id).localeCompare(
          stringValue(right.schedule.id)
        );
        if (scheduleDelta) return scheduleDelta;
        return stringValue(left.step.id).localeCompare(stringValue(right.step.id));
      });

      candidates.forEach(({schedule, step, state}) => {
        const cost = integerValue(objectValue(step).cost, 1, 1);
        if (cost > remaining) {
          deferredStepIds.push(stringValue(step.id));
          return;
        }
        state.attempts = integerValue(state.attempts, 0, 0) + 1;
        try {
          const execution = this.executeStep(
            schedule,
            step,
            integerValue(state.readyAt, toTime, 0)
          );
          const refreshed = this.stepState(schedule.id, step.id);
          refreshed.status = execution.rejected ? "rejected" : "completed";
          refreshed.attempts = state.attempts;
          refreshed.completedAt = integerValue(state.readyAt, toTime, 0);
          refreshed.resultIds = clone(execution.resultIds);
          refreshed.reason = stringValue(execution.reason);
        } catch (error) {
          const refreshed = this.stepState(schedule.id, step.id);
          refreshed.status = "rejected";
          refreshed.attempts = state.attempts;
          refreshed.completedAt = integerValue(state.readyAt, toTime, 0);
          refreshed.resultIds = [];
          refreshed.reason = stringValue(
            objectValue(error).code || objectValue(error).message || "execution-error"
          );
        }
        remaining -= cost;
        processedStepIds.push(stringValue(step.id));
      });

      this.state.offscreenSimulationTime = toTime;
      const receipt = {
        simulationReceiptId: this.nextReceiptId(),
        fromTime,
        toTime,
        activeSystemId,
        budget,
        consumedBudget: budget - remaining,
        processedStepIds: [...new Set(processedStepIds)],
        deferredStepIds: [...new Set(deferredStepIds)],
        skippedScheduleIds: [...new Set(skippedScheduleIds)],
        canonicalRevisionBefore: canonicalBefore,
        canonicalRevisionAfter: integerValue(
          objectValue(this.state.canonicalState).revision,
          0,
          0
        )
      };
      this.state.offscreenSimulationReceipts.push(receipt);
      this.refreshIndexes();
      return {
        receipt: clone(receipt),
        summaries: arrayValue(this.definition.offscreenSchedules)
          .filter((schedule) => stringValue(schedule.systemId) !== activeSystemId)
          .map((schedule) => this.getReturnSummary(schedule.systemId)),
        state: clone(this.state)
      };
    }

    getReturnSummary(systemId) {
      const id = stringValue(systemId);
      const schedules = arrayValue(this.definition.offscreenSchedules)
        .filter((schedule) => stringValue(schedule.systemId) === id)
        .sort((left, right) => stringValue(left.id).localeCompare(stringValue(right.id)));
      return {
        systemId: id,
        simulationTime: integerValue(this.state.offscreenSimulationTime, 0, 0),
        canonicalRevision: integerValue(
          objectValue(this.state.canonicalState).revision,
          0,
          0
        ),
        schedules: schedules.map((schedule) => ({
          scheduleId: stringValue(schedule.id),
          label: stringValue(schedule.label),
          steps: arrayValue(schedule.steps).map((step) => {
            const state = this.stepState(schedule.id, step.id);
            return {
              stepId: stringValue(step.id),
              kind: stringValue(step.kind),
              description: stringValue(step.description),
              status: stringValue(state.status),
              readyAt: integerValue(state.readyAt, 0, 0),
              completedAt: state.completedAt === null
                ? null
                : integerValue(state.completedAt, 0, 0),
              reason: stringValue(state.reason),
              resultIds: clone(arrayValue(state.resultIds))
            };
          })
        }))
      };
    }

    snapshot() {
      return clone(this.state);
    }
  }

  function create(definition, options = {}) {
    return new StrategicAIOffscreenRuntime(definition, options);
  }

  const api = {
    SCHEMA,
    DEFINITION_VERSION,
    STATE_VERSION,
    LEGACY_STATE_VERSIONS: LEGACY_STATE_VERSIONS.slice(),
    StrategicAIOffscreenError,
    StrategicAIOffscreenRuntime,
    defaultOffscreenStepStates,
    migrateState,
    validateDefinition,
    create
  };

  global.MainComputerStrategicAIOffscreenRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
