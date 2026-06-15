(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.status-api";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-status-api.js";

  const ENDPOINTS = Object.freeze({
    status: "/api/applications/git/status",
    patches: "/api/applications/git/patches",
    patchRead: "/api/applications/git/patch/read",
    dryRunRead: "/api/applications/git/dry-run/read",
    patchApply: "/api/applications/git/patch/apply",

    projects: "/api/applications/git/projects",
    projectAdd: "/api/applications/git/project/add",
    projectSelect: "/api/applications/git/project/select",
    projectArchive: "/api/applications/git/project/archive",
    projectRestore: "/api/applications/git/project/restore",
    projectInspect: "/api/applications/git/project/inspect",
    projectLock: "/api/applications/git/project/lock",
    projectActionRun: "/api/applications/git/project/action/run",
    projectCommitStart: "/api/applications/git/project/commit/start",
    projectCommitCancel: "/api/applications/git/project/commit/cancel",
    projectArchiveFilesStatus: "/api/applications/git/project/archive-files/status",
    projectArchiveFiles: "/api/applications/git/project/archive-files",
    projectGitignoreSave: "/api/applications/git/project/gitignore/save",

    shims: "/api/applications/git/shims",
    consoleExtract: "/api/applications/git/console/extract",
    consoleRun: "/api/applications/git/console/run",
    aiShim: "/api/applications/git/ai-shim",
    controlPlan: "/api/applications/git/control/plan",
    shimRead: "/api/applications/git/shim/read",
    shimRun: "/api/applications/git/shim/run",
    shimOrdination: "/api/applications/git/shim/ordination",
    shimDelete: "/api/applications/git/shim/delete",

    serverTargetPrefunk: "/api/applications/git/server/target-prefunk",
    serverMirrorPlan: "/api/applications/git/server/mirror/plan",
    serverMirrorSetup: "/api/applications/git/server/mirror/setup",
    serverSetupLocal: "/api/applications/git/server/setup-local",
    serverPushLocal: "/api/applications/git/server/push-local",
    serverStatus: "/api/applications/git/server/status",
    serverAction: "/api/applications/git/server/action",
    serverOperationStatus: "/api/applications/git/server/operation/status",
    serverOperationCancel: "/api/applications/git/server/operation/cancel",
  });

  async function request(path, payload = {}) {
    const response = await global.fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
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
  }

  function operationErrorText(prefix, error) {
    const lines = [`${prefix}: ${error?.message || error}`];
    if (error?.status) lines.push(`HTTP status: ${error.status}`);
    if (error?.details && Object.keys(error.details).length) {
      lines.push("", "Details:", JSON.stringify(error.details, null, 2));
    }
    return lines.join("\n");
  }

  function fetchStatus({repoDir = "."} = {}) {
    return request(ENDPOINTS.status, {repo_dir: repoDir});
  }

  function fetchPatches() {
    return request(ENDPOINTS.patches, {});
  }

  function readPatch(patchName) {
    return request(ENDPOINTS.patchRead, {patch_name: patchName});
  }

  function readDryRun(runName) {
    return request(ENDPOINTS.dryRunRead, {run_name: runName});
  }

  function applyPatchDryRun(options = {}) {
    const patchName = options.patchName ?? options.patch_name ?? "";
    const targetRoot = options.targetRoot ?? options.target_root ?? ".";
    const reverse = Boolean(options.reverse);
    const strictRoot = Boolean(options.strictRoot ?? options.strict_root ?? false);
    return request(ENDPOINTS.patchApply, {
      patch_name: patchName,
      target_root: targetRoot,
      dry_run: true,
      reverse,
      strict_root: strictRoot,
    });
  }

  function fetchProjects() {
    return request(ENDPOINTS.projects, {});
  }

  function addProject(options = {}) {
    return request(ENDPOINTS.projectAdd, options);
  }

  function selectProject(projectId) {
    return request(ENDPOINTS.projectSelect, {project_id: projectId});
  }

  function archiveProject(projectId) {
    return request(ENDPOINTS.projectArchive, {project_id: projectId});
  }

  function restoreProject(projectId, options = {}) {
    return request(ENDPOINTS.projectRestore, {project_id: projectId, ...options});
  }

  function setProjectLock(options = {}) {
    const projectId = options.projectId ?? options.project_id ?? "";
    return request(ENDPOINTS.projectLock, {project_id: projectId, locked: Boolean(options.locked)});
  }

  function inspectProject(payload = {}) {
    return request(ENDPOINTS.projectInspect, payload);
  }

  function runProjectAction(payload = {}) {
    return request(ENDPOINTS.projectActionRun, payload);
  }

  function startProjectCommit(payload = {}) {
    return request(ENDPOINTS.projectCommitStart, payload);
  }

  function cancelProjectCommit(jobId) {
    return request(ENDPOINTS.projectCommitCancel, {job_id: jobId});
  }

  function fetchArchiveFilesStatus(payload = {}) {
    return request(ENDPOINTS.projectArchiveFilesStatus, payload);
  }

  function archiveFiles(payload = {}) {
    return request(ENDPOINTS.projectArchiveFiles, payload);
  }

  function saveGitignore(payload = {}) {
    return request(ENDPOINTS.projectGitignoreSave, payload);
  }

  function fetchShims() {
    return request(ENDPOINTS.shims, {});
  }

  function extractConsoleCommands(aiOutput) {
    return request(ENDPOINTS.consoleExtract, {ai_output: aiOutput});
  }

  function runConsoleCommand({command = "", repoDir = "."} = {}) {
    return request(ENDPOINTS.consoleRun, {command, repo_dir: repoDir});
  }

  function createAiShim(prompt) {
    return request(ENDPOINTS.aiShim, {prompt});
  }

  function planControl(prompt) {
    return request(ENDPOINTS.controlPlan, {prompt});
  }

  function readShim(shimId) {
    return request(ENDPOINTS.shimRead, {shim_id: shimId});
  }

  function runShim(shimId) {
    return request(ENDPOINTS.shimRun, {shim_id: shimId});
  }

  function setShimOrdination(options = {}) {
    const shimId = options.shimId ?? options.shim_id ?? "";
    return request(ENDPOINTS.shimOrdination, {shim_id: shimId, ordained: Boolean(options.ordained)});
  }

  function deleteShim(shimId) {
    return request(ENDPOINTS.shimDelete, {shim_id: shimId});
  }

  function fetchServerTargetPrefunk({repoDir = "."} = {}) {
    return request(ENDPOINTS.serverTargetPrefunk, {repo_dir: repoDir});
  }

  function planServerMirror(payload = {}) {
    return request(ENDPOINTS.serverMirrorPlan, payload);
  }

  function fetchServerStatus() {
    return request(ENDPOINTS.serverStatus, {});
  }

  function runServerAction(action) {
    return request(ENDPOINTS.serverAction, {action});
  }

  function fetchOperationStatus() {
    return request(ENDPOINTS.serverOperationStatus, {});
  }

  function cancelOperation() {
    return request(ENDPOINTS.serverOperationCancel, {});
  }

  global.GitToolsStatusApi = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    endpoints: ENDPOINTS,
    request,
    operationErrorText,
    fetchStatus,
    fetchPatches,
    readPatch,
    readDryRun,
    applyPatchDryRun,
    fetchProjects,
    addProject,
    selectProject,
    archiveProject,
    restoreProject,
    setProjectLock,
    inspectProject,
    runProjectAction,
    startProjectCommit,
    cancelProjectCommit,
    fetchArchiveFilesStatus,
    archiveFiles,
    saveGitignore,
    fetchShims,
    extractConsoleCommands,
    runConsoleCommand,
    createAiShim,
    planControl,
    readShim,
    runShim,
    setShimOrdination,
    deleteShim,
    fetchServerTargetPrefunk,
    planServerMirror,
    fetchServerStatus,
    runServerAction,
    fetchOperationStatus,
    cancelOperation,
  });
})(window);
