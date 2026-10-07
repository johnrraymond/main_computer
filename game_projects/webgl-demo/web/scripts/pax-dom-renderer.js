(function (global) {
  "use strict";

  const PaxScenarioConfig = global.MainComputerPaxScenarioConfig
    || (typeof require === "function" ? require("./pax-scenario-config.js") : null);
  const PaxPresentationModel = global.MainComputerPaxPresentationModel
    || (typeof require === "function" ? require("./pax-presentation-model.js") : null);
  const PaxValueUtils = global.MainComputerPaxValueUtils
    || (typeof require === "function" ? require("./pax-value-utils.js") : null);

  if (!PaxScenarioConfig?.config) {
    throw new Error("MainComputerPaxScenarioConfig must load before Pax DOM renderer.");
  }
  if (!PaxPresentationModel?.viewModel) {
    throw new Error("MainComputerPaxPresentationModel must load before Pax DOM renderer.");
  }
  if (!PaxValueUtils?.objectValue) {
    throw new Error("MainComputerPaxValueUtils must load before Pax DOM renderer.");
  }

  const DEFAULT_CONFIG = PaxScenarioConfig.config;
  const {
    objectValue,
    arrayValue,
    stringValue,
    nowMs: fallbackNowMs
  } = PaxValueUtils;

  function createPaxDomRenderer(options = {}) {
    const config = objectValue(options.config) === options.config
      ? options.config
      : DEFAULT_CONFIG;
    const presentationModel = options.presentationModel || PaxPresentationModel;
    const scenarioId = config.ids?.scenarioId || "scenario.pax.neutrality-under-fire";

    function documentRef(documentOverride = null) {
      if (documentOverride) return documentOverride;
      if (typeof options.documentRef === "function") return options.documentRef();
      return options.documentRef || global.document || null;
    }

    function nodes(documentOverride = null) {
      const doc = documentRef(documentOverride);
      return {
        root: doc?.querySelector?.("#pax-scenario-contact"),
        briefing: doc?.querySelector?.("#pax-scenario-arrival-briefing"),
        briefingAck: doc?.querySelector?.("#pax-scenario-arrival-ack"),
        objective: doc?.querySelector?.("#pax-scenario-objective-banner"),
        objectiveKicker: doc?.querySelector?.("#pax-scenario-objective-kicker"),
        objectiveTitle: doc?.querySelector?.("#pax-scenario-objective-title"),
        objectiveDetail: doc?.querySelector?.("#pax-scenario-objective-detail"),
        threatTracker: doc?.querySelector?.("#pax-scenario-threat-tracker"),
        threatName: doc?.querySelector?.("#pax-scenario-threat-name"),
        threatDetail: doc?.querySelector?.("#pax-scenario-threat-detail"),
        threatAction: doc?.querySelector?.("#pax-scenario-threat-action"),
        hardStart: doc?.querySelector?.("#pax-scenario-hard-start"),
        hardStartTitle: doc?.querySelector?.("#pax-scenario-hard-start-title"),
        hardStartDetail: doc?.querySelector?.("#pax-scenario-hard-start-detail"),
        hardStartButton: doc?.querySelector?.("#pax-scenario-hard-start-button"),
        status: doc?.querySelector?.("#pax-scenario-status"),
        stageTitle: doc?.querySelector?.("#pax-scenario-stage-title"),
        stageDescription: doc?.querySelector?.("#pax-scenario-stage-description"),
        localRule: doc?.querySelector?.("#pax-scenario-local-rule"),
        vessel: doc?.querySelector?.("#pax-scenario-vessel"),
        characters: doc?.querySelector?.("#pax-scenario-characters"),
        evidence: doc?.querySelector?.("#pax-scenario-evidence"),
        evidenceList: doc?.querySelector?.("#pax-scenario-evidence-list"),
        proceed: doc?.querySelector?.("#pax-scenario-proceed"),
        resolutions: doc?.querySelector?.("#pax-scenario-resolutions"),
        resolutionList: doc?.querySelector?.("#pax-scenario-resolution-list"),
        outcome: doc?.querySelector?.("#pax-scenario-outcome")
      };
    }

    function contextValue(options = {}) {
      const context = objectValue(options);
      const nowProvider = typeof context.nowMs === "function"
        ? context.nowMs
        : fallbackNowMs;
      return Object.assign({}, context, {
        running: Boolean(context.running),
        runtime: context.runtime || null,
        scenarioId: stringValue(context.scenarioId) || scenarioId,
        runUi: typeof context.runUi === "function"
          ? context.runUi
          : ((operation) => (typeof operation === "function" ? operation() : null)),
        nowMs: nowProvider,
        presentationOptions: Object.assign(
          {config},
          objectValue(context.presentationOptions)
        )
      });
    }

    function nowValue(context) {
      const value = Number(context.nowMs());
      return Number.isFinite(value) ? value : fallbackNowMs();
    }

    function createElement(container, tagName) {
      const doc = container?.ownerDocument || documentRef();
      return doc?.createElement?.(tagName) || null;
    }

    function toPresentation(viewOrPresentation, options = {}) {
      const context = contextValue(options);
      if (presentationModel.isPaxPresentationViewModel?.(viewOrPresentation)) {
        return viewOrPresentation;
      }
      return presentationModel.toPaxPresentationViewModel(
        viewOrPresentation,
        context.presentationOptions
      );
    }

    function hideScenarioChrome(ui = {}) {
      if (ui.root) ui.root.hidden = true;
      if (ui.briefing) ui.briefing.hidden = true;
      if (ui.objective) ui.objective.hidden = true;
      if (ui.threatTracker) ui.threatTracker.hidden = true;
      if (ui.hardStart) ui.hardStart.hidden = true;
      return null;
    }

    function renderCharacters(container, viewOrPresentation, options = {}) {
      if (!container) return null;
      container.replaceChildren();
      const presentation = toPresentation(viewOrPresentation, options);
      const rows = arrayValue(presentation.characters?.rows);
      rows.forEach((row) => {
        const item = createElement(container, "article");
        const heading = createElement(container, "div");
        const name = createElement(container, "strong");
        const badge = createElement(container, "span");
        const detail = createElement(container, "small");
        if (!item || !heading || !name || !badge || !detail) return;

        item.className = "pax-scenario-character";
        item.dataset.characterId = row.id;
        item.dataset.characterStatus = row.status;
        name.textContent = row.label;
        badge.textContent = row.kind === "enemy" ? "HOSTILE" : "PROTECTED PERSON";
        heading.append(name, badge);

        const health = Math.max(0, Math.round(row.health));
        const maxHealth = Math.max(1, Math.round(row.maxHealth));
        const action = row.actionId.replace(/_/g, " ");
        detail.textContent = `${health}/${maxHealth} health • ${row.status} • ${action}`;
        if (row.protectedByPlayer) detail.textContent += " • protected by player";
        item.append(heading, detail);
        container.append(item);
      });
      return rows;
    }

    function renderEvidence(container, viewOrPresentation, options = {}) {
      if (!container) return null;
      const context = contextValue(options);
      const runtime = context.runtime;
      container.replaceChildren();
      const presentation = toPresentation(viewOrPresentation, context);
      const items = arrayValue(presentation.evidence?.items);
      items.forEach((evidence) => {
        const button = createElement(container, "button");
        const title = createElement(container, "strong");
        const description = createElement(container, "span");
        if (!button || !title || !description) return;

        button.type = "button";
        button.className = "pax-scenario-evidence-button";
        button.dataset.evidenceId = evidence.id;
        button.disabled = context.running || evidence.collected;
        title.textContent = evidence.collected ? `✓ ${evidence.label}` : evidence.label;
        description.textContent = evidence.description;
        button.append(title, description);
        button.addEventListener?.("click", () => context.runUi(() => (
          runtime?.recordEvidence?.(context.scenarioId, evidence.id, {
            nowMs: nowValue(context)
          })
        )));
        container.append(button);
      });
      return items;
    }

    function renderResolutions(container, viewOrPresentation, options = {}) {
      if (!container) return null;
      const context = contextValue(options);
      const runtime = context.runtime;
      container.replaceChildren();
      const presentation = toPresentation(viewOrPresentation, context);
      const items = arrayValue(presentation.resolutions?.items);
      items.forEach((resolution) => {
        const button = createElement(container, "button");
        const title = createElement(container, "strong");
        const description = createElement(container, "span");
        const requirement = createElement(container, "small");
        if (!button || !title || !description || !requirement) return;

        button.type = "button";
        button.className = "pax-scenario-resolution-button";
        button.dataset.resolutionId = resolution.id;
        button.disabled = context.running || !resolution.available;
        title.textContent = resolution.label;
        description.textContent = resolution.description;
        requirement.textContent = presentationModel.requirementText(resolution);
        button.append(title, description, requirement);
        button.addEventListener?.("click", () => context.runUi(() => (
          runtime?.resolveScenario?.(context.scenarioId, resolution.id, {
            nowMs: nowValue(context)
          })
        )));
        container.append(button);
      });
      return items;
    }

    function renderOutcome(container, viewOrPresentation, options = {}) {
      if (!container) return null;
      container.replaceChildren();
      const presentation = toPresentation(viewOrPresentation, options);
      const consequences = objectValue(presentation.outcome?.consequences);
      Object.entries(consequences).forEach(([key, value]) => {
        const item = createElement(container, "li");
        const label = createElement(container, "span");
        const result = createElement(container, "strong");
        if (!item || !label || !result) return;

        label.textContent = presentationModel.consequenceLabel(key);
        result.textContent = stringValue(value).replace(/-/g, " ");
        item.append(label, result);
        container.append(item);
      });
      return consequences;
    }

    function renderThreatTracker(ui, viewOrPresentation, options = {}) {
      const presentation = toPresentation(viewOrPresentation, options);
      const threat = presentation.threat;
      if (ui.threatTracker) {
        ui.threatTracker.hidden = !threat.visible;
        ui.threatTracker.dataset.scenarioStage = presentation.scenario.stageId;
        ui.threatTracker.dataset.threatVisible = threat.visible ? "true" : "false";
        ui.threatTracker.dataset.threatHealth = String(threat.health);
      }
      if (ui.threatName) ui.threatName.textContent = threat.name;
      if (ui.threatDetail) ui.threatDetail.textContent = threat.detail;
      if (ui.threatAction) ui.threatAction.textContent = threat.action;
      return threat;
    }

    function renderMissionCues(ui, viewOrPresentation, options = {}) {
      const presentation = toPresentation(viewOrPresentation, options);
      const briefing = presentation.missionCues.briefing;
      const objective = presentation.missionCues.objective;
      if (ui.briefing) {
        ui.briefing.hidden = !briefing.visible;
        ui.briefing.dataset.cueKey = briefing.cueKey;
        ui.briefing.dataset.scenarioStage = presentation.scenario.stageId;
      }

      if (ui.objective) {
        ui.objective.hidden = !objective.visible;
        ui.objective.dataset.scenarioStage = presentation.scenario.stageId;
        ui.objective.dataset.urgent = objective.urgent ? "true" : "false";
      }
      if (ui.objectiveKicker) ui.objectiveKicker.textContent = objective.kicker;
      if (ui.objectiveTitle) ui.objectiveTitle.textContent = objective.title;
      if (ui.objectiveDetail) ui.objectiveDetail.textContent = objective.detail;
      return {
        showBriefing: briefing.visible,
        cueKey: briefing.cueKey,
        objective
      };
    }

    function renderHardStart(ui, viewOrPresentation, options = {}) {
      const viewModel = toPresentation(viewOrPresentation, options);
      const presentation = viewModel.hardStart;
      if (ui.hardStart) {
        ui.hardStart.hidden = !presentation.visible;
        ui.hardStart.dataset.scenarioStatus = viewModel.scenario.status;
        ui.hardStart.dataset.scenarioStage = viewModel.scenario.stageId;
      }
      if (ui.hardStartTitle) ui.hardStartTitle.textContent = presentation.title;
      if (ui.hardStartDetail) ui.hardStartDetail.textContent = presentation.detail;
      if (ui.hardStartButton) {
        ui.hardStartButton.textContent = presentation.button;
        ui.hardStartButton.disabled = presentation.buttonDisabled;
        ui.hardStartButton.hidden = presentation.buttonHidden;
      }
      return presentation;
    }

    function renderPresentation(ui, viewOrPresentation, options = {}) {
      const context = contextValue(options);
      const presentation = toPresentation(viewOrPresentation, context);
      if (!ui?.root) return presentation;

      ui.root.hidden = !presentation.visible;
      renderHardStart(ui, presentation, context);
      if (!presentation.visible) return presentation;

      ui.root.dataset.scenarioStatus = presentation.scenario.status;
      ui.root.dataset.scenarioStage = presentation.scenario.stageId;
      renderMissionCues(ui, presentation, context);
      renderThreatTracker(ui, presentation, context);

      if (ui.status) ui.status.textContent = presentation.status.text;
      if (ui.stageTitle) ui.stageTitle.textContent = presentation.stage.title;
      if (ui.stageDescription) ui.stageDescription.textContent = presentation.stage.description;
      if (ui.localRule) ui.localRule.textContent = presentation.localRule.text;
      if (ui.vessel) ui.vessel.textContent = presentation.vessel.text;

      renderCharacters(ui.characters, presentation, context);

      const evidenceVisible = presentation.evidence.visible;
      if (ui.evidence) ui.evidence.hidden = !evidenceVisible;
      if (evidenceVisible) renderEvidence(ui.evidenceList, presentation, context);

      if (ui.proceed) {
        ui.proceed.hidden = !presentation.proceed.visible;
        ui.proceed.disabled = presentation.proceed.disabled;
      }

      const resolutionVisible = presentation.resolutions.visible;
      if (ui.resolutions) ui.resolutions.hidden = !resolutionVisible;
      if (resolutionVisible) renderResolutions(ui.resolutionList, presentation, context);

      if (ui.outcome) {
        ui.outcome.hidden = !presentation.outcome.visible;
        if (presentation.outcome.visible) renderOutcome(ui.outcome, presentation, context);
        else ui.outcome.replaceChildren();
      }

      return presentation;
    }

    function revealArrivalPanel(options = {}) {
      const root = nodes(options.documentRef).root;
      if (!root) return null;
      global.MainComputerStrategicAIPanelLayout?.applyPanelMode?.(
        root,
        "expanded",
        {persist: false}
      );
      return root;
    }

    return Object.freeze({
      nodes,
      hideScenarioChrome,
      renderPresentation,
      renderMissionCues,
      renderThreatTracker,
      renderHardStart,
      renderCharacters,
      renderEvidence,
      renderResolutions,
      renderOutcome,
      revealArrivalPanel
    });
  }

  const defaultRenderer = createPaxDomRenderer();

  const api = Object.freeze({
    create: createPaxDomRenderer,
    nodes: defaultRenderer.nodes,
    hideScenarioChrome: defaultRenderer.hideScenarioChrome,
    renderPresentation: defaultRenderer.renderPresentation,
    renderMissionCues: defaultRenderer.renderMissionCues,
    renderThreatTracker: defaultRenderer.renderThreatTracker,
    renderHardStart: defaultRenderer.renderHardStart,
    renderCharacters: defaultRenderer.renderCharacters,
    renderEvidence: defaultRenderer.renderEvidence,
    renderResolutions: defaultRenderer.renderResolutions,
    renderOutcome: defaultRenderer.renderOutcome,
    revealArrivalPanel: defaultRenderer.revealArrivalPanel
  });

  global.MainComputerPaxDomRenderer = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
