    async function initXtermTerminal() {
      if (xterm) {
        return;
      }
      terminalHistory = loadTerminalHistory();
      terminalHistoryCursor = terminalHistory.length;
      try {
        await loadScript("https://cdn.jsdelivr.net/npm/@xterm/xterm/lib/xterm.js");
        await loadScript("https://cdn.jsdelivr.net/npm/@xterm/addon-fit/lib/addon-fit.js");
        const TerminalCtor = window.Terminal;
        const FitAddonCtor = window.FitAddon?.FitAddon || window.FitAddon;
        if (!TerminalCtor || !FitAddonCtor) {
          throw new Error("xterm.js did not expose Terminal and FitAddon");
        }
        xterm = new TerminalCtor({
          cursorBlink: true,
          convertEol: true,
          fontFamily: 'Consolas, "Lucida Console", monospace',
          fontSize: 14,
          scrollback: 2000,
          theme: {
            background: "#010201",
            foreground: "#a7d86d",
            cursor: "#f6c75b",
            selectionBackground: "#2f4f32",
            black: "#010201",
            red: "#ff8f70",
            green: "#a7d86d",
            yellow: "#f6c75b",
            blue: "#73d6ff",
            magenta: "#df6794",
            cyan: "#73d6ff",
            white: "#f7f3e8"
          }
        });
        xtermFit = new FitAddonCtor();
        xterm.loadAddon(xtermFit);
        xterm.open(terminalXterm);
        fitXterm();
        xterm.writeln("Main Computer terminal");
        xterm.writeln("Enter runs. Up/Down recalls history. Ctrl+L clears. Ctrl+C clears input.");
        writePrompt();
        xterm.onData(handleXtermData);
      } catch (error) {
        terminalXterm.textContent = `terminal failed to load: ${error.message || error}`;
        glStatus.textContent = "terminal unavailable";
      }
    }
    function handleXtermData(data) {
      if (terminalBusy || !xterm) {
        return;
      }
      if (data === "\r") {
        const command = terminalBuffer.trim();
        xterm.write("\r\n");
        terminalBuffer = "";
        runTerminalCommand(command);
        return;
      }
      if (data === "\u007f") {
        if (terminalBuffer.length > 0) {
          terminalBuffer = terminalBuffer.slice(0, -1);
          xterm.write("\b \b");
        }
        return;
      }
      if (data === "\x03") {
        terminalBuffer = "";
        xterm.write("^C\r\n");
        writePrompt();
        return;
      }
      if (data === "\x0c") {
        xterm.clear();
        xterm.write(`${terminalPrompt()}${terminalBuffer}`);
        return;
      }
      if (data === "\x1b[A") {
        if (terminalHistory.length) {
          terminalHistoryCursor = Math.min(terminalHistory.length - 1, terminalHistoryCursor + 1);
          terminalBuffer = terminalHistory[terminalHistoryCursor] || "";
          redrawTerminalInput();
        }
        return;
      }
      if (data === "\x1b[B") {
        if (terminalHistory.length) {
          terminalHistoryCursor = Math.max(-1, terminalHistoryCursor - 1);
          terminalBuffer = terminalHistoryCursor === -1 ? "" : terminalHistory[terminalHistoryCursor] || "";
          redrawTerminalInput();
        }
        return;
      }
      for (const char of data) {
        if (char >= " " && char !== "\x7f") {
          terminalBuffer += char;
          xterm.write(char);
        }
      }
    }
    function writeTerminalBlock(label, value) {
      if (value) {
        xterm.writeln(`\x1b[33m${label}:\x1b[0m`);
        String(value).replace(/\r?\n$/, "").split(/\r?\n/).forEach((line) => xterm.writeln(line));
      }
    }
    function escapeHtml(value) {
      return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }
    function renderInlineMarkdown(value) {
      return escapeHtml(value)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/\*([^*]+)\*/g, "<em>$1</em>");
    }
    function renderAnalysisMarkdown(markdown) {
      const blocks = [];
      const fencePattern = /```[\w-]*\n?([\s\S]*?)```/g;
      let lastIndex = 0;
      let match = fencePattern.exec(markdown);
      while (match) {
        blocks.push({kind: "text", value: markdown.slice(lastIndex, match.index)});
        blocks.push({kind: "code", value: match[1].replace(/\n$/, "")});
        lastIndex = fencePattern.lastIndex;
        match = fencePattern.exec(markdown);
      }
      blocks.push({kind: "text", value: markdown.slice(lastIndex)});
      return blocks.map((block) => {
        if (block.kind === "code") {
          return `<pre><code>${escapeHtml(block.value)}</code></pre>`;
        }
        return block.value
          .split(/\n{2,}/)
          .map((paragraph) => paragraph.trim())
          .filter(Boolean)
          .map((paragraph) => {
            const heading = paragraph.match(/^\*\*([^*]+)\*\*$/);
            if (heading) {
              return `<h3>${escapeHtml(heading[1])}</h3>`;
            }
            return `<p>${renderInlineMarkdown(paragraph).replace(/\n/g, "<br>")}</p>`;
          })
          .join("");
      }).join("");
    }
    function setTerminalAnalysis(text, state = "ready") {
      terminalAnalysisRaw = text;
      terminalAnalysis.dataset.state = state;
      if (terminalAnalysisRawMode) {
        terminalAnalysis.textContent = text;
      } else {
        terminalAnalysis.innerHTML = renderAnalysisMarkdown(text);
      }
    }
    terminalAnalysisToggle.addEventListener("click", () => {
      terminalAnalysisRawMode = !terminalAnalysisRawMode;
      terminalAnalysisToggle.textContent = terminalAnalysisRawMode ? "Rendered" : "Raw";
      setTerminalAnalysis(terminalAnalysisRaw, terminalAnalysis.dataset.state || "ready");
    });
    terminalAiSuggest?.addEventListener("click", async () => {
      const prompt = terminalAiPrompt?.value.trim() || "";
      if (!prompt) {
        setTerminalAiStatus("Describe the command you want first.", "unknown");
        terminalAiPrompt?.focus();
        return;
      }
      if (terminalBusy) {
        setTerminalAiStatus("Wait for the running command to finish before staging another command.", "unknown");
        return;
      }
      terminalAiSuggest.disabled = true;
      setTerminalAiStatus("Asking local AI for one PowerShell command...", "");
      try {
        const response = await fetch("/api/applications/terminal/suggest", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            prompt,
            cwd: terminalCwd.value || "."
          })
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        stageTerminalCommand(data.command, data.cwd, data);
      } catch (error) {
        setTerminalAiStatus(`Suggestion failed: ${error.message || error}`, "unknown");
      } finally {
        terminalAiSuggest.disabled = false;
      }
    });
    async function analyzeTerminalFailure(result) {
      setTerminalAnalysis("Asking local model for terminal analysis...", "thinking");
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            prompt: [
              "Analyze this failed Main Computer terminal command.",
              "Be concise. Explain the likely cause and give the next command or correction to try.",
              `Command: ${result.command || ""}`,
              `Working directory: ${result.cwd || terminalCwd.value || "."}`,
              `Exit code: ${result.exit_code}`,
              `Timed out: ${Boolean(result.timed_out)}`,
              `Stdout:\n${result.stdout || ""}`,
              `Stderr:\n${result.stderr || result.error || ""}`
            ].join("\n\n")
          })
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        setTerminalAnalysis(data.content || "The model did not return analysis.", "ready");
      } catch (error) {
        setTerminalAnalysis(`Analysis failed: ${error.message || error}`, "error");
      }
    }
    async function runTerminalCommand(command) {
      if (!command) {
        writePrompt();
        return;
      }
      addTerminalHistory(command);
      terminalBusy = true;
      glStatus.textContent = "terminal running";
      xterm.writeln(`\x1b[2mstarted ${new Date().toLocaleTimeString()}\x1b[0m`);
      try {
        const response = await fetch("/api/applications/terminal/run", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            command,
            cwd: terminalCwd.value || ".",
            timeout_s: Number(terminalTimeout.value || 15)
          })
        });
        const data = await response.json();
        if (data.cwd) {
          terminalCwd.value = data.cwd;
        }
        writeTerminalBlock("stdout", data.stdout);
        writeTerminalBlock("stderr", data.stderr);
        if (!response.ok) {
          xterm.writeln(`\x1b[31m${data.error || `HTTP ${response.status}`}\x1b[0m`);
        }
        xterm.writeln(`\x1b[2mcwd: ${data.cwd}\x1b[0m`);
        xterm.writeln(`\x1b[2mexit: ${data.exit_code} | ${data.duration_ms}ms\x1b[0m`);
        if (response.ok && data.exit_code === 0 && !data.timed_out) {
          glStatus.textContent = "terminal complete";
        } else {
          glStatus.textContent = data.timed_out ? "terminal timed out" : "terminal exited";
          analyzeTerminalFailure(data);
        }
      } catch (error) {
        xterm.writeln(`\x1b[31merror: ${error.message || error}\x1b[0m`);
        glStatus.textContent = "terminal error";
        analyzeTerminalFailure({command, cwd: terminalCwd.value, exit_code: null, stdout: "", stderr: "", error: error.message || String(error), timed_out: false});
      } finally {
        terminalBusy = false;
        writePrompt();
        xterm.focus();
      }
    }



function taskManagerRequest(path, payload = {}) {
  return fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  }).then(async (response) => {
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  });
}

function taskHeartbeatPort() {
  const cached = Number(taskManagerSnapshotCache?.server?.heartbeat_port || 0);
  if (Number.isFinite(cached) && cached > 0) return cached;
  const currentPort = Number(window.location.port || 0);
  return currentPort > 0 ? currentPort + 1 : 8766;
}
function taskHeartbeatBaseUrl() {
  const host = window.location.hostname || "127.0.0.1";
  return `http://${host}:${taskHeartbeatPort()}`;
}
function taskHeartbeatRequest(action, extra = {}) {
  return fetch(`${taskHeartbeatBaseUrl()}/api/heartbeat/control`, {
    method: "POST",
    mode: "cors",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({action, ...extra})
  }).then(async (response) => {
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    return data;
  });
}
function updateTaskServerPanel(server = {}, overview = {}, headline = "") {
  const watch = overview.watch_hint || "operators can kill, restart, inspect, and schedule from here.";
  taskManagerServer.textContent = [
    headline || `server running: ${server.running ? "yes" : "no"}`,
    `pid: ${server.pid || "-"}`,
    `port: ${server.port || "-"}`,
    `listener: ${server.listener || "none"}`,
    `heartbeat: ${server.heartbeat_running ? `online pid ${server.heartbeat_pid || "-"}` : "offline"}`,
    `heartbeat port: ${server.heartbeat_port || taskHeartbeatPort()}`,
    `heartbeat url: ${server.heartbeat_url || `${taskHeartbeatBaseUrl()}/api/heartbeat/control`}`,
    `control script: ${server.control_script || "none"}`,
    `watch: ${watch}`,
  ].join("\n");
}
async function waitForTaskManagerRecovery(timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      await refreshTaskManager();
      return true;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => window.setTimeout(resolve, 750));
  }
  if (lastError) {
    throw lastError;
  }
  throw new Error("Viewport did not recover before the wait deadline.");
}
function formatTaskBytes(value) {
  const size = Number(value || 0);
  if (!Number.isFinite(size) || size <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let current = size;
  let unit = 0;
  while (current >= 1024 && unit < units.length - 1) {
    current /= 1024;
    unit += 1;
  }
  return `${current.toFixed(current >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}
function formatTaskPercent(value) {
  const percent = Number(value);
  if (!Number.isFinite(percent)) return "n/a";
  return `${percent.toFixed(percent >= 10 || Number.isInteger(percent) ? 0 : 1)}%`;
}
function renderTaskUtilizationBar(value) {
  const percent = Number(value);
  const bounded = Number.isFinite(percent) ? Math.max(0, Math.min(100, percent)) : 0;
  return `
    <div class="task-utilization">
      <div class="task-utilization-bar"><span style="width:${bounded}%"></span></div>
      <strong>${escapeHtml(formatTaskPercent(percent))}</strong>
    </div>
  `;
}
function renderTaskMemoryBar(usedMb, totalMb) {
  const used = Number(usedMb);
  const total = Number(totalMb);
  if (!Number.isFinite(used) || !Number.isFinite(total) || total <= 0) {
    return escapeHtml("n/a");
  }
  const percent = Math.max(0, Math.min(100, (used / total) * 100));
  const label = `${Math.round(used)} / ${Math.round(total)} MB`;
  return `
    <div class="task-utilization">
      <div class="task-utilization-bar"><span style="width:${percent}%"></span></div>
      <strong>${escapeHtml(label)}</strong>
    </div>
  `;
}
function summarizeTaskSnapshot(data) {
  if (!data || !data.ok) {
    return data?.error || "Task manager unavailable.";
  }
  const server = data.server || {};
  const overview = data.overview || {};
  const hardware = data.hardware || {};
  const cpuPercent = Number(hardware.cpu?.overall_percent);
  const gpuPercent = Number(hardware.gpu?.overall_percent);
  return [
    server.running ? `server pid ${server.pid || "unknown"}` : "server stopped",
    `heartbeat ${server.heartbeat_running ? "ready" : "missing"}`,
    `main computer ${overview.main_computer_process_count || 0}`,
    `connections ${overview.connection_count || 0}`,
    Number.isFinite(cpuPercent) ? `cpu ${formatTaskPercent(cpuPercent)}` : "cpu n/a",
    Number.isFinite(gpuPercent) ? `gpu ${formatTaskPercent(gpuPercent)}` : "gpu n/a",
    `schedules ${overview.schedule_count || 0}`,
  ].join(" | ");
}

function setTaskNotebookTab(tabName, {syncRoute = true, replaceRoute = false} = {}) {
  const activeTab = normalizedTaskNotebookTab(tabName);
  taskNotebookActiveTab = activeTab;
  taskNotebookTabButtons.forEach((button) => {
    const isActive = (button.dataset.taskTab || "server-processes") === activeTab;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-selected", isActive ? "true" : "false");
  });
  taskNotebookPanels.forEach((panel) => {
    panel.hidden = (panel.dataset.taskPanel || "server-processes") !== activeTab;
  });
  if (syncRoute) {
    syncTaskManagerTabRoute(activeTab, {replace: replaceRoute});
  }
}
function updateTaskManagerWidgetTickers(data, summary, phase = "") {
  const overview = data?.overview || {};
  const server = data?.server || {};
  const hardware = data?.hardware || {};
  const allProcessCount = Array.isArray(data?.all_processes)
    ? data.all_processes.length
    : Number(data?.all_process_overview?.process_count || 0);
  const cpuPercent = Number(hardware.cpu?.overall_percent);
  const gpuPercent = Number(hardware.gpu?.overall_percent);
  const hardwareTicker = [
    Number.isFinite(cpuPercent) ? `CPU ${formatTaskPercent(cpuPercent)}` : "CPU n/a",
    Number.isFinite(gpuPercent) ? `GPU ${formatTaskPercent(gpuPercent)}` : "GPU n/a",
  ].join(" | ");
  const prefix = phase ? `${phase} | ` : "";
  setApplicationWidgetTicker(taskOverviewCard, `${prefix}${summary}`);
  setApplicationWidgetTicker(taskControlsCard, `Server ${server.running ? "online" : "offline"} | pid ${server.pid || "-"} | port ${server.port || "-"} | ${taskAutoRefresh.checked ? "auto refresh on" : "auto refresh off"}`);
  setApplicationWidgetTicker(taskScheduleCard, `Schedules ${overview.schedule_count || 0} | ${Array.isArray(data?.schedules) && data.schedules.length ? `next ${data.schedules[0].action || "action"}` : "no queued actions"}`);
  setApplicationWidgetTicker(taskNotebookPane, `Server Processes ${Array.isArray(data?.processes) ? data.processes.length : 0} | All Processes ${allProcessCount} | Connections ${overview.connection_count || 0} | ${hardwareTicker}`);
  setApplicationWidgetTicker(taskAiToolbar, `AI operations | prompt ${taskAiPrompt.value.trim() ? "loaded" : "empty"} | review before action`);
}

function updateTaskAiTicker(message) {
  const cleaned = String(message || "AI brief ready.").replace(/\s+/g, " ").trim();
  setApplicationWidgetTicker(taskAiPane, `AI brief | ${cleaned.slice(0, 220) || "ready"}`);
}

function renderTaskProcessRows(target, items, emptyMessage) {
  const rows = Array.isArray(items) ? items : [];
  if (!target) return;
  if (!rows.length) {
    target.innerHTML = `<div class="task-empty">${escapeHtml(emptyMessage)}</div>`;
    return;
  }
  target.innerHTML = `
    <table class="task-table">
      <thead>
        <tr>
          <th>Pid</th>
          <th>Name</th>
          <th>Role</th>
          <th>Status</th>
          <th>Memory</th>
          <th>Command</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((item) => {
          const role = item.is_current_process
            ? '<span class="task-pill warn">current</span>'
            : item.is_main_computer
              ? '<span class="task-pill">main computer</span>'
              : '<span class="task-pill">workspace</span>';
          const actions = item.is_current_process
            ? '<div class="task-row-actions"><span class="task-pill warn">use server controls</span></div>'
            : `<div class="task-row-actions"><button type="button" data-task-action="terminate" data-task-pid="${item.pid}">Terminate</button><button type="button" data-task-action="kill" data-task-pid="${item.pid}">Force Kill</button></div>`;
          return `
            <tr>
              <td>${item.pid}</td>
              <td>${escapeHtml(item.name || "")}</td>
              <td>${role}</td>
              <td>${escapeHtml(item.status || "")}</td>
              <td>${escapeHtml(item.memory_human || formatTaskBytes(item.memory_rss || 0))}</td>
              <td><code>${escapeHtml(item.command_preview || item.cmdline || "")}</code></td>
              <td>${actions}</td>
            </tr>
          `;
        }).join("")}
      </tbody>
    </table>`;
}
function renderTaskProcesses(items) {
  renderTaskProcessRows(taskProcessTable, items, "No server process rows matched the current filter.");
}
function renderTaskAllProcesses(items) {
  renderTaskProcessRows(taskAllProcessTable, items, "No process rows matched the current filter.");
}
function renderTaskConnections(items) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) {
    taskConnectionTable.innerHTML = '<div class="task-empty">No connection rows are available for the current filter.</div>';
    return;
  }
  taskConnectionTable.innerHTML = `
    <table class="task-table">
      <thead>
        <tr>
          <th>Pid</th>
          <th>Process</th>
          <th>Status</th>
          <th>Local</th>
          <th>Remote</th>
        </tr>
      </thead>
      <tbody>
        ${rows.map((item) => `
          <tr>
            <td>${item.pid || "-"}</td>
            <td>${escapeHtml(item.process_name || "")}</td>
            <td>${escapeHtml(item.status || "")}</td>
            <td><code>${escapeHtml(item.local || "")}</code></td>
            <td><code>${escapeHtml(item.remote || "")}</code></td>
          </tr>
        `).join("")}
      </tbody>
    </table>`;
}
function renderTaskHardware(hardware = {}) {
  if (!taskHardwareTable) return;
  const cpu = hardware.cpu || {};
  const gpu = hardware.gpu || {};
  const cpuRows = Array.isArray(cpu.per_core) ? cpu.per_core : [];
  const gpuRows = Array.isArray(gpu.devices) ? gpu.devices : [];
  const cpuSummary = cpu.available ? `
    <div class="task-hardware-summary">
      <span class="task-hardware-meta">all CPUs ${escapeHtml(formatTaskPercent(cpu.overall_percent))}</span>
      <span class="task-hardware-meta">logical ${escapeHtml(String(cpu.logical_cores || cpuRows.length || "-"))}</span>
      <span class="task-hardware-meta">physical ${escapeHtml(String(cpu.physical_cores || "-"))}</span>
      ${Number.isFinite(Number(cpu.frequency_mhz)) ? `<span class="task-hardware-meta">${escapeHtml(String(Math.round(Number(cpu.frequency_mhz))))} MHz</span>` : ""}
      ${Array.isArray(cpu.load_average) && cpu.load_average.length ? `<span class="task-hardware-meta">load ${escapeHtml(cpu.load_average.join(" / "))}</span>` : ""}
    </div>
    <table class="task-table">
      <thead>
        <tr>
          <th>CPU</th>
          <th>Utilization</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td><strong>All CPUs</strong></td>
          <td class="task-utilization-cell">${renderTaskUtilizationBar(cpu.overall_percent)}</td>
          <td>${escapeHtml(`${cpuRows.length || cpu.logical_cores || 0} logical cores${cpu.physical_cores ? ` | ${cpu.physical_cores} physical` : ""}`)}</td>
        </tr>
        ${cpuRows.map((item) => `
          <tr>
            <td>${escapeHtml(item.label || `CPU ${item.index || 0}`)}</td>
            <td class="task-utilization-cell">${renderTaskUtilizationBar(item.percent)}</td>
            <td>${escapeHtml(item.percent >= 85 ? "hot" : item.percent >= 60 ? "busy" : "available")}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  ` : `<div class="task-empty">${escapeHtml(cpu.message || "CPU telemetry unavailable.")}</div>`;
  const gpuSummary = gpu.available || gpuRows.length ? `
    <div class="task-hardware-summary">
      <span class="task-hardware-meta">GPU rollup ${escapeHtml(formatTaskPercent(gpu.overall_percent))}</span>
      <span class="task-hardware-meta">${escapeHtml(gpu.message || "GPU telemetry current.")}</span>
    </div>
    <table class="task-table">
      <thead>
        <tr>
          <th>GPU</th>
          <th>Utilization</th>
          <th>Memory</th>
          <th>Temp</th>
        </tr>
      </thead>
      <tbody>
        ${gpuRows.map((item) => `
          <tr>
            <td>${escapeHtml(item.name || `GPU ${item.index || 0}`)}</td>
            <td class="task-utilization-cell">${renderTaskUtilizationBar(item.utilization_percent)}</td>
            <td class="task-utilization-cell">${renderTaskMemoryBar(item.memory_used_mb, item.memory_total_mb)}</td>
            <td>${escapeHtml(Number.isFinite(Number(item.temperature_c)) ? `${Math.round(Number(item.temperature_c))} C` : "n/a")}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  ` : `<div class="task-empty">${escapeHtml(gpu.message || "GPU telemetry unavailable on this host.")}</div>`;
  taskHardwareTable.innerHTML = `
    <div class="task-hardware-sections">
      <section class="task-hardware-section">
        <strong>CPU Utilization</strong>
        ${cpuSummary}
      </section>
      <section class="task-hardware-section">
        <strong>GPU Utilization</strong>
        ${gpuSummary}
      </section>
      ${hardware.summary ? `<div class="task-hardware-note">${escapeHtml(hardware.summary)}</div>` : ""}
    </div>
  `;
}
function renderTaskSchedules(items) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) {
    taskScheduleList.innerHTML = '<div class="task-empty">No scheduled actions yet.</div>';
    return;
  }
  taskScheduleList.innerHTML = rows.map((item) => `
    <div class="task-schedule-item">
      <strong>${escapeHtml(item.action || "action")}</strong>
      <div class="task-schedule-meta">${escapeHtml(item.run_at || "unscheduled")}
${escapeHtml(item.note || "")}</div>
      <div class="task-schedule-actions">
        <button type="button" data-task-run="${escapeHtml(item.id || "")}" data-task-action-name="${escapeHtml(item.action || "")}">Run Now</button>
        <button type="button" data-task-delete="${escapeHtml(item.id || "")}">Delete</button>
      </div>
    </div>
  `).join("");
}
async function refreshTaskManager() {
  const firstLoad = !taskManagerLoadingShown;
  if (firstLoad) {
    taskManagerStatus.textContent = "Loading task snapshot...";
    setApplicationWidgetTicker(taskOverviewCard, "Loading task snapshot...");
    taskManagerLoadingShown = true;
  }
  try {
    const requestBase = {
      query: taskQuery.value || "",
      limit: Number(taskLimit.value || 24),
    };
    const data = await taskManagerRequest("/api/applications/task/overview", {
      ...requestBase,
      include_all: false,
      include_connections: taskIncludeConnections.checked,
    });
    let allProcesses = [];
    let allProcessOverview = {};
    let allProcessMessage = "";
    try {
      const allProcessData = await taskManagerRequest("/api/applications/task/overview", {
        ...requestBase,
        include_all: true,
        include_connections: false,
      });
      allProcesses = Array.isArray(allProcessData.processes) ? allProcessData.processes : [];
      allProcessOverview = allProcessData.overview || {};
    } catch (allProcessError) {
      allProcessMessage = `all-process snapshot unavailable: ${allProcessError.message || allProcessError}`;
    }
    taskManagerSnapshotCache = {
      ...data,
      all_processes: allProcesses,
      all_process_overview: allProcessOverview,
      all_processes_error: allProcessMessage,
    };
    const summary = summarizeTaskSnapshot(taskManagerSnapshotCache);
    taskManagerStatus.textContent = allProcessMessage ? `Task snapshot current. ${allProcessMessage}` : "Task snapshot current.";
    const server = data.server || {};
    updateTaskServerPanel(server, data.overview || {}, "server snapshot current");
    renderTaskProcesses(data.processes || []);
    renderTaskAllProcesses(allProcesses);
    renderTaskConnections(data.connections || []);
    renderTaskHardware(data.hardware || {});
    renderTaskSchedules(data.schedules || []);
    updateTaskManagerWidgetTickers(taskManagerSnapshotCache, summary, firstLoad ? "Task snapshot ready" : "Task snapshot refreshed");
  } catch (error) {
    const message = `Task manager failed: ${error.message || error}`;
    taskManagerStatus.textContent = "Task manager unavailable.";
    updateTaskServerPanel(taskManagerSnapshotCache?.server || {}, taskManagerSnapshotCache?.overview || {}, "viewport offline | heartbeat path still available");
    taskProcessTable.innerHTML = '<div class="task-empty">Server process view unavailable.</div>';
    if (taskAllProcessTable) taskAllProcessTable.innerHTML = '<div class="task-empty">All-process view unavailable.</div>';
    taskConnectionTable.innerHTML = '<div class="task-empty">Connection view unavailable.</div>';
    if (taskHardwareTable) taskHardwareTable.innerHTML = '<div class="task-empty">Hardware utilization view unavailable.</div>';
    setApplicationWidgetTicker(taskOverviewCard, message);
    setApplicationWidgetTicker(taskNotebookPane, "Notebook waiting for viewport recovery.");
    throw error;
  }
}
function scheduleTaskManagerAutoRefresh() {
  stopTaskManagerAutoRefresh();
  if (!taskAutoRefresh.checked) return;
  taskManagerAutoTimer = window.setInterval(() => {
    if (currentApp === "task-manager") {
      refreshTaskManager().catch(() => null);
    }
  }, 5000);
}
function stopTaskManagerAutoRefresh() {
  if (taskManagerAutoTimer) {
    clearInterval(taskManagerAutoTimer);
    taskManagerAutoTimer = null;
  }
}
async function runTaskAction(action, extra = {}, confirmAction = false) {
  try {
    if (action === "server_status") {
      const data = await taskHeartbeatRequest("status");
      const server = {
        ...(taskManagerSnapshotCache?.server || {}),
        ...(data.server || {}),
        heartbeat_running: data.heartbeat?.running,
        heartbeat_pid: data.heartbeat?.pid,
        heartbeat_port: data.heartbeat?.port,
        heartbeat_url: data.heartbeat?.url,
      };
      taskManagerStatus.textContent = "Heartbeat status current.";
      updateTaskServerPanel(server, taskManagerSnapshotCache?.overview || {}, "heartbeat status current");
      setApplicationWidgetTicker(taskOverviewCard, data.message || "Heartbeat status current.");
      return;
    }
    if (action === "server_start") {
      const data = await taskHeartbeatRequest("start");
      const server = {
        ...(taskManagerSnapshotCache?.server || {}),
        ...(data.server || {}),
        heartbeat_running: data.heartbeat?.running,
        heartbeat_pid: data.heartbeat?.pid,
        heartbeat_port: data.heartbeat?.port,
        heartbeat_url: data.heartbeat?.url,
      };
      taskManagerStatus.textContent = "Server start requested through heartbeat.";
      updateTaskServerPanel(server, taskManagerSnapshotCache?.overview || {}, "heartbeat start dispatched");
      setApplicationWidgetTicker(taskOverviewCard, data.message || "Viewport start requested through heartbeat.");
      await waitForTaskManagerRecovery();
      return;
    }
    if (action === "server_restart") {
      stopTaskManagerAutoRefresh();
      taskManagerStatus.textContent = "Server restart requested through heartbeat.";
      updateTaskServerPanel(taskManagerSnapshotCache?.server || {}, taskManagerSnapshotCache?.overview || {}, "heartbeat restart dispatched");
      setApplicationWidgetTicker(taskOverviewCard, "Viewport restart requested through heartbeat.");
      await taskHeartbeatRequest("shutdown");
      await new Promise((resolve) => window.setTimeout(resolve, 750));
      const data = await taskHeartbeatRequest("start");
      const server = {
        ...(taskManagerSnapshotCache?.server || {}),
        ...(data.server || {}),
        heartbeat_running: data.heartbeat?.running,
        heartbeat_pid: data.heartbeat?.pid,
        heartbeat_port: data.heartbeat?.port,
        heartbeat_url: data.heartbeat?.url,
      };
      updateTaskServerPanel(server, taskManagerSnapshotCache?.overview || {}, "heartbeat restart dispatched");
      setApplicationWidgetTicker(taskOverviewCard, data.message || "Viewport restart requested through heartbeat.");
      await waitForTaskManagerRecovery();
      scheduleTaskManagerAutoRefresh();
      return;
    }
    const payload = {action, confirm: confirmAction, ...extra};
    const data = await taskManagerRequest("/api/applications/task/action", payload);
    taskManagerStatus.textContent = "Task action complete.";
    setApplicationWidgetTicker(taskOverviewCard, data.message || data.action || action);
    if (action === "server_shutdown") {
      const server = {
        ...(taskManagerSnapshotCache?.server || {}),
        running: false,
        listener: "",
      };
      taskManagerStatus.textContent = "Server shutdown requested. Heartbeat remains available for start.";
      updateTaskServerPanel(server, taskManagerSnapshotCache?.overview || {}, "viewport shutdown requested");
      setApplicationWidgetTicker(taskOverviewCard, "Shutdown requested | use Start Server to recover through heartbeat.");
      stopTaskManagerAutoRefresh();
      return;
    }
    if (action === "server_restart") {
      taskManagerStatus.textContent = "Server restart requested.";
      updateTaskServerPanel(taskManagerSnapshotCache?.server || {}, taskManagerSnapshotCache?.overview || {}, "viewport restart requested");
      stopTaskManagerAutoRefresh();
      await waitForTaskManagerRecovery();
      scheduleTaskManagerAutoRefresh();
      return;
    }
    await refreshTaskManager();
  } catch (error) {
    taskManagerStatus.textContent = "Task action failed.";
    setApplicationWidgetTicker(taskOverviewCard, `Task action failed: ${error.message || error}`);
  }
}
async function createTaskSchedule() {
  try {
    const data = await taskManagerRequest("/api/applications/task/schedule/create", {
      action: taskScheduleAction.value,
      run_at: taskScheduleWhen.value,
      note: taskScheduleNote.value || "",
      payload: {},
    });
    taskManagerStatus.textContent = "Scheduled action added.";
    setApplicationWidgetTicker(taskScheduleCard, data.message || "Scheduled action added.");
    taskScheduleNote.value = "";
    await refreshTaskManager();
  } catch (error) {
    taskManagerStatus.textContent = "Schedule create failed.";
    setApplicationWidgetTicker(taskScheduleCard, `Schedule create failed: ${error.message || error}`);
  }
}
async function deleteTaskSchedule(scheduleId) {
  try {
    const data = await taskManagerRequest("/api/applications/task/schedule/delete", {schedule_id: scheduleId});
    taskManagerStatus.textContent = "Scheduled action deleted.";
    setApplicationWidgetTicker(taskScheduleCard, data.message || "Scheduled action deleted.");
    await refreshTaskManager();
  } catch (error) {
    taskManagerStatus.textContent = "Schedule delete failed.";
    setApplicationWidgetTicker(taskScheduleCard, `Schedule delete failed: ${error.message || error}`);
  }
}
async function askTaskManagerAi() {
  taskAiOutput.textContent = "Asking model...";
  updateTaskAiTicker("Asking model...");
  try {
    const data = await taskManagerRequest("/api/applications/task/ai", {
      instruction: taskAiPrompt.value || "Explain what the operator should watch, kill, restart, or schedule next.",
      query: taskQuery.value || "",
      limit: Number(taskLimit.value || 24),
      include_all: taskNotebookActiveTab === "all-processes",
      include_connections: taskIncludeConnections.checked,
    });
    taskAiOutput.textContent = data.content || "No AI output returned.";
    updateTaskAiTicker(taskAiOutput.textContent);
    if (data.provider && data.model) {
      taskManagerStatus.textContent = "AI brief ready.";
      setApplicationWidgetTicker(taskOverviewCard, `${summarizeTaskSnapshot(taskManagerSnapshotCache)} | ${data.provider} / ${data.model}`);
    }
  } catch (error) {
    taskAiOutput.textContent = `AI analysis failed: ${error.message || error}`;
    updateTaskAiTicker(taskAiOutput.textContent);
  }
}
