(function (global) {
  "use strict";

  const EncounterState = global.MainComputerEncounterState
    || (typeof require === "function" ? require("./encounter-state.js") : null);
  if (!EncounterState?.classifyActorGroup) {
    throw new Error("MainComputerEncounterState must load before Pax scenario interaction.");
  }

  function freezeVector3(values) {
    return Object.freeze([
      Number(values?.[0] || 0),
      Number(values?.[1] || 0),
      Number(values?.[2] || 0)
    ]);
  }

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

  const SCENARIO_ID = PAX_SCENARIO_CONFIG.ids.scenarioId;
  const PAX_SYSTEM_ID = PAX_SCENARIO_CONFIG.ids.systemId;
  const BRIEFING_ACK_PREFIX = PAX_SCENARIO_CONFIG.storage.briefingAckPrefix;
  const PROTECTION_STAGE_ID = PAX_SCENARIO_CONFIG.stages.protection;
  const INVESTIGATION_STAGE_ID = PAX_SCENARIO_CONFIG.stages.investigation;
  const CONFERENCE_STAGE_ID = PAX_SCENARIO_CONFIG.stages.conference;
  const PAX_PROTECTION_ENCOUNTER_DEFINITION_ID = PAX_SCENARIO_CONFIG.encounter.definitionId;
  const PAX_PROTECTION_ENCOUNTER_KEY = PAX_SCENARIO_CONFIG.encounter.key;
  const PAX_PROTECTION_ENCOUNTER_SNAPSHOT_VERSION = PAX_SCENARIO_CONFIG.encounter.snapshotVersion;
  const PAX_PROTECTION_ENCOUNTER_INSTANCE_SOURCE =
    PAX_SCENARIO_CONFIG.encounter.diagnosticInstanceSource;
  const PAX_PROTECTION_ACTIVE_STAGE_IDS = PAX_SCENARIO_CONFIG.encounter.activeStageIds;
  const PAX_PROTECTION_COMPLETED_STAGE_IDS = PAX_SCENARIO_CONFIG.encounter.completedStageIds;
  const PAX_PROTECTION_RECOVERY = PAX_SCENARIO_CONFIG.recovery.actions;
  const PAX_BOARDER_GROUP_STATUS = PAX_SCENARIO_CONFIG.recovery.actorGroupStatus;
  const PAX_PROTECTION_STATE = PAX_SCENARIO_CONFIG.recovery.states;
  const PAX_RECONCILIATION_MODE = PAX_SCENARIO_CONFIG.recovery.reconciliationModes;
  const PAX_RECONCILIATION_REASON = PAX_SCENARIO_CONFIG.recovery.reconciliationReasons;
  const PAX_RECONCILIATION_POLICY = PAX_SCENARIO_CONFIG.recovery.reconciliationPolicy;
  const HARD_KICKOFF_STAGE_IDS = PAX_SCENARIO_CONFIG.stages.hardKickoff;
  const BOARDER_IDS = PAX_SCENARIO_CONFIG.actors.boarderIds;
  const HARD_KICKOFF_POSITIONS = PAX_SCENARIO_CONFIG.actors.hardKickoffPositions;
  const BOARDER_POSITIONS = PAX_SCENARIO_CONFIG.actors.boarderPositions;

  function objectValue(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function arrayValue(value) {
    return Array.isArray(value) ? value : [];
  }

  function stringValue(value) {
    return String(value || "").trim();
  }

  function finiteNumber(value, fallback = 0) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function vector3(value, fallback = [0, 0, 0]) {
    const source = Array.isArray(value) ? value : fallback;
    return [
      finiteNumber(source[0], fallback[0] || 0),
      finiteNumber(source[1], fallback[1] || 0),
      finiteNumber(source[2], fallback[2] || 0)
    ];
  }

  function consequenceLabel(key) {
    return String(key || "")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/[-_]/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  const uiState = {
    bound: false,
    runtime: null,
    characterRuntime: null,
    unsubscribe: null,
    characterUnsubscribe: null,
    running: false,
    recoveryInProgress: false,
    lastAutomaticRecovery: null,
    lastError: "",
    lastHardKickoff: null,
    recoveredCharacterRuntime: null,
    worldSnapshot: null,
    acknowledgedBriefings: new Set()
  };

  function nodes(documentRef = global.document) {
    return {
      root: documentRef?.querySelector?.("#pax-scenario-contact"),
      briefing: documentRef?.querySelector?.("#pax-scenario-arrival-briefing"),
      briefingAck: documentRef?.querySelector?.("#pax-scenario-arrival-ack"),
      objective: documentRef?.querySelector?.("#pax-scenario-objective-banner"),
      objectiveKicker: documentRef?.querySelector?.("#pax-scenario-objective-kicker"),
      objectiveTitle: documentRef?.querySelector?.("#pax-scenario-objective-title"),
      objectiveDetail: documentRef?.querySelector?.("#pax-scenario-objective-detail"),
      threatTracker: documentRef?.querySelector?.("#pax-scenario-threat-tracker"),
      threatName: documentRef?.querySelector?.("#pax-scenario-threat-name"),
      threatDetail: documentRef?.querySelector?.("#pax-scenario-threat-detail"),
      threatAction: documentRef?.querySelector?.("#pax-scenario-threat-action"),
      hardStart: documentRef?.querySelector?.("#pax-scenario-hard-start"),
      hardStartTitle: documentRef?.querySelector?.("#pax-scenario-hard-start-title"),
      hardStartDetail: documentRef?.querySelector?.("#pax-scenario-hard-start-detail"),
      hardStartButton: documentRef?.querySelector?.("#pax-scenario-hard-start-button"),
      status: documentRef?.querySelector?.("#pax-scenario-status"),
      stageTitle: documentRef?.querySelector?.("#pax-scenario-stage-title"),
      stageDescription: documentRef?.querySelector?.("#pax-scenario-stage-description"),
      localRule: documentRef?.querySelector?.("#pax-scenario-local-rule"),
      vessel: documentRef?.querySelector?.("#pax-scenario-vessel"),
      characters: documentRef?.querySelector?.("#pax-scenario-characters"),
      evidence: documentRef?.querySelector?.("#pax-scenario-evidence"),
      evidenceList: documentRef?.querySelector?.("#pax-scenario-evidence-list"),
      proceed: documentRef?.querySelector?.("#pax-scenario-proceed"),
      resolutions: documentRef?.querySelector?.("#pax-scenario-resolutions"),
      resolutionList: documentRef?.querySelector?.("#pax-scenario-resolution-list"),
      outcome: documentRef?.querySelector?.("#pax-scenario-outcome")
    };
  }

  /*
   * Pure Pax presentation model helpers. These return stable data
   * snapshots and do not mutate the DOM.
   */

  function characterRows(view) {
    const ids = arrayValue(view?.definition?.characterIds);
    const runtime = uiState.characterRuntime
      || global.MainComputerCharacterAIRuntime?.current?.()
      || null;
    return ids.map((id) => {
      const state = runtime?.character?.(id) || null;
      return {
        id,
        label: stringValue(state?.label || id),
        kind: stringValue(state?.kind),
        health: Number(state?.health || 0),
        maxHealth: Number(state?.maxHealth || 1),
        status: stringValue(state?.status || "not-present"),
        actionId: stringValue(state?.currentActionId || "not-present"),
        protectedByPlayer: Boolean(state?.memory?.protectedByPlayer)
      };
    });
  }

  function requirementText(resolution) {
    const missing = arrayValue(resolution.missingEvidenceIds);
    if (resolution.available) return "Available";
    const parts = [];
    if (missing.length) parts.push(`${missing.length} required evidence thread${missing.length === 1 ? "" : "s"} missing`);
    if (Number(resolution.currentEvidenceCount || 0) < Number(resolution.requiredEvidenceCount || 0)) {
      parts.push(`${resolution.requiredEvidenceCount} total evidence threads required`);
    }
    if (resolution.conductSatisfied === false) {
      parts.push(
        `neutrality limit exceeded: ${resolution.currentIntimidationShots} intimidation discharges`
      );
    }
    return parts.join(" • ") || "Locked";
  }

  function stageStatus(view) {
    const state = objectValue(view?.state);
    const stage = objectValue(view?.stage);
    if (uiState.lastError) return uiState.lastError;
    if (state.status === "resolved") {
      return `${view.resolution?.label || "Pax settlement adopted"}.`;
    }
    if (state.status === "available") {
      return "Pax arrival trigger armed. The emergency protection detail begins when system arrival commits.";
    }
    if (state.stageId === PROTECTION_STAGE_ID) {
      return "HOSTILE BOARDING DETECTED. Six Quiet Service boarders are aboard. Repel every attacker.";
    }
    if (state.stageId === INVESTIGATION_STAGE_ID) {
      return "Boarding party eliminated. Their command data and weapon records were recovered automatically.";
    }
    if (state.stageId === CONFERENCE_STAGE_ID) {
      return "The emergency conference is open. Choose a settlement supported by the evidence.";
    }
    return stage.label || "Pax scenario active.";
  }

  function syncProtection() {
    if (!uiState.runtime) return null;
    const runtime = uiState.characterRuntime
      || global.MainComputerCharacterAIRuntime?.current?.()
      || null;
    if (!runtime) return null;
    try {
      return uiState.runtime.syncCharacterRuntime(
        SCENARIO_ID,
        runtime,
        {nowMs: performance.now()}
      );
    } catch {
      return null;
    }
  }

  function storage() {
    try {
      return global.localStorage || null;
    } catch {
      return null;
    }
  }

  function scenarioStartReceipt(view) {
    return arrayValue(view?.state?.receipts)
      .find((receipt) => stringValue(receipt?.reason) === "scenario-started") || null;
  }

  function briefingCueKey(view) {
    const receipt = scenarioStartReceipt(view);
    return stringValue(
      receipt?.activationKey
      || receipt?.receiptId
      || view?.state?.startedAtMs
      || `${SCENARIO_ID}:active`
    );
  }

  function briefingStorageKey(cueKey) {
    return `${BRIEFING_ACK_PREFIX}:${stringValue(cueKey) || "active"}`;
  }

  function briefingAcknowledged(cueKey) {
    const key = stringValue(cueKey);
    if (!key) return false;
    if (uiState.acknowledgedBriefings.has(key)) return true;
    const store = storage();
    if (!store?.getItem) return false;
    try {
      return store.getItem(briefingStorageKey(key)) === "acknowledged";
    } catch {
      return false;
    }
  }

  function acknowledgeBriefing(view = null) {
    const currentView = view
      || uiState.runtime?.view?.(SCENARIO_ID)
      || null;
    const cueKey = briefingCueKey(currentView);
    if (cueKey) {
      uiState.acknowledgedBriefings.add(cueKey);
      const store = storage();
      try {
        store?.setItem?.(briefingStorageKey(cueKey), "acknowledged");
      } catch {
        // Acknowledgement persistence is best effort only.
      }
    }
    render();
    return {acknowledged: Boolean(cueKey), cueKey};
  }

  function isLegacyPaxOccupancy(navigation = {}) {
    return stringValue(navigation.currentSystemId) === PAX_SYSTEM_ID
      && stringValue(navigation.travelPhase || "in-system") === "in-system"
      && !stringValue(navigation.changeReason)
      && !isDurableCommittedArrival(navigation);
  }

  function legacyActivationKey(navigation = {}) {
    const sequence = Number(navigation.sequence);
    return [
      PAX_SYSTEM_ID,
      "current-system-recovery",
      Number.isFinite(sequence) ? Math.trunc(sequence) : 0
    ].join(":");
  }

  function objectivePresentation(view) {
    const state = objectValue(view?.state);
    const evidenceCount = arrayValue(state.evidenceIds).length;
    const evidenceTotal = arrayValue(view?.evidence).length;
    if (state.status === "active" && state.stageId === PROTECTION_STAGE_ID) {
      return {
        visible: true,
        kicker: "PAX PRIORITY ONE",
        title: "REPEL THE BOARDERS",
        detail: "SIX HOSTILES ABOARD • CLEAR THE BRIDGE AND AFT BREACH • PROTECT THE CREW",
        urgent: true
      };
    }
    if (state.status === "active" && state.stageId === INVESTIGATION_STAGE_ID) {
      return {
        visible: true,
        kicker: "BOARDING PARTY ELIMINATED",
        title: "RECOVERED COMMAND DATA",
        detail: "INTELLIGENCE SECURED AUTOMATICALLY • OPEN THE EMERGENCY CONFERENCE",
        urgent: false
      };
    }
    if (state.status === "active" && state.stageId === CONFERENCE_STAGE_ID) {
      return {
        visible: true,
        kicker: "PAX EMERGENCY CONFERENCE",
        title: "CHOOSE A DEFENSIBLE SETTLEMENT",
        detail: `${evidenceCount}/${evidenceTotal} EVIDENCE THREADS AVAILABLE`,
        urgent: false
      };
    }
    return {
      visible: false,
      kicker: "",
      title: "",
      detail: "",
      urgent: false
    };
  }

  function activeBoardersFromRuntime() {
    const runtime = currentCharacterRuntime();
    return BOARDER_IDS.map((id) => runtime?.character?.(id) || null)
      .filter((character) => (
        character
        && stringValue(character.status) === "active"
        && finiteNumber(character.health, 0) > 0
      ));
  }

  function activeBoardersFromSnapshot(snapshot = {}) {
    const characters = arrayValue(snapshot.characters);
    return characters.filter((character) => (
      BOARDER_IDS.includes(stringValue(character.id))
      && stringValue(character.status) === "active"
      && finiteNumber(character.health, 0) > 0
    ));
  }

  function directionText(playerPosition, attackerPosition) {
    const player = vector3(playerPosition, [0, 0, -36.7]);
    const attacker = vector3(attackerPosition, HARD_KICKOFF_POSITIONS.assassin);
    const dx = attacker[0] - player[0];
    const dz = attacker[2] - player[2];
    const distance = Math.hypot(dx, dz);
    const side = Math.abs(dx) < 0.45 ? "CENTER" : dx < 0 ? "LEFT" : "RIGHT";
    const depth = Math.abs(dz) < 0.55
      ? "SAME DECK"
      : dz < 0
        ? "AHEAD / VIEWSCREEN SIDE"
        : "BEHIND YOU";
    return `${side} • ${depth} • ${distance.toFixed(1)}m`;
  }

  function threatPresentation(view, snapshot = uiState.worldSnapshot) {
    const state = objectValue(view?.state);
    if (state.status !== "active" || state.stageId !== PROTECTION_STAGE_ID) {
      return {
        visible: false,
        name: "",
        detail: "",
        action: "",
        health: 0,
        maxHealth: 1,
        distance: "",
        remaining: 0
      };
    }
    const world = objectValue(snapshot);
    const boarders = activeBoardersFromSnapshot(world);
    const active = boarders.length ? boarders : activeBoardersFromRuntime();
    if (!active.length) {
      return {
        visible: true,
        name: "BOARDING PARTY",
        detail: "NO ACTIVE HOSTILES DETECTED • VERIFYING SHIP CLEAR",
        action: "Hold position while the tactical scan completes.",
        health: 0,
        maxHealth: 1,
        distance: "",
        remaining: 0
      };
    }
    const playerPosition = objectValue(world.player).position;
    const nearest = active.slice().sort((left, right) => {
      const lp = vector3(left.position, [0, 0, 0]);
      const rp = vector3(right.position, [0, 0, 0]);
      const pp = vector3(playerPosition, [0, 0, -36.7]);
      return Math.hypot(lp[0] - pp[0], lp[2] - pp[2])
        - Math.hypot(rp[0] - pp[0], rp[2] - pp[2]);
    })[0];
    const health = Math.max(0, Math.round(finiteNumber(nearest.health, 0)));
    const maxHealth = Math.max(1, Math.round(finiteNumber(nearest.maxHealth, 1)));
    const direction = directionText(playerPosition, nearest.position);
    return {
      visible: true,
      name: `REPEL THE BOARDERS — ${active.length} REMAIN`,
      detail: `NEAREST: ${stringValue(nearest.label || "HOSTILE").toUpperCase()} • ${direction} • ${health}/${maxHealth} HP`,
      action: "Red beacons mark every hostile. Clear the bridge and aft breach.",
      health,
      maxHealth,
      distance: direction,
      remaining: active.length
    };
  }

  function arrivalActivationKey(navigation = {}) {
    const routeId = stringValue(navigation.lastCompletedRouteId || "unknown-route");
    const arrivalAtMs = Number(navigation.lastArrivalAtMs);
    const sequence = Number(navigation.sequence);
    return [
      PAX_SYSTEM_ID,
      routeId,
      Number.isFinite(arrivalAtMs) ? arrivalAtMs : "unknown-time",
      Number.isFinite(sequence) ? Math.trunc(sequence) : "unknown-sequence"
    ].join(":");
  }

  function isDurableCommittedArrival(navigation = {}) {
    return stringValue(navigation.currentSystemId) === PAX_SYSTEM_ID
      && stringValue(navigation.travelPhase) === "in-system"
      && Boolean(stringValue(navigation.lastCompletedRouteId))
      && navigation.lastArrivalAtMs !== null
      && navigation.lastArrivalAtMs !== undefined
      && Number.isFinite(Number(navigation.lastArrivalAtMs));
  }

  function revealArrivalPanel() {
    const root = nodes().root;
    if (!root) return;
    global.MainComputerStrategicAIPanelLayout?.applyPanelMode?.(
      root,
      "expanded",
      {persist: false}
    );
  }

  function nowMs(options = {}) {
    if (Number.isFinite(Number(options.nowMs))) return Number(options.nowMs);
    if (typeof performance !== "undefined" && typeof performance.now === "function") {
      return performance.now();
    }
    return Date.now();
  }

  function currentRuntime() {
    return uiState.runtime
      || global.MainComputerSystemScenarioRuntime?.current?.()
      || null;
  }

  function currentCharacterRuntime() {
    return uiState.characterRuntime
      || global.MainComputerCharacterAIRuntime?.current?.()
      || null;
  }

  function activeShuttleRenderer() {
    return global.document
      ?.querySelector?.("#webgl-demo")
      ?.__mainComputerShuttle3dRenderer
      || null;
  }

  function cameraRelativeBoardingPositions() {
    const renderer = activeShuttleRenderer();
    const camera = vector3(renderer?.camera, [0, 0.9, -35]);
    const direction = typeof renderer?.cameraDirection === "function"
      ? vector3(renderer.cameraDirection(), [0, 0, -1])
      : [0, 0, -1];
    const horizontalLength = Math.hypot(direction[0], direction[2]) || 1;
    const forward = [
      direction[0] / horizontalLength,
      0,
      direction[2] / horizontalLength
    ];
    const right = [-forward[2], 0, forward[0]];
    const baseY = -0.55;
    const slots = [
      {forward: 5.0, right: 0.0},
      {forward: 6.4, right: -1.8},
      {forward: 6.4, right: 1.8},
      {forward: 8.2, right: -2.4},
      {forward: 8.2, right: 2.4},
      {forward: 10.0, right: 0.0}
    ];
    return slots.map((slot) => [
      camera[0] + (forward[0] * slot.forward) + (right[0] * slot.right),
      baseY,
      camera[2] + (forward[2] * slot.forward) + (right[2] * slot.right)
    ]);
  }

  function forceProtectionEncounterCharacters(reason = "pax-hard-kickoff", options = {}) {
    const runtime = currentCharacterRuntime();
    const clock = nowMs(options);
    if (!runtime?.forceCharacterState) {
      return {
        forced: false,
        reason: "character-force-unavailable",
        source: stringValue(reason)
      };
    }
    const source = stringValue(reason || "pax-hard-kickoff");
    const deploymentPositions = cameraRelativeBoardingPositions();
    uiState.lastHardKickoff = {
      reason: source,
      nowMs: clock,
      positions: {
        boarders: deploymentPositions.map((position) => position.slice()),
        witness: HARD_KICKOFF_POSITIONS.witness.slice(),
        marshal: HARD_KICKOFF_POSITIONS.marshal.slice()
      }
    };
    const results = {};
    BOARDER_IDS.forEach((characterId, index) => {
      results[characterId] = runtime.forceCharacterState(
        characterId,
        {
          revive: true,
          status: "active",
          position: deploymentPositions[index],
          currentActionId: index === 0 ? "call_support" : "move_to_player",
          currentTargetId: index === 0 ? "ship.pax.quiet-service-cutter-01" : "player",
          nextDecisionAtMs: clock + 900 + (index * 180),
          nextAttackAtMs: clock + 2200 + (index * 240),
          memory: {
            supportCalled: index !== 0,
            playerSeen: false,
            lastDamageAtMs: null,
            lastDamageSource: ""
          }
        },
        {nowMs: clock, source}
      );
    });
    results.witness = runtime.forceCharacterState(
      "npc.pax.refugee-witness-01",
      {
        revive: true,
        status: "active",
        position: HARD_KICKOFF_POSITIONS.witness,
        currentActionId: "warn_player",
        currentTargetId: "player",
        nextDecisionAtMs: 0,
        memory: {
          warnedPlayer: false,
          protectedByPlayer: false
        }
      },
      {nowMs: clock, source}
    );
    results.marshal = runtime.forceCharacterState(
      "npc.pax.neutrality-marshal-01",
      {
        revive: true,
        status: "active",
        position: HARD_KICKOFF_POSITIONS.marshal,
        currentActionId: "hold_position",
        currentTargetId: "npc.pax.refugee-witness-01",
        nextDecisionAtMs: 0,
        memory: {
          warnedPlayer: false,
          protectedByPlayer: false
        }
      },
      {nowMs: clock, source}
    );
    return {
      forced: true,
      reason: source,
      boarderIds: BOARDER_IDS.slice(),
      results
    };
  }

  function hardStartPresentation(view) {
    const state = objectValue(view?.state);
    if (!view?.visible) {
      return {
        visible: false,
        title: "",
        detail: "",
        button: "Start Pax now"
      };
    }
    if (state.status === "available") {
      return {
        visible: true,
        title: "PAX MISSION READY",
        detail: "Pax is the active system, but the encounter has not started. Press Start / Recover Pax to force the witness mission now.",
        button: "Start / recover Pax"
      };
    }
    if (state.status === "active" && HARD_KICKOFF_STAGE_IDS.includes(state.stageId)) {
      const protection = state.stageId === PROTECTION_STAGE_ID;
      return {
        visible: true,
        title: protection ? "PAX MISSION LIVE" : "PAX MISSION ACTIVE",
        detail: protection
          ? "Six hostile boarders are spread across the bridge and aft breach. Eliminate every marked hostile."
          : stageStatus(view),
        button: protection ? "Respawn visible encounter" : "Restart boarding encounter"
      };
    }
    if (state.status === "resolved") {
      return {
        visible: true,
        title: "PAX RESOLVED",
        detail: stageStatus(view),
        button: "Resolved"
      };
    }
    return {
      visible: false,
      title: "",
      detail: "",
      button: "Start Pax now"
    };
  }

  function isPaxPresentationViewModel(value) {
    return stringValue(value?.snapshotKind) === "pax-protection-presentation";
  }

  function toPaxPresentationViewModel(viewOrPresentation, options = {}) {
    return isPaxPresentationViewModel(viewOrPresentation)
      ? viewOrPresentation
      : paxProtectionPresentationViewModel(viewOrPresentation, options);
  }

  function missionCuesPresentation(view, context = {}) {
    const status = stringValue(context.status);
    const stageId = stringValue(context.stageId);
    const cueKey = briefingCueKey(view);
    return {
      briefing: {
        visible: status === "active"
          && stageId === PROTECTION_STAGE_ID
          && !briefingAcknowledged(cueKey),
        cueKey,
        scenarioStage: stageId
      },
      objective: context.objective || objectivePresentation(view)
    };
  }

  function evidencePresentation(view, context = {}) {
    const status = stringValue(context.status);
    const stageId = stringValue(context.stageId);
    return {
      visible: status === "active"
        && [INVESTIGATION_STAGE_ID, CONFERENCE_STAGE_ID].includes(stageId),
      items: arrayValue(view?.evidence)
    };
  }

  function proceedPresentation(view, context = {}) {
    const state = objectValue(view?.state);
    const stageId = stringValue(context.stageId || state.stageId);
    return {
      visible: stageId === INVESTIGATION_STAGE_ID,
      disabled: uiState.running || arrayValue(state.evidenceIds).length < 2
    };
  }

  function resolutionsPresentation(view, context = {}) {
    const status = stringValue(context.status);
    const stageId = stringValue(context.stageId);
    return {
      visible: status === "active" && stageId === CONFERENCE_STAGE_ID,
      items: arrayValue(view?.resolutions)
    };
  }

  function outcomePresentation(view, context = {}) {
    const status = stringValue(context.status || view?.state?.status);
    return {
      visible: status === "resolved",
      consequences: objectValue(view?.state?.consequences)
    };
  }

  function charactersPresentation(view) {
    return {
      rows: characterRows(view)
    };
  }

  function paxProtectionPresentationViewModel(view, options = {}) {
    const state = objectValue(view?.state);
    const stage = objectValue(view?.stage);
    const status = stringValue(state.status);
    const stageId = stringValue(state.stageId);
    const metrics = objectValue(state.metrics);
    const discharges = Number(metrics.weaponDischarges || 0);
    const intimidation = Number(metrics.intimidationDischarges || 0);
    const conduct = discharges
      ? ` • ${discharges} weapon discharges, ${intimidation} classed as intimidation`
      : "";
    const objective = objectivePresentation(view);
    const hardStart = hardStartPresentation(view);
    const threat = threatPresentation(
      view,
      Object.prototype.hasOwnProperty.call(options, "worldSnapshot")
        ? options.worldSnapshot
        : uiState.worldSnapshot
    );
    const missionCues = missionCuesPresentation(view, {status, stageId, objective});
    const evidence = evidencePresentation(view, {status, stageId});
    const proceed = proceedPresentation(view, {stageId});
    const resolutions = resolutionsPresentation(view, {status, stageId});
    const outcome = outcomePresentation(view, {status});
    const characters = charactersPresentation(view);
    const visible = Boolean(view?.visible);
    const statusText = stageStatus(view);

    return {
      snapshotVersion: "pax-protection-presentation-view-model.v1",
      snapshotKind: "pax-protection-presentation",
      readOnly: true,
      visible,
      scenario: {
        id: SCENARIO_ID,
        systemId: PAX_SYSTEM_ID,
        status,
        stageId,
        stageLabel: stringValue(stage.label || stageId),
        stageDescription: stringValue(stage.description || "")
      },
      status: {
        text: statusText
      },
      stage: {
        title: stringValue(stage.label || stageId),
        description: stringValue(stage.description || "")
      },
      localRule: {
        text: view?.definition?.localRule
          ? `Local rule: ${view.definition.localRule}${conduct}`
          : "",
        weaponDischarges: discharges,
        intimidationDischarges: intimidation
      },
      vessel: {
        text: `Enemy ship: ${view?.vesselStatus || "status unknown"}`
      },
      missionCues,
      objective,
      threat,
      hardStart: Object.assign({}, hardStart, {
        buttonDisabled: uiState.running || status === "resolved",
        buttonHidden: status === "resolved"
      }),
      characters,
      evidence,
      proceed,
      resolutions,
      outcome
    };
  }

  /*
   * Pax DOM presentation renderers. These are the only presentation
   * helpers in this file that mutate DOM nodes or attach DOM events.
   */

  function renderCharacters(container, viewOrPresentation) {
    if (!container) return;
    container.replaceChildren();
    const presentation = toPaxPresentationViewModel(viewOrPresentation);
    const rows = arrayValue(presentation.characters?.rows);
    rows.forEach((row) => {
      const item = document.createElement("article");
      item.className = "pax-scenario-character";
      item.dataset.characterId = row.id;
      item.dataset.characterStatus = row.status;
      const heading = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = row.label;
      const badge = document.createElement("span");
      badge.textContent = row.kind === "enemy" ? "HOSTILE" : "PROTECTED PERSON";
      heading.append(name, badge);
      const detail = document.createElement("small");
      const health = Math.max(0, Math.round(row.health));
      const maxHealth = Math.max(1, Math.round(row.maxHealth));
      const action = row.actionId.replace(/_/g, " ");
      detail.textContent = `${health}/${maxHealth} health • ${row.status} • ${action}`;
      if (row.protectedByPlayer) detail.textContent += " • protected by player";
      item.append(heading, detail);
      container.append(item);
    });
  }

  function renderEvidence(container, viewOrPresentation) {
    if (!container) return;
    container.replaceChildren();
    const presentation = toPaxPresentationViewModel(viewOrPresentation);
    arrayValue(presentation.evidence?.items).forEach((evidence) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pax-scenario-evidence-button";
      button.dataset.evidenceId = evidence.id;
      button.disabled = uiState.running || evidence.collected;
      const title = document.createElement("strong");
      title.textContent = evidence.collected ? `✓ ${evidence.label}` : evidence.label;
      const description = document.createElement("span");
      description.textContent = evidence.description;
      button.append(title, description);
      button.addEventListener("click", () => runUi(() => (
        uiState.runtime.recordEvidence(SCENARIO_ID, evidence.id, {
          nowMs: performance.now()
        })
      )));
      container.append(button);
    });
  }

  function renderResolutions(container, viewOrPresentation) {
    if (!container) return;
    container.replaceChildren();
    const presentation = toPaxPresentationViewModel(viewOrPresentation);
    arrayValue(presentation.resolutions?.items).forEach((resolution) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pax-scenario-resolution-button";
      button.dataset.resolutionId = resolution.id;
      button.disabled = uiState.running || !resolution.available;
      const title = document.createElement("strong");
      title.textContent = resolution.label;
      const description = document.createElement("span");
      description.textContent = resolution.description;
      const requirement = document.createElement("small");
      requirement.textContent = requirementText(resolution);
      button.append(title, description, requirement);
      button.addEventListener("click", () => runUi(() => (
        uiState.runtime.resolveScenario(SCENARIO_ID, resolution.id, {
          nowMs: performance.now()
        })
      )));
      container.append(button);
    });
  }

  function renderOutcome(container, viewOrPresentation) {
    if (!container) return;
    container.replaceChildren();
    const presentation = toPaxPresentationViewModel(viewOrPresentation);
    const consequences = objectValue(presentation.outcome?.consequences);
    Object.entries(consequences).forEach(([key, value]) => {
      const item = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = consequenceLabel(key);
      const result = document.createElement("strong");
      result.textContent = stringValue(value).replace(/-/g, " ");
      item.append(label, result);
      container.append(item);
    });
  }

  function renderThreatTracker(ui, viewOrPresentation) {
    const presentation = toPaxPresentationViewModel(viewOrPresentation);
    const threat = presentation.threat;
    if (ui.threatTracker) {
      ui.threatTracker.hidden = !threat.visible;
      ui.threatTracker.dataset.scenarioStage = presentation.scenario.stageId;
      ui.threatTracker.dataset.threatVisible = threat.visible ? "true" : "false";
      ui.threatTracker.dataset.threatHealth = String(threat.health);
    }
    if (ui.threatName) ui.threatName.textContent = threat.name;
    if (ui.threatDetail) ui.threatDetail.textContent = threat.detail;
    if (ui.threatAction) ui.threatAction.textContent = threat.action;
    return threat;
  }

  function renderMissionCues(ui, viewOrPresentation) {
    const presentation = toPaxPresentationViewModel(viewOrPresentation);
    const briefing = presentation.missionCues.briefing;
    const objective = presentation.missionCues.objective;
    if (ui.briefing) {
      ui.briefing.hidden = !briefing.visible;
      ui.briefing.dataset.cueKey = briefing.cueKey;
      ui.briefing.dataset.scenarioStage = presentation.scenario.stageId;
    }

    if (ui.objective) {
      ui.objective.hidden = !objective.visible;
      ui.objective.dataset.scenarioStage = presentation.scenario.stageId;
      ui.objective.dataset.urgent = objective.urgent ? "true" : "false";
    }
    if (ui.objectiveKicker) ui.objectiveKicker.textContent = objective.kicker;
    if (ui.objectiveTitle) ui.objectiveTitle.textContent = objective.title;
    if (ui.objectiveDetail) ui.objectiveDetail.textContent = objective.detail;
    return {
      showBriefing: briefing.visible,
      cueKey: briefing.cueKey,
      objective
    };
  }

  function renderHardStart(ui, viewOrPresentation) {
    const viewModel = toPaxPresentationViewModel(viewOrPresentation);
    const presentation = viewModel.hardStart;
    if (ui.hardStart) {
      ui.hardStart.hidden = !presentation.visible;
      ui.hardStart.dataset.scenarioStatus = viewModel.scenario.status;
      ui.hardStart.dataset.scenarioStage = viewModel.scenario.stageId;
    }
    if (ui.hardStartTitle) ui.hardStartTitle.textContent = presentation.title;
    if (ui.hardStartDetail) ui.hardStartDetail.textContent = presentation.detail;
    if (ui.hardStartButton) {
      ui.hardStartButton.textContent = presentation.button;
      ui.hardStartButton.disabled = presentation.buttonDisabled;
      ui.hardStartButton.hidden = presentation.buttonHidden;
    }
    return presentation;
  }


  function resetProtectionEncounter(reason = "pax-protection-reset", options = {}) {
    const runtime = currentRuntime();
    const clock = nowMs(options);
    if (!runtime?.resetProtectionEncounter) {
      return {
        reset: false,
        forced: false,
        reason: "scenario-reset-unavailable"
      };
    }

    /*
     * Revive the characters while the scenario is still outside the protection
     * stage. Character-runtime subscriptions may synchronously call
     * syncProtection(); keeping the old stage until all boarders are alive
     * prevents that callback from immediately completing the freshly reset
     * encounter.
     */
    uiState.lastHardKickoff = null;
    const forced = forceProtectionEncounterCharacters(reason, {nowMs: clock});
    if (!forced?.forced) {
      return {
        reset: false,
        forced: false,
        reason: "character-reset-failed",
        forceResult: forced
      };
    }

    const scenarioReset = runtime.resetProtectionEncounter(SCENARIO_ID, {
      nowMs: clock,
      source: stringValue(reason || "pax-protection-reset")
    });
    if (!scenarioReset?.reset) {
      return {
        reset: false,
        forced: true,
        reason: scenarioReset?.reason || "scenario-reset-failed",
        forceResult: forced,
        scenarioResult: scenarioReset
      };
    }

    uiState.recoveredCharacterRuntime = currentCharacterRuntime();
    revealArrivalPanel();
    render();
    return {
      reset: true,
      forced: true,
      reason: stringValue(reason || "pax-protection-reset"),
      forceResult: forced,
      scenarioResult: scenarioReset,
      view: scenarioReset.view
    };
  }

  function startOrRecoverProtectionEncounter(reason = "pax-hard-kickoff", options = {}) {
    const runtime = currentRuntime();
    const clock = nowMs(options);
    if (!runtime?.view) {
      return {
        handled: false,
        started: false,
        forced: false,
        reason: "scenario-runtime-unavailable"
      };
    }
    uiState.runtime = runtime;
    if (runtime.state?.activeSystemId !== PAX_SYSTEM_ID && options.allowSystemChange) {
      runtime.setActiveSystemId?.(PAX_SYSTEM_ID, {
        nowMs: clock,
        record: options.recordSystemChange !== false
      });
    }
    const before = runtime.view(SCENARIO_ID);
    if (!before) {
      return {
        handled: false,
        started: false,
        forced: false,
        reason: "pax-scenario-unavailable"
      };
    }
    if (!before.visible && options.allowSystemChange !== true) {
      return {
        handled: false,
        started: false,
        forced: false,
        reason: "pax-not-visible"
      };
    }
    if (before.state?.status === "active"
        && before.state.stageId !== PROTECTION_STAGE_ID
        && options.restartProtectionEncounter === true) {
      const reset = resetProtectionEncounter(reason, {nowMs: clock});
      return {
        handled: true,
        started: false,
        reused: false,
        ...reset
      };
    }

    let result = {
      handled: true,
      started: false,
      reused: before.state?.status !== "available",
      receipt: null,
      view: before
    };
    if (before.state?.status === "available") {
      result = runtime.startScenario(SCENARIO_ID, {
        nowMs: clock,
        trigger: stringValue(reason || "pax-hard-kickoff"),
        activationKey: [
          PAX_SYSTEM_ID,
          stringValue(reason || "hard-kickoff"),
          Math.trunc(clock)
        ].join(":"),
        routeId: stringValue(options.routeId),
        navigationSequence: Number(options.navigationSequence) || 0
      });
    }
    const after = runtime.view(SCENARIO_ID);
    let forced = {forced: false, reason: "not-protection-stage"};
    if (after?.state?.status === "active" && after.state.stageId === PROTECTION_STAGE_ID) {
      forced = forceProtectionEncounterCharacters(reason, {nowMs: clock});
    }
    revealArrivalPanel();
    render();
    return {
      handled: true,
      started: !result.reused,
      reused: Boolean(result.reused),
      forced: Boolean(forced.forced),
      forceResult: forced,
      view: after,
      ...result
    };
  }

  function handleNavigation(navigation = {}) {
    const runtime = uiState.runtime
      || global.MainComputerSystemScenarioRuntime?.current?.()
      || null;
    const systemId = stringValue(navigation.currentSystemId);
    const reason = stringValue(navigation.changeReason);
    const committed = reason === "arrival-committed";
    const recoveredCommit = !reason && isDurableCommittedArrival(navigation);
    const legacyRecovery = isLegacyPaxOccupancy(navigation);

    if (!runtime?.view || systemId !== PAX_SYSTEM_ID) {
      return {
        handled: false,
        started: false,
        reason: systemId === PAX_SYSTEM_ID
          ? "scenario-runtime-unavailable"
          : "not-pax"
      };
    }
    if (!committed && !recoveredCommit && !legacyRecovery) {
      return {
        handled: false,
        started: false,
        reason: reason || "no-committed-arrival"
      };
    }

    if (runtime.state?.activeSystemId !== systemId) {
      runtime.setActiveSystemId?.(systemId, {
        nowMs: Number(navigation.lastArrivalAtMs) || 0
      });
    }
    const before = runtime.view(SCENARIO_ID);
    if (!before) {
      return {
        handled: false,
        started: false,
        reason: "pax-scenario-unavailable"
      };
    }
    const activationKey = legacyRecovery
      ? legacyActivationKey(navigation)
      : arrivalActivationKey(navigation);
    if (before.state?.status !== "available") {
      let forced = {forced: false, reason: "not-protection-stage"};
      if (before.state?.status === "active" && before.state.stageId === PROTECTION_STAGE_ID) {
        forced = forceProtectionEncounterCharacters("navigation-recovered-protection", {
          nowMs: Number(navigation.lastArrivalAtMs) || 0
        });
      }
      revealArrivalPanel();
      render();
      return {
        handled: true,
        started: false,
        reused: true,
        activationKey,
        forced: Boolean(forced.forced),
        forceResult: forced,
        view: before
      };
    }

    const result = runtime.startScenario(SCENARIO_ID, {
      nowMs: Number(navigation.lastArrivalAtMs) || 0,
      trigger: legacyRecovery
        ? "navigation-current-system-recovery"
        : recoveredCommit
          ? "navigation-arrival-recovery"
          : "navigation-arrival",
      activationKey,
      routeId: stringValue(navigation.lastCompletedRouteId),
      navigationSequence: Number(navigation.sequence) || 0
    });
    const forced = forceProtectionEncounterCharacters("navigation-arrival-hard-kickoff", {
      nowMs: Number(navigation.lastArrivalAtMs) || 0
    });
    revealArrivalPanel();
    render();
    return {
      handled: true,
      started: !result.reused,
      reused: Boolean(result.reused),
      forced: Boolean(forced.forced),
      forceResult: forced,
      activationKey,
      ...result
    };
  }

  function render() {
    const ui = nodes();
    const runtime = uiState.runtime
      || global.MainComputerSystemScenarioRuntime?.current?.()
      || null;
    if (!ui.root) return null;
    if (!runtime?.view) {
      ui.root.hidden = true;
      if (ui.briefing) ui.briefing.hidden = true;
      if (ui.objective) ui.objective.hidden = true;
      if (ui.threatTracker) ui.threatTracker.hidden = true;
      if (ui.hardStart) ui.hardStart.hidden = true;
      return null;
    }
    uiState.runtime = runtime;
    syncProtection();
    let view = runtime.view(SCENARIO_ID);
    if (!view) {
      ui.root.hidden = true;
      if (ui.briefing) ui.briefing.hidden = true;
      if (ui.objective) ui.objective.hidden = true;
      if (ui.threatTracker) ui.threatTracker.hidden = true;
      if (ui.hardStart) ui.hardStart.hidden = true;
      return null;
    }

    let state = objectValue(view.state);
    if (view.visible && state.status === "available" && !uiState.recoveryInProgress) {
      uiState.recoveryInProgress = true;
      try {
        const recovered = startOrRecoverProtectionEncounter(
          "visible-pax-current-system-hard-kickoff",
          {nowMs: nowMs({}), allowSystemChange: false}
        );
        view = recovered.view || runtime.view(SCENARIO_ID) || view;
      } finally {
        uiState.recoveryInProgress = false;
      }
      state = objectValue(view.state);
    }

    if (view.visible
        && state.status === "active"
        && state.stageId === PROTECTION_STAGE_ID
        && !uiState.lastHardKickoff
        && !uiState.recoveryInProgress) {
      forceProtectionEncounterCharacters("visible-protection-hard-kickoff", {nowMs: nowMs({})});
    }

    const presentation = paxProtectionPresentationViewModel(view);
    ui.root.hidden = !presentation.visible;
    renderHardStart(ui, presentation);
    if (!presentation.visible) return view;
    state = objectValue(view.state);
    ui.root.dataset.scenarioStatus = presentation.scenario.status;
    ui.root.dataset.scenarioStage = presentation.scenario.stageId;
    renderMissionCues(ui, presentation);
    renderThreatTracker(ui, presentation);

    if (ui.status) ui.status.textContent = presentation.status.text;
    if (ui.stageTitle) ui.stageTitle.textContent = presentation.stage.title;
    if (ui.stageDescription) ui.stageDescription.textContent = presentation.stage.description;
    if (ui.localRule) ui.localRule.textContent = presentation.localRule.text;
    if (ui.vessel) ui.vessel.textContent = presentation.vessel.text;

    renderCharacters(ui.characters, presentation);

    const evidenceVisible = presentation.evidence.visible;
    if (ui.evidence) ui.evidence.hidden = !evidenceVisible;
    if (evidenceVisible) renderEvidence(ui.evidenceList, presentation);
    if (ui.proceed) {
      ui.proceed.hidden = !presentation.proceed.visible;
      ui.proceed.disabled = presentation.proceed.disabled;
    }

    const resolutionVisible = presentation.resolutions.visible;
    if (ui.resolutions) ui.resolutions.hidden = !resolutionVisible;
    if (resolutionVisible) renderResolutions(ui.resolutionList, presentation);

    if (ui.outcome) {
      ui.outcome.hidden = !presentation.outcome.visible;
      if (presentation.outcome.visible) renderOutcome(ui.outcome, presentation);
      else ui.outcome.replaceChildren();
    }
    return view;
  }

  async function runUi(operation) {
    if (uiState.running || typeof operation !== "function") return null;
    uiState.running = true;
    uiState.lastError = "";
    render();
    try {
      const result = await operation();
      return result;
    } catch (error) {
      uiState.lastError = error instanceof Error
        ? error.message
        : String(error || "Pax scenario operation failed.");
      return null;
    } finally {
      uiState.running = false;
      render();
    }
  }

  function setWorldSnapshot(snapshot = null) {
    uiState.worldSnapshot = snapshot && typeof snapshot === "object"
      ? cloneSnapshot(snapshot)
      : null;
    render();
    return uiState.worldSnapshot;
  }

  function cloneSnapshot(snapshot) {
    try {
      return JSON.parse(JSON.stringify(snapshot));
    } catch {
      return snapshot;
    }
  }

  function paxProtectionStateLabels() {
    return {
      unavailable: PAX_PROTECTION_STATE.unavailable,
      scenarioInactive: PAX_PROTECTION_STATE.scenarioInactive,
      actorRuntimeUnavailable: PAX_PROTECTION_STATE.characterRuntimeUnavailable,
      consistentActive: PAX_PROTECTION_STATE.consistentActive,
      recoverableActiveDefeated: PAX_PROTECTION_STATE.recoverableProtectionDefeated,
      invalidActiveActors: PAX_PROTECTION_STATE.invalidProtectionBoarders,
      recoverableCompletedActive: PAX_PROTECTION_STATE.recoverableInvestigationActive,
      recoverableCompletedDefeated: PAX_PROTECTION_STATE.recoverableInvestigationDefeated,
      invalidCompletedActors: PAX_PROTECTION_STATE.invalidInvestigationBoarders,
      outsideEncounter: PAX_PROTECTION_STATE.outsideProtection
    };
  }

  function paxProtectionRecoveryActions() {
    return {
      none: PAX_PROTECTION_RECOVERY.none,
      reviveActors: PAX_PROTECTION_RECOVERY.reviveBoarders,
      restartEncounter: PAX_PROTECTION_RECOVERY.restartEncounter
    };
  }

  function paxProtectionEncounterIdentity(options = {}) {
    const view = options.view || null;
    return EncounterState.encounterIdentity({
      key: PAX_PROTECTION_ENCOUNTER_KEY,
      definitionId: PAX_PROTECTION_ENCOUNTER_DEFINITION_ID,
      instanceId: options.instanceId || options.encounterInstanceId || options.runId,
      scenarioId: SCENARIO_ID,
      systemId: PAX_SYSTEM_ID,
      view,
      stageId: options.stageId || view?.state?.stageId,
      activeStageIds: PAX_PROTECTION_ACTIVE_STAGE_IDS,
      completedStageIds: PAX_PROTECTION_COMPLETED_STAGE_IDS,
      actorIds: BOARDER_IDS
    });
  }

  function paxProtectionEncounterInstanceDescriptor(options = {}) {
    const identity = options.identity || paxProtectionEncounterIdentity(options);
    return EncounterState.encounterInstanceDescriptor({
      identity,
      instanceId: options.instanceId || options.encounterInstanceId || options.runId,
      proposedInstanceId: options.proposedInstanceId,
      proposedInstanceKey: options.proposedInstanceKey,
      source: options.instanceSource || PAX_PROTECTION_ENCOUNTER_INSTANCE_SOURCE
    });
  }

  function classifyBoarderGroup(characterRuntime = currentCharacterRuntime()) {
    return EncounterState.classifyActorGroup({
      actorIds: BOARDER_IDS,
      actorRuntime: characterRuntime,
      actorCollectionKey: "characters",
      entriesKey: "boarders"
    });
  }

  function classifyPaxProtectionState(options = {}) {
    const scenarioRuntime = options.scenarioRuntime
      || uiState.runtime
      || global.MainComputerSystemScenarioRuntime?.current?.()
      || null;
    const characterRuntime = options.characterRuntime
      || currentCharacterRuntime();
    const view = scenarioRuntime?.view?.(SCENARIO_ID) || null;
    const boarderGroup = classifyBoarderGroup(characterRuntime);
    const classification = EncounterState.classifyStagedEncounterState({
      scenarioRuntime,
      actorRuntime: characterRuntime,
      view,
      actorGroup: boarderGroup,
      key: PAX_PROTECTION_ENCOUNTER_KEY,
      definitionId: PAX_PROTECTION_ENCOUNTER_DEFINITION_ID,
      scenarioId: SCENARIO_ID,
      systemId: PAX_SYSTEM_ID,
      actorIds: BOARDER_IDS,
      instanceSource: PAX_PROTECTION_ENCOUNTER_INSTANCE_SOURCE,
      activeStageIds: PAX_PROTECTION_ACTIVE_STAGE_IDS,
      completedStageIds: PAX_PROTECTION_COMPLETED_STAGE_IDS,
      stateLabels: paxProtectionStateLabels(),
      recoveryActions: paxProtectionRecoveryActions()
    });

    return Object.assign({}, classification, {
      scenarioRuntime,
      characterRuntime,
      actorRuntime: characterRuntime,
      view,
      identity: classification.identity || paxProtectionEncounterIdentity({view}),
      instance: classification.instance || paxProtectionEncounterInstanceDescriptor({
        identity: classification.identity || paxProtectionEncounterIdentity({view})
      }),
      boarderGroup,
      actorGroup: boarderGroup
    });
  }

  function paxProtectionReconciliationPlan(classification, options = {}) {
    return EncounterState.reconciliationPlan(classification, Object.assign({}, options, {
      recoveryActions: paxProtectionRecoveryActions()
    }));
  }

  function paxProtectionReconciliationOptions(mode = PAX_RECONCILIATION_MODE.passive, overrides = {}) {
    const selectedMode = stringValue(mode) || PAX_RECONCILIATION_MODE.passive;
    const policy = PAX_RECONCILIATION_POLICY[selectedMode]
      || PAX_RECONCILIATION_POLICY[PAX_RECONCILIATION_MODE.passive];
    return Object.assign({}, policy, objectValue(overrides), {
      mode: selectedMode
    });
  }

  function paxProtectionEncounterSnapshotData(options = {}) {
    const snapshotOptions = objectValue(options);
    const reconciliationOptions = paxProtectionReconciliationOptions(
      snapshotOptions.mode,
      snapshotOptions
    );
    const classification = classifyPaxProtectionState(reconciliationOptions);
    const plan = paxProtectionReconciliationPlan(classification, reconciliationOptions);
    const snapshot = buildPaxProtectionEncounterSnapshot(
      classification,
      plan,
      reconciliationOptions
    );
    return {
      classification,
      plan,
      reconciliationOptions,
      snapshot
    };
  }

  function buildPaxProtectionEncounterSnapshot(
    classification,
    plan,
    reconciliationOptions = {}
  ) {
    const identity = classification.identity || paxProtectionEncounterIdentity({
      view: classification.view,
      stageId: classification.stageId
    });
    const instance = classification.instance || paxProtectionEncounterInstanceDescriptor({
      identity
    });
    const diagnostic = EncounterState.diagnosticSnapshot(classification, plan, {
      identity,
      instance,
      instanceSource: PAX_PROTECTION_ENCOUNTER_INSTANCE_SOURCE
    });
    const completion = diagnostic.completion || EncounterState.completionDiagnostic(
      classification,
      plan,
      {identity}
    );
    const diagnosticInstance = diagnostic.instance || instance;
    const boarders = EncounterState.actorDiagnosticRows(classification.boarderGroup, {
      entriesKey: "boarders"
    });
    const boarderIds = BOARDER_IDS.slice();
    const lastAutomaticRecovery = uiState.lastAutomaticRecovery
      ? Object.assign({}, uiState.lastAutomaticRecovery)
      : null;
    const runtimeAttached = Boolean(classification.scenarioRuntime);
    const characterRuntimeAttached = Boolean(classification.characterRuntime);
    const recoveryInProgress = Boolean(uiState.recoveryInProgress);
    const mode = stringValue(reconciliationOptions.mode)
      || PAX_RECONCILIATION_MODE.passive;

    return Object.assign(
      diagnostic,
      {
        snapshotVersion: PAX_PROTECTION_ENCOUNTER_SNAPSHOT_VERSION,
        snapshotKind: "pax-protection-encounter",
        readOnly: true,
        source: "pax-scenario-interaction",
        mode,
        encounter: identity,
        encounterInstance: diagnosticInstance,
        encounterKey: identity.key,
        encounterDefinitionId: identity.definitionId,
        encounterInstanceId: identity.instanceId,
        encounterInstanceKnown: identity.instanceKnown,
        encounterProposedInstanceId: diagnosticInstance.proposedInstanceId,
        encounterProposedInstanceKey: diagnosticInstance.proposedInstanceKey,
        encounterInstancePlaceholder: diagnosticInstance.placeholder,
        encounterInstanceDurableCommitted: diagnosticInstance.durableCommitted,
        completion,
        completionStatus: completion.status,
        completionTrusted: completion.trusted,
        completionReason: completion.reason,
        staleActorState: completion.staleActorState,
        completionIssueCodes: completion.issueCodes.slice(),
        restartableCompletionCorruption: completion.corruption,
        scenarioId: SCENARIO_ID,
        systemId: PAX_SYSTEM_ID,
        activeStageId: PROTECTION_STAGE_ID,
        completedStageId: INVESTIGATION_STAGE_ID,
        boarderIds,
        boarders,
        actors: {
          ids: boarderIds,
          status: diagnostic.actorStatus,
          total: diagnostic.total,
          activeCount: diagnostic.activeCount,
          defeatedCount: diagnostic.defeatedCount,
          missingCount: diagnostic.missingCount,
          rows: boarders
        },
        runtimes: {
          scenarioAttached: runtimeAttached,
          characterAttached: characterRuntimeAttached
        },
        reconciliation: {
          mode,
          recoverDefeated: Boolean(reconciliationOptions.recoverDefeated),
          inProgress: recoveryInProgress,
          plan: diagnostic.plan,
          lastAutomaticRecovery
        },
        runtimeAttached,
        characterRuntimeAttached,
        recoveryInProgress,
        lastAutomaticRecovery
      }
    );
  }

  function paxProtectionEncounterSnapshot(options = {}) {
    return paxProtectionEncounterSnapshotData(options).snapshot;
  }

  function diagnosePaxProtectionEncounter(options = {}) {
    return paxProtectionEncounterSnapshot(options);
  }

  function performPaxProtectionRecovery(plan, reason, options = {}) {
    if (plan.action === PAX_PROTECTION_RECOVERY.reviveBoarders) {
      uiState.lastHardKickoff = null;
      return forceProtectionEncounterCharacters(reason, {nowMs: nowMs(options)});
    }
    if (plan.action === PAX_PROTECTION_RECOVERY.restartEncounter) {
      return resetProtectionEncounter(reason, {nowMs: nowMs(options)});
    }
    return {forced: false, reset: false, reason: "no-recovery-action"};
  }

  function paxProtectionRecoverySucceeded(plan, result) {
    return EncounterState.recoverySucceeded(plan, result, {
      successKeys: {
        [PAX_PROTECTION_RECOVERY.reviveBoarders]: "forced",
        [PAX_PROTECTION_RECOVERY.restartEncounter]: "reset"
      }
    });
  }

  function reconcilePaxProtectionState(
    reason = PAX_RECONCILIATION_REASON.automatic,
    options = {}
  ) {
    if (uiState.recoveryInProgress) {
      return {recovered: false, reason: "recovery-in-progress"};
    }

    const snapshotData = paxProtectionEncounterSnapshotData(options);
    const classification = snapshotData.classification;
    const plan = snapshotData.plan;
    const snapshot = snapshotData.snapshot;

    if (!plan.recover) {
      return {
        recovered: false,
        reason: plan.reason,
        classification,
        plan,
        snapshot
      };
    }

    uiState.recoveryInProgress = true;
    try {
      const result = performPaxProtectionRecovery(plan, reason, options);
      const recovered = paxProtectionRecoverySucceeded(plan, result);
      uiState.lastAutomaticRecovery = {
        reason: stringValue(reason),
        recovered,
        atMs: nowMs(options),
        classification: classification.status,
        action: plan.action,
        result
      };
      if (recovered && options.markCharacterRuntime !== false) {
        uiState.recoveredCharacterRuntime = classification.characterRuntime;
      }
      return {
        recovered,
        reason: recovered
          ? classification.status
          : result?.reason || "automatic-recovery-failed",
        classification,
        plan,
        result,
        snapshot
      };
    } finally {
      uiState.recoveryInProgress = false;
    }
  }

  function requestProtectionEncounterReconciliation(
    reason = PAX_RECONCILIATION_REASON.automatic,
    mode = PAX_RECONCILIATION_MODE.passive,
    options = {}
  ) {
    return reconcilePaxProtectionState(
      reason,
      paxProtectionReconciliationOptions(mode, Object.assign({reason}, objectValue(options)))
    );
  }

  function setRuntime(runtime) {
    if (uiState.unsubscribe) uiState.unsubscribe();
    uiState.runtime = runtime || null;
    uiState.unsubscribe = runtime?.subscribe?.(() => {
      requestProtectionEncounterReconciliation(
        PAX_RECONCILIATION_REASON.scenarioState,
        PAX_RECONCILIATION_MODE.passive
      );
      render();
    }) || null;
    requestProtectionEncounterReconciliation(
      PAX_RECONCILIATION_REASON.scenarioRuntimeAttach,
      PAX_RECONCILIATION_MODE.startupAttach
    );
    render();
    return uiState.runtime;
  }

  function setCharacterRuntime(runtime) {
    if (uiState.characterUnsubscribe) uiState.characterUnsubscribe();
    uiState.characterRuntime = runtime || null;

    let recovery = {recovered: false, reason: "already-checked"};
    if (runtime && uiState.recoveredCharacterRuntime !== runtime) {
      recovery = requestProtectionEncounterReconciliation(
        PAX_RECONCILIATION_REASON.characterRuntimeAttach,
        PAX_RECONCILIATION_MODE.startupAttach,
        {characterRuntime: runtime}
      );
      /*
       * A failed reconciliation must not mark this character runtime as
       * checked. The scenario runtime may not be attached yet, and a later
       * scenario attach is allowed to recover defeated persisted boarders.
       */
    }

    uiState.characterUnsubscribe = runtime?.subscribe?.(() => {
      syncProtection();
      requestProtectionEncounterReconciliation(
        PAX_RECONCILIATION_REASON.characterState,
        PAX_RECONCILIATION_MODE.passive
      );
      render();
    }) || null;
    render();

    if (recovery.recovered && typeof global.CustomEvent === "function") {
      global.dispatchEvent?.(new global.CustomEvent("main-computer-pax-boarders-recovered", {
        detail: recovery
      }));
    }
    return uiState.characterRuntime;
  }

  function bind() {
    if (uiState.bound || typeof document === "undefined") return;
    const ui = nodes();
    if (!ui.root) return;
    uiState.bound = true;
    ui.briefingAck?.addEventListener("click", () => acknowledgeBriefing());
    ui.hardStartButton?.addEventListener("click", () => {
      const runtime = currentRuntime();
      const view = runtime?.view?.(SCENARIO_ID) || null;
      return startOrRecoverProtectionEncounter(
        "player-hard-start-button",
        {
          allowSystemChange: true,
          restartProtectionEncounter: view?.state?.status === "active"
            && view.state.stageId !== PROTECTION_STAGE_ID
        }
      );
    });
    ui.proceed?.addEventListener("click", () => runUi(() => (
      uiState.runtime.proceedToConference(SCENARIO_ID, {
        nowMs: performance.now()
      })
    )));
    global.addEventListener?.("main-computer-system-scenario-change", render);
    global.addEventListener?.("main-computer-character-ai-change", () => {
      const current = global.MainComputerCharacterAIRuntime?.current?.();
      if (current && current !== uiState.characterRuntime) {
        setCharacterRuntime(current);
      } else {
        syncProtection();
        render();
      }
    });
    setRuntime(global.MainComputerSystemScenarioRuntime?.current?.() || null);
    setCharacterRuntime(global.MainComputerCharacterAIRuntime?.current?.() || null);
    render();
  }

  function paxProtectionDebugState() {
    return {
      bound: Boolean(uiState.bound),
      runtimeAttached: Boolean(uiState.runtime),
      characterRuntimeAttached: Boolean(uiState.characterRuntime),
      recoveredCharacterRuntimeAttached: Boolean(uiState.recoveredCharacterRuntime),
      recoveredCharacterRuntimeMatchesCurrent:
        Boolean(uiState.recoveredCharacterRuntime)
        && uiState.recoveredCharacterRuntime === uiState.characterRuntime,
      recoveryInProgress: Boolean(uiState.recoveryInProgress),
      lastHardKickoff: uiState.lastHardKickoff
        ? Object.assign({}, uiState.lastHardKickoff)
        : null,
      lastAutomaticRecovery: uiState.lastAutomaticRecovery
        ? Object.assign({}, uiState.lastAutomaticRecovery)
        : null,
      hasWorldSnapshot: Boolean(uiState.worldSnapshot)
    };
  }

  /*
   * Public surface rule: top-level exports are constants, runtime attachment,
   * and read-only diagnostics. Mutation-capable helpers stay behind
   * api.commands so UI/debug callers have one explicit command boundary.
   */
  const paxConstants = Object.freeze({
    config: PAX_SCENARIO_CONFIG,
    scenarioId: SCENARIO_ID,
    systemId: PAX_SYSTEM_ID,
    protectionStageId: PROTECTION_STAGE_ID,
    investigationStageId: INVESTIGATION_STAGE_ID,
    conferenceStageId: CONFERENCE_STAGE_ID,
    hardKickoffStageIds: HARD_KICKOFF_STAGE_IDS,
    encounterDefinitionId: PAX_PROTECTION_ENCOUNTER_DEFINITION_ID,
    encounterKey: PAX_PROTECTION_ENCOUNTER_KEY,
    encounterSnapshotVersion: PAX_PROTECTION_ENCOUNTER_SNAPSHOT_VERSION,
    encounterInstanceSource: PAX_PROTECTION_ENCOUNTER_INSTANCE_SOURCE,
    activeStageIds: PAX_PROTECTION_ACTIVE_STAGE_IDS,
    completedStageIds: PAX_PROTECTION_COMPLETED_STAGE_IDS,
    reconciliationModes: PAX_RECONCILIATION_MODE,
    reconciliationReasons: PAX_RECONCILIATION_REASON,
    reconciliationPolicy: PAX_RECONCILIATION_POLICY,
    recoveryActions: PAX_PROTECTION_RECOVERY,
    protectionStates: PAX_PROTECTION_STATE,
    boarderGroupStatus: PAX_BOARDER_GROUP_STATUS,
    boarderIds: Object.freeze(BOARDER_IDS.slice()),
    hardKickoffPositions: HARD_KICKOFF_POSITIONS,
    boarderPositions: BOARDER_POSITIONS
  });

  const paxDiagnostics = Object.freeze({
    snapshot: paxProtectionEncounterSnapshot,
    diagnose: diagnosePaxProtectionEncounter,
    state: paxProtectionDebugState,
    characterRows,
    encounterIdentity: paxProtectionEncounterIdentity,
    encounterInstanceDescriptor: paxProtectionEncounterInstanceDescriptor
  });

  const paxCommands = Object.freeze({
    startOrRecover: startOrRecoverProtectionEncounter,
    requestReconciliation: requestProtectionEncounterReconciliation,
    resetProtectionEncounter,
    forceCharacterStates: forceProtectionEncounterCharacters
  });

  const paxPresentationModel = Object.freeze({
    viewModel: paxProtectionPresentationViewModel,
    presentationViewModel: paxProtectionPresentationViewModel,
    toViewModel: toPaxPresentationViewModel,
    isViewModel: isPaxPresentationViewModel,
    stageStatus,
    objective: objectivePresentation,
    objectivePresentation,
    threat: threatPresentation,
    threatPresentation,
    hardStart: hardStartPresentation,
    hardStartPresentation,
    missionCues: missionCuesPresentation,
    missionCuesPresentation,
    evidence: evidencePresentation,
    evidencePresentation,
    proceed: proceedPresentation,
    proceedPresentation,
    resolutions: resolutionsPresentation,
    resolutionsPresentation,
    outcome: outcomePresentation,
    outcomePresentation,
    characters: charactersPresentation,
    charactersPresentation,
    characterRows,
    requirementText
  });

  const paxPresentationDom = Object.freeze({
    renderCharacters,
    renderEvidence,
    renderResolutions,
    renderOutcome,
    renderMissionCues,
    renderThreatTracker,
    renderHardStart
  });

  const paxPresentation = Object.freeze({
    model: paxPresentationModel,
    dom: paxPresentationDom,
    viewModel: paxProtectionPresentationViewModel,
    presentationViewModel: paxProtectionPresentationViewModel,
    objective: objectivePresentation,
    objectivePresentation,
    threat: threatPresentation,
    threatPresentation,
    hardStart: hardStartPresentation,
    hardStartPresentation,
    requirementText,
    renderMissionCues,
    renderThreatTracker,
    renderHardStart
  });

  const paxRuntime = Object.freeze({
    setRuntime,
    setCharacterRuntime,
    handleNavigation,
    setWorldSnapshot,
    bind,
    render
  });

  const api = {
    SCENARIO_ID,
    PAX_SYSTEM_ID,
    PAX_PROTECTION_ENCOUNTER_DEFINITION_ID,
    PAX_PROTECTION_ENCOUNTER_KEY,
    PAX_PROTECTION_ENCOUNTER_SNAPSHOT_VERSION,
    constants: paxConstants,
    config: PAX_SCENARIO_CONFIG,
    diagnostics: paxDiagnostics,
    commands: paxCommands,
    presentation: paxPresentation,
    runtime: paxRuntime,
    paxProtectionEncounterSnapshot,
    diagnosePaxProtectionEncounter,
    setRuntime,
    setCharacterRuntime,
    handleNavigation,
    setWorldSnapshot,
    bind,
    hardKickoffPositions: HARD_KICKOFF_POSITIONS,
    boarderIds: Object.freeze(BOARDER_IDS.slice())
  };

  global.MainComputerPaxScenarioInteraction = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") bind();
})(typeof globalThis !== "undefined" ? globalThis : window);
