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
    if (!LEGACY_STATE_VERSIONS.includes(stringValue(state.stateVersion))) return state;
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
    if (!arrayValue(definition.reportRoutes).length) {
      errors.push("reportRoutes must be a non-empty list");
    }
    if (!arrayValue(definition.captainModelProfiles).length) {
      errors.push("captainModelProfiles must be a non-empty list");
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

  class StrategicAISocialError extends Error {
    constructor(message, details = []) {
      super(message);
      this.name = "StrategicAISocialError";
      this.details = arrayValue(details).slice();
    }
  }

  class StrategicAISocialRuntime {
    constructor(definition, options = {}) {
      this.definition = clone(objectValue(definition));
      this.report = validateDefinition(this.definition);
      if (!this.report.valid) {
        throw new StrategicAISocialError(
          "Invalid strategic AI social definition",
          this.report.errors
        );
      }

      this.actors = indexById(this.definition.actors);
      this.channels = indexById(this.definition.observationChannels);
      this.routes = indexById(this.definition.reportRoutes);
      this.captainProfiles = indexById(this.definition.captainModelProfiles);
      this.profileByActor = new Map();
      this.captainProfiles.forEach((profile) => {
        this.profileByActor.set(stringValue(profile.actorId), profile);
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
        throw new StrategicAISocialError(
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
      this.state.reports = arrayValue(this.state.reports).map(clone);
      this.state.captainModels = arrayValue(this.state.captainModels).map(clone);
      this.state.commitments = arrayValue(this.state.commitments).map(clone);
      this.state.cooperationModels = arrayValue(this.state.cooperationModels).map(clone);
      this.state.campaignOpportunityStates = arrayValue(this.state.campaignOpportunityStates).map(clone);
      this.state.directorReceipts = arrayValue(this.state.directorReceipts).map(clone);
      const existingProfiles = new Set(
        this.state.captainModels.map((model) => stringValue(objectValue(model).profileId))
      );
      defaultCaptainModels(this.definition).forEach((model) => {
        if (!existingProfiles.has(model.profileId)) this.state.captainModels.push(model);
      });
      this.refreshIndexes();
    }

    refreshIndexes() {
      this.observations = indexById(this.state.observations);
      this.reports = indexById(this.state.reports, "reportId");
      this.captainModels = indexById(this.state.captainModels, "modelId");
      this.modelByActor = new Map();
      this.state.captainModels.forEach((model) => {
        this.modelByActor.set(stringValue(objectValue(model).holderActorId), model);
      });
    }

    actor(actorId) {
      const id = stringValue(actorId);
      const actor = this.actors.get(id);
      if (!actor) throw new StrategicAISocialError(`Unknown strategic actor ${id}`);
      return actor;
    }

    route(routeId) {
      const id = stringValue(routeId);
      const route = this.routes.get(id);
      if (!route) throw new StrategicAISocialError(`Unknown report route ${id}`);
      return route;
    }

    nextReportId(routeId) {
      return `report.runtime.${idSlug(routeId)}.${this.state.reports.length + 1}`;
    }

    createReport(routeId, senderActorId, recipientActorId, sourceObservationId, options = {}) {
      const route = this.route(routeId);
      const senderId = stringValue(senderActorId);
      const recipientId = stringValue(recipientActorId);
      this.actor(senderId);
      const recipient = this.actor(recipientId);
      if (!arrayValue(route.senderActorIds).map(stringValue).includes(senderId)) {
        throw new StrategicAISocialError(
          `Actor ${senderId} cannot send through route ${route.id}`
        );
      }
      if (!arrayValue(route.recipientActorIds).map(stringValue).includes(recipientId)) {
        throw new StrategicAISocialError(
          `Actor ${recipientId} cannot receive through route ${route.id}`
        );
      }
      const channelId = stringValue(route.channelId);
      if (!this.channels.has(channelId)) {
        throw new StrategicAISocialError(`Route ${route.id} uses missing channel ${channelId}`);
      }
      if (!arrayValue(recipient.observationChannelIds).map(stringValue).includes(channelId)) {
        throw new StrategicAISocialError(
          `Actor ${recipientId} cannot receive channel ${channelId}`
        );
      }

      const sourceId = stringValue(sourceObservationId);
      const source = this.observations.get(sourceId);
      if (!source) throw new StrategicAISocialError(`Unknown source observation ${sourceId}`);
      if (stringValue(source.observerId) !== senderId) {
        throw new StrategicAISocialError(
          `Actor ${senderId} did not observe ${sourceId}`
        );
      }
      const visibility = stringValue(source.visibility);
      if (!arrayValue(route.allowedVisibilities).map(stringValue).includes(visibility)) {
        throw new StrategicAISocialError(
          `Route ${route.id} cannot carry ${visibility} observations`
        );
      }
      if (stringValue(route.mode) === "public" && visibility !== "public") {
        throw new StrategicAISocialError(
          `Public route ${route.id} cannot expose a non-public observation`
        );
      }

      const requestedProposition = Object.prototype.hasOwnProperty.call(
        objectValue(options),
        "proposition"
      )
        ? clone(objectValue(options).proposition)
        : clone(source.proposition);
      const changed = !valuesEqual(requestedProposition, source.proposition);
      const distortion = probability(objectValue(options).distortion, changed ? 1 : 0);
      const maxDistortion = probability(route.maxDistortion);
      if (changed && distortion <= 0) {
        throw new StrategicAISocialError("Changed report proposition requires distortion");
      }
      if (distortion > maxDistortion) {
        throw new StrategicAISocialError(
          `Report distortion ${distortion} exceeds route maximum ${maxDistortion}`
        );
      }
      if (!changed && distortion > 0) {
        throw new StrategicAISocialError(
          "Undistorted proposition cannot declare report distortion"
        );
      }

      const reportId = stringValue(
        objectValue(options).reportId || this.nextReportId(route.id)
      );
      if (this.reports.has(reportId)) {
        throw new StrategicAISocialError(`Duplicate strategic report ${reportId}`);
      }
      const sourceReportId = stringValue(source.reportId);
      const parentReport = sourceReportId ? this.reports.get(sourceReportId) : null;
      const parentReportIds = sourceReportId
        ? [...new Set([
          ...arrayValue(objectValue(parentReport).parentReportIds).map(stringValue),
          sourceReportId
        ])].sort()
        : [];
      const originObservationId = stringValue(
        source.originObservationId || source.id
      );
      const sentAt = integerValue(
        objectValue(options).sentAt,
        integerValue(source.observedAt, 0),
        0
      );
      const receivedAt = sentAt + integerValue(route.latency, 0, 0);
      const reliability = roundNumber(
        probability(source.reliability)
        * probability(route.baseReliability)
        * (1 - distortion)
      );
      const recipientObservationId = `observation.report.${idSlug(reportId)}`;
      if (this.observations.has(recipientObservationId)) {
        throw new StrategicAISocialError(
          `Duplicate recipient observation ${recipientObservationId}`
        );
      }
      const deliveredVisibility = stringValue(route.mode) === "public"
        ? "public"
        : "private";
      const report = {
        reportId,
        routeId: stringValue(route.id),
        senderActorId: senderId,
        recipientActorId: recipientId,
        sourceObservationId: sourceId,
        originObservationId,
        parentReportIds,
        proposition: requestedProposition,
        reliability,
        distortion,
        sentAt,
        receivedAt,
        visibility: deliveredVisibility,
        recipientObservationId
      };
      const observation = {
        id: recipientObservationId,
        observerId: recipientId,
        proposition: clone(requestedProposition),
        channelId,
        sourceId: stringValue(source.sourceId),
        reliability,
        observedAt: receivedAt,
        visibility: deliveredVisibility,
        reportId,
        originObservationId
      };
      this.state.reports.push(report);
      this.state.observations.push(observation);
      this.refreshIndexes();
      return {report: clone(report), observation: clone(observation)};
    }

    propagatePublicObservation(senderActorId, sourceObservationId) {
      const senderId = stringValue(senderActorId);
      const sourceId = stringValue(sourceObservationId);
      const source = this.observations.get(sourceId);
      if (!source) throw new StrategicAISocialError(`Unknown source observation ${sourceId}`);
      if (stringValue(source.observerId) !== senderId) {
        throw new StrategicAISocialError(
          `Actor ${senderId} did not observe ${sourceId}`
        );
      }
      if (stringValue(source.visibility) !== "public") {
        throw new StrategicAISocialError(
          `Observation ${sourceId} is not public`
        );
      }
      const deliveries = [];
      arrayValue(this.definition.reportRoutes)
        .filter((route) => (
          stringValue(objectValue(route).mode) === "public"
          && arrayValue(objectValue(route).senderActorIds).map(stringValue).includes(senderId)
        ))
        .sort((left, right) => stringValue(left.id).localeCompare(stringValue(right.id)))
        .forEach((route) => {
          arrayValue(route.recipientActorIds)
            .map(stringValue)
            .filter((recipientId) => recipientId !== senderId)
            .sort()
            .forEach((recipientId) => {
              deliveries.push(
                this.createReport(route.id, senderId, recipientId, sourceId)
              );
            });
        });
      return clone(deliveries);
    }

    updateCaptainModel(actorId, observationIds = null) {
      const id = stringValue(actorId);
      this.actor(id);
      const profile = this.profileByActor.get(id);
      const model = this.modelByActor.get(id);
      if (!profile || !model) return null;
      const requested = observationIds === null
        ? null
        : new Set(arrayValue(observationIds).map(stringValue));
      const seen = new Set(arrayValue(model.observationIds).map(stringValue));
      const matchedObservationIds = [];
      const matchedReportIds = [];
      let updatedAt = integerValue(model.updatedAt, 0, 0);

      this.state.observations
        .filter((observation) => (
          stringValue(observation.observerId) === id
          && !seen.has(stringValue(observation.id))
          && (requested === null || requested.has(stringValue(observation.id)))
        ))
        .sort((left, right) => {
          const timeDelta = integerValue(left.observedAt) - integerValue(right.observedAt);
          if (timeDelta) return timeDelta;
          return stringValue(left.id).localeCompare(stringValue(right.id));
        })
        .forEach((observation) => {
          const signal = arrayValue(profile.signals).find((candidate) => (
            stringValue(objectValue(candidate).predicate)
              === stringValue(objectValue(observation.proposition).predicate)
            && valuesEqual(
              objectValue(candidate).value,
              objectValue(observation.proposition).value
            )
          ));
          if (!signal) return;
          const reliability = probability(observation.reliability);
          Object.keys(objectValue(signal.tendencyDeltas)).sort().forEach((tendencyId) => {
            const current = probability(objectValue(model.tendencies)[tendencyId], 0.5);
            const delta = finiteNumber(
              objectValue(signal.tendencyDeltas)[tendencyId],
              0,
              -1,
              1
            );
            const next = delta >= 0
              ? current + ((1 - current) * delta * reliability)
              : current * (1 - (Math.abs(delta) * reliability));
            model.tendencies[tendencyId] = roundNumber(probability(next));
          });
          const observationId = stringValue(observation.id);
          matchedObservationIds.push(observationId);
          const reportId = stringValue(observation.reportId);
          if (reportId) matchedReportIds.push(reportId);
          updatedAt = Math.max(updatedAt, integerValue(observation.observedAt, 0, 0));
        });

      if (!matchedObservationIds.length) return null;
      model.observationIds = [
        ...new Set([...arrayValue(model.observationIds).map(stringValue), ...matchedObservationIds])
      ];
      model.reportIds = [
        ...new Set([...arrayValue(model.reportIds).map(stringValue), ...matchedReportIds])
      ];
      model.updatedAt = updatedAt;
      this.refreshIndexes();
      return clone(model);
    }

    captainMetrics(actorId) {
      const model = this.modelByActor.get(stringValue(actorId));
      const tendencies = objectValue(objectValue(model).tendencies);
      return {
        captainCooperation: roundNumber(
          probability(tendencies["tendency.captain.cooperation"], 0)
        ),
        captainEvidenceDiscipline: roundNumber(
          probability(tendencies["tendency.captain.evidence-discipline"], 0)
        ),
        captainAuthorityResistance: roundNumber(
          probability(tendencies["tendency.captain.authority-resistance"], 0)
        )
      };
    }

    snapshot() {
      return clone(this.state);
    }

    getReports() {
      return clone(this.state.reports);
    }

    getCaptainModels() {
      return clone(this.state.captainModels);
    }
  }

  function create(definition, options = {}) {
    return new StrategicAISocialRuntime(definition, options);
  }

  const api = {
    SCHEMA,
    DEFINITION_VERSION,
    STATE_VERSION,
    LEGACY_STATE_VERSIONS: LEGACY_STATE_VERSIONS.slice(),
    StrategicAISocialError,
    StrategicAISocialRuntime,
    defaultCaptainModels,
    defaultCooperationModels,
    defaultOpportunityStates,
    defaultOffscreenStepStates,
    migrateState,
    validateDefinition,
    create
  };

  global.MainComputerStrategicAISocialRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
