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
    renderGitProjectWizard
  });
})(window);
