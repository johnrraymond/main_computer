(function (global) {
  "use strict";

  function objectValue(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  }

  function arrayValue(value) {
    return Array.isArray(value) ? value : [];
  }

  function stringValue(value) {
    return String(value || "").trim();
  }

  function integerValue(value, fallback = 0, minimum = 0) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.max(minimum, Math.trunc(parsed));
  }

  function option(value, label) {
    const node = document.createElement("option");
    node.value = stringValue(value);
    node.textContent = stringValue(label || value);
    return node;
  }

  function setOptions(select, records, valueKey, labelKey, preferred = "") {
    if (!select) return;
    const current = stringValue(preferred || select.value);
    select.replaceChildren();
    arrayValue(records).forEach((record) => {
      const raw = objectValue(record);
      select.append(option(raw[valueKey], raw[labelKey] || raw[valueKey]));
    });
    if (current && [...select.options].some((entry) => entry.value === current)) {
      select.value = current;
    }
  }

  function formatJson(value) {
    return JSON.stringify(value, null, 2);
  }

  const state = {
    bound: false,
    session: null,
    unsubscribe: null,
    lastResult: null,
    lastError: "",
    log: []
  };

  function nodes() {
    return {
      toggle: document.querySelector("#strategic-ai-debug-toggle"),
      panel: document.querySelector("#strategic-ai-debug-panel"),
      close: document.querySelector("#strategic-ai-debug-close"),
      status: document.querySelector("#strategic-ai-debug-status"),
      summary: document.querySelector("#strategic-ai-debug-summary"),
      output: document.querySelector("#strategic-ai-debug-output"),
      log: document.querySelector("#strategic-ai-debug-log"),
      actor: document.querySelector("#strategic-ai-debug-actor"),
      runActor: document.querySelector("#strategic-ai-debug-run-actor"),
      activeSystem: document.querySelector("#strategic-ai-debug-active-system"),
      targetTime: document.querySelector("#strategic-ai-debug-target-time"),
      budget: document.querySelector("#strategic-ai-debug-budget"),
      advance: document.querySelector("#strategic-ai-debug-advance"),
      opportunity: document.querySelector("#strategic-ai-debug-opportunity"),
      activate: document.querySelector("#strategic-ai-debug-activate"),
      deactivate: document.querySelector("#strategic-ai-debug-deactivate"),
      expire: document.querySelector("#strategic-ai-debug-expire"),
      commitmentType: document.querySelector("#strategic-ai-debug-commitment-type"),
      promisor: document.querySelector("#strategic-ai-debug-promisor"),
      promisee: document.querySelector("#strategic-ai-debug-promisee"),
      createCommitment: document.querySelector("#strategic-ai-debug-create-commitment"),
      intent: document.querySelector("#strategic-ai-debug-intent"),
      speaker: document.querySelector("#strategic-ai-debug-speaker"),
      audience: document.querySelector("#strategic-ai-debug-audience"),
      communicate: document.querySelector("#strategic-ai-debug-communicate"),
      snapshot: document.querySelector("#strategic-ai-debug-snapshot"),
      exportSnapshot: document.querySelector("#strategic-ai-debug-export"),
      importSnapshot: document.querySelector("#strategic-ai-debug-import"),
      reset: document.querySelector("#strategic-ai-debug-reset")
    };
  }

  function setOpen(open) {
    const ui = nodes();
    if (!ui.panel || !ui.toggle) return false;
    ui.panel.hidden = !open;
    ui.toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      render();
      ui.panel.focus?.({preventScroll: true});
    }
    return open;
  }

  function appendLog(operation, result = null, error = "") {
    const entry = {
      index: state.log.length + 1,
      operation: stringValue(operation || "operation"),
      ok: !error,
      error: stringValue(error),
      resultIds: state.session?.resultIds?.(result) || []
    };
    state.log.unshift(entry);
    state.log = state.log.slice(0, 40);
  }

  function showResult(operation, result) {
    state.lastResult = result;
    state.lastError = "";
    appendLog(operation, result);
    render();
  }

  function showError(operation, error) {
    const message = error instanceof Error
      ? `${error.code ? `${error.code}: ` : ""}${error.message}`
      : String(error || "unknown error");
    state.lastError = message;
    appendLog(operation, null, message);
    render();
  }

  function run(operation, callback) {
    try {
      if (!state.session) throw new Error("No strategic session is active.");
      const result = callback(state.session);
      showResult(operation, result);
      return result;
    } catch (error) {
      showError(operation, error);
      return null;
    }
  }

  function selectedCommitment() {
    const ui = nodes();
    const type = arrayValue(state.session?.catalog?.().commitmentTypes)
      .find((record) => stringValue(record.id) === stringValue(ui.commitmentType?.value));
    return type || null;
  }

  function selectedIntent() {
    const ui = nodes();
    const intent = arrayValue(state.session?.catalog?.().communicativeIntents)
      .find((record) => stringValue(record.id) === stringValue(ui.intent?.value));
    return intent || null;
  }

  function syncCommitmentParties() {
    const ui = nodes();
    const type = selectedCommitment();
    const actors = new Map(
      arrayValue(state.session?.catalog?.().actors)
        .map((actor) => [stringValue(actor.id), actor])
    );
    const records = (ids) => arrayValue(ids).map((id) => (
      actors.get(stringValue(id)) || {id: stringValue(id), name: stringValue(id)}
    ));
    setOptions(ui.promisor, records(objectValue(type).promisorActorIds), "id", "name");
    setOptions(ui.promisee, records(objectValue(type).promiseeActorIds), "id", "name");
  }

  function syncIntentParties() {
    const ui = nodes();
    const intent = selectedIntent();
    const actors = new Map(
      arrayValue(state.session?.catalog?.().actors)
        .map((actor) => [stringValue(actor.id), actor])
    );
    const records = (ids) => arrayValue(ids).map((id) => (
      actors.get(stringValue(id)) || {id: stringValue(id), name: stringValue(id)}
    ));
    setOptions(ui.speaker, records(objectValue(intent).speakerActorIds), "id", "name");
    setOptions(ui.audience, records(objectValue(intent).audienceActorIds), "id", "name");
  }

  function populate() {
    const ui = nodes();
    if (!state.session) return;
    const catalog = state.session.catalog();
    const summary = state.session.summary();
    setOptions(ui.actor, catalog.actors, "id", "name");
    setOptions(ui.activeSystem, catalog.systems, "id", "label", summary.activeSystemId);
    setOptions(ui.opportunity, catalog.campaignOpportunities, "id", "label");
    setOptions(ui.commitmentType, catalog.commitmentTypes, "id", "label");
    setOptions(ui.intent, catalog.communicativeIntents, "id", "label");
    if (ui.targetTime && !ui.targetTime.dataset.userEdited) {
      ui.targetTime.value = String(summary.offscreenSimulationTime + 1);
    }
    if (ui.budget && !ui.budget.value) ui.budget.value = "4";
    syncCommitmentParties();
    syncIntentParties();
  }

  function renderSummary(summary) {
    const ui = nodes();
    if (!ui.summary) return;
    const chips = [
      ["project", summary.projectId],
      ["system", summary.activeSystemId],
      ["revision", summary.canonicalRevision],
      ["actors", summary.actorCount],
      ["observations", summary.observationCount],
      ["beliefs", summary.beliefCount],
      ["reports", summary.reportCount],
      ["commitments", summary.commitmentCount],
      ["director receipts", summary.directorReceiptCount],
      ["off-screen time", summary.offscreenSimulationTime],
      ["simulation receipts", summary.offscreenReceiptCount]
    ];
    ui.summary.replaceChildren();
    chips.forEach(([label, value]) => {
      const chip = document.createElement("span");
      chip.className = "strategic-ai-debug-chip";
      const name = document.createElement("strong");
      name.textContent = `${label}: `;
      chip.append(name, document.createTextNode(String(value ?? "")));
      ui.summary.append(chip);
    });
  }

  function render() {
    const ui = nodes();
    const session = state.session || global.MainComputerStrategicAISession?.current?.();
    if (session && session !== state.session) setSession(session);
    if (!state.session) {
      if (ui.status) {
        ui.status.dataset.state = "idle";
        ui.status.textContent = "Load the Game Surface to create a strategic session.";
      }
      if (ui.summary) ui.summary.replaceChildren();
      if (ui.output) ui.output.textContent = "{}";
      if (ui.log) ui.log.textContent = "No operations yet.";
      return;
    }

    const summary = state.session.summary();
    if (ui.status) {
      ui.status.dataset.state = state.lastError ? "error" : "ready";
      ui.status.textContent = state.lastError
        ? state.lastError
        : `Strategic session ${summary.stateVersion} ready • sequence ${summary.sequence}`;
    }
    renderSummary(summary);
    if (ui.output) {
      ui.output.textContent = formatJson({
        summary,
        lastResult: state.lastResult,
        strategicState: state.session.strategicSnapshot()
      });
    }
    if (ui.log) {
      ui.log.textContent = state.log.length
        ? state.log.map((entry) => (
          `${entry.index}. ${entry.ok ? "OK" : "ERROR"} ${entry.operation}`
          + (entry.resultIds.length ? ` • ${entry.resultIds.join(", ")}` : "")
          + (entry.error ? ` • ${entry.error}` : "")
        )).join("\n")
        : "No operations yet.";
    }
  }

  function setSession(session) {
    if (state.session === session) {
      populate();
      render();
      return session;
    }
    state.unsubscribe?.();
    state.unsubscribe = null;
    state.session = session || null;
    state.lastResult = null;
    state.lastError = "";
    state.log = [];
    if (state.session?.subscribe) {
      state.unsubscribe = state.session.subscribe((detail) => {
        state.lastResult = objectValue(detail).result || state.lastResult;
        populate();
        render();
      });
    }
    populate();
    render();
    return state.session;
  }

  function bind() {
    if (state.bound || typeof document === "undefined") return;
    state.bound = true;
    const ui = nodes();
    ui.toggle?.addEventListener("click", () => setOpen(Boolean(ui.panel?.hidden)));
    ui.close?.addEventListener("click", () => setOpen(false));
    ui.panel?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setOpen(false);
    });
    ui.targetTime?.addEventListener("input", () => {
      ui.targetTime.dataset.userEdited = "true";
    });
    ui.activeSystem?.addEventListener("change", () => run(
      "active-system-changed",
      (session) => session.setActiveSystemId(ui.activeSystem.value)
    ));
    ui.runActor?.addEventListener("click", () => run(
      "actor-turn",
      (session) => session.runActorTurn(ui.actor.value)
    ));
    ui.advance?.addEventListener("click", () => run(
      "offscreen-advance",
      (session) => session.advanceOffscreen(
        integerValue(ui.targetTime.value, 0, 0),
        {
          activeSystemId: ui.activeSystem.value,
          budget: integerValue(ui.budget.value, 0, 0)
        }
      )
    ));
    ui.activate?.addEventListener("click", () => run(
      "campaign-activate",
      (session) => {
        const opportunity = session.catalog().campaignOpportunities.find(
          (record) => stringValue(record.id) === stringValue(ui.opportunity.value)
        );
        return session.activateCampaignRoute(
          objectValue(opportunity).routeSystemId,
          {selectedAt: session.summary().offscreenSimulationTime}
        );
      }
    ));
    ui.deactivate?.addEventListener("click", () => run(
      "campaign-deactivate",
      (session) => session.deactivateCampaignOpportunity(
        ui.opportunity.value,
        {selectedAt: session.summary().offscreenSimulationTime}
      )
    ));
    ui.expire?.addEventListener("click", () => run(
      "campaign-expire",
      (session) => session.expireCampaignOpportunities(
        integerValue(ui.targetTime.value, session.summary().offscreenSimulationTime, 0)
      )
    ));
    ui.commitmentType?.addEventListener("change", syncCommitmentParties);
    ui.createCommitment?.addEventListener("click", () => run(
      "commitment-create",
      (session) => session.createCommitment(
        ui.commitmentType.value,
        ui.promisor.value,
        ui.promisee.value,
        {createdAt: session.summary().offscreenSimulationTime}
      )
    ));
    ui.intent?.addEventListener("change", syncIntentParties);
    ui.communicate?.addEventListener("click", () => run(
      "communication",
      (session) => {
        const intent = selectedIntent();
        const options = {};
        if (arrayValue(objectValue(intent).commitmentTypeIds).length) {
          const commitment = arrayValue(session.strategicSnapshot().commitments)
            .slice()
            .reverse()
            .find((record) => (
              stringValue(record.promisorActorId) === stringValue(ui.speaker.value)
              && stringValue(record.promiseeActorId) === stringValue(ui.audience.value)
              && arrayValue(objectValue(intent).commitmentTypeIds)
                .map(stringValue)
                .includes(stringValue(record.commitmentTypeId))
            ));
          if (commitment) options.commitmentId = commitment.commitmentId;
        }
        return session.performCommunication(
          ui.intent.value,
          ui.speaker.value,
          [ui.audience.value],
          options
        );
      }
    ));
    ui.exportSnapshot?.addEventListener("click", () => run(
      "snapshot-export",
      (session) => {
        const text = session.exportSnapshot(2);
        ui.snapshot.value = text;
        return {bytes: text.length};
      }
    ));
    ui.importSnapshot?.addEventListener("click", () => run(
      "snapshot-import",
      (session) => session.restore(ui.snapshot.value)
    ));
    ui.reset?.addEventListener("click", () => run(
      "session-reset",
      (session) => session.reset()
    ));
    global.addEventListener?.("main-computer-strategic-ai-session-change", () => {
      const current = global.MainComputerStrategicAISession?.current?.();
      if (current) setSession(current);
    });
    const current = global.MainComputerStrategicAISession?.current?.();
    if (current) setSession(current);
    render();
  }

  const api = {
    bind,
    setSession,
    render,
    setOpen,
    state
  };

  global.MainComputerStrategicAIDebugPanel = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  if (typeof document !== "undefined") bind();
})(typeof globalThis !== "undefined" ? globalThis : window);
