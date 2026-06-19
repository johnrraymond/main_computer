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
    callConventions
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
    return '<p class="conductor-help">No documented calling convention found yet; use the command template above with explicit args.</p>';
  }
  const rows = examples.map((item) => {
    const doc = item.line ? `${item.doc}:${item.line}` : item.doc;
    return `<div class="conductor-script-example"><span>${escapeHtml(doc || "docs")}</span><code>${escapeHtml(item.command || "")}</code></div>`;
  }).join("");
  return `<div class="conductor-script-examples">${rows}</div>`;
}

function renderConductorScripts(scripts) {
  const filtered = conductorFilteredScripts(scripts);
  if (conductorScriptSelect) {
    conductorScriptSelect.innerHTML = filtered.slice(0, 300).map((script) => {
      const risk = script.risk ? ` · ${script.risk}` : "";
      const docs = conductorArray(script.call_conventions).length ? " · docs" : "";
      return `<option value="${escapeHtml(script.id || "")}">${escapeHtml(script.id || "")}${escapeHtml(risk)}${escapeHtml(docs)}</option>`;
    }).join("");
  }
  if (!conductorScripts) return;
  if (!filtered.length) {
    conductorScripts.innerHTML = '<div class="conductor-empty">No conductor-runnable scripts match this area/search.</div>';
    return;
  }
  conductorScripts.innerHTML = filtered.slice(0, 80).map((script) => {
    const markers = conductorArray(script.markers).join(", ");
    const command = conductorArray(script.command_template).join(" ");
    const description = script.description ? `<p>${escapeHtml(script.description)}</p>` : "";
    const areas = conductorArray(script.areas).join(", ");
    const docCount = conductorArray(script.call_conventions).length;
    return `<div class="conductor-item conductor-script-item"><strong>${escapeHtml(script.id || "")}</strong><span>${escapeHtml(script.kind || "")} · ${escapeHtml(script.risk || "")} · ${escapeHtml(markers)}</span><span>areas: ${escapeHtml(areas || "developer-tools")} · doc commands: ${escapeHtml(docCount)}</span><code>${escapeHtml(command)}</code>${description}${renderConductorScriptExamples(script)}</div>`;
  }).join("");
}

function conductorSelectedScriptPayload() {
  const scriptId = conductorScriptSelect?.value || "main_computer/log_rotator.py";
  const argsText = conductorScriptArgs?.value || "";
  const timeoutValue = Number(conductorScriptTimeout?.value || 60);
  return {
    script: scriptId,
    args: argsText,
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
  refreshConductor().catch((error) => conductorSetStatus(`Conductor error: ${error.message}`));
}
