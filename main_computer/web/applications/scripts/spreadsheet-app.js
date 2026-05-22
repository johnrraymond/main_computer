    async function initSpreadsheetApp() {
      if (spreadsheetInitialized) {
        spreadsheetStatus.textContent = spreadsheetDirty ? "local edits pending disk save" : "disk grid ready";
        return;
      }
      spreadsheetInitialized = true;
      spreadsheetRefresh.addEventListener("click", () => loadSpreadsheetFiles().catch((error) => spreadsheetStatus.textContent = error.message));
      spreadsheetNew.addEventListener("click", () => {
        const path = prompt("New spreadsheet path", "untitled.json") || "";
        if (path) createSpreadsheet(path).catch((error) => spreadsheetStatus.textContent = error.message);
      });
      spreadsheetOpen.addEventListener("click", () => {
        if (spreadsheetSelectedPath) openSpreadsheet(spreadsheetSelectedPath).catch((error) => spreadsheetStatus.textContent = error.message);
      });
      spreadsheetImportXlsx.addEventListener("click", () => {
        spreadsheetImportXlsxFile.value = "";
        spreadsheetImportXlsxFile.click();
      });
      spreadsheetImportXlsxFile.addEventListener("change", () => {
        const file = spreadsheetImportXlsxFile.files?.[0];
        if (file) importSpreadsheetXlsxFile(file).catch((error) => spreadsheetStatus.textContent = error.message);
      });
      spreadsheetSave.addEventListener("click", () => saveSpreadsheet().catch((error) => spreadsheetStatus.textContent = error.status === 409 ? "save conflict: reload before overwriting" : error.message));
      spreadsheetSaveAs.addEventListener("click", () => {
        const path = prompt("Save spreadsheet as", spreadsheetPath.replace(/\.json$/i, "-copy.json")) || "";
        if (path) saveSpreadsheet(path).catch((error) => spreadsheetStatus.textContent = error.message);
      });
      spreadsheetExportCsv.addEventListener("click", async () => {
        try {
          const data = await spreadsheetApi("/api/applications/spreadsheet/export-csv", {path: spreadsheetPath, sheet: spreadsheetWorkbook?.active_sheet || "Sheet1"});
          spreadsheetPlotStatus.textContent = `CSV ready: ${data.filename} (${data.content.length} chars)`;
        } catch (error) {
          spreadsheetPlotStatus.textContent = error.message;
        }
      });
      spreadsheetExportXlsx?.addEventListener("click", () => {
        exportSpreadsheetXlsx().catch((error) => {
          spreadsheetPlotStatus.textContent = error.message;
        });
      });
      spreadsheetAddSheet?.addEventListener("click", () => spreadsheetPromptAddSheet());
      spreadsheetSheetAdd?.addEventListener("click", () => spreadsheetPromptAddSheet());
      spreadsheetSheetRename?.addEventListener("click", () => spreadsheetPromptRenameSheet());
      spreadsheetSheetDuplicate?.addEventListener("click", () => spreadsheetPromptDuplicateSheet());
      spreadsheetSheetDelete?.addEventListener("click", () => spreadsheetPromptDeleteSheet());
      spreadsheetPlotSelection.addEventListener("click", plotSpreadsheetSelection);
      spreadsheetClearSelection.addEventListener("click", clearSpreadsheetSelection);
      spreadsheetCellType.addEventListener("change", () => {
        const ref = spreadsheetSelectedRef();
        if (ref) spreadsheetSetCellType(ref, spreadsheetCellType.value);
      });
      spreadsheetCellSource.addEventListener("input", () => {
        const ref = spreadsheetSelectedRef();
        const cell = spreadsheetGetCell(ref, true);
        if (!cell) return;
        const isFormula = typeof spreadsheetIsFormulaCell === "function" && spreadsheetIsFormulaCell(cell);
        if (isFormula) {
          const formulaSource = typeof spreadsheetNormalizeFormulaSource === "function" ? spreadsheetNormalizeFormulaSource(spreadsheetCellSource.value) : spreadsheetCellSource.value;
          const normalized = typeof spreadsheetNormalizeFormulaCell === "function"
            ? spreadsheetNormalizeFormulaCell({...cell, source: formulaSource, value: cell.value ?? "", status: "dirty"})
            : cell;
          Object.assign(cell, normalized, {kind: "formula", language: "none", status: "dirty"});
          spreadsheetSetDirty(true, "formula source changed");
          if (typeof spreadsheetRefreshFormulaResults === "function") spreadsheetRefreshFormulaResults({preserveSelection: true});
          spreadsheetRefreshCellElement(ref);
          spreadsheetCodeStatus.textContent = typeof spreadsheetFormulaStatusText === "function" ? spreadsheetFormulaStatusText(cell, spreadsheetWorkbook?.active_sheet || "Sheet1", ref) : "formula: dirty";
          return;
        }
        if (!spreadsheetIsCodeCell(cell)) return;
        cell.source = spreadsheetCellSource.value;
        cell.status = "dirty";
        spreadsheetSetDirty(true, "code cell source changed");
        spreadsheetRefreshCellElement(ref);
        spreadsheetCodeStatus.textContent = "status: dirty";
      });
      spreadsheetRunCell.addEventListener("click", () => runSpreadsheetSelectedCell());
      spreadsheetApplyWrites.addEventListener("click", applySpreadsheetWritePreview);
      spreadsheetAiRangeGenerate?.addEventListener("click", () => spreadsheetStageAiRangePromptInChat());
      spreadsheetAiRangeCopyContext?.addEventListener("click", () => spreadsheetCopyAiRangeContext());
      spreadsheetAiRangeRequest?.addEventListener("input", () => spreadsheetRenderAiRangeContextPreview());
      document.addEventListener("pointerup", () => { spreadsheetDragSelecting = false; });
      await loadSpreadsheetFiles().catch((error) => {
        spreadsheetWorkbook = spreadsheetDefaultWorkbook();
        renderDiskSpreadsheet();
        spreadsheetSetDirty(true, `disk unavailable: ${error.message}`);
      });
      await spreadsheetMaybeImportChatVariablesFromUrl().catch((error) => {
        spreadsheetStatus.textContent = error.message || "chat variable import failed";
      });
      if (typeof spreadsheetInitChatThreadIntegration === "function") spreadsheetInitChatThreadIntegration();
    }
    function spreadsheetChatVariablesBlobFromUrl() {
      try {
        return (new URLSearchParams(window.location.search).get("chat_vars") || "").trim();
      } catch {
        return "";
      }
    }
    function spreadsheetClearChatVariablesBlobFromUrl() {
      try {
        const url = new URL(window.location.href);
        if (!url.searchParams.has("chat_vars")) return;
        url.searchParams.delete("chat_vars");
        const next = `${url.pathname}${url.search}${url.hash}`;
        window.history.replaceState({app: "spreadsheet"}, "", next);
      } catch {
        // Leave the URL alone if the browser refuses history replacement.
      }
    }
    async function spreadsheetMaybeImportChatVariablesFromUrl() {
      const blobId = spreadsheetChatVariablesBlobFromUrl();
      if (!blobId) return null;
      spreadsheetStatus.textContent = "loading chat shared variables...";
      const data = await spreadsheetApi("/api/applications/spreadsheet/import-chat-variables", {blob_id: blobId});
      spreadsheetWorkbook = spreadsheetNormalizeLoadedWorkbook(data.workbook || spreadsheetDefaultWorkbook());
      spreadsheetPath = data.path || spreadsheetPath;
      spreadsheetContentHash = data.content_hash || "";
      spreadsheetSelectedPath = spreadsheetPath;
      spreadsheetSelectionAnchor = null;
      spreadsheetSelectedRange = null;
      renderDiskSpreadsheet();
      spreadsheetSetDirty(false, `loaded ${data.count || 0} chat shared variables`);
      if (typeof spreadsheetMountChatThreadController === "function") spreadsheetMountChatThreadController();
      spreadsheetClearChatVariablesBlobFromUrl();
      await loadSpreadsheetFiles();
      return data;
    }

    function loadTerminalHistory() {
      try {
        const parsed = JSON.parse(localStorage.getItem("main-computer-terminal-history-v1") || "[]");
        return Array.isArray(parsed) ? parsed.filter((item) => typeof item === "string") : [];
      } catch (error) {
        return [];
      }
    }
    function saveTerminalHistory(history) {
      localStorage.setItem("main-computer-terminal-history-v1", JSON.stringify(history.slice(0, 30)));
    }
    function addTerminalHistory(command) {
      terminalHistory = terminalHistory.filter((item) => item !== command);
      terminalHistory.unshift(command);
      terminalHistory = terminalHistory.slice(0, 30);
      terminalHistoryCursor = terminalHistory.length;
      saveTerminalHistory(terminalHistory);
    }
    function terminalPrompt() {
      return `PS ${terminalCwd.value || "."}> `;
    }
    function writePrompt() {
      if (!xterm) return;
      terminalBuffer = "";
      terminalHistoryCursor = -1;
      xterm.write(terminalPrompt());
    }
    function redrawTerminalInput() {
      if (!xterm) return;
      xterm.write(`\r\x1b[2K${terminalPrompt()}${terminalBuffer}`);
    }
    function setTerminalAiStatus(text, risk = "") {
      if (!terminalAiStatus) return;
      terminalAiStatus.textContent = text;
      if (risk) terminalAiStatus.dataset.risk = risk;
      else delete terminalAiStatus.dataset.risk;
    }
    function stageTerminalCommand(command, cwd, meta = {}) {
      if (!xterm || terminalBusy) return;
      if (cwd) terminalCwd.value = cwd;
      terminalBuffer = String(command || "");
      terminalHistoryCursor = -1;
      redrawTerminalInput();
      const risk = String(meta.risk || "unknown");
      const description = meta.description ? `${meta.description} ` : "";
      setTerminalAiStatus(`${description}Risk: ${risk}. Review the staged command, then press Enter to run it.`, risk);
      xterm.focus();
    }
    function fitXterm() {
      if (xtermFit && terminalApp.style.display !== "none") {
        try {
          xtermFit.fit();
        } catch (error) {
          // Fitting can fail while the app is hidden.
        }
      }
    }
