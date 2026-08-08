(function (global) {
  "use strict";

  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);
  const PaxProtectionActorDeployment = global.MainComputerPaxProtectionActorDeployment
    || (typeof require === "function" ? require("./pax-protection-actor-deployment.js") : null);
  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);
  const DEFAULT_CONFIG = PaxScenarioConfig?.config || PaxScenarioConfig?.PAX_SCENARIO_CONFIG || null;

  if (!DEFAULT_CONFIG?.ids?.scenarioId) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax protection encounter commands.");
  }
  if (!PaxProtectionActorDeployment?.create) {
    throw new Error("MainComputerPaxProtectionActorDeployment must load before Pax protection encounter commands.");
  }
  if (!PaxValueUtils?.stringValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax protection encounter commands.");
  }

  const {
    stringValue,
    cloneSnapshot
  } = PaxValueUtils;

  function createPaxProtectionEncounterCommands(options = {}) {
    const config = options.config || DEFAULT_CONFIG;
    const state = options.state || {};
    const SCENARIO_ID = config.ids.scenarioId;
    const PAX_SYSTEM_ID = config.ids.systemId;
    const PROTECTION_STAGE_ID = config.stages.protection;

    function nowMs(clockOptions = {}) {
      if (typeof options.nowMs === "function") return options.nowMs(clockOptions);
      if (Number.isFinite(Number(clockOptions.nowMs))) return Number(clockOptions.nowMs);
      if (typeof performance !== "undefined" && typeof performance.now === "function") {
        return performance.now();
      }
      return Date.now();
    }

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

    function revealArrivalPanel() {
      options.revealArrivalPanel?.();
    }

    function render() {
      options.render?.();
    }

    const actorDeployment = PaxProtectionActorDeployment.create({
      config,
      state,
      currentCharacterRuntime,
      activeShuttleRenderer: options.activeShuttleRenderer,
      nowMs
    });

    function syncProtection() {
      const scenarioRuntime = currentRuntime();
      if (!scenarioRuntime) return null;
      const characterRuntime = currentCharacterRuntime();
      if (!characterRuntime) return null;
      try {
        return scenarioRuntime.syncCharacterRuntime(
          SCENARIO_ID,
          characterRuntime,
          {nowMs: nowMs({})}
        );
      } catch {
        return null;
      }
    }

    function isDurableCommittedArrival(navigation = {}) {
      return stringValue(navigation.currentSystemId) === PAX_SYSTEM_ID
        && stringValue(navigation.travelPhase) === "in-system"
        && Boolean(stringValue(navigation.lastCompletedRouteId))
        && navigation.lastArrivalAtMs !== null
        && navigation.lastArrivalAtMs !== undefined
        && Number.isFinite(Number(navigation.lastArrivalAtMs));
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

    function cameraRelativeBoardingPositions() {
      return actorDeployment.cameraRelativeBoardingPositions();
    }

    function forceProtectionEncounterCharacters(reason = "pax-hard-kickoff", commandOptions = {}) {
      return actorDeployment.forceProtectionEncounterCharacters(reason, commandOptions);
    }

    function resetProtectionEncounter(reason = "pax-protection-reset", commandOptions = {}) {
      const runtime = currentRuntime();
      const clock = nowMs(commandOptions);
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
      state.lastHardKickoff = null;
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

      state.recoveredCharacterRuntime = currentCharacterRuntime();
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

    function startOrRecoverProtectionEncounter(reason = "pax-hard-kickoff", commandOptions = {}) {
      const runtime = currentRuntime();
      const clock = nowMs(commandOptions);
      if (!runtime?.view) {
        return {
          handled: false,
          started: false,
          forced: false,
          reason: "scenario-runtime-unavailable"
        };
      }
      state.runtime = runtime;
      if (runtime.state?.activeSystemId !== PAX_SYSTEM_ID && commandOptions.allowSystemChange) {
        runtime.setActiveSystemId?.(PAX_SYSTEM_ID, {
          nowMs: clock,
          record: commandOptions.recordSystemChange !== false
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
      if (!before.visible && commandOptions.allowSystemChange !== true) {
        return {
          handled: false,
          started: false,
          forced: false,
          reason: "pax-not-visible"
        };
      }
      if (before.state?.status === "active"
          && before.state.stageId !== PROTECTION_STAGE_ID
          && commandOptions.restartProtectionEncounter === true) {
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
          routeId: stringValue(commandOptions.routeId),
          navigationSequence: Number(commandOptions.navigationSequence) || 0
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
      const runtime = currentRuntime();
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

    function setWorldSnapshot(snapshot = null) {
      state.worldSnapshot = snapshot && typeof snapshot === "object"
        ? cloneSnapshot(snapshot)
        : null;
      render();
      return state.worldSnapshot;
    }

    return Object.freeze({
      syncProtection,
      isDurableCommittedArrival,
      isLegacyPaxOccupancy,
      legacyActivationKey,
      arrivalActivationKey,
      cameraRelativeBoardingPositions,
      forceProtectionEncounterCharacters,
      resetProtectionEncounter,
      startOrRecoverProtectionEncounter,
      handleNavigation,
      setWorldSnapshot
    });
  }

  const api = Object.freeze({
    create: createPaxProtectionEncounterCommands,
    createPaxProtectionEncounterCommands
  });

  global.MainComputerPaxProtectionEncounterCommands = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
