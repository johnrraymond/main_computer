    (function (global) {
      "use strict";

      const registry = global.McelElementRegistry;
      if (!registry) return;

      const inspectOnly = {risk: "none", proofPolicy: "inspect-only", blocked: false};
      const safe = {risk: "safe", proofPolicy: "inspect-only", blocked: false};
      const analysis = {risk: "analysis", proofPolicy: "inspect-only", blocked: false};
      const noClick = {risk: "destructive", proofPolicy: "no-click", blocked: true};
      const noSubmit = {risk: "remote-mutation", proofPolicy: "no-submit", blocked: true};
      const noCommand = {risk: "command-execution", proofPolicy: "no-command-execution", blocked: true};

      function def(id, label, family, kind, purpose, extra = {}) {
        return {
          id,
          label,
          family,
          kind,
          purpose,
          allowedChildren: extra.allowedChildren || [],
          layoutLaws: extra.layoutLaws || ["fit:content", "no-illegal-nested-scrollbars"],
          fitPolicy: extra.fitPolicy || "content-fit",
          scrollPolicy: extra.scrollPolicy || "no-owned-scroll",
          actionPolicy: extra.actionPolicy || {},
          riskPolicy: extra.riskPolicy || inspectOnly,
          proofPolicy: extra.proofPolicy || extra.riskPolicy?.proofPolicy || "inspect-only",
          serializationSchema: extra.serializationSchema || {
            required: ["id", "elementId", "purpose"],
            optional: ["label", "children", "layoutLaws", "riskPolicy", "proofPolicy"]
          },
          renderer: extra.renderer || {
            htmlTag: extra.htmlTag || "mcel-element",
            cssObject: extra.cssObject || id.replace(/^element\./, "mcel-").replace(/\./g, "-")
          },
          decoderHints: extra.decoderHints || [],
          supersedes: extra.supersedes || [],
          stateModel: extra.stateModel || {},
          interactionModel: extra.interactionModel || {},
          accessibility: extra.accessibility || {},
          dataModel: extra.dataModel || {},
          migrationHints: extra.migrationHints || {},
          proofFixtures: extra.proofFixtures || [],
          examples: extra.examples || []
        };
      }

      const definitions = [
        def("element.core.app", "App Surface", "core", "root", "Top-level semantic runtime surface.", {
          allowedChildren: ["element.core.region", "element.core.workflow"],
          layoutLaws: ["single-root", "viewport-fit", "no-illegal-nested-scrollbars"],
          htmlTag: "mcel-app",
          decoderHints: ["app", "root", "workspace", "shell"],
          supersedes: ["div#app", "main.app-shell"]
        }),
        def("element.core.region", "Region", "core", "region", "Major named app region or landmark.", {
          allowedChildren: ["element.core.panel", "element.core.toolbar", "element.core.status-feed"],
          htmlTag: "mcel-region",
          decoderHints: ["region", "section", "workspace", "aside", "main"]
        }),
        def("element.core.panel", "Panel", "core", "panel", "Bounded content panel with self-contained purpose.", {
          allowedChildren: ["element.core.field", "element.core.action", "element.core.status-feed"],
          htmlTag: "mcel-panel",
          decoderHints: ["panel", "card", "pane", "form", "surface"]
        }),
        def("element.core.toolbar", "Toolbar", "core", "toolbar", "Cluster of related controls.", {
          allowedChildren: ["element.core.action"],
          htmlTag: "mcel-toolbar",
          riskPolicy: safe,
          decoderHints: ["toolbar", "controls", "actions", "button row"]
        }),
        def("element.core.field", "Field", "core", "field", "Input, label, select, or data-entry surface.", {
          htmlTag: "mcel-field",
          riskPolicy: safe,
          decoderHints: ["input", "select", "textarea", "label", "field"]
        }),
        def("element.core.action", "Action", "core", "action", "User action with explicit execution policy.", {
          htmlTag: "mcel-action",
          riskPolicy: safe,
          actionPolicy: {default: "inspect-only", click: "allowed-if-safe"},
          decoderHints: ["button", "link", "submit", "action"]
        }),
        def("element.core.status-feed", "Status Feed", "core", "status-feed", "Output, log, report, or progress feed.", {
          htmlTag: "mcel-status-feed",
          scrollPolicy: "bounded-read-only-scroll",
          decoderHints: ["status", "output", "log", "report", "activity", "progress"]
        }),
        def("element.core.workflow", "Workflow", "core", "workflow", "Ordered process, wizard, or operational flow.", {
          allowedChildren: ["element.core.panel", "element.core.action", "element.core.status-feed"],
          htmlTag: "mcel-workflow",
          riskPolicy: analysis,
          decoderHints: ["workflow", "wizard", "steps", "operation", "queue"]
        }),
        def("element.core.collection", "Collection", "core", "collection", "List/grid/tree of repeated semantic rows.", {
          allowedChildren: ["element.core.collection-row"],
          htmlTag: "mcel-collection",
          scrollPolicy: "owned-vertical-scroll",
          decoderHints: ["list", "grid", "table", "collection", "rows"]
        }),
        def("element.core.collection-row", "Collection Row", "core", "collection-row", "One repeated item with stable identity and selection policy.", {
          htmlTag: "mcel-row",
          riskPolicy: safe,
          decoderHints: ["row", "item", "entry", "record"]
        }),
        def("element.core.preview-pane", "Preview Pane", "core", "preview", "Read-only preview surface for a selected resource.", {
          htmlTag: "mcel-preview",
          scrollPolicy: "owned-preview-scroll",
          decoderHints: ["preview", "details", "selected", "inspector"]
        }),

        def("element.resource.file-boundary", "Read-only File Boundary", "resource", "resource-boundary", "Filesystem boundary that separates browsing from mutation.", {
          riskPolicy: {risk: "filesystem-read-boundary", proofPolicy: "inspect-only", blocked: false},
          decoderHints: ["file", "folder", "path", "root", "directory"],
          supersedes: ["wunderbaum-tree", "plain file list"]
        }),
        def("element.resource.directory-tree", "Directory Tree", "resource", "tree", "Hierarchical resource browser with path, expansion, selection, preview coupling, keyboard navigation, and mutation boundaries.", {
          allowedChildren: [
            "element.resource.tree-viewport",
            "element.resource.tree-branch",
            "element.resource.tree-leaf",
            "element.resource.tree-selection-model",
            "element.resource.tree-keyboard-controller",
            "element.resource.tree-context-menu",
            "element.resource.tree-drag-drop-boundary"
          ],
          scrollPolicy: "owns-tree-viewport-scroll",
          layoutLaws: ["tree-owns-one-scrollport", "active-row-remains-visible", "indentation-preserves-readable-labels", "no-illegal-nested-scrollbars"],
          actionPolicy: {
            select: "inspect-only",
            preview: "inspect-only",
            expand: "read-boundary",
            collapse: "inspect-only",
            keyboardNavigate: "inspect-only",
            drag: "no-submit",
            drop: "no-submit",
            delete: "no-click",
            rename: "no-submit",
            move: "no-submit"
          },
          stateModel: {
            required: ["activeNodeId", "expandedNodeIds", "selectedNodeIds"],
            optional: ["focusedNodeId", "visibleWindow", "rootId", "path"],
            selection: "single-or-multi-explicit",
            expansion: "stable-id-set"
          },
          interactionModel: {
            mouse: ["select-preview", "expand-collapse", "open-directory"],
            keyboard: ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "Enter"],
            blocked: ["delete", "move", "rename", "drop-write"]
          },
          accessibility: {
            role: "tree",
            rowRole: "treeitem",
            requiredAttributes: ["aria-expanded", "aria-selected", "aria-level", "aria-setsize", "aria-posinset"]
          },
          dataModel: {
            key: "stable resource path or synthetic root id",
            nodeTypes: ["root", "folder", "file", "empty", "virtual-placeholder"],
            rowPayload: ["name", "kind", "relativePath", "pathDisplay", "bytes", "mtime", "category"]
          },
          migrationHints: {
            fromWunderbaum: ["node.key -> stable resource id", "node.type -> branch/leaf kind", "node.data.fileExplorerEntry -> resource payload", "activate/select/click -> preview", "dblclick/Enter on directory -> safe navigation"],
            replace: ["Wunderbaum constructor", "wb-node-list", "wb-list-container", "file-explorer-wunderbaum-host"]
          },
          proofFixtures: ["selecting a file previews metadata only", "expanding a folder does not write", "delete/move/rename/drop are absent or blocked"],
          presentationModes: ["explorer-sidebar", "ide-project-tree", "details-treegrid", "miller-columns", "outline-tree", "accessibility-proof"],
          viewPatterns: ["dense navigation tree", "project/file tree with decorations", "hierarchical details table", "column browser", "semantic outline", "keyboard/selection proof"],
          densityModes: ["compact", "comfortable", "touch"],
          decoderHints: ["tree", "directory", "roots", "folder", "file", "wunderbaum", "wb-node-list", "wb-list-container", "file-explorer-wunderbaum-host"],
          supersedes: ["Wunderbaum", "TreeView", "ul/li directory list", "file-explorer-wunderbaum-host", "wb-node-list"]
        }),
        def("element.resource.tree-viewport", "Tree Viewport", "resource", "tree-viewport", "The single owned scrollport for visible tree rows, including virtualization/windowing metadata.", {
          allowedChildren: ["element.resource.tree-branch", "element.resource.tree-leaf", "element.resource.tree-empty-state"],
          scrollPolicy: "owns-tree-viewport-scroll",
          layoutLaws: ["single-tree-scrollport", "no-nested-row-scroll", "virtual-window-has-stable-row-height"],
          stateModel: {required: ["visibleStart", "visibleEnd", "rowHeight"], optional: ["overscan", "totalRows"]},
          interactionModel: {scroll: "owned", resize: "recompute-visible-window"},
          accessibility: {role: "tree", managesActivedescendant: true},
          decoderHints: ["viewport", "scrollport", "wb-list-container", "wb-node-list", "virtual rows", "file list"],
          supersedes: ["wb-list-container", "wb-node-list", "virtual tree viewport"]
        }),
        def("element.resource.tree-branch", "Tree Branch", "resource", "tree-branch", "Expandable folder/root node with stable identity, child loading state, and preview/navigation behavior.", {
          allowedChildren: ["element.resource.tree-expander", "element.resource.tree-selection-model", "element.resource.resource-row"],
          riskPolicy: {risk: "filesystem-read-boundary", proofPolicy: "inspect-only", blocked: false},
          actionPolicy: {select: "inspect-only", preview: "inspect-only", expand: "read-boundary", collapse: "inspect-only", openDirectory: "read-boundary"},
          stateModel: {required: ["nodeId", "path", "expanded"], optional: ["loading", "childCount", "isRoot"]},
          interactionModel: {click: "select-preview", dblclick: "open-directory-read-only", Enter: "open-directory-read-only", ArrowRight: "expand", ArrowLeft: "collapse"},
          accessibility: {role: "treeitem", requiredAttributes: ["aria-expanded", "aria-level", "aria-selected"]},
          dataModel: {kind: "directory", identity: "rootId + relativePath"},
          decoderHints: ["folder", "directory", "root", "tree-directory", "file-explorer-tree-directory", "expanded", "collapsed"],
          supersedes: ["Wunderbaum directory node", "TreeView branch"]
        }),
        def("element.resource.tree-leaf", "Tree Leaf", "resource", "tree-leaf", "Selectable file/resource node that previews content or metadata without mutation.", {
          allowedChildren: ["element.resource.resource-row"],
          riskPolicy: safe,
          actionPolicy: {select: "inspect-only", preview: "inspect-only", openWith: "inspect-only"},
          stateModel: {required: ["nodeId", "path", "selected"], optional: ["category", "suggestedApp", "bytes", "mtime"]},
          interactionModel: {click: "select-preview", dblclick: "preview-or-route-if-safe", Enter: "preview-or-route-if-safe"},
          accessibility: {role: "treeitem", requiredAttributes: ["aria-level", "aria-selected"]},
          dataModel: {kind: "file", identity: "rootId + relativePath"},
          decoderHints: ["file", "resource", "tree-file", "file-explorer-tree-file", "document row"],
          supersedes: ["Wunderbaum file node", "TreeView leaf"]
        }),
        def("element.resource.tree-expander", "Tree Expander", "resource", "tree-expander", "Disclosure affordance that changes expansion state without selecting, previewing, or mutating resources.", {
          riskPolicy: safe,
          actionPolicy: {expand: "inspect-only", collapse: "inspect-only"},
          stateModel: {required: ["controlsNodeId", "expanded"]},
          interactionModel: {click: "toggle-expansion-only", Space: "toggle-expansion-only"},
          accessibility: {role: "button", requiredAttributes: ["aria-controls", "aria-expanded", "aria-label"]},
          decoderHints: ["twisty", "chevron", "caret", "expander", "disclosure", "wb-expander"],
          supersedes: ["Wunderbaum expander", "TreeView disclosure"]
        }),
        def("element.resource.tree-selection-model", "Tree Selection Model", "resource", "tree-selection", "Selection/focus controller that separates active row, selected resource, preview target, and multi-select state.", {
          riskPolicy: safe,
          actionPolicy: {focus: "inspect-only", select: "inspect-only", multiSelect: "explicit-only", preview: "inspect-only"},
          stateModel: {required: ["activeNodeId", "selectedNodeIds"], optional: ["previewNodeId", "anchorNodeId", "selectionMode"]},
          interactionModel: {click: "select", CtrlClick: "multi-select-if-enabled", ShiftClick: "range-select-if-enabled"},
          accessibility: {usesActivedescendant: true, selectedState: "aria-selected"},
          decoderHints: ["selected", "active", "focused", "selectMode", "activate", "selection"],
          supersedes: ["Wunderbaum select mode", "TreeView selection plugin"]
        }),
        def("element.resource.tree-keyboard-controller", "Tree Keyboard Controller", "resource", "tree-keyboard", "Keyboard navigation contract for tree focus, expansion, selection, preview, and safe directory opening.", {
          riskPolicy: safe,
          actionPolicy: {ArrowUp: "inspect-only", ArrowDown: "inspect-only", ArrowLeft: "collapse-or-parent", ArrowRight: "expand-or-child", Enter: "preview-or-open-directory-read-only", Delete: "blocked"},
          stateModel: {required: ["focusedNodeId"], optional: ["visibleWindow"]},
          interactionModel: {keys: ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "Enter"], blockedKeys: ["Delete", "Backspace-for-delete"]},
          accessibility: {requiresRovingFocus: true},
          decoderHints: ["keydown", "ArrowUp", "ArrowDown", "Enter", "Home", "End", "tree keyboard"],
          supersedes: ["Wunderbaum keyboard plugin", "TreeView keyboard handler"]
        }),
        def("element.resource.tree-context-menu", "Tree Context Menu", "resource", "tree-context-menu", "Resource action menu that separates safe preview/navigation from destructive filesystem mutation.", {
          riskPolicy: analysis,
          proofPolicy: "inspect-only",
          actionPolicy: {preview: "inspect-only", reveal: "inspect-only", copyPath: "inspect-only", rename: "no-submit", move: "no-submit", delete: "no-click"},
          stateModel: {required: ["targetNodeId", "availableActions"], optional: ["open"]},
          interactionModel: {contextmenu: "inspect-menu-only", destructiveActions: "disabled-during-proof"},
          accessibility: {role: "menu", itemRole: "menuitem"},
          decoderHints: ["context menu", "right click", "rename", "delete", "move", "copy path", "open with"],
          supersedes: ["Wunderbaum menu extension", "TreeView context menu"]
        }),
        def("element.resource.tree-drag-drop-boundary", "Tree Drag/Drop Boundary", "resource", "tree-drag-drop-boundary", "Drag/drop contract that makes filesystem move/copy/write mutations explicit and proof-blocked.", {
          riskPolicy: {risk: "filesystem-mutation", proofPolicy: "no-submit", blocked: true},
          proofPolicy: "no-submit",
          actionPolicy: {dragPreview: "inspect-only", dropMove: "no-submit", dropCopy: "no-submit", externalDrop: "no-submit"},
          stateModel: {required: ["dragSourceId", "dropTargetId", "dropEffect"], optional: ["isExternalPayload"]},
          interactionModel: {dragstart: "inspect-only", dragover: "inspect-only", drop: "no-submit"},
          accessibility: {announcesDropEffect: true},
          decoderHints: ["drag", "drop", "move", "copy", "upload", "filesystem mutation"],
          supersedes: ["Wunderbaum drag/drop extension", "TreeView DnD plugin"]
        }),
        def("element.resource.tree-empty-state", "Tree Empty State", "resource", "tree-empty-state", "Inert placeholder row for empty folders, failed loads, or filtered search with no matches.", {
          riskPolicy: inspectOnly,
          actionPolicy: {retry: "inspect-only", clearFilter: "inspect-only"},
          stateModel: {required: ["reason"], optional: ["query", "rootId", "path"]},
          interactionModel: {click: "none"},
          accessibility: {role: "status"},
          decoderHints: ["empty", "no files found", "no matches", "fallback", "loading failed"],
          supersedes: ["Wunderbaum empty node", "TreeView empty row"]
        }),
        def("element.resource.path-bar", "Path Bar", "resource", "path", "Current resource path and read-only navigation boundary.", {
          riskPolicy: safe,
          decoderHints: ["path", "breadcrumb", "cwd", "location"]
        }),
        def("element.resource.resource-row", "Resource Row", "resource", "resource-row", "File, folder, asset, or document row with safe selection and preview.", {
          riskPolicy: safe,
          decoderHints: ["file", "folder", "asset", "document", "row"]
        }),

        def("element.operational.process-table", "Process Table", "operational", "table", "Runtime process table with repeated process rows.", {
          scrollPolicy: "owned-table-scroll",
          riskPolicy: analysis,
          decoderHints: ["process", "pid", "memory", "cpu", "runtime"],
          supersedes: ["table.processes"]
        }),
        def("element.operational.server-control", "Server Control", "operational", "action-family", "Start, stop, restart, and shutdown server lifecycle controls.", {
          riskPolicy: {risk: "server-control", proofPolicy: "no-click", blocked: true},
          actionPolicy: {start: "no-click", stop: "no-click", restart: "no-click", shutdown: "no-click"},
          decoderHints: ["server", "start", "stop", "restart", "shutdown"]
        }),
        def("element.operational.pid-action", "PID Action", "operational", "action-family", "Process termination controls that must never execute during proof.", {
          riskPolicy: {risk: "process-destructive", proofPolicy: "no-click", blocked: true},
          actionPolicy: {kill: "no-click", terminate: "no-click"},
          decoderHints: ["pid", "kill", "terminate", "process"]
        }),
        def("element.operational.command-surface", "Command Surface", "operational", "console", "Command entry/output surface with no-execution proof policy.", {
          riskPolicy: noCommand,
          proofPolicy: "no-command-execution",
          actionPolicy: {run: "no-command-execution", submit: "no-command-execution"},
          decoderHints: ["terminal", "command", "shell", "run", "execute"],
          supersedes: ["xterm", "terminal widget", "textarea command"]
        }),

        def("element.network.remote-mutation-boundary", "Remote Mutation Boundary", "network", "boundary", "Push, publish, mirror, sync, or remote write boundary.", {
          riskPolicy: noSubmit,
          actionPolicy: {publish: "no-submit", push: "no-submit", mirror: "no-submit", sync: "no-submit"},
          decoderHints: ["remote", "push", "publish", "mirror", "deploy", "sync"]
        }),
        def("element.network.credential-boundary", "Credential Boundary", "network", "boundary", "Credential, token, account, RPC, wallet, or provider surface.", {
          riskPolicy: {risk: "credential-network-mutation", proofPolicy: "no-submit", blocked: true},
          actionPolicy: {save: "no-submit", connect: "no-submit", sign: "no-submit"},
          decoderHints: ["credential", "token", "wallet", "provider", "rpc", "account"]
        }),
        def("element.network.payment-boundary", "Payment / Rental Boundary", "network", "boundary", "Paid resource allocation, rental, registration, or transaction surface.", {
          riskPolicy: {risk: "payment-rental", proofPolicy: "no-submit", blocked: true},
          actionPolicy: {rent: "no-submit", register: "no-submit", pay: "no-submit", sign: "no-submit"},
          decoderHints: ["payment", "rent", "register", "price", "marketplace", "wallet"]
        }),
        def("element.network.message-thread", "Message Thread", "network", "collection", "Email/chat/message thread with send/delete/sync policy boundaries.", {
          riskPolicy: analysis,
          actionPolicy: {read: "inspect-only", send: "no-submit", delete: "no-click", sync: "no-submit"},
          decoderHints: ["email", "chat", "thread", "message", "compose", "send"]
        }),

        def("element.compute.local-display", "Local Compute Display", "compute", "display", "Calculator, graph, answer, or local result display.", {
          riskPolicy: analysis,
          decoderHints: ["display", "answer", "result", "graph", "calculator"]
        }),
        def("element.compute.keypad", "Keypad", "compute", "keypad", "Digit/operator/local compute controls.", {
          riskPolicy: safe,
          actionPolicy: {digit: "inspect-only", operator: "inspect-only", clear: "inspect-only", evaluate: "inspect-only"},
          decoderHints: ["keypad", "digit", "operator", "evaluate", "clear"]
        }),
        def("element.compute.graph-surface", "Graph Surface", "compute", "graph", "Canvas, chart, WebGL, or visual graph surface with explicit animation ownership.", {
          riskPolicy: analysis,
          scrollPolicy: "canvas-owns-resize-not-scroll",
          decoderHints: ["graph", "chart", "canvas", "webgl", "scene"]
        }),
        def("element.compute.runtime-cell", "Runtime Cell", "compute", "runtime-cell", "Code/formula/runtime cell with explicit execution boundary.", {
          riskPolicy: noCommand,
          actionPolicy: {run: "no-command-execution", evaluate: "no-command-execution"},
          decoderHints: ["runtime", "cell", "formula", "code", "execute"]
        }),

        def("element.authoring.document-surface", "Document Surface", "authoring", "document", "Rich document editor surface with save/export policy.", {
          riskPolicy: analysis,
          actionPolicy: {edit: "inspect-only", save: "no-submit", export: "no-submit"},
          decoderHints: ["document", "page", "editor", "paragraph", "export"]
        }),
        def("element.authoring.spreadsheet-grid", "Spreadsheet Grid", "authoring", "grid", "Workbook grid with cell/formula/chart/import-export semantics.", {
          riskPolicy: analysis,
          scrollPolicy: "grid-owns-orthogonal-scroll",
          actionPolicy: {edit: "inspect-only", import: "no-submit", export: "no-submit", formulaRuntime: "no-command-execution"},
          decoderHints: ["spreadsheet", "grid", "cell", "formula", "workbook", "xlsx"]
        }),
        def("element.authoring.code-editor", "Code Editor", "authoring", "code-editor", "Project code editor with patch/run/apply execution boundaries.", {
          riskPolicy: noCommand,
          actionPolicy: {edit: "inspect-only", applyPatch: "no-submit", run: "no-command-execution"},
          decoderHints: ["code", "editor", "patch", "aider", "run", "terminal"]
        }),
        def("element.authoring.website-publisher", "Website Publisher", "authoring", "publish-workflow", "Site manifest/build/publish lane with local/remote deployment boundaries.", {
          riskPolicy: noSubmit,
          actionPolicy: {build: "inspect-only", publish: "no-submit", deploy: "no-submit"},
          decoderHints: ["website", "builder", "publish", "deploy", "manifest"]
        }),
        def("element.authoring.game-editor", "Game Editor", "authoring", "scene-editor", "Scene/entity/asset editor with save/export/runtime boundaries.", {
          riskPolicy: analysis,
          actionPolicy: {preview: "inspect-only", save: "no-submit", export: "no-submit"},
          decoderHints: ["game", "scene", "entity", "asset", "editor"]
        })
      ];

      registry.registerMany(definitions);

      global.McelElementsCore = {
        ELEMENTS_CORE_VERSION: "0.1.0",
        definitions
      };
    })(window);
