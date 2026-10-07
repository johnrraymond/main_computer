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

  function isCommittedArrival(navigation) {
    const raw = objectValue(navigation);
    return Boolean(
      stringValue(raw.currentSystemId)
      && stringValue(raw.lastCompletedRouteId)
      && Number.isFinite(Number(raw.lastArrivalAtMs))
      && Number(raw.lastArrivalAtMs) >= 0
      && Number.isFinite(Number(raw.elapsedWorldTime))
      && Number(raw.elapsedWorldTime) >= 0
      && stringValue(raw.travelPhase || "in-system") === "in-system"
      && !raw.travelling
    );
  }

  function handleNavigation(session, navigation = {}, options = {}) {
    if (!session) return null;
    const raw = objectValue(navigation);
    const systemId = stringValue(raw.currentSystemId);
    if (!systemId) return null;
    if (isCommittedArrival(raw)) {
      return session.completeTravel(raw, objectValue(options));
    }
    if (systemId !== stringValue(session.activeSystemId)) {
      return session.setActiveSystemId(systemId);
    }
    return {
      reused: true,
      activeSystemId: systemId,
      reason: "navigation-unchanged"
    };
  }

  function statusLabel(status) {
    const value = stringValue(status);
    if (value === "completed") return "Completed";
    if (value === "rejected") return "Could not complete";
    if (value === "pending") return "Still pending";
    return value || "Updated";
  }

  function changeDetail(change) {
    const raw = objectValue(change);
    const time = raw.completedAt === null || raw.completedAt === undefined
      ? ""
      : ` at strategic time ${integerValue(raw.completedAt, 0, 0)}`;
    const reason = stringValue(raw.reason);
    if (stringValue(raw.status) === "rejected" && reason) {
      return `${statusLabel(raw.status)}${time} • ${reason.replace(/[-_]+/g, " ")}`;
    }
    return `${statusLabel(raw.status)}${time}`;
  }

  function buildViewModel(session) {
    if (!session?.travelSnapshot) {
      return {
        visible: false,
        title: "While you were away",
        status: "",
        changes: []
      };
    }
    const travel = objectValue(session.travelSnapshot());
    const notice = objectValue(travel.returnNotice);
    const activeSystemId = stringValue(session.summary?.().activeSystemId);
    const visible = Boolean(
      stringValue(notice.arrivalKey)
      && !notice.acknowledged
      && stringValue(notice.systemId) === activeSystemId
    );
    const changes = arrayValue(notice.changes).map((change) => ({
      stepId: stringValue(objectValue(change).stepId),
      description: stringValue(
        objectValue(change).description || objectValue(change).stepId
      ),
      status: stringValue(objectValue(change).status),
      detail: changeDetail(change)
    }));
    return {
      visible,
      arrivalKey: stringValue(notice.arrivalKey),
      systemId: stringValue(notice.systemId),
      systemLabel: stringValue(notice.systemLabel || notice.systemId),
      title: `While you were away — ${stringValue(notice.systemLabel || notice.systemId)}`,
      status: changes.length
        ? `${changes.length} strategic ${changes.length === 1 ? "development" : "developments"} occurred while this system was off-screen.`
        : "No authored strategic developments completed while this system was off-screen.",
      departedAtWorldTime: integerValue(notice.departedAtWorldTime, 0, 0),
      returnedAtWorldTime: integerValue(notice.returnedAtWorldTime, 0, 0),
      canonicalRevision: integerValue(notice.canonicalRevision, 0, 0),
      changes
    };
  }

  const state = {
    bound: false,
    session: null,
    unsubscribe: null,
    lastError: ""
  };

  function nodes() {
    if (typeof document === "undefined") return {};
    return {
      panel: document.querySelector("#strategic-ai-return-summary"),
      title: document.querySelector("#strategic-ai-return-title"),
      status: document.querySelector("#strategic-ai-return-status"),
      changes: document.querySelector("#strategic-ai-return-changes"),
      meta: document.querySelector("#strategic-ai-return-meta"),
      dismiss: document.querySelector("#strategic-ai-return-dismiss")
    };
  }

  function renderChanges(node, changes) {
    if (!node) return;
    node.replaceChildren();
    const records = arrayValue(changes);
    if (!records.length) {
      const item = document.createElement("li");
      item.className = "strategic-ai-return-change strategic-ai-return-change-empty";
      item.textContent = "No verified changes were recorded.";
      node.append(item);
      return;
    }
    records.forEach((change) => {
      const item = document.createElement("li");
      item.className = `strategic-ai-return-change strategic-ai-return-change-${stringValue(change.status)}`;

      const description = document.createElement("strong");
      description.textContent = stringValue(change.description);

      const detail = document.createElement("span");
      detail.textContent = stringValue(change.detail);

      item.append(description, detail);
      node.append(item);
    });
  }

  function render() {
    const ui = nodes();
    if (!ui.panel) return;
    const current = state.session || global.MainComputerStrategicAISession?.current?.();
    if (current && current !== state.session) setSession(current);
    const view = buildViewModel(state.session);
    ui.panel.hidden = !view.visible;
    if (!view.visible) return;

    if (ui.title) ui.title.textContent = view.title;
    if (ui.status) {
      ui.status.dataset.state = state.lastError ? "error" : "ready";
      ui.status.textContent = state.lastError || view.status;
    }
    renderChanges(ui.changes, view.changes);
    if (ui.meta) {
      ui.meta.textContent = (
        `World time ${view.departedAtWorldTime} → ${view.returnedAtWorldTime}`
        + ` • canonical revision ${view.canonicalRevision}`
      );
    }
    if (ui.dismiss) ui.dismiss.dataset.arrivalKey = view.arrivalKey;
  }

  function setSession(session) {
    if (state.session === session) {
      render();
      return session;
    }
    state.unsubscribe?.();
    state.unsubscribe = null;
    state.session = session || null;
    state.lastError = "";
    if (state.session?.subscribe) {
      state.unsubscribe = state.session.subscribe(() => render());
    }
    render();
    return state.session;
  }

  function dismiss() {
    const view = buildViewModel(state.session);
    if (!view.visible) return null;
    try {
      state.lastError = "";
      const result = state.session.acknowledgeReturnNotice(view.arrivalKey);
      render();
      return result;
    } catch (error) {
      state.lastError = error instanceof Error
        ? error.message
        : String(error || "Return summary could not be dismissed.");
      render();
      return null;
    }
  }

  function bind() {
    if (state.bound || typeof document === "undefined") return;
    state.bound = true;
    nodes().dismiss?.addEventListener("click", dismiss);
    global.addEventListener?.("main-computer-strategic-ai-session-change", () => {
      const current = global.MainComputerStrategicAISession?.current?.();
      if (current) setSession(current);
      else render();
    });
    const current = global.MainComputerStrategicAISession?.current?.();
    if (current) setSession(current);
    render();
  }

  const api = {
    isCommittedArrival,
    handleNavigation,
    statusLabel,
    changeDetail,
    buildViewModel,
    setSession,
    dismiss,
    render,
    bind,
    state
  };

  global.MainComputerStrategicAITravelIntegration = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") bind();
})(typeof globalThis !== "undefined" ? globalThis : window);
