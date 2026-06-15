function taskManagerMcelFlagValue(search = window.location.search) {
  try {
    return (new URLSearchParams(String(search || "")).get("mcel") || "").trim().toLowerCase();
  } catch {
    return "";
  }
}

function taskManagerMcelStorageFlag(key) {
  try {
    return String(localStorage.getItem(key) || "").trim().toLowerCase();
  } catch {
    return "";
  }
}

const taskManagerMcelEnableValues = new Set(["1", "true", "on", "yes", "enabled"]);
const taskManagerMcelDisableValues = new Set(["0", "false", "off", "no", "disabled"]);
let taskManagerMcelUrlSessionEnabled = false;
let taskManagerMcelLastReport = null;
let taskManagerMcelApplyScheduled = false;

function taskManagerMcelAppEnabled() {
  const queryValue = taskManagerMcelFlagValue();
  if (taskManagerMcelDisableValues.has(queryValue)) {
    taskManagerMcelUrlSessionEnabled = false;
    return false;
  }
  if (taskManagerMcelEnableValues.has(queryValue)) {
    taskManagerMcelUrlSessionEnabled = true;
  }

  const disabledValue = taskManagerMcelStorageFlag("taskManagerMcelDisabled");
  if (taskManagerMcelEnableValues.has(disabledValue)) {
    return false;
  }

  return true;
}

function applyTaskManagerMcelAppSemantics(reason = "app-refresh") {
  if (!taskManagerMcelAppEnabled()) {
    return null;
  }
  const adapter = window.TaskManagerMcel;
  if (typeof adapter?.applyTaskManagerMcelSemantics !== "function") {
    return null;
  }
  try {
    taskManagerMcelLastReport = adapter.applyTaskManagerMcelSemantics({
      document,
      rootSelector: "#task-manager-app",
      route: `${window.location.pathname}${window.location.search}${window.location.hash}`,
      mode: "app",
      reason,
      report: false
    });
    window.taskManagerMcelLastReport = taskManagerMcelLastReport;
    taskManagerApp?.setAttribute?.("data-task-manager-mcel-mode", "passive");
    return taskManagerMcelLastReport;
  } catch (error) {
    console.warn("Task Manager MCEL enrichment failed:", error);
    return null;
  }
}

function scheduleTaskManagerMcelAppSemantics(reason = "app-refresh") {
  if (!taskManagerMcelAppEnabled() || taskManagerMcelApplyScheduled) {
    return;
  }
  taskManagerMcelApplyScheduled = true;
  const raf = typeof window.requestAnimationFrame === "function"
    ? window.requestAnimationFrame.bind(window)
    : (callback) => window.setTimeout(callback, 16);
  raf(() => {
    taskManagerMcelApplyScheduled = false;
    applyTaskManagerMcelAppSemantics(reason);
  });
}

window.taskManagerMcelStatus = function taskManagerMcelStatus() {
  return {
    enabled: taskManagerMcelAppEnabled(),
    sessionEnabled: taskManagerMcelUrlSessionEnabled,
    adapterAvailable: typeof window.TaskManagerMcel?.applyTaskManagerMcelSemantics === "function",
    lastReport: taskManagerMcelLastReport
  };
};


function initTaskManagerApp() {
    if (!taskManagerInitialized) {
      taskManagerInitialized = true;
      taskNotebookTabButtons.forEach((button) => {
        button.addEventListener("click", () => {
          setTaskNotebookTab(button.dataset.taskTab || "server-processes");
        });
      });
    setTaskNotebookTab(taskNotebookTabFromPath(window.location.pathname), {replaceRoute: true});
    if (gitProjectNextStep) {
      gitProjectNextStep.addEventListener("click", (event) => {
        const target = event.target instanceof Element ? event.target : event.target?.parentElement;
        const button = target?.closest("button[data-git-repo-boundary-action='open']");
        if (!button) return;
        event.preventDefault();
        openGitProjectRepoBoundaryModal(gitProjectLastInspection);
      });
    }
    taskRefresh.addEventListener("click", () => refreshTaskManager().catch(() => null));
    if (taskServerStatus) taskServerStatus.addEventListener("click", () => runTaskAction("server_status", {}, false));
    if (taskServerShutdown) taskServerShutdown.addEventListener("click", () => runTaskAction("server_shutdown", {}, true));
    if (taskServerStart) taskServerStart.addEventListener("click", () => runTaskAction("server_start", {}, true));
    if (taskServerRestart) taskServerRestart.addEventListener("click", () => runTaskAction("server_restart", {}, true));
    taskScheduleCreate.addEventListener("click", createTaskSchedule);
    taskSchedulesRefresh.addEventListener("click", () => refreshTaskManager().catch(() => null));
    taskAiAnalyze.addEventListener("click", askTaskManagerAi);
    taskAutoRefresh.addEventListener("change", scheduleTaskManagerAutoRefresh);
    taskQuery.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        refreshTaskManager();
      }
    });
    [taskProcessTable, taskAllProcessTable].filter(Boolean).forEach((table) => {
      table.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-task-action]");
        if (!button) return;
        const pid = Number(button.dataset.taskPid || 0);
        if (!pid) return;
        const action = button.dataset.taskAction === "kill" ? "kill_pid" : "terminate_pid";
        runTaskAction(action, {pid}, true);
      });
    });
    taskScheduleList.addEventListener("click", (event) => {
      const deleteButton = event.target.closest("button[data-task-delete]");
      if (deleteButton) {
        deleteTaskSchedule(deleteButton.dataset.taskDelete || "");
        return;
      }
      const runButton = event.target.closest("button[data-task-run]");
      if (runButton) {
        const action = runButton.dataset.taskActionName || "server_status";
        runTaskAction(action, {}, true);
      }
    });
    if (!taskScheduleWhen.value) {
      const now = new Date(Date.now() + 10 * 60 * 1000);
      taskScheduleWhen.value = now.toISOString().slice(0, 16);
    }
  }
  scheduleTaskManagerAutoRefresh();
  updateTaskManagerWidgetTickers(taskManagerSnapshotCache, "Task manager awaiting first snapshot.", "Task manager ready");
  updateTaskAiTicker(taskAiOutput.textContent);
  scheduleTaskManagerMcelAppSemantics("init");
  refreshTaskManager().catch(() => null);
}

const gitProjectWizardActionMap = new Map();

function gitToolsRequest(path, payload = {}) {
  return fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  }).then(async (response) => {
    const raw = await response.text();
    let data = {};
    if (raw.trim()) {
      try {
        data = JSON.parse(raw);
      } catch (_error) {
        data = {raw};
      }
    }
    if (!response.ok) {
      const message = data.error || data.message || data.raw || `HTTP ${response.status}`;
      const error = new Error(message);
      error.status = response.status;
      error.details = data;
      throw error;
    }
    return data;
  });
}
function gitToolsOperationErrorText(prefix, error) {
  const lines = [`${prefix}: ${error?.message || error}`];
  if (error?.status) lines.push(`HTTP status: ${error.status}`);
  if (error?.details && Object.keys(error.details).length) {
    lines.push("", "Details:", JSON.stringify(error.details, null, 2));
  }
  return lines.join("\n");
}
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
function currentGitProject() {
  return gitProjectsLastState?.current_project || null;
}
function gitProjectNormalizePathForCompare(path = "") {
  return String(path || "").trim().replaceAll("/", "\\").replace(/\\+$/g, "").toLowerCase();
}
function gitProjectSamePath(left = "", right = "") {
  const normalizedLeft = gitProjectNormalizePathForCompare(left);
  const normalizedRight = gitProjectNormalizePathForCompare(right);
  return Boolean(normalizedLeft && normalizedRight && normalizedLeft === normalizedRight);
}
function gitProjectSetTargetPathInputs(path = "") {
  const cleaned = String(path || "").trim();
  if (!cleaned) return;
  [gitProjectPath, gitRepoDir].filter(Boolean).forEach((input) => {
    if (input.value === cleaned) return;
    input.value = cleaned;
    input.dispatchEvent(new Event("input", {bubbles: true}));
    input.dispatchEvent(new Event("change", {bubbles: true}));
  });
}
function gitToolsRepoDirValue(fallback = ".") {
  const candidates = [
    gitRepoDir?.value,
    gitProjectPath?.value,
    currentGitProject()?.path,
    gitProjectLastInspection?.selected_project,
    gitProjectLastInspection?.project?.path,
    fallback,
  ];
  for (const candidate of candidates) {
    const cleaned = String(candidate || "").trim();
    if (cleaned) return cleaned;
  }
  return fallback;
}
function gitProjectDescribeTargetSources(workbench = null) {
  const current = currentGitProject() || {};
  return {
    current_project_id: String(current.id || ""),
    current_project_path: String(current.path || ""),
    inspection_project_id: String(gitProjectLastInspection?.project?.id || ""),
    inspection_project_path: String(gitProjectLastInspection?.project?.path || ""),
    inspection_selected_project: String(gitProjectLastInspection?.selected_project || ""),
    workbench_repo: String(workbench?.dataset?.gitCommitRepo || ""),
    input_project_path: String(gitProjectPath?.value || ""),
    input_repo_dir: String(gitRepoDir?.value || ""),
  };
}
function gitProjectCommitTargetMismatch(workbench = null, repoDir = "") {
  const current = currentGitProject() || {};
  const expected = String(current.path || "").trim();
  const sources = gitProjectDescribeTargetSources(workbench);
  if (!expected) {
    return {ok: true, expected, sources, mismatches: []};
  }
  const mismatches = Object.entries(sources)
    .filter(([key, value]) => key.endsWith("_path") || key === "inspection_selected_project" || key === "workbench_repo" || key === "input_repo_dir")
    .filter(([_key, value]) => String(value || "").trim())
    .filter(([_key, value]) => !gitProjectSamePath(value, expected))
    .map(([key, value]) => `${key}: ${value}`);
  if (repoDir && !gitProjectSamePath(repoDir, expected)) {
    mismatches.unshift(`payload repo_dir: ${repoDir}`);
  }
  return {ok: mismatches.length === 0, expected, sources, mismatches};
}
function setGitProjectNextStep(title, message = "", meta = "", tone = "informative", actionHtml = "") {
  if (!gitProjectNextStep) return;
  gitProjectNextStep.classList.remove("tone-blocking", "tone-actionable", "tone-informative");
  gitProjectNextStep.classList.add(`tone-${tone || "informative"}`);
  gitProjectNextStep.innerHTML = [
    `<strong>${escapeHtml(title || "Next step")}</strong>`,
    message ? `<span>${escapeHtml(message)}</span>` : "",
    meta ? `<code>${escapeHtml(meta)}</code>` : "",
    actionHtml ? `<div class="git-project-next-step-actions">${actionHtml}</div>` : "",
  ].filter(Boolean).join("");
}

const gitProjectRepoBoundaryPrompted = new Set();

function gitProjectRepoBoundarySelectedPath(data = null) {
  const git = data?.git || {};
  const dirtyDetection = data?.dirty_plan?.git_detection || {};
  const project = data?.project || currentGitProject() || {};
  return String(
    project.path ||
    data?.selected_project ||
    git.input_path ||
    dirtyDetection.input_path ||
    ""
  ).trim();
}

function gitProjectRepoBoundaryParentRoot(data = null) {
  const git = data?.git || {};
  const dirtyDetection = data?.dirty_plan?.git_detection || {};
  return String(
    git.parent_git_root ||
    git.parent_worktree_root ||
    dirtyDetection.parent_worktree_root ||
    dirtyDetection.parent_git_root ||
    ""
  ).trim();
}

function gitProjectNeedsRepoBoundaryChoice(data = null) {
  const git = data?.git || {};
  const dirtyDetection = data?.dirty_plan?.git_detection || {};
  const repoState = String(
    git.repo_state ||
    git.repository_state ||
    dirtyDetection.repo_state ||
    dirtyDetection.repository_state ||
    ""
  );
  return Boolean(
    data &&
    git.is_git_repo === false &&
    (
      repoState === "inside_parent_repo_only" ||
      git.input_inside_parent_repo ||
      dirtyDetection.input_inside_parent_repo ||
      gitProjectRepoBoundaryParentRoot(data)
    )
  );
}

function gitProjectRepoBoundaryActionHtml(data = null) {
  if (!gitProjectNeedsRepoBoundaryChoice(data)) return "";
  return `<button type="button" class="git-project-repo-boundary-open" data-git-repo-boundary-action="open">Choose Git Tracking Method</button>`;
}

function maybePromptGitProjectRepoBoundary(data = null) {
  if (!gitProjectNeedsRepoBoundaryChoice(data)) return;
  const selected = gitProjectRepoBoundarySelectedPath(data);
  const parent = gitProjectRepoBoundaryParentRoot(data);
  const promptKey = `${selected} -> ${parent}`;
  if (!selected || !parent || gitProjectRepoBoundaryPrompted.has(promptKey)) return;
  gitProjectRepoBoundaryPrompted.add(promptKey);
  window.setTimeout(() => openGitProjectRepoBoundaryModal(data), 0);
}

function closeGitProjectRepoBoundaryModal() {
  document.querySelectorAll(".git-project-repo-boundary-overlay").forEach((node) => node.remove());
  document.body?.classList.remove("git-project-repo-boundary-modal-open");
}

function gitProjectRepoBoundaryModalStatus(modal, message = "", tone = "informative") {
  const status = modal?.querySelector("[data-git-repo-boundary-status]");
  if (!status) return;
  status.classList.remove("tone-blocking", "tone-actionable", "tone-informative");
  status.classList.add(`tone-${tone || "informative"}`);
  status.textContent = message || "";
}

function gitProjectSetRepoBoundaryButtonsDisabled(modal, disabled) {
  modal?.querySelectorAll("button[data-git-repo-boundary-action]").forEach((button) => {
    button.disabled = Boolean(disabled);
  });
}

function gitProjectRepoBoundaryInitCommands(selectedPath = "") {
  const quoted = gitProjectShellQuote(selectedPath);
  return [
    `git -C ${quoted} init`,
    `git -C ${quoted} status --short --branch`,
  ];
}

async function gitProjectInitializeSelectedFolderFromBoundary(selectedPath = "", modal = null) {
  const path = String(selectedPath || "").trim();
  if (!path) throw new Error("Selected folder path is missing.");
  gitProjectSetRepoBoundaryButtonsDisabled(modal, true);
  gitProjectRepoBoundaryModalStatus(modal, "Starting Git in the selected folder…", "actionable");
  setGitProjectNextStep("Starting Git in this folder…", "The backend safety runner is validating and running git init for the selected folder.", path, "actionable");
  const commands = gitProjectRepoBoundaryInitCommands(path);
  const data = await gitToolsRequest("/api/applications/git/project/action/run", {
    action_key: "repo-boundary:initialize_repository_here",
    label: "Start Git in this folder",
    repo_dir: path,
    commands,
    state: {
      panel_id: "repo-boundary:initialize_repository_here",
      action_id: "initialize_repository_here",
      label: "Start Git in this folder",
      repo: path,
      repo_slash: gitProjectSlashPath(path),
      allow_mutating_actions: false,
      allow_python_git_control: false,
      line_endings: "lf",
    },
  });
  showGitConsolePayload(data);
  closeGitProjectRepoBoundaryModal();
  setGitProjectNextStep("Git initialized here", "The selected folder now has local Git metadata. Re-running the project inspection.", path, "actionable");
  await refreshGitStatus().catch(() => null);
  await inspectSelectedGitProject();
}

async function gitProjectUseParentRepositoryFromBoundary(parentPath = "", modal = null) {
  const path = String(parentPath || "").trim();
  if (!path) throw new Error("Parent repository path is missing.");
  gitProjectSetRepoBoundaryButtonsDisabled(modal, true);
  gitProjectRepoBoundaryModalStatus(modal, "Switching Git Tools to the parent repository…", "actionable");
  setGitProjectNextStep("Using parent repository…", "Registering and selecting the parent Git root for future Git actions.", path, "actionable");
  const data = await gitToolsRequest("/api/applications/git/project/add", {path, select: true});
  gitProjectsLastState = data;
  renderGitProjects(data);
  if (data.current_project?.path) gitProjectSetTargetPathInputs(data.current_project.path);
  closeGitProjectRepoBoundaryModal();
  await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
  await inspectSelectedGitProject();
  await refreshGitStatus();
}

function openGitProjectRepoBoundaryModal(data = gitProjectLastInspection) {
  if (!gitProjectNeedsRepoBoundaryChoice(data)) return;
  const selected = gitProjectRepoBoundarySelectedPath(data);
  const parent = gitProjectRepoBoundaryParentRoot(data);
  closeGitProjectRepoBoundaryModal();
  const overlay = document.createElement("div");
  overlay.className = "git-project-repo-boundary-overlay";
  overlay.innerHTML = `<section class="git-project-repo-boundary-dialog" role="dialog" aria-modal="true" aria-labelledby="git-project-repo-boundary-title">
    <header class="git-project-repo-boundary-header">
      <div>
        <strong id="git-project-repo-boundary-title">How should Git track this folder?</strong>
        <span>repository boundary choice required</span>
      </div>
      <button type="button" data-git-repo-boundary-action="cancel" aria-label="Close repository boundary dialog">×</button>
    </header>
    <div class="git-project-repo-boundary-body">
      <p>The selected folder is not a Git repository yet, but Git discovered a parent repository. Choose the boundary before any mutating Git action runs.</p>
      <div class="git-project-repo-boundary-paths">
        <div><span>Selected folder</span><code>${escapeHtml(selected || "(unknown)")}</code></div>
        <div><span>Parent Git repository</span><code>${escapeHtml(parent || "(unknown)")}</code></div>
      </div>
      <div class="git-project-repo-boundary-options">
        <button type="button" class="primary" data-git-repo-boundary-action="init-here">
          <strong>Start Git in this folder</strong>
          <span>Create a local .git directory here. This makes the selected folder a standalone nested repository.</span>
        </button>
        <button type="button" data-git-repo-boundary-action="use-parent">
          <strong>Use parent repository</strong>
          <span>Switch Git Tools to the parent root and treat the selected folder as part of that repository.</span>
        </button>
        <button type="button" data-git-repo-boundary-action="choose-folder">
          <strong>Choose another folder</strong>
          <span>Return to the project path field without changing Git metadata.</span>
        </button>
      </div>
      <div class="git-project-repo-boundary-warning">
        Starting Git here intentionally creates a nested repository inside the parent repo. The parent repo will not track this folder's contents normally after that.
      </div>
      <div class="git-project-repo-boundary-status" data-git-repo-boundary-status></div>
    </div>
    <footer class="git-project-repo-boundary-footer">
      <button type="button" data-git-repo-boundary-action="cancel">Cancel</button>
    </footer>
  </section>`;
  document.body?.appendChild(overlay);
  document.body?.classList.add("git-project-repo-boundary-modal-open");
  overlay.addEventListener("click", async (event) => {
    if (event.target === overlay) {
      closeGitProjectRepoBoundaryModal();
      return;
    }
    const button = event.target.closest("button[data-git-repo-boundary-action]");
    if (!button) return;
    const action = button.dataset.gitRepoBoundaryAction || "";
    try {
      if (action === "cancel") {
        closeGitProjectRepoBoundaryModal();
      } else if (action === "choose-folder") {
        closeGitProjectRepoBoundaryModal();
        gitProjectPath?.focus();
        gitProjectPath?.select?.();
        setGitProjectNextStep("Choose a project folder", "Pick the folder that should own Git operations, then inspect it again.", selected || "", "actionable");
      } else if (action === "init-here") {
        await gitProjectInitializeSelectedFolderFromBoundary(selected, overlay);
      } else if (action === "use-parent") {
        await gitProjectUseParentRepositoryFromBoundary(parent, overlay);
      }
    } catch (error) {
      gitProjectSetRepoBoundaryButtonsDisabled(overlay, false);
      gitProjectRepoBoundaryModalStatus(overlay, error?.message || String(error), "blocking");
      if (gitProjectDashboard) gitProjectDashboard.textContent = gitToolsOperationErrorText("Repository boundary action failed", error);
      setGitProjectNextStep("Repository boundary action failed", error?.message || String(error), selected || parent || "", "blocking", gitProjectRepoBoundaryActionHtml(data));
    }
  });
  overlay.querySelector("[data-git-repo-boundary-action='init-here']")?.focus();
}

function nextGitProjectStepText(data = null) {
  const project = data?.project || currentGitProject() || {};
  const git = data?.git || {};
  const dirty = data?.dirty_plan || {};
  const blocking = Array.isArray(data?.blocking) ? data.blocking : [];
  if (!project.id) {
    return {
      title: "Next step",
      message: "Select a project from Current Projects, or add a project path.",
      meta: "",
      tone: "informative",
    };
  }
  if (!data) {
    return {
      title: "Next: inspect selected project",
      message: "Click Inspect or Rescan Selected to run git_dirty.py plan for the highlighted project.",
      meta: project.path || "",
      tone: "actionable",
    };
  }
  if (!git.is_git_repo) {
    const metadataKind = git.selected_path_git_metadata_kind || "missing";
    const parent = gitProjectRepoBoundaryParentRoot(data);
    if (gitProjectNeedsRepoBoundaryChoice(data)) {
      return {
        title: "Next: choose how Git should track this folder",
        message: "This folder is not a Git repository yet, but it is inside a parent Git repository. Choose whether to start a new repository here or use the parent repository.",
        meta: "choice required: repository boundary",
        tone: "actionable",
        action: "open_repo_boundary_modal",
      };
    }
    return {
      title: "Next: initialize this folder as a Git repo",
      message: `Selected folder has no local .git directory or .git file (${metadataKind}).${parent ? ` Parent Git root detected: ${parent}.` : ""} Start Git here before creating HEAD.`,
      meta: "blocked: git-init-required",
      tone: "blocking",
    };
  }
  if (!git.has_head) {
    return {
      title: "Next: Review Initial Snapshot",
      message: "Push is blocked because this repository has no HEAD commit. Review source/config and generated/noise groups before creating the first commit.",
      meta: "blocked: initial-snapshot-required",
      tone: "blocking",
    };
  }
  if (project.locked) {
    return {
      title: "Next: review the report, then unlock only if you intend to mutate",
      message: "Read-only inspection is allowed while locked. Commit, clean, remote, and push actions stay blocked.",
      meta: "project locked",
      tone: "blocking",
    };
  }
  if (blocking.length) {
    return {
      title: "Next: clear blocked actions",
      message: blocking.map((item) => `${item.action}: ${item.reason}`).join(" · "),
      meta: "",
      tone: "blocking",
    };
  }
  return {
    title: "Next: choose a wizard step",
    message: `Dirty ${Number(dirty.dirty_score || 0)}/100. Start with the highest-priority actionable step below.`,
    meta: dirty.recommended_strategy || "",
    tone: "actionable",
  };
}
function renderGitProjectNextStep(data = null) {
  const next = nextGitProjectStepText(data);
  const actionHtml = next.action === "open_repo_boundary_modal" ? gitProjectRepoBoundaryActionHtml(data) : "";
  setGitProjectNextStep(next.title, next.message, next.meta, next.tone || "informative", actionHtml);
  if (next.action === "open_repo_boundary_modal") {
    maybePromptGitProjectRepoBoundary(data);
  }
}
async function loadGitProjects() {
  const data = await gitToolsRequest("/api/applications/git/projects", {});
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
  if (gitProjectCurrent) {
    gitProjectCurrent.innerHTML = current
      ? `<strong>${current.vip ? "★ " : ""}${escapeHtml(current.name || current.id)}</strong><span>${escapeHtml(projectBadges(current))}</span><code>${escapeHtml(current.path || "")}</code>`
      : `<strong>No project selected</strong><span>Select a project from Current Projects.</span>`;
  }
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
    const data = await gitToolsRequest("/api/applications/git/project/select", {project_id: projectId});
    gitProjectsLastState = data;
    renderGitProjects(data);
    if (data.current_project?.path) gitProjectSetTargetPathInputs(data.current_project.path);
    await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
    await inspectSelectedGitProject();
    await refreshGitStatus();
  } else if (action === "archive") {
    const data = await gitToolsRequest("/api/applications/git/project/archive", {project_id: projectId});
    gitProjectsLastState = data;
    renderGitProjects(data);
  } else if (action === "restore") {
    clearGitServerTargetForProjectChange();
    const data = await gitToolsRequest("/api/applications/git/project/restore", {project_id: projectId, select: true});
    gitProjectsLastState = data;
    renderGitProjects(data);
    if (data.current_project?.path) gitProjectSetTargetPathInputs(data.current_project.path);
    await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
  } else if (action === "lock" || action === "unlock") {
    const data = await gitToolsRequest("/api/applications/git/project/lock", {project_id: projectId, locked: action === "lock"});
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
    const data = await gitToolsRequest("/api/applications/git/project/add", {path, select: true});
    gitProjectsLastState = data;
    renderGitProjects(data);
    if (data.current_project?.path) gitProjectSetTargetPathInputs(data.current_project.path);
    await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
    await inspectSelectedGitProject();
    await refreshGitStatus();
  } catch (error) {
    if (gitProjectDashboard) gitProjectDashboard.textContent = gitToolsOperationErrorText("Add project failed", error);
    setGitProjectNextStep("Add project failed", error?.message || String(error), path, "blocking");
  }
}
async function setSelectedGitProjectLock(locked) {
  const project = currentGitProject();
  if (!project) return;
  try {
    const data = await gitToolsRequest("/api/applications/git/project/lock", {project_id: project.id, locked});
    gitProjectsLastState = data;
    renderGitProjects(data);
    await inspectSelectedGitProject({quiet: true});
  } catch (error) {
    if (gitProjectDashboard) gitProjectDashboard.textContent = gitToolsOperationErrorText("Project lock update failed", error);
    setGitProjectNextStep("Project lock update failed", error?.message || String(error), project.path || "", "blocking");
  }
}
async function inspectSelectedGitProject(options = {}) {
  const projectId = options.project_id || currentGitProject()?.id || "";
  const payload = projectId ? {project_id: projectId} : {};
  try {
    if (!options.quiet) {
      setGitProjectNextStep("Inspecting selected project…", "Running git_dirty.py plan and Git state checks.", projectId || "current", "actionable");
    }
    const data = await gitToolsRequest("/api/applications/git/project/inspect", payload);
    gitProjectLastInspection = data;
    if (data.project?.path) gitProjectSetTargetPathInputs(data.project.path);
    renderGitProjectInspection(data);
    if (!options.quiet && gitProjectsLastState) {
      gitProjectsLastState.current_project = data.project;
      renderGitProjects(gitProjectsLastState);
    }
    return data;
  } catch (error) {
    if (gitProjectDashboard) gitProjectDashboard.textContent = gitToolsOperationErrorText("Project inspection failed", error);
    setGitProjectNextStep("Project inspection failed", error?.message || String(error), projectId || "", "blocking");
    throw error;
  }
}
function renderKeyValue(label, value) {
  return `<div class="git-project-kv"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? "")}</strong></div>`;
}
function dirtySummaryRows(summary = {}) {
  const ordered = ["source", "generated", "untracked", "modified", "staged", "unstaged", "conflicted", "blocking"];
  return ordered
    .filter((key) => Object.prototype.hasOwnProperty.call(summary, key))
    .map((key) => renderKeyValue(key, summary[key]))
    .join("");
}
function formatCommandForReport(command = {}) {
  return command.command || command.template || "";
}
function firstActionableWizardStep(wizard = {}) {
  const steps = Array.isArray(wizard.steps) ? wizard.steps : [];
  return steps.find((step) => !["succeeded", "skipped", "blocked"].includes(step.state || "") && !step.locked) || steps[0] || null;
}
function humanizeGitProjectToken(value = "") {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .split(" ")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}
function gitProjectActionKey(step = {}, scope = "wizard") {
  return `${scope}:${step.id || "step"}:${Number(step.order || 0)}`;
}
const GIT_PROJECT_MC_FEATURE_ID = "git-tools.feature.projects";
function gitProjectMcSlug(value = "", fallback = "item") {
  const slug = String(value || fallback)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || fallback;
}
function gitProjectMcAttribute(name, value) {
  return `${name}="${escapeHtml(value)}"`;
}
function gitProjectMcComponentAttrs(componentId, kind, label, owner = "git-tools.projects.panel", featureId = GIT_PROJECT_MC_FEATURE_ID) {
  return [
    gitProjectMcAttribute("data-mc-component-id", componentId),
    gitProjectMcAttribute("data-mc-component-kind", kind),
    gitProjectMcAttribute("data-mc-component-label", label),
    gitProjectMcAttribute("data-mc-component-owner", owner),
    gitProjectMcAttribute("data-mc-feature-id", featureId),
  ].join(" ");
}
function gitProjectWizardStepComponentId(step = {}, actionKey = "") {
  const slug = gitProjectMcSlug(step.id || step.label || actionKey, "step");
  return `git-tools.projects.wizard.step.${slug}.${Number(step.order || 0) + 1}`;
}
function gitProjectShellQuote(value = "") {
  return `"${String(value || ".").replace(/"/g, "\\\"")}"`;
}
function gitProjectSlashPath(value = "") {
  return String(value || "").replace(/\\/g, "/");
}
function gitProjectRuntimeContext(data = {}) {
  const project = data.project || currentGitProject() || {};
  const git = data.git || {};
  const repo = project.path || data.selected_project || git.git_root || gitRepoDir?.value || ".";
  return {
    repo,
    repoSlash: gitProjectSlashPath(repo),
    gitRoot: git.git_root || repo,
    gitDir: git.git_dir || ".git",
    gitCommonDir: git.git_common_dir || ".git",
    insideWorkTree: git.is_inside_work_tree ?? git.is_git_repo ?? "",
    branch: git.branch || "(none)",
    hasHead: Boolean(git.has_head),
    platform: navigator?.platform || "browser",
    lineEndings: "Commands and panel state are stored with LF in these boxes. The backend runner executes argv lists with shell disabled.",
  };
}
function gitProjectCommandText(command = {}) {
  return String(command.command || command.template || "").trim();
}
function gitProjectStepCommands(step = {}) {
  return Array.isArray(step.commands) ? step.commands : [];
}
function gitProjectInitialSnapshotCommands(step = {}, runtime = {}) {
  if (step.id !== "initial-snapshot-required") return [];
  const repo = runtime.repo || gitToolsRepoDirValue(".");
  return [
    {
      command: `git -C ${gitProjectShellQuote(repo)} rev-parse --show-toplevel --git-dir --git-common-dir --is-inside-work-tree`,
      purpose: "Confirm the selected repository root, git-dir, common-dir, and worktree state before creating the first commit.",
      safe: true,
      implemented: true,
      synthetic: true,
    },
    {
      command: `git -C ${gitProjectShellQuote(repo)} status --short --branch`,
      purpose: "Show the current branch and short status for the initial snapshot review.",
      safe: true,
      implemented: true,
      synthetic: true,
    },
  ];
}
function gitProjectCommandsForStep(step = {}) {
  const runtime = step.runtime || gitProjectRuntimeContext();
  const commands = gitProjectStepCommands(step).filter((command) => gitProjectCommandText(command));
  const synthetic = gitProjectInitialSnapshotCommands(step, runtime);
  return [...synthetic, ...commands];
}
const GIT_PROJECT_HEAD_FIX_RUNNER_HINT = "# python tools/git/git_tool_fix_project_head.py <validated-payload.json>";
const GIT_PROJECT_HEAD_FIX_STEP_IDS = new Set([
  "initial-snapshot-required",
  "initialize_repository_here",
  "start_tracking_this_folder",
  "update_gitignore_before_initial_commit",
  "create_initial_snapshot",
  "prepare_commit_snapshot",
  "start_tracking_real_work",
  "track_selected_files",
  "track_all_safe_source_files",
  "record_current_work_as_commit",
]);
function gitProjectPanelStateForStep(step = {}, actionKey = "") {
  const runtime = step.runtime || gitProjectRuntimeContext();
  return {
    panel_id: actionKey || gitProjectActionKey(step),
    action_id: step.id || "",
    label: step.label || "",
    repo: runtime.repo || ".",
    repo_slash: runtime.repoSlash || gitProjectSlashPath(runtime.repo || "."),
    git_root: runtime.gitRoot || "",
    git_dir: runtime.gitDir || "",
    git_common_dir: runtime.gitCommonDir || "",
    inside_work_tree: runtime.insideWorkTree,
    branch: runtime.branch || "",
    has_head: Boolean(runtime.hasHead),
    line_endings: "lf",
    allow_mutating_actions: false,
    allow_python_git_control: false,
    history_limit: 12,
    ui_note: "The browser sends structured commands and state only. The backend validates them before starting a disk-backed runner.",
  };
}
function gitProjectCommandLinesForStep(step = {}) {
  return gitProjectCommandsForStep(step).map(gitProjectCommandText).filter(Boolean);
}
function gitProjectStepUsesHeadFixRunner(step = {}) {
  const runtime = step.runtime || gitProjectRuntimeContext();
  const id = gitProjectStepId(step);
  return runtime.hasHead === false && GIT_PROJECT_HEAD_FIX_STEP_IDS.has(id);
}
function gitProjectCommandDetailsForStep(step = {}, actionKey = "") {
  const commands = gitProjectCommandLinesForStep(step);
  const lines = [];
  if (gitProjectStepUsesHeadFixRunner(step)) {
    lines.push("# Backend safety runner:");
    lines.push(GIT_PROJECT_HEAD_FIX_RUNNER_HINT);
    lines.push("");
  } else {
    lines.push("# Backend action request:");
    lines.push("# commands below are validated on the server before execution");
    lines.push("");
  }
  if (!commands.length) {
    lines.push("# <no command offered yet>");
  } else {
    commands.forEach((line) => lines.push(line));
  }
  return lines.join("\n");
}
function gitProjectExecutableLinesFromCommands(commands = []) {
  return (Array.isArray(commands) ? commands : [])
    .map((line) => String(line || "").trim())
    .filter(Boolean)
    .filter((line) => !line.startsWith("#") && !line.startsWith("//"));
}
function gitProjectCommandIsRunnable(commandText = "") {
  const firstLine = String(commandText || "").trim();
  if (!firstLine || firstLine.includes("<no command offered")) return false;
  if (/^git\s+/i.test(firstLine)) return true;
  if (/^"?[^"\s]*python(?:3|\.exe)?"?\s+.*git-control\.py\s+/i.test(firstLine)) return true;
  if (/^"?[^"\s]*python(?:3|\.exe)?"?\s+.*git_dirty\.py\s+/i.test(firstLine)) return true;
  if (/^py\s+.*git-control\.py\s+/i.test(firstLine)) return true;
  if (/^py\s+.*git_dirty\.py\s+/i.test(firstLine)) return true;
  return false;
}
function gitProjectRunnableCommandInfo(step = {}, actionKey = "") {
  const commands = gitProjectCommandLinesForStep(step);
  const runnableLines = gitProjectExecutableLinesFromCommands(commands).filter(gitProjectCommandIsRunnable);
  const details = gitProjectCommandDetailsForStep(step, actionKey);
  const state = gitProjectPanelStateForStep(step, actionKey);
  if (!commands.length || !runnableLines.length) {
    return {ready: false, reason: "No runnable command is attached to this step yet.", label: "Run command", command: "", commands: [], details, state};
  }
  if ((step.locked || step.state === "blocked" || step.tone === "blocking") && step.id !== "initial-snapshot-required") {
    return {ready: false, reason: "This step is blocked until the blocker above it is cleared.", label: "Run command", command: runnableLines[0] || "", commands: runnableLines, details, state};
  }
  const stepCommands = gitProjectCommandsForStep(step);
  if (stepCommands.some((command) => command.locked)) {
    return {ready: false, reason: "One or more commands are locked and cannot run yet.", label: "Run command", command: runnableLines[0] || "", commands: runnableLines, details, state};
  }
  if (stepCommands.some((command) => command.implemented === false)) {
    return {ready: false, reason: "One or more commands are only templates right now and are not ready to run.", label: "Run command", command: runnableLines[0] || "", commands: runnableLines, details, state};
  }
  return {
    ready: true,
    reason: runnableLines.length === 1 ? "This command is complete and ready to request through the backend safety runner." : `${runnableLines.length} safe command lines are complete and ready to request through the backend safety runner.`,
    label: "Run command",
    command: runnableLines[0] || "",
    commands: runnableLines,
    details,
    state,
  };
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
    const status = await gitToolsRequest("/api/applications/git/server/operation/status", {});
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
  await gitToolsRequest("/api/applications/git/server/operation/cancel", {}).catch((error) => {
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
    const data = await gitToolsRequest("/api/applications/git/project/action/run", {
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
function gitProjectFirstCommitGateOrder(step = {}) {
  const id = gitProjectStepId(step);
  if (id === "update_gitignore_before_initial_commit" || id === "ignore_generated_files" || id === "ignore_local_environment_files") return 10;
  if (id === "secrets_filter") return 20;
  if (["prepare_commit_snapshot", "create_initial_snapshot"].includes(id)) return 30;
  return Number.POSITIVE_INFINITY;
}
function weightForWizardStep(step = {}, data = {}) {
  const project = data.project || {};
  const git = data.git || {};
  const dirty = data.dirty_plan || {};
  let weight = 60 - Number(step.order || 0);
  const id = `${step.id || ""} ${step.label || ""}`.toLowerCase();
  if (!git.is_git_repo) weight += 25;
  if (!git.has_head && (id.includes("initial snapshot") || id.includes("initial_snapshot") || id.includes("first git commit") || id.includes("prepare_commit_snapshot") || id.includes("take snapshot") || id.includes("commit") || id.includes("gitignore"))) weight += 40;
  if (id.includes("prepare_commit_snapshot") || id.includes("take snapshot")) weight += 16;
  if (id.includes("secrets_filter") || id.includes("secrets / filter")) weight += 18;
  if (id.includes("push")) weight -= 10;
  if (id.includes("find repository root")) weight += 8;
  if (id.includes("save current state")) weight += 24;
  if (id.includes("classify")) weight += 6;
  if (id.includes("start tracking")) weight += 18;
  if (id.includes("ignore generated")) weight += 14;
  if (id.includes("inspect configured remotes")) weight += 4;
  if (id.includes("push")) weight += 8;
  if (project.locked && !id.includes("find repository root") && !id.includes("inspect configured remotes") && !id.includes("classify")) weight += 2;
  if (Number(dirty.dirty_score || 0) >= 20) weight += 4;
  return Math.max(weight, 1);
}
const GIT_PROJECT_EVIDENCE_STEP_IDS = new Set([
  "find_repository_root",
  "measure_dirty_state",
  "make_cleanup_plan",
  "classify_changed_files",
  "find_blocking_problems",
  "rank_cleanup_risk",
  "explain_each_dirty_item",
  "compare_to_remote_state",
  "find_nested_repositories",
  "find_generated_artifacts",
  "inspect_configured_remotes",
  "list_saved_states",
  "refresh_action_log",
]);
const GIT_PROJECT_ATTENTION_STEP_IDS = new Set([
  "choose_correct_repository_root",
  "stop_until_repository_is_clear",
  "show_merge_conflicts",
  "open_conflict_for_manual_fix",
  "abort_merge_or_rebase",
  "initial-snapshot-required",
]);
const GIT_PROJECT_USER_ACTION_KINDS = new Set([
  "repository",
  "safety",
  "preserve",
  "ignore",
  "cleanup",
  "conflict",
  "workflow",
  "remote",
  "execution",
]);
const GIT_PROJECT_COMMIT_CARD_STEP_IDS = new Set([
  "prepare_commit_snapshot",
  "create_initial_snapshot",
  "record_current_work_as_commit",
  "start_tracking_real_work",
]);
function gitProjectStepId(step = {}) {
  return String(step.id || "").trim();
}
function gitProjectStepKind(step = {}) {
  return String(step.kind || "").trim();
}
function gitProjectRemoteStepIsCurrentlyRequired(data = {}) {
  const wizard = data.wizard || {};
  const steps = Array.isArray(wizard.steps) ? wizard.steps : [];
  return steps.some((step = {}) => {
    const id = gitProjectStepId(step);
    if (id === "inspect_configured_remotes") return false;
    if (!id.includes("remote") && !id.includes("gitea") && !id.includes("server") && !id.includes("push")) return false;
    const key = gitProjectActionKey(step, "wizard");
    return gitProjectActionStatusLabel(key) !== "completed";
  });
}
function gitProjectStepIsReadOnlyEvidence(step = {}, data = {}) {
  const id = gitProjectStepId(step);
  const kind = gitProjectStepKind(step);
  if (step.locked || step.destructive || step.safe === false) return false;
  if (id === "inspect_configured_remotes") return !gitProjectRemoteStepIsCurrentlyRequired(data);
  if (GIT_PROJECT_EVIDENCE_STEP_IDS.has(id)) return true;
  if (kind === "analysis") return true;
  return false;
}
function gitProjectStepIsUserAction(step = {}, data = {}) {
  if (gitProjectStepIsReadOnlyEvidence(step, data)) return false;
  const id = gitProjectStepId(step);
  const kind = gitProjectStepKind(step);
  if (GIT_PROJECT_ATTENTION_STEP_IDS.has(id)) return true;
  if (step.locked || step.destructive || step.safe === false) return true;
  if (GIT_PROJECT_USER_ACTION_KINDS.has(kind)) return true;
  return ["blocked", "ready", "running", "planned"].includes(step.state || "");
}
function gitProjectStepBlockedReason(step = {}, data = {}) {
  const project = data.project || {};
  const git = data.git || {};
  const id = `${step.id || ""} ${step.label || ""}`.toLowerCase();
  if (git.is_git_repo && git.has_head === false && id.includes("push")) {
    return "Waiting for prerequisite: Has HEAD.";
  }
  if (project.locked && gitProjectStepIsUserAction(step, data) && !gitProjectStepIsReadOnlyEvidence(step, data)) {
    return "Project is locked; unlock only when you intend to mutate state.";
  }
  if (Array.isArray(step.requires) && step.requires.length) {
    return `Waiting for prerequisite: ${step.requires.map(humanizeGitProjectToken).join(", ")}.`;
  }
  if (step.locked) return "Locked until the prerequisite safety step is complete.";
  if (step.destructive) return "Destructive action; save current state before running.";
  if (step.state === "blocked") return "Blocked by current repository state.";
  return "";
}
function classifyGitProjectWizardStep(step = {}, data = {}, actionKey = "") {
  const status = actionKey ? gitProjectActionStatusLabel(actionKey) : "idle";
  if (step.state === "completed" || step.completed) {
    return {
      lane: "satisfied",
      tone: "complete",
      reason: step.gitignore_success?.message || "Prerequisite already satisfied.",
      showRunner: false,
      status,
    };
  }
  if (status === "completed") {
    return {
      lane: "completed",
      tone: "complete",
      reason: "Already completed in this browser session.",
      showRunner: false,
      status,
    };
  }
  if (["queued", "running"].includes(status)) {
    return {
      lane: "ready_action",
      tone: "actionable",
      reason: "This action is already active.",
      showRunner: true,
      status,
    };
  }
  if (gitProjectStepIsReadOnlyEvidence(step, data)) {
    return {
      lane: "evidence",
      tone: "informative",
      reason: "Read-only evidence; it does not require the user to unblock the workflow.",
      showRunner: false,
      status,
    };
  }
  if (GIT_PROJECT_ATTENTION_STEP_IDS.has(gitProjectStepId(step))) {
    return {
      lane: "attention",
      tone: "blocking",
      reason: "Requires a user decision before the workflow can safely continue.",
      showRunner: false,
      status,
    };
  }
  const blockedReason = gitProjectStepBlockedReason(step, data);
  if (step.locked || step.destructive) {
    return {
      lane: "destructive_locked",
      tone: "blocking",
      reason: blockedReason || "Locked or destructive action.",
      showRunner: true,
      status,
    };
  }
  if (blockedReason || step.state === "blocked") {
    return {
      lane: "waiting_action",
      tone: "blocking",
      reason: blockedReason || "Waiting for a prerequisite.",
      showRunner: true,
      status,
    };
  }
  if (gitProjectStepIsUserAction(step, data)) {
    return {
      lane: "ready_action",
      tone: "actionable",
      reason: "Actionable: the user must make a decision or run this to move the process forward.",
      showRunner: true,
      status,
    };
  }
  return {
    lane: "evidence",
    tone: "informative",
    reason: "Context only.",
    showRunner: false,
    status,
  };
}
function toneForWizardStep(step = {}, data = {}) {
  return classifyGitProjectWizardStep(step, data).tone;
}
function gitProjectCardSelector(attr, value = "") {
  const escaped = (window.CSS && typeof window.CSS.escape === "function")
    ? window.CSS.escape(String(value || ""))
    : String(value || "").replace(/\\/g, "\\\\").replace(/"/g, "\\\"");
  return `[${attr}="${escaped}"]`;
}
function gitProjectStepIsCommitCard(step = {}) {
  return Boolean(step.commit_review);
}
function gitProjectStepIsArchiveCard(step = {}) {
  return gitProjectStepId(step) === "archive_files" || Boolean(step.archive_files);
}
function gitProjectArchiveCardTitle(step = {}) {
  if (!gitProjectStepIsArchiveCard(step)) return "";
  return String(step.archive_files?.title || step.label || "Archive Files...").trim();
}
function gitProjectCommitCardTitle(step = {}) {
  if (!gitProjectStepIsCommitCard(step)) return "";
  const review = step.commit_review || {};
  const opened = gitProjectCommitOpenedCard(review);
  return String(opened.title || review.title || "TAKE SNAPSHOT / COMMIT").trim();
}
function gitProjectVisibleStepLabel(step = {}) {
  const label = String(step.label || "Step").trim() || "Step";
  const commitTitle = gitProjectCommitCardTitle(step);
  if (!commitTitle || /commit|snapshot/i.test(label)) return label;
  return `${label} — ${commitTitle}`;
}
function gitProjectOpenCardButtonLabel(step = {}) {
  if (gitProjectStepId(step) === "secrets_filter") return "Open Security Review";
  if (gitProjectStepIsCommitCard(step)) return "Open commit pane";
  if (gitProjectStepIsArchiveCard(step)) return "Open archive pane";
  return "Open card";
}
function gitProjectCommitCardAttachmentHtml(step = {}) {
  const commitTitle = gitProjectCommitCardTitle(step);
  if (!commitTitle) return "";
  return `<div class="git-project-step-note git-project-commit-card-note"><strong>Commit workbench attached here</strong><span>${escapeHtml(commitTitle)}</span></div>`;
}
function gitProjectClosedCardPurpose(step = {}) {
  if (gitProjectStepIsArchiveCard(step)) {
    return "Move selected work out of this branch without losing it. Open the card to load git status and choose staged, unstaged, or untracked files.";
  }
  if (gitProjectStepIsCommitCard(step)) {
    return "Capture intentional work in a local commit. Open the card to review files, gates, identity, and the final commit message.";
  }
  const id = gitProjectStepId(step);
  if (id === "update_gitignore_before_initial_commit") {
    return "Review suggested ignore rules before taking a snapshot. Open the card to choose which rules to save.";
  }
  if (id === "secrets_filter") {
    return "Check selected files for API keys, usernames, credentials, tokens, private keys, generated artifacts, and risky content before committing.";
  }
  if (Array.isArray(step.paths) && step.paths.length) {
    return "Review the affected files for this action. Open the card to see the file list.";
  }
  return String(step.why || "Open the card to review this action.").trim() || "Open the card to review this action.";
}

function gitProjectClosedCardChips(step = {}) {
  const chips = [];
  if (gitProjectStepIsCommitCard(step)) {
    const review = step.commit_review || {};
    const groups = gitProjectCommitGroups(review);
    const candidateCount = gitProjectCommitReviewCandidatePaths(review).length;
    const selectedCount = groups.selected_by_default.filter((item = {}) => item.path).length;
    if (candidateCount) chips.push(`${candidateCount} candidate file${candidateCount === 1 ? "" : "s"}`);
    if (selectedCount) chips.push(`${selectedCount} preselected`);
    const ready = gitProjectCommitReadySummary(review);
    if (ready.branch) chips.push(`branch ${ready.branch}`);
    chips.push("local commit");
    return chips;
  }
  if (gitProjectStepIsArchiveCard(step)) {
    chips.push("runs git status when opened");
    chips.push("staged / unstaged / untracked");
    chips.push("archives before removal");
    return chips;
  }
  if (gitProjectStepId(step) === "secrets_filter") {
    const model = step.secrets_filter || {};
    const summary = model.summary || model.scan?.summary || {};
    const enabled = Number(summary.enabled_rule_count || 0);
    const findings = Number(summary.finding_count || summary.blocking || summary.critical || 0);
    chips.push("safety gate");
    chips.push("secrets scan");
    if (enabled) chips.push(`${enabled} rules`);
    if (findings) chips.push(`${findings} findings`);
    return chips;
  }
  if (Array.isArray(step.paths) && step.paths.length) {
    const count = step.paths.length;
    chips.push(`${count} path${count === 1 ? "" : "s"}`);
  }
  if (step.locked) chips.push("locked");
  if (step.state) chips.push(String(step.state));
  return chips;
}

function gitProjectClosedCardSummaryHtml(step = {}, stepComponentId = "", stepLabel = "") {
  const purpose = gitProjectClosedCardPurpose(step);
  const chips = gitProjectClosedCardChips(step);
  const reason = step.uiReason ? `<p class="git-project-mini-card-note">${escapeHtml(step.uiReason)}</p>` : "";
  return `<div class="git-project-mini-card-summary" ${gitProjectMcComponentAttrs(`${stepComponentId}.mini-summary`, "status", `${stepLabel} Summary`, stepComponentId)}>
    <p>${escapeHtml(purpose)}</p>
    ${chips.length ? `<div class="git-project-mini-card-chips">${chips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join("")}</div>` : ""}
    ${reason}
  </div>`;
}

function gitProjectStepSupportsCardSubscreen(step = {}) {
  const id = gitProjectStepId(step);
  if (id === "update_gitignore_before_initial_commit") return true;
  if (id === "secrets_filter") return true;
  if (gitProjectStepIsArchiveCard(step)) return true;
  if (gitProjectStepIsCommitCard(step)) return true;
  if (Array.isArray(step.paths) && step.paths.length) return true;
  if (step.gitignore_file && (Array.isArray(step.ignore_rules) || Array.isArray(step.questionable_ignore_rules))) return true;
  return false;
}
function gitProjectPathChips(paths = [], limit = 32) {
  const items = Array.isArray(paths) ? paths.filter(Boolean) : [];
  const shown = items.slice(0, limit);
  const more = items.length > shown.length ? `<span>+${items.length - shown.length} more</span>` : "";
  return `<div class="git-project-path-chip-list">
    ${shown.map((path) => `<code>${escapeHtml(path)}</code>`).join("")}
    ${more}
  </div>`;
}
function gitProjectIgnoreRuleRows(rules = [], tone = "safe", checked = true) {
  const values = Array.isArray(rules) ? rules.filter(Boolean) : [];
  if (!values.length) {
    return `<div class="git-project-gitignore-empty">No ${escapeHtml(tone)} rules suggested.</div>`;
  }
  return `<div class="git-project-ignore-rule-list">
    ${values.map((rule, index) => `<label class="git-project-ignore-rule ${tone === "questionable" ? "questionable" : "safe"}">
      <input type="checkbox" data-git-ignore-rule="${escapeHtml(rule)}" data-git-ignore-rule-tone="${escapeHtml(tone)}" ${checked ? "checked" : ""}>
      <code>${escapeHtml(rule)}</code>
    </label>`).join("")}
  </div>`;
}
function gitProjectNormalizeGitignoreLineText(value = "") {
  return String(value ?? "").replace(/[\r\n]+/g, "");
}
function gitProjectNormalizeGitignoreMatchText(value = "") {
  return gitProjectNormalizeGitignoreLineText(value).trim();
}
function gitProjectGitignoreBaselineLines(gitignoreFile = {}) {
  if (gitignoreFile.content_read !== true || !Array.isArray(gitignoreFile.lines)) return [];
  return gitignoreFile.lines.map((line = {}) => gitProjectNormalizeGitignoreLineText(line.text ?? ""));
}
function gitProjectGitignoreLineRowHtml(text = "", number = 0, state = "saved", checked = true) {
  const value = gitProjectNormalizeGitignoreLineText(text);
  const trimmed = value.trim();
  const rowClass = trimmed ? (trimmed.startsWith("#") ? "is-comment" : "is-rule") : "is-blank";
  const lineNumber = Number(number || 0);
  const displayNumber = lineNumber > 0 ? String(lineNumber) : "+";
  return `<label class="git-project-gitignore-line ${rowClass} ${state === "pending" ? "is-pending" : ""}" data-gitignore-line-row data-gitignore-line-state="${escapeHtml(state)}">
    <input type="checkbox" data-gitignore-existing-line="${lineNumber}" ${checked ? "checked" : ""}>
    <span class="git-project-gitignore-line-number">${escapeHtml(displayNumber)}</span>
    <code contenteditable="true" spellcheck="false">${escapeHtml(value)}</code>
  </label>`;
}
function gitProjectGitignoreLinesHtml(gitignoreFile = {}) {
  const lines = Array.isArray(gitignoreFile.lines) ? gitignoreFile.lines : [];
  const exists = gitignoreFile.exists === true;
  const contentRead = gitignoreFile.content_read === true;
  const fileSize = Number(gitignoreFile.size || 0);
  let message = "";
  let rows = "";
  if (!exists) {
    message = "No .gitignore file exists yet. Use the selected suggestions on the left to create the first reviewed ignore rules.";
  } else if (!contentRead) {
    const note = String(gitignoreFile.note || gitignoreFile.error || "");
    message = `.gitignore exists, but its contents were not loaded.${note ? ` ${note}` : ""}`;
  } else if (fileSize > 0 && !lines.length) {
    message = ".gitignore exists and has content, but no lines were returned.";
  } else if (!lines.length) {
    message = ".gitignore exists but appears to be empty.";
  } else {
    rows = lines.map((line = {}) => gitProjectGitignoreLineRowHtml(line.text ?? "", Number(line.number || 0), "saved", true)).join("");
  }
  return `<div class="git-project-gitignore-lines" data-gitignore-lines>
    ${rows}
    <div class="git-project-gitignore-empty" data-gitignore-empty-message ${rows ? "hidden" : ""}>${escapeHtml(message || "No .gitignore lines are currently selected.")}</div>
  </div>`;
}

function gitProjectGitignoreFileSummary(gitignoreFile = {}) {
  if (gitignoreFile.exists !== true) return "missing";
  const lines = Array.isArray(gitignoreFile.lines) ? gitignoreFile.lines : [];
  const contentRead = gitignoreFile.content_read === true;
  const fileSize = Number(gitignoreFile.size || 0);
  const newline = gitignoreFile.newline || "unknown";
  if (!contentRead) {
    return `${fileSize} bytes · contents not loaded`;
  }
  if (fileSize > 0 && !lines.length) {
    return `${fileSize} bytes · no lines returned`;
  }
  return `${Number(gitignoreFile.line_count || 0)} lines · ${newline} newline`;
}
function gitProjectIgnoreWorkbenchHtml(step = {}) {
  const gitignoreFile = step.gitignore_file || {};
  const safeRules = Array.isArray(step.ignore_rules) ? step.ignore_rules : (step.ignore_rule_groups?.safe || []);
  const questionableRules = Array.isArray(step.questionable_ignore_rules) ? step.questionable_ignore_rules : (step.ignore_rule_groups?.questionable || []);
  const affectedPaths = Array.isArray(step.affected_paths) ? step.affected_paths : (Array.isArray(step.paths) ? step.paths : []);
  const safePaths = Array.isArray(step.safe_paths) ? step.safe_paths : [];
  const questionablePaths = Array.isArray(step.questionable_paths) ? step.questionable_paths : [];
  const initialPreview = safeRules.filter(Boolean).join("\n");
  const success = step.gitignore_success || {};
  const baselineLines = gitProjectGitignoreBaselineLines(gitignoreFile);
  const newline = String(gitignoreFile.newline || "lf");
  const gitignorePath = String(gitignoreFile.path || ".gitignore");
  const existingGitignoreUnread = gitignoreFile.exists === true && gitignoreFile.content_read !== true;
  const successHtml = success.status
    ? `<div class="git-project-gitignore-success"><strong>${escapeHtml(success.label || ".gitignore cleanup satisfied")}</strong><span>${escapeHtml(success.message || "No .gitignore changes are needed right now.")}</span></div>`
    : "";
  return `<div class="git-project-gitignore-workbench" data-gitignore-baseline="${escapeHtml(JSON.stringify(baselineLines))}" data-gitignore-newline="${escapeHtml(newline)}" data-gitignore-path="${escapeHtml(gitignorePath)}" data-gitignore-existing-unread="${existingGitignoreUnread ? "true" : "false"}" data-gitignore-dirty="false">
    ${successHtml}
    <section class="git-project-gitignore-suggestions">
      <div class="git-project-subscreen-panel-head">
        <strong>Planner suggestions</strong>
        <span>${safeRules.length} safe · ${questionableRules.length} review</span>
      </div>
      <p class="git-project-muted">Safe rules are selected by default. Questionable rules are visible but require human review before adding.</p>
      <h4>Safe pile</h4>
      ${gitProjectIgnoreRuleRows(safeRules, "safe", true)}
      <h4>Questionable pile</h4>
      ${gitProjectIgnoreRuleRows(questionableRules, "questionable", false)}
      <h4>Affected paths</h4>
      ${gitProjectPathChips(affectedPaths)}
      ${safePaths.length ? `<h4>Safe affected paths</h4>${gitProjectPathChips(safePaths, 20)}` : ""}
      ${questionablePaths.length ? `<h4>Review-before-ignoring paths</h4>${gitProjectPathChips(questionablePaths, 20)}` : ""}
      <div class="git-project-ignore-preview-tools">
        <button type="button" data-git-ignore-select="safe">Select safe</button>
        <button type="button" data-git-ignore-select="all">Select all visible</button>
        <button type="button" data-git-ignore-select="none">Clear</button>
      </div>
      <pre class="git-project-ignore-selected-preview" data-git-ignore-selected-preview>${escapeHtml(initialPreview || "# Select rules above to preview .gitignore additions.")}</pre>
    </section>
    <section class="git-project-gitignore-file-panel">
      <div class="git-project-subscreen-panel-head">
        <strong>Actual .gitignore on disk</strong>
        <span>${escapeHtml(gitProjectGitignoreFileSummary(gitignoreFile))}</span>
      </div>
      <span class="git-project-file-location">${escapeHtml(gitignoreFile.absolute_path || gitignoreFile.path || ".gitignore")}</span>
      ${gitProjectGitignoreLinesHtml(gitignoreFile)}
      <div class="git-project-gitignore-save-panel">
        <span data-gitignore-dirty-message>No unsaved .gitignore changes</span>
        <button type="button" data-gitignore-save disabled>Save .gitignore</button>
      </div>
      <div class="git-project-gitignore-save-status" data-gitignore-save-status role="status" aria-live="polite"></div>
    </section>
  </div>`;
}
const GIT_PROJECT_WUNDERBAUM_VERSION = "0.14.1";
const GIT_PROJECT_WUNDERBAUM_ASSETS = {
  css: `https://cdn.jsdelivr.net/gh/mar10/wunderbaum@v${GIT_PROJECT_WUNDERBAUM_VERSION}/dist/wunderbaum.css`,
  icons: "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css",
  js: `https://cdn.jsdelivr.net/gh/mar10/wunderbaum@v${GIT_PROJECT_WUNDERBAUM_VERSION}/dist/wunderbaum.umd.min.js`,
};
let gitProjectWunderbaumLoadPromise = null;

function gitProjectCommitGroups(review = {}) {
  const groups = review.candidate_groups || {};
  return {
    selected_by_default: Array.isArray(groups.selected_by_default) ? groups.selected_by_default : [],
    review_before_selecting: Array.isArray(groups.review_before_selecting) ? groups.review_before_selecting : [],
    blocked_possible_secrets: Array.isArray(groups.blocked_possible_secrets) ? groups.blocked_possible_secrets : [],
    excluded_generated_runtime: Array.isArray(groups.excluded_generated_runtime) ? groups.excluded_generated_runtime : [],
  };
}

function gitProjectCommitGroupConfig() {
  return [
    {
      key: "selected_by_default",
      title: "Selected by default",
      subtitle: "Clean source/config/test files selected by the planner",
      selectable: true,
      expanded: true,
      reason: "selected by default",
      tone: "clean",
    },
    {
      key: "review_before_selecting",
      title: "Review before selecting",
      subtitle: "Candidate files that need human approval before staging",
      selectable: true,
      expanded: true,
      reason: "needs review",
      tone: "review",
    },
    {
      key: "blocked_possible_secrets",
      title: "Blocked",
      subtitle: "Files blocked by upstream gates or secret-looking labels",
      selectable: false,
      expanded: true,
      reason: "blocked by Secrets / Filter",
      tone: "blocked",
    },
    {
      key: "excluded_generated_runtime",
      title: "Excluded generated/runtime",
      subtitle: "Generated, cache, runtime, or build-output paths kept out of staging",
      selectable: false,
      expanded: false,
      reason: "excluded generated/runtime",
      tone: "excluded",
    },
  ];
}

function gitProjectCommitOpenedCard(review = {}) {
  return review.opened_commit_card || {};
}

function gitProjectCommitIdentity(review = {}) {
  return review.commit_identity || {};
}

function gitProjectCommitHead(review = {}) {
  return review.head || {};
}

function gitProjectCommitBranch(review = {}) {
  const opened = gitProjectCommitOpenedCard(review);
  const fields = opened.config_strip?.fields || {};
  const head = gitProjectCommitHead(review);
  return String(fields.branch?.value || review.branch?.current || head.branch || head.default_branch || "master");
}

function gitProjectCommitMessage(review = {}) {
  const opened = gitProjectCommitOpenedCard(review);
  const fields = opened.config_strip?.fields || {};
  return String(fields.commit_message?.value || review.commit_message || "Take project snapshot");
}

function gitProjectCommitIdentitySource(review = {}) {
  const opened = gitProjectCommitOpenedCard(review);
  const strip = opened.status_strip || {};
  const identity = gitProjectCommitIdentity(review);
  if (strip.identity) return String(strip.identity);
  const nameSource = identity.name_source || "missing";
  const emailSource = identity.email_source || "missing";
  if (nameSource === emailSource) return String(nameSource);
  if (nameSource === "missing" && emailSource === "missing") return "missing";
  return "mixed";
}

function gitProjectCommitIdentityScope(review = {}) {
  const opened = gitProjectCommitOpenedCard(review);
  const fields = opened.config_strip?.fields || {};
  return String(fields.identity_scope?.value || (gitProjectCommitIdentity(review).ready ? "use_existing" : "save_local"));
}

function gitProjectCommitFieldValue(review = {}, key = "", fallback = "") {
  const opened = gitProjectCommitOpenedCard(review);
  const fields = opened.config_strip?.fields || {};
  const identity = gitProjectCommitIdentity(review);
  const fallbacks = {
    branch: gitProjectCommitBranch(review),
    commit_message: gitProjectCommitMessage(review),
    git_user_name: identity.name || "",
    git_user_email: identity.email || "",
    identity_scope: gitProjectCommitIdentityScope(review),
  };
  const value = fields[key]?.value;
  return String(value ?? fallbacks[key] ?? fallback ?? "");
}

function gitProjectCommitGateSource(review = {}) {
  const rawGates = review.gates || gitProjectCommitOpenedCard(review).gates || {};
  const byId = {};
  if (Array.isArray(rawGates)) {
    rawGates.forEach((gate = {}) => {
      if (gate.id) byId[String(gate.id)] = gate;
    });
  } else if (rawGates && typeof rawGates === "object") {
    Object.entries(rawGates).forEach(([key, gate]) => {
      if (gate && typeof gate === "object") {
        byId[key] = {id: key, ...gate};
      }
    });
  }
  return byId;
}

function gitProjectCommitGateSummary(review = {}) {
  const gates = gitProjectCommitGateSource(review);
  const blockers = Array.isArray(review.commit_blockers) ? review.commit_blockers.map(String) : [];
  const lockedReason = String(review.locked_reason || "");
  const privacySummary = review.privacy_scan?.summary || {};
  const gitignoreGate = gates.gitignore || gates.gitignore_gate || {};
  const secretsGate = gates.secrets_filter || gates.secrets_filter_gate || gates.privacy_scan || {};
  const gitignoreBlocked = gitignoreGate.state === "blocked" || gitignoreGate.ready === false || blockers.includes("gitignore_review_required") || Boolean(lockedReason);
  const secretsBlocked = secretsGate.state === "blocked" || secretsGate.ready === false || blockers.some((item) => [
    "security_scan_required",
    "critical_privacy_findings",
    "blocking_security_findings",
    "path_risk_review_required",
  ].includes(item));
  const secretSummaryParts = [];
  if (Number(privacySummary.blocking || 0)) secretSummaryParts.push(`${Number(privacySummary.blocking || 0)} blocking finding${Number(privacySummary.blocking || 0) === 1 ? "" : "s"}`);
  if (Number(privacySummary.critical || 0)) secretSummaryParts.push(`${Number(privacySummary.critical || 0)} critical`);
  if (Number(privacySummary.review || privacySummary.review_files || 0)) secretSummaryParts.push(`${Number(privacySummary.review || privacySummary.review_files || 0)} review`);
  if (privacySummary.requires_user_scan) secretSummaryParts.push("scan required");
  return {
    gitignore: {
      key: "gitignore",
      label: gitignoreGate.label || ".gitignore",
      state: gitignoreBlocked ? "blocked" : (gitignoreGate.state || "passed"),
      summary: gitignoreGate.summary || gitignoreGate.reason || lockedReason || (gitignoreBlocked ? ".gitignore review is required before staging." : ".gitignore gate is passing."),
      step_id: gitignoreGate.step_id || "update_gitignore_before_initial_commit",
    },
    secrets_filter: {
      key: "secrets_filter",
      label: secretsGate.label || "Secrets / Filter",
      state: secretsBlocked ? "blocked" : (secretsGate.state || (privacySummary.requires_user_scan ? "needs review" : "passed")),
      summary: secretsGate.summary || secretsGate.reason || secretSummaryParts.join(" · ") || (secretsBlocked ? "Secrets / Filter needs review before commit." : "Secrets / Filter gate is passing."),
      step_id: secretsGate.step_id || "secrets_filter",
    },
  };
}

function gitProjectCommitReadySummary(review = {}) {
  const groups = gitProjectCommitGroups(review);
  const gates = gitProjectCommitGateSummary(review);
  const selectedCount = groups.selected_by_default.filter((item) => item && item.path).length;
  const identity = gitProjectCommitIdentity(review);
  const head = gitProjectCommitHead(review);
  const backendReady = Boolean(review.commit_ready);
  const gateBlocked = Object.values(gates).some((gate) => String(gate.state || "").toLowerCase() === "blocked");
  const reasons = [];
  if (gateBlocked) reasons.push("upstream gate blocked");
  if (!gitProjectCommitMessage(review).trim()) reasons.push("commit message missing");
  if (!gitProjectCommitBranch(review).trim()) reasons.push("branch missing");
  if (!identity.ready && (!gitProjectCommitFieldValue(review, "git_user_name").trim() || !gitProjectCommitFieldValue(review, "git_user_email").includes("@"))) reasons.push("identity incomplete");
  if (!selectedCount) reasons.push("no files selected");
  if (!backendReady) reasons.push("backend not ready");
  return {
    backendReady,
    selectedCount,
    headState: head.head_state || "unknown",
    branch: gitProjectCommitBranch(review),
    identitySource: gitProjectCommitIdentitySource(review),
    ready: backendReady && !gateBlocked && selectedCount > 0,
    reasons,
  };
}

function gitProjectCommitFieldHtml(review = {}, key = "", label = "", options = {}) {
  const value = gitProjectCommitFieldValue(review, key, options.fallback || "");
  const type = options.type || "text";
  const placeholder = options.placeholder || "";
  if (type === "radio") {
    const scope = gitProjectCommitIdentityScope(review);
    return `<div class="git-project-commit-field is-wide" data-git-commit-field="${escapeHtml(key)}">
      <span>${escapeHtml(label)}</span>
      <div class="git-project-commit-radio-row">
        <label><input type="radio" name="git-commit-identity-scope" value="use_existing" ${scope === "use_existing" ? "checked" : ""}> use existing global</label>
        <label><input type="radio" name="git-commit-identity-scope" value="save_local" ${scope === "save_local" ? "checked" : ""}> save local repo identity</label>
      </div>
    </div>`;
  }
  return `<label class="git-project-commit-field" data-git-commit-field="${escapeHtml(key)}">
    <span>${escapeHtml(label)}</span>
    <input type="${escapeHtml(type)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}">
  </label>`;
}

function gitProjectCommitHeaderHtml(review = {}) {
  const opened = gitProjectCommitOpenedCard(review);
  const head = gitProjectCommitHead(review);
  const ready = gitProjectCommitReadySummary(review);
  const strip = opened.status_strip || {};
  const title = opened.title || "TAKE SNAPSHOT / COMMIT";
  const subtitle = opened.subtitle || "Local commit only · No push · No remote setup";
  const headState = strip.head || head.head_state || "unknown";
  const branch = strip.branch || ready.branch;
  const identity = strip.identity || ready.identitySource;
  const commitReady = strip.commit_ready || (review.commit_ready ? "yes" : "no");
  return `<header class="git-project-commit-header">
    <div>
      <strong>${escapeHtml(title)}</strong>
      <span>${escapeHtml(subtitle)}</span>
    </div>
    <div class="git-project-commit-status-strip">
      <span>HEAD: ${escapeHtml(headState)}</span>
      <span>Branch: ${escapeHtml(branch)}</span>
      <span>Identity: ${escapeHtml(identity)}</span>
      <span>Commit ready: ${escapeHtml(commitReady)}</span>
    </div>
  </header>`;
}

function gitProjectCommitConfigStripHtml(review = {}) {
  return `<section class="git-project-commit-config-strip" aria-label="Commit configuration">
    <div class="git-project-commit-config-title">CONFIG STRIP</div>
    <div class="git-project-commit-config-grid">
      ${gitProjectCommitFieldHtml(review, "branch", "Branch", {placeholder: "master"})}
      ${gitProjectCommitFieldHtml(review, "commit_message", "Commit message", {placeholder: "Take project snapshot"})}
      ${gitProjectCommitFieldHtml(review, "git_user_name", "Name", {placeholder: "Your Name"})}
      ${gitProjectCommitFieldHtml(review, "git_user_email", "Email", {type: "email", placeholder: "you@example.com"})}
      ${gitProjectCommitFieldHtml(review, "identity_scope", "Scope", {type: "radio"})}
    </div>
  </section>`;
}

function gitProjectCommitGateStepCardHtml(gate = {}, key = "") {
  const state = String(gate.state || "unknown").toLowerCase();
  const tone = state.replace(/[^a-z0-9_-]+/g, "-") || "unknown";
  const label = key === "gitignore" ? ".gitignore gate" : key === "secrets_filter" ? "Secrets / Filter gate" : `${gate.label || "Gate"} gate`;
  return `<article class="git-project-commit-upstream-gate is-${escapeHtml(tone)}" data-git-commit-upstream-gate="${escapeHtml(key || gate.key || "gate")}">
    <strong>${escapeHtml(label)}</strong>
    <span>${escapeHtml(state || "unknown")}</span>
    <small>${escapeHtml(gate.summary || "")}</small>
  </article>`;
}

function gitProjectCommitStepsHtml(review = {}) {
  const ready = gitProjectCommitReadySummary(review);
  const gates = gitProjectCommitGateSummary(review);
  const groups = gitProjectCommitGroups(review);
  const identityReady = Boolean(gitProjectCommitIdentity(review).ready) || (
    gitProjectCommitFieldValue(review, "git_user_name").trim() && gitProjectCommitFieldValue(review, "git_user_email").includes("@")
  );
  const steps = [
    {id: "repo_branch", label: "Repo / Branch", mark: ready.headState === "unknown" ? "!" : "✓", detail: `HEAD ${ready.headState} · ${ready.branch}`},
    {id: "identity", label: "Identity", mark: identityReady ? "✓" : "!", detail: `Source: ${ready.identitySource}`},
    {id: "file_basket", label: "File basket", mark: groups.selected_by_default.length ? "✓" : "!", detail: `${groups.selected_by_default.length} selected · ${groups.review_before_selecting.length} review`},
    {id: "stage_preview", label: "Review selected files", mark: "!", detail: "Confirm before staging"},
    {id: "create_commit", label: "Create commit", mark: ready.ready ? "✓" : "🔒", detail: ready.reasons.length ? ready.reasons.join(" · ") : "Commit only when ready"},
  ];
  return `<section class="git-project-commit-left">
    <div class="git-project-subscreen-panel-head">
      <strong>Commit steps</strong>
      <span>final local workbench</span>
    </div>
    <div class="git-project-commit-upstream-gates">
      <strong>Upstream gates</strong>
      ${gitProjectCommitGateStepCardHtml(gates.gitignore, "gitignore")}
      ${gitProjectCommitGateStepCardHtml(gates.secrets_filter, "secrets_filter")}
      <div class="git-project-commit-step-break" aria-hidden="true"></div>
      <span>Commit workflow</span>
    </div>
    <ol class="git-project-commit-steps">
      ${steps.map((step) => `<li data-git-commit-step="${escapeHtml(step.id)}" class="${step.mark === "✓" ? "is-ready" : step.mark === "🔒" ? "is-locked" : "needs-review"}">
        <button type="button" data-git-commit-step-button="${escapeHtml(step.id)}">
          <span class="git-project-commit-step-mark" data-git-commit-step-mark="${escapeHtml(step.id)}">${escapeHtml(step.mark)}</span>
          <span><strong>${escapeHtml(step.label)}</strong><small data-git-commit-step-detail="${escapeHtml(step.id)}">${escapeHtml(step.detail)}</small></span>
        </button>
      </li>`).join("")}
    </ol>
  </section>`;
}

function gitProjectCommitGateSummaryHtml(review = {}) {
  const gates = gitProjectCommitGateSummary(review);
  return `<section class="git-project-commit-panel git-project-commit-gate-summary" data-git-commit-panel="gate_summary">
    <div class="git-project-subscreen-panel-head">
      <strong>Gate summary</strong>
      <span>upstream cards only</span>
    </div>
    <p class="git-project-muted">This commit card summarizes upstream gates. Rule editing and ignore-rule changes stay in their own cards.</p>
    <div class="git-project-commit-gate-grid">
      ${Object.values(gates).map((gate) => `<article class="git-project-commit-gate is-${escapeHtml(gate.state || "unknown")}">
        <strong>${escapeHtml(gate.label)}</strong>
        <span>${escapeHtml(gate.state || "unknown")}</span>
        <p>${escapeHtml(gate.summary || "")}</p>
      </article>`).join("")}
    </div>
  </section>`;
}

function gitProjectCommitSecuritySecretsPaneHtml(review = {}) {
  const gates = gitProjectCommitGateSummary(review);
  const secretsGate = gates.secrets_filter || {};
  const gitignoreGate = gates.gitignore || {};
  const groups = gitProjectCommitGroups(review);
  const privacySummary = review.privacy_scan?.summary || {};
  const blockedFiles = groups.blocked_possible_secrets.filter((item = {}) => item.path);
  const reviewFiles = groups.review_before_selecting.filter((item = {}) => item.path);
  const status = String(secretsGate.state || "unknown").toLowerCase();
  const statusTone = status.replace(/[^a-z0-9_-]+/g, "-") || "unknown";
  const countParts = [
    `${blockedFiles.length} blocked file${blockedFiles.length === 1 ? "" : "s"}`,
    `${reviewFiles.length} review file${reviewFiles.length === 1 ? "" : "s"}`,
  ];
  if (Number(privacySummary.blocking || 0)) countParts.push(`${Number(privacySummary.blocking || 0)} blocking finding${Number(privacySummary.blocking || 0) === 1 ? "" : "s"}`);
  if (Number(privacySummary.critical || 0)) countParts.push(`${Number(privacySummary.critical || 0)} critical finding${Number(privacySummary.critical || 0) === 1 ? "" : "s"}`);
  const blockedPreview = blockedFiles.slice(0, 6);
  const extraBlocked = blockedFiles.length - blockedPreview.length;
  return `<section class="git-project-commit-panel git-project-commit-security-secrets is-${escapeHtml(statusTone)}" data-git-commit-panel="security_secrets">
    <div class="git-project-subscreen-panel-head">
      <strong>Security / Secrets review</strong>
      <span>${escapeHtml(status || "unknown")}</span>
    </div>
    <p class="git-project-muted">This is a commit readiness summary. Open the Security / Secrets card to run or review the full scan before committing.</p>
    <div class="git-project-commit-security-grid">
      <article class="git-project-commit-security-tile is-${escapeHtml(statusTone)}">
        <strong>${escapeHtml(secretsGate.label || "Secrets / Filter")}</strong>
        <span>${escapeHtml(secretsGate.state || "unknown")}</span>
        <p>${escapeHtml(secretsGate.summary || "No secrets summary was returned by the planner.")}</p>
      </article>
      <article class="git-project-commit-security-tile is-${escapeHtml(String(gitignoreGate.state || "unknown").toLowerCase().replace(/[^a-z0-9_-]+/g, "-") || "unknown")}">
        <strong>${escapeHtml(gitignoreGate.label || ".gitignore")}</strong>
        <span>${escapeHtml(gitignoreGate.state || "unknown")}</span>
        <p>${escapeHtml(gitignoreGate.summary || ".gitignore gate summary was not returned by the planner.")}</p>
      </article>
    </div>
    <div class="git-project-commit-security-counts">
      ${countParts.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
    </div>
    ${blockedPreview.length ? `<div class="git-project-commit-security-blocked">
      <strong>Blocked files stay out of the commit basket</strong>
      <ul>
        ${blockedPreview.map((item = {}) => `<li><code>${escapeHtml(item.path || "")}</code><span>${escapeHtml(item.reason || item.risk || "requires review")}</span></li>`).join("")}
        ${extraBlocked > 0 ? `<li><code>+${Number(extraBlocked)} more</code><span>Open the dedicated Secrets / Filter card for full scanner details.</span></li>` : ""}
      </ul>
    </div>` : ""}
  </section>`;
}

function gitProjectCommitRepoIdentityHtml(review = {}) {
  const head = gitProjectCommitHead(review);
  const identity = gitProjectCommitIdentity(review);
  const branch = gitProjectCommitBranch(review);
  return `<section class="git-project-commit-panel" data-git-commit-panel="repo_identity">
    <div class="git-project-subscreen-panel-head">
      <strong>Repo / Identity</strong>
      <span>local UI draft</span>
    </div>
    <div class="git-project-commit-summary-grid">
      <span>HEAD state</span><code>${escapeHtml(head.head_state || "unknown")}</code>
      <span>Branch</span><code data-git-commit-summary-value="branch">${escapeHtml(branch)}</code>
      <span>First commit</span><code>${head.needs_first_commit ? "yes — this will create HEAD" : "no"}</code>
      <span>Name</span><code data-git-commit-summary-value="git_user_name">${escapeHtml(gitProjectCommitFieldValue(review, "git_user_name") || "(missing)")}</code>
      <span>Email</span><code data-git-commit-summary-value="git_user_email">${escapeHtml(gitProjectCommitFieldValue(review, "git_user_email") || "(missing)")}</code>
      <span>Identity source</span><code data-git-commit-summary-value="identity_source">${escapeHtml(gitProjectCommitIdentitySource(review))}</code>
    </div>
    ${Array.isArray(identity.problems) && identity.problems.length ? `<div class="git-project-commit-validation">${identity.problems.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}
    <div class="git-project-commit-panel-actions">
      <button type="button" data-git-commit-action="use_current_branch">Use current branch</button>
      <button type="button" data-git-commit-action="preview_branch_command">Preview branch command</button>
      <button type="button" data-git-commit-action="preview_identity_commands">Preview identity commands</button>
    </div>
  </section>`;
}

function gitProjectCommitComposeHtml(review = {}) {
  return `<section class="git-project-commit-panel git-project-commit-compose" data-git-commit-panel="commit_message">
    ${gitProjectCommitFieldHtml(review, "commit_message", "Commit message", {placeholder: "Take project snapshot"})}
  </section>`;
}

function gitProjectCommitBasketControlsHtml(review = {}) {
  const groups = gitProjectCommitGroups(review);
  return `<section class="git-project-commit-panel" data-git-commit-panel="file_basket">
    <div class="git-project-subscreen-panel-head">
      <strong>File basket controls</strong>
      <span>${groups.selected_by_default.length} default · ${groups.review_before_selecting.length} review · ${groups.blocked_possible_secrets.length} blocked</span>
    </div>
    <p class="git-project-muted">Use the tree on the right to select files or whole directories. Directory selections expand to explicit file paths for staging.</p>
    <div class="git-project-commit-panel-actions">
      <button type="button" data-git-commit-basket-action="select_clean_files">Select clean files</button>
      <button type="button" data-git-commit-basket-action="clear_selection">Clear selection</button>
      <button type="button" data-git-commit-basket-action="show_review_files">Show review files</button>
      <button type="button" data-git-commit-basket-action="hide_generated_runtime_files">Hide generated/runtime files</button>
    </div>
    <div class="git-project-commit-selected-preview">
      <strong>Selected files preview</strong>
      <pre data-git-commit-selected-preview># Selected files will appear here.</pre>
    </div>
  </section>`;
}

function gitProjectCommitShellQuote(value = "") {
  const text = String(value);
  if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(text)) return text;
  return `"${text.replace(/(["\\$`])/g, "\\$1")}"`;
}

function gitProjectCommitJoinCommand(parts = []) {
  return parts.map(gitProjectCommitShellQuote).join(" ");
}

function gitProjectCommitStageCommands(step = {}, review = {}) {
  const commands = Array.isArray(step.commands) ? step.commands : [];
  const rendered = commands.map(formatCommandForReport).filter(Boolean);
  const defaults = [
    "git add -- <selected files>",
    "git status --short",
    "git diff --cached --stat",
    "git diff --cached --check",
  ];
  return rendered.length ? rendered : defaults;
}

function gitProjectCommitStageReviewStatusHtml() {
  return `<div class="git-project-commit-review-status is-blocked" data-git-commit-review-status>
    <strong data-git-commit-review-title>Choose files first</strong>
    <span data-git-commit-review-body>Select clean files from the File Basket. The selected paths will appear in the Selected Files Preview above.</span>
    <small data-git-commit-review-scope>Commit blocking is scoped to the selected files. Unselected repo warnings stay visible as context.</small>
  </div>`;
}

function gitProjectCommitStageReviewFlowHtml() {
  const steps = [
    ["1. Choose", "Select clean files from the basket. Review and blocked files stay out until resolved."],
    ["2. Review", "Use Selected Files Preview above as the single source of truth for commit contents."],
    ["3. Stage", "Backend stages selected paths only, then checks cached Git state."],
    ["4. Commit", "Create the local commit after gates, identity, and staged-state checks pass."],
  ];
  return `<div class="git-project-commit-review-flow">
    ${steps.map(([title, detail]) => `<article>
      <strong>${escapeHtml(title)}</strong>
      <span>${escapeHtml(detail)}</span>
    </article>`).join("")}
  </div>`;
}

function gitProjectCommitStageStatsHtml(review = {}) {
  const groups = gitProjectCommitGroups(review);
  return `<div class="git-project-commit-review-stats">
    <div>
      <strong data-git-commit-review-count="selected">0</strong>
      <span>selected</span>
    </div>
    <div>
      <strong data-git-commit-review-count="review">${groups.review_before_selecting.length}</strong>
      <span>need review</span>
    </div>
    <div>
      <strong data-git-commit-review-count="blocked">${groups.blocked_possible_secrets.length}</strong>
      <span>blocked</span>
    </div>
  </div>`;
}

function gitProjectCommitStagePreviewHtml(step = {}) {
  const review = step.commit_review || {};
  const commands = gitProjectCommitStageCommands(step, review);
  return `<section class="git-project-commit-panel git-project-commit-stage-preview" data-git-commit-panel="stage_preview">
    <div class="git-project-subscreen-panel-head">
      <strong>Review selected files</strong>
      <span>pre-stage check</span>
    </div>
    ${gitProjectCommitStageReviewStatusHtml()}
    ${gitProjectCommitStageReviewFlowHtml()}
    ${gitProjectCommitStageStatsHtml(review)}
    <div class="git-project-commit-checklist">
      <strong>Required confirmations</strong>
      <label><input type="checkbox" data-git-commit-stage-check="reviewed_staged_diff"> <span>I reviewed the Selected Files Preview and it matches the intended commit.</span></label>
      <label><input type="checkbox" data-git-commit-stage-check="upstream_gates_accepted"> <span>I understand the remaining warnings and still want to proceed with this selected-file commit.</span></label>
    </div>
    <details class="git-project-commit-dev-diagnostics" data-git-commit-dev-diagnostics>
      <summary>Developer diagnostics</summary>
      <pre data-git-commit-dev-preview>${escapeHtml(commands.join("\n"))}</pre>
    </details>
  </section>`;
}

function gitProjectCommitExecutionPaneHtml(message = "") {
  const renderedMessage = message || DEFAULT_COMMIT_MESSAGE;
  return `<div class="git-project-commit-execution-pane git-project-commit-execution-overlay" data-git-commit-execution-pane hidden>
    <section class="git-project-commit-execution-dialog" role="dialog" aria-modal="true" aria-labelledby="git-project-commit-execution-title">
      <header class="git-project-commit-execution-header">
        <div>
          <strong id="git-project-commit-execution-title">Commit execution preview</strong>
          <span data-git-commit-execution-state>dry run armed</span>
          <p>This nested sub-modal relists the exact selected files before any backend commit action. Dry Run is selected by default; uncheck it only when you want the backend to create real Git commits.</p>
        </div>
        <div class="git-project-commit-execution-header-actions">
          <button type="button" data-git-commit-action="stop_commit" disabled>Stop commit in progress</button>
          <button type="button" data-git-commit-action="close_commit_execution">Close</button>
        </div>
      </header>
      <div class="git-project-commit-execution-options">
        <label><input type="checkbox" data-git-commit-execution-option="dry_run" checked> <span><strong>Dry Run</strong><small>Selected automatically before Do Git Commit.</small></span></label>
        <label><input type="checkbox" data-git-commit-execution-option="one_at_a_time"> <span><strong>One at a time</strong><small>Create one commit per selected file, one Git call at a time.</small></span></label>
      </div>
      <main class="git-project-commit-execution-main">
        <section class="git-project-commit-execution-detail-card">
          <strong>Commit message</strong>
          <pre data-git-commit-execution-message>${escapeHtml(renderedMessage)}</pre>
          <strong>Target</strong>
          <pre data-git-commit-execution-target># Branch and identity will be verified here.</pre>
        </section>
        <section class="git-project-commit-execution-files">
          <div class="git-project-commit-execution-card-head">
            <strong>Files to stage</strong>
            <span data-git-commit-execution-file-count>0 selected files</span>
          </div>
          <ol data-git-commit-execution-files>
            <li>Select files and open the preview to relist them here.</li>
          </ol>
        </section>
        <section class="git-project-commit-execution-output-card">
          <strong>Execution output</strong>
          <div class="git-project-commit-execution-status" data-git-commit-execution-status>Waiting for commit preview.</div>
          <ol class="git-project-commit-execution-results" data-git-commit-execution-results></ol>
        </section>
      </main>
      <footer class="git-project-commit-execution-footer">
        <button type="button" class="git-project-commit-do-button" data-git-commit-action="do_git_commit">Do Git Commit</button>
      </footer>
    </section>
  </div>`;
}

function gitProjectCommitCreateHtml(step = {}) {
  const review = step.commit_review || {};
  const ready = gitProjectCommitReadySummary(review);
  const message = gitProjectCommitMessage(review);
  return `<section class="git-project-commit-panel git-project-commit-create" data-git-commit-panel="create_commit">
    <div class="git-project-subscreen-panel-head">
      <strong>Create commit</strong>
      <span>${ready.ready ? "ready" : "blocked"}</span>
    </div>
    <details class="git-project-commit-dev-diagnostics">
      <summary>Commit command</summary>
      <pre>${escapeHtml(gitProjectCommitJoinCommand(["git", "commit", "-m", message]))}</pre>
    </details>
    <div class="git-project-commit-readiness ${ready.ready ? "is-ready" : "is-blocked"}">
      <strong>Final readiness summary</strong>
      <span data-git-commit-final-readiness>${escapeHtml(ready.ready ? "All current checks are ready for a local commit." : ready.reasons.join(" · ") || "Commit is blocked until validation passes.")}</span>
    </div>
    <button type="button" class="git-project-commit-create-button" data-git-commit-action="create_local_commit" ${ready.ready ? "" : "disabled"}>Create local commit</button>
    ${gitProjectCommitExecutionPaneHtml(message)}
  </section>`;
}

function gitProjectCommitCenterHtml(step = {}, selectedPanel = "gate_summary") {
  const review = step.commit_review || {};
  return `<section class="git-project-commit-center">
    ${gitProjectCommitSecuritySecretsPaneHtml(review)}
    ${gitProjectCommitComposeHtml(review)}
    ${gitProjectCommitBasketControlsHtml(review)}
    ${gitProjectCommitStagePreviewHtml(step)}
    ${gitProjectCommitCreateHtml(step)}
  </section>`;
}

function gitProjectCommitNormalizeStatus(item = {}) {
  const raw = String(item.status || item.state || "").toLowerCase();
  if (raw.includes("untracked") || raw === "??" || item.untracked) return "untracked";
  if (raw.includes("renamed") || item.renamed) return "tracked_renamed";
  if (raw.includes("deleted") || item.deleted) return "tracked_deleted";
  if (raw.includes("conflict") || item.conflicted) return "conflicted";
  if (raw.includes("modified") || raw.includes("changed") || raw.includes("tracked") || raw.includes("staged") || item.staged || item.unstaged) {
    return "tracked_changed";
  }
  return raw || "unknown";
}

function gitProjectCommitStatusDisplay(status = "") {
  const normalized = String(status || "unknown").toLowerCase();
  if (normalized === "untracked") {
    return {symbol: "+", label: "untracked", tone: "untracked"};
  }
  if (normalized === "tracked_deleted") {
    return {symbol: "✓", label: "tracked deleted", tone: "tracked"};
  }
  if (normalized === "tracked_renamed") {
    return {symbol: "✓", label: "tracked renamed", tone: "tracked"};
  }
  if (normalized === "tracked_changed") {
    return {symbol: "✓", label: "tracked changed", tone: "tracked"};
  }
  if (normalized === "conflicted") {
    return {symbol: "!", label: "conflicted", tone: "blocked"};
  }
  return {symbol: "·", label: normalized, tone: "unknown"};
}

function gitProjectCommitTreeStats(nodes = []) {
  const stats = {total: 0, untracked: 0, changed: 0, blocked: 0};
  const visit = (node = {}) => {
    const data = node.data || {};
    if (data.kind === "file") {
      stats.total += 1;
      const status = gitProjectCommitNormalizeStatus(data);
      if (status === "untracked") stats.untracked += 1;
      if (status.startsWith("tracked_")) stats.changed += 1;
      if (data.blocked || data.selectable === false || data.group === "blocked_possible_secrets" || String(data.risk || "").toLowerCase().includes("block") || status === "conflicted") {
        stats.blocked += 1;
      }
    }
    (Array.isArray(node.children) ? node.children : []).forEach(visit);
  };
  nodes.forEach(visit);
  return stats;
}

function gitProjectCommitFileMeta(item = {}, group = {}) {
  const labels = Array.isArray(item.classifications) ? item.classifications.filter(Boolean) : [];
  const status = gitProjectCommitNormalizeStatus(item);
  const statusDisplay = gitProjectCommitStatusDisplay(status);
  const risk = item.risk || item.privacy_risk || group.tone || "review";
  const findings = Number(item.blocking_security_findings_count || item.privacy_findings_count || 0);
  const detailParts = [
    statusDisplay.label,
    labels.join(" · "),
    risk,
    findings ? `${findings} finding${findings === 1 ? "" : "s"}` : "",
    item.modified ? `edited ${item.modified}` : "",
  ].filter(Boolean);
  return {
    labels,
    status,
    statusDisplay,
    risk,
    findings,
    reason: item.reason || group.reason || "",
    modified: item.modified || "",
    meta: detailParts.join(" · "),
  };
}

function gitProjectCommitCreateTreeNode(title, key, options = {}) {
  const children = Array.isArray(options.children) ? options.children : [];
  const isContainer = Boolean(options.folder || options.type === "dir");
  const node = {
    title,
    key,
    type: options.type || (isContainer ? "dir" : "file"),
    selected: Boolean(options.selected),
    unselectable: Boolean(options.unselectable),
    checkbox: options.checkbox !== false,
    classes: options.classes || options.extraClasses || "",
    data: options.data || {},
  };
  if (options.expanded === true) {
    node.expanded = true;
  }
  if (isContainer || children.length) {
    node.children = children;
  }
  return node;
}

function gitProjectCommitCandidateItems(review = {}) {
  const groups = gitProjectCommitGroups(review);
  const configs = gitProjectCommitGroupConfig();
  const precedence = {
    selected_by_default: 10,
    review_before_selecting: 20,
    excluded_generated_runtime: 30,
    blocked_possible_secrets: 40,
  };
  const byPath = new Map();
  configs.forEach((group) => {
    (groups[group.key] || []).forEach((item = {}) => {
      const path = String(item.path || "").replace(/\\/g, "/").replace(/^\/+/, "");
      if (!path) return;
      const previous = byPath.get(path);
      const rank = precedence[group.key] || 0;
      if (!previous || rank >= previous.rank) {
        byPath.set(path, {item: {...item, path}, group, rank});
      }
    });
  });
  return Array.from(byPath.values()).map(({item, group}) => ({item, group}));
}

function gitProjectCommitFileBasketAdapter() {
  return globalThis.McelFileBasketModel || null;
}

function gitProjectCommitFileBasketControllerAdapter() {
  return globalThis.McelFileBasketController || null;
}

function gitProjectCommitFileBasketModel(review = {}) {
  const adapter = gitProjectCommitFileBasketAdapter();
  if (!adapter?.buildFileBasketModel) return null;
  try {
    return adapter.buildFileBasketModel(review, {
      surfaceId: "task-manager.file-basket",
      sourceConcern: "concern.file-basket",
      sourceFile: "main_computer/web/applications/scripts/task-manager.js"
    });
  } catch (error) {
    console.warn("Could not build MCEL file basket model.", error);
    return null;
  }
}

function gitProjectCommitFileBasketModelJson(model = null) {
  if (!model) return "";
  try {
    return JSON.stringify(model);
  } catch (error) {
    console.warn("Could not serialize MCEL file basket model.", error);
    return "";
  }
}

function gitProjectCommitTreeFileTitleFromModel(row = {}) {
  const titleParts = [
    `${row.statusSymbol || "·"} ${row.name || row.path || "file"}`,
    row.statusLabel || row.status || "",
    row.bucketLabel || row.bucket || "",
    String(row.meta || "").replace(String(row.statusLabel || ""), "").replace(/^\s*·\s*/, ""),
    row.reason && row.reason !== row.blockedReason ? row.reason : "",
  ].filter(Boolean);
  return titleParts.join(" · ");
}

function gitProjectCommitTreeNodeFromModelNode(modelNode = {}) {
  const kind = modelNode.kind || "file";
  if (kind === "file") {
    const selectable = modelNode.selectable !== false;
    const statusTone = modelNode.statusTone || "unknown";
    const bucketTone = modelNode.bucketTone || "review";
    return gitProjectCommitCreateTreeNode(gitProjectCommitTreeFileTitleFromModel(modelNode), `file:${modelNode.path}`, {
      type: "file",
      selected: Boolean(modelNode.selectedByDefault && selectable),
      unselectable: !selectable,
      checkbox: selectable,
      classes: [
        "git-project-commit-tree-file",
        `git-project-commit-tree-file-${statusTone}`,
        `git-project-commit-node-${bucketTone}`,
      ].join(" "),
      data: {
        kind: "file",
        path: modelNode.path || "",
        name: modelNode.name || "",
        group: modelNode.bucket || "",
        groupTitle: modelNode.bucketLabel || modelNode.bucket || "",
        bucket: modelNode.bucket || "",
        bucketLabel: modelNode.bucketLabel || modelNode.bucket || "",
        selectable,
        selectedByDefault: Boolean(modelNode.selectedByDefault && selectable),
        blocked: Boolean(modelNode.blocked || !selectable),
        blockedReason: modelNode.blockedReason || "",
        status: modelNode.status || "unknown",
        statusLabel: modelNode.statusLabel || modelNode.status || "unknown",
        statusSymbol: modelNode.statusSymbol || "·",
        statusTone,
        risk: modelNode.risk || "",
        classifications: Array.isArray(modelNode.classifications) ? modelNode.classifications.slice() : [],
        reason: modelNode.reason || "",
        modified: modelNode.modified || "",
        meta: modelNode.meta || "",
        findings: Number(modelNode.findings || 0),
        modelRowId: modelNode.id || "",
      },
    });
  }

  const children = (Array.isArray(modelNode.children) ? modelNode.children : [])
    .map(gitProjectCommitTreeNodeFromModelNode)
    .filter(Boolean);
  const selectable = modelNode.selectable !== false && Number(modelNode.selectableFileCount || 0) > 0;
  return gitProjectCommitCreateTreeNode(modelNode.name || modelNode.path || "Candidate files", `dir:${modelNode.path || ""}/`, {
    type: "dir",
    expanded: false,
    selected: modelNode.selectionState === "all",
    checkbox: selectable,
    unselectable: !selectable,
    classes: "git-project-commit-tree-dir git-project-commit-node-dir",
    children,
    data: {
      kind: "dir",
      name: modelNode.name || "",
      path: modelNode.path || "",
      selectable,
      selectionState: modelNode.selectionState || "none",
      totalFiles: Number(modelNode.fileCount || 0),
      blockedFiles: Number(modelNode.blockedFileCount || 0),
      selectableFiles: Number(modelNode.selectableFileCount || 0),
      modelRowId: modelNode.id || "",
    },
  });
}

function gitProjectCommitTreeSourceFromModel(model = null) {
  const hierarchy = Array.isArray(model?.hierarchy) ? model.hierarchy : [];
  if (!hierarchy.length) return null;
  const root = gitProjectCommitCreateTreeNode("Candidate files", "candidate-files", {
    type: "dir",
    folder: true,
    expanded: true,
    checkbox: true,
    data: {kind: "dir", path: "", selectable: true},
    children: hierarchy.map(gitProjectCommitTreeNodeFromModelNode).filter(Boolean),
  });
  gitProjectCommitAnnotateDirectoryStats(root);
  gitProjectCommitFinalizeDirectorySelection(root);
  return root.children.length ? root.children : null;
}

function gitProjectCommitSortTreeNodes(nodes = []) {
  nodes.sort((a, b) => {
    const aDir = a.data?.kind === "dir";
    const bDir = b.data?.kind === "dir";
    if (aDir !== bDir) return aDir ? -1 : 1;
    return String(a.title || "").localeCompare(String(b.title || ""), undefined, {sensitivity: "base"});
  });
  nodes.forEach((node) => {
    if (Array.isArray(node.children)) gitProjectCommitSortTreeNodes(node.children);
  });
  return nodes;
}

function gitProjectCommitAnnotateDirectoryStats(node) {
  const children = Array.isArray(node.children) ? node.children : [];
  let total = 0;
  let untracked = 0;
  let changed = 0;
  let blocked = 0;
  children.forEach((child) => {
    const data = child.data || {};
    if (data.kind === "file") {
      total += 1;
      const status = gitProjectCommitNormalizeStatus(data);
      if (status === "untracked") untracked += 1;
      if (status.startsWith("tracked_")) changed += 1;
      if (data.blocked || data.selectable === false || data.group === "blocked_possible_secrets" || String(data.risk || "").toLowerCase().includes("block") || status === "conflicted") {
        blocked += 1;
      }
    } else if (data.kind === "dir") {
      const childStats = gitProjectCommitAnnotateDirectoryStats(child);
      total += childStats.total;
      untracked += childStats.untracked;
      changed += childStats.changed;
      blocked += childStats.blocked;
    }
  });
  node.data = {...(node.data || {}), totalFiles: total, untrackedFiles: untracked, changedFiles: changed, blockedFiles: blocked};
  if (node.data.kind === "dir" && node.data.path) {
    const name = String(node.data.name || node.title || "");
    const countLabel = `${total} file${total === 1 ? "" : "s"}`;
    const statusParts = [
      untracked ? `+ ${untracked}` : "",
      changed ? `✓ ${changed}` : "",
      blocked ? `! ${blocked}` : "",
    ].filter(Boolean);
    node.title = [name, "dir", countLabel, ...statusParts].filter(Boolean).join(" · ");
  }
  return {total, untracked, changed, blocked};
}

function gitProjectCommitFinalizeDirectorySelection(node) {
  const children = Array.isArray(node.children) ? node.children : [];
  if (!children.length) return Boolean(node.data?.selectable);
  const selectableChildren = children
    .map(gitProjectCommitFinalizeDirectorySelection)
    .filter(Boolean);
  const selectable = selectableChildren.length > 0;
  node.data = {...(node.data || {}), selectable};
  node.checkbox = selectable;
  node.unselectable = !selectable;
  return selectable;
}

function gitProjectCommitInsertTreePath(root, item = {}, group = {}) {
  const path = String(item.path || "").replace(/\\/g, "/").replace(/^\/+/, "");
  if (!path) return;
  const parts = path.split("/").filter(Boolean);
  let cursor = root;
  let cursorPath = "";
  parts.forEach((part, index) => {
    cursorPath = cursorPath ? `${cursorPath}/${part}` : part;
    const isFile = index === parts.length - 1;
    let child = cursor.children.find((node) => node.data?.path === cursorPath && node.data?.kind === (isFile ? "file" : "dir"));
    if (!child) {
      if (isFile) {
        const meta = gitProjectCommitFileMeta(item, group);
        const selectable = group.selectable !== false;
        const selected = Boolean((group.key === "selected_by_default" || item.selected_by_default) && selectable);
        const groupLabel = group.title || group.key || "Candidate";
        const statusDisplay = meta.statusDisplay || gitProjectCommitStatusDisplay(meta.status);
        const titleParts = [
          `${statusDisplay.symbol} ${part}`,
          statusDisplay.label,
          groupLabel,
          meta.meta.replace(statusDisplay.label, "").replace(/^\s*·\s*/, ""),
          meta.reason && meta.reason !== group.reason ? meta.reason : "",
        ].filter(Boolean);
        child = gitProjectCommitCreateTreeNode(titleParts.join(" · "), `file:${cursorPath}`, {
          type: "file",
          selected,
          unselectable: !selectable,
          checkbox: selectable,
          classes: [
            "git-project-commit-tree-file",
            `git-project-commit-tree-file-${statusDisplay.tone}`,
            `git-project-commit-node-${group.tone || "review"}`,
          ].join(" "),
          data: {
            kind: "file",
            path,
            name: part,
            group: group.key,
            groupTitle: group.title,
            selectable,
            status: meta.status,
            statusLabel: statusDisplay.label,
            statusSymbol: statusDisplay.symbol,
            statusTone: statusDisplay.tone,
            risk: meta.risk,
            classifications: meta.labels,
            reason: meta.reason,
            modified: meta.modified,
            meta: meta.meta,
          },
        });
      } else {
        child = gitProjectCommitCreateTreeNode(part, `dir:${cursorPath}/`, {
          type: "dir",
          expanded: false,
          selected: false,
          checkbox: true,
          unselectable: false,
          classes: "git-project-commit-tree-dir git-project-commit-node-dir",
          data: {
            kind: "dir",
            name: part,
            path: cursorPath,
            selectable: true,
          },
        });
      }
      cursor.children.push(child);
    }
    cursor = child;
  });
}

function gitProjectCommitEmptyTreeSource() {
  return [
    gitProjectCommitCreateTreeNode("No candidate files returned by the planner", "empty:candidate-files", {
      type: "empty",
      checkbox: false,
      unselectable: true,
      data: {kind: "empty", selectable: false},
    }),
  ];
}

function gitProjectCommitTreeSource(review = {}, fileBasketModel = gitProjectCommitFileBasketModel(review)) {
  const modelTree = gitProjectCommitTreeSourceFromModel(fileBasketModel);
  if (Array.isArray(modelTree) && modelTree.length) return modelTree;

  const root = gitProjectCommitCreateTreeNode("Candidate files", "candidate-files", {
    type: "dir",
    folder: true,
    expanded: true,
    checkbox: true,
    data: {kind: "dir", path: "", selectable: true},
  });
  gitProjectCommitCandidateItems(review).forEach(({item, group}) => gitProjectCommitInsertTreePath(root, item, group));
  gitProjectCommitSortTreeNodes(root.children);
  gitProjectCommitAnnotateDirectoryStats(root);
  gitProjectCommitFinalizeDirectorySelection(root);
  if (!root.children.length) {
    return gitProjectCommitEmptyTreeSource();
  }
  return root.children;
}

function gitProjectCommitReviewCandidatePaths(review = {}) {
  const fileBasketModel = gitProjectCommitFileBasketModel(review);
  if (Array.isArray(fileBasketModel?.rows)) {
    return fileBasketModel.rows
      .map((row = {}) => row.path || "")
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
  }
  const paths = new Set();
  gitProjectCommitCandidateItems(review).forEach(({item = {}} = {}) => {
    const path = String(item.path || "").replace(/\\/g, "/").replace(/^\/+/, "");
    if (path) paths.add(path);
  });
  return Array.from(paths).sort((a, b) => a.localeCompare(b));
}

function gitProjectCommitStepFromInspection(data = {}) {
  const steps = Array.isArray(data?.wizard?.steps) ? data.wizard.steps : [];
  return steps.find((step = {}) => gitProjectStepIsCommitCard(step)) || {};
}

function gitProjectCommitReviewFromInspection(data = {}) {
  return gitProjectCommitStepFromInspection(data).commit_review || {};
}

function gitProjectCommitFallbackTreeHtml(nodes = []) {
  const renderNode = (node = {}) => {
    const data = node.data || {};
    const children = Array.isArray(node.children) ? node.children : [];
    const isFile = data.kind === "file";
    const isDir = data.kind === "dir" || data.kind === "group";
    const checkbox = node.checkbox !== false && data.selectable !== false;
    const checked = node.selected && checkbox ? "checked" : "";
    const disabled = checkbox ? "" : "disabled";
    const path = data.path || "";
    const meta = data.meta || data.subtitle || data.reason || "";
    return `<li class="git-project-commit-fallback-node ${isDir ? "is-dir" : ""} ${isFile ? "is-file" : ""}" data-git-commit-tree-node="${escapeHtml(data.kind || "node")}" data-git-commit-status="${escapeHtml(data.status || "")}">
      <label>
        <input type="checkbox"
          ${checked}
          ${disabled}
          data-git-commit-tree-checkbox="${isDir ? "dir" : isFile ? "file" : "none"}"
          data-git-commit-path="${escapeHtml(path)}"
          data-git-commit-selectable="${checkbox ? "true" : "false"}"
          ${isFile ? `data-git-commit-file="${escapeHtml(path)}"` : ""}>
        <span>
          <strong>${escapeHtml(isFile && path ? path : node.title || "")}</strong>
          ${meta ? `<small>${escapeHtml(meta)}</small>` : ""}
        </span>
      </label>
      ${children.length ? `<ul>${children.map(renderNode).join("")}</ul>` : ""}
    </li>`;
  };
  return `<ul class="git-project-commit-fallback-tree">${nodes.map(renderNode).join("")}</ul>`;
}

function gitProjectCommitBasketHtml(review = {}) {
  const fileBasketModel = gitProjectCommitFileBasketModel(review);
  const treeSource = gitProjectCommitTreeSource(review, fileBasketModel);
  const groups = gitProjectCommitGroups(review);
  const totals = gitProjectCommitTreeStats(treeSource);
  const selectedTotal = Array.isArray(fileBasketModel?.defaultSelectedPaths)
    ? fileBasketModel.defaultSelectedPaths.length
    : groups.selected_by_default.filter((item = {}) => item.path).length;
  const modelJson = gitProjectCommitFileBasketModelJson(fileBasketModel);
  return `<section class="git-project-commit-right" data-git-commit-basket data-git-commit-file-basket-model-ready="${fileBasketModel ? "true" : "false"}">
    ${gitProjectCommitRepoIdentityHtml(review)}
    <div class="git-project-subscreen-panel-head">
      <strong>File basket</strong>
      <span>directories first · files under paths</span>
    </div>
    <div class="git-project-commit-basket-summary">
      <span>Total candidates <strong>${Number(totals.total)}</strong></span>
      <span class="is-untracked">+ Untracked <strong>${Number(totals.untracked)}</strong></span>
      <span class="is-tracked">✓ Changed <strong>${Number(totals.changed)}</strong></span>
      <span class="is-blocked">Blocked <strong>${Number(totals.blocked)}</strong></span>
    </div>
    <p class="git-project-muted">Repo file tree: select files directly or select folders as a shortcut. Checked folders mean all selectable child files are selected; mixed folders mean only some child files are selected. ${selectedTotal ? `${selectedTotal} file${selectedTotal === 1 ? "" : "s"} selected by default.` : "Review candidates are not selected until you choose them."}</p>
    ${modelJson ? `<textarea hidden data-git-commit-file-basket-model>${escapeHtml(modelJson)}</textarea>` : ""}
    <textarea hidden data-git-commit-tree-source>${escapeHtml(JSON.stringify(treeSource))}</textarea>
    <div class="git-project-commit-wunderbaum-shell">
      <div class="git-project-commit-wunderbaum wb-skeleton wb-initializing" data-git-commit-tree></div>
      <div class="git-project-commit-tree-fallback" data-git-commit-tree-fallback>
        ${gitProjectCommitFallbackTreeHtml(treeSource)}
      </div>
    </div>
  </section>`;
}

function gitProjectWunderbaumConstructor() {
  return window.mar10?.Wunderbaum || window.Wunderbaum || window.wunderbaum?.Wunderbaum || null;
}

function gitProjectEnsureCommitTreeStylesheet(href, assetName) {
  if (!href || document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  link.dataset.gitCommitWunderbaumAsset = assetName || "css";
  document.head.appendChild(link);
}

function gitProjectLoadWunderbaum() {
  const loaded = gitProjectWunderbaumConstructor();
  if (loaded) return Promise.resolve(loaded);
  if (gitProjectWunderbaumLoadPromise) return gitProjectWunderbaumLoadPromise;
  gitProjectWunderbaumLoadPromise = new Promise((resolve, reject) => {
    gitProjectEnsureCommitTreeStylesheet(GIT_PROJECT_WUNDERBAUM_ASSETS.css, "css");
    gitProjectEnsureCommitTreeStylesheet(GIT_PROJECT_WUNDERBAUM_ASSETS.icons, "icons");
    const finish = () => {
      const constructor = gitProjectWunderbaumConstructor();
      if (constructor) {
        resolve(constructor);
      } else {
        reject(new Error("Wunderbaum global was not registered."));
      }
    };
    const existing = document.querySelector(`script[src="${GIT_PROJECT_WUNDERBAUM_ASSETS.js}"]`);
    if (existing) {
      if (gitProjectWunderbaumConstructor()) {
        finish();
        return;
      }
      existing.addEventListener("load", finish, {once: true});
      existing.addEventListener("error", () => reject(new Error("Could not load Wunderbaum.")), {once: true});
      window.setTimeout(() => {
        if (gitProjectWunderbaumConstructor()) {
          finish();
        } else {
          reject(new Error("Existing Wunderbaum script did not register a constructor."));
        }
      }, 3000);
      return;
    }
    const script = document.createElement("script");
    script.src = GIT_PROJECT_WUNDERBAUM_ASSETS.js;
    script.async = true;
    script.dataset.gitCommitWunderbaumAsset = "js";
    script.onload = finish;
    script.onerror = () => reject(new Error("Could not load Wunderbaum."));
    document.head.appendChild(script);
  });
  return gitProjectWunderbaumLoadPromise;
}

function gitProjectCommitReadTreeSource(workbench) {
  const sourceNode = workbench?.querySelector?.("[data-git-commit-tree-source]");
  if (!sourceNode) return [];
  try {
    const parsed = JSON.parse(sourceNode.value || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch (error) {
    return [];
  }
}

function gitProjectCommitReadFileBasketModel(workbench) {
  const sourceNode = workbench?.querySelector?.("[data-git-commit-file-basket-model]");
  if (!sourceNode) return null;
  try {
    const parsed = JSON.parse(sourceNode.value || "null");
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (error) {
    return null;
  }
}

function gitProjectCommitSortSelectedPaths(paths = []) {
  return Array.from(new Set((Array.isArray(paths) ? paths : []).filter(Boolean)))
    .sort((a, b) => String(a).localeCompare(String(b)));
}

function gitProjectCommitSelectionController(workbench, paths = null) {
  const controllerAdapter = gitProjectCommitFileBasketControllerAdapter();
  const model = gitProjectCommitReadFileBasketModel(workbench);
  if (!controllerAdapter?.createFileBasketController || !model) return null;
  try {
    return controllerAdapter.createFileBasketController(model, {
      selectedPaths: Array.isArray(paths) ? paths : model.defaultSelectedPaths,
      sourceSurface: "task-manager.file-basket"
    });
  } catch (error) {
    console.warn("Could not create MCEL file basket selection controller.", error);
    return null;
  }
}

function gitProjectCommitAdapterSelectedOutput(workbench, paths = []) {
  const fallbackPaths = gitProjectCommitSortSelectedPaths(paths);
  const model = gitProjectCommitReadFileBasketModel(workbench);
  const controllerAdapter = gitProjectCommitFileBasketControllerAdapter();
  if (controllerAdapter?.selectedOutput && model) {
    try {
      return controllerAdapter.selectedOutput(model, fallbackPaths);
    } catch (error) {
      console.warn("Could not normalize selection through MCEL file basket controller.", error);
    }
  }
  const adapter = gitProjectCommitFileBasketAdapter();
  if (!adapter?.selectedOutput || !model) return fallbackPaths;
  return adapter.selectedOutput(model, fallbackPaths);
}

function gitProjectCommitSelectionAdapterReport(workbench, rawPaths = []) {
  const fallbackPaths = gitProjectCommitSortSelectedPaths(rawPaths);
  const model = gitProjectCommitReadFileBasketModel(workbench);
  const controllerAdapter = gitProjectCommitFileBasketControllerAdapter();
  if (controllerAdapter?.selectionReport && model) {
    try {
      const report = controllerAdapter.selectionReport(model, fallbackPaths);
      return {
        enabled: true,
        controller: "McelFileBasketController",
        rawPaths: report.rawPaths || fallbackPaths,
        selectedPaths: report.selectedPaths || [],
        matches: report.matches === true,
        summary: report.summary || null,
      };
    } catch (error) {
      console.warn("Could not build MCEL file basket controller report.", error);
    }
  }
  const adapter = gitProjectCommitFileBasketAdapter();
  if (!adapter?.selectedOutput || !model) {
    return {
      enabled: false,
      rawPaths: fallbackPaths,
      selectedPaths: fallbackPaths,
      matches: true,
      summary: null,
    };
  }
  const selectedPaths = adapter.selectedOutput(model, fallbackPaths);
  return {
    enabled: true,
    controller: "McelFileBasketModel",
    rawPaths: fallbackPaths,
    selectedPaths,
    matches: JSON.stringify(fallbackPaths) === JSON.stringify(selectedPaths),
    summary: typeof adapter.selectionSummary === "function" ? adapter.selectionSummary(model, fallbackPaths) : null,
  };
}

function gitProjectCommitApplySelectionCommand(workbench, command = "", payload = {}, selectedPaths = []) {
  const model = gitProjectCommitReadFileBasketModel(workbench);
  const controllerAdapter = gitProjectCommitFileBasketControllerAdapter();
  const fallbackPaths = gitProjectCommitSortSelectedPaths(selectedPaths);
  if (!controllerAdapter?.applySelectionCommand || !model) {
    return {ok: false, selectedPaths: fallbackPaths, output: fallbackPaths, reason: "MCEL file basket controller unavailable"};
  }
  try {
    return controllerAdapter.applySelectionCommand(model, fallbackPaths, command, payload);
  } catch (error) {
    console.warn("Could not apply MCEL file basket selection command.", error);
    return {ok: false, selectedPaths: fallbackPaths, output: fallbackPaths, reason: String(error?.message || error)};
  }
}

function gitProjectCommitCanSelectTreeNode(workbench, node = {}) {
  const data = node?.data || {};
  if (data.kind === "empty" || data.selectable === false) return false;
  const model = gitProjectCommitReadFileBasketModel(workbench);
  const controllerAdapter = gitProjectCommitFileBasketControllerAdapter();
  if (controllerAdapter?.canSelectTreeNode && model) {
    try {
      return controllerAdapter.canSelectTreeNode(model, node);
    } catch (error) {
      console.warn("Could not ask MCEL file basket controller about tree-node selectability.", error);
    }
  }
  return data.selectable !== false && data.kind !== "empty";
}

function gitProjectCommitDirectorySelectionState(workbench, selectedPaths = [], directoryPath = "") {
  const model = gitProjectCommitReadFileBasketModel(workbench);
  const controllerAdapter = gitProjectCommitFileBasketControllerAdapter();
  if (controllerAdapter?.deriveDirectorySelectionState && model) {
    try {
      return controllerAdapter.deriveDirectorySelectionState(model, selectedPaths, directoryPath);
    } catch (error) {
      console.warn("Could not derive MCEL file basket directory selection state.", error);
    }
  }
  return "";
}

function gitProjectCommitFlattenTreeFiles(nodes = [], out = []) {
  nodes.forEach((node = {}) => {
    const data = node.data || {};
    if (data.kind === "file" && data.path) out.push(data);
    if (Array.isArray(node.children)) gitProjectCommitFlattenTreeFiles(node.children, out);
  });
  return out;
}

function gitProjectCommitBuildFileIndex(files = []) {
  const exact = new Set();
  const baseCounts = new Map();
  const baseToPath = new Map();
  files.forEach((file = {}) => {
    const path = String(file.path || "").replace(/\\/g, "/");
    if (!path) return;
    exact.add(path);
    const base = path.split("/").pop();
    baseCounts.set(base, (baseCounts.get(base) || 0) + 1);
    baseToPath.set(base, path);
  });
  const uniqueBaseToPath = new Map();
  baseToPath.forEach((path, base) => {
    if (baseCounts.get(base) === 1) uniqueBaseToPath.set(base, path);
  });
  return {files, exact, uniqueBaseToPath};
}

function gitProjectCommitCleanPathCandidate(value = "") {
  let text = String(value || "").trim();
  if (!text) return "";
  text = text
    .replace(/\\/g, "/")
    .replace(/^[\s✓☑☐+>›▸▾-]+/g, "")
    .replace(/^file:/i, "")
    .replace(/^folder:/i, "")
    .replace(/^dir:/i, "")
    .trim();
  text = text
    .replace(/\s+·\s+.*$/g, "")
    .replace(/\s+-\s+untracked\s+.*$/i, "")
    .replace(/\s+-\s+modified\s+.*$/i, "")
    .replace(/\s+-\s+deleted\s+.*$/i, "")
    .replace(/\s+-\s+renamed\s+.*$/i, "")
    .replace(/\s+-\s+review before selecting\s+.*$/i, "")
    .replace(/\s+/g, " ")
    .trim();
  return text;
}

function gitProjectCommitCanonicalFilePath(value = "", index = gitProjectCommitBuildFileIndex()) {
  const raw = gitProjectCommitCleanPathCandidate(value);
  if (!raw) return "";
  if (/\bdir\b.*\bfiles?\b/i.test(raw) || /^\d+\s+files?\b/i.test(raw)) return "";
  if (index.exact.has(raw)) return raw;
  const withoutRoot = raw.replace(/^main_computer_test\//, "");
  if (index.exact.has(withoutRoot)) return withoutRoot;
  const suffixMatches = index.files
    .map((file = {}) => String(file.path || "").replace(/\\/g, "/"))
    .filter((path) => path === raw || path.endsWith(`/${raw}`));
  if (suffixMatches.length === 1) return suffixMatches[0];
  const base = raw.split("/").pop();
  if (index.uniqueBaseToPath.has(base)) return index.uniqueBaseToPath.get(base);
  return "";
}

function gitProjectCommitTreeNodePath(node, index) {
  const candidates = [
    node?.data?.path,
    node?.data?.file,
    node?.data?.repoPath,
    node?.data?.gitCommitFile,
    node?.key,
    node?.title,
  ];
  for (const candidate of candidates) {
    const path = gitProjectCommitCanonicalFilePath(candidate, index);
    if (path) return path;
  }
  return "";
}

function gitProjectCommitTreeNodeSelected(node) {
  try {
    if (typeof node?.isSelected === "function" && node.isSelected()) return true;
  } catch (error) {
    return false;
  }
  return Boolean(node?.selected || node?._selected || node?.data?.selected);
}

function gitProjectCommitVisitTreeNodes(tree, visitor) {
  if (!tree || typeof visitor !== "function") return;
  if (typeof tree.visit === "function") {
    tree.visit(visitor);
    return;
  }
  if (tree.rootNode && typeof tree.rootNode.visit === "function") {
    tree.rootNode.visit(visitor);
  }
}

function gitProjectCommitRawSelectedFilesFromFallback(workbench) {
  const files = gitProjectCommitFlattenTreeFiles(gitProjectCommitReadTreeSource(workbench));
  const index = gitProjectCommitBuildFileIndex(files);
  return Array.from(workbench?.querySelectorAll?.("[data-git-commit-tree-checkbox='file']:checked") || [])
    .map((input) => gitProjectCommitCanonicalFilePath(input.dataset.gitCommitFile || input.dataset.gitCommitPath || input.value || "", index))
    .filter(Boolean);
}

function gitProjectCommitSelectedFilesFromFallback(workbench) {
  return gitProjectCommitAdapterSelectedOutput(workbench, gitProjectCommitRawSelectedFilesFromFallback(workbench));
}

function gitProjectCommitSelectedFilesFromWunderbaum(tree) {
  const workbench = tree?.gitCommitWorkbench || tree?.element?.closest?.("[data-git-commit-workbench]") || tree?.options?.element?.closest?.("[data-git-commit-workbench]");
  const files = gitProjectCommitFlattenTreeFiles(gitProjectCommitReadTreeSource(workbench));
  const index = gitProjectCommitBuildFileIndex(files);
  const paths = new Set();

  try {
    if (typeof tree?.getSelectedNodes === "function") {
      (tree.getSelectedNodes() || []).forEach((node) => {
        if (node?.data?.selectable === false) return;
        const path = gitProjectCommitTreeNodePath(node, index);
        if (path) paths.add(path);
      });
    }
  } catch (error) {
    console.warn("Could not read selected Wunderbaum nodes.", error);
  }

  try {
    gitProjectCommitVisitTreeNodes(tree, (node) => {
      if (!gitProjectCommitTreeNodeSelected(node) || node?.data?.selectable === false) return;
      const path = gitProjectCommitTreeNodePath(node, index);
      if (path) paths.add(path);
    });
  } catch (error) {
    console.warn("Could not visit selected Wunderbaum nodes.", error);
  }

  return gitProjectCommitAdapterSelectedOutput(workbench, Array.from(paths));
}

function gitProjectCommitSelectedFilesFromDom(workbench) {
  const files = gitProjectCommitFlattenTreeFiles(gitProjectCommitReadTreeSource(workbench));
  const index = gitProjectCommitBuildFileIndex(files);
  const treeElement = workbench?.querySelector?.("[data-git-commit-tree]");
  const paths = new Set();
  if (!treeElement) return [];
  const selectedElements = treeElement.querySelectorAll(`
    input[type="checkbox"]:checked,
    [role="checkbox"][aria-checked="true"],
    .wb-checkbox[aria-checked="true"],
    .wb-checkbox.wb-selected,
    .wb-checkbox.wb-checked,
    .wb-row.wb-selected,
    .wb-row[aria-selected="true"],
    .wb-node.wb-selected,
    [role="treeitem"][aria-selected="true"]
  `);
  selectedElements.forEach((element) => {
    const row = element.closest(".wb-row, .wb-node, [role='treeitem'], li, tr") || element.parentElement || element;
    const candidates = [];
    [element, row].forEach((candidateElement) => {
      if (!candidateElement) return;
      ["data-git-commit-file", "data-path", "data-key", "data-ref-key", "data-node-key", "title", "aria-label", "value"].forEach((attr) => {
        const value = candidateElement.getAttribute?.(attr);
        if (value && value !== "on") candidates.push(value);
      });
      Object.values(candidateElement.dataset || {}).forEach((value) => {
        if (value && value !== "on") candidates.push(value);
      });
    });
    const titleElement = row.querySelector?.(".wb-title, [class*='title'], [data-title]") || row;
    if (titleElement?.textContent) candidates.push(titleElement.textContent);
    for (const candidate of candidates) {
      const path = gitProjectCommitCanonicalFilePath(candidate, index);
      if (path) {
        paths.add(path);
        return;
      }
    }
  });
  return gitProjectCommitAdapterSelectedOutput(workbench, Array.from(paths));
}

function gitProjectCommitSelectedFilesFromWorkbench(workbench) {
  const tree = workbench?.gitCommitWunderbaum || workbench?.querySelector?.("[data-git-commit-tree]")?._wb_tree;
  const paths = new Set();
  gitProjectCommitSelectedFilesFromWunderbaum(tree).forEach((path) => paths.add(path));
  gitProjectCommitSelectedFilesFromDom(workbench).forEach((path) => paths.add(path));
  if (!tree || workbench?.dataset?.gitCommitWunderbaumFallback === "true") {
    gitProjectCommitSelectedFilesFromFallback(workbench).forEach((path) => paths.add(path));
  }
  return gitProjectCommitAdapterSelectedOutput(workbench, Array.from(paths));
}

function gitProjectCommitReviewStats(workbench, selectedPaths = []) {
  const files = gitProjectCommitFlattenTreeFiles(gitProjectCommitReadTreeSource(workbench));
  const selected = new Set(selectedPaths);
  const isReviewFile = (file = {}) => (
    file.group === "review_before_selecting" ||
    String(file.groupTitle || "").toLowerCase().includes("review") ||
    String(file.risk || file.privacy_risk || "").toLowerCase().includes("review")
  );
  const isBlockedFile = (file = {}) => (
    file.group === "blocked_possible_secrets" ||
    String(file.risk || file.privacy_risk || "").toLowerCase().includes("block") ||
    String(file.status || "").toLowerCase().includes("conflict") ||
    file.selectable === false
  );
  const reviewFiles = files.filter(isReviewFile);
  const blockedFiles = files.filter(isBlockedFile);
  const selectedFiles = files.filter((file = {}) => selected.has(file.path));
  const selectedReview = selectedFiles.filter(isReviewFile);
  const selectedBlocked = selectedFiles.filter(isBlockedFile);
  return {
    total: files.length,
    selected: selectedPaths.length,
    review: reviewFiles.length,
    blocked: blockedFiles.length,
    selectedReview: selectedReview.length,
    selectedBlocked: selectedBlocked.length,
    selectedBlockedPaths: selectedBlocked.map((file = {}) => file.path).filter(Boolean),
  };
}

function gitProjectCommitControlChecked(workbench, key = "") {
  const input = workbench?.querySelector?.(`[data-git-commit-stage-check="${CSS.escape(key)}"]`);
  return Boolean(input?.checked);
}

function gitProjectCommitSummaryValue(workbench, key = "") {
  const node = workbench?.querySelector?.(`[data-git-commit-summary-value="${CSS.escape(key)}"]`);
  const value = String(node?.textContent || "").trim();
  return value && value !== "(missing)" ? value : "";
}

function gitProjectCommitMessageFromWorkbench(workbench) {
  const input = workbench?.querySelector?.('[data-git-commit-field="commit_message"] input');
  return String(input?.value || DEFAULT_COMMIT_MESSAGE || "").trim();
}

function gitProjectCommitBranchFromWorkbench(workbench) {
  const input = workbench?.querySelector?.('[data-git-commit-field="branch"] input');
  return String(input?.value || gitProjectCommitSummaryValue(workbench, "branch") || "master").trim();
}

function gitProjectCommitIdentityFromWorkbench(workbench) {
  const name = String(
    workbench?.querySelector?.('[data-git-commit-field="git_user_name"] input')?.value ||
    gitProjectCommitSummaryValue(workbench, "git_user_name") ||
    ""
  ).trim();
  const email = String(
    workbench?.querySelector?.('[data-git-commit-field="git_user_email"] input')?.value ||
    gitProjectCommitSummaryValue(workbench, "git_user_email") ||
    ""
  ).trim();
  return {name, email, ready: Boolean(name && email.includes("@"))};
}

function gitProjectCommitSelectedReadiness(workbench, paths = []) {
  const stats = gitProjectCommitReviewStats(workbench, paths);
  const selectedPhrase = paths.length ? `${paths.length} FILE${paths.length === 1 ? "" : "S"} SELECTED` : "NO FILES SELECTED";
  const reviewed = gitProjectCommitControlChecked(workbench, "reviewed_staged_diff");
  const warningsAccepted = gitProjectCommitControlChecked(workbench, "upstream_gates_accepted");
  const message = gitProjectCommitMessageFromWorkbench(workbench);
  const branch = gitProjectCommitBranchFromWorkbench(workbench);
  const identity = gitProjectCommitIdentityFromWorkbench(workbench);
  const repoWarningsPresent = Boolean(stats.review || stats.blocked || stats.selectedReview);
  const reasons = [];
  let status = "blocked";

  if (!paths.length) {
    reasons.push("choose files before commit");
  }
  if (stats.selectedBlocked) {
    reasons.push(`${stats.selectedBlocked} selected hard blocker${stats.selectedBlocked === 1 ? "" : "s"}`);
  }
  if (paths.length && !reviewed) {
    reasons.push("selected files preview not confirmed");
  }
  if (paths.length && repoWarningsPresent && !warningsAccepted) {
    reasons.push("warnings not accepted");
  }
  if (!message) {
    reasons.push("commit message missing");
  }
  if (!branch) {
    reasons.push("branch missing");
  }
  if (!identity.ready) {
    reasons.push("identity incomplete");
  }

  const ready = reasons.length === 0;
  if (ready) {
    status = warningsAccepted || repoWarningsPresent ? "warnings-accepted" : "ready";
  } else if (stats.selectedBlocked) {
    status = "selected-blocked";
  } else if (paths.length && repoWarningsPresent && !warningsAccepted) {
    status = "warnings-needed";
  } else if (paths.length && !reviewed) {
    status = "review-needed";
  } else if (!paths.length) {
    status = "empty";
  }

  const summary = ready
    ? `${warningsAccepted || repoWarningsPresent ? "WARNINGS ACCEPTED" : "GATES CLEAR"} · ${selectedPhrase} · READY TO COMMIT`
    : `${selectedPhrase} · ${reasons.join(" · ") || "commit is blocked until validation passes"}`;

  return {
    ready,
    status,
    reasons,
    summary,
    stats,
    selectedPhrase,
    reviewed,
    warningsAccepted,
    repoWarningsPresent,
    message,
    branch,
    identity,
  };
}

function gitProjectCommitSelectedPreviewText(paths = []) {
  if (!paths.length) return "No files selected yet. Select clean files from the File Basket on the right.";
  const shown = paths.slice(0, 18);
  const hidden = paths.length - shown.length;
  return [
    `${paths.length} file${paths.length === 1 ? "" : "s"} selected:`,
    ...shown.map((path) => `✓ ${path}`),
    hidden > 0 ? `… ${hidden} more` : "",
  ].filter(Boolean).join("\n");
}

function gitProjectCommitDeveloperCommandPreview(paths = []) {
  if (!paths.length) {
    return [
      "git add -- <selected files>",
      "git diff --cached --stat",
      "git diff --cached --check",
    ].join("\n");
  }
  return [
    `git add -- ${paths.map((path) => gitProjectCommitShellQuote(path)).join(" ")}`,
    "git diff --cached --stat",
    "git diff --cached --check",
  ].join("\n");
}

function gitProjectCommitSetStepState(workbench, stepId = "", mark = "!", detail = "") {
  const step = workbench?.querySelector?.(`[data-git-commit-step="${CSS.escape(stepId)}"]`);
  const markNode = workbench?.querySelector?.(`[data-git-commit-step-mark="${CSS.escape(stepId)}"]`);
  const detailNode = workbench?.querySelector?.(`[data-git-commit-step-detail="${CSS.escape(stepId)}"]`);
  if (step) {
    step.classList.toggle("is-ready", mark === "✓");
    step.classList.toggle("is-locked", mark === "🔒");
    step.classList.toggle("needs-review", mark !== "✓" && mark !== "🔒");
  }
  if (markNode) markNode.textContent = mark;
  if (detailNode) detailNode.textContent = detail;
}

function gitProjectCommitUpdateReviewStatus(workbench, paths = []) {
  const state = gitProjectCommitSelectedReadiness(workbench, paths);
  const {stats} = state;

  let title = "Choose files first";
  let body = "Select clean files from the File Basket. The selected paths will appear in the Selected Files Preview above.";
  if (paths.length && stats.selectedBlocked) {
    title = "Remove selected hard blockers";
    body = `${stats.selectedBlocked} selected file${stats.selectedBlocked === 1 ? " is" : "s are"} hard-blocked. Warning acceptance does not override selected blocked files.`;
  } else if (paths.length && !state.reviewed) {
    title = "Review selected files";
    body = "Use Selected Files Preview as the source of truth, then confirm it matches the intended commit.";
  } else if (paths.length && state.repoWarningsPresent && !state.warningsAccepted) {
    title = "Accept warnings to proceed";
    body = "The selected files have no hard blockers. Accept the remaining warnings to proceed with this selected-file commit.";
  } else if (paths.length && state.ready) {
    title = "Ready to open commit preview";
    body = "Unselected repo warnings remain visible as context only. The selected files have no hard blockers and the warning acknowledgement is satisfied.";
  } else if (paths.length) {
    title = "Commit details need attention";
    body = state.reasons.join(" · ");
  }

  const status = workbench.querySelector("[data-git-commit-review-status]");
  if (status) {
    status.classList.toggle("is-ready", state.ready);
    status.classList.toggle("is-blocked", !state.ready);
  }
  const titleNode = workbench.querySelector("[data-git-commit-review-title]");
  const bodyNode = workbench.querySelector("[data-git-commit-review-body]");
  const scopeNode = workbench.querySelector("[data-git-commit-review-scope]");
  if (titleNode) titleNode.textContent = title;
  if (bodyNode) bodyNode.textContent = body;
  if (scopeNode) {
    scopeNode.textContent = `Selected hard blockers: ${stats.selectedBlocked}. Warnings accepted: ${state.warningsAccepted ? "yes" : "no"}. Global context: ${stats.review} need review, ${stats.blocked} blocked.`;
  }

  const selectedCount = workbench.querySelector("[data-git-commit-review-count='selected']");
  const reviewCount = workbench.querySelector("[data-git-commit-review-count='review']");
  const blockedCount = workbench.querySelector("[data-git-commit-review-count='blocked']");
  if (selectedCount) selectedCount.textContent = String(stats.selected);
  if (reviewCount) reviewCount.textContent = String(stats.review);
  if (blockedCount) blockedCount.textContent = String(stats.blocked);

  const diagnostics = workbench.querySelector("[data-git-commit-dev-diagnostics]");
  const preview = workbench.querySelector("[data-git-commit-dev-preview]");
  if (preview) preview.textContent = gitProjectCommitDeveloperCommandPreview(paths);
  if (diagnostics) diagnostics.hidden = false;

  const fileBasketDetail = workbench.querySelector("[data-git-commit-step-detail='file_basket']");
  if (fileBasketDetail) fileBasketDetail.textContent = `${stats.selected} selected · ${stats.review} review`;
}

function gitProjectCommitUpdateFinalReadiness(workbench, paths = []) {
  const state = gitProjectCommitSelectedReadiness(workbench, paths);
  const readiness = workbench.querySelector("[data-git-commit-final-readiness]");
  if (readiness) {
    readiness.textContent = state.summary;
    const box = readiness.closest(".git-project-commit-readiness");
    if (box) {
      box.classList.toggle("is-ready", state.ready);
      box.classList.toggle("is-blocked", !state.ready);
    }
  }

  const panelState = workbench.querySelector("[data-git-commit-panel='create_commit'] .git-project-subscreen-panel-head span");
  if (panelState) panelState.textContent = state.ready ? "ready" : "blocked";

  const createButton = workbench.querySelector('[data-git-commit-action="create_local_commit"]');
  if (createButton) {
    createButton.disabled = !state.ready;
    createButton.title = state.ready
      ? "Open the dry-run commit preview pane."
      : `Commit preview is blocked: ${state.reasons.join(" · ")}`;
  }

  gitProjectCommitSetStepState(
    workbench,
    "stage_preview",
    state.ready ? "✓" : (paths.length ? "!" : "🔒"),
    state.ready ? "Selected-file warning gate satisfied" : (paths.length ? state.reasons.join(" · ") : "Choose files before staging")
  );
  gitProjectCommitSetStepState(
    workbench,
    "create_commit",
    state.ready ? "✓" : "🔒",
    state.ready ? "Open dry-run commit preview" : state.summary
  );

  gitProjectCommitUpdateExecutionPane(workbench, paths, state);
}

function gitProjectCommitRenderExecutionFiles(listNode, countNode, paths = [], options = {}) {
  const label = options.label || "selected";
  const noun = options.noun || "file";
  if (countNode) countNode.textContent = `${paths.length} ${label} ${noun}${paths.length === 1 ? "" : "s"}`;
  if (!listNode) return;
  listNode.innerHTML = "";
  if (!paths.length) {
    const item = document.createElement("li");
    item.textContent = options.emptyText || "No files selected.";
    listNode.appendChild(item);
    return;
  }
  paths.forEach((path) => {
    const item = document.createElement("li");
    item.textContent = path;
    listNode.appendChild(item);
  });
}

function gitProjectCommitExecutionTargetText(state = {}) {
  const identity = state.identity || {};
  const target = gitProjectCommitTargetMismatch(null, gitProjectCommitRepoFromWorkbench(null));
  return [
    `Repository: ${target.expected || gitProjectCommitRepoFromWorkbench(null) || "(unknown)"}`,
    `Branch: ${state.branch || "master"}`,
    `Identity: ${identity.name || "(missing)"} <${identity.email || "missing"}>`,
    target.ok ? "" : `Target mismatch: ${target.mismatches.join(" | ")}`,
  ].filter(Boolean).join("\n");
}

function gitProjectCommitUpdateExecutionPane(workbench, paths = [], state = null) {
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  if (!pane) return;
  const currentState = state || gitProjectCommitSelectedReadiness(workbench, paths);
  const messageNode = pane.querySelector("[data-git-commit-execution-message]");
  const targetNode = pane.querySelector("[data-git-commit-execution-target]");
  const filesNode = pane.querySelector("[data-git-commit-execution-files]");
  const fileCountNode = pane.querySelector("[data-git-commit-execution-file-count]");
  const stateNode = pane.querySelector("[data-git-commit-execution-state]");
  const doButton = pane.querySelector('[data-git-commit-action="do_git_commit"]');
  const dryRun = pane.querySelector('[data-git-commit-execution-option="dry_run"]');
  if (messageNode) messageNode.textContent = currentState.message || DEFAULT_COMMIT_MESSAGE;
  if (targetNode) targetNode.textContent = gitProjectCommitExecutionTargetText(currentState);
  gitProjectCommitRenderExecutionFiles(filesNode, fileCountNode, paths);
  const isDryRun = dryRun?.checked !== false;
  if (stateNode) stateNode.textContent = isDryRun ? "dry run armed" : "real commit armed";
  if (doButton) {
    doButton.disabled = !currentState.ready;
    doButton.title = currentState.ready
      ? (isDryRun ? "Run the backend dry-run commit sequence." : "Start the real backend commit sequence.")
      : `Cannot run yet: ${currentState.reasons.join(" · ")}`;
  }
}

function gitProjectCommitRefreshExecutionRemainingFiles(workbench, review = {}) {
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  if (!pane || pane.hidden) return [];
  const paths = gitProjectCommitReviewCandidatePaths(review);
  const filesNode = pane.querySelector("[data-git-commit-execution-files]");
  const fileCountNode = pane.querySelector("[data-git-commit-execution-file-count]");
  const stateNode = pane.querySelector("[data-git-commit-execution-state]");
  gitProjectCommitRenderExecutionFiles(filesNode, fileCountNode, paths, {
    label: "remaining uncommitted",
    emptyText: "No uncommitted files remain after this commit.",
  });
  if (stateNode) stateNode.textContent = paths.length ? "uncommitted list refreshed" : "clean after commit";
  return paths;
}

function gitProjectCommitReinitializeBasketTree(workbench) {
  if (!workbench) return;
  const element = workbench.querySelector("[data-git-commit-tree]");
  if (element) {
    delete element.dataset.gitWunderbaumReady;
    element._wb_tree = null;
  }
  const fallback = workbench.querySelector("[data-git-commit-tree-fallback]");
  if (fallback) {
    delete fallback.dataset.gitCommitFallbackReady;
    fallback.hidden = false;
  }
  delete workbench.dataset.gitCommitWorkbenchReady;
  workbench.gitCommitWunderbaum = null;
  gitProjectInitializeCommitWunderbaum(workbench);
}

function gitProjectCommitRefreshWorkbenchFromReview(workbench, step = {}) {
  const review = step.commit_review || {};
  if (!workbench || !Object.keys(review).length) return [];
  const body = workbench.querySelector(".git-project-commit-body");
  if (!body) return gitProjectCommitReviewCandidatePaths(review);

  const left = body.querySelector(".git-project-commit-left");
  if (left) left.outerHTML = gitProjectCommitStepsHtml(review);

  const fileBasketPanel = body.querySelector("[data-git-commit-panel='file_basket']");
  if (fileBasketPanel) fileBasketPanel.outerHTML = gitProjectCommitBasketControlsHtml(review);

  const stagePreviewPanel = body.querySelector("[data-git-commit-panel='stage_preview']");
  if (stagePreviewPanel) stagePreviewPanel.outerHTML = gitProjectCommitStagePreviewHtml(step);

  const right = body.querySelector("[data-git-commit-basket]");
  if (right) {
    right.outerHTML = gitProjectCommitBasketHtml(review);
  } else {
    body.insertAdjacentHTML("beforeend", gitProjectCommitBasketHtml(review));
  }

  gitProjectCommitReinitializeBasketTree(workbench);
  const selectedPaths = gitProjectCommitSelectedFilesFromWorkbench(workbench);
  gitProjectCommitUpdateSelectedPreview(workbench, selectedPaths);
  return gitProjectCommitReviewCandidatePaths(review);
}

function gitProjectCommitEventCreatedRealCommit(event = {}) {
  const result = event.result || {};
  if (result.dry_run === true) return false;
  if (result.commit_hash) return true;
  if (Array.isArray(result.commits) && result.commits.some((commit = {}) => commit.hash)) return true;
  return Boolean(event.commit_hash);
}

async function gitProjectCommitRefreshAfterCompletion(workbench, event = {}) {
  if (!gitProjectCommitEventCreatedRealCommit(event)) return null;
  const current = currentGitProject() || {};
  const payload = current.id ? {project_id: current.id} : {};
  try {
    gitProjectCommitSetExecutionStatus(workbench, "Commit finished. Refreshing uncommitted file list…");
    const data = await gitToolsRequest("/api/applications/git/project/inspect", payload);
    gitProjectLastInspection = data;
    if (data.project?.path) gitProjectSetTargetPathInputs(data.project.path);
    if (gitProjectsLastState && data.project) {
      gitProjectsLastState.current_project = data.project;
      renderGitProjects(gitProjectsLastState);
    }
    const step = gitProjectCommitStepFromInspection(data);
    const review = step.commit_review || {};
    if (!Object.keys(review).length) {
      gitProjectCommitAppendExecutionLine(workbench, "Fresh inspection completed, but no commit review was returned.");
      return data;
    }
    const remaining = gitProjectCommitRefreshWorkbenchFromReview(workbench, step);
    const displayed = gitProjectCommitRefreshExecutionRemainingFiles(workbench, review);
    const count = displayed.length || remaining.length;
    gitProjectCommitAppendExecutionLine(
      workbench,
      count
        ? `Refreshed uncommitted file list after commit: ${count} candidate file${count === 1 ? "" : "s"} remain.`
        : "Refreshed uncommitted file list after commit: no candidate files remain."
    );
    gitProjectCommitSetExecutionStatus(workbench, count ? "Commit finished. Uncommitted file list refreshed." : "Commit finished. No uncommitted files remain.");
    return data;
  } catch (error) {
    gitProjectCommitAppendExecutionLine(workbench, gitToolsOperationErrorText("Could not refresh uncommitted file list after commit", error));
    return null;
  }
}

function gitProjectCommitOpenExecutionPane(workbench) {
  const paths = gitProjectCommitSelectedFilesFromWorkbench(workbench);
  const state = gitProjectCommitSelectedReadiness(workbench, paths);
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  if (!pane) return;
  pane.hidden = false;
  document.body.classList.add("git-project-commit-execution-modal-open");
  gitProjectCommitUpdateExecutionPane(workbench, paths, state);
  const results = pane.querySelector("[data-git-commit-execution-results]");
  if (results) results.innerHTML = "";
  gitProjectCommitSetExecutionStatus(workbench, state.ready ? "Dry Run is selected. Review the relisted files, then choose Do Git Commit." : state.summary);
  gitProjectCommitAppendExecutionLine(
    workbench,
    state.ready ? "Ready for dry-run preview. Click Do Git Commit to relist staged files and preview the commit operation." : `Blocked: ${state.summary}`
  );
  const runButton = pane.querySelector('[data-git-commit-action="do_git_commit"]');
  if (runButton && !runButton.disabled) runButton.focus();
}

function gitProjectCommitCloseExecutionPane(workbench) {
  gitProjectCommitStopExecution(workbench, "Commit preview closed.");
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  if (pane) pane.hidden = true;
  document.body.classList.remove("git-project-commit-execution-modal-open");
}

function gitProjectCommitSetExecutionStatus(workbench, message = "") {
  const status = workbench?.querySelector?.("[data-git-commit-execution-status]");
  if (status) status.textContent = message;
}

function gitProjectCommitAppendExecutionLine(workbench, message = "") {
  const results = workbench?.querySelector?.("[data-git-commit-execution-results]");
  if (!results) return;
  const item = document.createElement("li");
  item.textContent = message;
  results.appendChild(item);
  item.scrollIntoView({behavior: "smooth", block: "nearest", inline: "nearest"});
}

function gitProjectCommitSetExecutionRunning(workbench, running) {
  const stopButton = workbench?.querySelector?.('[data-git-commit-action="stop_commit"]');
  const doButton = workbench?.querySelector?.('[data-git-commit-action="do_git_commit"]');
  if (stopButton) stopButton.disabled = !running;
  if (doButton) doButton.disabled = running;
}

function gitProjectCommitStopExecution(workbench, message = "Commit run stopped.") {
  const run = workbench?.__gitCommitExecutionRun;
  if (run?.timer) window.clearTimeout(run.timer);
  if (run) run.cancelled = true;
  gitProjectCommitSetExecutionStatus(workbench, message);
  gitProjectCommitAppendExecutionLine(workbench, message);
  const stopButton = workbench?.querySelector?.('[data-git-commit-action="stop_commit"]');
  const doButton = workbench?.querySelector?.('[data-git-commit-action="do_git_commit"]');
  if (stopButton) stopButton.disabled = true;
  if (doButton) doButton.disabled = true;
  if (run?.jobId && !run.cancelRequestSent) {
    run.cancelRequestSent = true;
    gitToolsRequest("/api/applications/git/project/commit/cancel", {job_id: run.jobId})
      .then((data) => {
        gitProjectCommitAppendExecutionLine(workbench, data.message || "Backend cancellation requested.");
        if (data.status) gitProjectCommitSetExecutionStatus(workbench, `Backend commit job status: ${data.status}`);
        if (!run.stream) gitProjectCommitSetExecutionRunning(workbench, false);
      })
      .catch((error) => {
        gitProjectCommitSetExecutionRunning(workbench, false);
        gitProjectCommitAppendExecutionLine(workbench, gitToolsOperationErrorText("Commit cancellation request failed", error));
      });
  } else {
    gitProjectCommitSetExecutionRunning(workbench, false);
  }
}

function gitProjectCommitRepoFromWorkbench(workbench) {
  const current = currentGitProject() || {};
  const inspection = gitProjectLastInspection?.project || {};
  const inspectionBelongsToCurrent = !inspection.id || !current.id || inspection.id === current.id;
  const candidates = [
    current.path,
    inspectionBelongsToCurrent ? gitProjectLastInspection?.selected_project : "",
    inspectionBelongsToCurrent ? inspection.path : "",
    workbench?.dataset?.gitCommitRepo,
    gitRepoDir?.value,
  ];
  for (const candidate of candidates) {
    const cleaned = String(candidate || "").trim();
    if (cleaned && cleaned !== ".") return cleaned;
  }
  return ".";
}

function gitProjectCommitExecutionPayload(workbench, paths = [], state = {}) {
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  const dryRun = pane?.querySelector?.('[data-git-commit-execution-option="dry_run"]')?.checked !== false;
  const oneAtATime = pane?.querySelector?.('[data-git-commit-execution-option="one_at_a_time"]')?.checked === true;
  const identity = state.identity || gitProjectCommitIdentityFromWorkbench(workbench);
  const repoDir = gitProjectCommitRepoFromWorkbench(workbench);
  const currentProject = currentGitProject() || {};
  const inspectionProject = gitProjectLastInspection?.project || {};
  const project = inspectionProject.id && currentProject.id && inspectionProject.id !== currentProject.id ? currentProject : (inspectionProject.id ? inspectionProject : currentProject);
  return {
    repo_dir: repoDir,
    project_id: project.id || "",
    project_path: project.path || repoDir,
    payload_repo_source: {
      workbench_repo: String(workbench?.dataset?.gitCommitRepo || ""),
      inspection_selected_project: String(gitProjectLastInspection?.selected_project || ""),
      inspection_project_path: String(gitProjectLastInspection?.project?.path || ""),
      current_project_path: String(currentGitProject()?.path || ""),
      input_project_path: String(gitProjectPath?.value || ""),
      input_repo_dir: String(gitRepoDir?.value || ""),
    },
    paths,
    selected_paths: paths,
    blocked_paths: Array.isArray(state.stats?.selectedBlockedPaths) ? state.stats.selectedBlockedPaths : [],
    message: state.message || gitProjectCommitMessageFromWorkbench(workbench) || DEFAULT_COMMIT_MESSAGE,
    branch: state.branch || gitProjectCommitBranchFromWorkbench(workbench),
    git_user_name: identity.name || "",
    git_user_email: identity.email || "",
    dry_run: dryRun,
    one_at_a_time: oneAtATime,
    confirm_real_commit: !dryRun,
  };
}

function gitProjectCommitFormatGitState(state = {}) {
  const staged = Array.isArray(state.staged) ? state.staged : [];
  const unstaged = Array.isArray(state.unstaged) ? state.unstaged : [];
  const untracked = Array.isArray(state.untracked) ? state.untracked : [];
  const untrackedLine = state.untracked_skipped
    ? `Untracked: ${state.untracked_note || "scan skipped for speed"}`
    : `Untracked: ${untracked.length ? untracked.join(", ") : "(none)"}`;
  return [
    `Current Git state: branch ${state.branch || "(unknown)"}`,
    `Staged: ${staged.length ? staged.join(", ") : "(none)"}`,
    `Unstaged: ${unstaged.length ? unstaged.join(", ") : "(none)"}`,
    untrackedLine,
    state.recovery_hint ? `Recovery hint: ${state.recovery_hint}` : "",
  ].filter(Boolean).join("\n");
}

function gitProjectCommitAppendExecutionEvent(workbench, event = {}) {
  const type = event.type || "event";
  const message = event.message || event.error || event.status || type;
  if (type === "command_finish" && Number(event.returncode || 0) === 0) return;
  if (type === "git_state" && event.git_state) {
    gitProjectCommitAppendExecutionLine(workbench, gitProjectCommitFormatGitState(event.git_state));
    return;
  }
  if (type === "finished" && event.result?.git_state) {
    gitProjectCommitAppendExecutionLine(workbench, gitProjectCommitFormatGitState(event.result.git_state));
  }
  if (type === "complete" && event.commit_hash) {
    gitProjectCommitAppendExecutionLine(workbench, `${message} (${event.commit_hash})`);
    return;
  }
  if (type === "command_start") {
    gitProjectCommitAppendExecutionLine(workbench, message);
    return;
  }
  if (type === "command_finish" && Number(event.returncode || 0) !== 0) {
    gitProjectCommitAppendExecutionLine(workbench, `${message}\n${event.stderr || event.stdout || ""}`.trim());
    return;
  }
  if (type === "summary" && Array.isArray(event.selected_files)) {
    gitProjectCommitAppendExecutionLine(workbench, `${message}\n${event.selected_files.map((path) => `- ${path}`).join("\n")}`);
    return;
  }
  gitProjectCommitAppendExecutionLine(workbench, message);
}

function gitProjectCommitStartEventStream(workbench, data = {}) {
  const url = data.stream_url || (data.job_id ? `/api/applications/git/project/commit/stream?job_id=${encodeURIComponent(data.job_id)}` : "");
  if (!url || typeof EventSource === "undefined") {
    gitProjectCommitSetExecutionRunning(workbench, false);
    gitProjectCommitSetExecutionStatus(workbench, "Commit job started, but this browser cannot open an event stream.");
    return;
  }
  const run = workbench.__gitCommitExecutionRun || {};
  const stream = new EventSource(url);
  run.stream = stream;
  run.jobId = data.job_id || run.jobId || "";
  run.cancelled = false;
  workbench.__gitCommitExecutionRun = run;
  stream.addEventListener("commit", (event) => {
    let payload = {};
    try {
      payload = JSON.parse(event.data || "{}");
    } catch (_error) {
      payload = {type: "line", message: event.data || ""};
    }
    gitProjectCommitAppendExecutionEvent(workbench, payload);
    const finalType = ["finished", "failed", "cancelled", "stream_timeout"].includes(payload.type || "");
    if (payload.type === "complete") {
      gitProjectCommitSetExecutionStatus(workbench, payload.message || "Commit job complete.");
    } else if (payload.type === "failed") {
      gitProjectCommitSetExecutionStatus(workbench, payload.message || "Commit job failed.");
    } else if (payload.type === "cancelled") {
      gitProjectCommitSetExecutionStatus(workbench, payload.message || "Commit job cancelled.");
    } else if (payload.type === "finished") {
      gitProjectCommitSetExecutionStatus(workbench, payload.message || "Commit job finished.");
    }
    if (finalType) {
      stream.close();
      gitProjectCommitSetExecutionRunning(workbench, false);
      refreshGitStatus().catch(() => null);
      gitProjectCommitRefreshAfterCompletion(workbench, payload).catch((error) => {
        gitProjectCommitAppendExecutionLine(workbench, gitToolsOperationErrorText("Could not refresh uncommitted file list after commit", error));
      });
    }
  });
  stream.onerror = () => {
    gitProjectCommitAppendExecutionLine(workbench, "Commit event stream disconnected.");
    stream.close();
    gitProjectCommitSetExecutionRunning(workbench, false);
    gitProjectCommitSetExecutionStatus(workbench, "Commit event stream disconnected.");
  };
}

async function gitProjectCommitRunExecution(workbench) {
  const paths = gitProjectCommitSelectedFilesFromWorkbench(workbench);
  const state = gitProjectCommitSelectedReadiness(workbench, paths);
  gitProjectCommitUpdateExecutionPane(workbench, paths, state);
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  const results = pane?.querySelector?.("[data-git-commit-execution-results]");
  const payload = gitProjectCommitExecutionPayload(workbench, paths, state);
  const targetCheck = gitProjectCommitTargetMismatch(workbench, payload.repo_dir || payload.project_path || "");
  if (results) results.innerHTML = "";
  if (!targetCheck.ok) {
    const message = [
      "Commit target mismatch:",
      `selected project = ${targetCheck.expected || "(missing)"}`,
      ...targetCheck.mismatches,
      "",
      "Select and inspect the intended project again before committing.",
    ].join("\n");
    gitProjectCommitSetExecutionStatus(workbench, "Commit target mismatch. Backend call was not started.");
    gitProjectCommitAppendExecutionLine(workbench, message);
    return;
  }
  if (!state.ready) {
    gitProjectCommitSetExecutionStatus(workbench, state.summary);
    gitProjectCommitAppendExecutionLine(workbench, `Blocked: ${state.reasons.join(" · ")}`);
    return;
  }
  if (!payload.dry_run) {
    const modeText = payload.one_at_a_time
      ? `This will create ${paths.length} real Git commit${paths.length === 1 ? "" : "s"}, one selected file per commit.`
      : "This will create one real Git commit containing the selected files.";
    if (!window.confirm(`${modeText}\n\nThe backend may leave Git staged or otherwise messy if you stop it mid-run. Continue?`)) {
      gitProjectCommitSetExecutionStatus(workbench, "Real commit cancelled before backend start.");
      gitProjectCommitAppendExecutionLine(workbench, "Real commit was not started.");
      return;
    }
  }

  const run = {cancelled: false, jobId: "", stream: null};
  workbench.__gitCommitExecutionRun = run;
  gitProjectCommitSetExecutionRunning(workbench, true);
  gitProjectCommitSetExecutionStatus(
    workbench,
    payload.dry_run
      ? (payload.one_at_a_time ? "Backend dry run in progress: one-at-a-time plan." : "Backend dry run in progress.")
      : (payload.one_at_a_time ? "Real backend commits in progress: one selected file per commit." : "Real backend commit in progress.")
  );
  gitProjectCommitAppendExecutionLine(workbench, "Starting backend selected-file commit job...");
  try {
    const data = await gitToolsRequest("/api/applications/git/project/commit/start", payload);
    run.jobId = data.job_id || "";
    gitProjectCommitAppendExecutionLine(workbench, `Commit job started: ${run.jobId || "(unknown job id)"}`);
    gitProjectCommitStartEventStream(workbench, data);
  } catch (error) {
    gitProjectCommitSetExecutionRunning(workbench, false);
    gitProjectCommitSetExecutionStatus(workbench, `Commit job failed to start: ${error?.message || error}`);
    gitProjectCommitAppendExecutionLine(workbench, gitToolsOperationErrorText("Commit job failed to start", error));
  }
}

function gitProjectWireCommitExecution(workbench) {
  if (!workbench || workbench.dataset.gitCommitExecutionReady === "true") return;
  workbench.dataset.gitCommitExecutionReady = "true";
  workbench.addEventListener("click", (event) => {
    const overlay = event.target?.closest?.("[data-git-commit-execution-pane]");
    if (overlay && event.target === overlay) {
      event.preventDefault();
      event.stopPropagation();
      gitProjectCommitCloseExecutionPane(workbench);
      return;
    }

    const button = event.target?.closest?.("[data-git-commit-action]");
    if (!button || !workbench.contains(button)) return;
    const action = button.dataset.gitCommitAction || "";
    if (!["create_local_commit", "do_git_commit", "stop_commit", "close_commit_execution"].includes(action)) return;
    event.preventDefault();
    event.stopPropagation();
    if (action === "create_local_commit") {
      gitProjectCommitOpenExecutionPane(workbench);
    } else if (action === "do_git_commit") {
      gitProjectCommitRunExecution(workbench).catch((error) => {
        gitProjectCommitSetExecutionRunning(workbench, false);
        gitProjectCommitSetExecutionStatus(workbench, `Commit run failed: ${error?.message || error}`);
        gitProjectCommitAppendExecutionLine(workbench, gitToolsOperationErrorText("Commit run failed", error));
      });
    } else if (action === "stop_commit") {
      gitProjectCommitStopExecution(workbench, "Stopped by user before the commit sequence completed.");
    } else if (action === "close_commit_execution") {
      gitProjectCommitCloseExecutionPane(workbench);
    }
  });
  workbench.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !workbench.querySelector("[data-git-commit-execution-pane]")?.hidden) {
      event.preventDefault();
      gitProjectCommitCloseExecutionPane(workbench);
    }
  });
  workbench.addEventListener("change", (event) => {
    if (event.target?.matches?.("[data-git-commit-stage-check], [data-git-commit-execution-option]")) {
      const paths = gitProjectCommitSelectedFilesFromWorkbench(workbench);
      gitProjectCommitUpdateReviewStatus(workbench, paths);
      gitProjectCommitUpdateFinalReadiness(workbench, paths);
    }
  });
  workbench.addEventListener("input", (event) => {
    if (event.target?.closest?.('[data-git-commit-field="commit_message"], [data-git-commit-field="branch"], [data-git-commit-field="git_user_name"], [data-git-commit-field="git_user_email"]')) {
      const paths = gitProjectCommitSelectedFilesFromWorkbench(workbench);
      gitProjectCommitUpdateReviewStatus(workbench, paths);
      gitProjectCommitUpdateFinalReadiness(workbench, paths);
    }
  });
}

function gitProjectCommitUpdateSelectedPreview(workbench, paths = null) {
  const selectedPaths = Array.isArray(paths) ? gitProjectCommitAdapterSelectedOutput(workbench, paths) : gitProjectCommitSelectedFilesFromWorkbench(workbench);
  const adapterReport = gitProjectCommitSelectionAdapterReport(workbench, selectedPaths);
  const preview = workbench.querySelector("[data-git-commit-selected-preview]");
  if (preview) preview.textContent = gitProjectCommitSelectedPreviewText(adapterReport.selectedPaths);
  workbench.dataset.gitCommitSelectedCount = String(adapterReport.selectedPaths.length);
  if (adapterReport.enabled) {
    workbench.dataset.gitCommitSelectionAdapter = adapterReport.controller || "McelFileBasketModel";
    workbench.dataset.gitCommitSelectionController = adapterReport.controller === "McelFileBasketController" ? "true" : "false";
    workbench.dataset.gitCommitAdapterSelectedCount = String(adapterReport.selectedPaths.length);
    workbench.dataset.gitCommitAdapterSelectionMatches = adapterReport.matches ? "true" : "normalized";
    if (adapterReport.summary) {
      workbench.dataset.gitCommitAdapterBlockedSelected = String(adapterReport.summary.selectedBlocked || 0);
      workbench.dataset.gitCommitAdapterInvalidSelected = String((adapterReport.summary.invalidSelectedPaths || []).length);
    }
  } else {
    delete workbench.dataset.gitCommitSelectionAdapter;
    delete workbench.dataset.gitCommitSelectionController;
    delete workbench.dataset.gitCommitAdapterSelectedCount;
    delete workbench.dataset.gitCommitAdapterSelectionMatches;
    delete workbench.dataset.gitCommitAdapterBlockedSelected;
    delete workbench.dataset.gitCommitAdapterInvalidSelected;
  }
  gitProjectCommitUpdateReviewStatus(workbench, adapterReport.selectedPaths);
  gitProjectCommitUpdateFinalReadiness(workbench, adapterReport.selectedPaths);
}

function gitProjectCommitStepTarget(workbench, stepId = "") {
  const selectorByStep = {
    repo_branch: "[data-git-commit-panel='repo_identity']",
    identity: "[data-git-commit-panel='repo_identity']",
    file_basket: "[data-git-commit-panel='file_basket']",
    stage_preview: "[data-git-commit-panel='stage_preview']",
    create_commit: "[data-git-commit-panel='create_commit']",
  };
  return workbench?.querySelector?.(selectorByStep[stepId] || `[data-git-commit-panel="${CSS.escape(stepId)}"]`);
}

function gitProjectCommitActivateStep(workbench, stepId = "") {
  workbench?.querySelectorAll?.("[data-git-commit-step]").forEach((step) => {
    step.classList.toggle("is-active", step.dataset.gitCommitStep === stepId);
  });
  const target = gitProjectCommitStepTarget(workbench, stepId);
  if (!target) return;
  target.scrollIntoView({behavior: "smooth", block: "center", inline: "nearest"});
  target.classList.remove("is-step-target");
  void target.offsetWidth;
  target.classList.add("is-step-target");
}

function gitProjectWireCommitStepNavigation(workbench) {
  if (!workbench || workbench.dataset.gitCommitStepNavigationReady === "true") return;
  workbench.dataset.gitCommitStepNavigationReady = "true";
  workbench.addEventListener("click", (event) => {
    const button = event.target?.closest?.("[data-git-commit-step-button]");
    if (!button || !workbench.contains(button)) return;
    event.preventDefault();
    gitProjectCommitActivateStep(workbench, button.dataset.gitCommitStepButton || "");
  });
  workbench.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const button = event.target?.closest?.("[data-git-commit-step-button]");
    if (!button || !workbench.contains(button)) return;
    event.preventDefault();
    gitProjectCommitActivateStep(workbench, button.dataset.gitCommitStepButton || "");
  });
}

function gitProjectCommitSizeWunderbaum(element) {
  if (!element) return;
  const applyToViewport = () => {
    const viewportHeight = Number(window.innerHeight || 720);
    const rect = element.getBoundingClientRect?.();
    const top = Number(rect?.top || 0);
    const desiredHeight = Math.max(420, Math.round(viewportHeight - top - 28));
    element.style.setProperty("height", `${desiredHeight}px`, "important");
    element.style.setProperty("min-height", "420px", "important");
    element.style.setProperty("max-height", "none", "important");
    element.style.setProperty("overflow-y", "auto", "important");
    element.style.setProperty("overflow-x", "hidden", "important");
    element.style.setProperty("--wb-row-outer-height", "22px", "important");
    element.style.setProperty("--wb-row-inner-height", "20px", "important");
    element.style.setProperty("background-color", "#010201", "important");
    element.style.setProperty("color", "var(--text)", "important");
    const listContainer = element.querySelector(".wb-list-container");
    const nodeList = element.querySelector(".wb-node-list");
    if (listContainer) {
      listContainer.style.setProperty("min-height", "0", "important");
      listContainer.style.setProperty("max-height", "none", "important");
      listContainer.style.setProperty("overflow", "visible", "important");
      listContainer.style.setProperty("background-color", "#010201", "important");
      listContainer.style.setProperty("color", "var(--text)", "important");
    }
    if (nodeList) {
      nodeList.style.setProperty("min-width", "0", "important");
      nodeList.style.setProperty("width", "100%", "important");
      nodeList.style.setProperty("overflow", "visible", "important");
      nodeList.style.setProperty("background-color", "#010201", "important");
      nodeList.style.setProperty("color", "var(--text)", "important");
    }
  };
  applyToViewport();
  window.requestAnimationFrame(applyToViewport);
  window.setTimeout(applyToViewport, 150);
}

function gitProjectCommitNotifyWunderbaumViewport(tree, change = "resize") {
  if (!tree?.update) return;
  try {
    tree.update(change, {immediate: true});
  } catch (error) {
    console.warn("Commit Wunderbaum viewport update skipped.", error);
  }
}

function gitProjectCommitScrollWunderbaumTop(element) {
  window.requestAnimationFrame(() => {
    if (!element) return;
    element.scrollTop = 0;
    element.dispatchEvent(new Event("scroll", {bubbles: true}));
  });
}

function gitProjectCommitUpdateFallbackParents(scope, workbench = null, selectedPaths = []) {
  if (!scope) return;
  const selected = new Set(gitProjectCommitSortSelectedPaths(selectedPaths));
  const nodes = Array.from(scope.querySelectorAll("[data-git-commit-tree-node='dir'], [data-git-commit-tree-node='group']")).reverse();
  nodes.forEach((node) => {
    const dirInput = node.querySelector(":scope > label input[data-git-commit-tree-checkbox='dir']");
    if (!dirInput || dirInput.disabled) return;
    const path = dirInput.dataset.gitCommitPath || "";
    const controllerState = workbench ? gitProjectCommitDirectorySelectionState(workbench, Array.from(selected), path) : "";
    if (controllerState) {
      dirInput.checked = controllerState === "all";
      dirInput.indeterminate = controllerState === "mixed";
      return;
    }
    const childInputs = Array.from(node.querySelectorAll(":scope > ul input[data-git-commit-tree-checkbox='file'], :scope > ul input[data-git-commit-tree-checkbox='dir']")).filter((input) => !input.disabled);
    if (!childInputs.length) return;
    const checked = childInputs.filter((input) => input.checked).length;
    const mixed = childInputs.some((input) => input.indeterminate);
    dirInput.checked = checked === childInputs.length && !mixed;
    dirInput.indeterminate = (checked > 0 && checked < childInputs.length) || mixed;
  });
}

function gitProjectCommitSyncFallbackSelection(workbench, selectedPaths = []) {
  const fallback = workbench?.querySelector?.("[data-git-commit-tree-fallback]");
  if (!fallback) return gitProjectCommitAdapterSelectedOutput(workbench, selectedPaths);
  const normalized = gitProjectCommitAdapterSelectedOutput(workbench, selectedPaths);
  const selected = new Set(normalized);
  const files = gitProjectCommitFlattenTreeFiles(gitProjectCommitReadTreeSource(workbench));
  const index = gitProjectCommitBuildFileIndex(files);
  fallback.querySelectorAll("[data-git-commit-tree-checkbox='file']").forEach((input) => {
    const path = gitProjectCommitCanonicalFilePath(input.dataset.gitCommitFile || input.dataset.gitCommitPath || input.value || "", index);
    input.checked = selected.has(path);
    input.indeterminate = false;
  });
  fallback.querySelectorAll("[data-git-commit-tree-checkbox='dir']").forEach((input) => {
    input.indeterminate = false;
  });
  gitProjectCommitUpdateFallbackParents(fallback, workbench, normalized);
  return normalized;
}

function gitProjectInitializeCommitFallbackTree(workbench) {
  const fallback = workbench.querySelector("[data-git-commit-tree-fallback]");
  if (!fallback || fallback.dataset.gitCommitFallbackReady === "true") return;
  fallback.dataset.gitCommitFallbackReady = "true";
  fallback.addEventListener("change", (event) => {
    const input = event.target?.closest?.("[data-git-commit-tree-checkbox]");
    if (!input) return;
    const currentPaths = gitProjectCommitRawSelectedFilesFromFallback(workbench);
    let selectedPaths = currentPaths;
    if (input.dataset.gitCommitTreeCheckbox === "dir") {
      const result = gitProjectCommitApplySelectionCommand(workbench, "set-directory-selection", {
        path: input.dataset.gitCommitPath || "",
        selected: input.checked
      }, currentPaths);
      selectedPaths = result.selectedPaths || currentPaths;
    } else if (input.dataset.gitCommitTreeCheckbox === "file") {
      const result = gitProjectCommitApplySelectionCommand(workbench, "set-file-selection", {
        path: input.dataset.gitCommitFile || input.dataset.gitCommitPath || "",
        selected: input.checked
      }, currentPaths);
      selectedPaths = result.selectedPaths || currentPaths;
    }
    const normalized = gitProjectCommitSyncFallbackSelection(workbench, selectedPaths);
    gitProjectCommitUpdateSelectedPreview(workbench, normalized);
  });
  const initialPaths = gitProjectCommitSelectedFilesFromWorkbench(workbench);
  gitProjectCommitSyncFallbackSelection(workbench, initialPaths);
  gitProjectCommitUpdateSelectedPreview(workbench, initialPaths);
}

function gitProjectInitializeCommitWunderbaum(workbench) {
  if (!workbench || workbench.dataset.gitCommitWorkbenchReady === "true") return;
  workbench.dataset.gitCommitWorkbenchReady = "true";
  gitProjectInitializeCommitFallbackTree(workbench);
  const element = workbench.querySelector("[data-git-commit-tree]");
  const sourceNode = workbench.querySelector("[data-git-commit-tree-source]");
  if (!element || !sourceNode) return;
  let source = [];
  try {
    source = JSON.parse(sourceNode.value || "[]");
  } catch (error) {
    element.textContent = "Could not parse commit file tree data.";
    element.classList.remove("wb-initializing");
    return;
  }
  gitProjectLoadWunderbaum()
    .then((Wunderbaum) => {
      if (!Wunderbaum || element.dataset.gitWunderbaumReady === "true") return;
      element.dataset.gitWunderbaumReady = "true";
      gitProjectCommitSizeWunderbaum(element);
      const tree = new Wunderbaum({
        id: `git-commit-${Math.random().toString(36).slice(2)}`,
        element,
        checkbox: (event) => gitProjectCommitCanSelectTreeNode(workbench, event.node),
        selectMode: "hier",
        types: {
          dir: {icon: "bi bi-folder", classes: "git-project-commit-tree-dir"},
          file: {icon: "bi bi-file-earmark-text", classes: "git-project-commit-tree-file"},
          empty: {icon: "bi bi-dash-circle", classes: "git-project-commit-tree-empty", checkbox: false, unselectable: true},
        },
        source: {children: source},
        beforeSelect: (event) => gitProjectCommitCanSelectTreeNode(workbench, event.node),
        tooltip: (event) => {
          const data = event.node?.data || {};
          return [data.path, data.groupTitle, data.reason, data.meta].filter(Boolean).join(" · ");
        },
        init: (event) => {
          event.tree.gitCommitWorkbench = workbench;
          element._wb_tree = event.tree;
          workbench.gitCommitWunderbaum = event.tree;
          event.tree.root?.fixSelection3FromEndNodes?.();
          gitProjectCommitUpdateSelectedPreview(workbench, gitProjectCommitSelectedFilesFromWunderbaum(event.tree));
          gitProjectCommitSizeWunderbaum(element);
          gitProjectCommitScrollWunderbaumTop(element);
          gitProjectCommitNotifyWunderbaumViewport(event.tree, "resize");
          gitProjectCommitNotifyWunderbaumViewport(event.tree, "scroll");
        },
        select: (event) => {
          event.tree.gitCommitWorkbench = workbench;
          element._wb_tree = event.tree;
          workbench.gitCommitWunderbaum = event.tree;
          gitProjectCommitUpdateSelectedPreview(workbench, gitProjectCommitSelectedFilesFromWunderbaum(event.tree));
        },
      });
      tree.gitCommitWorkbench = workbench;
      element._wb_tree = tree;
      workbench.gitCommitWunderbaum = tree;
      const fallback = workbench.querySelector("[data-git-commit-tree-fallback]");
      if (fallback) fallback.hidden = true;
      gitProjectCommitSizeWunderbaum(element);
      gitProjectCommitUpdateSelectedPreview(workbench, gitProjectCommitSelectedFilesFromWunderbaum(tree));
      gitProjectCommitScrollWunderbaumTop(element);
      gitProjectCommitNotifyWunderbaumViewport(tree, "resize");
      gitProjectCommitNotifyWunderbaumViewport(tree, "scroll");
      element.classList.remove("wb-initializing");
    })
    .catch(() => {
      element.textContent = "Wunderbaum could not be loaded. Using the built-in tri-state fallback selector below.";
      element.classList.remove("wb-initializing");
      workbench.dataset.gitCommitWunderbaumFallback = "true";
    });
}

function gitProjectInitializeCommitWorkbenches(container) {
  container?.querySelectorAll?.("[data-git-commit-workbench]").forEach((workbench) => {
    if (workbench.closest("[hidden]")) return;
    gitProjectWireCommitStepNavigation(workbench);
    gitProjectWireCommitExecution(workbench);
    gitProjectCommitUpdateSelectedPreview(workbench);
    gitProjectInitializeCommitWunderbaum(workbench);
  });
}

function gitProjectSecretsFilterRuleRowsHtml(rules = [], options = {}) {
  const list = Array.isArray(rules) ? rules.filter((rule) => rule && rule.id) : [];
  const interactive = options.interactive !== false;
  const namePrefix = options.namePrefix || (interactive ? "draft" : "saved");
  const emptyText = options.emptyText || "No security/privacy rules were returned by the backend.";
  if (!list.length) {
    return `<div class="git-project-secrets-empty">${escapeHtml(emptyText)}</div>`;
  }
  return `<div class="git-project-secrets-rule-list ${interactive ? "is-editable" : "is-readonly"}">
    ${list.map((rule = {}) => {
      const enabled = rule.enabled ? "checked" : "";
      const disabled = interactive ? "" : "disabled";
      const availability = rule.availability_status || (rule.available === false ? "unavailable" : "available");
      const scanState = rule.scan_state || (rule.ran ? "ran" : "pending");
      const findingCount = Number(rule.finding_count || 0);
      const rowClass = [
        "git-project-secrets-rule",
        interactive ? "is-editable" : "is-readonly",
        rule.enabled ? "is-enabled" : "is-disabled",
        rule.available === false ? "is-unavailable" : "is-available",
      ].join(" ");
      const meta = [
        `source: ${rule.source || "default"}`,
        `engine: ${rule.engine || "builtin"}`,
        `severity: ${rule.severity || "review"}`,
        availability,
        scanState,
        `findings: ${findingCount}`,
      ].filter(Boolean).join(" · ");
      return `<label class="${escapeHtml(rowClass)}">
        <input type="checkbox" name="${escapeHtml(namePrefix)}-${escapeHtml(rule.id)}" data-git-security-rule="${escapeHtml(rule.id)}" ${enabled} ${disabled}>
        <span>
          <strong>${escapeHtml(rule.id)}</strong>
          <small>${escapeHtml(meta)}</small>
          ${rule.description ? `<em>${escapeHtml(rule.description)}</em>` : ""}
          ${rule.install_hint ? `<em class="git-project-secrets-install-hint">${escapeHtml(rule.install_hint)}</em>` : ""}
        </span>
      </label>`;
    }).join("")}
  </div>`;
}
function gitProjectSecretsFindingsByRuleHtml(findings = [], rules = [], groupedFindings = []) {
  const explicitGroups = Array.isArray(groupedFindings) ? groupedFindings.filter((group) => group && group.rule_id) : [];
  const list = Array.isArray(findings) ? findings : [];
  const labels = new Map((Array.isArray(rules) ? rules : []).map((rule) => [String(rule.id || ""), rule.label || rule.id || "rule"]));
  let groups = explicitGroups.map((group = {}) => [String(group.rule_id || "unknown"), Array.isArray(group.findings) ? group.findings : []]);
  if (!groups.length && list.length) {
    const grouped = new Map();
    list.forEach((finding = {}) => {
      const ruleId = String(finding.rule_id || "unknown");
      if (!grouped.has(ruleId)) grouped.set(ruleId, []);
      grouped.get(ruleId).push(finding);
    });
    groups = Array.from(grouped.entries());
  }
  if (!groups.length) {
    return `<div class="git-project-secrets-empty">No findings have been returned yet. The backend filter check has not populated grouped findings.</div>`;
  }
  return `<div class="git-project-secrets-finding-groups">
    ${groups.map(([ruleId, group]) => `<section class="git-project-secrets-finding-group">
      <div class="git-project-subscreen-panel-head">
        <strong>${escapeHtml(ruleId)}</strong>
        <span>${group.length} finding${group.length === 1 ? "" : "s"}</span>
      </div>
      <p>${escapeHtml(labels.get(ruleId) || ruleId)}</p>
      <ul>
        ${group.slice(0, 30).map((finding = {}) => `<li>
          <code>${escapeHtml(finding.path || finding.file || "")}${finding.line ? `:${escapeHtml(finding.line)}` : ""}</code>
          <span>${escapeHtml([finding.severity || "", finding.kind || "", finding.evidence || finding.evidence_redacted || finding.message || ""].filter(Boolean).join(" · "))}</span>
        </li>`).join("")}
      </ul>
    </section>`).join("")}
  </div>`;
}
function gitProjectSecretsFilterContextJson(step = {}, model = {}) {
  const runtime = step.runtime || gitProjectRuntimeContext();
  const actionKey = gitProjectActionKey(step, "secrets_filter");
  const savedPolicyExists = Boolean(model.saved_policy_exists || model.policy?.exists);
  return JSON.stringify({
    step: {
      id: step.id || "secrets_filter",
      order: Number(step.order || 0),
      label: step.label || "Secrets / Filter",
      why: step.why || "",
      runtime,
    },
    panelState: gitProjectPanelStateForStep({...step, runtime}, actionKey),
    candidatePaths: Array.isArray(model.scan?.candidate_paths) ? model.scan.candidate_paths : [],
    secrets_filter: {
      policy: model.policy || {},
      policy_path: model.policy_path || ".git_dirty_rules.json",
      saved_policy_exists: savedPolicyExists,
      rules: Array.isArray(model.rules) ? model.rules : [],
      saved_rules: savedPolicyExists && Array.isArray(model.saved_rules) ? model.saved_rules : [],
      summary: model.summary || {},
      saved_summary: savedPolicyExists ? (model.saved_summary || {}) : {},
    },
  });
}
function gitProjectSecretsScanResultHtml(scanResult = {}, rules = []) {
  const findings = Array.isArray(scanResult.findings) ? scanResult.findings : [];
  const grouped = Array.isArray(scanResult.findings_by_rule) ? scanResult.findings_by_rule : [];
  const summary = scanResult.summary || {};
  const mode = scanResult.mode || "pending";
  const modeLabel = scanResult.label || (
    mode === "draft_selected_rules"
      ? "Draft selected-rule scan"
      : mode === "full_saved_policy"
        ? "Full saved policy scan"
        : mode === "policy_saved"
          ? "Policy saved; scan not run"
          : "No filter scan has run yet"
  );
  const sourceNote = mode === "draft_selected_rules"
    ? "Uses the current left-side checkbox choices. Not saved. Does not unblock commit."
    : mode === "full_saved_policy"
      ? "Uses .git_dirty_rules.json after backend merge. This is the commit gate result."
      : mode === "policy_saved"
        ? "The right-side saved policy was updated. Run the full saved filter check before commit."
        : "Review rule choices, then run a draft selected-rule scan or a full saved policy check.";
  return `<section class="git-project-secrets-results-panel">
    <div class="git-project-subscreen-panel-head">
      <strong>Scan results</strong>
      <span>${escapeHtml(modeLabel)} · gate ${escapeHtml(scanResult.gate_status || "pending")}</span>
    </div>
    <div class="git-project-secrets-result-banner mode-${escapeHtml(mode)}">
      <strong>${escapeHtml(modeLabel)}</strong>
      <span>${escapeHtml(sourceNote)}</span>
    </div>
    <div class="git-project-secrets-status-grid">
      ${renderKeyValue("Result source", modeLabel)}
      ${renderKeyValue("Status", scanResult.status || "pending")}
      ${renderKeyValue("Gate", scanResult.gate_status || "pending")}
      ${renderKeyValue("Findings", Number(summary.finding_count || findings.length || 0))}
      ${renderKeyValue("Blocking findings", Number(summary.blocking_finding_count || 0))}
      ${renderKeyValue("Candidate files", Number(summary.candidate_file_count || (Array.isArray(scanResult.candidate_paths) ? scanResult.candidate_paths.length : 0)))}
      ${renderKeyValue("Scanned files", Number(summary.scanned_file_count || 0))}
      ${renderKeyValue("detect-secrets", summary.detect_secrets_status || scanResult.detect_secrets?.status || "pending")}
    </div>
    ${scanResult.pending_message ? `<p class="git-project-muted">${escapeHtml(scanResult.pending_message)}</p>` : ""}
    ${scanResult.note ? `<p class="git-project-muted">${escapeHtml(scanResult.note)}</p>` : ""}
    <h4>Live scan events</h4>
    <div class="git-project-secrets-live-events" data-git-secrets-live-events>
      ${scanResult.status === "running" ? `<div>Scan subprocess started. Waiting for backend events…</div>` : ""}
    </div>
    <h4>Findings grouped by rule</h4>
    <div data-git-secrets-live-findings>
      ${gitProjectSecretsFindingsByRuleHtml(findings, rules, grouped)}
    </div>
  </section>`;
}
function gitProjectSecretsFilterWorkbenchHtml(step = {}) {
  const model = step.secrets_filter || {};
  const summary = model.summary || {};
  const savedPolicyExists = Boolean(model.saved_policy_exists || model.policy?.exists);
  const savedSummary = savedPolicyExists ? (model.saved_summary || {}) : {};
  const scan = model.scan || {};
  const scanResult = model.scan_result || scan || {};
  const rules = Array.isArray(model.rules) ? model.rules : [];
  const savedRules = savedPolicyExists && Array.isArray(model.saved_rules) ? model.saved_rules : [];
  const detectSecrets = savedRules.find((rule = {}) => rule.id === "detect_secrets") || {};
  const title = model.title || "SECRETS / FILTER";
  const contextJson = gitProjectSecretsFilterContextJson(step, model);
  return `<div class="git-project-secrets-filter-workbench" data-git-secrets-filter-workbench>
    <textarea hidden data-git-secrets-filter-model>${escapeHtml(contextJson)}</textarea>
    <section class="git-project-secrets-rules-panel">
      <div class="git-project-secrets-card-title">
        <strong>${escapeHtml(title)}</strong>
        <span>Draft rules: ${Number(summary.available_rule_count || 0)} available · ${Number(summary.enabled_rule_count || 0)} enabled · Saved rules: ${Number(savedSummary.available_rule_count || 0)} available · ${Number(savedSummary.enabled_rule_count || 0)} enabled</span>
      </div>
      <div class="git-project-subscreen-panel-head">
        <strong>Draft rule switches</strong>
        <span>${Number(summary.available_rule_count || 0)} available · ${Number(summary.enabled_rule_count || 0)} enabled</span>
      </div>
      <p class="git-project-muted">Edit the current checkbox choices here. These switches are a draft until you merge them into the backend policy.</p>
      <div class="git-project-secrets-policy-strip">
        <span>Draft source</span>
        <code>${model.draft_policy ? "current left-side choices" : "backend merged defaults"}</code>
        <span>${model.policy?.exists ? "saved policy loaded" : "default merge"}</span>
      </div>
      ${gitProjectSecretsFilterRuleRowsHtml(rules, {interactive: true, namePrefix: "draft"})}
      <div class="git-project-secrets-actions">
        <button type="button" data-git-secrets-action="merge_rule_choices">Merge rule choices</button>
        <button type="button" data-git-secrets-action="run_selected_rules">Run selected rules only</button>
      </div>
    </section>
    <section class="git-project-secrets-status-panel">
      <div class="git-project-subscreen-panel-head">
        <strong>Saved merged policy</strong>
        <span>${Number(savedSummary.available_rule_count || 0)} available · ${Number(savedSummary.enabled_rule_count || 0)} enabled</span>
      </div>
      <p class="git-project-muted">This right-side rule set exists only after .git_dirty_rules.json has been saved and merged with the backend catalog. Editing a saved switch writes .git_dirty_rules.json immediately; what is shown here is the saved file state. The full saved check uses this set.</p>
      <div class="git-project-secrets-policy-strip">
        <span>Policy</span>
        <code>${escapeHtml(model.policy_path || ".git_dirty_rules.json")}</code>
        <span>${savedPolicyExists ? "saved" : "not saved yet"}</span>
      </div>
      ${savedPolicyExists
        ? gitProjectSecretsFilterRuleRowsHtml(savedRules, {interactive: true, namePrefix: "saved", emptyText: "No saved merged rules were returned by the backend."})
        : `<div class="git-project-secrets-empty">No saved policy yet. Merge rule choices to create .git_dirty_rules.json before running the full saved filter check.</div>`}
      ${savedPolicyExists ? `<div class="git-project-secrets-detect-status ${detectSecrets.available === false ? "is-unavailable" : "is-available"}">
        <strong>detect-secrets</strong>
        <span>${escapeHtml(detectSecrets.availability_status || (detectSecrets.available === false ? "unavailable" : "available"))}</span>
        <p>${escapeHtml(detectSecrets.install_hint || "detect-secrets is represented as its own policy rule row.")}</p>
      </div>` : ""}
      <div class="git-project-secrets-actions">
        <button type="button" data-git-secrets-action="run_saved_filter_check" ${savedPolicyExists ? "" : "disabled"} title="${savedPolicyExists ? "" : "Merge rule choices before running the full saved filter check."}">Run full saved filter check</button>
      </div>
    </section>
    ${gitProjectSecretsScanResultHtml(scanResult, scanResult.rules || savedRules || [])}
  </div>`;
}
function gitProjectParseSecretsFilterContext(workbench) {
  const node = workbench?.querySelector("[data-git-secrets-filter-model]");
  if (!node) return {};
  try {
    return JSON.parse(node.value || node.textContent || "{}");
  } catch (_error) {
    return {};
  }
}
function gitProjectCollectSecretsRuleChoices(workbench, source = "draft") {
  const choices = {};
  const panelSelector = source === "saved"
    ? ".git-project-secrets-status-panel [data-git-security-rule]"
    : ".git-project-secrets-rules-panel [data-git-security-rule]";
  workbench?.querySelectorAll(panelSelector).forEach((input) => {
    const ruleId = input.dataset.gitSecurityRule || "";
    if (ruleId) choices[ruleId] = Boolean(input.checked);
  });
  return choices;
}
function gitProjectRefreshSecretsRuleVisualState(input) {
  const row = input?.closest(".git-project-secrets-rule");
  if (!row) return;
  row.classList.toggle("is-enabled", Boolean(input.checked));
  row.classList.toggle("is-disabled", !input.checked);
}
function gitProjectBindSecretsRuleVisualState(workbench) {
  if (!workbench) return;
  workbench.querySelectorAll("[data-git-security-rule]").forEach((input) => {
    gitProjectRefreshSecretsRuleVisualState(input);
    if (input.dataset.gitSecretsRuleVisualBound === "true") return;
    input.dataset.gitSecretsRuleVisualBound = "true";
    input.addEventListener("change", () => {
      gitProjectRefreshSecretsRuleVisualState(input);
    });
  });
}
function gitProjectReplaceSecretsFilterWorkbench(workbench, step, secretsFilter, data = {}) {
  if (!workbench || !secretsFilter) return null;
  const replacementStep = {
    ...step,
    runtime: step.runtime || {repo: data.repo || secretsFilter.repo || "."},
    secrets_filter: secretsFilter,
  };
  const parent = workbench.parentElement;
  workbench.outerHTML = gitProjectSecretsFilterWorkbenchHtml(replacementStep);
  const fresh = parent?.querySelector("[data-git-secrets-filter-workbench]") || document.querySelector("[data-git-secrets-filter-workbench]");
  gitProjectBindSecretsFilterActions(fresh?.closest("[data-git-project-card-subscreen]") || fresh);
  return fresh;
}
function gitProjectSecretsActionLabel(action = "") {
  if (action === "merge_rule_choices") return "Merge rule choices";
  if (action === "save_rule_choices") return "Merge rule choices";
  if (action === "update_saved_rule_choices") return "Save saved rule choice";
  if (action === "run_selected_rules") return "Run selected rules only";
  if (action === "run_saved_filter_check") return "Run full saved filter check";
  return "Secrets / Filter action";
}
function gitProjectAppendSecretsLiveEvent(workbench, event = {}) {
  const log = workbench?.querySelector("[data-git-secrets-live-events]");
  if (!log) return;
  const message = event.type === "finding"
    ? `finding · ${event.finding?.rule_id || "unknown"} · ${event.finding?.path || ""}${event.finding?.line ? `:${event.finding.line}` : ""}`
    : event.type === "file_scanned"
      ? `file · ${event.index || "?"}/${event.total || "?"} · ${event.path || ""} · ${event.status || "scanned"} · findings ${event.finding_count || 0}`
      : event.type === "rule_status"
        ? `rule · ${event.rule_id || ""} · ${event.status || ""}`
        : event.type === "finished"
          ? `finished · gate ${event.gate_status || "pending"} · findings ${event.finding_count || 0}`
          : event.message || event.label || event.status || event.type || "event";
  const row = document.createElement("div");
  row.textContent = message;
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}
function gitProjectAppendSecretsFinding(workbench, finding = {}) {
  const target = workbench?.querySelector("[data-git-secrets-live-findings]");
  if (!target) return;
  const ruleId = finding.rule_id || "unknown";
  const existing = Array.from(target.querySelectorAll("[data-git-secrets-live-rule]")).find((section) => section.dataset.gitSecretsLiveRule === ruleId);
  let list = existing?.querySelector("ul");
  if (!list) {
    if (target.querySelector(".git-project-secrets-empty")) target.innerHTML = "";
    const section = document.createElement("section");
    section.className = "git-project-secrets-finding-group";
    section.dataset.gitSecretsLiveRule = ruleId;
    section.innerHTML = `<div class="git-project-subscreen-panel-head"><strong>${escapeHtml(ruleId)}</strong><span>live findings</span></div><ul></ul>`;
    target.appendChild(section);
    list = section.querySelector("ul");
  }
  const item = document.createElement("li");
  const evidence = finding.evidence || finding.evidence_redacted || finding.message || "";
  item.innerHTML = `<code>${escapeHtml(finding.path || "")}${finding.line ? `:${escapeHtml(finding.line)}` : ""}</code> <span>${escapeHtml([finding.severity || "", finding.kind || "", evidence].filter(Boolean).join(" · "))}</span>`;
  list.appendChild(item);
}
function gitProjectApplySecretsScanEvent(workbench, event = {}) {
  if (!workbench) return;
  gitProjectAppendSecretsLiveEvent(workbench, event);
  if (event.type === "finding" && event.finding) {
    gitProjectAppendSecretsFinding(workbench, event.finding);
  }
  if (event.type === "started" || event.type === "queued") {
    const banner = workbench.querySelector(".git-project-secrets-result-banner");
    if (banner) {
      const strong = banner.querySelector("strong");
      const span = banner.querySelector("span");
      if (strong) strong.textContent = event.label || "Secrets / Filter scan running";
      if (span) span.textContent = `${event.type === "queued" ? "Queued" : "Running"} backend subprocess scan. Results stream here as they are produced.`;
    }
  }
  if ((event.type === "finished" || event.type === "error") && event.scan_result) {
    const resultPanel = workbench.querySelector(".git-project-secrets-results-panel");
    if (resultPanel) {
      const rules = Array.isArray(event.scan_result.rules) ? event.scan_result.rules : [];
      resultPanel.outerHTML = gitProjectSecretsScanResultHtml(event.scan_result, rules);
    }
    workbench.classList.remove("is-running");
    workbench.querySelectorAll("[data-git-secrets-action]").forEach((item) => {
      item.disabled = false;
    });
  }
}
function gitProjectStartSecretsFilterEventStream(workbench, data = {}) {
  const jobId = data.scan_job_id || "";
  const url = data.stream_url || (jobId ? `/api/applications/git/project/secrets-filter/stream?job_id=${encodeURIComponent(jobId)}` : "");
  if (!url || typeof EventSource === "undefined") {
    gitProjectAppendSecretsLiveEvent(workbench, {type: "error", message: "Browser streaming is not available for this scan."});
    return;
  }
  const stream = new EventSource(url);
  workbench.dataset.gitSecretsScanJobId = jobId;
  stream.addEventListener("scan", (event) => {
    let payload = {};
    try {
      payload = JSON.parse(event.data || "{}");
    } catch (_error) {
      payload = {type: "error", message: event.data || "Invalid scan event."};
    }
    gitProjectApplySecretsScanEvent(workbench, payload);
    if (["finished", "error", "stream_timeout"].includes(payload.type)) {
      stream.close();
      if (payload.type === "stream_timeout") {
        workbench.classList.remove("is-running");
        workbench.querySelectorAll("[data-git-secrets-action]").forEach((item) => {
          item.disabled = false;
        });
      }
      refreshGitStatus().catch(() => null);
    }
  });
  stream.onerror = () => {
    gitProjectAppendSecretsLiveEvent(workbench, {type: "error", message: "Secrets / Filter event stream disconnected."});
    stream.close();
    workbench.classList.remove("is-running");
    workbench.querySelectorAll("[data-git-secrets-action]").forEach((item) => {
      item.disabled = false;
    });
  };
}

async function runGitProjectSecretsFilterSavedChoiceUpdate(input) {
  const workbench = input?.closest("[data-git-secrets-filter-workbench]");
  if (!workbench) return;
  const context = gitProjectParseSecretsFilterContext(workbench);
  const step = context.step || {id: "secrets_filter", order: 0, label: "Secrets / Filter", why: ""};
  const panelState = context.panelState || {};
  const candidatePaths = Array.isArray(context.candidatePaths) ? context.candidatePaths : [];
  const ruleChoices = gitProjectCollectSecretsRuleChoices(workbench, "saved");
  const ruleId = input.dataset.gitSecurityRule || "";
  const previousChecked = input.dataset.gitSecretsLastChecked === "true";
  const action = "update_saved_rule_choices";
  const label = gitProjectSecretsActionLabel(action);
  const actionKey = `secrets_filter:${action}:${Number(step.order || 0)}`;
  const state = {
    ...panelState,
    repo: panelState.repo || step.runtime?.repo || gitRepoDir?.value || ".",
    action_id: "secrets_filter",
    secrets_filter_action: action,
    changed_rule_id: ruleId,
    changed_rule_enabled: Boolean(input.checked),
    rule_choices: ruleChoices,
    candidate_paths: candidatePaths,
    secrets_filter: {
      candidate_paths: candidatePaths,
    },
  };
  workbench.classList.add("is-saving");
  workbench.querySelectorAll(".git-project-secrets-status-panel [data-git-security-rule]").forEach((item) => {
    item.disabled = true;
  });
  try {
    const data = await gitToolsRequest("/api/applications/git/project/action/run", {
      action_key: actionKey,
      label,
      commands: [],
      state,
      repo_dir: state.repo || gitRepoDir?.value || ".",
    });
    showGitConsolePayload(data);
    if (data.secrets_filter) {
      gitProjectReplaceSecretsFilterWorkbench(workbench, step, data.secrets_filter, {
        repo: state.repo || ".",
      });
    } else {
      input.dataset.gitSecretsLastChecked = String(Boolean(input.checked));
      workbench.classList.remove("is-saving");
      workbench.querySelectorAll(".git-project-secrets-status-panel [data-git-security-rule]").forEach((item) => {
        item.disabled = false;
      });
    }
  } catch (error) {
    input.checked = previousChecked;
    gitProjectRefreshSecretsRuleVisualState(input);
    if (gitConsoleOutput) gitConsoleOutput.textContent = gitToolsOperationErrorText(`${label} failed`, error);
    workbench.classList.remove("is-saving");
    workbench.querySelectorAll(".git-project-secrets-status-panel [data-git-security-rule]").forEach((item) => {
      item.disabled = false;
    });
  }
}
function gitProjectBindSecretsSavedPolicyCheckboxes(workbench) {
  if (!workbench) return;
  workbench.querySelectorAll(".git-project-secrets-status-panel [data-git-security-rule]").forEach((input) => {
    input.dataset.gitSecretsLastChecked = String(Boolean(input.checked));
    if (input.dataset.gitSecretsSavedBound === "true") return;
    input.dataset.gitSecretsSavedBound = "true";
    input.addEventListener("change", (event) => {
      event.preventDefault();
      runGitProjectSecretsFilterSavedChoiceUpdate(input).catch((error) => {
        if (gitConsoleOutput) gitConsoleOutput.textContent = gitToolsOperationErrorText("Save saved rule choice failed", error);
      });
    });
  });
}

async function runGitProjectSecretsFilterAction(button) {
  const workbench = button?.closest("[data-git-secrets-filter-workbench]");
  if (!workbench) return;
  const action = button.dataset.gitSecretsAction || "";
  const context = gitProjectParseSecretsFilterContext(workbench);
  const step = context.step || {id: "secrets_filter", order: 0, label: "Secrets / Filter", why: ""};
  const panelState = context.panelState || {};
  const candidatePaths = Array.isArray(context.candidatePaths) ? context.candidatePaths : [];
  const ruleChoices = gitProjectCollectSecretsRuleChoices(workbench, action === "update_saved_rule_choices" ? "saved" : "draft");
  const label = gitProjectSecretsActionLabel(action);
  const actionKey = `secrets_filter:${action}:${Number(step.order || 0)}`;
  const state = {
    ...panelState,
    repo: panelState.repo || step.runtime?.repo || gitRepoDir?.value || ".",
    action_id: "secrets_filter",
    secrets_filter_action: action,
    rule_choices: ruleChoices,
    candidate_paths: candidatePaths,
    secrets_filter: {
      candidate_paths: candidatePaths,
    },
  };
  workbench.classList.add("is-running");
  workbench.querySelectorAll("[data-git-secrets-action]").forEach((item) => {
    item.disabled = true;
  });
  try {
    const data = await gitToolsRequest("/api/applications/git/project/action/run", {
      action_key: actionKey,
      label,
      commands: [],
      state,
      repo_dir: state.repo || gitRepoDir?.value || ".",
    });
    showGitConsolePayload(data);
    if (data.secrets_filter) {
      const fresh = gitProjectReplaceSecretsFilterWorkbench(workbench, step, data.secrets_filter, {
        repo: state.repo || ".",
      });
      if (fresh && data.scan_job_id) {
        gitProjectStartSecretsFilterEventStream(fresh, data);
      }
    }
    if (!data.scan_job_id) {
      await refreshGitStatus().catch(() => null);
    }
  } catch (error) {
    if (gitConsoleOutput) gitConsoleOutput.textContent = gitToolsOperationErrorText(`${label} failed`, error);
    workbench.classList.remove("is-running");
    workbench.querySelectorAll("[data-git-secrets-action]").forEach((item) => {
      item.disabled = false;
    });
  }
}
function gitProjectBindSecretsFilterActions(container) {
  if (!container) return;
  container.querySelectorAll("[data-git-secrets-filter-workbench]").forEach((workbench) => {
    gitProjectBindSecretsRuleVisualState(workbench);
    gitProjectBindSecretsSavedPolicyCheckboxes(workbench);
  });
  container.querySelectorAll("[data-git-secrets-action]").forEach((button) => {
    if (button.dataset.gitSecretsBound === "true") return;
    button.dataset.gitSecretsBound = "true";
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      runGitProjectSecretsFilterAction(button).catch((error) => {
        if (gitConsoleOutput) gitConsoleOutput.textContent = gitToolsOperationErrorText("Secrets / Filter action failed", error);
      });
    });
  });
}

function gitProjectArchiveRuntimePayload(workbench) {
  const project = currentGitProject() || {};
  return {
    project_id: workbench?.dataset.gitArchiveProjectId || project.id || "",
    project_path: workbench?.dataset.gitArchiveProjectPath || project.path || "",
    repo_dir: workbench?.dataset.gitArchiveRepo || project.path || gitRepoDir?.value || ".",
  };
}

function gitProjectArchiveWorkbenchHtml(step = {}) {
  const runtime = step.runtime || gitProjectRuntimeContext();
  const archive = step.archive_files || {};
  const defaultBranch = archive.default_branch || "archive/files";
  return `<div class="git-project-commit-workbench git-project-archive-workbench" data-git-archive-workbench data-git-archive-repo="${escapeHtml(runtime.repo || ".")}" data-git-archive-project-id="${escapeHtml(currentGitProject()?.id || "")}" data-git-archive-project-path="${escapeHtml(currentGitProject()?.path || runtime.repo || ".")}">
    <section class="git-project-commit-header">
      <div>
        <span class="git-project-commit-eyebrow">archive branch file mover</span>
        <h3>Archive Files...</h3>
        <p>Runs <code>git status</code> when opened, splits files into staged / unstaged / untracked groups, and archives selected paths before removing them from the active working tree.</p>
      </div>
      <div class="git-project-commit-ready-summary" data-git-archive-summary>Open card to load status.</div>
    </section>
    <section class="git-project-commit-compose git-project-archive-compose">
      <label>Archive branch<input data-git-archive-branch value="${escapeHtml(defaultBranch)}" placeholder="archive/files-YYYYMMDD-HHMMSS"></label>
      <label>Archive commit message<input data-git-archive-message value="archive: preserve selected files" placeholder="archive: preserve selected files"></label>
    </section>
    <section class="git-project-commit-basket-controls">
      <button type="button" data-git-archive-action="refresh">Refresh git status</button>
      <button type="button" data-git-archive-action="select-all">Select all</button>
      <button type="button" data-git-archive-action="select-none">Select none</button>
      <button type="button" data-git-archive-action="dry-run">Preview archive</button>
      <button type="button" class="danger" data-git-archive-action="archive">Archive selected files</button>
    </section>
    <section class="git-project-commit-center">
      <div class="git-project-archive-status" data-git-archive-status>Loading git status…</div>
      <div class="git-project-archive-groups" data-git-archive-groups></div>
    </section>
    <section class="git-project-commit-execution-pane-inline">
      <strong>Archive output</strong>
      <pre data-git-archive-output>Ready.</pre>
    </section>
  </div>`;
}

function gitProjectArchiveSelectedPaths(workbench) {
  return Array.from(workbench?.querySelectorAll("[data-git-archive-path]:checked") || [])
    .map((input) => input.dataset.gitArchivePath || "")
    .filter(Boolean);
}

function gitProjectArchiveGroupHtml(title, groupKey, items = []) {
  if (!items.length) {
    return `<section class="git-project-archive-group"><div class="git-project-subscreen-panel-head"><strong>${escapeHtml(title)}</strong><span>0 files</span></div><p>No files in this group.</p></section>`;
  }
  return `<section class="git-project-archive-group" data-git-archive-group="${escapeHtml(groupKey)}">
    <div class="git-project-subscreen-panel-head"><strong>${escapeHtml(title)}</strong><span>${items.length} file${items.length === 1 ? "" : "s"}</span></div>
    <div class="git-project-archive-file-list">
      ${items.map((item = {}) => `<label class="git-project-archive-file-row">
        <input type="checkbox" data-git-archive-path="${escapeHtml(item.path || "")}" data-git-archive-group="${escapeHtml(groupKey)}">
        <span><code>${escapeHtml(item.path || "")}</code><small>${escapeHtml(item.label || item.status_code || groupKey)}</small></span>
      </label>`).join("")}
    </div>
  </section>`;
}

function gitProjectArchiveRenderStatus(workbench, data = {}) {
  const groups = data.groups || {};
  const counts = data.counts || {};
  const summary = workbench?.querySelector("[data-git-archive-summary]");
  const container = workbench?.querySelector("[data-git-archive-groups]");
  const status = workbench?.querySelector("[data-git-archive-status]");
  if (summary) summary.textContent = `Staged ${counts.staged || 0} · Unstaged ${counts.unstaged || 0} · Untracked ${counts.untracked || 0}`;
  if (status) status.textContent = `Git status loaded from ${data.repo || "selected project"}.`;
  if (container) {
    container.innerHTML = [
      gitProjectArchiveGroupHtml("Changes to be committed", "staged", Array.isArray(groups.staged) ? groups.staged : []),
      gitProjectArchiveGroupHtml("Changes not staged for commit", "unstaged", Array.isArray(groups.unstaged) ? groups.unstaged : []),
      gitProjectArchiveGroupHtml("Untracked files", "untracked", Array.isArray(groups.untracked) ? groups.untracked : []),
    ].join("");
  }
}

async function gitProjectArchiveRefresh(workbench) {
  const status = workbench?.querySelector("[data-git-archive-status]");
  const output = workbench?.querySelector("[data-git-archive-output]");
  if (status) status.textContent = "Running git status…";
  const data = await gitToolsRequest("/api/applications/git/project/archive-files/status", gitProjectArchiveRuntimePayload(workbench));
  gitProjectArchiveRenderStatus(workbench, data);
  if (output) output.textContent = data.short_status || "No changed files.";
  return data;
}

async function gitProjectArchiveRun(workbench, dryRun = true) {
  const paths = gitProjectArchiveSelectedPaths(workbench);
  const output = workbench?.querySelector("[data-git-archive-output]");
  const status = workbench?.querySelector("[data-git-archive-status]");
  if (!paths.length) {
    if (status) status.textContent = "Select at least one file to archive.";
    return;
  }
  if (!dryRun && !window.confirm(`Archive ${paths.length} selected path(s) and remove them from this working branch?`)) {
    if (status) status.textContent = "Archive cancelled.";
    return;
  }
  const payload = {
    ...gitProjectArchiveRuntimePayload(workbench),
    paths,
    archive_branch: workbench?.querySelector("[data-git-archive-branch]")?.value || "",
    message: workbench?.querySelector("[data-git-archive-message]")?.value || "",
    dry_run: dryRun,
  };
  if (status) status.textContent = dryRun ? "Previewing archive operation…" : "Archiving selected files…";
  const data = await gitToolsRequest("/api/applications/git/project/archive-files", payload);
  if (output) output.textContent = JSON.stringify(data, null, 2);
  if (status) status.textContent = dryRun ? "Archive preview complete." : `Archived to ${data.archive_branch || "archive branch"}.`;
  if (!dryRun) {
    await gitProjectArchiveRefresh(workbench).catch(() => null);
    await refreshGitStatus().catch(() => null);
    await inspectSelectedGitProject({quiet: true}).catch(() => null);
  }
}

function gitProjectInitializeArchiveWorkbenches(container) {
  container?.querySelectorAll?.("[data-git-archive-workbench]").forEach((workbench) => {
    if (workbench.dataset.gitArchiveReady === "true") return;
    workbench.dataset.gitArchiveReady = "true";
    workbench.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-git-archive-action]");
      if (!button || !workbench.contains(button)) return;
      event.preventDefault();
      event.stopPropagation();
      const action = button.dataset.gitArchiveAction || "";
      if (action === "refresh") {
        gitProjectArchiveRefresh(workbench).catch((error) => {
          const output = workbench.querySelector("[data-git-archive-output]");
          if (output) output.textContent = gitToolsOperationErrorText("Archive status failed", error);
        });
      } else if (action === "select-all") {
        workbench.querySelectorAll("[data-git-archive-path]").forEach((input) => { input.checked = true; });
      } else if (action === "select-none") {
        workbench.querySelectorAll("[data-git-archive-path]").forEach((input) => { input.checked = false; });
      } else if (action === "dry-run") {
        gitProjectArchiveRun(workbench, true).catch((error) => {
          const output = workbench.querySelector("[data-git-archive-output]");
          if (output) output.textContent = gitToolsOperationErrorText("Archive dry run failed", error);
        });
      } else if (action === "archive") {
        gitProjectArchiveRun(workbench, false).catch((error) => {
          const output = workbench.querySelector("[data-git-archive-output]");
          if (output) output.textContent = gitToolsOperationErrorText("Archive failed", error);
        });
      }
    });
    gitProjectArchiveRefresh(workbench).catch((error) => {
      const output = workbench.querySelector("[data-git-archive-output]");
      const status = workbench.querySelector("[data-git-archive-status]");
      if (status) status.textContent = "Archive status failed.";
      if (output) output.textContent = gitToolsOperationErrorText("Archive status failed", error);
    });
  });
}

function gitProjectCommitWorkbenchHtml(step = {}) {
  const review = step.commit_review || {};
  const runtime = step.runtime || gitProjectRuntimeContext();
  return `<div class="git-project-commit-workbench" data-git-commit-workbench data-git-commit-repo="${escapeHtml(runtime.repo || ".")}">
    ${gitProjectCommitHeaderHtml(review)}
    <div class="git-project-commit-body">
      ${gitProjectCommitStepsHtml(review)}
      ${gitProjectCommitCenterHtml(step, "file_basket")}
      ${gitProjectCommitBasketHtml(review)}
    </div>
  </div>`;
}
function gitProjectCardSubscreenHtml(step = {}, actionKey = "") {
  if (!gitProjectStepSupportsCardSubscreen(step)) return "";
  const stepId = gitProjectStepId(step);
  const isGitignore = stepId === "update_gitignore_before_initial_commit" || (step.gitignore_file && (Array.isArray(step.ignore_rules) || Array.isArray(step.questionable_ignore_rules)));
  const isSecretsFilter = stepId === "secrets_filter";
  const isCommit = gitProjectStepIsCommitCard(step);
  const isArchive = gitProjectStepIsArchiveCard(step);
  const isPathList = !isCommit && !isArchive && !isGitignore && Array.isArray(step.paths) && step.paths.length;
  const dialogLabel = isCommit ? gitProjectCommitCardTitle(step) : (isArchive ? gitProjectArchiveCardTitle(step) : (step.label || "Git project card"));

  const body = isSecretsFilter
    ? gitProjectSecretsFilterWorkbenchHtml(step)
    : isGitignore
      ? gitProjectIgnoreWorkbenchHtml(step)
      : isCommit
        ? gitProjectCommitWorkbenchHtml(step)
        : isArchive
          ? gitProjectArchiveWorkbenchHtml(step)
          : isPathList
          ? `<section class="git-project-subscreen-panel">
              <div class="git-project-subscreen-panel-head">
                <strong>${escapeHtml(step.label || "Paths")}</strong>
                <span>${Number(Array.isArray(step.paths) ? step.paths.length : 0)} path${Array.isArray(step.paths) && step.paths.length === 1 ? "" : "s"}</span>
              </div>
              <p>${escapeHtml(step.why || "")}</p>
              ${gitProjectPathChips(step.paths || [], 80)}
            </section>`
          : `<section class="git-project-subscreen-panel">
              <div class="git-project-subscreen-panel-head">
                <strong>Card details</strong>
                <span>${escapeHtml(step.state || "planned")}</span>
              </div>
              <p>${escapeHtml(step.why || "")}</p>
            </section>`;

  return `<div class="git-project-card-subscreen-backdrop" data-git-project-card-subscreen="${escapeHtml(actionKey)}" aria-hidden="true" hidden>
    <section class="git-project-card-subscreen" role="dialog" aria-modal="true" aria-label="${escapeHtml(dialogLabel || "Git project card")}">
      <header class="git-project-card-subscreen-header">
        <div>
          <strong>${escapeHtml(dialogLabel || "Git project card")}</strong>
          <span>${escapeHtml(step.why || "")}</span>
        </div>
        <button type="button" class="git-project-card-subscreen-close" data-git-project-close-card="${escapeHtml(actionKey)}">Close</button>
      </header>
      <div class="git-project-card-subscreen-body ${isSecretsFilter ? "is-secrets-filter" : isGitignore ? "is-gitignore" : isCommit ? "is-commit" : isArchive ? "is-archive-files" : isPathList ? "is-path-list" : ""}">
        ${body}
      </div>
    </section>
  </div>`;
}
function gitProjectRefreshIgnoreRulePreview(scope) {
  if (!scope) return;
  const preview = scope.querySelector("[data-git-ignore-selected-preview]");
  if (!preview) return;
  const selected = Array.from(scope.querySelectorAll("[data-git-ignore-rule]:checked"))
    .map((input) => input.dataset.gitIgnoreRule || "")
    .filter(Boolean);
  preview.textContent = selected.length ? selected.join("\n") : "# No ignore rules selected.";
}
function gitProjectParseGitignoreBaseline(workbench) {
  try {
    const parsed = JSON.parse(workbench?.dataset.gitignoreBaseline || "[]");
    return Array.isArray(parsed) ? parsed.map((line) => gitProjectNormalizeGitignoreLineText(line)) : [];
  } catch (_error) {
    return [];
  }
}
function gitProjectGitignoreRows(workbench) {
  return Array.from(workbench?.querySelectorAll("[data-gitignore-line-row]") || []);
}
function gitProjectGitignoreRowText(row) {
  return gitProjectNormalizeGitignoreLineText(row?.querySelector("code")?.textContent || "");
}
function gitProjectGitignoreCheckedLines(workbench) {
  return gitProjectGitignoreRows(workbench)
    .filter((row) => row.querySelector("input[type='checkbox']")?.checked)
    .map((row) => gitProjectGitignoreRowText(row));
}
function gitProjectGitignoreLinesEqual(left = [], right = []) {
  if (left.length !== right.length) return false;
  return left.every((line, index) => line === right[index]);
}
function gitProjectFindGitignoreRightRow(workbench, ruleText = "") {
  const wanted = gitProjectNormalizeGitignoreMatchText(ruleText);
  if (!wanted) return null;
  return gitProjectGitignoreRows(workbench).find((row) => gitProjectNormalizeGitignoreMatchText(gitProjectGitignoreRowText(row)) === wanted) || null;
}
function gitProjectSyncGitignoreLeftRule(workbench, ruleText = "", checked = false) {
  const wanted = gitProjectNormalizeGitignoreMatchText(ruleText);
  if (!wanted) return;
  workbench.querySelectorAll("[data-git-ignore-rule]").forEach((input) => {
    if (gitProjectNormalizeGitignoreMatchText(input.dataset.gitIgnoreRule || "") === wanted) {
      input.checked = checked;
      input.closest(".git-project-ignore-rule")?.classList.toggle("is-linked", checked);
    }
  });
}
function gitProjectUpdateGitignoreEmptyMessage(workbench) {
  const lines = workbench?.querySelector("[data-gitignore-lines]");
  const empty = lines?.querySelector("[data-gitignore-empty-message]");
  if (empty) empty.hidden = gitProjectGitignoreRows(workbench).length > 0;
}
function gitProjectSetGitignoreRowChecked(workbench, row, checked) {
  if (!row) return;
  const input = row.querySelector("input[type='checkbox']");
  if (input) input.checked = checked;
  row.classList.toggle("is-deleted", !checked);
  row.classList.toggle("is-pending", checked && row.dataset.gitignoreLineState === "pending");
  const text = gitProjectGitignoreRowText(row);
  if (text.trim()) gitProjectSyncGitignoreLeftRule(workbench, text, checked);
}
function gitProjectAppendGitignoreRightRow(workbench, text = "", state = "pending") {
  const container = workbench?.querySelector("[data-gitignore-lines]");
  if (!container) return null;
  const empty = container.querySelector("[data-gitignore-empty-message]");
  const html = gitProjectGitignoreLineRowHtml(text, 0, state, true);
  if (empty) empty.insertAdjacentHTML("beforebegin", html);
  else container.insertAdjacentHTML("beforeend", html);
  gitProjectUpdateGitignoreEmptyMessage(workbench);
  return container.querySelector("[data-gitignore-line-row]:last-of-type") || gitProjectGitignoreRows(workbench).at(-1) || null;
}
function gitProjectApplyIgnoreRuleToRightPane(workbench, input) {
  if (!workbench || !input) return;
  const rule = gitProjectNormalizeGitignoreMatchText(input.dataset.gitIgnoreRule || "");
  if (!rule) {
    gitProjectUpdateGitignoreDirtyState(workbench);
    return;
  }
  let row = gitProjectFindGitignoreRightRow(workbench, rule);
  if (input.checked) {
    if (!row) row = gitProjectAppendGitignoreRightRow(workbench, rule, "pending");
    gitProjectSetGitignoreRowChecked(workbench, row, true);
  } else if (row) {
    gitProjectSetGitignoreRowChecked(workbench, row, false);
  }
  gitProjectUpdateGitignoreDirtyState(workbench);
}
function gitProjectUpdateGitignoreDirtyState(workbench) {
  if (!workbench) return false;
  const baseline = gitProjectParseGitignoreBaseline(workbench);
  const current = gitProjectGitignoreCheckedLines(workbench);
  const dirty = !gitProjectGitignoreLinesEqual(current, baseline);
  const unreadExisting = workbench.dataset.gitignoreExistingUnread === "true";
  workbench.dataset.gitignoreDirty = dirty ? "true" : "false";
  workbench.classList.toggle("is-dirty", dirty);
  const saveButton = workbench.querySelector("[data-gitignore-save]");
  if (saveButton) saveButton.disabled = unreadExisting || !dirty || saveButton.dataset.gitignoreSaving === "true";
  const message = workbench.querySelector("[data-gitignore-dirty-message]");
  if (message) message.textContent = dirty ? "Unsaved .gitignore changes" : "No unsaved .gitignore changes";
  return dirty;
}
function gitProjectRenderSavedGitignoreRows(workbench, lines = []) {
  const container = workbench?.querySelector("[data-gitignore-lines]");
  if (!container) return;
  const rows = Array.isArray(lines) ? lines.map((line, index) => gitProjectGitignoreLineRowHtml(line, index + 1, "saved", true)).join("") : "";
  container.innerHTML = `${rows}<div class="git-project-gitignore-empty" data-gitignore-empty-message ${rows ? "hidden" : ""}>.gitignore exists but appears to be empty.</div>`;
  gitProjectUpdateGitignoreEmptyMessage(workbench);
}
async function gitProjectSaveGitignoreWorkbench(workbench) {
  if (!workbench) return;
  const saveButton = workbench.querySelector("[data-gitignore-save]");
  const status = workbench.querySelector("[data-gitignore-save-status]");
  const lines = gitProjectGitignoreCheckedLines(workbench);
  const projectId = gitProjectLastInspection?.project?.id || currentGitProject()?.id || "";
  const payload = {
    project_id: projectId,
    path: workbench.dataset.gitignorePath || ".gitignore",
    lines,
    newline: workbench.dataset.gitignoreNewline || "lf",
  };
  if (saveButton) {
    saveButton.disabled = true;
    saveButton.dataset.gitignoreSaving = "true";
  }
  if (status) status.textContent = "Saving .gitignore…";
  try {
    const data = await gitToolsRequest("/api/applications/git/project/gitignore/save", payload);
    const gitignoreFile = data.gitignore_file || {};
    const savedLines = Array.isArray(gitignoreFile.lines)
      ? gitignoreFile.lines.map((line = {}) => gitProjectNormalizeGitignoreLineText(line.text ?? line))
      : lines;
    workbench.dataset.gitignoreBaseline = JSON.stringify(savedLines);
    workbench.dataset.gitignoreNewline = gitignoreFile.newline || payload.newline || "lf";
    workbench.dataset.gitignorePath = gitignoreFile.path || payload.path || ".gitignore";
    gitProjectRenderSavedGitignoreRows(workbench, savedLines);
    workbench.querySelectorAll("[data-git-ignore-rule]").forEach((input) => {
      const rule = gitProjectNormalizeGitignoreMatchText(input.dataset.gitIgnoreRule || "");
      input.checked = !!rule && savedLines.some((line) => gitProjectNormalizeGitignoreMatchText(line) === rule);
      input.closest(".git-project-ignore-rule")?.classList.toggle("is-linked", input.checked);
    });
    gitProjectRefreshIgnoreRulePreview(workbench.closest("[data-git-project-card-subscreen]") || workbench);
    gitProjectUpdateGitignoreDirtyState(workbench);
    if (status) status.textContent = "Saved .gitignore.";
  } catch (error) {
    if (status) status.textContent = gitToolsOperationErrorText("Save .gitignore failed", error);
    gitProjectUpdateGitignoreDirtyState(workbench);
  } finally {
    if (saveButton) {
      delete saveButton.dataset.gitignoreSaving;
      gitProjectUpdateGitignoreDirtyState(workbench);
    }
  }
}
function gitProjectHandleGitignoreRightChange(workbench, input) {
  const row = input?.closest("[data-gitignore-line-row]");
  if (!row) return;
  gitProjectSetGitignoreRowChecked(workbench, row, !!input.checked);
  gitProjectUpdateGitignoreDirtyState(workbench);
  gitProjectRefreshIgnoreRulePreview(workbench.closest("[data-git-project-card-subscreen]") || workbench);
}
function gitProjectInitializeGitignoreWorkbench(workbench) {
  if (!workbench || workbench.dataset.gitignoreBound === "true") return;
  workbench.dataset.gitignoreBound = "true";
  workbench.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.matches("[data-git-ignore-rule]")) {
      gitProjectApplyIgnoreRuleToRightPane(workbench, target);
      gitProjectRefreshIgnoreRulePreview(workbench.closest("[data-git-project-card-subscreen]") || workbench);
    } else if (target.matches("[data-gitignore-existing-line]")) {
      gitProjectHandleGitignoreRightChange(workbench, target);
    }
  });
  workbench.addEventListener("input", (event) => {
    if (event.target?.matches?.("[data-gitignore-line-row] code")) {
      gitProjectUpdateGitignoreDirtyState(workbench);
    }
  });
  workbench.querySelector("[data-gitignore-save]")?.addEventListener("click", (event) => {
    event.preventDefault();
    gitProjectSaveGitignoreWorkbench(workbench).catch((error) => {
      const status = workbench.querySelector("[data-gitignore-save-status]");
      if (status) status.textContent = gitToolsOperationErrorText("Save .gitignore failed", error);
    });
  });
  if (workbench.dataset.gitignoreExistingUnread === "true") {
    const status = workbench.querySelector("[data-gitignore-save-status]");
    if (status) status.textContent = ".gitignore exists, but its contents were not loaded; saving is disabled to avoid overwriting unread file content.";
  } else {
    workbench.querySelectorAll("[data-git-ignore-rule]:checked").forEach((input) => {
      gitProjectApplyIgnoreRuleToRightPane(workbench, input);
    });
  }
  gitProjectUpdateGitignoreDirtyState(workbench);
  gitProjectUpdateGitignoreEmptyMessage(workbench);
}
function gitProjectInitializeGitignoreWorkbenches(container) {
  container?.querySelectorAll(".git-project-gitignore-workbench").forEach((workbench) => {
    gitProjectInitializeGitignoreWorkbench(workbench);
  });
  gitProjectEnsureGitignoreBeforeUnloadGuard();
}
function gitProjectDirtyGitignoreWorkbenches(container = document) {
  return Array.from(container.querySelectorAll?.(".git-project-gitignore-workbench[data-gitignore-dirty='true']") || []);
}
function gitProjectConfirmDiscardGitignoreChanges(subscreen) {
  if (!subscreen || !gitProjectDirtyGitignoreWorkbenches(subscreen).length) return true;
  return window.confirm("Discard unsaved .gitignore changes?");
}
function gitProjectEnsureGitignoreBeforeUnloadGuard() {
  if (window.gitProjectGitignoreBeforeUnloadBound === true) return;
  window.gitProjectGitignoreBeforeUnloadBound = true;
  window.addEventListener("beforeunload", (event) => {
    if (!gitProjectDirtyGitignoreWorkbenches(document).length) return;
    event.preventDefault();
    event.returnValue = "";
  });
}
function gitProjectOpenCardSubscreen(actionKey = "") {
  const current = document.querySelector("[data-git-project-card-subscreen]:not([hidden])");
  if (current && current.dataset.gitProjectCardSubscreen !== actionKey && !gitProjectConfirmDiscardGitignoreChanges(current)) return false;
  const subscreen = document.querySelector(gitProjectCardSelector("data-git-project-card-subscreen", actionKey));
  if (!subscreen) return false;
  subscreen.hidden = false;
  subscreen.setAttribute("aria-hidden", "false");
  gitProjectRefreshIgnoreRulePreview(subscreen);
  gitProjectInitializeGitignoreWorkbenches(subscreen);
  gitProjectInitializeCommitWorkbenches(subscreen);
  const close = subscreen.querySelector("[data-git-project-close-card]");
  if (close) close.focus();
  return true;
}
function gitProjectCloseCardSubscreen(actionKey = "", options = {}) {
  const subscreen = document.querySelector(gitProjectCardSelector("data-git-project-card-subscreen", actionKey));
  if (!subscreen) return false;
  if (!options.force && !gitProjectConfirmDiscardGitignoreChanges(subscreen)) return false;
  subscreen.hidden = true;
  subscreen.setAttribute("aria-hidden", "true");
  const opener = document.querySelector(gitProjectCardSelector("data-git-project-open-card", actionKey));
  if (opener) opener.focus();
  return true;
}
function bindGitProjectCardSubscreen(container) {
  if (!container) return;
  container.querySelectorAll("[data-git-project-open-card]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      gitProjectOpenCardSubscreen(button.dataset.gitProjectOpenCard || "");
    });
  });
  container.querySelectorAll("[data-git-project-close-card]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      gitProjectCloseCardSubscreen(button.dataset.gitProjectCloseCard || "");
    });
  });
  container.querySelectorAll("[data-git-project-card-subscreen]").forEach((backdrop) => {
    backdrop.addEventListener("click", (event) => {
      if (event.target === backdrop) {
        event.preventDefault();
        event.stopPropagation();
        gitProjectCloseCardSubscreen(backdrop.dataset.gitProjectCardSubscreen || "");
      }
    });
  });
  container.querySelectorAll("[data-git-project-card-shell]").forEach((card) => {
    card.addEventListener("click", (event) => {
      if (event.target.closest("button, a, input, textarea, select, details, summary, code[contenteditable='true']")) return;
      const actionKey = card.dataset.gitProjectCardShell || "";
      if (actionKey) gitProjectOpenCardSubscreen(actionKey);
    });
  });
  container.querySelectorAll("[data-git-ignore-select]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      const scope = button.closest("[data-git-project-card-subscreen]");
      const workbench = scope?.querySelector(".git-project-gitignore-workbench");
      const mode = button.dataset.gitIgnoreSelect || "safe";
      scope?.querySelectorAll("[data-git-ignore-rule]").forEach((input) => {
        const tone = input.dataset.gitIgnoreRuleTone || "safe";
        input.checked = mode === "all" || (mode === "safe" && tone === "safe");
        if (mode === "none") input.checked = false;
        if (workbench) gitProjectApplyIgnoreRuleToRightPane(workbench, input);
      });
      gitProjectRefreshIgnoreRulePreview(scope);
      if (workbench) gitProjectUpdateGitignoreDirtyState(workbench);
    });
  });
  gitProjectInitializeGitignoreWorkbenches(container);
  gitProjectBindSecretsFilterActions(container);
  gitProjectInitializeCommitWorkbenches(container);
  gitProjectInitializeArchiveWorkbenches(container);
}
function renderGitProjectInspection(data) {
  const project = data.project || {};
  const git = data.git || {};
  const dirty = data.dirty_plan || {};
  const summary = dirty.summary || {};
  const wizard = data.wizard || {};
  renderGitProjectNextStep(data);
  if (gitProjectDashboard) {
    gitProjectDashboard.innerHTML = `<div class="git-project-report" ${gitProjectMcComponentAttrs("git-tools.projects.dashboard.report", "output", "Prioritized Project Report", "git-tools.projects.dashboard")}>
      <div class="git-project-report-heading" ${gitProjectMcComponentAttrs("git-tools.projects.report.heading", "status", "Prioritized Project Report Heading", "git-tools.projects.dashboard.report")}>
        <strong data-mc-component-id="git-tools.projects.report.title" data-mc-component-kind="status" data-mc-component-label="Prioritized Project Report Title" data-mc-component-owner="git-tools.projects.report.heading" data-mc-feature-id="git-tools.feature.projects">Prioritized project report</strong>
        <span class="git-project-report-copy" data-mc-component-id="git-tools.projects.report.copy" data-mc-component-kind="status" data-mc-component-label="Prioritized Project Report Copy" data-mc-component-owner="git-tools.projects.report.heading" data-mc-feature-id="git-tools.feature.projects">This section is informational. The single wizard activity queue below owns runnable buttons, backend action requests, command previews, status, and history.</span>
        <code data-mc-component-id="git-tools.projects.report.command" data-mc-component-kind="output" data-mc-component-label="Prioritized Project Report Command" data-mc-component-owner="git-tools.projects.report.heading" data-mc-feature-id="git-tools.feature.projects">python git_dirty.py plan --repo "${escapeHtml(project.path || data.selected_project || ".")}" --json --include-actions</code>
      </div>
      <details class="git-project-report-section" ${gitProjectMcComponentAttrs("git-tools.projects.report.raw-details", "panel", "Raw Report Details", "git-tools.projects.dashboard.report")}>
        <summary data-mc-component-id="git-tools.projects.report.raw-details.summary" data-mc-component-kind="status" data-mc-component-label="Raw Report Details Summary" data-mc-component-owner="git-tools.projects.report.raw-details" data-mc-feature-id="git-tools.feature.projects">Raw report details</summary>
        <div class="git-project-report-grid" data-mc-component-id="git-tools.projects.report.raw-details.grid" data-mc-component-kind="list" data-mc-component-label="Raw Report Details Grid" data-mc-component-owner="git-tools.projects.report.raw-details" data-mc-feature-id="git-tools.feature.projects">
          ${renderKeyValue("Selected project", `${project.vip ? "★ " : ""}${project.name || "Selected project"}`)}
          ${renderKeyValue("Protection", `${project.vip ? "VIP · " : ""}${project.locked ? "Locked" : "Unlocked"}${project.can_archive === false ? " · cannot archive" : ""}`)}
          ${renderKeyValue("Git root", git.git_root || "not detected")}
          ${renderKeyValue("Branch", git.branch || "(none)")}
          ${renderKeyValue("HEAD", git.is_git_repo ? (git.has_head ? "exists" : "missing") : "not applicable")}
          ${renderKeyValue("Dirty score", `${dirty.dirty_score ?? 0} / 100 (${dirty.level || "unknown"})`)}
          ${renderKeyValue("Strategy", dirty.recommended_strategy || wizard.strategy || "review")}
          ${renderKeyValue("Classification", `Source ${summary.source ?? 0} · Generated ${summary.generated ?? 0} · Untracked ${summary.untracked ?? 0}`)}
        </div>
      </details>
      <details class="git-project-report-section" ${gitProjectMcComponentAttrs("git-tools.projects.report.classification", "panel", "Classification Summary", "git-tools.projects.dashboard.report")}>
        <summary data-mc-component-id="git-tools.projects.report.classification.summary" data-mc-component-kind="status" data-mc-component-label="Classification Summary Toggle" data-mc-component-owner="git-tools.projects.report.classification" data-mc-feature-id="git-tools.feature.projects">Classification summary</summary>
        <div class="git-project-summary-grid" data-mc-component-id="git-tools.projects.report.classification.grid" data-mc-component-kind="list" data-mc-component-label="Classification Summary Grid" data-mc-component-owner="git-tools.projects.report.classification" data-mc-feature-id="git-tools.feature.projects">${dirtySummaryRows(summary) || "<span>No summary returned.</span>"}</div>
      </details>
      <details class="git-project-report-section" ${gitProjectMcComponentAttrs("git-tools.projects.report.plan-metadata", "panel", "Raw Plan Metadata", "git-tools.projects.dashboard.report")}>
        <summary data-mc-component-id="git-tools.projects.report.plan-metadata.summary" data-mc-component-kind="status" data-mc-component-label="Raw Plan Metadata Toggle" data-mc-component-owner="git-tools.projects.report.plan-metadata" data-mc-feature-id="git-tools.feature.projects">Raw plan metadata</summary>
        <pre data-mc-component-id="git-tools.projects.report.plan-metadata.output" data-mc-component-kind="output" data-mc-component-label="Raw Plan Metadata Output" data-mc-component-owner="git-tools.projects.report.plan-metadata" data-mc-feature-id="git-tools.feature.projects">${escapeHtml(JSON.stringify({
          plan_id: dirty.plan_id || wizard.plan_id || "",
          recommended_strategy: dirty.recommended_strategy || wizard.strategy || "",
          repo: git.git_root || data.selected_project || "",
          app_root: data.app_root || "",
        }, null, 2))}</pre>
      </details>
    </div>`;
    bindGitProjectActionButtons(gitProjectDashboard);
  }
  renderGitProjectWizard(wizard, data);
}

const GIT_PROJECT_WIZARD_HIDDEN_ACTION_IDS = new Set([
  "save_current_state",
  "push_current_branch_to_local_server",
  "inspect_configured_remotes",
  "remove_untracked_generated_files",
]);
const GIT_PROJECT_WIZARD_HIDDEN_ACTION_LABELS = new Set([
  "save current state",
  "push current branch to local server",
  "push to local gitea",
  "inspect configured remotes",
  "remove generated untracked files",
]);
const GIT_PROJECT_GITIGNORE_REVIEW_IDS = new Set([
  "ignore_generated_files",
  "ignore_local_environment_files",
]);
const GIT_PROJECT_GITIGNORE_REVIEW_LABELS = new Set([
  "ignore generated files",
  "ignore local environment files",
]);
const GIT_PROJECT_SECRETS_FILTER_LABELS = new Set([
  "secrets / filter",
  "security / secrets",
  "review security / secrets",
]);
function gitProjectNormalizedWizardLabel(value = "") {
  return String(value || "")
    .replace(/^\s*\d+\.\s*/, "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
}
function gitProjectWizardStepMatches(step = {}, ids = new Set(), labels = new Set()) {
  const id = gitProjectStepId(step);
  if (ids.has(id)) return true;
  const visibleLabel = gitProjectNormalizedWizardLabel(gitProjectVisibleStepLabel(step));
  const rawLabel = gitProjectNormalizedWizardLabel(step.label || "");
  return labels.has(visibleLabel) || labels.has(rawLabel);
}
function gitProjectWizardStepShouldHideInActionQueue(step = {}) {
  return gitProjectWizardStepMatches(
    step,
    GIT_PROJECT_WIZARD_HIDDEN_ACTION_IDS,
    GIT_PROJECT_WIZARD_HIDDEN_ACTION_LABELS
  );
}
function gitProjectWizardStepIsGitignoreReviewCandidate(step = {}) {
  return gitProjectWizardStepMatches(
    step,
    GIT_PROJECT_GITIGNORE_REVIEW_IDS,
    GIT_PROJECT_GITIGNORE_REVIEW_LABELS
  );
}
function gitProjectWizardStepIsSecretsFilterCandidate(step = {}) {
  const id = gitProjectStepId(step);
  if (id === "secrets_filter") return true;
  const label = gitProjectNormalizedWizardLabel(step.label || gitProjectVisibleStepLabel(step));
  return GIT_PROJECT_SECRETS_FILTER_LABELS.has(label);
}
function gitProjectNormalizeSecretsFilterStep(step = {}) {
  return {
    ...step,
    id: "secrets_filter",
    label: "Review Security / Secrets",
    why: step.why || "Check selected files for API keys, usernames, credentials, tokens, private keys, generated artifacts, and risky content before committing.",
    kind: step.kind || "safety",
  };
}
function gitProjectUniqueStrings(...groups) {
  const seen = new Set();
  const values = [];
  groups.flat().forEach((item) => {
    const value = String(item || "").trim();
    if (!value || seen.has(value)) return;
    seen.add(value);
    values.push(value);
  });
  return values;
}
function gitProjectWizardStepPaths(step = {}) {
  return Array.isArray(step.paths) ? step.paths : [];
}
function gitProjectWizardIgnoreRules(step = {}, key = "ignore_rules") {
  if (Array.isArray(step[key])) return step[key];
  const groups = step.ignore_rule_groups || {};
  if (key === "ignore_rules" && Array.isArray(groups.safe)) return groups.safe;
  if (key === "questionable_ignore_rules" && Array.isArray(groups.questionable)) return groups.questionable;
  return [];
}
function gitProjectMergeGitignoreReviewSteps(steps = []) {
  const candidates = steps.filter(Boolean);
  if (!candidates.length) return null;
  const generatedStep = candidates.find((step) => gitProjectStepId(step) === "ignore_generated_files") || null;
  const localEnvStep = candidates.find((step) => gitProjectStepId(step) === "ignore_local_environment_files") || null;
  const base = generatedStep || localEnvStep || candidates[0];
  const generatedPaths = generatedStep ? gitProjectWizardStepPaths(generatedStep) : [];
  const localEnvPaths = localEnvStep ? gitProjectWizardStepPaths(localEnvStep) : [];
  const uniquePaths = gitProjectUniqueStrings(
    ...candidates.map((step) => gitProjectWizardStepPaths(step)),
    ...candidates.map((step) => Array.isArray(step.affected_paths) ? step.affected_paths : [])
  ).sort();
  const generatedPathSet = new Set(generatedPaths);
  const sharedPathCount = localEnvPaths.filter((path) => generatedPathSet.has(path)).length;
  const safeRules = gitProjectUniqueStrings(...candidates.map((step) => gitProjectWizardIgnoreRules(step, "ignore_rules")));
  const questionableRules = gitProjectUniqueStrings(...candidates.map((step) => gitProjectWizardIgnoreRules(step, "questionable_ignore_rules")));
  const safePaths = gitProjectUniqueStrings(...candidates.map((step) => Array.isArray(step.safe_paths) ? step.safe_paths : []));
  const questionablePaths = gitProjectUniqueStrings(...candidates.map((step) => Array.isArray(step.questionable_paths) ? step.questionable_paths : []));
  const mergedStep = {
    ...base,
    label: ".gitignore review",
    why: [
      "Generated/debug files and local environment files appear to be untracked noise.",
      "Review the combined candidate list and add appropriate patterns to .gitignore or local excludes.",
    ].join(" "),
    paths: uniquePaths,
    affected_paths: uniquePaths,
    safe_paths: safePaths,
    questionable_paths: questionablePaths,
    ignore_rules: safeRules,
    questionable_ignore_rules: questionableRules,
    ignore_rule_groups: {
      ...(base.ignore_rule_groups || {}),
      safe: safeRules,
      questionable: questionableRules,
    },
    gitignore_review_summary: {
      generated_path_count: generatedPaths.length,
      local_environment_path_count: localEnvPaths.length,
      unique_path_count: uniquePaths.length,
      shared_path_count: sharedPathCount,
    },
    gitignore_path_summary: `Paths (${uniquePaths.length}; ${generatedPaths.length} generated, ${localEnvPaths.length} local/env, ${sharedPathCount} overlap)`,
    uiReason: "Combined .gitignore review card: generated/debug and local environment candidates are shown together.",
    tone: "actionable",
    uiLane: "ready_action",
    weight: Math.max(...candidates.map((step) => Number(step.weight || 0)), Number(base.weight || 0)),
    showRunner: candidates.some((step) => step.showRunner !== false),
  };
  return mergedStep;
}
function gitProjectWizardDisplayActions(actions = []) {
  const gitignoreCandidates = actions.filter(gitProjectWizardStepIsGitignoreReviewCandidate);
  const mergedGitignoreReview = gitProjectMergeGitignoreReviewSteps(gitignoreCandidates);
  const secretsFilterStep = actions.find(gitProjectWizardStepIsSecretsFilterCandidate) || null;
  let insertedGitignoreReview = false;
  let insertedSecretsFilter = false;

  const displayActions = actions.reduce((displayActions, step) => {
    if (gitProjectWizardStepShouldHideInActionQueue(step)) return displayActions;

    if (gitProjectWizardStepIsGitignoreReviewCandidate(step)) {
      if (!insertedGitignoreReview && mergedGitignoreReview) {
        displayActions.push(mergedGitignoreReview);
        insertedGitignoreReview = true;
      }
      return displayActions;
    }

    if (gitProjectWizardStepIsSecretsFilterCandidate(step)) {
      if (!insertedSecretsFilter) {
        displayActions.push(gitProjectNormalizeSecretsFilterStep(step));
        insertedSecretsFilter = true;
      }
      return displayActions;
    }

    displayActions.push(step);
    return displayActions;
  }, []);

  if (!insertedSecretsFilter && secretsFilterStep) {
    const insertAt = displayActions.findIndex((step) => gitProjectStepIsCommitCard(step));
    const normalized = gitProjectNormalizeSecretsFilterStep(secretsFilterStep);
    if (insertAt >= 0) {
      displayActions.splice(insertAt, 0, normalized);
    } else {
      displayActions.push(normalized);
    }
  }

  return displayActions;
}

function renderGitProjectWizard(wizard, data = {}) {
  if (!gitProjectWizardPlan) return;
  const steps = Array.isArray(wizard.steps) ? wizard.steps : [];
  if (!steps.length) {
    gitProjectWizardPlan.textContent = "No wizard steps available.";
    return;
  }
  const runtime = gitProjectRuntimeContext(data);
  const grouped = {
    attention: [],
    satisfied: [],
    ready_action: [],
    waiting_action: [],
    destructive_locked: [],
    evidence: [],
    completed: [],
  };
  steps.forEach((step) => {
    const actionKey = gitProjectActionKey(step, "wizard");
    const ui = classifyGitProjectWizardStep(step, data, actionKey);
    const weightedStep = {
      ...step,
      tone: ui.tone,
      originalTone: toneForWizardStep(step, data),
      runtime,
      status: ui.status || gitProjectActionStatusLabel(actionKey),
      weight: weightForWizardStep(step, data),
      uiLane: ui.lane,
      uiReason: ui.reason,
      showRunner: ui.showRunner,
    };
    grouped[ui.lane] = grouped[ui.lane] || [];
    grouped[ui.lane].push(weightedStep);
  });
  const sortByPriority = (items) => items.sort((a, b) => {
    const gateA = gitProjectFirstCommitGateOrder(a);
    const gateB = gitProjectFirstCommitGateOrder(b);
    if (Number.isFinite(gateA) || Number.isFinite(gateB)) {
      if (gateA !== gateB) return gateA - gateB;
      return Number(a.order || 0) - Number(b.order || 0);
    }
    if (a.tone !== b.tone) return a.tone === "blocking" ? -1 : 1;
    return Number(b.weight || 0) - Number(a.weight || 0);
  });
  Object.values(grouped).forEach(sortByPriority);
  const readyActions = sortByPriority([...grouped.ready_action]);
  const waitingActions = sortByPriority([
    ...grouped.waiting_action,
    ...grouped.destructive_locked,
  ]);
  const remainingActions = [
    ...readyActions,
    ...waitingActions,
  ];
  const visibleActions = gitProjectWizardDisplayActions(remainingActions);
  const renderStepCard = (step, displayIndex) => {
    const actionKey = gitProjectActionKey(step, "wizard");
    const stepComponentId = gitProjectWizardStepComponentId(step, actionKey);
    const stepLabel = gitProjectVisibleStepLabel(step);
    const cardSubscreen = gitProjectCardSubscreenHtml(step, actionKey);
    const openCardButton = cardSubscreen
      ? `<button type="button" class="git-project-card-open-button" data-git-project-open-card="${escapeHtml(actionKey)}">${escapeHtml(gitProjectOpenCardButtonLabel(step))}</button>`
      : "";
    const openCardCorner = openCardButton ? `<div class="git-project-card-open-corner" ${gitProjectMcComponentAttrs(`${stepComponentId}.open-card`, "toolbar", `${stepLabel} Open Card Control`, stepComponentId)}>${openCardButton}</div>` : "";
    const closedSummary = gitProjectClosedCardSummaryHtml(step, stepComponentId, stepLabel);
    const cardAttrs = [
      `data-priority-weight="${Number(step.weight || 0)}"`,
      cardSubscreen ? `data-git-project-card-shell="${escapeHtml(actionKey)}"` : "",
      gitProjectMcComponentAttrs(stepComponentId, "panel", stepLabel, "git-tools.projects.wizard.queue"),
    ].filter(Boolean).join(" ");
    const cardClass = `git-project-wizard-step git-project-mini-action-card tone-${escapeHtml(step.tone)} ${escapeHtml(step.uiLane || step.state || "planned")}${gitProjectStepIsCommitCard(step) ? " has-commit-workbench" : ""}${gitProjectStepIsArchiveCard(step) ? " has-archive-workbench" : ""}${cardSubscreen ? " has-card-open-control" : ""}`;
    const displayNumber = Number.isFinite(displayIndex) ? displayIndex + 1 : Number(step.order ?? 0) + 1;
    return `<div class="${cardClass}" ${cardAttrs}>
      <div class="git-project-wizard-step-title" ${gitProjectMcComponentAttrs(`${stepComponentId}.title`, "status", `${stepLabel} Title`, stepComponentId)}>
        <strong>${displayNumber}. ${escapeHtml(stepLabel)}</strong>
        <span class="git-project-mini-card-state">${escapeHtml(step.status || step.state || "ready")}</span>
      </div>
      ${closedSummary}
      ${openCardCorner}
      ${cardSubscreen}
    </div>`;
  };
  const renderStepGroup = (title, tone, items, emptyText, options = {}) => {
    const groupSlug = gitProjectMcSlug(options.key || title, "section");
    const groupComponentId = `git-tools.projects.wizard.section.${groupSlug}`;
    const countLabel = items.length ? `${items.length} step${items.length === 1 ? "" : "s"}` : "0 steps";
    const body = `<div class="git-project-wizard-list" ${gitProjectMcComponentAttrs(`${groupComponentId}.list`, "list", `${title} Items`, groupComponentId)}>
        ${items.length ? items.map((step, index) => renderStepCard(step, index)).join("") : `<div class="git-project-wizard-empty" ${gitProjectMcComponentAttrs(`${groupComponentId}.empty`, "status", `${title} Empty State`, groupComponentId)}>${escapeHtml(emptyText)}</div>`}
      </div>`;
    if (options.collapsed) {
      return `<details class="git-project-wizard-section tone-${escapeHtml(tone)} ${escapeHtml(options.className || "")}" ${gitProjectMcComponentAttrs(groupComponentId, "panel", title, "git-tools.projects.wizard.queue")}>
        <summary class="git-project-wizard-section-head" ${gitProjectMcComponentAttrs(`${groupComponentId}.head`, "status", `${title} Summary`, groupComponentId)}>
          <strong>${escapeHtml(title)}</strong>
          <span>${countLabel}</span>
        </summary>
        ${body}
      </details>`;
    }
    return `<section class="git-project-wizard-section tone-${escapeHtml(tone)} ${escapeHtml(options.className || "")}" ${gitProjectMcComponentAttrs(groupComponentId, "panel", title, "git-tools.projects.wizard.queue")}>
      <div class="git-project-wizard-section-head" ${gitProjectMcComponentAttrs(`${groupComponentId}.head`, "status", `${title} Summary`, groupComponentId)}>
        <strong>${escapeHtml(title)}</strong>
        <span>${countLabel}</span>
      </div>
      ${body}
    </section>`;
  };
  gitProjectWizardPlan.innerHTML = [
    `<div class="git-project-wizard-summary" ${gitProjectMcComponentAttrs("git-tools.projects.wizard.summary", "status", "Prioritized Workflow Queue Summary", "git-tools.projects.wizard-plan")}><strong>Prioritized workflow queue</strong><span>${escapeHtml(wizard.plan_id || "")}</span><span>${escapeHtml(wizard.strategy || "")}</span><span>Dirty ${Number(wizard.dirty_score || 0)}/100</span><span>Showing action queue only</span></div>`,
    renderStepGroup("Action queue", "actionable", visibleActions, "No workflow actions need review.", {key: "action-queue"}),
  ].join("");
  bindGitProjectActionButtons(gitProjectWizardPlan);
  bindGitProjectCardSubscreen(gitProjectWizardPlan);
}

function startGitServerProgress(title, lines = [], phases = []) {
  if (!gitServerOperationState) return {stop: () => null};
  const started = Date.now();
  let tick = 0;
  const render = () => {
    const elapsed = Math.max(0, Math.floor((Date.now() - started) / 1000));
    const phase = phases.length ? phases[Math.min(phases.length - 1, Math.floor(elapsed / 5))] : "";
    const dots = ".".repeat((tick % 3) + 1);
    gitServerOperationState.textContent = [
      `${title} ${dots}`,
      `Elapsed: ${elapsed}s`,
      phase ? `Current phase: ${phase}` : "",
    ].filter(Boolean).join(" — ");
    tick += 1;
  };
  render();
  const interval = window.setInterval(render, 1000);
  return {
    stop: () => window.clearInterval(interval),
  };
}
function gitServerOperationButtons() {
  return [
    gitServerStart,
    gitServerRestart,
    gitServerStop,
    gitServerLogs,
    gitServerUseLocal,
    gitServerRemoteApplyLocal,
    gitServerPushLocal,
    gitServerUseExternal,
    gitServerMirrorPlan,
    gitServerMirrorSetup,
    gitServerRemoteRun,
    gitServerRemoteCopyConsole,
    ...gitServerRemotePresetButtons,
  ].filter(Boolean);
}
function gitServerDockerDependentButtons() {
  return [
    gitServerStart,
    gitServerRestart,
    gitServerStop,
    gitServerLogs,
    gitServerRemoteApplyLocal,
    gitServerPushLocal,
    gitServerMirrorSetup,
  ].filter(Boolean);
}
function gitServerDockerUnavailableText(status = gitServerLastStatus) {
  const composeFile = status?.compose_file || "docker-compose.dev.yml";
  return [
    "Docker CLI is not available in the environment running Main Computer.",
    "",
    "Local Gitea actions need Docker in the backend process: Start, Restart, Stop, Logs, Create / verify repo + configure remote, Push to Local Gitea, and Set Up Server -> External Mirror.",
    "Opening http://localhost:3000 in your browser, or starting Gitea somewhere else, does not give this backend process Docker access.",
    "You can still use Reset suggested target and Run command to configure the local-gitea remote without starting Gitea.",
    "",
    `Compose file seen by backend: ${composeFile}`,
  ].join("\n");
}
function applyGitServerDockerAvailability(status = gitServerLastStatus) {
  gitServerLastStatus = status || gitServerLastStatus;
  if (gitToolsOperationRunning) return;
  const dockerUnavailable = Boolean(gitServerLastStatus && gitServerLastStatus.docker_available === false);
  gitServerDockerDependentButtons().forEach((button) => {
    if (button === gitServerRemoteApplyLocal || button === gitServerPushLocal) return;
    button.disabled = dockerUnavailable;
    button.setAttribute("aria-disabled", dockerUnavailable ? "true" : "false");
    button.title = dockerUnavailable ? "Docker CLI is not available where Main Computer is running." : "";
  });
  gitServerSetLocalActionAvailability({
    configurable: !gitServerTargetPrefunk || gitServerTargetPrefunk.is_git_repo !== false,
    pushable: !gitServerTargetPrefunk || (gitServerTargetPrefunk.is_git_repo !== false && gitServerTargetPrefunk.has_head !== false),
    reason: gitServerTargetPrefunk?.is_git_repo === false
      ? gitServerTargetUnavailableReason(gitServerTargetPrefunk)
      : (gitServerTargetPrefunk?.has_head === false ? "Create an initial commit before pushing to Local Gitea." : ""),
  });
  if (gitServerOperationState && dockerUnavailable) {
    gitServerOperationState.textContent = "Docker unavailable: local Gitea controls disabled.";
  }
}
function ensureGitServerDockerAvailable(actionLabel) {
  if (gitServerLastStatus && gitServerLastStatus.docker_available === false) {
    if (gitServerOutput) gitServerOutput.textContent = `${actionLabel} cannot run.\n\n${gitServerDockerUnavailableText(gitServerLastStatus)}`;
    updateGitWorkflowSectionSummary("git-server", "docker unavailable");
    applyGitServerDockerAvailability(gitServerLastStatus);
    return false;
  }
  return true;
}
function setGitServerOperationRunning(running) {
  gitToolsOperationRunning = Boolean(running);
  gitServerOperationButtons().forEach((button) => {
    button.disabled = gitToolsOperationRunning;
  });
  if (gitServerOperationCancel) gitServerOperationCancel.disabled = !gitToolsOperationRunning;
  if (gitServerOperationState) {
    gitServerOperationState.textContent = gitToolsOperationRunning ? "Command running..." : "No command running.";
  }
  if (!gitToolsOperationRunning) {
    applyGitServerDockerAvailability(gitServerLastStatus);
  }
}
function formatGitOperation(operation) {
  if (!operation) return "No Git operation is currently running.";
  const lines = [
    `${operation.label || operation.kind || "Git operation"} — ${operation.status || "running"}`,
    `id: ${operation.id || "unknown"}`,
    `elapsed: ${operation.elapsed || 0}s`,
  ];
  if (operation.process?.pid) lines.push(`pid: ${operation.process.pid}`);
  if (operation.cancel_requested) lines.push("cancel requested");
  const logs = Array.isArray(operation.logs) ? operation.logs.slice(-12) : [];
  if (logs.length) {
    lines.push("", "Recent logs:");
    logs.forEach((entry) => {
      lines.push(`- +${entry.elapsed || 0}s ${entry.message || ""}`);
      if (entry.data && Object.keys(entry.data).length) {
        lines.push(`  ${JSON.stringify(entry.data)}`);
      }
    });
  }
  if (operation.result) {
    lines.push("", "Result:");
    lines.push(JSON.stringify(operation.result, null, 2));
  }
  return lines.join("\n");
}
function renderGitOperationStatus(data, {renderOutput = false} = {}) {
  const active = data?.active || null;
  setGitServerOperationRunning(Boolean(active));
  if (gitServerOperationState) {
    if (active) {
      gitServerOperationState.textContent = `${active.label || active.kind || "Git operation"}: ${active.status || "running"} (${active.elapsed || 0}s)`;
    } else {
      const last = Array.isArray(data?.history) && data.history.length ? data.history[data.history.length - 1] : null;
      gitServerOperationState.textContent = last ? `Last: ${last.label || last.kind} ${last.status} (${last.elapsed || 0}s)` : "No command running.";
    }
  }
  if (renderOutput && gitServerOutput) {
    gitServerOutput.textContent = active ? formatGitOperation(active) : JSON.stringify(data, null, 2);
  }
}
async function refreshGitOperationStatus(options = {}) {
  const data = await gitToolsRequest("/api/applications/git/server/operation/status", {});
  renderGitOperationStatus(data, options);
  return data;
}
function startGitOperationPolling({renderOutput = true} = {}) {
  stopGitOperationPolling();
  refreshGitOperationStatus({renderOutput}).catch(() => null);
  gitToolsOperationPollTimer = window.setInterval(() => {
    refreshGitOperationStatus({renderOutput}).catch(() => null);
  }, 1000);
}
function stopGitOperationPolling() {
  if (gitToolsOperationPollTimer) {
    window.clearInterval(gitToolsOperationPollTimer);
    gitToolsOperationPollTimer = null;
  }
}
async function runGitServerOperationRequest(path, payload, initialLines = []) {
  if (gitToolsOperationRunning) {
    if (gitServerOutput) gitServerOutput.textContent = "A Git command is already running. Cancel it or wait for it to finish before starting another.";
    return null;
  }
  setGitServerOperationRunning(true);
  if (gitServerOutput) {
    gitServerOutput.textContent = [
      "Starting Git operation...",
      ...initialLines,
      "",
      "Operation logs will update here.",
    ].filter(Boolean).join("\n");
  }
  startGitOperationPolling({renderOutput: true});
  try {
    const data = await gitToolsRequest(path, payload);
    renderGitOperationStatus({active: null, history: data.operation ? [data.operation] : []}, {renderOutput: false});
    return data;
  } finally {
    stopGitOperationPolling();
    await refreshGitOperationStatus({renderOutput: false}).catch(() => null);
  }
}
async function cancelGitServerOperation() {
  if (!gitToolsOperationRunning) {
    await refreshGitOperationStatus({renderOutput: true}).catch(() => null);
    return;
  }
  if (gitServerOutput) gitServerOutput.textContent = "Cancel requested. Waiting for the running subprocess to stop...";
  try {
    const data = await gitToolsRequest("/api/applications/git/server/operation/cancel", {});
    renderGitOperationStatus(data, {renderOutput: true});
    startGitOperationPolling({renderOutput: true});
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = gitToolsOperationErrorText("Cancel failed", error);
  }
}
function summarizeGitStatus(data) {
  if (!data || !data.ok) {
    return data?.error || "Git status unavailable.";
  }
  return `${data.branch || "unknown branch"} | dirty ${data.dirty ? "yes" : "no"} | changed ${data.changed_count || 0} | untracked ${data.untracked_count || 0}`;
}

function gitWorkflowSection(sectionName) {
  return gitWorkflowSections?.get(sectionName) || null;
}
function updateGitWorkflowSectionSummary(sectionName, statusText) {
  const section = gitWorkflowSection(sectionName);
  if (!section || !statusText) return;
  const status = section.querySelector(".git-workflow-section-status");
  if (status) status.textContent = statusText;
}
function expandGitWorkflowSection(sectionName, statusText = "") {
  const section = gitWorkflowSection(sectionName);
  if (!section) return;
  section.open = true;
  updateGitWorkflowSectionSummary(sectionName, statusText);
}
function collapseGitWorkflowSection(sectionName, statusText = "") {
  const section = gitWorkflowSection(sectionName);
  if (!section) return;
  section.open = false;
  updateGitWorkflowSectionSummary(sectionName, statusText);
}
function initializeGitWorkflowDisclosure() {
  if (!gitWorkflowAccordion) return;
  updateGitWorkflowSectionSummary("ai-interpretation", "waiting for an AI request");
  updateGitWorkflowSectionSummary("proposed-plan", "closed until the request needs a plan");
  updateGitWorkflowSectionSummary("patch-inventory", "closed until patches are needed");
  updateGitWorkflowSectionSummary("patch-actions", "locked until a patch is selected");
  updateGitWorkflowSectionSummary("shim-builder", "closed until AI creates or selects a shim");
  updateGitWorkflowSectionSummary("dry-run", "waiting for a patch or shim dry run");
  updateGitWorkflowSectionSummary("advanced-diagnostics", "raw details for power users");
}
function syncGitPageWizardWorkflowDisclosure() {
  const answered = gitPageWizardCompletedRequiredCount();
  const complete = gitPageWizardIsComplete();
  if (!answered && !complete && !gitPageWizardConsoleSent) {
    updateGitWorkflowSectionSummary("proposed-plan", "closed until the request needs a plan");
    return;
  }
  expandGitWorkflowSection(
    "proposed-plan",
    complete
      ? (gitPageWizardConsoleSent ? "sent to Git Console; create a shim next" : "draft ready; send to Git Console next")
      : `collecting plan details: ${answered} of ${GIT_PAGE_WIZARD_STEPS.length} answers`
  );
  if (gitPageWizardConsoleSent) {
    expandGitWorkflowSection("ai-interpretation", "wizard prompt copied to AI request console");
    updateGitWorkflowSectionSummary("shim-builder", "ready for Ask AI / Generate Shim or Plan Shim");
  }
}
const GIT_PAGE_WIZARD_STEPS = [
  {
    key: "target",
    label: "Target page/file",
    prompt: "Which page or repository file should receive this page element? Example: main_computer/web/applications/apps/git-tools.html"
  },
  {
    key: "purpose",
    label: "User purpose",
    prompt: "What should the new page element help the user do?"
  },
  {
    key: "kind",
    label: "Element kind",
    prompt: "What kind of element is it: panel, action, input, output, list, toolbar, or workspace?"
  },
  {
    key: "label",
    label: "Visible label",
    prompt: "What visible label should the element use?"
  },
  {
    key: "owner",
    label: "Owner/placement",
    prompt: "Where should it sit, or which component should own it? Example: git-tools.sidebar or git-tools.detail"
  },
  {
    key: "behavior",
    label: "Behavior and tests",
    prompt: "What behavior, API route, state, or acceptance test should be included?"
  }
];
const GIT_PAGE_WIZARD_REQUIRED_KEYS = GIT_PAGE_WIZARD_STEPS.map((step) => step.key);
const GIT_PAGE_WIZARD_WORKFLOW_STAGES = ["answer", "draft", "console", "shim", "verify"];

function gitPageWizardStepDefinition(index = gitPageWizardStep) {
  return GIT_PAGE_WIZARD_STEPS[Math.min(Math.max(index, 0), GIT_PAGE_WIZARD_STEPS.length - 1)];
}
function gitPageWizardAnswerFor(key) {
  return String(gitPageWizardAnswers[key] || "").trim();
}
function gitPageWizardCompletedRequiredCount() {
  return GIT_PAGE_WIZARD_REQUIRED_KEYS.filter((key) => gitPageWizardAnswerFor(key)).length;
}
function gitPageWizardIsComplete() {
  return GIT_PAGE_WIZARD_REQUIRED_KEYS.every((key) => gitPageWizardAnswerFor(key));
}
function gitPageWizardMissingLabels() {
  return GIT_PAGE_WIZARD_STEPS
    .filter((step) => !gitPageWizardAnswerFor(step.key))
    .map((step) => step.label);
}
function gitPageWizardWorkflowStage() {
  if (!gitPageWizardIsComplete()) return "answer";
  return gitPageWizardConsoleSent ? "console" : "draft";
}
function renderGitPageWizardWorkflow(stage) {
  if (!gitPageWizardWorkflow) return;
  const activeIndex = Math.max(GIT_PAGE_WIZARD_WORKFLOW_STAGES.indexOf(stage), 0);
  gitPageWizardWorkflow.querySelectorAll("[data-wizard-stage]").forEach((item) => {
    const itemIndex = GIT_PAGE_WIZARD_WORKFLOW_STAGES.indexOf(item.dataset.wizardStage || "");
    item.classList.toggle("active", itemIndex === activeIndex);
    item.classList.toggle("complete", itemIndex >= 0 && itemIndex < activeIndex);
  });
}
function gitPageWizardStatusText(complete) {
  if (!complete) {
    const next = gitPageWizardStepDefinition();
    const completed = gitPageWizardCompletedRequiredCount();
    return `Step ${completed + 1} of ${GIT_PAGE_WIZARD_STEPS.length}: ${next.label}. ${next.prompt}`;
  }
  if (gitPageWizardConsoleSent) {
    return "Prompt sent to Git Console. Use Ask AI / Generate Shim or Plan Shim, then review the shim and dry-run before applying changes.";
  }
  return "Draft ready. Send it to the Git Console before creating or planning a shim.";
}
function gitPageWizardSlug(value, fallback = "page-element") {
  const slug = String(value || fallback)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || fallback;
}
function gitPageWizardKind() {
  const requested = gitPageWizardSlug(gitPageWizardAnswers.kind || "panel", "panel");
  const allowed = new Set(["panel", "action", "input", "output", "list", "toolbar", "workspace"]);
  return allowed.has(requested) ? requested : "panel";
}
function gitPageWizardLabel() {
  return String(gitPageWizardAnswers.label || gitPageWizardAnswers.purpose || "New Page Element").trim();
}
function gitPageWizardOwner() {
  return String(gitPageWizardAnswers.owner || "git-tools.detail").trim();
}
function gitPageWizardFeatureId(slug) {
  const target = String(gitPageWizardAnswers.target || "").toLowerCase();
  const prefix = target.includes("git-tools") || gitPageWizardOwner().startsWith("git-tools")
    ? "git-tools.feature.page-wizard"
    : "applications.feature.page-wizard";
  return `${prefix}.${slug}`;
}
function gitPageWizardMetadata(slug, kind) {
  const label = gitPageWizardLabel();
  const owner = gitPageWizardOwner();
  const featureId = gitPageWizardFeatureId(slug);
  const componentId = `${owner}.${slug}`;
  return {
    componentId,
    featureId,
    kind,
    label,
    owner,
    widgetId: componentId.replace(/\./g, "-"),
  };
}
function gitPageWizardAttribute(name, value) {
  return `${name}="${escapeHtml(value)}"`;
}
function gitPageWizardMetadataAttributes(meta, includeWidget = false) {
  const attrs = [
    gitPageWizardAttribute("data-mc-component-id", meta.componentId),
    gitPageWizardAttribute("data-mc-component-kind", meta.kind),
    gitPageWizardAttribute("data-mc-component-label", meta.label),
    gitPageWizardAttribute("data-mc-component-owner", meta.owner),
    gitPageWizardAttribute("data-mc-feature-id", meta.featureId),
  ];
  if (includeWidget) {
    attrs.unshift(
      gitPageWizardAttribute("data-widget-label", meta.label),
      gitPageWizardAttribute("data-mc-widget-class", meta.kind),
      gitPageWizardAttribute("data-mc-widget-kind", meta.kind),
      gitPageWizardAttribute("data-mc-widget-id", meta.widgetId)
    );
  }
  return attrs.join(" ");
}
function gitPageWizardBuildDraft() {
  const label = gitPageWizardLabel();
  const slug = gitPageWizardSlug(label);
  const kind = gitPageWizardKind();
  const meta = gitPageWizardMetadata(slug, kind);
  const escapedLabel = escapeHtml(label);
  const purpose = String(gitPageWizardAnswers.purpose || "Describe the user-facing purpose here.").trim();
  const escapedPurpose = escapeHtml(purpose);

  if (kind === "action") {
    return `<button type="button" id="${slug}" class="${slug}-action" ${gitPageWizardMetadataAttributes(meta)}>${escapedLabel}</button>`;
  }
  if (kind === "input") {
    const inputMeta = {...meta, componentId: `${meta.componentId}.input`, kind: "input", owner: meta.componentId};
    return [
      `<label class="${slug}-field" ${gitPageWizardMetadataAttributes(meta)}>`,
      `  ${escapedLabel}`,
      `  <input id="${slug}" placeholder="${escapedPurpose}" ${gitPageWizardMetadataAttributes(inputMeta)}>`,
      `</label>`
    ].join("\n");
  }
  if (kind === "output") {
    return `<pre id="${slug}" class="${slug}-output" ${gitPageWizardMetadataAttributes(meta)}>${escapedPurpose}</pre>`;
  }
  if (kind === "list") {
    return `<div id="${slug}" class="${slug}-list" ${gitPageWizardMetadataAttributes(meta)}>No ${escapedLabel.toLowerCase()} items yet.</div>`;
  }
  if (kind === "toolbar") {
    return [
      `<div class="${slug}-toolbar" ${gitPageWizardMetadataAttributes(meta, true)}>`,
      `  <button type="button" data-action="${slug}-primary">${escapedLabel}</button>`,
      `</div>`
    ].join("\n");
  }
  const tag = kind === "workspace" ? "section" : "div";
  return [
    `<${tag} class="${slug}-${kind} app-widget" ${gitPageWizardMetadataAttributes(meta, true)}>`,
    `  <strong data-mc-component-id="${escapeHtml(meta.componentId)}.heading" data-mc-component-kind="status" data-mc-component-label="${escapedLabel} Heading" data-mc-component-owner="${escapeHtml(meta.componentId)}" data-mc-feature-id="${escapeHtml(meta.featureId)}">${escapedLabel}</strong>`,
    `  <p data-mc-component-id="${escapeHtml(meta.componentId)}.copy" data-mc-component-kind="status" data-mc-component-label="${escapedLabel} Copy" data-mc-component-owner="${escapeHtml(meta.componentId)}" data-mc-feature-id="${escapeHtml(meta.featureId)}">${escapedPurpose}</p>`,
    `</${tag}>`
  ].join("\n");
}
function gitPageWizardSummaryLines() {
  return GIT_PAGE_WIZARD_STEPS.map((step) => {
    const value = String(gitPageWizardAnswers[step.key] || "").trim() || "(not set)";
    return `- ${step.label}: ${value}`;
  });
}
function buildGitPageWizardPrompt() {
  const draft = gitPageWizardBuildDraft();
  return [
    "Build the requested page element using the existing Git Tools page conventions.",
    "",
    "Wizard summary:",
    ...gitPageWizardSummaryLines(),
    "",
    "Implementation rules:",
    "- Keep the change narrow and update only the files needed for this page element.",
    "- Preserve data-mc-component metadata, widget labels, and existing Git Tools shim/patch flows.",
    "- Add or update static tests that assert the new ids, labels, and wizard functions are present.",
    "- Do not remove existing Git Console, patch, shim, or dry-run behavior.",
    "",
    "Workflow expectations:",
    "- The wizard must collect target, purpose, kind, label, owner, and behavior before sending a Git Console prompt.",
    "- The Git Console prompt should be used with Ask AI / Generate Shim or Plan Shim before any shim is reviewed or run.",
    "- Review the generated shim and run a dry-run before applying repository changes.",
    "",
    "Draft markup to adapt:",
    draft
  ].join("\n");
}
function renderGitPageWizard() {
  if (!gitPageWizardTranscript || !gitPageWizardOutput) return;
  const complete = gitPageWizardIsComplete();
  const messages = [
    {who: "Wizard", text: "I can turn a chat-style request into metadata-ready page markup and a Git Console AI prompt."}
  ];
  GIT_PAGE_WIZARD_STEPS.forEach((step, index) => {
    if (index < gitPageWizardStep || gitPageWizardAnswers[step.key]) {
      messages.push({who: "Wizard", text: step.prompt});
      messages.push({who: "You", text: String(gitPageWizardAnswers[step.key] || "(skipped)")});
    }
  });
  if (!complete) {
    messages.push({who: "Wizard", text: gitPageWizardStepDefinition().prompt});
  } else if (gitPageWizardConsoleSent) {
    messages.push({who: "Wizard", text: "Prompt sent. Use Ask AI / Generate Shim or Plan Shim, then review the shim and dry-run before applying changes."});
  } else {
    messages.push({who: "Wizard", text: "Draft ready. Send it to the Git Console, then use Ask AI / Generate Shim or Plan Shim to turn it into repository changes."});
  }
  gitPageWizardTranscript.innerHTML = "";
  messages.forEach((message) => {
    const item = document.createElement("div");
    item.className = `git-page-wizard-message ${message.who === "You" ? "user" : "assistant"}`;
    item.innerHTML = `<strong>${escapeHtml(message.who)}:</strong> ${escapeHtml(message.text)}`;
    gitPageWizardTranscript.append(item);
  });
  gitPageWizardTranscript.scrollTop = gitPageWizardTranscript.scrollHeight;
  const stage = gitPageWizardWorkflowStage();
  renderGitPageWizardWorkflow(stage);
  if (gitPageWizardStatus) {
    gitPageWizardStatus.textContent = gitPageWizardStatusText(complete);
  }
  if (gitPageWizardInput) {
    gitPageWizardInput.placeholder = complete ? "Add a revision note, or reset to start a new element" : gitPageWizardStepDefinition().prompt;
  }
  if (gitPageWizardNext) {
    gitPageWizardNext.textContent = complete ? "Update Draft" : (gitPageWizardStep === GIT_PAGE_WIZARD_STEPS.length - 1 ? "Build Draft" : "Next");
  }
  if (gitPageWizardSendConsole) {
    gitPageWizardSendConsole.disabled = !complete;
    gitPageWizardSendConsole.title = complete ? "Copy the completed wizard prompt into the Git Console." : `Complete required fields first: ${gitPageWizardMissingLabels().join(", ")}`;
  }
  gitPageWizardOutput.textContent = [
    "Wizard draft markup:",
    "",
    gitPageWizardBuildDraft(),
    "",
    "Git Console prompt:",
    "",
    buildGitPageWizardPrompt()
  ].join("\n");
  syncGitPageWizardWorkflowDisclosure();
}
function advanceGitPageWizard() {
  if (!gitPageWizardInput) return;
  const answer = String(gitPageWizardInput.value || "").trim();
  if (!answer) {
    renderGitPageWizard();
    return;
  }
  gitPageWizardConsoleSent = false;
  if (gitPageWizardStep >= GIT_PAGE_WIZARD_STEPS.length) {
    const prior = String(gitPageWizardAnswers.behavior || "").trim();
    gitPageWizardAnswers.behavior = [prior, `Revision note: ${answer}`].filter(Boolean).join("\n");
  } else {
    const step = gitPageWizardStepDefinition();
    gitPageWizardAnswers[step.key] = answer;
    gitPageWizardStep += 1;
  }
  gitPageWizardInput.value = "";
  renderGitPageWizard();
}
function resetGitPageWizard() {
  gitPageWizardStep = 0;
  gitPageWizardAnswers = {};
  gitPageWizardConsoleSent = false;
  if (gitPageWizardInput) gitPageWizardInput.value = "";
  renderGitPageWizard();
}
function sendGitPageWizardToConsole() {
  if (!gitConsoleInput) return;
  expandGitWorkflowSection("proposed-plan", "preparing wizard prompt for Git Console");
  if (!gitPageWizardIsComplete()) {
    const missing = gitPageWizardMissingLabels().join(", ");
    if (gitPageWizardStatus) {
      gitPageWizardStatus.textContent = `Complete required fields before sending to Git Console: ${missing}`;
    }
    if (gitConsoleOutput) {
      gitConsoleOutput.textContent = `Page Element Wizard is incomplete. Finish these fields first: ${missing}`;
    }
    renderGitPageWizard();
    return;
  }
  gitConsoleInput.value = buildGitPageWizardPrompt();
  gitPageWizardConsoleSent = true;
  expandGitWorkflowSection("ai-interpretation", "wizard prompt copied to AI request console");
  updateGitWorkflowSectionSummary("shim-builder", "ready for Ask AI / Generate Shim or Plan Shim");
  if (gitConsoleOutput) {
    gitConsoleOutput.textContent = "Page Element Wizard prompt copied here. Use Ask AI / Generate Shim or Plan Shim to convert it into a stored git-control shim, then review and dry-run before applying changes.";
  }
  renderGitPageWizard();
  gitConsoleInput.focus();
}

function renderGitPatchGroups(data) {
  if (!gitPatchList) return;
  updateGitWorkflowSectionSummary("patch-inventory", "loaded; expand to choose patches or dry runs");
  const groups = [
    ["incoming", "Incoming Patches"],
    ["applied", "Applied Patches"],
    ["archive", "Archive"],
    ["dry_runs", "Dry Runs"],
  ];
  gitPatchList.innerHTML = "";
  let rendered = 0;
  groups.forEach(([key, title]) => {
    const items = Array.isArray(data?.[key]) ? data[key] : [];
    if (!items.length) {
      return;
    }
    rendered += 1;
    const section = document.createElement("section");
    section.className = "git-patch-group";
    section.innerHTML = `<h3>${escapeHtml(title)}</h3>`;
    const list = document.createElement("div");
    list.className = "git-patch-group-list";
    items.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "git-patch-item";
      const itemName = item.name || item.relative_path || item.path || "unnamed";
      const active = key === "dry_runs" ? itemName === gitToolsSelectedDryRun : itemName === gitToolsSelectedPatch;
      button.classList.toggle("active", active);
      button.innerHTML = `<strong>${escapeHtml(itemName)}</strong><small>${escapeHtml(item.relative_path || item.path || "")}</small>`;
      button.addEventListener("click", () => {
        if (key === "dry_runs") {
          gitToolsSelectedDryRun = itemName;
          gitDryRunName.value = itemName;
          expandGitWorkflowSection("dry-run", `selected dry-run preview: ${itemName}`);
          loadGitDryRun();
        } else {
          gitToolsSelectedPatch = itemName;
          gitPatchName.value = itemName;
          expandGitWorkflowSection("patch-actions", `selected patch: ${itemName}`);
          previewGitPatch();
        }
      });
      list.append(button);
    });
    section.append(list);
    gitPatchList.append(section);
  });
  if (!rendered) {
    gitPatchList.innerHTML = '<div class="git-tools-empty">No patches or dry-run previews are available yet.</div>';
  }
}
function renderGitShimList(data) {
  if (!gitShimList) return;
  const shims = Array.isArray(data?.shims) ? data.shims : [];
  gitShimList.innerHTML = "";
  if (!shims.length) {
    updateGitWorkflowSectionSummary("shim-builder", "no stored shims yet");
    gitShimList.innerHTML = '<div class="git-tools-empty">No git-control shims are stored yet. Use Ask AI / Generate Shim, Plan Shim, Run / Save Shim, or Extract AI Shims.</div>';
    return;
  }
  updateGitWorkflowSectionSummary("shim-builder", `${shims.length} stored shim${shims.length === 1 ? "" : "s"} available`);
  shims.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "git-patch-item git-shim-item";
    const shimId = item.id || "unknown-shim";
    button.classList.toggle("active", shimId === gitToolsSelectedShim);
    const commandText = Array.isArray(item.git_commands) && item.git_commands.length
      ? item.git_commands.join("\\n")
      : (item.title || "documentation-only shim");
    const recommendation = item.ordination_recommendation || item.metadata?.["ordination-recommendation"] || "not-recommended";
    const ordained = item.ordained || item.ordination_state === "ordained";
    button.innerHTML = [
      `<strong>${escapeHtml(shimId)}</strong>`,
      `<small>${escapeHtml(item.kind || "shim")} | ${escapeHtml(item.risk || "unknown")} | ${ordained ? "ordained" : "candidate"} | recommend: ${escapeHtml(recommendation)}</small>`,
      `<small>${escapeHtml(commandText)}</small>`
    ].join("");
    button.addEventListener("click", () => {
      gitToolsSelectedShim = shimId;
      if (gitShimId) gitShimId.value = shimId;
      expandGitWorkflowSection("shim-builder", `selected shim: ${shimId}`);
      viewGitShim();
      renderGitShimList(data);
    });
    gitShimList.append(button);
  });
}
async function refreshGitShims() {
  if (!gitShimList) return;
  try {
    const data = await gitToolsRequest("/api/applications/git/shims", {});
    renderGitShimList(data);
  } catch (error) {
    gitShimList.innerHTML = `<div class="git-tools-empty">Shim inventory failed: ${escapeHtml(error.message || error)}</div>`;
  }
}
function selectedGitShimId() {
  return (gitShimId?.value || gitToolsSelectedShim || "").trim();
}
function showGitConsolePayload(payload) {
  if (!gitConsoleOutput) return;
  gitConsoleOutput.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
}
async function extractGitConsoleShims() {
  expandGitWorkflowSection("ai-interpretation", "extracting shims from AI output");
  if (!gitConsoleInput || !gitConsoleOutput) return;
  const aiOutput = gitConsoleInput.value || "";
  if (!aiOutput.trim()) {
    gitConsoleOutput.textContent = "Paste AI output containing python git-control.py commands first.";
    return;
  }
  gitConsoleOutput.textContent = "Extracting git-control.py commands into shims...";
  try {
    const data = await gitToolsRequest("/api/applications/git/console/extract", {ai_output: aiOutput});
    showGitConsolePayload(data);
    if (Array.isArray(data.shims) && data.shims.length && gitShimId) {
      gitToolsSelectedShim = data.shims[0].id || "";
      gitShimId.value = gitToolsSelectedShim;
    }
    expandGitWorkflowSection("shim-builder", "extracted shims are ready to review");
    await refreshGitShims();
    await refreshGitStatus();
  } catch (error) {
    gitConsoleOutput.textContent = `Shim extraction failed: ${error.message || error}`;
  }
}
async function runGitConsoleCommand() {
  expandGitWorkflowSection("ai-interpretation", "running git-control command");
  if (!gitConsoleInput || !gitConsoleOutput) return;
  const command = gitConsoleInput.value || "";
  if (!command.trim()) {
    gitConsoleOutput.textContent = "Enter a git command or python git-control.py command first.";
    return;
  }
  gitConsoleOutput.textContent = "Running git-control command and saving its shim...";
  try {
    const data = await gitToolsRequest("/api/applications/git/console/run", {command, repo_dir: gitToolsRepoDirValue(".")});
    showGitConsolePayload(data);
    if (data.shim?.id && gitShimId) {
      gitToolsSelectedShim = data.shim.id;
      gitShimId.value = data.shim.id;
    }
    expandGitWorkflowSection("shim-builder", "saved shim is ready to review");
    await refreshGitShims();
    await refreshGitStatus();
  } catch (error) {
    gitConsoleOutput.textContent = `Git console command failed: ${error.message || error}`;
  }
}
async function askGitAiForShim() {
  expandGitWorkflowSection("ai-interpretation", "asking AI for a shim");
  if (!gitConsoleInput || !gitConsoleOutput) return;
  const prompt = gitConsoleInput.value || "";
  gitConsoleOutput.textContent = "Asking AI with ordained git-control shims loaded as context...";
  try {
    const data = await gitToolsRequest("/api/applications/git/ai-shim", {prompt});
    showGitConsolePayload(data);
    const firstShim = Array.isArray(data.shims) && data.shims.length ? data.shims[0] : null;
    if (firstShim?.id && gitShimId) {
      gitToolsSelectedShim = firstShim.id;
      gitShimId.value = firstShim.id;
    }
    expandGitWorkflowSection("shim-builder", "AI-generated shim is ready to review");
    await refreshGitShims();
  } catch (error) {
    gitConsoleOutput.textContent = `AI shim generation failed: ${error.message || error}`;
  }
}
async function createGitPlanShim() {
  expandGitWorkflowSection("ai-interpretation", "creating shim-first git plan");
  updateGitWorkflowSectionSummary("shim-builder", "plan shim requested");
  if (gitConsoleOutput) gitConsoleOutput.textContent = "Creating shim-first git plan...";
  try {
    const data = await gitToolsRequest("/api/applications/git/control/plan", {prompt: gitConsoleInput?.value || ""});
    showGitConsolePayload(data);
    if (data.plan_shim?.id && gitShimId) {
      gitToolsSelectedShim = data.plan_shim.id;
      gitShimId.value = data.plan_shim.id;
    }
    expandGitWorkflowSection("shim-builder", "plan shim is ready to review");
    await refreshGitShims();
  } catch (error) {
    if (gitConsoleOutput) gitConsoleOutput.textContent = `Plan shim failed: ${error.message || error}`;
  }
}
async function viewGitShim() {
  expandGitWorkflowSection("shim-builder", "loading shim details");
  const shimId = selectedGitShimId();
  if (!shimId) {
    if (gitShimOutput) gitShimOutput.textContent = "Select a shim first.";
    return;
  }
  if (gitShimOutput) gitShimOutput.textContent = "Loading shim...";
  try {
    const data = await gitToolsRequest("/api/applications/git/shim/read", {shim_id: shimId});
    gitToolsSelectedShim = shimId;
    if (gitShimId) gitShimId.value = shimId;
    const commands = Array.isArray(data.git_commands) && data.git_commands.length
      ? `\\n\\nExtracted git commands:\\n${data.git_commands.join("\\n")}`
      : "";
    const recommendation = data.ordination_recommendation || data.metadata?.["ordination-recommendation"] || "not-recommended";
    const ordination = data.ordained ? "ordained" : (data.ordination_state || "candidate");
    const summary = `Ordination: ${ordination}\\nRecommendation: ${recommendation}\\nReason: ${data.ordination_reason || data.metadata?.["ordination-reason"] || ""}\\n\\n`;
    gitShimOutput.textContent = `${summary}${data.text || JSON.stringify(data, null, 2)}${commands}`;
  } catch (error) {
    if (gitShimOutput) gitShimOutput.textContent = `Shim read failed: ${error.message || error}`;
  }
}
async function runGitShim() {
  expandGitWorkflowSection("shim-builder", "running stored shim");
  const shimId = selectedGitShimId();
  if (!shimId) {
    if (gitShimOutput) gitShimOutput.textContent = "Select a shim first.";
    return;
  }
  if (gitShimOutput) gitShimOutput.textContent = "Running stored shim...";
  try {
    const data = await gitToolsRequest("/api/applications/git/shim/run", {shim_id: shimId});
    gitShimOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitStatus();
    await refreshGitShims();
  } catch (error) {
    if (gitShimOutput) gitShimOutput.textContent = `Shim run failed: ${error.message || error}`;
  }
}
async function setGitShimOrdination(ordained) {
  expandGitWorkflowSection("shim-builder", ordained ? "ordaining selected shim" : "removing shim ordination");
  const shimId = selectedGitShimId();
  if (!shimId) {
    if (gitShimOutput) gitShimOutput.textContent = "Select a shim first.";
    return;
  }
  if (gitShimOutput) gitShimOutput.textContent = ordained ? "Ordaining shim for future AI context..." : "Removing shim from ordained AI context...";
  try {
    const data = await gitToolsRequest("/api/applications/git/shim/ordination", {shim_id: shimId, ordained});
    gitShimOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitShims();
    await viewGitShim();
  } catch (error) {
    if (gitShimOutput) gitShimOutput.textContent = `Shim ordination update failed: ${error.message || error}`;
  }
}
async function deleteGitShim() {
  expandGitWorkflowSection("shim-builder", "deleting selected shim");
  const shimId = selectedGitShimId();
  if (!shimId) {
    if (gitShimOutput) gitShimOutput.textContent = "Select a shim first.";
    return;
  }
  if (gitShimOutput) gitShimOutput.textContent = "Deleting shim...";
  try {
    const data = await gitToolsRequest("/api/applications/git/shim/delete", {shim_id: shimId});
    gitToolsSelectedShim = "";
    if (gitShimId) gitShimId.value = "";
    gitShimOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitShims();
  } catch (error) {
    if (gitShimOutput) gitShimOutput.textContent = `Shim delete failed: ${error.message || error}`;
  }
}
async function refreshGitStatus() {
  const repoDir = gitToolsRepoDirValue(".");
  if (gitToolsStatus) gitToolsStatus.textContent = "Loading git status...";
  try {
    const data = await gitToolsRequest("/api/applications/git/status", {repo_dir: repoDir});
    gitToolsLastStatus = data;
    gitServerApplyTargetPrefunk(gitServerTargetFromStatus(data), {announce: false, preserveEdited: true});
    if (gitServerExternalUrl && !gitServerExternalUrl.value.trim()) {
      gitServerExternalUrl.value = gitServerBestExternalUrl();
    }
    if (gitToolsStatus) gitToolsStatus.textContent = summarizeGitStatus(data);
    const recent = Array.isArray(data.recent_commits) && data.recent_commits.length
      ? data.recent_commits.join("\n")
      : "No recent commits reported.";
    const shortStatus = data.short_status || "No short status output.";
    if (gitToolsBranch) gitToolsBranch.textContent = `${data.git_root || ""}\n\n${shortStatus}\n\nRecent commits:\n${recent}`;
  } catch (error) {
    gitServerApplyTargetPrefunk(gitServerTargetFromStatus({ok: false, repo_dir: repoDir, error: error.message || String(error)}), {announce: false, preserveEdited: false});
    if (gitToolsStatus) gitToolsStatus.textContent = `Git status failed: ${error.message || error}`;
    if (gitToolsBranch) gitToolsBranch.textContent = "No git status details available.";
  }
}
async function refreshGitPatches() {
  if (!gitPatchList) return;
  try {
    const data = await gitToolsRequest("/api/applications/git/patches", {});
    renderGitPatchGroups(data);
    if (gitDryRunName && !gitDryRunName.value && Array.isArray(data.dry_runs) && data.dry_runs.length) {
      gitDryRunName.value = data.dry_runs[0].name || "";
    }
  } catch (error) {
    gitPatchList.innerHTML = `<div class="git-tools-empty">Patch inventory failed: ${escapeHtml(error.message || error)}</div>`;
  }
}
async function previewGitPatch() {
  expandGitWorkflowSection("patch-actions", "loading patch preview");
  const patchName = (gitPatchName.value || "").trim();
  if (!patchName) {
    gitPatchPreviewOutput.textContent = "Choose a patch from the inventory first.";
    return;
  }
  gitPatchPreviewOutput.textContent = "Loading patch preview...";
  try {
    const data = await gitToolsRequest("/api/applications/git/patch/read", {patch_name: patchName});
    gitToolsSelectedPatch = patchName;
    gitPatchPreviewOutput.textContent = data.preview || "Patch preview is empty.";
    updateGitWorkflowSectionSummary("patch-actions", `preview loaded: ${patchName}`);
    renderGitPatchGroups(await gitToolsRequest("/api/applications/git/patches", {}));
  } catch (error) {
    gitPatchPreviewOutput.textContent = `Patch preview failed: ${error.message || error}`;
  }
}
async function loadGitDryRun() {
  expandGitWorkflowSection("dry-run", "loading dry-run preview");
  const runName = (gitDryRunName.value || "").trim();
  if (!runName) {
    gitDryRunOutput.textContent = "Run a dry run or select a stored preview.";
    return;
  }
  gitDryRunOutput.textContent = "Loading dry-run preview...";
  try {
    const data = await gitToolsRequest("/api/applications/git/dry-run/read", {run_name: runName});
    gitToolsSelectedDryRun = runName;
    const previewFiles = Array.isArray(data.preview_files) ? data.preview_files.map((item) => item.relative_path).join("\n") : "";
    const deletions = Array.isArray(data.deletions) ? data.deletions.map((item) => item.relative_path).join("\n") : "";
    gitDryRunOutput.textContent = [
      JSON.stringify(data.manifest || {}, null, 2),
      previewFiles ? `Preview files:\n${previewFiles}` : "",
      deletions ? `Deletion markers:\n${deletions}` : "",
    ].filter(Boolean).join("\n\n");
    updateGitWorkflowSectionSummary("dry-run", `dry-run preview loaded: ${runName}`);
    renderGitPatchGroups(await gitToolsRequest("/api/applications/git/patches", {}));
  } catch (error) {
    gitDryRunOutput.textContent = `Dry-run preview failed: ${error.message || error}`;
  }
}
async function runGitPatchDryRun() {
  expandGitWorkflowSection("dry-run", "running patch harness dry run");
  const patchName = (gitPatchName.value || "").trim();
  if (!patchName) {
    gitDryRunOutput.textContent = "Choose a patch before running the harness.";
    return;
  }
  gitDryRunOutput.textContent = "Running patch harness dry run...";
  try {
    const data = await gitToolsRequest("/api/applications/git/patch/apply", {
      patch_name: patchName,
      target_root: gitPatchTarget.value || ".",
      dry_run: true,
      reverse: gitPatchReverse.checked,
      strict_root: false
    });
    const result = data.result || {};
    gitDryRunOutput.textContent = JSON.stringify(result, null, 2);
    if (data.dry_run_output_dir) {
      const parts = String(data.dry_run_output_dir).split(/[\\/]+/);
      const runName = parts[parts.length - 1] || "";
      if (runName) {
        gitDryRunName.value = runName;
        await loadGitDryRun();
      }
    }
    await refreshGitStatus();
    await refreshGitPatches();
  } catch (error) {
    gitDryRunOutput.textContent = `Patch dry run failed: ${error.message || error}`;
  }
}
function gitServerPaneRequested() {
  return Boolean(gitServerPane);
}
function setGitServerPaneVisible(_visible = true, options = {}) {
  if (!gitServerPane) return;
  gitServerPane.hidden = false;
  gitServerPane.open = true;
  updateGitWorkflowSectionSummary("git-server", "visible; shared Gitea controls enabled");
  if (options.persist && window.localStorage) {
    window.localStorage.setItem("mainComputerShowGitServerPane", "1");
  }
}
function toggleGitServerPane() {
  setGitServerPaneVisible(true, {persist: true});
  refreshGitServerStatus();
}
function initializeGitServerHiddenPane() {
  if (!gitServerPane) return;
  setGitServerPaneVisible(true, {persist: false});
  refreshGitServerStatus();
}
function renderGitServerStatus(data) {
  if (!gitServerStatus || !gitServerOutput) return;
  gitServerLastStatus = data || null;
  const state = data?.state || "unknown";
  const docker = data?.docker_available ? "docker available" : "docker unavailable";
  const configured = data?.configured ? "configured" : "not configured";
  const webUrl = data?.web_url || "http://localhost:3000/";
  gitServerStatus.textContent = `Git server: ${state} | ${docker} | ${configured} | ${webUrl}`;
  if (gitServerUrlPill && webUrl) {
    gitServerUrlPill.textContent = webUrl.replace(/\/$/, "");
  }
  if (gitServerOpen && webUrl) {
    gitServerOpen.href = webUrl;
  }
  const cloneExamples = Array.isArray(data?.clone_examples) ? data.clone_examples.join("\n") : "";
  const commandLines = data?.commands ? Object.entries(data.commands).map(([name, command]) => `${name}: ${command}`).join("\n") : "";
  const remotePresetLines = Array.isArray(data?.remote_command_presets)
    ? data.remote_command_presets.map((item) => `${item.name}: ${item.command}`).join("\n")
    : "";
  const psText = data?.ps ? `\n\nCompose ps:\n${data.ps.stdout || ""}${data.ps.stderr || ""}` : "";
  const dockerWarning = data?.docker_available === false ? gitServerDockerUnavailableText(data) : "";
  gitServerOutput.textContent = [
    JSON.stringify({
      service: data?.service,
      profile: data?.profile,
      compose_file: data?.compose_file,
      running: data?.running,
      state,
      web_url: webUrl,
      ssh_available: data?.ssh_available === true,
      docker_available: data?.docker_available,
    }, null, 2),
    dockerWarning ? `Docker unavailable:\n${dockerWarning}` : "",
    cloneExamples ? `Clone examples:\n${cloneExamples}` : "",
    commandLines ? `Commands:\n${commandLines}` : "",
    remotePresetLines ? `Remote command presets:\n${remotePresetLines}` : "",
    psText.trim(),
  ].filter(Boolean).join("\n\n");
  applyGitServerDockerAvailability(data);
  updateGitWorkflowSectionSummary("git-server", `${state}; ${docker}`);
}
const LOCAL_GITEA_REMOTE_NAME = "local-gitea";
let gitServerTargetPrefunk = null;
let gitServerTargetDirty = false;
let gitServerTargetProjectKeyValue = "";

function gitServerSetControlValue(control, value, {markDirty = false} = {}) {
  if (!control) return;
  const next = String(value ?? "");
  if (control.value === next) return;
  control.value = next;
  if (markDirty) gitServerTargetDirty = true;
}
function gitServerCleanRemoteSegment(value, fallback = "") {
  return String(value || fallback).trim().replace(/\s+/g, "-");
}
function gitServerBaseName(path, fallback = "repository") {
  const cleaned = String(path || "").replace(/[\\/]+$/, "");
  const tail = cleaned.split(/[\\/]/).filter(Boolean).pop();
  return gitServerCleanRemoteSegment(tail, fallback) || fallback;
}
function gitServerTargetProjectKeyFromData(data = gitServerTargetPrefunk) {
  return String(data?.git_root || data?.repo_dir || "");
}
function gitServerLocalGiteaUrl(owner, repo, protocol = "http") {
  const cleanOwner = gitServerCleanRemoteSegment(owner, "local") || "local";
  const cleanRepo = gitServerCleanRemoteSegment(repo, "repository") || "repository";
  return `http://localhost:3000/${cleanOwner}/${cleanRepo}.git`;
}
function gitServerTargetFromStatus(status = {}) {
  if (!status?.ok || !status?.is_git_repo || !status?.git_root) {
    return {
      ok: true,
      repo_dir: status?.repo_dir || gitToolsRepoDirValue("."),
      is_git_repo: false,
      has_head: false,
      git_root: "",
      target: {
        remote: LOCAL_GITEA_REMOTE_NAME,
        owner: "local",
        repo: "",
        protocol: "http",
        url: "",
        source: "not-a-git-repo",
        saved: false,
        configurable: false,
        pushable: false,
      },
      reason: status?.reason || "git-init-required",
      error: status?.error || "Selected project is not inside a git worktree.",
    };
  }
  const remotes = Array.isArray(status.remotes) ? status.remotes : [];
  const local = remotes.find((remote) => remote.name === LOCAL_GITEA_REMOTE_NAME);
  const parsed = gitServerParseLocalGiteaUrl(local?.fetch || local?.push || "");
  const target = parsed
    ? {
        remote: LOCAL_GITEA_REMOTE_NAME,
        owner: parsed.owner,
        repo: parsed.repo,
        protocol: parsed.protocol === "ssh" ? "http" : parsed.protocol,
        url: parsed.protocol === "ssh" ? gitServerLocalGiteaUrl(parsed.owner, parsed.repo, "http") : parsed.url,
        legacy_url: parsed.protocol === "ssh" ? parsed.url : "",
        source: parsed.protocol === "ssh" ? "detected-legacy-ssh-local-gitea-remote" : "detected-from-git-remote",
        saved: parsed.protocol !== "ssh",
        configurable: true,
      }
    : {
        remote: LOCAL_GITEA_REMOTE_NAME,
        owner: "local",
        repo: gitServerBaseName(status.git_root),
        protocol: "http",
        url: gitServerLocalGiteaUrl("local", gitServerBaseName(status.git_root), "http"),
        source: "suggested-from-git-root",
        saved: false,
        configurable: true,
      };
  target.pushable = status.has_head !== false;
  return {
    ok: true,
    repo_dir: status.repo_dir || gitToolsRepoDirValue("."),
    is_git_repo: true,
    has_head: status.has_head !== false,
    git_root: status.git_root,
    remotes,
    target,
  };
}
function gitServerParseLocalGiteaUrl(url) {
  const raw = String(url || "").trim();
  let match = raw.match(/^https?:\/\/localhost:3000\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/i);
  if (match) return {protocol: "http", owner: match[1], repo: match[2], url: raw};
  match = raw.match(/^ssh:\/\/git@localhost:2222\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/i);
  if (match) return {protocol: "ssh", owner: match[1], repo: match[2], url: raw};
  return null;
}
function gitServerSetTargetControlsEnabled(enabled) {
  [gitServerOwner, gitServerRepo, gitServerRemoteProtocol].filter(Boolean).forEach((control) => {
    control.disabled = !enabled;
  });
  if (gitServerRemoteName) {
    gitServerRemoteName.value = LOCAL_GITEA_REMOTE_NAME;
    gitServerRemoteName.disabled = true;
    gitServerRemoteName.readOnly = true;
  }
}
function gitServerSetLocalActionAvailability({configurable = true, pushable = true, reason = ""} = {}) {
  const dockerUnavailable = Boolean(gitServerLastStatus && gitServerLastStatus.docker_available === false);
  if (gitServerRemoteApplyLocal) {
    gitServerRemoteApplyLocal.disabled = dockerUnavailable || !configurable;
    gitServerRemoteApplyLocal.setAttribute("aria-disabled", gitServerRemoteApplyLocal.disabled ? "true" : "false");
    gitServerRemoteApplyLocal.title = !configurable ? reason : (dockerUnavailable ? "Docker CLI is not available where Main Computer is running." : "");
  }
  if (gitServerPushLocal) {
    gitServerPushLocal.disabled = dockerUnavailable || !pushable;
    gitServerPushLocal.setAttribute("aria-disabled", gitServerPushLocal.disabled ? "true" : "false");
    gitServerPushLocal.title = !pushable ? reason : (dockerUnavailable ? "Docker CLI is not available where Main Computer is running." : "");
  }
}
function gitServerTargetNote() {
  if (!gitServerTargetPrefunk) return "Checking selected project before showing a target.";
  if (!gitServerTargetPrefunk.is_git_repo) return "Selected project is not a Git repo; no Local Gitea target is suggested or saved.";
  if (gitServerTargetDirty) return "Edited in this pane; not saved until Create / verify repo + configure remote succeeds.";
  const source = gitServerTargetPrefunk.target?.source || "";
  if (source === "detected-legacy-ssh-local-gitea-remote") return "Detected an old SSH local-gitea remote; configure remote to replace it with HTTP localhost:3000.";
  if (source === "detected-from-git-remote") return "Detected from this repo's local-gitea remote; this is saved.";
  if (source === "suggested-from-git-root") return "Suggested from Git root; not saved until Create / verify repo + configure remote succeeds.";
  return "Fixed remote name; repository is a suggested project target until configured.";
}
function gitServerTargetUnavailableReason(data = gitServerTargetPrefunk) {
  if (!data || data.is_git_repo) return "";
  return "Select or initialize a Git repo before configuring Local Gitea.";
}
function gitServerCurrentTargetUrl() {
  const repo = (gitServerRepo?.value || "").trim();
  if (!repo) return "";
  return gitServerLocalGiteaUrl(gitServerOwner?.value || "local", repo, gitServerRemoteProtocol?.value || "http");
}
function gitServerRenderTargetPreview() {
  const mode = gitServerRemoteModeValue();
  const switchOrigin = mode === "switch-origin";
  const localRemote = switchOrigin ? "origin" : LOCAL_GITEA_REMOTE_NAME;
  const unavailable = Boolean(gitServerTargetPrefunk && !gitServerTargetPrefunk.is_git_repo);
  const url = unavailable ? "" : gitServerCurrentTargetUrl();
  const note = switchOrigin && !unavailable
    ? "Replacing origin makes Local Gitea the primary remote for this checkout."
    : gitServerTargetNote();
  if (gitServerRemoteModePill) {
    gitServerRemoteModePill.textContent = switchOrigin ? "replaces origin" : "second remote";
  }
  if (gitServerTargetPreview) {
    const source = document.createElement("span");
    source.className = "gitea-target-source";
    source.id = "git-server-target-source";
    source.textContent = note;
    gitServerTargetPreview.replaceChildren(
      document.createTextNode("Local Gitea target: "),
      Object.assign(document.createElement("code"), {textContent: unavailable ? "unavailable" : localRemote}),
      document.createTextNode(url ? " → " : " — "),
      Object.assign(document.createElement("code"), {textContent: url || "no Git repo selected"}),
      source
    );
  }
  if (gitServerFixedRemotePreview) gitServerFixedRemotePreview.textContent = LOCAL_GITEA_REMOTE_NAME;
  if (gitServerAddLocalNamePreview) gitServerAddLocalNamePreview.textContent = LOCAL_GITEA_REMOTE_NAME;
  if (gitServerAddLocalUrlPreview) gitServerAddLocalUrlPreview.textContent = url || "not available";
  if (gitServerAddLocalPushPreview) gitServerAddLocalPushPreview.textContent = `git push ${LOCAL_GITEA_REMOTE_NAME} HEAD`;
  if (gitServerOriginUrlPreview) gitServerOriginUrlPreview.textContent = url || "not available";
  if (gitServerRemoteCommand && !url) gitServerRemoteCommand.value = "git remote -v";
}
function gitServerApplyTargetPrefunk(data, {announce = false, preserveEdited = true} = {}) {
  const previousKey = gitServerTargetProjectKeyFromData();
  const nextKey = gitServerTargetProjectKeyFromData(data);
  const projectChanged = previousKey && nextKey && previousKey !== nextKey;
  gitServerTargetPrefunk = data || null;
  if (projectChanged) gitServerTargetDirty = false;
  gitServerTargetProjectKeyValue = nextKey;
  if (gitServerRemoteMode) gitServerRemoteMode.value = "add-local";
  if (gitServerRemoteName) {
    gitServerRemoteName.value = LOCAL_GITEA_REMOTE_NAME;
    gitServerRemoteName.disabled = true;
    gitServerRemoteName.readOnly = true;
  }

  if (!data?.is_git_repo) {
    gitServerTargetDirty = false;
    gitServerSetTargetControlsEnabled(false);
    gitServerSetControlValue(gitServerOwner, "local");
    gitServerSetControlValue(gitServerRepo, "");
    if (gitServerRepo) gitServerRepo.placeholder = "No Git repo selected";
    gitServerSetControlValue(gitServerRemoteProtocol, "http");
    gitServerSetLocalActionAvailability({
      configurable: false,
      pushable: false,
      reason: gitServerTargetUnavailableReason(data),
    });
    gitServerRenderTargetPreview();
    if (announce && gitServerOutput) {
      gitServerOutput.textContent = "Local Gitea target unavailable: selected project is not a Git repository yet.";
    }
    return;
  }

  gitServerSetTargetControlsEnabled(true);
  if (gitServerRepo) gitServerRepo.placeholder = "repository";
  const target = data.target || {};
  if (!preserveEdited || !gitServerTargetDirty) {
    gitServerSetControlValue(gitServerOwner, target.owner || "local");
    gitServerSetControlValue(gitServerRepo, target.repo || gitServerBaseName(data.git_root));
    gitServerSetControlValue(gitServerRemoteProtocol, target.protocol || "http");
  }
  gitServerSetLocalActionAvailability({
    configurable: true,
    pushable: data.has_head !== false,
    reason: data.has_head === false ? "Create an initial commit before pushing to Local Gitea." : "",
  });
  updateGitServerRemoteMode();
  if (announce && gitServerOutput) {
    gitServerOutput.textContent = target.saved
      ? `Local Gitea target detected from this repo's ${LOCAL_GITEA_REMOTE_NAME} remote.`
      : `Local Gitea target suggested from Git root: ${target.repo || gitServerBaseName(data.git_root)}. Not saved yet.`;
  }
}
async function refreshGitServerTargetPrefunk(options = {}) {
  const repoDir = gitToolsRepoDirValue(".");
  try {
    const data = await gitToolsRequest("/api/applications/git/server/target-prefunk", {repo_dir: repoDir});
    gitServerApplyTargetPrefunk(data, options);
    return data;
  } catch (error) {
    const data = {
      ok: true,
      repo_dir: repoDir,
      is_git_repo: false,
      has_head: false,
      target: {
        remote: LOCAL_GITEA_REMOTE_NAME,
        owner: "local",
        repo: "",
        protocol: "http",
        url: "",
        source: "not-a-git-repo",
        saved: false,
        configurable: false,
        pushable: false,
      },
      error: error.message || String(error),
    };
    gitServerApplyTargetPrefunk(data, options);
    return data;
  }
}
function clearGitServerTargetForProjectChange() {
  gitServerTargetPrefunk = {
    ok: true,
    repo_dir: gitToolsRepoDirValue("."),
    is_git_repo: false,
    has_head: false,
    git_root: "",
    target: {
      remote: LOCAL_GITEA_REMOTE_NAME,
      owner: "local",
      repo: "",
      protocol: "http",
      url: "",
      source: "checking-selected-project",
      saved: false,
      configurable: false,
      pushable: false,
    },
  };
  gitServerTargetDirty = false;
  gitServerTargetProjectKeyValue = "";
  gitServerSetTargetControlsEnabled(false);
  gitServerSetControlValue(gitServerOwner, "local");
  gitServerSetControlValue(gitServerRepo, "");
  if (gitServerRepo) gitServerRepo.placeholder = "Checking selected project…";
  gitServerSetControlValue(gitServerRemoteProtocol, "http");
  gitServerSetLocalActionAvailability({
    configurable: false,
    pushable: false,
    reason: "Checking selected project Git state.",
  });
  gitServerRenderTargetPreview();
}
function gitServerEnsureConfigurable(actionLabel) {
  if (gitServerTargetPrefunk && !gitServerTargetPrefunk.is_git_repo) {
    if (gitServerOutput) gitServerOutput.textContent = `${actionLabel} cannot run.

Select or initialize a Git repo before configuring Local Gitea.`;
    updateGitWorkflowSectionSummary("git-server", "git repo required");
    gitServerApplyTargetPrefunk(gitServerTargetPrefunk, {preserveEdited: false});
    return false;
  }
  return true;
}
function gitServerEnsurePushable() {
  if (!gitServerEnsureConfigurable("Push to Local Gitea")) return false;
  if (gitServerTargetPrefunk && gitServerTargetPrefunk.has_head === false) {
    if (gitServerOutput) gitServerOutput.textContent = "Push to Local Gitea cannot run. Create an initial commit before pushing.";
    updateGitWorkflowSectionSummary("git-server", "initial commit required");
    gitServerSetLocalActionAvailability({
      configurable: true,
      pushable: false,
      reason: "Create an initial commit before pushing to Local Gitea.",
    });
    return false;
  }
  return true;
}
function gitServerMarkTargetEdited() {
  if (gitServerTargetPrefunk && !gitServerTargetPrefunk.is_git_repo) return;
  gitServerTargetDirty = true;
  gitServerRenderTargetPreview();
}

function gitServerRemoteModeValue() {
  return gitServerRemoteMode?.value || "add-local";
}
function gitServerSwitchesOrigin() {
  return gitServerRemoteModeValue() === "switch-origin";
}
function gitServerRemoteNameValue() {
  if (gitServerSwitchesOrigin()) return "origin";
  return LOCAL_GITEA_REMOTE_NAME;
}
function gitServerExternalRemoteNameValue() {
  return (gitServerExternalRemoteName?.value || "origin").trim() || "origin";
}
function gitServerRemoteToken(control, label) {
  const value = (control?.value || "").trim();
  if (!value) throw new Error(`Enter a Gitea ${label} first.`);
  return value.replace(/\s+/g, "-");
}
function gitServerRemoteUrl() {
  const owner = gitServerRemoteToken(gitServerOwner, "owner");
  const repo = gitServerRemoteToken(gitServerRepo, "repository");
  return `http://localhost:3000/${owner}/${repo}.git`;
}
function gitServerRemoteUrlPreviewValue() {
  const repo = (gitServerRepo?.value || "").trim();
  if (!repo) return "";
  return gitServerCurrentTargetUrl();
}
function updateGitServerRemoteChoicePreview() {
  const mode = gitServerRemoteModeValue();
  gitServerRemoteChoiceButtons.forEach((choice) => {
    choice.checked = choice.value === mode;
  });
  gitServerRenderTargetPreview();
}
function setGitServerRemoteMode(mode) {
  if (!gitServerRemoteMode) return;
  gitServerRemoteMode.value = mode === "switch-origin" ? "switch-origin" : "add-local";
  updateGitServerRemoteMode();
}
function gitServerLocalRepoName() {
  const candidates = [
    gitToolsLastStatus?.git_root,
    gitToolsRepoDirValue(""),
    window.location?.pathname || "",
    "main_computer_test",
  ];
  for (const candidate of candidates) {
    const cleaned = String(candidate || "").replace(/[\\/]+$/, "");
    const tail = cleaned.split(/[\\/]/).filter(Boolean).pop();
    if (tail && tail !== "." && tail !== "git") {
      return tail.replace(/\s+/g, "-");
    }
  }
  return "main_computer_test";
}
function gitServerHasNonLocalOrigin() {
  const remotes = Array.isArray(gitToolsLastStatus?.remotes) ? gitToolsLastStatus.remotes : [];
  const origin = remotes.find((remote) => remote.name === "origin");
  const url = origin?.fetch || origin?.push || "";
  return Boolean(url && !/localhost:3000|localhost:2222/.test(url));
}
function gitServerIsLocalServerUrl(url) {
  return /(^|@|\/)localhost:(3000|2222)(\/|:|$)/.test(String(url || ""));
}
function gitServerBestExternalUrl() {
  const remotes = Array.isArray(gitToolsLastStatus?.remotes) ? gitToolsLastStatus.remotes : [];
  const origin = remotes.find((remote) => remote.name === "origin");
  const originUrl = origin?.fetch || origin?.push || "";
  if (originUrl && !gitServerIsLocalServerUrl(originUrl)) return originUrl;
  const external = remotes.find((remote) => {
    const url = remote.fetch || remote.push || "";
    return url && !gitServerIsLocalServerUrl(url);
  });
  return external?.fetch || external?.push || "";
}
function updateGitServerRemoteMode() {
  const switchOrigin = gitServerSwitchesOrigin();
  if (gitServerRemoteName) {
    gitServerRemoteName.value = switchOrigin ? "origin" : LOCAL_GITEA_REMOTE_NAME;
    gitServerRemoteName.disabled = !switchOrigin || !gitServerTargetPrefunk?.is_git_repo;
    gitServerRemoteName.readOnly = !switchOrigin;
  }
  if (gitServerRemoteCommand) {
    try {
      gitServerRemoteCommand.value = gitServerRemoteCommandForPreset(switchOrigin ? "set-url" : "add-remote");
    } catch (_error) {
      gitServerRemoteCommand.value = "git remote -v";
    }
  }
  updateGitServerRemoteChoicePreview();
}
async function useLocalGitServerRemote() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "reviewing local Gitea target");
  if (gitServerRemoteMode) {
    gitServerRemoteMode.value = "add-local";
  }
  if (gitServerRemoteName) {
    gitServerRemoteName.disabled = true;
    gitServerRemoteName.readOnly = true;
    gitServerRemoteName.value = LOCAL_GITEA_REMOTE_NAME;
  }
  const target = await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
  if (gitServerExternalUrl && !gitServerExternalUrl.value.trim()) {
    gitServerExternalUrl.value = gitServerBestExternalUrl();
  }
  updateGitServerRemoteMode();
  if (gitServerOutput) {
    if (!target?.is_git_repo) {
      gitServerOutput.textContent = [
        "Local Gitea target is not available yet.",
        "",
        "The selected project is not a Git repository.",
        "Initialize Git or select a Git repo before configuring Local Gitea.",
      ].join("\n");
    } else {
      gitServerOutput.textContent = [
        target.target?.saved ? "Saved Local Gitea target detected." : "Suggested Local Gitea target is ready.",
        "",
        "Mode: keep origin unchanged and add Local Gitea as a second remote",
        `Remote: ${LOCAL_GITEA_REMOTE_NAME}`,
        `URL: ${gitServerCurrentTargetUrl()}`,
        "",
        "Click Create / verify repo + configure remote to start Gitea, create/verify the repo, and save the local-gitea remote.",
        target.has_head === false ? "Create an initial commit before pushing HEAD to local Gitea." : "Then click Push to Local Gitea when you want to publish HEAD to local Gitea.",
      ].join("\n");
    }
  }
  updateGitWorkflowSectionSummary("git-server", target?.is_git_repo ? "local target ready" : "git repo required");
}
function gitServerRemoteConfigPayload() {
  return {
    repo_dir: gitToolsRepoDirValue("."),
    remote: gitServerRemoteNameValue(),
    owner: gitServerRemoteToken(gitServerOwner, "owner"),
    repo: gitServerRemoteToken(gitServerRepo, "repository"),
    protocol: gitServerRemoteProtocol?.value || "http",
    switch_origin: gitServerSwitchesOrigin(),
  };
}
function gitServerRemoteCommandForPreset(preset) {
  const remote = gitServerRemoteNameValue();
  switch (preset) {
    case "add-remote":
      return `git remote add ${remote} ${gitServerRemoteUrl()}`;
    case "set-url":
      return `git remote set-url ${remote} ${gitServerRemoteUrl()}`;
    case "push-head":
      return `git push -u ${remote} HEAD`;
    case "fetch":
      return `git fetch ${remote}`;
    case "show-remotes":
      return "git remote -v";
    default:
      throw new Error(`Unknown git remote preset: ${preset}`);
  }
}
function fillGitServerRemoteCommand(preset) {
  setGitServerPaneVisible(true, {persist: true});
  try {
    const command = gitServerRemoteCommandForPreset(preset);
    if (gitServerRemoteCommand) {
      gitServerRemoteCommand.value = command;
      gitServerRemoteCommand.focus();
    }
    if (gitServerOutput) {
      gitServerOutput.textContent = `Prepared preset ${preset}:\n${command}\n\nReview the command, then click Apply Command.`;
    }
    updateGitWorkflowSectionSummary("git-server", `remote preset ready: ${preset}`);
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Remote preset failed: ${error.message || error}`;
  }
}
function copyGitServerRemoteCommandToConsole() {
  const command = (gitServerRemoteCommand?.value || "").trim();
  if (!command) {
    if (gitServerOutput) gitServerOutput.textContent = "Choose a remote preset or enter a command first.";
    return;
  }
  if (gitConsoleInput) gitConsoleInput.value = command;
  expandGitWorkflowSection("ai-interpretation", "remote command copied to Git Console");
  if (gitServerOutput) gitServerOutput.textContent = `Copied to Git Console:\n${command}`;
}
async function applyLocalGitServerRemote() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "setting up local git server");
  if (!gitServerTargetPrefunk) await refreshGitServerTargetPrefunk({announce: false});
  if (!gitServerEnsureConfigurable("Create / verify repo + configure remote")) return;
  if (!(await ensureGitServerDockerAvailable("Create / verify repo + configure remote"))) return;
  let payload;
  try {
    payload = gitServerRemoteConfigPayload();
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Local server setup is incomplete: ${error.message || error}`;
    return;
  }
  const url = gitServerCurrentTargetUrl();
  const progress = startGitServerProgress(
    "Setting up local Git server...",
    [
      "This will start the standalone Docker Gitea stack if needed, create/verify the local Gitea repo, and configure the selected remote.",
      "",
      `Remote: ${payload.remote}`,
      `URL: ${url}`,
    ],
    ["starting/verifying standalone Gitea", "checking local Gitea user", "creating/verifying repository", "configuring local-gitea remote"]
  );
  try {
    const data = await runGitServerOperationRequest("/api/applications/git/server/setup-local", payload, [
      "Setting up local Git server...",
      `Remote: ${payload.remote}`,
      `URL: ${url}`,
    ]);
    progress.stop();
    if (!data) return;
    gitServerTargetDirty = false;
    if (gitServerRemoteCommand) {
      const verb = data.mode === "switch-origin" ? "set-url" : (data.action === "set-url" ? "set-url" : "add");
      gitServerRemoteCommand.value = verb === "set-url"
        ? `git remote set-url ${data.remote} ${data.url}`
        : `git remote add ${data.remote} ${data.url}`;
    }
    if (gitServerOutput) gitServerOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
    await refreshGitStatus();
    if (data.status) renderGitServerStatus(data.status);
    updateGitWorkflowSectionSummary("git-server", data.ok ? "local server ready" : "local server setup failed");
  } catch (error) {
    progress.stop();
    if (gitServerOutput) gitServerOutput.textContent = gitToolsOperationErrorText("Create / verify repo + configure remote failed", error);
    updateGitWorkflowSectionSummary("git-server", "local server setup failed");
  }
}
async function pushLocalGitServerRemote() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "pushing to local git server");
  if (!gitServerTargetPrefunk) await refreshGitServerTargetPrefunk({announce: false});
  if (!gitServerEnsurePushable()) return;
  if (!(await ensureGitServerDockerAvailable("Push to Local Gitea"))) return;
  let payload;
  try {
    payload = gitServerRemoteConfigPayload();
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Local push is incomplete: ${error.message || error}`;
    return;
  }
  const url = gitServerCurrentTargetUrl();
  const progress = startGitServerProgress(
    "Preparing local Git server and pushing HEAD...",
    [
      "The push uses a temporary local Gitea token and does not save that token in the remote URL.",
      "",
      `Remote: ${payload.remote}`,
      `URL: ${url}`,
    ],
    ["starting/verifying standalone Gitea", "creating/verifying repository", "creating temporary push token", "running git push -u HEAD"]
  );
  try {
    const data = await runGitServerOperationRequest("/api/applications/git/server/push-local", payload, [
      "Preparing local Git server and pushing HEAD...",
      `Remote: ${payload.remote}`,
      `URL: ${url}`,
    ]);
    progress.stop();
    if (!data) return;
    gitServerTargetDirty = false;
    if (gitServerOutput) gitServerOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
    await refreshGitStatus();
    if (data.status) renderGitServerStatus(data.status);
    updateGitWorkflowSectionSummary("git-server", data.ok ? "pushed to local server" : "local push failed");
  } catch (error) {
    progress.stop();
    if (gitServerOutput) gitServerOutput.textContent = gitToolsOperationErrorText("Push to Local Gitea failed", error);
    updateGitWorkflowSectionSummary("git-server", "local push failed");
  }
}
function useExternalGitRemoteDirect() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "external direct remote ready");
  const externalUrl = (gitServerExternalUrl?.value || "").trim();
  const externalRemote = gitServerExternalRemoteNameValue();
  if (!externalUrl) {
    if (gitServerOutput) {
      gitServerOutput.textContent = "Enter a GitHub, GitLab, Gitea Cloud, or other Git URL first. Example: https://github.com/owner/repo.git";
    }
    return;
  }
  const command = `git remote set-url ${externalRemote} ${externalUrl}`;
  if (gitServerRemoteCommand) {
    gitServerRemoteCommand.value = command;
    gitServerRemoteCommand.focus();
  }
  if (gitServerOutput) {
    gitServerOutput.textContent = [
      "External direct remote command prepared.",
      "",
      command,
      "",
      "Click Apply Command to point this checkout directly at the external service, bypassing local Gitea.",
    ].join("\n");
  }
}
async function planGiteaPushMirror() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "planning server mirror");
  const externalUrl = (gitServerMirrorUrl?.value || gitServerExternalUrl?.value || "").trim();
  if (!externalUrl) {
    if (gitServerOutput) gitServerOutput.textContent = "Enter a server push mirror URL first.";
    return;
  }
  let payload;
  try {
    payload = {
      owner: gitServerRemoteToken(gitServerOwner, "owner"),
      repo: gitServerRemoteToken(gitServerRepo, "repository"),
      external_url: externalUrl,
      external_username: (gitServerMirrorUsername?.value || "").trim(),
    };
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Server mirror plan is incomplete: ${error.message || error}`;
    return;
  }
  try {
    const data = await gitToolsRequest("/api/applications/git/server/mirror/plan", payload);
    if (gitServerOutput) gitServerOutput.textContent = JSON.stringify(data, null, 2);
    updateGitWorkflowSectionSummary("git-server", "server mirror plan ready");
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Server mirror plan failed: ${error.message || error}`;
    updateGitWorkflowSectionSummary("git-server", "server mirror plan failed");
  }
}
async function setupGiteaPushMirror() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "setting up server mirror");
  if (!(await ensureGitServerDockerAvailable("Set Up Server → External Mirror"))) return;
  const externalUrl = (gitServerMirrorUrl?.value || gitServerExternalUrl?.value || "").trim();
  const externalPassword = (gitServerMirrorPassword?.value || "").trim();
  if (!externalUrl) {
    if (gitServerOutput) gitServerOutput.textContent = "Enter a server push mirror URL first.";
    return;
  }
  if (!externalPassword) {
    if (gitServerOutput) {
      gitServerOutput.textContent = [
        "Enter the external token/password first.",
        "Gitea stores that credential for the push mirror. It is not written to .git/config or the manual command box.",
      ].join("\n");
    }
    return;
  }
  let payload;
  try {
    payload = {
      owner: gitServerRemoteToken(gitServerOwner, "owner"),
      repo: gitServerRemoteToken(gitServerRepo, "repository"),
      external_url: externalUrl,
      external_username: (gitServerMirrorUsername?.value || "").trim(),
      external_password: externalPassword,
    };
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Server mirror setup is incomplete: ${error.message || error}`;
    return;
  }
  const progress = startGitServerProgress(
    "Setting up local Gitea push mirror...",
    [
      "This starts/verifies the standalone local Gitea stack and creates the local repository before adding the external push mirror.",
      "The external credential is sent to local Gitea for the mirror configuration.",
      "",
      `External URL: ${externalUrl}`,
    ],
    ["starting/verifying local Gitea", "creating/verifying local repository", "checking existing push mirrors", "creating push mirror"]
  );
  try {
    const data = await runGitServerOperationRequest("/api/applications/git/server/mirror/setup", payload, [
      "Setting up local Gitea push mirror...",
      `External URL: ${externalUrl}`,
    ]);
    progress.stop();
    if (!data) return;
    if (gitServerOutput) gitServerOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitStatus();
    updateGitWorkflowSectionSummary("git-server", data.ok ? "server mirror ready" : "server mirror setup failed");
  } catch (error) {
    progress.stop();
    if (gitServerOutput) gitServerOutput.textContent = gitToolsOperationErrorText("Server mirror setup failed", error);
    updateGitWorkflowSectionSummary("git-server", "server mirror setup failed");
  }
}
async function runGitServerRemoteCommand() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "running remote command");
  const command = (gitServerRemoteCommand?.value || "").trim();
  if (!command) {
    if (gitServerOutput) gitServerOutput.textContent = "Choose a remote preset or enter a command first.";
    return;
  }
  if (gitServerOutput) gitServerOutput.textContent = `Running local git command:\n${command}`;
  try {
    const data = await runGitServerOperationRequest("/api/applications/git/console/run", {command, repo_dir: gitToolsRepoDirValue(".")}, [
      `Running local git command: ${command}`,
      `Repository path: ${gitToolsRepoDirValue(".")}`,
    ]);
    if (!data) return;
    if (gitServerOutput) gitServerOutput.textContent = JSON.stringify(data, null, 2);
    if (data.shim?.id && gitShimId) {
      gitToolsSelectedShim = data.shim.id;
      gitShimId.value = data.shim.id;
      await refreshGitShims();
    }
    await refreshGitStatus();
    updateGitWorkflowSectionSummary("git-server", `remote command ${data.ok ? "finished" : "failed"}`);
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Remote command failed: ${error.message || error}`;
    updateGitWorkflowSectionSummary("git-server", "remote command failed");
  }
}

function initializeGitServerRemoteComposer() {
  if (!gitServerRemoteCommand) return;
  if (!gitServerRemoteCommand.value.trim()) {
    gitServerRemoteCommand.value = "git remote -v";
  }
  gitServerRemoteChoiceButtons.forEach((choice) => {
    choice.addEventListener("change", () => {
      if (choice.checked) {
        setGitServerRemoteMode(choice.value);
      }
    });
  });
  [gitServerOwner, gitServerRepo, gitServerRemoteProtocol].filter(Boolean).forEach((control) => {
    control.addEventListener("input", gitServerMarkTargetEdited);
    control.addEventListener("change", gitServerMarkTargetEdited);
  });
  if (gitServerRemoteName) {
    gitServerRemoteName.addEventListener("input", updateGitServerRemoteChoicePreview);
    gitServerRemoteName.addEventListener("change", updateGitServerRemoteChoicePreview);
  }
  updateGitServerRemoteMode();
  gitServerRemoteCommand.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      runGitServerRemoteCommand();
    }
  });
}
async function refreshGitServerStatus() {
  if (!gitServerPane) return;
  if (gitServerStatus) gitServerStatus.textContent = "Loading Git server status...";
  try {
    const data = await gitToolsRequest("/api/applications/git/server/status", {});
    renderGitServerStatus(data);
  } catch (error) {
    if (gitServerStatus) gitServerStatus.textContent = `Git server status failed: ${error.message || error}`;
    if (gitServerOutput) gitServerOutput.textContent = String(error.message || error);
  }
}
async function runGitServerAction(action) {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", `running ${action}`);
  if (!(await ensureGitServerDockerAvailable(`Git server ${action}`))) return;
  if (gitServerOutput) gitServerOutput.textContent = `Running Docker git server action: ${action}...`;
  try {
    const data = await runGitServerOperationRequest("/api/applications/git/server/action", {action}, [
      `Running Docker git server action: ${action}`,
    ]);
    if (!data) return;
    if (gitServerOutput) {
      gitServerOutput.textContent = JSON.stringify(data, null, 2);
    }
    if (data.status) {
      renderGitServerStatus(data.status);
    } else if (action === "logs") {
      updateGitWorkflowSectionSummary("git-server", "logs loaded");
    } else {
      await refreshGitServerStatus();
    }
  } catch (error) {
    if (gitServerStatus) gitServerStatus.textContent = `Git server ${action} failed: ${error.message || error}`;
    if (gitServerOutput) gitServerOutput.textContent = String(error.message || error);
  }
}
async function refreshGitTools() {
  await loadGitProjects().catch(() => null);
  await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false}).catch(() => null);
  await refreshGitStatus();
  await refreshGitPatches();
  await refreshGitShims();
  await refreshGitServerStatus();
}
