(function (global) {
  "use strict";

  const API_ROOT = "/api/applications/game/tactical-ai";
  const state = {
    bound: false,
    open: false,
    pollTimer: null,
    requestActive: false,
    payload: null,
    lastError: ""
  };

  function nodes() {
    return {
      toggle: document.querySelector("#tactical-ai-debug-toggle"),
      panel: document.querySelector("#tactical-ai-debug-panel"),
      close: document.querySelector("#tactical-ai-debug-close"),
      status: document.querySelector("#tactical-ai-debug-status"),
      summary: document.querySelector("#tactical-ai-debug-summary"),
      timeStep: document.querySelector("#tactical-ai-debug-time-step"),
      consecutive: document.querySelector("#tactical-ai-debug-consecutive"),
      warmupTimeout: document.querySelector("#tactical-ai-debug-warmup-timeout"),
      prepare: document.querySelector("#tactical-ai-debug-prepare"),
      seed: document.querySelector("#tactical-ai-debug-seed"),
      duration: document.querySelector("#tactical-ai-debug-duration"),
      start: document.querySelector("#tactical-ai-debug-start"),
      stop: document.querySelector("#tactical-ai-debug-stop"),
      reset: document.querySelector("#tactical-ai-debug-reset"),
      alpha: document.querySelector("#tactical-ai-debug-alpha"),
      beta: document.querySelector("#tactical-ai-debug-beta"),
      events: document.querySelector("#tactical-ai-debug-events"),
      output: document.querySelector("#tactical-ai-debug-output")
    };
  }

  function finiteNumber(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? number : fallback;
  }

  function integer(value, fallback) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.trunc(number) : fallback;
  }

  async function request(path, options = {}) {
    const response = await fetch(API_ROOT + path, {
      cache: "no-store",
      headers: {"content-type": "application/json"},
      ...options
    });
    const payload = await response.json().catch(() => ({ok: false, error: `HTTP ${response.status}`}));
    if (!response.ok || payload.ok === false) {
      const error = new Error(String(payload.error || `HTTP ${response.status}`));
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  async function post(path, payload = {}) {
    return request(path, {method: "POST", body: JSON.stringify(payload)});
  }

  function phaseLabel(phase) {
    const labels = {
      "idle": "Idle",
      "starting-ai": "Starting NanoJev",
      "loading-model": "Loading NanoJev checkpoint",
      "warming-ai": "Warming Tactical AI",
      "restarting-ai": "Restarting Tactical AI warmup",
      "performance-ready": "Tactical AI ready",
      "priming-battle": "Priming both captains before t=0",
      "running": "Battle running",
      "stopping-battle": "Stopping battle",
      "complete": "Battle complete",
      "error": "Tactical AI error"
    };
    return labels[String(phase || "")] || String(phase || "Unknown");
  }

  function chip(label, value) {
    const node = document.createElement("span");
    node.className = "tactical-ai-debug-chip";
    const strong = document.createElement("strong");
    strong.textContent = `${label}: `;
    node.append(strong, document.createTextNode(String(value ?? "—")));
    return node;
  }

  function renderShip(target, ship, action) {
    if (!target) return;
    if (!ship) {
      target.textContent = "No battle state.";
      return;
    }
    const subsystems = ship.subsystems || {};
    const rows = [
      ["Hull", `${Math.round(finiteNumber(ship.hull, 0) * 100)}%`],
      ["Position", Array.isArray(ship.positionM) ? ship.positionM.join(", ") : "—"],
      ["Speed", `${finiteNumber(ship.speedMps, 0).toFixed(1)} m/s`],
      ["Maneuver", action?.maneuver || "—"],
      ["Weapon", action?.weapon || "—"],
      ["Defense", action?.defense || "—"],
      ["Warp intent", action?.warp || "—"],
      ["Warp state", `${ship.warpState || "—"} ${Math.round(finiteNumber(ship.warpProgress, 0) * 100)}%`],
      ["Propulsion", finiteNumber(subsystems.propulsion, 0).toFixed(2)],
      ["Weapons", finiteNumber(subsystems.weapons, 0).toFixed(2)],
      ["Sensors", finiteNumber(subsystems.sensors, 0).toFixed(2)]
    ];
    const list = document.createElement("dl");
    rows.forEach(([label, value]) => {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = label;
      dd.textContent = value;
      list.append(dt, dd);
    });
    target.replaceChildren(list);
  }

  function renderEvents(target, events) {
    if (!target) return;
    const rows = Array.isArray(events) ? events.slice(-24) : [];
    target.textContent = rows.length
      ? rows.map((event) => {
          const copy = {...event};
          delete copy.t;
          delete copy.kind;
          return `${Number(event.t || 0).toFixed(1)}  ${String(event.kind || "event")}  ${JSON.stringify(copy)}`;
        }).join("\n")
      : "No tactical events yet.";
  }

  function render() {
    const ui = nodes();
    const payload = state.payload || {};
    const phase = String(payload.phase || "idle");
    const performance = payload.performance || {};
    const manager = payload.manager || {};
    const adapter = payload.adapter || {};
    const battle = payload.battle || null;
    const lastSample = performance.lastSample || null;
    const selectedTimeStep = Math.max(1, Math.min(5, integer(ui.timeStep?.value, 5)));
    const preparedTimeStep = Math.max(1, Math.min(5, integer(performance.timeStepSeconds, selectedTimeStep)));
    const timeStepMatches = selectedTimeStep === preparedTimeStep;
    const preparing = phase === "starting-ai" || phase === "loading-model" || phase === "warming-ai" || phase === "restarting-ai";
    const modelLoaded = payload.modelLoaded === true && manager.model_loaded === true && manager.backend_ready === true;
    const lifecycle = manager.ok === true ? String(manager.runtime_state || "unknown") : "offline";
    const container = manager.ok === true ? String(manager.container_state || "unknown") : "unknown";
    const runtimeError = String(manager.last_error || manager.backend_error || manager.container_error || manager.error || manager.image_bootstrap?.error || "");
    const recovery = Number(performance.softResetCount || 0) > 0
      ? `${performance.softResetCount} reset; ${performance.lastSoftResetReason || "retrying"}`
      : "none";

    if (ui.status) {
      ui.status.dataset.state = state.lastError || phase === "error" || lifecycle === "error" || lifecycle === "offline"
        ? "error"
        : performance.ready && timeStepMatches
          ? "ready"
          : "working";
      ui.status.textContent = state.lastError
        || payload.lastError
        || (lifecycle === "error" || lifecycle === "offline" ? runtimeError || "NanoJev manager or Docker unavailable" : "")
        || (!timeStepMatches && preparing
          ? `Switching Tactical AI warmup from ${preparedTimeStep}s to ${selectedTimeStep}s...`
          : performance.ready && !timeStepMatches
            ? `Tactical time step changed to ${selectedTimeStep}s; preparing again.`
            : phaseLabel(phase));
    }

    // Bridge captain is a different execution context from the two-captain
    // tactical debug battle. Show its *actual* decision source, not readiness.
    const liveRenderer = Array.from(document.querySelectorAll('[data-scene-viewer="true"]'))
      .map(node => node.__mainComputerShuttle3dRenderer)
      .find(renderer => renderer && !renderer.disposed && renderer.bridgeCaptainProviderStatus);
    const bridgeCaptain = liveRenderer?.bridgeCaptainProviderStatus?.();
    const bridgeOrder = bridgeCaptain?.activeOrder;
    const bridgeLive = bridgeCaptain?.live;
    if (ui.summary) {
      ui.summary.replaceChildren(
        chip("bridge captain", bridgeOrder?.source || bridgeCaptain?.boarding?.source || "not started"),
        chip("boarding", bridgeCaptain?.boarding?.phase || "not started"),
        chip("boarders", bridgeCaptain?.boarding?.boarders || "aboard"),
        chip("live decisions", bridgeLive?.liveDecisionCount ?? 0),
        chip("live inference", bridgeLive?.lastOutcome || "not requested"),
        chip("manager", lifecycle),
        chip("container", container),
        chip("model", modelLoaded ? "loaded" : manager.model_loaded === true ? "loaded (unverified)" : lifecycle === "loading" || phase === "loading-model" ? "loading" : "not loaded"),
        chip("checkpoint", `${manager.checkpoint_selector || "—"} ${manager.checkpoint_validated === true ? "(verified)" : "(unverified)"}`),
        chip("adapter", adapter.running ? "running" : "stopped"),
        chip("step", timeStepMatches ? `${preparedTimeStep} s` : `${preparedTimeStep} s → ${selectedTimeStep} s`),
        chip("gate", performance.ready && timeStepMatches && modelLoaded ? "PASS" : !timeStepMatches ? "RESTART" : `${performance.consecutivePasses || 0}/${performance.requiredConsecutivePasses || 0}`),
        chip("recovery", recovery),
        chip("last call", lastSample ? `${finiteNumber(lastSample.wallLatencySeconds, 0).toFixed(3)} s` : "—"),
        chip("sim", battle ? `${finiteNumber(battle.simulationTimeSeconds, 0).toFixed(1)} s` : "—")
      );
    }

    const running = Boolean(battle?.running) || phase === "priming-battle" || phase === "running" || phase === "stopping-battle";
    if (ui.prepare) ui.prepare.disabled = state.requestActive || running || preparing;
    if (ui.start) ui.start.disabled = state.requestActive || !performance.ready || !modelLoaded || !timeStepMatches || running;
    if (ui.stop) ui.stop.disabled = state.requestActive || !running;
    if (ui.reset) ui.reset.disabled = state.requestActive || running;

    const actions = battle?.currentActions || {};
    renderShip(ui.alpha, battle?.ships?.alpha, actions.alpha);
    renderShip(ui.beta, battle?.ships?.beta, actions.beta);
    renderEvents(ui.events, battle?.recentEvents);
    if (ui.output) ui.output.textContent = JSON.stringify(payload, null, 2);
  }

  async function refresh() {
    if (state.requestActive) return state.payload;
    try {
      const payload = await request("/status");
      state.payload = payload;
      state.lastError = "";
      render();
      return payload;
    } catch (error) {
      state.lastError = error instanceof Error ? error.message : String(error);
      render();
      return null;
    }
  }

  function ensurePolling() {
    if (state.pollTimer) return;
    state.pollTimer = global.setInterval(() => {
      if (state.open) refresh();
    }, 500);
  }

  function stopPolling() {
    if (!state.pollTimer) return;
    global.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }

  function setOpen(open) {
    const ui = nodes();
    if (!ui.panel || !ui.toggle) return false;
    state.open = Boolean(open);
    ui.panel.hidden = !state.open;
    ui.toggle.setAttribute("aria-expanded", state.open ? "true" : "false");
    if (state.open) {
      const strategicPanel = document.querySelector("#strategic-ai-debug-panel");
      const strategicToggle = document.querySelector("#strategic-ai-debug-toggle");
      if (strategicPanel) strategicPanel.hidden = true;
      strategicToggle?.setAttribute("aria-expanded", "false");
      ensurePolling();
      refresh();
      ui.panel.focus?.({preventScroll: true});
    } else {
      stopPolling();
    }
    return state.open;
  }

  async function perform(operation) {
    if (state.requestActive) return;
    state.requestActive = true;
    state.lastError = "";
    render();
    try {
      const payload = await operation();
      state.payload = payload;
    } catch (error) {
      state.lastError = error instanceof Error ? error.message : String(error);
    } finally {
      state.requestActive = false;
      render();
      if (state.open) refresh();
    }
  }

  function prepareConfig(ui) {
    return {
      time_step_seconds: Math.max(1, Math.min(5, integer(ui.timeStep?.value, 5))),
      consecutive_passes: integer(ui.consecutive?.value, 3),
      warmup_timeout_seconds: finiteNumber(ui.warmupTimeout?.value, 120),
      startup_timeout_seconds: Math.max(180, finiteNumber(ui.warmupTimeout?.value, 120)),
      request_timeout_seconds: 300
    };
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
    ui.timeStep?.addEventListener("change", () => {
      const phase = String(state.payload?.phase || "idle");
      render();
      if (["starting-ai", "warming-ai", "restarting-ai", "performance-ready"].includes(phase)) {
        perform(() => post("/prepare", prepareConfig(ui)));
      }
    });
    ui.prepare?.addEventListener("click", () => perform(() => post("/prepare", prepareConfig(ui))));
    ui.start?.addEventListener("click", () => perform(() => post("/battle/start", {
      seed: integer(ui.seed?.value, 7),
      duration_seconds: finiteNumber(ui.duration?.value, 120),
      time_step_seconds: Math.max(1, Math.min(5, integer(ui.timeStep?.value, 5))),
      request_timeout_seconds: 300,
      include_call_snapshots: false
    })));
    ui.stop?.addEventListener("click", () => perform(() => post("/battle/stop")));
    ui.reset?.addEventListener("click", () => perform(() => post("/reset")));
    render();
  }

  const api = {bind, refresh, render, setOpen, state};
  global.MainComputerTacticalAIDebugPanel = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document !== "undefined") bind();
})(typeof globalThis !== "undefined" ? globalThis : window);
