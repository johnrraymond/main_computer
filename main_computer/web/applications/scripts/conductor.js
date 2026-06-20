const CONDUCTOR_STATUS_ENDPOINT = "/api/applications/conductor/status";
const CONDUCTOR_ACTION_ENDPOINT = "/api/applications/conductor/action";
const CONDUCTOR_RUN_DUE_ENDPOINT = "/api/applications/conductor/run-due";
let conductorInitialized = false;
let conductorStatusCache = null;

function conductorSetStatus(message) {
  if (conductorStatus) conductorStatus.textContent = message;
}

function conductorPretty(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function conductorPayloadObject() {
  const text = conductorPayload ? conductorPayload.value.trim() : "{}";
  if (!text) return {};
  const payload = JSON.parse(text);
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("payload JSON must be an object");
  }
  return payload;
}

function conductorArray(value) {
  return Array.isArray(value) ? value : [];
}

function renderConductorStatus(status) {
  conductorStatusCache = status;
  const counts = status?.counts || {};
  conductorSetStatus(status?.ok ? "Conductor state loaded." : "Conductor state unavailable.");
  if (conductorCounts) {
    conductorCounts.textContent = `jobs ${counts.jobs || 0} | scheduled ${counts.scheduled || 0} | scripts ${counts.scripts || 0} | DNS ${counts.dns_records || 0} | keys ${counts.generated_keys || 0}`;
  }
  renderConductorJobs(status?.jobs || []);
  renderConductorRecords(status?.dns_records || [], status?.generated_keys || []);
  renderConductorScriptAreas(status?.script_areas || []);
  renderConductorScripts(status?.scripts || []);
}

function renderConductorJobs(jobs) {
  if (!conductorJobs) return;
  if (!jobs.length) {
    conductorJobs.innerHTML = '<div class="conductor-empty">No conductor jobs yet.</div>';
    return;
  }
  conductorJobs.innerHTML = jobs.slice(0, 30).map((job) => {
    const action = String(job.action || "");
    const status = String(job.status || "");
    const runAt = String(job.run_at || job.updated_at || "");
    const error = String(job.error || "");
    return `<div class="conductor-item"><strong>${escapeHtml(action)} · ${escapeHtml(status)}</strong><code>${escapeHtml(job.id || "")}</code><span>${escapeHtml(runAt)}</span>${error ? `<p>${escapeHtml(error)}</p>` : ""}</div>`;
  }).join("");
}

function renderConductorRecords(dnsRecords, keys) {
  if (!conductorRecords) return;
  const rows = [];
  dnsRecords.slice(0, 20).forEach((record) => {
    rows.push(`<div class="conductor-item"><strong>DNS ${escapeHtml(record.record_type || "")} ${escapeHtml(record.fqdn || "")}</strong><code>${escapeHtml(record.record_value || "")}</code><span>ttl ${escapeHtml(record.ttl || "")} · rev ${escapeHtml(record.revision || "")} · ${escapeHtml(record.provider_mode || "")}</span></div>`);
  });
  keys.slice(0, 20).forEach((key) => {
    rows.push(`<div class="conductor-item"><strong>Key ${escapeHtml(key.kind || "")} ${escapeHtml(key.name || "")}</strong><code>${escapeHtml(key.fingerprint || "")}</code><span>${escapeHtml(key.created_at || "")}</span></div>`);
  });
  conductorRecords.innerHTML = rows.length ? rows.join("") : '<div class="conductor-empty">No DNS or key records yet.</div>';
}

function renderConductorScriptAreas(areas) {
  if (!conductorScriptArea) return;
  const previous = conductorScriptArea.value || "all";
  const normalized = conductorArray(areas);
  const options = normalized.length ? normalized : [{id: "all", label: "All", count: 0}];
  conductorScriptArea.innerHTML = options.map((area) => {
    const id = String(area.id || "all");
    const label = String(area.label || id);
    const count = Number(area.count || 0);
    return `<option value="${escapeHtml(id)}">${escapeHtml(label)} (${escapeHtml(count)})</option>`;
  }).join("");
  const values = new Set(options.map((area) => String(area.id || "all")));
  conductorScriptArea.value = values.has(previous) ? previous : "all";
}

function conductorScriptSearchText(script) {
  const callConventions = conductorArray(script.call_conventions).map((item) => `${item.doc || ""} ${item.command || ""}`).join(" ");
  const suggested = conductorArray(script.suggested_invocations).map((item) => `${item.label || ""} ${item.command || ""} ${conductorArray(item.args).join(" ")}`).join(" ");
  const quarantine = script.quarantine ? `${script.quarantine.safety || ""} ${script.quarantine.notes || ""}` : "";
  return [
    script.id,
    script.path,
    script.name,
    script.directory,
    script.kind,
    script.risk,
    script.description,
    script.primary_area,
    conductorArray(script.areas).join(" "),
    conductorArray(script.doc_sources).join(" "),
    conductorArray(script.markers).join(" "),
    callConventions,
    suggested,
    quarantine
  ].join(" ").toLowerCase();
}

function conductorFilteredScripts(scripts) {
  const selectedArea = conductorScriptArea?.value || "all";
  const query = String(conductorScriptSearch?.value || "").trim().toLowerCase();
  return conductorArray(scripts).filter((script) => {
    const areas = conductorArray(script.areas);
    if (selectedArea !== "all" && !areas.includes(selectedArea)) {
      return false;
    }
    if (!query) return true;
    return conductorScriptSearchText(script).includes(query);
  });
}

function renderConductorScriptExamples(script) {
  const examples = conductorArray(script.call_conventions).slice(0, 4);
  if (!examples.length) {
    return '<p class="conductor-script-note">No documented calling convention found yet. Use the command template with explicit args.</p>';
  }
  const rows = examples.map((item) => {
    const doc = item.line ? `${item.doc}:${item.line}` : item.doc;
    return `<li class="conductor-script-example"><span class="conductor-script-doc-source">${escapeHtml(doc || "docs")}</span><code class="conductor-script-doc-command">${escapeHtml(item.command || "")}</code></li>`;
  }).join("");
  return `<ul class="conductor-script-examples" aria-label="Documented calling conventions">${rows}</ul>`;
}

function renderConductorSuggestedInvocations(script) {
  const suggestions = conductorArray(script.suggested_invocations).slice(0, 4);
  if (!suggestions.length) return "";
  const rows = suggestions.map((item) => {
    const label = item.label ? `<span class="conductor-script-doc-source">${escapeHtml(item.label)}</span>` : "";
    const command = item.command || `${conductorArray(script.command_template).join(" ")} ${conductorArray(item.args).join(" ")}`.trim();
    return `<li class="conductor-script-example conductor-quarantine-example">${label}<code class="conductor-script-doc-command">${escapeHtml(command)}</code></li>`;
  }).join("");
  return `<section class="conductor-quarantine-block"><strong>Quarantine-first suggested calls</strong><ul class="conductor-script-examples" aria-label="Quarantine suggested invocations">${rows}</ul></section>`;
}

function renderConductorQuarantineNote(script) {
  const quarantine = script.quarantine;
  if (!quarantine) return "";
  const safety = quarantine.safety ? `<p>${escapeHtml(quarantine.safety)}</p>` : "";
  const notes = quarantine.notes ? `<p>${escapeHtml(quarantine.notes)}</p>` : "";
  return `<section class="conductor-quarantine-note"><strong>Quarantine-safe first pass</strong>${safety}${notes}</section>`;
}

function conductorScriptBadge(value, className = "") {
  const text = String(value || "").trim();
  if (!text) return "";
  return `<span class="conductor-script-badge ${className}">${escapeHtml(text)}</span>`;
}

function conductorScriptId(script) {
  return String(script?.id || "");
}

function renderConductorScriptItem(script, selectedId = "") {
  const scriptId = conductorScriptId(script);
  const markers = conductorArray(script.markers).filter(Boolean);
  const command = conductorArray(script.command_template).join(" ");
  const description = script.description ? `<p class="conductor-script-description">${escapeHtml(script.description)}</p>` : "";
  const areas = conductorArray(script.areas).filter(Boolean);
  const docCount = conductorArray(script.call_conventions).length;
  const badges = [
    conductorScriptBadge(script.kind),
    conductorScriptBadge(script.risk, `conductor-risk-${script.risk || "unknown"}`),
    script.quarantine_safe ? conductorScriptBadge("quarantine first-pass", "conductor-quarantine-badge") : "",
    conductorScriptBadge(`${docCount} doc command${docCount === 1 ? "" : "s"}`),
    ...areas.slice(0, 5).map((area) => conductorScriptBadge(area, "conductor-area-badge")),
    ...markers.slice(0, 4).map((marker) => conductorScriptBadge(marker, "conductor-marker-badge"))
  ].join("");
  const selected = scriptId && scriptId === selectedId;
  const selectedClass = selected ? " conductor-script-selected" : "";
  const selectedAttr = selected ? ' aria-current="true"' : "";
  return `<article class="conductor-item conductor-script-item${selectedClass}"${selectedAttr}><header class="conductor-script-header"><strong class="conductor-script-title">${escapeHtml(scriptId)}</strong></header><div class="conductor-script-badges">${badges}</div><code class="conductor-command-template">${escapeHtml(command)}</code>${description}${renderConductorQuarantineNote(script)}${renderConductorSuggestedInvocations(script)}${renderConductorScriptExamples(script)}</article>`;
}

function renderConductorScripts(scripts) {
  const filtered = conductorFilteredScripts(scripts);
  let selectedId = conductorScriptSelect?.value || "";
  let selectedScript = selectedId ? filtered.find((script) => conductorScriptId(script) === selectedId) : null;
  if (conductorScriptSelect) {
    const optionScripts = selectedScript
      ? [selectedScript, ...filtered.filter((script) => conductorScriptId(script) !== selectedId).slice(0, 299)]
      : filtered.slice(0, 300);
    conductorScriptSelect.innerHTML = optionScripts.map((script) => {
      const scriptId = conductorScriptId(script);
      const risk = script.risk ? ` · ${script.risk}` : "";
      const docs = conductorArray(script.call_conventions).length ? " · docs" : "";
      return `<option value="${escapeHtml(scriptId)}">${escapeHtml(scriptId)}${escapeHtml(risk)}${escapeHtml(docs)}</option>`;
    }).join("");
    if (selectedScript) {
      conductorScriptSelect.value = selectedId;
    } else {
      selectedId = conductorScriptSelect.value || conductorScriptId(filtered[0]);
      selectedScript = selectedId ? filtered.find((script) => conductorScriptId(script) === selectedId) : null;
    }
  }
  if (!conductorScripts) return;
  if (!filtered.length) {
    conductorScripts.innerHTML = '<div class="conductor-empty">No conductor-runnable scripts match this area/search.</div>';
    conductorScripts.scrollTop = 0;
    return;
  }
  const detailScripts = selectedScript ? [selectedScript] : [];
  if (!detailScripts.length) {
    conductorScripts.innerHTML = '<div class="conductor-empty">Choose a script to inspect its catalog details.</div>';
    conductorScripts.scrollTop = 0;
    return;
  }
  conductorScripts.innerHTML = detailScripts.map((script) => renderConductorScriptItem(script, selectedId)).join("");
  conductorScripts.scrollTop = 0;
}

function conductorSelectedScript() {
  const scriptId = conductorScriptSelect?.value || "";
  return conductorArray(conductorStatusCache?.scripts).find((script) => script.id === scriptId) || null;
}

function conductorFirstSuggestedInvocation(script) {
  return conductorArray(script?.suggested_invocations)[0] || null;
}

function conductorSelectedScriptPayload() {
  const scriptId = conductorScriptSelect?.value || "main_computer/log_rotator.py";
  const script = conductorSelectedScript();
  const suggestion = conductorFirstSuggestedInvocation(script);
  const argsText = conductorScriptArgs?.value || "";
  const timeoutValue = Number(conductorScriptTimeout?.value || suggestion?.timeout_s || 60);
  const args = argsText.trim() ? argsText : conductorArray(suggestion?.args);
  return {
    script: scriptId,
    args,
    timeout_s: Number.isFinite(timeoutValue) ? timeoutValue : 60
  };
}

function refreshConductorScriptsFromCache() {
  renderConductorScripts(conductorStatusCache?.scripts || []);
}

async function refreshConductor() {
  conductorSetStatus("Loading conductor state...");
  const response = await fetch(CONDUCTOR_STATUS_ENDPOINT, {headers: {"Accept": "application/json"}});
  const status = await response.json();
  if (!response.ok || status.error) {
    throw new Error(status.error || `status HTTP ${response.status}`);
  }
  renderConductorStatus(status);
  return status;
}

async function submitConductorAction(event) {
  if (event) event.preventDefault();
  try {
    const body = {
      action: conductorAction.value,
      payload: conductorPayloadObject(),
      run_at: conductorRunAt.value || "",
      confirm: Boolean(conductorConfirm.checked),
      note: conductorNote.value || ""
    };
    conductorSetStatus("Submitting conductor action...");
    const response = await fetch(CONDUCTOR_ACTION_ENDPOINT, {
      method: "POST",
      headers: {"Content-Type": "application/json", "Accept": "application/json"},
      body: JSON.stringify(body)
    });
    const result = await response.json();
    conductorResult.textContent = conductorPretty(result);
    if (!response.ok || result.error) {
      throw new Error(result.error || `submit HTTP ${response.status}`);
    }
    renderConductorStatus(result.status || result.worker?.status || conductorStatusCache || {});
    conductorSetStatus(result.scheduled ? "Conductor action scheduled." : "Conductor worker finished.");
  } catch (error) {
    conductorSetStatus(`Conductor error: ${error.message}`);
    if (conductorResult) conductorResult.textContent = String(error.stack || error);
  }
}

async function runDueConductorJobs() {
  try {
    conductorSetStatus("Running due conductor jobs...");
    const response = await fetch(CONDUCTOR_RUN_DUE_ENDPOINT, {
      method: "POST",
      headers: {"Content-Type": "application/json", "Accept": "application/json"},
      body: JSON.stringify({limit: 10})
    });
    const result = await response.json();
    conductorResult.textContent = conductorPretty(result);
    if (!response.ok || result.error) {
      throw new Error(result.error || `run-due HTTP ${response.status}`);
    }
    renderConductorStatus(result.status || {});
    conductorSetStatus(`Ran ${result.ran || 0} due conductor job(s).`);
  } catch (error) {
    conductorSetStatus(`Conductor run-due error: ${error.message}`);
    if (conductorResult) conductorResult.textContent = String(error.stack || error);
  }
}

function fillConductorDnsPayload() {
  conductorAction.value = "dns.record.upsert";
  conductorPayload.value = conductorPretty({
    zone: "example.test",
    record_name: "worker",
    record_type: "A",
    record_value: "127.0.0.1",
    ttl: 300,
    provider_mode: "self-hosted"
  });
}

function fillConductorSecretPayload() {
  conductorAction.value = "local.secret.generate";
  conductorPayload.value = conductorPretty({
    name: "worker-session-signing",
    purpose: "local conductor scheduled worker authority",
    bytes: 32
  });
}

function fillConductorScriptPayload() {
  conductorAction.value = "script.run";
  conductorPayload.value = conductorPretty(conductorSelectedScriptPayload());
}

function fillConductorSslPayload() {
  conductorAction.value = "ssl.key.generate";
  conductorPayload.value = conductorPretty({
    name: "localhost-dev",
    common_name: "localhost",
    days: 30
  });
}

function initConductorApp() {
  if (!conductorApp || conductorInitialized) return;
  conductorInitialized = true;
  conductorRefresh?.addEventListener("click", () => refreshConductor().catch((error) => conductorSetStatus(`Conductor error: ${error.message}`)));
  conductorRunDue?.addEventListener("click", runDueConductorJobs);
  conductorActionForm?.addEventListener("submit", submitConductorAction);
  conductorFillDns?.addEventListener("click", fillConductorDnsPayload);
  conductorFillSecret?.addEventListener("click", fillConductorSecretPayload);
  conductorFillScript?.addEventListener("click", fillConductorScriptPayload);
  conductorFillSelectedScript?.addEventListener("click", fillConductorScriptPayload);
  conductorFillSsl?.addEventListener("click", fillConductorSslPayload);
  conductorScriptArea?.addEventListener("change", refreshConductorScriptsFromCache);
  conductorScriptSearch?.addEventListener("input", refreshConductorScriptsFromCache);
  conductorScriptSelect?.addEventListener("change", refreshConductorScriptsFromCache);
  refreshConductor().catch((error) => conductorSetStatus(`Conductor error: ${error.message}`));
}
