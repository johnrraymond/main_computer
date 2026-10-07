(function (global) {
  "use strict";

  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);
  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);
  const DEFAULT_CONFIG = PaxScenarioConfig?.config || PaxScenarioConfig?.PAX_SCENARIO_CONFIG || null;

  if (!DEFAULT_CONFIG?.ids?.scenarioId) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax protection reconciliation.");
  }
  if (!PaxValueUtils?.objectValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax protection reconciliation.");
  }

  const {
    objectValue,
    stringValue
  } = PaxValueUtils;

  function createPaxProtectionReconciliation(options = {}) {
    const config = options.config || DEFAULT_CONFIG;
    const state = options.state || {};
    const encounterModel = options.encounterModel || null;
    const commands = options.commands || null;
    const PAX_PROTECTION_RECOVERY = config.recovery.actions;
    const PAX_RECONCILIATION_MODE = config.recovery.reconciliationModes;
    const PAX_RECONCILIATION_REASON = config.recovery.reconciliationReasons;

    if (!encounterModel?.classifyPaxProtectionState) {
      throw new Error("MainComputerPaxProtectionReconciliation requires Pax protection encounter model.");
    }
    if (!commands?.forceProtectionEncounterCharacters || !commands?.resetProtectionEncounter) {
      throw new Error("MainComputerPaxProtectionReconciliation requires Pax protection encounter commands.");
    }

    function nowMs(clockOptions = {}) {
      if (typeof options.nowMs === "function") return options.nowMs(clockOptions);
      if (Number.isFinite(Number(clockOptions.nowMs))) return Number(clockOptions.nowMs);
      if (typeof performance !== "undefined" && typeof performance.now === "function") {
        return performance.now();
      }
      return Date.now();
    }

    function performPaxProtectionRecovery(plan, reason, recoveryOptions = {}) {
      if (plan.action === PAX_PROTECTION_RECOVERY.reviveBoarders) {
        state.lastHardKickoff = null;
        return commands.forceProtectionEncounterCharacters(reason, {nowMs: nowMs(recoveryOptions)});
      }
      if (plan.action === PAX_PROTECTION_RECOVERY.restartEncounter) {
        return commands.resetProtectionEncounter(reason, {nowMs: nowMs(recoveryOptions)});
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

      const snapshotData = encounterModel.paxProtectionEncounterSnapshotData(reconciliationOptions);
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
        const recovered = encounterModel.paxProtectionRecoverySucceeded(plan, result);
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
        encounterModel.paxProtectionReconciliationOptions(mode, Object.assign({reason}, objectValue(reconciliationOptions)))
      );
    }

    return Object.freeze({
      performRecovery: performPaxProtectionRecovery,
      reconcile: reconcilePaxProtectionState,
      request: requestProtectionEncounterReconciliation
    });
  }

  const api = Object.freeze({
    create: createPaxProtectionReconciliation,
    createPaxProtectionReconciliation
  });

  global.MainComputerPaxProtectionReconciliation = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
