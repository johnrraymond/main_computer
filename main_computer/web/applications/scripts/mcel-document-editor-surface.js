var McelDocumentEditorSurface = (() => {
  "use strict";

  const contractVersion = "mcel.document-editor-surface.v1";
  const surfaceId = "document-editor.surface.primary";
  const surfaceContract = "document-editor.contract.semantic-surface-pilot";
  const runtimeContractId = "document-editor.contract.default.app-health";
  const channel = "DOCUMENT_EDITOR";

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

  function setAttrs(element, attrs) {
    if (!element || !attrs) return element;
    if (typeof element.setAttribute !== "function" && typeof element.forEach === "function") {
      element.forEach((item) => setAttrs(item, attrs));
      return element;
    }
    if (typeof element.setAttribute !== "function") return element;
    Object.entries(attrs).forEach(([name, value]) => {
      if (value === undefined || value === null || value === "") return;
      element.setAttribute(name, String(value));
    });
    return element;
  }

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
      "data-mcel-surface-role": "document-authoring-workbench",
      "data-mcel-surface-contract": surfaceContract,
      "data-mcel-authoritative": "true",
      "data-mcel-renderer": "document-editor.runtime-dom",
      "data-mcel-projection": "html",
      "data-layout-viewport-width": "1440",
      "data-layout-viewport-height": "900",
      "data-layout-safe-margin": "16",
      "data-mcel-visual-owner": surfaceId,
      "data-mcel-layout-zone": surfaceId
    });
  }

  function regionAttrs(id, role, x, y, width, height) {
    return Object.freeze({
      "data-mcel-region": `document-editor.region.${id}`,
      "data-mcel-region-role": role,
      "data-layout-x": x,
      "data-layout-y": y,
      "data-layout-region-width": width,
      "data-layout-region-height": height,
      "data-mcel-visual-owner": `document-editor.region.${id}`,
      "data-mcel-zone": `document-editor.region.${id}`
    });
  }

  function nodeAttrs(id, type, label, region, layout, source = "document-editor.dom") {
    return Object.freeze({
      "data-mcel-node-id": `document-editor.node.${id}`,
      "data-mcel-node-type": type,
      "data-mcel-node-label": label,
      "data-mcel-source": source,
      "data-mcel-provenance": "patch:mcel-safe-20-document-editor-surface",
      "data-mcel-channel": channel,
      "data-mcel-home-region": `document-editor.region.${region}`,
      "data-mcel-actual-region": `document-editor.region.${region}`,
      "data-layout-anchor-x": layout.x,
      "data-layout-anchor-y": layout.y,
      "data-layout-width": layout.width,
      "data-layout-height": layout.height,
      "data-layout-z": layout.z || 1,
      "data-layout-region": `document-editor.region.${region}`,
      "data-layout-ports": "north,south,east,west",
      "data-mcel-readable": "true"
    });
  }

  function edgeAttrs(id, kind, from, to, relation, routeKind, fromPort, toPort, z = 1) {
    return Object.freeze({
      "data-mcel-edge-id": `document-editor.edge.${id}`,
      "data-mcel-edge-kind": kind,
      "data-mcel-from": `document-editor.node.${from}`,
      "data-mcel-to": `document-editor.node.${to}`,
      "data-mcel-relation": relation,
      "data-mcel-causal-link": "false",
      "data-mcel-allowed-inferences": "surface-navigation,content-selection,layout-export",
      "data-mcel-forbidden-inferences": "unrelated-domain,identity-claim",
      "data-layout-route-kind": routeKind,
      "data-layout-from-port": fromPort,
      "data-layout-to-port": toPort,
      "data-layout-z": z
    });
  }

  function controlAttrs(id, action, reveals, layout) {
    return Object.freeze({
      "data-mcel-control": `document-editor.control.${id}`,
      "data-mcel-control-action": action,
      "data-mcel-reveals": reveals ? `document-editor.node.${reveals}` : "",
      "data-layout-anchor-x": layout.x,
      "data-layout-anchor-y": layout.y,
      "data-layout-width": layout.width,
      "data-layout-height": layout.height,
      "data-layout-z": layout.z || 2,
      "data-mcel-source": "document-editor.dom",
      "data-mcel-provenance": "patch:mcel-safe-20-document-editor-surface",
      "data-mcel-readable": "true"
    });
  }

  function staticRegionRecords() {
    return Object.freeze([
      regionAttrs("navigation", "document-library-navigation", 16, 16, 272, 868),
      regionAttrs("menu", "document-session-menu", 304, 16, 832, 72),
      regionAttrs("toolbar", "document-format-toolbar", 304, 88, 832, 64),
      regionAttrs("status", "document-status-strip", 304, 152, 832, 48),
      regionAttrs("primary", "document-authoring-primary", 304, 168, 832, 716),
      regionAttrs("advanced", "document-plugin-rail", 320, 216, 64, 620),
      regionAttrs("document-page", "document-page-frame", 392, 216, 656, 620),
      regionAttrs("document-content", "document-editable-content", 424, 264, 592, 524),
      regionAttrs("companion", "document-ai-companion", 1152, 16, 272, 868)
    ]);
  }

  function staticNodeRecords() {
    return Object.freeze([
      nodeAttrs("document-session", "document_session", "Document editing session", "menu", {x: 420, y: 52, width: 180, height: 40}),
      nodeAttrs("selected-document", "selected_document", "Selected document path", "menu", {x: 640, y: 52, width: 180, height: 40}),
      nodeAttrs("document-library", "document_library", "Pretty Docs library", "navigation", {x: 152, y: 128, width: 216, height: 56}),
      nodeAttrs("layout-state", "layout_state", "Page layout state", "toolbar", {x: 440, y: 120, width: 180, height: 40}),
      nodeAttrs("export-target", "export_target", "Document export target", "menu", {x: 1000, y: 52, width: 160, height: 40}),
      nodeAttrs("document-page", "document_page", "Document page", "document-page", {x: 516, y: 260, width: 160, height: 64}),
      nodeAttrs("document-content", "document_content", "Editable document content", "document-content", {x: 728, y: 360, width: 160, height: 64}),
      nodeAttrs("document-block", "document_block", "Authored document block", "document-content", {x: 728, y: 500, width: 160, height: 64}),
      nodeAttrs("selected-object", "selected_object", "Selected embedded document object", "primary", {x: 932, y: 620, width: 160, height: 64}),
      nodeAttrs("ai-context", "ai_context", "Document AI context", "companion", {x: 1288, y: 200, width: 220, height: 64}),
      nodeAttrs("status-message", "status_message", "Document status message", "status", {x: 980, y: 176, width: 160, height: 32})
    ]);
  }

  function staticEdgeRecords() {
    return Object.freeze([
      edgeAttrs("library-selects-document", "NAVIGATION_SELECTS_DOCUMENT", "document-library", "selected-document", "library selection selects the active document", "orthogonal", "east", "west"),
      edgeAttrs("session-owns-page", "SESSION_OWNS_PAGE", "document-session", "document-page", "document session owns the active page", "orthogonal", "south", "north"),
      edgeAttrs("page-owns-content", "PAGE_OWNS_CONTENT", "document-page", "document-content", "page owns editable content", "straight", "south", "north"),
      edgeAttrs("content-contains-block", "DOCUMENT_CONTAINS_BLOCK", "document-content", "document-block", "content contains authored blocks", "straight", "south", "north"),
      edgeAttrs("content-targets-object", "SELECTION_TARGETS_OBJECT", "document-content", "selected-object", "selection targets embedded objects", "orthogonal", "east", "west"),
      edgeAttrs("toolbar-configures-layout", "LAYOUT_CONFIGURES_PAGE", "layout-state", "document-page", "layout controls configure page geometry", "orthogonal", "south", "north"),
      edgeAttrs("companion-describes-content", "COMPANION_DESCRIBES_DOCUMENT", "ai-context", "document-content", "companion context describes document content", "orthogonal", "west", "east"),
      edgeAttrs("export-projects-document", "EXPORT_PROJECTS_DOCUMENT", "export-target", "document-content", "export target projects document content", "orthogonal", "south", "north")
    ]);
  }

  function staticControlRecords() {
    return Object.freeze([
      controlAttrs("toggle-library", "toggle_document_library", "document-library", {x: 1180, y: 60, width: 64, height: 32}),
      controlAttrs("toggle-ai", "toggle_document_ai_companion", "ai-context", {x: 1260, y: 60, width: 48, height: 32}),
      controlAttrs("insert-scene", "insert_document_scene", "document-block", {x: 1180, y: 104, width: 72, height: 32}),
      controlAttrs("export-pdf", "export_document_pdf", "export-target", {x: 1268, y: 104, width: 72, height: 32}),
      controlAttrs("format-bold", "format_text_bold", "document-content", {x: 340, y: 120, width: 52, height: 32}),
      controlAttrs("layout-apply", "apply_page_layout", "layout-state", {x: 420, y: 120, width: 80, height: 32}),
      controlAttrs("reload-disk", "reload_document_from_disk", "selected-document", {x: 520, y: 176, width: 96, height: 32}),
      controlAttrs("discard-draft", "discard_document_draft", "selected-document", {x: 640, y: 176, width: 96, height: 32}),
      controlAttrs("ai-apply", "apply_ai_suggestion", "document-content", {x: 1268, y: 760, width: 104, height: 32}),
      controlAttrs("ai-send", "send_ai_prompt", "ai-context", {x: 1180, y: 820, width: 72, height: 32})
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

  function ensureSemanticCarrier(app) {
    if (!app || typeof app.querySelector !== "function") return null;
    let carrier = app.querySelector("#document-mcel-surface-carriers");
    if (!carrier && typeof document !== "undefined" && typeof document.createElement === "function") {
      carrier = document.createElement("div");
      carrier.id = "document-mcel-surface-carriers";
      carrier.hidden = true;
      carrier.setAttribute("aria-hidden", "true");
      app.appendChild(carrier);
    }
    return carrier;
  }

  function writeCarrierRecord(carrier, attrs, index, kind) {
    if (!carrier || !attrs) return null;
    const doc = carrier.ownerDocument || (typeof document !== "undefined" ? document : null);
    if (!doc?.createElement) return null;
    let node = carrier.querySelector(`[data-document-mcel-carrier="${kind}.${index}"]`);
    if (!node) {
      node = doc.createElement("span");
      node.setAttribute("data-document-mcel-carrier", `${kind}.${index}`);
      carrier.appendChild(node);
    }
    setAttrs(node, attrs);
    return node;
  }

  function applyStaticSurfaceRidges(root) {
    const scope = root || (typeof document !== "undefined" ? document : null);
    const app = scope && typeof scope.querySelector === "function" ? scope.querySelector("#document-app") : null;
    if (!app) return null;

    setAttrs(app, staticSurfaceAttrs());
    setAttrs(scope.querySelector(".document-shell"), {
      "data-mcel-visual-owner": surfaceId,
      "data-mcel-layout-zone": surfaceId
    });
    setAttrs(scope.querySelector(".document-head-actions"), {
      "data-mcel-visual-owner": surfaceId
    });
    [
      ".document-library-head",
      ".document-canvas",
      ".document-ai-main",
      ".document-ai-composer"
    ].forEach((selector) => setAttrs(scope.querySelector(selector), {
      "data-mcel-visual-owner": surfaceId,
      "data-mcel-readable": "true"
    }));
    [
      ".document-library-head",
      ".document-library-head strong",
      ".document-library-head-actions"
    ].forEach((selector) => applyFitPolicy(scope.querySelector(selector), "wrap", {
      fitRole: "document-library-header",
      required: true
    }));
    [
      "#document-library-refresh",
      "#document-library-close"
    ].forEach((selector) => applyFitPolicy(scope.querySelector(selector), "compact-icon", {
      readable: true,
      visualOwner: surfaceId,
      fitRole: "document-library-header-control",
      required: true
    }));
    [
      ".document-library-item strong",
      ".document-library-item span"
    ].forEach((selector) => applyFitPolicy(scope.querySelectorAll(selector), "truncate", {
      fitRole: "document-library-item-label",
      required: true
    }));
    [
      ".document-library-list",
      "#document-export-menu"
    ].forEach((selector) => setAttrs(scope.querySelector(selector), {
      "data-mcel-visual-owner": surfaceId
    }));

    const regionMappings = [
      ["#document-library", staticRegionRecords()[0]],
      [".document-head", staticRegionRecords()[1]],
      ["#document-toolbar", staticRegionRecords()[2]],
      [".document-top-status", staticRegionRecords()[3]],
      ["#document-object-stage", staticRegionRecords()[4]],
      ["#document-plugin-rail", staticRegionRecords()[5]],
      ["#document-page", staticRegionRecords()[6]],
      ["#document-editor", staticRegionRecords()[7]],
      ["#document-ai-pane", staticRegionRecords()[8]]
    ];
    regionMappings.forEach(([selector, attrs]) => setAttrs(scope.querySelector(selector), attrs));

    const nodeMappings = [
      [".document-identity", staticNodeRecords()[0]],
      ["#document-current-path", staticNodeRecords()[1]],
      ["#document-library-list", staticNodeRecords()[2]],
      ["#document-layout-popover", staticNodeRecords()[3]],
      ["#document-export-menu", staticNodeRecords()[4]],
      [".mc-page-break-guide", staticNodeRecords()[5]],
      ["#document-editor", null],
      ["#document-draft-state", staticNodeRecords()[10]],
      ["#document-ai-anchor-summary", staticNodeRecords()[9]]
    ];
    nodeMappings.forEach(([selector, attrs]) => attrs && setAttrs(scope.querySelector(selector), attrs));
    [
      "#document-library-list",
      "#document-export-menu"
    ].forEach((selector) => scope.querySelector(selector)?.removeAttribute("data-mcel-readable"));

    const carrier = ensureSemanticCarrier(app);
    staticNodeRecords().slice(6, 9).forEach((attrs, index) => writeCarrierRecord(carrier, attrs, index, "node"));
    staticEdgeRecords().forEach((attrs, index) => writeCarrierRecord(carrier, attrs, index, "edge"));

    const controlMappings = [
      ["#document-library-toggle", staticControlRecords()[0]],
      ["#document-ai-toggle", staticControlRecords()[1]],
      ["#document-insert-scene", staticControlRecords()[2]],
      ["#document-export-pdf", staticControlRecords()[3]],
      ["[data-document-command=\"bold\"]", staticControlRecords()[4]],
      ["#document-layout-apply", staticControlRecords()[5]],
      ["#document-reload-doc", staticControlRecords()[6]],
      ["#document-discard-draft", staticControlRecords()[7]],
      ["#document-ai-apply", staticControlRecords()[8]],
      ["#document-ai-send", staticControlRecords()[9]]
    ];
    controlMappings.forEach(([selector, attrs]) => setAttrs(scope.querySelector(selector), attrs));

    [
      "#document-current-path",
      "#document-status",
      "#document-library-status",
      "#document-draft-state",
      "#document-version-token",
      "#document-ai-status",
      "#document-ai-anchor-summary",
      "#document-ai-preview",
      "#document-editor"
    ].forEach((selector) => setAttrs(scope.querySelector(selector), {"data-mcel-readable": "true"}));

    return app;
  }

  function extractCurrentSurface(root) {
    if (!extractorsApi || typeof extractorsApi.extractSurfaceBundleFromHtml !== "function") {
      return Object.freeze({
        valid: false,
        diagnostics: Object.freeze([{code: "surface-extractors-missing", severity: "error", finding: "McelSurfaceExtractors is not loaded."}])
      });
    }
    const scope = root || (typeof document !== "undefined" ? document : null);
    const app = scope && typeof scope.querySelector === "function" ? scope.querySelector("#document-app") : null;
    const html = app && app.outerHTML ? app.outerHTML : "";
    if (!html) {
      return Object.freeze({
        valid: false,
        diagnostics: Object.freeze([{code: "document-editor-surface-missing", severity: "error", finding: "Document Editor surface is not mounted."}])
      });
    }
    return extractorsApi.extractSurfaceBundleFromHtml(html, {surfaceId});
  }

  return Object.freeze({
    contractVersion,
    surfaceId,
    surfaceContract,
    runtimeContractId,
    buildStaticSurfaceRidgeRecords,
    staticSurfaceAttrs,
    staticRegionRecords,
    staticNodeRecords,
    staticEdgeRecords,
    staticControlRecords,
    applyStaticSurfaceRidges,
    extractCurrentSurface
  });
})();

if (typeof window !== "undefined") {
  window.McelDocumentEditorSurface = McelDocumentEditorSurface;
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => McelDocumentEditorSurface.applyStaticSurfaceRidges(document), {once: true});
    } else {
      McelDocumentEditorSurface.applyStaticSurfaceRidges(document);
    }
  }
}
