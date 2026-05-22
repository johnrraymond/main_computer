    function selectedAiderArchiveId() {
      const selected = aiderArchiveList && aiderArchiveList.value ? aiderArchiveList.value : "";
      if (selected) return selected;
      const active = aiderContextState.active && typeof aiderContextState.active === "object" ? aiderContextState.active : {};
      return active.archive_id || active.id || "";
    }
    function latestAiderActivityForArchive(archiveId) {
      const target = String(archiveId || "").trim();
      if (!target) return null;
      const matches = aiderContextActivities().filter((activity) => String(activity.archive_id || "") === target);
      matches.sort((left, right) => String(right.updated_at || right.started_at || "").localeCompare(String(left.updated_at || left.started_at || "")));
      return matches[0] || null;
    }
    function latestRunningAiderActivityForArchive(archiveId) {
      const activity = latestAiderActivityForArchive(archiveId);
      return activity && aiderActivityIsRunning(activity) ? activity : null;
    }
    function latestRunningAiderActivity() {
      const running = aiderContextActivities().filter(aiderActivityIsRunning);
      running.sort((left, right) => String(right.updated_at || right.started_at || "").localeCompare(String(left.updated_at || left.started_at || "")));
      return running[0] || null;
    }
    function restoreAiderEditorState(active) {
      const repoDir = String(active && active.repo_dir || "").trim();
      if (repoDir) {
        aiderRepo.value = repoDir;
      }
      const threadId = String(active && (active.archive_id || active.id) || "").trim();
      const runningActivity = threadId ? latestRunningAiderActivityForArchive(threadId) : null;
      const activeEntries = Array.isArray(active && active.entries) ? active.entries : [];
      if (runningActivity) {
        aiderInstruction.value = String(runningActivity.instruction || "");
      } else if (!activeEntries.length) {
        aiderInstruction.value = loadAiderInstructionDraft(threadId);
      } else {
        aiderInstruction.value = "";
        clearAiderInstructionDraft(threadId);
      }
      const activeFiles = Array.isArray(active && active.files) ? active.files : [];
      if (activeFiles.length) {
        fileMapMarked = new Set(activeFiles);
        renderFileMap();
      }
      updateAiderRunButtonState();
    }
    function renderAiderActivity(activity, {force = false} = {}) {
      if (!activity) return;
      const status = String(activity.status || "running").toLowerCase();
      const label = status === "running" ? "Attached backend activity" : `Backend activity ${status}`;
      const result = activity.result && typeof activity.result === "object" ? activity.result : null;
      if (aiderActivityIsRunning(activity)) {
        aiderAttachedActivityId = activity.id || aiderAttachedActivityId;
        const started = activity.started_at ? `started ${formatAiderContextTimestamp(activity.started_at)}` : "";
        const target = [
          activity.archive_id ? `archive ${activity.archive_id}` : "",
          activity.repo_dir ? `repo ${activity.repo_dir}` : "",
          `${Number(activity.file_count || 0)} file${Number(activity.file_count || 0) === 1 ? "" : "s"}`,
          activity.dry_run ? "dry run" : "live run",
          started
        ].filter(Boolean).join(" | ");
        const command = Array.isArray(activity.command) ? activity.command.join(" ") : "";
        const liveOutput = String(activity.output_excerpt || activity.stdout_excerpt || activity.stderr_excerpt || "").trim();
        aiderOutput.innerHTML = [
          `<div class="aider-result">${escapeHtml(label)}: ${escapeHtml(clipAiderContextText(activity.instruction || "Aider command is still running.", 260))}</div>`,
          `<details class="aider-console" open><summary>Activity console</summary><pre>${escapeHtml([target, command].filter(Boolean).join("\n\n"))}</pre></details>`,
          liveOutput ? `<details class="aider-console" open><summary>Live output</summary><pre>${escapeHtml(liveOutput)}</pre></details>` : ""
        ].join("");
        const startedAt = activity.started_at ? Date.parse(activity.started_at) : performance.now();
        startAiderTimer(
          "aider backend activity attached",
          Number.isFinite(startedAt) ? startedAt : performance.now(),
          `activity:${activity.id || activity.archive_id || "attached"}`,
          activity.started_at ? "epoch" : "performance"
        );
        updateAiderRunButtonState();
        return;
      }
      if (result && (force || aiderAttachedActivityId === activity.id)) {
        aiderAttachedActivityId = "";
        renderAiderResult(result);
        aiderInstruction.value = "";
        clearAiderInstructionDraft(activity.archive_id || activity.id || aiderThreadIdFromLocation());
        glStatus.textContent = status === "complete" ? "aider backend activity complete" : "aider backend activity failed";
        stopAiderTimer();
      }
      updateAiderRunButtonState();
    }
    function renderAttachedAiderActivity({force = false} = {}) {
      const archiveId = selectedAiderArchiveId();
      const activity = latestAiderActivityForArchive(archiveId);
      if (!activity) return;
      const shouldForce = force || aiderActivityIsRunning(activity) || aiderAttachedActivityId === activity.id || Boolean(activity.result);
      if (shouldForce) {
        renderAiderActivity(activity, {force: shouldForce});
      } else {
        updateAiderRunButtonState();
      }
    }
    function syncAiderActivityPolling() {
      const hasRunningActivity = aiderContextActivities().some(aiderActivityIsRunning);
      if (hasRunningActivity && !aiderActivityPollTimer) {
        aiderActivityPollTimer = setInterval(() => {
          loadAiderContext().catch(() => {});
        }, 1500);
      } else if (!hasRunningActivity && aiderActivityPollTimer) {
        clearInterval(aiderActivityPollTimer);
        aiderActivityPollTimer = null;
      }
      updateAiderRunButtonState();
    }
    function updateAiderRunButtonState() {
      const selectedActivity = latestAiderActivityForArchive(selectedAiderArchiveId());
      const busy = aiderActionInFlight || aiderActivityIsRunning(selectedActivity);
      if (aiderInstruction) {
        aiderInstruction.readOnly = busy;
        aiderInstruction.setAttribute("aria-readonly", busy ? "true" : "false");
        aiderInstruction.title = busy ? "The instruction box is locked while this thread is running." : "";
      }
      if (aiderPreview) {
        aiderPreview.disabled = busy;
        aiderPreview.title = busy
          ? "Aider actions are disabled while a backend action is running."
          : "";
      }
      if (!aiderRun) return;
      aiderRun.disabled = busy;
      aiderRun.title = busy
        ? "Run Aider is disabled while a backend action is running."
        : "";
    }
    function normalizedAiderTimeoutSeconds() {
      const fallback = 600;
      const value = Number.parseInt(String(aiderTimeoutSeconds.value || "").trim(), 10);
      const normalized = Number.isFinite(value) && value > 0 ? value : fallback;
      aiderTimeoutSeconds.value = String(normalized);
      return normalized;
    }
    function aiderPayload() {
      return {
        repo_dir: aiderRepo.value || ".",
        files: [...fileMapMarked].sort(),
        instruction: aiderInstruction.value || "Prepare the requested code edit.",
        model: aiderModel.value || "",
        dry_run: aiderDryRun.checked,
        timeout_seconds: normalizedAiderTimeoutSeconds()
      };
    }

    aiderInstruction.addEventListener("input", () => {
      const threadId = selectedAiderArchiveId() || aiderThreadIdFromLocation();
      saveAiderInstructionDraft(threadId, aiderInstruction.value);
    });

    function formatAiderContextTimestamp(value) {
      if (!value) return "unknown";
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return String(value);
      return parsed.toLocaleString();
    }
    function clipAiderContextText(value, limit = 180) {
      const text = String(value || "").replace(/\s+/g, " ").trim();
      if (!text) return "";
      if (text.length <= limit) return text;
      return `${text.slice(0, Math.max(1, limit - 1)).trimEnd()}…`;
    }
    function aiderHistoryTitle(entry) {
      const action = String(entry.kind || "event").replace(/[_-]+/g, " ").trim() || "event";
      return action;
    }
    function aiderHistorySummary(entry) {
      const result = clipAiderContextText(entry.result_excerpt || "", 220);
      if (result) return result;
      const instruction = clipAiderContextText(entry.instruction || "", 220);
      if (instruction) return instruction;
      return "No summary text saved for this action.";
    }
    function aiderHistoryPrompt(entry) {
      const instruction = clipAiderContextText(entry.instruction || "", 600);
      return instruction || "";
    }
    function aiderHistoryResult(entry) {
      return String(entry.result_excerpt || "").trim();
    }
    function aiderHistoryDetail(entry) {
      const parts = [];
      if (entry.repo_dir) parts.push(`<code>${escapeHtml(entry.repo_dir)}</code>`);
      if (entry.file_count) parts.push(`${Number(entry.file_count) || 0} file${Number(entry.file_count) === 1 ? "" : "s"}`);
      parts.push(entry.dry_run ? "dry run" : "live run");
      if (entry.returncode !== null && entry.returncode !== undefined) parts.push(`exit ${entry.returncode}`);
      return parts.join(" | ");
    }
