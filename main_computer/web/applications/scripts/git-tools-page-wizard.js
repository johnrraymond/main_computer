(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.page-wizard";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-page-wizard.js";

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

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    GIT_PAGE_WIZARD_STEPS,
    GIT_PAGE_WIZARD_REQUIRED_KEYS,
    GIT_PAGE_WIZARD_WORKFLOW_STAGES,
    gitPageWizardStepDefinition,
    gitPageWizardAnswerFor,
    gitPageWizardCompletedRequiredCount,
    gitPageWizardIsComplete,
    gitPageWizardMissingLabels,
    gitPageWizardWorkflowStage,
    renderGitPageWizardWorkflow,
    gitPageWizardStatusText,
    gitPageWizardSlug,
    gitPageWizardKind,
    gitPageWizardLabel,
    gitPageWizardOwner,
    gitPageWizardFeatureId,
    gitPageWizardMetadata,
    gitPageWizardAttribute,
    gitPageWizardMetadataAttributes,
    gitPageWizardBuildDraft,
    gitPageWizardSummaryLines,
    buildGitPageWizardPrompt,
    renderGitPageWizard,
    advanceGitPageWizard,
    resetGitPageWizard,
    sendGitPageWizardToConsole
  });

  global.GitToolsPageWizard = api;
  Object.assign(global, {
    GIT_PAGE_WIZARD_STEPS,
    GIT_PAGE_WIZARD_REQUIRED_KEYS,
    GIT_PAGE_WIZARD_WORKFLOW_STAGES,
    gitPageWizardStepDefinition,
    gitPageWizardAnswerFor,
    gitPageWizardCompletedRequiredCount,
    gitPageWizardIsComplete,
    gitPageWizardMissingLabels,
    gitPageWizardWorkflowStage,
    renderGitPageWizardWorkflow,
    gitPageWizardStatusText,
    gitPageWizardSlug,
    gitPageWizardKind,
    gitPageWizardLabel,
    gitPageWizardOwner,
    gitPageWizardFeatureId,
    gitPageWizardMetadata,
    gitPageWizardAttribute,
    gitPageWizardMetadataAttributes,
    gitPageWizardBuildDraft,
    gitPageWizardSummaryLines,
    buildGitPageWizardPrompt,
    renderGitPageWizard,
    advanceGitPageWizard,
    resetGitPageWizard,
    sendGitPageWizardToConsole
  });
})(window);
