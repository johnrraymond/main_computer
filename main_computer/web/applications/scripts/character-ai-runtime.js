(function (global) {
  "use strict";

  const DEFINITION_SCHEMA = "game.characterAI.v1";
  const DEFINITION_VERSION = "game.characterAI.definition.v1";
  const STATE_VERSION = "game.characterAI.state.v1";
  const POLICY_RESULT_SCHEMA = "game.characterAI.policyResult.v1";
  const CAMPAIGN_EXTENSION_SCHEMA = "game.characterAI.campaignExtension.v1";
  const STORAGE_PREFIX = "main-computer.character-ai.state.v1";
  const DETERMINISTIC_POLICY_ID = "policy.character.deterministic-v1";

  const ACTION_IDS = Object.freeze([
    "patrol",
    "move_to_player",
    "take_cover",
    "attack_player",
    "retreat",
    "call_support",
    "hold_position",
    "warn_player",
    "repair_power",
    "follow_player"
  ]);

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

  function finiteNumber(value, fallback = 0, minimum = -Infinity, maximum = Infinity) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(maximum, Math.max(minimum, parsed));
  }

  function integerValue(value, fallback = 0, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
    return Math.trunc(finiteNumber(value, fallback, minimum, maximum));
  }

  function vector3(value, fallback = [0, -0.55, 0]) {
    if (!Array.isArray(value) || value.length !== 3) return fallback.slice();
    const parsed = value.map(Number);
    return parsed.every(Number.isFinite) ? parsed : fallback.slice();
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

  function distance2d(left, right) {
    const a = vector3(left);
    const b = vector3(right);
    return Math.hypot(a[0] - b[0], a[2] - b[2]);
  }

  function normalizeDefinition(value) {
    const raw = objectValue(value);
    const tickIntervalMs = integerValue(raw.tickIntervalMs, 650, 150, 10000);
    const maxExternalDecisionAgeMs = integerValue(
      raw.maxExternalDecisionAgeMs,
      2500,
      tickIntervalMs,
      30000
    );
    const coverPoints = arrayValue(raw.coverPoints)
      .map((entry, index) => {
        const point = objectValue(entry);
        const id = stringValue(point.id || `cover-${index + 1}`);
        return id
          ? {
            id,
            position: vector3(point.position),
            systemId: stringValue(point.systemId)
          }
          : null;
      })
      .filter(Boolean);
    const patrolRoutes = {};
    Object.entries(objectValue(raw.patrolRoutes)).forEach(([routeId, points]) => {
      const id = stringValue(routeId);
      if (!id) return;
      patrolRoutes[id] = arrayValue(points).map((point) => vector3(point));
    });
    const repairTargets = {};
    Object.entries(objectValue(raw.repairTargets)).forEach(([targetId, entry]) => {
      const id = stringValue(targetId);
      if (!id) return;
      const target = objectValue(entry);
      repairTargets[id] = {
        id,
        position: vector3(target.position),
        systemId: stringValue(target.systemId),
        label: stringValue(target.label || id)
      };
    });
    const vessels = arrayValue(raw.vessels)
      .map((entry) => {
        const vessel = objectValue(entry);
        const id = stringValue(vessel.id);
        return id
          ? {
            id,
            label: stringValue(vessel.label || id),
            faction: stringValue(vessel.faction),
            role: stringValue(vessel.role),
            sceneObjectId: stringValue(vessel.sceneObjectId),
            systemIds: [...new Set(
              arrayValue(vessel.systemIds).map(stringValue).filter(Boolean)
            )]
          }
          : null;
      })
      .filter(Boolean);

    const characters = arrayValue(raw.characters).map((entry, index) => {
      const character = objectValue(entry);
      const id = stringValue(character.id);
      const kind = stringValue(character.kind);
      const spawn = objectValue(character.spawn);
      const stats = objectValue(character.stats);
      const allowedActions = [...new Set(
        arrayValue(character.allowedActions).map(stringValue).filter(
          (actionId) => ACTION_IDS.includes(actionId)
        )
      )];
      return {
        id,
        label: stringValue(character.label || id || `Character ${index + 1}`),
        kind,
        faction: stringValue(character.faction),
        policyId: stringValue(character.policyId || DETERMINISTIC_POLICY_ID),
        spawn: {
          position: vector3(spawn.position),
          sceneId: stringValue(spawn.sceneId),
          systemId: stringValue(spawn.systemId)
        },
        stats: {
          maxHealth: finiteNumber(stats.maxHealth, kind === "enemy" ? 60 : 100, 1, 10000),
          speed: finiteNumber(stats.speed, kind === "enemy" ? 1.15 : 0.9, 0.05, 20),
          perceptionRange: finiteNumber(stats.perceptionRange, 14, 1, 100),
          attackRange: finiteNumber(stats.attackRange, 1.35, 0.25, 20),
          attackDamage: finiteNumber(stats.attackDamage, 8, 0, 1000),
          attackCooldownMs: integerValue(stats.attackCooldownMs, 900, 100, 30000),
          retreatHealth: finiteNumber(stats.retreatHealth, 18, 0, 10000),
          repairRange: finiteNumber(stats.repairRange, 1.8, 0.25, 20),
          dangerRange: finiteNumber(stats.dangerRange, 5.5, 0.5, 100)
        },
        allowedActions,
        goals: arrayValue(character.goals).map(stringValue).filter(Boolean),
        inventory: arrayValue(character.inventory).map(stringValue).filter(Boolean),
        patrolRouteId: stringValue(character.patrolRouteId),
        repairTargetId: stringValue(character.repairTargetId),
        activePhases: [...new Set(
          arrayValue(character.activePhases).map(stringValue).filter(Boolean)
        )],
        activeSystemIds: [...new Set(
          (
            arrayValue(character.activeSystemIds).length
              ? arrayValue(character.activeSystemIds)
              : [spawn.systemId]
          ).map(stringValue).filter(Boolean)
        )],
        activeScenarioId: stringValue(character.activeScenarioId),
        activeScenarioStages: [...new Set(
          arrayValue(character.activeScenarioStages).map(stringValue).filter(Boolean)
        )],
        supportVesselId: stringValue(character.supportVesselId),
        warningText: stringValue(character.warningText),
        supportText: stringValue(character.supportText),
        memoryDefaults: clone(objectValue(character.memoryDefaults))
      };
    });

    return {
      schema: stringValue(raw.schema),
      definitionVersion: stringValue(raw.definitionVersion),
      stateVersion: stringValue(raw.stateVersion),
      enabled: raw.enabled !== false,
      tickIntervalMs,
      maxExternalDecisionAgeMs,
      receiptLimit: integerValue(raw.receiptLimit, 192, 16, 1024),
      characters,
      coverPoints,
      patrolRoutes,
      repairTargets,
      vessels
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
    if (!definition.characters.length) errors.push("characters must not be empty");
    const vesselIds = new Set();
    definition.vessels.forEach((vessel, index) => {
      const prefix = `vessels[${index}]`;
      if (!vessel.id) errors.push(`${prefix}.id is required`);
      else if (vesselIds.has(vessel.id)) errors.push(`${prefix}.id must be unique`);
      else vesselIds.add(vessel.id);
      if (!vessel.faction) warnings.push(`${vessel.id || prefix} has no faction`);
      if (!vessel.sceneObjectId) warnings.push(`${vessel.id || prefix} has no sceneObjectId`);
    });
    const ids = new Set();
    definition.characters.forEach((character, index) => {
      const prefix = `characters[${index}]`;
      if (!character.id) errors.push(`${prefix}.id is required`);
      else if (ids.has(character.id)) errors.push(`${prefix}.id must be unique`);
      else ids.add(character.id);
      if (!["enemy", "npc"].includes(character.kind)) {
        errors.push(`${prefix}.kind must be enemy or npc`);
      }
      if (!character.faction) errors.push(`${prefix}.faction is required`);
      if (!character.allowedActions.length) {
        errors.push(`${prefix}.allowedActions must not be empty`);
      }
      if (character.kind === "enemy"
          && !character.allowedActions.includes("attack_player")) {
        warnings.push(`${character.id} cannot attack the player`);
      }
      if (character.kind === "npc"
          && !character.allowedActions.includes("hold_position")) {
        warnings.push(`${character.id} has no stable idle action`);
      }
      if (character.patrolRouteId
          && !definition.patrolRoutes[character.patrolRouteId]) {
        errors.push(`${character.id} references an unknown patrol route`);
      }
      if (character.repairTargetId
          && !definition.repairTargets[character.repairTargetId]) {
        errors.push(`${character.id} references an unknown repair target`);
      }
      if (character.supportVesselId
          && !vesselIds.has(character.supportVesselId)) {
        errors.push(`${character.id} references an unknown support vessel`);
      }
      if (character.allowedActions.includes("call_support")
          && !character.supportVesselId) {
        errors.push(`${character.id} can call support but has no supportVesselId`);
      }
    });
    return {ok: errors.length === 0, definition, errors, warnings};
  }

  class CharacterAIDefinitionError extends Error {
    constructor(report) {
      super(`Invalid character AI definition: ${report.errors.join("; ")}`);
      this.name = "CharacterAIDefinitionError";
      this.report = report;
    }
  }

  class CharacterAIStateError extends Error {
    constructor(message, code = "character-state-invalid") {
      super(message);
      this.name = "CharacterAIStateError";
      this.code = code;
    }
  }

  class DeterministicCharacterPolicy {
    chooseAction(context) {
      const actor = objectValue(context.actor);
      const player = objectValue(context.player);
      const ship = objectValue(context.ship);
      const recent = objectValue(context.recent);
      const legal = new Set(arrayValue(context.legalActionIds));
      if (actor.kind === "enemy") {
        if (actor.health <= actor.retreatHealth && legal.has("retreat")) {
          return {actionId: "retreat", targetId: actor.spawnId, rationale: "low health"};
        }
        if (player.visible && recent.damagedRecently && legal.has("take_cover")
            && context.cover?.recommendedId) {
          return {
            actionId: "take_cover",
            targetId: context.cover.recommendedId,
            rationale: "recent incoming fire"
          };
        }
        if (player.visible && player.distance <= actor.attackRange
            && actor.attackReady && legal.has("attack_player")) {
          return {actionId: "attack_player", targetId: "player", rationale: "player in range"};
        }
        if (player.visible && !recent.supportCalled && legal.has("call_support")
            && actor.supportVesselId) {
          return {
            actionId: "call_support",
            targetId: actor.supportVesselId,
            rationale: "contact established"
          };
        }
        if (player.visible && legal.has("move_to_player")) {
          return {actionId: "move_to_player", targetId: "player", rationale: "close distance"};
        }
        return {actionId: "patrol", targetId: context.patrol?.targetId || "", rationale: "search area"};
      }

      if (context.threat?.visible && context.threat.distance <= actor.dangerRange
          && legal.has("take_cover") && context.cover?.recommendedId) {
        return {
          actionId: "take_cover",
          targetId: context.cover.recommendedId,
          rationale: "nearby hostile"
        };
      }
      if (ship.power !== "online"
          && context.repair?.available
          && legal.has("repair_power")) {
        return {
          actionId: "repair_power",
          targetId: context.repair.targetId,
          rationale: "restore engineering power"
        };
      }
      if (player.visible && !recent.warnedPlayer && legal.has("warn_player")) {
        return {actionId: "warn_player", targetId: "player", rationale: "warn about boarder"};
      }
      if (player.visible && recent.protectedByPlayer && legal.has("follow_player")) {
        return {actionId: "follow_player", targetId: "player", rationale: "trusted escort"};
      }
      return {actionId: "hold_position", targetId: actor.id, rationale: "hold engineering station"};
    }
  }

  class RemoteCharacterPolicy {
    constructor(request, options = {}) {
      if (typeof request !== "function") {
        throw new TypeError("RemoteCharacterPolicy requires a request function.");
      }
      this.request = request;
      this.id = stringValue(options.id || "policy.character.remote");
    }

    chooseAction(context) {
      return this.request(clone(context));
    }
  }

  function createRemotePolicy(request, options = {}) {
    return new RemoteCharacterPolicy(request, options);
  }

  class CharacterAIRuntime {
    constructor(definitionValue, options = {}) {
      const report = validateDefinition(definitionValue);
      if (!report.ok) throw new CharacterAIDefinitionError(report);
      this.definition = report.definition;
      this.report = report;
      this.projectId = stringValue(options.projectId || "game-project");
      this.definitionFingerprint = definitionFingerprint(this.definition);
      this.storage = options.storage === undefined ? defaultStorage() : options.storage;
      this.storageKey = `${STORAGE_PREFIX}:${this.projectId}`;
      this.storageIssue = "";
      this.listeners = new Set();
      this.policyRegistry = new Map();
      this.policyRegistry.set(DETERMINISTIC_POLICY_ID, new DeterministicCharacterPolicy());
      this.pendingRequests = new Map();
      this.pendingResults = new Map();
      this.lastStepAtMs = -Infinity;

      const supplied = options.state === undefined ? null : options.state;
      const stored = supplied === null && options.restore !== false
        ? this.readStoredState()
        : null;
      this.state = this.normalizeState(supplied || stored);
      this.clockRebasePending = Boolean(supplied || stored);
      this.persist();
    }

    characterDefinition(characterId) {
      return this.definition.characters.find(
        (character) => character.id === stringValue(characterId)
      ) || null;
    }

    normalizeCharacterState(definition, value = {}) {
      const raw = objectValue(value);
      const health = finiteNumber(
        raw.health,
        definition.stats.maxHealth,
        0,
        definition.stats.maxHealth
      );
      const memory = {
        ...clone(definition.memoryDefaults),
        ...clone(objectValue(raw.memory))
      };
      return {
        id: definition.id,
        label: definition.label,
        kind: definition.kind,
        faction: definition.faction,
        policyId: stringValue(raw.policyId || definition.policyId),
        health,
        maxHealth: definition.stats.maxHealth,
        position: vector3(raw.position, definition.spawn.position),
        spawnPosition: definition.spawn.position.slice(),
        status: health <= 0 ? "down" : stringValue(raw.status || "active"),
        currentActionId: stringValue(raw.currentActionId || "hold_position"),
        currentTargetId: stringValue(raw.currentTargetId),
        nextDecisionAtMs: finiteNumber(raw.nextDecisionAtMs, 0, 0),
        nextAttackAtMs: finiteNumber(raw.nextAttackAtMs, 0, 0),
        patrolIndex: integerValue(raw.patrolIndex, 0, 0),
        decisionCount: integerValue(raw.decisionCount, 0, 0),
        memory
      };
    }

    normalizeState(value) {
      const raw = objectValue(value);
      if (raw.schema && raw.schema !== STATE_VERSION) {
        throw new CharacterAIStateError(
          `Character state schema must be ${STATE_VERSION}.`,
          "character-state-schema-mismatch"
        );
      }
      if (raw.projectId && stringValue(raw.projectId) !== this.projectId) {
        throw new CharacterAIStateError(
          "Character state belongs to another project.",
          "character-state-project-mismatch"
        );
      }
      if (raw.definitionFingerprint
          && stringValue(raw.definitionFingerprint) !== this.definitionFingerprint) {
        throw new CharacterAIStateError(
          "Character state definition does not match the active project.",
          "character-state-definition-mismatch"
        );
      }
      const suppliedCharacters = objectValue(raw.characters);
      const characters = {};
      this.definition.characters.forEach((definition) => {
        characters[definition.id] = this.normalizeCharacterState(
          definition,
          suppliedCharacters[definition.id]
        );
      });
      return {
        schema: STATE_VERSION,
        projectId: this.projectId,
        definitionFingerprint: this.definitionFingerprint,
        sequence: integerValue(raw.sequence, 0, 0),
        lastUpdatedAtMs: finiteNumber(raw.lastUpdatedAtMs, 0, 0),
        characters,
        receipts: arrayValue(raw.receipts).map((receipt) => clone(objectValue(receipt)))
          .slice(-this.definition.receiptLimit)
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
        this.storageIssue = "stored-character-state-unreadable";
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
        this.storageIssue = "character-state-storage-write-failed";
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
        summary: this.summary(),
        detail: clone(detail)
      };
      this.listeners.forEach((listener) => {
        try {
          listener(clone(event));
        } catch {
          // Character observers cannot block the simulation.
        }
      });
      try {
        if (typeof global.dispatchEvent === "function"
            && typeof global.CustomEvent === "function") {
          global.dispatchEvent(
            new global.CustomEvent("main-computer-character-ai-change", {
              detail: clone(event)
            })
          );
        }
      } catch {
        // Browser event publication is optional.
      }
      return event;
    }

    registerPolicy(policyId, policy) {
      const id = stringValue(policyId);
      if (!id || typeof policy?.chooseAction !== "function") {
        throw new TypeError("Character policy requires an id and chooseAction(context).");
      }
      this.policyRegistry.set(id, policy);
      return policy;
    }

    setCharacterPolicy(characterId, policyId) {
      const state = this.state.characters[stringValue(characterId)];
      const id = stringValue(policyId);
      if (!state) throw new CharacterAIStateError("Unknown character.");
      if (!this.policyRegistry.has(id)) {
        throw new CharacterAIStateError(`Unknown character policy ${id}.`);
      }
      state.policyId = id;
      this.record("policy-changed", {
        characterId: state.id,
        policyId: id
      });
      return clone(state);
    }

    snapshot() {
      return clone(this.state);
    }

    exportSnapshot(space = 2) {
      return JSON.stringify(this.snapshot(), null, integerValue(space, 2, 0, 8));
    }

    restore(value, options = {}) {
      let parsed = value;
      if (typeof parsed === "string") {
        try {
          parsed = JSON.parse(parsed);
        } catch {
          throw new CharacterAIStateError(
            "Character state text is not valid JSON.",
            "character-state-json-invalid"
          );
        }
      }
      this.state = this.normalizeState(parsed);
      this.clockRebasePending = true;
      this.pendingRequests.clear();
      this.pendingResults.clear();
      this.persist();
      if (options.emit !== false) this.emit("state-restored", null);
      return this.snapshot();
    }

    campaignExtension() {
      return {
        schema: CAMPAIGN_EXTENSION_SCHEMA,
        projectId: this.projectId,
        definitionFingerprint: this.definitionFingerprint,
        characterAI: this.snapshot()
      };
    }

    restoreCampaignExtension(value, options = {}) {
      const extension = objectValue(value);
      if (extension.schema !== CAMPAIGN_EXTENSION_SCHEMA) {
        throw new CharacterAIStateError(
          `Campaign extension schema must be ${CAMPAIGN_EXTENSION_SCHEMA}.`,
          "character-campaign-schema-mismatch"
        );
      }
      if (stringValue(extension.projectId) !== this.projectId) {
        throw new CharacterAIStateError(
          "Character campaign extension belongs to another project.",
          "character-campaign-project-mismatch"
        );
      }
      if (stringValue(extension.definitionFingerprint) !== this.definitionFingerprint) {
        throw new CharacterAIStateError(
          "Character campaign extension definition is incompatible.",
          "character-campaign-definition-mismatch"
        );
      }
      return this.restore(extension.characterAI, options);
    }

    reset(options = {}) {
      this.state = this.normalizeState(null);
      this.clockRebasePending = false;
      this.pendingRequests.clear();
      this.pendingResults.clear();
      this.persist();
      if (options.emit !== false) this.emit("state-reset", null);
      return this.snapshot();
    }

    activeCharacters() {
      return Object.values(this.state.characters)
        .filter((character) => character.status === "active" && character.health > 0)
        .map(clone);
    }

    characterIsActiveInWorld(characterId, worldValue = {}) {
      const definition = this.characterDefinition(characterId);
      const state = this.state.characters[stringValue(characterId)];
      if (!definition || !state || state.status !== "active" || state.health <= 0) {
        return false;
      }
      const world = objectValue(worldValue);
      const phase = stringValue(world.phase);
      if (definition.activePhases.length
          && !definition.activePhases.includes(phase)) {
        return false;
      }
      const currentSystemId = stringValue(objectValue(world.ship).currentSystemId);
      if (definition.activeSystemIds.length
          && !definition.activeSystemIds.includes(currentSystemId)) {
        return false;
      }
      if (definition.activeScenarioId) {
        const scenario = objectValue(world.scenario);
        if (stringValue(scenario.id) !== definition.activeScenarioId) return false;
        if (definition.activeScenarioStages.length
            && !definition.activeScenarioStages.includes(stringValue(scenario.stageId))) {
          return false;
        }
        if (stringValue(scenario.status) === "available") return false;
      }
      return true;
    }

    activeCharactersForWorld(worldValue = {}) {
      return Object.values(this.state.characters)
        .filter((character) => this.characterIsActiveInWorld(character.id, worldValue))
        .map(clone);
    }

    character(characterId) {
      const state = this.state.characters[stringValue(characterId)];
      return state ? clone(state) : null;
    }

    nearestThreat(character, world) {
      const actor = objectValue(character);
      const enemies = this.activeCharactersForWorld(world).filter((candidate) => (
        candidate.kind === "enemy"
        && candidate.faction !== actor.faction
      ));
      let nearest = null;
      enemies.forEach((candidate) => {
        const distance = distance2d(actor.position, candidate.position);
        if (!nearest || distance < nearest.distance) {
          nearest = {
            id: candidate.id,
            label: candidate.label,
            distance,
            position: candidate.position.slice()
          };
        }
      });
      return nearest;
    }

    recommendedCover(character, world) {
      const playerPosition = vector3(objectValue(world.player).position);
      const currentSystemId = stringValue(objectValue(world.ship).currentSystemId);
      const points = this.definition.coverPoints
        .filter((point) => !point.systemId || point.systemId === currentSystemId)
        .map((point) => {
        const actorDistance = distance2d(character.position, point.position);
        const playerDistance = distance2d(playerPosition, point.position);
          return {...point, actorDistance, playerDistance};
        });
      points.sort((left, right) => {
        const leftScore = left.actorDistance - Math.min(6, left.playerDistance) * 0.3;
        const rightScore = right.actorDistance - Math.min(6, right.playerDistance) * 0.3;
        return leftScore - rightScore || left.id.localeCompare(right.id);
      });
      return points[0] || null;
    }

    buildPerception(characterId, worldValue = {}, nowMs = 0) {
      const definition = this.characterDefinition(characterId);
      const state = this.state.characters[stringValue(characterId)];
      if (!definition || !state) {
        throw new CharacterAIStateError(`Unknown character ${characterId}.`);
      }
      const world = objectValue(worldValue);
      const player = objectValue(world.player);
      const playerPosition = vector3(player.position);
      const playerDistance = distance2d(state.position, playerPosition);
      const playerVisible = player.alive !== false
        && playerDistance <= definition.stats.perceptionRange;
      const threat = definition.kind === "npc"
        ? this.nearestThreat(state, world)
        : null;
      const cover = this.recommendedCover(state, world);
      const repairTarget = this.definition.repairTargets[definition.repairTargetId] || null;
      const repairDistance = repairTarget
        ? distance2d(state.position, repairTarget.position)
        : Infinity;
      const patrol = arrayValue(
        this.definition.patrolRoutes[definition.patrolRouteId]
      );
      const patrolTarget = patrol.length
        ? patrol[state.patrolIndex % patrol.length]
        : state.spawnPosition;
      const memory = objectValue(state.memory);
      const lastDamageAtMs = memory.lastDamageAtMs === null
          || memory.lastDamageAtMs === undefined
        ? -Infinity
        : finiteNumber(memory.lastDamageAtMs, -Infinity);
      return {
        schema: "game.characterAI.perception.v1",
        requestId: `${state.id}:${state.decisionCount + 1}:${Math.trunc(nowMs)}`,
        nowMs: finiteNumber(nowMs, 0, 0),
        actor: {
          id: state.id,
          label: state.label,
          kind: state.kind,
          faction: state.faction,
          health: state.health,
          maxHealth: state.maxHealth,
          position: state.position.slice(),
          currentActionId: state.currentActionId,
          retreatHealth: definition.stats.retreatHealth,
          attackRange: definition.stats.attackRange,
          dangerRange: definition.stats.dangerRange,
          attackReady: nowMs >= state.nextAttackAtMs,
          spawnId: `${state.id}.spawn`,
          supportVesselId: definition.supportVesselId,
          activeSystemIds: definition.activeSystemIds.slice(),
          activeScenarioId: definition.activeScenarioId
        },
        player: {
          visible: playerVisible,
          distance: Number(playerDistance.toFixed(3)),
          health: finiteNumber(player.health, 100, 0),
          position: playerPosition
        },
        threat: threat
          ? {
            visible: threat.distance <= definition.stats.perceptionRange,
            id: threat.id,
            label: threat.label,
            distance: Number(threat.distance.toFixed(3)),
            position: threat.position
          }
          : {visible: false, id: "", label: "", distance: Infinity, position: null},
        cover: cover
          ? {
            recommendedId: cover.id,
            position: cover.position.slice(),
            distance: Number(cover.actorDistance.toFixed(3))
          }
          : {recommendedId: "", position: null, distance: Infinity},
        patrol: {
          targetId: `${definition.patrolRouteId || state.id}.point.${state.patrolIndex}`,
          position: vector3(patrolTarget)
        },
        repair: repairTarget
          ? {
            targetId: repairTarget.id,
            label: repairTarget.label,
            position: repairTarget.position.slice(),
            distance: Number(repairDistance.toFixed(3)),
            available: repairDistance <= definition.stats.repairRange
          }
          : {targetId: "", label: "", position: null, distance: Infinity, available: false},
        ship: {
          power: stringValue(objectValue(world.ship).power || "unknown"),
          security: stringValue(objectValue(world.ship).security || "unknown"),
          currentSystemId: stringValue(objectValue(world.ship).currentSystemId),
          knownVessels: this.definition.vessels
            .filter((vessel) => (
              !vessel.systemIds.length
              || vessel.systemIds.includes(stringValue(objectValue(world.ship).currentSystemId))
            ))
            .map((vessel) => ({
              id: vessel.id,
              label: vessel.label,
              faction: vessel.faction,
              role: vessel.role
            }))
        },
        recent: {
          damagedRecently: nowMs - lastDamageAtMs <= 2500,
          lastDamageAtMs: Number.isFinite(lastDamageAtMs) ? lastDamageAtMs : null,
          supportCalled: Boolean(memory.supportCalled),
          warnedPlayer: Boolean(memory.warnedPlayer),
          protectedByPlayer: Boolean(memory.protectedByPlayer),
          repairedPower: Boolean(memory.repairedPower)
        },
        legalActionIds: definition.allowedActions.slice()
      };
    }

    normalizePolicyResult(characterId, rawValue, perception) {
      const definition = this.characterDefinition(characterId);
      const raw = objectValue(rawValue);
      const actionId = stringValue(raw.actionId || raw.action);
      const targetId = stringValue(raw.targetId || raw.target);
      const requestId = stringValue(raw.requestId || perception.requestId);
      if (raw.schema && raw.schema !== POLICY_RESULT_SCHEMA) {
        return {ok: false, reason: "policy-result-schema-invalid"};
      }
      if (raw.characterId && stringValue(raw.characterId) !== characterId) {
        return {ok: false, reason: "policy-result-character-mismatch"};
      }
      if (requestId !== perception.requestId) {
        return {ok: false, reason: "policy-result-request-stale"};
      }
      if (!definition.allowedActions.includes(actionId)) {
        return {ok: false, reason: "policy-result-action-not-allowed"};
      }
      return {
        ok: true,
        result: {
          schema: POLICY_RESULT_SCHEMA,
          requestId,
          characterId,
          actionId,
          targetId,
          rationale: stringValue(raw.rationale).slice(0, 240)
        }
      };
    }

    deterministicResult(characterId, perception) {
      const policy = this.policyRegistry.get(DETERMINISTIC_POLICY_ID);
      const raw = policy.chooseAction(perception);
      const normalized = this.normalizePolicyResult(
        characterId,
        {
          ...objectValue(raw),
          requestId: perception.requestId,
          characterId
        },
        perception
      );
      if (!normalized.ok) {
        throw new CharacterAIStateError(
          `Deterministic policy returned ${normalized.reason}.`,
          "deterministic-policy-invalid"
        );
      }
      return normalized.result;
    }

    requestPolicy(characterId, perception, nowMs) {
      const state = this.state.characters[characterId];
      const requestedPolicyId = stringValue(state.policyId || DETERMINISTIC_POLICY_ID);
      const policy = this.policyRegistry.get(requestedPolicyId);
      const pendingResult = this.pendingResults.get(characterId);
      if (pendingResult) {
        this.pendingResults.delete(characterId);
        this.pendingRequests.delete(characterId);
        if (nowMs - pendingResult.requestedAtMs
            <= this.definition.maxExternalDecisionAgeMs) {
          const normalized = this.normalizePolicyResult(
            characterId,
            pendingResult.value,
            pendingResult.perception
          );
          if (normalized.ok) {
            return {
              result: normalized.result,
              fallbackUsed: false,
              fallbackReason: ""
            };
          }
          return {
            result: this.deterministicResult(characterId, perception),
            fallbackUsed: true,
            fallbackReason: normalized.reason
          };
        }
      }

      if (!policy) {
        return {
          result: this.deterministicResult(characterId, perception),
          fallbackUsed: true,
          fallbackReason: "policy-unavailable"
        };
      }
      if (policy === this.policyRegistry.get(DETERMINISTIC_POLICY_ID)) {
        return {
          result: this.deterministicResult(characterId, perception),
          fallbackUsed: false,
          fallbackReason: ""
        };
      }

      const pending = this.pendingRequests.get(characterId);
      if (pending && nowMs <= pending.expiresAtMs) {
        return {
          result: this.deterministicResult(characterId, perception),
          fallbackUsed: true,
          fallbackReason: "policy-pending"
        };
      }
      if (pending) this.pendingRequests.delete(characterId);

      let raw;
      try {
        raw = policy.chooseAction(clone(perception));
      } catch {
        return {
          result: this.deterministicResult(characterId, perception),
          fallbackUsed: true,
          fallbackReason: "policy-threw"
        };
      }

      if (raw && typeof raw.then === "function") {
        const token = perception.requestId;
        this.pendingRequests.set(characterId, {
          token,
          requestedAtMs: nowMs,
          expiresAtMs: nowMs + this.definition.maxExternalDecisionAgeMs
        });
        Promise.resolve(raw).then((value) => {
          const current = this.pendingRequests.get(characterId);
          if (!current || current.token !== token) return;
          this.pendingResults.set(characterId, {
            value,
            perception: clone(perception),
            requestedAtMs: nowMs
          });
        }).catch(() => {
          const current = this.pendingRequests.get(characterId);
          if (!current || current.token !== token) return;
          this.pendingResults.set(characterId, {
            value: {actionId: "__policy-error__"},
            perception: clone(perception),
            requestedAtMs: nowMs
          });
        });
        return {
          result: this.deterministicResult(characterId, perception),
          fallbackUsed: true,
          fallbackReason: "policy-pending"
        };
      }

      const normalized = this.normalizePolicyResult(
        characterId,
        {
          ...objectValue(raw),
          requestId: perception.requestId,
          characterId
        },
        perception
      );
      if (!normalized.ok) {
        return {
          result: this.deterministicResult(characterId, perception),
          fallbackUsed: true,
          fallbackReason: normalized.reason
        };
      }
      return {
        result: normalized.result,
        fallbackUsed: false,
        fallbackReason: ""
      };
    }

    validateAction(characterId, result, perception) {
      const definition = this.characterDefinition(characterId);
      const state = this.state.characters[characterId];
      const actionId = result.actionId;
      if (!definition.allowedActions.includes(actionId)) {
        return {ok: false, reason: "action-not-allowed"};
      }
      if (state.status !== "active" || state.health <= 0) {
        return {ok: false, reason: "character-not-active"};
      }
      if (actionId === "attack_player") {
        if (!perception.player.visible) return {ok: false, reason: "player-not-visible"};
        if (perception.player.distance > definition.stats.attackRange) {
          return {ok: false, reason: "player-out-of-range"};
        }
        if (!perception.actor.attackReady) return {ok: false, reason: "attack-cooldown"};
      }
      if (["move_to_player", "follow_player", "warn_player"].includes(actionId)
          && !perception.player.visible) {
        return {ok: false, reason: "player-not-visible"};
      }
      if (actionId === "take_cover" && !perception.cover.recommendedId) {
        return {ok: false, reason: "cover-unavailable"};
      }
      if (actionId === "repair_power") {
        if (!perception.repair.available) return {ok: false, reason: "repair-target-out-of-range"};
        if (perception.ship.power === "online") return {ok: false, reason: "power-already-online"};
      }
      if (actionId === "call_support") {
        if (perception.recent.supportCalled) {
          return {ok: false, reason: "support-already-called"};
        }
        if (!this.definition.vessels.some((vessel) => vessel.id === result.targetId)) {
          return {ok: false, reason: "support-vessel-unknown"};
        }
        if (definition.supportVesselId
            && result.targetId !== definition.supportVesselId) {
          return {ok: false, reason: "support-vessel-not-authorized"};
        }
      }
      return {ok: true, reason: ""};
    }

    targetPosition(actionId, state, definition, perception) {
      if (actionId === "move_to_player") return perception.player.position;
      if (actionId === "follow_player") {
        return [
          perception.player.position[0] + 1.2,
          state.position[1],
          perception.player.position[2] + 1.2
        ];
      }
      if (actionId === "take_cover") return perception.cover.position;
      if (actionId === "retreat") return state.spawnPosition;
      if (actionId === "patrol") return perception.patrol.position;
      return state.position;
    }

    moveCharacter(state, definition, target, world) {
      const destination = vector3(target, state.position);
      const dx = destination[0] - state.position[0];
      const dz = destination[2] - state.position[2];
      const distance = Math.hypot(dx, dz);
      if (distance <= 0.04) return false;
      const step = Math.min(
        distance,
        definition.stats.speed * this.definition.tickIntervalMs / 1000
      );
      const nextX = state.position[0] + dx / distance * step;
      const nextZ = state.position[2] + dz / distance * step;
      const canOccupy = typeof world.canOccupy === "function"
        ? world.canOccupy
        : () => true;
      let changed = false;
      if (canOccupy(state.id, nextX, state.position[2])) {
        state.position[0] = nextX;
        changed = true;
      }
      if (canOccupy(state.id, state.position[0], nextZ)) {
        state.position[2] = nextZ;
        changed = true;
      }
      return changed;
    }

    executeAction(characterId, result, perception, worldValue, nowMs) {
      const world = objectValue(worldValue);
      const state = this.state.characters[characterId];
      const definition = this.characterDefinition(characterId);
      const actionId = result.actionId;
      const effects = [];
      const before = {
        position: state.position.slice(),
        health: state.health,
        memory: clone(state.memory)
      };

      if (["patrol", "move_to_player", "take_cover", "retreat", "follow_player"].includes(actionId)) {
        const moved = this.moveCharacter(
          state,
          definition,
          this.targetPosition(actionId, state, definition, perception),
          world
        );
        if (actionId === "patrol" && !moved) {
          const route = arrayValue(this.definition.patrolRoutes[definition.patrolRouteId]);
          if (route.length) state.patrolIndex = (state.patrolIndex + 1) % route.length;
        }
      } else if (actionId === "attack_player") {
        state.nextAttackAtMs = nowMs + definition.stats.attackCooldownMs;
        effects.push({
          type: "damage-player",
          characterId,
          amount: definition.stats.attackDamage
        });
      } else if (actionId === "call_support") {
        state.memory.supportCalled = true;
        effects.push({
          type: "support-requested",
          characterId,
          shipId: result.targetId || definition.supportVesselId,
          message: definition.supportText || `${state.label} transmitted a support request.`
        });
      } else if (actionId === "repair_power") {
        state.memory.repairedPower = true;
        effects.push({
          type: "repair-ship-power",
          characterId,
          targetId: definition.repairTargetId,
          value: "online"
        });
      } else if (actionId === "warn_player") {
        state.memory.warnedPlayer = true;
        effects.push({
          type: "character-message",
          characterId,
          message: definition.warningText || `${state.label} warns the player.`
        });
      }

      if (perception.player.visible) state.memory.playerSeen = true;
      state.currentActionId = actionId;
      state.currentTargetId = result.targetId;
      state.nextDecisionAtMs = nowMs + this.definition.tickIntervalMs;
      state.decisionCount += 1;
      state.memory.lastDecisionAtMs = nowMs;
      state.memory.lastRationale = result.rationale;

      return {
        effects,
        before,
        after: {
          position: state.position.slice(),
          health: state.health,
          memory: clone(state.memory)
        }
      };
    }

    record(reason, detail) {
      this.state.sequence += 1;
      this.state.lastUpdatedAtMs = finiteNumber(
        objectValue(detail).nowMs,
        this.state.lastUpdatedAtMs,
        0
      );
      const receipt = {
        schema: "game.characterAI.receipt.v1",
        receiptId: `character-receipt.${this.projectId}.${this.state.sequence}`,
        sequence: this.state.sequence,
        reason: stringValue(reason),
        ...clone(objectValue(detail))
      };
      this.state.receipts = [
        ...this.state.receipts,
        receipt
      ].slice(-this.definition.receiptLimit);
      this.persist();
      this.emit(reason, receipt);
      return receipt;
    }

    rebaseClock(nowMs) {
      if (!this.clockRebasePending) return false;
      const previousClock = finiteNumber(this.state.lastUpdatedAtMs, 0, 0);
      Object.values(this.state.characters).forEach((state) => {
        const decisionRemaining = Math.max(
          0,
          finiteNumber(state.nextDecisionAtMs, previousClock, 0) - previousClock
        );
        const attackRemaining = Math.max(
          0,
          finiteNumber(state.nextAttackAtMs, previousClock, 0) - previousClock
        );
        state.nextDecisionAtMs = nowMs + Math.min(
          decisionRemaining,
          this.definition.tickIntervalMs
        );
        const definition = this.characterDefinition(state.id);
        state.nextAttackAtMs = nowMs + Math.min(
          attackRemaining,
          definition?.stats?.attackCooldownMs || attackRemaining
        );
      });
      this.state.lastUpdatedAtMs = nowMs;
      this.clockRebasePending = false;
      return true;
    }

    step(worldValue = {}, nowMs = 0) {
      if (!this.definition.enabled) {
        return {changed: false, decisions: [], effects: [], summary: this.summary()};
      }
      const clock = finiteNumber(nowMs, 0, 0);
      this.rebaseClock(clock);
      const world = objectValue(worldValue);
      const decisions = [];
      const effects = [];

      const phase = stringValue(world.phase);
      Object.values(this.state.characters)
        .sort((left, right) => left.id.localeCompare(right.id))
        .forEach((state) => {
          if (state.status !== "active" || state.health <= 0) return;
          const definition = this.characterDefinition(state.id);
          if (!this.characterIsActiveInWorld(state.id, world)) return;
          if (clock < state.nextDecisionAtMs) return;
          const perception = this.buildPerception(state.id, world, clock);
          const selected = this.requestPolicy(state.id, perception, clock);
          let result = selected.result;
          let validation = this.validateAction(state.id, result, perception);
          let fallbackUsed = selected.fallbackUsed;
          let fallbackReason = selected.fallbackReason;
          if (!validation.ok) {
            const rejectedReason = validation.reason || "unknown";
            const fallback = this.deterministicResult(state.id, perception);
            const fallbackValidation = this.validateAction(state.id, fallback, perception);
            if (!fallbackValidation.ok) {
              result = {
                schema: POLICY_RESULT_SCHEMA,
                requestId: perception.requestId,
                characterId: state.id,
                actionId: state.kind === "npc" ? "hold_position" : "patrol",
                targetId: state.id,
                rationale: "safe deterministic idle"
              };
              validation = this.validateAction(state.id, result, perception);
            } else {
              result = fallback;
              validation = fallbackValidation;
            }
            fallbackUsed = true;
            fallbackReason = fallbackReason || `action-invalid:${rejectedReason}`;
          }
          if (!validation.ok) return;

          const executed = this.executeAction(
            state.id,
            result,
            perception,
            world,
            clock
          );
          const decision = {
            characterId: state.id,
            policyId: state.policyId,
            requestedAction: clone(result),
            actionId: result.actionId,
            targetId: result.targetId,
            rationale: result.rationale,
            fallbackUsed,
            fallbackReason,
            effects: clone(executed.effects),
            before: executed.before,
            after: executed.after,
            nowMs: clock
          };
          const receipt = this.record("character-decision", decision);
          decisions.push({...decision, receiptId: receipt.receiptId});
          effects.push(...executed.effects);
        });

      this.lastStepAtMs = clock;
      return {
        changed: decisions.length > 0,
        decisions,
        effects,
        summary: this.summary()
      };
    }

    damageCharacter(characterId, amount, options = {}) {
      const id = stringValue(characterId);
      const state = this.state.characters[id];
      if (!state) throw new CharacterAIStateError(`Unknown character ${id}.`);
      if (state.status !== "active" || state.health <= 0) {
        return {reused: true, character: clone(state), receipt: null};
      }
      const nowMs = finiteNumber(options.nowMs, 0, 0);
      const damage = finiteNumber(amount, 0, 0, state.maxHealth * 10);
      const beforeHealth = state.health;
      state.health = Math.max(0, state.health - damage);
      state.memory.lastDamageAtMs = nowMs;
      state.memory.lastDamageSource = stringValue(options.sourceId || "player");
      if (state.health <= 0) {
        state.status = "down";
        state.currentActionId = "down";
        state.currentTargetId = "";
      }
      const receipt = this.record("character-damaged", {
        characterId: id,
        sourceId: stringValue(options.sourceId || "player"),
        amount: damage,
        beforeHealth,
        afterHealth: state.health,
        status: state.status,
        nowMs
      });
      return {reused: false, character: clone(state), receipt};
    }

    markProtectedByPlayer(characterId, nowMs = 0) {
      const id = stringValue(characterId);
      const state = this.state.characters[id];
      if (!state) throw new CharacterAIStateError(`Unknown character ${id}.`);
      if (state.memory.protectedByPlayer) {
        return {reused: true, character: clone(state)};
      }
      state.memory.protectedByPlayer = true;
      state.memory.protectedAtMs = finiteNumber(nowMs, 0, 0);
      this.record("character-protected", {
        characterId: id,
        nowMs: finiteNumber(nowMs, 0, 0)
      });
      return {reused: false, character: clone(state)};
    }

    summary() {
      const characters = Object.values(this.state.characters);
      return {
        schema: STATE_VERSION,
        projectId: this.projectId,
        definitionFingerprint: this.definitionFingerprint,
        sequence: this.state.sequence,
        characterCount: characters.length,
        vesselCount: this.definition.vessels.length,
        activeCount: characters.filter((character) => character.status === "active").length,
        enemyCount: characters.filter((character) => character.kind === "enemy").length,
        npcCount: characters.filter((character) => character.kind === "npc").length,
        receiptCount: this.state.receipts.length,
        storageIssue: this.storageIssue,
        characters: characters.map((character) => ({
          id: character.id,
          label: character.label,
          kind: character.kind,
          health: character.health,
          maxHealth: character.maxHealth,
          status: character.status,
          actionId: character.currentActionId,
          policyId: character.policyId,
          position: character.position.slice()
        }))
      };
    }
  }

  function create(definition, options = {}) {
    return new CharacterAIRuntime(definition, options);
  }

  let currentRuntime = null;

  function ensure(projectId, definition, options = {}) {
    const id = stringValue(projectId || "game-project");
    const report = validateDefinition(definition);
    if (!report.ok) throw new CharacterAIDefinitionError(report);
    const fingerprint = definitionFingerprint(report.definition);
    if (
      currentRuntime
      && currentRuntime.projectId === id
      && currentRuntime.definitionFingerprint === fingerprint
    ) {
      return currentRuntime;
    }
    currentRuntime = new CharacterAIRuntime(report.definition, {
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
    POLICY_RESULT_SCHEMA,
    CAMPAIGN_EXTENSION_SCHEMA,
    STORAGE_PREFIX,
    DETERMINISTIC_POLICY_ID,
    ACTION_IDS,
    CharacterAIDefinitionError,
    CharacterAIStateError,
    DeterministicCharacterPolicy,
    RemoteCharacterPolicy,
    createRemotePolicy,
    normalizeDefinition,
    validateDefinition,
    definitionFingerprint,
    create,
    ensure,
    current,
    clearCurrent
  };

  global.MainComputerCharacterAIRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
