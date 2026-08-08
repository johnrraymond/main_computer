(function (global) {
  "use strict";

  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);
  const PaxPresentationModel = global.MainComputerPaxPresentationModel
    || (typeof require === "function" ? require("./pax-presentation-model.js") : null);
  const PaxDomRenderer = global.MainComputerPaxDomRenderer
    || (typeof require === "function" ? require("./pax-dom-renderer.js") : null);
  const PaxScenarioSessionPresentation = global.MainComputerPaxScenarioSessionPresentation
    || (typeof require === "function" ? require("./pax-scenario-session-presentation.js") : null);
  const PaxScenarioSessionState = global.MainComputerPaxScenarioSessionState
    || (typeof require === "function" ? require("./pax-scenario-session-state.js") : null);
  const PaxScenarioSessionRuntime = global.MainComputerPaxScenarioSessionRuntime
    || (typeof require === "function" ? require("./pax-scenario-session-runtime.js") : null);
  const PaxScenarioSessionApi = global.MainComputerPaxScenarioSessionApi
    || (typeof require === "function" ? require("./pax-scenario-session-api.js") : null);
  const PaxProtectionEncounterController = global.MainComputerPaxProtectionEncounterController
    || (typeof require === "function" ? require("./pax-protection-encounter-controller.js") : null);

  if (!PaxScenarioConfig?.config) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax scenario session.");
  }
  if (!PaxPresentationModel?.viewModel) {
    throw new Error("MainComputerPaxPresentationModel must load before Pax scenario session.");
  }
  if (!PaxDomRenderer?.create) {
    throw new Error("MainComputerPaxDomRenderer must load before Pax scenario session.");
  }
  if (!PaxScenarioSessionPresentation?.create) {
    throw new Error("MainComputerPaxScenarioSessionPresentation must load before Pax scenario session.");
  }
  if (!PaxScenarioSessionState?.create) {
    throw new Error("MainComputerPaxScenarioSessionState must load before Pax scenario session.");
  }
  if (!PaxScenarioSessionRuntime?.create) {
    throw new Error("MainComputerPaxScenarioSessionRuntime must load before Pax scenario session.");
  }
  if (!PaxScenarioSessionApi?.create) {
    throw new Error("MainComputerPaxScenarioSessionApi must load before Pax scenario session.");
  }
  if (!PaxProtectionEncounterController?.create) {
    throw new Error("MainComputerPaxProtectionEncounterController must load before Pax scenario session.");
  }

  const config = PaxScenarioConfig.config;
  let sessionRuntime = null;

  const sessionState = PaxScenarioSessionState.create({
    config,
    globalRef: global,
    briefingCueKey: (view) => PaxPresentationModel.briefingCueKey(view, {config})
  });

  const paxDomRenderer = PaxDomRenderer.create({
    config,
    presentationModel: PaxPresentationModel
  });

  const presentationSession = PaxScenarioSessionPresentation.create({
    config,
    state: sessionState.state,
    presentationModel: PaxPresentationModel,
    domRenderer: paxDomRenderer,
    currentRuntime: () => sessionState.currentRuntime(),
    currentCharacterRuntime: () => sessionState.currentCharacterRuntime(),
    briefingAcknowledged: (cueKey) => sessionState.briefingAcknowledged(cueKey),
    runUi: (operation) => sessionRuntime?.runUi?.(operation) || null,
    nowMs: (options = {}) => sessionState.nowMs(options)
  });

  const protectionEncounterController = PaxProtectionEncounterController.create({
    config,
    state: sessionState.state,
    currentRuntime: () => sessionState.currentRuntime(),
    currentCharacterRuntime: () => sessionState.currentCharacterRuntime(),
    nowMs: (options = {}) => sessionState.nowMs(options),
    activeShuttleRenderer: () => sessionState.activeShuttleRenderer(),
    revealArrivalPanel: () => presentationSession.revealArrivalPanel(),
    render: () => sessionRuntime?.render?.() || null
  });

  sessionRuntime = PaxScenarioSessionRuntime.create({
    config,
    globalRef: global,
    sessionState,
    presentation: presentationSession,
    protectionEncounter: protectionEncounterController
  });

  /*
   * Public surface rule: top-level exports are constants, runtime attachment,
   * and read-only diagnostics. Mutation-capable helpers stay behind
   * api.commands so UI/debug callers have one explicit command boundary.
   */
  const api = PaxScenarioSessionApi.create({
    config,
    diagnostics: sessionRuntime.diagnostics,
    commands: sessionRuntime.commands,
    presentation: sessionRuntime.presentation,
    runtime: sessionRuntime.runtime
  });

  global.MainComputerPaxScenarioSession = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
