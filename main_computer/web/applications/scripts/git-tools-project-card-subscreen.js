(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.project-card-subscreen";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-project-card-subscreen.js";

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
  const wizard = data.wizard || {};
  renderGitProjectNextStep(data);
  renderGitProjectWizard(wizard, data);
}

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    gitProjectCommitWorkbenchHtml,
    gitProjectCardSubscreenHtml,
    gitProjectOpenCardSubscreen,
    gitProjectCloseCardSubscreen,
    bindGitProjectCardSubscreen,
    renderGitProjectInspection
  });

  global.GitToolsProjectCardSubscreen = api;
  Object.assign(global, {
    gitProjectCommitWorkbenchHtml,
    gitProjectCardSubscreenHtml,
    gitProjectOpenCardSubscreen,
    gitProjectCloseCardSubscreen,
    bindGitProjectCardSubscreen,
    renderGitProjectInspection
  });
})(window);
