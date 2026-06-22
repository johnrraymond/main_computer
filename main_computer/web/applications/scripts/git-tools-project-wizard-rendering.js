(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.project-wizard-rendering";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-project-wizard-rendering.js";

function gitProjectNormalizedWizardLabel(value = "") {
  return gitProjectWorkflowIntegration().normalizedWizardLabel(value);
}
function gitProjectWizardStepMatches(step = {}, ids = new Set(), labels = new Set()) {
  return gitProjectWorkflowIntegration().wizardStepMatches(step, ids, labels, gitProjectWorkflowHooks());
}
function gitProjectWizardStepShouldHideInActionQueue(step = {}) {
  return gitProjectWorkflowIntegration().wizardStepShouldHideInActionQueue(step, gitProjectWorkflowHooks());
}
function gitProjectWizardStepIsGitignoreReviewCandidate(step = {}) {
  return gitProjectWorkflowIntegration().wizardStepIsGitignoreReviewCandidate(step, gitProjectWorkflowHooks());
}
function gitProjectWizardStepIsSecretsFilterCandidate(step = {}) {
  return gitProjectWorkflowIntegration().wizardStepIsSecretsFilterCandidate(step, gitProjectWorkflowHooks());
}
function gitProjectNormalizeSecretsFilterStep(step = {}) {
  return gitProjectWorkflowIntegration().normalizeSecretsFilterStep(step);
}
function gitProjectUniqueStrings(...groups) {
  return gitProjectWorkflowIntegration().uniqueStrings(...groups);
}
function gitProjectWizardStepPaths(step = {}) {
  return gitProjectWorkflowIntegration().wizardStepPaths(step);
}
function gitProjectWizardIgnoreRules(step = {}, key = "ignore_rules") {
  return gitProjectWorkflowIntegration().wizardIgnoreRules(step, key);
}
function gitProjectMergeGitignoreReviewSteps(steps = []) {
  return gitProjectWorkflowIntegration().mergeGitignoreReviewSteps(steps);
}
function gitProjectWizardDisplayActions(actions = []) {
  return gitProjectWorkflowIntegration().wizardDisplayActions(actions, gitProjectWorkflowHooks());
}
function clearGitProjectWizardLoadingState() {
  if (!gitProjectWizardPlan) return;
  gitProjectWizardPlan.classList.remove("git-project-wizard-loading", "git-project-wizard-error");
  gitProjectWizardPlan.removeAttribute("role");
  gitProjectWizardPlan.removeAttribute("aria-live");
  gitProjectWizardPlan.removeAttribute("aria-busy");
}
function renderGitProjectWizardLoading(options = {}) {
  if (!gitProjectWizardPlan) return;
  const title = options.title || "Inspecting project";
  const detail = options.detail || "Waiting for backend results. The wizard plan will appear after inspection completes.";
  const context = String(options.context || "").trim();
  const contextHtml = context
    ? `<div class="git-project-wizard-loading-context"><span>Target</span><code>${escapeHtml(context)}</code></div>`
    : "";
  gitProjectWizardPlan.classList.add("git-project-wizard-loading");
  gitProjectWizardPlan.classList.remove("git-project-wizard-error");
  gitProjectWizardPlan.setAttribute("role", "status");
  gitProjectWizardPlan.setAttribute("aria-live", "polite");
  gitProjectWizardPlan.setAttribute("aria-busy", "true");
  gitProjectWizardPlan.innerHTML = `<div class="git-project-wizard-loading-box">
    <div class="git-project-wizard-loading-spinner" aria-hidden="true"></div>
    <div class="git-project-wizard-loading-title">${escapeHtml(title)}<span class="git-project-wizard-loading-dots"></span></div>
    <div class="git-project-wizard-loading-detail">${escapeHtml(detail)}</div>
    ${contextHtml}
  </div>`;
}
function renderGitProjectWizardInspectionFailed(error, context = "") {
  if (!gitProjectWizardPlan) return;
  const message = error?.message || String(error || "Unknown project inspection error.");
  const contextText = String(context || "").trim();
  const contextHtml = contextText
    ? `<div class="git-project-wizard-loading-context"><span>Target</span><code>${escapeHtml(contextText)}</code></div>`
    : "";
  clearGitProjectWizardLoadingState();
  gitProjectWizardPlan.classList.add("git-project-wizard-error");
  gitProjectWizardPlan.setAttribute("role", "alert");
  gitProjectWizardPlan.setAttribute("aria-live", "assertive");
  gitProjectWizardPlan.innerHTML = `<div class="git-project-wizard-error-box">
    <strong>Project inspection failed</strong>
    <span>${escapeHtml(message)}</span>
    ${contextHtml}
  </div>`;
}
function renderGitProjectWizard(wizard, data = {}) {
  if (!gitProjectWizardPlan) return;
  clearGitProjectWizardLoadingState();
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
    const cardPanel = gitProjectCardInlinePanelHtml(step, actionKey);
    const openCardLabel = gitProjectOpenCardButtonLabel(step);
    const cardPanelId = gitProjectInlineCardDomId(actionKey);
    const openCardButton = cardPanel
      ? `<button type="button" class="git-project-card-open-button" data-git-project-open-card="${escapeHtml(actionKey)}" data-git-project-open-label="${escapeHtml(openCardLabel)}" data-git-project-close-label="Collapse" aria-expanded="false" aria-controls="${escapeHtml(cardPanelId)}">${escapeHtml(openCardLabel)}</button>`
      : "";
    const openCardCorner = openCardButton ? `<div class="git-project-card-open-corner" ${gitProjectMcComponentAttrs(`${stepComponentId}.open-card`, "toolbar", `${stepLabel} Expand/Collapse Control`, stepComponentId)}>${openCardButton}</div>` : "";
    const closedSummary = gitProjectClosedCardSummaryHtml(step, stepComponentId, stepLabel);
    const cardAttrs = [
      `data-priority-weight="${Number(step.weight || 0)}"`,
      cardPanel ? `data-git-project-card-shell="${escapeHtml(actionKey)}"` : "",
      gitProjectMcComponentAttrs(stepComponentId, "panel", stepLabel, "git-tools.projects.wizard.queue"),
    ].filter(Boolean).join(" ");
    const cardClass = `git-project-wizard-step git-project-mini-action-card tone-${escapeHtml(step.tone)} ${escapeHtml(step.uiLane || step.state || "planned")}${gitProjectStepIsCommitCard(step) ? " has-commit-workbench" : ""}${gitProjectStepIsArchiveCard(step) ? " has-archive-workbench" : ""}${cardPanel ? " has-card-open-control" : ""}`;
    const displayNumber = Number.isFinite(displayIndex) ? displayIndex + 1 : Number(step.order ?? 0) + 1;
    return `<div class="${cardClass}" ${cardAttrs}>
      <div class="git-project-wizard-step-title" ${gitProjectMcComponentAttrs(`${stepComponentId}.title`, "status", `${stepLabel} Title`, stepComponentId)}>
        <strong>${displayNumber}. ${escapeHtml(stepLabel)}</strong>
        <span class="git-project-mini-card-state">${escapeHtml(step.status || step.state || "ready")}</span>
      </div>
      ${closedSummary}
      ${openCardCorner}
      ${cardPanel}
    </div>`;
  };
  const renderActionQueue = (items, emptyText) => {
    const groupComponentId = "git-tools.projects.wizard.action-queue";
    const countLabel = items.length ? `${items.length} step${items.length === 1 ? "" : "s"}` : "0 steps";
    return [
      `<div class="git-project-wizard-section-head git-project-action-queue-head" ${gitProjectMcComponentAttrs(`${groupComponentId}.head`, "status", "Action Queue Summary", "git-tools.projects.wizard-plan")}>
        <strong>Action queue</strong>
        <span>${countLabel}</span>
      </div>`,
      `<div class="git-project-wizard-list git-project-action-queue-list" ${gitProjectMcComponentAttrs(`${groupComponentId}.list`, "list", "Action Queue Items", "git-tools.projects.wizard-plan")}>
        ${items.length ? items.map((step, index) => renderStepCard(step, index)).join("") : `<div class="git-project-wizard-empty" ${gitProjectMcComponentAttrs(`${groupComponentId}.empty`, "status", "Action Queue Empty State", "git-tools.projects.wizard-plan")}>${escapeHtml(emptyText)}</div>`}
      </div>`,
    ].join("");
  };
  gitProjectWizardPlan.innerHTML = renderActionQueue(visibleActions, "No workflow actions need review.");
  bindGitProjectActionButtons(gitProjectWizardPlan);
  bindGitProjectCardSubscreen(gitProjectWizardPlan);
}

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    gitProjectNormalizedWizardLabel,
    gitProjectWizardStepMatches,
    gitProjectWizardStepShouldHideInActionQueue,
    gitProjectWizardStepIsGitignoreReviewCandidate,
    gitProjectWizardStepIsSecretsFilterCandidate,
    gitProjectNormalizeSecretsFilterStep,
    gitProjectUniqueStrings,
    gitProjectWizardStepPaths,
    gitProjectWizardIgnoreRules,
    gitProjectMergeGitignoreReviewSteps,
    gitProjectWizardDisplayActions,
    clearGitProjectWizardLoadingState,
    renderGitProjectWizardLoading,
    renderGitProjectWizardInspectionFailed,
    renderGitProjectWizard
  });

  global.GitToolsProjectWizardRendering = api;
  Object.assign(global, {
    gitProjectNormalizedWizardLabel,
    gitProjectWizardStepMatches,
    gitProjectWizardStepShouldHideInActionQueue,
    gitProjectWizardStepIsGitignoreReviewCandidate,
    gitProjectWizardStepIsSecretsFilterCandidate,
    gitProjectNormalizeSecretsFilterStep,
    gitProjectUniqueStrings,
    gitProjectWizardStepPaths,
    gitProjectWizardIgnoreRules,
    gitProjectMergeGitignoreReviewSteps,
    gitProjectWizardDisplayActions,
    clearGitProjectWizardLoadingState,
    renderGitProjectWizardLoading,
    renderGitProjectWizardInspectionFailed,
    renderGitProjectWizard
  });
})(window);
