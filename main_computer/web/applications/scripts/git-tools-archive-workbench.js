(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.archive-workbench";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-archive-workbench.js";

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
  const data = await gitToolsStatusApi().fetchArchiveFilesStatus(gitProjectArchiveRuntimePayload(workbench));
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
  const data = await gitToolsStatusApi().archiveFiles(payload);
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

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    gitProjectArchiveRuntimePayload,
    gitProjectArchiveWorkbenchHtml,
    gitProjectArchiveSelectedPaths,
    gitProjectArchiveGroupHtml,
    gitProjectArchiveRenderStatus,
    gitProjectArchiveRefresh,
    gitProjectArchiveRun,
    gitProjectInitializeArchiveWorkbenches
  });

  global.GitToolsArchiveWorkbench = api;
  Object.assign(global, {
    gitProjectArchiveRuntimePayload,
    gitProjectArchiveWorkbenchHtml,
    gitProjectArchiveSelectedPaths,
    gitProjectArchiveGroupHtml,
    gitProjectArchiveRenderStatus,
    gitProjectArchiveRefresh,
    gitProjectArchiveRun,
    gitProjectInitializeArchiveWorkbenches
  });
})(window);
