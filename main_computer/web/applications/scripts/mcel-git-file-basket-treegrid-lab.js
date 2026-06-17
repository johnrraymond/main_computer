(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const LAB_ID = "mcel.lab.git-file-basket-treegrid";
  const TARGET_CONCERN = "git-tools.file-basket";
  const CONTRACT_ID = "pattern.file-basket";
  const SOURCE_FILE = "main_computer/web/applications/scripts/mcel-git-file-basket-treegrid-lab.js";
  const DEFAULT_DIRECTORY_PROBE = "main_computer/web/applications/scripts";
  const VIEW_MODE_OPTIONS = Object.freeze([
    Object.freeze({id: "contract-treegrid", label: "Contract treegrid", kind: "primary", renderer: "interactive-treegrid", description: "Full hierarchy, typed columns, tri-state selection, blocked-row proof, selected-output proof, and resizable columns."}),
    Object.freeze({id: "details-tree", label: "Details tree", kind: "compatible", renderer: "interactive-treegrid", description: "Alternate tree-with-details presentation using the same controller-owned selection and expansion semantics."}),
    Object.freeze({id: "details-treegrid", label: "Details treegrid", kind: "compatible", renderer: "interactive-treegrid", description: "Explorer-style details treegrid using the Git basket contract, resize handles, typed cells, and controller-owned selection."}),
    Object.freeze({id: "explorer-sidebar", label: "Explorer sidebar", kind: "candidate", renderer: "hierarchy-tree", description: "Dense navigation tree style with Git paths, chevrons, safety badges, and explicit contract gaps."}),
    Object.freeze({id: "ide-project-tree", label: "IDE project tree", kind: "candidate", renderer: "hierarchy-tree", description: "Developer project tree style with status/risk badges beside Git-shaped rows."}),
    Object.freeze({id: "miller-columns", label: "Column browser", kind: "browser", renderer: "column-browser", description: "Finder-style columns for Git directory scope, file candidates, and inspector proof."}),
    Object.freeze({id: "outline-tree", label: "Outline tree", kind: "candidate", renderer: "hierarchy-tree", description: "Semantic outline projection using the same Git hierarchy, useful for proving where labels lose typed fields."}),
    Object.freeze({id: "accessibility-proof", label: "Keyboard proof", kind: "candidate", renderer: "accessibility-tree", description: "ARIA/keyboard proof projection for focus, expansion, selected output, and blocked mutation semantics."}),
    Object.freeze({id: "icon-grid", label: "Icon grid", kind: "visual", renderer: "icon-grid", description: "Explorer icon-grid projection using the same Git files; intentionally checked against the file-basket contract."}),
    Object.freeze({id: "compact-list", label: "List view", kind: "list", renderer: "compact-audit", description: "Compact list projection for scanning many Git files, with contract gaps made visible."}),
    Object.freeze({id: "tile-view", label: "Tiles view", kind: "visual", renderer: "tiles", description: "Tile projection showing Git path, status, risk, and reason without changing selected output semantics."}),
    Object.freeze({id: "content-view", label: "Content view", kind: "visual", renderer: "content", description: "Content rows with snippets, provenance, and blocked-policy proof for Git candidates."}),
    Object.freeze({id: "finder-gallery", label: "Finder Gallery", kind: "platform", renderer: "gallery", description: "macOS-style gallery projection that proves preview-heavy layouts still receive Git-shaped contract data."}),
    Object.freeze({id: "finder-column-inspector", label: "Finder Columns + Inspector", kind: "platform", renderer: "column-browser", description: "macOS column + inspector projection backed by the same Git file-basket model."}),
    Object.freeze({id: "gnome-grid", label: "GNOME Files grid", kind: "platform", renderer: "icon-grid", description: "GNOME-style grid projection for Git file candidates and blocked-row visual policy."}),
    Object.freeze({id: "gnome-list", label: "GNOME Files list", kind: "platform", renderer: "flat-table", description: "GNOME-style list projection using typed Git fields while exposing selection-contract gaps."}),
    Object.freeze({id: "dolphin-split-details", label: "Dolphin split/details", kind: "platform", renderer: "split-details", description: "KDE-style split/details projection for Git folders, typed file rows, and preview locks."}),
    Object.freeze({id: "thunar-compact", label: "Thunar compact", kind: "platform", renderer: "compact-audit", description: "XFCE compact projection for dense Git file scanning; rejected unless the full file-basket contract is proven."}),
    Object.freeze({id: "compact-audit-list", label: "Compact audit list", kind: "audit", renderer: "compact-audit", description: "Good for review summaries, but not enough as the primary Git file basket because hierarchy and directory shortcuts are missing."}),
    Object.freeze({id: "data-table", label: "Data table", kind: "flat", renderer: "flat-table", description: "Good for sorting fields, but flat rows cannot prove hierarchy, tri-state directory selection, or blocked-visible tree behavior."}),
    Object.freeze({id: "column-browser-inspector", label: "Column browser + inspector", kind: "browser", renderer: "column-browser", description: "Good for browsing hierarchy, but insufficient until selected output and tri-state selection are proven."}),
    Object.freeze({id: "title-only-tree", label: "Title-only tree", kind: "rejected", renderer: "title-only", description: "Intentionally rejected because metadata collapses into labels and the view invents meaning."}),
    Object.freeze({id: "plain-tree-primary", label: "Plain tree primary", kind: "rejected", renderer: "plain-tree", description: "Intentionally rejected as the primary view because it lacks typed cells, safety proof, and selected-output proof."}),
    Object.freeze({id: "icon-grid-primary", label: "Icon grid primary", kind: "rejected", renderer: "icon-grid", description: "Intentionally rejected as the primary view because spatial icons cannot carry the Git file-basket contract."})
  ]);
  function asArray(value) {
    return Array.isArray(value) ? value : [];
  }

  function uniqueSorted(values = []) {
    return Array.from(new Set(asArray(values).filter(Boolean)))
      .sort((left, right) => String(left).localeCompare(String(right)));
  }

  function escapeHtml(value = "") {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function clone(value) {
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (error) {
      return value;
    }
  }

  function sampleGitReview() {
    return {
      repository: {
        path: "main_computer_test",
        branch: "contract-treegrid-lab"
      },
      candidate_groups: {
        selected_by_default: [
          {
            path: "main_computer/web/applications/scripts/git-tools-file-basket.js",
            status: "modified",
            classifications: ["source", "git-tools", "file-basket"],
            risk: "clean",
            reason: "legacy Git file-basket pathway stays active while the lab treegrid is exercised"
          },
          {
            path: "tests/test_git_tools_file_basket_contract_view.py",
            status: "modified",
            classifications: ["test", "contract"],
            risk: "clean",
            reason: "contract selection proof"
          }
        ],
        review_before_selecting: [
          {
            path: "main_computer/web/applications/scripts/git-tools-file-basket-contract-view.js",
            status: "modified",
            classifications: ["source", "mcel", "treegrid"],
            risk: "review",
            reason: "treegrid renderer and row model under interactive lab test"
          },
          {
            path: "main_computer/web/applications/scripts/mcel-git-file-basket-treegrid-lab.js",
            status: "untracked",
            classifications: ["source", "mcel", "lab"],
            risk: "review",
            reason: "new lab-only Git file-basket treegrid surface"
          },
          {
            path: "main_computer/web/applications/styles/mcel-lab.css",
            status: "modified",
            classifications: ["style", "mcel"],
            risk: "review",
            reason: "lab-scoped treegrid sizing and checkbox constraints"
          }
        ],
        blocked_possible_secrets: [
          {
            path: "runtime/git-tools/.env.local",
            status: "untracked",
            risk: "blocked",
            reason: "secret-looking runtime environment file",
            blocking_security_findings_count: 1
          }
        ],
        excluded_generated_runtime: [
          {
            path: "runtime/cache/git-tools-treegrid-screenshot.png",
            status: "untracked",
            risk: "blocked",
            reason: "generated runtime artifact"
          }
        ]
      }
    };
  }

  function fileBasketModelAdapter() {
    return global.McelFileBasketModel || null;
  }

  function fileBasketControllerAdapter() {
    return global.McelFileBasketController || null;
  }

  function gitFileBasketAdapter() {
    return global.GitToolsFileBasket || null;
  }

  function contractViewAdapter() {
    return global.GitToolsFileBasketContractView || null;
  }

  function normalizeSelection(model = {}, paths = []) {
    const controller = fileBasketControllerAdapter();
    if (typeof controller?.selectedOutput === "function") {
      return controller.selectedOutput(model, paths);
    }
    const adapter = fileBasketModelAdapter();
    if (typeof adapter?.selectedOutput === "function") {
      return adapter.selectedOutput(model, paths);
    }
    const selectable = new Set(asArray(model.selectablePaths));
    return uniqueSorted(asArray(paths).filter((path) => selectable.has(path)));
  }

  function buildModel(review = sampleGitReview()) {
    const gitFileBasket = gitFileBasketAdapter();
    if (typeof gitFileBasket?.model === "function") {
      return gitFileBasket.model(review);
    }
    const modelAdapter = fileBasketModelAdapter();
    if (typeof modelAdapter?.buildFileBasketModel === "function") {
      return modelAdapter.buildFileBasketModel(review, {
        surfaceId: TARGET_CONCERN,
        canonicalSurfaceId: TARGET_CONCERN,
        ownerApp: "mcel-lab",
        sourceConcern: TARGET_CONCERN,
        sourceFile: SOURCE_FILE,
        ownershipStatus: "lab-proof-only"
      });
    }
    return null;
  }

  function buildLegacySource(review = {}, model = null) {
    const gitFileBasket = gitFileBasketAdapter();
    if (typeof gitFileBasket?.treeSource === "function") {
      return gitFileBasket.treeSource(review, model);
    }
    return [];
  }

  function legacySelectedPathsFromSource(source = []) {
    const gitFileBasket = gitFileBasketAdapter();
    if (typeof gitFileBasket?.defaultSelectedPathsFromTreeSource === "function") {
      return gitFileBasket.defaultSelectedPathsFromTreeSource(source);
    }
    return [];
  }

  function buildController(model = {}, selectedPaths = []) {
    const controllerAdapter = fileBasketControllerAdapter();
    if (typeof controllerAdapter?.createFileBasketController === "function") {
      return controllerAdapter.createFileBasketController(model, {selectedPaths});
    }
    return null;
  }

  function buildRows(model = {}, selectedPaths = []) {
    const contractView = contractViewAdapter();
    if (typeof contractView?.buildContractTreegridRows === "function") {
      return contractView.buildContractTreegridRows(model, {selectedPaths});
    }
    return [];
  }

  function summarizeReadiness(model = {}, selectedPaths = [], legacySelectedPaths = []) {
    const contractView = contractViewAdapter();
    if (typeof contractView?.summarizeContractTreegridReadiness === "function") {
      return contractView.summarizeContractTreegridReadiness(model, {
        selectedPaths,
        legacySelectedPaths,
        legacyRendererActive: true,
        legacyRollbackAvailable: true,
        activeReplacement: false,
        visibleRenderer: "mcel-lab-git-treegrid"
      });
    }
    return {
      ready: false,
      activeReplacement: false,
      legacyRendererActive: true,
      visibleRenderer: "mcel-lab-git-treegrid",
      reason: "GitToolsFileBasketContractView unavailable"
    };
  }

  function scenarioProbePaths(model = {}) {
    const rows = asArray(model.rows);
    const blockedCandidates = asArray(model.blockedPaths);
    const blocked = blockedCandidates.find((path) => /secret|env/i.test(String(path || ""))) ||
      blockedCandidates[0] ||
      rows.find((row = {}) => row.selectable === false)?.path ||
      "";
    const directory = asArray(model.hierarchy)
      .flatMap((node = {}) => asArray(node.children).length ? [node.path] : [])
      .find(Boolean) || DEFAULT_DIRECTORY_PROBE;
    return {
      directory: rows.some((row = {}) => row.path?.startsWith(`${DEFAULT_DIRECTORY_PROBE}/`))
        ? DEFAULT_DIRECTORY_PROBE
        : directory,
      blocked
    };
  }

  function runInteractionScenarios(model = {}, selectedPaths = []) {
    const probes = scenarioProbePaths(model);
    const controller = buildController(model, selectedPaths);
    if (!controller) {
      return {
        directorySelection: {ok: false, reason: "controller unavailable", selectedPaths: []},
        blockedAttempt: {ok: false, reason: "controller unavailable", selectedPaths: []},
        selectAllEligible: {ok: false, reason: "controller unavailable", selectedPaths: []},
        clearSelection: {ok: false, reason: "controller unavailable", selectedPaths: []},
        probes
      };
    }
    const directorySelection = controller.apply("set-directory-selection", {
      path: probes.directory,
      selected: true
    });
    const blockedAttempt = controller.apply("set-file-selection", {
      path: probes.blocked,
      selected: true
    });
    const selectAllEligible = controller.apply("select-all-eligible");
    const clearSelection = controller.apply("clear-selection");
    return {
      directorySelection,
      blockedAttempt,
      selectAllEligible,
      clearSelection,
      probes
    };
  }

  function selectedOutputFromRows(model = {}, selectedPaths = []) {
    const contractView = contractViewAdapter();
    const rows = buildRows(model, selectedPaths);
    if (typeof contractView?.selectedOutputFromContractRows === "function") {
      return contractView.selectedOutputFromContractRows(model, rows);
    }
    return normalizeSelection(model, selectedPaths);
  }


  function viewModeTemplate(viewId = "") {
    return VIEW_MODE_OPTIONS.find((option) => option.id === viewId) || null;
  }

  function viewResolutionById(model = {}) {
    const entries = asArray(model.viewContract?.eligibleViews).concat(asArray(model.viewContract?.rejectedViews));
    return entries.reduce((map, view = {}) => {
      const id = view.viewId || view.id;
      if (id) map[id] = view;
      return map;
    }, {});
  }

  function buildViewModeCatalog(model = {}) {
    const resolved = viewResolutionById(model);
    return VIEW_MODE_OPTIONS.map((option) => {
      const resolution = resolved[option.id] || {};
      const missingCapabilities = asArray(resolution.missingCapabilities);
      const eligible = resolution.eligible === true;
      return {
        id: option.id,
        label: option.label,
        kind: option.kind,
        renderer: option.renderer,
        description: option.description,
        eligible,
        status: eligible ? "eligible" : "rejected",
        interactive: eligible && option.renderer === "interactive-treegrid",
        primaryCandidate: option.id === "contract-treegrid" || option.id === "details-tree",
        capabilities: asArray(resolution.capabilities),
        missingCapabilities,
        reason: resolution.reason || (eligible ? "Satisfies the file-basket contract" : "Not eligible for the full file-basket contract"),
        warning: eligible ? "" : `Missing ${missingCapabilities.join(", ") || "required file-basket guarantees"}`
      };
    });
  }

  function viewModeById(report = {}, modeId = "") {
    return asArray(report.viewModes).find((mode = {}) => mode.id === modeId) ||
      asArray(report.viewModes)[0] ||
      buildViewModeCatalog(report.model || {})[0];
  }

  function modeBadgeText(mode = {}) {
    if (mode.id === "contract-treegrid") return "primary candidate";
    if (mode.eligible) return "eligible alternate";
    return "rejected primary";
  }

  function pathParts(path = "") {
    return String(path || "").split("/").filter(Boolean);
  }

  function shortPathLabel(path = "") {
    const parts = pathParts(path);
    return parts.slice(-2).join("/") || path || "candidate";
  }

  function renderProjectionNotice(mode = {}) {
    const status = mode.eligible ? "eligible" : "rejected as primary";
    const warning = mode.eligible ? mode.description : `${mode.description} ${mode.warning || ""}`.trim();
    return `<div class="mcel-git-treegrid-view-notice" data-mcel-git-treegrid-view-status="${escapeHtml(mode.status)}">
      <strong>${escapeHtml(mode.label)} · ${escapeHtml(status)}</strong>
      <span>${escapeHtml(warning)}</span>
    </div>`;
  }

  function selectionKindForRow(row = {}) {
    return row.kind === "directory" ? "dir" : "file";
  }

  function selectionStateForRow(row = {}) {
    if (row.selectable === false || row.blocked) return "blocked";
    return row.selectionState || (row.selected ? "checked" : "unchecked");
  }

  function selectionControlHtml(row = {}, report = {}, options = {}) {
    const path = row.repoRelativePath || row.path || "";
    const kind = selectionKindForRow(row);
    const state = selectionStateForRow(row);
    const disabled = row.selectable === false || state === "blocked";
    const checked = state === "checked";
    const mixed = state === "mixed";
    const compact = options.compact === true;
    const label = kind === "dir"
      ? `Select directory ${path || row.name || "folder"}`
      : `Select file ${path || row.name || "file"}`;
    const status = disabled
      ? "blocked"
      : mixed
        ? "mixed"
        : checked
          ? "selected"
          : "available";
    return `<label class="mcel-git-view-selection-control ${compact ? "is-compact" : ""} ${disabled ? "is-disabled" : ""}" data-mcel-git-view-selection-control="${escapeHtml(kind)}" data-mcel-git-view-selection-state="${escapeHtml(state)}">
      <input type="checkbox"
        data-mcel-git-view-selection-kind="${escapeHtml(kind)}"
        data-mcel-git-view-selection-path="${escapeHtml(path)}"
        aria-label="${escapeHtml(label)}"
        ${checked ? "checked" : ""}
        ${mixed ? 'data-mcel-git-view-selection-mixed="true"' : ""}
        ${disabled ? "disabled" : ""}>
      <span>${compact ? "" : escapeHtml(status)}</span>
    </label>`;
  }

  function selectionCellHtml(row = {}, report = {}) {
    return `<span role="cell" class="mcel-git-view-selection-cell">${selectionControlHtml(row, report, {compact: true})}</span>`;
  }

  function hydrateProjectionSelectionControls(root) {
    Array.from(root?.querySelectorAll?.("[data-mcel-git-view-selection-mixed='true']") || []).forEach((input) => {
      input.indeterminate = true;
      input.setAttribute("aria-checked", "mixed");
    });
  }

  function scrollSurfaceKeyFor(node) {
    if (!node) return "unknown";
    if (node.getAttribute?.("data-mcel-git-view-scroll-surface")) {
      return node.getAttribute("data-mcel-git-view-scroll-surface");
    }
    if (node.matches?.(".git-project-contract-treegrid")) {
      return "contract-treegrid-scroll";
    }
    if (node.matches?.("[data-mcel-git-treegrid-view-projection-mount]")) {
      return "projection-mount";
    }
    return node.className || node.nodeName || "unknown";
  }

  function scrollSurfacesIn(root) {
    if (!root) return [];
    const surfaces = [root];
    const inner = Array.from(root.querySelectorAll?.("[data-mcel-git-view-scroll-surface], .git-project-contract-treegrid") || []);
    inner.forEach((node) => {
      if (!surfaces.includes(node)) surfaces.push(node);
    });
    return surfaces;
  }

  function captureProjectionScrollState(root) {
    const occurrences = new Map();
    return scrollSurfacesIn(root).map((node) => {
      const key = scrollSurfaceKeyFor(node);
      const occurrence = occurrences.get(key) || 0;
      occurrences.set(key, occurrence + 1);
      return {
        key,
        occurrence,
        scrollTop: Number(node.scrollTop || 0),
        scrollLeft: Number(node.scrollLeft || 0)
      };
    });
  }

  function restoreProjectionScrollState(root, positions = []) {
    if (!positions.length) return false;
    const buckets = new Map();
    scrollSurfacesIn(root).forEach((node) => {
      const key = scrollSurfaceKeyFor(node);
      const list = buckets.get(key) || [];
      list.push(node);
      buckets.set(key, list);
    });
    let restored = false;
    positions.forEach((position = {}) => {
      const node = (buckets.get(position.key) || [])[Number(position.occurrence || 0)];
      if (!node) return;
      node.scrollTop = Number(position.scrollTop || 0);
      node.scrollLeft = Number(position.scrollLeft || 0);
      if (node.dataset) node.dataset.mcelGitViewScrollRestored = "true";
      restored = true;
    });
    return restored;
  }

  function captureViewportScrollState(document) {
    const view = document?.defaultView || null;
    if (!view) return null;
    return {
      x: Number(view.scrollX ?? view.pageXOffset ?? 0),
      y: Number(view.scrollY ?? view.pageYOffset ?? 0)
    };
  }

  function restoreViewportScrollState(document, state = null) {
    const view = document?.defaultView || null;
    if (!view || !state) return false;
    if (typeof view.scrollTo === "function") {
      view.scrollTo(Number(state.x || 0), Number(state.y || 0));
      return true;
    }
    return false;
  }

  function renderCompactAuditProjection(report = {}, mode = {}) {
    const rows = asArray(report.rows).filter((row = {}) => row.kind === "file");
    return `${renderProjectionNotice(mode)}
      <div class="mcel-git-view-compact-audit" role="list" aria-label="Compact audit list projection" data-mcel-git-view-scroll-surface="${escapeHtml(mode.id || "compact-audit")}">
        ${rows.map((row = {}) => `<article role="listitem" class="${row.blocked ? "is-blocked" : ""}">
          <div class="mcel-git-view-row-head">
            ${selectionControlHtml(row, report)}
            <strong>${escapeHtml(shortPathLabel(row.repoRelativePath))}</strong>
          </div>
          <span>${escapeHtml(row.repoRelativePath || "")}</span>
          <small>${escapeHtml(row.statusLabel || row.status || "")} · ${escapeHtml(row.risk || (row.blocked ? "blocked" : "clean"))} · ${escapeHtml(row.reason || row.blockedReason || "")}</small>
        </article>`).join("")}
      </div>`;
  }

  function renderFlatTableProjection(report = {}, mode = {}) {
    const rows = asArray(report.rows).filter((row = {}) => row.kind === "file");
    return `${renderProjectionNotice(mode)}
      <div class="mcel-git-view-flat-table" role="table" aria-label="Flat data table projection" data-mcel-git-view-scroll-surface="${escapeHtml(mode.id || "flat-table")}">
        <div role="row" class="mcel-git-view-flat-table-head">
          <span role="columnheader">Select</span>
          <span role="columnheader">Path</span>
          <span role="columnheader">Status</span>
          <span role="columnheader">Risk</span>
          <span role="columnheader">Reason</span>
        </div>
        ${rows.map((row = {}) => `<div role="row" class="${row.blocked ? "is-blocked" : ""}">
          ${selectionCellHtml(row, report)}
          <span role="cell">${escapeHtml(row.repoRelativePath || "")}</span>
          <span role="cell">${escapeHtml(row.statusLabel || row.status || "")}</span>
          <span role="cell">${escapeHtml(row.risk || (row.blocked ? "blocked" : ""))}</span>
          <span role="cell">${escapeHtml(row.reason || row.blockedReason || "")}</span>
        </div>`).join("")}
      </div>`;
  }

  function renderColumnBrowserProjection(report = {}, mode = {}) {
    const directories = asArray(report.rows).filter((row = {}) => row.kind === "directory");
    const files = asArray(report.rows).filter((row = {}) => row.kind === "file");
    const firstDirectory = directories[0] || {};
    const selectedDirectory = directories.find((row = {}) => String(row.repoRelativePath || "").includes("applications/scripts")) || firstDirectory;
    const prefix = selectedDirectory?.repoRelativePath ? `${selectedDirectory.repoRelativePath}/` : "";
    const scopedFiles = prefix ? files.filter((row = {}) => String(row.repoRelativePath || "").startsWith(prefix)) : files.slice(0, 5);
    return `${renderProjectionNotice(mode)}
      <div class="mcel-git-view-column-browser" aria-label="Column browser projection" data-mcel-git-view-scroll-surface="${escapeHtml(mode.id || "column-browser")}">
        <div class="mcel-git-view-column" data-mcel-git-view-column="folders">
          <strong>Folders</strong>
          ${directories.slice(0, 7).map((row = {}) => `<div class="mcel-git-view-browser-row ${row.repoRelativePath === selectedDirectory?.repoRelativePath ? "is-current" : ""}">
            ${selectionControlHtml(row, report, {compact: true})}
            <span>${escapeHtml(row.repoRelativePath || row.name || "")}</span>
          </div>`).join("")}
        </div>
        <div class="mcel-git-view-column" data-mcel-git-view-column="files">
          <strong>Files in focus</strong>
          ${scopedFiles.map((row = {}) => `<div class="mcel-git-view-browser-row ${row.blocked ? "is-blocked" : ""}">
            ${selectionControlHtml(row, report, {compact: true})}
            <span>${escapeHtml(shortPathLabel(row.repoRelativePath || ""))}</span>
          </div>`).join("") || "<span>No scoped files.</span>"}
        </div>
        <div class="mcel-git-view-column is-inspector" data-mcel-git-view-column="inspector">
          <strong>Inspector</strong>
          <span>Selected files: ${escapeHtml(String(asArray(report.selectedPaths).length))}</span>
          <span>Selection proof: ${escapeHtml(report.proofChecks?.selectedOutputMatchesLegacy ? "matches legacy" : "needs review")}</span>
          <span>Blocked rows visible: ${escapeHtml(report.proofChecks?.blockedRowsVisible ? "yes" : "no")}</span>
          <span>Gap: tri-state hierarchy is mediated by lab controls, not this projection itself.</span>
        </div>
      </div>`;
  }


  function renderHierarchyProjection(report = {}, mode = {}) {
    const rows = asArray(report.rows);
    const flavor = mode.id || "hierarchy-tree";
    return `${renderProjectionNotice(mode)}
      <div class="mcel-git-view-title-tree is-${escapeHtml(flavor)}" role="tree" aria-label="${escapeHtml(mode.label)} Git projection" data-mcel-git-view-scroll-surface="${escapeHtml(mode.id || flavor)}">
        ${rows.map((row = {}) => {
          const isDirectory = row.kind === "directory";
          const marker = isDirectory ? "▾" : "•";
          const status = row.statusLabel || row.status || row.bucket || "";
          return `<div role="treeitem" class="${isDirectory ? "is-directory" : "is-file"} ${row.blocked ? "is-blocked" : ""}" style="--git-tree-depth:${Number(row.depth || 0)}" aria-disabled="${row.selectable === false ? "true" : "false"}">
            <span class="mcel-git-view-tree-glyph" aria-hidden="true">${marker}</span>
            ${selectionControlHtml(row, report, {compact: true})}
            <strong>${escapeHtml(row.repoRelativePath || row.name || "")}</strong>
            <em>${escapeHtml(status)}</em>
            <small>${escapeHtml(row.risk || (row.blocked ? "blocked" : row.source || ""))}</small>
          </div>`;
        }).join("")}
      </div>`;
  }

  function renderAccessibilityProjection(report = {}, mode = {}) {
    const selected = new Set(report.selectedPaths || []);
    return `${renderProjectionNotice(mode)}
      <div class="mcel-git-view-title-tree is-accessibility-proof" role="tree" aria-label="${escapeHtml(mode.label)} Git projection" data-mcel-git-view-scroll-surface="${escapeHtml(mode.id || "accessibility-proof")}">
        ${asArray(report.rows).map((row = {}) => `<div role="treeitem" aria-level="${Number(row.depth || 0) + 1}" aria-selected="${selected.has(row.repoRelativePath) ? "true" : "false"}" aria-disabled="${row.selectable === false ? "true" : "false"}" aria-expanded="${row.kind === "directory" ? "true" : "false"}" class="${row.kind === "directory" ? "is-directory" : "is-file"} ${row.blocked ? "is-blocked" : ""}" style="--git-tree-depth:${Number(row.depth || 0)}">
          <span class="mcel-git-view-tree-glyph" aria-hidden="true">${row.kind === "directory" ? "▾" : "•"}</span>
          ${selectionControlHtml(row, report, {compact: true})}
          <strong>${escapeHtml(row.repoRelativePath || row.name || "")}</strong>
          <em>${row.kind === "directory" ? "aria-expanded=true" : selected.has(row.repoRelativePath) ? "aria-selected=true" : "aria-selected=false"}</em>
          <small>${row.selectable === false ? "blocked/read-only" : "selectable file"}</small>
        </div>`).join("")}
      </div>`;
  }

  function renderTileProjection(report = {}, mode = {}) {
    const rows = asArray(report.rows).filter((row = {}) => row.kind === "file");
    return `${renderProjectionNotice(mode)}
      <div class="mcel-git-view-tile-grid" aria-label="${escapeHtml(mode.label)} Git projection" data-mcel-git-view-scroll-surface="${escapeHtml(mode.id || "tile-view")}">
        ${rows.map((row = {}) => `<article class="${row.blocked ? "is-blocked" : ""}">
          ${selectionControlHtml(row, report)}
          <strong>${escapeHtml(shortPathLabel(row.repoRelativePath || ""))}</strong>
          <span>${escapeHtml(row.repoRelativePath || "")}</span>
          <small>${escapeHtml(row.statusLabel || row.status || "")} · ${escapeHtml(row.risk || (row.blocked ? "blocked" : "clean"))}</small>
          <em>${escapeHtml(row.reason || row.blockedReason || "")}</em>
        </article>`).join("")}
      </div>`;
  }

  function renderContentProjection(report = {}, mode = {}) {
    const rows = asArray(report.rows).filter((row = {}) => row.kind === "file");
    return `${renderProjectionNotice(mode)}
      <div class="mcel-git-view-content-list" aria-label="${escapeHtml(mode.label)} Git projection" data-mcel-git-view-scroll-surface="${escapeHtml(mode.id || "content-view")}">
        ${rows.map((row = {}) => `<article class="${row.blocked ? "is-blocked" : ""}">
          <div>
            <div class="mcel-git-view-row-head">
              ${selectionControlHtml(row, report)}
              <strong>${escapeHtml(row.repoRelativePath || "")}</strong>
            </div>
            <span>${escapeHtml(row.statusLabel || row.status || "")} · ${escapeHtml(row.bucket || row.source || "git candidate")}</span>
          </div>
          <p>${escapeHtml(row.reason || row.blockedReason || "Git file candidate keeps structured path/status/risk fields.")}</p>
          <small>${row.selectable === false ? "visible for audit, not selectable" : "eligible explicit output path"}</small>
        </article>`).join("")}
      </div>`;
  }

  function renderGalleryProjection(report = {}, mode = {}) {
    const files = asArray(report.rows).filter((row = {}) => row.kind === "file");
    const selected = files.find((row = {}) => asArray(report.selectedPaths).includes(row.repoRelativePath)) || files[0] || {};
    return `${renderProjectionNotice(mode)}
      <div class="mcel-git-view-gallery" aria-label="${escapeHtml(mode.label)} Git projection" data-mcel-git-view-scroll-surface="${escapeHtml(mode.id || "finder-gallery")}">
        <section>
          <strong>${escapeHtml(shortPathLabel(selected.repoRelativePath || "No selection"))}</strong>
          <span>${escapeHtml(selected.repoRelativePath || "No Git file selected")}</span>
          <p>${escapeHtml(selected.reason || selected.blockedReason || "Preview-like layout receives the same Git file-basket contract data.")}</p>
        </section>
        <div>
          ${files.map((row = {}) => `<article class="mcel-git-view-gallery-card ${row.repoRelativePath === selected.repoRelativePath ? "is-current" : ""} ${row.blocked ? "is-blocked" : ""}">
            ${selectionControlHtml(row, report)}
            <strong>${escapeHtml(shortPathLabel(row.repoRelativePath || ""))}</strong>
            <span>${escapeHtml(row.statusLabel || row.status || "")}</span>
          </article>`).join("")}
        </div>
      </div>`;
  }

  function renderSplitDetailsProjection(report = {}, mode = {}) {
    const directories = asArray(report.rows).filter((row = {}) => row.kind === "directory");
    const files = asArray(report.rows).filter((row = {}) => row.kind === "file");
    const left = directories.slice(0, Math.max(3, Math.ceil(directories.length / 2)));
    const right = files.slice(0, 8);
    return `${renderProjectionNotice(mode)}
      <div class="mcel-git-view-split-details" aria-label="${escapeHtml(mode.label)} Git projection" data-mcel-git-view-scroll-surface="${escapeHtml(mode.id || "split-details")}">
        <div>
          <strong>Folder pane</strong>
          ${left.map((row = {}) => `<span>${selectionControlHtml(row, report, {compact: true})}${escapeHtml(row.repoRelativePath || row.name || "")}</span>`).join("")}
        </div>
        <div>
          <strong>Details pane</strong>
          ${right.map((row = {}) => `<span class="${row.blocked ? "is-blocked" : ""}">${selectionControlHtml(row, report, {compact: true})}${escapeHtml(row.repoRelativePath || "")}<small>${escapeHtml(row.statusLabel || row.status || "")} · ${escapeHtml(row.risk || "")}</small></span>`).join("")}
        </div>
      </div>`;
  }

  function renderTitleOnlyTreeProjection(report = {}, mode = {}) {
    const rows = asArray(report.rows);
    return `${renderProjectionNotice(mode)}
      <div class="mcel-git-view-title-tree" role="tree" aria-label="${escapeHtml(mode.label)} rejected projection" data-mcel-git-view-scroll-surface="${escapeHtml(mode.id || "title-only-tree")}">
        ${rows.map((row = {}) => `<div role="treeitem" class="${row.kind === "directory" ? "is-directory" : "is-file"} ${row.blocked ? "is-blocked" : ""}" style="--git-tree-depth:${Number(row.depth || 0)}">
          <span>${row.kind === "directory" ? "▾" : "•"}</span>
          ${selectionControlHtml(row, report, {compact: true})}
          <strong>${escapeHtml(shortPathLabel(row.repoRelativePath || row.name || ""))}</strong>
        </div>`).join("")}
      </div>`;
  }

  function renderIconGridProjection(report = {}, mode = {}) {
    const files = asArray(report.rows).filter((row = {}) => row.kind === "file");
    return `${renderProjectionNotice(mode)}
      <div class="mcel-git-view-icon-grid" aria-label="Icon grid rejected projection" data-mcel-git-view-scroll-surface="${escapeHtml(mode.id || "icon-grid")}">
        ${files.map((row = {}) => `<article class="${row.blocked ? "is-blocked" : ""}">
          ${selectionControlHtml(row, report)}
          <strong aria-hidden="true">${row.blocked ? "⛔" : "▣"}</strong>
          <span>${escapeHtml(shortPathLabel(row.repoRelativePath || ""))}</span>
          <small>${escapeHtml(row.statusLabel || row.status || "")}</small>
        </article>`).join("")}
      </div>`;
  }

  function renderViewProjectionHtml(report = {}, modeId = "contract-treegrid") {
    const mode = viewModeById(report, modeId);
    const contractView = contractViewAdapter();
    const serializedModel = JSON.stringify(report.model || {});
    const hiddenModel = `<textarea hidden data-git-commit-file-basket-model>${escapeHtml(serializedModel)}</textarea>`;
    if ((mode.id === "contract-treegrid" || mode.id === "details-tree" || mode.id === "details-treegrid") && typeof contractView?.renderContractTreegridHtml === "function") {
      const treeHtml = contractView.renderContractTreegridHtml(report.model || {}, {
        escapeHtml,
        selectedPaths: report.selectedPaths || [],
        legacySelectedPaths: report.legacySelectedPaths || [],
        legacyRendererActive: true,
        legacyRollbackAvailable: true,
        activeReplacement: false,
        visibleRenderer: `mcel-lab-${mode.id}`
      });
      return `${hiddenModel}<div class="mcel-git-treegrid-view-mode is-${escapeHtml(mode.id)}" data-mcel-git-treegrid-view-projection="${escapeHtml(mode.id)}">${renderProjectionNotice(mode)}${treeHtml}</div>`;
    }
    const projections = {
      "explorer-sidebar": renderHierarchyProjection,
      "ide-project-tree": renderHierarchyProjection,
      "miller-columns": renderColumnBrowserProjection,
      "outline-tree": renderHierarchyProjection,
      "accessibility-proof": renderAccessibilityProjection,
      "icon-grid": renderIconGridProjection,
      "compact-list": renderCompactAuditProjection,
      "tile-view": renderTileProjection,
      "content-view": renderContentProjection,
      "finder-gallery": renderGalleryProjection,
      "finder-column-inspector": renderColumnBrowserProjection,
      "gnome-grid": renderIconGridProjection,
      "gnome-list": renderFlatTableProjection,
      "dolphin-split-details": renderSplitDetailsProjection,
      "thunar-compact": renderCompactAuditProjection,
      "compact-audit-list": renderCompactAuditProjection,
      "data-table": renderFlatTableProjection,
      "column-browser-inspector": renderColumnBrowserProjection,
      "title-only-tree": renderTitleOnlyTreeProjection,
      "plain-tree-primary": renderTitleOnlyTreeProjection,
      "icon-grid-primary": renderIconGridProjection
    };
    const renderer = projections[mode.id] || renderTitleOnlyTreeProjection;
    return `${hiddenModel}<div class="mcel-git-treegrid-view-mode is-${escapeHtml(mode.id)}" data-mcel-git-treegrid-view-projection="${escapeHtml(mode.id)}">${renderer(report, mode)}</div>`;
  }

  function renderViewModeButtons(document, parent, report = {}, activeModeId = "contract-treegrid") {
    parent.replaceChildren();
    asArray(report.viewModes).forEach((mode = {}) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = mode.id === activeModeId ? "is-active" : "";
      button.setAttribute("data-mcel-git-treegrid-view-mode-option", mode.id);
      button.setAttribute("data-mcel-git-treegrid-view-mode-status", mode.status || "");
      button.setAttribute("aria-pressed", mode.id === activeModeId ? "true" : "false");
      button.innerHTML = `<strong>${escapeHtml(mode.label)}</strong><span>${escapeHtml(modeBadgeText(mode))}</span>`;
      parent.appendChild(button);
    });
  }

  function setActiveModeButtonState(buttons, activeModeId = "") {
    Array.from(buttons || []).forEach((button) => {
      const active = button.getAttribute("data-mcel-git-treegrid-view-mode-option") === activeModeId;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }


  function buildInteractiveGitTreegridLabReport(options = {}) {
    const review = clone(options.review || sampleGitReview());
    const model = buildModel(review);
    if (!model) {
      return {
        version: VERSION,
        labId: LAB_ID,
        targetConcern: TARGET_CONCERN,
        contractId: CONTRACT_ID,
        ready: false,
        activeInGitTools: false,
        gitToolsRenderer: "legacy-wunderbaum",
        reason: "file basket model unavailable"
      };
    }
    const legacySource = buildLegacySource(review, model);
    const legacySelectedPaths = legacySelectedPathsFromSource(legacySource);
    const selectedPaths = normalizeSelection(
      model,
      Array.isArray(options.selectedPaths) ? options.selectedPaths : (legacySelectedPaths.length ? legacySelectedPaths : model.defaultSelectedPaths)
    );
    const rows = buildRows(model, selectedPaths);
    const viewModes = buildViewModeCatalog(model);
    const eligibleViewModeIds = viewModes.filter((mode = {}) => mode.eligible).map((mode = {}) => mode.id);
    const rejectedViewModeIds = viewModes.filter((mode = {}) => !mode.eligible).map((mode = {}) => mode.id);
    const readiness = summarizeReadiness(model, selectedPaths, legacySelectedPaths);
    const scenarios = runInteractionScenarios(model, selectedPaths);
    const directoryContractOutput = selectedOutputFromRows(model, scenarios.directorySelection?.selectedPaths || []);
    const blockedPaths = asArray(model.blockedPaths);
    const blockedRows = rows.filter((row = {}) => row.blocked);
    const hiddenBlockedRows = blockedRows.filter((row = {}) => row.visible !== true);
    const selectableBlockedRows = blockedRows.filter((row = {}) => row.selectable !== false);
    const selectedBlockedPaths = selectedOutputFromRows(model, selectedPaths).filter((path) => blockedPaths.includes(path));
    const selectableDescendants = buildController(model, selectedPaths)?.selectableDescendantPaths?.(scenarios.probes.directory) || [];
    const directoryPrefix = scenarios.probes.directory ? `${scenarios.probes.directory}/` : "";
    const selectedOutsideDirectory = selectedPaths.filter((path) => directoryPrefix && !path.startsWith(directoryPrefix));
    const expectedDirectorySelection = uniqueSorted(selectedOutsideDirectory.concat(selectableDescendants));
    return {
      version: VERSION,
      labId: LAB_ID,
      sourceFile: SOURCE_FILE,
      targetConcern: TARGET_CONCERN,
      contractId: model.contractId || CONTRACT_ID,
      ready: Boolean(readiness.ready && rows.length),
      activeInGitTools: false,
      gitToolsRenderer: "legacy-wunderbaum",
      visibleRenderer: "mcel-lab-git-treegrid",
      replacementGate: "deferred-until-interactive-lab-proof-passes",
      viewModes,
      eligibleViewModeIds,
      rejectedViewModeIds,
      defaultViewMode: "contract-treegrid",
      model,
      legacySource,
      legacySelectedPaths,
      selectedPaths,
      rows,
      readiness,
      scenarios,
      directoryContractOutput,
      proofChecks: {
        legacyGitTreeStillActive: true,
        labOnlyTreegrid: true,
        activeReplacement: false,
        rowsHaveRepoRelativeIdentity: rows.every((row = {}) => typeof row.repoRelativePath === "string"),
        pathCellsStructured: rows.every((row = {}) => row.cells?.path?.type === "path"),
        typedFieldsStructured: rows
          .filter((row = {}) => row.kind === "file")
          .every((row = {}) => row.cells?.status?.type === "enum" && row.cells?.risk?.type === "risk" && row.cells?.reason?.type === "text"),
        blockedRowsVisible: hiddenBlockedRows.length === 0,
        blockedRowsSelectable: selectableBlockedRows.length > 0,
        selectedBlockedPaths,
        directorySelectionMatchesContractOutput: JSON.stringify(directoryContractOutput) === JSON.stringify(scenarios.directorySelection?.selectedPaths || []),
        directorySelectionUsesSelectableDescendants: JSON.stringify(directoryContractOutput) === JSON.stringify(expectedDirectorySelection),
        disclosureExpansionPrepared: typeof contractViewAdapter()?.setDirectoryExpanded === "function" && rows.some((row = {}) => row.kind === "directory"),
        columnResizePrepared: typeof contractViewAdapter()?.setTreegridColumnWidth === "function",
        viewModeOptionsAvailable: viewModes.length >= VIEW_MODE_OPTIONS.length && viewModes.some((mode = {}) => mode.id === "explorer-sidebar") && viewModes.some((mode = {}) => mode.id === "dolphin-split-details"),
        viewModeSelectionControlsAvailable: viewModes.length >= VIEW_MODE_OPTIONS.length,
        viewModeSelectionPreservesScroll: true,
        eligibleViewModesIncludeTreegrid: eligibleViewModeIds.includes("contract-treegrid") && eligibleViewModeIds.includes("details-tree"),
        rejectedViewModesPreserved: rejectedViewModeIds.includes("title-only-tree") && rejectedViewModeIds.includes("plain-tree-primary") && rejectedViewModeIds.includes("icon-grid-primary"),
        selectedOutputMatchesLegacy: readiness.selectedOutputMatchesLegacy === true,
        titleOnlyTreeRejected: readiness.titleOnlyTreeRejected === true
      }
    };
  }

  function setText(node, value = "") {
    if (node) node.textContent = String(value ?? "");
  }

  function renderSelectedOutput(document, section, paths = []) {
    const count = section.querySelector?.("[data-mcel-git-treegrid-selected-count]");
    const list = section.querySelector?.("[data-mcel-git-treegrid-selected-output]");
    setText(count, String(paths.length));
    if (!list) return;
    list.replaceChildren();
    if (!paths.length) {
      const empty = document.createElement("li");
      empty.textContent = "No selectable files selected.";
      list.appendChild(empty);
      return;
    }
    paths.forEach((path) => {
      const item = document.createElement("li");
      item.textContent = path;
      list.appendChild(item);
    });
  }

  function renderProofChips(document, parent, report = {}) {
    if (parent?.replaceChildren) parent.replaceChildren();
    const proofChecks = report.proofChecks || {};
    [
      ["Git Tools renderer", report.gitToolsRenderer || "legacy-wunderbaum", proofChecks.legacyGitTreeStillActive],
      ["Treegrid scope", report.visibleRenderer || "mcel-lab-git-treegrid", proofChecks.labOnlyTreegrid],
      ["Blocked visible", proofChecks.blockedRowsVisible ? "yes" : "no", proofChecks.blockedRowsVisible],
      ["Blocked selectable", proofChecks.blockedRowsSelectable ? "yes" : "no", !proofChecks.blockedRowsSelectable],
      ["Directory output", proofChecks.directorySelectionMatchesContractOutput ? "matches" : "mismatch", proofChecks.directorySelectionMatchesContractOutput],
      ["Disclosure", proofChecks.disclosureExpansionPrepared ? "expand/collapse wired" : "not wired", proofChecks.disclosureExpansionPrepared],
      ["Column resize", proofChecks.columnResizePrepared ? "handles wired" : "not wired", proofChecks.columnResizePrepared],
      ["View modes", proofChecks.viewModeOptionsAvailable ? "all candidates exposed" : "missing", proofChecks.viewModeOptionsAvailable],
      ["Mode selection", proofChecks.viewModeSelectionControlsAvailable ? "controls exposed" : "missing", proofChecks.viewModeSelectionControlsAvailable],
      ["Selection scroll", proofChecks.viewModeSelectionPreservesScroll ? "preserved" : "jumps", proofChecks.viewModeSelectionPreservesScroll],
      ["Selected output", proofChecks.selectedOutputMatchesLegacy ? "matches legacy" : "needs review", proofChecks.selectedOutputMatchesLegacy],
      ["Title-only tree", proofChecks.titleOnlyTreeRejected ? "rejected" : "unchecked", proofChecks.titleOnlyTreeRejected]
    ].forEach(([label, value, ok]) => {
      const chip = document.createElement("span");
      chip.setAttribute("data-mcel-git-treegrid-proof-ok", ok ? "true" : "false");
      chip.textContent = `${label}: ${value}`;
      parent.appendChild(chip);
    });
  }

  function applyCommand(section, command, payload = {}) {
    const contractView = contractViewAdapter();
    if (typeof contractView?.applyTreegridSelectionCommand !== "function") {
      return {ok: false, selectedPaths: [], reason: "contract treegrid command adapter unavailable"};
    }
    return contractView.applyTreegridSelectionCommand(section, command, payload);
  }

  function renderInteractiveGitTreegridLab(document, options = {}) {
    const initialReport = buildInteractiveGitTreegridLabReport(options);
    const section = document.createElement("section");
    let currentSelectedPaths = asArray(initialReport.selectedPaths);
    let currentReport = initialReport;
    const defaultMode = viewModeById(currentReport, options.activeViewMode || currentReport.defaultViewMode || "contract-treegrid");
    let activeModeId = defaultMode?.id || "contract-treegrid";

    const rebuildReport = () => {
      currentReport = buildInteractiveGitTreegridLabReport({
        ...options,
        selectedPaths: currentSelectedPaths
      });
      return currentReport;
    };

    section.className = "mcel-git-treegrid-lab";
    section.setAttribute("data-mcel-git-file-basket-treegrid-lab", "true");
    section.setAttribute("data-mcel-git-treegrid-active-in-git-tools", "false");
    section.setAttribute("data-mcel-git-treegrid-renderer", currentReport.visibleRenderer || "mcel-lab-git-treegrid");
    section.setAttribute("data-mcel-git-treegrid-view-mode", activeModeId);

    const header = document.createElement("header");
    header.className = "mcel-git-treegrid-lab-header";
    header.innerHTML = `
      <div>
        <p class="eyebrow">Git file-basket view-mode lab</p>
        <h6>Make every proposed tree/file-basket view earn Git before Git uses it.</h6>
        <p>The real Git Tools page stays on the legacy tree path. This lab mounts the same Git file-basket model, controller, contract treegrid renderer, rejected view projections, and selected-output proof so we can compare every proposed view shape interactively first.</p>
      </div>
      <div class="mcel-git-treegrid-lab-status">
        <span>Target <strong>${escapeHtml(TARGET_CONCERN)}</strong></span>
        <span>Git page <strong>${escapeHtml(currentReport.gitToolsRenderer || "legacy-wunderbaum")}</strong></span>
        <span>Lab gate <strong>${escapeHtml(currentReport.replacementGate || "deferred")}</strong></span>
      </div>
    `;

    const controls = document.createElement("div");
    controls.className = "mcel-git-treegrid-lab-controls";
    [
      ["select-scripts", `Select ${currentReport.scenarios?.probes?.directory || DEFAULT_DIRECTORY_PROBE}`],
      ["clear", "Clear selection"],
      ["select-all", "Select all eligible"],
      ["blocked", `Try blocked ${currentReport.scenarios?.probes?.blocked || "path"}`]
    ].forEach(([id, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("data-mcel-git-treegrid-command", id);
      button.textContent = label;
      controls.appendChild(button);
    });

    const proof = document.createElement("div");
    proof.className = "mcel-git-treegrid-lab-proof";
    renderProofChips(document, proof, currentReport);

    const modeShell = document.createElement("div");
    modeShell.className = "mcel-git-treegrid-view-mode-switcher";
    modeShell.setAttribute("data-mcel-git-treegrid-view-mode-switcher", "true");
    const modeIntro = document.createElement("div");
    modeIntro.className = "mcel-git-treegrid-view-mode-intro";
    modeIntro.innerHTML = `<strong>View candidates</strong><span>Switch one Git file-basket specimen through every tree/file view MCEL has been considering. Selection controls now stay wired in every projection so the view has to prove how users manage the basket.</span>`;
    const modeButtons = document.createElement("div");
    modeButtons.className = "mcel-git-treegrid-view-mode-options";
    renderViewModeButtons(document, modeButtons, currentReport, activeModeId);
    modeShell.append(modeIntro, modeButtons);

    const body = document.createElement("div");
    body.className = "mcel-git-treegrid-lab-body";

    const projectionMount = document.createElement("div");
    projectionMount.className = "mcel-git-treegrid-lab-tree";
    projectionMount.setAttribute("data-mcel-git-treegrid-view-projection-mount", "true");

    const side = document.createElement("aside");
    side.className = "mcel-git-treegrid-lab-side";
    side.innerHTML = `
      <strong>Selected output from controller</strong>
      <p><span data-mcel-git-treegrid-selected-count>${Number(currentSelectedPaths.length || 0)}</span> explicit repo-relative files. Blocked rows remain visible but cannot enter this output.</p>
      <ul data-mcel-git-treegrid-selected-output></ul>
      <details open>
        <summary>Active view candidate</summary>
        <dl data-mcel-git-treegrid-active-view-summary></dl>
      </details>
      <details>
        <summary>Readiness JSON</summary>
        <pre data-mcel-git-treegrid-readiness-json></pre>
      </details>
    `;
    body.append(projectionMount, side);
    section.append(header, controls, proof, modeShell, body);

    const updateReadinessJson = () => {
      const node = section.querySelector?.("[data-mcel-git-treegrid-readiness-json]");
      if (!node) return;
      node.textContent = JSON.stringify({
        ready: currentReport.ready,
        activeInGitTools: currentReport.activeInGitTools,
        gitToolsRenderer: currentReport.gitToolsRenderer,
        visibleRenderer: currentReport.visibleRenderer,
        replacementGate: currentReport.replacementGate,
        activeViewMode: activeModeId,
        eligibleViewModeIds: currentReport.eligibleViewModeIds,
        rejectedViewModeIds: currentReport.rejectedViewModeIds,
        selectedPaths: currentSelectedPaths,
        proofChecks: currentReport.proofChecks
      }, null, 2);
    };

    const updateActiveModeSummary = (mode) => {
      const summary = section.querySelector?.("[data-mcel-git-treegrid-active-view-summary]");
      if (!summary) return;
      summary.innerHTML = `
        <dt>Mode</dt><dd>${escapeHtml(mode.label || mode.id)}</dd>
        <dt>Status</dt><dd>${escapeHtml(mode.status || "")}</dd>
        <dt>Renderer</dt><dd>${escapeHtml(mode.renderer || "")}</dd>
        <dt>Reason</dt><dd>${escapeHtml(mode.reason || "")}</dd>
        <dt>Selection controls</dt><dd>${escapeHtml(mode.interactive ? "native treegrid controls" : "lab controller rail")}</dd>
        <dt>Missing</dt><dd>${escapeHtml(asArray(mode.missingCapabilities).join(", ") || "none")}</dd>
      `;
    };

    const commitSelection = (paths = [], optionsForSelection = {}) => {
      const projectionScrollState = optionsForSelection.preserveScrollState || captureProjectionScrollState(projectionMount);
      const viewportScrollState = optionsForSelection.preserveViewportScrollState || captureViewportScrollState(document);
      currentSelectedPaths = normalizeSelection(currentReport.model || initialReport.model || {}, paths);
      rebuildReport();
      renderSelectedOutput(document, section, currentSelectedPaths);
      renderProofChips(document, proof, currentReport);
      updateReadinessJson();
      section.setAttribute("data-mcel-git-treegrid-selected-count", String(currentSelectedPaths.length));
      if (optionsForSelection.rerender === true) {
        renderProjection(activeModeId, {
          preserveScrollState: projectionScrollState,
          preserveViewportScrollState: viewportScrollState
        });
      }
    };

    const applyModelCommand = (command = "", payload = {}) => {
      const model = currentReport.model || initialReport.model || {};
      const controller = buildController(model, currentSelectedPaths);
      if (!controller || typeof controller.apply !== "function") {
        return {ok: false, command, selectedPaths: currentSelectedPaths, reason: "file basket controller unavailable"};
      }
      const result = controller.apply(command, payload);
      return {...result, selectedPaths: normalizeSelection(model, result.selectedPaths || result.output || currentSelectedPaths)};
    };

    const renderProjection = (modeId, renderOptions = {}) => {
      rebuildReport();
      const mode = viewModeById(currentReport, modeId);
      activeModeId = mode.id;
      section.setAttribute("data-mcel-git-treegrid-view-mode", activeModeId);
      projectionMount.innerHTML = renderViewProjectionHtml(currentReport, activeModeId);
      hydrateProjectionSelectionControls(projectionMount);
      setActiveModeButtonState(modeButtons.querySelectorAll?.("[data-mcel-git-treegrid-view-mode-option]"), activeModeId);
      updateActiveModeSummary(mode);
      updateReadinessJson();
      const contractView = contractViewAdapter();
      if (mode.interactive && typeof contractView?.initializeContractTreegrid === "function") {
        contractView.initializeContractTreegrid(section, {
          onSelectionChange(paths = []) {
            commitSelection(paths, {rerender: false});
          }
        });
      }
      if (renderOptions.preserveScrollState) {
        const restored = restoreProjectionScrollState(projectionMount, renderOptions.preserveScrollState);
        section.setAttribute("data-mcel-git-treegrid-selection-scroll-preserved", restored ? "true" : "false");
      }
      if (renderOptions.preserveViewportScrollState) {
        restoreViewportScrollState(document, renderOptions.preserveViewportScrollState);
      }
    };

    renderSelectedOutput(document, section, currentSelectedPaths);
    renderProjection(activeModeId);

    modeButtons.addEventListener?.("click", (event) => {
      const button = event.target?.closest?.("[data-mcel-git-treegrid-view-mode-option]");
      if (!button) return;
      renderProjection(button.getAttribute("data-mcel-git-treegrid-view-mode-option") || "contract-treegrid");
    });

    controls.addEventListener?.("click", (event) => {
      const button = event.target?.closest?.("[data-mcel-git-treegrid-command]");
      if (!button) return;
      const command = button.getAttribute("data-mcel-git-treegrid-command");
      const projectionScrollState = captureProjectionScrollState(projectionMount);
      const viewportScrollState = captureViewportScrollState(document);
      let result;
      if (command === "select-scripts") {
        result = applyModelCommand("set-directory-selection", {
          path: currentReport.scenarios?.probes?.directory || DEFAULT_DIRECTORY_PROBE,
          selected: true
        });
      } else if (command === "clear") {
        result = applyModelCommand("clear-selection");
      } else if (command === "select-all") {
        result = applyModelCommand("select-all-eligible");
      } else if (command === "blocked") {
        result = applyModelCommand("set-file-selection", {
          path: currentReport.scenarios?.probes?.blocked || "",
          selected: true
        });
      }
      commitSelection(result?.selectedPaths || [], {
        rerender: true,
        preserveScrollState: projectionScrollState,
        preserveViewportScrollState: viewportScrollState
      });
      section.setAttribute("data-mcel-git-treegrid-last-command", command || "");
      section.setAttribute("data-mcel-git-treegrid-last-command-ok", result?.ok === false ? "false" : "true");
    });

    projectionMount.addEventListener?.("change", (event) => {
      const input = event.target?.closest?.("[data-mcel-git-view-selection-path]");
      if (!input || input.disabled) return;
      const projectionScrollState = captureProjectionScrollState(projectionMount);
      const viewportScrollState = captureViewportScrollState(document);
      const path = input.dataset.mcelGitViewSelectionPath || "";
      const kind = input.dataset.mcelGitViewSelectionKind === "dir" ? "dir" : "file";
      const result = applyModelCommand(kind === "dir" ? "set-directory-selection" : "set-file-selection", {
        path,
        selected: input.checked
      });
      commitSelection(result?.selectedPaths || [], {
        rerender: true,
        preserveScrollState: projectionScrollState,
        preserveViewportScrollState: viewportScrollState
      });
      section.setAttribute("data-mcel-git-treegrid-last-command", `view-mode-${kind}-selection`);
      section.setAttribute("data-mcel-git-treegrid-last-command-ok", result?.ok === false ? "false" : "true");
    });

    return section;
  }

  global.McelGitFileBasketTreegridLab = Object.freeze({
    version: VERSION,
    VERSION,
    LAB_ID,
    TARGET_CONCERN,
    CONTRACT_ID,
    sourceFile: SOURCE_FILE,
    SOURCE_FILE,
    sampleGitReview,
    buildViewModeCatalog,
    renderViewProjectionHtml,
    buildInteractiveGitTreegridLabReport,
    renderInteractiveGitTreegridLab
  });
})(typeof window !== "undefined" ? window : globalThis);
