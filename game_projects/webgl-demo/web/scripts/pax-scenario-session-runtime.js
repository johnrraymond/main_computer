(function (global) {
  "use strict";

  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);

  if (!PaxValueUtils?.objectValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax scenario session runtime.");
  }

  const {
    objectValue
  } = PaxValueUtils;

  function createPaxScenarioSessionRuntime(options = {}) {
    const config = options.config || null;
    const sessionState = options.sessionState || null;
    const presentation = options.presentation || null;
    const protectionEncounter = options.protectionEncounter || null;
    const globalRef = options.globalRef || global;

    if (!config?.ids?.scenarioId) {
      throw new Error("MainComputerPaxScenarioSessionRuntime requires Pax scenario config.");
    }
    if (!sessionState?.state) {
      throw new Error("MainComputerPaxScenarioSessionRuntime requires session state.");
    }
    if (!presentation?.renderPresentation) {
      throw new Error("MainComputerPaxScenarioSessionRuntime requires session presentation bridge.");
    }
    const encounterCommands = objectValue(protectionEncounter?.commands);
    const encounterDiagnostics = objectValue(protectionEncounter?.diagnostics);
    const encounterReconciliation = objectValue(protectionEncounter?.reconciliation);

    if (typeof encounterCommands.syncProtection !== "function") {
      throw new Error("MainComputerPaxScenarioSessionRuntime requires protection encounter command boundary.");
    }
    if (typeof encounterDiagnostics.snapshot !== "function") {
      throw new Error("MainComputerPaxScenarioSessionRuntime requires protection encounter diagnostic boundary.");
    }
    if (typeof encounterReconciliation.request !== "function") {
      throw new Error("MainComputerPaxScenarioSessionRuntime requires protection encounter reconciliation boundary.");
    }

    const uiState = sessionState.state;
    const scenarioId = config.ids.scenarioId;
    const protectionStageId = config.stages.protection;
    const reconciliationModes = config.recovery.reconciliationModes;
    const reconciliationReasons = config.recovery.reconciliationReasons;

    function nodes(documentRef = globalRef.document) {
      return presentation.nodes(documentRef);
    }

    function nowMs(options = {}) {
      return sessionState.nowMs(options);
    }

    function currentRuntime() {
      return sessionState.currentRuntime();
    }

    function currentCharacterRuntime() {
      return sessionState.currentCharacterRuntime();
    }

    function activeShuttleRenderer() {
      return sessionState.activeShuttleRenderer();
    }

    function briefingAcknowledged(cueKey) {
      return sessionState.briefingAcknowledged(cueKey);
    }

    function acknowledgeBriefing(view = null) {
      const currentView = view
        || uiState.runtime?.view?.(scenarioId)
        || null;
      const result = sessionState.acknowledgeBriefing(currentView);
      render();
      return result;
    }

    function syncProtection() {
      return encounterCommands.syncProtection();
    }

    function forceProtectionEncounterCharacters(reason = "pax-hard-kickoff", options = {}) {
      return encounterCommands.forceCharacterStates(reason, options);
    }

    function resetProtectionEncounter(reason = "pax-protection-reset", options = {}) {
      return encounterCommands.resetProtectionEncounter(reason, options);
    }

    function startOrRecoverProtectionEncounter(reason = "pax-hard-kickoff", options = {}) {
      return encounterCommands.startOrRecover(reason, options);
    }

    function handleNavigation(navigation = {}) {
      return encounterCommands.handleNavigation(navigation);
    }

    function paxProtectionEncounterIdentity(options = {}) {
      return encounterDiagnostics.encounterIdentity(options);
    }

    function paxProtectionEncounterInstanceDescriptor(options = {}) {
      return encounterDiagnostics.encounterInstanceDescriptor(options);
    }

    function paxProtectionEncounterSnapshot(options = {}) {
      return encounterDiagnostics.snapshot(options);
    }

    function diagnosePaxProtectionEncounter(options = {}) {
      return encounterDiagnostics.diagnose(options);
    }

    function requestProtectionEncounterReconciliation(
      reason = reconciliationReasons.automatic,
      mode = reconciliationModes.passive,
      options = {}
    ) {
      return encounterReconciliation.request(
        reason,
        mode,
        options
      );
    }

    function render() {
      const ui = nodes();
      const runtime = uiState.runtime
        || globalRef.MainComputerSystemScenarioRuntime?.current?.()
        || null;
      if (!ui.root) return null;
      if (!runtime?.view) {
        presentation.hideScenarioChrome(ui);
        return null;
      }
      uiState.runtime = runtime;
      syncProtection();
      let view = runtime.view(scenarioId);
      if (!view) {
        presentation.hideScenarioChrome(ui);
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
          view = recovered.view || runtime.view(scenarioId) || view;
        } finally {
          uiState.recoveryInProgress = false;
        }
        state = objectValue(view.state);
      }

      if (view.visible
          && state.status === "active"
          && state.stageId === protectionStageId
          && !uiState.lastHardKickoff
          && !uiState.recoveryInProgress) {
        forceProtectionEncounterCharacters("visible-protection-hard-kickoff", {nowMs: nowMs({})});
      }

      const viewModel = presentation.paxProtectionPresentationViewModel(view);
      presentation.renderPresentation(ui, viewModel);
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
      return encounterCommands.setWorldSnapshot(snapshot);
    }

    function setRuntime(runtime) {
      if (uiState.unsubscribe) uiState.unsubscribe();
      uiState.runtime = runtime || null;
      uiState.unsubscribe = runtime?.subscribe?.(() => {
        requestProtectionEncounterReconciliation(
          reconciliationReasons.scenarioState,
          reconciliationModes.passive
        );
        render();
      }) || null;
      requestProtectionEncounterReconciliation(
        reconciliationReasons.scenarioRuntimeAttach,
        reconciliationModes.startupAttach
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
          reconciliationReasons.characterRuntimeAttach,
          reconciliationModes.startupAttach,
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
          reconciliationReasons.characterState,
          reconciliationModes.passive
        );
        render();
      }) || null;
      render();

      if (recovery.recovered && typeof globalRef.CustomEvent === "function") {
        globalRef.dispatchEvent?.(new globalRef.CustomEvent("main-computer-pax-boarders-recovered", {
          detail: recovery
        }));
      }
      return uiState.characterRuntime;
    }

    function eventNowMs() {
      if (typeof globalRef.performance?.now === "function") return globalRef.performance.now();
      return nowMs({});
    }

    function bind() {
      if (uiState.bound) return;
      uiState.bound = true;
      const ui = nodes();
      ui.briefingAck?.addEventListener("click", () => acknowledgeBriefing());
      ui.hardStartButton?.addEventListener("click", () => runUi(() => (
        startOrRecoverProtectionEncounter("manual-hard-start", {
          nowMs: eventNowMs(),
          allowSystemChange: true,
          restartProtectionEncounter: true
        })
      )));
      ui.proceed?.addEventListener("click", () => runUi(() => (
        uiState.runtime.proceedToConference(scenarioId, {
          nowMs: eventNowMs()
        })
      )));
      globalRef.addEventListener?.("main-computer-system-scenario-change", render);
      globalRef.addEventListener?.("main-computer-character-ai-change", () => {
        const current = globalRef.MainComputerCharacterAIRuntime?.current?.();
        if (current && current !== uiState.characterRuntime) {
          setCharacterRuntime(current);
        } else {
          syncProtection();
          render();
        }
      });
      setRuntime(globalRef.MainComputerSystemScenarioRuntime?.current?.() || null);
      setCharacterRuntime(globalRef.MainComputerCharacterAIRuntime?.current?.() || null);
      render();
    }

    function paxProtectionDebugState() {
      return sessionState.debugState();
    }

    const diagnostics = Object.freeze({
      snapshot: paxProtectionEncounterSnapshot,
      diagnose: diagnosePaxProtectionEncounter,
      state: paxProtectionDebugState,
      characterRows: presentation.characterRows,
      encounterIdentity: paxProtectionEncounterIdentity,
      encounterInstanceDescriptor: paxProtectionEncounterInstanceDescriptor
    });

    const commands = Object.freeze({
      startOrRecover: startOrRecoverProtectionEncounter,
      requestReconciliation: requestProtectionEncounterReconciliation,
      resetProtectionEncounter,
      forceCharacterStates: forceProtectionEncounterCharacters
    });

    const presentationModel = Object.freeze({
      viewModel: presentation.paxProtectionPresentationViewModel,
      presentationViewModel: presentation.paxProtectionPresentationViewModel,
      objective: presentation.objectivePresentation,
      objectivePresentation: presentation.objectivePresentation,
      threat: presentation.threatPresentation,
      threatPresentation: presentation.threatPresentation,
      hardStart: presentation.hardStartPresentation,
      hardStartPresentation: presentation.hardStartPresentation,
      requirementText: presentation.requirementText,
      stageStatus: presentation.stageStatus,
      characterRows: presentation.characterRows
    });

    const presentationDom = Object.freeze({
      nodes,
      hideScenarioChrome: presentation.hideScenarioChrome,
      renderPresentation: presentation.renderPresentation,
      renderMissionCues: presentation.renderMissionCues,
      renderThreatTracker: presentation.renderThreatTracker,
      renderHardStart: presentation.renderHardStart,
      renderCharacters: presentation.renderCharacters,
      renderEvidence: presentation.renderEvidence,
      renderResolutions: presentation.renderResolutions,
      renderOutcome: presentation.renderOutcome
    });

    const runtime = Object.freeze({
      setRuntime,
      setCharacterRuntime,
      handleNavigation,
      setWorldSnapshot,
      bind,
      render
    });

    return Object.freeze({
      diagnostics,
      commands,
      presentation: Object.freeze({
        model: presentationModel,
        dom: presentationDom
      }),
      runtime
    });
  }

  const api = Object.freeze({
    create: createPaxScenarioSessionRuntime,
    createPaxScenarioSessionRuntime
  });

  global.MainComputerPaxScenarioSessionRuntime = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
