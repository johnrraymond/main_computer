    const log = document.querySelector("#log");
    const form = document.querySelector("#prompt-form");
    const promptBox = document.querySelector("#prompt");
    const sendButton = document.querySelector("#send");
    const workingIndicator = document.querySelector("#working-indicator");
    const statusLine = document.querySelector("#status");
    const providerState = document.querySelector("#provider-state");
    const workspaceLine = document.querySelector("#workspace");
    const workspaceBandMeta = document.querySelector("#workspace-band-meta");
    const timestampPanel = document.querySelector("#timestamp-panel");
    const timestampState = document.querySelector("#timestamp-state");
    const workspacePatchLevel = document.querySelector("#workspace-patch-level");
    const consoleLoadedAtLine = document.querySelector("#console-loaded-at");
    const directoryUpdatedAtLine = document.querySelector("#directory-updated-at");
    const projects = document.querySelector("#projects");
    const widgetSearch = document.querySelector("#widget-search");
    const diagnosticButtons = document.querySelectorAll("[data-diagnostic-level]");
    const projectCount = document.querySelector("#project-count");
    const markedCount = document.querySelector("#marked-count");
    const messageCount = document.querySelector("#message-count");
    const modelRoute = document.querySelector("#model-route");
    const catalogFeed = document.querySelector("#catalog-feed");
    const promptLink = document.querySelector("#prompt-link");
    const readySystems = document.querySelector("#ready-systems");
    const modelSystems = document.querySelector("#model-systems");
    const catalogSystems = document.querySelector("#catalog-systems");
    const readoutSystems = document.querySelector("#readout-systems");
    const workspaceSystems = document.querySelector("#workspace-systems");
    const runtimeBridgeSummary = document.querySelector("#runtime-bridge-summary");
    const runtimeBridgeRole = document.querySelector("#runtime-bridge-role");
    const runtimeBridgeCurrentRoot = document.querySelector("#runtime-bridge-current-root");
    const runtimeBridgeProductionRoot = document.querySelector("#runtime-bridge-production-root");
    const runtimeBridgeEngineeringRoot = document.querySelector("#runtime-bridge-engineering-root");
    const runtimeBridgeProductionCommand = document.querySelector("#runtime-bridge-production-command");
    const runtimeBridgeDevCommand = document.querySelector("#runtime-bridge-dev-command");
    const clock = document.querySelector("#clock");
    const buddhabrotCanvas = document.querySelector("#buddhabrot-canvas");
    const buddhabrotStatus = document.querySelector("#buddhabrot-status");
    const fractalSelector = document.querySelector("#fractal-selector");
    const buddhabrotOrbits = document.querySelector("#buddhabrot-orbits");
    const buddhabrotDelay = document.querySelector("#buddhabrot-delay");
    const SESSION_KEY = "main-computer-viewport-session-v1";
    const consoleLoadedAtMs = Date.now();
    let messages = 0;
    let projectData = [];
    let session = loadSession();
    let fractalRenderRun = 0;
    let ollamaTimeoutS = 600;
    let workingCountdownTimer = null;

    function loadSession() {
      try {
        return JSON.parse(localStorage.getItem(SESSION_KEY)) || {entries: [], draft: ""};
      } catch (error) {
        return {entries: [], draft: ""};
      }
    }

    function saveSession() {
      try {
        localStorage.setItem(SESSION_KEY, JSON.stringify(session));
      } catch (error) {
      }
    }

    function ensureWidgetTickers() {
      document.querySelectorAll(".fullscreen-widget").forEach((widget) => {
        if (widget.querySelector(":scope > .widget-ticker")) return;
        const label = widget.getAttribute("aria-label") || widget.querySelector("h2")?.textContent || widget.textContent.trim().split("\n")[0] || "Widget";
        const ticker = document.createElement("div");
        ticker.className = "widget-ticker";
        ticker.innerHTML = `<span>${label} | embedded ticker view | full screen switches this widget into projection mode</span>`;
        const control = widget.querySelector(":scope > .fullscreen-control");
        if (control) {
          control.insertAdjacentElement("afterend", ticker);
        } else {
          widget.prepend(ticker);
        }
      });
    }

    function setTicker(widget, text) {
      const ticker = widget?.querySelector(":scope > .widget-ticker span");
      if (ticker) ticker.textContent = text;
    }

    function renderProjectionList(target, items, prefix) {
      if (!target) return;
      target.textContent = "";
      items.forEach((item) => {
        const li = document.createElement("li");
        li.dataset.prefix = prefix;
        li.textContent = item;
        target.append(li);
      });
    }

    function renderPlainTextContent(target, content) {
      target.textContent = content;
    }

    function renderEntry(role, content, kind = "", options = {}) {
      const renderMode = options.renderMode || (kind === "error" ? "plain" : "model");
      const entry = document.createElement("div");
      entry.className = `entry ${kind || role}`;
      entry.setAttribute("data-raw-content", content);
      entry.dataset.renderMode = renderMode;
      const label = document.createElement("div");
      label.className = "role";
      label.textContent = role;
      const text = document.createElement("div");
      text.setAttribute("data-raw-content", content);
      text.dataset.renderMode = renderMode;
      renderPlainTextContent(text, content);
      entry.append(label, text);
      log.append(entry);
      log.scrollTop = log.scrollHeight;
      messages += 1;
      messageCount.textContent = String(messages).padStart(2, "0");
      setTicker(log.closest(".fullscreen-widget"), `Transcript feed | ${messages} messages | preserved between modes`);
    }

    function addEntry(role, content, kind = "", options = {}) {
      const renderMode = options.renderMode || (kind === "error" ? "plain" : "model");
      const saved = {role, content, kind, renderMode};
      session.entries.push(saved);
      session.entries = session.entries.slice(-200);
      saveSession();
      renderEntry(role, content, kind, {renderMode});
    }

    function restoreSession() {
      log.textContent = "";
      messages = 0;
      session.entries.forEach((entry) => renderEntry(entry.role, entry.content, entry.kind, {renderMode: entry.renderMode || "plain"}));
      promptBox.value = session.draft || "";
      messageCount.textContent = String(messages).padStart(2, "0");
    }

    function stopWorkingCountdown() {
      if (workingCountdownTimer) {
        clearInterval(workingCountdownTimer);
        workingCountdownTimer = null;
      }
    }

    function setThinkingCountdown(startedAtMs) {
      const timeout = Math.max(1, Number(ollamaTimeoutS || 600));
      const elapsed = Math.floor((Date.now() - startedAtMs) / 1000);
      const remaining = Math.max(0, Math.ceil(timeout - elapsed));
      statusLine.textContent = `thinking | timeout in ${remaining}s`;
      promptLink.textContent = `timeout ${remaining}s`;
    }

    function startWorkingCountdown() {
      stopWorkingCountdown();
      const startedAtMs = Date.now();
      setThinkingCountdown(startedAtMs);
      workingCountdownTimer = setInterval(() => setThinkingCountdown(startedAtMs), 1000);
    }

    function setWorking(isWorking) {
      workingIndicator.classList.toggle("active", isWorking);
      workingIndicator.setAttribute("aria-hidden", isWorking ? "false" : "true");
      sendButton.disabled = isWorking;
      if (!isWorking) stopWorkingCountdown();
    }

    function formatTime(ms) {
      return new Date(ms).toLocaleString();
    }

    function updateTimestampPanel(data) {
      const directoryMs = Number(data.latest_mtime_ms || 0);
      consoleLoadedAtLine.textContent = formatTime(consoleLoadedAtMs);
      directoryUpdatedAtLine.textContent = data.latest_mtime_iso || formatTime(directoryMs);
      const outOfDate = directoryMs > consoleLoadedAtMs + 1000;
