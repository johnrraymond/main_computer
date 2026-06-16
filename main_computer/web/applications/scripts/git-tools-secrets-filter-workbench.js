(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.secrets-filter-workbench";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-secrets-filter-workbench.js";

function gitProjectSecretsFilterRuleRowsHtml(rules = [], options = {}) {
  const list = Array.isArray(rules) ? rules.filter((rule) => rule && rule.id) : [];
  const interactive = options.interactive !== false;
  const namePrefix = options.namePrefix || (interactive ? "draft" : "saved");
  const emptyText = options.emptyText || "No security/privacy rules were returned by the backend.";
  if (!list.length) {
    return `<div class="git-project-secrets-empty">${escapeHtml(emptyText)}</div>`;
  }
  return `<div class="git-project-secrets-rule-list ${interactive ? "is-editable" : "is-readonly"}">
    ${list.map((rule = {}) => {
      const enabled = rule.enabled ? "checked" : "";
      const disabled = interactive ? "" : "disabled";
      const availability = rule.availability_status || (rule.available === false ? "unavailable" : "available");
      const scanState = rule.scan_state || (rule.ran ? "ran" : "pending");
      const findingCount = Number(rule.finding_count || 0);
      const rowClass = [
        "git-project-secrets-rule",
        interactive ? "is-editable" : "is-readonly",
        rule.enabled ? "is-enabled" : "is-disabled",
        rule.available === false ? "is-unavailable" : "is-available",
      ].join(" ");
      const meta = [
        `source: ${rule.source || "default"}`,
        `engine: ${rule.engine || "builtin"}`,
        `severity: ${rule.severity || "review"}`,
        availability,
        scanState,
        `findings: ${findingCount}`,
      ].filter(Boolean).join(" · ");
      return `<label class="${escapeHtml(rowClass)}">
        <input type="checkbox" name="${escapeHtml(namePrefix)}-${escapeHtml(rule.id)}" data-git-security-rule="${escapeHtml(rule.id)}" ${enabled} ${disabled}>
        <span>
          <strong>${escapeHtml(rule.id)}</strong>
          <small>${escapeHtml(meta)}</small>
          ${rule.description ? `<em>${escapeHtml(rule.description)}</em>` : ""}
          ${rule.install_hint ? `<em class="git-project-secrets-install-hint">${escapeHtml(rule.install_hint)}</em>` : ""}
        </span>
      </label>`;
    }).join("")}
  </div>`;
}
function gitProjectSecretsFindingsByRuleHtml(findings = [], rules = [], groupedFindings = []) {
  const explicitGroups = Array.isArray(groupedFindings) ? groupedFindings.filter((group) => group && group.rule_id) : [];
  const list = Array.isArray(findings) ? findings : [];
  const labels = new Map((Array.isArray(rules) ? rules : []).map((rule) => [String(rule.id || ""), rule.label || rule.id || "rule"]));
  let groups = explicitGroups.map((group = {}) => [String(group.rule_id || "unknown"), Array.isArray(group.findings) ? group.findings : []]);
  if (!groups.length && list.length) {
    const grouped = new Map();
    list.forEach((finding = {}) => {
      const ruleId = String(finding.rule_id || "unknown");
      if (!grouped.has(ruleId)) grouped.set(ruleId, []);
      grouped.get(ruleId).push(finding);
    });
    groups = Array.from(grouped.entries());
  }
  if (!groups.length) {
    return `<div class="git-project-secrets-empty">No findings have been returned yet. The backend filter check has not populated grouped findings.</div>`;
  }
  return `<div class="git-project-secrets-finding-groups">
    ${groups.map(([ruleId, group]) => `<section class="git-project-secrets-finding-group">
      <div class="git-project-subscreen-panel-head">
        <strong>${escapeHtml(ruleId)}</strong>
        <span>${group.length} finding${group.length === 1 ? "" : "s"}</span>
      </div>
      <p>${escapeHtml(labels.get(ruleId) || ruleId)}</p>
      <ul>
        ${group.slice(0, 30).map((finding = {}) => `<li>
          <code>${escapeHtml(finding.path || finding.file || "")}${finding.line ? `:${escapeHtml(finding.line)}` : ""}</code>
          <span>${escapeHtml([finding.severity || "", finding.kind || "", finding.evidence || finding.evidence_redacted || finding.message || ""].filter(Boolean).join(" · "))}</span>
        </li>`).join("")}
      </ul>
    </section>`).join("")}
  </div>`;
}
function gitProjectSecretsFilterContextJson(step = {}, model = {}) {
  const runtime = step.runtime || gitProjectRuntimeContext();
  const actionKey = gitProjectActionKey(step, "secrets_filter");
  const savedPolicyExists = Boolean(model.saved_policy_exists || model.policy?.exists);
  return JSON.stringify({
    step: {
      id: step.id || "secrets_filter",
      order: Number(step.order || 0),
      label: step.label || "Secrets / Filter",
      why: step.why || "",
      runtime,
    },
    panelState: gitProjectPanelStateForStep({...step, runtime}, actionKey),
    candidatePaths: Array.isArray(model.scan?.candidate_paths) ? model.scan.candidate_paths : [],
    secrets_filter: {
      policy: model.policy || {},
      policy_path: model.policy_path || ".git_dirty_rules.json",
      saved_policy_exists: savedPolicyExists,
      rules: Array.isArray(model.rules) ? model.rules : [],
      saved_rules: savedPolicyExists && Array.isArray(model.saved_rules) ? model.saved_rules : [],
      summary: model.summary || {},
      saved_summary: savedPolicyExists ? (model.saved_summary || {}) : {},
    },
  });
}
function gitProjectSecretsScanResultHtml(scanResult = {}, rules = []) {
  const findings = Array.isArray(scanResult.findings) ? scanResult.findings : [];
  const grouped = Array.isArray(scanResult.findings_by_rule) ? scanResult.findings_by_rule : [];
  const summary = scanResult.summary || {};
  const mode = scanResult.mode || "pending";
  const modeLabel = scanResult.label || (
    mode === "draft_selected_rules"
      ? "Draft selected-rule scan"
      : mode === "full_saved_policy"
        ? "Full saved policy scan"
        : mode === "policy_saved"
          ? "Policy saved; scan not run"
          : "No filter scan has run yet"
  );
  const sourceNote = mode === "draft_selected_rules"
    ? "Uses the current left-side checkbox choices. Not saved. Does not unblock commit."
    : mode === "full_saved_policy"
      ? "Uses .git_dirty_rules.json after backend merge. This is the commit gate result."
      : mode === "policy_saved"
        ? "The right-side saved policy was updated. Run the full saved filter check before commit."
        : "Review rule choices, then run a draft selected-rule scan or a full saved policy check.";
  return `<section class="git-project-secrets-results-panel">
    <div class="git-project-subscreen-panel-head">
      <strong>Scan results</strong>
      <span>${escapeHtml(modeLabel)} · gate ${escapeHtml(scanResult.gate_status || "pending")}</span>
    </div>
    <div class="git-project-secrets-result-banner mode-${escapeHtml(mode)}">
      <strong>${escapeHtml(modeLabel)}</strong>
      <span>${escapeHtml(sourceNote)}</span>
    </div>
    <div class="git-project-secrets-status-grid">
      ${renderKeyValue("Result source", modeLabel)}
      ${renderKeyValue("Status", scanResult.status || "pending")}
      ${renderKeyValue("Gate", scanResult.gate_status || "pending")}
      ${renderKeyValue("Findings", Number(summary.finding_count || findings.length || 0))}
      ${renderKeyValue("Blocking findings", Number(summary.blocking_finding_count || 0))}
      ${renderKeyValue("Candidate files", Number(summary.candidate_file_count || (Array.isArray(scanResult.candidate_paths) ? scanResult.candidate_paths.length : 0)))}
      ${renderKeyValue("Scanned files", Number(summary.scanned_file_count || 0))}
      ${renderKeyValue("detect-secrets", summary.detect_secrets_status || scanResult.detect_secrets?.status || "pending")}
    </div>
    ${scanResult.pending_message ? `<p class="git-project-muted">${escapeHtml(scanResult.pending_message)}</p>` : ""}
    ${scanResult.note ? `<p class="git-project-muted">${escapeHtml(scanResult.note)}</p>` : ""}
    <h4>Live scan events</h4>
    <div class="git-project-secrets-live-events" data-git-secrets-live-events>
      ${scanResult.status === "running" ? `<div>Scan subprocess started. Waiting for backend events…</div>` : ""}
    </div>
    <h4>Findings grouped by rule</h4>
    <div data-git-secrets-live-findings>
      ${gitProjectSecretsFindingsByRuleHtml(findings, rules, grouped)}
    </div>
  </section>`;
}
function gitProjectSecretsFilterWorkbenchHtml(step = {}) {
  const model = step.secrets_filter || {};
  const summary = model.summary || {};
  const savedPolicyExists = Boolean(model.saved_policy_exists || model.policy?.exists);
  const savedSummary = savedPolicyExists ? (model.saved_summary || {}) : {};
  const scan = model.scan || {};
  const scanResult = model.scan_result || scan || {};
  const rules = Array.isArray(model.rules) ? model.rules : [];
  const savedRules = savedPolicyExists && Array.isArray(model.saved_rules) ? model.saved_rules : [];
  const detectSecrets = savedRules.find((rule = {}) => rule.id === "detect_secrets") || {};
  const title = model.title || "SECRETS / FILTER";
  const contextJson = gitProjectSecretsFilterContextJson(step, model);
  return `<div class="git-project-secrets-filter-workbench" data-git-secrets-filter-workbench>
    <textarea hidden data-git-secrets-filter-model>${escapeHtml(contextJson)}</textarea>
    <section class="git-project-secrets-rules-panel">
      <div class="git-project-secrets-card-title">
        <strong>${escapeHtml(title)}</strong>
        <span>Draft rules: ${Number(summary.available_rule_count || 0)} available · ${Number(summary.enabled_rule_count || 0)} enabled · Saved rules: ${Number(savedSummary.available_rule_count || 0)} available · ${Number(savedSummary.enabled_rule_count || 0)} enabled</span>
      </div>
      <div class="git-project-subscreen-panel-head">
        <strong>Draft rule switches</strong>
        <span>${Number(summary.available_rule_count || 0)} available · ${Number(summary.enabled_rule_count || 0)} enabled</span>
      </div>
      <p class="git-project-muted">Edit the current checkbox choices here. These switches are a draft until you merge them into the backend policy.</p>
      <div class="git-project-secrets-policy-strip">
        <span>Draft source</span>
        <code>${model.draft_policy ? "current left-side choices" : "backend merged defaults"}</code>
        <span>${model.policy?.exists ? "saved policy loaded" : "default merge"}</span>
      </div>
      ${gitProjectSecretsFilterRuleRowsHtml(rules, {interactive: true, namePrefix: "draft"})}
      <div class="git-project-secrets-actions">
        <button type="button" data-git-secrets-action="merge_rule_choices">Merge rule choices</button>
        <button type="button" data-git-secrets-action="run_selected_rules">Run selected rules only</button>
      </div>
    </section>
    <section class="git-project-secrets-status-panel">
      <div class="git-project-subscreen-panel-head">
        <strong>Saved merged policy</strong>
        <span>${Number(savedSummary.available_rule_count || 0)} available · ${Number(savedSummary.enabled_rule_count || 0)} enabled</span>
      </div>
      <p class="git-project-muted">This right-side rule set exists only after .git_dirty_rules.json has been saved and merged with the backend catalog. Editing a saved switch writes .git_dirty_rules.json immediately; what is shown here is the saved file state. The full saved check uses this set.</p>
      <div class="git-project-secrets-policy-strip">
        <span>Policy</span>
        <code>${escapeHtml(model.policy_path || ".git_dirty_rules.json")}</code>
        <span>${savedPolicyExists ? "saved" : "not saved yet"}</span>
      </div>
      ${savedPolicyExists
        ? gitProjectSecretsFilterRuleRowsHtml(savedRules, {interactive: true, namePrefix: "saved", emptyText: "No saved merged rules were returned by the backend."})
        : `<div class="git-project-secrets-empty">No saved policy yet. Merge rule choices to create .git_dirty_rules.json before running the full saved filter check.</div>`}
      ${savedPolicyExists ? `<div class="git-project-secrets-detect-status ${detectSecrets.available === false ? "is-unavailable" : "is-available"}">
        <strong>detect-secrets</strong>
        <span>${escapeHtml(detectSecrets.availability_status || (detectSecrets.available === false ? "unavailable" : "available"))}</span>
        <p>${escapeHtml(detectSecrets.install_hint || "detect-secrets is represented as its own policy rule row.")}</p>
      </div>` : ""}
      <div class="git-project-secrets-actions">
        <button type="button" data-git-secrets-action="run_saved_filter_check" ${savedPolicyExists ? "" : "disabled"} title="${savedPolicyExists ? "" : "Merge rule choices before running the full saved filter check."}">Run full saved filter check</button>
      </div>
    </section>
    ${gitProjectSecretsScanResultHtml(scanResult, scanResult.rules || savedRules || [])}
  </div>`;
}
function gitProjectParseSecretsFilterContext(workbench) {
  const node = workbench?.querySelector("[data-git-secrets-filter-model]");
  if (!node) return {};
  try {
    return JSON.parse(node.value || node.textContent || "{}");
  } catch (_error) {
    return {};
  }
}
function gitProjectCollectSecretsRuleChoices(workbench, source = "draft") {
  const choices = {};
  const panelSelector = source === "saved"
    ? ".git-project-secrets-status-panel [data-git-security-rule]"
    : ".git-project-secrets-rules-panel [data-git-security-rule]";
  workbench?.querySelectorAll(panelSelector).forEach((input) => {
    const ruleId = input.dataset.gitSecurityRule || "";
    if (ruleId) choices[ruleId] = Boolean(input.checked);
  });
  return choices;
}
function gitProjectRefreshSecretsRuleVisualState(input) {
  const row = input?.closest(".git-project-secrets-rule");
  if (!row) return;
  row.classList.toggle("is-enabled", Boolean(input.checked));
  row.classList.toggle("is-disabled", !input.checked);
}
function gitProjectBindSecretsRuleVisualState(workbench) {
  if (!workbench) return;
  workbench.querySelectorAll("[data-git-security-rule]").forEach((input) => {
    gitProjectRefreshSecretsRuleVisualState(input);
    if (input.dataset.gitSecretsRuleVisualBound === "true") return;
    input.dataset.gitSecretsRuleVisualBound = "true";
    input.addEventListener("change", () => {
      gitProjectRefreshSecretsRuleVisualState(input);
    });
  });
}
function gitProjectReplaceSecretsFilterWorkbench(workbench, step, secretsFilter, data = {}) {
  if (!workbench || !secretsFilter) return null;
  const replacementStep = {
    ...step,
    runtime: step.runtime || {repo: data.repo || secretsFilter.repo || "."},
    secrets_filter: secretsFilter,
  };
  const parent = workbench.parentElement;
  workbench.outerHTML = gitProjectSecretsFilterWorkbenchHtml(replacementStep);
  const fresh = parent?.querySelector("[data-git-secrets-filter-workbench]") || document.querySelector("[data-git-secrets-filter-workbench]");
  gitProjectBindSecretsFilterActions(fresh?.closest("[data-git-project-card-subscreen]") || fresh);
  return fresh;
}
function gitProjectSecretsActionLabel(action = "") {
  if (action === "merge_rule_choices") return "Merge rule choices";
  if (action === "save_rule_choices") return "Merge rule choices";
  if (action === "update_saved_rule_choices") return "Save saved rule choice";
  if (action === "run_selected_rules") return "Run selected rules only";
  if (action === "run_saved_filter_check") return "Run full saved filter check";
  return "Secrets / Filter action";
}
function gitProjectAppendSecretsLiveEvent(workbench, event = {}) {
  const log = workbench?.querySelector("[data-git-secrets-live-events]");
  if (!log) return;
  const message = event.type === "finding"
    ? `finding · ${event.finding?.rule_id || "unknown"} · ${event.finding?.path || ""}${event.finding?.line ? `:${event.finding.line}` : ""}`
    : event.type === "file_scanned"
      ? `file · ${event.index || "?"}/${event.total || "?"} · ${event.path || ""} · ${event.status || "scanned"} · findings ${event.finding_count || 0}`
      : event.type === "rule_status"
        ? `rule · ${event.rule_id || ""} · ${event.status || ""}`
        : event.type === "finished"
          ? `finished · gate ${event.gate_status || "pending"} · findings ${event.finding_count || 0}`
          : event.message || event.label || event.status || event.type || "event";
  const row = document.createElement("div");
  row.textContent = message;
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
}
function gitProjectAppendSecretsFinding(workbench, finding = {}) {
  const target = workbench?.querySelector("[data-git-secrets-live-findings]");
  if (!target) return;
  const ruleId = finding.rule_id || "unknown";
  const existing = Array.from(target.querySelectorAll("[data-git-secrets-live-rule]")).find((section) => section.dataset.gitSecretsLiveRule === ruleId);
  let list = existing?.querySelector("ul");
  if (!list) {
    if (target.querySelector(".git-project-secrets-empty")) target.innerHTML = "";
    const section = document.createElement("section");
    section.className = "git-project-secrets-finding-group";
    section.dataset.gitSecretsLiveRule = ruleId;
    section.innerHTML = `<div class="git-project-subscreen-panel-head"><strong>${escapeHtml(ruleId)}</strong><span>live findings</span></div><ul></ul>`;
    target.appendChild(section);
    list = section.querySelector("ul");
  }
  const item = document.createElement("li");
  const evidence = finding.evidence || finding.evidence_redacted || finding.message || "";
  item.innerHTML = `<code>${escapeHtml(finding.path || "")}${finding.line ? `:${escapeHtml(finding.line)}` : ""}</code> <span>${escapeHtml([finding.severity || "", finding.kind || "", evidence].filter(Boolean).join(" · "))}</span>`;
  list.appendChild(item);
}
function gitProjectApplySecretsScanEvent(workbench, event = {}) {
  if (!workbench) return;
  gitProjectAppendSecretsLiveEvent(workbench, event);
  if (event.type === "finding" && event.finding) {
    gitProjectAppendSecretsFinding(workbench, event.finding);
  }
  if (event.type === "started" || event.type === "queued") {
    const banner = workbench.querySelector(".git-project-secrets-result-banner");
    if (banner) {
      const strong = banner.querySelector("strong");
      const span = banner.querySelector("span");
      if (strong) strong.textContent = event.label || "Secrets / Filter scan running";
      if (span) span.textContent = `${event.type === "queued" ? "Queued" : "Running"} backend subprocess scan. Results stream here as they are produced.`;
    }
  }
  if ((event.type === "finished" || event.type === "error") && event.scan_result) {
    const resultPanel = workbench.querySelector(".git-project-secrets-results-panel");
    if (resultPanel) {
      const rules = Array.isArray(event.scan_result.rules) ? event.scan_result.rules : [];
      resultPanel.outerHTML = gitProjectSecretsScanResultHtml(event.scan_result, rules);
    }
    workbench.classList.remove("is-running");
    workbench.querySelectorAll("[data-git-secrets-action]").forEach((item) => {
      item.disabled = false;
    });
  }
}
function gitProjectStartSecretsFilterEventStream(workbench, data = {}) {
  const jobId = data.scan_job_id || "";
  const url = data.stream_url || (jobId ? `/api/applications/git/project/secrets-filter/stream?job_id=${encodeURIComponent(jobId)}` : "");
  if (!url || typeof EventSource === "undefined") {
    gitProjectAppendSecretsLiveEvent(workbench, {type: "error", message: "Browser streaming is not available for this scan."});
    return;
  }
  const stream = new EventSource(url);
  workbench.dataset.gitSecretsScanJobId = jobId;
  stream.addEventListener("scan", (event) => {
    let payload = {};
    try {
      payload = JSON.parse(event.data || "{}");
    } catch (_error) {
      payload = {type: "error", message: event.data || "Invalid scan event."};
    }
    gitProjectApplySecretsScanEvent(workbench, payload);
    if (["finished", "error", "stream_timeout"].includes(payload.type)) {
      stream.close();
      if (payload.type === "stream_timeout") {
        workbench.classList.remove("is-running");
        workbench.querySelectorAll("[data-git-secrets-action]").forEach((item) => {
          item.disabled = false;
        });
      }
      refreshGitStatus().catch(() => null);
    }
  });
  stream.onerror = () => {
    gitProjectAppendSecretsLiveEvent(workbench, {type: "error", message: "Secrets / Filter event stream disconnected."});
    stream.close();
    workbench.classList.remove("is-running");
    workbench.querySelectorAll("[data-git-secrets-action]").forEach((item) => {
      item.disabled = false;
    });
  };
}

async function runGitProjectSecretsFilterSavedChoiceUpdate(input) {
  const workbench = input?.closest("[data-git-secrets-filter-workbench]");
  if (!workbench) return;
  const context = gitProjectParseSecretsFilterContext(workbench);
  const step = context.step || {id: "secrets_filter", order: 0, label: "Secrets / Filter", why: ""};
  const panelState = context.panelState || {};
  const candidatePaths = Array.isArray(context.candidatePaths) ? context.candidatePaths : [];
  const ruleChoices = gitProjectCollectSecretsRuleChoices(workbench, "saved");
  const ruleId = input.dataset.gitSecurityRule || "";
  const previousChecked = input.dataset.gitSecretsLastChecked === "true";
  const action = "update_saved_rule_choices";
  const label = gitProjectSecretsActionLabel(action);
  const actionKey = `secrets_filter:${action}:${Number(step.order || 0)}`;
  const state = {
    ...panelState,
    repo: panelState.repo || step.runtime?.repo || gitRepoDir?.value || ".",
    action_id: "secrets_filter",
    secrets_filter_action: action,
    changed_rule_id: ruleId,
    changed_rule_enabled: Boolean(input.checked),
    rule_choices: ruleChoices,
    candidate_paths: candidatePaths,
    secrets_filter: {
      candidate_paths: candidatePaths,
    },
  };
  workbench.classList.add("is-saving");
  workbench.querySelectorAll(".git-project-secrets-status-panel [data-git-security-rule]").forEach((item) => {
    item.disabled = true;
  });
  try {
    const data = await gitToolsStatusApi().runProjectAction({
      action_key: actionKey,
      label,
      commands: [],
      state,
      repo_dir: state.repo || gitRepoDir?.value || ".",
    });
    showGitConsolePayload(data);
    if (data.secrets_filter) {
      gitProjectReplaceSecretsFilterWorkbench(workbench, step, data.secrets_filter, {
        repo: state.repo || ".",
      });
    } else {
      input.dataset.gitSecretsLastChecked = String(Boolean(input.checked));
      workbench.classList.remove("is-saving");
      workbench.querySelectorAll(".git-project-secrets-status-panel [data-git-security-rule]").forEach((item) => {
        item.disabled = false;
      });
    }
  } catch (error) {
    input.checked = previousChecked;
    gitProjectRefreshSecretsRuleVisualState(input);
    if (gitConsoleOutput) gitConsoleOutput.textContent = gitToolsOperationErrorText(`${label} failed`, error);
    workbench.classList.remove("is-saving");
    workbench.querySelectorAll(".git-project-secrets-status-panel [data-git-security-rule]").forEach((item) => {
      item.disabled = false;
    });
  }
}
function gitProjectBindSecretsSavedPolicyCheckboxes(workbench) {
  if (!workbench) return;
  workbench.querySelectorAll(".git-project-secrets-status-panel [data-git-security-rule]").forEach((input) => {
    input.dataset.gitSecretsLastChecked = String(Boolean(input.checked));
    if (input.dataset.gitSecretsSavedBound === "true") return;
    input.dataset.gitSecretsSavedBound = "true";
    input.addEventListener("change", (event) => {
      event.preventDefault();
      runGitProjectSecretsFilterSavedChoiceUpdate(input).catch((error) => {
        if (gitConsoleOutput) gitConsoleOutput.textContent = gitToolsOperationErrorText("Save saved rule choice failed", error);
      });
    });
  });
}

async function runGitProjectSecretsFilterAction(button) {
  const workbench = button?.closest("[data-git-secrets-filter-workbench]");
  if (!workbench) return;
  const action = button.dataset.gitSecretsAction || "";
  const context = gitProjectParseSecretsFilterContext(workbench);
  const step = context.step || {id: "secrets_filter", order: 0, label: "Secrets / Filter", why: ""};
  const panelState = context.panelState || {};
  const candidatePaths = Array.isArray(context.candidatePaths) ? context.candidatePaths : [];
  const ruleChoices = gitProjectCollectSecretsRuleChoices(workbench, action === "update_saved_rule_choices" ? "saved" : "draft");
  const label = gitProjectSecretsActionLabel(action);
  const actionKey = `secrets_filter:${action}:${Number(step.order || 0)}`;
  const state = {
    ...panelState,
    repo: panelState.repo || step.runtime?.repo || gitRepoDir?.value || ".",
    action_id: "secrets_filter",
    secrets_filter_action: action,
    rule_choices: ruleChoices,
    candidate_paths: candidatePaths,
    secrets_filter: {
      candidate_paths: candidatePaths,
    },
  };
  workbench.classList.add("is-running");
  workbench.querySelectorAll("[data-git-secrets-action]").forEach((item) => {
    item.disabled = true;
  });
  try {
    const data = await gitToolsStatusApi().runProjectAction({
      action_key: actionKey,
      label,
      commands: [],
      state,
      repo_dir: state.repo || gitRepoDir?.value || ".",
    });
    showGitConsolePayload(data);
    if (data.secrets_filter) {
      const fresh = gitProjectReplaceSecretsFilterWorkbench(workbench, step, data.secrets_filter, {
        repo: state.repo || ".",
      });
      if (fresh && data.scan_job_id) {
        gitProjectStartSecretsFilterEventStream(fresh, data);
      }
    }
    if (!data.scan_job_id) {
      await refreshGitStatus().catch(() => null);
    }
  } catch (error) {
    if (gitConsoleOutput) gitConsoleOutput.textContent = gitToolsOperationErrorText(`${label} failed`, error);
    workbench.classList.remove("is-running");
    workbench.querySelectorAll("[data-git-secrets-action]").forEach((item) => {
      item.disabled = false;
    });
  }
}
function gitProjectBindSecretsFilterActions(container) {
  if (!container) return;
  container.querySelectorAll("[data-git-secrets-filter-workbench]").forEach((workbench) => {
    gitProjectBindSecretsRuleVisualState(workbench);
    gitProjectBindSecretsSavedPolicyCheckboxes(workbench);
  });
  container.querySelectorAll("[data-git-secrets-action]").forEach((button) => {
    if (button.dataset.gitSecretsBound === "true") return;
    button.dataset.gitSecretsBound = "true";
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      runGitProjectSecretsFilterAction(button).catch((error) => {
        if (gitConsoleOutput) gitConsoleOutput.textContent = gitToolsOperationErrorText("Secrets / Filter action failed", error);
      });
    });
  });
}

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    gitProjectSecretsFilterRuleRowsHtml,
    gitProjectSecretsFindingsByRuleHtml,
    gitProjectSecretsFilterContextJson,
    gitProjectSecretsScanResultHtml,
    gitProjectSecretsFilterWorkbenchHtml,
    gitProjectParseSecretsFilterContext,
    gitProjectCollectSecretsRuleChoices,
    gitProjectRefreshSecretsRuleVisualState,
    gitProjectBindSecretsRuleVisualState,
    gitProjectReplaceSecretsFilterWorkbench,
    gitProjectSecretsActionLabel,
    gitProjectAppendSecretsLiveEvent,
    gitProjectAppendSecretsFinding,
    gitProjectApplySecretsScanEvent,
    gitProjectStartSecretsFilterEventStream,
    runGitProjectSecretsFilterSavedChoiceUpdate,
    gitProjectBindSecretsSavedPolicyCheckboxes,
    runGitProjectSecretsFilterAction,
    gitProjectBindSecretsFilterActions
  });

  global.GitToolsSecretsFilterWorkbench = api;
  Object.assign(global, {
    gitProjectSecretsFilterRuleRowsHtml,
    gitProjectSecretsFindingsByRuleHtml,
    gitProjectSecretsFilterContextJson,
    gitProjectSecretsScanResultHtml,
    gitProjectSecretsFilterWorkbenchHtml,
    gitProjectParseSecretsFilterContext,
    gitProjectCollectSecretsRuleChoices,
    gitProjectRefreshSecretsRuleVisualState,
    gitProjectBindSecretsRuleVisualState,
    gitProjectReplaceSecretsFilterWorkbench,
    gitProjectSecretsActionLabel,
    gitProjectAppendSecretsLiveEvent,
    gitProjectAppendSecretsFinding,
    gitProjectApplySecretsScanEvent,
    gitProjectStartSecretsFilterEventStream,
    runGitProjectSecretsFilterSavedChoiceUpdate,
    gitProjectBindSecretsSavedPolicyCheckboxes,
    runGitProjectSecretsFilterAction,
    gitProjectBindSecretsFilterActions
  });
})(window);
