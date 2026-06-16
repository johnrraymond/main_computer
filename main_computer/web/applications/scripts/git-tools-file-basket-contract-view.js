(function (global) {
  "use strict";

  const VERSION = "0.2.0";
  const SURFACE_ID = "git-tools.file-basket.contract-treegrid";
  const CONTRACT_ID = "pattern.file-basket";
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-file-basket-contract-view.js";
  const TOOLKIT_PRIMITIVES = Object.freeze({
    collectionView: "collection.treegrid",
    selectionControl: "control.selection.tristate",
    disclosureControl: "control.disclosure",
    pathCell: "cell.path",
    statusCell: "cell.status",
    riskCell: "cell.risk",
    reasonCell: "cell.reason",
    expansionController: "controller.expansion",
    selectionController: "controller.selection"
  });

  function escapeHtml(value = "") {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function uniqueSorted(values = []) {
    return Array.from(new Set(asArray(values).filter(Boolean)))
      .sort((left, right) => String(left).localeCompare(String(right)));
  }

  function modelAdapter() {
    return global.McelFileBasketModel || null;
  }

  function controllerAdapter() {
    return global.McelFileBasketController || null;
  }

  function normalizeRepoPath(value = "") {
    const adapter = modelAdapter();
    if (typeof adapter?.normalizeRepoPath === "function") {
      return adapter.normalizeRepoPath(value);
    }
    const raw = String(value || "").replace(/\\/g, "/").trim();
    if (!raw || /^[A-Za-z]:\//.test(raw) || raw.startsWith("//")) return "";
    const withoutRepoRoot = raw.replace(/^\/+/, "").replace(/^main_computer_test\//, "");
    const parts = withoutRepoRoot.split("/").filter(Boolean);
    if (!parts.length || parts.some((part) => part === "." || part === "..")) return "";
    return parts.join("/");
  }

  function normalizeSelection(fileBasketModel = {}, selectedPaths = []) {
    const controller = controllerAdapter();
    if (typeof controller?.selectedOutput === "function") {
      return controller.selectedOutput(fileBasketModel, selectedPaths);
    }
    const adapter = modelAdapter();
    if (typeof adapter?.selectedOutput === "function") {
      return adapter.selectedOutput(fileBasketModel, selectedPaths);
    }
    const selectable = new Set(asArray(fileBasketModel.selectablePaths).map(normalizeRepoPath).filter(Boolean));
    return uniqueSorted(asArray(selectedPaths).map(normalizeRepoPath).filter((path) => selectable.has(path)));
  }

  function createController(fileBasketModel = {}, selectedPaths = []) {
    const controller = controllerAdapter();
    if (typeof controller?.createFileBasketController === "function") {
      return controller.createFileBasketController(fileBasketModel, {selectedPaths});
    }
    return {
      model: fileBasketModel,
      selectedPaths: () => normalizeSelection(fileBasketModel, selectedPaths),
      selectedOutput: (paths = selectedPaths) => normalizeSelection(fileBasketModel, paths),
      apply(command = "", payload = {}) {
        const selected = normalizeSelection(fileBasketModel, selectedPaths);
        const path = normalizeRepoPath(payload.path || payload.directoryPath || payload.filePath || "");
        const selectable = new Set(asArray(fileBasketModel.selectablePaths));
        if (command === "set-file-selection" && selectable.has(path)) {
          if (payload.selected === false) selected.splice(selected.indexOf(path), 1);
          else if (!selected.includes(path)) selected.push(path);
        }
        return {
          ok: true,
          command,
          selectedPaths: normalizeSelection(fileBasketModel, selected),
          output: normalizeSelection(fileBasketModel, selected),
          summary: selectionSummary(fileBasketModel, selected)
        };
      },
      selectableDescendantPaths(path = "") {
        return selectableDescendantPaths(fileBasketModel, path);
      },
      deriveDirectorySelectionState(paths = selectedPaths, path = "") {
        return directorySelectionState(fileBasketModel, paths, path);
      },
      selectionSummary(paths = selectedPaths) {
        return selectionSummary(fileBasketModel, paths);
      }
    };
  }

  function selectableDescendantPaths(fileBasketModel = {}, directoryPath = "") {
    const controller = controllerAdapter();
    if (typeof controller?.selectableDescendantPaths === "function") {
      return controller.selectableDescendantPaths(fileBasketModel, directoryPath);
    }
    const adapter = modelAdapter();
    if (typeof adapter?.selectableDescendantPaths === "function") {
      return adapter.selectableDescendantPaths(fileBasketModel, directoryPath);
    }
    const directory = normalizeRepoPath(directoryPath);
    const prefix = directory ? `${directory}/` : "";
    return uniqueSorted(asArray(fileBasketModel.rows)
      .filter((row = {}) => row.kind === "file" && row.selectable && (!directory || row.path === directory || row.path.startsWith(prefix)))
      .map((row = {}) => row.path));
  }

  function directorySelectionState(fileBasketModel = {}, selectedPaths = [], directoryPath = "") {
    const controller = controllerAdapter();
    if (typeof controller?.deriveDirectorySelectionState === "function") {
      return controller.deriveDirectorySelectionState(fileBasketModel, selectedPaths, directoryPath);
    }
    const descendants = selectableDescendantPaths(fileBasketModel, directoryPath);
    if (!descendants.length) return "none";
    const selected = new Set(normalizeSelection(fileBasketModel, selectedPaths));
    const selectedCount = descendants.filter((path) => selected.has(path)).length;
    if (selectedCount === 0) return "none";
    if (selectedCount === descendants.length) return "all";
    return "mixed";
  }

  function selectionSummary(fileBasketModel = {}, selectedPaths = []) {
    const controller = controllerAdapter();
    if (typeof controller?.selectionSummary === "function") {
      return controller.selectionSummary(fileBasketModel, selectedPaths);
    }
    const selected = normalizeSelection(fileBasketModel, selectedPaths);
    const blocked = new Set(asArray(fileBasketModel.blockedPaths));
    return {
      total: asArray(fileBasketModel.rows).length,
      selectable: asArray(fileBasketModel.selectablePaths).length,
      blocked: asArray(fileBasketModel.blockedPaths).length,
      selected: selected.length,
      selectedPaths: selected,
      selectedBlocked: selected.filter((path) => blocked.has(path)).length,
      invalidSelectedPaths: uniqueSorted(asArray(selectedPaths).map(normalizeRepoPath).filter((path) => path && !selected.includes(path)))
    };
  }

  function fileRowByPath(fileBasketModel = {}) {
    const byPath = new Map();
    asArray(fileBasketModel.rows).forEach((row = {}) => {
      if (row.path) byPath.set(row.path, row);
    });
    return byPath;
  }

  function childIdsForNode(node = {}, out = []) {
    asArray(node.children).forEach((child = {}) => {
      const childKind = child.kind === "dir" ? "directory" : child.kind || "file";
      const childPath = child.path || "";
      out.push(`${childKind === "directory" ? "dir" : "file"}:${childPath}`);
    });
    return out;
  }

  function rowCells(row = {}, kind = "file") {
    return {
      path: {
        type: "path",
        value: row.path || row.repoRelativePath || "",
        primary: true,
        primitive: TOOLKIT_PRIMITIVES.pathCell
      },
      status: {
        type: "enum",
        value: kind === "directory" ? "directory" : (row.status || "unknown"),
        label: kind === "directory" ? "directory" : (row.statusLabel || row.status || "unknown"),
        primitive: TOOLKIT_PRIMITIVES.statusCell
      },
      risk: {
        type: "risk",
        value: row.risk || (row.blocked ? "blocked" : row.bucketTone || "review"),
        primitive: TOOLKIT_PRIMITIVES.riskCell
      },
      reason: {
        type: "text",
        value: row.reason || row.blockedReason || "",
        primitive: TOOLKIT_PRIMITIVES.reasonCell
      }
    };
  }

  function mapSelectionState(state = "none", blocked = false) {
    if (blocked) return "blocked";
    if (state === "all" || state === "checked" || state === true) return "checked";
    if (state === "mixed") return "mixed";
    return "unchecked";
  }

  function buildRowsFromHierarchy(nodes = [], fileBasketModel = {}, selected = [], rows = []) {
    const selectedSet = new Set(normalizeSelection(fileBasketModel, selected));
    const files = fileRowByPath(fileBasketModel);
    asArray(nodes).forEach((node = {}) => {
      const isDirectory = node.kind === "dir" || node.kind === "directory";
      const kind = isDirectory ? "directory" : "file";
      const repoRelativePath = normalizeRepoPath(node.path || "");
      const sourceRow = kind === "file" ? (files.get(repoRelativePath) || node) : node;
      const selectable = kind === "directory"
        ? selectableDescendantPaths(fileBasketModel, repoRelativePath).length > 0
        : sourceRow.selectable !== false;
      const blocked = kind === "file"
        ? Boolean(sourceRow.blocked || sourceRow.selectable === false)
        : !selectable;
      const directoryState = kind === "directory"
        ? directorySelectionState(fileBasketModel, selected, repoRelativePath)
        : "none";
      const fileState = kind === "file" && selectedSet.has(repoRelativePath) ? "checked" : "unchecked";
      const selectionState = kind === "directory"
        ? mapSelectionState(directoryState, blocked)
        : mapSelectionState(fileState, blocked);
      const childIds = childIdsForNode(node);
      const row = {
        id: `${kind === "directory" ? "dir" : "file"}:${repoRelativePath}`,
        contractId: CONTRACT_ID,
        surfaceId: SURFACE_ID,
        repoRelativePath,
        path: repoRelativePath,
        name: node.name || sourceRow.name || repoRelativePath.split("/").pop() || "Candidate files",
        kind,
        status: kind === "directory" ? "directory" : (sourceRow.status || "unknown"),
        statusLabel: kind === "directory" ? "directory" : (sourceRow.statusLabel || sourceRow.status || "unknown"),
        statusSymbol: kind === "directory" ? "▸" : (sourceRow.statusSymbol || "·"),
        risk: sourceRow.risk || sourceRow.bucketTone || "",
        reason: sourceRow.reason || "",
        blockedReason: sourceRow.blockedReason || "",
        selectable,
        blocked,
        visible: true,
        depth: Number.isFinite(Number(sourceRow.depth ?? node.depth)) ? Number(sourceRow.depth ?? node.depth) : 0,
        parentPath: normalizeRepoPath(sourceRow.parentPath || node.parentPath || ""),
        children: childIds,
        childCount: childIds.length,
        selectableDescendantPaths: kind === "directory" ? selectableDescendantPaths(fileBasketModel, repoRelativePath) : [],
        selectionState,
        selected: selectionState === "checked",
        selectedOutputPath: kind === "file" && selectionState === "checked" ? repoRelativePath : "",
        cells: rowCells({...sourceRow, path: repoRelativePath, blocked}, kind),
        toolkitPrimitives: {
          collection: TOOLKIT_PRIMITIVES.collectionView,
          selection: TOOLKIT_PRIMITIVES.selectionControl,
          disclosure: kind === "directory" ? TOOLKIT_PRIMITIVES.disclosureControl : "",
          cells: [TOOLKIT_PRIMITIVES.pathCell, TOOLKIT_PRIMITIVES.statusCell, TOOLKIT_PRIMITIVES.riskCell, TOOLKIT_PRIMITIVES.reasonCell].filter(Boolean)
        },
        commands: kind === "directory"
          ? ["set-directory-selection", "toggle-directory", "expand", "collapse"]
          : selectable
            ? ["set-file-selection", "toggle-file"]
            : []
      };
      rows.push(row);
      buildRowsFromHierarchy(node.children, fileBasketModel, selected, rows);
    });
    return rows;
  }

  function buildContractTreegridRows(fileBasketModel = {}, options = {}) {
    const selectedPaths = normalizeSelection(
      fileBasketModel,
      Array.isArray(options.selectedPaths) ? options.selectedPaths : fileBasketModel.defaultSelectedPaths
    );
    const hierarchy = asArray(fileBasketModel.hierarchy);
    return buildRowsFromHierarchy(hierarchy, fileBasketModel, selectedPaths);
  }

  function selectedOutputFromContractRows(fileBasketModel = {}, rows = []) {
    const paths = asArray(rows)
      .filter((row = {}) => row.kind === "file" && row.selectionState === "checked" && row.selectable !== false)
      .map((row = {}) => row.repoRelativePath || row.path)
      .filter(Boolean);
    return normalizeSelection(fileBasketModel, paths);
  }

  function compareLegacyAndContractSelection(fileBasketModel = {}, legacySelectedPaths = [], options = {}) {
    const legacyOutput = normalizeSelection(fileBasketModel, legacySelectedPaths);
    const contractRows = buildContractTreegridRows(fileBasketModel, {selectedPaths: options.contractSelectedPaths || legacyOutput});
    const contractOutput = selectedOutputFromContractRows(fileBasketModel, contractRows);
    return {
      contractId: fileBasketModel.contractId || CONTRACT_ID,
      legacyOutput,
      contractOutput,
      matches: JSON.stringify(legacyOutput) === JSON.stringify(contractOutput),
      selectedCount: contractOutput.length,
      invalidLegacyPaths: uniqueSorted(asArray(legacySelectedPaths).map(normalizeRepoPath).filter((path) => path && !legacyOutput.includes(path)))
    };
  }

  function summarizeContractTreegridReadiness(fileBasketModel = {}, options = {}) {
    const selectedPaths = normalizeSelection(
      fileBasketModel,
      Array.isArray(options.selectedPaths) ? options.selectedPaths : fileBasketModel.defaultSelectedPaths
    );
    const rows = buildContractTreegridRows(fileBasketModel, {selectedPaths});
    const files = rows.filter((row = {}) => row.kind === "file");
    const directories = rows.filter((row = {}) => row.kind === "directory");
    const blockedRows = rows.filter((row = {}) => row.blocked);
    const selectableRows = rows.filter((row = {}) => row.selectable);
    const treegridEligibility = asArray(fileBasketModel.viewContract?.eligibleViews).some((view = {}) => view.viewId === "contract-treegrid" || view.id === "contract-treegrid");
    const titleOnlyRejected = Boolean(fileBasketModel.viewContract?.titleOnlyTreeRejected) ||
      asArray(fileBasketModel.viewContract?.rejectedViews).some((view = {}) => view.viewId === "title-only-tree" || view.id === "title-only-tree");
    const comparison = compareLegacyAndContractSelection(fileBasketModel, options.legacySelectedPaths || selectedPaths, {contractSelectedPaths: selectedPaths});
    const blockedRowsVisible = blockedRows.every((row = {}) => row.visible === true);
    const blockedRowsSelectable = blockedRows.some((row = {}) => row.selectable !== false);
    const selectedBlocked = comparison.contractOutput.filter((path) => asArray(fileBasketModel.blockedPaths).includes(path));
    return {
      ready: Boolean(
        fileBasketModel.contractId === CONTRACT_ID &&
        rows.length &&
        files.length === asArray(fileBasketModel.rows).length &&
        directories.length > 0 &&
        treegridEligibility &&
        titleOnlyRejected &&
        blockedRowsVisible &&
        !blockedRowsSelectable &&
        selectedBlocked.length === 0 &&
        comparison.matches
      ),
      version: VERSION,
      surfaceId: SURFACE_ID,
      contractId: fileBasketModel.contractId || CONTRACT_ID,
      visibleRenderer: options.visibleRenderer || "legacy-wunderbaum",
      legacyRendererActive: options.legacyRendererActive !== false,
      legacyRollbackAvailable: options.legacyRollbackAvailable !== false,
      prepared: true,
      activeReplacement: options.activeReplacement === true,
      treegridEligible: treegridEligibility,
      titleOnlyTreeRejected: titleOnlyRejected,
      rowCount: rows.length,
      fileRowCount: files.length,
      directoryRowCount: directories.length,
      selectableRowCount: selectableRows.length,
      blockedRowCount: blockedRows.length,
      blockedRowsVisible,
      blockedRowsSelectable,
      selectedOutputMatchesLegacy: comparison.matches,
      selectedOutput: comparison.contractOutput,
      selectedBlockedPaths: selectedBlocked,
      directorySelectionControllerOwned: Boolean(controllerAdapter()?.createFileBasketController),
      toolkitPrimitives: {...TOOLKIT_PRIMITIVES},
      comparison
    };
  }

  function ariaCheckedForState(state = "unchecked") {
    if (state === "mixed") return "mixed";
    if (state === "checked") return "true";
    return "false";
  }

  function contractRowHtml(row = {}) {
    const isDirectory = row.kind === "directory";
    const disabled = row.selectable === false || row.selectionState === "blocked";
    const checked = row.selectionState === "checked";
    const mixed = row.selectionState === "mixed";
    const label = row.repoRelativePath || row.name || "Candidate files";
    const reason = row.blockedReason || row.reason || "";
    return `<div class="git-project-contract-treegrid-row ${isDirectory ? "is-directory" : "is-file"} ${row.blocked ? "is-blocked" : ""}" role="row"
        data-git-commit-contract-row="${escapeHtml(row.kind)}"
        data-git-commit-contract-path="${escapeHtml(row.repoRelativePath)}"
        data-git-commit-contract-parent="${escapeHtml(row.parentPath || "")}"
        data-git-commit-contract-depth="${Number(row.depth || 0)}"
        data-git-commit-contract-visible="true"
        data-git-commit-contract-selection-state="${escapeHtml(row.selectionState)}"
        ${isDirectory ? `aria-expanded="true" data-git-commit-contract-expanded="true"` : ""}
        style="--git-tree-depth:${Number(row.depth || 0)}">
      <div class="git-project-contract-treegrid-cell is-path" role="gridcell">
        ${isDirectory ? `<button type="button" class="git-project-contract-treegrid-disclosure" data-git-commit-contract-disclosure="${escapeHtml(row.repoRelativePath)}" aria-label="Collapse ${escapeHtml(label)}" aria-expanded="true">▾</button>` : `<span class="git-project-contract-treegrid-leaf" aria-hidden="true">•</span>`}
        <input type="checkbox"
          data-git-commit-contract-checkbox="${isDirectory ? "dir" : "file"}"
          data-git-commit-contract-path="${escapeHtml(row.repoRelativePath)}"
          data-git-commit-contract-selection-state="${escapeHtml(row.selectionState)}"
          aria-checked="${ariaCheckedForState(row.selectionState)}"
          ${checked ? "checked" : ""}
          ${mixed ? 'data-git-commit-contract-mixed="true"' : ""}
          ${disabled ? "disabled" : ""}>
        <span class="git-project-contract-treegrid-path">${escapeHtml(label || row.name)}</span>
      </div>
      <div class="git-project-contract-treegrid-cell is-status" role="gridcell" data-cell-type="enum">${escapeHtml(row.statusLabel || row.status || "")}</div>
      <div class="git-project-contract-treegrid-cell is-risk" role="gridcell" data-cell-type="risk">${escapeHtml(row.risk || (row.blocked ? "blocked" : ""))}</div>
      <div class="git-project-contract-treegrid-cell is-reason" role="gridcell" data-cell-type="text">${escapeHtml(reason)}</div>
    </div>`;
  }

  function renderContractTreegridHtml(fileBasketModel = {}, options = {}) {
    const selectedPaths = normalizeSelection(
      fileBasketModel,
      Array.isArray(options.selectedPaths) ? options.selectedPaths : fileBasketModel.defaultSelectedPaths
    );
    const rows = buildContractTreegridRows(fileBasketModel, {selectedPaths});
    const readiness = summarizeContractTreegridReadiness(fileBasketModel, {
      selectedPaths,
      legacySelectedPaths: options.legacySelectedPaths || selectedPaths,
      legacyRollbackAvailable: options.legacyRollbackAvailable !== false,
      visibleRenderer: "contract-treegrid",
      legacyRendererActive: false,
      activeReplacement: true
    });
    const htmlEscape = typeof options.escapeHtml === "function" ? options.escapeHtml : escapeHtml;
    const serializedRows = JSON.stringify(rows);
    const serializedReadiness = JSON.stringify(readiness);
    return `<textarea hidden data-git-commit-contract-treegrid-source>${htmlEscape(serializedRows)}</textarea>
      <textarea hidden data-git-commit-contract-treegrid-readiness>${htmlEscape(serializedReadiness)}</textarea>
      <div class="git-project-contract-treegrid-shell" data-git-commit-contract-treegrid data-git-commit-contract-treegrid-active="true" data-git-commit-contract-view="${htmlEscape(SURFACE_ID)}">
        <div class="git-project-contract-treegrid-proof" data-git-commit-contract-proof>
          <strong>MCEL contract treegrid active</strong>
          <span>selected output proof ${readiness.selectedOutputMatchesLegacy ? "matches" : "needs review"}</span>
          <span>blocked rows visible · non-selectable</span>
        </div>
        <div class="git-project-contract-treegrid" role="treegrid" aria-label="Git Tools file basket contract treegrid" aria-colcount="4">
          <div class="git-project-contract-treegrid-head" role="row">
            <div role="columnheader">Path</div>
            <div role="columnheader">Status</div>
            <div role="columnheader">Risk</div>
            <div role="columnheader">Reason</div>
          </div>
          <div class="git-project-contract-treegrid-body" data-git-commit-contract-treegrid-body>
            ${rows.map(contractRowHtml).join("") || `<div class="git-project-contract-treegrid-empty">No candidate files.</div>`}
          </div>
        </div>
      </div>`;
  }

  function readFileBasketModel(workbench) {
    const sourceNode = workbench?.querySelector?.("[data-git-commit-file-basket-model]");
    if (!sourceNode) return null;
    try {
      const parsed = JSON.parse(sourceNode.value || "null");
      return parsed && typeof parsed === "object" ? parsed : null;
    } catch (error) {
      return null;
    }
  }

  function currentSelectedPathsFromDom(workbench, fileBasketModel = readFileBasketModel(workbench)) {
    const paths = Array.from(workbench?.querySelectorAll?.("[data-git-commit-contract-checkbox='file']:checked") || [])
      .map((input) => normalizeRepoPath(input?.dataset?.gitCommitContractPath || ""))
      .filter(Boolean);
    return normalizeSelection(fileBasketModel || {}, paths);
  }

  function selectedFilesFromContractTreegrid(workbench) {
    return currentSelectedPathsFromDom(workbench);
  }

  function readContractRows(workbench) {
    const sourceNode = workbench?.querySelector?.("[data-git-commit-contract-treegrid-source]");
    if (!sourceNode) return [];
    try {
      const parsed = JSON.parse(sourceNode.value || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      return [];
    }
  }

  function writeContractRows(workbench, rows = []) {
    const sourceNode = workbench?.querySelector?.("[data-git-commit-contract-treegrid-source]");
    if (sourceNode) sourceNode.value = JSON.stringify(rows);
  }

  function setCheckboxState(input, state = "unchecked") {
    if (!input) return;
    const checked = state === "checked";
    const mixed = state === "mixed";
    input.checked = checked;
    input.indeterminate = mixed;
    input.dataset.gitCommitContractSelectionState = state;
    input.setAttribute("aria-checked", ariaCheckedForState(state));
    if (mixed) input.dataset.gitCommitContractMixed = "true";
    else delete input.dataset.gitCommitContractMixed;
  }

  function syncTreegridDom(workbench, selectedPaths = []) {
    const fileBasketModel = readFileBasketModel(workbench);
    if (!fileBasketModel) return [];
    const selected = normalizeSelection(fileBasketModel, selectedPaths);
    const rows = buildContractTreegridRows(fileBasketModel, {selectedPaths: selected});
    writeContractRows(workbench, rows);
    const byPath = new Map(rows.map((row) => [`${row.kind}:${row.repoRelativePath}`, row]));
    Array.from(workbench?.querySelectorAll?.("[data-git-commit-contract-row]") || []).forEach((rowElement) => {
      const kind = rowElement.dataset.gitCommitContractRow === "directory" ? "directory" : "file";
      const path = normalizeRepoPath(rowElement.dataset.gitCommitContractPath || "");
      const row = byPath.get(`${kind}:${path}`);
      if (!row) return;
      rowElement.dataset.gitCommitContractSelectionState = row.selectionState;
      const checkbox = rowElement.querySelector?.("[data-git-commit-contract-checkbox]");
      setCheckboxState(checkbox, row.selectionState);
    });
    return selected;
  }

  function applyTreegridSelectionCommand(workbench, command = "", payload = {}) {
    const fileBasketModel = readFileBasketModel(workbench);
    if (!fileBasketModel) {
      return {ok: false, command, reason: "file basket model unavailable", selectedPaths: []};
    }
    const current = currentSelectedPathsFromDom(workbench, fileBasketModel);
    const controller = createController(fileBasketModel, current);
    const result = controller.apply(command, payload);
    const selected = syncTreegridDom(workbench, result.selectedPaths || result.output || current);
    return {...result, selectedPaths: selected, output: selected};
  }

  function setDescendantsHidden(workbench, parentPath = "", hidden = false) {
    const prefix = parentPath ? `${parentPath}/` : "";
    Array.from(workbench?.querySelectorAll?.("[data-git-commit-contract-row]") || []).forEach((row) => {
      const path = normalizeRepoPath(row.dataset.gitCommitContractPath || "");
      if (!path || path === parentPath || !path.startsWith(prefix)) return;
      row.hidden = hidden;
    });
  }

  function initializeContractTreegrid(workbench, options = {}) {
    const treegrid = workbench?.querySelector?.("[data-git-commit-contract-treegrid]");
    if (!treegrid || treegrid.dataset.gitCommitContractTreegridReady === "true") return false;
    treegrid.dataset.gitCommitContractTreegridReady = "true";
    const onSelectionChange = typeof options.onSelectionChange === "function" ? options.onSelectionChange : null;

    Array.from(workbench.querySelectorAll("[data-git-commit-contract-checkbox]") || []).forEach((input) => {
      if (input.dataset.gitCommitContractMixed === "true") {
        input.indeterminate = true;
        input.setAttribute("aria-checked", "mixed");
      }
    });

    treegrid.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-git-commit-contract-disclosure]");
      if (!button) return;
      const path = normalizeRepoPath(button.dataset.gitCommitContractDisclosure || "");
      const row = button.closest("[data-git-commit-contract-row]");
      const nextExpanded = row?.dataset?.gitCommitContractExpanded !== "false" ? false : true;
      if (row) {
        row.dataset.gitCommitContractExpanded = nextExpanded ? "true" : "false";
        row.setAttribute("aria-expanded", nextExpanded ? "true" : "false");
      }
      button.textContent = nextExpanded ? "▾" : "▸";
      button.setAttribute("aria-expanded", nextExpanded ? "true" : "false");
      button.setAttribute("aria-label", `${nextExpanded ? "Collapse" : "Expand"} ${path || "Candidate files"}`);
      setDescendantsHidden(workbench, path, !nextExpanded);
    });

    treegrid.addEventListener("change", (event) => {
      const input = event.target?.closest?.("[data-git-commit-contract-checkbox]");
      if (!input || input.disabled) return;
      const kind = input.dataset.gitCommitContractCheckbox === "dir" ? "dir" : "file";
      const path = normalizeRepoPath(input.dataset.gitCommitContractPath || "");
      const result = kind === "dir"
        ? applyTreegridSelectionCommand(workbench, "set-directory-selection", {path, selected: input.checked})
        : applyTreegridSelectionCommand(workbench, "set-file-selection", {path, selected: input.checked});
      if (onSelectionChange) onSelectionChange(result.selectedPaths || []);
    });

    const selected = syncTreegridDom(workbench, currentSelectedPathsFromDom(workbench));
    if (onSelectionChange) onSelectionChange(selected);
    return true;
  }

  global.GitToolsFileBasketContractView = Object.freeze({
    version: VERSION,
    VERSION,
    SURFACE_ID,
    CONTRACT_ID,
    sourceFile: SOURCE_FILE,
    SOURCE_FILE,
    TOOLKIT_PRIMITIVES,
    normalizeSelection,
    buildContractTreegridRows,
    selectedOutputFromContractRows,
    summarizeContractTreegridReadiness,
    compareLegacyAndContractSelection,
    renderContractTreegridHtml,
    readContractRows,
    selectedFilesFromContractTreegrid,
    applyTreegridSelectionCommand,
    initializeContractTreegrid
  });
})(typeof window !== "undefined" ? window : globalThis);
