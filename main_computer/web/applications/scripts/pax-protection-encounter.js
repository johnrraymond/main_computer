(function (global) {
  "use strict";

  const EncounterState = global.MainComputerEncounterState
    || (typeof require === "function" ? require("./encounter-state.js") : null);
  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);

  const config = PaxScenarioConfig?.config || PaxScenarioConfig?.PAX_SCENARIO_CONFIG || null;

  if (!config?.ids?.scenarioId) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax protection encounter compatibility API.");
  }

  function currentEncounterState(encounterState = null) {
    return encounterState
      || global.MainComputerEncounterState
      || EncounterState
      || null;
  }

  function paxProtectionStateLabels() {
    return Object.freeze({
      unavailable: config.recovery.states.unavailable,
      scenarioInactive: config.recovery.states.scenarioInactive,
      actorRuntimeUnavailable: config.recovery.states.characterRuntimeUnavailable,
      consistentActive: config.recovery.states.consistentActive,
      recoverableActiveDefeated: config.recovery.states.recoverableProtectionDefeated,
      invalidActiveActors: config.recovery.states.invalidProtectionBoarders,
      recoverableCompletedActive: config.recovery.states.recoverableInvestigationActive,
      recoverableCompletedDefeated: config.recovery.states.recoverableInvestigationDefeated,
      invalidCompletedActors: config.recovery.states.invalidInvestigationBoarders,
      outsideActiveStage: config.recovery.states.outsideProtection
    });
  }

  function paxProtectionRecoveryLabels() {
    return Object.freeze({
      none: config.recovery.actions.none,
      reviveActors: config.recovery.actions.reviveBoarders,
      restartEncounter: config.recovery.actions.restartEncounter
    });
  }

  function createEncounterAdapter(encounterState = null) {
    const runtime = currentEncounterState(encounterState);
    if (!runtime?.classifyActorGroup || !runtime?.classifyStagedEncounterState) {
      throw new Error("MainComputerEncounterState is required before Pax protection encounter setup.");
    }
    const stateLabels = paxProtectionStateLabels();
    const recoveryLabels = paxProtectionRecoveryLabels();

    function classifyActorGroup(options = {}) {
      return runtime.classifyActorGroup(Object.assign({}, options, {
        actorIds: options.actorIds || config.actors.boarderIds,
        entriesKey: options.entriesKey || "boarders"
      }));
    }

    function classifyStagedEncounterState(options = {}) {
      return runtime.classifyStagedEncounterState(Object.assign({}, options, {
        scenarioId: options.scenarioId || config.ids.scenarioId,
        systemId: options.systemId || config.ids.systemId,
        actorIds: options.actorIds || config.actors.boarderIds,
        activeStageIds: options.activeStageIds || config.encounter.activeStageIds,
        completedStageIds: options.completedStageIds || config.encounter.completedStageIds,
        entriesKey: options.entriesKey || "boarders",
        stateLabels,
        recoveryActions: recoveryLabels
      }));
    }

    function reconciliationPlan(classification, options = {}) {
      return runtime.reconciliationPlan(classification, Object.assign({}, options, {
        recoveryActions: recoveryLabels
      }));
    }

    function recoverySucceeded(plan, result, options = {}) {
      return runtime.recoverySucceeded(plan, result, Object.assign({}, options, {
        successKeys: Object.assign(
          {
            [recoveryLabels.reviveActors]: "forced",
            [recoveryLabels.restartEncounter]: "reset"
          },
          options.successKeys || {}
        )
      }));
    }

    function diagnosticSnapshot(classification, plan = null, options = {}) {
      return runtime.diagnosticSnapshot(classification, plan, Object.assign({}, options, {
        identity: Object.assign(
          {
            key: config.encounter.key,
            definitionId: config.encounter.definitionId,
            scenarioId: config.ids.scenarioId,
            systemId: config.ids.systemId,
            activeStageIds: config.encounter.activeStageIds,
            completedStageIds: config.encounter.completedStageIds,
            actorIds: config.actors.boarderIds
          },
          options.identity || {}
        ),
        proposedInstanceKey: options.proposedInstanceKey || [
          config.encounter.key,
          "instance",
          config.stages.protection,
          "pending"
        ].join(":"),
        instanceSource: options.instanceSource || config.encounter.diagnosticInstanceSource
      }));
    }

    return Object.freeze({
      scenarioId: config.ids.scenarioId,
      systemId: config.ids.systemId,
      activeStageId: config.stages.protection,
      completedStageIds: config.encounter.completedStageIds,
      actorIds: config.actors.boarderIds,
      actorGroupStatusLabels: config.recovery.actorGroupStatus,
      stateLabels,
      recoveryLabels,
      classifyActorGroup,
      classifyStagedEncounterState,
      reconciliationPlan,
      recoverySucceeded,
      diagnosticSnapshot
    });
  }

  /*
   * Compatibility shim for older tests/tools that import
   * MainComputerPaxProtectionEncounter directly. Browser gameplay now uses the
   * split config/model/commands/reconciliation modules instead of this file.
   */
  const api = Object.freeze({
    SCENARIO_ID: config.ids.scenarioId,
    PAX_SYSTEM_ID: config.ids.systemId,
    PROTECTION_STAGE_ID: config.stages.protection,
    INVESTIGATION_STAGE_ID: config.stages.investigation,
    CONFERENCE_STAGE_ID: config.stages.conference,
    PAX_PROTECTION_RECOVERY: config.recovery.actions,
    PAX_BOARDER_GROUP_STATUS: config.recovery.actorGroupStatus,
    PAX_PROTECTION_STATE: config.recovery.states,
    PAX_PROTECTION_STATE_LABELS: paxProtectionStateLabels(),
    PAX_PROTECTION_RECOVERY_LABELS: paxProtectionRecoveryLabels(),
    HARD_KICKOFF_STAGE_IDS: config.stages.hardKickoff,
    BOARDER_IDS: config.actors.boarderIds,
    HARD_KICKOFF_POSITIONS: config.actors.hardKickoffPositions,
    BOARDER_POSITIONS: config.actors.boarderPositions,
    config,
    createEncounterAdapter
  });

  global.MainComputerPaxProtectionEncounter = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
