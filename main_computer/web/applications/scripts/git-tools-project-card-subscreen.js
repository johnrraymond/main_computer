(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.project-card-inline";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-project-card-subscreen.js";

function gitProjectCommitWorkbenchHtml(step = {}) {
  const review = step.commit_review || {};
  const runtime = step.runtime || gitProjectRuntimeContext();
  return `<div class="git-project-commit-workbench" data-git-commit-workbench data-git-commit-repo="${escapeHtml(runtime.repo || ".")}">
    ${gitProjectCommitHeaderHtml(review)}
    <div class="git-project-commit-body">
      ${gitProjectCommitCenterHtml(step, "file_basket")}
      ${gitProjectCommitBasketHtml(review)}
    </div>
  </div>`;
}
function gitProjectInlineCardDomId(actionKey = "") {
  const safeKey = String(actionKey || "card")
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 72) || "card";
  return `git-project-card-inline-${safeKey}`;
}
function gitProjectCardInlinePanelHtml(step = {}, actionKey = "") {
  if (!gitProjectStepSupportsCardSubscreen(step)) return "";
  const stepId = gitProjectStepId(step);
  const isGitignore = stepId === "update_gitignore_before_initial_commit" || (step.gitignore_file && (Array.isArray(step.ignore_rules) || Array.isArray(step.questionable_ignore_rules)));
  const isSecretsFilter = stepId === "secrets_filter";
  const isCommit = gitProjectStepIsCommitCard(step);
  const isArchive = gitProjectStepIsArchiveCard(step);
  const isPathList = !isCommit && !isArchive && !isGitignore && Array.isArray(step.paths) && step.paths.length;

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

  return `<div id="${escapeHtml(gitProjectInlineCardDomId(actionKey))}" class="git-project-card-inline-panel" data-git-project-card-inline-panel="${escapeHtml(actionKey)}" aria-hidden="true" hidden>
      <div class="git-project-card-inline-body ${isSecretsFilter ? "is-secrets-filter" : isGitignore ? "is-gitignore" : isCommit ? "is-commit" : isArchive ? "is-archive-files" : isPathList ? "is-path-list" : ""}">
        ${body}
      </div>
    </div>`;
}
function gitProjectCardSubscreenHtml(step = {}, actionKey = "") {
  return gitProjectCardInlinePanelHtml(step, actionKey);
}
function gitProjectCardInlinePanelForAction(actionKey = "") {
  return document.querySelector(gitProjectCardSelector("data-git-project-card-inline-panel", actionKey));
}
function gitProjectCardShellForAction(actionKey = "") {
  return document.querySelector(gitProjectCardSelector("data-git-project-card-shell", actionKey));
}
function gitProjectSetInlineCardButtonState(actionKey = "", expanded = false) {
  const button = document.querySelector(gitProjectCardSelector("data-git-project-open-card", actionKey));
  if (!button) return;
  button.setAttribute("aria-expanded", expanded ? "true" : "false");
  const openLabel = button.dataset.gitProjectOpenLabel || button.textContent || "Expand";
  const closeLabel = button.dataset.gitProjectCloseLabel || "Collapse";
  button.textContent = expanded ? closeLabel : openLabel;
}
function gitProjectSetCardInlinePanelState(actionKey = "", expanded = false) {
  const panel = gitProjectCardInlinePanelForAction(actionKey);
  const card = gitProjectCardShellForAction(actionKey);
  if (!panel) return false;
  panel.hidden = !expanded;
  panel.setAttribute("aria-hidden", expanded ? "false" : "true");
  if (card) card.classList.toggle("is-expanded", !!expanded);
  gitProjectSetInlineCardButtonState(actionKey, !!expanded);
  return true;
}
function gitProjectScrollExpandedCardIntoView(panel) {
  const card = panel && typeof panel.closest === "function"
    ? panel.closest("[data-git-project-card-shell]")
    : null;
  const target = card || panel;
  if (!target || typeof target.scrollIntoView !== "function") return;
  const scrollCard = () => {
    try {
      target.scrollIntoView({ behavior: "smooth", block: "start", inline: "nearest" });
    } catch (error) {
      target.scrollIntoView(true);
    }
  };
  if (global.requestAnimationFrame) {
    global.requestAnimationFrame(() => global.requestAnimationFrame(scrollCard));
    return;
  }
  global.setTimeout(scrollCard, 0);
}
function gitProjectOpenCardSubscreen(actionKey = "") {
  const current = document.querySelector("[data-git-project-card-inline-panel]:not([hidden])");
  if (current && current.dataset.gitProjectCardInlinePanel === actionKey) return true;
  if (current && !gitProjectConfirmDiscardGitignoreChanges(current)) return false;
  if (current) gitProjectSetCardInlinePanelState(current.dataset.gitProjectCardInlinePanel || "", false);
  const panel = gitProjectCardInlinePanelForAction(actionKey);
  if (!panel) return false;
  gitProjectSetCardInlinePanelState(actionKey, true);
  gitProjectRefreshIgnoreRulePreview(panel);
  gitProjectInitializeGitignoreWorkbenches(panel);
  gitProjectInitializeCommitWorkbenches(panel);
  gitProjectInitializeArchiveWorkbenches(panel);
  gitProjectBindSecretsFilterActions(panel);
  gitProjectScrollExpandedCardIntoView(panel);
  return true;
}
function gitProjectCloseCardSubscreen(actionKey = "", options = {}) {
  const panel = gitProjectCardInlinePanelForAction(actionKey);
  if (!panel) return false;
  if (!panel.hidden && !options.force && !gitProjectConfirmDiscardGitignoreChanges(panel)) return false;
  return gitProjectSetCardInlinePanelState(actionKey, false);
}
function gitProjectToggleCardSubscreen(actionKey = "") {
  const panel = gitProjectCardInlinePanelForAction(actionKey);
  if (!panel) return false;
  if (!panel.hidden) return gitProjectCloseCardSubscreen(actionKey);
  return gitProjectOpenCardSubscreen(actionKey);
}
function bindGitProjectCardSubscreen(container) {
  if (!container) return;
  container.querySelectorAll("[data-git-project-open-card]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      gitProjectToggleCardSubscreen(button.dataset.gitProjectOpenCard || "");
    });
  });
  container.querySelectorAll("[data-git-project-close-card]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      gitProjectCloseCardSubscreen(button.dataset.gitProjectCloseCard || "");
    });
  });
  container.querySelectorAll("[data-git-project-card-inline-panel]").forEach((panel) => {
    if (panel.dataset.gitProjectInlinePanelClickBoundary === "true") return;
    panel.dataset.gitProjectInlinePanelClickBoundary = "true";
    panel.addEventListener("click", (event) => {
      event.stopPropagation();
    });
  });
  container.querySelectorAll("[data-git-project-card-shell]").forEach((card) => {
    card.addEventListener("click", (event) => {
      if (event.target.closest("button, a, input, textarea, select, details, summary, code[contenteditable='true'], [data-git-project-card-inline-panel]")) return;
      const actionKey = card.dataset.gitProjectCardShell || "";
      if (actionKey) gitProjectToggleCardSubscreen(actionKey);
    });
  });
  container.querySelectorAll("[data-git-ignore-select]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      const scope = button.closest("[data-git-project-card-inline-panel]");
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
    gitProjectInlineCardDomId,
    gitProjectCardInlinePanelHtml,
    gitProjectCardSubscreenHtml,
    gitProjectCardInlinePanelForAction,
    gitProjectOpenCardSubscreen,
    gitProjectCloseCardSubscreen,
    gitProjectToggleCardSubscreen,
    bindGitProjectCardSubscreen,
    renderGitProjectInspection
  });

  global.GitToolsProjectCardSubscreen = api;
  Object.assign(global, {
    gitProjectCommitWorkbenchHtml,
    gitProjectInlineCardDomId,
    gitProjectCardInlinePanelHtml,
    gitProjectCardSubscreenHtml,
    gitProjectCardInlinePanelForAction,
    gitProjectOpenCardSubscreen,
    gitProjectCloseCardSubscreen,
    gitProjectToggleCardSubscreen,
    bindGitProjectCardSubscreen,
    renderGitProjectInspection
  });
})(window);
