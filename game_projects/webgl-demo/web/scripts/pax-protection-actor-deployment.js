(function (global) {
  "use strict";

  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);
  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);
  const DEFAULT_CONFIG = PaxScenarioConfig?.config || PaxScenarioConfig?.PAX_SCENARIO_CONFIG || null;

  if (!DEFAULT_CONFIG?.ids?.scenarioId) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax protection actor deployment.");
  }
  if (!PaxValueUtils?.objectValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax protection actor deployment.");
  }

  const {
    stringValue,
    vector3
  } = PaxValueUtils;

  function createPaxProtectionActorDeployment(options = {}) {
    const config = options.config || DEFAULT_CONFIG;
    const state = options.state || {};
    const HARD_KICKOFF_POSITIONS = config.actors.hardKickoffPositions;
    const BOARDER_IDS = config.actors.boarderIds;

    function nowMs(clockOptions = {}) {
      if (typeof options.nowMs === "function") return options.nowMs(clockOptions);
      if (Number.isFinite(Number(clockOptions.nowMs))) return Number(clockOptions.nowMs);
      if (typeof performance !== "undefined" && typeof performance.now === "function") {
        return performance.now();
      }
      return Date.now();
    }

    function currentCharacterRuntime() {
      return options.currentCharacterRuntime?.()
        || state.characterRuntime
        || global.MainComputerCharacterAIRuntime?.current?.()
        || null;
    }

    function activeShuttleRenderer() {
      return options.activeShuttleRenderer?.()
        || global.document
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

    function forceProtectionEncounterCharacters(reason = "pax-hard-kickoff", commandOptions = {}) {
      const runtime = currentCharacterRuntime();
      const clock = nowMs(commandOptions);
      if (!runtime?.forceCharacterState) {
        return {
          forced: false,
          reason: "character-force-unavailable",
          source: stringValue(reason)
        };
      }
      const source = stringValue(reason || "pax-hard-kickoff");
      const deploymentPositions = cameraRelativeBoardingPositions();
      state.lastHardKickoff = {
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

    return Object.freeze({
      cameraRelativeBoardingPositions,
      forceProtectionEncounterCharacters
    });
  }

  const api = Object.freeze({
    create: createPaxProtectionActorDeployment,
    createPaxProtectionActorDeployment
  });

  global.MainComputerPaxProtectionActorDeployment = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
