    async function sendAiderAction(path, busyLabel) {
      aiderActionInFlight = true;
      let keepTimerRunning = false;
      updateAiderRunButtonState();
      const readOnly = isReadOnlyEditorInstruction();
      const targetPath = readOnly ? "/api/applications/editor/read" : path;
      const targetLabel = readOnly ? "editor reading" : busyLabel;
      const startedAt = startAiderTimer(targetLabel, performance.now(), "request");
      aiderOutput.innerHTML = `<div class="aider-result">${escapeHtml(`${targetLabel}...`)}</div>`;
      try {
        const response = await fetch(targetPath, {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(aiderPayload())
        });
        const data = await response.json();
        data.frontend_duration_ms = Math.round(performance.now() - startedAt);
        if (data.accepted && data.job) {
          renderAiderContext(data);
          renderAiderActivity(data.job, {force: true});
          syncAiderActivityPolling();
          updateAiderRunButtonState();
          keepTimerRunning = true;
          return;
        }
        renderAiderResult(data);
        await loadAiderContext();
        if (!response.ok) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        glStatus.textContent = readOnly
          ? `editor read complete | ${formatDuration(data.frontend_duration_ms)}`
          : `${path.endsWith("/run") ? "aider run complete" : "aider command ready"} | ${formatDuration(data.frontend_duration_ms)}`;
      } catch (error) {
        glStatus.textContent = "aider action failed";
        aiderOutput.innerHTML += `<div class="aider-result">error: ${escapeHtml(error.message || error)}</div>`;
      } finally {
        aiderActionInFlight = false;
        updateAiderRunButtonState();
        if (!keepTimerRunning) {
          stopAiderTimer();
        }
        updateAiderRunButtonState();
      }
    }

    aiderTimeoutSeconds.addEventListener("change", normalizedAiderTimeoutSeconds);
    aiderTimeoutSeconds.addEventListener("blur", normalizedAiderTimeoutSeconds);
    normalizedAiderTimeoutSeconds();
    aiderPreview.addEventListener("click", () => sendAiderAction("/api/applications/aider/prepare", "aider preview"));
    aiderRun.addEventListener("click", () => sendAiderAction("/api/applications/aider/run", "aider running"));
    aiderArchiveList.addEventListener("change", () => {
      syncAiderThreadRoute(aiderArchiveList.value || "", {replace: false});
      renderSelectedAiderArchiveMeta();
      renderAttachedAiderActivity({force: true});
      syncAiderActivityPolling();
      updateAiderRunButtonState();
    });
    aiderArchiveCurrent.addEventListener("click", () => updateAiderContext(
      "/api/applications/aider/context/archive",
      {repo_dir: aiderRepo.value || ".", files: [...fileMapMarked].sort()},
      "aider context archived"
    ));
    aiderResetContext.addEventListener("click", () => updateAiderContext(
      "/api/applications/aider/context/reset",
      {repo_dir: aiderRepo.value || ".", files: [...fileMapMarked].sort()},
      "aider context reset"
    ));
    aiderLoadArchive.addEventListener("click", () => {
      if (!aiderArchiveList.value) {
        aiderArchiveMeta.textContent = "Select archived content first.";
        return;
      }
      updateAiderContext(
        "/api/applications/aider/context/load",
        {archive_id: aiderArchiveList.value},
        "aider archived content loaded"
      ).catch(() => {});
    });
    fileMapRefresh.addEventListener("click", loadFileMap);
    fileMapApply.addEventListener("click", syncAiderFilesFromMarked);
    fileMapSearch.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        loadFileMap();
      }
    });

    updateAiderRunButtonState();
