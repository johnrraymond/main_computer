(function (global) {
  "use strict";

  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);
  const PaxPresentationModel = global.MainComputerPaxPresentationModel
    || (typeof require === "function" ? require("./pax-presentation-model.js") : null);
  const PaxDomRenderer = global.MainComputerPaxDomRenderer
    || (typeof require === "function" ? require("./pax-dom-renderer.js") : null);
  const PaxScenarioSessionState = global.MainComputerPaxScenarioSessionState
    || (typeof require === "function" ? require("./pax-scenario-session-state.js") : null);
  const PaxProtectionEncounterController = global.MainComputerPaxProtectionEncounterController
    || (typeof require === "function" ? require("./pax-protection-encounter-controller.js") : null);
  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);

  if (!PaxScenarioConfig?.config) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax scenario session.");
  }
  if (!PaxPresentationModel?.viewModel) {
    throw new Error("MainComputerPaxPresentationModel must load before Pax scenario session.");
  }
  if (!PaxDomRenderer?.create) {
    throw new Error("MainComputerPaxDomRenderer must load before Pax scenario session.");
  }
  if (!PaxScenarioSessionState?.create) {
    throw new Error("MainComputerPaxScenarioSessionState must load before Pax scenario session.");
  }
  if (!PaxProtectionEncounterController?.create) {
    throw new Error("MainComputerPaxProtectionEncounterController must load before Pax scenario session.");
  }
  if (!PaxValueUtils?.objectValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax scenario session.");
  }

  const {
    objectValue
  } = PaxValueUtils;

  const PAX_SCENARIO_CONFIG = PaxScenarioConfig.config;
  const SCENARIO_ID = PAX_SCENARIO_CONFIG.ids.scenarioId;
  const PAX_SYSTEM_ID = PAX_SCENARIO_CONFIG.ids.systemId;
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

  const sessionState = PaxScenarioSessionState.create({
    config: PAX_SCENARIO_CONFIG,
    globalRef: global,
    briefingCueKey: (view) => PaxPresentationModel.briefingCueKey(view, {
      config: PAX_SCENARIO_CONFIG
    })
  });
  const uiState = sessionState.state;

  const paxDomRenderer = PaxDomRenderer.create({
    config: PAX_SCENARIO_CONFIG,
    presentationModel: PaxPresentationModel
  });

  function nodes(documentRef = global.document) {
    return paxDomRenderer.nodes(documentRef);
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
      || uiState.runtime?.view?.(SCENARIO_ID)
      || null;
    const result = sessionState.acknowledgeBriefing(currentView);
    render();
    return result;
  }

  function presentationOptions(options = {}) {
    return Object.assign({}, objectValue(options), {
      config: PAX_SCENARIO_CONFIG,
      characterRuntime: currentCharacterRuntime(),
      worldSnapshot: Object.prototype.hasOwnProperty.call(options, "worldSnapshot")
        ? options.worldSnapshot
        : uiState.worldSnapshot,
      briefingAcknowledged,
      running: uiState.running,
      lastError: uiState.lastError
    });
  }

  function characterRows(view) {
    return PaxPresentationModel.characterRows(view, presentationOptions());
  }

  function requirementText(resolution) {
    return PaxPresentationModel.requirementText(resolution);
  }

  function stageStatus(view) {
    return PaxPresentationModel.stageStatus(view, presentationOptions());
  }

  function objectivePresentation(view) {
    return PaxPresentationModel.objectivePresentation(view, presentationOptions());
  }

  function threatPresentation(view, snapshot = uiState.worldSnapshot) {
    return PaxPresentationModel.threatPresentation(
      view,
      snapshot,
      presentationOptions({worldSnapshot: snapshot})
    );
  }

  function hardStartPresentation(view) {
    return PaxPresentationModel.hardStartPresentation(view, presentationOptions());
  }

  function isPaxPresentationViewModel(value) {
    return PaxPresentationModel.isPaxPresentationViewModel(value);
  }

  function toPaxPresentationViewModel(viewOrPresentation, options = {}) {
    return PaxPresentationModel.toPaxPresentationViewModel(
      viewOrPresentation,
      presentationOptions(options)
    );
  }

  function paxProtectionPresentationViewModel(view, options = {}) {
    return PaxPresentationModel.paxProtectionPresentationViewModel(
      view,
      presentationOptions(options)
    );
  }

  function domRenderContext(options = {}) {
    const context = objectValue(options);
    const presentationContext = objectValue(context.presentationOptions);
    return Object.assign({}, context, {
      running: uiState.running,
      runtime: context.runtime || currentRuntime(),
      scenarioId: SCENARIO_ID,
      runUi,
      nowMs: () => nowMs({}),
      presentationOptions: presentationOptions(presentationContext)
    });
  }

  function renderCharacters(container, viewOrPresentation, options = {}) {
    return paxDomRenderer.renderCharacters(
      container,
      viewOrPresentation,
      domRenderContext(options)
    );
  }

  function renderEvidence(container, viewOrPresentation, options = {}) {
    return paxDomRenderer.renderEvidence(
      container,
      viewOrPresentation,
      domRenderContext(options)
    );
  }

  function renderResolutions(container, viewOrPresentation, options = {}) {
    return paxDomRenderer.renderResolutions(
      container,
      viewOrPresentation,
      domRenderContext(options)
    );
  }

  function renderOutcome(container, viewOrPresentation, options = {}) {
    return paxDomRenderer.renderOutcome(
      container,
      viewOrPresentation,
      domRenderContext(options)
    );
  }

  function renderThreatTracker(ui, viewOrPresentation, options = {}) {
    return paxDomRenderer.renderThreatTracker(
      ui,
      viewOrPresentation,
      domRenderContext(options)
    );
  }

  function renderMissionCues(ui, viewOrPresentation, options = {}) {
    return paxDomRenderer.renderMissionCues(
      ui,
      viewOrPresentation,
      domRenderContext(options)
    );
  }

  function renderHardStart(ui, viewOrPresentation, options = {}) {
    return paxDomRenderer.renderHardStart(
      ui,
      viewOrPresentation,
      domRenderContext(options)
    );
  }

  function renderPresentation(ui, viewOrPresentation, options = {}) {
    return paxDomRenderer.renderPresentation(
      ui,
      viewOrPresentation,
      domRenderContext(options)
    );
  }

  function hideScenarioChrome(ui) {
    return paxDomRenderer.hideScenarioChrome(ui);
  }

  function revealArrivalPanel() {
    return paxDomRenderer.revealArrivalPanel();
  }

  const protectionEncounterController = PaxProtectionEncounterController.create({
    config: PAX_SCENARIO_CONFIG,
    state: uiState,
    currentRuntime,
    currentCharacterRuntime,
    nowMs,
    activeShuttleRenderer,
    revealArrivalPanel,
    render
  });

  function syncProtection() {
    return protectionEncounterController.syncProtection();
  }

  function forceProtectionEncounterCharacters(reason = "pax-hard-kickoff", options = {}) {
    return protectionEncounterController.forceProtectionEncounterCharacters(reason, options);
  }

  function resetProtectionEncounter(reason = "pax-protection-reset", options = {}) {
    return protectionEncounterController.resetProtectionEncounter(reason, options);
  }

  function startOrRecoverProtectionEncounter(reason = "pax-hard-kickoff", options = {}) {
    return protectionEncounterController.startOrRecoverProtectionEncounter(reason, options);
  }

  function handleNavigation(navigation = {}) {
    return protectionEncounterController.handleNavigation(navigation);
  }

  function paxProtectionEncounterIdentity(options = {}) {
    return protectionEncounterController.paxProtectionEncounterIdentity(options);
  }

  function paxProtectionEncounterInstanceDescriptor(options = {}) {
    return protectionEncounterController.paxProtectionEncounterInstanceDescriptor(options);
  }

  function paxProtectionEncounterSnapshot(options = {}) {
    return protectionEncounterController.paxProtectionEncounterSnapshot(options);
  }

  function diagnosePaxProtectionEncounter(options = {}) {
    return protectionEncounterController.diagnosePaxProtectionEncounter(options);
  }

  function requestProtectionEncounterReconciliation(
    reason = PAX_RECONCILIATION_REASON.automatic,
    mode = PAX_RECONCILIATION_MODE.passive,
    options = {}
  ) {
    return protectionEncounterController.requestProtectionEncounterReconciliation(
      reason,
      mode,
      options
    );
  }

  function render() {
    const ui = nodes();
    const runtime = uiState.runtime
      || global.MainComputerSystemScenarioRuntime?.current?.()
      || null;
    if (!ui.root) return null;
    if (!runtime?.view) {
      hideScenarioChrome(ui);
      return null;
    }
    uiState.runtime = runtime;
    syncProtection();
    let view = runtime.view(SCENARIO_ID);
    if (!view) {
      hideScenarioChrome(ui);
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
    renderPresentation(ui, presentation);
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
    return protectionEncounterController.setWorldSnapshot(snapshot);
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
    if (uiState.bound) return;
    uiState.bound = true;
    const ui = nodes();
    ui.briefingAck?.addEventListener("click", () => acknowledgeBriefing());
    ui.hardStartButton?.addEventListener("click", () => runUi(() => (
      startOrRecoverProtectionEncounter("manual-hard-start", {
        nowMs: performance.now(),
        allowSystemChange: true,
        restartProtectionEncounter: true
      })
    )));
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
    return sessionState.debugState();
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
    objective: objectivePresentation,
    objectivePresentation,
    threat: threatPresentation,
    threatPresentation,
    hardStart: hardStartPresentation,
    hardStartPresentation,
    requirementText,
    stageStatus,
    characterRows
  });

  const paxPresentationDom = Object.freeze({
    nodes,
    hideScenarioChrome,
    renderPresentation,
    renderMissionCues,
    renderThreatTracker,
    renderHardStart,
    renderCharacters,
    renderEvidence,
    renderResolutions,
    renderOutcome
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

  global.MainComputerPaxScenarioSession = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
