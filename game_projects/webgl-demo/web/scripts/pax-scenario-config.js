(function (global) {
  "use strict";

  const EncounterState = global.MainComputerEncounterState
    || (typeof require === "function" ? require("./encounter-state.js") : null);
  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);

  if (!EncounterState?.ACTOR_GROUP_STATUS) {
    throw new Error("MainComputerEncounterState must load before Pax scenario config.");
  }
  if (!PaxValueUtils?.freezeVector3) {
    throw new Error("MainComputerPaxValueUtils must load before Pax scenario config.");
  }

  const {freezeVector3} = PaxValueUtils;

  const PAX_SCENARIO_CONFIG = (() => {
    const ids = Object.freeze({
      scenarioId: "scenario.pax.neutrality-under-fire",
      systemId: "system.pax"
    });
    const stages = Object.freeze({
      protection: "protect-witness",
      investigation: "investigation",
      conference: "conference",
      hardKickoff: Object.freeze([
        "protect-witness",
        "investigation",
        "conference"
      ])
    });
    const encounterDefinitionId = "encounter.pax.protection-boarding";
    const encounter = Object.freeze({
      definitionId: encounterDefinitionId,
      key: `${ids.scenarioId}:${encounterDefinitionId}`,
      snapshotVersion: "pax-protection-encounter-snapshot.v1",
      activeStageIds: Object.freeze([stages.protection]),
      completedStageIds: Object.freeze([stages.investigation]),
      diagnosticInstanceSource: "pax-diagnostic-placeholder"
    });
    const positions = Object.freeze({
      assassin: freezeVector3([-0.35, -0.55, -37.65]),
      boarder01: freezeVector3([-3.2, -0.55, -35.8]),
      boarder02: freezeVector3([3.2, -0.55, -35.8]),
      boarder03: freezeVector3([-2.4, -0.55, -39.0]),
      boarder04: freezeVector3([2.4, -0.55, -39.0]),
      boarder05: freezeVector3([0.0, -0.55, -40.5]),
      witness: freezeVector3([-1.45, -0.55, -36.45]),
      marshal: freezeVector3([1.45, -0.55, -36.35])
    });
    const recoveryActions = Object.freeze({
      none: "none",
      reviveBoarders: "revive-boarders",
      restartEncounter: "restart-encounter"
    });
    const reconciliationModes = Object.freeze({
      passive: "passive",
      startupAttach: "startup-attach"
    });
    const reconciliationReasons = Object.freeze({
      automatic: "automatic-inconsistent-encounter-recovery",
      scenarioState: "scenario-state-inconsistent-recovery",
      scenarioRuntimeAttach: "scenario-runtime-attach-inconsistent-recovery",
      characterRuntimeAttach: "character-runtime-attach-encounter-reconciliation",
      characterState: "character-state-inconsistent-recovery"
    });

    return Object.freeze({
      ids,
      storage: Object.freeze({
        briefingAckPrefix: "main-computer.pax-scenario.briefing-ack.v1"
      }),
      stages,
      encounter,
      actors: Object.freeze({
        boarderIds: Object.freeze([
          "enemy.pax.quiet-service-assassin-01",
          "enemy.pax.boarder-01",
          "enemy.pax.boarder-02",
          "enemy.pax.boarder-03",
          "enemy.pax.boarder-04",
          "enemy.pax.boarder-05"
        ]),
        hardKickoffPositions: positions,
        boarderPositions: Object.freeze([
          positions.assassin,
          positions.boarder01,
          positions.boarder02,
          positions.boarder03,
          positions.boarder04,
          positions.boarder05
        ])
      }),
      recovery: Object.freeze({
        actions: recoveryActions,
        actorGroupStatus: EncounterState.ACTOR_GROUP_STATUS,
        states: Object.freeze({
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
        }),
        reconciliationModes,
        reconciliationReasons,
        reconciliationPolicy: Object.freeze({
          [reconciliationModes.passive]: Object.freeze({
            recoverDefeated: false
          }),
          [reconciliationModes.startupAttach]: Object.freeze({
            recoverDefeated: true
          })
        })
      })
    });
  })();

  const api = Object.freeze({
    config: PAX_SCENARIO_CONFIG,
    PAX_SCENARIO_CONFIG,
    scenarioId: PAX_SCENARIO_CONFIG.ids.scenarioId,
    systemId: PAX_SCENARIO_CONFIG.ids.systemId,
    protectionStageId: PAX_SCENARIO_CONFIG.stages.protection,
    investigationStageId: PAX_SCENARIO_CONFIG.stages.investigation,
    conferenceStageId: PAX_SCENARIO_CONFIG.stages.conference,
    encounterDefinitionId: PAX_SCENARIO_CONFIG.encounter.definitionId,
    encounterKey: PAX_SCENARIO_CONFIG.encounter.key,
    encounterSnapshotVersion: PAX_SCENARIO_CONFIG.encounter.snapshotVersion,
    encounterInstanceSource: PAX_SCENARIO_CONFIG.encounter.diagnosticInstanceSource,
    activeStageIds: PAX_SCENARIO_CONFIG.encounter.activeStageIds,
    completedStageIds: PAX_SCENARIO_CONFIG.encounter.completedStageIds,
    reconciliationModes: PAX_SCENARIO_CONFIG.recovery.reconciliationModes,
    reconciliationReasons: PAX_SCENARIO_CONFIG.recovery.reconciliationReasons,
    reconciliationPolicy: PAX_SCENARIO_CONFIG.recovery.reconciliationPolicy,
    recoveryActions: PAX_SCENARIO_CONFIG.recovery.actions,
    protectionStates: PAX_SCENARIO_CONFIG.recovery.states,
    boarderGroupStatus: PAX_SCENARIO_CONFIG.recovery.actorGroupStatus,
    boarderIds: Object.freeze(PAX_SCENARIO_CONFIG.actors.boarderIds.slice()),
    hardKickoffPositions: PAX_SCENARIO_CONFIG.actors.hardKickoffPositions,
    boarderPositions: PAX_SCENARIO_CONFIG.actors.boarderPositions,
    freezeVector3
  });

  global.MainComputerPaxScenarioConfig = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
