(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.gitignore-workbench";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-gitignore-workbench.js";

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
  gitProjectScheduleGitignoreWorkbenchLayoutCaps(workbench.closest("[data-git-project-card-inline-panel]") || document);
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
    const data = await gitToolsStatusApi().saveGitignore(payload);
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
    gitProjectRefreshIgnoreRulePreview(workbench.closest("[data-git-project-card-inline-panel]") || workbench);
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
  gitProjectRefreshIgnoreRulePreview(workbench.closest("[data-git-project-card-inline-panel]") || workbench);
  gitProjectScheduleGitignoreWorkbenchLayoutCaps(workbench.closest("[data-git-project-card-inline-panel]") || document);
}
function gitProjectGitignoreViewportHeight() {
  return Number(global.visualViewport?.height || document.documentElement?.clientHeight || global.innerHeight || 700);
}
function gitProjectGitignoreCapPx(value) {
  return `${Math.max(180, Math.floor(Number(value) || 0))}px`;
}
function gitProjectUpdateGitignoreWorkbenchLayoutCaps(container = document) {
  const scope = container?.querySelectorAll ? container : document;
  const viewportHeight = gitProjectGitignoreViewportHeight();
  const bottomGap = 16;
  scope.querySelectorAll(".git-project-gitignore-workbench").forEach((workbench) => {
    if (!workbench || workbench.offsetParent === null) return;
    const inlineBody = workbench.closest(".git-project-card-inline-body.is-gitignore");
    const inlinePanel = workbench.closest(".git-project-card-inline-panel");
    const suggestions = workbench.querySelector(".git-project-gitignore-suggestions");
    const filePanel = workbench.querySelector(".git-project-gitignore-file-panel");
    const inlineTop = (inlineBody || workbench).getBoundingClientRect().top;
    const workbenchTop = workbench.getBoundingClientRect().top;
    const inlineCap = gitProjectGitignoreCapPx(viewportHeight - inlineTop - bottomGap);
    const workbenchCap = gitProjectGitignoreCapPx(viewportHeight - workbenchTop - bottomGap);
    inlineBody?.style.setProperty("--gitignore-inline-cap", inlineCap);
    inlinePanel?.style.setProperty("--gitignore-inline-cap", inlineCap);
    workbench.style.setProperty("--gitignore-workbench-cap", workbenchCap);
    [suggestions, filePanel].filter(Boolean).forEach((element) => {
      const panelCap = gitProjectGitignoreCapPx(viewportHeight - element.getBoundingClientRect().top - bottomGap);
      element.style.setProperty("--gitignore-panel-cap", panelCap);
    });
  });
}
function gitProjectScheduleGitignoreWorkbenchLayoutCaps(container = document) {
  if (global.gitProjectGitignoreLayoutCapFrame) {
    global.cancelAnimationFrame?.(global.gitProjectGitignoreLayoutCapFrame);
  }
  const update = () => {
    global.gitProjectGitignoreLayoutCapFrame = 0;
    gitProjectUpdateGitignoreWorkbenchLayoutCaps(container);
  };
  if (global.requestAnimationFrame) {
    global.gitProjectGitignoreLayoutCapFrame = global.requestAnimationFrame(update);
  } else {
    global.setTimeout(update, 0);
  }
}
function gitProjectEnsureGitignoreLayoutCapListeners() {
  if (global.gitProjectGitignoreLayoutCapListenersBound === true) return;
  global.gitProjectGitignoreLayoutCapListenersBound = true;
  const schedule = () => gitProjectScheduleGitignoreWorkbenchLayoutCaps(document);
  global.addEventListener("resize", schedule, { passive: true });
  global.visualViewport?.addEventListener("resize", schedule, { passive: true });
  global.addEventListener("scroll", schedule, { passive: true, capture: true });
}
function gitProjectInitializeGitignoreWorkbench(workbench) {
  if (!workbench) return;
  gitProjectEnsureGitignoreLayoutCapListeners();
  if (workbench.dataset.gitignoreBound === "true") {
    gitProjectScheduleGitignoreWorkbenchLayoutCaps(workbench.closest("[data-git-project-card-inline-panel]") || document);
    return;
  }
  workbench.dataset.gitignoreBound = "true";
  workbench.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) return;
    if (target.matches("[data-git-ignore-rule]")) {
      gitProjectApplyIgnoreRuleToRightPane(workbench, target);
      gitProjectRefreshIgnoreRulePreview(workbench.closest("[data-git-project-card-inline-panel]") || workbench);
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
  gitProjectScheduleGitignoreWorkbenchLayoutCaps(workbench.closest("[data-git-project-card-inline-panel]") || document);
  global.setTimeout(() => gitProjectScheduleGitignoreWorkbenchLayoutCaps(workbench.closest("[data-git-project-card-inline-panel]") || document), 50);
  global.setTimeout(() => gitProjectScheduleGitignoreWorkbenchLayoutCaps(workbench.closest("[data-git-project-card-inline-panel]") || document), 250);
}
function gitProjectInitializeGitignoreWorkbenches(container) {
  container?.querySelectorAll(".git-project-gitignore-workbench").forEach((workbench) => {
    gitProjectInitializeGitignoreWorkbench(workbench);
  });
  gitProjectEnsureGitignoreBeforeUnloadGuard();
  gitProjectScheduleGitignoreWorkbenchLayoutCaps(container || document);
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

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    gitProjectIgnoreRuleRows,
    gitProjectNormalizeGitignoreLineText,
    gitProjectNormalizeGitignoreMatchText,
    gitProjectGitignoreBaselineLines,
    gitProjectGitignoreLineRowHtml,
    gitProjectGitignoreLinesHtml,
    gitProjectGitignoreFileSummary,
    gitProjectIgnoreWorkbenchHtml,
    gitProjectRefreshIgnoreRulePreview,
    gitProjectParseGitignoreBaseline,
    gitProjectGitignoreRows,
    gitProjectGitignoreRowText,
    gitProjectGitignoreCheckedLines,
    gitProjectGitignoreLinesEqual,
    gitProjectFindGitignoreRightRow,
    gitProjectSyncGitignoreLeftRule,
    gitProjectUpdateGitignoreEmptyMessage,
    gitProjectSetGitignoreRowChecked,
    gitProjectAppendGitignoreRightRow,
    gitProjectApplyIgnoreRuleToRightPane,
    gitProjectUpdateGitignoreDirtyState,
    gitProjectRenderSavedGitignoreRows,
    gitProjectSaveGitignoreWorkbench,
    gitProjectHandleGitignoreRightChange,
    gitProjectGitignoreViewportHeight,
    gitProjectGitignoreCapPx,
    gitProjectUpdateGitignoreWorkbenchLayoutCaps,
    gitProjectScheduleGitignoreWorkbenchLayoutCaps,
    gitProjectEnsureGitignoreLayoutCapListeners,
    gitProjectInitializeGitignoreWorkbench,
    gitProjectInitializeGitignoreWorkbenches,
    gitProjectDirtyGitignoreWorkbenches,
    gitProjectConfirmDiscardGitignoreChanges,
    gitProjectEnsureGitignoreBeforeUnloadGuard
  });

  global.GitToolsGitignoreWorkbench = api;
  Object.assign(global, {
    gitProjectIgnoreRuleRows,
    gitProjectNormalizeGitignoreLineText,
    gitProjectNormalizeGitignoreMatchText,
    gitProjectGitignoreBaselineLines,
    gitProjectGitignoreLineRowHtml,
    gitProjectGitignoreLinesHtml,
    gitProjectGitignoreFileSummary,
    gitProjectIgnoreWorkbenchHtml,
    gitProjectRefreshIgnoreRulePreview,
    gitProjectParseGitignoreBaseline,
    gitProjectGitignoreRows,
    gitProjectGitignoreRowText,
    gitProjectGitignoreCheckedLines,
    gitProjectGitignoreLinesEqual,
    gitProjectFindGitignoreRightRow,
    gitProjectSyncGitignoreLeftRule,
    gitProjectUpdateGitignoreEmptyMessage,
    gitProjectSetGitignoreRowChecked,
    gitProjectAppendGitignoreRightRow,
    gitProjectApplyIgnoreRuleToRightPane,
    gitProjectUpdateGitignoreDirtyState,
    gitProjectRenderSavedGitignoreRows,
    gitProjectSaveGitignoreWorkbench,
    gitProjectHandleGitignoreRightChange,
    gitProjectGitignoreViewportHeight,
    gitProjectGitignoreCapPx,
    gitProjectUpdateGitignoreWorkbenchLayoutCaps,
    gitProjectScheduleGitignoreWorkbenchLayoutCaps,
    gitProjectEnsureGitignoreLayoutCapListeners,
    gitProjectInitializeGitignoreWorkbench,
    gitProjectInitializeGitignoreWorkbenches,
    gitProjectDirtyGitignoreWorkbenches,
    gitProjectConfirmDiscardGitignoreChanges,
    gitProjectEnsureGitignoreBeforeUnloadGuard
  });
})(window);
