    function renderSelectedAiderArchiveMeta() {
      const archives = Array.isArray(aiderContextState.archives) ? aiderContextState.archives : [];
      const selectedMeta = archives.find((archive) => archive.id === aiderArchiveList.value) || archives[0];
      if (!selectedMeta) {
        aiderArchiveMeta.textContent = "No archived content yet.";
        return;
      }
      const activity = latestAiderActivityForArchive(selectedMeta.id);
      const activityText = activity
        ? `${aiderActivityIsRunning(activity) ? "running" : String(activity.status || "finished")} activity ${activity.id || ""}`.trim()
        : "";
      aiderArchiveMeta.textContent = [
        `${selectedMeta.entry_count || 0} entr${selectedMeta.entry_count === 1 ? "y" : "ies"}`,
        selectedMeta.repo_dir ? `repo ${selectedMeta.repo_dir}` : "",
        selectedMeta.archived_at ? `archived ${formatAiderContextTimestamp(selectedMeta.archived_at)}` : "",
        activityText,
        selectedMeta.preview ? selectedMeta.preview : ""
      ].filter(Boolean).join(" | ");
    }

    function compactAiderRepoPath(path) {
      const value = String(path || "").trim();
      if (!value) return "";
      const parts = value.split(/[\\/]+/).filter(Boolean);
      if (parts.length <= 2 || value.length <= 42) return value;
      const drive = /^[A-Za-z]:$/.test(parts[0]) ? `${parts[0]}\\` : "";
      return `${drive}...\\${parts[parts.length - 1]}`;
    }

    // Only history rendering is cached; live status, archives, and controls still refresh.
    let aiderRenderedHistorySignature = null;

    function renderAiderContext(data) {
      aiderContextState = data && typeof data === "object" ? data : {active: {entries: []}, archives: [], activities: []};
      const active = aiderContextState.active ? aiderContextState.active : {entries: [], files: []};
      const entries = Array.isArray(active.entries) ? active.entries : [];
      const archives = Array.isArray(aiderContextState.archives) ? aiderContextState.archives : [];
      const sessionMetaParts = [
        `Active context ${active.id || ""}`.trim(),
        active.archive_id ? `archive ${active.archive_id}` : "",
        `${entries.length} entr${entries.length === 1 ? "y" : "ies"}`,
        active.repo_dir ? `repo ${compactAiderRepoPath(active.repo_dir)}` : "",
        active.origin_archive_id ? `seeded from ${active.origin_archive_id}` : "",
        active.updated_at ? `updated ${formatAiderContextTimestamp(active.updated_at)}` : ""
      ].filter(Boolean);
      aiderSessionMeta.textContent = sessionMetaParts.join(" | ");
      aiderSessionMeta.title = [
        `Active context ${active.id || ""}`.trim(),
        active.archive_id ? `archive ${active.archive_id}` : "",
        `${entries.length} entr${entries.length === 1 ? "y" : "ies"}`,
        active.repo_dir ? `repo ${active.repo_dir}` : "",
        active.origin_archive_id ? `seeded from ${active.origin_archive_id}` : "",
        active.updated_at ? `updated ${formatAiderContextTimestamp(active.updated_at)}` : ""
      ].filter(Boolean).join(" | ");
      // Capture exactly the values rendered into history cards, plus thread identity.
      // Activity/status changes must not destroy unchanged <details> state or card nodes.
      const historyRows = entries.slice().reverse().map((entry) => ({
        key: entry.id || entry.timestamp || aiderHistoryTitle(entry),
        title: aiderHistoryTitle(entry),
        timestamp: formatAiderContextTimestamp(entry.timestamp),
        summary: aiderHistorySummary(entry),
        prompt: aiderHistoryPrompt(entry),
        result: aiderHistoryResult(entry),
        detail: aiderHistoryDetail(entry)
      }));
      const historySignature = JSON.stringify([
        active.id || "",
        active.archive_id || "",
        active.origin_archive_id || "",
        historyRows
      ]);
      if (historySignature !== aiderRenderedHistorySignature) {
        aiderHistoryList.innerHTML = "";
        if (!historyRows.length) {
          aiderHistoryList.innerHTML = '<div class="aider-history-empty">No Aider web-context history yet.</div>';
        } else {
          historyRows.forEach((row) => {
            const card = document.createElement("article");
            card.className = "aider-history-entry";
            card.dataset.mcGeneratedItem = "true";
            card.dataset.mcItemKind = "aider-history-entry";
            card.dataset.mcItemKey = row.key;
            card.dataset.mcComponentOwner = "code-editor.aider.history-list";
            card.dataset.mcFeatureId = "code-editor.feature.aider-context";
            card.innerHTML = [
              "<header>",
              `<span>${escapeHtml(row.title)}</span>`,
              `<span>${escapeHtml(row.timestamp)}</span>`,
              "</header>",
              row.summary ? `<div class="aider-result">${escapeHtml(row.summary)}</div>` : "",
              row.prompt ? `<details class="aider-console" open><summary>Prompt</summary><pre>${escapeHtml(row.prompt)}</pre></details>` : "",
              row.result ? `<details class="aider-console" open><summary>Latest result</summary><pre>${escapeHtml(row.result)}</pre></details>` : "",
              `<p>${escapeHtml(row.detail)}</p>`
            ].join("");
            aiderHistoryList.append(card);
          });
        }
        aiderRenderedHistorySignature = historySignature;
      }
      const selectedArchive = aiderArchiveList.value;
      aiderArchiveList.innerHTML = "";
      archives.forEach((archive) => {
        const option = document.createElement("option");
        option.value = archive.id || "";
        option.dataset.mcGeneratedItem = "true";
        option.dataset.mcItemKind = "aider-archive-option";
        option.dataset.mcItemKey = archive.id || archive.label || "";
        option.dataset.mcComponentOwner = "code-editor.aider.archive-list";
        option.dataset.mcFeatureId = "code-editor.feature.aider-context";
        option.textContent = `${archive.label || archive.id || "archive"} (${archive.entry_count || 0})`;
        aiderArchiveList.append(option);
      });
      const preferredArchive = [
        active.archive_id,
        selectedArchive
      ].find((candidate) => candidate && archives.some((archive) => archive.id === candidate));
      if (preferredArchive && archives.some((archive) => archive.id === preferredArchive)) {
        aiderArchiveList.value = preferredArchive;
      } else if (archives.length) {
        aiderArchiveList.selectedIndex = 0;
      }
      syncAiderThreadRoute(aiderArchiveList.value || active.archive_id || active.id || "", {replace: true});
      restoreAiderEditorState(active);
      renderSelectedAiderArchiveMeta();
      renderAttachedAiderActivity();
      syncAiderActivityPolling();
      updateAiderRunButtonState();
    }
    async function loadAiderContext() {
      try {
        const response = await fetch("/api/applications/aider/context");
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        const requestedArchive = aiderThreadIdFromLocation();
        const activeThread = String(data?.active?.archive_id || data?.active?.id || "").trim();
        const archiveExists = requestedArchive
          && Array.isArray(data.archives)
          && data.archives.some((archive) => String(archive.id || "") === requestedArchive);
        if (requestedArchive && requestedArchive !== activeThread && archiveExists && !aiderThreadLoadInFlight) {
          aiderThreadLoadInFlight = true;
          try {
            await updateAiderContext(
              "/api/applications/aider/context/load",
              {archive_id: requestedArchive},
              "aider archived content loaded"
            );
            return;
          } finally {
            aiderThreadLoadInFlight = false;
          }
        }
        renderAiderContext(data);
      } catch (error) {
        aiderSessionMeta.textContent = error.message || "Aider web context failed to load.";
        aiderArchiveMeta.textContent = error.message || "Aider web context failed to load.";
        syncAiderActivityPolling();
      }
    }
    async function updateAiderContext(path, body, busyLabel) {
      aiderArchiveCurrent.disabled = true;
      aiderResetContext.disabled = true;
      aiderLoadArchive.disabled = true;
      try {
        glStatus.textContent = busyLabel;
        const response = await fetch(path, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(body || {})
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        renderAiderContext(data);
        glStatus.textContent = busyLabel;
      } catch (error) {
        aiderArchiveMeta.textContent = error.message || "Aider web context update failed.";
        glStatus.textContent = "aider context failed";
      } finally {
        aiderArchiveCurrent.disabled = false;
        aiderResetContext.disabled = false;
        aiderLoadArchive.disabled = false;
      }
    }
    function startAiderTimer(label, startedAt = performance.now(), sourceKey = "", clock = "performance") {
      if (aiderTimer && sourceKey && sourceKey === aiderTimerSourceKey && label === aiderTimerLabel) {
        return startedAt;
      }
      clearInterval(aiderTimer);
      aiderTimerLabel = label;
      aiderTimerSourceKey = sourceKey;
      const useEpochClock = clock === "epoch";
      const nowFn = useEpochClock ? Date.now : performance.now.bind(performance);
      const normalizedStartedAt = Number.isFinite(Number(startedAt)) ? Number(startedAt) : nowFn();
      aiderTimer = setInterval(() => {
        const elapsed = ((nowFn() - normalizedStartedAt) / 1000).toFixed(1);
        glStatus.textContent = `${label} | ${elapsed}s`;
      }, 200);
      glStatus.textContent = `${label} | 0.0s`;
      return normalizedStartedAt;
    }
    function stopAiderTimer() {
      clearInterval(aiderTimer);
      aiderTimer = null;
      aiderTimerLabel = "";
      aiderTimerSourceKey = "";
    }
    function formatDuration(ms) {
      return `${(Math.max(0, ms) / 1000).toFixed(2)}s`;
    }
    function isReadOnlyEditorInstruction() {
      const text = aiderInstruction.value.trim().toLowerCase();
      return /^(show|list|read|display|open|load|cat|print|view)\b/.test(text)
        || /\b(contents?|read[- ]?only|show me|list the)\b/.test(text);
    }
