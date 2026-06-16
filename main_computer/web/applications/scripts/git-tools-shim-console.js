(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.shim-console";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-shim-console.js";

function renderGitShimList(data) {
  if (!gitShimList) return;
  const shims = Array.isArray(data?.shims) ? data.shims : [];
  gitShimList.innerHTML = "";
  if (!shims.length) {
    updateGitWorkflowSectionSummary("shim-builder", "no stored shims yet");
    gitShimList.innerHTML = '<div class="git-tools-empty">No git-control shims are stored yet. Use Ask AI / Generate Shim, Plan Shim, Run / Save Shim, or Extract AI Shims.</div>';
    return;
  }
  updateGitWorkflowSectionSummary("shim-builder", `${shims.length} stored shim${shims.length === 1 ? "" : "s"} available`);
  shims.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "git-patch-item git-shim-item";
    const shimId = item.id || "unknown-shim";
    button.classList.toggle("active", shimId === gitToolsSelectedShim);
    const commandText = Array.isArray(item.git_commands) && item.git_commands.length
      ? item.git_commands.join("\\n")
      : (item.title || "documentation-only shim");
    const recommendation = item.ordination_recommendation || item.metadata?.["ordination-recommendation"] || "not-recommended";
    const ordained = item.ordained || item.ordination_state === "ordained";
    button.innerHTML = [
      `<strong>${escapeHtml(shimId)}</strong>`,
      `<small>${escapeHtml(item.kind || "shim")} | ${escapeHtml(item.risk || "unknown")} | ${ordained ? "ordained" : "candidate"} | recommend: ${escapeHtml(recommendation)}</small>`,
      `<small>${escapeHtml(commandText)}</small>`
    ].join("");
    button.addEventListener("click", () => {
      gitToolsSelectedShim = shimId;
      if (gitShimId) gitShimId.value = shimId;
      expandGitWorkflowSection("shim-builder", `selected shim: ${shimId}`);
      viewGitShim();
      renderGitShimList(data);
    });
    gitShimList.append(button);
  });
}
async function refreshGitShims() {
  if (!gitShimList) return;
  try {
    const data = await gitToolsStatusApi().fetchShims();
    renderGitShimList(data);
  } catch (error) {
    gitShimList.innerHTML = `<div class="git-tools-empty">Shim inventory failed: ${escapeHtml(error.message || error)}</div>`;
  }
}
function selectedGitShimId() {
  return (gitShimId?.value || gitToolsSelectedShim || "").trim();
}
function showGitConsolePayload(payload) {
  if (!gitConsoleOutput) return;
  gitConsoleOutput.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
}
async function extractGitConsoleShims() {
  expandGitWorkflowSection("ai-interpretation", "extracting shims from AI output");
  if (!gitConsoleInput || !gitConsoleOutput) return;
  const aiOutput = gitConsoleInput.value || "";
  if (!aiOutput.trim()) {
    gitConsoleOutput.textContent = "Paste AI output containing python git-control.py commands first.";
    return;
  }
  gitConsoleOutput.textContent = "Extracting git-control.py commands into shims...";
  try {
    const data = await gitToolsStatusApi().extractConsoleCommands(aiOutput);
    showGitConsolePayload(data);
    if (Array.isArray(data.shims) && data.shims.length && gitShimId) {
      gitToolsSelectedShim = data.shims[0].id || "";
      gitShimId.value = gitToolsSelectedShim;
    }
    expandGitWorkflowSection("shim-builder", "extracted shims are ready to review");
    await refreshGitShims();
    await refreshGitStatus();
  } catch (error) {
    gitConsoleOutput.textContent = `Shim extraction failed: ${error.message || error}`;
  }
}
async function runGitConsoleCommand() {
  expandGitWorkflowSection("ai-interpretation", "running git-control command");
  if (!gitConsoleInput || !gitConsoleOutput) return;
  const command = gitConsoleInput.value || "";
  if (!command.trim()) {
    gitConsoleOutput.textContent = "Enter a git command or python git-control.py command first.";
    return;
  }
  gitConsoleOutput.textContent = "Running git-control command and saving its shim...";
  try {
    const data = await gitToolsStatusApi().runConsoleCommand({command, repoDir: gitToolsRepoDirValue(".")});
    showGitConsolePayload(data);
    if (data.shim?.id && gitShimId) {
      gitToolsSelectedShim = data.shim.id;
      gitShimId.value = data.shim.id;
    }
    expandGitWorkflowSection("shim-builder", "saved shim is ready to review");
    await refreshGitShims();
    await refreshGitStatus();
  } catch (error) {
    gitConsoleOutput.textContent = `Git console command failed: ${error.message || error}`;
  }
}
async function askGitAiForShim() {
  expandGitWorkflowSection("ai-interpretation", "asking AI for a shim");
  if (!gitConsoleInput || !gitConsoleOutput) return;
  const prompt = gitConsoleInput.value || "";
  gitConsoleOutput.textContent = "Asking AI with ordained git-control shims loaded as context...";
  try {
    const data = await gitToolsStatusApi().createAiShim(prompt);
    showGitConsolePayload(data);
    const firstShim = Array.isArray(data.shims) && data.shims.length ? data.shims[0] : null;
    if (firstShim?.id && gitShimId) {
      gitToolsSelectedShim = firstShim.id;
      gitShimId.value = firstShim.id;
    }
    expandGitWorkflowSection("shim-builder", "AI-generated shim is ready to review");
    await refreshGitShims();
  } catch (error) {
    gitConsoleOutput.textContent = `AI shim generation failed: ${error.message || error}`;
  }
}
async function createGitPlanShim() {
  expandGitWorkflowSection("ai-interpretation", "creating shim-first git plan");
  updateGitWorkflowSectionSummary("shim-builder", "plan shim requested");
  if (gitConsoleOutput) gitConsoleOutput.textContent = "Creating shim-first git plan...";
  try {
    const data = await gitToolsStatusApi().planControl(gitConsoleInput?.value || "");
    showGitConsolePayload(data);
    if (data.plan_shim?.id && gitShimId) {
      gitToolsSelectedShim = data.plan_shim.id;
      gitShimId.value = data.plan_shim.id;
    }
    expandGitWorkflowSection("shim-builder", "plan shim is ready to review");
    await refreshGitShims();
  } catch (error) {
    if (gitConsoleOutput) gitConsoleOutput.textContent = `Plan shim failed: ${error.message || error}`;
  }
}
async function viewGitShim() {
  expandGitWorkflowSection("shim-builder", "loading shim details");
  const shimId = selectedGitShimId();
  if (!shimId) {
    if (gitShimOutput) gitShimOutput.textContent = "Select a shim first.";
    return;
  }
  if (gitShimOutput) gitShimOutput.textContent = "Loading shim...";
  try {
    const data = await gitToolsStatusApi().readShim(shimId);
    gitToolsSelectedShim = shimId;
    if (gitShimId) gitShimId.value = shimId;
    const commands = Array.isArray(data.git_commands) && data.git_commands.length
      ? `\\n\\nExtracted git commands:\\n${data.git_commands.join("\\n")}`
      : "";
    const recommendation = data.ordination_recommendation || data.metadata?.["ordination-recommendation"] || "not-recommended";
    const ordination = data.ordained ? "ordained" : (data.ordination_state || "candidate");
    const summary = `Ordination: ${ordination}\\nRecommendation: ${recommendation}\\nReason: ${data.ordination_reason || data.metadata?.["ordination-reason"] || ""}\\n\\n`;
    gitShimOutput.textContent = `${summary}${data.text || JSON.stringify(data, null, 2)}${commands}`;
  } catch (error) {
    if (gitShimOutput) gitShimOutput.textContent = `Shim read failed: ${error.message || error}`;
  }
}
async function runGitShim() {
  expandGitWorkflowSection("shim-builder", "running stored shim");
  const shimId = selectedGitShimId();
  if (!shimId) {
    if (gitShimOutput) gitShimOutput.textContent = "Select a shim first.";
    return;
  }
  if (gitShimOutput) gitShimOutput.textContent = "Running stored shim...";
  try {
    const data = await gitToolsStatusApi().runShim(shimId);
    gitShimOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitStatus();
    await refreshGitShims();
  } catch (error) {
    if (gitShimOutput) gitShimOutput.textContent = `Shim run failed: ${error.message || error}`;
  }
}
async function setGitShimOrdination(ordained) {
  expandGitWorkflowSection("shim-builder", ordained ? "ordaining selected shim" : "removing shim ordination");
  const shimId = selectedGitShimId();
  if (!shimId) {
    if (gitShimOutput) gitShimOutput.textContent = "Select a shim first.";
    return;
  }
  if (gitShimOutput) gitShimOutput.textContent = ordained ? "Ordaining shim for future AI context..." : "Removing shim from ordained AI context...";
  try {
    const data = await gitToolsStatusApi().setShimOrdination({shimId, ordained});
    gitShimOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitShims();
    await viewGitShim();
  } catch (error) {
    if (gitShimOutput) gitShimOutput.textContent = `Shim ordination update failed: ${error.message || error}`;
  }
}
async function deleteGitShim() {
  expandGitWorkflowSection("shim-builder", "deleting selected shim");
  const shimId = selectedGitShimId();
  if (!shimId) {
    if (gitShimOutput) gitShimOutput.textContent = "Select a shim first.";
    return;
  }
  if (gitShimOutput) gitShimOutput.textContent = "Deleting shim...";
  try {
    const data = await gitToolsStatusApi().deleteShim(shimId);
    gitToolsSelectedShim = "";
    if (gitShimId) gitShimId.value = "";
    gitShimOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitShims();
  } catch (error) {
    if (gitShimOutput) gitShimOutput.textContent = `Shim delete failed: ${error.message || error}`;
  }
}

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    renderGitShimList,
    refreshGitShims,
    selectedGitShimId,
    showGitConsolePayload,
    extractGitConsoleShims,
    runGitConsoleCommand,
    askGitAiForShim,
    createGitPlanShim,
    viewGitShim,
    runGitShim,
    setGitShimOrdination,
    deleteGitShim
  });

  global.GitToolsShimConsole = api;
  Object.assign(global, {
    renderGitShimList,
    refreshGitShims,
    selectedGitShimId,
    showGitConsolePayload,
    extractGitConsoleShims,
    runGitConsoleCommand,
    askGitAiForShim,
    createGitPlanShim,
    viewGitShim,
    runGitShim,
    setGitShimOrdination,
    deleteGitShim
  });
})(window);
