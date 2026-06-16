(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.server-panel";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-server-panel.js";

function startGitServerProgress(title, lines = [], phases = []) {
  if (!gitServerOperationState) return {stop: () => null};
  const started = Date.now();
  let tick = 0;
  const render = () => {
    const elapsed = Math.max(0, Math.floor((Date.now() - started) / 1000));
    const phase = phases.length ? phases[Math.min(phases.length - 1, Math.floor(elapsed / 5))] : "";
    const dots = ".".repeat((tick % 3) + 1);
    gitServerOperationState.textContent = [
      `${title} ${dots}`,
      `Elapsed: ${elapsed}s`,
      phase ? `Current phase: ${phase}` : "",
    ].filter(Boolean).join(" — ");
    tick += 1;
  };
  render();
  const interval = window.setInterval(render, 1000);
  return {
    stop: () => window.clearInterval(interval),
  };
}
function gitServerOperationButtons() {
  return [
    gitServerStart,
    gitServerRestart,
    gitServerStop,
    gitServerLogs,
    gitServerUseLocal,
    gitServerRemoteApplyLocal,
    gitServerPushLocal,
    gitServerUseExternal,
    gitServerMirrorPlan,
    gitServerMirrorSetup,
    gitServerRemoteRun,
    gitServerRemoteCopyConsole,
    ...gitServerRemotePresetButtons,
  ].filter(Boolean);
}
function gitServerDockerDependentButtons() {
  return [
    gitServerStart,
    gitServerRestart,
    gitServerStop,
    gitServerLogs,
    gitServerRemoteApplyLocal,
    gitServerPushLocal,
    gitServerMirrorSetup,
  ].filter(Boolean);
}
function gitServerDockerUnavailableText(status = gitServerLastStatus) {
  const composeFile = status?.compose_file || "docker-compose.dev.yml";
  return [
    "Docker CLI is not available in the environment running Main Computer.",
    "",
    "Local Gitea actions need Docker in the backend process: Start, Restart, Stop, Logs, Create / verify repo + configure remote, Push to Local Gitea, and Set Up Server -> External Mirror.",
    "Opening http://localhost:3000 in your browser, or starting Gitea somewhere else, does not give this backend process Docker access.",
    "You can still use Reset suggested target and Run command to configure the local-gitea remote without starting Gitea.",
    "",
    `Compose file seen by backend: ${composeFile}`,
  ].join("\n");
}
function applyGitServerDockerAvailability(status = gitServerLastStatus) {
  gitServerLastStatus = status || gitServerLastStatus;
  if (gitToolsOperationRunning) return;
  const dockerUnavailable = Boolean(gitServerLastStatus && gitServerLastStatus.docker_available === false);
  gitServerDockerDependentButtons().forEach((button) => {
    if (button === gitServerRemoteApplyLocal || button === gitServerPushLocal) return;
    button.disabled = dockerUnavailable;
    button.setAttribute("aria-disabled", dockerUnavailable ? "true" : "false");
    button.title = dockerUnavailable ? "Docker CLI is not available where Main Computer is running." : "";
  });
  gitServerSetLocalActionAvailability({
    configurable: !gitServerTargetPrefunk || gitServerTargetPrefunk.is_git_repo !== false,
    pushable: !gitServerTargetPrefunk || (gitServerTargetPrefunk.is_git_repo !== false && gitServerTargetPrefunk.has_head !== false),
    reason: gitServerTargetPrefunk?.is_git_repo === false
      ? gitServerTargetUnavailableReason(gitServerTargetPrefunk)
      : (gitServerTargetPrefunk?.has_head === false ? "Create an initial commit before pushing to Local Gitea." : ""),
  });
  if (gitServerOperationState && dockerUnavailable) {
    gitServerOperationState.textContent = "Docker unavailable: local Gitea controls disabled.";
  }
}
function ensureGitServerDockerAvailable(actionLabel) {
  if (gitServerLastStatus && gitServerLastStatus.docker_available === false) {
    if (gitServerOutput) gitServerOutput.textContent = `${actionLabel} cannot run.\n\n${gitServerDockerUnavailableText(gitServerLastStatus)}`;
    updateGitWorkflowSectionSummary("git-server", "docker unavailable");
    applyGitServerDockerAvailability(gitServerLastStatus);
    return false;
  }
  return true;
}
function setGitServerOperationRunning(running) {
  gitToolsOperationRunning = Boolean(running);
  gitServerOperationButtons().forEach((button) => {
    button.disabled = gitToolsOperationRunning;
  });
  if (gitServerOperationCancel) gitServerOperationCancel.disabled = !gitToolsOperationRunning;
  if (gitServerOperationState) {
    gitServerOperationState.textContent = gitToolsOperationRunning ? "Command running..." : "No command running.";
  }
  if (!gitToolsOperationRunning) {
    applyGitServerDockerAvailability(gitServerLastStatus);
  }
}
function formatGitOperation(operation) {
  if (!operation) return "No Git operation is currently running.";
  const lines = [
    `${operation.label || operation.kind || "Git operation"} — ${operation.status || "running"}`,
    `id: ${operation.id || "unknown"}`,
    `elapsed: ${operation.elapsed || 0}s`,
  ];
  if (operation.process?.pid) lines.push(`pid: ${operation.process.pid}`);
  if (operation.cancel_requested) lines.push("cancel requested");
  const logs = Array.isArray(operation.logs) ? operation.logs.slice(-12) : [];
  if (logs.length) {
    lines.push("", "Recent logs:");
    logs.forEach((entry) => {
      lines.push(`- +${entry.elapsed || 0}s ${entry.message || ""}`);
      if (entry.data && Object.keys(entry.data).length) {
        lines.push(`  ${JSON.stringify(entry.data)}`);
      }
    });
  }
  if (operation.result) {
    lines.push("", "Result:");
    lines.push(JSON.stringify(operation.result, null, 2));
  }
  return lines.join("\n");
}
function renderGitOperationStatus(data, {renderOutput = false} = {}) {
  const active = data?.active || null;
  setGitServerOperationRunning(Boolean(active));
  if (gitServerOperationState) {
    if (active) {
      gitServerOperationState.textContent = `${active.label || active.kind || "Git operation"}: ${active.status || "running"} (${active.elapsed || 0}s)`;
    } else {
      const last = Array.isArray(data?.history) && data.history.length ? data.history[data.history.length - 1] : null;
      gitServerOperationState.textContent = last ? `Last: ${last.label || last.kind} ${last.status} (${last.elapsed || 0}s)` : "No command running.";
    }
  }
  if (renderOutput && gitServerOutput) {
    gitServerOutput.textContent = active ? formatGitOperation(active) : JSON.stringify(data, null, 2);
  }
}
async function refreshGitOperationStatus(options = {}) {
  const data = await gitToolsStatusApi().fetchOperationStatus();
  renderGitOperationStatus(data, options);
  return data;
}
function startGitOperationPolling({renderOutput = true} = {}) {
  stopGitOperationPolling();
  refreshGitOperationStatus({renderOutput}).catch(() => null);
  gitToolsOperationPollTimer = window.setInterval(() => {
    refreshGitOperationStatus({renderOutput}).catch(() => null);
  }, 1000);
}
function stopGitOperationPolling() {
  if (gitToolsOperationPollTimer) {
    window.clearInterval(gitToolsOperationPollTimer);
    gitToolsOperationPollTimer = null;
  }
}
async function runGitServerOperationRequest(path, payload, initialLines = []) {
  if (gitToolsOperationRunning) {
    if (gitServerOutput) gitServerOutput.textContent = "A Git command is already running. Cancel it or wait for it to finish before starting another.";
    return null;
  }
  setGitServerOperationRunning(true);
  if (gitServerOutput) {
    gitServerOutput.textContent = [
      "Starting Git operation...",
      ...initialLines,
      "",
      "Operation logs will update here.",
    ].filter(Boolean).join("\n");
  }
  startGitOperationPolling({renderOutput: true});
  try {
    const data = await gitToolsRequest(path, payload);
    renderGitOperationStatus({active: null, history: data.operation ? [data.operation] : []}, {renderOutput: false});
    return data;
  } finally {
    stopGitOperationPolling();
    await refreshGitOperationStatus({renderOutput: false}).catch(() => null);
  }
}
async function cancelGitServerOperation() {
  if (!gitToolsOperationRunning) {
    await refreshGitOperationStatus({renderOutput: true}).catch(() => null);
    return;
  }
  if (gitServerOutput) gitServerOutput.textContent = "Cancel requested. Waiting for the running subprocess to stop...";
  try {
    const data = await gitToolsStatusApi().cancelOperation();
    renderGitOperationStatus(data, {renderOutput: true});
    startGitOperationPolling({renderOutput: true});
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = gitToolsOperationErrorText("Cancel failed", error);
  }
}

function gitServerPaneRequested() {
  return Boolean(gitServerPane);
}
function setGitServerPaneVisible(_visible = true, options = {}) {
  if (!gitServerPane) return;
  gitServerPane.hidden = false;
  gitServerPane.open = true;
  updateGitWorkflowSectionSummary("git-server", "visible; shared Gitea controls enabled");
  if (options.persist && window.localStorage) {
    window.localStorage.setItem("mainComputerShowGitServerPane", "1");
  }
}
function toggleGitServerPane() {
  setGitServerPaneVisible(true, {persist: true});
  refreshGitServerStatus();
}
function initializeGitServerHiddenPane() {
  if (!gitServerPane) return;
  setGitServerPaneVisible(true, {persist: false});
  refreshGitServerStatus();
}
function renderGitServerStatus(data) {
  if (!gitServerStatus || !gitServerOutput) return;
  gitServerLastStatus = data || null;
  const state = data?.state || "unknown";
  const docker = data?.docker_available ? "docker available" : "docker unavailable";
  const configured = data?.configured ? "configured" : "not configured";
  const webUrl = data?.web_url || "http://localhost:3000/";
  gitServerStatus.textContent = `Git server: ${state} | ${docker} | ${configured} | ${webUrl}`;
  if (gitServerUrlPill && webUrl) {
    gitServerUrlPill.textContent = webUrl.replace(/\/$/, "");
  }
  if (gitServerOpen && webUrl) {
    gitServerOpen.href = webUrl;
  }
  const cloneExamples = Array.isArray(data?.clone_examples) ? data.clone_examples.join("\n") : "";
  const commandLines = data?.commands ? Object.entries(data.commands).map(([name, command]) => `${name}: ${command}`).join("\n") : "";
  const remotePresetLines = Array.isArray(data?.remote_command_presets)
    ? data.remote_command_presets.map((item) => `${item.name}: ${item.command}`).join("\n")
    : "";
  const psText = data?.ps ? `\n\nCompose ps:\n${data.ps.stdout || ""}${data.ps.stderr || ""}` : "";
  const dockerWarning = data?.docker_available === false ? gitServerDockerUnavailableText(data) : "";
  gitServerOutput.textContent = [
    JSON.stringify({
      service: data?.service,
      profile: data?.profile,
      compose_file: data?.compose_file,
      running: data?.running,
      state,
      web_url: webUrl,
      ssh_available: data?.ssh_available === true,
      docker_available: data?.docker_available,
    }, null, 2),
    dockerWarning ? `Docker unavailable:\n${dockerWarning}` : "",
    cloneExamples ? `Clone examples:\n${cloneExamples}` : "",
    commandLines ? `Commands:\n${commandLines}` : "",
    remotePresetLines ? `Remote command presets:\n${remotePresetLines}` : "",
    psText.trim(),
  ].filter(Boolean).join("\n\n");
  applyGitServerDockerAvailability(data);
  updateGitWorkflowSectionSummary("git-server", `${state}; ${docker}`);
}
const LOCAL_GITEA_REMOTE_NAME = "local-gitea";
let gitServerTargetPrefunk = null;
let gitServerTargetDirty = false;
let gitServerTargetProjectKeyValue = "";

function gitServerSetControlValue(control, value, {markDirty = false} = {}) {
  if (!control) return;
  const next = String(value ?? "");
  if (control.value === next) return;
  control.value = next;
  if (markDirty) gitServerTargetDirty = true;
}
function gitServerCleanRemoteSegment(value, fallback = "") {
  return String(value || fallback).trim().replace(/\s+/g, "-");
}
function gitServerBaseName(path, fallback = "repository") {
  const cleaned = String(path || "").replace(/[\\/]+$/, "");
  const tail = cleaned.split(/[\\/]/).filter(Boolean).pop();
  return gitServerCleanRemoteSegment(tail, fallback) || fallback;
}
function gitServerTargetProjectKeyFromData(data = gitServerTargetPrefunk) {
  return String(data?.git_root || data?.repo_dir || "");
}
function gitServerLocalGiteaUrl(owner, repo, protocol = "http") {
  const cleanOwner = gitServerCleanRemoteSegment(owner, "local") || "local";
  const cleanRepo = gitServerCleanRemoteSegment(repo, "repository") || "repository";
  return `http://localhost:3000/${cleanOwner}/${cleanRepo}.git`;
}
function gitServerTargetFromStatus(status = {}) {
  if (!status?.ok || !status?.is_git_repo || !status?.git_root) {
    return {
      ok: true,
      repo_dir: status?.repo_dir || gitToolsRepoDirValue("."),
      is_git_repo: false,
      has_head: false,
      git_root: "",
      target: {
        remote: LOCAL_GITEA_REMOTE_NAME,
        owner: "local",
        repo: "",
        protocol: "http",
        url: "",
        source: "not-a-git-repo",
        saved: false,
        configurable: false,
        pushable: false,
      },
      reason: status?.reason || "git-init-required",
      error: status?.error || "Selected project is not inside a git worktree.",
    };
  }
  const remotes = Array.isArray(status.remotes) ? status.remotes : [];
  const local = remotes.find((remote) => remote.name === LOCAL_GITEA_REMOTE_NAME);
  const parsed = gitServerParseLocalGiteaUrl(local?.fetch || local?.push || "");
  const target = parsed
    ? {
        remote: LOCAL_GITEA_REMOTE_NAME,
        owner: parsed.owner,
        repo: parsed.repo,
        protocol: parsed.protocol === "ssh" ? "http" : parsed.protocol,
        url: parsed.protocol === "ssh" ? gitServerLocalGiteaUrl(parsed.owner, parsed.repo, "http") : parsed.url,
        legacy_url: parsed.protocol === "ssh" ? parsed.url : "",
        source: parsed.protocol === "ssh" ? "detected-legacy-ssh-local-gitea-remote" : "detected-from-git-remote",
        saved: parsed.protocol !== "ssh",
        configurable: true,
      }
    : {
        remote: LOCAL_GITEA_REMOTE_NAME,
        owner: "local",
        repo: gitServerBaseName(status.git_root),
        protocol: "http",
        url: gitServerLocalGiteaUrl("local", gitServerBaseName(status.git_root), "http"),
        source: "suggested-from-git-root",
        saved: false,
        configurable: true,
      };
  target.pushable = status.has_head !== false;
  return {
    ok: true,
    repo_dir: status.repo_dir || gitToolsRepoDirValue("."),
    is_git_repo: true,
    has_head: status.has_head !== false,
    git_root: status.git_root,
    remotes,
    target,
  };
}
function gitServerParseLocalGiteaUrl(url) {
  const raw = String(url || "").trim();
  let match = raw.match(/^https?:\/\/localhost:3000\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/i);
  if (match) return {protocol: "http", owner: match[1], repo: match[2], url: raw};
  match = raw.match(/^ssh:\/\/git@localhost:2222\/([^/]+)\/([^/]+?)(?:\.git)?\/?$/i);
  if (match) return {protocol: "ssh", owner: match[1], repo: match[2], url: raw};
  return null;
}
function gitServerSetTargetControlsEnabled(enabled) {
  [gitServerOwner, gitServerRepo, gitServerRemoteProtocol].filter(Boolean).forEach((control) => {
    control.disabled = !enabled;
  });
  if (gitServerRemoteName) {
    gitServerRemoteName.value = LOCAL_GITEA_REMOTE_NAME;
    gitServerRemoteName.disabled = true;
    gitServerRemoteName.readOnly = true;
  }
}
function gitServerSetLocalActionAvailability({configurable = true, pushable = true, reason = ""} = {}) {
  const dockerUnavailable = Boolean(gitServerLastStatus && gitServerLastStatus.docker_available === false);
  if (gitServerRemoteApplyLocal) {
    gitServerRemoteApplyLocal.disabled = dockerUnavailable || !configurable;
    gitServerRemoteApplyLocal.setAttribute("aria-disabled", gitServerRemoteApplyLocal.disabled ? "true" : "false");
    gitServerRemoteApplyLocal.title = !configurable ? reason : (dockerUnavailable ? "Docker CLI is not available where Main Computer is running." : "");
  }
  if (gitServerPushLocal) {
    gitServerPushLocal.disabled = dockerUnavailable || !pushable;
    gitServerPushLocal.setAttribute("aria-disabled", gitServerPushLocal.disabled ? "true" : "false");
    gitServerPushLocal.title = !pushable ? reason : (dockerUnavailable ? "Docker CLI is not available where Main Computer is running." : "");
  }
}
function gitServerTargetNote() {
  if (!gitServerTargetPrefunk) return "Checking selected project before showing a target.";
  if (!gitServerTargetPrefunk.is_git_repo) return "Selected project is not a Git repo; no Local Gitea target is suggested or saved.";
  if (gitServerTargetDirty) return "Edited in this pane; not saved until Create / verify repo + configure remote succeeds.";
  const source = gitServerTargetPrefunk.target?.source || "";
  if (source === "detected-legacy-ssh-local-gitea-remote") return "Detected an old SSH local-gitea remote; configure remote to replace it with HTTP localhost:3000.";
  if (source === "detected-from-git-remote") return "Detected from this repo's local-gitea remote; this is saved.";
  if (source === "suggested-from-git-root") return "Suggested from Git root; not saved until Create / verify repo + configure remote succeeds.";
  return "Fixed remote name; repository is a suggested project target until configured.";
}
function gitServerTargetUnavailableReason(data = gitServerTargetPrefunk) {
  if (!data || data.is_git_repo) return "";
  return "Select or initialize a Git repo before configuring Local Gitea.";
}
function gitServerCurrentTargetUrl() {
  const repo = (gitServerRepo?.value || "").trim();
  if (!repo) return "";
  return gitServerLocalGiteaUrl(gitServerOwner?.value || "local", repo, gitServerRemoteProtocol?.value || "http");
}
function gitServerRenderTargetPreview() {
  const mode = gitServerRemoteModeValue();
  const switchOrigin = mode === "switch-origin";
  const localRemote = switchOrigin ? "origin" : LOCAL_GITEA_REMOTE_NAME;
  const unavailable = Boolean(gitServerTargetPrefunk && !gitServerTargetPrefunk.is_git_repo);
  const url = unavailable ? "" : gitServerCurrentTargetUrl();
  const note = switchOrigin && !unavailable
    ? "Replacing origin makes Local Gitea the primary remote for this checkout."
    : gitServerTargetNote();
  if (gitServerRemoteModePill) {
    gitServerRemoteModePill.textContent = switchOrigin ? "replaces origin" : "second remote";
  }
  if (gitServerTargetPreview) {
    const source = document.createElement("span");
    source.className = "gitea-target-source";
    source.id = "git-server-target-source";
    source.textContent = note;
    gitServerTargetPreview.replaceChildren(
      document.createTextNode("Local Gitea target: "),
      Object.assign(document.createElement("code"), {textContent: unavailable ? "unavailable" : localRemote}),
      document.createTextNode(url ? " → " : " — "),
      Object.assign(document.createElement("code"), {textContent: url || "no Git repo selected"}),
      source
    );
  }
  if (gitServerFixedRemotePreview) gitServerFixedRemotePreview.textContent = LOCAL_GITEA_REMOTE_NAME;
  if (gitServerAddLocalNamePreview) gitServerAddLocalNamePreview.textContent = LOCAL_GITEA_REMOTE_NAME;
  if (gitServerAddLocalUrlPreview) gitServerAddLocalUrlPreview.textContent = url || "not available";
  if (gitServerAddLocalPushPreview) gitServerAddLocalPushPreview.textContent = `git push ${LOCAL_GITEA_REMOTE_NAME} HEAD`;
  if (gitServerOriginUrlPreview) gitServerOriginUrlPreview.textContent = url || "not available";
  if (gitServerRemoteCommand && !url) gitServerRemoteCommand.value = "git remote -v";
}
function gitServerApplyTargetPrefunk(data, {announce = false, preserveEdited = true} = {}) {
  const previousKey = gitServerTargetProjectKeyFromData();
  const nextKey = gitServerTargetProjectKeyFromData(data);
  const projectChanged = previousKey && nextKey && previousKey !== nextKey;
  gitServerTargetPrefunk = data || null;
  if (projectChanged) gitServerTargetDirty = false;
  gitServerTargetProjectKeyValue = nextKey;
  if (gitServerRemoteMode) gitServerRemoteMode.value = "add-local";
  if (gitServerRemoteName) {
    gitServerRemoteName.value = LOCAL_GITEA_REMOTE_NAME;
    gitServerRemoteName.disabled = true;
    gitServerRemoteName.readOnly = true;
  }

  if (!data?.is_git_repo) {
    gitServerTargetDirty = false;
    gitServerSetTargetControlsEnabled(false);
    gitServerSetControlValue(gitServerOwner, "local");
    gitServerSetControlValue(gitServerRepo, "");
    if (gitServerRepo) gitServerRepo.placeholder = "No Git repo selected";
    gitServerSetControlValue(gitServerRemoteProtocol, "http");
    gitServerSetLocalActionAvailability({
      configurable: false,
      pushable: false,
      reason: gitServerTargetUnavailableReason(data),
    });
    gitServerRenderTargetPreview();
    if (announce && gitServerOutput) {
      gitServerOutput.textContent = "Local Gitea target unavailable: selected project is not a Git repository yet.";
    }
    return;
  }

  gitServerSetTargetControlsEnabled(true);
  if (gitServerRepo) gitServerRepo.placeholder = "repository";
  const target = data.target || {};
  if (!preserveEdited || !gitServerTargetDirty) {
    gitServerSetControlValue(gitServerOwner, target.owner || "local");
    gitServerSetControlValue(gitServerRepo, target.repo || gitServerBaseName(data.git_root));
    gitServerSetControlValue(gitServerRemoteProtocol, target.protocol || "http");
  }
  gitServerSetLocalActionAvailability({
    configurable: true,
    pushable: data.has_head !== false,
    reason: data.has_head === false ? "Create an initial commit before pushing to Local Gitea." : "",
  });
  updateGitServerRemoteMode();
  if (announce && gitServerOutput) {
    gitServerOutput.textContent = target.saved
      ? `Local Gitea target detected from this repo's ${LOCAL_GITEA_REMOTE_NAME} remote.`
      : `Local Gitea target suggested from Git root: ${target.repo || gitServerBaseName(data.git_root)}. Not saved yet.`;
  }
}
async function refreshGitServerTargetPrefunk(options = {}) {
  const repoDir = gitToolsRepoDirValue(".");
  try {
    const data = await gitToolsStatusApi().fetchServerTargetPrefunk({repoDir});
    gitServerApplyTargetPrefunk(data, options);
    return data;
  } catch (error) {
    const data = {
      ok: true,
      repo_dir: repoDir,
      is_git_repo: false,
      has_head: false,
      target: {
        remote: LOCAL_GITEA_REMOTE_NAME,
        owner: "local",
        repo: "",
        protocol: "http",
        url: "",
        source: "not-a-git-repo",
        saved: false,
        configurable: false,
        pushable: false,
      },
      error: error.message || String(error),
    };
    gitServerApplyTargetPrefunk(data, options);
    return data;
  }
}
function clearGitServerTargetForProjectChange() {
  gitServerTargetPrefunk = {
    ok: true,
    repo_dir: gitToolsRepoDirValue("."),
    is_git_repo: false,
    has_head: false,
    git_root: "",
    target: {
      remote: LOCAL_GITEA_REMOTE_NAME,
      owner: "local",
      repo: "",
      protocol: "http",
      url: "",
      source: "checking-selected-project",
      saved: false,
      configurable: false,
      pushable: false,
    },
  };
  gitServerTargetDirty = false;
  gitServerTargetProjectKeyValue = "";
  gitServerSetTargetControlsEnabled(false);
  gitServerSetControlValue(gitServerOwner, "local");
  gitServerSetControlValue(gitServerRepo, "");
  if (gitServerRepo) gitServerRepo.placeholder = "Checking selected project…";
  gitServerSetControlValue(gitServerRemoteProtocol, "http");
  gitServerSetLocalActionAvailability({
    configurable: false,
    pushable: false,
    reason: "Checking selected project Git state.",
  });
  gitServerRenderTargetPreview();
}
function gitServerEnsureConfigurable(actionLabel) {
  if (gitServerTargetPrefunk && !gitServerTargetPrefunk.is_git_repo) {
    if (gitServerOutput) gitServerOutput.textContent = `${actionLabel} cannot run.

Select or initialize a Git repo before configuring Local Gitea.`;
    updateGitWorkflowSectionSummary("git-server", "git repo required");
    gitServerApplyTargetPrefunk(gitServerTargetPrefunk, {preserveEdited: false});
    return false;
  }
  return true;
}
function gitServerEnsurePushable() {
  if (!gitServerEnsureConfigurable("Push to Local Gitea")) return false;
  if (gitServerTargetPrefunk && gitServerTargetPrefunk.has_head === false) {
    if (gitServerOutput) gitServerOutput.textContent = "Push to Local Gitea cannot run. Create an initial commit before pushing.";
    updateGitWorkflowSectionSummary("git-server", "initial commit required");
    gitServerSetLocalActionAvailability({
      configurable: true,
      pushable: false,
      reason: "Create an initial commit before pushing to Local Gitea.",
    });
    return false;
  }
  return true;
}
function gitServerMarkTargetEdited() {
  if (gitServerTargetPrefunk && !gitServerTargetPrefunk.is_git_repo) return;
  gitServerTargetDirty = true;
  gitServerRenderTargetPreview();
}

function gitServerRemoteModeValue() {
  return gitServerRemoteMode?.value || "add-local";
}
function gitServerSwitchesOrigin() {
  return gitServerRemoteModeValue() === "switch-origin";
}
function gitServerRemoteNameValue() {
  if (gitServerSwitchesOrigin()) return "origin";
  return LOCAL_GITEA_REMOTE_NAME;
}
function gitServerExternalRemoteNameValue() {
  return (gitServerExternalRemoteName?.value || "origin").trim() || "origin";
}
function gitServerRemoteToken(control, label) {
  const value = (control?.value || "").trim();
  if (!value) throw new Error(`Enter a Gitea ${label} first.`);
  return value.replace(/\s+/g, "-");
}
function gitServerRemoteUrl() {
  const owner = gitServerRemoteToken(gitServerOwner, "owner");
  const repo = gitServerRemoteToken(gitServerRepo, "repository");
  return `http://localhost:3000/${owner}/${repo}.git`;
}
function gitServerRemoteUrlPreviewValue() {
  const repo = (gitServerRepo?.value || "").trim();
  if (!repo) return "";
  return gitServerCurrentTargetUrl();
}
function updateGitServerRemoteChoicePreview() {
  const mode = gitServerRemoteModeValue();
  gitServerRemoteChoiceButtons.forEach((choice) => {
    choice.checked = choice.value === mode;
  });
  gitServerRenderTargetPreview();
}
function setGitServerRemoteMode(mode) {
  if (!gitServerRemoteMode) return;
  gitServerRemoteMode.value = mode === "switch-origin" ? "switch-origin" : "add-local";
  updateGitServerRemoteMode();
}
function gitServerLocalRepoName() {
  const candidates = [
    gitToolsLastStatus?.git_root,
    gitToolsRepoDirValue(""),
    window.location?.pathname || "",
    "main_computer_test",
  ];
  for (const candidate of candidates) {
    const cleaned = String(candidate || "").replace(/[\\/]+$/, "");
    const tail = cleaned.split(/[\\/]/).filter(Boolean).pop();
    if (tail && tail !== "." && tail !== "git") {
      return tail.replace(/\s+/g, "-");
    }
  }
  return "main_computer_test";
}
function gitServerHasNonLocalOrigin() {
  const remotes = Array.isArray(gitToolsLastStatus?.remotes) ? gitToolsLastStatus.remotes : [];
  const origin = remotes.find((remote) => remote.name === "origin");
  const url = origin?.fetch || origin?.push || "";
  return Boolean(url && !/localhost:3000|localhost:2222/.test(url));
}
function gitServerIsLocalServerUrl(url) {
  return /(^|@|\/)localhost:(3000|2222)(\/|:|$)/.test(String(url || ""));
}
function gitServerBestExternalUrl() {
  const remotes = Array.isArray(gitToolsLastStatus?.remotes) ? gitToolsLastStatus.remotes : [];
  const origin = remotes.find((remote) => remote.name === "origin");
  const originUrl = origin?.fetch || origin?.push || "";
  if (originUrl && !gitServerIsLocalServerUrl(originUrl)) return originUrl;
  const external = remotes.find((remote) => {
    const url = remote.fetch || remote.push || "";
    return url && !gitServerIsLocalServerUrl(url);
  });
  return external?.fetch || external?.push || "";
}
function updateGitServerRemoteMode() {
  const switchOrigin = gitServerSwitchesOrigin();
  if (gitServerRemoteName) {
    gitServerRemoteName.value = switchOrigin ? "origin" : LOCAL_GITEA_REMOTE_NAME;
    gitServerRemoteName.disabled = !switchOrigin || !gitServerTargetPrefunk?.is_git_repo;
    gitServerRemoteName.readOnly = !switchOrigin;
  }
  if (gitServerRemoteCommand) {
    try {
      gitServerRemoteCommand.value = gitServerRemoteCommandForPreset(switchOrigin ? "set-url" : "add-remote");
    } catch (_error) {
      gitServerRemoteCommand.value = "git remote -v";
    }
  }
  updateGitServerRemoteChoicePreview();
}
async function useLocalGitServerRemote() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "reviewing local Gitea target");
  if (gitServerRemoteMode) {
    gitServerRemoteMode.value = "add-local";
  }
  if (gitServerRemoteName) {
    gitServerRemoteName.disabled = true;
    gitServerRemoteName.readOnly = true;
    gitServerRemoteName.value = LOCAL_GITEA_REMOTE_NAME;
  }
  const target = await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
  if (gitServerExternalUrl && !gitServerExternalUrl.value.trim()) {
    gitServerExternalUrl.value = gitServerBestExternalUrl();
  }
  updateGitServerRemoteMode();
  if (gitServerOutput) {
    if (!target?.is_git_repo) {
      gitServerOutput.textContent = [
        "Local Gitea target is not available yet.",
        "",
        "The selected project is not a Git repository.",
        "Initialize Git or select a Git repo before configuring Local Gitea.",
      ].join("\n");
    } else {
      gitServerOutput.textContent = [
        target.target?.saved ? "Saved Local Gitea target detected." : "Suggested Local Gitea target is ready.",
        "",
        "Mode: keep origin unchanged and add Local Gitea as a second remote",
        `Remote: ${LOCAL_GITEA_REMOTE_NAME}`,
        `URL: ${gitServerCurrentTargetUrl()}`,
        "",
        "Click Create / verify repo + configure remote to start Gitea, create/verify the repo, and save the local-gitea remote.",
        target.has_head === false ? "Create an initial commit before pushing HEAD to local Gitea." : "Then click Push to Local Gitea when you want to publish HEAD to local Gitea.",
      ].join("\n");
    }
  }
  updateGitWorkflowSectionSummary("git-server", target?.is_git_repo ? "local target ready" : "git repo required");
}
function gitServerRemoteConfigPayload() {
  return {
    repo_dir: gitToolsRepoDirValue("."),
    remote: gitServerRemoteNameValue(),
    owner: gitServerRemoteToken(gitServerOwner, "owner"),
    repo: gitServerRemoteToken(gitServerRepo, "repository"),
    protocol: gitServerRemoteProtocol?.value || "http",
    switch_origin: gitServerSwitchesOrigin(),
  };
}
function gitServerRemoteCommandForPreset(preset) {
  const remote = gitServerRemoteNameValue();
  switch (preset) {
    case "add-remote":
      return `git remote add ${remote} ${gitServerRemoteUrl()}`;
    case "set-url":
      return `git remote set-url ${remote} ${gitServerRemoteUrl()}`;
    case "push-head":
      return `git push -u ${remote} HEAD`;
    case "fetch":
      return `git fetch ${remote}`;
    case "show-remotes":
      return "git remote -v";
    default:
      throw new Error(`Unknown git remote preset: ${preset}`);
  }
}
function fillGitServerRemoteCommand(preset) {
  setGitServerPaneVisible(true, {persist: true});
  try {
    const command = gitServerRemoteCommandForPreset(preset);
    if (gitServerRemoteCommand) {
      gitServerRemoteCommand.value = command;
      gitServerRemoteCommand.focus();
    }
    if (gitServerOutput) {
      gitServerOutput.textContent = `Prepared preset ${preset}:\n${command}\n\nReview the command, then click Apply Command.`;
    }
    updateGitWorkflowSectionSummary("git-server", `remote preset ready: ${preset}`);
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Remote preset failed: ${error.message || error}`;
  }
}
function copyGitServerRemoteCommandToConsole() {
  const command = (gitServerRemoteCommand?.value || "").trim();
  if (!command) {
    if (gitServerOutput) gitServerOutput.textContent = "Choose a remote preset or enter a command first.";
    return;
  }
  if (gitConsoleInput) gitConsoleInput.value = command;
  expandGitWorkflowSection("ai-interpretation", "remote command copied to Git Console");
  if (gitServerOutput) gitServerOutput.textContent = `Copied to Git Console:\n${command}`;
}
async function applyLocalGitServerRemote() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "setting up local git server");
  if (!gitServerTargetPrefunk) await refreshGitServerTargetPrefunk({announce: false});
  if (!gitServerEnsureConfigurable("Create / verify repo + configure remote")) return;
  if (!(await ensureGitServerDockerAvailable("Create / verify repo + configure remote"))) return;
  let payload;
  try {
    payload = gitServerRemoteConfigPayload();
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Local server setup is incomplete: ${error.message || error}`;
    return;
  }
  const url = gitServerCurrentTargetUrl();
  const progress = startGitServerProgress(
    "Setting up local Git server...",
    [
      "This will start the standalone Docker Gitea stack if needed, create/verify the local Gitea repo, and configure the selected remote.",
      "",
      `Remote: ${payload.remote}`,
      `URL: ${url}`,
    ],
    ["starting/verifying standalone Gitea", "checking local Gitea user", "creating/verifying repository", "configuring local-gitea remote"]
  );
  try {
    const data = await runGitServerOperationRequest(gitToolsStatusApi().endpoints.serverSetupLocal, payload, [
      "Setting up local Git server...",
      `Remote: ${payload.remote}`,
      `URL: ${url}`,
    ]);
    progress.stop();
    if (!data) return;
    gitServerTargetDirty = false;
    if (gitServerRemoteCommand) {
      const verb = data.mode === "switch-origin" ? "set-url" : (data.action === "set-url" ? "set-url" : "add");
      gitServerRemoteCommand.value = verb === "set-url"
        ? `git remote set-url ${data.remote} ${data.url}`
        : `git remote add ${data.remote} ${data.url}`;
    }
    if (gitServerOutput) gitServerOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
    await refreshGitStatus();
    if (data.status) renderGitServerStatus(data.status);
    updateGitWorkflowSectionSummary("git-server", data.ok ? "local server ready" : "local server setup failed");
  } catch (error) {
    progress.stop();
    if (gitServerOutput) gitServerOutput.textContent = gitToolsOperationErrorText("Create / verify repo + configure remote failed", error);
    updateGitWorkflowSectionSummary("git-server", "local server setup failed");
  }
}
async function pushLocalGitServerRemote() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "pushing to local git server");
  if (!gitServerTargetPrefunk) await refreshGitServerTargetPrefunk({announce: false});
  if (!gitServerEnsurePushable()) return;
  if (!(await ensureGitServerDockerAvailable("Push to Local Gitea"))) return;
  let payload;
  try {
    payload = gitServerRemoteConfigPayload();
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Local push is incomplete: ${error.message || error}`;
    return;
  }
  const url = gitServerCurrentTargetUrl();
  const progress = startGitServerProgress(
    "Preparing local Git server and pushing HEAD...",
    [
      "The push uses a temporary local Gitea token and does not save that token in the remote URL.",
      "",
      `Remote: ${payload.remote}`,
      `URL: ${url}`,
    ],
    ["starting/verifying standalone Gitea", "creating/verifying repository", "creating temporary push token", "running git push -u HEAD"]
  );
  try {
    const data = await runGitServerOperationRequest(gitToolsStatusApi().endpoints.serverPushLocal, payload, [
      "Preparing local Git server and pushing HEAD...",
      `Remote: ${payload.remote}`,
      `URL: ${url}`,
    ]);
    progress.stop();
    if (!data) return;
    gitServerTargetDirty = false;
    if (gitServerOutput) gitServerOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitServerTargetPrefunk({announce: false, preserveEdited: false});
    await refreshGitStatus();
    if (data.status) renderGitServerStatus(data.status);
    updateGitWorkflowSectionSummary("git-server", data.ok ? "pushed to local server" : "local push failed");
  } catch (error) {
    progress.stop();
    if (gitServerOutput) gitServerOutput.textContent = gitToolsOperationErrorText("Push to Local Gitea failed", error);
    updateGitWorkflowSectionSummary("git-server", "local push failed");
  }
}
function useExternalGitRemoteDirect() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "external direct remote ready");
  const externalUrl = (gitServerExternalUrl?.value || "").trim();
  const externalRemote = gitServerExternalRemoteNameValue();
  if (!externalUrl) {
    if (gitServerOutput) {
      gitServerOutput.textContent = "Enter a GitHub, GitLab, Gitea Cloud, or other Git URL first. Example: https://github.com/owner/repo.git";
    }
    return;
  }
  const command = `git remote set-url ${externalRemote} ${externalUrl}`;
  if (gitServerRemoteCommand) {
    gitServerRemoteCommand.value = command;
    gitServerRemoteCommand.focus();
  }
  if (gitServerOutput) {
    gitServerOutput.textContent = [
      "External direct remote command prepared.",
      "",
      command,
      "",
      "Click Apply Command to point this checkout directly at the external service, bypassing local Gitea.",
    ].join("\n");
  }
}
async function planGiteaPushMirror() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "planning server mirror");
  const externalUrl = (gitServerMirrorUrl?.value || gitServerExternalUrl?.value || "").trim();
  if (!externalUrl) {
    if (gitServerOutput) gitServerOutput.textContent = "Enter a server push mirror URL first.";
    return;
  }
  let payload;
  try {
    payload = {
      owner: gitServerRemoteToken(gitServerOwner, "owner"),
      repo: gitServerRemoteToken(gitServerRepo, "repository"),
      external_url: externalUrl,
      external_username: (gitServerMirrorUsername?.value || "").trim(),
    };
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Server mirror plan is incomplete: ${error.message || error}`;
    return;
  }
  try {
    const data = await gitToolsStatusApi().planServerMirror(payload);
    if (gitServerOutput) gitServerOutput.textContent = JSON.stringify(data, null, 2);
    updateGitWorkflowSectionSummary("git-server", "server mirror plan ready");
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Server mirror plan failed: ${error.message || error}`;
    updateGitWorkflowSectionSummary("git-server", "server mirror plan failed");
  }
}
async function setupGiteaPushMirror() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "setting up server mirror");
  if (!(await ensureGitServerDockerAvailable("Set Up Server → External Mirror"))) return;
  const externalUrl = (gitServerMirrorUrl?.value || gitServerExternalUrl?.value || "").trim();
  const externalPassword = (gitServerMirrorPassword?.value || "").trim();
  if (!externalUrl) {
    if (gitServerOutput) gitServerOutput.textContent = "Enter a server push mirror URL first.";
    return;
  }
  if (!externalPassword) {
    if (gitServerOutput) {
      gitServerOutput.textContent = [
        "Enter the external token/password first.",
        "Gitea stores that credential for the push mirror. It is not written to .git/config or the manual command box.",
      ].join("\n");
    }
    return;
  }
  let payload;
  try {
    payload = {
      owner: gitServerRemoteToken(gitServerOwner, "owner"),
      repo: gitServerRemoteToken(gitServerRepo, "repository"),
      external_url: externalUrl,
      external_username: (gitServerMirrorUsername?.value || "").trim(),
      external_password: externalPassword,
    };
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Server mirror setup is incomplete: ${error.message || error}`;
    return;
  }
  const progress = startGitServerProgress(
    "Setting up local Gitea push mirror...",
    [
      "This starts/verifies the standalone local Gitea stack and creates the local repository before adding the external push mirror.",
      "The external credential is sent to local Gitea for the mirror configuration.",
      "",
      `External URL: ${externalUrl}`,
    ],
    ["starting/verifying local Gitea", "creating/verifying local repository", "checking existing push mirrors", "creating push mirror"]
  );
  try {
    const data = await runGitServerOperationRequest(gitToolsStatusApi().endpoints.serverMirrorSetup, payload, [
      "Setting up local Gitea push mirror...",
      `External URL: ${externalUrl}`,
    ]);
    progress.stop();
    if (!data) return;
    if (gitServerOutput) gitServerOutput.textContent = JSON.stringify(data, null, 2);
    await refreshGitStatus();
    updateGitWorkflowSectionSummary("git-server", data.ok ? "server mirror ready" : "server mirror setup failed");
  } catch (error) {
    progress.stop();
    if (gitServerOutput) gitServerOutput.textContent = gitToolsOperationErrorText("Server mirror setup failed", error);
    updateGitWorkflowSectionSummary("git-server", "server mirror setup failed");
  }
}
async function runGitServerRemoteCommand() {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", "running remote command");
  const command = (gitServerRemoteCommand?.value || "").trim();
  if (!command) {
    if (gitServerOutput) gitServerOutput.textContent = "Choose a remote preset or enter a command first.";
    return;
  }
  if (gitServerOutput) gitServerOutput.textContent = `Running local git command:\n${command}`;
  try {
    const data = await runGitServerOperationRequest(gitToolsStatusApi().endpoints.consoleRun, {command, repo_dir: gitToolsRepoDirValue(".")}, [
      `Running local git command: ${command}`,
      `Repository path: ${gitToolsRepoDirValue(".")}`,
    ]);
    if (!data) return;
    if (gitServerOutput) gitServerOutput.textContent = JSON.stringify(data, null, 2);
    if (data.shim?.id && gitShimId) {
      gitToolsSelectedShim = data.shim.id;
      gitShimId.value = data.shim.id;
      await refreshGitShims();
    }
    await refreshGitStatus();
    updateGitWorkflowSectionSummary("git-server", `remote command ${data.ok ? "finished" : "failed"}`);
  } catch (error) {
    if (gitServerOutput) gitServerOutput.textContent = `Remote command failed: ${error.message || error}`;
    updateGitWorkflowSectionSummary("git-server", "remote command failed");
  }
}

function initializeGitServerRemoteComposer() {
  if (!gitServerRemoteCommand) return;
  if (!gitServerRemoteCommand.value.trim()) {
    gitServerRemoteCommand.value = "git remote -v";
  }
  gitServerRemoteChoiceButtons.forEach((choice) => {
    choice.addEventListener("change", () => {
      if (choice.checked) {
        setGitServerRemoteMode(choice.value);
      }
    });
  });
  [gitServerOwner, gitServerRepo, gitServerRemoteProtocol].filter(Boolean).forEach((control) => {
    control.addEventListener("input", gitServerMarkTargetEdited);
    control.addEventListener("change", gitServerMarkTargetEdited);
  });
  if (gitServerRemoteName) {
    gitServerRemoteName.addEventListener("input", updateGitServerRemoteChoicePreview);
    gitServerRemoteName.addEventListener("change", updateGitServerRemoteChoicePreview);
  }
  updateGitServerRemoteMode();
  gitServerRemoteCommand.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      runGitServerRemoteCommand();
    }
  });
}
async function refreshGitServerStatus() {
  if (!gitServerPane) return;
  if (gitServerStatus) gitServerStatus.textContent = "Loading Git server status...";
  try {
    const data = await gitToolsStatusApi().fetchServerStatus();
    renderGitServerStatus(data);
  } catch (error) {
    if (gitServerStatus) gitServerStatus.textContent = `Git server status failed: ${error.message || error}`;
    if (gitServerOutput) gitServerOutput.textContent = String(error.message || error);
  }
}
async function runGitServerAction(action) {
  setGitServerPaneVisible(true, {persist: true});
  expandGitWorkflowSection("git-server", `running ${action}`);
  if (!(await ensureGitServerDockerAvailable(`Git server ${action}`))) return;
  if (gitServerOutput) gitServerOutput.textContent = `Running Docker git server action: ${action}...`;
  try {
    const data = await runGitServerOperationRequest(gitToolsStatusApi().endpoints.serverAction, {action}, [
      `Running Docker git server action: ${action}`,
    ]);
    if (!data) return;
    if (gitServerOutput) {
      gitServerOutput.textContent = JSON.stringify(data, null, 2);
    }
    if (data.status) {
      renderGitServerStatus(data.status);
    } else if (action === "logs") {
      updateGitWorkflowSectionSummary("git-server", "logs loaded");
    } else {
      await refreshGitServerStatus();
    }
  } catch (error) {
    if (gitServerStatus) gitServerStatus.textContent = `Git server ${action} failed: ${error.message || error}`;
    if (gitServerOutput) gitServerOutput.textContent = String(error.message || error);
  }
}

  const api = Object.freeze({
    version: VERSION,
    surfaceId: SURFACE_ID,
    sourceFile: SOURCE_FILE,
    startGitServerProgress,
    gitServerOperationButtons,
    gitServerDockerDependentButtons,
    gitServerDockerUnavailableText,
    applyGitServerDockerAvailability,
    ensureGitServerDockerAvailable,
    setGitServerOperationRunning,
    formatGitOperation,
    renderGitOperationStatus,
    refreshGitOperationStatus,
    startGitOperationPolling,
    stopGitOperationPolling,
    runGitServerOperationRequest,
    cancelGitServerOperation,
    gitServerPaneRequested,
    setGitServerPaneVisible,
    toggleGitServerPane,
    initializeGitServerHiddenPane,
    renderGitServerStatus,
    gitServerSetControlValue,
    gitServerCleanRemoteSegment,
    gitServerBaseName,
    gitServerTargetProjectKeyFromData,
    gitServerLocalGiteaUrl,
    gitServerTargetFromStatus,
    gitServerParseLocalGiteaUrl,
    gitServerSetTargetControlsEnabled,
    gitServerSetLocalActionAvailability,
    gitServerTargetNote,
    gitServerTargetUnavailableReason,
    gitServerCurrentTargetUrl,
    gitServerRenderTargetPreview,
    gitServerApplyTargetPrefunk,
    refreshGitServerTargetPrefunk,
    clearGitServerTargetForProjectChange,
    gitServerEnsureConfigurable,
    gitServerEnsurePushable,
    gitServerMarkTargetEdited,
    gitServerRemoteModeValue,
    gitServerSwitchesOrigin,
    gitServerRemoteNameValue,
    gitServerExternalRemoteNameValue,
    gitServerRemoteToken,
    gitServerRemoteUrl,
    gitServerRemoteUrlPreviewValue,
    updateGitServerRemoteChoicePreview,
    setGitServerRemoteMode,
    gitServerLocalRepoName,
    gitServerHasNonLocalOrigin,
    gitServerIsLocalServerUrl,
    gitServerBestExternalUrl,
    updateGitServerRemoteMode,
    useLocalGitServerRemote,
    gitServerRemoteConfigPayload,
    gitServerRemoteCommandForPreset,
    fillGitServerRemoteCommand,
    copyGitServerRemoteCommandToConsole,
    applyLocalGitServerRemote,
    pushLocalGitServerRemote,
    useExternalGitRemoteDirect,
    planGiteaPushMirror,
    setupGiteaPushMirror,
    runGitServerRemoteCommand,
    initializeGitServerRemoteComposer,
    refreshGitServerStatus,
    runGitServerAction
  });

  global.GitToolsServerPanel = api;
  Object.assign(global, {
    startGitServerProgress,
    gitServerOperationButtons,
    gitServerDockerDependentButtons,
    gitServerDockerUnavailableText,
    applyGitServerDockerAvailability,
    ensureGitServerDockerAvailable,
    setGitServerOperationRunning,
    formatGitOperation,
    renderGitOperationStatus,
    refreshGitOperationStatus,
    startGitOperationPolling,
    stopGitOperationPolling,
    runGitServerOperationRequest,
    cancelGitServerOperation,
    gitServerPaneRequested,
    setGitServerPaneVisible,
    toggleGitServerPane,
    initializeGitServerHiddenPane,
    renderGitServerStatus,
    gitServerSetControlValue,
    gitServerCleanRemoteSegment,
    gitServerBaseName,
    gitServerTargetProjectKeyFromData,
    gitServerLocalGiteaUrl,
    gitServerTargetFromStatus,
    gitServerParseLocalGiteaUrl,
    gitServerSetTargetControlsEnabled,
    gitServerSetLocalActionAvailability,
    gitServerTargetNote,
    gitServerTargetUnavailableReason,
    gitServerCurrentTargetUrl,
    gitServerRenderTargetPreview,
    gitServerApplyTargetPrefunk,
    refreshGitServerTargetPrefunk,
    clearGitServerTargetForProjectChange,
    gitServerEnsureConfigurable,
    gitServerEnsurePushable,
    gitServerMarkTargetEdited,
    gitServerRemoteModeValue,
    gitServerSwitchesOrigin,
    gitServerRemoteNameValue,
    gitServerExternalRemoteNameValue,
    gitServerRemoteToken,
    gitServerRemoteUrl,
    gitServerRemoteUrlPreviewValue,
    updateGitServerRemoteChoicePreview,
    setGitServerRemoteMode,
    gitServerLocalRepoName,
    gitServerHasNonLocalOrigin,
    gitServerIsLocalServerUrl,
    gitServerBestExternalUrl,
    updateGitServerRemoteMode,
    useLocalGitServerRemote,
    gitServerRemoteConfigPayload,
    gitServerRemoteCommandForPreset,
    fillGitServerRemoteCommand,
    copyGitServerRemoteCommandToConsole,
    applyLocalGitServerRemote,
    pushLocalGitServerRemote,
    useExternalGitRemoteDirect,
    planGiteaPushMirror,
    setupGiteaPushMirror,
    runGitServerRemoteCommand,
    initializeGitServerRemoteComposer,
    refreshGitServerStatus,
    runGitServerAction
  });
})(window);
