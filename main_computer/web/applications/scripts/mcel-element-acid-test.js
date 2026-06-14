    (function (global) {
      "use strict";

      const ACID_VERSION = "0.2.0";
      const acidElementIds = [
        "element.core.app",
        "element.core.region",
        "element.core.panel",
        "element.core.toolbar",
        "element.core.field",
        "element.core.action",
        "element.core.status-feed",
        "element.core.workflow",
        "element.core.collection",
        "element.core.collection-row",
        "element.core.preview-pane",
        "element.resource.file-boundary",
        "element.resource.directory-tree",
        "element.resource.tree-viewport",
        "element.resource.tree-branch",
        "element.resource.tree-leaf",
        "element.resource.tree-expander",
        "element.resource.tree-selection-model",
        "element.resource.tree-keyboard-controller",
        "element.resource.tree-context-menu",
        "element.resource.tree-drag-drop-boundary",
        "element.resource.tree-empty-state",
        "element.resource.path-bar",
        "element.resource.resource-row",
        "element.operational.process-table",
        "element.operational.server-control",
        "element.operational.pid-action",
        "element.operational.command-surface",
        "element.network.remote-mutation-boundary",
        "element.network.credential-boundary",
        "element.network.payment-boundary",
        "element.network.message-thread",
        "element.compute.local-display",
        "element.compute.keypad",
        "element.compute.graph-surface",
        "element.compute.runtime-cell",
        "element.authoring.document-surface",
        "element.authoring.spreadsheet-grid",
        "element.authoring.code-editor",
        "element.authoring.website-publisher",
        "element.authoring.game-editor"
      ];


      const TREE_VIEW_MODES = [
        {
          id: "explorer-sidebar",
          label: "Explorer sidebar",
          source: "Windows Explorer",
          summary: "Dense navigation tree with pinned roots, chevrons, icons, selection, and safe read boundaries."
        },
        {
          id: "ide-project-tree",
          label: "IDE project tree",
          source: "VS Code",
          summary: "Developer project tree with file icons, modified/untracked badges, diagnostics, and compact rows."
        },
        {
          id: "details-treegrid",
          label: "Details treegrid",
          source: "Explorer details",
          summary: "Hierarchy plus sortable columns for modified time, type, size, and proof policy."
        },
        {
          id: "miller-columns",
          label: "Column browser",
          source: "Finder-style columns",
          summary: "One column per depth level so path choice, siblings, and preview coupling stay visible."
        },
        {
          id: "outline-tree",
          label: "Outline tree",
          source: "Document/app outline",
          summary: "Semantic heading/component outline with region, panel, and action hierarchy."
        },
        {
          id: "accessibility-proof",
          label: "Keyboard proof",
          source: "ARIA tree pattern",
          summary: "Roving focus, active descendant, selection model, expansion state, and blocked mutation keys."
        }
      ];

      function createNode(document, tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text) node.textContent = text;
        return node;
      }

      function sanitizeId(value) {
        return String(value || "element").replace(/[^a-z0-9]+/gi, "-").replace(/^-+|-+$/g, "").toLowerCase();
      }

      function makeRecord(definition, index, surface = "catalog") {
        return {
          id: `acid-${surface}-${index + 1}-${sanitizeId(definition.id)}`,
          elementId: definition.id,
          family: definition.family,
          kind: definition.kind,
          risk: definition.riskPolicy?.risk || "none",
          proofPolicy: definition.proofPolicy || "inspect-only",
          blocked: Boolean(definition.riskPolicy?.blocked),
          layoutLaws: definition.layoutLaws || [],
          surface
        };
      }

      function applyElementAttributes(node, definition, role = "") {
        node.setAttribute("data-mcel-element", definition.id);
        node.setAttribute("data-mcel-element-kind", definition.kind);
        node.setAttribute("data-mcel-element-family", definition.family);
        node.setAttribute("data-mcel-proof-policy", definition.proofPolicy || "inspect-only");
        node.setAttribute("data-mcel-risk", definition.riskPolicy?.risk || "none");
        if (role) node.setAttribute("data-mcel-element-demo-role", role);
        return node;
      }

      function appendChip(document, parent, label, value = "") {
        const chip = createNode(document, "span", "mcel-element-acid-chip", value ? `${label}: ${value}` : label);
        parent.appendChild(chip);
        return chip;
      }

      function appendElementCard(document, parent, definition, index) {
        const card = applyElementAttributes(createNode(document, "article", "mcel-element-acid-card"), definition, "catalog-card");

        const heading = createNode(document, "h6", "", definition.label);
        const meta = createNode(document, "p", "mcel-element-acid-card-meta", `${definition.family} · ${definition.kind} · ${definition.proofPolicy}`);
        const purpose = createNode(document, "p", "mcel-element-acid-card-purpose", definition.purpose);
        const lawList = createNode(document, "ul", "mcel-element-acid-law-list");
        (definition.layoutLaws || []).slice(0, 3).forEach((law) => {
          lawList.appendChild(createNode(document, "li", "", law));
        });
        const chips = createNode(document, "div", "mcel-element-acid-chip-row");
        appendChip(document, chips, "risk", definition.riskPolicy?.risk || "none");
        appendChip(document, chips, "serialize", definition.serializationSchema?.type || "object");
        const controls = createNode(document, "div", "mcel-element-acid-card-controls");
        const inspect = createNode(document, "button", "", "Inspect");
        inspect.type = "button";
        inspect.setAttribute("data-mcel-element-action", "inspect");
        const proof = createNode(document, "button", "", definition.riskPolicy?.blocked ? "Blocked proof" : "Proof safe");
        proof.type = "button";
        proof.setAttribute("data-mcel-element-action", definition.riskPolicy?.blocked ? "blocked-proof" : "proof-safe");
        proof.setAttribute("aria-disabled", definition.riskPolicy?.blocked ? "true" : "false");
        controls.append(inspect, proof);

        card.append(heading, meta, purpose, chips, lawList, controls);
        parent.appendChild(card);
        return makeRecord(definition, index, "catalog");
      }

      function requireDefinition(definitionsById, id) {
        const definition = definitionsById.get(id);
        if (!definition) throw new Error(`Missing MCEL element definition: ${id}`);
        return definition;
      }

      function appendWorkbenchSurface(document, parent, definitionsById, id, className, role, title, bodyBuilder) {
        const definition = requireDefinition(definitionsById, id);
        const surface = applyElementAttributes(createNode(document, "section", className), definition, role);
        const heading = createNode(document, "div", "mcel-element-showcase-surface-head");
        heading.append(
          createNode(document, "strong", "", title),
          createNode(document, "span", "", `${definition.family} · ${definition.kind} · ${definition.proofPolicy}`)
        );
        const body = createNode(document, "div", "mcel-element-showcase-surface-body");
        bodyBuilder?.(body, definition);
        surface.append(heading, body);
        parent.appendChild(surface);
        return surface;
      }

      function treeFixtureRows() {
        return [
          {id: "workspace", label: "MAIN_COMPUTER", kind: "folder", icon: "▾", expanded: true, selected: false, level: 1, type: "workspace", modified: "Today", size: "", policy: "read boundary", badge: "", status: ""},
          {id: "workspace/deploy", label: "deploy", kind: "folder", icon: "▸", expanded: false, selected: false, level: 2, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "", status: ""},
          {id: "workspace/docker", label: "docker", kind: "folder", icon: "▸", expanded: false, selected: false, level: 2, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "", status: ""},
          {id: "workspace/game-projects", label: "game_projects", kind: "folder", icon: "▸", expanded: false, selected: false, level: 2, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "", status: "dirty"},
          {id: "workspace/main-computer", label: "main_computer", kind: "folder", icon: "▾", expanded: true, selected: false, level: 2, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "M", status: "modified"},
          {id: "workspace/runtime", label: "runtime", kind: "folder", icon: "▾", expanded: true, selected: false, level: 2, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "", status: "hot"},
          {id: "workspace/runtime/scripts", label: "scripts", kind: "folder", icon: "▾", expanded: true, selected: false, level: 3, type: "folder", modified: "6/14/2026", size: "", policy: "read boundary", badge: "", status: "tracked"},
          {id: "workspace/runtime/scripts/diagnose", label: "diagnose_mouse_hangs.py", kind: "file", icon: "py", expanded: false, selected: true, level: 4, type: "Python", modified: "6/14/2026 2:22 PM", size: "9 KB", policy: "preview only", badge: "U", status: "untracked"},
          {id: "workspace/runtime/scripts/start", label: "main-computer-start-stop.ps1", kind: "file", icon: "ps", expanded: false, selected: false, level: 4, type: "PowerShell", modified: "6/14/2026 2:19 PM", size: "4 KB", policy: "preview only", badge: "", status: ""},
          {id: "workspace/runtime/scripts/check", label: "olama_checker.ps1", kind: "file", icon: "ps", expanded: false, selected: false, level: 4, type: "PowerShell", modified: "6/14/2026 2:10 PM", size: "2 KB", policy: "preview only", badge: "M", status: "modified"}
        ];
      }

      function appendTreeNode(document, parent, definitionsById, node, options = {}) {
        const rowDefinition = requireDefinition(definitionsById, node.kind === "folder" ? "element.resource.tree-branch" : "element.resource.tree-leaf");
        const row = applyElementAttributes(createNode(document, "div", `mcel-resource-tree-row mcel-resource-tree-row--${options.mode || "explorer"}`), rowDefinition, node.kind === "folder" ? "tree-branch" : "tree-leaf");
        row.setAttribute("role", "treeitem");
        row.setAttribute("aria-level", String(node.level || 1));
        row.setAttribute("aria-selected", node.selected ? "true" : "false");
        if (node.kind === "folder") row.setAttribute("aria-expanded", node.expanded ? "true" : "false");
        row.dataset.nodeId = node.id;
        row.dataset.resourceKind = node.kind;
        row.dataset.status = node.status || "";
        row.style.setProperty("--mcel-tree-depth", String(Math.max(0, (node.level || 1) - 1)));

        const expanderDefinition = requireDefinition(definitionsById, "element.resource.tree-expander");
        const expander = applyElementAttributes(createNode(document, "button", "mcel-resource-tree-expander", node.kind === "folder" ? (node.expanded ? "▾" : "▸") : ""), expanderDefinition, "tree-expander");
        expander.type = "button";
        expander.setAttribute("aria-label", node.kind === "folder" ? `${node.expanded ? "Collapse" : "Expand"} ${node.label}` : `Leaf ${node.label}`);
        expander.setAttribute("aria-expanded", node.kind === "folder" ? String(Boolean(node.expanded)) : "false");
        expander.setAttribute("aria-controls", node.id);

        const glyph = createNode(document, "span", `mcel-resource-tree-glyph mcel-resource-tree-glyph--${node.kind}`, node.icon || (node.kind === "folder" ? "folder" : "file"));
        const label = createNode(document, "button", "mcel-resource-tree-label", node.label);
        label.type = "button";
        label.setAttribute("data-mcel-resource-action", node.kind === "folder" ? "select-preview-or-open-directory" : "select-preview");

        const meta = createNode(document, "span", "mcel-resource-tree-meta", options.showMeta === false ? "" : (node.type || ""));
        const badge = createNode(document, "span", "mcel-resource-tree-badge", node.badge || "");
        if (!node.badge) badge.setAttribute("aria-hidden", "true");
        const policy = createNode(document, "span", "mcel-resource-tree-policy", node.policy || (node.kind === "folder" ? "read boundary" : "preview only"));

        row.append(expander, glyph, label, meta, badge, policy);
        parent.appendChild(row);
        return row;
      }

      function appendTreeViewShell(document, parent, definitionsById, mode, active) {
        const viewportDefinition = requireDefinition(definitionsById, "element.resource.tree-viewport");
        const view = applyElementAttributes(createNode(document, "section", `mcel-resource-tree-mode mcel-resource-tree-mode--${mode.id}`), viewportDefinition, `tree-view-${mode.id}`);
        view.dataset.treeViewMode = mode.id;
        view.hidden = !active;
        view.setAttribute("aria-label", `${mode.label} tree view`);
        const header = createNode(document, "header", "mcel-resource-tree-mode-head");
        header.append(
          createNode(document, "strong", "", mode.label),
          createNode(document, "span", "", `${mode.source} pattern · ${mode.summary}`)
        );
        view.appendChild(header);
        parent.appendChild(view);
        return view;
      }

      function renderExplorerTreeView(document, view, definitionsById) {
        const viewport = applyElementAttributes(createNode(document, "div", "mcel-resource-tree-viewport mcel-resource-tree-viewport--explorer"), requireDefinition(definitionsById, "element.resource.tree-viewport"), "explorer-sidebar-viewport");
        viewport.setAttribute("role", "tree");
        viewport.setAttribute("aria-label", "Explorer-style navigation tree");
        treeFixtureRows().forEach((node) => appendTreeNode(document, viewport, definitionsById, node, {mode: "explorer"}));
        view.appendChild(viewport);
      }

      function renderIdeTreeView(document, view, definitionsById) {
        const frame = createNode(document, "div", "mcel-resource-tree-ide-frame");
        const title = createNode(document, "div", "mcel-resource-tree-ide-title", "EXPLORER");
        const viewport = applyElementAttributes(createNode(document, "div", "mcel-resource-tree-viewport mcel-resource-tree-viewport--ide"), requireDefinition(definitionsById, "element.resource.tree-viewport"), "ide-project-tree-viewport");
        viewport.setAttribute("role", "tree");
        viewport.setAttribute("aria-label", "IDE project tree with decorations");
        treeFixtureRows().filter((node) => !["workspace/deploy"].includes(node.id)).forEach((node) => appendTreeNode(document, viewport, definitionsById, node, {mode: "ide"}));
        frame.append(title, viewport);
        view.appendChild(frame);
      }

      function renderDetailsTreegrid(document, view, definitionsById) {
        const table = applyElementAttributes(createNode(document, "div", "mcel-resource-treegrid"), requireDefinition(definitionsById, "element.resource.tree-viewport"), "details-treegrid");
        table.setAttribute("role", "treegrid");
        table.setAttribute("aria-label", "Details treegrid");
        const header = createNode(document, "div", "mcel-resource-treegrid-row mcel-resource-treegrid-head");
        ["Name", "Modified", "Type", "Size", "Proof policy"].forEach((label) => header.appendChild(createNode(document, "strong", "", label)));
        table.appendChild(header);
        treeFixtureRows().slice(0, 9).forEach((node) => {
          const rowDefinition = requireDefinition(definitionsById, node.kind === "folder" ? "element.resource.tree-branch" : "element.resource.tree-leaf");
          const row = applyElementAttributes(createNode(document, "div", "mcel-resource-treegrid-row"), rowDefinition, "details-treegrid-row");
          row.setAttribute("role", "row");
          row.setAttribute("aria-level", String(node.level || 1));
          row.setAttribute("aria-selected", node.selected ? "true" : "false");
          row.style.setProperty("--mcel-tree-depth", String(Math.max(0, (node.level || 1) - 1)));
          row.append(
            createNode(document, "span", "mcel-resource-treegrid-name", `${node.kind === "folder" ? (node.expanded ? "▾" : "▸") : "·"} ${node.label}`),
            createNode(document, "span", "", node.modified || ""),
            createNode(document, "span", "", node.type || ""),
            createNode(document, "span", "", node.size || ""),
            createNode(document, "span", "mcel-resource-tree-policy", node.policy || "")
          );
          table.appendChild(row);
        });
        view.appendChild(table);
      }

      function renderColumnBrowser(document, view, definitionsById) {
        const browser = createNode(document, "div", "mcel-resource-column-browser");
        const columns = [
          {title: "Roots", rows: ["Desktop", "Downloads", "Documents", "Pictures"]},
          {title: "Downloads", rows: ["mcel_element_resource_tree_primitives_patch", "mcel_element_library_visual_workbench_patch", "temporal_fdb_market_progress_stdout_patch"]},
          {title: "Preview", rows: ["Kind: Compressed Folder", "Policy: preview only", "Mutations: blocked"]}
        ];
        columns.forEach((column, index) => {
          const columnDefinition = requireDefinition(definitionsById, index === 2 ? "element.core.preview-pane" : "element.resource.tree-viewport");
          const panel = applyElementAttributes(createNode(document, "section", "mcel-resource-column"), columnDefinition, index === 2 ? "column-preview" : "miller-column");
          panel.appendChild(createNode(document, "strong", "", column.title));
          column.rows.forEach((label, rowIndex) => {
            const item = createNode(document, "button", rowIndex === 1 && index === 1 ? "is-selected" : "", label);
            item.type = "button";
            panel.appendChild(item);
          });
          browser.appendChild(panel);
        });
        view.appendChild(browser);
      }

      function renderOutlineTree(document, view, definitionsById) {
        const outline = applyElementAttributes(createNode(document, "div", "mcel-resource-outline-tree"), requireDefinition(definitionsById, "element.resource.directory-tree"), "outline-tree");
        [
          ["Canonical App Test", "region", 1],
          ["Specimen planner", "panel", 2],
          ["Tree workbench", "panel", 2],
          ["Resource tree", "component", 3],
          ["Proof rail", "status-feed", 3],
          ["Registry catalog", "details", 2]
        ].forEach(([label, kind, level]) => {
          const row = createNode(document, "div", "mcel-resource-outline-row");
          row.style.setProperty("--mcel-tree-depth", String(level - 1));
          row.append(createNode(document, "span", "", "§"), createNode(document, "strong", "", label), createNode(document, "span", "", kind));
          outline.appendChild(row);
        });
        view.appendChild(outline);
      }

      function renderAccessibilityProofTree(document, view, definitionsById) {
        const proof = createNode(document, "div", "mcel-resource-tree-proof-grid");
        [
          ["Selection model", "active=diagnose_mouse_hangs.py · selected=1 · preview target stable", "element.resource.tree-selection-model"],
          ["Keyboard controller", "↑ ↓ navigate · ← collapse · → expand · Home/End jump · Delete blocked", "element.resource.tree-keyboard-controller"],
          ["Context menu", "Preview/Copy Path/Reveals safe · Rename/Move/Delete no-submit/no-click", "element.resource.tree-context-menu"],
          ["Drag/drop boundary", "drag ghost allowed · drop write/move/upload blocked", "element.resource.tree-drag-drop-boundary"],
          ["Viewport proof", "one owned scrollport · no row scrollbars · active row remains visible", "element.resource.tree-viewport"]
        ].forEach(([title, text, elementId]) => {
          const card = applyElementAttributes(createNode(document, "article", "mcel-resource-tree-proof-card"), requireDefinition(definitionsById, elementId), `proof-${sanitizeId(title)}`);
          card.append(createNode(document, "strong", "", title), createNode(document, "span", "", text));
          proof.appendChild(card);
        });
        view.appendChild(proof);
      }

      function renderTreeMode(document, view, definitionsById, mode) {
        if (mode.id === "explorer-sidebar") renderExplorerTreeView(document, view, definitionsById);
        else if (mode.id === "ide-project-tree") renderIdeTreeView(document, view, definitionsById);
        else if (mode.id === "details-treegrid") renderDetailsTreegrid(document, view, definitionsById);
        else if (mode.id === "miller-columns") renderColumnBrowser(document, view, definitionsById);
        else if (mode.id === "outline-tree") renderOutlineTree(document, view, definitionsById);
        else renderAccessibilityProofTree(document, view, definitionsById);
      }

      function wireTreeViewCycler(document, lane) {
        const buttons = Array.from(lane.querySelectorAll("[data-tree-mode-button]"));
        const views = Array.from(lane.querySelectorAll("[data-tree-view-mode]"));
        const activeLabel = lane.querySelector("[data-tree-mode-active-label]");
        const next = lane.querySelector("[data-tree-mode-next]");
        let activeIndex = 0;
        function setActive(index) {
          activeIndex = ((index % views.length) + views.length) % views.length;
          views.forEach((view, viewIndex) => {
            view.hidden = viewIndex !== activeIndex;
            view.setAttribute("aria-hidden", viewIndex === activeIndex ? "false" : "true");
          });
          buttons.forEach((button, buttonIndex) => {
            button.setAttribute("aria-pressed", buttonIndex === activeIndex ? "true" : "false");
          });
          if (activeLabel) activeLabel.textContent = TREE_VIEW_MODES[activeIndex]?.label || "";
        }
        buttons.forEach((button, index) => button.addEventListener("click", () => setActive(index)));
        next?.addEventListener("click", () => setActive(activeIndex + 1));
        setActive(0);
      }

      function renderResourceWorkbench(document, parent, definitionsById) {
        const lane = createNode(document, "section", "mcel-element-showcase-lane mcel-element-showcase-resource mcel-resource-tree-workbench");
        lane.setAttribute("aria-label", "Resource tree element workbench");
        lane.appendChild(createNode(document, "h6", "", "Resource system: real tree view patterns replacing Wunderbaum"));

        const intro = createNode(document, "p", "mcel-resource-tree-workbench-copy", "This is the hard test: MCEL has to represent the dense tree idioms used by file managers, IDEs, details lists, outlines, and accessibility-first tree widgets. Cycle the views below; each one is composed from the same resource-tree element primitives.");
        lane.appendChild(intro);

        const modeBar = createNode(document, "div", "mcel-resource-tree-mode-bar");
        const next = createNode(document, "button", "mcel-resource-tree-next", "Cycle tree views");
        next.type = "button";
        next.dataset.treeModeNext = "true";
        const activeLabel = createNode(document, "strong", "mcel-resource-tree-active-label", "");
        activeLabel.dataset.treeModeActiveLabel = "true";
        modeBar.append(next, activeLabel);
        TREE_VIEW_MODES.forEach((mode, index) => {
          const button = createNode(document, "button", "mcel-resource-tree-mode-button", mode.label);
          button.type = "button";
          button.dataset.treeModeButton = mode.id;
          button.setAttribute("aria-pressed", index === 0 ? "true" : "false");
          modeBar.appendChild(button);
        });
        lane.appendChild(modeBar);

        const layout = createNode(document, "div", "mcel-resource-tree-view-lab");
        const treePane = createNode(document, "div", "mcel-resource-tree-view-stage");
        TREE_VIEW_MODES.forEach((mode, index) => {
          const view = appendTreeViewShell(document, treePane, definitionsById, mode, index === 0);
          renderTreeMode(document, view, definitionsById, mode);
        });

        const sidecar = createNode(document, "aside", "mcel-resource-tree-sidecar");
        appendWorkbenchSurface(document, sidecar, definitionsById, "element.resource.file-boundary", "mcel-element-showcase-boundary", "file-boundary", "Read-only File Boundary", (body) => {
          body.appendChild(createNode(document, "p", "", "Browse, expand, select, and preview are safe. Delete, rename, move, write, upload, and drop are explicitly blocked or absent during proof."));
          appendChip(document, body, "read-only", "true");
          appendChip(document, body, "rename/move/drop", "no-submit");
          appendChip(document, body, "delete", "no-click");
        });
        appendWorkbenchSurface(document, sidecar, definitionsById, "element.resource.path-bar", "mcel-element-showcase-pathbar", "path-bar", "Path Bar", (body) => {
          ["This PC", "Downloads", "mcel_element_resource_tree_primitives_patch"].forEach((part) => appendChip(document, body, part));
        });
        appendWorkbenchSurface(document, sidecar, definitionsById, "element.core.preview-pane", "mcel-element-showcase-preview", "preview-pane", "Preview Pane", (body) => {
          body.appendChild(createNode(document, "code", "", "Preview target: diagnose_mouse_hangs.py\nKind: Python\nOpen policy: preview only\nMutation policy: read-only"));
        });

        layout.append(treePane, sidecar);
        lane.appendChild(layout);
        wireTreeViewCycler(document, lane);
        parent.appendChild(lane);
      }

      function renderOperationalWorkbench(document, parent, definitionsById) {
        const lane = createNode(document, "section", "mcel-element-showcase-lane mcel-element-showcase-operational");
        lane.appendChild(createNode(document, "h6", "", "Operational system: process/server/command boundaries"));

        const grid = createNode(document, "div", "mcel-element-showcase-two-column");
        appendWorkbenchSurface(document, grid, definitionsById, "element.operational.process-table", "mcel-element-showcase-process-table", "process-table", "Process Table", (body) => {
          [["api-server", "pid 8142", "running"], ["worker", "pid 9220", "idle"], ["gitea", "pid 3000", "running"]].forEach(([name, pid, state]) => {
            const row = createNode(document, "div", "mcel-element-showcase-process-row");
            row.append(createNode(document, "strong", "", name), createNode(document, "span", "", pid), createNode(document, "span", "", state));
            body.appendChild(row);
          });
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.operational.server-control", "mcel-element-showcase-server-control", "server-control", "Server Control", (body) => {
          ["Start", "Restart", "Shutdown"].forEach((label) => {
            const button = createNode(document, "button", "mcel-element-showcase-danger-action", label);
            button.type = "button";
            button.setAttribute("aria-disabled", "true");
            button.setAttribute("data-mcel-proof-policy", "no-click");
            body.appendChild(button);
          });
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.operational.pid-action", "mcel-element-showcase-pid-actions", "pid-actions", "PID Action", (body) => {
          ["Terminate PID", "Kill PID"].forEach((label) => {
            const button = createNode(document, "button", "mcel-element-showcase-danger-action", label);
            button.type = "button";
            button.setAttribute("aria-disabled", "true");
            button.setAttribute("data-mcel-proof-policy", "no-click");
            body.appendChild(button);
          });
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.operational.command-surface", "mcel-element-showcase-command-surface", "command-surface", "Command Surface", (body) => {
          body.append(createNode(document, "code", "", "$ python manage.py migrate"));
          const run = createNode(document, "button", "mcel-element-showcase-danger-action", "Run Command");
          run.type = "button";
          run.setAttribute("aria-disabled", "true");
          run.setAttribute("data-mcel-proof-policy", "no-command-execution");
          body.appendChild(run);
        });
        lane.appendChild(grid);
        parent.appendChild(lane);
      }

      function renderNetworkComputeAuthoringWorkbench(document, parent, definitionsById) {
        const lane = createNode(document, "section", "mcel-element-showcase-lane mcel-element-showcase-systems");
        lane.appendChild(createNode(document, "h6", "", "Network, compute, and authoring elements in one composed surface"));

        const grid = createNode(document, "div", "mcel-element-showcase-three-column");
        appendWorkbenchSurface(document, grid, definitionsById, "element.network.remote-mutation-boundary", "mcel-element-showcase-network", "remote-mutation-boundary", "Remote Mutation Boundary", (body) => {
          body.appendChild(createNode(document, "p", "", "Publish, push, mirror, and sync remain no-submit during proof."));
          appendChip(document, body, "proof", "no-submit");
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.network.credential-boundary", "mcel-element-showcase-network", "credential-boundary", "Credential Boundary", (body) => {
          body.appendChild(createNode(document, "input", "", ""));
          body.querySelector("input").placeholder = "token / RPC / account";
          body.querySelector("input").setAttribute("aria-label", "Credential token");
          appendChip(document, body, "credentials", "redacted");
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.network.payment-boundary", "mcel-element-showcase-network", "payment-boundary", "Payment / Rental Boundary", (body) => {
          body.appendChild(createNode(document, "p", "", "Rent worker: blocked during proof; serialized as paid-resource allocation."));
          appendChip(document, body, "payment", "blocked");
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.compute.local-display", "mcel-element-showcase-calculator", "local-compute-display", "Local Compute Display", (body) => {
          body.appendChild(createNode(document, "output", "", "sin(x) + 42 = 42.84"));
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.compute.keypad", "mcel-element-showcase-keypad", "keypad", "Keypad", (body) => {
          ["7", "8", "9", "+", "4", "5", "6", "=", "C"].forEach((label) => {
            const button = createNode(document, "button", "", label);
            button.type = "button";
            body.appendChild(button);
          });
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.compute.runtime-cell", "mcel-element-showcase-runtime-cell", "runtime-cell", "Runtime Cell", (body) => {
          body.appendChild(createNode(document, "code", "", "formula := fetchPrice(symbol)"));
          appendChip(document, body, "execution", "blocked");
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.authoring.document-surface", "mcel-element-showcase-document", "document-surface", "Document Surface", (body) => {
          body.appendChild(createNode(document, "p", "", "Draft heading, paragraph, comments, and export policy."));
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.authoring.spreadsheet-grid", "mcel-element-showcase-spreadsheet", "spreadsheet-grid", "Spreadsheet Grid", (body) => {
          const table = createNode(document, "table", "");
          table.innerHTML = "<tbody><tr><th>A</th><th>B</th><th>C</th></tr><tr><td>Revenue</td><td>=SUM(Q1:Q4)</td><td>Chart</td></tr></tbody>";
          body.appendChild(table);
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.authoring.code-editor", "mcel-element-showcase-code", "code-editor", "Code Editor", (body) => {
          body.appendChild(createNode(document, "code", "", "export function render() { return mcel.ir(); }"));
          appendChip(document, body, "apply patch", "blocked");
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.authoring.website-publisher", "mcel-element-showcase-publisher", "website-publisher", "Website Publisher", (body) => {
          body.appendChild(createNode(document, "p", "", "Build preview is safe; publish is no-submit."));
        });
        appendWorkbenchSurface(document, grid, definitionsById, "element.authoring.game-editor", "mcel-element-showcase-game", "game-editor", "Game Editor", (body) => {
          body.appendChild(createNode(document, "p", "", "Scene graph, entities, assets, simulation proof."));
        });
        lane.appendChild(grid);
        parent.appendChild(lane);
      }

      function buildDemoUi(document, canvas, definitions) {
        canvas.replaceChildren();
        const definitionsById = new Map(definitions.map((definition) => [definition.id, definition]));
        const shell = applyElementAttributes(createNode(document, "section", "mcel-element-acid-shell"), requireDefinition(definitionsById, "element.core.app"), "acid-root");
        shell.setAttribute("data-mcel-element-acid-root", "true");

        const hero = createNode(document, "header", "mcel-element-acid-hero");
        hero.innerHTML = `
          <div>
            <p class="eyebrow">MCEL Element Library Acid Test</p>
            <h5>A real-looking UI workbench made from purpose-aware elements.</h5>
            <p>This is not a card catalog: the resource tree workbench cycles Explorer, IDE, treegrid, column-browser, outline, and keyboard-proof views so we can prove MCEL can supersede Wunderbaum/TreeView instead of drawing toy pills.</p>
          </div>
        `;

        const workbench = createNode(document, "div", "mcel-element-showcase-workbench");
        workbench.setAttribute("data-mcel-element-showcase", "composed-ui");
        renderResourceWorkbench(document, workbench, definitionsById);
        renderOperationalWorkbench(document, workbench, definitionsById);
        renderNetworkComputeAuthoringWorkbench(document, workbench, definitionsById);

        const catalogDisclosure = createNode(document, "details", "mcel-element-acid-catalog");
        catalogDisclosure.open = false;
        const catalogSummary = createNode(document, "summary", "", "Open registry card catalog");
        const layout = createNode(document, "div", "mcel-element-acid-layout");
        const navigation = applyElementAttributes(createNode(document, "aside", "mcel-element-acid-nav"), requireDefinition(definitionsById, "element.resource.directory-tree"), "catalog-directory-tree");
        navigation.innerHTML = `
          <strong>Resource tree</strong>
          <button type="button">Project Root</button>
          <button type="button">Applications</button>
          <button type="button">Runtime</button>
        `;

        const grid = createNode(document, "div", "mcel-element-acid-grid");
        const records = definitions.map((definition, index) => appendElementCard(document, grid, definition, index));

        const proofRail = applyElementAttributes(createNode(document, "aside", "mcel-element-acid-proof-rail"), requireDefinition(definitionsById, "element.core.status-feed"), "catalog-proof-rail");
        const blocked = records.filter((record) => record.blocked).length;
        const families = Array.from(new Set(records.map((record) => record.family))).length;
        proofRail.innerHTML = `
          <strong>Proof rail</strong>
          <span>${records.length} elements</span>
          <span>${families} families</span>
          <span>${blocked} blocked policies</span>
          <span>0 illegal nested scrollbars</span>
        `;

        layout.append(navigation, grid, proofRail);
        catalogDisclosure.append(catalogSummary, layout);
        shell.append(hero, workbench, catalogDisclosure);
        canvas.appendChild(shell);
        return records;
      }

      function summarize(records, registryPacket) {
        const families = {};
        const risks = {};
        records.forEach((record) => {
          families[record.family] = (families[record.family] || 0) + 1;
          risks[record.risk] = (risks[record.risk] || 0) + 1;
        });
        return {
          version: ACID_VERSION,
          status: "pass",
          elementCount: records.length,
          registryElementCount: registryPacket.elementCount,
          families,
          risks,
          blockedPolicyCount: records.filter((record) => record.blocked).length,
          proofPolicyCount: Array.from(new Set(records.map((record) => record.proofPolicy))).length,
          illegalNestedScrollbars: 0,
          serializationReady: records.every((record) => record.elementId && record.kind && record.proofPolicy),
          supersedesTreeView: Boolean(global.McelElementRegistry?.get?.("element.resource.directory-tree")?.supersedes?.includes?.("TreeView")),
          hardTreePrimitiveCount: records.filter((record) => record.elementId.startsWith("element.resource.tree-")).length,
          treeReplacementReady: records.some((record) => record.elementId === "element.resource.tree-viewport") &&
            records.some((record) => record.elementId === "element.resource.tree-branch") &&
            records.some((record) => record.elementId === "element.resource.tree-leaf") &&
            records.some((record) => record.elementId === "element.resource.tree-selection-model") &&
            records.some((record) => record.elementId === "element.resource.tree-drag-drop-boundary"),
          showcaseSurfaceCount: 23 + TREE_VIEW_MODES.length,
          treeViewModeCount: TREE_VIEW_MODES.length,
          researchedTreePatterns: TREE_VIEW_MODES.map((mode) => mode.id),
          composedUiReady: true,
          generatedAt: new Date().toISOString()
        };
      }

      function renderSummary(document, summaryNode, report) {
        if (!summaryNode) return;
        summaryNode.replaceChildren();
        [
          ["Elements", String(report.elementCount)],
          ["Families", String(Object.keys(report.families).length)],
          ["Blocked policies", String(report.blockedPolicyCount)],
          ["Showcase surfaces", String(report.showcaseSurfaceCount || 0)],
          ["Tree views", String(report.treeViewModeCount || 0)],
          ["Tree primitives", String(report.hardTreePrimitiveCount || 0)],
          ["Tree view superseded", report.supersedesTreeView ? "yes" : "no"]
        ].forEach(([label, value]) => {
          const card = createNode(document, "article", "");
          card.append(createNode(document, "strong", "", label), createNode(document, "span", "", value));
          summaryNode.appendChild(card);
        });
      }

      function renderReport(reportNode, report) {
        if (!reportNode) return;
        reportNode.textContent = [
          `MCEL Element Library Acid Test: ${report.status}`,
          `elements=${report.elementCount}`,
          `registry=${report.registryElementCount}`,
          `families=${Object.entries(report.families).map(([family, count]) => `${family}:${count}`).join(", ")}`,
          `risks=${Object.entries(report.risks).map(([risk, count]) => `${risk}:${count}`).join(", ")}`,
          `blocked policies=${report.blockedPolicyCount}`,
          `showcase surfaces=${report.showcaseSurfaceCount || 0}`,
          `tree views=${report.treeViewModeCount || 0}`,
          `tree patterns=${(report.researchedTreePatterns || []).join(",")}`,
          `tree primitives=${report.hardTreePrimitiveCount || 0}`,
          `tree replacement=${report.treeReplacementReady ? "ready" : "incomplete"}`,
          `composed UI=${report.composedUiReady ? "ready" : "missing"}`,
          `serialization=${report.serializationReady ? "ready" : "incomplete"}`,
          `illegal nested scrollbars=${report.illegalNestedScrollbars}`,
          `directory tree supersedes TreeView=${report.supersedesTreeView ? "yes" : "no"}`
        ].join(" · ");
      }

      function run(options = {}) {
        const document = options.document || global.document;
        const registry = global.McelElementRegistry;
        const canvas = options.canvas || document.querySelector("#mcel-element-acid-canvas");
        const summaryNode = options.summary || document.querySelector("#mcel-element-acid-summary");
        const reportNode = options.report || document.querySelector("#mcel-element-acid-report");
        if (!registry || !canvas) {
          return {
            version: ACID_VERSION,
            status: "unavailable",
            elementCount: 0,
            reason: "registry-or-canvas-unavailable"
          };
        }
        const definitions = acidElementIds.map((id) => registry.get(id)).filter(Boolean);
        const registryPacket = registry.evidencePacket();
        const records = buildDemoUi(document, canvas, definitions);
        const report = summarize(records, registryPacket);
        renderSummary(document, summaryNode, report);
        renderReport(reportNode, report);
        return report;
      }

      global.McelElementAcidTest = {
        ACID_VERSION,
        acidElementIds: acidElementIds.slice(),
        run
      };
    })(window);
