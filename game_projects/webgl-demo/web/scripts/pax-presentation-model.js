(function (global) {
  "use strict";

  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);
  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);
  const DEFAULT_CONFIG = PaxScenarioConfig?.config || PaxScenarioConfig?.PAX_SCENARIO_CONFIG || null;

  if (!DEFAULT_CONFIG?.ids?.scenarioId) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax presentation model.");
  }
  if (!PaxValueUtils?.objectValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax presentation model.");
  }

  const {
    objectValue,
    arrayValue,
    stringValue,
    finiteNumber,
    vector3
  } = PaxValueUtils;

  function configValue(options = {}) {
    return options.config || DEFAULT_CONFIG;
  }

  function configAliases(options = {}) {
    const config = configValue(options);
    return {
      config,
      scenarioId: config.ids.scenarioId,
      systemId: config.ids.systemId,
      protectionStageId: config.stages.protection,
      investigationStageId: config.stages.investigation,
      conferenceStageId: config.stages.conference,
      hardKickoffStageIds: config.stages.hardKickoff,
      boarderIds: config.actors.boarderIds,
      hardKickoffPositions: config.actors.hardKickoffPositions
    };
  }

  function consequenceLabel(key) {
    return String(key || "")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/[-_]/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function scenarioStartReceipt(view) {
    return arrayValue(view?.state?.receipts)
      .find((receipt) => stringValue(receipt?.reason) === "scenario-started") || null;
  }

  function briefingCueKey(view, options = {}) {
    const {scenarioId} = configAliases(options);
    const receipt = scenarioStartReceipt(view);
    return stringValue(
      receipt?.activationKey
      || receipt?.receiptId
      || view?.state?.startedAtMs
      || `${scenarioId}:active`
    );
  }

  function characterRows(view, options = {}) {
    const ids = arrayValue(view?.definition?.characterIds);
    const runtime = options.characterRuntime
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

  function stageStatus(view, options = {}) {
    const {protectionStageId, investigationStageId, conferenceStageId} = configAliases(options);
    const state = objectValue(view?.state);
    const stage = objectValue(view?.stage);
    const lastError = stringValue(options.lastError);
    if (lastError) return lastError;
    if (state.status === "resolved") {
      return `${view.resolution?.label || "Pax settlement adopted"}.`;
    }
    if (state.status === "available") {
      return "Pax arrival trigger armed. The emergency protection detail begins when system arrival commits.";
    }
    if (state.stageId === protectionStageId) {
      return "HOSTILE BOARDING DETECTED. Six Quiet Service boarders are aboard. Repel every attacker.";
    }
    if (state.stageId === investigationStageId) {
      return "Boarding party eliminated. Their command data and weapon records were recovered automatically.";
    }
    if (state.stageId === conferenceStageId) {
      return "The emergency conference is open. Choose a settlement supported by the evidence.";
    }
    return stage.label || "Pax scenario active.";
  }

  function objectivePresentation(view, options = {}) {
    const {protectionStageId, investigationStageId, conferenceStageId} = configAliases(options);
    const state = objectValue(view?.state);
    const evidenceCount = arrayValue(state.evidenceIds).length;
    const evidenceTotal = arrayValue(view?.evidence).length;
    if (state.status === "active" && state.stageId === protectionStageId) {
      return {
        visible: true,
        kicker: "PAX PRIORITY ONE",
        title: "REPEL THE BOARDERS",
        detail: "SIX HOSTILES ABOARD • CLEAR THE BRIDGE AND AFT BREACH • PROTECT THE CREW",
        urgent: true
      };
    }
    if (state.status === "active" && state.stageId === investigationStageId) {
      return {
        visible: true,
        kicker: "BOARDING PARTY ELIMINATED",
        title: "RECOVERED COMMAND DATA",
        detail: "INTELLIGENCE SECURED AUTOMATICALLY • OPEN THE EMERGENCY CONFERENCE",
        urgent: false
      };
    }
    if (state.status === "active" && state.stageId === conferenceStageId) {
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

  function activeBoardersFromRuntime(options = {}) {
    const {boarderIds} = configAliases(options);
    const runtime = options.characterRuntime
      || global.MainComputerCharacterAIRuntime?.current?.()
      || null;
    return boarderIds.map((id) => runtime?.character?.(id) || null)
      .filter((character) => (
        character
        && stringValue(character.status) === "active"
        && finiteNumber(character.health, 0) > 0
      ));
  }

  function activeBoardersFromSnapshot(snapshot = {}, options = {}) {
    const {boarderIds} = configAliases(options);
    const characters = arrayValue(snapshot.characters);
    return characters.filter((character) => (
      boarderIds.includes(stringValue(character.id))
      && stringValue(character.status) === "active"
      && finiteNumber(character.health, 0) > 0
    ));
  }

  function directionText(playerPosition, attackerPosition, options = {}) {
    const {hardKickoffPositions} = configAliases(options);
    const player = vector3(playerPosition, [0, 0, -36.7]);
    const attacker = vector3(attackerPosition, hardKickoffPositions.assassin);
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

  function threatPresentation(view, snapshot = {}, options = {}) {
    const {protectionStageId} = configAliases(options);
    const state = objectValue(view?.state);
    if (state.status !== "active" || state.stageId !== protectionStageId) {
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
    const boarders = activeBoardersFromSnapshot(world, options);
    const active = boarders.length ? boarders : activeBoardersFromRuntime(options);
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
    const direction = directionText(playerPosition, nearest.position, options);
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

  function hardStartPresentation(view, options = {}) {
    const {protectionStageId, hardKickoffStageIds} = configAliases(options);
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
    if (state.status === "active" && hardKickoffStageIds.includes(state.stageId)) {
      const protection = state.stageId === protectionStageId;
      return {
        visible: true,
        title: protection ? "PAX MISSION LIVE" : "PAX MISSION ACTIVE",
        detail: protection
          ? "Six hostile boarders are spread across the bridge and aft breach. Eliminate every marked hostile."
          : stageStatus(view, options),
        button: protection ? "Respawn visible encounter" : "Restart boarding encounter"
      };
    }
    if (state.status === "resolved") {
      return {
        visible: true,
        title: "PAX RESOLVED",
        detail: stageStatus(view, options),
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

  function paxProtectionPresentationViewModel(view, options = {}) {
    const {
      scenarioId,
      systemId,
      protectionStageId,
      investigationStageId,
      conferenceStageId
    } = configAliases(options);
    const state = objectValue(view?.state);
    const stage = objectValue(view?.stage);
    const status = stringValue(state.status);
    const stageId = stringValue(state.stageId);
    const evidenceIds = arrayValue(state.evidenceIds);
    const evidenceItems = arrayValue(view?.evidence);
    const resolutionItems = arrayValue(view?.resolutions);
    const metrics = objectValue(state.metrics);
    const discharges = Number(metrics.weaponDischarges || 0);
    const intimidation = Number(metrics.intimidationDischarges || 0);
    const conduct = discharges
      ? ` • ${discharges} weapon discharges, ${intimidation} classed as intimidation`
      : "";
    const cueKey = briefingCueKey(view, options);
    const briefingAcknowledged = typeof options.briefingAcknowledged === "function"
      ? options.briefingAcknowledged
      : () => false;
    const worldSnapshot = Object.prototype.hasOwnProperty.call(options, "worldSnapshot")
      ? options.worldSnapshot
      : null;
    const modelOptions = Object.assign({}, options, {
      lastError: options.lastError,
      characterRuntime: options.characterRuntime
    });
    const objective = objectivePresentation(view, modelOptions);
    const hardStart = hardStartPresentation(view, modelOptions);
    const threat = threatPresentation(view, worldSnapshot, modelOptions);
    const evidenceVisible = status === "active"
      && [investigationStageId, conferenceStageId].includes(stageId);
    const resolutionVisible = status === "active" && stageId === conferenceStageId;
    const outcomeVisible = status === "resolved";
    const visible = Boolean(view?.visible);
    const statusText = stageStatus(view, modelOptions);
    const running = Boolean(options.running);

    return {
      snapshotVersion: "pax-protection-presentation-view-model.v1",
      snapshotKind: "pax-protection-presentation",
      readOnly: true,
      visible,
      scenario: {
        id: scenarioId,
        systemId,
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
      missionCues: {
        briefing: {
          visible: status === "active"
            && stageId === protectionStageId
            && !briefingAcknowledged(cueKey),
          cueKey,
          scenarioStage: stageId
        },
        objective
      },
      objective,
      threat,
      hardStart: Object.assign({}, hardStart, {
        buttonDisabled: running || status === "resolved",
        buttonHidden: status === "resolved"
      }),
      characters: {
        rows: characterRows(view, modelOptions)
      },
      evidence: {
        visible: evidenceVisible,
        items: evidenceItems
      },
      proceed: {
        visible: stageId === investigationStageId,
        disabled: running || evidenceIds.length < 2
      },
      resolutions: {
        visible: resolutionVisible,
        items: resolutionItems
      },
      outcome: {
        visible: outcomeVisible,
        consequences: objectValue(state.consequences)
      }
    };
  }

  const api = Object.freeze({
    objectValue,
    arrayValue,
    stringValue,
    finiteNumber,
    vector3,
    consequenceLabel,
    scenarioStartReceipt,
    briefingCueKey,
    characterRows,
    requirementText,
    stageStatus,
    objectivePresentation,
    activeBoardersFromRuntime,
    activeBoardersFromSnapshot,
    directionText,
    threatPresentation,
    hardStartPresentation,
    isPaxPresentationViewModel,
    toPaxPresentationViewModel,
    paxProtectionPresentationViewModel,
    viewModel: paxProtectionPresentationViewModel
  });

  global.MainComputerPaxPresentationModel = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
