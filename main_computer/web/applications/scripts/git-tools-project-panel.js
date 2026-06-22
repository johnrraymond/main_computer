(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.project-panel";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-project-panel.js";

function projectBadges(project = {}, inspection = null) {
  const badges = [];
  if (project.vip) badges.push("VIP");
  if (project.locked) badges.push("Locked");
  if (project.archived) badges.push("Archived");
  const summary = project.last_inspection || inspection || {};
  if (summary.is_git_repo === false) badges.push("Not Git repo");
  if (summary.is_git_repo === true) badges.push("Git repo");
  if (summary.has_head === false) badges.push("No HEAD");
  if (summary.has_head === true) badges.push("Has HEAD");
  if (typeof summary.dirty_score === "number") badges.push(`Dirty ${summary.dirty_score}/100`);
  return badges.join(" · ") || "Uninspected";
}

async function loadGitProjects() {
  const data = await gitToolsStatusApi().fetchProjects();
  gitProjectsLastState = data;
  renderGitProjects(data);
  if (data.current_project?.path) {
    gitProjectSetTargetPathInputs(data.current_project.path);
  }
  await inspectSelectedGitProject({quiet: true}).catch(() => null);
  return data;
}

function renderGitProjects(data) {
  if (!data) return;
  const current = data.current_project || null;
  if (current?.path) gitProjectSetTargetPathInputs(current.path);
  renderGitProjectNextStep(gitProjectLastInspection?.project?.id === current?.id ? gitProjectLastInspection : null);
  renderGitProjectList(gitProjectList, data.projects || [], {archived: false});
  renderGitProjectList(gitProjectArchiveList, data.archived_projects || [], {archived: true});
}

function renderGitProjectList(container, projects, {archived = false} = {}) {
  if (!container) return;
  const listScope = archived ? "archived" : "active";
  const listComponentId = `git-tools.projects.${listScope}.list`;
  if (!projects.length) {
    container.innerHTML = `<div class="git-tools-empty" ${gitProjectMcComponentAttrs(`${listComponentId}.empty`, "status", archived ? "No Archived Projects" : "No Active Projects", listComponentId)}>${archived ? "No archived projects." : "No active projects."}</div>`;
    return;
  }
  container.innerHTML = projects.map((project, index) => {
    const selected = project.id === gitProjectsLastState?.current_project_id ? " selected" : "";
    const projectLabel = project.name || project.id || `Project ${index + 1}`;
    const projectSlug = gitProjectMcSlug(project.id || project.name || project.path || `project-${index + 1}`, `project-${index + 1}`);
    const projectComponentId = `${listComponentId}.project.${projectSlug}.${index + 1}`;
    const projectActionAttrs = (action, label) => gitProjectMcComponentAttrs(
      `${projectComponentId}.action.${gitProjectMcSlug(action, "action")}`,
      "action",
      `${projectLabel} ${label}`,
      `${projectComponentId}.actions`
    );
    const selectedBadge = selected
      ? `<span class="git-project-selected-pill" ${gitProjectMcComponentAttrs(`${projectComponentId}.selected`, "status", `${projectLabel} Selected State`, projectComponentId)}>Selected</span>`
      : "";
    const archiveButton = archived
      ? `<button type="button" data-git-project-action="restore" data-project-id="${escapeHtml(project.id)}" ${projectActionAttrs("restore", "Restore Button")}>Restore</button>`
      : project.can_archive === false
        ? `<button type="button" disabled title="VIP project cannot be archived" ${projectActionAttrs("archive-disabled", "Archive Disabled Button")}>Archive disabled</button>`
        : `<button type="button" data-git-project-action="archive" data-project-id="${escapeHtml(project.id)}" ${projectActionAttrs("archive", "Archive Button")}>Archive</button>`;
    const selectButton = archived
      ? ""
      : `<button type="button" data-git-project-action="select" data-project-id="${escapeHtml(project.id)}" ${projectActionAttrs("select", selected ? "Selected Button" : "Select Button")}>${selected ? "Selected" : "Select"}</button>`;
    const lockButton = project.locked
      ? `<button type="button" data-git-project-action="unlock" data-project-id="${escapeHtml(project.id)}" ${projectActionAttrs("unlock", "Unlock Button")}>Unlock</button>`
      : `<button type="button" data-git-project-action="lock" data-project-id="${escapeHtml(project.id)}" ${projectActionAttrs("lock", "Lock Button")}>Lock</button>`;
    const inspectButton = `<button type="button" data-git-project-action="inspect" data-project-id="${escapeHtml(project.id)}" ${projectActionAttrs("inspect", "Inspect Button")}>Inspect</button>`;
    return `<div class="git-project-row${selected}" data-project-id="${escapeHtml(project.id)}" ${gitProjectMcComponentAttrs(projectComponentId, "panel", `${projectLabel} Project Row`, listComponentId)}>
      <div class="git-project-row-main" ${gitProjectMcComponentAttrs(`${projectComponentId}.main`, "status", `${projectLabel} Summary`, projectComponentId)}>
        <div class="git-project-row-title" ${gitProjectMcComponentAttrs(`${projectComponentId}.title`, "status", `${projectLabel} Title`, projectComponentId)}><strong>${project.vip ? "★ " : ""}${escapeHtml(projectLabel)}</strong>${selectedBadge}</div>
        <span ${gitProjectMcComponentAttrs(`${projectComponentId}.badges`, "status", `${projectLabel} Badges`, projectComponentId)}>${escapeHtml(projectBadges(project))}</span>
        <code ${gitProjectMcComponentAttrs(`${projectComponentId}.path`, "output", `${projectLabel} Path`, projectComponentId)}>${escapeHtml(project.path || "")}</code>
      </div>
      <div class="git-project-row-actions" ${gitProjectMcComponentAttrs(`${projectComponentId}.actions`, "toolbar", `${projectLabel} Actions`, projectComponentId)}>${selectButton}${inspectButton}${lockButton}${archiveButton}</div>
    </div>`;
  }).join("");
  container.querySelectorAll("[data-git-project-action]").forEach((button) => {
    button.addEventListener("click", () => handleGitProjectAction(button.dataset.gitProjectAction || "", button.dataset.projectId || ""));
  });
}

async function handleGitProjectAction(action, projectId) {
  if (!projectId) return;
  if (action === "select") {
    clearGitServerTargetForProjectChange();
    setGitProjectNextStep("Inspecting selected project…", "Running git_dirty.py plan and Git state checks.", projectId, "actionable");
    const data = await gitToolsStatusApi().selectProject(projectId);
    gitProjectsLastState = data;
    renderGitProjects(data);
    if (data.current_project?.path) gitProjectSetTargetPathInputs(data.current_project.path);
    await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
    await inspectSelectedGitProject();
    await refreshGitStatus();
  } else if (action === "archive") {
    const data = await gitToolsStatusApi().archiveProject(projectId);
    gitProjectsLastState = data;
    renderGitProjects(data);
  } else if (action === "restore") {
    clearGitServerTargetForProjectChange();
    const data = await gitToolsStatusApi().restoreProject(projectId, {select: true});
    gitProjectsLastState = data;
    renderGitProjects(data);
    if (data.current_project?.path) gitProjectSetTargetPathInputs(data.current_project.path);
    await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
  } else if (action === "lock" || action === "unlock") {
    const data = await gitToolsStatusApi().setProjectLock({projectId, locked: action === "lock"});
    gitProjectsLastState = data;
    renderGitProjects(data);
    await inspectSelectedGitProject({quiet: true}).catch(() => null);
  } else if (action === "inspect") {
    setGitProjectNextStep("Refreshing project report…", "Running git_dirty.py plan for this project.", projectId, "actionable");
    await inspectSelectedGitProject({project_id: projectId});
  }
}

async function addGitProjectFromInput() {
  const path = (gitProjectPath?.value || gitRepoDir?.value || ".").trim();
  if (!path) return;
  try {
    setGitProjectNextStep("Adding project…", "The project will be selected and inspected after it is registered.", path, "actionable");
    const data = await gitToolsStatusApi().addProject({path, select: true});
    gitProjectsLastState = data;
    renderGitProjects(data);
    if (data.current_project?.path) gitProjectSetTargetPathInputs(data.current_project.path);
    await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
    await inspectSelectedGitProject();
    await refreshGitStatus();
  } catch (error) {
    setGitProjectNextStep("Add project failed", error?.message || String(error), path, "blocking");
  }
}

async function setSelectedGitProjectLock(locked) {
  const project = currentGitProject();
  if (!project) return;
  try {
    const data = await gitToolsStatusApi().setProjectLock({projectId: project.id, locked});
    gitProjectsLastState = data;
    renderGitProjects(data);
    await inspectSelectedGitProject({quiet: true});
  } catch (error) {
    setGitProjectNextStep("Project lock update failed", error?.message || String(error), project.path || "", "blocking");
  }
}

async function inspectSelectedGitProject(options = {}) {
  const project = currentGitProject();
  const projectId = options.project_id || project?.id || "";
  const projectContext = projectId || project?.path || "current";
  const payload = projectId ? {project_id: projectId} : {};
  try {
    if (typeof renderGitProjectWizardLoading === "function") {
      renderGitProjectWizardLoading({
        title: options.quiet ? "Refreshing project plan" : "Inspecting selected project",
        detail: "Waiting for backend results. Running git_dirty.py plan and Git state checks.",
        context: projectContext,
      });
    }
    if (!options.quiet) {
      setGitProjectNextStep("Inspecting selected project…", "Running git_dirty.py plan and Git state checks.", projectContext, "actionable");
    }
    const data = await gitToolsStatusApi().inspectProject(payload);
    gitProjectLastInspection = data;
    if (data.project?.path) gitProjectSetTargetPathInputs(data.project.path);
    renderGitProjectInspection(data);
    if (!options.quiet && gitProjectsLastState) {
      gitProjectsLastState.current_project = data.project;
      renderGitProjects(gitProjectsLastState);
    }
    return data;
  } catch (error) {
    if (typeof renderGitProjectWizardInspectionFailed === "function") {
      renderGitProjectWizardInspectionFailed(error, projectContext);
    }
    setGitProjectNextStep("Project inspection failed", error?.message || String(error), projectContext, "blocking");
    throw error;
  }
}

function gitProjectHistoryStorageKey(actionKey = "") {
  const project = currentGitProject();
  return `main-computer.git-project-action-history.${project?.id || "current"}.${actionKey || "unknown"}`;
}

function gitProjectReadHistory(actionKey = "") {
  try {
    return JSON.parse(window.localStorage.getItem(gitProjectHistoryStorageKey(actionKey)) || "[]");
  } catch (_error) {
    return [];
  }
}

function gitProjectWriteHistory(actionKey = "", history = []) {
  const trimmed = Array.isArray(history) ? history.slice(-20) : [];
  try {
    window.localStorage.setItem(gitProjectHistoryStorageKey(actionKey), JSON.stringify(trimmed));
  } catch (_error) {
    return;
  }
}

function gitProjectFormatHistoryEntry(entry = {}) {
  const result = entry.result || {};
  const logs = Array.isArray(entry.logs) ? entry.logs : [];
  const commandText = Array.isArray(entry.commands) ? entry.commands.join("\n") : (entry.command || "");
  return [
    `${entry.time || new Date().toLocaleString()} — ${entry.status || "recorded"}`,
    entry.label ? `Action: ${entry.label}` : "",
    commandText ? `Commands:\n${commandText}` : "",
    typeof result.returncode !== "undefined" ? `Return code: ${result.returncode}` : "",
    result.mode ? `Mode: ${result.mode}` : "",
    result.repo ? `Repo: ${result.repo}` : "",
    result.stdout ? `stdout:\n${result.stdout}` : "",
    result.stderr ? `stderr:\n${result.stderr}` : "",
    logs.length ? `runner logs:\n${logs.map((item) => `${item.elapsed || 0}s ${item.message || ""}`).join("\n")}` : "",
    result.error ? `error:\n${result.error}` : "",
  ].filter(Boolean).join("\n\n");
}

function renderGitProjectActionHistory(actionKey = "") {
  const history = gitProjectReadHistory(actionKey);
  if (!history.length) {
    return `<details class="git-project-command-history" data-git-project-action-history="${escapeHtml(actionKey)}"><summary>Command history</summary><pre>No runs yet.</pre></details>`;
  }
  return `<details class="git-project-command-history" data-git-project-action-history="${escapeHtml(actionKey)}"><summary>Command history (${history.length})</summary><pre>${escapeHtml(history.map(gitProjectFormatHistoryEntry).join("\n\n---\n\n"))}</pre></details>`;
}

function appendGitProjectActionHistory(actionKey = "", entry = {}) {
  const history = gitProjectReadHistory(actionKey);
  history.push({
    time: new Date().toLocaleString(),
    ...entry,
  });
  gitProjectWriteHistory(actionKey, history);
  const node = document.querySelector(`[data-git-project-action-history="${CSS.escape(actionKey)}"]`);
  if (node) {
    node.outerHTML = renderGitProjectActionHistory(actionKey);
    const fresh = document.querySelector(`[data-git-project-action-history="${CSS.escape(actionKey)}"]`);
    if (fresh) fresh.open = true;
  }
}

function gitProjectLastActionStatus(actionKey = "") {
  const history = gitProjectReadHistory(actionKey);
  const last = history.length ? history[history.length - 1] : null;
  return String(last?.status || "idle");
}

function gitProjectActionStatusLabel(actionKey = "") {
  const status = gitProjectLastActionStatus(actionKey);
  const allowed = new Set(["idle", "queued", "running", "completed", "failed", "canceled"]);
  return allowed.has(status) ? status : "idle";
}

function renderGitProjectCommandBox(step = {}, actionKey = "") {
  const action = gitProjectRunnableCommandInfo(step, actionKey);
  if (!action.details) return "";
  const status = gitProjectActionStatusLabel(actionKey);
  const state = action.state || gitProjectPanelStateForStep(step, actionKey);
  const stepComponentId = gitProjectWizardStepComponentId(step, actionKey);
  const stepLabel = gitProjectVisibleStepLabel(step);
  return `<details class="git-project-command-runner" data-git-project-runner="${escapeHtml(actionKey)}" ${gitProjectMcComponentAttrs(`${stepComponentId}.command-runner`, "panel", `${stepLabel} Command Details`, stepComponentId)}>
    <summary data-mc-component-id="${escapeHtml(stepComponentId)}.command-runner.summary" data-mc-component-kind="status" data-mc-component-label="${escapeHtml(stepLabel)} Command Details Summary" data-mc-component-owner="${escapeHtml(stepComponentId)}.command-runner" data-mc-feature-id="git-tools.feature.projects">Command details</summary>
    <div class="git-project-action-status" data-git-project-action-status="${escapeHtml(actionKey)}" data-mc-component-id="${escapeHtml(stepComponentId)}.command-runner.status" data-mc-component-kind="status" data-mc-component-label="${escapeHtml(stepLabel)} Command Status" data-mc-component-owner="${escapeHtml(stepComponentId)}.command-runner" data-mc-feature-id="git-tools.feature.projects">current status: ${escapeHtml(status)}</div>
    <pre class="git-project-command-preview" data-mc-component-id="${escapeHtml(stepComponentId)}.command-runner.preview" data-mc-component-kind="output" data-mc-component-label="${escapeHtml(stepLabel)} Command Preview" data-mc-component-owner="${escapeHtml(stepComponentId)}.command-runner" data-mc-feature-id="git-tools.feature.projects">${escapeHtml(action.details)}</pre>
    <details class="git-project-request-state" data-mc-component-id="${escapeHtml(stepComponentId)}.command-runner.request-state" data-mc-component-kind="output" data-mc-component-label="${escapeHtml(stepLabel)} Request State" data-mc-component-owner="${escapeHtml(stepComponentId)}.command-runner" data-mc-feature-id="git-tools.feature.projects">
      <summary>Request state</summary>
      <pre>${escapeHtml(JSON.stringify(state, null, 2))}</pre>
    </details>
    ${renderGitProjectActionHistory(actionKey)}
  </details>`;
}

function renderGitProjectActionButton(_step = {}, _scope = "wizard") {
  return "";
}

function bindGitProjectActionButtons(_container) {
  return;
}

function setGitProjectActionRunning(actionKey = "", running = false) {
  document.querySelectorAll(`[data-git-project-action-status="${CSS.escape(actionKey)}"]`).forEach((node) => {
    node.textContent = `current status: ${running ? "running" : gitProjectActionStatusLabel(actionKey)}`;
  });
}

async function pollGitProjectActionHistory(actionKey = "", label = "") {
  try {
    const status = await gitToolsStatusApi().fetchOperationStatus();
    const active = status.active || null;
    if (active) {
      appendGitProjectActionHistory(actionKey, {
        status: active.status === "cancelling" ? "canceled" : "running",
        label,
        logs: active.logs || [],
        result: active.result || {},
      });
    }
    return status;
  } catch (error) {
    appendGitProjectActionHistory(actionKey, {status: "poll failed", label, result: {error: error.message || String(error)}});
    return null;
  }
}

async function stopGitProjectAction(actionKey = "") {
  appendGitProjectActionHistory(actionKey, {status: "canceled", label: actionKey, result: {message: "Stop button clicked."}});
  await gitToolsStatusApi().cancelOperation().catch((error) => {
    appendGitProjectActionHistory(actionKey, {status: "failed", label: actionKey, result: {error: error.message || String(error)}});
  });
}

async function runGitProjectAction(actionKey) {
  const action = gitProjectWizardActionMap.get(actionKey);
  if (!action) return;
  const commandLines = gitProjectExecutableLinesFromCommands(action.commands || [action.command]).filter(gitProjectCommandIsRunnable);
  const state = action.state || {};
  if (!action.ready || !commandLines.length) {
    appendGitProjectActionHistory(actionKey, {
      status: "not ready",
      label: action.label,
      commands: commandLines,
      result: {error: action?.reason || "This step is not ready to run yet."},
    });
    if (gitConsoleOutput) gitConsoleOutput.textContent = action?.reason || "This step is not ready to run yet.";
    return;
  }
  expandGitWorkflowSection("ai-interpretation", `running backend action: ${action.label}`);
  updateGitWorkflowSectionSummary("ai-interpretation", `running backend action: ${action.label}`);
  appendGitProjectActionHistory(actionKey, {
    status: "queued",
    label: action.label,
    commands: commandLines,
    result: {mode: "backend-action-runner", repo: state.repo || gitRepoDir?.value || "."},
  });
  setGitProjectActionRunning(actionKey, true);
  const pollTimer = window.setInterval(() => {
    pollGitProjectActionHistory(actionKey, action.label).catch(() => null);
  }, 1200);
  try {
    const data = await gitToolsStatusApi().runProjectAction({
      action_key: actionKey,
      label: action.label,
      commands: commandLines,
      state,
      repo_dir: gitRepoDir?.value || state.repo || ".",
    });
    appendGitProjectActionHistory(actionKey, {
      status: data.ok ? "completed" : "failed",
      label: action.label,
      commands: commandLines,
      logs: data.operation?.logs || [],
      result: data,
    });
    showGitConsolePayload(data);
    await refreshGitStatus().catch(() => null);
    await inspectSelectedGitProject({quiet: true}).catch(() => null);
  } catch (error) {
    appendGitProjectActionHistory(actionKey, {
      status: "failed",
      label: action.label,
      commands: commandLines,
      result: error.details || {error: error.message || String(error)},
    });
    if (gitConsoleOutput) gitConsoleOutput.textContent = `Backend action failed: ${error.message || error}`;
  } finally {
    window.clearInterval(pollTimer);
    setGitProjectActionRunning(actionKey, false);
  }
}

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    projectBadges,
    loadGitProjects,
    renderGitProjects,
    renderGitProjectList,
    handleGitProjectAction,
    addGitProjectFromInput,
    setSelectedGitProjectLock,
    inspectSelectedGitProject,
    gitProjectHistoryStorageKey,
    gitProjectReadHistory,
    gitProjectWriteHistory,
    gitProjectFormatHistoryEntry,
    renderGitProjectActionHistory,
    appendGitProjectActionHistory,
    gitProjectLastActionStatus,
    gitProjectActionStatusLabel,
    renderGitProjectCommandBox,
    renderGitProjectActionButton,
    bindGitProjectActionButtons,
    setGitProjectActionRunning,
    pollGitProjectActionHistory,
    stopGitProjectAction,
    runGitProjectAction
  });

  global.GitToolsProjectPanel = api;
  Object.assign(global, {
    projectBadges,
    loadGitProjects,
    renderGitProjects,
    renderGitProjectList,
    handleGitProjectAction,
    addGitProjectFromInput,
    setSelectedGitProjectLock,
    inspectSelectedGitProject,
    gitProjectHistoryStorageKey,
    gitProjectReadHistory,
    gitProjectWriteHistory,
    gitProjectFormatHistoryEntry,
    renderGitProjectActionHistory,
    appendGitProjectActionHistory,
    gitProjectLastActionStatus,
    gitProjectActionStatusLabel,
    renderGitProjectCommandBox,
    renderGitProjectActionButton,
    bindGitProjectActionButtons,
    setGitProjectActionRunning,
    pollGitProjectActionHistory,
    stopGitProjectAction,
    runGitProjectAction
  });
})(window);
