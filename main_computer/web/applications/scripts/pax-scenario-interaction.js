(function (global) {
  "use strict";

  const SCENARIO_ID = "scenario.pax.neutrality-under-fire";
  const PAX_SYSTEM_ID = "system.pax";
  const BRIEFING_ACK_PREFIX = "main-computer.pax-scenario.briefing-ack.v1";

  function objectValue(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function arrayValue(value) {
    return Array.isArray(value) ? value : [];
  }

  function stringValue(value) {
    return String(value || "").trim();
  }

  function consequenceLabel(key) {
    return String(key || "")
      .replace(/([a-z])([A-Z])/g, "$1 $2")
      .replace(/[-_]/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  const uiState = {
    bound: false,
    runtime: null,
    characterRuntime: null,
    unsubscribe: null,
    characterUnsubscribe: null,
    running: false,
    lastError: "",
    acknowledgedBriefings: new Set()
  };

  function nodes(documentRef = global.document) {
    return {
      root: documentRef?.querySelector?.("#pax-scenario-contact"),
      briefing: documentRef?.querySelector?.("#pax-scenario-arrival-briefing"),
      briefingAck: documentRef?.querySelector?.("#pax-scenario-arrival-ack"),
      objective: documentRef?.querySelector?.("#pax-scenario-objective-banner"),
      objectiveKicker: documentRef?.querySelector?.("#pax-scenario-objective-kicker"),
      objectiveTitle: documentRef?.querySelector?.("#pax-scenario-objective-title"),
      objectiveDetail: documentRef?.querySelector?.("#pax-scenario-objective-detail"),
      status: documentRef?.querySelector?.("#pax-scenario-status"),
      stageTitle: documentRef?.querySelector?.("#pax-scenario-stage-title"),
      stageDescription: documentRef?.querySelector?.("#pax-scenario-stage-description"),
      localRule: documentRef?.querySelector?.("#pax-scenario-local-rule"),
      vessel: documentRef?.querySelector?.("#pax-scenario-vessel"),
      characters: documentRef?.querySelector?.("#pax-scenario-characters"),
      evidence: documentRef?.querySelector?.("#pax-scenario-evidence"),
      evidenceList: documentRef?.querySelector?.("#pax-scenario-evidence-list"),
      proceed: documentRef?.querySelector?.("#pax-scenario-proceed"),
      resolutions: documentRef?.querySelector?.("#pax-scenario-resolutions"),
      resolutionList: documentRef?.querySelector?.("#pax-scenario-resolution-list"),
      outcome: documentRef?.querySelector?.("#pax-scenario-outcome")
    };
  }

  function characterRows(view) {
    const ids = arrayValue(view?.definition?.characterIds);
    const runtime = uiState.characterRuntime
      || global.MainComputerCharacterAIRuntime?.current?.()
      || null;
    return ids.map((id) => {
      const state = runtime?.character?.(id) || null;
      return {
        id,
        label: stringValue(state?.label || id),
        kind: stringValue(state?.kind),
        health: Number(state?.health || 0),
        maxHealth: Number(state?.maxHealth || 1),
        status: stringValue(state?.status || "not-present"),
        actionId: stringValue(state?.currentActionId || "not-present"),
        protectedByPlayer: Boolean(state?.memory?.protectedByPlayer)
      };
    });
  }

  function renderCharacters(container, view) {
    if (!container) return;
    container.replaceChildren();
    const rows = characterRows(view);
    rows.forEach((row) => {
      const item = document.createElement("article");
      item.className = "pax-scenario-character";
      item.dataset.characterId = row.id;
      item.dataset.characterStatus = row.status;
      const heading = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = row.label;
      const badge = document.createElement("span");
      badge.textContent = row.kind === "enemy" ? "HOSTILE" : "PROTECTED PERSON";
      heading.append(name, badge);
      const detail = document.createElement("small");
      const health = Math.max(0, Math.round(row.health));
      const maxHealth = Math.max(1, Math.round(row.maxHealth));
      const action = row.actionId.replace(/_/g, " ");
      detail.textContent = `${health}/${maxHealth} health • ${row.status} • ${action}`;
      if (row.protectedByPlayer) detail.textContent += " • protected by player";
      item.append(heading, detail);
      container.append(item);
    });
  }

  function renderEvidence(container, view) {
    if (!container) return;
    container.replaceChildren();
    arrayValue(view?.evidence).forEach((evidence) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pax-scenario-evidence-button";
      button.dataset.evidenceId = evidence.id;
      button.disabled = uiState.running || evidence.collected;
      const title = document.createElement("strong");
      title.textContent = evidence.collected ? `✓ ${evidence.label}` : evidence.label;
      const description = document.createElement("span");
      description.textContent = evidence.description;
      button.append(title, description);
      button.addEventListener("click", () => runUi(() => (
        uiState.runtime.recordEvidence(SCENARIO_ID, evidence.id, {
          nowMs: performance.now()
        })
      )));
      container.append(button);
    });
  }

  function requirementText(resolution) {
    const missing = arrayValue(resolution.missingEvidenceIds);
    if (resolution.available) return "Available";
    const parts = [];
    if (missing.length) parts.push(`${missing.length} required evidence thread${missing.length === 1 ? "" : "s"} missing`);
    if (Number(resolution.currentEvidenceCount || 0) < Number(resolution.requiredEvidenceCount || 0)) {
      parts.push(`${resolution.requiredEvidenceCount} total evidence threads required`);
    }
    if (resolution.conductSatisfied === false) {
      parts.push(
        `neutrality limit exceeded: ${resolution.currentIntimidationShots} intimidation discharges`
      );
    }
    return parts.join(" • ") || "Locked";
  }

  function renderResolutions(container, view) {
    if (!container) return;
    container.replaceChildren();
    arrayValue(view?.resolutions).forEach((resolution) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pax-scenario-resolution-button";
      button.dataset.resolutionId = resolution.id;
      button.disabled = uiState.running || !resolution.available;
      const title = document.createElement("strong");
      title.textContent = resolution.label;
      const description = document.createElement("span");
      description.textContent = resolution.description;
      const requirement = document.createElement("small");
      requirement.textContent = requirementText(resolution);
      button.append(title, description, requirement);
      button.addEventListener("click", () => runUi(() => (
        uiState.runtime.resolveScenario(SCENARIO_ID, resolution.id, {
          nowMs: performance.now()
        })
      )));
      container.append(button);
    });
  }

  function renderOutcome(container, view) {
    if (!container) return;
    container.replaceChildren();
    const consequences = objectValue(view?.state?.consequences);
    Object.entries(consequences).forEach(([key, value]) => {
      const item = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = consequenceLabel(key);
      const result = document.createElement("strong");
      result.textContent = stringValue(value).replace(/-/g, " ");
      item.append(label, result);
      container.append(item);
    });
  }

  function stageStatus(view) {
    const state = objectValue(view?.state);
    const stage = objectValue(view?.stage);
    if (uiState.lastError) return uiState.lastError;
    if (state.status === "resolved") {
      return `${view.resolution?.label || "Pax settlement adopted"}.`;
    }
    if (state.status === "available") {
      return "Pax arrival trigger armed. The emergency protection detail begins when system arrival commits.";
    }
    if (state.stageId === "protect-witness") {
      return "Quiet Service assassin aboard. Protect Nera Saye and stop the attack.";
    }
    if (state.stageId === "investigation") {
      return `${state.evidenceIds.length} of ${view.evidence.length} evidence threads secured. Collect at least two before the conference.`;
    }
    if (state.stageId === "conference") {
      return "The emergency conference is open. Choose a settlement supported by the evidence.";
    }
    return stage.label || "Pax scenario active.";
  }

  function syncProtection() {
    if (!uiState.runtime) return null;
    const runtime = uiState.characterRuntime
      || global.MainComputerCharacterAIRuntime?.current?.()
      || null;
    if (!runtime) return null;
    try {
      return uiState.runtime.syncCharacterRuntime(
        SCENARIO_ID,
        runtime,
        {nowMs: performance.now()}
      );
    } catch {
      return null;
    }
  }

  function storage() {
    try {
      return global.localStorage || null;
    } catch {
      return null;
    }
  }

  function scenarioStartReceipt(view) {
    return arrayValue(view?.state?.receipts)
      .find((receipt) => stringValue(receipt?.reason) === "scenario-started") || null;
  }

  function briefingCueKey(view) {
    const receipt = scenarioStartReceipt(view);
    return stringValue(
      receipt?.activationKey
      || receipt?.receiptId
      || view?.state?.startedAtMs
      || `${SCENARIO_ID}:active`
    );
  }

  function briefingStorageKey(cueKey) {
    return `${BRIEFING_ACK_PREFIX}:${stringValue(cueKey) || "active"}`;
  }

  function briefingAcknowledged(cueKey) {
    const key = stringValue(cueKey);
    if (!key) return false;
    if (uiState.acknowledgedBriefings.has(key)) return true;
    const store = storage();
    if (!store?.getItem) return false;
    try {
      return store.getItem(briefingStorageKey(key)) === "acknowledged";
    } catch {
      return false;
    }
  }

  function acknowledgeBriefing(view = null) {
    const currentView = view
      || uiState.runtime?.view?.(SCENARIO_ID)
      || null;
    const cueKey = briefingCueKey(currentView);
    if (cueKey) {
      uiState.acknowledgedBriefings.add(cueKey);
      const store = storage();
      try {
        store?.setItem?.(briefingStorageKey(cueKey), "acknowledged");
      } catch {
        // Acknowledgement persistence is best effort only.
      }
    }
    render();
    return {acknowledged: Boolean(cueKey), cueKey};
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

  function objectivePresentation(view) {
    const state = objectValue(view?.state);
    const evidenceCount = arrayValue(state.evidenceIds).length;
    const evidenceTotal = arrayValue(view?.evidence).length;
    if (state.status === "active" && state.stageId === "protect-witness") {
      return {
        visible: true,
        kicker: "PAX PRIORITY ONE",
        title: "PROTECT NERA SAYE",
        detail: "HOSTILE AT BRIDGE ACCESS • TURN FROM THE VIEWSCREEN",
        urgent: true
      };
    }
    if (state.status === "active" && state.stageId === "investigation") {
      return {
        visible: true,
        kicker: "PAX INVESTIGATION",
        title: "SECURE THE EVIDENCE",
        detail: `${evidenceCount}/${evidenceTotal} THREADS COLLECTED • USE THE PAX PANEL`,
        urgent: false
      };
    }
    if (state.status === "active" && state.stageId === "conference") {
      return {
        visible: true,
        kicker: "PAX EMERGENCY CONFERENCE",
        title: "CHOOSE A DEFENSIBLE SETTLEMENT",
        detail: `${evidenceCount}/${evidenceTotal} EVIDENCE THREADS AVAILABLE`,
        urgent: false
      };
    }
    return {
      visible: false,
      kicker: "",
      title: "",
      detail: "",
      urgent: false
    };
  }

  function renderMissionCues(ui, view) {
    const state = objectValue(view?.state);
    const cueKey = briefingCueKey(view);
    const showBriefing = state.status === "active"
      && state.stageId === "protect-witness"
      && !briefingAcknowledged(cueKey);
    if (ui.briefing) {
      ui.briefing.hidden = !showBriefing;
      ui.briefing.dataset.cueKey = cueKey;
      ui.briefing.dataset.scenarioStage = stringValue(state.stageId);
    }

    const objective = objectivePresentation(view);
    if (ui.objective) {
      ui.objective.hidden = !objective.visible;
      ui.objective.dataset.scenarioStage = stringValue(state.stageId);
      ui.objective.dataset.urgent = objective.urgent ? "true" : "false";
    }
    if (ui.objectiveKicker) ui.objectiveKicker.textContent = objective.kicker;
    if (ui.objectiveTitle) ui.objectiveTitle.textContent = objective.title;
    if (ui.objectiveDetail) ui.objectiveDetail.textContent = objective.detail;
    return {showBriefing, cueKey, objective};
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

  function isDurableCommittedArrival(navigation = {}) {
    return stringValue(navigation.currentSystemId) === PAX_SYSTEM_ID
      && stringValue(navigation.travelPhase) === "in-system"
      && Boolean(stringValue(navigation.lastCompletedRouteId))
      && navigation.lastArrivalAtMs !== null
      && navigation.lastArrivalAtMs !== undefined
      && Number.isFinite(Number(navigation.lastArrivalAtMs));
  }

  function revealArrivalPanel() {
    const root = nodes().root;
    if (!root) return;
    global.MainComputerStrategicAIPanelLayout?.applyPanelMode?.(
      root,
      "expanded",
      {persist: false}
    );
  }

  function handleNavigation(navigation = {}) {
    const runtime = uiState.runtime
      || global.MainComputerSystemScenarioRuntime?.current?.()
      || null;
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
      return {
        handled: true,
        started: false,
        reused: true,
        activationKey,
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
    revealArrivalPanel();
    render();
    return {
      handled: true,
      started: !result.reused,
      reused: Boolean(result.reused),
      activationKey,
      ...result
    };
  }

  function render() {
    const ui = nodes();
    const runtime = uiState.runtime
      || global.MainComputerSystemScenarioRuntime?.current?.()
      || null;
    if (!ui.root) return null;
    if (!runtime?.view) {
      ui.root.hidden = true;
      if (ui.briefing) ui.briefing.hidden = true;
      if (ui.objective) ui.objective.hidden = true;
      return null;
    }
    uiState.runtime = runtime;
    syncProtection();
    const view = runtime.view(SCENARIO_ID);
    if (!view) {
      ui.root.hidden = true;
      if (ui.briefing) ui.briefing.hidden = true;
      if (ui.objective) ui.objective.hidden = true;
      return null;
    }

    ui.root.hidden = !view.visible;
    if (!view.visible) return view;
    const state = objectValue(view.state);
    const stage = objectValue(view.stage);
    ui.root.dataset.scenarioStatus = state.status;
    ui.root.dataset.scenarioStage = state.stageId;
    renderMissionCues(ui, view);

    if (ui.status) ui.status.textContent = stageStatus(view);
    if (ui.stageTitle) ui.stageTitle.textContent = stage.label || state.stageId;
    if (ui.stageDescription) ui.stageDescription.textContent = stage.description || "";
    if (ui.localRule) {
      const metrics = objectValue(state.metrics);
      const discharges = Number(metrics.weaponDischarges || 0);
      const intimidation = Number(metrics.intimidationDischarges || 0);
      const conduct = discharges
        ? ` • ${discharges} weapon discharges, ${intimidation} classed as intimidation`
        : "";
      ui.localRule.textContent = `Local rule: ${view.definition.localRule}${conduct}`;
    }
    if (ui.vessel) ui.vessel.textContent = `Enemy ship: ${view.vesselStatus || "status unknown"}`;

    renderCharacters(ui.characters, view);

    const evidenceVisible = state.status === "active"
      && ["investigation", "conference"].includes(state.stageId);
    if (ui.evidence) ui.evidence.hidden = !evidenceVisible;
    if (evidenceVisible) renderEvidence(ui.evidenceList, view);
    if (ui.proceed) {
      ui.proceed.hidden = state.stageId !== "investigation";
      ui.proceed.disabled = uiState.running || state.evidenceIds.length < 2;
    }

    const resolutionVisible = state.status === "active" && state.stageId === "conference";
    if (ui.resolutions) ui.resolutions.hidden = !resolutionVisible;
    if (resolutionVisible) renderResolutions(ui.resolutionList, view);

    if (ui.outcome) {
      ui.outcome.hidden = state.status !== "resolved";
      if (state.status === "resolved") renderOutcome(ui.outcome, view);
      else ui.outcome.replaceChildren();
    }
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

  function setRuntime(runtime) {
    if (uiState.unsubscribe) uiState.unsubscribe();
    uiState.runtime = runtime || null;
    uiState.unsubscribe = runtime?.subscribe?.(() => render()) || null;
    render();
    return uiState.runtime;
  }

  function setCharacterRuntime(runtime) {
    if (uiState.characterUnsubscribe) uiState.characterUnsubscribe();
    uiState.characterRuntime = runtime || null;
    uiState.characterUnsubscribe = runtime?.subscribe?.(() => {
      syncProtection();
      render();
    }) || null;
    render();
    return uiState.characterRuntime;
  }

  function bind() {
    if (uiState.bound || typeof document === "undefined") return;
    const ui = nodes();
    if (!ui.root) return;
    uiState.bound = true;
    ui.briefingAck?.addEventListener("click", () => acknowledgeBriefing());
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

  const api = {
    SCENARIO_ID,
    PAX_SYSTEM_ID,
    setRuntime,
    setCharacterRuntime,
    handleNavigation,
    arrivalActivationKey,
    isDurableCommittedArrival,
    isLegacyPaxOccupancy,
    legacyActivationKey,
    briefingCueKey,
    briefingAcknowledged,
    acknowledgeBriefing,
    objectivePresentation,
    renderMissionCues,
    syncProtection,
    characterRows,
    requirementText,
    render,
    bind,
    state: uiState
  };

  global.MainComputerPaxScenarioInteraction = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") bind();
})(typeof globalThis !== "undefined" ? globalThis : window);
