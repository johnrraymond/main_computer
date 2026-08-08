(function (global) {
  "use strict";

  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);

  if (!PaxValueUtils?.objectValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax scenario session presentation.");
  }

  const {
    objectValue
  } = PaxValueUtils;

  function functionValue(value, name) {
    if (typeof value !== "function") {
      throw new Error(`MainComputerPaxScenarioSessionPresentation requires ${name}.`);
    }
    return value;
  }

  function createPaxScenarioSessionPresentation(options = {}) {
    const config = options.config || null;
    const state = options.state || null;
    const presentationModel = options.presentationModel || null;
    const domRenderer = options.domRenderer || null;

    if (!config?.ids?.scenarioId) {
      throw new Error("MainComputerPaxScenarioSessionPresentation requires Pax scenario config.");
    }
    if (!state || typeof state !== "object") {
      throw new Error("MainComputerPaxScenarioSessionPresentation requires mutable session state.");
    }
    if (!presentationModel?.paxProtectionPresentationViewModel) {
      throw new Error("MainComputerPaxScenarioSessionPresentation requires Pax presentation model.");
    }
    if (!domRenderer?.renderPresentation) {
      throw new Error("MainComputerPaxScenarioSessionPresentation requires Pax DOM renderer.");
    }

    const currentRuntime = functionValue(options.currentRuntime, "currentRuntime");
    const currentCharacterRuntime = functionValue(
      options.currentCharacterRuntime,
      "currentCharacterRuntime"
    );
    const briefingAcknowledged = functionValue(options.briefingAcknowledged, "briefingAcknowledged");
    const runUi = functionValue(options.runUi, "runUi");
    const nowMs = typeof options.nowMs === "function"
      ? options.nowMs
      : () => Date.now();

    const scenarioId = config.ids.scenarioId;

    function nodes(documentRef = global.document) {
      return domRenderer.nodes(documentRef);
    }

    function presentationOptions(options = {}) {
      return Object.assign({}, objectValue(options), {
        config,
        characterRuntime: currentCharacterRuntime(),
        worldSnapshot: Object.prototype.hasOwnProperty.call(options, "worldSnapshot")
          ? options.worldSnapshot
          : state.worldSnapshot,
        briefingAcknowledged,
        running: state.running,
        lastError: state.lastError
      });
    }

    function characterRows(view) {
      return presentationModel.characterRows(view, presentationOptions());
    }

    function requirementText(resolution) {
      return presentationModel.requirementText(resolution);
    }

    function stageStatus(view) {
      return presentationModel.stageStatus(view, presentationOptions());
    }

    function objectivePresentation(view) {
      return presentationModel.objectivePresentation(view, presentationOptions());
    }

    function threatPresentation(view, snapshot = state.worldSnapshot) {
      return presentationModel.threatPresentation(
        view,
        snapshot,
        presentationOptions({worldSnapshot: snapshot})
      );
    }

    function hardStartPresentation(view) {
      return presentationModel.hardStartPresentation(view, presentationOptions());
    }

    function isPaxPresentationViewModel(value) {
      return presentationModel.isPaxPresentationViewModel(value);
    }

    function toPaxPresentationViewModel(viewOrPresentation, options = {}) {
      return presentationModel.toPaxPresentationViewModel(
        viewOrPresentation,
        presentationOptions(options)
      );
    }

    function paxProtectionPresentationViewModel(view, options = {}) {
      return presentationModel.paxProtectionPresentationViewModel(
        view,
        presentationOptions(options)
      );
    }

    function domRenderContext(options = {}) {
      const context = objectValue(options);
      const presentationContext = objectValue(context.presentationOptions);
      return Object.assign({}, context, {
        running: state.running,
        runtime: context.runtime || currentRuntime(),
        scenarioId,
        runUi,
        nowMs: () => nowMs({}),
        presentationOptions: presentationOptions(presentationContext)
      });
    }

    function renderCharacters(container, viewOrPresentation, options = {}) {
      return domRenderer.renderCharacters(
        container,
        viewOrPresentation,
        domRenderContext(options)
      );
    }

    function renderEvidence(container, viewOrPresentation, options = {}) {
      return domRenderer.renderEvidence(
        container,
        viewOrPresentation,
        domRenderContext(options)
      );
    }

    function renderResolutions(container, viewOrPresentation, options = {}) {
      return domRenderer.renderResolutions(
        container,
        viewOrPresentation,
        domRenderContext(options)
      );
    }

    function renderOutcome(container, viewOrPresentation, options = {}) {
      return domRenderer.renderOutcome(
        container,
        viewOrPresentation,
        domRenderContext(options)
      );
    }

    function renderThreatTracker(ui, viewOrPresentation, options = {}) {
      return domRenderer.renderThreatTracker(
        ui,
        viewOrPresentation,
        domRenderContext(options)
      );
    }

    function renderMissionCues(ui, viewOrPresentation, options = {}) {
      return domRenderer.renderMissionCues(
        ui,
        viewOrPresentation,
        domRenderContext(options)
      );
    }

    function renderHardStart(ui, viewOrPresentation, options = {}) {
      return domRenderer.renderHardStart(
        ui,
        viewOrPresentation,
        domRenderContext(options)
      );
    }

    function renderPresentation(ui, viewOrPresentation, options = {}) {
      return domRenderer.renderPresentation(
        ui,
        viewOrPresentation,
        domRenderContext(options)
      );
    }

    function hideScenarioChrome(ui) {
      return domRenderer.hideScenarioChrome(ui);
    }

    function revealArrivalPanel() {
      return domRenderer.revealArrivalPanel();
    }

    return Object.freeze({
      nodes,
      presentationOptions,
      characterRows,
      requirementText,
      stageStatus,
      objectivePresentation,
      threatPresentation,
      hardStartPresentation,
      isPaxPresentationViewModel,
      toPaxPresentationViewModel,
      paxProtectionPresentationViewModel,
      domRenderContext,
      renderCharacters,
      renderEvidence,
      renderResolutions,
      renderOutcome,
      renderThreatTracker,
      renderMissionCues,
      renderHardStart,
      renderPresentation,
      hideScenarioChrome,
      revealArrivalPanel
    });
  }

  const api = Object.freeze({
    create: createPaxScenarioSessionPresentation,
    createPaxScenarioSessionPresentation
  });

  global.MainComputerPaxScenarioSessionPresentation = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
