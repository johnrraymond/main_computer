(function (global) {
  "use strict";

  const SESSION_SCHEMA = "game.strategicAI.session.v1";
  const STORAGE_PREFIX = "main-computer.strategic-ai.session.v1";

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

  function definitionFromProject(project) {
    return objectValue(objectValue(project).metadata).strategicAI || null;
  }

  function navigationDefinitionFromProject(project) {
    return objectValue(objectValue(project).metadata).spaceNavigation || null;
  }

  function defaultActiveSystemId(project) {
    const navigation = objectValue(navigationDefinitionFromProject(project));
    return stringValue(
      objectValue(navigation.stateDefaults).currentSystemId
      || navigation.startSystem
    );
  }

  function definitionFingerprint(definition) {
    return `fnv1a-${hashString(stableStringify(definition)).toString(16).padStart(8, "0")}`;
  }

  function defaultStorage() {
    try {
      return global.localStorage || null;
    } catch {
      return null;
    }
  }

  function defaultTravelState() {
    return {
      processedArrivalKeys: [],
      absences: {},
      returnNotice: null
    };
  }

  function normalizeTravelState(value) {
    const raw = objectValue(value);
    const absences = {};
    Object.entries(objectValue(raw.absences)).forEach(([systemId, absence]) => {
      const id = stringValue(systemId);
      if (!id) return;
      const record = objectValue(absence);
      absences[id] = {
        systemId: id,
        departedAtWorldTime: integerValue(record.departedAtWorldTime, 0, 0),
        baseline: clone(objectValue(record.baseline))
      };
    });
    const notice = objectValue(raw.returnNotice);
    return {
      processedArrivalKeys: [...new Set(
        arrayValue(raw.processedArrivalKeys).map(stringValue).filter(Boolean)
      )].slice(-64),
      absences,
      returnNotice: stringValue(notice.arrivalKey)
        ? {
          arrivalKey: stringValue(notice.arrivalKey),
          systemId: stringValue(notice.systemId),
          systemLabel: stringValue(notice.systemLabel || notice.systemId),
          departedAtWorldTime: integerValue(notice.departedAtWorldTime, 0, 0),
          returnedAtWorldTime: integerValue(notice.returnedAtWorldTime, 0, 0),
          simulationTime: integerValue(notice.simulationTime, 0, 0),
          canonicalRevision: integerValue(notice.canonicalRevision, 0, 0),
          acknowledged: Boolean(notice.acknowledged),
          changes: clone(arrayValue(notice.changes))
        }
        : null
    };
  }

  function summaryStepMap(summary) {
    const found = new Map();
    arrayValue(objectValue(summary).schedules).forEach((schedule) => {
      arrayValue(objectValue(schedule).steps).forEach((step) => {
        const id = stringValue(objectValue(step).stepId);
        if (!id) return;
        found.set(id, {
          scheduleId: stringValue(objectValue(schedule).scheduleId),
          scheduleLabel: stringValue(objectValue(schedule).label),
          ...clone(objectValue(step))
        });
      });
    });
    return found;
  }

  function returnSummaryChanges(beforeSummary, afterSummary) {
    const before = summaryStepMap(beforeSummary);
    const after = summaryStepMap(afterSummary);
    return [...after.entries()]
      .filter(([stepId, step]) => {
        const previous = objectValue(before.get(stepId));
        if (stringValue(step.status) === "pending"
            && stringValue(previous.status || "pending") === "pending") {
          return false;
        }
        return stableStringify(previous) !== stableStringify(step);
      })
      .map(([, step]) => ({
        stepId: stringValue(step.stepId),
        scheduleId: stringValue(step.scheduleId),
        scheduleLabel: stringValue(step.scheduleLabel),
        kind: stringValue(step.kind),
        description: stringValue(step.description),
        status: stringValue(step.status),
        completedAt: step.completedAt === null
          ? null
          : integerValue(step.completedAt, 0, 0),
        reason: stringValue(step.reason),
        resultIds: clone(arrayValue(step.resultIds))
      }))
      .sort((left, right) => {
        const timeDelta = integerValue(left.completedAt, 0, 0)
          - integerValue(right.completedAt, 0, 0);
        if (timeDelta) return timeDelta;
        return stringValue(left.stepId).localeCompare(stringValue(right.stepId));
      });
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

  function resolveOffscreenApi() {
    if (global.MainComputerStrategicAIOffscreenRuntime) {
      return global.MainComputerStrategicAIOffscreenRuntime;
    }
    if (typeof module !== "undefined" && module.exports) {
      return require("./strategic-ai-offscreen-runtime.js");
    }
    return null;
  }

  class StrategicAISessionError extends Error {
    constructor(message, code = "session-error") {
      super(message);
      this.name = "StrategicAISessionError";
      this.code = code;
    }
  }

  class StrategicAISession {
    constructor(projectId, project, options = {}) {
      this.projectId = stringValue(projectId || objectValue(project).id || "game-project");
      this.project = clone(objectValue(project));
      this.definition = clone(definitionFromProject(this.project));
      if (!this.definition) {
        throw new StrategicAISessionError(
          `Project ${this.projectId} has no strategic AI definition`,
          "definition-missing"
        );
      }

      this.definitionFingerprint = definitionFingerprint(this.definition);
      this.coordinatorApi = objectValue(options.coordinatorApi);
      this.offscreenApi = objectValue(options.offscreenApi);
      if (!Object.keys(this.coordinatorApi).length) {
        this.coordinatorApi = resolveCoordinatorApi();
      }
      if (!Object.keys(this.offscreenApi).length) {
        this.offscreenApi = resolveOffscreenApi();
      }
      if (!this.coordinatorApi?.create || !this.offscreenApi?.create) {
        throw new StrategicAISessionError(
          "Strategic coordinator and off-screen runtimes must be loaded",
          "runtime-missing"
        );
      }
      [this.coordinatorApi, this.offscreenApi].forEach((api) => {
        if (api.DEFINITION_VERSION !== this.definition.definitionVersion) {
          throw new StrategicAISessionError(
            `Strategic runtime definition version mismatch for ${this.projectId}`,
            "definition-version-mismatch"
          );
        }
        if (api.STATE_VERSION !== this.definition.stateVersion) {
          throw new StrategicAISessionError(
            `Strategic runtime state version mismatch for ${this.projectId}`,
            "state-version-mismatch"
          );
        }
      });

      this.seed = integerValue(
        options.seed,
        hashString(`${this.projectId}|${this.definitionFingerprint}`),
        0
      );
      this.storage = options.storage === undefined ? defaultStorage() : options.storage;
      this.storageKey = `${STORAGE_PREFIX}:${this.projectId}`;
      this.listeners = new Set();
      this.sequence = 0;
      this.activeSystemId = stringValue(
        options.activeSystemId || defaultActiveSystemId(this.project)
      );
      this.lastOperation = null;
      this.storageIssue = "";

      const suppliedState = options.state === undefined
        ? null
        : clone(objectValue(options.state));
      const stored = suppliedState === null && options.restore !== false
        ? this.readStoredEnvelope()
        : null;
      const initialState = suppliedState
        || objectValue(stored).strategicState
        || clone(objectValue(this.definition.stateDefaults));
      if (stored && !options.activeSystemId) {
        this.activeSystemId = stringValue(
          objectValue(stored).activeSystemId || this.activeSystemId
        );
      }
      this.sequence = integerValue(objectValue(stored).sequence, 0, 0);
      this.travelState = normalizeTravelState(objectValue(stored).travelState);
      this.state = this.validateState(initialState);
      this.persist();
    }

    validateState(state) {
      try {
        const coordinator = this.coordinatorApi.create(this.definition, {
          state,
          seed: this.seed
        });
        return coordinator.snapshot();
      } catch (error) {
        throw new StrategicAISessionError(
          error instanceof Error ? error.message : String(error || "Invalid strategic state"),
          "state-invalid"
        );
      }
    }

    readStoredEnvelope() {
      if (!this.storage?.getItem) return null;
      try {
        const raw = this.storage.getItem(this.storageKey);
        if (!raw) return null;
        const envelope = JSON.parse(raw);
        if (objectValue(envelope).schema !== SESSION_SCHEMA) return null;
        if (stringValue(envelope.projectId) !== this.projectId) return null;
        if (stringValue(envelope.definitionFingerprint) !== this.definitionFingerprint) {
          this.storageIssue = "stored-definition-mismatch";
          return null;
        }
        return envelope;
      } catch {
        this.storageIssue = "stored-session-unreadable";
        return null;
      }
    }

    envelope() {
      return {
        schema: SESSION_SCHEMA,
        projectId: this.projectId,
        definitionVersion: stringValue(this.definition.definitionVersion),
        stateVersion: stringValue(this.definition.stateVersion),
        definitionFingerprint: this.definitionFingerprint,
        seed: this.seed,
        activeSystemId: this.activeSystemId,
        sequence: this.sequence,
        lastOperation: clone(this.lastOperation),
        travelState: clone(this.travelState),
        strategicState: clone(this.state)
      };
    }

    persist() {
      if (!this.storage?.setItem) return false;
      try {
        this.storage.setItem(this.storageKey, JSON.stringify(this.envelope()));
        this.storageIssue = "";
        return true;
      } catch {
        this.storageIssue = "session-storage-write-failed";
        return false;
      }
    }

    subscribe(listener) {
      if (typeof listener !== "function") return () => {};
      this.listeners.add(listener);
      return () => this.listeners.delete(listener);
    }

    emit(operation, result = null) {
      const detail = {
        projectId: this.projectId,
        operation: stringValue(operation),
        sequence: this.sequence,
        activeSystemId: this.activeSystemId,
        summary: this.summary(),
        result: clone(result)
      };
      this.listeners.forEach((listener) => {
        try {
          listener(clone(detail));
        } catch {
          // Inspection listeners must never block strategic state progression.
        }
      });
      try {
        if (typeof global.dispatchEvent === "function"
            && typeof global.CustomEvent === "function") {
          global.dispatchEvent(
            new global.CustomEvent("main-computer-strategic-ai-session-change", {
              detail: clone(detail)
            })
          );
        }
      } catch {
        // Browser event publication is optional.
      }
      return detail;
    }

    record(operation, result = null, {persist = true} = {}) {
      this.sequence += 1;
      this.lastOperation = {
        operation: stringValue(operation),
        sequence: this.sequence,
        resultIds: this.resultIds(result)
      };
      if (persist) this.persist();
      this.emit(operation, result);
      return result;
    }

    resultIds(result) {
      const found = new Set();
      const visit = (value) => {
        if (Array.isArray(value)) {
          value.forEach(visit);
          return;
        }
        if (!value || typeof value !== "object") return;
        Object.entries(value).forEach(([key, item]) => {
          if (typeof item === "string" && /(?:^|_)(?:id|ids)$/i.test(key)) {
            if (item) found.add(item);
          } else {
            visit(item);
          }
        });
      };
      visit(result);
      return [...found].sort();
    }

    coordinator() {
      return this.coordinatorApi.create(this.definition, {
        state: this.state,
        seed: this.seed
      });
    }

    offscreen() {
      return this.offscreenApi.create(this.definition, {
        state: this.state,
        seed: this.seed
      });
    }

    setActiveSystemId(systemId, options = {}) {
      const id = stringValue(systemId);
      if (!id) {
        throw new StrategicAISessionError(
          "Active system id is required",
          "active-system-required"
        );
      }
      const systems = arrayValue(
        objectValue(navigationDefinitionFromProject(this.project)).systems
      );
      if (systems.length && !systems.some((system) => stringValue(system.id) === id)) {
        throw new StrategicAISessionError(
          `Unknown active system ${id}`,
          "active-system-unknown"
        );
      }
      const changed = id !== this.activeSystemId;
      this.activeSystemId = id;
      if (changed && options.record !== false) {
        return this.record("active-system-changed", {activeSystemId: id});
      }
      if (changed && options.persist !== false) this.persist();
      return {activeSystemId: id};
    }

    completeTravel(navigation, options = {}) {
      const raw = objectValue(navigation);
      const currentSystemId = stringValue(raw.currentSystemId);
      const routeId = stringValue(raw.lastCompletedRouteId);
      const arrivalAt = integerValue(raw.lastArrivalAtMs, -1, -1);
      const worldTime = integerValue(raw.elapsedWorldTime, -1, -1);
      if (!currentSystemId || !routeId || arrivalAt < 0 || worldTime < 0) {
        throw new StrategicAISessionError(
          "Completed travel requires a destination, route, arrival time, and world time",
          "travel-arrival-invalid"
        );
      }
      if (stringValue(raw.travelPhase || "in-system") !== "in-system"
          || Boolean(raw.travelling)) {
        throw new StrategicAISessionError(
          "Off-screen progression begins only after travel is committed",
          "travel-not-complete"
        );
      }

      const arrivalKey = [
        routeId,
        String(arrivalAt),
        currentSystemId,
        String(worldTime)
      ].join("|");
      if (arrayValue(this.travelState.processedArrivalKeys).includes(arrivalKey)) {
        return {
          reused: true,
          arrivalKey,
          activeSystemId: this.activeSystemId,
          returnNotice: clone(this.travelState.returnNotice)
        };
      }

      const previousSystemId = stringValue(this.activeSystemId);
      const fromTime = integerValue(
        objectValue(this.state).offscreenSimulationTime,
        0,
        0
      );
      if (previousSystemId && previousSystemId !== currentSystemId) {
        this.travelState.absences[previousSystemId] = {
          systemId: previousSystemId,
          departedAtWorldTime: fromTime,
          baseline: this.returnSummary(previousSystemId)
        };
      }

      const targetTime = Math.max(fromTime, worldTime);
      const runtime = this.offscreen();
      const simulation = runtime.simulateUntil(targetTime, {
        activeSystemId: currentSystemId,
        ...(Object.prototype.hasOwnProperty.call(objectValue(options), "budget")
          ? {budget: integerValue(objectValue(options).budget, 0, 0)}
          : {})
      });
      this.state = runtime.snapshot();
      this.activeSystemId = currentSystemId;

      const absence = objectValue(this.travelState.absences[currentSystemId]);
      let returnNotice = null;
      if (stringValue(absence.systemId)) {
        const currentSummary = runtime.getReturnSummary(currentSystemId);
        const systems = arrayValue(
          objectValue(navigationDefinitionFromProject(this.project)).systems
        );
        const system = systems.find(
          (candidate) => stringValue(candidate.id) === currentSystemId
        );
        returnNotice = {
          arrivalKey,
          systemId: currentSystemId,
          systemLabel: stringValue(objectValue(system).label || currentSystemId),
          departedAtWorldTime: integerValue(absence.departedAtWorldTime, 0, 0),
          returnedAtWorldTime: worldTime,
          simulationTime: integerValue(currentSummary.simulationTime, targetTime, 0),
          canonicalRevision: integerValue(currentSummary.canonicalRevision, 0, 0),
          acknowledged: false,
          changes: returnSummaryChanges(absence.baseline, currentSummary)
        };
        delete this.travelState.absences[currentSystemId];
      }

      this.travelState.processedArrivalKeys = [
        ...arrayValue(this.travelState.processedArrivalKeys),
        arrivalKey
      ].slice(-64);
      this.travelState.returnNotice = returnNotice;

      return this.record("travel-offscreen-advance", {
        reused: false,
        arrivalKey,
        routeId,
        departedSystemId: previousSystemId,
        activeSystemId: currentSystemId,
        worldTime,
        simulation: {
          receipt: clone(objectValue(simulation).receipt),
          summaries: clone(arrayValue(objectValue(simulation).summaries))
        },
        returnNotice: clone(returnNotice)
      });
    }

    travelSnapshot() {
      return clone(this.travelState);
    }

    acknowledgeReturnNotice(arrivalKey = "") {
      const notice = objectValue(this.travelState.returnNotice);
      if (!stringValue(notice.arrivalKey)) {
        return {acknowledged: false, reason: "return-notice-missing"};
      }
      const requested = stringValue(arrivalKey || notice.arrivalKey);
      if (requested !== stringValue(notice.arrivalKey)) {
        throw new StrategicAISessionError(
          "Return notice does not match the active arrival",
          "return-notice-mismatch"
        );
      }
      if (notice.acknowledged) {
        return {
          acknowledged: true,
          reused: true,
          arrivalKey: requested
        };
      }
      notice.acknowledged = true;
      return this.record("travel-return-acknowledged", {
        acknowledged: true,
        reused: false,
        arrivalKey: requested,
        systemId: stringValue(notice.systemId)
      });
    }

    runActorTurn(actorId, options = {}) {
      const coordinator = this.coordinator();
      const turn = coordinator.runTurn(actorId, objectValue(options));
      this.state = coordinator.snapshot();
      return this.record("actor-turn", turn);
    }

    activateCampaignRoute(routeSystemId, options = {}) {
      const coordinator = this.coordinator();
      const canonicalRevision = integerValue(
        objectValue(this.state.canonicalState).revision,
        0,
        0
      );
      const transition = coordinator.activateCampaignRoute(
        routeSystemId,
        {
          canonicalRevision,
          ...clone(objectValue(options))
        }
      );
      this.state = coordinator.snapshot();
      return this.record("campaign-activate", transition);
    }

    deactivateCampaignOpportunity(opportunityId, options = {}) {
      const coordinator = this.coordinator();
      const transition = coordinator.deactivateCampaignOpportunity(
        opportunityId,
        objectValue(options)
      );
      this.state = coordinator.snapshot();
      return this.record("campaign-deactivate", transition);
    }

    expireCampaignOpportunities(worldTime) {
      const coordinator = this.coordinator();
      const transitions = coordinator.expireCampaignOpportunities(worldTime);
      this.state = coordinator.snapshot();
      return this.record("campaign-expire", transitions);
    }

    createCommitment(commitmentTypeId, promisorActorId, promiseeActorId, options = {}) {
      const coordinator = this.coordinator();
      const commitment = coordinator.createCommitment(
        commitmentTypeId,
        promisorActorId,
        promiseeActorId,
        objectValue(options)
      );
      this.state = coordinator.snapshot();
      return this.record("commitment-create", commitment);
    }

    performCommunication(intentId, speakerActorId, audienceActorIds, options = {}) {
      const coordinator = this.coordinator();
      const result = coordinator.performCommunication(
        intentId,
        speakerActorId,
        arrayValue(audienceActorIds),
        objectValue(options)
      );
      return this.record("communication", result);
    }

    advanceOffscreen(targetTime, options = {}) {
      const runtime = this.offscreen();
      const result = runtime.simulateUntil(targetTime, {
        activeSystemId: stringValue(
          objectValue(options).activeSystemId || this.activeSystemId
        ),
        ...(Object.prototype.hasOwnProperty.call(objectValue(options), "budget")
          ? {budget: integerValue(objectValue(options).budget, 0, 0)}
          : {})
      });
      this.state = runtime.snapshot();
      return this.record("offscreen-advance", result);
    }

    returnSummary(systemId) {
      return this.offscreen().getReturnSummary(systemId);
    }

    strategicSnapshot() {
      return clone(this.state);
    }

    snapshot() {
      return clone(this.envelope());
    }

    exportSnapshot(space = 2) {
      return JSON.stringify(this.envelope(), null, integerValue(space, 2, 0));
    }

    restore(value, options = {}) {
      let parsed = value;
      if (typeof parsed === "string") {
        try {
          parsed = JSON.parse(parsed);
        } catch {
          throw new StrategicAISessionError(
            "Snapshot text is not valid JSON",
            "snapshot-json-invalid"
          );
        }
      }
      const raw = objectValue(parsed);
      const state = raw.schema === SESSION_SCHEMA
        ? objectValue(raw.strategicState)
        : raw;
      if (raw.schema === SESSION_SCHEMA) {
        if (stringValue(raw.projectId) !== this.projectId) {
          throw new StrategicAISessionError(
            `Snapshot belongs to ${stringValue(raw.projectId) || "another project"}`,
            "snapshot-project-mismatch"
          );
        }
        if (stringValue(raw.definitionFingerprint) !== this.definitionFingerprint) {
          throw new StrategicAISessionError(
            "Snapshot strategic definition does not match the active project",
            "snapshot-definition-mismatch"
          );
        }
        this.activeSystemId = stringValue(
          raw.activeSystemId || this.activeSystemId
        );
        this.travelState = normalizeTravelState(raw.travelState);
      } else {
        this.travelState = defaultTravelState();
      }
      this.state = this.validateState(state);
      if (options.record === false) {
        this.persist();
        this.emit("snapshot-restored", null);
        return this.snapshot();
      }
      this.record("snapshot-restored", {
        stateVersion: this.state.stateVersion
      });
      return this.snapshot();
    }

    reset() {
      this.state = this.validateState(clone(objectValue(this.definition.stateDefaults)));
      this.activeSystemId = defaultActiveSystemId(this.project);
      this.travelState = defaultTravelState();
      return this.record("session-reset", {
        stateVersion: this.state.stateVersion,
        activeSystemId: this.activeSystemId
      });
    }

    summary() {
      const state = objectValue(this.state);
      const canonical = objectValue(state.canonicalState);
      const statusCount = (records, key = "status") => {
        const counts = {};
        arrayValue(records).forEach((record) => {
          const status = stringValue(objectValue(record)[key] || "unknown");
          counts[status] = (counts[status] || 0) + 1;
        });
        return counts;
      };
      return {
        schema: SESSION_SCHEMA,
        projectId: this.projectId,
        definitionVersion: stringValue(this.definition.definitionVersion),
        stateVersion: stringValue(state.stateVersion),
        activeSystemId: this.activeSystemId,
        sequence: this.sequence,
        canonicalRevision: integerValue(canonical.revision, 0, 0),
        checkpointId: stringValue(state.currentCheckpointId),
        actorCount: arrayValue(this.definition.actors).length,
        observationCount: arrayValue(state.observations).length,
        beliefCount: arrayValue(state.beliefs).length,
        memoryCount: arrayValue(state.memories).length,
        receiptCount: arrayValue(state.receipts).length,
        proposalCount: arrayValue(state.proposals).length,
        outcomeCount: arrayValue(state.outcomes).length,
        reportCount: arrayValue(state.reports).length,
        commitmentCount: arrayValue(state.commitments).length,
        commitmentStatus: statusCount(state.commitments),
        campaignOpportunityStatus: statusCount(state.campaignOpportunityStates),
        directorReceiptCount: arrayValue(state.directorReceipts).length,
        offscreenSimulationTime: integerValue(state.offscreenSimulationTime, 0, 0),
        offscreenStepStatus: statusCount(state.offscreenStepStates),
        offscreenReceiptCount: arrayValue(state.offscreenSimulationReceipts).length,
        processedTravelCount: arrayValue(this.travelState.processedArrivalKeys).length,
        returnNoticeAvailable: Boolean(
          stringValue(objectValue(this.travelState.returnNotice).arrivalKey)
          && !objectValue(this.travelState.returnNotice).acknowledged
        ),
        storageIssue: this.storageIssue,
        lastOperation: clone(this.lastOperation)
      };
    }

    catalog() {
      return {
        actors: clone(arrayValue(this.definition.actors)),
        campaignOpportunities: clone(arrayValue(this.definition.campaignOpportunities)),
        commitmentTypes: clone(arrayValue(this.definition.commitmentTypes)),
        communicativeIntents: clone(arrayValue(this.definition.communicativeIntents)),
        reportRoutes: clone(arrayValue(this.definition.reportRoutes)),
        systems: clone(arrayValue(
          objectValue(navigationDefinitionFromProject(this.project)).systems
        ))
      };
    }
  }

  let currentSession = null;

  function ensure(projectId, project, options = {}) {
    const id = stringValue(projectId || objectValue(project).id || "game-project");
    const definition = definitionFromProject(project);
    if (!definition) {
      throw new StrategicAISessionError(
        `Project ${id} has no strategic AI definition`,
        "definition-missing"
      );
    }
    const fingerprint = definitionFingerprint(definition);
    if (
      currentSession
      && currentSession.projectId === id
      && currentSession.definitionFingerprint === fingerprint
    ) {
      const activeSystemId = stringValue(objectValue(options).activeSystemId);
      if (activeSystemId && activeSystemId !== currentSession.activeSystemId) {
        currentSession.setActiveSystemId(activeSystemId);
      }
      return currentSession;
    }
    currentSession = new StrategicAISession(id, project, options);
    currentSession.emit("session-created", currentSession.summary());
    return currentSession;
  }

  function current() {
    return currentSession;
  }

  function clearCurrent() {
    const previous = currentSession;
    currentSession = null;
    return previous;
  }

  const api = {
    SESSION_SCHEMA,
    STORAGE_PREFIX,
    StrategicAISessionError,
    StrategicAISession,
    definitionFromProject,
    defaultActiveSystemId,
    definitionFingerprint,
    defaultTravelState,
    normalizeTravelState,
    returnSummaryChanges,
    ensure,
    current,
    clearCurrent
  };

  global.MainComputerStrategicAISession = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
