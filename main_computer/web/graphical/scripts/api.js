      timestampPanel.classList.toggle("outdated", outOfDate);
      timestampState.textContent = outOfDate ? "console out of date: refresh needed" : "console current";
      timestampPanel.title = `newest local entry: ${data.latest_path || data.workspace}`;
      renderProjectionList(workspaceSystems, [
        outOfDate ? "refresh needed" : "console current",
        `patch ${workspacePatchLevel?.textContent || "--"}`,
        `loaded ${formatTime(consoleLoadedAtMs)}`,
        `local ${data.latest_mtime_iso || formatTime(directoryMs)}`,
        data.latest_path || data.workspace
      ], "WORKSPACE");
      setTicker(workspaceSystems?.closest(".fullscreen-widget"), `Workspace patch ${workspacePatchLevel?.textContent || "--"} | ${outOfDate ? "refresh needed" : "current"} | local ${data.latest_mtime_iso || formatTime(directoryMs)}`);
    }

    async function pollWorkspaceTimestamp() {
      try {
        const response = await fetch("/api/workspace-timestamp", {cache: "no-store"});
        const data = await response.json();
        updateTimestampPanel(data);
      } catch (error) {
        timestampState.textContent = "timestamp check failed";
        timestampPanel.classList.add("outdated");
      }
    }

    function markExists(path, exists) {
      if (!path) return "unknown";
      return `${path}${exists ? "" : " (not found)"}`;
    }

    function renderRuntimeBridge(bridge) {
      if (!bridge || !runtimeBridgeSummary) return;
      const commands = bridge.commands || {};
      runtimeBridgeSummary.textContent = bridge.coexistence_rule || bridge.control_model || "same-machine bridge status";
      runtimeBridgeRole.textContent = bridge.current_role || "unknown";
      runtimeBridgeCurrentRoot.textContent = bridge.current_root || "unknown";
      runtimeBridgeProductionRoot.textContent = markExists(bridge.production_root, bridge.production_exists);
      runtimeBridgeEngineeringRoot.textContent = markExists(bridge.engineering_root, bridge.engineering_exists);
      runtimeBridgeProductionCommand.textContent = commands.production || "production command unavailable";
      runtimeBridgeDevCommand.textContent = commands.dev || "dev command unavailable";
      setTicker(runtimeBridgeSummary?.closest(".fullscreen-widget"), `Prod/dev bridge | active ${bridge.current_role || "unknown"} | dev port ${bridge.ports?.dev || "--"} | prod port ${bridge.ports?.production || "--"}`);
    }

    async function loadProjects() {
      const response = await fetch("/api/projects");
      const data = await response.json();
      ollamaTimeoutS = Number(data.ollama_timeout_s || ollamaTimeoutS);
      statusLine.textContent = `${data.provider} / ${data.model}`;
      providerState.textContent = `workspace linked on ${data.provider}`;
      workspaceLine.textContent = `${data.workspace} | patch ${data.patch_level} | ${data.count} project folders`;
      if (workspaceBandMeta) workspaceBandMeta.textContent = `patch ${data.patch_level} | ${data.workspace} | ${data.count} project folders`;
      if (workspacePatchLevel) workspacePatchLevel.textContent = data.patch_level || "--";
      renderRuntimeBridge(data.runtime_bridge);
      addEntry("workspace", `patch ${data.patch_level} | ${data.workspace} | ${data.count} project folders`, "assistant", {renderMode: "plain"});
      projectCount.textContent = String(data.count).padStart(2, "0");
      const marked = data.projects.filter((project) => project.markers.length).length;
      markedCount.textContent = String(marked).padStart(2, "0");
      modelRoute.textContent = `${data.provider} / ${data.model}`;
      catalogFeed.textContent = `${data.count} folders`;
      renderProjectionList(readoutSystems, [
        `${data.count} projects`,
        `${marked} marked`,
        `patch ${data.patch_level}`,
        `${messages} messages`,
        "graphical viewport"
      ], "READOUT");
      renderProjectionList(modelSystems, [
        `provider ${data.provider}`,
        `model ${data.model}`,
        "local route active",
        "openai route available"
      ], "ROUTE");
      renderProjectionList(catalogSystems, [
        `${data.count} folders`,
        `${marked} marked roots`,
        "search ready",
        "first 80 displayed"
      ], "CATALOG");
      setTicker(readoutSystems?.closest(".fullscreen-widget"), `Metrics feed | patch ${data.patch_level} | ${data.count} projects | ${marked} marked | ${messages} messages`);
      setTicker(modelSystems?.closest(".fullscreen-widget"), `Route feed | ${data.provider} | ${data.model} | local bridge`);
      setTicker(catalogSystems?.closest(".fullscreen-widget"), `Index feed | ${data.count} folders | ${marked} marked | search ready`);
      projectData = data.projects;
      renderProjects();
    }

    function renderProjects() {
      const query = widgetSearch.value.trim().toLowerCase();
      projects.textContent = "";
      const fullscreenButton = document.createElement("button");
      fullscreenButton.className = "fullscreen-control";
      fullscreenButton.type = "button";
      fullscreenButton.dataset.fullscreenTarget = "closest";
      fullscreenButton.textContent = "Full Screen";
      projects.append(fullscreenButton);
      ensureWidgetTickers();
      projectData
        .filter((project) => !query || `${project.name} ${project.markers.join(" ")}`.toLowerCase().includes(query))
        .slice(0, 80)
        .forEach((project) => {
        const item = document.createElement("div");
        item.className = "project";
        const markers = project.markers.length ? project.markers.join(", ") : "no root marker";
        item.textContent = project.name;
        const detail = document.createElement("span");
        detail.textContent = markers;
        item.append(detail);
        projects.append(item);
      });
      setTicker(projects, `Project list | ${projectData.length} folders indexed | search ${query || "all"} | first 80 visible`);
    }

    async function loadReadySystems() {
      const ready = [
        "viewport",
        "text console",
        "graphical console",
        "diagnostics",
        "debug assets"
      ];
      try {
        const energyResponse = await fetch("/api/energy/status", {cache: "no-store"});
        if (energyResponse.ok) ready.push("energy credits");
        const revisionResponse = await fetch("/api/revisions/status", {cache: "no-store"});
        if (revisionResponse.ok) ready.push("revisions");
      } catch (error) {
      }
      readySystems.textContent = "";
      ready.forEach((item) => {
        const li = document.createElement("li");
        li.dataset.prefix = "READY";
        li.textContent = item;
        readySystems.append(li);
      });
      promptLink.textContent = `${ready.length} ready`;
      setTicker(readySystems.closest(".fullscreen-widget"), `Operational feed | ${ready.join(" | ")}`);
    }

    async function sendPrompt(prompt) {
      addEntry("you", prompt, "user", {renderMode: "plain"});
      setWorking(true);
      startWorkingCountdown();
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({prompt})
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        addEntry("main computer", data.content, "assistant");
        statusLine.textContent = `${data.provider} / ${data.model}`;
        providerState.textContent = "response received";
        promptLink.textContent = "ready";
      } catch (error) {
        addEntry("error", String(error.message || error), "error", {renderMode: "plain"});
        statusLine.textContent = "waiting";
        providerState.textContent = "route error";
        promptLink.textContent = "retry";
      } finally {
        setWorking(false);
        promptBox.focus();
      }
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const prompt = promptBox.value.trim();
      if (!prompt) return;
      promptBox.value = "";
      session.draft = "";
      saveSession();
      sendPrompt(prompt);
    });

    widgetSearch.addEventListener("input", renderProjects);

    function formatDiagnosticReport(data) {
      const checks = Array.isArray(data.checks) ? data.checks : [];
      const failed = checks.filter((check) => !check.ok).length;
      const lines = [
        `${data.level}: ${checks.length} checks, ${failed} failed.`,
        `ok: ${Boolean(data.ok)}`,
        `elapsed: ${data.elapsed_s}s`,
        `report file: ${data.output_dir}/diagnostics_report.json`,
        "",
        "checks:"
      ];
      checks.forEach((check) => {
        const detail = check.detail === undefined || check.detail === null ? "" : ` | ${JSON.stringify(check.detail)}`;
        lines.push(`- ${check.ok ? "PASS" : "FAIL"} ${check.level}/${check.name}${detail}`);
      });
      return lines.join("\n");
    }

    async function runDiagnostic(level, button) {
      addEntry("diagnostics", `starting ${level}`, "assistant", {renderMode: "plain"});
      button.disabled = true;
      statusLine.textContent = `diagnostics ${level}`;
      providerState.textContent = "diagnostic route active";
      try {
        const response = await fetch("/api/diagnostics", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({level})
        });
        const data = await response.json();
        if (!response.ok && !Array.isArray(data.checks)) throw new Error(data.error || `HTTP ${response.status}`);
        if (Array.isArray(data.checks)) {
