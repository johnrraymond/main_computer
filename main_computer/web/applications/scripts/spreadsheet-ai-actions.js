    const SPREADSHEET_AI_RANGE_CONTEXT_MAX_CELLS = 200;

    function spreadsheetAiRangeLabel(range = spreadsheetSelectedRange) {
      if (!range?.start) return "";
      return range.start === range.end ? range.start : `${range.start}:${range.end}`;
    }

    function spreadsheetAiSelectedRange() {
      if (spreadsheetSelectedRange?.start) return spreadsheetSelectedRange;
      const ref = typeof spreadsheetSelectedRef === "function" ? spreadsheetSelectedRef() : "";
      return ref ? spreadsheetRangeRefs(ref, ref) : null;
    }

    function spreadsheetAiSafeCellValue(cell) {
      if (!cell) return "";
      if (cell.value !== undefined) return String(cell.value ?? "");
      if (cell.source !== undefined && spreadsheetIsCodeCell(cell)) return "";
      return "";
    }

    function spreadsheetAiCellSnapshot(ref) {
      const cell = spreadsheetGetCell(ref, false);
      const normalized = cell ? spreadsheetNormalizeCell(cell) : spreadsheetNormalizeCell({});
      const item = {
        ref,
        value: spreadsheetAiSafeCellValue(normalized),
        kind: normalized.kind || "value",
      };
      if (spreadsheetIsCodeCell(normalized)) {
        item.language = normalized.language || normalized.kind || "javascript";
        item.status = normalized.status || "clean";
        item.has_source = Boolean(String(normalized.source || "").trim());
        if (normalized.dependencies?.length) item.dependencies = normalized.dependencies.slice(0, 20);
      }
      return item;
    }

    function spreadsheetAiHeaderSnapshots(range) {
      if (!range?.start) return [];
      const rows = [];
      const seen = new Set();
      const addRow = (row, role) => {
        if (row < 1 || seen.has(`${role}:${row}`)) return;
        seen.add(`${role}:${row}`);
        const cells = [];
        for (let col = range.colMin; col <= range.colMax; col += 1) {
          const ref = spreadsheetCellRef(row, col);
          cells.push(spreadsheetAiCellSnapshot(ref));
        }
        rows.push({role, row, cells});
      };
      if (range.rowMin > 1) addRow(range.rowMin - 1, "row_above_selection");
      addRow(range.rowMin, "first_selected_row");
      return rows;
    }

    function spreadsheetBuildAiRangeContext(userRequest = "", options = {}) {
      const range = options.range || spreadsheetAiSelectedRange();
      if (!range?.start) {
        throw new Error("Select a spreadsheet range before staging an AI range action.");
      }
      const maxCells = Math.max(1, Number(options.maxCells) || SPREADSHEET_AI_RANGE_CONTEXT_MAX_CELLS);
      const sheet = spreadsheetActiveSheet();
      const cells = [];
      const allRefs = Array.isArray(range.cells) ? range.cells : [];
      allRefs.slice(0, maxCells).forEach((ref) => cells.push(spreadsheetAiCellSnapshot(ref)));
      const context = {
        kind: "spreadsheet-ai-range-action",
        version: 1,
        workbook_path: spreadsheetPath || "untitled.json",
        active_sheet: spreadsheetWorkbook?.active_sheet || "Sheet1",
        selected_range: {
          label: spreadsheetAiRangeLabel(range),
          start: range.start,
          end: range.end,
          row_count: Math.max(1, range.rowMax - range.rowMin + 1),
          col_count: Math.max(1, range.colMax - range.colMin + 1),
          cell_count: allRefs.length,
        },
        sheet_size: {
          rows: Math.max(1, Number(sheet?.rows) || 1),
          cols: Math.max(1, Number(sheet?.cols) || 1),
        },
        cells,
        context_truncated: allRefs.length > cells.length,
        omitted_cell_count: Math.max(0, allRefs.length - cells.length),
        nearby_headers: spreadsheetAiHeaderSnapshots(range),
        user_request: String(userRequest || "").trim(),
        allowed_api: [
          "sheet.get(ref)",
          "sheet.getNumber(ref)",
          "sheet.range(range)",
          "sheet.write(ref, value)",
          "sheet.writeRange(range, values)",
        ],
        safety_rules: [
          "Return code only; the user will review it before running.",
          "Do not call fetch, window, document, localStorage, XMLHttpRequest, importScripts, or browser APIs.",
          "Use sheet.write or sheet.writeRange for proposed changes so the spreadsheet shows a write preview.",
          "Do not save the workbook or claim that changes were applied.",
        ],
      };
      return context;
    }

    function spreadsheetBuildAiRangePrompt(userRequest = "") {
      const context = spreadsheetBuildAiRangeContext(userRequest);
      return [
        "You are generating a JavaScript code cell for this spreadsheet app.",
        "",
        "Return exactly one fenced `javascript` code block and no extra prose.",
        "The user will review the generated code, run it manually, inspect the write preview, and then choose whether to apply writes.",
        "",
        "Use only these spreadsheet APIs:",
        "- sheet.get(ref)",
        "- sheet.getNumber(ref)",
        "- sheet.range(range)",
        "- sheet.write(ref, value)",
        "- sheet.writeRange(range, values)",
        "",
        "Do not use browser, network, storage, or DOM APIs. Do not save the workbook. Do not claim execution.",
        "Prefer clear, small code that reads from the selected range and writes results outside or inside that range only when the user request implies it.",
        "",
        "Spreadsheet context:",
        "```json",
        JSON.stringify(context, null, 2),
        "```",
      ].join("\n");
    }

    function spreadsheetRenderAiRangeContextPreview(context = null) {
      const target = spreadsheetAiRangeContextPreview || document.querySelector("#spreadsheet-ai-range-context-preview");
      if (!target) return;
      let nextContext = context;
      if (!nextContext) {
        try {
          nextContext = spreadsheetBuildAiRangeContext(spreadsheetAiRangeRequest?.value || "");
        } catch {
          target.textContent = "Select a range to preview the AI context.";
          return;
        }
      }
      const range = nextContext.selected_range || {};
      const truncated = nextContext.context_truncated ? ` · ${nextContext.omitted_cell_count} cells omitted` : "";
      target.textContent = `${range.label || "range"} · ${nextContext.cells.length}/${range.cell_count || nextContext.cells.length} cells included${truncated}`;
    }

    function spreadsheetAiRangeStatus(message) {
      const target = spreadsheetAiRangeStatusNode || document.querySelector("#spreadsheet-ai-range-status");
      if (target) target.textContent = message || "";
      if (message && spreadsheetStatus) spreadsheetStatus.textContent = message;
    }

    async function spreadsheetCopyAiRangeContext() {
      try {
        const context = spreadsheetBuildAiRangeContext(spreadsheetAiRangeRequest?.value || "");
        await navigator.clipboard?.writeText?.(JSON.stringify(context, null, 2));
        spreadsheetRenderAiRangeContextPreview(context);
        spreadsheetAiRangeStatus("AI range context copied");
        return context;
      } catch (error) {
        spreadsheetAiRangeStatus(error?.message || "AI range context copy failed");
        return null;
      }
    }

    function spreadsheetStageAiRangePromptInChat() {
      const request = String(spreadsheetAiRangeRequest?.value || "").trim();
      if (!request) {
        spreadsheetAiRangeStatus("Describe what the AI should do to the selected range.");
        spreadsheetAiRangeRequest?.focus?.();
        return null;
      }
      let context;
      let prompt;
      try {
        context = spreadsheetBuildAiRangeContext(request);
        prompt = spreadsheetBuildAiRangePrompt(request);
      } catch (error) {
        spreadsheetAiRangeStatus(error?.message || "Could not build AI range context.");
        return null;
      }
      if (typeof spreadsheetMountChatThreadController === "function") spreadsheetMountChatThreadController();
      if (typeof addChatConsoleCell !== "function" || !chatConsoleState) {
        spreadsheetAiRangeStatus("embedded Chat Console is not ready");
        return null;
      }
      const afterId = chatConsoleState?.selected_cell_id || "";
      const cell = addChatConsoleCell("ai", prompt, afterId);
      if (cell) {
        cell.metadata = {
          ...(cell.metadata && typeof cell.metadata === "object" ? cell.metadata : {}),
          spreadsheet_ai_range_action: {
            origin_app: "spreadsheet-ai-range-action",
            workbook_path: context.workbook_path,
            active_sheet: context.active_sheet,
            user_request: request,
            selected_range: context.selected_range,
            context_truncated: context.context_truncated,
            omitted_cell_count: context.omitted_cell_count,
            staged_at: typeof spreadsheetChatNow === "function" ? spreadsheetChatNow() : new Date().toISOString(),
          },
        };
        if (typeof saveChatConsoleState === "function") saveChatConsoleState("AI range prompt staged");
        if (typeof renderChatConsoleNotebook === "function") renderChatConsoleNotebook();
      }
      spreadsheetRenderAiRangeContextPreview(context);
      spreadsheetAiRangeStatus(`AI range prompt staged for ${context.selected_range.label}; run it in Chat Console, then import the snippet into a code cell.`);
      return cell;
    }
