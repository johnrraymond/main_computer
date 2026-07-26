var McelFileExplorerSurface = (() => {
  "use strict";

  const contractVersion = "mcel.file-explorer-surface.v1";
  const surfaceId = "file-explorer.surface.primary";
  const surfaceContract = "file-explorer.contract.semantic-surface-pilot";
  const channel = "FILE_EXPLORER";
  const maxLayoutEntryNodes = 16;
  const firstEntryAnchorY = 222;
  const entryAnchorStepY = 28;

  const extractorsApi = (() => {
    if (typeof McelSurfaceExtractors !== "undefined") return McelSurfaceExtractors;
    if (typeof window !== "undefined" && window.McelSurfaceExtractors) return window.McelSurfaceExtractors;
    return null;
  })();

  const fitContractApi = (() => {
    if (typeof McelSurfaceFitContract !== "undefined") return McelSurfaceFitContract;
    if (typeof window !== "undefined" && window.McelSurfaceFitContract) return window.McelSurfaceFitContract;
    if (typeof window !== "undefined" && window.MCEL?.surfaceFitContract) return window.MCEL.surfaceFitContract;
    return null;
  })();

  function safeString(value) {
    if (value === undefined || value === null) return "";
    return String(value);
  }

  function safeToken(value, fallback = "item") {
    const token = safeString(value)
      .replace(/\\/g, "/")
      .toLowerCase()
      .replace(/[^a-z0-9._/-]+/g, "-")
      .replace(/[\/]+/g, ".")
      .replace(/^-+|-+$/g, "")
      .replace(/\.+/g, ".")
      .slice(0, 96);
    return token || fallback;
  }

  function setAttrs(element, attrs) {
    if (!element || !attrs) return element;
    Object.entries(attrs).forEach(([name, value]) => {
      if (value === undefined || value === null || value === "") return;
      element.setAttribute(name, String(value));
    });
    return element;
  }

  function removeAttrs(element, names) {
    if (!element || typeof element.removeAttribute !== "function") return element;
    (names || []).forEach((name) => element.removeAttribute(name));
    return element;
  }

  const dynamicEntryNodeAttrs = Object.freeze([
    "data-mcel-node-id",
    "data-mcel-node-type",
    "data-mcel-node-label",
    "data-mcel-channel",
    "data-mcel-signal",
    "data-mcel-source",
    "data-mcel-provenance",
    "data-mcel-symbol",
    "data-mcel-home-region",
    "data-mcel-actual-region",
    "data-mcel-teleported",
    "data-layout-anchor-x",
    "data-layout-anchor-y",
    "data-layout-width",
    "data-layout-height",
    "data-layout-z",
    "data-layout-region",
    "data-layout-ports"
  ]);

  function applyFitPolicy(element, policy, options = {}) {
    if (fitContractApi && typeof fitContractApi.applyFitPolicy === "function") {
      try {
        return fitContractApi.applyFitPolicy(element, policy, options);
      } catch {}
    }
    const attrs = {"data-mcel-fit-policy": policy};
    if (options.readable) attrs["data-mcel-readable"] = "true";
    if (options.visualOwner) attrs["data-mcel-visual-owner"] = options.visualOwner;
    if (options.fitRole) attrs["data-mcel-fit-role"] = options.fitRole;
    if (options.required !== undefined) attrs["data-mcel-fit-required"] = options.required ? "true" : "false";
    return setAttrs(element, attrs);
  }

  function staticSurfaceAttrs() {
    return Object.freeze({
      "data-mcel-surface-id": surfaceId,
      "data-mcel-surface-kind": "application-surface",
      "data-mcel-surface-role": "file-navigation-workbench",
      "data-mcel-surface-contract": surfaceContract,
      "data-mcel-authoritative": "true",
      "data-mcel-renderer": "file-explorer.runtime-dom",
      "data-mcel-projection": "html",
      "data-layout-viewport-width": "1280",
      "data-layout-viewport-height": "720",
      "data-layout-safe-margin": "16",
      "data-mcel-visual-owner": "file-explorer.surface.primary",
      "data-mcel-layout-zone": "file-explorer.surface.primary"
    });
  }

  function regionAttrs(id, role, x, y, width, height) {
    return Object.freeze({
      "data-mcel-region": id,
      "data-mcel-region-role": role,
      "data-layout-x": x,
      "data-layout-y": y,
      "data-layout-region-width": width,
      "data-layout-region-height": height,
      "data-mcel-layout-zone": id,
      "data-mcel-visual-owner": id
    });
  }

  function nodeAttrs(id, type, label, source, provenance, symbol, homeRegion, x, y, width, height, z, signal) {
    return Object.freeze({
      "data-mcel-node-id": id,
      "data-mcel-node-type": type,
      "data-mcel-node-label": label,
      "data-mcel-channel": channel,
      "data-mcel-signal": signal || type,
      "data-mcel-source": source,
      "data-mcel-provenance": provenance,
      "data-mcel-symbol": symbol,
      "data-mcel-home-region": homeRegion,
      "data-mcel-actual-region": homeRegion,
      "data-mcel-teleported": "false",
      "data-layout-anchor-x": x,
      "data-layout-anchor-y": y,
      "data-layout-width": width,
      "data-layout-height": height,
      "data-layout-z": z,
      "data-layout-region": homeRegion,
      "data-layout-ports": "north,south,east,west",
      "data-mcel-readable": "true"
    });
  }

  function edgeAttrs(id, kind, from, to, relation, routeKind, fromPort, toPort, z) {
    return Object.freeze({
      "data-mcel-edge-id": id,
      "data-mcel-edge-kind": kind,
      "data-mcel-from": from,
      "data-mcel-to": to,
      "data-mcel-relation": relation,
      "data-mcel-causal-link": "false",
      "data-mcel-allowed-inferences": "navigation,selection,metadata_lookup",
      "data-mcel-forbidden-inferences": "content_mutation,filesystem_write,identity",
      "data-layout-route-kind": routeKind || "orthogonal",
      "data-layout-from-port": fromPort || "east",
      "data-layout-to-port": toPort || "west",
      "data-layout-z": z || 2
    });
  }

  function controlAttrs(id, action, reveals, x, y, width, height, z) {
    return Object.freeze({
      "data-mcel-control": id,
      "data-mcel-control-action": action,
      "data-mcel-reveals": reveals,
      "data-layout-anchor-x": x,
      "data-layout-anchor-y": y,
      "data-layout-width": width,
      "data-layout-height": height,
      "data-layout-z": z || 5
    });
  }

  function staticRegionRecords() {
    return Object.freeze([
      regionAttrs("file-explorer.region.roots", "filesystem-root-selector", 24, 72, 260, 600),
      regionAttrs("file-explorer.region.toolbar", "navigation-toolbar", 304, 72, 640, 96),
      regionAttrs("file-explorer.region.file-list", "directory-entry-list", 304, 184, 640, 488),
      regionAttrs("file-explorer.region.details", "selected-entry-details", 972, 72, 284, 600),
      regionAttrs("file-explorer.region.status", "navigation-status", 48, 614, 212, 42)
    ]);
  }

  function staticNodeRecords() {
    return Object.freeze([
      nodeAttrs("file-explorer.node.root-set", "root_set", "Available roots", "file-explorer.roots-api", "file-explorer:roots", "⛁", "file-explorer.region.roots", 154, 164, 200, 60, 3, "root_selection"),
      nodeAttrs("file-explorer.node.current-directory", "current_directory", "Current directory", "file-explorer.list-api", "file-explorer:path", "⌁", "file-explorer.region.toolbar", 524, 116, 360, 42, 4, "current_path"),
      nodeAttrs("file-explorer.node.directory-list", "directory_listing", "Directory listing", "file-explorer.list-api", "file-explorer:list", "▤", "file-explorer.region.file-list", 624, 196, 560, 16, 4, "entry_collection"),
      nodeAttrs("file-explorer.node.details-panel", "details_panel", "Selected entry details", "file-explorer.preview", "file-explorer:details", "ⓘ", "file-explorer.region.details", 1114, 380, 240, 360, 4, "entry_metadata")
    ]);
  }

  function staticEdgeRecords() {
    return Object.freeze([
      edgeAttrs("file-explorer.edge.roots-select-current", "SELECTS", "file-explorer.node.root-set", "file-explorer.node.current-directory", "root_selects_current_directory", "orthogonal", "east", "west", 2),
      edgeAttrs("file-explorer.edge.current-contains-list", "CONTAINS", "file-explorer.node.current-directory", "file-explorer.node.directory-list", "directory_contains_visible_entries", "orthogonal", "south", "north", 2),
      edgeAttrs("file-explorer.edge.list-describes-details", "DESCRIBES", "file-explorer.node.directory-list", "file-explorer.node.details-panel", "selected_entry_describes_details", "orthogonal", "east", "west", 2)
    ]);
  }

  function staticControlRecords() {
    return Object.freeze([
      controlAttrs("file-explorer.control.search", "filter_visible_entries", "file-explorer.node.directory-list", 760, 116, 160, 36, 5),
      controlAttrs("file-explorer.control.up", "navigate_parent_directory", "file-explorer.node.current-directory", 895, 116, 72, 36, 5),
      controlAttrs("file-explorer.control.open", "open_selected_entry", "file-explorer.node.details-panel", 1114, 620, 160, 36, 5)
    ]);
  }

  function buildStaticSurfaceRidgeRecords() {
    return Object.freeze([
      staticSurfaceAttrs(),
      ...staticRegionRecords(),
      ...staticNodeRecords(),
      ...staticEdgeRecords(),
      ...staticControlRecords()
    ]);
  }

  function entryNodeId(entry = {}, index = 0) {
    const kind = safeString(entry.kind || "file").toLowerCase() === "directory" ? "folder" : "file";
    const path = entry.relative_path || entry.path_display || entry.name || index;
    return `file-explorer.node.entry.${kind}.${safeToken(path, String(index))}`;
  }

  function rootNodeId(root = {}, index = 0) {
    return `file-explorer.node.root.${safeToken(root.id || root.label || index, String(index))}`;
  }

  function decorateRootButton(button, root = {}, index = 0) {
    const id = rootNodeId(root, index);
    const decorated = setAttrs(button, {
      ...nodeAttrs(
        id,
        "filesystem_root",
        root.label || root.id || "Root",
        "file-explorer.roots-api",
        `file-explorer:root:${safeString(root.id || index)}`,
        "⛁",
        "file-explorer.region.roots",
        154,
        224 + (index * 34),
        212,
        28,
        4,
        "root_option"
      ),
      "data-mcel-visual-owner": "file-explorer.root-button",
      "data-mcel-readable": "true"
    });
    return applyFitPolicy(decorated, "wrap", {
      readable: true,
      visualOwner: "file-explorer.root-button",
      fitRole: "file-explorer-root-label",
      required: true
    });
  }

  function decorateEntryElement(element, entry = {}, options = {}) {
    const index = Number.isFinite(options.index) ? options.index : Number(options.index || 0);
    const kind = safeString(entry.kind || "file").toLowerCase() === "directory" ? "folder_item" : "file_item";
    const symbol = kind === "folder_item" ? "▸" : "•";
    if (index >= maxLayoutEntryNodes) {
      const overflowDecorated = setAttrs(removeAttrs(element, dynamicEntryNodeAttrs), {
        "data-mcel-visual-owner": "file-explorer.entry",
        "data-mcel-readable": "true",
        "data-mcel-overflow-entry": "true"
      });
      return applyFitPolicy(overflowDecorated, "truncate", {
        readable: true,
        visualOwner: "file-explorer.entry",
        fitRole: "file-explorer-entry-label",
        required: true
      });
    }
    const y = firstEntryAnchorY + (index * entryAnchorStepY);
    const decorated = setAttrs(removeAttrs(element, dynamicEntryNodeAttrs), {
      ...nodeAttrs(
        entryNodeId(entry, index),
        kind,
        entry.name || entry.relative_path || entry.path_display || "Entry",
        "file-explorer.list-api",
        `file-explorer:entry:${safeString(options.rootId || "root")}:${safeString(entry.relative_path || entry.path_display || index)}`,
        symbol,
        "file-explorer.region.file-list",
        624,
        y,
        560,
        24,
        5,
        "directory_entry"
      ),
      "data-mcel-visual-owner": "file-explorer.entry",
      "data-mcel-readable": "true"
    });
    return applyFitPolicy(decorated, "truncate", {
      readable: true,
      visualOwner: "file-explorer.entry",
      fitRole: "file-explorer-entry-label",
      required: true
    });
  }

  function decorateTreeHost(host) {
    return setAttrs(host, nodeAttrs(
      "file-explorer.node.directory-list",
      "directory_listing",
      "Directory listing",
      "file-explorer.list-api",
      "file-explorer:list",
      "▤",
      "file-explorer.region.file-list",
      624,
      420,
      560,
      360,
      4,
      "entry_collection"
    ));
  }

  function decoratePreviewPanel(panel, entry = null) {
    if (!panel) return panel;
    const label = entry ? `Selected ${entry.kind || "entry"} details` : "Selected entry details";
    return setAttrs(panel, nodeAttrs(
      "file-explorer.node.details-panel",
      "details_panel",
      label,
      "file-explorer.preview",
      entry ? `file-explorer:details:${safeString(entry.relative_path || entry.name || "selected")}` : "file-explorer:details",
      "ⓘ",
      "file-explorer.region.details",
      1114,
      380,
      240,
      360,
      4,
      "entry_metadata"
    ));
  }

  function applyStaticSurfaceRidges(root) {
    const scope = root || (typeof document !== "undefined" ? document : null);
    if (!scope || typeof scope.querySelector !== "function") return null;
    const app = scope.querySelector("#file-explorer-app");
    if (!app) return null;
    setAttrs(app, staticSurfaceAttrs());
    const mappings = [
      [".file-explorer-roots-panel", staticRegionRecords()[0]],
      [".file-explorer-toolbar", staticRegionRecords()[1]],
      ["#file-explorer-list", staticRegionRecords()[2]],
      ["#file-explorer-preview", staticRegionRecords()[3]],
      ["#file-explorer-status", staticRegionRecords()[4]],
      ["#file-explorer-path", staticNodeRecords()[1]],
      ["#file-explorer-list", staticNodeRecords()[2]],
      ["#file-explorer-preview", staticNodeRecords()[3]],
      ["#file-explorer-search-run", staticControlRecords()[0]],
      ["#file-explorer-up", staticControlRecords()[1]]
    ];
    mappings.forEach(([selector, attrs]) => setAttrs(scope.querySelector(selector), attrs));
    applyFitPolicy(scope.querySelector("#file-explorer-path"), "wrap", {
      readable: true,
      visualOwner: "file-explorer.current-path",
      fitRole: "file-explorer-current-path",
      required: true
    });
    applyFitPolicy(scope.querySelector(".file-explorer-roots-panel"), "scroll", {
      fitRole: "file-explorer-roots-scroll",
      required: true
    });
    applyFitPolicy(scope.querySelector("#file-explorer-list"), "scroll", {
      fitRole: "file-explorer-list-scroll",
      required: true
    });
    decoratePreviewPanel(scope.querySelector("#file-explorer-preview"));
    return app;
  }

  function extractCurrentSurface(root) {
    if (!extractorsApi || typeof extractorsApi.extractSurfaceBundleFromHtml !== "function") {
      return Object.freeze({valid: false, diagnostics: Object.freeze([{code: "surface-extractors-missing", severity: "error", finding: "McelSurfaceExtractors is not loaded."}])});
    }
    const scope = root || (typeof document !== "undefined" ? document : null);
    const app = scope && typeof scope.querySelector === "function" ? scope.querySelector("#file-explorer-app") : null;
    const html = app && app.outerHTML ? app.outerHTML : "";
    if (!html) return Object.freeze({valid: false, diagnostics: Object.freeze([{code: "file-explorer-surface-missing", severity: "error", finding: "File Explorer surface is not mounted."}])});
    return extractorsApi.extractSurfaceBundleFromHtml(html, {surfaceId});
  }

  return Object.freeze({
    contractVersion,
    surfaceId,
    surfaceContract,
    buildStaticSurfaceRidgeRecords,
    staticSurfaceAttrs,
    staticRegionRecords,
    staticNodeRecords,
    staticEdgeRecords,
    staticControlRecords,
    entryNodeId,
    rootNodeId,
    decorateRootButton,
    decorateEntryElement,
    decorateTreeHost,
    decoratePreviewPanel,
    applyStaticSurfaceRidges,
    extractCurrentSurface
  });
})();

if (typeof window !== "undefined") {
  window.McelFileExplorerSurface = McelFileExplorerSurface;
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => McelFileExplorerSurface.applyStaticSurfaceRidges(document), {once: true});
    } else {
      McelFileExplorerSurface.applyStaticSurfaceRidges(document);
    }
  }
}
