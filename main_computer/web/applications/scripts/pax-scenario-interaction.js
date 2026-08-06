(function (global) {
  "use strict";

  const SCENARIO_ID = "scenario.pax.neutrality-under-fire";
  const PAX_SYSTEM_ID = "system.pax";
  const BRIEFING_ACK_PREFIX = "main-computer.pax-scenario.briefing-ack.v1";
  const PROTECTION_STAGE_ID = "protect-witness";
  const HARD_KICKOFF_STAGE_IDS = Object.freeze([
    "protect-witness",
    "investigation",
    "conference"
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

  function renderCharacters(container, view) {
    if (!container) return;
    container.replaceChildren();
    const rows = characterRows(view);
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

  function renderEvidence(container, view) {
    if (!container) return;
    container.replaceChildren();
    arrayValue(view?.evidence).forEach((evidence) => {
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

  function renderResolutions(container, view) {
    if (!container) return;
    container.replaceChildren();
    arrayValue(view?.resolutions).forEach((resolution) => {
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

  function renderOutcome(container, view) {
    if (!container) return;
    container.replaceChildren();
    const consequences = objectValue(view?.state?.consequences);
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
    if (state.stageId === "protect-witness") {
      return "HOSTILE BOARDING DETECTED. Six Quiet Service boarders are aboard. Repel every attacker.";
    }
    if (state.stageId === "investigation") {
      return "Boarding party eliminated. Their command data and weapon records were recovered automatically.";
    }
    if (state.stageId === "conference") {
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
    if (state.status === "active" && state.stageId === "protect-witness") {
      return {
        visible: true,
        kicker: "PAX PRIORITY ONE",
        title: "REPEL THE BOARDERS",
        detail: "SIX HOSTILES ABOARD • CLEAR THE BRIDGE AND AFT BREACH • PROTECT THE CREW",
        urgent: true
      };
    }
    if (state.status === "active" && state.stageId === "investigation") {
      return {
        visible: true,
        kicker: "BOARDING PARTY ELIMINATED",
        title: "RECOVERED COMMAND DATA",
        detail: "INTELLIGENCE SECURED AUTOMATICALLY • OPEN THE EMERGENCY CONFERENCE",
        urgent: false
      };
    }
    if (state.status === "active" && state.stageId === "conference") {
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

  function renderThreatTracker(ui, view) {
    const threat = threatPresentation(view);
    if (ui.threatTracker) {
      ui.threatTracker.hidden = !threat.visible;
      ui.threatTracker.dataset.scenarioStage = stringValue(view?.state?.stageId);
      ui.threatTracker.dataset.threatVisible = threat.visible ? "true" : "false";
      ui.threatTracker.dataset.threatHealth = String(threat.health);
    }
    if (ui.threatName) ui.threatName.textContent = threat.name;
    if (ui.threatDetail) ui.threatDetail.textContent = threat.detail;
    if (ui.threatAction) ui.threatAction.textContent = threat.action;
    return threat;
  }

  function renderMissionCues(ui, view) {
    const state = objectValue(view?.state);
    const cueKey = briefingCueKey(view);
    const showBriefing = state.status === "active"
      && state.stageId === "protect-witness"
      && !briefingAcknowledged(cueKey);
    if (ui.briefing) {
      ui.briefing.hidden = !showBriefing;
      ui.briefing.dataset.cueKey = cueKey;
      ui.briefing.dataset.scenarioStage = stringValue(state.stageId);
    }

    const objective = objectivePresentation(view);
    if (ui.objective) {
      ui.objective.hidden = !objective.visible;
      ui.objective.dataset.scenarioStage = stringValue(state.stageId);
      ui.objective.dataset.urgent = objective.urgent ? "true" : "false";
    }
    if (ui.objectiveKicker) ui.objectiveKicker.textContent = objective.kicker;
    if (ui.objectiveTitle) ui.objectiveTitle.textContent = objective.title;
    if (ui.objectiveDetail) ui.objectiveDetail.textContent = objective.detail;
    return {showBriefing, cueKey, objective};
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

  function forcePaxCharacterStates(reason = "pax-hard-kickoff", options = {}) {
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
        button: protection ? "Respawn visible encounter" : "Refresh Pax status"
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

  function renderHardStart(ui, view) {
    const presentation = hardStartPresentation(view);
    if (ui.hardStart) {
      ui.hardStart.hidden = !presentation.visible;
      ui.hardStart.dataset.scenarioStatus = stringValue(view?.state?.status);
      ui.hardStart.dataset.scenarioStage = stringValue(view?.state?.stageId);
    }
    if (ui.hardStartTitle) ui.hardStartTitle.textContent = presentation.title;
    if (ui.hardStartDetail) ui.hardStartDetail.textContent = presentation.detail;
    if (ui.hardStartButton) {
      ui.hardStartButton.textContent = presentation.button;
      ui.hardStartButton.disabled = uiState.running
        || stringValue(view?.state?.status) === "resolved";
      ui.hardStartButton.hidden = stringValue(view?.state?.status) === "resolved";
    }
    return presentation;
  }

  function startOrRecoverPax(reason = "pax-hard-kickoff", options = {}) {
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
      forced = forcePaxCharacterStates(reason, {nowMs: clock});
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
        forced = forcePaxCharacterStates("navigation-recovered-protection", {
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
    const forced = forcePaxCharacterStates("navigation-arrival-hard-kickoff", {
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
        const recovered = startOrRecoverPax(
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
      forcePaxCharacterStates("visible-protection-hard-kickoff", {nowMs: nowMs({})});
    }

    ui.root.hidden = !view.visible;
    renderHardStart(ui, view);
    if (!view.visible) return view;
    state = objectValue(view.state);
    const stage = objectValue(view.stage);
    ui.root.dataset.scenarioStatus = state.status;
    ui.root.dataset.scenarioStage = state.stageId;
    renderMissionCues(ui, view);
    renderThreatTracker(ui, view);

    if (ui.status) ui.status.textContent = stageStatus(view);
    if (ui.stageTitle) ui.stageTitle.textContent = stage.label || state.stageId;
    if (ui.stageDescription) ui.stageDescription.textContent = stage.description || "";
    if (ui.localRule) {
      const metrics = objectValue(state.metrics);
      const discharges = Number(metrics.weaponDischarges || 0);
      const intimidation = Number(metrics.intimidationDischarges || 0);
      const conduct = discharges
        ? ` • ${discharges} weapon discharges, ${intimidation} classed as intimidation`
        : "";
      ui.localRule.textContent = `Local rule: ${view.definition.localRule}${conduct}`;
    }
    if (ui.vessel) ui.vessel.textContent = `Enemy ship: ${view.vesselStatus || "status unknown"}`;

    renderCharacters(ui.characters, view);

    const evidenceVisible = state.status === "active"
      && ["investigation", "conference"].includes(state.stageId);
    if (ui.evidence) ui.evidence.hidden = !evidenceVisible;
    if (evidenceVisible) renderEvidence(ui.evidenceList, view);
    if (ui.proceed) {
      ui.proceed.hidden = state.stageId !== "investigation";
      ui.proceed.disabled = uiState.running || state.evidenceIds.length < 2;
    }

    const resolutionVisible = state.status === "active" && state.stageId === "conference";
    if (ui.resolutions) ui.resolutions.hidden = !resolutionVisible;
    if (resolutionVisible) renderResolutions(ui.resolutionList, view);

    if (ui.outcome) {
      ui.outcome.hidden = state.status !== "resolved";
      if (state.status === "resolved") renderOutcome(ui.outcome, view);
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

  function setRuntime(runtime) {
    if (uiState.unsubscribe) uiState.unsubscribe();
    uiState.runtime = runtime || null;
    uiState.unsubscribe = runtime?.subscribe?.(() => render()) || null;
    render();
    return uiState.runtime;
  }


  function recoverDefeatedProtectionBoardersOnAttach(runtime) {
    if (!runtime || uiState.recoveredCharacterRuntime === runtime) {
      return {recovered: false, reason: "already-checked"};
    }
    uiState.recoveredCharacterRuntime = runtime;

    const scenarioRuntime = uiState.runtime
      || global.MainComputerSystemScenarioRuntime?.current?.()
      || null;
    const view = scenarioRuntime?.view?.(SCENARIO_ID) || null;
    if (!view?.visible
        || view.state?.status !== "active"
        || view.state?.stageId !== PROTECTION_STAGE_ID) {
      return {recovered: false, reason: "protection-not-active"};
    }

    const snapshot = runtime.snapshot?.() || {};
    const characters = objectValue(snapshot.characters);
    const boarders = BOARDER_IDS.map((characterId) => characters[characterId] || null);
    const allPersistedDown = boarders.length === BOARDER_IDS.length
      && boarders.every((character) => character
        && (character.status === "down" || Number(character.health) <= 0));

    if (!allPersistedDown) {
      return {recovered: false, reason: "boarders-not-all-down"};
    }

    uiState.lastHardKickoff = null;
    const result = forcePaxCharacterStates(
      "character-runtime-attach-defeated-boarder-recovery",
      {nowMs: nowMs({})}
    );
    return {
      recovered: Boolean(result?.forced),
      reason: result?.forced ? "persisted-defeated-boarders-reset" : "force-failed",
      result
    };
  }

  function setCharacterRuntime(runtime) {
    if (uiState.characterUnsubscribe) uiState.characterUnsubscribe();
    uiState.characterRuntime = runtime || null;
    const recovery = recoverDefeatedProtectionBoardersOnAttach(runtime);
    uiState.characterUnsubscribe = runtime?.subscribe?.(() => {
      syncProtection();
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
    ui.hardStartButton?.addEventListener("click", () => startOrRecoverPax(
      "player-hard-start-button",
      {allowSystemChange: true}
    ));
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

  const api = {
    SCENARIO_ID,
    PAX_SYSTEM_ID,
    setRuntime,
    setCharacterRuntime,
    handleNavigation,
    arrivalActivationKey,
    isDurableCommittedArrival,
    isLegacyPaxOccupancy,
    legacyActivationKey,
    briefingCueKey,
    briefingAcknowledged,
    acknowledgeBriefing,
    objectivePresentation,
    renderMissionCues,
    renderThreatTracker,
    threatPresentation,
    renderHardStart,
    hardStartPresentation,
    setWorldSnapshot,
    forcePaxCharacterStates,
    startOrRecoverPax,
    hardKickoffPositions: HARD_KICKOFF_POSITIONS,
    boarderIds: BOARDER_IDS,
    syncProtection,
    characterRows,
    requirementText,
    render,
    bind,
    state: uiState
  };

  global.MainComputerPaxScenarioInteraction = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") bind();
})(typeof globalThis !== "undefined" ? globalThis : window);
