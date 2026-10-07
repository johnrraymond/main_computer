(function (global) {
  "use strict";

  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);
  const PaxProtectionEncounterModel = global.MainComputerPaxProtectionEncounterModel
    || (typeof require === "function" ? require("./pax-protection-encounter-model.js") : null);
  const PaxProtectionEncounterCommands = global.MainComputerPaxProtectionEncounterCommands
    || (typeof require === "function" ? require("./pax-protection-encounter-commands.js") : null);
  const PaxProtectionReconciliation = global.MainComputerPaxProtectionReconciliation
    || (typeof require === "function" ? require("./pax-protection-reconciliation.js") : null);
  const DEFAULT_CONFIG = PaxScenarioConfig?.config || PaxScenarioConfig?.PAX_SCENARIO_CONFIG || null;

  if (!DEFAULT_CONFIG?.ids?.scenarioId) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax protection encounter controller.");
  }
  if (!PaxProtectionEncounterModel?.create) {
    throw new Error("MainComputerPaxProtectionEncounterModel must load before Pax protection encounter controller.");
  }
  if (!PaxProtectionEncounterCommands?.create) {
    throw new Error("MainComputerPaxProtectionEncounterCommands must load before Pax protection encounter controller.");
  }
  if (!PaxProtectionReconciliation?.create) {
    throw new Error("MainComputerPaxProtectionReconciliation must load before Pax protection encounter controller.");
  }

  function createPaxProtectionEncounterController(options = {}) {
    const config = options.config || DEFAULT_CONFIG;
    const state = options.state || {};

    function currentRuntime() {
      return options.currentRuntime?.()
        || state.runtime
        || global.MainComputerSystemScenarioRuntime?.current?.()
        || null;
    }

    function currentCharacterRuntime() {
      return options.currentCharacterRuntime?.()
        || state.characterRuntime
        || global.MainComputerCharacterAIRuntime?.current?.()
        || null;
    }

    const model = PaxProtectionEncounterModel.create({
      config,
      state,
      currentRuntime,
      currentCharacterRuntime
    });

    const rawCommands = PaxProtectionEncounterCommands.create({
      config,
      state,
      currentRuntime,
      currentCharacterRuntime,
      nowMs: options.nowMs,
      activeShuttleRenderer: options.activeShuttleRenderer,
      revealArrivalPanel: options.revealArrivalPanel,
      render: options.render
    });

    const rawReconciliation = PaxProtectionReconciliation.create({
      config,
      state,
      encounterModel: model,
      commands: rawCommands,
      nowMs: options.nowMs
    });

    const commands = Object.freeze({
      syncProtection: rawCommands.syncProtection,
      startOrRecover: rawCommands.startOrRecoverProtectionEncounter,
      resetProtectionEncounter: rawCommands.resetProtectionEncounter,
      forceCharacterStates: rawCommands.forceProtectionEncounterCharacters,
      handleNavigation: rawCommands.handleNavigation,
      setWorldSnapshot: rawCommands.setWorldSnapshot,
      isDurableCommittedArrival: rawCommands.isDurableCommittedArrival,
      isLegacyPaxOccupancy: rawCommands.isLegacyPaxOccupancy,
      legacyActivationKey: rawCommands.legacyActivationKey,
      arrivalActivationKey: rawCommands.arrivalActivationKey,
      cameraRelativeBoardingPositions: rawCommands.cameraRelativeBoardingPositions
    });

    const diagnostics = Object.freeze({
      stateLabels: model.paxProtectionStateLabels,
      recoveryActions: model.paxProtectionRecoveryActions,
      encounterIdentity: model.paxProtectionEncounterIdentity,
      encounterInstanceDescriptor: model.paxProtectionEncounterInstanceDescriptor,
      classifyActorGroup: model.classifyBoarderGroup,
      classifyEncounterState: model.classifyPaxProtectionState,
      snapshotData: model.paxProtectionEncounterSnapshotData,
      buildSnapshot: model.buildPaxProtectionEncounterSnapshot,
      snapshot: model.paxProtectionEncounterSnapshot,
      diagnose: model.diagnosePaxProtectionEncounter
    });

    const reconciliation = Object.freeze({
      options: model.paxProtectionReconciliationOptions,
      plan: model.paxProtectionReconciliationPlan,
      recoverySucceeded: model.paxProtectionRecoverySucceeded,
      performRecovery: rawReconciliation.performRecovery,
      reconcile: rawReconciliation.reconcile,
      request: rawReconciliation.request
    });

    return Object.freeze({
      commands,
      diagnostics,
      reconciliation,
      model
    });
  }

  const api = Object.freeze({
    create: createPaxProtectionEncounterController,
    createPaxProtectionEncounterController
  });

  global.MainComputerPaxProtectionEncounterController = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
