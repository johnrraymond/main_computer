(function (global) {
  "use strict";

  const DEFINITION_SCHEMA = "game.systemScenarios.v1";
  const DEFINITION_VERSION = "game.systemScenarios.definition.v1";
  const STATE_VERSION = "game.systemScenarios.state.v1";
  const CAMPAIGN_EXTENSION_SCHEMA = "game.systemScenarios.campaignExtension.v1";
  const GENERATED_GAMEPLAY_CATALOG_SCHEMA = "game.gameplayPluginProjectCatalog.v1";
  const GENERATED_GAMEPLAY_CATALOG_KIND = "gameplay-plugin-project-catalog";
  const GENERATED_GAMEPLAY_CATALOG_RUNTIME_STATUS = "browser-static-project-data-only";
  const GENERATED_SCENARIO_AVAILABILITY_SCHEMA = "game.generatedScenarioAvailability.v1";
  const GENERATED_SCENARIO_AVAILABILITY_KIND = "generated-scenario-availability";
  const GENERATED_SCENARIO_START_PREVIEW_SCHEMA = "game.generatedScenarioStartPreview.v1";
  const GENERATED_SCENARIO_START_PREVIEW_KIND = "generated-scenario-start-preview";
  const GENERATED_SCENARIO_START_COMMAND_SCHEMA = "game.generatedScenarioStartCommand.v1";
  const GENERATED_SCENARIO_START_COMMAND_KIND = "generated-scenario-start-command";
  const GENERATED_SCENARIO_PREVIEW_SHELL_STATE_SCHEMA = "game.generatedScenarioPreviewShellState.v1";
  const GENERATED_SCENARIO_PREVIEW_SHELL_STATE_KIND = "generated-scenario-preview-shell-state";
  const GENERATED_SCENARIO_PREVIEW_EVENT_SCHEMA = "game.generatedScenarioPreviewEvent.v1";
  const GENERATED_SCENARIO_PREVIEW_EVENT_KIND = "generated-scenario-preview-event";
  const GENERATED_SCENARIO_TEMPLATE_HANDOFF_SCHEMA = "game.generatedScenarioTemplateHandoff.v1";
  const GENERATED_SCENARIO_TEMPLATE_HANDOFF_KIND = "generated-scenario-template-handoff";
  const GENERATED_SCENARIO_TEMPLATE_HANDOFF_SUPPORTED_TEMPLATES = Object.freeze([
    "encounter-template.boarding-defense",
    "encounter-template.cave-combat-run",
    "encounter-template.surface-transporter-extraction",
    "encounter-template.shuttle-ambush",
    "encounter-template.social-investigation"
  ]);
  const GENERATED_TEMPLATE_EXECUTOR_REGISTRY_SCHEMA = "game.generatedTemplateExecutorRegistry.v1";
  const GENERATED_TEMPLATE_EXECUTOR_REGISTRY_KIND = "generated-template-executor-registry";
  const GENERATED_TEMPLATE_EXECUTOR_STATUS_SCHEMA = "game.generatedTemplateExecutorStatus.v1";
  const GENERATED_TEMPLATE_EXECUTOR_STATUS_KIND = "generated-template-executor-status";
  const GENERATED_TEMPLATE_EXECUTOR_DEFAULT_MODE = "disabled-no-op";
  const OPENING_SHUTTLE_ENCOUNTER_BRIDGE_SCHEMA = "game.openingShuttleEncounterBridge.v1";
  const OPENING_SHUTTLE_ENCOUNTER_BRIDGE_KIND = "opening-shuttle-encounter-runtime-bridge";
  const OPENING_SHUTTLE_ENCOUNTER_BRIDGE_DIAGNOSTIC_SCHEMA = "game.openingShuttleEncounterBridgeDiagnostic.v1";
  const OPENING_SHUTTLE_ENCOUNTER_BRIDGE_DIAGNOSTIC_KIND = "opening-shuttle-encounter-bridge-diagnostic";
  const ACTIVE_GAMEPLAY_PACK_SELECTION_SCHEMA = "game.activeGameplayPackSelection.v1";
  const ACTIVE_GAMEPLAY_PACK_SELECTION_KIND = "active-gameplay-pack-selection";
  const OPENING_SHUTTLE_GAMEPLAY_PACK_CONFIG_SCHEMA = "game.openingShuttleGameplayPackConfig.v1";
  const OPENING_SHUTTLE_GAMEPLAY_PACK_CONFIG_KIND = "opening-shuttle-gameplay-pack-config";
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

  function uniqueStrings(value) {
    return [...new Set(arrayValue(value).map(stringValue).filter(Boolean))];
  }

  function generatedObjectiveRecords(value) {
    return arrayValue(value).map((item) => {
      const raw = objectValue(item);
      const type = stringValue(raw.type || raw.objectiveType || raw.objectiveTypeId);
      if (!type) return null;
      const id = stringValue(raw.id || raw.objectiveId || type);
      const label = stringValue(raw.label);
      const record = {
        id,
        type,
        required: raw.required !== false
      };
      if (label) record.label = label;
      return record;
    }).filter(Boolean);
  }

  function generatedParticipantRecords(value) {
    return arrayValue(value).map((item) => {
      const raw = objectValue(item);
      const actorArchetypeId = stringValue(
        raw.actorArchetypeId || raw.actorArchetype || raw.archetype || raw.archetypeId
      );
      if (!actorArchetypeId) return null;
      return {
        role: stringValue(raw.role || (actorArchetypeId === "actor-archetype.shuttle-raider" ? "hostile" : "")),
        actorArchetypeId,
        count: integerValue(raw.count, 1, 1, 64)
      };
    }).filter(Boolean);
  }

  function generatedLocationRecord(value) {
    const raw = objectValue(value);
    const systemId = stringValue(raw.systemId);
    const destinationId = stringValue(raw.destinationId);
    const location = {};
    if (systemId) location.systemId = systemId;
    if (destinationId) location.destinationId = destinationId;
    return location;
  }

  function activeGameplayPackIdTokens(value) {
    if (value === null || value === undefined) return [];
    if (Array.isArray(value)) return value.flatMap((item) => activeGameplayPackIdTokens(item));
    if (typeof value === "string") {
      return value.split(",").map((item) => stringValue(item).trim()).filter(Boolean);
    }
    const raw = objectValue(value);
    if (stringValue(raw.mode).toLowerCase() === "none") return ["None"];
    if (Object.prototype.hasOwnProperty.call(raw, "activeGameplayPackIds")) {
      return activeGameplayPackIdTokens(raw.activeGameplayPackIds);
    }
    if (Object.prototype.hasOwnProperty.call(raw, "activePluginIds")) {
      return activeGameplayPackIdTokens(raw.activePluginIds);
    }
    if (Object.prototype.hasOwnProperty.call(raw, "pluginIds")) {
      return activeGameplayPackIdTokens(raw.pluginIds);
    }
    if (Object.prototype.hasOwnProperty.call(raw, "selectedPluginIds")) {
      return activeGameplayPackIdTokens(raw.selectedPluginIds);
    }
    if (Object.prototype.hasOwnProperty.call(raw, "value")) {
      return activeGameplayPackIdTokens(raw.value);
    }
    return [];
  }

  function normalizeActiveGameplayPackIds(value) {
    const tokens = activeGameplayPackIdTokens(value);
    const hasNone = tokens.some((token) => stringValue(token).toLowerCase() === "none");
    if (hasNone) return [];
    return uniqueStrings(tokens.filter((token) => stringValue(token).toLowerCase() !== "none"));
  }

  function generatedCatalogPluginIds(catalog) {
    const normalized = normalizeGeneratedGameplayCatalog(catalog);
    return uniqueStrings(
      normalized.enabledPluginIds
        .concat(arrayValue(normalized.plugins).map((plugin) => objectValue(plugin).pluginId))
        .concat(arrayValue(normalized.documents).map((document) => objectValue(document).pluginId))
        .concat(arrayValue(normalized.scenarios).map((scenario) => objectValue(scenario).pluginId))
        .concat(arrayValue(normalized.encounters).map((encounter) => objectValue(encounter).pluginId))
    );
  }

  function activeGameplayPackSelection(catalog, requestedPackIds = []) {
    const normalized = normalizeGeneratedGameplayCatalog(catalog);
    const availablePluginIds = generatedCatalogPluginIds(normalized);
    const requestedPluginIds = normalizeActiveGameplayPackIds(requestedPackIds);
    const available = new Set(availablePluginIds);
    const activePluginIds = requestedPluginIds.filter((pluginId) => available.has(pluginId));
    const missingPluginIds = requestedPluginIds.filter((pluginId) => !available.has(pluginId));
    const mode = activePluginIds.length ? "selected" : "none";
    return {
      schema: ACTIVE_GAMEPLAY_PACK_SELECTION_SCHEMA,
      kind: ACTIVE_GAMEPLAY_PACK_SELECTION_KIND,
      readOnly: true,
      runtimeLocal: true,
      persisted: false,
      generated: false,
      projectJsonModified: false,
      saveStateMutated: false,
      generatedPluginExecution: false,
      generatedTemplateExecution: false,
      rendererHandoff: false,
      mode,
      none: activePluginIds.length === 0,
      catalogReady: normalized.ready === true,
      catalogStatus: normalized.status,
      availablePluginIds,
      requestedPluginIds,
      activePluginIds,
      missingPluginIds,
      options: ["None"].concat(availablePluginIds),
      reason: activePluginIds.length
        ? "active-gameplay-packs-selected"
        : (
          requestedPluginIds.length
            ? "requested-gameplay-packs-unavailable"
            : "no-active-gameplay-pack-selected"
        )
    };
  }

  function openingShuttleGameplayPackConfig(catalog, requestedPackIds = [], options = {}) {
    const rawOptions = objectValue(options);
    const normalized = normalizeGeneratedGameplayCatalog(catalog);
    const selection = activeGameplayPackSelection(normalized, requestedPackIds);
    const active = new Set(selection.activePluginIds);
    const problems = selection.missingPluginIds.map((pluginId) => `active gameplay pack unavailable: ${pluginId}`);
    const baseHostileCount = integerValue(rawOptions.baseHostileCount, 2, 0, 64);
    const systemId = stringValue(rawOptions.systemId || "system.solace-reach");
    const destinationId = stringValue(rawOptions.destinationId || "destination.solace-reach.haven-orbit");
    const encountersById = new Map(normalized.encounters.map((encounter) => [encounter.id, encounter]));
    let matchedScenario = null;
    let matchedEncounter = null;

    if (selection.activePluginIds.length) {
      const candidateScenarios = normalized.scenarios.filter((scenario) => active.has(scenario.pluginId));
      for (const scenario of candidateScenarios) {
        const linkedEncounters = uniqueStrings(scenario.encounterIds)
          .map((encounterId) => encountersById.get(encounterId))
          .filter((encounter) => encounter && active.has(encounter.pluginId));
        const encounter = linkedEncounters.find((entry) => {
          const location = generatedLocationRecord(entry.location);
          return stringValue(entry.template) === "encounter-template.shuttle-ambush"
            && (!location.systemId || location.systemId === systemId)
            && (!location.destinationId || location.destinationId === destinationId);
        });
        if (encounter) {
          matchedScenario = scenario;
          matchedEncounter = encounter;
          break;
        }
      }
    }

    if (!matchedScenario || !matchedEncounter) {
      if (selection.activePluginIds.length) {
        problems.push("no-active-opening-shuttle-shuttle-ambush-pack");
      }
      return {
        schema: OPENING_SHUTTLE_GAMEPLAY_PACK_CONFIG_SCHEMA,
        kind: OPENING_SHUTTLE_GAMEPLAY_PACK_CONFIG_KIND,
        readOnly: true,
        runtimeLocal: true,
        persisted: false,
        generated: false,
        projectJsonModified: false,
        saveStateMutated: false,
        generatedPluginExecution: false,
        generatedTemplateExecution: false,
        rendererHandoff: false,
        available: false,
        active: false,
        mode: selection.mode,
        reason: selection.none ? "no-active-gameplay-pack-selected" : "no-active-opening-shuttle-pack",
        selection,
        activePluginIds: selection.activePluginIds.slice(),
        scenarioId: "",
        pluginId: "",
        encounterId: "",
        templateId: "encounter-template.shuttle-ambush",
        systemId,
        destinationId,
        baseHostileCount,
        hostileCount: baseHostileCount,
        extraHostileCount: 0,
        objectiveIds: [],
        objectiveTypes: [],
        receiptIds: [],
        eliteWave: {
          enabled: false,
          triggerDefeats: baseHostileCount,
          count: 0,
          actorArchetypeId: "actor-archetype.shuttle-raider",
          source: "none"
        },
        problems
      };
    }

    const objectives = generatedObjectiveRecords(matchedEncounter.objectives);
    const participants = generatedParticipantRecords(matchedEncounter.participants);
    const hostileParticipants = participants.filter((participant) => (
      stringValue(participant.role) === "hostile"
      || stringValue(participant.actorArchetypeId) === "actor-archetype.shuttle-raider"
    ));
    const hostileCount = hostileParticipants.reduce(
      (total, participant) => total + integerValue(participant.count, 1, 1, 64),
      0
    );
    const effectiveHostileCount = hostileCount > 0 ? hostileCount : baseHostileCount;
    const extraHostileCount = Math.max(0, effectiveHostileCount - baseHostileCount);
    const actorArchetypeId = stringValue(hostileParticipants[0]?.actorArchetypeId || "actor-archetype.shuttle-raider");
    const location = generatedLocationRecord(matchedEncounter.location);
    return {
      schema: OPENING_SHUTTLE_GAMEPLAY_PACK_CONFIG_SCHEMA,
      kind: OPENING_SHUTTLE_GAMEPLAY_PACK_CONFIG_KIND,
      readOnly: true,
      runtimeLocal: true,
      persisted: false,
      generated: false,
      projectJsonModified: false,
      saveStateMutated: false,
      generatedPluginExecution: false,
      generatedTemplateExecution: false,
      rendererHandoff: false,
      available: true,
      active: extraHostileCount > 0,
      mode: selection.mode,
      reason: extraHostileCount > 0
        ? "active-opening-shuttle-pack-configured"
        : "active-opening-shuttle-pack-no-extra-hostiles",
      selection,
      activePluginIds: selection.activePluginIds.slice(),
      scenarioId: matchedScenario.id,
      pluginId: matchedScenario.pluginId,
      encounterId: matchedEncounter.id,
      templateId: matchedEncounter.template,
      systemId: location.systemId || systemId,
      destinationId: location.destinationId || destinationId,
      baseHostileCount,
      hostileCount: effectiveHostileCount,
      extraHostileCount,
      objectiveIds: objectives.map((objective) => objective.id),
      objectiveTypes: uniqueStrings(
        matchedEncounter.objectiveTypes.concat(objectives.map((objective) => objective.type))
      ),
      receiptIds: uniqueStrings(matchedScenario.receiptIds.concat(matchedEncounter.receiptIds)),
      eliteWave: {
        enabled: extraHostileCount > 0,
        triggerDefeats: baseHostileCount,
        count: extraHostileCount,
        actorArchetypeId,
        source: matchedScenario.pluginId,
        scenarioId: matchedScenario.id,
        encounterId: matchedEncounter.id
      },
      problems
    };
  }

  function openingShuttleEncounterObjectiveRecords(value) {
    return arrayValue(value).map((item) => {
      const raw = objectValue(item);
      const id = stringValue(raw.id || raw.objectiveId);
      if (!id) return null;
      const record = {
        id,
        type: stringValue(raw.type || raw.objectiveType || raw.objectiveTypeId),
        label: stringValue(raw.label || raw.title || id),
        required: raw.required !== false,
        status: stringValue(raw.status || "unknown"),
        progress: clone(objectValue(raw.progress))
      };
      return record;
    }).filter(Boolean);
  }

  function openingShuttleEncounterBridgeSnapshot(value, options = {}) {
    const raw = objectValue(value);
    const rawOptions = objectValue(options);
    const counters = objectValue(raw.counters);
    const eliteWave = objectValue(raw.eliteWave);
    const completion = objectValue(raw.completion);
    const execution = objectValue(raw.execution);
    const receiptIds = uniqueStrings(
      arrayValue(raw.receiptIds).concat(
        arrayValue(raw.receipts).map((receipt) => objectValue(receipt).id)
      )
    );
    const completionReceiptIds = uniqueStrings(completion.receiptIds);
    const bridgedAtMs = Object.prototype.hasOwnProperty.call(rawOptions, "nowMs")
      ? finiteNumber(rawOptions.nowMs, 0, 0)
      : null;
    return {
      schema: OPENING_SHUTTLE_ENCOUNTER_BRIDGE_SCHEMA,
      kind: OPENING_SHUTTLE_ENCOUNTER_BRIDGE_KIND,
      source: stringValue(rawOptions.source || "scene-viewer.openingShuttleEncounter"),
      runtimeLocal: true,
      readOnly: true,
      persisted: false,
      generated: false,
      encounterId: stringValue(raw.encounterId || "encounter.solace-reach.opening-shuttle-ambush"),
      templateId: stringValue(raw.templateId || "encounter-template.shuttle-ambush"),
      systemId: stringValue(raw.systemId || "system.solace-reach"),
      destinationId: stringValue(raw.destinationId || "destination.solace-reach.haven-orbit"),
      builtInConsumerId: stringValue(raw.builtInConsumerId || "built-in.solace-reach.opening-shuttle-ambush"),
      status: stringValue(raw.status || "unknown"),
      objectiveLine: stringValue(raw.objectiveLine),
      objectiveSequence: openingShuttleEncounterObjectiveRecords(raw.objectiveSequence),
      counters: {
        spawned: integerValue(counters.spawned, 0, 0),
        activated: integerValue(counters.activated, 0, 0),
        defeated: integerValue(counters.defeated, 0, 0),
        eliteSpawned: integerValue(counters.eliteSpawned, 0, 0),
        eliteDefeated: integerValue(counters.eliteDefeated, 0, 0),
        playerDamaged: integerValue(counters.playerDamaged, 0, 0),
        playerDefeated: integerValue(counters.playerDefeated, 0, 0),
        destinationReached: integerValue(counters.destinationReached, 0, 0)
      },
      eliteWave: {
        enabled: eliteWave.enabled === true,
        triggerDefeats: integerValue(eliteWave.triggerDefeats, 0, 0),
        requested: eliteWave.requested === true,
        spawned: eliteWave.spawned === true,
        cleared: eliteWave.cleared === true,
        requiredExtraHostiles: integerValue(eliteWave.requiredExtraHostiles, 0, 0),
        spawnedCount: integerValue(eliteWave.spawnedCount, 0, 0),
        actorArchetypeId: stringValue(eliteWave.actorArchetypeId),
        source: stringValue(eliteWave.source),
        pluginId: stringValue(eliteWave.pluginId),
        scenarioId: stringValue(eliteWave.scenarioId),
        encounterId: stringValue(eliteWave.encounterId),
        packConfigAvailable: eliteWave.packConfigAvailable === true,
        packConfigured: eliteWave.packConfigured === true,
        activePluginIds: uniqueStrings(eliteWave.activePluginIds)
      },
      receiptIds,
      completion: {
        destinationReached: completion.destinationReached === true,
        completed: completion.completed === true,
        failed: completion.failed === true,
        completedAtMs: completion.completedAtMs === null || completion.completedAtMs === undefined
          ? null
          : finiteNumber(completion.completedAtMs, 0, 0),
        failedAtMs: completion.failedAtMs === null || completion.failedAtMs === undefined
          ? null
          : finiteNumber(completion.failedAtMs, 0, 0),
        receiptIds: completionReceiptIds.length ? completionReceiptIds : receiptIds
      },
      execution: {
        generatedPluginExecution: execution.generatedPluginExecution === true,
        generatedTemplateExecution: execution.generatedTemplateExecution === true,
        rendererHandoff: execution.rendererHandoff === true,
        saveStateMutated: execution.saveStateMutated === true,
        projectJsonModified: execution.projectJsonModified === true,
        additionalSpawnRequested: execution.additionalSpawnRequested === true
      },
      bridgedAtSequence: integerValue(rawOptions.sequence, 0, 0),
      bridgedAtMs
    };
  }

  function openingShuttleEncounterBridgeDiagnostic(value, options = {}) {
    const rawOptions = objectValue(options);
    const bridge = objectValue(value);
    const available = stringValue(bridge.kind) === OPENING_SHUTTLE_ENCOUNTER_BRIDGE_KIND
      || Boolean(bridge.encounterId || bridge.templateId || bridge.status);
    const objectiveSequence = openingShuttleEncounterObjectiveRecords(bridge.objectiveSequence);
    const receiptIds = uniqueStrings(bridge.receiptIds);
    const execution = objectValue(bridge.execution);
    const completion = objectValue(bridge.completion);
    const eliteWave = objectValue(bridge.eliteWave);
    const counters = objectValue(bridge.counters);
    const generatedPreview = objectValue(rawOptions.generatedStartPreview);
    const generatedPreviewPlan = objectValue(generatedPreview.plan);
    const generatedEncounters = arrayValue(generatedPreviewPlan.encounters).map(objectValue);
    const generatedEncounter = generatedEncounters.find((encounter) => (
      stringValue(encounter.template) === stringValue(bridge.templateId)
    )) || generatedEncounters[0] || {};
    const generatedObjectives = generatedObjectiveRecords(generatedEncounter.objectives);
    const generatedParticipants = generatedParticipantRecords(generatedEncounter.participants);
    const generatedLocation = generatedLocationRecord(generatedEncounter.location);
    const generatedObjectiveTypes = uniqueStrings(
      generatedEncounter.objectiveTypes || generatedObjectives.map((objective) => objective.type)
    );
    const bridgeObjectiveTypes = uniqueStrings(objectiveSequence.map((objective) => objective.type));
    const generatedHostileCount = generatedParticipants.reduce((total, participant) => {
      if (
        stringValue(participant.role) === "hostile"
        || stringValue(participant.actorArchetypeId) === "actor-archetype.shuttle-raider"
      ) {
        return total + integerValue(participant.count, 1, 1, 100);
      }
      return total;
    }, 0);
    const generatedReceiptIds = uniqueStrings(
      generatedPreviewPlan.receiptIds || generatedEncounter.receiptIds
    );
    const liveDefeats = integerValue(counters.defeated, 0, 0);
    const liveEliteDefeats = integerValue(counters.eliteDefeated, 0, 0);
    const liveDestinationReached = completion.destinationReached === true
      || integerValue(counters.destinationReached, 0, 0) > 0;
    const livePlayerSurvived = completion.failed !== true
      && integerValue(counters.playerDefeated, 0, 0) === 0;
    const generatedPreviewAvailable = stringValue(generatedPreview.kind) === GENERATED_SCENARIO_START_PREVIEW_KIND;
    const previewTemplateId = stringValue(generatedPreviewPlan.primaryTemplateId || generatedEncounter.template);
    const templateMatches = generatedPreviewAvailable
      && previewTemplateId
      && previewTemplateId === stringValue(bridge.templateId);
    const locationMatches = generatedPreviewAvailable
      && (!generatedLocation.systemId || generatedLocation.systemId === stringValue(bridge.systemId))
      && (!generatedLocation.destinationId || generatedLocation.destinationId === stringValue(bridge.destinationId));
    const objectiveTypesCovered = generatedPreviewAvailable
      && bridgeObjectiveTypes.every((type) => generatedObjectiveTypes.includes(type));
    const hostileDefeatsSatisfied = !generatedPreviewAvailable
      || generatedHostileCount <= 0
      || liveDefeats >= generatedHostileCount;
    const completionReported = completion.completed === true;
    const completionCoverageSatisfied = !completionReported
      || (hostileDefeatsSatisfied && liveDestinationReached && livePlayerSurvived);
    const problems = [];
    if (!available) {
      problems.push("missing-opening-shuttle-bridge");
    }
    if (generatedPreviewAvailable && !templateMatches) {
      problems.push("generated-preview-template-mismatch");
    }
    if (generatedPreviewAvailable && !locationMatches) {
      problems.push("generated-preview-location-mismatch");
    }
    if (generatedPreviewAvailable && !objectiveTypesCovered) {
      problems.push("generated-preview-objective-types-missing-live-objectives");
    }
    if (generatedPreviewAvailable && completionReported && !hostileDefeatsSatisfied) {
      problems.push("opening-shuttle-completion-missing-authored-hostile-defeats");
    }
    if (completionReported && !liveDestinationReached) {
      problems.push("opening-shuttle-completion-missing-destination-reached");
    }
    if (completionReported && !livePlayerSurvived) {
      problems.push("opening-shuttle-completion-after-player-defeat");
    }
    const executionSafe = execution.generatedPluginExecution !== true
      && execution.generatedTemplateExecution !== true
      && execution.rendererHandoff !== true
      && execution.saveStateMutated !== true
      && execution.projectJsonModified !== true;
    if (!executionSafe) {
      problems.push("opening-shuttle-bridge-execution-safety-flag-enabled");
    }
    const aligned = available
      && executionSafe
      && completionCoverageSatisfied
      && (
        !generatedPreviewAvailable
        || (templateMatches && locationMatches && objectiveTypesCovered)
      );
    return {
      schema: OPENING_SHUTTLE_ENCOUNTER_BRIDGE_DIAGNOSTIC_SCHEMA,
      kind: OPENING_SHUTTLE_ENCOUNTER_BRIDGE_DIAGNOSTIC_KIND,
      generated: false,
      readOnly: true,
      runtimeLocal: true,
      persisted: false,
      projectJsonModified: false,
      saveStateMutated: false,
      available,
      aligned,
      alignmentStatus: aligned ? (generatedPreviewAvailable ? "aligned-with-generated-preview" : "bridge-only") : "blocked",
      bridge: available ? {
        encounterId: stringValue(bridge.encounterId),
        templateId: stringValue(bridge.templateId),
        builtInConsumerId: stringValue(bridge.builtInConsumerId),
        systemId: stringValue(bridge.systemId),
        destinationId: stringValue(bridge.destinationId),
        status: stringValue(bridge.status || "unknown"),
        objectiveLine: stringValue(bridge.objectiveLine),
        objectiveStatuses: objectiveSequence.map((objective) => ({
          id: objective.id,
          type: objective.type,
          status: objective.status,
          required: objective.required
        })),
        counters: {
          spawned: integerValue(counters.spawned, 0, 0),
          activated: integerValue(counters.activated, 0, 0),
          defeated: integerValue(counters.defeated, 0, 0),
          eliteSpawned: integerValue(counters.eliteSpawned, 0, 0),
          eliteDefeated: integerValue(counters.eliteDefeated, 0, 0),
          playerDamaged: integerValue(counters.playerDamaged, 0, 0),
          playerDefeated: integerValue(counters.playerDefeated, 0, 0),
          destinationReached: integerValue(counters.destinationReached, 0, 0)
        },
        eliteWave: {
          enabled: eliteWave.enabled === true,
          triggerDefeats: integerValue(eliteWave.triggerDefeats, 0, 0),
          requested: eliteWave.requested === true,
          spawned: eliteWave.spawned === true,
          cleared: eliteWave.cleared === true,
          requiredExtraHostiles: integerValue(eliteWave.requiredExtraHostiles, 0, 0),
          spawnedCount: integerValue(eliteWave.spawnedCount, 0, 0),
          actorArchetypeId: stringValue(eliteWave.actorArchetypeId),
          source: stringValue(eliteWave.source),
          pluginId: stringValue(eliteWave.pluginId),
          scenarioId: stringValue(eliteWave.scenarioId),
          encounterId: stringValue(eliteWave.encounterId),
          packConfigAvailable: eliteWave.packConfigAvailable === true,
          packConfigured: eliteWave.packConfigured === true,
          activePluginIds: uniqueStrings(eliteWave.activePluginIds)
        },
        completion: {
          destinationReached: completion.destinationReached === true,
          completed: completion.completed === true,
          failed: completion.failed === true,
          receiptIds: uniqueStrings(completion.receiptIds).length
            ? uniqueStrings(completion.receiptIds)
            : receiptIds
        },
        receiptIds
      } : null,
      generatedPreview: generatedPreviewAvailable ? {
        scenarioId: stringValue(generatedPreview.scenarioId),
        knownGeneratedScenario: generatedPreview.knownGeneratedScenario === true,
        canPreviewStart: generatedPreview.canPreviewStart === true,
        startable: generatedPreview.startable === true,
        startableInRuntime: generatedPreview.startableInRuntime === true,
        activationStatus: stringValue(generatedPreview.activationStatus),
        reason: stringValue(generatedPreview.reason),
        primaryTemplateId: previewTemplateId,
        primaryEncounterId: stringValue(generatedPreviewPlan.primaryEncounterId || generatedEncounter.id),
        templateMatches,
        location: generatedLocation,
        locationMatches,
        objectiveTypes: generatedObjectiveTypes,
        objectiveTypesCovered,
        hostileCount: generatedHostileCount,
        receiptIds: generatedReceiptIds,
        previewOnly: true,
        rendererHandoff: false,
        gameplayTemplateExecution: false,
        saveStateMutated: false,
        projectJsonModified: false
      } : null,
      coverage: {
        generatedPreviewAvailable,
        authoredHostileCount: generatedHostileCount,
        liveDefeats,
        liveEliteDefeats,
        hostileDefeatsSatisfied,
        liveDestinationReached,
        livePlayerSurvived,
        completionReported,
        completionCoverageSatisfied,
        authoredObjectiveTypes: generatedObjectiveTypes,
        liveObjectiveTypes: bridgeObjectiveTypes,
        objectiveTypesCovered,
        authoredReceiptIds: generatedReceiptIds,
        liveReceiptIds: receiptIds
      },
      safety: {
        executionSafe,
        generatedPluginExecution: execution.generatedPluginExecution === true,
        generatedTemplateExecution: execution.generatedTemplateExecution === true,
        rendererHandoff: execution.rendererHandoff === true,
        saveStateMutated: execution.saveStateMutated === true,
        projectJsonModified: execution.projectJsonModified === true,
        additionalSpawnRequested: execution.additionalSpawnRequested === true
      },
      problems
    };
  }

  function generatedGameplayDocument(value) {
    const raw = objectValue(value);
    const kind = stringValue(raw.kind);
    return {
      pluginId: stringValue(raw.pluginId),
      kind,
      id: stringValue(raw.id),
      title: stringValue(raw.title || raw.id),
      path: stringValue(raw.path),
      template: stringValue(raw.template),
      stageIds: uniqueStrings(raw.stageIds),
      encounterIds: uniqueStrings(raw.encounterIds),
      objectiveTypes: uniqueStrings(raw.objectiveTypes),
      actorArchetypes: uniqueStrings(raw.actorArchetypes),
      receiptIds: uniqueStrings(raw.receiptIds),
      consequenceTypes: uniqueStrings(raw.consequenceTypes),
      objectives: generatedObjectiveRecords(raw.objectives),
      participants: generatedParticipantRecords(raw.participants),
      location: generatedLocationRecord(raw.location),
      generated: true,
      readOnly: true
    };
  }

  function generatedGameplayPluginSummary(value) {
    const raw = objectValue(value);
    return {
      pluginId: stringValue(raw.pluginId),
      status: stringValue(raw.status),
      generatedRoot: stringValue(raw.generatedRoot),
      entryPoints: uniqueStrings(raw.entryPoints),
      scenarioIds: uniqueStrings(raw.scenarioIds),
      encounterIds: uniqueStrings(raw.encounterIds),
      receiptIds: uniqueStrings(raw.receiptIds),
      consequenceIds: uniqueStrings(raw.consequenceIds),
      runtimeLoaded: raw.runtimeLoaded === true,
      projectJsonModified: raw.projectJsonModified === true,
      activatedInRuntime: raw.activatedInRuntime === true,
      problems: uniqueStrings(raw.problems)
    };
  }

  function mergeGeneratedGameplayDocuments(primary, fallback, kind) {
    const byId = new Map();
    arrayValue(primary).concat(arrayValue(fallback)).forEach((entry) => {
      const document = generatedGameplayDocument(entry);
      if (document.kind === kind && document.id && !byId.has(document.id)) {
        byId.set(document.id, document);
      }
    });
    return [...byId.values()];
  }

  function generatedScenarioAvailabilityCard(catalog, scenario) {
    const entryPoints = new Set(catalog.entryPoints || []);
    const encounters = new Map(
      arrayValue(catalog.encounters).map((encounter) => [encounter.id, encounter])
    );
    const linkedEncounters = uniqueStrings(scenario.encounterIds).map(
      (encounterId) => encounters.get(encounterId)
    ).filter(Boolean);
    const encounterTemplates = uniqueStrings(
      linkedEncounters.map((encounter) => encounter.template).filter(Boolean)
    );
    const objectiveTypes = uniqueStrings(
      linkedEncounters.flatMap((encounter) => (
        uniqueStrings(encounter.objectiveTypes)
          .concat(generatedObjectiveRecords(encounter.objectives).map((objective) => objective.type))
      ))
    );
    const actorArchetypes = uniqueStrings(
      linkedEncounters.flatMap((encounter) => (
        uniqueStrings(encounter.actorArchetypes)
          .concat(generatedParticipantRecords(encounter.participants).map((participant) => participant.actorArchetypeId))
      ))
    );
    const consequenceTypes = uniqueStrings(
      scenario.consequenceTypes.concat(
        linkedEncounters.flatMap((encounter) => encounter.consequenceTypes)
      )
    );
    return {
      schema: GENERATED_SCENARIO_AVAILABILITY_SCHEMA,
      kind: "generated-scenario-card",
      id: scenario.id,
      scenarioId: scenario.id,
      pluginId: scenario.pluginId,
      title: scenario.title || scenario.id,
      path: scenario.path,
      generated: true,
      readOnly: true,
      available: catalog.ready === true,
      startable: false,
      activationStatus: "read-only",
      reason: "generated-catalog-read-only",
      source: "project.metadata.generatedGameplayPlugins",
      entryPoint: entryPoints.has(scenario.id),
      stageIds: uniqueStrings(scenario.stageIds),
      encounterIds: uniqueStrings(scenario.encounterIds),
      encounterTemplates,
      objectiveTypes,
      actorArchetypes,
      receiptIds: uniqueStrings(scenario.receiptIds),
      consequenceTypes
    };
  }

  function generatedScenarioAvailability(catalog) {
    const normalized = normalizeGeneratedGameplayCatalog(catalog);
    const cards = normalized.ready
      ? normalized.scenarios.map((scenario) => generatedScenarioAvailabilityCard(normalized, scenario))
      : [];
    return {
      schema: GENERATED_SCENARIO_AVAILABILITY_SCHEMA,
      kind: GENERATED_SCENARIO_AVAILABILITY_KIND,
      readOnly: true,
      runtimeLoaded: false,
      projectJsonModified: false,
      activatedInRuntime: false,
      status: normalized.status,
      ready: normalized.ready,
      exported: normalized.exported,
      enabledPluginIds: normalized.enabledPluginIds.slice(),
      entryPoints: normalized.entryPoints.slice(),
      count: cards.length,
      cards,
      scenarioIds: cards.map((card) => card.scenarioId),
      entryPointScenarioIds: cards.filter((card) => card.entryPoint).map((card) => card.scenarioId),
      problems: normalized.problems.slice()
    };
  }

  function generatedScenarioStartPreviewPlan(catalog, scenario, linkedEncounters) {
    const stageIds = uniqueStrings(scenario.stageIds);
    const encounterIds = uniqueStrings(scenario.encounterIds);
    const encounterTemplates = uniqueStrings(
      linkedEncounters.map((encounter) => encounter.template).filter(Boolean)
    );
    const objectiveTypes = uniqueStrings(
      linkedEncounters.flatMap((encounter) => encounter.objectiveTypes)
    );
    const actorArchetypes = uniqueStrings(
      linkedEncounters.flatMap((encounter) => encounter.actorArchetypes)
    );
    const receiptIds = uniqueStrings(
      scenario.receiptIds.concat(linkedEncounters.flatMap((encounter) => encounter.receiptIds))
    );
    const consequenceTypes = uniqueStrings(
      scenario.consequenceTypes.concat(
        linkedEncounters.flatMap((encounter) => encounter.consequenceTypes)
      )
    );
    return {
      source: "project.metadata.generatedGameplayPlugins",
      catalogSchema: catalog.schema,
      catalogKind: catalog.kind,
      catalogStatus: catalog.status,
      scenario: {
        id: scenario.id,
        scenarioId: scenario.id,
        pluginId: scenario.pluginId,
        title: scenario.title || scenario.id,
        path: scenario.path,
        stageIds,
        encounterIds,
        receiptIds: uniqueStrings(scenario.receiptIds),
        consequenceTypes: uniqueStrings(scenario.consequenceTypes)
      },
      encounters: linkedEncounters.map((encounter) => ({
        id: encounter.id,
        encounterId: encounter.id,
        pluginId: encounter.pluginId,
        title: encounter.title || encounter.id,
        path: encounter.path,
        template: encounter.template,
        objectiveTypes: uniqueStrings(encounter.objectiveTypes),
        actorArchetypes: uniqueStrings(encounter.actorArchetypes),
        objectives: generatedObjectiveRecords(encounter.objectives),
        participants: generatedParticipantRecords(encounter.participants),
        location: generatedLocationRecord(encounter.location),
        receiptIds: uniqueStrings(encounter.receiptIds),
        consequenceTypes: uniqueStrings(encounter.consequenceTypes)
      })),
      primaryEncounterId: linkedEncounters.length ? linkedEncounters[0].id : "",
      primaryTemplateId: encounterTemplates.length ? encounterTemplates[0] : "",
      encounterTemplates,
      objectiveTypes,
      actorArchetypes,
      receiptIds,
      consequenceTypes,
      operations: [
        {
          kind: "read-generated-scenario",
          id: scenario.id,
          path: scenario.path,
          reversible: true
        }
      ].concat(linkedEncounters.map((encounter) => ({
        kind: "read-generated-encounter",
        id: encounter.id,
        path: encounter.path,
        template: encounter.template,
        reversible: true
      })))
    };
  }

  function generatedScenarioStartPreview(catalog, scenarioId, options = {}) {
    const normalized = normalizeGeneratedGameplayCatalog(catalog);
    const id = stringValue(scenarioId);
    const problems = normalized.problems.slice();
    const entryPoints = new Set(normalized.entryPoints || []);
    const scenarios = new Map(
      arrayValue(normalized.scenarios).map((scenario) => [scenario.id, scenario])
    );
    const encounters = new Map(
      arrayValue(normalized.encounters).map((encounter) => [encounter.id, encounter])
    );
    const scenario = scenarios.get(id) || null;
    const missingEncounterIds = scenario
      ? uniqueStrings(scenario.encounterIds).filter((encounterId) => !encounters.has(encounterId))
      : [];
    missingEncounterIds.forEach((encounterId) => {
      problems.push(`generated scenario references missing encounter: ${encounterId}`);
    });
    const linkedEncounters = scenario
      ? uniqueStrings(scenario.encounterIds).map((encounterId) => encounters.get(encounterId)).filter(Boolean)
      : [];
    const catalogReady = normalized.ready === true;
    const entryPoint = !!(scenario && entryPoints.has(id));
    const previewReady = catalogReady && !!scenario && missingEncounterIds.length === 0;
    const canPreviewStart = previewReady && entryPoint;
    let reason = "generated-runtime-start-not-implemented";
    if (!id) {
      reason = "missing-generated-scenario-id";
    } else if (!scenario) {
      reason = "unknown-generated-scenario";
    } else if (!catalogReady) {
      reason = "generated-catalog-not-ready";
    } else if (missingEncounterIds.length) {
      reason = "generated-scenario-missing-encounter";
    } else if (!entryPoint) {
      reason = "generated-scenario-not-entry-point";
    }
    return {
      schema: GENERATED_SCENARIO_START_PREVIEW_SCHEMA,
      kind: GENERATED_SCENARIO_START_PREVIEW_KIND,
      id,
      scenarioId: id,
      generated: true,
      readOnly: true,
      dryRun: true,
      runtimeLoaded: false,
      projectJsonModified: false,
      activatedInRuntime: false,
      startable: false,
      startableInRuntime: false,
      canPreviewStart,
      couldStartAfterRuntimeActivation: canPreviewStart,
      activationStatus: "preview-only",
      reason,
      catalogStatus: normalized.status,
      catalogReady,
      catalogExported: normalized.exported,
      entryPoint,
      pluginId: scenario ? scenario.pluginId : "",
      title: scenario ? scenario.title || scenario.id : "",
      missingEncounterIds,
      plan: previewReady ? generatedScenarioStartPreviewPlan(normalized, scenario, linkedEncounters) : null,
      options: {
        dryRun: true,
        source: stringValue(objectValue(options).source || "generated-scenario-start-preview")
      },
      problems: [...new Set(problems)]
    };
  }

  function generatedScenarioActivationOptions(options = {}) {
    const raw = objectValue(options);
    return {
      runtimeActivationEnabled: raw.runtimeActivationEnabled === true
        || raw.generatedScenarioActivationEnabled === true,
      dryRun: true,
      source: stringValue(raw.source || "generated-scenario-start-command")
    };
  }

  function generatedScenarioPreviewShell(preview, activationOptions = {}) {
    const plan = objectValue(preview.plan);
    const scenario = objectValue(plan.scenario);
    return {
      schema: "game.generatedScenarioPreviewShell.v1",
      kind: "generated-scenario-preview-shell",
      generated: true,
      readOnly: true,
      dryRun: true,
      status: "preview-shell",
      scenarioId: stringValue(preview.scenarioId),
      title: stringValue(preview.title || scenario.title),
      pluginId: stringValue(preview.pluginId || scenario.pluginId),
      entryPoint: preview.entryPoint === true,
      encounterIds: uniqueStrings(scenario.encounterIds || preview.plan?.encounters?.map((encounter) => encounter.id)),
      primaryEncounterId: stringValue(plan.primaryEncounterId),
      primaryTemplateId: stringValue(plan.primaryTemplateId),
      objectiveTypes: uniqueStrings(plan.objectiveTypes),
      actorArchetypes: uniqueStrings(plan.actorArchetypes),
      receiptIds: uniqueStrings(plan.receiptIds),
      consequenceTypes: uniqueStrings(plan.consequenceTypes),
      encounters: arrayValue(plan.encounters).map((encounter) => {
        const raw = objectValue(encounter);
        return {
          id: stringValue(raw.id || raw.encounterId),
          encounterId: stringValue(raw.encounterId || raw.id),
          pluginId: stringValue(raw.pluginId),
          title: stringValue(raw.title || raw.id || raw.encounterId),
          path: stringValue(raw.path),
          template: stringValue(raw.template),
          objectiveTypes: uniqueStrings(raw.objectiveTypes),
          actorArchetypes: uniqueStrings(raw.actorArchetypes),
          objectives: generatedObjectiveRecords(raw.objectives),
          participants: generatedParticipantRecords(raw.participants),
          location: generatedLocationRecord(raw.location),
          receiptIds: uniqueStrings(raw.receiptIds),
          consequenceTypes: uniqueStrings(raw.consequenceTypes)
        };
      }).filter((encounter) => encounter.id),
      operations: arrayValue(plan.operations).map((operation) => ({
        kind: stringValue(objectValue(operation).kind),
        id: stringValue(objectValue(operation).id),
        path: stringValue(objectValue(operation).path),
        template: stringValue(objectValue(operation).template),
        reversible: objectValue(operation).reversible !== false
      })).filter((operation) => operation.kind && operation.id),
      activation: {
        runtimeActivationEnabled: activationOptions.runtimeActivationEnabled === true,
        rendererHandoff: false,
        gameplayTemplateExecution: false,
        projectJsonModified: false,
        saveStateMutated: false
      }
    };
  }



  function generatedScenarioPreviewShellState(command, options = {}) {
    const raw = objectValue(command);
    const previewShell = objectValue(raw.previewShell);
    if (raw.accepted !== true || !previewShell.scenarioId) return null;
    return {
      schema: GENERATED_SCENARIO_PREVIEW_SHELL_STATE_SCHEMA,
      kind: GENERATED_SCENARIO_PREVIEW_SHELL_STATE_KIND,
      generated: true,
      readOnly: true,
      dryRun: true,
      active: true,
      status: "active-preview-shell",
      reason: "generated-runtime-preview-shell-active",
      source: stringValue(objectValue(options).source || raw.options?.source || "generated-scenario-preview-shell-state"),
      scenarioId: stringValue(previewShell.scenarioId),
      title: stringValue(previewShell.title),
      pluginId: stringValue(previewShell.pluginId),
      entryPoint: previewShell.entryPoint === true,
      encounterIds: uniqueStrings(previewShell.encounterIds),
      primaryEncounterId: stringValue(previewShell.primaryEncounterId),
      primaryTemplateId: stringValue(previewShell.primaryTemplateId),
      objectiveTypes: uniqueStrings(previewShell.objectiveTypes),
      actorArchetypes: uniqueStrings(previewShell.actorArchetypes),
      receiptIds: uniqueStrings(previewShell.receiptIds),
      consequenceTypes: uniqueStrings(previewShell.consequenceTypes),
      encounters: arrayValue(previewShell.encounters).map((encounter) => {
        const raw = objectValue(encounter);
        return {
          id: stringValue(raw.id || raw.encounterId),
          encounterId: stringValue(raw.encounterId || raw.id),
          pluginId: stringValue(raw.pluginId),
          title: stringValue(raw.title || raw.id || raw.encounterId),
          path: stringValue(raw.path),
          template: stringValue(raw.template),
          objectiveTypes: uniqueStrings(raw.objectiveTypes),
          actorArchetypes: uniqueStrings(raw.actorArchetypes),
          objectives: generatedObjectiveRecords(raw.objectives),
          participants: generatedParticipantRecords(raw.participants),
          location: generatedLocationRecord(raw.location),
          receiptIds: uniqueStrings(raw.receiptIds),
          consequenceTypes: uniqueStrings(raw.consequenceTypes)
        };
      }).filter((encounter) => encounter.id),
      operations: arrayValue(previewShell.operations).map((operation) => ({
        kind: stringValue(objectValue(operation).kind),
        id: stringValue(objectValue(operation).id),
        path: stringValue(objectValue(operation).path),
        template: stringValue(objectValue(operation).template),
        reversible: objectValue(operation).reversible !== false
      })).filter((operation) => operation.kind && operation.id),
      activation: {
        runtimeActivationEnabled: true,
        previewShellOnly: true,
        rendererHandoff: false,
        gameplayTemplateExecution: false,
        projectJsonModified: false,
        saveStateMutated: false,
        persisted: false
      },
      baseRuntime: {
        sequence: integerValue(objectValue(options).sequence, 0, 0),
        activeSystemId: stringValue(objectValue(options).activeSystemId)
      }
    };
  }


  function generatedScenarioPreviewEventRecord(eventType, detail = {}, options = {}) {
    const rawDetail = objectValue(detail);
    const rawOptions = objectValue(options);
    const preview = objectValue(rawDetail.preview || rawDetail.previous || rawDetail.current);
    return {
      schema: GENERATED_SCENARIO_PREVIEW_EVENT_SCHEMA,
      kind: GENERATED_SCENARIO_PREVIEW_EVENT_KIND,
      generated: true,
      readOnly: true,
      dryRun: true,
      runtimeLocal: true,
      persisted: false,
      runtimeLoaded: false,
      projectJsonModified: false,
      activatedInRuntime: false,
      rendererHandoff: false,
      gameplayTemplateExecution: false,
      saveStateMutated: false,
      eventType: stringValue(eventType || rawDetail.eventType || "generated-preview-event"),
      reason: stringValue(rawDetail.reason || rawOptions.reason || eventType || "generated-preview-event"),
      source: stringValue(rawDetail.source || rawOptions.source || ""),
      eventSequence: integerValue(rawOptions.eventSequence, 0, 0),
      runtimeSequence: integerValue(rawOptions.runtimeSequence, 0, 0),
      activeSystemId: stringValue(rawOptions.activeSystemId || rawDetail.activeSystemId),
      active: rawDetail.active === true,
      scenarioId: stringValue(rawDetail.scenarioId || preview.scenarioId),
      title: stringValue(rawDetail.title || preview.title),
      pluginId: stringValue(rawDetail.pluginId || preview.pluginId),
      primaryEncounterId: stringValue(rawDetail.primaryEncounterId || preview.primaryEncounterId),
      primaryTemplateId: stringValue(rawDetail.primaryTemplateId || preview.primaryTemplateId),
      objectiveTypes: uniqueStrings(rawDetail.objectiveTypes || preview.objectiveTypes),
      actorArchetypes: uniqueStrings(rawDetail.actorArchetypes || preview.actorArchetypes),
      receiptIds: uniqueStrings(rawDetail.receiptIds || preview.receiptIds),
      consequenceTypes: uniqueStrings(rawDetail.consequenceTypes || preview.consequenceTypes),
      problems: uniqueStrings(rawDetail.problems)
    };
  }

  function generatedScenarioTemplateHandoff(previewShell, options = {}) {
    const raw = objectValue(previewShell);
    const rawOptions = objectValue(options);
    const supportedTemplates = uniqueStrings(rawOptions.supportedTemplates).length
      ? uniqueStrings(rawOptions.supportedTemplates)
      : GENERATED_SCENARIO_TEMPLATE_HANDOFF_SUPPORTED_TEMPLATES.slice();
    const supportedSet = new Set(supportedTemplates);
    const scenarioId = stringValue(raw.scenarioId);
    const templateId = stringValue(raw.primaryTemplateId || raw.templateId);
    const active = raw.active === true
      || stringValue(raw.status) === "active-preview-shell"
      || stringValue(raw.kind) === "generated-scenario-preview-shell";
    const primaryEncounterId = stringValue(raw.primaryEncounterId);
    let reason = "generated-template-handoff-ready";
    if (!scenarioId) {
      reason = "missing-generated-preview-shell";
    } else if (!active) {
      reason = "generated-preview-shell-inactive";
    } else if (!templateId) {
      reason = "missing-generated-gameplay-template";
    } else if (!supportedSet.has(templateId)) {
      reason = "unsupported-generated-gameplay-template";
    }
    const accepted = reason === "generated-template-handoff-ready";
    const encounterDetails = arrayValue(raw.encounters).map((encounter) => {
      const current = objectValue(encounter);
      return {
        id: stringValue(current.id || current.encounterId),
        encounterId: stringValue(current.encounterId || current.id),
        pluginId: stringValue(current.pluginId),
        title: stringValue(current.title || current.id || current.encounterId),
        path: stringValue(current.path),
        template: stringValue(current.template),
        objectiveTypes: uniqueStrings(current.objectiveTypes),
        actorArchetypes: uniqueStrings(current.actorArchetypes),
        objectives: generatedObjectiveRecords(current.objectives),
        participants: generatedParticipantRecords(current.participants),
        location: generatedLocationRecord(current.location),
        receiptIds: uniqueStrings(current.receiptIds),
        consequenceTypes: uniqueStrings(current.consequenceTypes)
      };
    }).filter((encounter) => encounter.id);
    const primaryEncounter = encounterDetails.find((encounter) => encounter.id === primaryEncounterId)
      || encounterDetails[0]
      || {};
    const detailedObjectives = generatedObjectiveRecords(primaryEncounter.objectives);
    const detailedActors = generatedParticipantRecords(primaryEncounter.participants);
    const objectiveTypes = uniqueStrings(
      raw.objectiveTypes || detailedObjectives.map((objective) => objective.type)
    );
    const actorArchetypes = uniqueStrings(
      raw.actorArchetypes || detailedActors.map((participant) => participant.actorArchetypeId)
    );
    const receiptIds = uniqueStrings(raw.receiptIds);
    const consequenceTypes = uniqueStrings(raw.consequenceTypes);
    const encounterIds = uniqueStrings(raw.encounterIds || (primaryEncounterId ? [primaryEncounterId] : []));
    const templateObjectives = detailedObjectives.length
      ? detailedObjectives
      : objectiveTypes.map((type) => ({type}));
    const templateActors = detailedActors.length
      ? detailedActors
      : actorArchetypes.map((archetype) => ({archetype}));
    const operations = accepted ? [{
      kind: "handoff-generated-gameplay-template",
      scenarioId,
      encounterId: primaryEncounterId,
      templateId,
      reversible: true,
      executed: false
    }] : [];
    return {
      schema: GENERATED_SCENARIO_TEMPLATE_HANDOFF_SCHEMA,
      kind: GENERATED_SCENARIO_TEMPLATE_HANDOFF_KIND,
      generated: true,
      readOnly: true,
      dryRun: true,
      runtimeLocal: true,
      persisted: false,
      runtimeLoaded: false,
      projectJsonModified: false,
      activatedInRuntime: false,
      rendererHandoff: false,
      gameplayTemplateExecution: false,
      saveStateMutated: false,
      accepted,
      blocked: !accepted,
      handoffStatus: accepted ? "ready" : "blocked",
      activationStatus: accepted ? "template-handoff-ready" : "template-handoff-blocked",
      reason,
      source: stringValue(rawOptions.source || "generated-scenario-template-handoff"),
      scenarioId,
      title: stringValue(raw.title),
      pluginId: stringValue(raw.pluginId),
      entryPoint: raw.entryPoint === true,
      encounterIds,
      primaryEncounterId,
      templateId,
      primaryTemplateId: templateId,
      supportedTemplate: supportedSet.has(templateId),
      supportedTemplates,
      objectiveTypes,
      actorArchetypes,
      receiptIds,
      consequenceTypes,
      encounters: encounterDetails,
      operations,
      templateInput: {
        scenario: {
          id: scenarioId,
          scenarioId,
          title: stringValue(raw.title),
          pluginId: stringValue(raw.pluginId),
          entryPoint: raw.entryPoint === true
        },
        encounter: {
          id: primaryEncounterId,
          encounterId: primaryEncounterId,
          encounterIds,
          templateId,
          primaryTemplateId: templateId,
          title: stringValue(primaryEncounter.title),
          path: stringValue(primaryEncounter.path),
          location: generatedLocationRecord(primaryEncounter.location)
        },
        objectives: templateObjectives,
        actors: templateActors,
        receipts: receiptIds.map((id) => ({id})),
        consequences: consequenceTypes.map((type) => ({type}))
      },
      activation: {
        previewShellOnly: true,
        templateHandoffOnly: true,
        rendererHandoff: false,
        gameplayTemplateExecution: false,
        projectJsonModified: false,
        saveStateMutated: false,
        persisted: false
      },
      problems: accepted ? [] : [reason]
    };
  }


  function generatedTemplateExecutorRegistry(options = {}) {
    const rawOptions = objectValue(options);
    const templateIds = uniqueStrings(rawOptions.templateIds).length
      ? uniqueStrings(rawOptions.templateIds)
      : GENERATED_SCENARIO_TEMPLATE_HANDOFF_SUPPORTED_TEMPLATES.slice();
    const executors = templateIds.map((templateId) => ({
      templateId,
      registered: true,
      executorMode: GENERATED_TEMPLATE_EXECUTOR_DEFAULT_MODE,
      executionEnabled: false,
      enabled: false,
      dryRunOnly: true,
      noOp: true,
      rendererHandoff: false,
      gameplayTemplateExecution: false,
      saveStateMutated: false,
      projectJsonModified: false,
      description: `Generated gameplay template executor placeholder for ${templateId}.`
    }));
    return {
      schema: GENERATED_TEMPLATE_EXECUTOR_REGISTRY_SCHEMA,
      kind: GENERATED_TEMPLATE_EXECUTOR_REGISTRY_KIND,
      generated: true,
      readOnly: true,
      dryRun: true,
      runtimeLocal: true,
      persisted: false,
      runtimeLoaded: false,
      projectJsonModified: false,
      activatedInRuntime: false,
      rendererHandoff: false,
      gameplayTemplateExecution: false,
      saveStateMutated: false,
      executorMode: GENERATED_TEMPLATE_EXECUTOR_DEFAULT_MODE,
      executionEnabled: false,
      enabled: false,
      noOp: true,
      registeredTemplateIds: templateIds,
      executors,
      problems: []
    };
  }

  function generatedTemplateExecutorForTemplate(registry, templateId) {
    const rawRegistry = objectValue(registry);
    const id = stringValue(templateId);
    return arrayValue(rawRegistry.executors)
      .map(objectValue)
      .find((executor) => stringValue(executor.templateId) === id) || null;
  }

  function generatedTemplateObjectivePlan(handoff) {
    const rawHandoff = objectValue(handoff);
    const templateInput = objectValue(rawHandoff.templateInput);
    const inputObjectives = arrayValue(templateInput.objectives).map((item, index) => {
      const raw = objectValue(item);
      const type = stringValue(raw.type || raw.objectiveType || raw.objectiveTypeId);
      if (!type) return null;
      return {
        id: stringValue(raw.id || raw.objectiveId || type),
        type,
        required: raw.required !== false,
        sequence: index + 1
      };
    }).filter(Boolean);
    if (inputObjectives.length) return inputObjectives;
    return uniqueStrings(rawHandoff.objectiveTypes).map((type, index) => ({
      id: type,
      type,
      required: true,
      sequence: index + 1
    }));
  }

  function generatedTemplateActorPlan(handoff) {
    const rawHandoff = objectValue(handoff);
    const templateInput = objectValue(rawHandoff.templateInput);
    const inputActors = arrayValue(templateInput.actors).map((item) => {
      const raw = objectValue(item);
      const archetypeId = stringValue(
        raw.actorArchetypeId || raw.actorArchetype || raw.archetype || raw.archetypeId
      );
      if (!archetypeId) return null;
      return {
        role: stringValue(raw.role || (archetypeId === "actor-archetype.shuttle-raider" ? "hostile" : "")),
        actorArchetypeId: archetypeId,
        count: integerValue(raw.count, 1, 1, 100)
      };
    }).filter(Boolean);
    if (inputActors.length) return inputActors;
    return uniqueStrings(rawHandoff.actorArchetypes).map((archetypeId) => ({
      role: archetypeId === "actor-archetype.shuttle-raider" ? "hostile" : "",
      actorArchetypeId: archetypeId,
      count: 1
    }));
  }

  function generatedShuttleAmbushExecutorPreview(handoff) {
    const rawHandoff = objectValue(handoff);
    const templateInput = objectValue(rawHandoff.templateInput);
    const scenario = objectValue(templateInput.scenario);
    const encounter = objectValue(templateInput.encounter);
    const objectives = generatedTemplateObjectivePlan(rawHandoff);
    const actors = generatedTemplateActorPlan(rawHandoff);
    const hostileActors = actors.filter((actor) => (
      stringValue(actor.role) === "hostile"
      || stringValue(actor.actorArchetypeId) === "actor-archetype.shuttle-raider"
    ));
    const primaryHostile = hostileActors[0] || actors[0] || {
      role: "hostile",
      actorArchetypeId: "actor-archetype.shuttle-raider",
      count: 1
    };
    const rawWaves = arrayValue(objectValue(encounter).waves);
    const waves = rawWaves.length
      ? rawWaves.map((item, index) => {
        const raw = objectValue(item);
        return {
          id: stringValue(raw.id || `wave-${index + 1}`),
          trigger: stringValue(raw.trigger || (index === 0 ? "encounter-start" : "after-previous-wave-cleared")),
          hostileArchetypeId: stringValue(raw.actorArchetypeId || raw.hostileArchetypeId || primaryHostile.actorArchetypeId),
          count: integerValue(raw.count, primaryHostile.count || 1, 1, 100),
          spawnPointIds: uniqueStrings(raw.spawnPointIds),
          rendererHandoff: false,
          gameplayTemplateExecution: false
        };
      })
      : [{
        id: "wave-1",
        trigger: "encounter-start",
        hostileArchetypeId: stringValue(primaryHostile.actorArchetypeId),
        count: integerValue(primaryHostile.count, 1, 1, 100),
        spawnPointIds: [],
        spawnPointContract: "opening-shuttle-ambush-runtime-selects-safe-boarder-pads",
        rendererHandoff: false,
        gameplayTemplateExecution: false
      }];
    const objectiveTypes = objectives.map((objective) => objective.type);
    const receiptIds = uniqueStrings(rawHandoff.receiptIds);
    const consequenceTypes = uniqueStrings(rawHandoff.consequenceTypes);
    return {
      schema: "game.generatedTemplateExecutorPreview.shuttleAmbush.v1",
      kind: "generated-template-executor-preview",
      templateId: "encounter-template.shuttle-ambush",
      previewKind: "shuttle-ambush-plan",
      generated: true,
      readOnly: true,
      dryRun: true,
      runtimeLocal: true,
      persisted: false,
      runtimeLoaded: false,
      projectJsonModified: false,
      activatedInRuntime: false,
      rendererHandoff: false,
      gameplayTemplateExecution: false,
      saveStateMutated: false,
      noOp: true,
      executionEnabled: false,
      executed: false,
      scenario: {
        id: stringValue(rawHandoff.scenarioId || scenario.id),
        scenarioId: stringValue(rawHandoff.scenarioId || scenario.scenarioId || scenario.id),
        title: stringValue(rawHandoff.title || scenario.title),
        pluginId: stringValue(rawHandoff.pluginId || scenario.pluginId)
      },
      encounter: {
        id: stringValue(rawHandoff.primaryEncounterId || encounter.id),
        encounterId: stringValue(rawHandoff.primaryEncounterId || encounter.encounterId || encounter.id),
        templateId: "encounter-template.shuttle-ambush",
        location: generatedLocationRecord(encounter.location)
      },
      objectives,
      objectiveSequence: objectives.map((objective) => ({
        id: objective.id,
        type: objective.type,
        required: objective.required
      })),
      waves,
      hostiles: hostileActors,
      survivalRequired: objectiveTypes.includes("objective-type.survive"),
      clearHostilesRequired: objectiveTypes.includes("objective-type.clear-hostiles"),
      destinationRequired: objectiveTypes.includes("objective-type.reach-destination"),
      completion: {
        receiptIds,
        consequenceTypes,
        requiredSignals: [
          "opening-shuttle-ambush-started",
          "opening-shuttle-raider-defeated",
          "opening-shuttle-hostiles-cleared",
          "opening-shuttle-destination-reached"
        ]
      },
      execution: {
        rendererHandoff: false,
        gameplayTemplateExecution: false,
        saveStateMutated: false,
        projectJsonModified: false,
        persisted: false
      },
      operations: [{
        kind: "preview-shuttle-ambush-template-plan",
        scenarioId: stringValue(rawHandoff.scenarioId || scenario.id),
        encounterId: stringValue(rawHandoff.primaryEncounterId || encounter.id),
        templateId: "encounter-template.shuttle-ambush",
        executed: false,
        reversible: true,
        rendererHandoff: false,
        gameplayTemplateExecution: false,
        saveStateMutated: false
      }],
      problems: []
    };
  }

  function generatedTemplateExecutorPreview(handoff, options = {}) {
    const rawHandoff = objectValue(handoff);
    const templateId = stringValue(rawHandoff.templateId || rawHandoff.primaryTemplateId);
    const executor = objectValue(objectValue(options).executor);
    const executorRegistered = Boolean(executor.templateId);
    if (!executorRegistered || rawHandoff.accepted !== true || !templateId) return null;
    if (templateId === "encounter-template.shuttle-ambush") {
      return generatedShuttleAmbushExecutorPreview(rawHandoff);
    }
    return {
      schema: "game.generatedTemplateExecutorPreview.generic.v1",
      kind: "generated-template-executor-preview",
      templateId,
      previewKind: "generic-template-plan",
      generated: true,
      readOnly: true,
      dryRun: true,
      runtimeLocal: true,
      persisted: false,
      runtimeLoaded: false,
      projectJsonModified: false,
      activatedInRuntime: false,
      rendererHandoff: false,
      gameplayTemplateExecution: false,
      saveStateMutated: false,
      noOp: true,
      executionEnabled: false,
      executed: false,
      objectiveTypes: uniqueStrings(rawHandoff.objectiveTypes),
      actorArchetypes: uniqueStrings(rawHandoff.actorArchetypes),
      receiptIds: uniqueStrings(rawHandoff.receiptIds),
      consequenceTypes: uniqueStrings(rawHandoff.consequenceTypes),
      operations: [],
      problems: []
    };
  }

  function generatedTemplateExecutorStatus(handoff, options = {}) {
    const rawHandoff = objectValue(handoff);
    const rawOptions = objectValue(options);
    const registry = objectValue(rawOptions.registry).kind
      ? objectValue(rawOptions.registry)
      : generatedTemplateExecutorRegistry(rawOptions);
    const templateId = stringValue(rawHandoff.templateId || rawHandoff.primaryTemplateId);
    const executor = generatedTemplateExecutorForTemplate(registry, templateId);
    const handoffAccepted = rawHandoff.accepted === true;
    const executorRegistered = Boolean(executor);
    const executionEnabled = executorRegistered && objectValue(executor).executionEnabled === true;
    let reason = "generated-template-execution-disabled";
    if (!handoffAccepted) {
      reason = "generated-template-handoff-blocked";
    } else if (!templateId) {
      reason = "missing-generated-gameplay-template";
    } else if (!executorRegistered) {
      reason = "no-generated-template-executor";
    } else if (executionEnabled) {
      reason = "generated-template-execution-enabled-preview-only";
    }
    const couldExecuteIfEnabled = handoffAccepted && executorRegistered && Boolean(templateId);
    const blocked = !executionEnabled;
    const templatePreview = generatedTemplateExecutorPreview(rawHandoff, {executor});
    const templateInput = objectValue(rawHandoff.templateInput);
    const scenario = objectValue(templateInput.scenario);
    const encounter = objectValue(templateInput.encounter);
    const operation = {
      kind: "preview-generated-gameplay-template-executor",
      scenarioId: stringValue(rawHandoff.scenarioId || scenario.id),
      encounterId: stringValue(rawHandoff.primaryEncounterId || encounter.id),
      templateId,
      executorMode: executorRegistered
        ? stringValue(objectValue(executor).executorMode || GENERATED_TEMPLATE_EXECUTOR_DEFAULT_MODE)
        : "",
      wouldExecuteIfEnabled: couldExecuteIfEnabled,
      executed: false,
      rendererHandoff: false,
      gameplayTemplateExecution: false,
      reversible: true
    };
    return {
      schema: GENERATED_TEMPLATE_EXECUTOR_STATUS_SCHEMA,
      kind: GENERATED_TEMPLATE_EXECUTOR_STATUS_KIND,
      generated: true,
      readOnly: true,
      dryRun: true,
      runtimeLocal: true,
      persisted: false,
      runtimeLoaded: false,
      projectJsonModified: false,
      activatedInRuntime: false,
      rendererHandoff: false,
      gameplayTemplateExecution: false,
      saveStateMutated: false,
      accepted: executionEnabled,
      blocked,
      reason,
      executionStatus: executionEnabled ? "enabled-preview-only" : (executorRegistered ? GENERATED_TEMPLATE_EXECUTOR_DEFAULT_MODE : "blocked"),
      handoffStatus: stringValue(rawHandoff.handoffStatus || (handoffAccepted ? "ready" : "blocked")),
      handoffAccepted,
      executorRegistered,
      executionEnabled,
      enabled: executionEnabled,
      noOp: true,
      wouldExecuteIfEnabled: couldExecuteIfEnabled,
      executed: false,
      templateId,
      primaryTemplateId: templateId,
      scenarioId: operation.scenarioId,
      title: stringValue(rawHandoff.title || scenario.title),
      pluginId: stringValue(rawHandoff.pluginId || scenario.pluginId),
      primaryEncounterId: operation.encounterId,
      objectiveTypes: uniqueStrings(rawHandoff.objectiveTypes),
      actorArchetypes: uniqueStrings(rawHandoff.actorArchetypes),
      receiptIds: uniqueStrings(rawHandoff.receiptIds),
      consequenceTypes: uniqueStrings(rawHandoff.consequenceTypes),
      registry: {
        schema: GENERATED_TEMPLATE_EXECUTOR_REGISTRY_SCHEMA,
        kind: GENERATED_TEMPLATE_EXECUTOR_REGISTRY_KIND,
        executionEnabled: registry.executionEnabled === true,
        executorMode: stringValue(registry.executorMode || GENERATED_TEMPLATE_EXECUTOR_DEFAULT_MODE),
        registeredTemplateIds: uniqueStrings(registry.registeredTemplateIds)
      },
      executor: executorRegistered ? {
        templateId: stringValue(executor.templateId),
        registered: true,
        executorMode: stringValue(executor.executorMode || GENERATED_TEMPLATE_EXECUTOR_DEFAULT_MODE),
        executionEnabled: executor.executionEnabled === true,
        noOp: executor.noOp !== false,
        rendererHandoff: false,
        gameplayTemplateExecution: false
      } : null,
      templateInput: clone(templateInput || {}),
      templatePreview,
      previewContract: templatePreview,
      operations: couldExecuteIfEnabled ? [operation] : [],
      problems: reason === "generated-template-execution-enabled-preview-only" ? [] : [reason]
    };
  }

  function generatedScenarioStartCommandGate(catalog, scenarioId, options = {}) {
    const activationOptions = generatedScenarioActivationOptions(options);
    const preview = generatedScenarioStartPreview(catalog, scenarioId, {
      ...objectValue(options),
      source: activationOptions.source
    });
    const knownGeneratedScenario = preview.reason !== "unknown-generated-scenario"
      && preview.reason !== "missing-generated-scenario-id";
    const canAcceptPreviewShell = activationOptions.runtimeActivationEnabled === true
      && preview.canPreviewStart === true;
    const blockedReason = knownGeneratedScenario
      ? "generated-runtime-activation-disabled"
      : preview.reason;
    return {
      schema: GENERATED_SCENARIO_START_COMMAND_SCHEMA,
      kind: GENERATED_SCENARIO_START_COMMAND_KIND,
      id: preview.scenarioId,
      scenarioId: preview.scenarioId,
      generated: true,
      readOnly: true,
      dryRun: true,
      runtimeLoaded: false,
      projectJsonModified: false,
      activatedInRuntime: false,
      knownGeneratedScenario,
      accepted: canAcceptPreviewShell,
      blocked: !canAcceptPreviewShell,
      startable: canAcceptPreviewShell,
      startableInRuntime: false,
      previewShellAccepted: canAcceptPreviewShell,
      commandStatus: canAcceptPreviewShell ? "accepted-preview-shell" : "blocked",
      activationStatus: canAcceptPreviewShell
        ? "preview-shell"
        : (knownGeneratedScenario ? "runtime-activation-disabled" : "unavailable"),
      reason: canAcceptPreviewShell ? "generated-runtime-preview-shell-created" : blockedReason,
      previewReason: preview.reason,
      canPreviewStart: preview.canPreviewStart === true,
      couldStartAfterRuntimeActivation: preview.couldStartAfterRuntimeActivation === true,
      pluginId: preview.pluginId,
      title: preview.title,
      entryPoint: preview.entryPoint,
      plan: preview.plan,
      previewShell: canAcceptPreviewShell ? generatedScenarioPreviewShell(preview, activationOptions) : null,
      preview,
      options: activationOptions,
      problems: uniqueStrings(preview.problems)
    };
  }

  function generatedScenarioStartCommandGates(catalog, options = {}) {
    const normalized = normalizeGeneratedGameplayCatalog(catalog);
    if (!normalized.ready) return [];
    return normalized.scenarios.map(
      (scenario) => generatedScenarioStartCommandGate(normalized, scenario.id, options)
    );
  }

  function generatedScenarioStartPreviews(catalog) {
    const normalized = normalizeGeneratedGameplayCatalog(catalog);
    if (!normalized.ready) return [];
    return normalized.scenarios.map((scenario) => generatedScenarioStartPreview(normalized, scenario.id));
  }

  function normalizeGeneratedGameplayCatalog(value) {
    const raw = objectValue(value);
    const status = stringValue(raw.status || (Object.keys(raw).length ? "rejected" : "absent"));
    const problems = uniqueStrings(raw.problems);
    if (!Object.keys(raw).length) {
      return {
        schema: GENERATED_GAMEPLAY_CATALOG_SCHEMA,
        kind: GENERATED_GAMEPLAY_CATALOG_KIND,
        status: "absent",
        runtimeStatus: GENERATED_GAMEPLAY_CATALOG_RUNTIME_STATUS,
        exported: false,
        ready: false,
        readOnly: true,
        runtimeLoaded: false,
        projectJsonModified: false,
        activatedInRuntime: false,
        enabledPluginIds: [],
        disabledPluginIds: [],
        entryPoints: [],
        scenarioIds: [],
        encounterIds: [],
        receiptIds: [],
        consequenceIds: [],
        documentPaths: [],
        documents: [],
        scenarios: [],
        encounters: [],
        plugins: [],
        problems: []
      };
    }

    const safetyProblems = [];
    if (stringValue(raw.schema) !== GENERATED_GAMEPLAY_CATALOG_SCHEMA) {
      safetyProblems.push("generated gameplay catalog schema mismatch");
    }
    if (stringValue(raw.kind) !== GENERATED_GAMEPLAY_CATALOG_KIND) {
      safetyProblems.push("generated gameplay catalog kind mismatch");
    }
    if (stringValue(raw.runtimeStatus) !== GENERATED_GAMEPLAY_CATALOG_RUNTIME_STATUS) {
      safetyProblems.push("generated gameplay catalog runtime status mismatch");
    }
    if (raw.runtimeLoaded === true) {
      safetyProblems.push("generated gameplay catalog claims runtime loading");
    }
    if (raw.projectJsonModified === true) {
      safetyProblems.push("generated gameplay catalog claims project.json mutation");
    }
    if (raw.activatedInRuntime === true) {
      safetyProblems.push("generated gameplay catalog claims runtime activation");
    }
    const documents = arrayValue(raw.documents).map(generatedGameplayDocument).filter(
      (document) => document.id && ["scenario", "encounter"].includes(document.kind)
    );
    const scenarios = mergeGeneratedGameplayDocuments(raw.scenarios, documents, "scenario");
    const encounters = mergeGeneratedGameplayDocuments(raw.encounters, documents, "encounter");
    const scenarioIds = uniqueStrings(raw.scenarioIds).concat(
      scenarios.map((scenario) => scenario.id).filter(Boolean)
    );
    const encounterIds = uniqueStrings(raw.encounterIds).concat(
      encounters.map((encounter) => encounter.id).filter(Boolean)
    );
    const uniqueScenarioIds = [...new Set(scenarioIds)];
    const uniqueEncounterIds = [...new Set(encounterIds)];
    const entryPoints = uniqueStrings(raw.entryPoints);
    entryPoints.forEach((entryPoint) => {
      if (!uniqueScenarioIds.includes(entryPoint)) {
        safetyProblems.push(`generated gameplay catalog entry point is not a generated scenario: ${entryPoint}`);
      }
    });
    const allProblems = [...new Set(problems.concat(safetyProblems))];
    const ready = status === "ready" && allProblems.length === 0;

    return {
      schema: GENERATED_GAMEPLAY_CATALOG_SCHEMA,
      kind: GENERATED_GAMEPLAY_CATALOG_KIND,
      status,
      runtimeStatus: GENERATED_GAMEPLAY_CATALOG_RUNTIME_STATUS,
      exported: raw.exported === true,
      ready,
      readOnly: true,
      runtimeLoaded: false,
      projectJsonModified: false,
      activatedInRuntime: false,
      enabledPluginIds: uniqueStrings(raw.enabledPluginIds),
      disabledPluginIds: uniqueStrings(raw.disabledPluginIds),
      entryPoints,
      scenarioIds: uniqueScenarioIds,
      encounterIds: uniqueEncounterIds,
      receiptIds: uniqueStrings(raw.receiptIds),
      consequenceIds: uniqueStrings(raw.consequenceIds),
      documentPaths: uniqueStrings(raw.documentPaths),
      documents,
      scenarios,
      encounters,
      plugins: arrayValue(raw.plugins).map(generatedGameplayPluginSummary),
      problems: allProblems
    };
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
        completionCharacterIds: [...new Set(
          arrayValue(scenario.completionCharacterIds)
            .map(stringValue)
            .filter(Boolean)
            .concat(stringValue(scenario.completionCharacterId) ? [stringValue(scenario.completionCharacterId)] : [])
        )],
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
      this.generatedGameplayCatalogData = normalizeGeneratedGameplayCatalog(
        options.generatedGameplayCatalog
      );
      this.generatedScenarioActivationEnabled = options.generatedScenarioActivationEnabled === true
        || options.enableGeneratedScenarioActivation === true;
      this.activeGeneratedGameplayPackIds = normalizeActiveGameplayPackIds(
        options.activeGameplayPackIds || options.activeGeneratedGameplayPackIds
      );
      this.generatedScenarioPreviewShellState = null;
      this.generatedScenarioPreviewEventLog = [];
      this.openingShuttleEncounterBridgeState = null;
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

    setGeneratedGameplayCatalog(value) {
      this.generatedGameplayCatalogData = normalizeGeneratedGameplayCatalog(value);
      const current = this.generatedScenarioPreviewShellState;
      if (current && !this.generatedScenarioDefinition(current.scenarioId)) {
        this.clearGeneratedScenarioPreview("generated-preview-catalog-changed", {
          eventType: "preview-cleared-catalog-changed"
        });
      }
      return this.generatedGameplayCatalog();
    }

    setGeneratedScenarioActivationEnabled(value) {
      this.generatedScenarioActivationEnabled = value === true;
      if (!this.generatedScenarioActivationEnabled && this.generatedScenarioPreviewShellState) {
        this.clearGeneratedScenarioPreview("generated-activation-disabled", {
          eventType: "preview-cleared-activation-disabled"
        });
      }
      return this.generatedScenarioActivationStatus();
    }

    setActiveGameplayPackIds(value) {
      const selection = activeGameplayPackSelection(this.generatedGameplayCatalogData, value);
      this.activeGeneratedGameplayPackIds = selection.activePluginIds.slice();
      return selection;
    }

    activeGameplayPackSelection() {
      return clone(activeGameplayPackSelection(
        this.generatedGameplayCatalogData,
        this.activeGeneratedGameplayPackIds
      ));
    }

    openingShuttleGameplayPackConfig(options = {}) {
      return clone(openingShuttleGameplayPackConfig(
        this.generatedGameplayCatalogData,
        this.activeGeneratedGameplayPackIds,
        options
      ));
    }

    setOpeningShuttleEncounterBridgeSnapshot(value, options = {}) {
      const bridge = openingShuttleEncounterBridgeSnapshot(value, {
        ...objectValue(options),
        sequence: this.state.sequence
      });
      this.openingShuttleEncounterBridgeState = bridge;
      if (objectValue(options).emit !== false) {
        this.emit("opening-shuttle-encounter-bridge-updated", {
          bridge: clone(bridge)
        });
      }
      return clone(bridge);
    }

    currentOpeningShuttleEncounterBridge() {
      return clone(this.openingShuttleEncounterBridgeState);
    }

    openingShuttleEncounterBridgeDiagnostic(options = {}) {
      const rawOptions = objectValue(options);
      const generatedScenarioId = stringValue(rawOptions.generatedScenarioId || rawOptions.scenarioId);
      const generatedStartPreview = objectValue(rawOptions.generatedStartPreview).kind
        ? objectValue(rawOptions.generatedStartPreview)
        : (
          generatedScenarioId
            ? this.generatedScenarioStartPreview(generatedScenarioId, {
              source: stringValue(rawOptions.source || "opening-shuttle-encounter-bridge-diagnostic")
            })
            : null
        );
      return clone(openingShuttleEncounterBridgeDiagnostic(this.openingShuttleEncounterBridgeState, {
        ...rawOptions,
        generatedStartPreview
      }));
    }

    clearOpeningShuttleEncounterBridge(reason = "opening-shuttle-encounter-bridge-cleared", options = {}) {
      const previous = this.openingShuttleEncounterBridgeState;
      this.openingShuttleEncounterBridgeState = null;
      if (objectValue(options).emit !== false) {
        this.emit(stringValue(reason || "opening-shuttle-encounter-bridge-cleared"), {
          previous: clone(previous)
        });
      }
      return {
        cleared: Boolean(previous),
        reason: stringValue(reason || "opening-shuttle-encounter-bridge-cleared"),
        previous: clone(previous)
      };
    }

    currentGeneratedScenarioPreview() {
      return clone(this.generatedScenarioPreviewShellState);
    }

    currentGeneratedScenarioTemplateHandoff(options = {}) {
      return clone(generatedScenarioTemplateHandoff(
        this.generatedScenarioPreviewShellState,
        options
      ));
    }

    generatedTemplateExecutorRegistry(options = {}) {
      return clone(generatedTemplateExecutorRegistry(options));
    }

    currentGeneratedTemplateExecutorStatus(options = {}) {
      const registry = generatedTemplateExecutorRegistry(options);
      const handoff = generatedScenarioTemplateHandoff(
        this.generatedScenarioPreviewShellState,
        options
      );
      return clone(generatedTemplateExecutorStatus(handoff, {
        ...objectValue(options),
        registry
      }));
    }

    generatedScenarioPreviewEvents() {
      return clone(this.generatedScenarioPreviewEventLog);
    }

    recordGeneratedScenarioPreviewEvent(eventType, detail = {}) {
      const event = generatedScenarioPreviewEventRecord(eventType, detail, {
        eventSequence: this.generatedScenarioPreviewEventLog.length + 1,
        runtimeSequence: this.state.sequence,
        activeSystemId: this.state.activeSystemId,
        source: objectValue(detail).source
      });
      this.generatedScenarioPreviewEventLog = [...this.generatedScenarioPreviewEventLog, event].slice(-50);
      return clone(event);
    }

    clearGeneratedScenarioPreviewEvents() {
      const count = this.generatedScenarioPreviewEventLog.length;
      this.generatedScenarioPreviewEventLog = [];
      return {
        schema: GENERATED_SCENARIO_PREVIEW_EVENT_SCHEMA,
        kind: "generated-scenario-preview-event-clear-result",
        generated: true,
        readOnly: true,
        dryRun: true,
        runtimeLocal: true,
        persisted: false,
        accepted: true,
        cleared: count
      };
    }

    clearGeneratedScenarioPreview(reason = "generated-preview-cleared", options = {}) {
      const previous = this.currentGeneratedScenarioPreview();
      this.generatedScenarioPreviewShellState = null;
      const eventType = stringValue(objectValue(options).eventType || "preview-cleared");
      let event = null;
      if (previous) {
        event = this.recordGeneratedScenarioPreviewEvent(eventType, {
          reason: stringValue(reason || "generated-preview-cleared"),
          source: stringValue(objectValue(options).source || "clear-generated-scenario-preview"),
          active: false,
          previous
        });
      }
      return {
        schema: GENERATED_SCENARIO_PREVIEW_SHELL_STATE_SCHEMA,
        kind: "generated-scenario-preview-shell-clear-result",
        generated: true,
        readOnly: true,
        dryRun: true,
        active: false,
        accepted: true,
        cleared: Boolean(previous),
        reason: stringValue(reason || "generated-preview-cleared"),
        previous,
        event
      };
    }

    abortGeneratedScenarioPreview(reason = "generated-preview-aborted") {
      return this.clearGeneratedScenarioPreview(reason || "generated-preview-aborted", {
        eventType: "preview-aborted",
        source: "abort-generated-scenario-preview"
      });
    }

    generatedScenarioActivationStatus() {
      return {
        schema: "game.generatedScenarioActivation.v1",
        kind: "generated-scenario-activation-status",
        enabled: this.generatedScenarioActivationEnabled === true,
        defaultEnabled: false,
        mode: this.generatedScenarioActivationEnabled ? "preview-shell" : "disabled",
        startMode: this.generatedScenarioActivationEnabled ? "preview-shell-only" : "blocked",
        runtimeLoaded: false,
        projectJsonModified: false,
        activatedInRuntime: false,
        rendererHandoff: false,
        gameplayTemplateExecution: false,
        saveStateMutated: false,
        previewShellActive: Boolean(this.generatedScenarioPreviewShellState),
        currentPreviewScenarioId: this.generatedScenarioPreviewShellState
          ? this.generatedScenarioPreviewShellState.scenarioId
          : "",
        previewEventCount: this.generatedScenarioPreviewEventLog.length
      };
    }

    generatedGameplayCatalog() {
      return clone(this.generatedGameplayCatalogData);
    }

    generatedScenarioDefinitions() {
      return clone(this.generatedGameplayCatalogData.scenarios);
    }

    generatedEncounterDefinitions() {
      return clone(this.generatedGameplayCatalogData.encounters);
    }

    generatedScenarioDefinition(scenarioId) {
      const id = stringValue(scenarioId);
      const scenario = this.generatedGameplayCatalogData.scenarios.find(
        (entry) => entry.id === id
      ) || null;
      return clone(scenario);
    }

    generatedEncounterDefinition(encounterId) {
      const id = stringValue(encounterId);
      const encounter = this.generatedGameplayCatalogData.encounters.find(
        (entry) => entry.id === id
      ) || null;
      return clone(encounter);
    }

    generatedScenarioAvailability() {
      return clone(generatedScenarioAvailability(this.generatedGameplayCatalogData));
    }

    generatedScenarioAvailabilityCards() {
      return this.generatedScenarioAvailability().cards;
    }

    generatedScenarioAvailabilityCard(scenarioId) {
      const id = stringValue(scenarioId);
      return this.generatedScenarioAvailabilityCards().find(
        (card) => card.scenarioId === id
      ) || null;
    }

    generatedScenarioStartPreview(scenarioId, options = {}) {
      return clone(generatedScenarioStartPreview(
        this.generatedGameplayCatalogData,
        scenarioId,
        options
      ));
    }

    dryRunStartGeneratedScenario(scenarioId, options = {}) {
      return this.generatedScenarioStartPreview(scenarioId, {
        ...objectValue(options),
        source: stringValue(objectValue(options).source || "dry-run-start-generated-scenario")
      });
    }

    startGeneratedScenario(scenarioId, options = {}) {
      const command = generatedScenarioStartCommandGate(
        this.generatedGameplayCatalogData,
        scenarioId,
        {
          ...objectValue(options),
          runtimeActivationEnabled: this.generatedScenarioActivationEnabled === true
        }
      );
      if (command.accepted === true) {
        this.generatedScenarioPreviewShellState = generatedScenarioPreviewShellState(command, {
          source: command.options?.source || "start-generated-scenario",
          sequence: this.state.sequence,
          activeSystemId: this.state.activeSystemId
        });
        this.recordGeneratedScenarioPreviewEvent("preview-created", {
          reason: command.reason,
          source: command.options?.source || "start-generated-scenario",
          active: true,
          current: this.generatedScenarioPreviewShellState
        });
      }
      return clone(command);
    }

    generatedScenarioStartCommands() {
      return clone(generatedScenarioStartCommandGates(this.generatedGameplayCatalogData, {
        runtimeActivationEnabled: this.generatedScenarioActivationEnabled === true
      }));
    }

    generatedScenarioStartPreviews() {
      return clone(generatedScenarioStartPreviews(this.generatedGameplayCatalogData));
    }

    generatedGameplaySummary() {
      const catalog = this.generatedGameplayCatalogData;
      return {
        schema: catalog.schema,
        kind: catalog.kind,
        status: catalog.status,
        runtimeStatus: catalog.runtimeStatus,
        ready: catalog.ready,
        exported: catalog.exported,
        readOnly: true,
        runtimeLoaded: false,
        projectJsonModified: false,
        activatedInRuntime: false,
        enabledPluginIds: catalog.enabledPluginIds.slice(),
        entryPoints: catalog.entryPoints.slice(),
        scenarioCount: catalog.scenarios.length,
        encounterCount: catalog.encounters.length,
        scenarioIds: catalog.scenarioIds.slice(),
        encounterIds: catalog.encounterIds.slice(),
        problems: catalog.problems.slice()
      };
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

    resetProtectionEncounter(scenarioId, options = {}) {
      const definition = this.scenarioDefinition(scenarioId);
      const state = this.state.scenarios[stringValue(scenarioId)];
      if (!definition || !state) throw new SystemScenarioStateError("Unknown scenario.");
      if (state.status === "resolved") {
        return {
          reset: false,
          reason: "scenario-resolved",
          view: this.view(definition.id)
        };
      }
      state.status = "active";
      state.stageId = definition.protectionStageId || definition.startStageId;
      state.evidenceIds = [];
      state.resolutionId = "";
      state.consequences = {};
      state.metrics = {
        weaponDischarges: 0,
        defensiveDischarges: 0,
        intimidationDischarges: 0
      };
      const receipt = this.record(
        definition.id,
        "protection-encounter-reset",
        {
          stageId: state.stageId,
          evidenceIds: [],
          metrics: clone(state.metrics),
          source: stringValue(options.source || "manual")
        },
        options.nowMs
      );
      return {
        reset: true,
        receipt,
        view: this.view(definition.id)
      };
    }

    syncCharacterRuntime(scenarioId, characterRuntime, options = {}) {
      const definition = this.scenarioDefinition(scenarioId);
      const state = this.state.scenarios[stringValue(scenarioId)];
      if (!definition || !state) throw new SystemScenarioStateError("Unknown scenario.");
      const completionCharacterIds = arrayValue(definition.completionCharacterIds);
      if (state.status !== "active"
          || state.stageId !== definition.protectionStageId
          || !completionCharacterIds.length) {
        return {changed: false, view: this.view(definition.id)};
      }
      const activeCompletionCharacters = completionCharacterIds.filter((characterId) => {
        const character = characterRuntime?.character?.(characterId);
        return character && character.status === "active" && character.health > 0;
      });
      if (activeCompletionCharacters.length) {
        return {
          changed: false,
          remainingCharacterIds: activeCompletionCharacters,
          view: this.view(definition.id)
        };
      }
      state.stageId = definition.investigationStageId;
      definition.evidence.forEach((item) => {
        if (!state.evidenceIds.includes(item.id)) state.evidenceIds.push(item.id);
      });
      const receipt = this.record(
        definition.id,
        "protection-completed",
        {
          characterId: definition.completionCharacterId,
          characterIds: completionCharacterIds.slice(),
          evidenceIds: state.evidenceIds.slice(),
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
          && arrayValue(definition.completionCharacterIds).includes(targetId)
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
        generatedGameplayCatalog: this.generatedGameplaySummary(),
        activeGameplayPackSelection: this.activeGameplayPackSelection(),
        openingShuttleGameplayPackConfig: this.openingShuttleGameplayPackConfig(),
        generatedScenarioActivation: this.generatedScenarioActivationStatus(),
        generatedScenarioAvailability: this.generatedScenarioAvailability(),
        generatedScenarioStartPreviews: this.generatedScenarioStartPreviews(),
        generatedScenarioStartCommands: this.generatedScenarioStartCommands(),
        currentGeneratedScenarioPreview: this.currentGeneratedScenarioPreview(),
        currentGeneratedScenarioTemplateHandoff: this.currentGeneratedScenarioTemplateHandoff(),
        currentGeneratedTemplateExecutorStatus: this.currentGeneratedTemplateExecutorStatus(),
        generatedScenarioPreviewEvents: this.generatedScenarioPreviewEvents(),
        openingShuttleEncounterBridge: this.currentOpeningShuttleEncounterBridge(),
        openingShuttleEncounterBridgeDiagnostic: this.openingShuttleEncounterBridgeDiagnostic(),
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
      if (Object.prototype.hasOwnProperty.call(options, "generatedGameplayCatalog")) {
        currentRuntime.setGeneratedGameplayCatalog(options.generatedGameplayCatalog);
      }
      if (
        Object.prototype.hasOwnProperty.call(options, "activeGameplayPackIds")
        || Object.prototype.hasOwnProperty.call(options, "activeGeneratedGameplayPackIds")
      ) {
        currentRuntime.setActiveGameplayPackIds(
          options.activeGameplayPackIds || options.activeGeneratedGameplayPackIds
        );
      }
      if (
        Object.prototype.hasOwnProperty.call(options, "generatedScenarioActivationEnabled")
        || Object.prototype.hasOwnProperty.call(options, "enableGeneratedScenarioActivation")
      ) {
        currentRuntime.setGeneratedScenarioActivationEnabled(
          options.generatedScenarioActivationEnabled === true
            || options.enableGeneratedScenarioActivation === true
        );
      }
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
    GENERATED_GAMEPLAY_CATALOG_SCHEMA,
    GENERATED_GAMEPLAY_CATALOG_KIND,
    GENERATED_GAMEPLAY_CATALOG_RUNTIME_STATUS,
    GENERATED_SCENARIO_AVAILABILITY_SCHEMA,
    GENERATED_SCENARIO_AVAILABILITY_KIND,
    GENERATED_SCENARIO_START_PREVIEW_SCHEMA,
    GENERATED_SCENARIO_START_PREVIEW_KIND,
    GENERATED_SCENARIO_START_COMMAND_SCHEMA,
    GENERATED_SCENARIO_START_COMMAND_KIND,
    GENERATED_SCENARIO_PREVIEW_SHELL_STATE_SCHEMA,
    GENERATED_SCENARIO_PREVIEW_SHELL_STATE_KIND,
    GENERATED_SCENARIO_PREVIEW_EVENT_SCHEMA,
    GENERATED_SCENARIO_PREVIEW_EVENT_KIND,
    GENERATED_SCENARIO_TEMPLATE_HANDOFF_SCHEMA,
    GENERATED_SCENARIO_TEMPLATE_HANDOFF_KIND,
    GENERATED_SCENARIO_TEMPLATE_HANDOFF_SUPPORTED_TEMPLATES,
    GENERATED_TEMPLATE_EXECUTOR_REGISTRY_SCHEMA,
    GENERATED_TEMPLATE_EXECUTOR_REGISTRY_KIND,
    GENERATED_TEMPLATE_EXECUTOR_STATUS_SCHEMA,
    GENERATED_TEMPLATE_EXECUTOR_STATUS_KIND,
    GENERATED_TEMPLATE_EXECUTOR_DEFAULT_MODE,
    OPENING_SHUTTLE_ENCOUNTER_BRIDGE_SCHEMA,
    OPENING_SHUTTLE_ENCOUNTER_BRIDGE_KIND,
    OPENING_SHUTTLE_ENCOUNTER_BRIDGE_DIAGNOSTIC_SCHEMA,
    OPENING_SHUTTLE_ENCOUNTER_BRIDGE_DIAGNOSTIC_KIND,
    ACTIVE_GAMEPLAY_PACK_SELECTION_SCHEMA,
    ACTIVE_GAMEPLAY_PACK_SELECTION_KIND,
    OPENING_SHUTTLE_GAMEPLAY_PACK_CONFIG_SCHEMA,
    OPENING_SHUTTLE_GAMEPLAY_PACK_CONFIG_KIND,
    STORAGE_PREFIX,
    SystemScenarioDefinitionError,
    SystemScenarioStateError,
    normalizeDefinition,
    validateDefinition,
    normalizeGeneratedGameplayCatalog,
    normalizeActiveGameplayPackIds,
    activeGameplayPackSelection,
    openingShuttleGameplayPackConfig,
    generatedScenarioAvailability,
    generatedScenarioStartPreview,
    generatedScenarioStartPreviews,
    generatedScenarioStartCommandGate,
    generatedScenarioStartCommandGates,
    generatedScenarioPreviewShellState,
    generatedScenarioPreviewEventRecord,
    generatedScenarioTemplateHandoff,
    generatedTemplateExecutorRegistry,
    generatedTemplateExecutorStatus,
    openingShuttleEncounterBridgeSnapshot,
    openingShuttleEncounterBridgeDiagnostic,
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
