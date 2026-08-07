(function (global) {
  "use strict";

  const SCENARIO_ID = "scenario.pax.neutrality-under-fire";
  const PAX_SYSTEM_ID = "system.pax";
  const PROTECTION_STAGE_ID = "protect-witness";
  const INVESTIGATION_STAGE_ID = "investigation";
  const CONFERENCE_STAGE_ID = "conference";

  const PAX_PROTECTION_RECOVERY = Object.freeze({
    none: "none",
    reviveBoarders: "revive-boarders",
    restartEncounter: "restart-encounter"
  });

  const PAX_BOARDER_GROUP_STATUS = Object.freeze({
    unavailable: "unavailable",
    missing: "missing",
    active: "active",
    defeated: "defeated",
    mixed: "mixed"
  });

  const PAX_PROTECTION_STATE = Object.freeze({
    unavailable: "unavailable",
    scenarioInactive: "scenario-inactive",
    characterRuntimeUnavailable: "character-runtime-unavailable",
    consistentActive: "consistent-active",
    recoverableProtectionDefeated: "recoverable-protection-defeated",
    invalidProtectionBoarders: "invalid-protection-boarders",
    recoverableInvestigationActive: "recoverable-investigation-active",
    recoverableInvestigationDefeated: "recoverable-investigation-defeated",
    invalidInvestigationBoarders: "invalid-investigation-boarders",
    outsideProtection: "outside-protection"
  });

  const PAX_PROTECTION_STATE_LABELS = Object.freeze({
    unavailable: PAX_PROTECTION_STATE.unavailable,
    scenarioInactive: PAX_PROTECTION_STATE.scenarioInactive,
    actorRuntimeUnavailable: PAX_PROTECTION_STATE.characterRuntimeUnavailable,
    consistentActive: PAX_PROTECTION_STATE.consistentActive,
    recoverableActiveDefeated: PAX_PROTECTION_STATE.recoverableProtectionDefeated,
    invalidActiveActors: PAX_PROTECTION_STATE.invalidProtectionBoarders,
    recoverableCompletedActive: PAX_PROTECTION_STATE.recoverableInvestigationActive,
    recoverableCompletedDefeated: PAX_PROTECTION_STATE.recoverableInvestigationDefeated,
    invalidCompletedActors: PAX_PROTECTION_STATE.invalidInvestigationBoarders,
    outsideActiveStage: PAX_PROTECTION_STATE.outsideProtection
  });

  const PAX_PROTECTION_RECOVERY_LABELS = Object.freeze({
    none: PAX_PROTECTION_RECOVERY.none,
    reviveActors: PAX_PROTECTION_RECOVERY.reviveBoarders,
    restartEncounter: PAX_PROTECTION_RECOVERY.restartEncounter
  });

  const HARD_KICKOFF_STAGE_IDS = Object.freeze([
    PROTECTION_STAGE_ID,
    INVESTIGATION_STAGE_ID,
    CONFERENCE_STAGE_ID
  ]);

  const BOARDER_IDS = Object.freeze([
    "enemy.pax.quiet-service-assassin-01",
    "enemy.pax.boarder-01",
    "enemy.pax.boarder-02",
    "enemy.pax.boarder-03",
    "enemy.pax.boarder-04",
    "enemy.pax.boarder-05"
  ]);

  const HARD_KICKOFF_POSITIONS = Object.freeze({
    assassin: Object.freeze([-0.35, -0.55, -37.65]),
    boarder01: Object.freeze([-3.2, -0.55, -35.8]),
    boarder02: Object.freeze([3.2, -0.55, -35.8]),
    boarder03: Object.freeze([-2.4, -0.55, -39.0]),
    boarder04: Object.freeze([2.4, -0.55, -39.0]),
    boarder05: Object.freeze([0.0, -0.55, -40.5]),
    witness: Object.freeze([-1.45, -0.55, -36.45]),
    marshal: Object.freeze([1.45, -0.55, -36.35])
  });

  const BOARDER_POSITIONS = Object.freeze([
    HARD_KICKOFF_POSITIONS.assassin,
    HARD_KICKOFF_POSITIONS.boarder01,
    HARD_KICKOFF_POSITIONS.boarder02,
    HARD_KICKOFF_POSITIONS.boarder03,
    HARD_KICKOFF_POSITIONS.boarder04,
    HARD_KICKOFF_POSITIONS.boarder05
  ]);

  function currentEncounterState(encounterState = null) {
    return encounterState
      || global.MainComputerEncounterState
      || (typeof require === "function" ? require("./encounter-state.js") : null);
  }

  function createEncounterAdapter(encounterState = null) {
    const runtime = currentEncounterState(encounterState);
    if (!runtime?.createStageActorEncounterAdapter) {
      throw new Error("MainComputerEncounterState is required before Pax protection encounter setup.");
    }
    return runtime.createStageActorEncounterAdapter({
      scenarioId: SCENARIO_ID,
      systemId: PAX_SYSTEM_ID,
      activeStageId: PROTECTION_STAGE_ID,
      completedStageIds: [INVESTIGATION_STAGE_ID],
      actorIds: BOARDER_IDS,
      actorGroupStatusLabels: PAX_BOARDER_GROUP_STATUS,
      stateLabels: PAX_PROTECTION_STATE_LABELS,
      recoveryLabels: PAX_PROTECTION_RECOVERY_LABELS
    });
  }

  const api = Object.freeze({
    SCENARIO_ID,
    PAX_SYSTEM_ID,
    PROTECTION_STAGE_ID,
    INVESTIGATION_STAGE_ID,
    CONFERENCE_STAGE_ID,
    PAX_PROTECTION_RECOVERY,
    PAX_BOARDER_GROUP_STATUS,
    PAX_PROTECTION_STATE,
    PAX_PROTECTION_STATE_LABELS,
    PAX_PROTECTION_RECOVERY_LABELS,
    HARD_KICKOFF_STAGE_IDS,
    BOARDER_IDS,
    HARD_KICKOFF_POSITIONS,
    BOARDER_POSITIONS,
    createEncounterAdapter
  });

  global.MainComputerPaxProtectionEncounter = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
