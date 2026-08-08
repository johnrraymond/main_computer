(function (global) {
  "use strict";

  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);
  const PaxProtectionEncounterModel = global.MainComputerPaxProtectionEncounterModel
    || (typeof require === "function" ? require("./pax-protection-encounter-model.js") : null);
  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);
  const DEFAULT_CONFIG = PaxScenarioConfig?.config || PaxScenarioConfig?.PAX_SCENARIO_CONFIG || null;

  if (!DEFAULT_CONFIG?.ids?.scenarioId) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax protection encounter controller.");
  }
  if (!PaxProtectionEncounterModel?.create) {
    throw new Error("MainComputerPaxProtectionEncounterModel must load before Pax protection encounter controller.");
  }
  if (!PaxValueUtils?.objectValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax protection encounter controller.");
  }

  const {
    objectValue,
    stringValue,
    finiteNumber,
    vector3,
    cloneSnapshot
  } = PaxValueUtils;

  function createPaxProtectionEncounterController(options = {}) {
    const config = options.config || DEFAULT_CONFIG;
    const state = options.state || {};
    const SCENARIO_ID = config.ids.scenarioId;
    const PAX_SYSTEM_ID = config.ids.systemId;
    const PROTECTION_STAGE_ID = config.stages.protection;
    const PAX_PROTECTION_RECOVERY = config.recovery.actions;
    const PAX_RECONCILIATION_MODE = config.recovery.reconciliationModes;
    const PAX_RECONCILIATION_REASON = config.recovery.reconciliationReasons;
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

    const encounterModel = PaxProtectionEncounterModel.create({
      config,
      state,
      currentRuntime,
      currentCharacterRuntime
    });

    function revealArrivalPanel() {
      options.revealArrivalPanel?.();
    }

    function render() {
      options.render?.();
    }

    function activeShuttleRenderer() {
      return options.activeShuttleRenderer?.()
        || global.document
          ?.querySelector?.("#webgl-demo")
          ?.__mainComputerShuttle3dRenderer
        || null;
    }

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

    function paxProtectionStateLabels() {
      return encounterModel.paxProtectionStateLabels();
    }

    function paxProtectionRecoveryActions() {
      return encounterModel.paxProtectionRecoveryActions();
    }

    function paxProtectionEncounterIdentity(diagnosticOptions = {}) {
      return encounterModel.paxProtectionEncounterIdentity(diagnosticOptions);
    }

    function paxProtectionEncounterInstanceDescriptor(diagnosticOptions = {}) {
      return encounterModel.paxProtectionEncounterInstanceDescriptor(diagnosticOptions);
    }

    function classifyBoarderGroup(characterRuntime = currentCharacterRuntime()) {
      return encounterModel.classifyBoarderGroup(characterRuntime);
    }

    function classifyPaxProtectionState(diagnosticOptions = {}) {
      return encounterModel.classifyPaxProtectionState(diagnosticOptions);
    }

    function paxProtectionReconciliationPlan(classification, diagnosticOptions = {}) {
      return encounterModel.paxProtectionReconciliationPlan(classification, diagnosticOptions);
    }

    function paxProtectionReconciliationOptions(
      mode = PAX_RECONCILIATION_MODE.passive,
      overrides = {}
    ) {
      return encounterModel.paxProtectionReconciliationOptions(mode, overrides);
    }

    function paxProtectionEncounterSnapshotData(diagnosticOptions = {}) {
      return encounterModel.paxProtectionEncounterSnapshotData(diagnosticOptions);
    }

    function buildPaxProtectionEncounterSnapshot(
      classification,
      plan,
      reconciliationOptions = {}
    ) {
      return encounterModel.buildPaxProtectionEncounterSnapshot(
        classification,
        plan,
        reconciliationOptions
      );
    }

    function paxProtectionEncounterSnapshot(diagnosticOptions = {}) {
      return encounterModel.paxProtectionEncounterSnapshot(diagnosticOptions);
    }

    function diagnosePaxProtectionEncounter(diagnosticOptions = {}) {
      return encounterModel.diagnosePaxProtectionEncounter(diagnosticOptions);
    }

    function paxProtectionRecoverySucceeded(plan, result) {
      return encounterModel.paxProtectionRecoverySucceeded(plan, result);
    }

    function performPaxProtectionRecovery(plan, reason, recoveryOptions = {}) {
      if (plan.action === PAX_PROTECTION_RECOVERY.reviveBoarders) {
        state.lastHardKickoff = null;
        return forceProtectionEncounterCharacters(reason, {nowMs: nowMs(recoveryOptions)});
      }
      if (plan.action === PAX_PROTECTION_RECOVERY.restartEncounter) {
        return resetProtectionEncounter(reason, {nowMs: nowMs(recoveryOptions)});
      }
      return {forced: false, reset: false, reason: "no-recovery-action"};
    }
    function reconcilePaxProtectionState(
      reason = PAX_RECONCILIATION_REASON.automatic,
      reconciliationOptions = {}
    ) {
      if (state.recoveryInProgress) {
        return {recovered: false, reason: "recovery-in-progress"};
      }

      const snapshotData = paxProtectionEncounterSnapshotData(reconciliationOptions);
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

      state.recoveryInProgress = true;
      try {
        const result = performPaxProtectionRecovery(plan, reason, reconciliationOptions);
        const recovered = paxProtectionRecoverySucceeded(plan, result);
        state.lastAutomaticRecovery = {
          reason: stringValue(reason),
          recovered,
          atMs: nowMs(reconciliationOptions),
          classification: classification.status,
          action: plan.action,
          result
        };
        if (recovered && reconciliationOptions.markCharacterRuntime !== false) {
          state.recoveredCharacterRuntime = classification.characterRuntime;
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
        state.recoveryInProgress = false;
      }
    }

    function requestProtectionEncounterReconciliation(
      reason = PAX_RECONCILIATION_REASON.automatic,
      mode = PAX_RECONCILIATION_MODE.passive,
      reconciliationOptions = {}
    ) {
      return reconcilePaxProtectionState(
        reason,
        paxProtectionReconciliationOptions(mode, Object.assign({reason}, objectValue(reconciliationOptions)))
      );
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
      paxProtectionStateLabels,
      paxProtectionRecoveryActions,
      paxProtectionEncounterIdentity,
      paxProtectionEncounterInstanceDescriptor,
      classifyBoarderGroup,
      classifyPaxProtectionState,
      paxProtectionReconciliationPlan,
      paxProtectionReconciliationOptions,
      paxProtectionEncounterSnapshotData,
      buildPaxProtectionEncounterSnapshot,
      paxProtectionEncounterSnapshot,
      diagnosePaxProtectionEncounter,
      performPaxProtectionRecovery,
      paxProtectionRecoverySucceeded,
      reconcilePaxProtectionState,
      requestProtectionEncounterReconciliation,
      setWorldSnapshot
    });
  }

  const api = Object.freeze({
    create: createPaxProtectionEncounterController,
    createPaxProtectionEncounterController
  });

  global.MainComputerPaxProtectionEncounterController = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
