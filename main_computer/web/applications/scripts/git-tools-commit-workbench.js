(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.commit-workbench";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-commit-workbench.js";

const GIT_PROJECT_WUNDERBAUM_VERSION = "0.14.1";
const GIT_PROJECT_WUNDERBAUM_ASSETS = {
  css: `https://cdn.jsdelivr.net/gh/mar10/wunderbaum@v${GIT_PROJECT_WUNDERBAUM_VERSION}/dist/wunderbaum.css`,
  icons: "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css",
  js: `https://cdn.jsdelivr.net/gh/mar10/wunderbaum@v${GIT_PROJECT_WUNDERBAUM_VERSION}/dist/wunderbaum.umd.min.js`,
};
let gitProjectWunderbaumLoadPromise = null;

function gitProjectCommitFileBasketIntegration() {
  const integration = globalThis.GitToolsFileBasket;
  if (!integration) {
    throw new Error("GitToolsFileBasket integration module is not loaded.");
  }
  return integration;
}

function gitProjectCommitFileBasketContractView() {
  return globalThis.GitToolsFileBasketContractView || null;
}

function gitProjectCommitFileBasketHooks() {
  return {
    escapeHtml,
    repoIdentityHtml: gitProjectCommitRepoIdentityHtml,
  };
}

function gitProjectCommitGroups(review = {}) {
  return gitProjectCommitFileBasketIntegration().groups(review);
}

function gitProjectCommitGroupConfig() {
  return gitProjectCommitFileBasketIntegration().groupConfig();
}

function gitProjectCommitOpenedCard(review = {}) {
  return review.opened_commit_card || {};
}

function gitProjectCommitIdentity(review = {}) {
  return review.commit_identity || {};
}

function gitProjectCommitHead(review = {}) {
  return review.head || {};
}

function gitProjectCommitBranch(review = {}) {
  const opened = gitProjectCommitOpenedCard(review);
  const fields = opened.config_strip?.fields || {};
  const head = gitProjectCommitHead(review);
  return String(fields.branch?.value || review.branch?.current || head.branch || head.default_branch || "master");
}

function gitProjectCommitMessage(review = {}) {
  const opened = gitProjectCommitOpenedCard(review);
  const fields = opened.config_strip?.fields || {};
  return String(fields.commit_message?.value || review.commit_message || "Take project snapshot");
}

function gitProjectCommitIdentitySource(review = {}) {
  const opened = gitProjectCommitOpenedCard(review);
  const strip = opened.status_strip || {};
  const identity = gitProjectCommitIdentity(review);
  if (strip.identity) return String(strip.identity);
  const nameSource = identity.name_source || "missing";
  const emailSource = identity.email_source || "missing";
  if (nameSource === emailSource) return String(nameSource);
  if (nameSource === "missing" && emailSource === "missing") return "missing";
  return "mixed";
}

function gitProjectCommitIdentityScope(review = {}) {
  const opened = gitProjectCommitOpenedCard(review);
  const fields = opened.config_strip?.fields || {};
  return String(fields.identity_scope?.value || (gitProjectCommitIdentity(review).ready ? "use_existing" : "save_local"));
}

function gitProjectCommitFieldValue(review = {}, key = "", fallback = "") {
  const opened = gitProjectCommitOpenedCard(review);
  const fields = opened.config_strip?.fields || {};
  const identity = gitProjectCommitIdentity(review);
  const fallbacks = {
    branch: gitProjectCommitBranch(review),
    commit_message: gitProjectCommitMessage(review),
    git_user_name: identity.name || "",
    git_user_email: identity.email || "",
    identity_scope: gitProjectCommitIdentityScope(review),
  };
  const value = fields[key]?.value;
  return String(value ?? fallbacks[key] ?? fallback ?? "");
}

function gitProjectCommitGateSource(review = {}) {
  const rawGates = review.gates || gitProjectCommitOpenedCard(review).gates || {};
  const byId = {};
  if (Array.isArray(rawGates)) {
    rawGates.forEach((gate = {}) => {
      if (gate.id) byId[String(gate.id)] = gate;
    });
  } else if (rawGates && typeof rawGates === "object") {
    Object.entries(rawGates).forEach(([key, gate]) => {
      if (gate && typeof gate === "object") {
        byId[key] = {id: key, ...gate};
      }
    });
  }
  return byId;
}

function gitProjectCommitGateSummary(review = {}) {
  const gates = gitProjectCommitGateSource(review);
  const blockers = Array.isArray(review.commit_blockers) ? review.commit_blockers.map(String) : [];
  const lockedReason = String(review.locked_reason || "");
  const privacySummary = review.privacy_scan?.summary || {};
  const gitignoreGate = gates.gitignore || gates.gitignore_gate || {};
  const secretsGate = gates.secrets_filter || gates.secrets_filter_gate || gates.privacy_scan || {};
  const gitignoreBlocked = gitignoreGate.state === "blocked" || gitignoreGate.ready === false || blockers.includes("gitignore_review_required") || Boolean(lockedReason);
  const secretsBlocked = secretsGate.state === "blocked" || secretsGate.ready === false || blockers.some((item) => [
    "security_scan_required",
    "critical_privacy_findings",
    "blocking_security_findings",
    "path_risk_review_required",
  ].includes(item));
  const secretSummaryParts = [];
  if (Number(privacySummary.blocking || 0)) secretSummaryParts.push(`${Number(privacySummary.blocking || 0)} blocking finding${Number(privacySummary.blocking || 0) === 1 ? "" : "s"}`);
  if (Number(privacySummary.critical || 0)) secretSummaryParts.push(`${Number(privacySummary.critical || 0)} critical`);
  if (Number(privacySummary.review || privacySummary.review_files || 0)) secretSummaryParts.push(`${Number(privacySummary.review || privacySummary.review_files || 0)} review`);
  if (privacySummary.requires_user_scan) secretSummaryParts.push("scan required");
  return {
    gitignore: {
      key: "gitignore",
      label: gitignoreGate.label || ".gitignore",
      state: gitignoreBlocked ? "blocked" : (gitignoreGate.state || "passed"),
      summary: gitignoreGate.summary || gitignoreGate.reason || lockedReason || (gitignoreBlocked ? ".gitignore review is required before staging." : ".gitignore gate is passing."),
      step_id: gitignoreGate.step_id || "update_gitignore_before_initial_commit",
    },
    secrets_filter: {
      key: "secrets_filter",
      label: secretsGate.label || "Secrets / Filter",
      state: secretsBlocked ? "blocked" : (secretsGate.state || (privacySummary.requires_user_scan ? "needs review" : "passed")),
      summary: secretsGate.summary || secretsGate.reason || secretSummaryParts.join(" · ") || (secretsBlocked ? "Secrets / Filter needs review before commit." : "Secrets / Filter gate is passing."),
      step_id: secretsGate.step_id || "secrets_filter",
    },
  };
}

function gitProjectCommitReadySummary(review = {}) {
  const groups = gitProjectCommitGroups(review);
  const gates = gitProjectCommitGateSummary(review);
  const selectedCount = groups.selected_by_default.filter((item) => item && item.path).length;
  const identity = gitProjectCommitIdentity(review);
  const head = gitProjectCommitHead(review);
  const backendReady = Boolean(review.commit_ready);
  const gateBlocked = Object.values(gates).some((gate) => String(gate.state || "").toLowerCase() === "blocked");
  const reasons = [];
  if (gateBlocked) reasons.push("upstream gate blocked");
  if (!gitProjectCommitMessage(review).trim()) reasons.push("commit message missing");
  if (!gitProjectCommitBranch(review).trim()) reasons.push("branch missing");
  if (!identity.ready && (!gitProjectCommitFieldValue(review, "git_user_name").trim() || !gitProjectCommitFieldValue(review, "git_user_email").includes("@"))) reasons.push("identity incomplete");
  if (!selectedCount) reasons.push("no files selected");
  if (!backendReady) reasons.push("backend not ready");
  return {
    backendReady,
    selectedCount,
    headState: head.head_state || "unknown",
    branch: gitProjectCommitBranch(review),
    identitySource: gitProjectCommitIdentitySource(review),
    ready: backendReady && !gateBlocked && selectedCount > 0,
    reasons,
  };
}

function gitProjectCommitFieldHtml(review = {}, key = "", label = "", options = {}) {
  const value = gitProjectCommitFieldValue(review, key, options.fallback || "");
  const type = options.type || "text";
  const placeholder = options.placeholder || "";
  if (type === "radio") {
    const scope = gitProjectCommitIdentityScope(review);
    return `<div class="git-project-commit-field is-wide" data-git-commit-field="${escapeHtml(key)}">
      <span>${escapeHtml(label)}</span>
      <div class="git-project-commit-radio-row">
        <label><input type="radio" name="git-commit-identity-scope" value="use_existing" ${scope === "use_existing" ? "checked" : ""}> use existing global</label>
        <label><input type="radio" name="git-commit-identity-scope" value="save_local" ${scope === "save_local" ? "checked" : ""}> save local repo identity</label>
      </div>
    </div>`;
  }
  return `<label class="git-project-commit-field" data-git-commit-field="${escapeHtml(key)}">
    <span>${escapeHtml(label)}</span>
    <input type="${escapeHtml(type)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}">
  </label>`;
}

function gitProjectCommitHeaderHtml(review = {}) {
  const opened = gitProjectCommitOpenedCard(review);
  const head = gitProjectCommitHead(review);
  const ready = gitProjectCommitReadySummary(review);
  const strip = opened.status_strip || {};
  const title = opened.title || "TAKE SNAPSHOT / COMMIT";
  const subtitle = opened.subtitle || "Local commit only · No push · No remote setup";
  const headState = strip.head || head.head_state || "unknown";
  const branch = strip.branch || ready.branch;
  const identity = strip.identity || ready.identitySource;
  const commitReady = strip.commit_ready || (review.commit_ready ? "yes" : "no");
  return `<header class="git-project-commit-header">
    <div>
      <strong>${escapeHtml(title)}</strong>
      <span>${escapeHtml(subtitle)}</span>
    </div>
    <div class="git-project-commit-status-strip">
      <span>HEAD: ${escapeHtml(headState)}</span>
      <span>Branch: ${escapeHtml(branch)}</span>
      <span>Identity: ${escapeHtml(identity)}</span>
      <span>Commit ready: ${escapeHtml(commitReady)}</span>
    </div>
  </header>`;
}

function gitProjectCommitConfigStripHtml(review = {}) {
  return `<section class="git-project-commit-config-strip" aria-label="Commit configuration">
    <div class="git-project-commit-config-title">CONFIG STRIP</div>
    <div class="git-project-commit-config-grid">
      ${gitProjectCommitFieldHtml(review, "branch", "Branch", {placeholder: "master"})}
      ${gitProjectCommitFieldHtml(review, "git_user_name", "Name", {placeholder: "Your Name"})}
      ${gitProjectCommitFieldHtml(review, "git_user_email", "Email", {type: "email", placeholder: "you@example.com"})}
      ${gitProjectCommitFieldHtml(review, "identity_scope", "Scope", {type: "radio"})}
    </div>
  </section>`;
}

function gitProjectCommitGateSummaryHtml(review = {}) {
  const gates = gitProjectCommitGateSummary(review);
  return `<section class="git-project-commit-panel git-project-commit-gate-summary" data-git-commit-panel="gate_summary">
    <div class="git-project-subscreen-panel-head">
      <strong>Gate summary</strong>
      <span>upstream cards only</span>
    </div>
    <p class="git-project-muted">This commit card summarizes upstream gates. Rule editing and ignore-rule changes stay in their own cards.</p>
    <div class="git-project-commit-gate-grid">
      ${Object.values(gates).map((gate) => `<article class="git-project-commit-gate is-${escapeHtml(gate.state || "unknown")}">
        <strong>${escapeHtml(gate.label)}</strong>
        <span>${escapeHtml(gate.state || "unknown")}</span>
        <p>${escapeHtml(gate.summary || "")}</p>
      </article>`).join("")}
    </div>
  </section>`;
}

function gitProjectCommitRepoIdentityHtml(review = {}) {
  const head = gitProjectCommitHead(review);
  const identity = gitProjectCommitIdentity(review);
  const branch = gitProjectCommitBranch(review);
  return `<section class="git-project-commit-panel" data-git-commit-panel="repo_identity">
    <div class="git-project-subscreen-panel-head">
      <strong>Repo / Identity</strong>
      <span>local UI draft</span>
    </div>
    <div class="git-project-commit-summary-grid">
      <span>HEAD state</span><code>${escapeHtml(head.head_state || "unknown")}</code>
      <span>Branch</span><code data-git-commit-summary-value="branch">${escapeHtml(branch)}</code>
      <span>First commit</span><code>${head.needs_first_commit ? "yes — this will create HEAD" : "no"}</code>
      <span>Name</span><code data-git-commit-summary-value="git_user_name">${escapeHtml(gitProjectCommitFieldValue(review, "git_user_name") || "(missing)")}</code>
      <span>Email</span><code data-git-commit-summary-value="git_user_email">${escapeHtml(gitProjectCommitFieldValue(review, "git_user_email") || "(missing)")}</code>
      <span>Identity source</span><code data-git-commit-summary-value="identity_source">${escapeHtml(gitProjectCommitIdentitySource(review))}</code>
    </div>
    ${Array.isArray(identity.problems) && identity.problems.length ? `<div class="git-project-commit-validation">${identity.problems.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>` : ""}
    <div class="git-project-commit-panel-actions">
      <button type="button" data-git-commit-action="use_current_branch">Use current branch</button>
      <button type="button" data-git-commit-action="preview_branch_command">Preview branch command</button>
      <button type="button" data-git-commit-action="preview_identity_commands">Preview identity commands</button>
    </div>
  </section>`;
}

function gitProjectCommitComposeHtml(review = {}) {
  return "";
}

function gitProjectCommitBasketControlsHtml(review = {}) {
  const groups = gitProjectCommitGroups(review);
  return `<section class="git-project-commit-panel" data-git-commit-panel="file_basket">
    <div class="git-project-subscreen-panel-head">
      <strong>File basket controls</strong>
      <span>${groups.selected_by_default.length} default · ${groups.review_before_selecting.length} review · ${groups.blocked_possible_secrets.length} blocked</span>
    </div>
    <p class="git-project-muted">Use the tree on the right to select files or whole directories. Directory selections expand to explicit file paths for staging.</p>
    <div class="git-project-commit-panel-actions">
      <button type="button" data-git-commit-basket-action="select_clean_files">Select clean files</button>
      <button type="button" data-git-commit-basket-action="clear_selection">Clear selection</button>
      <button type="button" data-git-commit-basket-action="show_review_files">Show review files</button>
      <button type="button" data-git-commit-basket-action="hide_generated_runtime_files">Hide generated/runtime files</button>
    </div>
    <div class="git-project-commit-selected-preview">
      <strong>Selected files preview</strong>
      <pre data-git-commit-selected-preview># Selected files will appear here.</pre>
    </div>
  </section>`;
}

function gitProjectCommitShellQuote(value = "") {
  const text = String(value);
  if (/^[A-Za-z0-9_@%+=:,./-]+$/.test(text)) return text;
  return `"${text.replace(/(["\\$`])/g, "\\$1")}"`;
}

function gitProjectCommitJoinCommand(parts = []) {
  return parts.map(gitProjectCommitShellQuote).join(" ");
}

function gitProjectCommitStageCommands(step = {}, review = {}) {
  const commands = Array.isArray(step.commands) ? step.commands : [];
  const rendered = commands.map(formatCommandForReport).filter(Boolean);
  const defaults = [
    "git add -- <selected files>",
    "git status --short",
    "git diff --cached --stat",
    "git diff --cached --check",
  ];
  return rendered.length ? rendered : defaults;
}


function gitProjectCommitStageStatsHtml(review = {}) {
  const groups = gitProjectCommitGroups(review);
  return `<div class="git-project-commit-review-stats">
    <div>
      <strong data-git-commit-review-count="selected">0</strong>
      <span>selected</span>
    </div>
    <div>
      <strong data-git-commit-review-count="review">${groups.review_before_selecting.length}</strong>
      <span>need review</span>
    </div>
    <div>
      <strong data-git-commit-review-count="blocked">${groups.blocked_possible_secrets.length}</strong>
      <span>blocked</span>
    </div>
  </div>`;
}

function gitProjectCommitStagePreviewHtml(step = {}) {
  const review = step.commit_review || {};
  const commands = gitProjectCommitStageCommands(step, review);
  return `<section class="git-project-commit-panel git-project-commit-stage-preview" data-git-commit-panel="stage_preview">
    <div class="git-project-subscreen-panel-head">
      <strong>Review selected files</strong>
      <span>pre-stage check</span>
    </div>
    ${gitProjectCommitStageStatsHtml(review)}
    <div class="git-project-commit-checklist">
      <strong>Required confirmations</strong>
      <label><input type="checkbox" data-git-commit-stage-check="reviewed_staged_diff"> <span>I reviewed the Selected Files Preview and it matches the intended commit.</span></label>
    </div>
    <details class="git-project-commit-dev-diagnostics" data-git-commit-dev-diagnostics>
      <summary>Developer diagnostics</summary>
      <pre data-git-commit-dev-preview>${escapeHtml(commands.join("\n"))}</pre>
    </details>
  </section>`;
}

function gitProjectCommitExecutionPaneHtml(message = "") {
  const renderedMessage = message || DEFAULT_COMMIT_MESSAGE;
  return `<div class="git-project-commit-execution-pane git-project-commit-execution-overlay" data-git-commit-execution-pane hidden>
    <section class="git-project-commit-execution-dialog" role="dialog" aria-modal="true" aria-labelledby="git-project-commit-execution-title">
      <header class="git-project-commit-execution-header">
        <div>
          <strong id="git-project-commit-execution-title">Commit execution preview</strong>
          <span data-git-commit-execution-state>dry run armed</span>
          <p>This nested sub-modal relists the exact selected files before any backend commit action. Dry Run is selected by default; uncheck it only when you want the backend to create real Git commits.</p>
        </div>
        <div class="git-project-commit-execution-header-actions">
          <button type="button" data-git-commit-action="stop_commit" disabled>Stop commit in progress</button>
          <button type="button" data-git-commit-action="close_commit_execution">Close</button>
        </div>
      </header>
      <div class="git-project-commit-execution-options">
        <label><input type="checkbox" data-git-commit-execution-option="dry_run" checked> <span><strong>Dry Run</strong><small>Selected automatically before Do Git Commit.</small></span></label>
        <label><input type="checkbox" data-git-commit-execution-option="one_at_a_time"> <span><strong>One at a time</strong><small>Create one commit per selected file, one Git call at a time.</small></span></label>
      </div>
      <main class="git-project-commit-execution-main">
        <section class="git-project-commit-execution-detail-card">
          <label class="git-project-commit-execution-message-field">
            <strong>Commit message</strong>
            <textarea data-git-commit-execution-message rows="3" placeholder="Take project snapshot">${escapeHtml(renderedMessage)}</textarea>
          </label>
          <strong>Target</strong>
          <pre data-git-commit-execution-target># Branch and identity will be verified here.</pre>
        </section>
        <section class="git-project-commit-execution-files">
          <div class="git-project-commit-execution-card-head">
            <strong>Files to stage</strong>
            <span data-git-commit-execution-file-count>0 selected files</span>
          </div>
          <ol data-git-commit-execution-files>
            <li>Select files and open the preview to relist them here.</li>
          </ol>
        </section>
        <section class="git-project-commit-execution-output-card">
          <strong>Execution output</strong>
          <div class="git-project-commit-execution-status" data-git-commit-execution-status>Waiting for commit preview.</div>
          <ol class="git-project-commit-execution-results" data-git-commit-execution-results></ol>
        </section>
      </main>
      <footer class="git-project-commit-execution-footer">
        <button type="button" class="git-project-commit-do-button" data-git-commit-action="do_git_commit">Do Git Commit</button>
      </footer>
    </section>
  </div>`;
}

function gitProjectCommitCreateHtml(step = {}) {
  const review = step.commit_review || {};
  const ready = gitProjectCommitReadySummary(review);
  const message = gitProjectCommitMessage(review);
  return `<section class="git-project-commit-panel git-project-commit-create" data-git-commit-panel="create_commit">
    <div class="git-project-subscreen-panel-head">
      <strong>Create commit</strong>
      <span>${ready.ready ? "ready" : "blocked"}</span>
    </div>
    <details class="git-project-commit-dev-diagnostics">
      <summary>Commit command</summary>
      <pre>${escapeHtml(gitProjectCommitJoinCommand(["git", "commit", "-m", message]))}</pre>
    </details>
    <div class="git-project-commit-readiness ${ready.ready ? "is-ready" : "is-blocked"}">
      <strong>Final readiness summary</strong>
      <span data-git-commit-final-readiness>${escapeHtml(ready.ready ? "All current checks are ready for a local commit." : ready.reasons.join(" · ") || "Commit is blocked until validation passes.")}</span>
    </div>
    <button type="button" class="git-project-commit-create-button" data-git-commit-action="create_local_commit" ${ready.ready ? "" : "disabled"}>Create local commit</button>
    ${gitProjectCommitExecutionPaneHtml(message)}
  </section>`;
}

function gitProjectCommitCenterHtml(step = {}, selectedPanel = "gate_summary") {
  const review = step.commit_review || {};
  return `<section class="git-project-commit-center">
    ${gitProjectCommitComposeHtml(review)}
    ${gitProjectCommitBasketControlsHtml(review)}
    ${gitProjectCommitStagePreviewHtml(step)}
    ${gitProjectCommitCreateHtml(step)}
  </section>`;
}

function gitProjectCommitNormalizeStatus(item = {}) {
  return gitProjectCommitFileBasketIntegration().normalizeStatus(item);
}

function gitProjectCommitStatusDisplay(status = "") {
  return gitProjectCommitFileBasketIntegration().statusDisplay(status);
}

function gitProjectCommitTreeStats(nodes = []) {
  return gitProjectCommitFileBasketIntegration().treeStats(nodes);
}

function gitProjectCommitFileMeta(item = {}, group = {}) {
  return gitProjectCommitFileBasketIntegration().fileMeta(item, group);
}

function gitProjectCommitCreateTreeNode(title, key, options = {}) {
  return gitProjectCommitFileBasketIntegration().createTreeNode(title, key, options);
}

function gitProjectCommitCandidateItems(review = {}) {
  return gitProjectCommitFileBasketIntegration().candidateItems(review);
}

function gitProjectCommitFileBasketAdapter() {
  return gitProjectCommitFileBasketIntegration().adapter();
}

function gitProjectCommitFileBasketModel(review = {}) {
  return gitProjectCommitFileBasketIntegration().model(review);
}

function gitProjectCommitFileBasketModelJson(model = null) {
  return gitProjectCommitFileBasketIntegration().modelJson(model);
}

function gitProjectCommitTreeFileTitleFromModel(row = {}) {
  return gitProjectCommitFileBasketIntegration().treeFileTitleFromModel(row);
}

function gitProjectCommitTreeNodeFromModelNode(modelNode = {}) {
  return gitProjectCommitFileBasketIntegration().treeNodeFromModelNode(modelNode);
}

function gitProjectCommitTreeSourceFromModel(model = null) {
  return gitProjectCommitFileBasketIntegration().treeSourceFromModel(model);
}

function gitProjectCommitSortTreeNodes(nodes = []) {
  return gitProjectCommitFileBasketIntegration().sortTreeNodes(nodes);
}

function gitProjectCommitAnnotateDirectoryStats(node) {
  return gitProjectCommitFileBasketIntegration().annotateDirectoryStats(node);
}

function gitProjectCommitFinalizeDirectorySelection(node) {
  return gitProjectCommitFileBasketIntegration().finalizeDirectorySelection(node);
}

function gitProjectCommitInsertTreePath(root, item = {}, group = {}) {
  return gitProjectCommitFileBasketIntegration().insertTreePath(root, item, group);
}

function gitProjectCommitEmptyTreeSource() {
  return gitProjectCommitFileBasketIntegration().emptyTreeSource();
}

function gitProjectCommitTreeSource(review = {}, fileBasketModel = gitProjectCommitFileBasketModel(review)) {
  return gitProjectCommitFileBasketIntegration().treeSource(review, fileBasketModel);
}

function gitProjectCommitReviewCandidatePaths(review = {}) {
  return gitProjectCommitFileBasketIntegration().reviewCandidatePaths(review);
}

function gitProjectCommitStepFromInspection(data = {}) {
  const steps = Array.isArray(data?.wizard?.steps) ? data.wizard.steps : [];
  return steps.find((step = {}) => gitProjectStepIsCommitCard(step)) || {};
}

function gitProjectCommitReviewFromInspection(data = {}) {
  return gitProjectCommitStepFromInspection(data).commit_review || {};
}

function gitProjectCommitFallbackTreeHtml(nodes = []) {
  return gitProjectCommitFileBasketIntegration().fallbackTreeHtml(nodes, {escapeHtml});
}

function gitProjectCommitBasketHtml(review = {}) {
  return gitProjectCommitFileBasketIntegration().basketHtml(review, gitProjectCommitFileBasketHooks());
}

function gitProjectWunderbaumConstructor() {
  return window.mar10?.Wunderbaum || window.Wunderbaum || window.wunderbaum?.Wunderbaum || null;
}

function gitProjectEnsureCommitTreeStylesheet(href, assetName) {
  if (!href || document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  link.dataset.gitCommitWunderbaumAsset = assetName || "css";
  document.head.appendChild(link);
}

function gitProjectLoadWunderbaum() {
  const loaded = gitProjectWunderbaumConstructor();
  if (loaded) return Promise.resolve(loaded);
  if (gitProjectWunderbaumLoadPromise) return gitProjectWunderbaumLoadPromise;
  gitProjectWunderbaumLoadPromise = new Promise((resolve, reject) => {
    gitProjectEnsureCommitTreeStylesheet(GIT_PROJECT_WUNDERBAUM_ASSETS.css, "css");
    gitProjectEnsureCommitTreeStylesheet(GIT_PROJECT_WUNDERBAUM_ASSETS.icons, "icons");
    const finish = () => {
      const constructor = gitProjectWunderbaumConstructor();
      if (constructor) {
        resolve(constructor);
      } else {
        reject(new Error("Wunderbaum global was not registered."));
      }
    };
    const existing = document.querySelector(`script[src="${GIT_PROJECT_WUNDERBAUM_ASSETS.js}"]`);
    if (existing) {
      if (gitProjectWunderbaumConstructor()) {
        finish();
        return;
      }
      existing.addEventListener("load", finish, {once: true});
      existing.addEventListener("error", () => reject(new Error("Could not load Wunderbaum.")), {once: true});
      window.setTimeout(() => {
        if (gitProjectWunderbaumConstructor()) {
          finish();
        } else {
          reject(new Error("Existing Wunderbaum script did not register a constructor."));
        }
      }, 3000);
      return;
    }
    const script = document.createElement("script");
    script.src = GIT_PROJECT_WUNDERBAUM_ASSETS.js;
    script.async = true;
    script.dataset.gitCommitWunderbaumAsset = "js";
    script.onload = finish;
    script.onerror = () => reject(new Error("Could not load Wunderbaum."));
    document.head.appendChild(script);
  });
  return gitProjectWunderbaumLoadPromise;
}

function gitProjectCommitReadTreeSource(workbench) {
  return gitProjectCommitFileBasketIntegration().readTreeSource(workbench);
}

function gitProjectCommitReadFileBasketModel(workbench) {
  return gitProjectCommitFileBasketIntegration().readFileBasketModel(workbench);
}

function gitProjectCommitSortSelectedPaths(paths = []) {
  return gitProjectCommitFileBasketIntegration().sortSelectedPaths(paths);
}

function gitProjectCommitAdapterSelectedOutput(workbench, paths = []) {
  return gitProjectCommitFileBasketIntegration().adapterSelectedOutput(workbench, paths);
}

function gitProjectCommitSelectionAdapterReport(workbench, rawPaths = []) {
  return gitProjectCommitFileBasketIntegration().selectionAdapterReport(workbench, rawPaths);
}

function gitProjectCommitFlattenTreeFiles(nodes = [], out = []) {
  return gitProjectCommitFileBasketIntegration().flattenTreeFiles(nodes, out);
}

function gitProjectCommitBuildFileIndex(files = []) {
  return gitProjectCommitFileBasketIntegration().buildFileIndex(files);
}

function gitProjectCommitCleanPathCandidate(value = "") {
  return gitProjectCommitFileBasketIntegration().cleanPathCandidate(value);
}

function gitProjectCommitCanonicalFilePath(value = "", index = gitProjectCommitBuildFileIndex()) {
  return gitProjectCommitFileBasketIntegration().canonicalFilePath(value, index);
}

function gitProjectCommitTreeNodePath(node, index) {
  return gitProjectCommitFileBasketIntegration().treeNodePath(node, index);
}

function gitProjectCommitTreeNodeSelected(node) {
  return gitProjectCommitFileBasketIntegration().treeNodeSelected(node);
}

function gitProjectCommitVisitTreeNodes(tree, visitor) {
  return gitProjectCommitFileBasketIntegration().visitTreeNodes(tree, visitor);
}

function gitProjectCommitSelectedFilesFromFallback(workbench) {
  return gitProjectCommitFileBasketIntegration().selectedFilesFromFallback(workbench);
}

function gitProjectCommitSelectedFilesFromWunderbaum(tree) {
  return gitProjectCommitFileBasketIntegration().selectedFilesFromWunderbaum(tree);
}

function gitProjectCommitSelectedFilesFromDom(workbench) {
  return gitProjectCommitFileBasketIntegration().selectedFilesFromDom(workbench);
}

function gitProjectCommitSelectedFilesFromWorkbench(workbench) {
  return gitProjectCommitFileBasketIntegration().selectedFilesFromWorkbench(workbench);
}

function gitProjectCommitInitializeContractTreegrid(workbench) {
  const contractView = gitProjectCommitFileBasketContractView();
  if (!workbench?.querySelector?.("[data-git-commit-contract-treegrid]") || typeof contractView?.initializeContractTreegrid !== "function") {
    return false;
  }
  return contractView.initializeContractTreegrid(workbench, {
    onSelectionChange: (selectedPaths = []) => gitProjectCommitUpdateSelectedPreview(workbench, selectedPaths),
  });
}

function gitProjectCommitReviewStats(workbench, selectedPaths = []) {
  return gitProjectCommitFileBasketIntegration().reviewStats(workbench, selectedPaths);
}

function gitProjectCommitControlChecked(workbench, key = "") {
  const input = workbench?.querySelector?.(`[data-git-commit-stage-check="${CSS.escape(key)}"]`);
  return Boolean(input?.checked);
}

function gitProjectCommitSummaryValue(workbench, key = "") {
  const node = workbench?.querySelector?.(`[data-git-commit-summary-value="${CSS.escape(key)}"]`);
  const value = String(node?.textContent || "").trim();
  return value && value !== "(missing)" ? value : "";
}

function gitProjectCommitMessageNodeValue(node) {
  if (!node) return "";
  return String("value" in node ? node.value : node.textContent || "").trim();
}

function gitProjectCommitSetMessageNodeValue(node, value = "") {
  if (!node) return;
  const nextValue = String(value ?? "");
  if ("value" in node) {
    if (node.value !== nextValue) node.value = nextValue;
  } else if (node.textContent !== nextValue) {
    node.textContent = nextValue;
  }
}

function gitProjectCommitMessageFromExecutionPane(workbench) {
  const messageNode = workbench?.querySelector?.("[data-git-commit-execution-message]");
  return gitProjectCommitMessageNodeValue(messageNode);
}

function gitProjectCommitMessageFromWorkbench(workbench) {
  const messageNode = workbench?.querySelector?.("[data-git-commit-execution-message]");
  if (messageNode) return gitProjectCommitMessageNodeValue(messageNode);
  const input = workbench?.querySelector?.('[data-git-commit-field="commit_message"] input');
  return String(input?.value || DEFAULT_COMMIT_MESSAGE || "").trim();
}

function gitProjectCommitBranchFromWorkbench(workbench) {
  const input = workbench?.querySelector?.('[data-git-commit-field="branch"] input');
  return String(input?.value || gitProjectCommitSummaryValue(workbench, "branch") || "master").trim();
}

function gitProjectCommitIdentityFromWorkbench(workbench) {
  const name = String(
    workbench?.querySelector?.('[data-git-commit-field="git_user_name"] input')?.value ||
    gitProjectCommitSummaryValue(workbench, "git_user_name") ||
    ""
  ).trim();
  const email = String(
    workbench?.querySelector?.('[data-git-commit-field="git_user_email"] input')?.value ||
    gitProjectCommitSummaryValue(workbench, "git_user_email") ||
    ""
  ).trim();
  return {name, email, ready: Boolean(name && email.includes("@"))};
}

function gitProjectCommitSelectedReadiness(workbench, paths = []) {
  const stats = gitProjectCommitReviewStats(workbench, paths);
  const selectedPhrase = paths.length ? `${paths.length} FILE${paths.length === 1 ? "" : "S"} SELECTED` : "NO FILES SELECTED";
  const reviewed = gitProjectCommitControlChecked(workbench, "reviewed_staged_diff");
  const message = gitProjectCommitMessageFromWorkbench(workbench);
  const branch = gitProjectCommitBranchFromWorkbench(workbench);
  const identity = gitProjectCommitIdentityFromWorkbench(workbench);
  const repoWarningsPresent = Boolean(stats.review || stats.blocked || stats.selectedReview);
  const reasons = [];
  let status = "blocked";

  if (!paths.length) {
    reasons.push("choose files before commit");
  }
  if (stats.selectedBlocked) {
    reasons.push(`${stats.selectedBlocked} selected hard blocker${stats.selectedBlocked === 1 ? "" : "s"}`);
  }
  if (paths.length && !reviewed) {
    reasons.push("selected files preview not confirmed");
  }
  if (!message) {
    reasons.push("commit message missing");
  }
  if (!branch) {
    reasons.push("branch missing");
  }
  if (!identity.ready) {
    reasons.push("identity incomplete");
  }

  const ready = reasons.length === 0;
  if (ready) {
    status = repoWarningsPresent ? "ready-with-warnings" : "ready";
  } else if (stats.selectedBlocked) {
    status = "selected-blocked";
  } else if (paths.length && !reviewed) {
    status = "review-needed";
  } else if (!paths.length) {
    status = "empty";
  }

  const summary = ready
    ? `${repoWarningsPresent ? "WARNINGS PRESENT" : "GATES CLEAR"} · ${selectedPhrase} · READY TO COMMIT`
    : `${selectedPhrase} · ${reasons.join(" · ") || "commit is blocked until validation passes"}`;

  return {
    ready,
    status,
    reasons,
    summary,
    stats,
    selectedPhrase,
    reviewed,
    repoWarningsPresent,
    message,
    branch,
    identity,
  };
}

function gitProjectCommitSelectedPreviewText(paths = []) {
  if (!paths.length) return "No files selected yet. Select clean files from the File Basket on the right.";
  const shown = paths.slice(0, 18);
  const hidden = paths.length - shown.length;
  return [
    `${paths.length} file${paths.length === 1 ? "" : "s"} selected:`,
    ...shown.map((path) => `✓ ${path}`),
    hidden > 0 ? `… ${hidden} more` : "",
  ].filter(Boolean).join("\n");
}

function gitProjectCommitDeveloperCommandPreview(paths = []) {
  if (!paths.length) {
    return [
      "git add -- <selected files>",
      "git diff --cached --stat",
      "git diff --cached --check",
    ].join("\n");
  }
  return [
    `git add -- ${paths.map((path) => gitProjectCommitShellQuote(path)).join(" ")}`,
    "git diff --cached --stat",
    "git diff --cached --check",
  ].join("\n");
}

function gitProjectCommitUpdateReviewStatus(workbench, paths = []) {
  const state = gitProjectCommitSelectedReadiness(workbench, paths);
  const {stats} = state;

  const selectedCount = workbench.querySelector("[data-git-commit-review-count='selected']");
  const reviewCount = workbench.querySelector("[data-git-commit-review-count='review']");
  const blockedCount = workbench.querySelector("[data-git-commit-review-count='blocked']");
  if (selectedCount) selectedCount.textContent = String(stats.selected);
  if (reviewCount) reviewCount.textContent = String(stats.review);
  if (blockedCount) blockedCount.textContent = String(stats.blocked);

  const diagnostics = workbench.querySelector("[data-git-commit-dev-diagnostics]");
  const preview = workbench.querySelector("[data-git-commit-dev-preview]");
  if (preview) preview.textContent = gitProjectCommitDeveloperCommandPreview(paths);
  if (diagnostics) diagnostics.hidden = false;
}

function gitProjectCommitUpdateFinalReadiness(workbench, paths = []) {
  const state = gitProjectCommitSelectedReadiness(workbench, paths);
  const readiness = workbench.querySelector("[data-git-commit-final-readiness]");
  if (readiness) {
    readiness.textContent = state.summary;
    const box = readiness.closest(".git-project-commit-readiness");
    if (box) {
      box.classList.toggle("is-ready", state.ready);
      box.classList.toggle("is-blocked", !state.ready);
    }
  }

  const panelState = workbench.querySelector("[data-git-commit-panel='create_commit'] .git-project-subscreen-panel-head span");
  if (panelState) panelState.textContent = state.ready ? "ready" : "blocked";

  const createButton = workbench.querySelector('[data-git-commit-action="create_local_commit"]');
  if (createButton) {
    createButton.disabled = !state.ready;
    createButton.title = state.ready
      ? "Open the dry-run commit preview pane."
      : `Commit preview is blocked: ${state.reasons.join(" · ")}`;
  }


  gitProjectCommitUpdateExecutionPane(workbench, paths, state);
}

function gitProjectCommitRenderExecutionFiles(listNode, countNode, paths = [], options = {}) {
  const label = options.label || "selected";
  const noun = options.noun || "file";
  if (countNode) countNode.textContent = `${paths.length} ${label} ${noun}${paths.length === 1 ? "" : "s"}`;
  if (!listNode) return;
  listNode.innerHTML = "";
  if (!paths.length) {
    const item = document.createElement("li");
    item.textContent = options.emptyText || "No files selected.";
    listNode.appendChild(item);
    return;
  }
  paths.forEach((path) => {
    const item = document.createElement("li");
    item.textContent = path;
    listNode.appendChild(item);
  });
}

function gitProjectCommitExecutionTargetText(state = {}) {
  const identity = state.identity || {};
  const target = gitProjectCommitTargetMismatch(null, gitProjectCommitRepoFromWorkbench(null));
  return [
    `Repository: ${target.expected || gitProjectCommitRepoFromWorkbench(null) || "(unknown)"}`,
    `Branch: ${state.branch || "master"}`,
    `Identity: ${identity.name || "(missing)"} <${identity.email || "missing"}>`,
    target.ok ? "" : `Target mismatch: ${target.mismatches.join(" | ")}`,
  ].filter(Boolean).join("\n");
}

function gitProjectCommitUpdateExecutionPane(workbench, paths = [], state = null) {
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  if (!pane) return;
  const currentState = state || gitProjectCommitSelectedReadiness(workbench, paths);
  const messageNode = pane.querySelector("[data-git-commit-execution-message]");
  const targetNode = pane.querySelector("[data-git-commit-execution-target]");
  const filesNode = pane.querySelector("[data-git-commit-execution-files]");
  const fileCountNode = pane.querySelector("[data-git-commit-execution-file-count]");
  const stateNode = pane.querySelector("[data-git-commit-execution-state]");
  const doButton = pane.querySelector('[data-git-commit-action="do_git_commit"]');
  const dryRun = pane.querySelector('[data-git-commit-execution-option="dry_run"]');
  if (messageNode) {
    const renderedMessage = typeof currentState.message === "string" ? currentState.message : DEFAULT_COMMIT_MESSAGE;
    gitProjectCommitSetMessageNodeValue(messageNode, renderedMessage);
  }
  if (targetNode) targetNode.textContent = gitProjectCommitExecutionTargetText(currentState);
  gitProjectCommitRenderExecutionFiles(filesNode, fileCountNode, paths);
  const isDryRun = dryRun?.checked !== false;
  if (stateNode) stateNode.textContent = isDryRun ? "dry run armed" : "real commit armed";
  if (doButton) {
    doButton.disabled = !currentState.ready;
    doButton.title = currentState.ready
      ? (isDryRun ? "Run the backend dry-run commit sequence." : "Start the real backend commit sequence.")
      : `Cannot run yet: ${currentState.reasons.join(" · ")}`;
  }
}

function gitProjectCommitRefreshExecutionRemainingFiles(workbench, review = {}) {
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  if (!pane || pane.hidden) return [];
  const paths = gitProjectCommitReviewCandidatePaths(review);
  const filesNode = pane.querySelector("[data-git-commit-execution-files]");
  const fileCountNode = pane.querySelector("[data-git-commit-execution-file-count]");
  const stateNode = pane.querySelector("[data-git-commit-execution-state]");
  gitProjectCommitRenderExecutionFiles(filesNode, fileCountNode, paths, {
    label: "remaining uncommitted",
    emptyText: "No uncommitted files remain after this commit.",
  });
  if (stateNode) stateNode.textContent = paths.length ? "uncommitted list refreshed" : "clean after commit";
  return paths;
}

function gitProjectCommitReinitializeBasketTree(workbench) {
  if (!workbench) return;
  const contractTreegrid = workbench.querySelector("[data-git-commit-contract-treegrid]");
  if (contractTreegrid) {
    delete contractTreegrid.dataset.gitCommitContractTreegridReady;
  }
  const element = workbench.querySelector("[data-git-commit-tree]");
  if (element) {
    delete element.dataset.gitWunderbaumReady;
    element._wb_tree = null;
  }
  const fallback = workbench.querySelector("[data-git-commit-tree-fallback]");
  if (fallback) {
    delete fallback.dataset.gitCommitFallbackReady;
    fallback.hidden = false;
  }
  delete workbench.dataset.gitCommitWorkbenchReady;
  delete workbench.dataset.gitCommitWunderbaumFallback;
  workbench.gitCommitWunderbaum = null;
  if (!gitProjectCommitInitializeContractTreegrid(workbench)) {
    gitProjectInitializeCommitWunderbaum(workbench);
  }
}

function gitProjectCommitRefreshWorkbenchFromReview(workbench, step = {}) {
  const review = step.commit_review || {};
  if (!workbench || !Object.keys(review).length) return [];
  const body = workbench.querySelector(".git-project-commit-body");
  if (!body) return gitProjectCommitReviewCandidatePaths(review);


  const fileBasketPanel = body.querySelector("[data-git-commit-panel='file_basket']");
  if (fileBasketPanel) fileBasketPanel.outerHTML = gitProjectCommitBasketControlsHtml(review);

  const stagePreviewPanel = body.querySelector("[data-git-commit-panel='stage_preview']");
  if (stagePreviewPanel) stagePreviewPanel.outerHTML = gitProjectCommitStagePreviewHtml(step);

  const right = body.querySelector("[data-git-commit-basket]");
  if (right) {
    right.outerHTML = gitProjectCommitBasketHtml(review);
  } else {
    body.insertAdjacentHTML("beforeend", gitProjectCommitBasketHtml(review));
  }

  gitProjectCommitReinitializeBasketTree(workbench);
  const selectedPaths = gitProjectCommitSelectedFilesFromWorkbench(workbench);
  gitProjectCommitUpdateSelectedPreview(workbench, selectedPaths);
  return gitProjectCommitReviewCandidatePaths(review);
}

function gitProjectCommitEventCreatedRealCommit(event = {}) {
  const result = event.result || {};
  if (result.dry_run === true) return false;
  if (result.commit_hash) return true;
  if (Array.isArray(result.commits) && result.commits.some((commit = {}) => commit.hash)) return true;
  return Boolean(event.commit_hash);
}

async function gitProjectCommitRefreshAfterCompletion(workbench, event = {}) {
  if (!gitProjectCommitEventCreatedRealCommit(event)) return null;
  const current = currentGitProject() || {};
  const payload = current.id ? {project_id: current.id} : {};
  try {
    gitProjectCommitSetExecutionStatus(workbench, "Commit finished. Refreshing uncommitted file list…");
    const data = await gitToolsStatusApi().inspectProject(payload);
    gitProjectLastInspection = data;
    if (data.project?.path) gitProjectSetTargetPathInputs(data.project.path);
    if (gitProjectsLastState && data.project) {
      gitProjectsLastState.current_project = data.project;
      renderGitProjects(gitProjectsLastState);
    }
    const step = gitProjectCommitStepFromInspection(data);
    const review = step.commit_review || {};
    if (!Object.keys(review).length) {
      gitProjectCommitAppendExecutionLine(workbench, "Fresh inspection completed, but no commit review was returned.");
      return data;
    }
    const remaining = gitProjectCommitRefreshWorkbenchFromReview(workbench, step);
    const displayed = gitProjectCommitRefreshExecutionRemainingFiles(workbench, review);
    const count = displayed.length || remaining.length;
    gitProjectCommitAppendExecutionLine(
      workbench,
      count
        ? `Refreshed uncommitted file list after commit: ${count} candidate file${count === 1 ? "" : "s"} remain.`
        : "Refreshed uncommitted file list after commit: no candidate files remain."
    );
    gitProjectCommitSetExecutionStatus(workbench, count ? "Commit finished. Uncommitted file list refreshed." : "Commit finished. No uncommitted files remain.");
    return data;
  } catch (error) {
    gitProjectCommitAppendExecutionLine(workbench, gitToolsOperationErrorText("Could not refresh uncommitted file list after commit", error));
    return null;
  }
}

function gitProjectCommitOpenExecutionPane(workbench) {
  const paths = gitProjectCommitSelectedFilesFromWorkbench(workbench);
  const state = gitProjectCommitSelectedReadiness(workbench, paths);
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  if (!pane) return;
  pane.hidden = false;
  document.body.classList.add("git-project-commit-execution-modal-open");
  gitProjectCommitUpdateExecutionPane(workbench, paths, state);
  const results = pane.querySelector("[data-git-commit-execution-results]");
  if (results) results.innerHTML = "";
  gitProjectCommitSetExecutionStatus(workbench, state.ready ? "Dry Run is selected. Review the relisted files, then choose Do Git Commit." : state.summary);
  gitProjectCommitAppendExecutionLine(
    workbench,
    state.ready ? "Ready for dry-run preview. Click Do Git Commit to relist staged files and preview the commit operation." : `Blocked: ${state.summary}`
  );
  const runButton = pane.querySelector('[data-git-commit-action="do_git_commit"]');
  if (runButton && !runButton.disabled) runButton.focus();
}

function gitProjectCommitCloseExecutionPane(workbench) {
  gitProjectCommitStopExecution(workbench, "Commit preview closed.");
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  if (pane) pane.hidden = true;
  document.body.classList.remove("git-project-commit-execution-modal-open");
}

function gitProjectCommitSetExecutionStatus(workbench, message = "") {
  const status = workbench?.querySelector?.("[data-git-commit-execution-status]");
  if (status) status.textContent = message;
}

function gitProjectCommitAppendExecutionLine(workbench, message = "") {
  const results = workbench?.querySelector?.("[data-git-commit-execution-results]");
  if (!results) return;
  const item = document.createElement("li");
  item.textContent = message;
  results.appendChild(item);
  item.scrollIntoView({behavior: "smooth", block: "nearest", inline: "nearest"});
}

function gitProjectCommitSetExecutionRunning(workbench, running) {
  const stopButton = workbench?.querySelector?.('[data-git-commit-action="stop_commit"]');
  const doButton = workbench?.querySelector?.('[data-git-commit-action="do_git_commit"]');
  if (stopButton) stopButton.disabled = !running;
  if (doButton) doButton.disabled = running;
}

function gitProjectCommitStopExecution(workbench, message = "Commit run stopped.") {
  const run = workbench?.__gitCommitExecutionRun;
  if (run?.timer) window.clearTimeout(run.timer);
  if (run) run.cancelled = true;
  gitProjectCommitSetExecutionStatus(workbench, message);
  gitProjectCommitAppendExecutionLine(workbench, message);
  const stopButton = workbench?.querySelector?.('[data-git-commit-action="stop_commit"]');
  const doButton = workbench?.querySelector?.('[data-git-commit-action="do_git_commit"]');
  if (stopButton) stopButton.disabled = true;
  if (doButton) doButton.disabled = true;
  if (run?.jobId && !run.cancelRequestSent) {
    run.cancelRequestSent = true;
    gitToolsStatusApi().cancelProjectCommit(run.jobId)
      .then((data) => {
        gitProjectCommitAppendExecutionLine(workbench, data.message || "Backend cancellation requested.");
        if (data.status) gitProjectCommitSetExecutionStatus(workbench, `Backend commit job status: ${data.status}`);
        if (!run.stream) gitProjectCommitSetExecutionRunning(workbench, false);
      })
      .catch((error) => {
        gitProjectCommitSetExecutionRunning(workbench, false);
        gitProjectCommitAppendExecutionLine(workbench, gitToolsOperationErrorText("Commit cancellation request failed", error));
      });
  } else {
    gitProjectCommitSetExecutionRunning(workbench, false);
  }
}

function gitProjectCommitRepoFromWorkbench(workbench) {
  const current = currentGitProject() || {};
  const inspection = gitProjectLastInspection?.project || {};
  const inspectionBelongsToCurrent = !inspection.id || !current.id || inspection.id === current.id;
  const candidates = [
    current.path,
    inspectionBelongsToCurrent ? gitProjectLastInspection?.selected_project : "",
    inspectionBelongsToCurrent ? inspection.path : "",
    workbench?.dataset?.gitCommitRepo,
    gitRepoDir?.value,
  ];
  for (const candidate of candidates) {
    const cleaned = String(candidate || "").trim();
    if (cleaned && cleaned !== ".") return cleaned;
  }
  return ".";
}

function gitProjectCommitExecutionPayload(workbench, paths = [], state = {}) {
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  const dryRun = pane?.querySelector?.('[data-git-commit-execution-option="dry_run"]')?.checked !== false;
  const oneAtATime = pane?.querySelector?.('[data-git-commit-execution-option="one_at_a_time"]')?.checked === true;
  const identity = state.identity || gitProjectCommitIdentityFromWorkbench(workbench);
  const repoDir = gitProjectCommitRepoFromWorkbench(workbench);
  const currentProject = currentGitProject() || {};
  const inspectionProject = gitProjectLastInspection?.project || {};
  const project = inspectionProject.id && currentProject.id && inspectionProject.id !== currentProject.id ? currentProject : (inspectionProject.id ? inspectionProject : currentProject);
  return {
    repo_dir: repoDir,
    project_id: project.id || "",
    project_path: project.path || repoDir,
    payload_repo_source: {
      workbench_repo: String(workbench?.dataset?.gitCommitRepo || ""),
      inspection_selected_project: String(gitProjectLastInspection?.selected_project || ""),
      inspection_project_path: String(gitProjectLastInspection?.project?.path || ""),
      current_project_path: String(currentGitProject()?.path || ""),
      input_project_path: String(gitProjectPath?.value || ""),
      input_repo_dir: String(gitRepoDir?.value || ""),
    },
    paths,
    selected_paths: paths,
    blocked_paths: Array.isArray(state.stats?.selectedBlockedPaths) ? state.stats.selectedBlockedPaths : [],
    message: gitProjectCommitMessageFromWorkbench(workbench) || state.message || DEFAULT_COMMIT_MESSAGE,
    branch: state.branch || gitProjectCommitBranchFromWorkbench(workbench),
    git_user_name: identity.name || "",
    git_user_email: identity.email || "",
    dry_run: dryRun,
    one_at_a_time: oneAtATime,
    confirm_real_commit: !dryRun,
  };
}

function gitProjectCommitFormatGitState(state = {}) {
  const staged = Array.isArray(state.staged) ? state.staged : [];
  const unstaged = Array.isArray(state.unstaged) ? state.unstaged : [];
  const untracked = Array.isArray(state.untracked) ? state.untracked : [];
  const untrackedLine = state.untracked_skipped
    ? `Untracked: ${state.untracked_note || "scan skipped for speed"}`
    : `Untracked: ${untracked.length ? untracked.join(", ") : "(none)"}`;
  return [
    `Current Git state: branch ${state.branch || "(unknown)"}`,
    `Staged: ${staged.length ? staged.join(", ") : "(none)"}`,
    `Unstaged: ${unstaged.length ? unstaged.join(", ") : "(none)"}`,
    untrackedLine,
    state.recovery_hint ? `Recovery hint: ${state.recovery_hint}` : "",
  ].filter(Boolean).join("\n");
}

function gitProjectCommitAppendExecutionEvent(workbench, event = {}) {
  const type = event.type || "event";
  const message = event.message || event.error || event.status || type;
  if (type === "command_finish" && Number(event.returncode || 0) === 0) return;
  if (type === "git_state" && event.git_state) {
    gitProjectCommitAppendExecutionLine(workbench, gitProjectCommitFormatGitState(event.git_state));
    return;
  }
  if (type === "finished" && event.result?.git_state) {
    gitProjectCommitAppendExecutionLine(workbench, gitProjectCommitFormatGitState(event.result.git_state));
  }
  if (type === "complete" && event.commit_hash) {
    gitProjectCommitAppendExecutionLine(workbench, `${message} (${event.commit_hash})`);
    return;
  }
  if (type === "command_start") {
    gitProjectCommitAppendExecutionLine(workbench, message);
    return;
  }
  if (type === "command_finish" && Number(event.returncode || 0) !== 0) {
    gitProjectCommitAppendExecutionLine(workbench, `${message}\n${event.stderr || event.stdout || ""}`.trim());
    return;
  }
  if (type === "summary" && Array.isArray(event.selected_files)) {
    gitProjectCommitAppendExecutionLine(workbench, `${message}\n${event.selected_files.map((path) => `- ${path}`).join("\n")}`);
    return;
  }
  gitProjectCommitAppendExecutionLine(workbench, message);
}

function gitProjectCommitStartEventStream(workbench, data = {}) {
  const url = data.stream_url || (data.job_id ? `/api/applications/git/project/commit/stream?job_id=${encodeURIComponent(data.job_id)}` : "");
  if (!url || typeof EventSource === "undefined") {
    gitProjectCommitSetExecutionRunning(workbench, false);
    gitProjectCommitSetExecutionStatus(workbench, "Commit job started, but this browser cannot open an event stream.");
    return;
  }
  const run = workbench.__gitCommitExecutionRun || {};
  const stream = new EventSource(url);
  run.stream = stream;
  run.jobId = data.job_id || run.jobId || "";
  run.cancelled = false;
  workbench.__gitCommitExecutionRun = run;
  stream.addEventListener("commit", (event) => {
    let payload = {};
    try {
      payload = JSON.parse(event.data || "{}");
    } catch (_error) {
      payload = {type: "line", message: event.data || ""};
    }
    gitProjectCommitAppendExecutionEvent(workbench, payload);
    const finalType = ["finished", "failed", "cancelled", "stream_timeout"].includes(payload.type || "");
    if (payload.type === "complete") {
      gitProjectCommitSetExecutionStatus(workbench, payload.message || "Commit job complete.");
    } else if (payload.type === "failed") {
      gitProjectCommitSetExecutionStatus(workbench, payload.message || "Commit job failed.");
    } else if (payload.type === "cancelled") {
      gitProjectCommitSetExecutionStatus(workbench, payload.message || "Commit job cancelled.");
    } else if (payload.type === "finished") {
      gitProjectCommitSetExecutionStatus(workbench, payload.message || "Commit job finished.");
    }
    if (finalType) {
      stream.close();
      gitProjectCommitSetExecutionRunning(workbench, false);
      refreshGitStatus().catch(() => null);
      gitProjectCommitRefreshAfterCompletion(workbench, payload).catch((error) => {
        gitProjectCommitAppendExecutionLine(workbench, gitToolsOperationErrorText("Could not refresh uncommitted file list after commit", error));
      });
    }
  });
  stream.onerror = () => {
    gitProjectCommitAppendExecutionLine(workbench, "Commit event stream disconnected.");
    stream.close();
    gitProjectCommitSetExecutionRunning(workbench, false);
    gitProjectCommitSetExecutionStatus(workbench, "Commit event stream disconnected.");
  };
}

async function gitProjectCommitRunExecution(workbench) {
  const paths = gitProjectCommitSelectedFilesFromWorkbench(workbench);
  const state = gitProjectCommitSelectedReadiness(workbench, paths);
  gitProjectCommitUpdateExecutionPane(workbench, paths, state);
  const pane = workbench?.querySelector?.("[data-git-commit-execution-pane]");
  const results = pane?.querySelector?.("[data-git-commit-execution-results]");
  const payload = gitProjectCommitExecutionPayload(workbench, paths, state);
  const targetCheck = gitProjectCommitTargetMismatch(workbench, payload.repo_dir || payload.project_path || "");
  if (results) results.innerHTML = "";
  if (!targetCheck.ok) {
    const message = [
      "Commit target mismatch:",
      `selected project = ${targetCheck.expected || "(missing)"}`,
      ...targetCheck.mismatches,
      "",
      "Select and inspect the intended project again before committing.",
    ].join("\n");
    gitProjectCommitSetExecutionStatus(workbench, "Commit target mismatch. Backend call was not started.");
    gitProjectCommitAppendExecutionLine(workbench, message);
    return;
  }
  if (!state.ready) {
    gitProjectCommitSetExecutionStatus(workbench, state.summary);
    gitProjectCommitAppendExecutionLine(workbench, `Blocked: ${state.reasons.join(" · ")}`);
    return;
  }
  if (!payload.dry_run) {
    const modeText = payload.one_at_a_time
      ? `This will create ${paths.length} real Git commit${paths.length === 1 ? "" : "s"}, one selected file per commit.`
      : "This will create one real Git commit containing the selected files.";
    if (!window.confirm(`${modeText}\n\nThe backend may leave Git staged or otherwise messy if you stop it mid-run. Continue?`)) {
      gitProjectCommitSetExecutionStatus(workbench, "Real commit cancelled before backend start.");
      gitProjectCommitAppendExecutionLine(workbench, "Real commit was not started.");
      return;
    }
  }

  const run = {cancelled: false, jobId: "", stream: null};
  workbench.__gitCommitExecutionRun = run;
  gitProjectCommitSetExecutionRunning(workbench, true);
  gitProjectCommitSetExecutionStatus(
    workbench,
    payload.dry_run
      ? (payload.one_at_a_time ? "Backend dry run in progress: one-at-a-time plan." : "Backend dry run in progress.")
      : (payload.one_at_a_time ? "Real backend commits in progress: one selected file per commit." : "Real backend commit in progress.")
  );
  gitProjectCommitAppendExecutionLine(workbench, "Starting backend selected-file commit job...");
  try {
    const data = await gitToolsStatusApi().startProjectCommit(payload);
    run.jobId = data.job_id || "";
    gitProjectCommitAppendExecutionLine(workbench, `Commit job started: ${run.jobId || "(unknown job id)"}`);
    gitProjectCommitStartEventStream(workbench, data);
  } catch (error) {
    gitProjectCommitSetExecutionRunning(workbench, false);
    gitProjectCommitSetExecutionStatus(workbench, `Commit job failed to start: ${error?.message || error}`);
    gitProjectCommitAppendExecutionLine(workbench, gitToolsOperationErrorText("Commit job failed to start", error));
  }
}

function gitProjectWireCommitExecution(workbench) {
  if (!workbench || workbench.dataset.gitCommitExecutionReady === "true") return;
  workbench.dataset.gitCommitExecutionReady = "true";
  workbench.addEventListener("click", (event) => {
    const overlay = event.target?.closest?.("[data-git-commit-execution-pane]");
    if (overlay && event.target === overlay) {
      event.preventDefault();
      event.stopPropagation();
      gitProjectCommitCloseExecutionPane(workbench);
      return;
    }

    const button = event.target?.closest?.("[data-git-commit-action]");
    if (!button || !workbench.contains(button)) return;
    const action = button.dataset.gitCommitAction || "";
    if (!["create_local_commit", "do_git_commit", "stop_commit", "close_commit_execution"].includes(action)) return;
    event.preventDefault();
    event.stopPropagation();
    if (action === "create_local_commit") {
      gitProjectCommitOpenExecutionPane(workbench);
    } else if (action === "do_git_commit") {
      gitProjectCommitRunExecution(workbench).catch((error) => {
        gitProjectCommitSetExecutionRunning(workbench, false);
        gitProjectCommitSetExecutionStatus(workbench, `Commit run failed: ${error?.message || error}`);
        gitProjectCommitAppendExecutionLine(workbench, gitToolsOperationErrorText("Commit run failed", error));
      });
    } else if (action === "stop_commit") {
      gitProjectCommitStopExecution(workbench, "Stopped by user before the commit sequence completed.");
    } else if (action === "close_commit_execution") {
      gitProjectCommitCloseExecutionPane(workbench);
    }
  });
  workbench.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !workbench.querySelector("[data-git-commit-execution-pane]")?.hidden) {
      event.preventDefault();
      gitProjectCommitCloseExecutionPane(workbench);
    }
  });
  workbench.addEventListener("change", (event) => {
    if (event.target?.matches?.("[data-git-commit-stage-check], [data-git-commit-execution-option]")) {
      const paths = gitProjectCommitSelectedFilesFromWorkbench(workbench);
      gitProjectCommitUpdateReviewStatus(workbench, paths);
      gitProjectCommitUpdateFinalReadiness(workbench, paths);
    }
  });
  workbench.addEventListener("input", (event) => {
    const messageChanged = event.target?.matches?.("[data-git-commit-execution-message]");
    const configChanged = event.target?.closest?.('[data-git-commit-field="branch"], [data-git-commit-field="git_user_name"], [data-git-commit-field="git_user_email"]');
    if (messageChanged || configChanged) {
      const paths = gitProjectCommitSelectedFilesFromWorkbench(workbench);
      gitProjectCommitUpdateReviewStatus(workbench, paths);
      gitProjectCommitUpdateFinalReadiness(workbench, paths);
    }
  });
}

function gitProjectCommitUpdateSelectedPreview(workbench, paths = null) {
  const selectedPaths = Array.isArray(paths) ? gitProjectCommitAdapterSelectedOutput(workbench, paths) : gitProjectCommitSelectedFilesFromWorkbench(workbench);
  const adapterReport = gitProjectCommitSelectionAdapterReport(workbench, selectedPaths);
  const preview = workbench.querySelector("[data-git-commit-selected-preview]");
  if (preview) preview.textContent = gitProjectCommitSelectedPreviewText(adapterReport.selectedPaths);
  workbench.dataset.gitCommitSelectedCount = String(adapterReport.selectedPaths.length);
  if (adapterReport.enabled) {
    workbench.dataset.gitCommitSelectionAdapter = "McelFileBasketModel";
    workbench.dataset.gitCommitAdapterSelectedCount = String(adapterReport.selectedPaths.length);
    workbench.dataset.gitCommitAdapterSelectionMatches = adapterReport.matches ? "true" : "normalized";
    if (adapterReport.summary) {
      workbench.dataset.gitCommitAdapterBlockedSelected = String(adapterReport.summary.selectedBlocked || 0);
      workbench.dataset.gitCommitAdapterInvalidSelected = String((adapterReport.summary.invalidSelectedPaths || []).length);
    }
  } else {
    delete workbench.dataset.gitCommitSelectionAdapter;
    delete workbench.dataset.gitCommitAdapterSelectedCount;
    delete workbench.dataset.gitCommitAdapterSelectionMatches;
    delete workbench.dataset.gitCommitAdapterBlockedSelected;
    delete workbench.dataset.gitCommitAdapterInvalidSelected;
  }
  gitProjectCommitUpdateReviewStatus(workbench, adapterReport.selectedPaths);
  gitProjectCommitUpdateFinalReadiness(workbench, adapterReport.selectedPaths);
}

function gitProjectCommitSizeWunderbaum(element) {
  if (!element) return;
  const applyToViewport = () => {
    const viewportHeight = Number(window.innerHeight || 720);
    const rect = element.getBoundingClientRect?.();
    const top = Number(rect?.top || 0);
    const desiredHeight = Math.max(420, Math.round(viewportHeight - top - 28));
    element.style.setProperty("height", `${desiredHeight}px`, "important");
    element.style.setProperty("min-height", "420px", "important");
    element.style.setProperty("max-height", "none", "important");
    element.style.setProperty("overflow-y", "auto", "important");
    element.style.setProperty("overflow-x", "hidden", "important");
    element.style.setProperty("--wb-row-outer-height", "22px", "important");
    element.style.setProperty("--wb-row-inner-height", "20px", "important");
    element.style.setProperty("background-color", "#010201", "important");
    element.style.setProperty("color", "var(--text)", "important");
    const listContainer = element.querySelector(".wb-list-container");
    const nodeList = element.querySelector(".wb-node-list");
    if (listContainer) {
      listContainer.style.setProperty("min-height", "0", "important");
      listContainer.style.setProperty("max-height", "none", "important");
      listContainer.style.setProperty("overflow", "visible", "important");
      listContainer.style.setProperty("background-color", "#010201", "important");
      listContainer.style.setProperty("color", "var(--text)", "important");
    }
    if (nodeList) {
      nodeList.style.setProperty("min-width", "0", "important");
      nodeList.style.setProperty("width", "100%", "important");
      nodeList.style.setProperty("overflow", "visible", "important");
      nodeList.style.setProperty("background-color", "#010201", "important");
      nodeList.style.setProperty("color", "var(--text)", "important");
    }
  };
  applyToViewport();
  window.requestAnimationFrame(applyToViewport);
  window.setTimeout(applyToViewport, 150);
}

function gitProjectCommitNotifyWunderbaumViewport(tree, change = "resize") {
  if (!tree?.update) return;
  try {
    tree.update(change, {immediate: true});
  } catch (error) {
    console.warn("Commit Wunderbaum viewport update skipped.", error);
  }
}

function gitProjectCommitScrollWunderbaumTop(element) {
  window.requestAnimationFrame(() => {
    if (!element) return;
    element.scrollTop = 0;
    element.dispatchEvent(new Event("scroll", {bubbles: true}));
  });
}

function gitProjectCommitUpdateFallbackParents(scope) {
  if (!scope) return;
  const nodes = Array.from(scope.querySelectorAll("[data-git-commit-tree-node='dir'], [data-git-commit-tree-node='group']")).reverse();
  nodes.forEach((node) => {
    const dirInput = node.querySelector(":scope > label input[data-git-commit-tree-checkbox='dir']");
    if (!dirInput || dirInput.disabled) return;
    const childInputs = Array.from(node.querySelectorAll(":scope > ul input[data-git-commit-tree-checkbox='file'], :scope > ul input[data-git-commit-tree-checkbox='dir']")).filter((input) => !input.disabled);
    if (!childInputs.length) return;
    const checked = childInputs.filter((input) => input.checked).length;
    const mixed = childInputs.some((input) => input.indeterminate);
    dirInput.checked = checked === childInputs.length && !mixed;
    dirInput.indeterminate = (checked > 0 && checked < childInputs.length) || mixed;
  });
}

function gitProjectInitializeCommitFallbackTree(workbench) {
  const fallback = workbench.querySelector("[data-git-commit-tree-fallback]");
  if (!fallback || fallback.dataset.gitCommitFallbackReady === "true") return;
  fallback.dataset.gitCommitFallbackReady = "true";
  fallback.addEventListener("change", (event) => {
    const input = event.target?.closest?.("[data-git-commit-tree-checkbox]");
    if (!input) return;
    if (input.dataset.gitCommitTreeCheckbox === "dir") {
      const node = input.closest("[data-git-commit-tree-node]");
      node?.querySelectorAll("ul input[data-git-commit-tree-checkbox]").forEach((child) => {
        if (!child.disabled) {
          child.checked = input.checked;
          child.indeterminate = false;
        }
      });
    }
    gitProjectCommitUpdateFallbackParents(fallback);
    gitProjectCommitUpdateSelectedPreview(workbench, gitProjectCommitSelectedFilesFromWorkbench(workbench));
  });
  gitProjectCommitUpdateFallbackParents(fallback);
  gitProjectCommitUpdateSelectedPreview(workbench, gitProjectCommitSelectedFilesFromWorkbench(workbench));
}

function gitProjectInitializeCommitWunderbaum(workbench) {
  if (!workbench || workbench.dataset.gitCommitWorkbenchReady === "true") return;
  if (workbench.querySelector?.("[data-git-commit-contract-treegrid]")) return;
  workbench.dataset.gitCommitWorkbenchReady = "true";
  gitProjectInitializeCommitFallbackTree(workbench);
  const element = workbench.querySelector("[data-git-commit-tree]");
  const sourceNode = workbench.querySelector("[data-git-commit-tree-source]");
  if (!element || !sourceNode) return;
  let source = [];
  try {
    source = JSON.parse(sourceNode.value || "[]");
  } catch (error) {
    element.textContent = "Could not parse commit file tree data.";
    element.classList.remove("wb-initializing");
    return;
  }
  gitProjectLoadWunderbaum()
    .then((Wunderbaum) => {
      if (!Wunderbaum || element.dataset.gitWunderbaumReady === "true") return;
      element.dataset.gitWunderbaumReady = "true";
      gitProjectCommitSizeWunderbaum(element);
      const tree = new Wunderbaum({
        id: `git-commit-${Math.random().toString(36).slice(2)}`,
        element,
        checkbox: (event) => event.node?.data?.selectable !== false && event.node?.data?.kind !== "empty",
        selectMode: "hier",
        types: {
          dir: {icon: "bi bi-folder", classes: "git-project-commit-tree-dir"},
          file: {icon: "bi bi-file-earmark-text", classes: "git-project-commit-tree-file"},
          empty: {icon: "bi bi-dash-circle", classes: "git-project-commit-tree-empty", checkbox: false, unselectable: true},
        },
        source: {children: source},
        beforeSelect: (event) => event.node?.data?.selectable !== false && event.node?.data?.kind !== "empty",
        tooltip: (event) => {
          const data = event.node?.data || {};
          return [data.path, data.groupTitle, data.reason, data.meta].filter(Boolean).join(" · ");
        },
        init: (event) => {
          event.tree.gitCommitWorkbench = workbench;
          element._wb_tree = event.tree;
          workbench.gitCommitWunderbaum = event.tree;
          event.tree.root?.fixSelection3FromEndNodes?.();
          gitProjectCommitUpdateSelectedPreview(workbench, gitProjectCommitSelectedFilesFromWunderbaum(event.tree));
          gitProjectCommitSizeWunderbaum(element);
          gitProjectCommitScrollWunderbaumTop(element);
          gitProjectCommitNotifyWunderbaumViewport(event.tree, "resize");
          gitProjectCommitNotifyWunderbaumViewport(event.tree, "scroll");
        },
        select: (event) => {
          event.tree.gitCommitWorkbench = workbench;
          element._wb_tree = event.tree;
          workbench.gitCommitWunderbaum = event.tree;
          gitProjectCommitUpdateSelectedPreview(workbench, gitProjectCommitSelectedFilesFromWunderbaum(event.tree));
        },
      });
      tree.gitCommitWorkbench = workbench;
      element._wb_tree = tree;
      workbench.gitCommitWunderbaum = tree;
      const fallback = workbench.querySelector("[data-git-commit-tree-fallback]");
      if (fallback) fallback.hidden = true;
      gitProjectCommitSizeWunderbaum(element);
      gitProjectCommitUpdateSelectedPreview(workbench, gitProjectCommitSelectedFilesFromWunderbaum(tree));
      gitProjectCommitScrollWunderbaumTop(element);
      gitProjectCommitNotifyWunderbaumViewport(tree, "resize");
      gitProjectCommitNotifyWunderbaumViewport(tree, "scroll");
      element.classList.remove("wb-initializing");
    })
    .catch(() => {
      element.textContent = "Wunderbaum could not be loaded. Using the built-in tri-state fallback selector below.";
      element.classList.remove("wb-initializing");
      workbench.dataset.gitCommitWunderbaumFallback = "true";
    });
}

function gitProjectInitializeCommitWorkbenches(container) {
  container?.querySelectorAll?.("[data-git-commit-workbench]").forEach((workbench) => {
    if (workbench.closest("[hidden]")) return;
    gitProjectWireCommitExecution(workbench);
    gitProjectCommitUpdateSelectedPreview(workbench);
    if (!gitProjectCommitInitializeContractTreegrid(workbench)) {
      gitProjectInitializeCommitWunderbaum(workbench);
    }
  });
}

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    GIT_PROJECT_WUNDERBAUM_VERSION,
    GIT_PROJECT_WUNDERBAUM_ASSETS,
    gitProjectWunderbaumLoadPromise,
    gitProjectCommitFileBasketIntegration,
    gitProjectCommitFileBasketContractView,
    gitProjectCommitFileBasketHooks,
    gitProjectCommitGroups,
    gitProjectCommitGroupConfig,
    gitProjectCommitOpenedCard,
    gitProjectCommitIdentity,
    gitProjectCommitHead,
    gitProjectCommitBranch,
    gitProjectCommitMessage,
    gitProjectCommitIdentitySource,
    gitProjectCommitIdentityScope,
    gitProjectCommitFieldValue,
    gitProjectCommitGateSource,
    gitProjectCommitGateSummary,
    gitProjectCommitReadySummary,
    gitProjectCommitFieldHtml,
    gitProjectCommitHeaderHtml,
    gitProjectCommitConfigStripHtml,
    gitProjectCommitGateSummaryHtml,
    gitProjectCommitRepoIdentityHtml,
    gitProjectCommitComposeHtml,
    gitProjectCommitBasketControlsHtml,
    gitProjectCommitShellQuote,
    gitProjectCommitJoinCommand,
    gitProjectCommitStageCommands,
    gitProjectCommitStageStatsHtml,
    gitProjectCommitStagePreviewHtml,
    gitProjectCommitExecutionPaneHtml,
    gitProjectCommitCreateHtml,
    gitProjectCommitCenterHtml,
    gitProjectCommitNormalizeStatus,
    gitProjectCommitStatusDisplay,
    gitProjectCommitTreeStats,
    gitProjectCommitFileMeta,
    gitProjectCommitCreateTreeNode,
    gitProjectCommitCandidateItems,
    gitProjectCommitFileBasketAdapter,
    gitProjectCommitFileBasketModel,
    gitProjectCommitFileBasketModelJson,
    gitProjectCommitTreeFileTitleFromModel,
    gitProjectCommitTreeNodeFromModelNode,
    gitProjectCommitTreeSourceFromModel,
    gitProjectCommitSortTreeNodes,
    gitProjectCommitAnnotateDirectoryStats,
    gitProjectCommitFinalizeDirectorySelection,
    gitProjectCommitInsertTreePath,
    gitProjectCommitEmptyTreeSource,
    gitProjectCommitTreeSource,
    gitProjectCommitReviewCandidatePaths,
    gitProjectCommitStepFromInspection,
    gitProjectCommitReviewFromInspection,
    gitProjectCommitFallbackTreeHtml,
    gitProjectCommitBasketHtml,
    gitProjectWunderbaumConstructor,
    gitProjectEnsureCommitTreeStylesheet,
    gitProjectLoadWunderbaum,
    gitProjectCommitReadTreeSource,
    gitProjectCommitReadFileBasketModel,
    gitProjectCommitSortSelectedPaths,
    gitProjectCommitAdapterSelectedOutput,
    gitProjectCommitSelectionAdapterReport,
    gitProjectCommitFlattenTreeFiles,
    gitProjectCommitBuildFileIndex,
    gitProjectCommitCleanPathCandidate,
    gitProjectCommitCanonicalFilePath,
    gitProjectCommitTreeNodePath,
    gitProjectCommitTreeNodeSelected,
    gitProjectCommitVisitTreeNodes,
    gitProjectCommitSelectedFilesFromFallback,
    gitProjectCommitSelectedFilesFromWunderbaum,
    gitProjectCommitSelectedFilesFromDom,
    gitProjectCommitSelectedFilesFromWorkbench,
    gitProjectCommitInitializeContractTreegrid,
    gitProjectCommitReviewStats,
    gitProjectCommitControlChecked,
    gitProjectCommitSummaryValue,
    gitProjectCommitMessageNodeValue,
    gitProjectCommitSetMessageNodeValue,
    gitProjectCommitMessageFromExecutionPane,
    gitProjectCommitMessageFromWorkbench,
    gitProjectCommitBranchFromWorkbench,
    gitProjectCommitIdentityFromWorkbench,
    gitProjectCommitSelectedReadiness,
    gitProjectCommitSelectedPreviewText,
    gitProjectCommitDeveloperCommandPreview,
    gitProjectCommitUpdateReviewStatus,
    gitProjectCommitUpdateFinalReadiness,
    gitProjectCommitRenderExecutionFiles,
    gitProjectCommitExecutionTargetText,
    gitProjectCommitUpdateExecutionPane,
    gitProjectCommitRefreshExecutionRemainingFiles,
    gitProjectCommitReinitializeBasketTree,
    gitProjectCommitRefreshWorkbenchFromReview,
    gitProjectCommitEventCreatedRealCommit,
    gitProjectCommitRefreshAfterCompletion,
    gitProjectCommitOpenExecutionPane,
    gitProjectCommitCloseExecutionPane,
    gitProjectCommitSetExecutionStatus,
    gitProjectCommitAppendExecutionLine,
    gitProjectCommitSetExecutionRunning,
    gitProjectCommitStopExecution,
    gitProjectCommitRepoFromWorkbench,
    gitProjectCommitExecutionPayload,
    gitProjectCommitFormatGitState,
    gitProjectCommitAppendExecutionEvent,
    gitProjectCommitStartEventStream,
    gitProjectCommitRunExecution,
    gitProjectWireCommitExecution,
    gitProjectCommitUpdateSelectedPreview,
    gitProjectCommitSizeWunderbaum,
    gitProjectCommitNotifyWunderbaumViewport,
    gitProjectCommitScrollWunderbaumTop,
    gitProjectCommitUpdateFallbackParents,
    gitProjectInitializeCommitFallbackTree,
    gitProjectInitializeCommitWunderbaum,
    gitProjectInitializeCommitWorkbenches
  });

  global.GitToolsCommitWorkbench = api;
  Object.assign(global, {
    GIT_PROJECT_WUNDERBAUM_VERSION,
    GIT_PROJECT_WUNDERBAUM_ASSETS,
    gitProjectWunderbaumLoadPromise,
    gitProjectCommitFileBasketIntegration,
    gitProjectCommitFileBasketContractView,
    gitProjectCommitFileBasketHooks,
    gitProjectCommitGroups,
    gitProjectCommitGroupConfig,
    gitProjectCommitOpenedCard,
    gitProjectCommitIdentity,
    gitProjectCommitHead,
    gitProjectCommitBranch,
    gitProjectCommitMessage,
    gitProjectCommitIdentitySource,
    gitProjectCommitIdentityScope,
    gitProjectCommitFieldValue,
    gitProjectCommitGateSource,
    gitProjectCommitGateSummary,
    gitProjectCommitReadySummary,
    gitProjectCommitFieldHtml,
    gitProjectCommitHeaderHtml,
    gitProjectCommitConfigStripHtml,
    gitProjectCommitGateSummaryHtml,
    gitProjectCommitRepoIdentityHtml,
    gitProjectCommitComposeHtml,
    gitProjectCommitBasketControlsHtml,
    gitProjectCommitShellQuote,
    gitProjectCommitJoinCommand,
    gitProjectCommitStageCommands,
    gitProjectCommitStageStatsHtml,
    gitProjectCommitStagePreviewHtml,
    gitProjectCommitExecutionPaneHtml,
    gitProjectCommitCreateHtml,
    gitProjectCommitCenterHtml,
    gitProjectCommitNormalizeStatus,
    gitProjectCommitStatusDisplay,
    gitProjectCommitTreeStats,
    gitProjectCommitFileMeta,
    gitProjectCommitCreateTreeNode,
    gitProjectCommitCandidateItems,
    gitProjectCommitFileBasketAdapter,
    gitProjectCommitFileBasketModel,
    gitProjectCommitFileBasketModelJson,
    gitProjectCommitTreeFileTitleFromModel,
    gitProjectCommitTreeNodeFromModelNode,
    gitProjectCommitTreeSourceFromModel,
    gitProjectCommitSortTreeNodes,
    gitProjectCommitAnnotateDirectoryStats,
    gitProjectCommitFinalizeDirectorySelection,
    gitProjectCommitInsertTreePath,
    gitProjectCommitEmptyTreeSource,
    gitProjectCommitTreeSource,
    gitProjectCommitReviewCandidatePaths,
    gitProjectCommitStepFromInspection,
    gitProjectCommitReviewFromInspection,
    gitProjectCommitFallbackTreeHtml,
    gitProjectCommitBasketHtml,
    gitProjectWunderbaumConstructor,
    gitProjectEnsureCommitTreeStylesheet,
    gitProjectLoadWunderbaum,
    gitProjectCommitReadTreeSource,
    gitProjectCommitReadFileBasketModel,
    gitProjectCommitSortSelectedPaths,
    gitProjectCommitAdapterSelectedOutput,
    gitProjectCommitSelectionAdapterReport,
    gitProjectCommitFlattenTreeFiles,
    gitProjectCommitBuildFileIndex,
    gitProjectCommitCleanPathCandidate,
    gitProjectCommitCanonicalFilePath,
    gitProjectCommitTreeNodePath,
    gitProjectCommitTreeNodeSelected,
    gitProjectCommitVisitTreeNodes,
    gitProjectCommitSelectedFilesFromFallback,
    gitProjectCommitSelectedFilesFromWunderbaum,
    gitProjectCommitSelectedFilesFromDom,
    gitProjectCommitSelectedFilesFromWorkbench,
    gitProjectCommitInitializeContractTreegrid,
    gitProjectCommitReviewStats,
    gitProjectCommitControlChecked,
    gitProjectCommitSummaryValue,
    gitProjectCommitMessageNodeValue,
    gitProjectCommitSetMessageNodeValue,
    gitProjectCommitMessageFromExecutionPane,
    gitProjectCommitMessageFromWorkbench,
    gitProjectCommitBranchFromWorkbench,
    gitProjectCommitIdentityFromWorkbench,
    gitProjectCommitSelectedReadiness,
    gitProjectCommitSelectedPreviewText,
    gitProjectCommitDeveloperCommandPreview,
    gitProjectCommitUpdateReviewStatus,
    gitProjectCommitUpdateFinalReadiness,
    gitProjectCommitRenderExecutionFiles,
    gitProjectCommitExecutionTargetText,
    gitProjectCommitUpdateExecutionPane,
    gitProjectCommitRefreshExecutionRemainingFiles,
    gitProjectCommitReinitializeBasketTree,
    gitProjectCommitRefreshWorkbenchFromReview,
    gitProjectCommitEventCreatedRealCommit,
    gitProjectCommitRefreshAfterCompletion,
    gitProjectCommitOpenExecutionPane,
    gitProjectCommitCloseExecutionPane,
    gitProjectCommitSetExecutionStatus,
    gitProjectCommitAppendExecutionLine,
    gitProjectCommitSetExecutionRunning,
    gitProjectCommitStopExecution,
    gitProjectCommitRepoFromWorkbench,
    gitProjectCommitExecutionPayload,
    gitProjectCommitFormatGitState,
    gitProjectCommitAppendExecutionEvent,
    gitProjectCommitStartEventStream,
    gitProjectCommitRunExecution,
    gitProjectWireCommitExecution,
    gitProjectCommitUpdateSelectedPreview,
    gitProjectCommitSizeWunderbaum,
    gitProjectCommitNotifyWunderbaumViewport,
    gitProjectCommitScrollWunderbaumTop,
    gitProjectCommitUpdateFallbackParents,
    gitProjectInitializeCommitFallbackTree,
    gitProjectInitializeCommitWunderbaum,
    gitProjectInitializeCommitWorkbenches
  });
})(window);
