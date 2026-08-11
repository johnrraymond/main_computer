(function (global) {
  "use strict";

  const VELA_SYSTEM_ID = "system.vela-gate";
  const VELA_OPPORTUNITY_ID = "opportunity.campaign.vela-gate-intervention";
  const OFFICIAL_ACTOR_ID = "actor.vela.gate-official";
  const ORGANIZER_ACTOR_ID = "actor.vela.rescue-organizer";
  const SURVIVOR_ACTOR_ID = "actor.vela.survivor";
  const BRIEFING_INTENT_ID = "communicative-intent.vela.official-customs-briefing";
  const VELA_ESCAPE_SCENARIO_ID = "scenario.vela.underground-captivity-escape";
  const VELA_ESCAPE_ENCOUNTER_ID = "encounter.vela.subsurface-captive-breakout";
  const VELA_ESCAPE_LOCATION_ID = "destination.vela-gate.subsurface-cavern";
  const VELA_SURFACE_TRANSPORTER_ID = "destination.vela-gate.surface-transporter";
  const VELA_ESCAPE_STAGE_IDS = Object.freeze({
    arrival: "arrival-hold",
    investigation: "investigation-trigger",
    captive: "captive-under-surface",
    breakout: "hand-to-hand-breakout",
    extraction: "surface-transporter-extraction",
    complete: "beam-back-complete"
  });
  const VELA_ESCAPE_STAGE_LABELS = Object.freeze({
    [VELA_ESCAPE_STAGE_IDS.arrival]: "Arrival hold",
    [VELA_ESCAPE_STAGE_IDS.investigation]: "Investigation trigger",
    [VELA_ESCAPE_STAGE_IDS.captive]: "Captive beneath Vela Gate",
    [VELA_ESCAPE_STAGE_IDS.breakout]: "Hand-to-hand breakout",
    [VELA_ESCAPE_STAGE_IDS.extraction]: "Fight to the surface transporter",
    [VELA_ESCAPE_STAGE_IDS.complete]: "Beam-back complete"
  });
  const VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER = Object.freeze({
    id: "built-in.vela-gate.subsurface-cave-escape",
    source: "built-in",
    registryVersion: "gameplay-template-registry.v1",
    templateId: "encounter-template.cave-combat-run",
    templateTitle: "Cave Combat Run",
    scenarioId: VELA_ESCAPE_SCENARIO_ID,
    encounterId: VELA_ESCAPE_ENCOUNTER_ID,
    systemId: VELA_SYSTEM_ID,
    destinationId: VELA_ESCAPE_LOCATION_ID,
    objectiveTypeIds: Object.freeze([
      "objective-type.escape-captivity",
      "objective-type.recover-item",
      "objective-type.clear-hostiles",
      "objective-type.reach-extraction"
    ]),
    actorArchetypeIds: Object.freeze([
      "actor-archetype.vela-cave-guard"
    ]),
    consequenceTypeIds: Object.freeze([
      "consequence-type.record-receipt",
      "consequence-type.unlock-route"
    ])
  });
  const VELA_CAVE_ROOMS = Object.freeze([
    {id: "vela-cave.holding-ledge", label: "Holding Ledge", position: [0, -0.55, 3.05], kind: "start"},
    {id: "vela-cave.guard-post", label: "Guard Post", position: [1.85, -0.55, 0.85], kind: "weapon-recovery"},
    {id: "vela-cave.crystal-narrows", label: "Crystal Narrows", position: [-2.6, -0.55, -2.6], kind: "passage"},
    {id: "vela-cave.supply-hollow", label: "Supply Hollow", position: [2.8, -0.55, -4.85], kind: "ambush"},
    {id: "vela-cave.generator-grotto", label: "Generator Grotto", position: [-3.1, -0.55, -7.6], kind: "route-control"},
    {id: "vela-cave.surface-transporter-room", label: "Surface Transporter Room", position: [0, -0.55, -12.65], kind: "extraction"}
  ]);
  const VELA_CAVE_HOSTILES = Object.freeze([
    {id: "enemy.vela.cave-guard-02", label: "Cave guard", roomId: "vela-cave.crystal-narrows", position: [-2.45, -0.03, -2.15]},
    {id: "enemy.vela.cave-guard-03", label: "Cave guard", roomId: "vela-cave.crystal-narrows", position: [-1.35, -0.03, -3.0]},
    {id: "enemy.vela.cave-guard-04", label: "Supply guard", roomId: "vela-cave.supply-hollow", position: [2.3, -0.03, -4.25]},
    {id: "enemy.vela.cave-guard-05", label: "Supply guard", roomId: "vela-cave.supply-hollow", position: [3.35, -0.03, -5.45]},
    {id: "enemy.vela.cave-guard-06", label: "Generator guard", roomId: "vela-cave.generator-grotto", position: [-3.45, -0.03, -7.05]},
    {id: "enemy.vela.cave-guard-07", label: "Generator guard", roomId: "vela-cave.generator-grotto", position: [-2.15, -0.03, -8.15]},
    {id: "enemy.vela.cave-guard-08", label: "Transporter guard", roomId: "vela-cave.surface-transporter-room", position: [-0.75, -0.03, -11.85]},
    {id: "enemy.vela.cave-guard-09", label: "Transporter guard", roomId: "vela-cave.surface-transporter-room", position: [0.85, -0.03, -12.85]}
  ]);
  const velaEscapeScenarioStateBySession = new WeakMap();

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

  function finiteNumber(value, fallback = 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function finiteVector3(value, fallback = [0, 0, 0]) {
    const source = Array.isArray(value) ? value : fallback;
    return [0, 1, 2].map((index) => finiteNumber(source[index], fallback[index] || 0));
  }

  function defaultVelaCaveSystem() {
    const rooms = VELA_CAVE_ROOMS.map((room, index) => ({
      id: stringValue(room.id),
      label: stringValue(room.label),
      kind: stringValue(room.kind),
      order: index + 1,
      position: finiteVector3(room.position)
    }));
    const enemies = VELA_CAVE_HOSTILES.map((enemy) => ({
      id: stringValue(enemy.id),
      label: stringValue(enemy.label),
      roomId: stringValue(enemy.roomId),
      position: finiteVector3(enemy.position),
      health: 1,
      status: "active"
    }));
    return {
      templateId: VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER.templateId,
      templateConsumerId: VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER.id,
      objectiveTypeIds: clone(VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER.objectiveTypeIds),
      actorArchetypeIds: clone(VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER.actorArchetypeIds),
      consequenceTypeIds: clone(VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER.consequenceTypeIds),
      rooms,
      roomCount: rooms.length,
      enemies,
      enemiesTotal: enemies.length,
      enemiesActive: enemies.length,
      enemiesDefeated: 0,
      transporterRoomId: "vela-cave.surface-transporter-room",
      transporterPosition: finiteVector3(
        VELA_CAVE_ROOMS.find((room) => room.id === "vela-cave.surface-transporter-room")?.position,
        [0, -0.55, -12.65]
      )
    };
  }

  function normalizeVelaCaveSystem(record) {
    const base = defaultVelaCaveSystem();
    const source = objectValue(record);
    const sourceRooms = arrayValue(source.rooms);
    const rooms = sourceRooms.length
      ? sourceRooms.map((room, index) => ({
        id: stringValue(objectValue(room).id || base.rooms[index]?.id),
        label: stringValue(objectValue(room).label || base.rooms[index]?.label || "Cave room"),
        kind: stringValue(objectValue(room).kind || base.rooms[index]?.kind || "cave"),
        order: finiteNumber(objectValue(room).order, index + 1),
        position: finiteVector3(objectValue(room).position, base.rooms[index]?.position || [0, -0.55, 0])
      }))
      : base.rooms;
    const knownEnemies = new Map(base.enemies.map((enemy) => [enemy.id, enemy]));
    const sourceEnemies = arrayValue(source.enemies);
    const enemies = sourceEnemies.length
      ? sourceEnemies.map((enemy, index) => {
        const record = objectValue(enemy);
        const fallback = knownEnemies.get(stringValue(record.id)) || base.enemies[index] || {};
        const health = Math.max(0, finiteNumber(record.health, finiteNumber(fallback.health, 1)));
        const status = stringValue(record.status || fallback.status || (health > 0 ? "active" : "defeated"));
        return {
          id: stringValue(record.id || fallback.id),
          label: stringValue(record.label || fallback.label || "Cave hostile"),
          roomId: stringValue(record.roomId || fallback.roomId),
          position: finiteVector3(record.position, fallback.position || [0, -0.03, 0]),
          health,
          status: status === "defeated" || health <= 0 ? "defeated" : "active"
        };
      })
      : base.enemies;
    const enemiesActive = enemies.filter((enemy) => enemy.status !== "defeated" && finiteNumber(enemy.health, 1) > 0).length;
    const enemiesDefeated = enemies.length - enemiesActive;
    return {
      templateId: stringValue(source.templateId || source.encounterTemplateId || base.templateId),
      templateConsumerId: stringValue(source.templateConsumerId || base.templateConsumerId),
      objectiveTypeIds: arrayValue(source.objectiveTypeIds).length
        ? clone(arrayValue(source.objectiveTypeIds).map((id) => stringValue(id)).filter(Boolean))
        : clone(base.objectiveTypeIds),
      actorArchetypeIds: arrayValue(source.actorArchetypeIds).length
        ? clone(arrayValue(source.actorArchetypeIds).map((id) => stringValue(id)).filter(Boolean))
        : clone(base.actorArchetypeIds),
      consequenceTypeIds: arrayValue(source.consequenceTypeIds).length
        ? clone(arrayValue(source.consequenceTypeIds).map((id) => stringValue(id)).filter(Boolean))
        : clone(base.consequenceTypeIds),
      rooms,
      roomCount: rooms.length,
      enemies,
      enemiesTotal: enemies.length,
      enemiesActive,
      enemiesDefeated,
      transporterRoomId: stringValue(source.transporterRoomId || base.transporterRoomId),
      transporterPosition: finiteVector3(source.transporterPosition, base.transporterPosition)
    };
  }

  function titleWords(value) {
    return stringValue(value)
      .replace(/^[^.]+\.[^.]+\./, "")
      .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
      .replace(/[._-]+/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  const SIGNAL_LABELS = Object.freeze({
    goalPriority: "Mission priorities",
    evidenceSupport: "Available evidence",
    uncertainty: "Remaining uncertainty",
    memoryRelevance: "Relevant history",
    observationReliability: "Source reliability",
    captainCooperation: "Expected cooperation",
    captainEvidenceDiscipline: "Evidence discipline",
    captainAuthorityResistance: "Authority resistance",
    commitmentTrust: "Commitment trust"
  });

  function signalLabel(id) {
    return SIGNAL_LABELS[stringValue(id)] || titleWords(id);
  }

  function signalAssessment(value) {
    const amount = finiteNumber(value);
    if (amount < 0) return "Reduced confidence";
    if (Math.abs(amount) >= 0.15) return "Major influence";
    if (Math.abs(amount) >= 0.08) return "Meaningful influence";
    return "Supporting influence";
  }

  function alternativeAssessment(score, selectedScore) {
    const selected = Math.max(0.000001, finiteNumber(selectedScore));
    const ratio = finiteNumber(score) / selected;
    if (ratio >= 0.7) return "Strong alternative";
    if (ratio >= 0.5) return "Viable alternative";
    return "Lower-confidence option";
  }

  function resourceLabel(resourceId, amount = 1) {
    const base = titleWords(resourceId).toLowerCase();
    return `${finiteNumber(amount)} ${base}${finiteNumber(amount) === 1 ? "" : "s"}`;
  }

  function indexById(records, key = "id") {
    return new Map(
      arrayValue(records)
        .filter((record) => stringValue(objectValue(record)[key]))
        .map((record) => [stringValue(objectValue(record)[key]), record])
    );
  }

  function velaActiveSystemId(session) {
    return stringValue(session?.summary?.().activeSystemId);
  }

  function velaEscapeScenarioPhaseForStage(stageId) {
    const stage = stringValue(stageId || VELA_ESCAPE_STAGE_IDS.arrival);
    if (stage === VELA_ESCAPE_STAGE_IDS.captive) return "underground-captive";
    if (stage === VELA_ESCAPE_STAGE_IDS.breakout) return "hand-to-hand-breakout";
    if (stage === VELA_ESCAPE_STAGE_IDS.extraction) return "surface-transporter-run";
    if (stage === VELA_ESCAPE_STAGE_IDS.complete) return "complete";
    if (stage === VELA_ESCAPE_STAGE_IDS.investigation) return "investigation-active";
    return "investigation-ready";
  }

  function defaultVelaEscapeScenarioSnapshot(session) {
    const visible = velaActiveSystemId(session) === VELA_SYSTEM_ID;
    return {
      snapshotVersion: 1,
      snapshotKind: "vela-underground-escape-scenario",
      scenarioId: VELA_ESCAPE_SCENARIO_ID,
      encounterId: VELA_ESCAPE_ENCOUNTER_ID,
      encounterTemplateId: VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER.templateId,
      gameplayTemplate: clone(VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER),
      systemId: VELA_SYSTEM_ID,
      activeSystemId: velaActiveSystemId(session),
      visible,
      active: false,
      phase: visible ? "investigation-ready" : "away",
      stageId: visible ? VELA_ESCAPE_STAGE_IDS.arrival : "",
      stageLabel: visible ? VELA_ESCAPE_STAGE_LABELS[VELA_ESCAPE_STAGE_IDS.arrival] : "",
      locationId: visible ? VELA_SYSTEM_ID : "",
      locationLabel: visible ? "Vela Gate" : "",
      objective: visible
        ? "Start the Vela Gate investigation."
        : "Reach Vela Gate to begin the investigation.",
      trapTriggered: false,
      captive: false,
      investigationComplete: false,
      playerEquipment: {
        phaser: "available",
        melee: "available"
      },
      guardState: {
        total: 0,
        watching: 0,
        combatMode: "none"
      },
      extraction: {
        destinationId: VELA_SURFACE_TRANSPORTER_ID,
        surfaceTransporter: "not-reached",
        beamBack: "locked"
      },
      caveSystem: defaultVelaCaveSystem(),
      trigger: null,
      notes: visible
        ? ["Investigation has not yet sprung the trap."]
        : ["Vela Gate escape scenario is unavailable outside Vela Gate."]
    };
  }

  function normalizeVelaEscapeScenarioSnapshot(session, snapshot) {
    const base = defaultVelaEscapeScenarioSnapshot(session);
    const record = objectValue(snapshot);
    const stageId = stringValue(record.stageId || base.stageId);
    const phase = stringValue(record.phase || velaEscapeScenarioPhaseForStage(stageId));
    const trapTriggered = Boolean(record.trapTriggered || stageId === VELA_ESCAPE_STAGE_IDS.captive);
    const captive = Boolean(record.captive || stageId === VELA_ESCAPE_STAGE_IDS.captive);
    return {
      ...base,
      ...record,
      snapshotVersion: 1,
      snapshotKind: "vela-underground-escape-scenario",
      scenarioId: VELA_ESCAPE_SCENARIO_ID,
      encounterId: VELA_ESCAPE_ENCOUNTER_ID,
      encounterTemplateId: VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER.templateId,
      gameplayTemplate: clone(VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER),
      systemId: VELA_SYSTEM_ID,
      activeSystemId: velaActiveSystemId(session),
      visible: velaActiveSystemId(session) === VELA_SYSTEM_ID,
      active: Boolean(record.active || trapTriggered || captive),
      phase,
      stageId,
      stageLabel: stringValue(record.stageLabel || VELA_ESCAPE_STAGE_LABELS[stageId] || titleWords(stageId)),
      trapTriggered,
      captive,
      investigationComplete: Boolean(record.investigationComplete),
      playerEquipment: {
        ...base.playerEquipment,
        ...objectValue(record.playerEquipment)
      },
      guardState: {
        ...base.guardState,
        ...objectValue(record.guardState)
      },
      extraction: {
        ...base.extraction,
        ...objectValue(record.extraction)
      },
      caveSystem: normalizeVelaCaveSystem(record.caveSystem || base.caveSystem),
      notes: arrayValue(record.notes).length ? clone(arrayValue(record.notes)) : base.notes
    };
  }

  function velaEscapeScenarioSnapshot(session) {
    if (!session || typeof session !== "object") return defaultVelaEscapeScenarioSnapshot(session);
    return normalizeVelaEscapeScenarioSnapshot(
      session,
      velaEscapeScenarioStateBySession.get(session)
    );
  }

  function notifyVelaSceneRenderer(snapshot) {
    if (typeof document === "undefined") return false;
    const renderer = document.querySelector?.("#webgl-demo")?.__mainComputerShuttle3dRenderer;
    if (!renderer || typeof renderer.syncVelaSubsurfaceScene !== "function") return false;
    try {
      renderer.syncVelaSubsurfaceScene(snapshot);
      return true;
    } catch (error) {
      console.warn("Vela subsurface renderer sync failed", error);
      return false;
    }
  }

  function activeVelaEscapeScenarioSnapshot() {
    const session = state.session || global.MainComputerStrategicAISession?.current?.() || null;
    return velaEscapeScenarioSnapshot(session);
  }

  function setVelaEscapeScenarioSnapshot(session, snapshot) {
    if (!session || typeof session !== "object") return defaultVelaEscapeScenarioSnapshot(session);
    const normalized = normalizeVelaEscapeScenarioSnapshot(session, snapshot);
    velaEscapeScenarioStateBySession.set(session, normalized);
    const result = clone(normalized);
    notifyVelaSceneRenderer(result);
    return result;
  }

  function triggerVelaUndergroundCapture(session, context = {}) {
    const turn = objectValue(context.turn);
    const briefing = objectValue(context.briefing);
    const existing = velaEscapeScenarioSnapshot(session);
    if (existing.trapTriggered && existing.stageId === VELA_ESCAPE_STAGE_IDS.captive) {
      return clone(existing);
    }
    return setVelaEscapeScenarioSnapshot(session, {
      active: true,
      phase: "underground-captive",
      stageId: VELA_ESCAPE_STAGE_IDS.captive,
      stageLabel: VELA_ESCAPE_STAGE_LABELS[VELA_ESCAPE_STAGE_IDS.captive],
      locationId: VELA_ESCAPE_LOCATION_ID,
      locationLabel: "Subsurface cavern beneath Vela Gate",
      objective: "Escape captivity beneath Vela Gate. Only one guard is watching.",
      trapTriggered: true,
      captive: true,
      investigationComplete: false,
      playerEquipment: {
        phaser: "stripped",
        melee: "available"
      },
      guardState: {
        total: 1,
        watching: 1,
        combatMode: "hand-to-hand-pending"
      },
      extraction: {
        destinationId: VELA_SURFACE_TRANSPORTER_ID,
        surfaceTransporter: "unreached",
        beamBack: "locked"
      },
      trigger: {
        reason: "investigation-action-triggered-transporter-trap",
        actionTypeId: stringValue(objectValue(turn.decision).selectedActionTypeId),
        outcomeId: stringValue(objectValue(turn.outcome).outcomeId),
        communicationId: stringValue(briefing.communicationId)
      },
      notes: [
        "The Vela investigation has become an away-mission captivity escape.",
        "Combat is not implemented in this patch; the next stage is one-guard hand-to-hand escape."
      ]
    });
  }

  function velaGuardTakedownAvailable(snapshot) {
    const escape = objectValue(snapshot);
    const guardState = objectValue(escape.guardState);
    return Boolean(
      escape.trapTriggered
      && escape.stageId === VELA_ESCAPE_STAGE_IDS.captive
      && finiteNumber(guardState.watching) === 1
      && stringValue(guardState.combatMode || "hand-to-hand-pending") === "hand-to-hand-pending"
    );
  }

  function velaGuardPhaserRecoveryAvailable(snapshot) {
    const escape = objectValue(snapshot);
    const equipment = objectValue(escape.playerEquipment);
    const guardState = objectValue(escape.guardState);
    return Boolean(
      escape.trapTriggered
      && escape.stageId === VELA_ESCAPE_STAGE_IDS.breakout
      && stringValue(equipment.phaser || "stripped") === "stripped"
      && (
        finiteNumber(guardState.watching) <= 0
        || finiteNumber(guardState.defeated) >= 1
      )
    );
  }

  function resolveVelaGuardTakedown(session, context = {}) {
    if (!session || typeof session !== "object") return defaultVelaEscapeScenarioSnapshot(session);
    const existing = velaEscapeScenarioSnapshot(session);
    if (existing.stageId === VELA_ESCAPE_STAGE_IDS.breakout) return clone(existing);
    if (!velaGuardTakedownAvailable(existing)) return clone(existing);
    return setVelaEscapeScenarioSnapshot(session, {
      ...existing,
      active: true,
      phase: "hand-to-hand-breakout",
      stageId: VELA_ESCAPE_STAGE_IDS.breakout,
      stageLabel: VELA_ESCAPE_STAGE_LABELS[VELA_ESCAPE_STAGE_IDS.breakout],
      locationId: VELA_ESCAPE_LOCATION_ID,
      locationLabel: "Subsurface cavern beneath Vela Gate",
      objective: "The lone guard is down. Search the guard post for a weapon and the route toward the surface transporter.",
      trapTriggered: true,
      captive: false,
      investigationComplete: false,
      playerEquipment: {
        ...objectValue(existing.playerEquipment),
        phaser: "stripped",
        melee: "dominant"
      },
      guardState: {
        ...objectValue(existing.guardState),
        total: 1,
        watching: 0,
        defeated: 1,
        combatMode: "guard-disabled"
      },
      extraction: {
        ...objectValue(existing.extraction),
        destinationId: VELA_SURFACE_TRANSPORTER_ID,
        surfaceTransporter: "unreached",
        beamBack: "locked"
      },
      breakout: {
        guardDefeated: true,
        method: "hand-to-hand",
        reason: stringValue(context.reason || "player-hand-to-hand-action")
      },
      notes: [
        "The lone guard is down.",
        "The next step is recovering a phaser from the guard post before the surface transporter run."
      ]
    });
  }

  function resolveVelaGuardPhaserRecovery(session, context = {}) {
    if (!session || typeof session !== "object") return defaultVelaEscapeScenarioSnapshot(session);
    const existing = velaEscapeScenarioSnapshot(session);
    const equipment = objectValue(existing.playerEquipment);
    if (stringValue(equipment.phaser) === "recovered") return clone(existing);
    if (!velaGuardPhaserRecoveryAvailable(existing)) return clone(existing);
    return setVelaEscapeScenarioSnapshot(session, {
      ...existing,
      active: true,
      phase: "surface-transporter-run",
      stageId: VELA_ESCAPE_STAGE_IDS.extraction,
      stageLabel: VELA_ESCAPE_STAGE_LABELS[VELA_ESCAPE_STAGE_IDS.extraction],
      locationId: VELA_ESCAPE_LOCATION_ID,
      locationLabel: "Subsurface cavern beneath Vela Gate",
      objective: "Phaser recovered. Fight through six cave sectors to reach the surface transporter.",
      trapTriggered: true,
      captive: false,
      investigationComplete: false,
      playerEquipment: {
        ...objectValue(existing.playerEquipment),
        phaser: "recovered",
        melee: "available"
      },
      guardState: {
        ...objectValue(existing.guardState),
        total: 1,
        watching: 0,
        defeated: 1,
        combatMode: "phaser-recovered"
      },
      extraction: {
        ...objectValue(existing.extraction),
        destinationId: VELA_SURFACE_TRANSPORTER_ID,
        surfaceTransporter: "ahead",
        beamBack: "locked"
      },
      caveSystem: normalizeVelaCaveSystem(existing.caveSystem),
      weaponRecovery: {
        phaserRecovered: true,
        method: "guard-post-pickup",
        reason: stringValue(context.reason || "player-recovered-phaser-after-guard")
      },
      notes: [
        "The guard post phaser is back in your hand.",
        "The cave route to the surface transporter is now the active objective."
      ]
    });
  }

  function resolveVelaCaveEnemyHit(session, context = {}) {
    if (!session || typeof session !== "object") return defaultVelaEscapeScenarioSnapshot(session);
    const existing = velaEscapeScenarioSnapshot(session);
    if (existing.stageId !== VELA_ESCAPE_STAGE_IDS.extraction) return clone(existing);
    const caveSystem = normalizeVelaCaveSystem(existing.caveSystem);
    const enemyId = stringValue(context.enemyId);
    if (!enemyId) return clone(existing);
    let changed = false;
    const enemies = caveSystem.enemies.map((enemy) => {
      if (enemy.id !== enemyId || enemy.status === "defeated") return enemy;
      changed = true;
      return {
        ...enemy,
        health: 0,
        status: "defeated",
        defeatedReason: stringValue(context.reason || "player-phaser-hit")
      };
    });
    if (!changed) return clone(existing);
    const nextCaveSystem = normalizeVelaCaveSystem({
      ...caveSystem,
      enemies
    });
    return setVelaEscapeScenarioSnapshot(session, {
      ...existing,
      active: true,
      phase: "surface-transporter-run",
      stageId: VELA_ESCAPE_STAGE_IDS.extraction,
      stageLabel: VELA_ESCAPE_STAGE_LABELS[VELA_ESCAPE_STAGE_IDS.extraction],
      objective: nextCaveSystem.enemiesActive > 0
        ? `Fight through the cave system. ${nextCaveSystem.enemiesActive} hostiles still block the surface transporter.`
        : "The cave route is clear. Reach the surface transporter and press E to beam back.",
      caveSystem: nextCaveSystem,
      guardState: {
        ...objectValue(existing.guardState),
        watching: 0,
        defeated: 1,
        combatMode: nextCaveSystem.enemiesActive > 0 ? "phaser-fight" : "transporter-route-clear"
      },
      extraction: {
        ...objectValue(existing.extraction),
        destinationId: VELA_SURFACE_TRANSPORTER_ID,
        surfaceTransporter: nextCaveSystem.enemiesActive > 0 ? "contested" : "reachable",
        beamBack: nextCaveSystem.enemiesActive > 0 ? "locked" : "ready"
      },
      notes: [
        nextCaveSystem.enemiesActive > 0
          ? `${nextCaveSystem.enemiesActive} Vela cave hostiles remain between you and the transporter room.`
          : "All cave hostiles are down. The surface transporter can beam you back."
      ]
    });
  }

  function resolveVelaCaveEnemyPhaserHit(context = {}) {
    const session = state.session || global.MainComputerStrategicAISession?.current?.() || null;
    return resolveVelaCaveEnemyHit(session, {
      ...objectValue(context),
      reason: stringValue(context.reason || "renderer-phaser-hit-cave-hostile")
    });
  }

  function resolveVelaSurfaceTransporterBeamBack(session, context = {}) {
    if (!session || typeof session !== "object") return defaultVelaEscapeScenarioSnapshot(session);
    const existing = velaEscapeScenarioSnapshot(session);
    const caveSystem = normalizeVelaCaveSystem(existing.caveSystem);
    if (existing.stageId !== VELA_ESCAPE_STAGE_IDS.extraction) return clone(existing);
    if (caveSystem.enemiesActive > 0) return clone(existing);
    return setVelaEscapeScenarioSnapshot(session, {
      ...existing,
      active: false,
      phase: "complete",
      stageId: VELA_ESCAPE_STAGE_IDS.complete,
      stageLabel: VELA_ESCAPE_STAGE_LABELS[VELA_ESCAPE_STAGE_IDS.complete],
      locationId: VELA_SYSTEM_ID,
      locationLabel: "Returned aboard ship",
      objective: "Beam-back complete. The Vela investigation is complete.",
      trapTriggered: true,
      captive: false,
      investigationComplete: true,
      playerEquipment: {
        ...objectValue(existing.playerEquipment),
        phaser: "recovered",
        melee: "available"
      },
      guardState: {
        ...objectValue(existing.guardState),
        watching: 0,
        defeated: 1,
        combatMode: "complete"
      },
      extraction: {
        ...objectValue(existing.extraction),
        destinationId: VELA_SURFACE_TRANSPORTER_ID,
        surfaceTransporter: "reached",
        beamBack: "complete"
      },
      caveSystem,
      completion: {
        method: "surface-transporter-beam-back",
        reason: stringValue(context.reason || "player-reached-surface-transporter")
      },
      notes: [
        "The player escaped the Vela subsurface cave system and returned aboard with direct evidence."
      ]
    });
  }

  function resolveVelaSurfaceTransporter(context = {}) {
    const session = state.session || global.MainComputerStrategicAISession?.current?.() || null;
    return resolveVelaSurfaceTransporterBeamBack(session, {
      ...objectValue(context),
      reason: stringValue(context.reason || "renderer-e-key-surface-transporter")
    });
  }

  function resolveVelaPhaserRecovery(context = {}) {
    const session = state.session || global.MainComputerStrategicAISession?.current?.() || null;
    return resolveVelaGuardPhaserRecovery(session, {
      ...objectValue(context),
      reason: stringValue(context.reason || "renderer-e-key-recover-phaser")
    });
  }

  function resolveVelaGuardMelee(context = {}) {
    const session = state.session || global.MainComputerStrategicAISession?.current?.() || null;
    return resolveVelaGuardTakedown(session, {
      ...objectValue(context),
      reason: stringValue(context.reason || "renderer-e-key-unarmed-melee")
    });
  }


  function latestOfficialTurn(session) {
    const state = objectValue(session?.strategicSnapshot?.());
    const receipts = arrayValue(state.receipts)
      .filter((receipt) => stringValue(receipt.actorId) === OFFICIAL_ACTOR_ID)
      .slice()
      .sort((left, right) => (
        stringValue(left.decisionId).localeCompare(stringValue(right.decisionId))
      ));
    const outcomes = indexById(state.outcomes, "decisionId");
    for (let index = receipts.length - 1; index >= 0; index -= 1) {
      const decision = receipts[index];
      const outcome = outcomes.get(stringValue(decision.decisionId));
      if (outcome) return {decision: clone(decision), outcome: clone(outcome)};
    }
    return null;
  }

  function opportunityState(session) {
    return arrayValue(objectValue(session?.strategicSnapshot?.()).campaignOpportunityStates)
      .find((record) => stringValue(record.opportunityId) === VELA_OPPORTUNITY_ID)
      || null;
  }

  function previewBriefing(session) {
    const coordinator = session?.coordinator?.();
    if (!coordinator?.performCommunication) return null;
    return coordinator.performCommunication(
      BRIEFING_INTENT_ID,
      OFFICIAL_ACTOR_ID,
      [ORGANIZER_ACTOR_ID, SURVIVOR_ACTOR_ID]
    );
  }

  function actionLabel(session, actionTypeId) {
    const action = arrayValue(objectValue(session?.definition).actionTypes)
      .find((record) => stringValue(record.id) === stringValue(actionTypeId));
    return stringValue(objectValue(action).label || titleWords(actionTypeId));
  }

  function goalLabels(session, goalIds) {
    const goals = indexById(objectValue(session?.definition).goals);
    return arrayValue(goalIds).map((goalId) => (
      stringValue(objectValue(goals.get(stringValue(goalId))).label || titleWords(goalId))
    ));
  }

  function scoreSignals(candidate) {
    return Object.entries(objectValue(objectValue(candidate).scoreComponents))
      .filter(([key, value]) => key !== "baseScore" && Math.abs(finiteNumber(value)) > 0)
      .sort((left, right) => Math.abs(finiteNumber(right[1])) - Math.abs(finiteNumber(left[1])))
      .slice(0, 3)
      .map(([key, value]) => ({
        id: key,
        label: signalLabel(key),
        value: finiteNumber(value),
        assessment: signalAssessment(value)
      }));
  }

  function captiveSceneDetails(escape) {
    const snapshot = objectValue(escape);
    const equipment = objectValue(snapshot.playerEquipment);
    const guardState = objectValue(snapshot.guardState);
    const extraction = objectValue(snapshot.extraction);
    const caveSystem = normalizeVelaCaveSystem(snapshot.caveSystem);
    const phaser = stringValue(equipment.phaser || "unknown");
    const melee = stringValue(equipment.melee || "unknown");
    const watching = finiteNumber(guardState.watching);
    const total = finiteNumber(guardState.total);
    const phaserRecovered = phaser === "recovered" || phaser === "available";
    const guardDefeated = snapshot.stageId === VELA_ESCAPE_STAGE_IDS.breakout
      || snapshot.stageId === VELA_ESCAPE_STAGE_IDS.extraction
      || watching <= 0;
    const guardLine = guardDefeated
      ? "The lone guard is down. The holding ledge is clear."
      : watching === 1
        ? "One guard is watching the holding ledge."
        : `${watching} guards are watching the holding ledge.`;
    return {
      title: "Vela Subsurface Cavern",
      kicker: guardDefeated
        ? "Guard down • holding ledge breached"
        : "Transporter trap • captive beneath the gate",
      objective: stringValue(snapshot.objective || "Escape captivity beneath Vela Gate."),
      location: stringValue(snapshot.locationLabel || "Subsurface cavern beneath Vela Gate"),
      equipment: [
        {
          label: "Phaser",
          value: phaser === "stripped"
            ? "Stripped during transport"
            : phaserRecovered
              ? "Recovered from the guard post"
              : titleWords(phaser)
        },
        {
          label: "Melee",
          value: melee === "available"
            ? "Available — close the distance"
            : melee === "dominant"
              ? "Used to disable the guard"
              : titleWords(melee)
        }
      ],
      guard: {
        label: "Guard detail",
        value: guardDefeated
          ? guardLine
          : `${guardLine} ${total || watching || 1} hostile assigned.`
      },
      combat: {
        label: "Combat mode",
        value: stringValue(guardState.combatMode || "hand-to-hand-pending")
          .replace(/[._-]+/g, " ")
      },
      extraction: {
        label: "Extraction",
        value: stringValue(extraction.beamBack) === "locked"
          ? "Beam-back locked until the surface transporter is reached"
          : titleWords(extraction.beamBack)
      },
      caveSystem: {
        label: "Cave system",
        value: `${caveSystem.roomCount} sectors • ${caveSystem.enemiesActive} hostiles active`,
        rooms: clone(caveSystem.rooms),
        enemiesActive: caveSystem.enemiesActive,
        enemiesDefeated: caveSystem.enemiesDefeated
      },
      prompt: phaserRecovered
        ? caveSystem.enemiesActive > 0
          ? `Phaser recovered. Fight through ${caveSystem.roomCount} cave sectors; ${caveSystem.enemiesActive} hostiles remain.`
          : "Route clear. Reach the surface transporter and press E to beam back."
        : guardDefeated
          ? "Guard disabled. Recover your phaser from the guard post."
          : "Hand-to-hand escape is next: disable the lone guard, recover your phaser, and fight upward to the surface transporter.",
      action: {
        label: phaserRecovered
          ? "✓ Phaser recovered"
          : guardDefeated
            ? "Recover phaser"
            : "Fight the lone guard",
        disabled: phaserRecovered,
        state: phaserRecovered ? "phaser-recovered" : guardDefeated ? "phaser-ready" : "ready"
      }
    };
  }

  function buildViewModel(session) {
    if (!session) {
      return {
        visible: false,
        phase: "unavailable",
        title: "Vela Gate Authority Channel",
        status: "Strategic session unavailable.",
        canRun: false
      };
    }
    const summary = session.summary();
    const visible = stringValue(summary.activeSystemId) === VELA_SYSTEM_ID;
    const escape = velaEscapeScenarioSnapshot(session);
    const opportunity = opportunityState(session);
    const opportunityStatus = stringValue(objectValue(opportunity).status || "unavailable");
    const turn = latestOfficialTurn(session);
    let briefing = null;
    let briefingError = "";
    if (turn?.outcome?.status === "accepted") {
      try {
        briefing = previewBriefing(session);
      } catch (error) {
        briefingError = error instanceof Error ? error.message : String(error || "Briefing unavailable");
      }
    }

    const turnAccepted = stringValue(objectValue(turn?.outcome).status) === "accepted";
    const trapContinuationAvailable = Boolean(
      visible && turnAccepted && !escape.trapTriggered
    );

    const selectedCandidate = arrayValue(objectValue(turn?.decision).candidateActions)
      .find((candidate) => (
        stringValue(candidate.actionTypeId)
        === stringValue(objectValue(turn?.decision).selectedActionTypeId)
      ));
    const selectedScore = finiteNumber(objectValue(selectedCandidate).score);
    const alternatives = arrayValue(objectValue(turn?.decision).candidateActions)
      .filter((candidate) => (
        stringValue(candidate.actionTypeId)
        !== stringValue(objectValue(turn?.decision).selectedActionTypeId)
      ))
      .map((candidate) => ({
        actionTypeId: stringValue(candidate.actionTypeId),
        label: actionLabel(session, candidate.actionTypeId),
        score: finiteNumber(candidate.score),
        assessment: alternativeAssessment(candidate.score, selectedScore)
      }));

    const guardTakedownAvailable = velaGuardTakedownAvailable(escape);
    const phaserRecoveryAvailable = velaGuardPhaserRecoveryAvailable(escape);
    let phase = "ready";
    let status = "A Gate Authority channel is available.";
    if (!visible) {
      phase = "away";
      status = "The Vela Gate channel is available only while the ship is in Vela Gate.";
    } else if (escape.trapTriggered && escape.stageId === VELA_ESCAPE_STAGE_IDS.complete) {
      phase = "escape-complete";
      status = "You escaped the Vela subsurface cave system and beamed back aboard.";
    } else if (escape.trapTriggered && escape.stageId === VELA_ESCAPE_STAGE_IDS.captive) {
      phase = "captive";
      status = "The Vela investigation triggered a transporter trap. You are captive beneath the surface.";
    } else if (escape.trapTriggered && escape.stageId === VELA_ESCAPE_STAGE_IDS.breakout) {
      phase = "breakout";
      status = "You disabled the lone guard. The holding ledge is clear; recover your phaser next.";
    } else if (escape.trapTriggered && escape.stageId === VELA_ESCAPE_STAGE_IDS.extraction) {
      phase = "armed-breakout";
      status = "You recovered your phaser. Fight toward the surface transporter.";
    } else if (trapContinuationAvailable) {
      phase = "trap-ready";
      status = "The Gate Authority briefing opened an investigation lead. Follow it to continue.";
    } else if (turnAccepted) {
      phase = "complete";
      status = "The Gate Authority has issued a verified response.";
    } else if (turn?.outcome?.status === "rejected") {
      phase = "rejected";
      status = stringValue(turn.outcome.rejectionReason || "The verified response was rejected.");
    } else if (opportunityStatus === "closed") {
      phase = "closed";
      status = "The Vela Gate intervention window has closed.";
    } else if (opportunityStatus === "active") {
      phase = "active";
      status = "The intervention window is active. Request the official response.";
    }

    return {
      visible,
      phase,
      title: "Vela Gate Investigation",
      status,
      canRun: visible && (
        guardTakedownAvailable
        || phaserRecoveryAvailable
        || trapContinuationAvailable
        || (!turn && ["available", "active"].includes(opportunityStatus))
      ),
      trapContinuationAvailable,
      guardTakedownAvailable,
      phaserRecoveryAvailable,
      opportunityStatus,
      escape,
      captiveScene: escape.trapTriggered ? captiveSceneDetails(escape) : null,
      briefingText: stringValue(objectValue(briefing).text),
      briefingError,
      communicationId: stringValue(objectValue(briefing).communicationId),
      actionTypeId: stringValue(objectValue(turn?.decision).selectedActionTypeId),
      actionLabel: actionLabel(session, objectValue(turn?.decision).selectedActionTypeId),
      outcomeStatus: stringValue(objectValue(turn?.outcome).status),
      confidence: finiteNumber(objectValue(turn?.decision).confidence),
      canonicalRevisionAfter: finiteNumber(objectValue(turn?.outcome).canonicalRevisionAfter),
      score: finiteNumber(objectValue(selectedCandidate).score),
      scoreSignals: scoreSignals(selectedCandidate),
      goals: goalLabels(session, objectValue(turn?.decision).activeGoalIds),
      alternatives,
      resultingObservationCount: arrayValue(objectValue(turn?.outcome).resultingObservationIds).length,
      consumedResources: clone(arrayValue(objectValue(turn?.outcome).consumedResources)),
      consequenceRows: turn?.outcome ? [
        {
          label: "Verification",
          value: stringValue(turn.outcome.status) === "accepted"
            ? "Accepted by the action verifier"
            : stringValue(turn.outcome.rejectionReason || "Rejected by the action verifier")
        },
        {
          label: "World state",
          value: `Advanced to revision ${finiteNumber(turn.outcome.canonicalRevisionAfter)}`
        },
        {
          label: "Shared knowledge",
          value: `${arrayValue(turn.outcome.resultingObservationIds).length} Vela actors received updates`
        },
        ...arrayValue(turn.outcome.consumedResources).map((resource) => ({
          label: "Resource used",
          value: resourceLabel(resource.resourceId, resource.amount)
        }))
      ] : [],
      decisionId: stringValue(objectValue(turn?.decision).decisionId),
      outcomeId: stringValue(objectValue(turn?.outcome).outcomeId)
    };
  }

  function runInteraction(session) {
    if (!session) throw new Error("Strategic session unavailable.");
    if (stringValue(session.summary().activeSystemId) !== VELA_SYSTEM_ID) {
      throw new Error("The Vela Gate channel can be used only in Vela Gate.");
    }

    const existing = latestOfficialTurn(session);
    if (existing?.outcome?.status === "accepted") {
      const briefing = previewBriefing(session);
      const escape = triggerVelaUndergroundCapture(session, {turn: existing, briefing});
      return {
        reused: true,
        activation: null,
        turn: clone(existing),
        briefing,
        escape,
        view: buildViewModel(session)
      };
    }

    const state = opportunityState(session);
    const status = stringValue(objectValue(state).status || "unavailable");
    if (status === "closed") {
      throw new Error("The Vela Gate intervention window has closed.");
    }

    let activation = null;
    if (status === "available") {
      activation = session.activateCampaignRoute(VELA_SYSTEM_ID, {
        selectedAt: finiteNumber(session.summary().offscreenSimulationTime, 0)
      });
    } else if (status !== "active") {
      throw new Error(`Vela Gate opportunity is ${status || "unavailable"}.`);
    }

    const turn = session.runActorTurn(OFFICIAL_ACTOR_ID);
    if (stringValue(objectValue(turn).outcome?.status) !== "accepted") {
      return {
        reused: false,
        activation,
        turn,
        briefing: null,
        escape: velaEscapeScenarioSnapshot(session),
        view: buildViewModel(session)
      };
    }
    const briefing = session.performCommunication(
      BRIEFING_INTENT_ID,
      OFFICIAL_ACTOR_ID,
      [ORGANIZER_ACTOR_ID, SURVIVOR_ACTOR_ID]
    );
    const escape = triggerVelaUndergroundCapture(session, {turn, briefing});
    return {
      reused: false,
      activation,
      turn,
      briefing,
      escape,
      view: buildViewModel(session)
    };
  }

  const state = {
    bound: false,
    requestBound: false,
    domObserver: null,
    session: null,
    unsubscribe: null,
    running: false,
    lastError: ""
  };

  function nodes() {
    if (typeof document === "undefined") return {};
    return {
      panel: document.querySelector("#vela-gate-strategic-contact"),
      status: document.querySelector("#vela-gate-strategic-status"),
      request: document.querySelector("#vela-gate-strategic-request"),
      briefing: document.querySelector("#vela-gate-strategic-briefing"),
      action: document.querySelector("#vela-gate-strategic-action"),
      confidence: document.querySelector("#vela-gate-strategic-confidence"),
      reasons: document.querySelector("#vela-gate-strategic-reasons"),
      alternatives: document.querySelector("#vela-gate-strategic-alternatives"),
      consequences: document.querySelector("#vela-gate-strategic-consequences"),
      explanation: document.querySelector("#vela-gate-strategic-explanation"),
      captiveScene: document.querySelector("#vela-gate-captive-scene"),
      captiveStatus: document.querySelector("#vela-gate-captive-status"),
      captiveObjective: document.querySelector("#vela-gate-captive-objective"),
      captiveLocation: document.querySelector("#vela-gate-captive-location"),
      captiveEquipment: document.querySelector("#vela-gate-captive-equipment"),
      captiveGuard: document.querySelector("#vela-gate-captive-guard"),
      captiveCombat: document.querySelector("#vela-gate-captive-combat"),
      captiveExtraction: document.querySelector("#vela-gate-captive-extraction"),
      captivePrompt: document.querySelector("#vela-gate-captive-prompt"),
      captiveAction: document.querySelector("#vela-gate-captive-action")
    };
  }

  function bindRequestButton(request) {
    if (!request?.addEventListener) return false;
    if (request.dataset?.velaGateRequestBound === "true") {
      state.requestBound = true;
      return true;
    }
    request.addEventListener("click", handleRequest);
    if (request.dataset) request.dataset.velaGateRequestBound = "true";
    state.requestBound = true;
    return true;
  }

  function bindCaptiveActionButton(action) {
    if (!action?.addEventListener) return false;
    if (action.dataset?.velaGateCaptiveActionBound === "true") return true;
    action.addEventListener("click", handleRequest);
    if (action.dataset) action.dataset.velaGateCaptiveActionBound = "true";
    return true;
  }

  function watchForDomNodes() {
    if (
      state.domObserver
      || typeof document === "undefined"
      || !document.body
      || typeof global.MutationObserver !== "function"
    ) {
      return false;
    }
    state.domObserver = new global.MutationObserver(() => {
      const ui = nodes();
      bindRequestButton(ui.request);
      bindCaptiveActionButton(ui.captiveAction);
      if (ui.panel) {
        render();
      }
      if (ui.panel && ui.request && state.domObserver?.disconnect) {
        state.domObserver.disconnect();
        state.domObserver = null;
      }
    });
    state.domObserver.observe(document.body, {childList: true, subtree: true});
    return true;
  }

  function replaceRows(node, entries, rowClass = "") {
    if (!node) return;
    node.replaceChildren();
    arrayValue(entries).forEach((entry) => {
      const record = typeof entry === "string"
        ? {label: stringValue(entry), value: ""}
        : objectValue(entry);
      const item = document.createElement("li");
      item.className = `vela-gate-strategic-list-row ${stringValue(rowClass)}`.trim();

      const label = document.createElement("span");
      label.className = "vela-gate-strategic-list-label";
      label.textContent = stringValue(record.label);

      const value = document.createElement("span");
      value.className = "vela-gate-strategic-list-value";
      value.textContent = stringValue(record.value);

      item.append(label);
      if (value.textContent) item.append(value);
      node.append(item);
    });
  }

  function renderCaptiveScene(ui, view) {
    // The subsurface escape is now played in the 3D cave scene. Keep the old
    // overlay/card suppressed so the player uses in-world movement + E instead
    // of a side-panel button.
    if (!ui.captiveScene) return;
    ui.captiveScene.hidden = true;
    ui.captiveScene.dataset.phase = stringValue(view?.phase || "hidden");
    if (ui.captiveAction) {
      ui.captiveAction.hidden = true;
      ui.captiveAction.disabled = true;
      ui.captiveAction.dataset.state = "in-world-only";
    }
  }

  function render() {
    const ui = nodes();
    bindRequestButton(ui.request);
    bindCaptiveActionButton(ui.captiveAction);
    if (!ui.panel) {
      watchForDomNodes();
      return;
    }
    const session = state.session || global.MainComputerStrategicAISession?.current?.();
    if (session && session !== state.session) setSession(session);
    const view = buildViewModel(state.session);
    renderCaptiveScene(ui, view);
    const caveSceneActive = Boolean(view.visible && ["captive", "breakout", "armed-breakout"].includes(view.phase));
    ui.panel.hidden = !view.visible || caveSceneActive;
    ui.panel.dataset.phase = stringValue(view.phase);
    ui.panel.dataset.escapeStage = stringValue(objectValue(view.escape).stageId);
    ui.panel.dataset.inWorldVelaEscape = caveSceneActive ? "true" : "false";
    if (!view.visible || caveSceneActive) return;

    if (ui.status) {
      ui.status.dataset.state = state.lastError ? "error" : view.phase;
      ui.status.textContent = state.lastError || view.status;
    }
    if (ui.request) {
      ui.request.disabled = state.running || !view.canRun;
      ui.request.dataset.state = stringValue(view.phase);
      ui.request.textContent = state.running
        ? "Investigating Vela Gate…"
        : view.phase === "captive"
          ? "Fight the lone guard"
          : view.phase === "breakout"
            ? "Recover phaser"
            : view.phase === "armed-breakout"
              ? "✓ Phaser recovered"
              : view.phase === "trap-ready"
              ? "Follow the Vela lead"
              : view.phase === "complete"
                ? "✓ Briefing received"
                : "Start Vela investigation";
    }
    if (ui.briefing) {
      ui.briefing.textContent = view.briefingText
        || view.briefingError
        || "No official briefing has been received.";
    }
    if (ui.action) ui.action.textContent = view.actionLabel || "Awaiting verified decision";
    if (ui.confidence) {
      ui.confidence.textContent = view.actionTypeId
        ? `${Math.round(view.confidence * 100)}% decision confidence`
        : "";
    }
    replaceRows(
      ui.reasons,
      view.scoreSignals.map((signal) => ({
        label: signal.label,
        value: signal.assessment
      })),
      "vela-gate-strategic-factor-meter"
    );
    replaceRows(
      ui.alternatives,
      view.alternatives.map((alternative) => ({
        label: alternative.label,
        value: alternative.assessment
      }))
    );
    replaceRows(ui.consequences, view.consequenceRows);
    if (ui.explanation) ui.explanation.hidden = !view.actionTypeId;
  }

  function setSession(session) {
    if (state.session === session) {
      render();
      return session;
    }
    state.unsubscribe?.();
    state.unsubscribe = null;
    state.session = session || null;
    state.lastError = "";
    if (state.session?.subscribe) {
      state.unsubscribe = state.session.subscribe(() => render());
    }
    render();
    return state.session;
  }

  function runCurrentVelaAction(session) {
    const view = buildViewModel(session);
    if (view.guardTakedownAvailable) {
      const escape = resolveVelaGuardTakedown(session, {reason: "player-hand-to-hand-button"});
      return {
        reused: false,
        action: "vela-guard-hand-to-hand-takedown",
        escape,
        view: buildViewModel(session)
      };
    }
    if (view.phaserRecoveryAvailable) {
      const escape = resolveVelaGuardPhaserRecovery(session, {reason: "player-phaser-recovery-button"});
      return {
        reused: false,
        action: "vela-guard-post-phaser-recovery",
        escape,
        view: buildViewModel(session)
      };
    }
    return runInteraction(session);
  }

  function handleRequest() {
    if (state.running) return;
    state.running = true;
    state.lastError = "";
    render();
    try {
      runCurrentVelaAction(state.session);
    } catch (error) {
      state.lastError = error instanceof Error ? error.message : String(error || "Interaction failed.");
    } finally {
      state.running = false;
      render();
    }
  }

  function bind() {
    if (typeof document === "undefined") return;
    if (!state.bound) {
      state.bound = true;
      global.addEventListener?.("main-computer-strategic-ai-session-change", () => {
        const current = global.MainComputerStrategicAISession?.current?.();
        if (current) setSession(current);
        else render();
      });
    }
    const ui = nodes();
    bindRequestButton(ui.request);
    bindCaptiveActionButton(ui.captiveAction);
    if (!ui.panel || !ui.request) watchForDomNodes();
    const current = global.MainComputerStrategicAISession?.current?.();
    if (current) setSession(current);
    render();
  }

  const api = {
    VELA_SYSTEM_ID,
    VELA_OPPORTUNITY_ID,
    VELA_ESCAPE_SCENARIO_ID,
    VELA_ESCAPE_ENCOUNTER_ID,
    VELA_ESCAPE_LOCATION_ID,
    VELA_SURFACE_TRANSPORTER_ID,
    VELA_ESCAPE_STAGE_IDS,
    VELA_CAVE_GAMEPLAY_TEMPLATE_CONSUMER,
    VELA_CAVE_ROOMS,
    VELA_CAVE_HOSTILES,
    OFFICIAL_ACTOR_ID,
    BRIEFING_INTENT_ID,
    velaEscapeScenarioSnapshot,
    activeVelaEscapeScenarioSnapshot,
    triggerVelaUndergroundCapture,
    velaGuardTakedownAvailable,
    velaGuardPhaserRecoveryAvailable,
    normalizeVelaCaveSystem,
    resolveVelaCaveEnemyHit,
    resolveVelaCaveEnemyPhaserHit,
    resolveVelaSurfaceTransporterBeamBack,
    resolveVelaSurfaceTransporter,
    resolveVelaGuardTakedown,
    resolveVelaGuardMelee,
    resolveVelaGuardPhaserRecovery,
    resolveVelaPhaserRecovery,
    latestOfficialTurn,
    opportunityState,
    previewBriefing,
    signalLabel,
    signalAssessment,
    alternativeAssessment,
    resourceLabel,
    captiveSceneDetails,
    buildViewModel,
    runInteraction,
    setSession,
    render,
    bind,
    state
  };

  global.MainComputerStrategicAIVelaInteraction = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") bind();
})(typeof globalThis !== "undefined" ? globalThis : window);
