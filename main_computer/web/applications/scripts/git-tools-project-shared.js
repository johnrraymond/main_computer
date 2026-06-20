(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.project-shared";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-project-shared.js";


function gitProjectWorkflowIntegration() {
  const workflow = globalThis.GitToolsProjectWorkflow;
  if (!workflow) {
    throw new Error("GitToolsProjectWorkflow module is not loaded.");
  }
  return workflow;
}

function gitProjectWorkflowHooks() {
  return {
    actionKey: gitProjectActionKey,
    actionStatusLabel: gitProjectActionStatusLabel,
    commitCardTitle: gitProjectCommitCardTitle,
    isCommitCard: gitProjectStepIsCommitCard,
  };
}

const gitProjectWizardActionMap = new Map();

function gitToolsStatusApi() {
  const api = globalThis.GitToolsStatusApi;
  if (!api) {
    throw new Error("GitToolsStatusApi module is not loaded.");
  }
  return api;
}

function gitToolsRequest(path, payload = {}) {
  return gitToolsStatusApi().request(path, payload);
}

function gitToolsOperationErrorText(prefix, error) {
  return gitToolsStatusApi().operationErrorText(prefix, error);
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
  const data = await gitToolsStatusApi().runProjectAction({
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
  const data = await gitToolsStatusApi().addProject({path, select: true});
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
  return gitProjectWorkflowIntegration().firstActionableWizardStep(wizard);
}
function humanizeGitProjectToken(value = "") {
  return gitProjectWorkflowIntegration().humanizeToken(value);
}
function gitProjectActionKey(step = {}, scope = "wizard") {
  return gitProjectWorkflowIntegration().actionKey(step, scope);
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
function gitProjectFirstCommitGateOrder(step = {}) {
  const id = gitProjectStepId(step);
  if (id === "update_gitignore_before_initial_commit" || id === "ignore_generated_files" || id === "ignore_local_environment_files") return 10;
  if (id === "secrets_filter") return 20;
  if (["prepare_commit_snapshot", "create_initial_snapshot"].includes(id)) return 30;
  return Number.POSITIVE_INFINITY;
}
function weightForWizardStep(step = {}, data = {}) {
  return gitProjectWorkflowIntegration().weightForWizardStep(step, data);
}
function gitProjectStepId(step = {}) {
  return gitProjectWorkflowIntegration().stepId(step);
}
function gitProjectStepKind(step = {}) {
  return gitProjectWorkflowIntegration().stepKind(step);
}
function gitProjectRemoteStepIsCurrentlyRequired(data = {}) {
  return gitProjectWorkflowIntegration().remoteStepIsCurrentlyRequired(data, gitProjectWorkflowHooks());
}
function gitProjectStepIsReadOnlyEvidence(step = {}, data = {}) {
  return gitProjectWorkflowIntegration().stepIsReadOnlyEvidence(step, data, gitProjectWorkflowHooks());
}
function gitProjectStepIsUserAction(step = {}, data = {}) {
  return gitProjectWorkflowIntegration().stepIsUserAction(step, data, gitProjectWorkflowHooks());
}
function gitProjectStepBlockedReason(step = {}, data = {}) {
  return gitProjectWorkflowIntegration().stepBlockedReason(step, data, gitProjectWorkflowHooks());
}
function classifyGitProjectWizardStep(step = {}, data = {}, actionKey = "") {
  return gitProjectWorkflowIntegration().classifyWizardStep(step, data, actionKey, gitProjectWorkflowHooks());
}
function toneForWizardStep(step = {}, data = {}) {
  return gitProjectWorkflowIntegration().toneForWizardStep(step, data, gitProjectWorkflowHooks());
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
  return gitProjectWorkflowIntegration().visibleStepLabel(step, gitProjectWorkflowHooks());
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

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    gitProjectWorkflowIntegration,
    gitProjectWorkflowHooks,
    gitProjectWizardActionMap,
    gitToolsStatusApi,
    gitToolsRequest,
    gitToolsOperationErrorText,
    currentGitProject,
    gitProjectNormalizePathForCompare,
    gitProjectSamePath,
    gitProjectSetTargetPathInputs,
    gitToolsRepoDirValue,
    gitProjectDescribeTargetSources,
    gitProjectCommitTargetMismatch,
    setGitProjectNextStep,
    gitProjectRepoBoundaryPrompted,
    gitProjectRepoBoundarySelectedPath,
    gitProjectRepoBoundaryParentRoot,
    gitProjectNeedsRepoBoundaryChoice,
    gitProjectRepoBoundaryActionHtml,
    maybePromptGitProjectRepoBoundary,
    closeGitProjectRepoBoundaryModal,
    gitProjectRepoBoundaryModalStatus,
    gitProjectSetRepoBoundaryButtonsDisabled,
    gitProjectRepoBoundaryInitCommands,
    gitProjectInitializeSelectedFolderFromBoundary,
    gitProjectUseParentRepositoryFromBoundary,
    openGitProjectRepoBoundaryModal,
    nextGitProjectStepText,
    renderGitProjectNextStep,
    renderKeyValue,
    dirtySummaryRows,
    formatCommandForReport,
    firstActionableWizardStep,
    humanizeGitProjectToken,
    gitProjectActionKey,
    GIT_PROJECT_MC_FEATURE_ID,
    gitProjectMcSlug,
    gitProjectMcAttribute,
    gitProjectMcComponentAttrs,
    gitProjectWizardStepComponentId,
    gitProjectShellQuote,
    gitProjectSlashPath,
    gitProjectRuntimeContext,
    gitProjectCommandText,
    gitProjectStepCommands,
    gitProjectInitialSnapshotCommands,
    gitProjectCommandsForStep,
    GIT_PROJECT_HEAD_FIX_RUNNER_HINT,
    GIT_PROJECT_HEAD_FIX_STEP_IDS,
    gitProjectPanelStateForStep,
    gitProjectCommandLinesForStep,
    gitProjectStepUsesHeadFixRunner,
    gitProjectCommandDetailsForStep,
    gitProjectExecutableLinesFromCommands,
    gitProjectCommandIsRunnable,
    gitProjectRunnableCommandInfo,
    gitProjectFirstCommitGateOrder,
    weightForWizardStep,
    gitProjectStepId,
    gitProjectStepKind,
    gitProjectRemoteStepIsCurrentlyRequired,
    gitProjectStepIsReadOnlyEvidence,
    gitProjectStepIsUserAction,
    gitProjectStepBlockedReason,
    classifyGitProjectWizardStep,
    toneForWizardStep,
    gitProjectCardSelector,
    gitProjectStepIsCommitCard,
    gitProjectStepIsArchiveCard,
    gitProjectArchiveCardTitle,
    gitProjectCommitCardTitle,
    gitProjectVisibleStepLabel,
    gitProjectOpenCardButtonLabel,
    gitProjectCommitCardAttachmentHtml,
    gitProjectClosedCardPurpose,
    gitProjectClosedCardChips,
    gitProjectClosedCardSummaryHtml,
    gitProjectStepSupportsCardSubscreen,
    gitProjectPathChips
  });

  global.GitToolsProjectShared = api;
  Object.assign(global, {
    gitProjectWorkflowIntegration,
    gitProjectWorkflowHooks,
    gitProjectWizardActionMap,
    gitToolsStatusApi,
    gitToolsRequest,
    gitToolsOperationErrorText,
    currentGitProject,
    gitProjectNormalizePathForCompare,
    gitProjectSamePath,
    gitProjectSetTargetPathInputs,
    gitToolsRepoDirValue,
    gitProjectDescribeTargetSources,
    gitProjectCommitTargetMismatch,
    setGitProjectNextStep,
    gitProjectRepoBoundaryPrompted,
    gitProjectRepoBoundarySelectedPath,
    gitProjectRepoBoundaryParentRoot,
    gitProjectNeedsRepoBoundaryChoice,
    gitProjectRepoBoundaryActionHtml,
    maybePromptGitProjectRepoBoundary,
    closeGitProjectRepoBoundaryModal,
    gitProjectRepoBoundaryModalStatus,
    gitProjectSetRepoBoundaryButtonsDisabled,
    gitProjectRepoBoundaryInitCommands,
    gitProjectInitializeSelectedFolderFromBoundary,
    gitProjectUseParentRepositoryFromBoundary,
    openGitProjectRepoBoundaryModal,
    nextGitProjectStepText,
    renderGitProjectNextStep,
    renderKeyValue,
    dirtySummaryRows,
    formatCommandForReport,
    firstActionableWizardStep,
    humanizeGitProjectToken,
    gitProjectActionKey,
    GIT_PROJECT_MC_FEATURE_ID,
    gitProjectMcSlug,
    gitProjectMcAttribute,
    gitProjectMcComponentAttrs,
    gitProjectWizardStepComponentId,
    gitProjectShellQuote,
    gitProjectSlashPath,
    gitProjectRuntimeContext,
    gitProjectCommandText,
    gitProjectStepCommands,
    gitProjectInitialSnapshotCommands,
    gitProjectCommandsForStep,
    GIT_PROJECT_HEAD_FIX_RUNNER_HINT,
    GIT_PROJECT_HEAD_FIX_STEP_IDS,
    gitProjectPanelStateForStep,
    gitProjectCommandLinesForStep,
    gitProjectStepUsesHeadFixRunner,
    gitProjectCommandDetailsForStep,
    gitProjectExecutableLinesFromCommands,
    gitProjectCommandIsRunnable,
    gitProjectRunnableCommandInfo,
    gitProjectFirstCommitGateOrder,
    weightForWizardStep,
    gitProjectStepId,
    gitProjectStepKind,
    gitProjectRemoteStepIsCurrentlyRequired,
    gitProjectStepIsReadOnlyEvidence,
    gitProjectStepIsUserAction,
    gitProjectStepBlockedReason,
    classifyGitProjectWizardStep,
    toneForWizardStep,
    gitProjectCardSelector,
    gitProjectStepIsCommitCard,
    gitProjectStepIsArchiveCard,
    gitProjectArchiveCardTitle,
    gitProjectCommitCardTitle,
    gitProjectVisibleStepLabel,
    gitProjectOpenCardButtonLabel,
    gitProjectCommitCardAttachmentHtml,
    gitProjectClosedCardPurpose,
    gitProjectClosedCardChips,
    gitProjectClosedCardSummaryHtml,
    gitProjectStepSupportsCardSubscreen,
    gitProjectPathChips
  });
})(window);
