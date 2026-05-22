    function syncAiderFilesFromMarked() {
      const selected = [...fileMapMarked].sort();
      aiderFiles.value = selected.join("\n");
      fileMapStatus.textContent = selected.length
        ? `${selected.length} marked for the Aider map`
        : "No files marked for the Aider map.";
      saveFileMapMarked();
    }
    function fileExplorerKey(path) {
      return path || "";
    }
    function setFileExplorerNode(path, entries, loaded = true) {
      fileExplorerNodes.set(fileExplorerKey(path), {path: path || "", entries: entries || [], loaded});
    }
    function visibleFileExplorerRows() {
      const rows = [];
      function walk(path, depth) {
        const node = fileExplorerNodes.get(fileExplorerKey(path));
        if (!node) return;
        node.entries.forEach((entry) => {
          rows.push({...entry, depth});
          if (entry.kind === "dir" && fileExplorerOpen.has(entry.path)) {
            walk(entry.path, depth + 1);
          }
        });
      }
      walk("", 0);
      return rows;
    }
    function renderFileMapSearchResults(entries) {
      fileMapList.innerHTML = "";
      if (!entries.length) {
        const empty = document.createElement("div");
        empty.className = "file-map-empty";
        empty.textContent = "No files match that search.";
        fileMapList.append(empty);
        return;
      }
      entries.forEach((entry) => fileMapList.append(renderFileExplorerRow(entry, 0, true)));
      fileMapStatus.textContent = `${entries.length} search matches | ${fileMapMarked.size} marked`;
      syncAiderFilesFromMarked();
    }
    function renderFileMap() {
      const rows = visibleFileExplorerRows();
      fileMapList.innerHTML = "";
      if (!rows.length) {
        const empty = document.createElement("div");
        empty.className = "file-map-empty";
        empty.textContent = fileExplorerRoot ? "This directory is empty." : "No directory loaded yet.";
        fileMapList.append(empty);
        return;
      }
      rows.forEach((entry) => fileMapList.append(renderFileExplorerRow(entry, entry.depth || 0, false)));
      fileMapStatus.textContent = `${rows.length} visible | ${fileMapMarked.size} marked`;
      syncAiderFilesFromMarked();
    }
    function renderFileExplorerRow(entry, depth, fromSearch) {
      const row = document.createElement("div");
      row.className = "file-map-row";
      row.dataset.kind = entry.kind;
      row.dataset.mcGeneratedItem = "true";
      row.dataset.mcItemKind = entry.kind === "dir" ? "file-map-directory" : "file-map-file";
      row.dataset.mcItemKey = entry.path || entry.name || "";
      row.dataset.mcComponentOwner = "code-editor.file-map.list";
      row.dataset.mcFeatureId = "code-editor.feature.file-map";
      row.style.paddingLeft = `${Math.min(depth, 10) * 14}px`;
      const expander = document.createElement("button");
      expander.type = "button";
      expander.className = "file-map-expander";
      expander.textContent = entry.kind === "dir" ? (fileExplorerOpen.has(entry.path) ? "-" : "+") : "";
      expander.disabled = entry.kind !== "dir";
      expander.addEventListener("click", () => toggleFileExplorerDir(entry.path));
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.disabled = entry.kind !== "file";
      checkbox.checked = fileMapMarked.has(entry.path);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          fileMapMarked.add(entry.path);
        } else {
          fileMapMarked.delete(entry.path);
        }
        syncAiderFilesFromMarked();
      });
      const name = document.createElement("span");
      name.title = entry.path || ".";
      name.textContent = `${entry.kind === "dir" ? "[dir] " : ""}${fromSearch ? entry.path : entry.name}`;
      const meta = document.createElement("span");
      meta.className = "file-map-meta";
      meta.textContent = entry.kind === "dir" ? "folder" : `${entry.bytes || 0}b`;
      row.append(expander, checkbox, name, meta);
      return row;
    }
    async function loadFileMap() {
      fileMapRefresh.disabled = true;
      const query = fileMapSearch.value.trim();
      fileMapStatus.textContent = query ? "searching files" : "loading directory";
      try {
        const response = await fetch("/api/applications/editor/files", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({repo_dir: aiderRepo.value || ".", path: "", query, limit: 500})
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        if (query) {
          renderFileMapSearchResults(data.files || []);
        } else {
          fileExplorerRoot = data.repo_dir;
          fileExplorerNodes = new Map();
          fileExplorerOpen = new Set([""]);
          setFileExplorerNode("", data.entries || [], true);
          renderFileMap();
        }
      } catch (error) {
        fileMapStatus.textContent = error.message || "file map failed";
        fileMapList.innerHTML = `<div class="file-map-empty">${escapeHtml(error.message || error)}</div>`;
      } finally {
        fileMapRefresh.disabled = false;
      }
    }
    async function toggleFileExplorerDir(path) {
      if (fileExplorerOpen.has(path)) {
        fileExplorerOpen.delete(path);
        renderFileMap();
        return;
      }
      fileExplorerOpen.add(path);
      if (!fileExplorerNodes.has(fileExplorerKey(path))) {
        await loadFileExplorerDir(path);
      } else {
        renderFileMap();
      }
    }
    async function loadFileExplorerDir(path) {
      fileMapStatus.textContent = `loading ${path || "."}`;
      try {
        const response = await fetch("/api/applications/editor/files", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({repo_dir: aiderRepo.value || ".", path, query: "", limit: 500})
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.error || `HTTP ${response.status}`);
        }
        setFileExplorerNode(data.path || "", data.entries || [], true);
        renderFileMap();
      } catch (error) {
        fileMapStatus.textContent = error.message || "directory load failed";
      }
    }
    function formatAiderResult(data) {
      const lines = [];
      lines.push(data.kind === "read" ? "editor read ready" : (data.ok ? "aider action ready" : "aider action failed"));
      lines.push(`repo: ${data.repo_dir || aiderRepo.value || "."}`);
      if (data.git_root) lines.push(`git root: ${data.git_root}`);
      lines.push(`dry run: ${Boolean(data.dry_run)}`);
      if (data.timeout_seconds !== null && data.timeout_seconds !== undefined) {
        lines.push(`timeout: ${data.timeout_seconds}s`);
      }
      if (data.duration_ms !== null && data.duration_ms !== undefined) {
        lines.push(`backend duration: ${formatDuration(data.duration_ms)}`);
      }
      if (data.frontend_duration_ms !== null && data.frontend_duration_ms !== undefined) {
        lines.push(`frontend duration: ${formatDuration(data.frontend_duration_ms)}`);
      }
      if (data.command) {
        lines.push("");
        lines.push("command:");
        lines.push(data.command.join(" "));
      }
      if (data.returncode !== null && data.returncode !== undefined) {
        lines.push("");
        lines.push(`exit: ${data.returncode} | ${data.duration_ms || 0}ms`);
      }
      if (data.stdout) {
        lines.push("");
        lines.push("stdout:");
        lines.push(data.stdout);
      }
      if (data.stderr) {
        lines.push("");
        lines.push("stderr:");
        lines.push(data.stderr);
      }
      if (data.error) {
        lines.push("");
        lines.push(`error: ${data.error}`);
      }
      return lines.join("\n");
    }
    function isAiderStartupWarningLine(line) {
      return String(line || "").trim().startsWith("Can't initialize prompt toolkit:");
    }
    function isAiderBannerLine(line) {
      return /^Aider v[\w.-]*/i.test(String(line || "").trim());
    }
    function isAiderHeaderDetailLine(line) {
      const trimmed = String(line || "").trim();
      return trimmed.startsWith("Model:")
        || trimmed.startsWith("Git repo:")
        || trimmed.startsWith("Warning:")
        || trimmed.startsWith("See:")
        || trimmed.startsWith("Repo-map:")
        || trimmed.startsWith("Added ") && trimmed.endsWith(" to the chat.")
        || trimmed.startsWith("Note:")
        || trimmed.startsWith("Cur working dir:")
        || trimmed.startsWith("Git working dir:")
        || trimmed.startsWith("Tokens:")
        || trimmed === "...";
    }
    function skipAiderStartupWarning(lines, startIndex) {
      if (!isAiderStartupWarningLine(lines[startIndex])) {
        return { index: startIndex, stripped: false };
      }
      let index = startIndex + 1;
      while (index < lines.length) {
        const trimmed = lines[index].trim();
        if (!trimmed || isAiderBannerLine(lines[index])) break;
        index += 1;
      }
      while (index < lines.length && !lines[index].trim()) {
        index += 1;
      }
      return { index, stripped: true };
    }
    function skipAiderBannerBlock(lines, startIndex) {
      if (!isAiderBannerLine(lines[startIndex])) {
        return { index: startIndex, stripped: false };
      }
      let index = startIndex + 1;
      let sawDetail = false;
      while (index < lines.length && isAiderHeaderDetailLine(lines[index])) {
        sawDetail = true;
        index += 1;
      }
      if (!sawDetail && (index >= lines.length || lines[index].trim())) {
        return { index: startIndex, stripped: false };
      }
      while (index < lines.length && !lines[index].trim()) {
        index += 1;
      }
      return { index, stripped: true };
    }
    function isAiderGitWorkingDirLine(line) {
      return String(line || "").trim().startsWith("Git working dir:");
    }
    function findAiderPreambleEnd(lines, startIndex) {
      for (let index = startIndex; index < lines.length; index += 1) {
        if (isAiderGitWorkingDirLine(lines[index])) {
          let nextIndex = index + 1;
          while (nextIndex < lines.length && !lines[nextIndex].trim()) {
            nextIndex += 1;
          }
          return nextIndex;
        }
      }
      return startIndex;
    }
    function stripInitialAiderPreamble(stdout) {
      const text = String(stdout || "").replace(/\r\n/g, "\n");
      const lines = text.split("\n");
      let index = 0;
      while (index < lines.length && !lines[index].trim()) {
        index += 1;
      }
      if (index >= lines.length) {
        return "";
      }
      if (
        !isAiderStartupWarningLine(lines[index]) &&
        !isAiderBannerLine(lines[index])
      ) {
        return text.trim();
      }
      const preambleEnd = findAiderPreambleEnd(lines, index);
      if (preambleEnd <= index) {
        return text.trim();
      }
      return lines.slice(preambleEnd).join("\n").trim();
    }

    function stripTrailingAiderTokenLine(stdout) {
      const text = String(stdout || "").replace(/\r\n/g, "\n");
      const lines = text.split("\n");
      let end = lines.length;
      while (end > 0 && !lines[end - 1].trim()) {
        end -= 1;
      }
      if (end > 0 && /^Tokens:\s+/i.test(lines[end - 1].trim())) {
        end -= 1;
        while (end > 0 && !lines[end - 1].trim()) {
          end -= 1;
        }
      }
      return lines.slice(0, end).join("\n").trim();
    }
    function cleanedAiderStdout(stdout) {
      const text = String(stdout || "").replace(/\r\n/g, "\n");
      const fenceIndex = text.indexOf("```diff");
      if (fenceIndex >= 0) return stripTrailingAiderTokenLine(text.slice(fenceIndex).trim());
      const diffIndex = text.search(/^diff --git|^@@ |^\+\+\+ |^--- /m);
      if (diffIndex >= 0) return stripTrailingAiderTokenLine(text.slice(diffIndex).trim());
      return stripTrailingAiderTokenLine(stripInitialAiderPreamble(text));
    }
    function userFacingAiderResult(data) {
      if (data.kind === "read") {
        return cleanedAiderStdout(data.stdout || "") || "No content returned.";
      }
      if (data.returncode === null || data.returncode === undefined) {
        return data.ok ? "Command preview is ready." : (data.error || "Command preview failed.");
      }
      const cleaned = cleanedAiderStdout(data.stdout || "");
      if (cleaned) return cleaned;
      if (data.ok && data.dry_run) return "Dry run completed. No changes were applied.";
      if (data.ok) return "Aider completed.";
      return data.error || data.stderr || "Aider failed.";
    }
    function renderAiderResult(data) {
      const result = userFacingAiderResult(data);
      const activity = formatAiderResult(data);
      aiderOutput.innerHTML = [
        `<div class="aider-result">${escapeHtml(result)}</div>`,
        `<details class="aider-console"><summary>Activity console</summary><pre>${escapeHtml(activity)}</pre></details>`
      ].join("");
    }
    function aiderContextActivities() {
      return Array.isArray(aiderContextState.activities) ? aiderContextState.activities : [];
    }
    function aiderActivityIsRunning(activity) {
      return ["queued", "running"].includes(String(activity && activity.status || "").toLowerCase());
    }
