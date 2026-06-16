(function (global) {
  "use strict";

  const VERSION = "0.1.0";
  const SURFACE_ID = "git-tools.file-basket";
  const LEGACY_SURFACE_IDS = ["task-manager.file-basket"];
  const SOURCE_FILE = "main_computer/web/applications/scripts/git-tools-file-basket.js";

  function escapeHtml(value = "") {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function groupConfig() {
    return [
      {
        key: "selected_by_default",
        title: "Selected by default",
        subtitle: "Clean source/config/test files selected by the planner",
        selectable: true,
        expanded: true,
        reason: "selected by default",
        tone: "clean",
      },
      {
        key: "review_before_selecting",
        title: "Review before selecting",
        subtitle: "Candidate files that need human approval before staging",
        selectable: true,
        expanded: true,
        reason: "needs review",
        tone: "review",
      },
      {
        key: "blocked_possible_secrets",
        title: "Blocked",
        subtitle: "Files blocked by upstream gates or secret-looking labels",
        selectable: false,
        expanded: true,
        reason: "blocked by Secrets / Filter",
        tone: "blocked",
      },
      {
        key: "excluded_generated_runtime",
        title: "Excluded generated/runtime",
        subtitle: "Generated, cache, runtime, or build-output paths kept out of staging",
        selectable: false,
        expanded: false,
        reason: "excluded generated/runtime",
        tone: "excluded",
      },
    ];
  }

  function groups(review = {}) {
    const source = review.candidate_groups || {};
    return {
      selected_by_default: Array.isArray(source.selected_by_default) ? source.selected_by_default : [],
      review_before_selecting: Array.isArray(source.review_before_selecting) ? source.review_before_selecting : [],
      blocked_possible_secrets: Array.isArray(source.blocked_possible_secrets) ? source.blocked_possible_secrets : [],
      excluded_generated_runtime: Array.isArray(source.excluded_generated_runtime) ? source.excluded_generated_runtime : [],
    };
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
    if (normalized === "untracked") {
      return {symbol: "+", label: "untracked", tone: "untracked"};
    }
    if (normalized === "tracked_deleted") {
      return {symbol: "−", label: "tracked deleted", tone: "deleted"};
    }
    if (normalized === "tracked_renamed") {
      return {symbol: "↦", label: "tracked renamed", tone: "renamed"};
    }
    if (normalized === "tracked_changed") {
      return {symbol: "✓", label: "tracked changed", tone: "tracked"};
    }
    if (normalized === "conflicted") {
      return {symbol: "!", label: "conflicted", tone: "blocked"};
    }
    return {symbol: "·", label: normalized, tone: "unknown"};
  }

  function treeStats(nodes = []) {
    const stats = {total: 0, untracked: 0, changed: 0, blocked: 0};
    const visit = (node = {}) => {
      const data = node.data || {};
      if (data.kind === "file") {
        stats.total += 1;
        const status = normalizeStatus(data);
        if (status === "untracked") stats.untracked += 1;
        if (status.startsWith("tracked_")) stats.changed += 1;
        if (data.blocked || data.selectable === false || data.group === "blocked_possible_secrets" || String(data.risk || "").toLowerCase().includes("block") || status === "conflicted") {
          stats.blocked += 1;
        }
      }
      (Array.isArray(node.children) ? node.children : []).forEach(visit);
    };
    nodes.forEach(visit);
    return stats;
  }

  function fileMeta(item = {}, group = {}) {
    const labels = Array.isArray(item.classifications) ? item.classifications.filter(Boolean) : [];
    const status = normalizeStatus(item);
    const display = statusDisplay(status);
    const risk = item.risk || item.privacy_risk || group.tone || "review";
    const findings = Number(item.blocking_security_findings_count || item.privacy_findings_count || 0);
    const detailParts = [
      display.label,
      labels.join(" · "),
      risk,
      findings ? `${findings} finding${findings === 1 ? "" : "s"}` : "",
      item.modified ? `edited ${item.modified}` : "",
    ].filter(Boolean);
    return {
      labels,
      status,
      statusDisplay: display,
      risk,
      findings,
      reason: item.reason || group.reason || "",
      modified: item.modified || "",
      meta: detailParts.join(" · "),
    };
  }

  function createTreeNode(title, key, options = {}) {
    const children = Array.isArray(options.children) ? options.children : [];
    const isContainer = Boolean(options.folder || options.type === "dir");
    const node = {
      title,
      key,
      type: options.type || (isContainer ? "dir" : "file"),
      selected: Boolean(options.selected),
      unselectable: Boolean(options.unselectable),
      checkbox: options.checkbox !== false,
      classes: options.classes || options.extraClasses || "",
      data: options.data || {},
    };
    if (options.expanded === true) {
      node.expanded = true;
    }
    if (isContainer || children.length) {
      node.children = children;
    }
    return node;
  }

  function candidateItems(review = {}) {
    const currentGroups = groups(review);
    const configs = groupConfig();
    const precedence = {
      selected_by_default: 10,
      review_before_selecting: 20,
      excluded_generated_runtime: 30,
      blocked_possible_secrets: 40,
    };
    const byPath = new Map();
    configs.forEach((group) => {
      (currentGroups[group.key] || []).forEach((item = {}) => {
        const path = String(item.path || "").replace(/\\/g, "/").replace(/^\/+/, "");
        if (!path) return;
        const previous = byPath.get(path);
        const rank = precedence[group.key] || 0;
        if (!previous || rank >= previous.rank) {
          byPath.set(path, {item: {...item, path}, group, rank});
        }
      });
    });
    return Array.from(byPath.values()).map(({item, group}) => ({item, group}));
  }

  function adapter() {
    return global.McelFileBasketModel || null;
  }

  function contractView() {
    return global.GitToolsFileBasketContractView || null;
  }

  function model(review = {}) {
    const modelAdapter = adapter();
    if (!modelAdapter?.buildFileBasketModel) return null;
    try {
      return modelAdapter.buildFileBasketModel(review, {
        surfaceId: SURFACE_ID,
        canonicalSurfaceId: SURFACE_ID,
        legacySurfaceIds: LEGACY_SURFACE_IDS,
        ownerApp: "git-tools",
        sourceConcern: "concern.file-basket",
        sourceFile: SOURCE_FILE,
        ownershipStatus: "extracted-git-tools-boundary",
        ownershipNote: "Git Tools file-basket model/view glue is extracted into git-tools-file-basket.js; task-manager.js keeps only compatibility wrappers while callers are strangled."
      });
    } catch (error) {
      console.warn("Could not build MCEL file basket model.", error);
      return null;
    }
  }

  function modelJson(fileBasketModel = null) {
    if (!fileBasketModel) return "";
    try {
      return JSON.stringify(fileBasketModel);
    } catch (error) {
      console.warn("Could not serialize MCEL file basket model.", error);
      return "";
    }
  }

  function treeFileTitleFromModel(row = {}) {
    const titleParts = [
      `${row.statusSymbol || "·"} ${row.name || row.path || "file"}`,
      row.statusLabel || row.status || "",
      row.bucketLabel || row.bucket || "",
      String(row.meta || "").replace(String(row.statusLabel || ""), "").replace(/^\s*·\s*/, ""),
      row.reason && row.reason !== row.blockedReason ? row.reason : "",
    ].filter(Boolean);
    return titleParts.join(" · ");
  }

  function treeNodeFromModelNode(modelNode = {}) {
    const kind = modelNode.kind || "file";
    if (kind === "file") {
      const selectable = modelNode.selectable !== false;
      const statusTone = modelNode.statusTone || "unknown";
      const bucketTone = modelNode.bucketTone || "review";
      return createTreeNode(treeFileTitleFromModel(modelNode), `file:${modelNode.path}`, {
        type: "file",
        selected: Boolean(modelNode.selectedByDefault && selectable),
        unselectable: !selectable,
        checkbox: selectable,
        classes: [
          "git-project-commit-tree-file",
          `git-project-commit-tree-file-${statusTone}`,
          `git-project-commit-node-${bucketTone}`,
        ].join(" "),
        data: {
          kind: "file",
          path: modelNode.path || "",
          name: modelNode.name || "",
          group: modelNode.bucket || "",
          groupTitle: modelNode.bucketLabel || modelNode.bucket || "",
          bucket: modelNode.bucket || "",
          bucketLabel: modelNode.bucketLabel || modelNode.bucket || "",
          selectable,
          selectedByDefault: Boolean(modelNode.selectedByDefault && selectable),
          blocked: Boolean(modelNode.blocked || !selectable),
          blockedReason: modelNode.blockedReason || "",
          status: modelNode.status || "unknown",
          statusLabel: modelNode.statusLabel || modelNode.status || "unknown",
          statusSymbol: modelNode.statusSymbol || "·",
          statusTone,
          risk: modelNode.risk || "",
          classifications: Array.isArray(modelNode.classifications) ? modelNode.classifications.slice() : [],
          reason: modelNode.reason || "",
          modified: modelNode.modified || "",
          meta: modelNode.meta || "",
          findings: Number(modelNode.findings || 0),
          modelRowId: modelNode.id || "",
        },
      });
    }

    const children = (Array.isArray(modelNode.children) ? modelNode.children : [])
      .map(treeNodeFromModelNode)
      .filter(Boolean);
    const selectable = modelNode.selectable !== false && Number(modelNode.selectableFileCount || 0) > 0;
    return createTreeNode(modelNode.name || modelNode.path || "Candidate files", `dir:${modelNode.path || ""}/`, {
      type: "dir",
      expanded: false,
      selected: modelNode.selectionState === "all",
      checkbox: selectable,
      unselectable: !selectable,
      classes: "git-project-commit-tree-dir git-project-commit-node-dir",
      children,
      data: {
        kind: "dir",
        name: modelNode.name || "",
        path: modelNode.path || "",
        selectable,
        selectionState: modelNode.selectionState || "none",
        totalFiles: Number(modelNode.fileCount || 0),
        blockedFiles: Number(modelNode.blockedFileCount || 0),
        selectableFiles: Number(modelNode.selectableFileCount || 0),
        modelRowId: modelNode.id || "",
      },
    });
  }

  function treeSourceFromModel(fileBasketModel = null) {
    const hierarchy = Array.isArray(fileBasketModel?.hierarchy) ? fileBasketModel.hierarchy : [];
    if (!hierarchy.length) return null;
    const root = createTreeNode("Candidate files", "candidate-files", {
      type: "dir",
      folder: true,
      expanded: true,
      checkbox: true,
      data: {kind: "dir", path: "", selectable: true},
      children: hierarchy.map(treeNodeFromModelNode).filter(Boolean),
    });
    annotateDirectoryStats(root);
    finalizeDirectorySelection(root);
    return root.children.length ? root.children : null;
  }

  function sortTreeNodes(nodes = []) {
    nodes.sort((a, b) => {
      const aDir = a.data?.kind === "dir";
      const bDir = b.data?.kind === "dir";
      if (aDir !== bDir) return aDir ? -1 : 1;
      return String(a.title || "").localeCompare(String(b.title || ""), undefined, {sensitivity: "base"});
    });
    nodes.forEach((node) => {
      if (Array.isArray(node.children)) sortTreeNodes(node.children);
    });
    return nodes;
  }

  function annotateDirectoryStats(node) {
    const children = Array.isArray(node.children) ? node.children : [];
    let total = 0;
    let untracked = 0;
    let changed = 0;
    let blocked = 0;
    children.forEach((child) => {
      const data = child.data || {};
      if (data.kind === "file") {
        total += 1;
        const status = normalizeStatus(data);
        if (status === "untracked") untracked += 1;
        if (status.startsWith("tracked_")) changed += 1;
        if (data.blocked || data.selectable === false || data.group === "blocked_possible_secrets" || String(data.risk || "").toLowerCase().includes("block") || status === "conflicted") {
          blocked += 1;
        }
      } else if (data.kind === "dir") {
        const childStats = annotateDirectoryStats(child);
        total += childStats.total;
        untracked += childStats.untracked;
        changed += childStats.changed;
        blocked += childStats.blocked;
      }
    });
    node.data = {...(node.data || {}), totalFiles: total, untrackedFiles: untracked, changedFiles: changed, blockedFiles: blocked};
    if (node.data.kind === "dir" && node.data.path) {
      const name = String(node.data.name || node.title || "");
      const countLabel = `${total} file${total === 1 ? "" : "s"}`;
      const statusParts = [
        untracked ? `+ ${untracked}` : "",
        changed ? `✓ ${changed}` : "",
        blocked ? `! ${blocked}` : "",
      ].filter(Boolean);
      node.title = [name, "dir", countLabel, ...statusParts].filter(Boolean).join(" · ");
    }
    return {total, untracked, changed, blocked};
  }

  function finalizeDirectorySelection(node) {
    const children = Array.isArray(node.children) ? node.children : [];
    if (!children.length) return Boolean(node.data?.selectable);
    const selectableChildren = children
      .map(finalizeDirectorySelection)
      .filter(Boolean);
    const selectable = selectableChildren.length > 0;
    node.data = {...(node.data || {}), selectable};
    node.checkbox = selectable;
    node.unselectable = !selectable;
    return selectable;
  }

  function insertTreePath(root, item = {}, group = {}) {
    const path = String(item.path || "").replace(/\\/g, "/").replace(/^\/+/, "");
    if (!path) return;
    const parts = path.split("/").filter(Boolean);
    let cursor = root;
    let cursorPath = "";
    parts.forEach((part, index) => {
      cursorPath = cursorPath ? `${cursorPath}/${part}` : part;
      const isFile = index === parts.length - 1;
      let child = cursor.children.find((node) => node.data?.path === cursorPath && node.data?.kind === (isFile ? "file" : "dir"));
      if (!child) {
        if (isFile) {
          const meta = fileMeta(item, group);
          const selectable = group.selectable !== false;
          const selected = Boolean((group.key === "selected_by_default" || item.selected_by_default) && selectable);
          const groupLabel = group.title || group.key || "Candidate";
          const display = meta.statusDisplay || statusDisplay(meta.status);
          const titleParts = [
            `${display.symbol} ${part}`,
            display.label,
            groupLabel,
            meta.meta.replace(display.label, "").replace(/^\s*·\s*/, ""),
            meta.reason && meta.reason !== group.reason ? meta.reason : "",
          ].filter(Boolean);
          child = createTreeNode(titleParts.join(" · "), `file:${cursorPath}`, {
            type: "file",
            selected,
            unselectable: !selectable,
            checkbox: selectable,
            classes: [
              "git-project-commit-tree-file",
              `git-project-commit-tree-file-${display.tone}`,
              `git-project-commit-node-${group.tone || "review"}`,
            ].join(" "),
            data: {
              kind: "file",
              path,
              name: part,
              group: group.key,
              groupTitle: group.title,
              selectable,
              status: meta.status,
              statusLabel: display.label,
              statusSymbol: display.symbol,
              statusTone: display.tone,
              risk: meta.risk,
              classifications: meta.labels,
              reason: meta.reason,
              modified: meta.modified,
              meta: meta.meta,
            },
          });
        } else {
          child = createTreeNode(part, `dir:${cursorPath}/`, {
            type: "dir",
            expanded: false,
            selected: false,
            checkbox: true,
            unselectable: false,
            classes: "git-project-commit-tree-dir git-project-commit-node-dir",
            data: {
              kind: "dir",
              name: part,
              path: cursorPath,
              selectable: true,
            },
          });
        }
        cursor.children.push(child);
      }
      cursor = child;
    });
  }

  function emptyTreeSource() {
    return [
      createTreeNode("No candidate files returned by the planner", "empty:candidate-files", {
        type: "empty",
        checkbox: false,
        unselectable: true,
        data: {kind: "empty", selectable: false},
      }),
    ];
  }

  function treeSource(review = {}, fileBasketModel = model(review)) {
    const modelTree = treeSourceFromModel(fileBasketModel);
    if (Array.isArray(modelTree) && modelTree.length) return modelTree;

    const root = createTreeNode("Candidate files", "candidate-files", {
      type: "dir",
      folder: true,
      expanded: true,
      checkbox: true,
      data: {kind: "dir", path: "", selectable: true},
    });
    candidateItems(review).forEach(({item, group}) => insertTreePath(root, item, group));
    sortTreeNodes(root.children);
    annotateDirectoryStats(root);
    finalizeDirectorySelection(root);
    if (!root.children.length) {
      return emptyTreeSource();
    }
    return root.children;
  }

  function reviewCandidatePaths(review = {}) {
    const fileBasketModel = model(review);
    if (Array.isArray(fileBasketModel?.rows)) {
      return fileBasketModel.rows
        .map((row = {}) => row.path || "")
        .filter(Boolean)
        .sort((a, b) => a.localeCompare(b));
    }
    const paths = new Set();
    candidateItems(review).forEach(({item = {}} = {}) => {
      const path = String(item.path || "").replace(/\\/g, "/").replace(/^\/+/, "");
      if (path) paths.add(path);
    });
    return Array.from(paths).sort((a, b) => a.localeCompare(b));
  }

  function defaultSelectedPathsFromTreeSource(nodes = []) {
    const paths = [];
    const visit = (node = {}) => {
      const data = node.data || {};
      if (data.kind === "file" && node.selected && data.selectable !== false && data.path) {
        paths.push(data.path);
      }
      (Array.isArray(node.children) ? node.children : []).forEach(visit);
    };
    nodes.forEach(visit);
    return sortSelectedPaths(paths);
  }

  function fallbackTreeHtml(nodes = [], options = {}) {
    const htmlEscape = typeof options.escapeHtml === "function" ? options.escapeHtml : escapeHtml;
    const renderNode = (node = {}) => {
      const data = node.data || {};
      const children = Array.isArray(node.children) ? node.children : [];
      const isFile = data.kind === "file";
      const isDir = data.kind === "dir" || data.kind === "group";
      const checkbox = node.checkbox !== false && data.selectable !== false;
      const checked = node.selected && checkbox ? "checked" : "";
      const disabled = checkbox ? "" : "disabled";
      const path = data.path || "";
      const meta = data.meta || data.subtitle || data.reason || "";
      return `<li class="git-project-commit-fallback-node ${isDir ? "is-dir" : ""} ${isFile ? "is-file" : ""}" data-git-commit-tree-node="${htmlEscape(data.kind || "node")}" data-git-commit-status="${htmlEscape(data.status || "")}">
        <label>
          <input type="checkbox"
            ${checked}
            ${disabled}
            data-git-commit-tree-checkbox="${isDir ? "dir" : isFile ? "file" : "none"}"
            ${isFile ? `data-git-commit-file="${htmlEscape(path)}"` : ""}>
          <span>
            <strong>${htmlEscape(isFile && path ? path : node.title || "")}</strong>
            ${meta ? `<small>${htmlEscape(meta)}</small>` : ""}
          </span>
        </label>
        ${children.length ? `<ul>${children.map(renderNode).join("")}</ul>` : ""}
      </li>`;
    };
    return `<ul class="git-project-commit-fallback-tree">${nodes.map(renderNode).join("")}</ul>`;
  }

  function basketHtml(review = {}, options = {}) {
    const fileBasketModel = model(review);
    const source = treeSource(review, fileBasketModel);
    const currentGroups = groups(review);
    const totals = treeStats(source);
    const selectedTotal = Array.isArray(fileBasketModel?.defaultSelectedPaths)
      ? fileBasketModel.defaultSelectedPaths.length
      : currentGroups.selected_by_default.filter((item = {}) => item.path).length;
    const serializedModel = modelJson(fileBasketModel);
    const htmlEscape = typeof options.escapeHtml === "function" ? options.escapeHtml : escapeHtml;
    const repoIdentityHtml = typeof options.repoIdentityHtml === "function" ? options.repoIdentityHtml(review) : "";
    const renderer = "legacy-wunderbaum";
    const rendererLabel = "directories first · files under paths";
    return `<section class="git-project-commit-right" data-git-commit-basket data-git-commit-file-basket-model-ready="${fileBasketModel ? "true" : "false"}" data-git-commit-file-basket-renderer="${renderer}">
      ${repoIdentityHtml}
      <div class="git-project-subscreen-panel-head">
        <strong>File basket</strong>
        <span>${rendererLabel}</span>
      </div>
      <div class="git-project-commit-basket-summary">
        <span>Total candidates <strong>${Number(totals.total)}</strong></span>
        <span class="is-untracked">+ Untracked <strong>${Number(totals.untracked)}</strong></span>
        <span class="is-tracked">✓ Changed <strong>${Number(totals.changed)}</strong></span>
        <span class="is-blocked">Blocked <strong>${Number(totals.blocked)}</strong></span>
      </div>
      <p class="git-project-muted">Repo file tree: select files directly or select folders as a shortcut. Checked folders mean all selectable child files are selected; mixed folders mean only some child files are selected. ${selectedTotal ? `${selectedTotal} file${selectedTotal === 1 ? "" : "s"} selected by default.` : "Review candidates are not selected until you choose them."}</p>
      ${serializedModel ? `<textarea hidden data-git-commit-file-basket-model>${htmlEscape(serializedModel)}</textarea>` : ""}
      <textarea hidden data-git-commit-tree-source data-git-commit-legacy-tree-source>${htmlEscape(JSON.stringify(source))}</textarea>
      <div class="git-project-commit-wunderbaum-shell" data-git-commit-legacy-tree-active="true">
        <div class="git-project-commit-wunderbaum wb-skeleton wb-initializing" data-git-commit-tree></div>
        <div class="git-project-commit-tree-fallback" data-git-commit-tree-fallback>
          ${fallbackTreeHtml(source, {escapeHtml: htmlEscape})}
        </div>
      </div>
    </section>`;
  }

  function readTreeSource(workbench) {
    const sourceNode = workbench?.querySelector?.("[data-git-commit-tree-source]");
    if (!sourceNode) return [];
    try {
      const parsed = JSON.parse(sourceNode.value || "[]");
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      return [];
    }
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

  function sortSelectedPaths(paths = []) {
    return Array.from(new Set((Array.isArray(paths) ? paths : []).filter(Boolean)))
      .sort((a, b) => String(a).localeCompare(String(b)));
  }

  function adapterSelectedOutput(workbench, paths = []) {
    const fallbackPaths = sortSelectedPaths(paths);
    const modelAdapter = adapter();
    const fileBasketModel = readFileBasketModel(workbench);
    if (!modelAdapter?.selectedOutput || !fileBasketModel) return fallbackPaths;
    return modelAdapter.selectedOutput(fileBasketModel, fallbackPaths);
  }

  function selectionAdapterReport(workbench, rawPaths = []) {
    const fallbackPaths = sortSelectedPaths(rawPaths);
    const modelAdapter = adapter();
    const fileBasketModel = readFileBasketModel(workbench);
    if (!modelAdapter?.selectedOutput || !fileBasketModel) {
      return {
        enabled: false,
        rawPaths: fallbackPaths,
        selectedPaths: fallbackPaths,
        matches: true,
        summary: null,
      };
    }
    const selectedPaths = modelAdapter.selectedOutput(fileBasketModel, fallbackPaths);
    return {
      enabled: true,
      rawPaths: fallbackPaths,
      selectedPaths,
      matches: JSON.stringify(fallbackPaths) === JSON.stringify(selectedPaths),
      summary: typeof modelAdapter.selectionSummary === "function" ? modelAdapter.selectionSummary(fileBasketModel, fallbackPaths) : null,
    };
  }

  function flattenTreeFiles(nodes = [], out = []) {
    nodes.forEach((node = {}) => {
      const data = node.data || {};
      if (data.kind === "file" && data.path) out.push(data);
      if (Array.isArray(node.children)) flattenTreeFiles(node.children, out);
    });
    return out;
  }

  function buildFileIndex(files = []) {
    const exact = new Set();
    const baseCounts = new Map();
    const baseToPath = new Map();
    files.forEach((file = {}) => {
      const path = String(file.path || "").replace(/\\/g, "/");
      if (!path) return;
      exact.add(path);
      const base = path.split("/").pop();
      baseCounts.set(base, (baseCounts.get(base) || 0) + 1);
      baseToPath.set(base, path);
    });
    const uniqueBaseToPath = new Map();
    baseToPath.forEach((path, base) => {
      if (baseCounts.get(base) === 1) uniqueBaseToPath.set(base, path);
    });
    return {files, exact, uniqueBaseToPath};
  }

  function cleanPathCandidate(value = "") {
    let text = String(value || "").trim();
    if (!text) return "";
    text = text
      .replace(/\\/g, "/")
      .replace(/^[\s✓☑☐+>›▸▾-]+/g, "")
      .replace(/^file:/i, "")
      .replace(/^folder:/i, "")
      .replace(/^dir:/i, "")
      .trim();
    text = text
      .replace(/\s+·\s+.*$/g, "")
      .replace(/\s+-\s+untracked\s+.*$/i, "")
      .replace(/\s+-\s+modified\s+.*$/i, "")
      .replace(/\s+-\s+deleted\s+.*$/i, "")
      .replace(/\s+-\s+renamed\s+.*$/i, "")
      .replace(/\s+-\s+review before selecting\s+.*$/i, "")
      .replace(/\s+/g, " ")
      .trim();
    return text;
  }

  function canonicalFilePath(value = "", index = buildFileIndex()) {
    const raw = cleanPathCandidate(value);
    if (!raw) return "";
    if (/\bdir\b.*\bfiles?\b/i.test(raw) || /^\d+\s+files?\b/i.test(raw)) return "";
    if (index.exact.has(raw)) return raw;
    const withoutRoot = raw.replace(/^main_computer_test\//, "");
    if (index.exact.has(withoutRoot)) return withoutRoot;
    const suffixMatches = index.files
      .map((file = {}) => String(file.path || "").replace(/\\/g, "/"))
      .filter((path) => path === raw || path.endsWith(`/${raw}`));
    if (suffixMatches.length === 1) return suffixMatches[0];
    const base = raw.split("/").pop();
    if (index.uniqueBaseToPath.has(base)) return index.uniqueBaseToPath.get(base);
    return "";
  }

  function treeNodePath(node, index) {
    const candidates = [
      node?.data?.path,
      node?.data?.file,
      node?.data?.repoPath,
      node?.data?.gitCommitFile,
      node?.key,
      node?.title,
    ];
    for (const candidate of candidates) {
      const path = canonicalFilePath(candidate, index);
      if (path) return path;
    }
    return "";
  }

  function treeNodeSelected(node) {
    try {
      if (typeof node?.isSelected === "function" && node.isSelected()) return true;
    } catch (error) {
      return false;
    }
    return Boolean(node?.selected || node?._selected || node?.data?.selected);
  }

  function visitTreeNodes(tree, visitor) {
    if (!tree || typeof visitor !== "function") return;
    if (typeof tree.visit === "function") {
      tree.visit(visitor);
      return;
    }
    if (tree.rootNode && typeof tree.rootNode.visit === "function") {
      tree.rootNode.visit(visitor);
    }
  }

  function selectedFilesFromFallback(workbench) {
    const files = flattenTreeFiles(readTreeSource(workbench));
    const index = buildFileIndex(files);
    const paths = Array.from(workbench?.querySelectorAll?.("[data-git-commit-tree-checkbox='file']:checked") || [])
      .map((input) => canonicalFilePath(input.dataset.gitCommitFile || input.value || "", index))
      .filter(Boolean);
    return adapterSelectedOutput(workbench, paths);
  }

  function selectedFilesFromWunderbaum(tree) {
    const workbench = tree?.gitCommitWorkbench || tree?.element?.closest?.("[data-git-commit-workbench]") || tree?.options?.element?.closest?.("[data-git-commit-workbench]");
    const files = flattenTreeFiles(readTreeSource(workbench));
    const index = buildFileIndex(files);
    const paths = new Set();

    try {
      if (typeof tree?.getSelectedNodes === "function") {
        (tree.getSelectedNodes() || []).forEach((node) => {
          if (node?.data?.selectable === false) return;
          const path = treeNodePath(node, index);
          if (path) paths.add(path);
        });
      }
    } catch (error) {
      console.warn("Could not read selected Wunderbaum nodes.", error);
    }

    try {
      visitTreeNodes(tree, (node) => {
        if (!treeNodeSelected(node) || node?.data?.selectable === false) return;
        const path = treeNodePath(node, index);
        if (path) paths.add(path);
      });
    } catch (error) {
      console.warn("Could not visit selected Wunderbaum nodes.", error);
    }

    return adapterSelectedOutput(workbench, Array.from(paths));
  }

  function selectedFilesFromDom(workbench) {
    const files = flattenTreeFiles(readTreeSource(workbench));
    const index = buildFileIndex(files);
    const treeElement = workbench?.querySelector?.("[data-git-commit-tree]");
    const paths = new Set();
    if (!treeElement) return [];
    const selectedElements = treeElement.querySelectorAll(`
      input[type="checkbox"]:checked,
      [role="checkbox"][aria-checked="true"],
      .wb-checkbox[aria-checked="true"],
      .wb-checkbox.wb-selected,
      .wb-checkbox.wb-checked,
      .wb-row.wb-selected,
      .wb-row[aria-selected="true"],
      .wb-node.wb-selected,
      [role="treeitem"][aria-selected="true"]
    `);
    selectedElements.forEach((element) => {
      const row = element.closest(".wb-row, .wb-node, [role='treeitem'], li, tr") || element.parentElement || element;
      const candidates = [];
      [element, row].forEach((candidateElement) => {
        if (!candidateElement) return;
        ["data-git-commit-file", "data-path", "data-key", "data-ref-key", "data-node-key", "title", "aria-label", "value"].forEach((attr) => {
          const value = candidateElement.getAttribute?.(attr);
          if (value && value !== "on") candidates.push(value);
        });
        Object.values(candidateElement.dataset || {}).forEach((value) => {
          if (value && value !== "on") candidates.push(value);
        });
      });
      const titleElement = row.querySelector?.(".wb-title, [class*='title'], [data-title]") || row;
      if (titleElement?.textContent) candidates.push(titleElement.textContent);
      for (const candidate of candidates) {
        const path = canonicalFilePath(candidate, index);
        if (path) {
          paths.add(path);
          return;
        }
      }
    });
    return adapterSelectedOutput(workbench, Array.from(paths));
  }

  function selectedFilesFromContractTreegrid(workbench) {
    const view = contractView();
    if (!workbench?.querySelector?.("[data-git-commit-contract-treegrid]") || typeof view?.selectedFilesFromContractTreegrid !== "function") return [];
    return adapterSelectedOutput(workbench, view.selectedFilesFromContractTreegrid(workbench));
  }

  function selectedFilesFromWorkbench(workbench) {
    if (workbench?.querySelector?.("[data-git-commit-contract-treegrid]")) {
      return selectedFilesFromContractTreegrid(workbench);
    }
    const tree = workbench?.gitCommitWunderbaum || workbench?.querySelector?.("[data-git-commit-tree]")?._wb_tree;
    const paths = new Set();
    selectedFilesFromWunderbaum(tree).forEach((path) => paths.add(path));
    selectedFilesFromDom(workbench).forEach((path) => paths.add(path));
    if (!tree || workbench?.dataset?.gitCommitWunderbaumFallback === "true") {
      selectedFilesFromFallback(workbench).forEach((path) => paths.add(path));
    }
    return adapterSelectedOutput(workbench, Array.from(paths));
  }

  function reviewStats(workbench, selectedPaths = []) {
    const files = flattenTreeFiles(readTreeSource(workbench));
    const selected = new Set(selectedPaths);
    const isReviewFile = (file = {}) => (
      file.group === "review_before_selecting" ||
      String(file.groupTitle || "").toLowerCase().includes("review") ||
      String(file.risk || file.privacy_risk || "").toLowerCase().includes("review")
    );
    const isBlockedFile = (file = {}) => (
      file.group === "blocked_possible_secrets" ||
      String(file.risk || file.privacy_risk || "").toLowerCase().includes("block") ||
      String(file.status || "").toLowerCase().includes("conflict") ||
      file.selectable === false
    );
    const reviewFiles = files.filter(isReviewFile);
    const blockedFiles = files.filter(isBlockedFile);
    const selectedFiles = files.filter((file = {}) => selected.has(file.path));
    const selectedReview = selectedFiles.filter(isReviewFile);
    const selectedBlocked = selectedFiles.filter(isBlockedFile);
    return {
      total: files.length,
      selected: selectedPaths.length,
      review: reviewFiles.length,
      blocked: blockedFiles.length,
      selectedReview: selectedReview.length,
      selectedBlocked: selectedBlocked.length,
      selectedBlockedPaths: selectedBlocked.map((file = {}) => file.path).filter(Boolean),
    };
  }

  global.GitToolsFileBasket = {
    VERSION,
    SURFACE_ID,
    LEGACY_SURFACE_IDS: LEGACY_SURFACE_IDS.slice(),
    SOURCE_FILE,
    groupConfig,
    groups,
    normalizeStatus,
    statusDisplay,
    treeStats,
    fileMeta,
    createTreeNode,
    candidateItems,
    adapter,
    contractView,
    model,
    modelJson,
    treeFileTitleFromModel,
    treeNodeFromModelNode,
    treeSourceFromModel,
    sortTreeNodes,
    annotateDirectoryStats,
    finalizeDirectorySelection,
    insertTreePath,
    emptyTreeSource,
    treeSource,
    reviewCandidatePaths,
    defaultSelectedPathsFromTreeSource,
    fallbackTreeHtml,
    basketHtml,
    readTreeSource,
    readFileBasketModel,
    sortSelectedPaths,
    adapterSelectedOutput,
    selectionAdapterReport,
    flattenTreeFiles,
    buildFileIndex,
    cleanPathCandidate,
    canonicalFilePath,
    treeNodePath,
    treeNodeSelected,
    visitTreeNodes,
    selectedFilesFromFallback,
    selectedFilesFromWunderbaum,
    selectedFilesFromDom,
    selectedFilesFromContractTreegrid,
    selectedFilesFromWorkbench,
    reviewStats,
  };
})(typeof window !== "undefined" ? window : globalThis);
