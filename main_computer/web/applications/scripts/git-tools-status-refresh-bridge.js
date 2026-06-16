(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.status-refresh-bridge";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-status-refresh-bridge.js";

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

async function refreshGitStatus() {
  const repoDir = gitToolsRepoDirValue(".");
  if (gitToolsStatus) gitToolsStatus.textContent = "Loading git status...";
  try {
    const data = await gitToolsStatusApi().fetchStatus({repoDir});
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
async function refreshGitTools() {
  await loadGitProjects().catch(() => null);
  await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false}).catch(() => null);
  await refreshGitStatus();
  await refreshGitPatches();
  await refreshGitShims();
  await refreshGitServerStatus();
}

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    summarizeGitStatus,
    gitWorkflowSection,
    updateGitWorkflowSectionSummary,
    expandGitWorkflowSection,
    collapseGitWorkflowSection,
    initializeGitWorkflowDisclosure,
    syncGitPageWizardWorkflowDisclosure,
    refreshGitStatus,
    refreshGitTools
  });

  global.GitToolsStatusRefreshBridge = api;
  Object.assign(global, {
    summarizeGitStatus,
    gitWorkflowSection,
    updateGitWorkflowSectionSummary,
    expandGitWorkflowSection,
    collapseGitWorkflowSection,
    initializeGitWorkflowDisclosure,
    syncGitPageWizardWorkflowDisclosure,
    refreshGitStatus,
    refreshGitTools
  });
})(window);
