    (function (global) {
      "use strict";

      const FILE_BASKET_MODEL_VERSION = "0.1.0";
      const CONTRACT_ID = "pattern.file-basket";

      const FILE_BASKET_FIELDS = [
        {id: "path", label: "Path", type: "path", primary: true},
        {id: "name", label: "Name", type: "name"},
        {id: "status", label: "Git status", type: "enum"},
        {id: "bucket", label: "Planner bucket", type: "enum"},
        {id: "risk", label: "Risk", type: "risk"},
        {id: "reason", label: "Reason", type: "text"},
        {id: "modified", label: "Modified", type: "datetime"},
        {id: "blockedReason", label: "Blocked reason", type: "text"}
      ];

      const REQUIRED_CAPABILITIES = [
        "hierarchy",
        "multi-column-fields",
        "typed-cells",
        "tri-state-selection",
        "blocked-visible-not-selectable",
        "selected-output-proof",
        "keyboard-navigation"
      ];

      const REJECTED_VIEWS = [
        "title-only-tree",
        "plain-tree-primary",
        "icon-grid-primary"
      ];

      const GROUP_CONFIGS = [
        {
          key: "selected_by_default",
          title: "Selected by default",
          bucket: "selected_by_default",
          selectable: true,
          selectedByDefault: true,
          reason: "selected by default",
          tone: "clean"
        },
        {
          key: "review_before_selecting",
          title: "Review before selecting",
          bucket: "review_before_selecting",
          selectable: true,
          selectedByDefault: false,
          reason: "needs review",
          tone: "review"
        },
        {
          key: "blocked_possible_secrets",
          title: "Blocked",
          bucket: "blocked_possible_secrets",
          selectable: false,
          selectedByDefault: false,
          reason: "blocked by Secrets / Filter",
          tone: "blocked"
        },
        {
          key: "excluded_generated_runtime",
          title: "Excluded generated/runtime",
          bucket: "excluded_generated_runtime",
          selectable: false,
          selectedByDefault: false,
          reason: "excluded generated/runtime",
          tone: "excluded"
        }
      ];

      const GROUP_PRECEDENCE = {
        selected_by_default: 10,
        review_before_selecting: 20,
        excluded_generated_runtime: 30,
        blocked_possible_secrets: 40
      };

      const VIEW_CAPABILITIES = {
        "contract-treegrid": REQUIRED_CAPABILITIES.concat(["resizable-columns", "row-inspector", "sort-filter"]),
        "details-tree": REQUIRED_CAPABILITIES.concat(["resizable-columns", "sort-filter"]),
        "compact-audit-list": ["multi-column-fields", "typed-cells", "blocked-visible-not-selectable", "selected-output-proof", "keyboard-navigation"],
        "data-table": ["multi-column-fields", "typed-cells", "selected-output-proof", "keyboard-navigation"],
        "column-browser-inspector": ["hierarchy", "typed-cells", "blocked-visible-not-selectable", "keyboard-navigation"],
        "title-only-tree": ["hierarchy", "keyboard-navigation"],
        "plain-tree-primary": ["hierarchy", "keyboard-navigation", "basic-selection"],
        "icon-grid-primary": ["typed-cells", "keyboard-navigation"]
      };

      function clone(value) {
        return JSON.parse(JSON.stringify(value));
      }

      function asArray(value) {
        return Array.isArray(value) ? value : [];
      }

      function uniqueSorted(values) {
        return Array.from(new Set(asArray(values).filter(Boolean))).sort((left, right) => String(left).localeCompare(String(right)));
      }

      function normalizeRepoPath(value = "") {
        const raw = String(value || "").replace(/\\/g, "/").trim();
        if (!raw || /^[A-Za-z]:\//.test(raw) || raw.startsWith("//")) return "";
        const withoutRepoRoot = raw.replace(/^\/+/, "").replace(/^main_computer_test\//, "");
        const parts = withoutRepoRoot.split("/").filter(Boolean);
        if (!parts.length || parts.some((part) => part === "." || part === "..")) return "";
        return parts.join("/");
      }

      function pathName(path = "") {
        return String(path || "").split("/").filter(Boolean).pop() || "";
      }

      function parentPath(path = "") {
        const parts = String(path || "").split("/").filter(Boolean);
        parts.pop();
        return parts.join("/");
      }

      function normalizeStatus(item = {}) {
        const raw = String(item.status || item.state || "").toLowerCase();
        if (raw.includes("untracked") || raw === "??" || item.untracked) return "untracked";
        if (raw.includes("renamed") || item.renamed) return "tracked_renamed";
        if (raw.includes("deleted") || item.deleted) return "tracked_deleted";
        if (raw.includes("conflict") || item.conflicted) return "conflicted";
        if (raw.includes("modified") || raw.includes("changed") || raw.includes("tracked") || raw.includes("staged") || item.staged || item.unstaged) {
          return "tracked_changed";
        }
        return raw || "unknown";
      }

      function statusDisplay(status = "") {
        const normalized = String(status || "unknown").toLowerCase();
        if (normalized === "untracked") return {symbol: "+", label: "untracked", tone: "untracked"};
        if (normalized === "tracked_deleted") return {symbol: "✓", label: "tracked deleted", tone: "tracked"};
        if (normalized === "tracked_renamed") return {symbol: "✓", label: "tracked renamed", tone: "tracked"};
        if (normalized === "tracked_changed") return {symbol: "✓", label: "tracked changed", tone: "tracked"};
        if (normalized === "conflicted") return {symbol: "!", label: "conflicted", tone: "blocked"};
        return {symbol: "·", label: normalized, tone: "unknown"};
      }

      function groupsFromReview(review = {}) {
        const groups = review.candidate_groups || {};
        return GROUP_CONFIGS.map((group) => ({
          ...group,
          items: asArray(groups[group.key])
        }));
      }

      function fileMeta(item = {}, group = {}) {
        const labels = asArray(item.classifications).filter(Boolean);
        const status = normalizeStatus(item);
        const display = statusDisplay(status);
        const risk = String(item.risk || item.privacy_risk || group.tone || "review");
        const findings = Number(item.blocking_security_findings_count || item.privacy_findings_count || 0);
        const reason = String(item.reason || group.reason || "");
        const modified = String(item.modified || "");
        const detailParts = [
          display.label,
          labels.join(" · "),
          risk,
          findings ? `${findings} finding${findings === 1 ? "" : "s"}` : "",
          modified ? `edited ${modified}` : ""
        ].filter(Boolean);
        return {
          labels,
          status,
          statusLabel: display.label,
          statusSymbol: display.symbol,
          statusTone: display.tone,
          risk,
          findings,
          reason,
          modified,
          meta: detailParts.join(" · ")
        };
      }

      function blockedReasonFor(item = {}, group = {}, meta = {}) {
        if (item.selectable === false && item.blocked_reason) return String(item.blocked_reason);
        if (item.selectable === false) return String(item.reason || "item marked unselectable");
        if (group.selectable === false) return String(item.reason || group.reason || "planner bucket is not selectable");
        if (meta.status === "conflicted") return "conflicted status requires manual resolution";
        if (String(meta.risk || "").toLowerCase().includes("block")) return String(meta.reason || "risk policy marks this file blocked");
        if (Number(meta.findings || 0) > 0 && /secret|block/i.test(String(meta.reason || group.reason || ""))) {
          return String(meta.reason || group.reason || "security finding blocks selection");
        }
        return "";
      }

      function candidateItems(review = {}) {
        const byPath = new Map();
        const invalid = [];
        groupsFromReview(review).forEach((group) => {
          group.items.forEach((item = {}, index) => {
            const path = normalizeRepoPath(item.path || item.file || item.repoPath);
            if (!path) {
              invalid.push({group: group.key, index, rawPath: item.path || item.file || item.repoPath || ""});
              return;
            }
            const rank = GROUP_PRECEDENCE[group.key] || 0;
            const previous = byPath.get(path);
            if (!previous || rank >= previous.rank) {
              byPath.set(path, {item: {...item, path}, group, rank});
            }
          });
        });
        return {
          items: Array.from(byPath.values()).map(({item, group}) => ({item, group})),
          invalid
        };
      }

      function sortRows(rows = []) {
        return rows.slice().sort((left, right) => {
          const leftParts = String(left.path || "").split("/");
          const rightParts = String(right.path || "").split("/");
          for (let index = 0; index < Math.max(leftParts.length, rightParts.length); index += 1) {
            const leftPart = leftParts[index] || "";
            const rightPart = rightParts[index] || "";
            if (leftPart !== rightPart) return leftPart.localeCompare(rightPart, undefined, {sensitivity: "base"});
          }
          return String(left.path || "").localeCompare(String(right.path || ""), undefined, {sensitivity: "base"});
        });
      }

      function buildRows(review = {}) {
        const candidates = candidateItems(review);
        const rows = candidates.items.map(({item, group}) => {
          const path = normalizeRepoPath(item.path);
          const meta = fileMeta(item, group);
          const blockedReason = blockedReasonFor(item, group, meta);
          const selectable = !blockedReason;
          const selectedByDefault = Boolean(selectable && (group.selectedByDefault || item.selected_by_default));
          const parts = path.split("/").filter(Boolean);
          return {
            id: `file:${path}`,
            kind: "file",
            path,
            name: pathName(path),
            parentPath: parentPath(path),
            depth: Math.max(0, parts.length - 1),
            status: meta.status,
            statusLabel: meta.statusLabel,
            statusSymbol: meta.statusSymbol,
            statusTone: meta.statusTone,
            bucket: group.key,
            bucketLabel: group.title || group.key,
            bucketTone: group.tone,
            risk: meta.risk,
            reason: meta.reason,
            modified: meta.modified,
            classifications: meta.labels,
            findings: meta.findings,
            meta: meta.meta,
            selectable,
            selectedByDefault,
            blocked: !selectable,
            blockedReason,
            ancestors: parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join("/"))
          };
        });
        return {rows: sortRows(rows), invalid: candidates.invalid};
      }

      function createDirectoryNode(path = "") {
        return {
          id: path ? `dir:${path}` : "dir:",
          kind: "dir",
          path,
          name: path ? pathName(path) : "Candidate files",
          parentPath: path ? parentPath(path) : "",
          depth: path ? path.split("/").filter(Boolean).length - 1 : 0,
          children: [],
          fileCount: 0,
          selectableFileCount: 0,
          blockedFileCount: 0,
          selectedByDefaultFileCount: 0,
          selectable: false,
          selectionState: "none"
        };
      }

      function ensureDirectory(root, directories, path) {
        const normalized = normalizeRepoPath(path);
        if (!normalized) return root;
        if (directories.has(normalized)) return directories.get(normalized);
        const parent = ensureDirectory(root, directories, parentPath(normalized));
        const node = createDirectoryNode(normalized);
        directories.set(normalized, node);
        parent.children.push(node);
        return node;
      }

      function computeDirectoryStats(node = {}) {
        const children = asArray(node.children);
        const stats = children.reduce((accumulator, child) => {
          if (child.kind === "file") {
            accumulator.fileCount += 1;
            if (child.selectable) accumulator.selectableFileCount += 1;
            if (child.blocked) accumulator.blockedFileCount += 1;
            if (child.selectedByDefault) accumulator.selectedByDefaultFileCount += 1;
          } else {
            const childStats = computeDirectoryStats(child);
            accumulator.fileCount += childStats.fileCount;
            accumulator.selectableFileCount += childStats.selectableFileCount;
            accumulator.blockedFileCount += childStats.blockedFileCount;
            accumulator.selectedByDefaultFileCount += childStats.selectedByDefaultFileCount;
          }
          return accumulator;
        }, {fileCount: 0, selectableFileCount: 0, blockedFileCount: 0, selectedByDefaultFileCount: 0});
        Object.assign(node, stats, {
          selectable: stats.selectableFileCount > 0,
          selectionState: stats.selectableFileCount === 0
            ? "none"
            : stats.selectedByDefaultFileCount === 0
              ? "none"
              : stats.selectedByDefaultFileCount === stats.selectableFileCount
                ? "all"
                : "mixed"
        });
        node.children.sort((left, right) => {
          if (left.kind !== right.kind) return left.kind === "dir" ? -1 : 1;
          return String(left.name || "").localeCompare(String(right.name || ""), undefined, {sensitivity: "base"});
        });
        return stats;
      }

      function buildHierarchy(rows = []) {
        const root = createDirectoryNode("");
        const directories = new Map([["", root]]);
        rows.forEach((row) => {
          const parent = ensureDirectory(root, directories, row.parentPath);
          parent.children.push({...row});
        });
        computeDirectoryStats(root);
        return {root, directories: Array.from(directories.values()).map((dir) => ({...dir, children: undefined}))};
      }

      function selectedPathSet(model = {}, selectedPaths) {
        const selected = Array.isArray(selectedPaths) ? selectedPaths : model.defaultSelectedPaths;
        const selectable = new Set(asArray(model.selectablePaths));
        return new Set(asArray(selected).map(normalizeRepoPath).filter((path) => selectable.has(path)));
      }

      function selectedOutput(model = {}, selectedPaths) {
        return uniqueSorted(Array.from(selectedPathSet(model, selectedPaths)));
      }

      function selectableDescendantPaths(model = {}, directoryPath = "") {
        const directory = normalizeRepoPath(directoryPath);
        const prefix = directory ? `${directory}/` : "";
        return uniqueSorted(asArray(model.rows)
          .filter((row) => row.kind === "file" && row.selectable && (!directory || row.path === directory || row.path.startsWith(prefix)))
          .map((row) => row.path));
      }

      function deriveDirectorySelectionState(model = {}, selectedPaths = [], directoryPath = "") {
        const descendants = selectableDescendantPaths(model, directoryPath);
        if (!descendants.length) return "none";
        const selected = selectedPathSet(model, selectedPaths);
        const selectedCount = descendants.filter((path) => selected.has(path)).length;
        if (selectedCount === 0) return "none";
        if (selectedCount === descendants.length) return "all";
        return "mixed";
      }

      function toggleFileSelection(model = {}, selectedPaths = [], path = "", nextSelected) {
        const normalized = normalizeRepoPath(path);
        const selectable = new Set(asArray(model.selectablePaths));
        const selected = selectedPathSet(model, selectedPaths);
        if (!selectable.has(normalized)) return selectedOutput(model, Array.from(selected));
        const shouldSelect = typeof nextSelected === "boolean" ? nextSelected : !selected.has(normalized);
        if (shouldSelect) selected.add(normalized);
        else selected.delete(normalized);
        return selectedOutput(model, Array.from(selected));
      }

      function toggleDirectorySelection(model = {}, selectedPaths = [], directoryPath = "", nextSelected) {
        const descendants = selectableDescendantPaths(model, directoryPath);
        const selected = selectedPathSet(model, selectedPaths);
        const allSelected = descendants.length > 0 && descendants.every((path) => selected.has(path));
        const shouldSelect = typeof nextSelected === "boolean" ? nextSelected : !allSelected;
        descendants.forEach((path) => {
          if (shouldSelect) selected.add(path);
          else selected.delete(path);
        });
        return selectedOutput(model, Array.from(selected));
      }

      function selectAllEligible(model = {}) {
        return uniqueSorted(model.selectablePaths);
      }

      function clearSelection() {
        return [];
      }

      function selectionSummary(model = {}, selectedPaths = []) {
        const selected = selectedOutput(model, selectedPaths);
        const selectedSet = new Set(selected);
        const blockedPaths = new Set(asArray(model.blockedPaths));
        const selectedRows = asArray(model.rows).filter((row) => selectedSet.has(row.path));
        const invalidSelectedPaths = asArray(selectedPaths)
          .map(normalizeRepoPath)
          .filter((path) => path && !selectedSet.has(path));
        return {
          total: asArray(model.rows).length,
          selectable: asArray(model.selectablePaths).length,
          blocked: asArray(model.blockedPaths).length,
          selected: selected.length,
          selectedPaths: selected,
          selectedReview: selectedRows.filter((row) => row.bucket === "review_before_selecting").length,
          selectedBlocked: selected.filter((path) => blockedPaths.has(path)).length,
          invalidSelectedPaths: uniqueSorted(invalidSelectedPaths)
        };
      }

      function resolveViewEligibility(model = {}, viewId = "") {
        const capabilities = VIEW_CAPABILITIES[viewId] || [];
        const missingCapabilities = asArray(model.requiredCapabilities || REQUIRED_CAPABILITIES)
          .filter((capability) => !capabilities.includes(capability));
        return {
          viewId,
          eligible: missingCapabilities.length === 0,
          capabilities: capabilities.slice(),
          missingCapabilities,
          reason: missingCapabilities.length
            ? `Missing ${missingCapabilities.join(", ")}`
            : "Satisfies the file-basket contract"
        };
      }

      function buildViewContract(model = {}) {
        const views = ["contract-treegrid", "details-tree", "compact-audit-list", "data-table", "column-browser-inspector", "title-only-tree", "plain-tree-primary", "icon-grid-primary"]
          .map((viewId) => resolveViewEligibility(model, viewId));
        return {
          contractId: CONTRACT_ID,
          requiredCapabilities: REQUIRED_CAPABILITIES.slice(),
          eligibleViews: views.filter((view) => view.eligible),
          rejectedViews: views.filter((view) => !view.eligible),
          titleOnlyTreeRejected: views.some((view) => view.viewId === "title-only-tree" && !view.eligible)
        };
      }

      function buildFileBasketModel(review = {}, options = {}) {
        const built = buildRows(review);
        const hierarchy = buildHierarchy(built.rows);
        const selectablePaths = uniqueSorted(built.rows.filter((row) => row.selectable).map((row) => row.path));
        const blockedPaths = uniqueSorted(built.rows.filter((row) => row.blocked).map((row) => row.path));
        const defaultSelectedPaths = uniqueSorted(built.rows.filter((row) => row.selectedByDefault && row.selectable).map((row) => row.path));
        const model = {
          version: FILE_BASKET_MODEL_VERSION,
          contractId: CONTRACT_ID,
          surfaceId: options.surfaceId || "task-manager.file-basket",
          sourceConcern: options.sourceConcern || "concern.file-basket",
          sourceFile: options.sourceFile || "main_computer/web/applications/scripts/task-manager.js",
          fields: clone(FILE_BASKET_FIELDS),
          requiredCapabilities: REQUIRED_CAPABILITIES.slice(),
          selectionContract: {
            mode: "hierarchical-explicit-files",
            output: "explicit-repo-relative-file-paths",
            directoryBehavior: "selects-selectable-descendant-files",
            mixedDirectoryState: true,
            blockedRowsVisible: true,
            blockedRowsSelectable: false,
            selectedFilesAreSourceOfTruth: true
          },
          safetyContract: {
            blockedItemsVisible: true,
            blockedItemsSelectable: false,
            destructiveActionsRequirePreview: true,
            warningsCanBeAcceptedButHardBlocksCannot: true
          },
          rows: built.rows,
          hierarchy: hierarchy.root.children,
          directoryRows: hierarchy.directories.filter((dir) => dir.path),
          selectablePaths,
          blockedPaths,
          defaultSelectedPaths,
          invalidCandidates: built.invalid,
          stats: {
            total: built.rows.length,
            selectable: selectablePaths.length,
            blocked: blockedPaths.length,
            selectedByDefault: defaultSelectedPaths.length,
            invalid: built.invalid.length,
            review: built.rows.filter((row) => row.bucket === "review_before_selecting").length
          },
          rejectedViews: REJECTED_VIEWS.slice()
        };
        model.viewContract = buildViewContract(model);
        return model;
      }

      function buildReadinessReport() {
        const specimen = buildFileBasketModel({
          candidate_groups: {
            selected_by_default: [{path: "main_computer/web/applications/scripts/task-manager.js", status: "modified", classifications: ["source"], modified: "today"}],
            review_before_selecting: [{path: "tests/test_task_manager.py", status: "untracked", risk: "review", reason: "new proof"}],
            blocked_possible_secrets: [{path: "runtime/secrets.env", status: "untracked", risk: "blocked", reason: "secret-looking runtime file"}],
            excluded_generated_runtime: [{path: "runtime/cache/app.tmp", status: "untracked", reason: "generated runtime"}]
          }
        });
        const rootSelection = toggleDirectorySelection(specimen, [], "");
        const titleOnly = resolveViewEligibility(specimen, "title-only-tree");
        return {
          ready: specimen.contractId === CONTRACT_ID &&
            specimen.fields.length >= 8 &&
            specimen.rows.length === 4 &&
            specimen.selectablePaths.length === 2 &&
            rootSelection.length === 2 &&
            titleOnly.eligible === false,
          version: FILE_BASKET_MODEL_VERSION,
          rowCount: specimen.rows.length,
          fieldCount: specimen.fields.length,
          selectableCount: specimen.selectablePaths.length,
          blockedCount: specimen.blockedPaths.length,
          defaultSelectedCount: specimen.defaultSelectedPaths.length,
          titleOnlyTreeRejected: titleOnly.eligible === false,
          rootSelectionCount: rootSelection.length,
          contractId: specimen.contractId
        };
      }

      global.McelFileBasketModel = {
        FILE_BASKET_MODEL_VERSION,
        CONTRACT_ID,
        FILE_BASKET_FIELDS: clone(FILE_BASKET_FIELDS),
        REQUIRED_CAPABILITIES: REQUIRED_CAPABILITIES.slice(),
        GROUP_CONFIGS: clone(GROUP_CONFIGS),
        VIEW_CAPABILITIES: clone(VIEW_CAPABILITIES),
        normalizeRepoPath,
        normalizeStatus,
        statusDisplay,
        groupsFromReview,
        candidateItems,
        buildFileBasketModel,
        selectedOutput,
        selectableDescendantPaths,
        deriveDirectorySelectionState,
        toggleFileSelection,
        toggleDirectorySelection,
        selectAllEligible,
        clearSelection,
        selectionSummary,
        resolveViewEligibility,
        buildViewContract,
        buildReadinessReport
      };
    })(typeof window !== "undefined" ? window : globalThis);
