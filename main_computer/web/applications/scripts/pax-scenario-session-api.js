(function (global) {
  "use strict";

  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);

  if (!PaxValueUtils?.objectValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax scenario session API.");
  }

  const {
    objectValue,
    arrayValue
  } = PaxValueUtils;

  function functionValue(value, name) {
    if (typeof value !== "function") {
      throw new Error(`MainComputerPaxScenarioSessionApi requires ${name}.`);
    }
    return value;
  }

  function createPaxScenarioSessionApi(options = {}) {
    const config = options.config || null;
    if (!config?.ids?.scenarioId) {
      throw new Error("MainComputerPaxScenarioSessionApi requires Pax scenario config.");
    }

    const diagnosticsInput = objectValue(options.diagnostics);
    const commandsInput = objectValue(options.commands);
    const presentationInput = objectValue(options.presentation);
    const presentationModelInput = objectValue(presentationInput.model);
    const presentationDomInput = objectValue(presentationInput.dom);
    const runtimeInput = objectValue(options.runtime);

    const ids = objectValue(config.ids);
    const stages = objectValue(config.stages);
    const encounter = objectValue(config.encounter);
    const recovery = objectValue(config.recovery);
    const actors = objectValue(config.actors);

    const constants = Object.freeze({
      config,
      scenarioId: ids.scenarioId,
      systemId: ids.systemId,
      protectionStageId: stages.protection,
      investigationStageId: stages.investigation,
      conferenceStageId: stages.conference,
      hardKickoffStageIds: Object.freeze(arrayValue(stages.hardKickoff).slice()),
      encounterDefinitionId: encounter.definitionId,
      encounterKey: encounter.key,
      encounterSnapshotVersion: encounter.snapshotVersion,
      encounterInstanceSource: encounter.diagnosticInstanceSource,
      activeStageIds: Object.freeze(arrayValue(encounter.activeStageIds).slice()),
      completedStageIds: Object.freeze(arrayValue(encounter.completedStageIds).slice()),
      reconciliationModes: recovery.reconciliationModes,
      reconciliationReasons: recovery.reconciliationReasons,
      reconciliationPolicy: recovery.reconciliationPolicy,
      recoveryActions: recovery.actions,
      protectionStates: recovery.states,
      boarderGroupStatus: recovery.actorGroupStatus,
      boarderIds: Object.freeze(arrayValue(actors.boarderIds).slice()),
      hardKickoffPositions: actors.hardKickoffPositions,
      boarderPositions: actors.boarderPositions
    });

    const diagnostics = Object.freeze({
      snapshot: functionValue(diagnosticsInput.snapshot, "diagnostics.snapshot"),
      diagnose: functionValue(diagnosticsInput.diagnose, "diagnostics.diagnose"),
      state: functionValue(diagnosticsInput.state, "diagnostics.state"),
      characterRows: functionValue(diagnosticsInput.characterRows, "diagnostics.characterRows"),
      encounterIdentity: functionValue(
        diagnosticsInput.encounterIdentity,
        "diagnostics.encounterIdentity"
      ),
      encounterInstanceDescriptor: functionValue(
        diagnosticsInput.encounterInstanceDescriptor,
        "diagnostics.encounterInstanceDescriptor"
      )
    });

    const commands = Object.freeze({
      startOrRecover: functionValue(commandsInput.startOrRecover, "commands.startOrRecover"),
      requestReconciliation: functionValue(
        commandsInput.requestReconciliation,
        "commands.requestReconciliation"
      ),
      resetProtectionEncounter: functionValue(
        commandsInput.resetProtectionEncounter,
        "commands.resetProtectionEncounter"
      ),
      forceCharacterStates: functionValue(
        commandsInput.forceCharacterStates,
        "commands.forceCharacterStates"
      )
    });

    const presentationModel = Object.freeze({
      viewModel: functionValue(presentationModelInput.viewModel, "presentation.model.viewModel"),
      presentationViewModel: functionValue(
        presentationModelInput.presentationViewModel || presentationModelInput.viewModel,
        "presentation.model.presentationViewModel"
      ),
      objective: functionValue(presentationModelInput.objective, "presentation.model.objective"),
      objectivePresentation: functionValue(
        presentationModelInput.objectivePresentation || presentationModelInput.objective,
        "presentation.model.objectivePresentation"
      ),
      threat: functionValue(presentationModelInput.threat, "presentation.model.threat"),
      threatPresentation: functionValue(
        presentationModelInput.threatPresentation || presentationModelInput.threat,
        "presentation.model.threatPresentation"
      ),
      hardStart: functionValue(presentationModelInput.hardStart, "presentation.model.hardStart"),
      hardStartPresentation: functionValue(
        presentationModelInput.hardStartPresentation || presentationModelInput.hardStart,
        "presentation.model.hardStartPresentation"
      ),
      requirementText: functionValue(
        presentationModelInput.requirementText,
        "presentation.model.requirementText"
      ),
      stageStatus: functionValue(
        presentationModelInput.stageStatus,
        "presentation.model.stageStatus"
      ),
      characterRows: functionValue(
        presentationModelInput.characterRows,
        "presentation.model.characterRows"
      )
    });

    const presentationDom = Object.freeze({
      nodes: functionValue(presentationDomInput.nodes, "presentation.dom.nodes"),
      hideScenarioChrome: functionValue(
        presentationDomInput.hideScenarioChrome,
        "presentation.dom.hideScenarioChrome"
      ),
      renderPresentation: functionValue(
        presentationDomInput.renderPresentation,
        "presentation.dom.renderPresentation"
      ),
      renderMissionCues: functionValue(
        presentationDomInput.renderMissionCues,
        "presentation.dom.renderMissionCues"
      ),
      renderThreatTracker: functionValue(
        presentationDomInput.renderThreatTracker,
        "presentation.dom.renderThreatTracker"
      ),
      renderHardStart: functionValue(
        presentationDomInput.renderHardStart,
        "presentation.dom.renderHardStart"
      ),
      renderCharacters: functionValue(
        presentationDomInput.renderCharacters,
        "presentation.dom.renderCharacters"
      ),
      renderEvidence: functionValue(
        presentationDomInput.renderEvidence,
        "presentation.dom.renderEvidence"
      ),
      renderResolutions: functionValue(
        presentationDomInput.renderResolutions,
        "presentation.dom.renderResolutions"
      ),
      renderOutcome: functionValue(
        presentationDomInput.renderOutcome,
        "presentation.dom.renderOutcome"
      )
    });

    const presentation = Object.freeze({
      model: presentationModel,
      dom: presentationDom,
      viewModel: presentationModel.viewModel,
      presentationViewModel: presentationModel.presentationViewModel,
      objective: presentationModel.objective,
      objectivePresentation: presentationModel.objectivePresentation,
      threat: presentationModel.threat,
      threatPresentation: presentationModel.threatPresentation,
      hardStart: presentationModel.hardStart,
      hardStartPresentation: presentationModel.hardStartPresentation,
      requirementText: presentationModel.requirementText,
      renderMissionCues: presentationDom.renderMissionCues,
      renderThreatTracker: presentationDom.renderThreatTracker,
      renderHardStart: presentationDom.renderHardStart
    });

    const runtime = Object.freeze({
      setRuntime: functionValue(runtimeInput.setRuntime, "runtime.setRuntime"),
      setCharacterRuntime: functionValue(
        runtimeInput.setCharacterRuntime,
        "runtime.setCharacterRuntime"
      ),
      handleNavigation: functionValue(runtimeInput.handleNavigation, "runtime.handleNavigation"),
      setWorldSnapshot: functionValue(runtimeInput.setWorldSnapshot, "runtime.setWorldSnapshot"),
      bind: functionValue(runtimeInput.bind, "runtime.bind"),
      render: functionValue(runtimeInput.render, "runtime.render")
    });

    return Object.freeze({
      SCENARIO_ID: constants.scenarioId,
      PAX_SYSTEM_ID: constants.systemId,
      PAX_PROTECTION_ENCOUNTER_DEFINITION_ID: constants.encounterDefinitionId,
      PAX_PROTECTION_ENCOUNTER_KEY: constants.encounterKey,
      PAX_PROTECTION_ENCOUNTER_SNAPSHOT_VERSION: constants.encounterSnapshotVersion,
      constants,
      config,
      diagnostics,
      commands,
      presentation,
      runtime,
      paxProtectionEncounterSnapshot: diagnostics.snapshot,
      diagnosePaxProtectionEncounter: diagnostics.diagnose,
      setRuntime: runtime.setRuntime,
      setCharacterRuntime: runtime.setCharacterRuntime,
      handleNavigation: runtime.handleNavigation,
      setWorldSnapshot: runtime.setWorldSnapshot,
      bind: runtime.bind,
      hardKickoffPositions: constants.hardKickoffPositions,
      boarderIds: constants.boarderIds
    });
  }

  const api = Object.freeze({
    create: createPaxScenarioSessionApi,
    createPaxScenarioSessionApi
  });

  global.MainComputerPaxScenarioSessionApi = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
