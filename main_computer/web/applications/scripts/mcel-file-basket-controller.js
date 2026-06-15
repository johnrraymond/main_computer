    (function (global) {
      "use strict";

      const FILE_BASKET_CONTROLLER_VERSION = "0.1.0";
      const CONTROLLER_ID = "controller.file-basket-selection";
      const CONTRACT_ID = "pattern.file-basket";

      function modelAdapter() {
        return global.McelFileBasketModel || null;
      }

      function asArray(value) {
        return Array.isArray(value) ? value : [];
      }

      function uniqueSorted(values) {
        return Array.from(new Set(asArray(values).filter(Boolean)))
          .sort((left, right) => String(left).localeCompare(String(right)));
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

      function buildFileBasketModel(review = {}, options = {}) {
        const adapter = modelAdapter();
        if (typeof adapter?.buildFileBasketModel !== "function") return null;
        return adapter.buildFileBasketModel(review, options);
      }

      function normalizeModel(modelOrReview = {}, options = {}) {
        if (modelOrReview?.contractId === CONTRACT_ID && Array.isArray(modelOrReview?.rows)) {
          return modelOrReview;
        }
        return buildFileBasketModel(modelOrReview, options);
      }

      function selectablePathSet(model = {}) {
        return new Set(asArray(model.selectablePaths).map(normalizeRepoPath).filter(Boolean));
      }

      function blockedPathSet(model = {}) {
        return new Set(asArray(model.blockedPaths).map(normalizeRepoPath).filter(Boolean));
      }

      function visiblePathSet(model = {}) {
        return new Set(asArray(model.rows).map((row = {}) => normalizeRepoPath(row.path)).filter(Boolean));
      }

      function selectedOutput(model = {}, selectedPaths = []) {
        const adapter = modelAdapter();
        if (typeof adapter?.selectedOutput === "function") {
          return adapter.selectedOutput(model, selectedPaths);
        }
        const selectable = selectablePathSet(model);
        return uniqueSorted(asArray(selectedPaths).map(normalizeRepoPath).filter((path) => selectable.has(path)));
      }

      function selectableDescendantPaths(model = {}, directoryPath = "") {
        const adapter = modelAdapter();
        if (typeof adapter?.selectableDescendantPaths === "function") {
          return adapter.selectableDescendantPaths(model, directoryPath);
        }
        const directory = normalizeRepoPath(directoryPath);
        const prefix = directory ? `${directory}/` : "";
        return uniqueSorted(asArray(model.rows)
          .filter((row = {}) => row.kind === "file" && row.selectable && (!directory || row.path === directory || row.path.startsWith(prefix)))
          .map((row = {}) => row.path));
      }

      function deriveDirectorySelectionState(model = {}, selectedPaths = [], directoryPath = "") {
        const adapter = modelAdapter();
        if (typeof adapter?.deriveDirectorySelectionState === "function") {
          return adapter.deriveDirectorySelectionState(model, selectedPaths, directoryPath);
        }
        const descendants = selectableDescendantPaths(model, directoryPath);
        if (!descendants.length) return "none";
        const selected = new Set(selectedOutput(model, selectedPaths));
        const selectedCount = descendants.filter((path) => selected.has(path)).length;
        if (selectedCount === 0) return "none";
        if (selectedCount === descendants.length) return "all";
        return "mixed";
      }

      function canSelectPath(model = {}, path = "") {
        const normalized = normalizeRepoPath(path);
        return Boolean(normalized && selectablePathSet(model).has(normalized));
      }

      function canSeePath(model = {}, path = "") {
        const normalized = normalizeRepoPath(path);
        return Boolean(normalized && visiblePathSet(model).has(normalized));
      }

      function pathReason(model = {}, path = "") {
        const normalized = normalizeRepoPath(path);
        const row = asArray(model.rows).find((candidate = {}) => candidate.path === normalized);
        if (!row) return "path is not part of this file-basket contract";
        if (row.blockedReason) return row.blockedReason;
        if (row.selectable === false) return "row is visible but not selectable";
        return "";
      }

      function makeResult(model = {}, selectedPaths = [], command = "normalize-selection", details = {}) {
        const output = selectedOutput(model, selectedPaths);
        const summary = selectionSummary(model, selectedPaths);
        return {
          ok: details.ok !== false,
          controllerId: CONTROLLER_ID,
          controllerVersion: FILE_BASKET_CONTROLLER_VERSION,
          contractId: model.contractId || CONTRACT_ID,
          command,
          path: details.path || "",
          reason: details.reason || "",
          selectedPaths: output,
          output,
          summary
        };
      }

      function setFileSelection(model = {}, selectedPaths = [], path = "", nextSelected) {
        const normalized = normalizeRepoPath(path);
        if (!canSelectPath(model, normalized)) {
          return makeResult(model, selectedPaths, "set-file-selection", {
            ok: false,
            path: normalized,
            reason: pathReason(model, normalized)
          });
        }
        const selected = new Set(selectedOutput(model, selectedPaths));
        const shouldSelect = typeof nextSelected === "boolean" ? nextSelected : !selected.has(normalized);
        if (shouldSelect) selected.add(normalized);
        else selected.delete(normalized);
        return makeResult(model, Array.from(selected), "set-file-selection", {path: normalized});
      }

      function setDirectorySelection(model = {}, selectedPaths = [], directoryPath = "", nextSelected) {
        const normalized = normalizeRepoPath(directoryPath);
        const descendants = selectableDescendantPaths(model, normalized);
        if (!descendants.length) {
          return makeResult(model, selectedPaths, "set-directory-selection", {
            ok: false,
            path: normalized,
            reason: normalized && canSeePath(model, normalized)
              ? "directory has no selectable descendant files"
              : "directory is not part of this file-basket contract"
          });
        }
        const selected = new Set(selectedOutput(model, selectedPaths));
        const allSelected = descendants.every((path) => selected.has(path));
        const shouldSelect = typeof nextSelected === "boolean" ? nextSelected : !allSelected;
        descendants.forEach((path) => {
          if (shouldSelect) selected.add(path);
          else selected.delete(path);
        });
        return makeResult(model, Array.from(selected), "set-directory-selection", {path: normalized});
      }

      function selectAllEligible(model = {}) {
        const adapter = modelAdapter();
        const selected = typeof adapter?.selectAllEligible === "function"
          ? adapter.selectAllEligible(model)
          : uniqueSorted(model.selectablePaths);
        return makeResult(model, selected, "select-all-eligible");
      }

      function clearSelection(model = {}) {
        return makeResult(model, [], "clear-selection");
      }

      function replaceSelection(model = {}, selectedPaths = []) {
        return makeResult(model, selectedPaths, "replace-selection");
      }

      function applySelectionCommand(model = {}, selectedPaths = [], command = "", payload = {}) {
        const normalizedCommand = String(command || "").trim().toLowerCase();
        const path = payload.path ?? payload.directoryPath ?? payload.filePath ?? "";
        const selected = payload.selected ?? payload.nextSelected;
        if (["set-file-selection", "select-file", "toggle-file"].includes(normalizedCommand)) {
          const nextSelected = normalizedCommand === "toggle-file"
            ? undefined
            : normalizedCommand === "select-file"
              ? true
              : Boolean(selected);
          return setFileSelection(model, selectedPaths, path, nextSelected);
        }
        if (["set-directory-selection", "select-directory", "toggle-directory"].includes(normalizedCommand)) {
          const nextSelected = normalizedCommand === "toggle-directory"
            ? undefined
            : normalizedCommand === "select-directory"
              ? true
              : Boolean(selected);
          return setDirectorySelection(model, selectedPaths, path, nextSelected);
        }
        if (["select-all", "select-all-eligible"].includes(normalizedCommand)) {
          return selectAllEligible(model);
        }
        if (["clear", "clear-selection"].includes(normalizedCommand)) {
          return clearSelection(model);
        }
        if (["replace-selection", "normalize-selection"].includes(normalizedCommand)) {
          return replaceSelection(model, payload.selectedPaths || selectedPaths);
        }
        return makeResult(model, selectedPaths, normalizedCommand || "unknown-command", {
          ok: false,
          path: normalizeRepoPath(path),
          reason: `unknown file-basket selection command: ${command || "(missing)"}`
        });
      }

      function selectionSummary(model = {}, selectedPaths = []) {
        const adapter = modelAdapter();
        if (typeof adapter?.selectionSummary === "function") {
          return adapter.selectionSummary(model, selectedPaths);
        }
        const selected = selectedOutput(model, selectedPaths);
        const selectedSet = new Set(selected);
        const blocked = blockedPathSet(model);
        return {
          total: asArray(model.rows).length,
          selectable: asArray(model.selectablePaths).length,
          blocked: asArray(model.blockedPaths).length,
          selected: selected.length,
          selectedPaths: selected,
          selectedBlocked: selected.filter((path) => blocked.has(path)).length,
          invalidSelectedPaths: uniqueSorted(asArray(selectedPaths).map(normalizeRepoPath).filter((path) => path && !selectedSet.has(path)))
        };
      }

      function selectionReport(model = {}, rawPaths = []) {
        const raw = uniqueSorted(asArray(rawPaths).map(normalizeRepoPath).filter(Boolean));
        const selected = selectedOutput(model, raw);
        return {
          enabled: true,
          controllerId: CONTROLLER_ID,
          controllerVersion: FILE_BASKET_CONTROLLER_VERSION,
          contractId: model.contractId || CONTRACT_ID,
          rawPaths: raw,
          selectedPaths: selected,
          matches: JSON.stringify(raw) === JSON.stringify(selected),
          summary: selectionSummary(model, raw)
        };
      }

      function canSelectTreeNode(model = {}, node = {}) {
        const data = node?.data || node || {};
        if (data.kind === "empty" || data.selectable === false) return false;
        if (data.kind === "dir" || data.kind === "group") {
          return selectableDescendantPaths(model, data.path || "").length > 0;
        }
        return canSelectPath(model, data.path || data.file || data.repoPath || node?.key || "");
      }

      function treeNodeSelectionState(model = {}, selectedPaths = [], node = {}) {
        const data = node?.data || node || {};
        if (data.kind === "dir" || data.kind === "group") {
          return deriveDirectorySelectionState(model, selectedPaths, data.path || "");
        }
        const path = normalizeRepoPath(data.path || data.file || data.repoPath || node?.key || "");
        if (!canSelectPath(model, path)) return "blocked";
        return selectedOutput(model, selectedPaths).includes(path) ? "all" : "none";
      }

      function createFileBasketController(modelOrReview = {}, options = {}) {
        const model = normalizeModel(modelOrReview, options) || {};
        let selected = selectedOutput(model, Array.isArray(options.selectedPaths) ? options.selectedPaths : model.defaultSelectedPaths);
        const controller = {
          controllerId: CONTROLLER_ID,
          controllerVersion: FILE_BASKET_CONTROLLER_VERSION,
          contractId: model.contractId || CONTRACT_ID,
          model,
          selectedOutput(paths = selected) {
            return selectedOutput(model, paths);
          },
          selectedPaths() {
            return selected.slice();
          },
          replaceSelection(paths = []) {
            const result = replaceSelection(model, paths);
            selected = result.selectedPaths.slice();
            return result;
          },
          apply(command = "", payload = {}) {
            const result = applySelectionCommand(model, selected, command, payload);
            if (result.ok || ["replace-selection", "normalize-selection", "clear-selection", "select-all-eligible"].includes(result.command)) {
              selected = result.selectedPaths.slice();
            }
            return result;
          },
          canSelectPath(path = "") {
            return canSelectPath(model, path);
          },
          canSeePath(path = "") {
            return canSeePath(model, path);
          },
          canSelectTreeNode(node = {}) {
            return canSelectTreeNode(model, node);
          },
          selectableDescendantPaths(path = "") {
            return selectableDescendantPaths(model, path);
          },
          deriveDirectorySelectionState(paths = selected, path = "") {
            return deriveDirectorySelectionState(model, paths, path);
          },
          treeNodeSelectionState(node = {}, paths = selected) {
            return treeNodeSelectionState(model, paths, node);
          },
          selectionSummary(paths = selected) {
            return selectionSummary(model, paths);
          },
          selectionReport(paths = selected) {
            return selectionReport(model, paths);
          }
        };
        return controller;
      }

      function buildReadinessReport() {
        const model = buildFileBasketModel({
          candidate_groups: {
            selected_by_default: [{path: "main_computer/web/applications/scripts/task-manager.js", status: "modified"}],
            review_before_selecting: [{path: "tests/test_mcel_file_basket_model.py", status: "untracked"}],
            blocked_possible_secrets: [{path: "runtime/secrets.env", status: "untracked", risk: "blocked", reason: "secret-looking runtime file"}]
          }
        }, {surfaceId: "task-manager.file-basket"});
        const controller = createFileBasketController(model, {selectedPaths: []});
        const directoryResult = controller.apply("set-directory-selection", {path: "", selected: true});
        const blockedResult = controller.apply("set-file-selection", {path: "runtime/secrets.env", selected: true});
        return {
          ready: Boolean(model && directoryResult.selectedPaths.length === 2 && blockedResult.ok === false && blockedResult.selectedPaths.length === 2),
          version: FILE_BASKET_CONTROLLER_VERSION,
          controllerId: CONTROLLER_ID,
          contractId: model?.contractId || CONTRACT_ID,
          directorySelectionCount: directoryResult.selectedPaths.length,
          blockedCommandRejected: blockedResult.ok === false,
          selectedBlocked: blockedResult.summary?.selectedBlocked || 0
        };
      }

      global.McelFileBasketController = {
        FILE_BASKET_CONTROLLER_VERSION,
        CONTROLLER_ID,
        CONTRACT_ID,
        createFileBasketController,
        buildFileBasketModel,
        selectedOutput,
        selectableDescendantPaths,
        deriveDirectorySelectionState,
        canSelectPath,
        canSeePath,
        canSelectTreeNode,
        treeNodeSelectionState,
        applySelectionCommand,
        selectionSummary,
        selectionReport,
        buildReadinessReport
      };
    })(typeof window !== "undefined" ? window : globalThis);
