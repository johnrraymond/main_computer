(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.patch-inventory";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-patch-inventory.js";

function renderGitPatchGroups(data) {
  if (!gitPatchList) return;
  updateGitWorkflowSectionSummary("patch-inventory", "loaded; expand to choose patches or dry runs");
  const groups = [
    ["incoming", "Incoming Patches"],
    ["applied", "Applied Patches"],
    ["archive", "Archive"],
    ["dry_runs", "Dry Runs"],
  ];
  gitPatchList.innerHTML = "";
  let rendered = 0;
  groups.forEach(([key, title]) => {
    const items = Array.isArray(data?.[key]) ? data[key] : [];
    if (!items.length) {
      return;
    }
    rendered += 1;
    const section = document.createElement("section");
    section.className = "git-patch-group";
    section.innerHTML = `<h3>${escapeHtml(title)}</h3>`;
    const list = document.createElement("div");
    list.className = "git-patch-group-list";
    items.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "git-patch-item";
      const itemName = item.name || item.relative_path || item.path || "unnamed";
      const active = key === "dry_runs" ? itemName === gitToolsSelectedDryRun : itemName === gitToolsSelectedPatch;
      button.classList.toggle("active", active);
      button.innerHTML = `<strong>${escapeHtml(itemName)}</strong><small>${escapeHtml(item.relative_path || item.path || "")}</small>`;
      button.addEventListener("click", () => {
        if (key === "dry_runs") {
          gitToolsSelectedDryRun = itemName;
          gitDryRunName.value = itemName;
          expandGitWorkflowSection("dry-run", `selected dry-run preview: ${itemName}`);
          loadGitDryRun();
        } else {
          gitToolsSelectedPatch = itemName;
          gitPatchName.value = itemName;
          expandGitWorkflowSection("patch-actions", `selected patch: ${itemName}`);
          previewGitPatch();
        }
      });
      list.append(button);
    });
    section.append(list);
    gitPatchList.append(section);
  });
  if (!rendered) {
    gitPatchList.innerHTML = '<div class="git-tools-empty">No patches or dry-run previews are available yet.</div>';
  }
}

async function refreshGitPatches() {
  if (!gitPatchList) return;
  try {
    const data = await gitToolsStatusApi().fetchPatches();
    renderGitPatchGroups(data);
    if (gitDryRunName && !gitDryRunName.value && Array.isArray(data.dry_runs) && data.dry_runs.length) {
      gitDryRunName.value = data.dry_runs[0].name || "";
    }
  } catch (error) {
    gitPatchList.innerHTML = `<div class="git-tools-empty">Patch inventory failed: ${escapeHtml(error.message || error)}</div>`;
  }
}

async function previewGitPatch() {
  expandGitWorkflowSection("patch-actions", "loading patch preview");
  const patchName = (gitPatchName.value || "").trim();
  if (!patchName) {
    gitPatchPreviewOutput.textContent = "Choose a patch from the inventory first.";
    return;
  }
  gitPatchPreviewOutput.textContent = "Loading patch preview...";
  try {
    const data = await gitToolsStatusApi().readPatch(patchName);
    gitToolsSelectedPatch = patchName;
    gitPatchPreviewOutput.textContent = data.preview || "Patch preview is empty.";
    updateGitWorkflowSectionSummary("patch-actions", `preview loaded: ${patchName}`);
    renderGitPatchGroups(await gitToolsStatusApi().fetchPatches());
  } catch (error) {
    gitPatchPreviewOutput.textContent = `Patch preview failed: ${error.message || error}`;
  }
}

async function loadGitDryRun() {
  expandGitWorkflowSection("dry-run", "loading dry-run preview");
  const runName = (gitDryRunName.value || "").trim();
  if (!runName) {
    gitDryRunOutput.textContent = "Run a dry run or select a stored preview.";
    return;
  }
  gitDryRunOutput.textContent = "Loading dry-run preview...";
  try {
    const data = await gitToolsStatusApi().readDryRun(runName);
    gitToolsSelectedDryRun = runName;
    const previewFiles = Array.isArray(data.preview_files) ? data.preview_files.map((item) => item.relative_path).join("\n") : "";
    const deletions = Array.isArray(data.deletions) ? data.deletions.map((item) => item.relative_path).join("\n") : "";
    gitDryRunOutput.textContent = [
      JSON.stringify(data.manifest || {}, null, 2),
      previewFiles ? `Preview files:\n${previewFiles}` : "",
      deletions ? `Deletion markers:\n${deletions}` : "",
    ].filter(Boolean).join("\n\n");
    updateGitWorkflowSectionSummary("dry-run", `dry-run preview loaded: ${runName}`);
    renderGitPatchGroups(await gitToolsStatusApi().fetchPatches());
  } catch (error) {
    gitDryRunOutput.textContent = `Dry-run preview failed: ${error.message || error}`;
  }
}

async function runGitPatchDryRun() {
  expandGitWorkflowSection("dry-run", "running patch harness dry run");
  const patchName = (gitPatchName.value || "").trim();
  if (!patchName) {
    gitDryRunOutput.textContent = "Choose a patch before running the harness.";
    return;
  }
  gitDryRunOutput.textContent = "Running patch harness dry run...";
  try {
    const data = await gitToolsStatusApi().applyPatchDryRun({
      patch_name: patchName,
      target_root: gitPatchTarget.value || ".",
      dry_run: true,
      reverse: gitPatchReverse.checked,
      strict_root: false
    });
    const result = data.result || {};
    gitDryRunOutput.textContent = JSON.stringify(result, null, 2);
    if (data.dry_run_output_dir) {
      const parts = String(data.dry_run_output_dir).split(/[\\/]+/);
      const runName = parts[parts.length - 1] || "";
      if (runName) {
        gitDryRunName.value = runName;
        await loadGitDryRun();
      }
    }
    await refreshGitStatus();
    await refreshGitPatches();
  } catch (error) {
    gitDryRunOutput.textContent = `Patch dry run failed: ${error.message || error}`;
  }
}

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    renderGitPatchGroups,
    refreshGitPatches,
    previewGitPatch,
    loadGitDryRun,
    runGitPatchDryRun,
  });

  global.GitToolsPatchInventory = api;
  Object.assign(global, {
    renderGitPatchGroups,
    refreshGitPatches,
    previewGitPatch,
    loadGitDryRun,
    runGitPatchDryRun,
  });
})(window);
