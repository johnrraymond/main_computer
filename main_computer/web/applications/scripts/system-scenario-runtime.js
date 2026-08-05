(function (global) {
  "use strict";

  const DEFINITION_SCHEMA = "game.systemScenarios.v1";
  const DEFINITION_VERSION = "game.systemScenarios.definition.v1";
  const STATE_VERSION = "game.systemScenarios.state.v1";
  const CAMPAIGN_EXTENSION_SCHEMA = "game.systemScenarios.campaignExtension.v1";
  const STORAGE_PREFIX = "main-computer.system-scenarios.state.v1";

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

  function integerValue(value, fallback = 0, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
    return Math.trunc(finiteNumber(value, fallback, minimum, maximum));
  }

  function stableStringify(value) {
    if (Array.isArray(value)) {
      return `[${value.map((item) => stableStringify(item)).join(",")}]`;
    }
    if (value && typeof value === "object") {
      return `{${Object.keys(value).sort().map(
        (key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`
      ).join(",")}}`;
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

  function normalizeRequirement(value) {
    const raw = objectValue(value);
    return {
      allEvidenceIds: [...new Set(
        arrayValue(raw.allEvidenceIds).map(stringValue).filter(Boolean)
      )],
      anyEvidenceCount: integerValue(raw.anyEvidenceCount, 0, 0, 100),
      maxIntimidationShots: raw.maxIntimidationShots === null
          || raw.maxIntimidationShots === undefined
        ? null
        : integerValue(raw.maxIntimidationShots, 0, 0, 1000)
    };
  }

  function normalizeDefinition(value) {
    const raw = objectValue(value);
    const scenarios = arrayValue(raw.scenarios).map((entry, index) => {
      const scenario = objectValue(entry);
      const id = stringValue(scenario.id);
      const stages = arrayValue(scenario.stages).map((stage, stageIndex) => {
        const rawStage = objectValue(stage);
        return {
          id: stringValue(rawStage.id),
          label: stringValue(rawStage.label || `Stage ${stageIndex + 1}`),
          description: stringValue(rawStage.description),
          vesselStatus: stringValue(rawStage.vesselStatus),
          nextStageId: stringValue(rawStage.nextStageId)
        };
      });
      const evidence = arrayValue(scenario.evidence).map((item) => {
        const rawItem = objectValue(item);
        return {
          id: stringValue(rawItem.id),
          label: stringValue(rawItem.label),
          description: stringValue(rawItem.description)
        };
      });
      const resolutions = arrayValue(scenario.resolutions).map((item) => {
        const rawItem = objectValue(item);
        return {
          id: stringValue(rawItem.id),
          label: stringValue(rawItem.label),
          description: stringValue(rawItem.description),
          requirement: normalizeRequirement(rawItem.requirement),
          consequences: clone(objectValue(rawItem.consequences)),
          vesselStatus: stringValue(rawItem.vesselStatus)
        };
      });
      return {
        id,
        systemId: stringValue(scenario.systemId),
        title: stringValue(scenario.title || id || `Scenario ${index + 1}`),
        subtitle: stringValue(scenario.subtitle),
        description: stringValue(scenario.description),
        localRule: stringValue(scenario.localRule),
        startStageId: stringValue(scenario.startStageId || stages[0]?.id),
        protectionStageId: stringValue(scenario.protectionStageId),
        investigationStageId: stringValue(scenario.investigationStageId),
        conferenceStageId: stringValue(scenario.conferenceStageId),
        resolvedStageId: stringValue(scenario.resolvedStageId || "resolved"),
        completionCharacterId: stringValue(scenario.completionCharacterId),
        characterIds: [...new Set(
          arrayValue(scenario.characterIds).map(stringValue).filter(Boolean)
        )],
        vesselIds: [...new Set(
          arrayValue(scenario.vesselIds).map(stringValue).filter(Boolean)
        )],
        stages,
        evidence,
        resolutions
      };
    });
    return {
      schema: stringValue(raw.schema),
      definitionVersion: stringValue(raw.definitionVersion),
      stateVersion: stringValue(raw.stateVersion),
      enabled: raw.enabled !== false,
      receiptLimit: integerValue(raw.receiptLimit, 128, 16, 1024),
      scenarios
    };
  }

  function validateDefinition(value) {
    const definition = normalizeDefinition(value);
    const errors = [];
    const warnings = [];
    if (definition.schema !== DEFINITION_SCHEMA) {
      errors.push(`schema must be ${DEFINITION_SCHEMA}`);
    }
    if (definition.definitionVersion !== DEFINITION_VERSION) {
      errors.push(`definitionVersion must be ${DEFINITION_VERSION}`);
    }
    if (definition.stateVersion !== STATE_VERSION) {
      errors.push(`stateVersion must be ${STATE_VERSION}`);
    }
    if (!definition.scenarios.length) errors.push("scenarios must not be empty");

    const scenarioIds = new Set();
    definition.scenarios.forEach((scenario, index) => {
      const prefix = `scenarios[${index}]`;
      if (!scenario.id) errors.push(`${prefix}.id is required`);
      else if (scenarioIds.has(scenario.id)) errors.push(`${prefix}.id must be unique`);
      else scenarioIds.add(scenario.id);
      if (!scenario.systemId) errors.push(`${prefix}.systemId is required`);
      if (!scenario.stages.length) errors.push(`${scenario.id || prefix} requires stages`);
      if (!scenario.evidence.length) warnings.push(`${scenario.id || prefix} has no evidence`);
      if (!scenario.resolutions.length) errors.push(`${scenario.id || prefix} requires resolutions`);

      const stageIds = new Set();
      scenario.stages.forEach((stage, stageIndex) => {
        if (!stage.id) errors.push(`${prefix}.stages[${stageIndex}].id is required`);
        else if (stageIds.has(stage.id)) {
          errors.push(`${prefix}.stages[${stageIndex}].id must be unique`);
        } else stageIds.add(stage.id);
      });
      [
        scenario.startStageId,
        scenario.protectionStageId,
        scenario.investigationStageId,
        scenario.conferenceStageId,
        scenario.resolvedStageId
      ].filter(Boolean).forEach((stageId) => {
        if (!stageIds.has(stageId)) errors.push(`${scenario.id} references unknown stage ${stageId}`);
      });

      const evidenceIds = new Set();
      scenario.evidence.forEach((item, evidenceIndex) => {
        if (!item.id) errors.push(`${prefix}.evidence[${evidenceIndex}].id is required`);
        else if (evidenceIds.has(item.id)) {
          errors.push(`${prefix}.evidence[${evidenceIndex}].id must be unique`);
        } else evidenceIds.add(item.id);
      });
      const resolutionIds = new Set();
      scenario.resolutions.forEach((resolution, resolutionIndex) => {
        if (!resolution.id) {
          errors.push(`${prefix}.resolutions[${resolutionIndex}].id is required`);
        } else if (resolutionIds.has(resolution.id)) {
          errors.push(`${prefix}.resolutions[${resolutionIndex}].id must be unique`);
        } else resolutionIds.add(resolution.id);
        resolution.requirement.allEvidenceIds.forEach((evidenceId) => {
          if (!evidenceIds.has(evidenceId)) {
            errors.push(`${scenario.id} resolution ${resolution.id} requires unknown evidence ${evidenceId}`);
          }
        });
      });
    });
    return {ok: errors.length === 0, definition, errors, warnings};
  }

  class SystemScenarioDefinitionError extends Error {
    constructor(report) {
      super(`Invalid system-scenario definition: ${report.errors.join("; ")}`);
      this.name = "SystemScenarioDefinitionError";
      this.report = report;
    }
  }

  class SystemScenarioStateError extends Error {
    constructor(message, code = "system-scenario-state-invalid") {
      super(message);
      this.name = "SystemScenarioStateError";
      this.code = code;
    }
  }

  class SystemScenarioRuntime {
    constructor(definitionValue, options = {}) {
      const report = validateDefinition(definitionValue);
      if (!report.ok) throw new SystemScenarioDefinitionError(report);
      this.definition = report.definition;
      this.report = report;
      this.projectId = stringValue(options.projectId || "game-project");
      this.definitionFingerprint = definitionFingerprint(this.definition);
      this.storage = options.storage === undefined ? defaultStorage() : options.storage;
      this.storageKey = `${STORAGE_PREFIX}:${this.projectId}`;
      this.storageIssue = "";
      this.listeners = new Set();

      const supplied = options.state === undefined ? null : options.state;
      const stored = supplied === null && options.restore !== false
        ? this.readStoredState()
        : null;
      this.state = this.normalizeState(supplied || stored, options.activeSystemId);
      this.persist();
    }

    scenarioDefinition(scenarioId) {
      return this.definition.scenarios.find(
        (scenario) => scenario.id === stringValue(scenarioId)
      ) || null;
    }

    scenarioForSystem(systemId) {
      return this.definition.scenarios.find(
        (scenario) => scenario.systemId === stringValue(systemId)
      ) || null;
    }

    initialScenarioState(definition) {
      return {
        scenarioId: definition.id,
        systemId: definition.systemId,
        status: "available",
        stageId: definition.startStageId,
        evidenceIds: [],
        resolutionId: "",
        consequences: {},
        metrics: {
          weaponDischarges: 0,
          defensiveDischarges: 0,
          intimidationDischarges: 0
        },
        startedAtMs: null,
        resolvedAtMs: null,
        receipts: []
      };
    }

    normalizeScenarioState(definition, value) {
      const raw = objectValue(value);
      const stageIds = new Set(definition.stages.map((stage) => stage.id));
      const evidenceIds = new Set(definition.evidence.map((item) => item.id));
      const resolutionIds = new Set(definition.resolutions.map((item) => item.id));
      const status = ["available", "active", "resolved"].includes(stringValue(raw.status))
        ? stringValue(raw.status)
        : "available";
      const stageId = stageIds.has(stringValue(raw.stageId))
        ? stringValue(raw.stageId)
        : definition.startStageId;
      const resolutionId = resolutionIds.has(stringValue(raw.resolutionId))
        ? stringValue(raw.resolutionId)
        : "";
      return {
        scenarioId: definition.id,
        systemId: definition.systemId,
        status,
        stageId,
        evidenceIds: [...new Set(
          arrayValue(raw.evidenceIds).map(stringValue).filter((id) => evidenceIds.has(id))
        )],
        resolutionId,
        consequences: clone(objectValue(raw.consequences)),
        metrics: {
          weaponDischarges: integerValue(
            objectValue(raw.metrics).weaponDischarges,
            0,
            0
          ),
          defensiveDischarges: integerValue(
            objectValue(raw.metrics).defensiveDischarges,
            0,
            0
          ),
          intimidationDischarges: integerValue(
            objectValue(raw.metrics).intimidationDischarges,
            0,
            0
          )
        },
        startedAtMs: raw.startedAtMs === null || raw.startedAtMs === undefined
          ? null
          : finiteNumber(raw.startedAtMs, 0, 0),
        resolvedAtMs: raw.resolvedAtMs === null || raw.resolvedAtMs === undefined
          ? null
          : finiteNumber(raw.resolvedAtMs, 0, 0),
        receipts: arrayValue(raw.receipts).map((receipt) => clone(objectValue(receipt)))
          .slice(-this.definition.receiptLimit)
      };
    }

    normalizeState(value, activeSystemId = "") {
      const raw = objectValue(value);
      if (raw.schema && raw.schema !== STATE_VERSION) {
        throw new SystemScenarioStateError(`State schema must be ${STATE_VERSION}.`);
      }
      if (raw.projectId && stringValue(raw.projectId) !== this.projectId) {
        throw new SystemScenarioStateError(
          "System-scenario state belongs to another project.",
          "system-scenario-project-mismatch"
        );
      }
      if (raw.definitionFingerprint
          && stringValue(raw.definitionFingerprint) !== this.definitionFingerprint) {
        throw new SystemScenarioStateError(
          "System-scenario state definition is incompatible.",
          "system-scenario-definition-mismatch"
        );
      }
      const supplied = objectValue(raw.scenarios);
      const scenarios = {};
      this.definition.scenarios.forEach((definition) => {
        scenarios[definition.id] = this.normalizeScenarioState(
          definition,
          supplied[definition.id] || this.initialScenarioState(definition)
        );
      });
      return {
        schema: STATE_VERSION,
        projectId: this.projectId,
        definitionFingerprint: this.definitionFingerprint,
        activeSystemId: stringValue(activeSystemId || raw.activeSystemId),
        sequence: integerValue(raw.sequence, 0, 0),
        scenarios
      };
    }

    readStoredState() {
      if (!this.storage?.getItem) return null;
      try {
        const raw = this.storage.getItem(this.storageKey);
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        if (objectValue(parsed).schema !== STATE_VERSION) return null;
        if (stringValue(parsed.projectId) !== this.projectId) return null;
        if (stringValue(parsed.definitionFingerprint) !== this.definitionFingerprint) {
          this.storageIssue = "stored-definition-mismatch";
          return null;
        }
        return parsed;
      } catch {
        this.storageIssue = "stored-system-scenario-state-unreadable";
        return null;
      }
    }

    persist() {
      if (!this.storage?.setItem) return false;
      try {
        this.storage.setItem(this.storageKey, JSON.stringify(this.snapshot()));
        this.storageIssue = "";
        return true;
      } catch {
        this.storageIssue = "system-scenario-storage-write-failed";
        return false;
      }
    }

    subscribe(listener) {
      if (typeof listener !== "function") return () => {};
      this.listeners.add(listener);
      return () => this.listeners.delete(listener);
    }

    emit(reason, detail = null) {
      const event = {
        projectId: this.projectId,
        reason: stringValue(reason),
        sequence: this.state.sequence,
        activeSystemId: this.state.activeSystemId,
        detail: clone(detail),
        summary: this.summary()
      };
      this.listeners.forEach((listener) => {
        try {
          listener(clone(event));
        } catch {
          // Scenario observers cannot block campaign progression.
        }
      });
      try {
        if (typeof global.dispatchEvent === "function"
            && typeof global.CustomEvent === "function") {
          global.dispatchEvent(
            new global.CustomEvent("main-computer-system-scenario-change", {
              detail: clone(event)
            })
          );
        }
      } catch {
        // Browser event publication is optional.
      }
      return event;
    }

    record(scenarioId, reason, detail = {}, nowMs = 0) {
      const state = this.state.scenarios[stringValue(scenarioId)];
      if (!state) throw new SystemScenarioStateError(`Unknown scenario ${scenarioId}.`);
      this.state.sequence += 1;
      const receipt = {
        schema: "game.systemScenario.receipt.v1",
        receiptId: `scenario-receipt.${this.projectId}.${this.state.sequence}`,
        sequence: this.state.sequence,
        scenarioId: state.scenarioId,
        reason: stringValue(reason),
        nowMs: finiteNumber(nowMs, 0, 0),
        ...clone(objectValue(detail))
      };
      state.receipts = [...state.receipts, receipt].slice(-this.definition.receiptLimit);
      this.persist();
      this.emit(reason, receipt);
      return receipt;
    }

    setActiveSystemId(systemId, options = {}) {
      const id = stringValue(systemId);
      if (!id) throw new SystemScenarioStateError("Active system id is required.");
      const changed = id !== this.state.activeSystemId;
      this.state.activeSystemId = id;
      if (changed && options.record !== false) {
        const scenario = this.scenarioForSystem(id);
        if (scenario) {
          this.record(
            scenario.id,
            "active-system-changed",
            {activeSystemId: id},
            options.nowMs
          );
        } else {
          this.persist();
          this.emit("active-system-changed", {activeSystemId: id});
        }
      } else if (changed) {
        this.persist();
        this.emit("active-system-changed", {activeSystemId: id});
      }
      return {changed, activeSystemId: id};
    }

    requirementStatus(scenario, resolution, evidenceIds, metricsValue = {}) {
      const evidence = new Set(evidenceIds);
      const metrics = objectValue(metricsValue);
      const missing = resolution.requirement.allEvidenceIds.filter(
        (evidenceId) => !evidence.has(evidenceId)
      );
      const requiredCount = resolution.requirement.anyEvidenceCount;
      const countSatisfied = evidence.size >= requiredCount;
      const currentIntimidationShots = integerValue(
        metrics.intimidationDischarges,
        0,
        0
      );
      const maxIntimidationShots = resolution.requirement.maxIntimidationShots;
      const conductSatisfied = maxIntimidationShots === null
        || currentIntimidationShots <= maxIntimidationShots;
      return {
        available: missing.length === 0 && countSatisfied && conductSatisfied,
        missingEvidenceIds: missing,
        requiredEvidenceCount: requiredCount,
        currentEvidenceCount: evidence.size,
        maxIntimidationShots,
        currentIntimidationShots,
        conductSatisfied
      };
    }

    view(scenarioId) {
      const definition = this.scenarioDefinition(scenarioId);
      const state = this.state.scenarios[stringValue(scenarioId)];
      if (!definition || !state) return null;
      const evidence = definition.evidence.map((item) => ({
        ...clone(item),
        collected: state.evidenceIds.includes(item.id)
      }));
      const resolutions = definition.resolutions.map((resolution) => ({
        ...clone(resolution),
        ...this.requirementStatus(
          definition,
          resolution,
          state.evidenceIds,
          state.metrics
        )
      }));
      const stage = definition.stages.find((item) => item.id === state.stageId) || null;
      const resolution = definition.resolutions.find(
        (item) => item.id === state.resolutionId
      ) || null;
      return {
        visible: this.state.activeSystemId === definition.systemId,
        activeSystemId: this.state.activeSystemId,
        definition: clone(definition),
        state: clone(state),
        stage: clone(stage),
        evidence,
        resolutions,
        resolution: clone(resolution),
        vesselStatus: stringValue(
          resolution?.vesselStatus || stage?.vesselStatus
        )
      };
    }

    activeScenarioContext() {
      const definition = this.scenarioForSystem(this.state.activeSystemId);
      if (!definition) {
        return {
          id: "",
          systemId: this.state.activeSystemId,
          status: "none",
          stageId: ""
        };
      }
      const state = this.state.scenarios[definition.id];
      return {
        id: definition.id,
        systemId: definition.systemId,
        status: state.status,
        stageId: state.stageId,
        resolutionId: state.resolutionId
      };
    }

    startScenario(scenarioId, options = {}) {
      const definition = this.scenarioDefinition(scenarioId);
      const state = this.state.scenarios[stringValue(scenarioId)];
      if (!definition || !state) throw new SystemScenarioStateError("Unknown scenario.");
      if (state.status === "resolved") {
        return {reused: true, view: this.view(definition.id)};
      }
      if (state.status === "active") {
        return {reused: true, view: this.view(definition.id)};
      }
      state.status = "active";
      state.stageId = definition.protectionStageId || definition.startStageId;
      state.startedAtMs = finiteNumber(options.nowMs, 0, 0);
      const trigger = stringValue(options.trigger || "manual");
      const activationKey = stringValue(options.activationKey);
      const receipt = this.record(
        definition.id,
        "scenario-started",
        {
          stageId: state.stageId,
          trigger,
          ...(activationKey ? {activationKey} : {}),
          ...(stringValue(options.routeId)
            ? {routeId: stringValue(options.routeId)}
            : {}),
          ...(options.navigationSequence === undefined
            ? {}
            : {
                navigationSequence: integerValue(
                  options.navigationSequence,
                  0,
                  0
                )
              })
        },
        options.nowMs
      );
      return {reused: false, receipt, view: this.view(definition.id)};
    }

    syncCharacterRuntime(scenarioId, characterRuntime, options = {}) {
      const definition = this.scenarioDefinition(scenarioId);
      const state = this.state.scenarios[stringValue(scenarioId)];
      if (!definition || !state) throw new SystemScenarioStateError("Unknown scenario.");
      if (state.status !== "active"
          || state.stageId !== definition.protectionStageId
          || !definition.completionCharacterId) {
        return {changed: false, view: this.view(definition.id)};
      }
      const character = characterRuntime?.character?.(definition.completionCharacterId);
      if (!character || character.status === "active" && character.health > 0) {
        return {changed: false, view: this.view(definition.id)};
      }
      state.stageId = definition.investigationStageId;
      const receipt = this.record(
        definition.id,
        "protection-completed",
        {
          characterId: definition.completionCharacterId,
          stageId: state.stageId
        },
        options.nowMs
      );
      return {changed: true, receipt, view: this.view(definition.id)};
    }

    recordPlayerAction(scenarioId, actionId, detail = {}, options = {}) {
      const definition = this.scenarioDefinition(scenarioId);
      const state = this.state.scenarios[stringValue(scenarioId)];
      if (!definition || !state) throw new SystemScenarioStateError("Unknown scenario.");
      if (state.status !== "active") {
        return {recorded: false, reason: "scenario-not-active", view: this.view(definition.id)};
      }
      const id = stringValue(actionId);
      if (id !== "weapon-discharge") {
        const receipt = this.record(
          definition.id,
          "player-action-recorded",
          {actionId: id, detail: clone(objectValue(detail))},
          options.nowMs
        );
        return {recorded: true, receipt, view: this.view(definition.id)};
      }

      const targetId = stringValue(objectValue(detail).targetId);
      const explicitDefensive = objectValue(detail).defensive;
      const defensive = typeof explicitDefensive === "boolean"
        ? explicitDefensive
        : (
          state.stageId === definition.protectionStageId
          && targetId === definition.completionCharacterId
        );
      state.metrics.weaponDischarges += 1;
      if (defensive) state.metrics.defensiveDischarges += 1;
      else state.metrics.intimidationDischarges += 1;
      const receipt = this.record(
        definition.id,
        "weapon-discharge-recorded",
        {
          actionId: id,
          targetId,
          targetKind: stringValue(objectValue(detail).targetKind),
          defensive,
          metrics: clone(state.metrics)
        },
        options.nowMs
      );
      return {
        recorded: true,
        defensive,
        receipt,
        view: this.view(definition.id)
      };
    }

    recordEvidence(scenarioId, evidenceId, options = {}) {
      const definition = this.scenarioDefinition(scenarioId);
      const state = this.state.scenarios[stringValue(scenarioId)];
      if (!definition || !state) throw new SystemScenarioStateError("Unknown scenario.");
      if (state.status !== "active"
          || ![definition.investigationStageId, definition.conferenceStageId].includes(state.stageId)) {
        throw new SystemScenarioStateError(
          "Evidence can be recorded only after the protection stage.",
          "system-scenario-evidence-stage-invalid"
        );
      }
      const id = stringValue(evidenceId);
      if (!definition.evidence.some((item) => item.id === id)) {
        throw new SystemScenarioStateError(`Unknown evidence ${id}.`);
      }
      if (state.evidenceIds.includes(id)) {
        return {reused: true, view: this.view(definition.id)};
      }
      state.evidenceIds.push(id);
      const receipt = this.record(
        definition.id,
        "evidence-recorded",
        {evidenceId: id, evidenceCount: state.evidenceIds.length},
        options.nowMs
      );
      return {reused: false, receipt, view: this.view(definition.id)};
    }

    proceedToConference(scenarioId, options = {}) {
      const definition = this.scenarioDefinition(scenarioId);
      const state = this.state.scenarios[stringValue(scenarioId)];
      if (!definition || !state) throw new SystemScenarioStateError("Unknown scenario.");
      if (state.stageId === definition.conferenceStageId) {
        return {reused: true, view: this.view(definition.id)};
      }
      if (state.stageId !== definition.investigationStageId) {
        throw new SystemScenarioStateError(
          "Conference can begin only after the protection stage.",
          "system-scenario-conference-stage-invalid"
        );
      }
      if (state.evidenceIds.length < 2) {
        throw new SystemScenarioStateError(
          "Collect at least two evidence threads before the conference.",
          "system-scenario-evidence-insufficient"
        );
      }
      state.stageId = definition.conferenceStageId;
      const receipt = this.record(
        definition.id,
        "conference-opened",
        {stageId: state.stageId, evidenceIds: state.evidenceIds.slice()},
        options.nowMs
      );
      return {reused: false, receipt, view: this.view(definition.id)};
    }

    resolveScenario(scenarioId, resolutionId, options = {}) {
      const definition = this.scenarioDefinition(scenarioId);
      const state = this.state.scenarios[stringValue(scenarioId)];
      if (!definition || !state) throw new SystemScenarioStateError("Unknown scenario.");
      if (state.status === "resolved") {
        if (state.resolutionId !== stringValue(resolutionId)) {
          throw new SystemScenarioStateError(
            "Scenario is already resolved with another outcome.",
            "system-scenario-already-resolved"
          );
        }
        return {reused: true, view: this.view(definition.id)};
      }
      if (state.stageId !== definition.conferenceStageId) {
        throw new SystemScenarioStateError(
          "Scenario resolution requires the conference stage.",
          "system-scenario-resolution-stage-invalid"
        );
      }
      const resolution = definition.resolutions.find(
        (item) => item.id === stringValue(resolutionId)
      );
      if (!resolution) throw new SystemScenarioStateError("Unknown scenario resolution.");
      const requirement = this.requirementStatus(
        definition,
        resolution,
        state.evidenceIds,
        state.metrics
      );
      if (!requirement.available) {
        throw new SystemScenarioStateError(
          "The selected resolution does not have sufficient evidence.",
          "system-scenario-resolution-locked"
        );
      }
      state.status = "resolved";
      state.stageId = definition.resolvedStageId;
      state.resolutionId = resolution.id;
      state.consequences = {
        ...clone(resolution.consequences),
        forceConduct: state.metrics.intimidationDischarges > 0
          ? "neutrality-violation-recorded"
          : "defensive-force-only"
      };
      state.resolvedAtMs = finiteNumber(options.nowMs, 0, 0);
      const receipt = this.record(
        definition.id,
        "scenario-resolved",
        {
          resolutionId: resolution.id,
          evidenceIds: state.evidenceIds.slice(),
          consequences: clone(state.consequences)
        },
        options.nowMs
      );
      return {reused: false, receipt, view: this.view(definition.id)};
    }

    snapshot() {
      return clone(this.state);
    }

    restore(value, options = {}) {
      let parsed = value;
      if (typeof parsed === "string") {
        try {
          parsed = JSON.parse(parsed);
        } catch {
          throw new SystemScenarioStateError(
            "System-scenario state text is not valid JSON.",
            "system-scenario-json-invalid"
          );
        }
      }
      this.state = this.normalizeState(parsed);
      this.persist();
      if (options.emit !== false) this.emit("state-restored", null);
      return this.snapshot();
    }

    campaignExtension() {
      return {
        schema: CAMPAIGN_EXTENSION_SCHEMA,
        projectId: this.projectId,
        definitionFingerprint: this.definitionFingerprint,
        systemScenarios: this.snapshot()
      };
    }

    restoreCampaignExtension(value, options = {}) {
      const extension = objectValue(value);
      if (extension.schema !== CAMPAIGN_EXTENSION_SCHEMA) {
        throw new SystemScenarioStateError(
          `Campaign extension schema must be ${CAMPAIGN_EXTENSION_SCHEMA}.`,
          "system-scenario-campaign-schema-mismatch"
        );
      }
      if (stringValue(extension.projectId) !== this.projectId) {
        throw new SystemScenarioStateError(
          "System-scenario campaign extension belongs to another project.",
          "system-scenario-campaign-project-mismatch"
        );
      }
      if (stringValue(extension.definitionFingerprint) !== this.definitionFingerprint) {
        throw new SystemScenarioStateError(
          "System-scenario campaign extension is incompatible.",
          "system-scenario-campaign-definition-mismatch"
        );
      }
      return this.restore(extension.systemScenarios, options);
    }

    summary() {
      return {
        schema: STATE_VERSION,
        projectId: this.projectId,
        definitionFingerprint: this.definitionFingerprint,
        activeSystemId: this.state.activeSystemId,
        sequence: this.state.sequence,
        storageIssue: this.storageIssue,
        scenarios: this.definition.scenarios.map((definition) => {
          const state = this.state.scenarios[definition.id];
          return {
            id: definition.id,
            systemId: definition.systemId,
            title: definition.title,
            status: state.status,
            stageId: state.stageId,
            evidenceCount: state.evidenceIds.length,
            resolutionId: state.resolutionId
          };
        })
      };
    }
  }

  function create(definition, options = {}) {
    return new SystemScenarioRuntime(definition, options);
  }

  let currentRuntime = null;

  function ensure(projectId, definition, options = {}) {
    const id = stringValue(projectId || "game-project");
    const report = validateDefinition(definition);
    if (!report.ok) throw new SystemScenarioDefinitionError(report);
    const fingerprint = definitionFingerprint(report.definition);
    if (
      currentRuntime
      && currentRuntime.projectId === id
      && currentRuntime.definitionFingerprint === fingerprint
    ) {
      const activeSystemId = stringValue(options.activeSystemId);
      if (activeSystemId && activeSystemId !== currentRuntime.state.activeSystemId) {
        currentRuntime.setActiveSystemId(activeSystemId);
      }
      return currentRuntime;
    }
    currentRuntime = new SystemScenarioRuntime(report.definition, {
      ...objectValue(options),
      projectId: id
    });
    currentRuntime.emit("runtime-created", currentRuntime.summary());
    return currentRuntime;
  }

  function current() {
    return currentRuntime;
  }

  function clearCurrent() {
    const previous = currentRuntime;
    currentRuntime = null;
    return previous;
  }

  const api = {
    DEFINITION_SCHEMA,
    DEFINITION_VERSION,
    STATE_VERSION,
    CAMPAIGN_EXTENSION_SCHEMA,
    STORAGE_PREFIX,
    SystemScenarioDefinitionError,
    SystemScenarioStateError,
    normalizeDefinition,
    validateDefinition,
    definitionFingerprint,
    SystemScenarioRuntime,
    create,
    ensure,
    current,
    clearCurrent
  };

  global.MainComputerSystemScenarioRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
