    (function (global) {
      "use strict";

      const TOOLKIT_VERSION = "0.1.0";

      const LAYERS = [
        {id: "foundation", label: "Foundation", purpose: "Shared density, focus, hit target, state, motion, and contrast promises."},
        {id: "control", label: "Controls", purpose: "Small interactive controls with state machines instead of one-off glyphs."},
        {id: "cell", label: "Data cells", purpose: "Typed value renderers used by lists, tables, trees, inspectors, and cards."},
        {id: "collection", label: "Collections", purpose: "Views for many records, each declaring capabilities before it can render a contract."},
        {id: "layout", label: "Layout shells", purpose: "Toolbars, panes, splitters, previews, inspectors, and command surfaces."},
        {id: "controller", label: "Controllers", purpose: "MVC transition owners for selection, expansion, sizing, sorting, filtering, grouping, and safety gates."},
        {id: "contract", label: "Patterns", purpose: "Reusable user-job contracts that map needs to eligible visualization recipes."}
      ];

      const PRIMITIVES = [
        {id: "foundation.density-scale", elementId: "element.toolkit.foundation-token", layer: "foundation", label: "Density scale", contract: "Every collection declares compact, normal, and spacious geometry before rows are drawn.", states: ["compact", "normal", "spacious", "touch"], supports: ["developer-tool-density", "touch-target-floor", "row-height-contract"]},
        {id: "foundation.focus-ring", elementId: "element.toolkit.foundation-token", layer: "foundation", label: "Focus ring", contract: "Keyboard focus must be visible, non-overlapping, and not confused with selection.", states: ["idle", "focus-visible", "focus-within", "invalid-focus"], supports: ["keyboard-proof", "roving-tabindex", "a11y-state"]},
        {id: "foundation.hit-target", elementId: "element.toolkit.foundation-token", layer: "foundation", label: "Hit target", contract: "Interactive affordances keep a minimum target without polluting dense text columns.", states: ["mouse", "touch", "pen", "keyboard"], supports: ["minimum-target", "dense-layout", "no-tiny-debug-glyphs"]},
        {id: "foundation.state-color", elementId: "element.toolkit.foundation-token", layer: "foundation", label: "State color", contract: "Selected, focused, blocked, warning, danger, success, and muted states use shared tokens.", states: ["selected", "focused", "blocked", "warning", "danger", "success", "muted"], supports: ["state-legibility", "contrast-floor", "risk-language"]},
        {id: "foundation.overflow-law", elementId: "element.toolkit.foundation-token", layer: "foundation", label: "Overflow law", contract: "Long names truncate, wrap, or reveal by policy; they never smash adjacent columns.", states: ["ellipsis", "wrap", "popover", "expanded"], supports: ["long-paths", "column-integrity", "no-overlap"]},

        {id: "control.selection.checkbox", elementId: "element.toolkit.selection-control", layer: "control", label: "Checkbox", contract: "Boolean selection with real checked/unchecked/disabled/focus states.", states: ["unchecked", "checked", "disabled", "focus", "hover", "pressed"], supports: ["single-row-select", "bulk-membership", "aria-checked"]},
        {id: "control.selection.tristate", elementId: "element.toolkit.selection-control", layer: "control", label: "Tri-state checkbox", contract: "Hierarchical selection states are derived by controller, not hand-painted in the view.", states: ["unchecked", "checked", "mixed", "blocked", "disabled", "focus"], supports: ["folder-shortcut-selection", "mixed-state", "blocked-visible-not-selectable"]},
        {id: "control.disclosure", elementId: "element.toolkit.disclosure-control", layer: "control", label: "Disclosure control", contract: "Expand/collapse affordance that distinguishes branch, leaf, loading, and disabled rows.", states: ["collapsed", "expanded", "leaf", "loading", "disabled", "focus"], supports: ["aria-expanded", "tree-depth", "lazy-children"]},
        {id: "control.resize-handle", elementId: "element.toolkit.resize-handle", layer: "control", label: "Column resize handle", contract: "Resizable boundary with mouse, touch, keyboard, min/max, reset, and no header-text collision.", states: ["idle", "hover", "active", "focus", "min", "max"], supports: ["pointer-resize", "keyboard-resize", "column-width-view-state"]},
        {id: "control.sort-indicator", elementId: "element.toolkit.sort-indicator", layer: "control", label: "Sort indicator", contract: "Sort order is explicit, keyboard reachable, and attached to a typed field.", states: ["none", "ascending", "descending", "multi-sort"], supports: ["field-sort", "aria-sort", "column-header"]},
        {id: "control.filter-chip", elementId: "element.toolkit.filter-chip", layer: "control", label: "Filter chip", contract: "Active filters are visible, removable, and connected to a field predicate.", states: ["inactive", "active", "negated", "invalid", "locked"], supports: ["field-filter", "facet-summary", "clear-filter"]},
        {id: "control.command-button", elementId: "element.toolkit.command-button", layer: "control", label: "Command button", contract: "Actions declare risk, policy, enablement reason, and preview requirements.", states: ["enabled", "disabled", "busy", "danger", "blocked", "requires-preview"], supports: ["action-policy", "safety-gate", "status-feedback"]},
        {id: "control.drag-handle", elementId: "element.toolkit.drag-handle", layer: "control", label: "Drag handle", contract: "Move/reorder affordances are separate from selection and require explicit drop policy.", states: ["idle", "grabbed", "dragging", "drop-allowed", "drop-blocked"], supports: ["reorder", "drag-proof", "drop-boundary"]},
        {id: "control.bulk-selector", elementId: "element.toolkit.bulk-selector", layer: "control", label: "Bulk selector", contract: "Bulk state represents visible, filtered, and total eligible records without lying.", states: ["none", "some", "all-visible", "all-filtered", "all-eligible", "blocked-present"], supports: ["select-all-eligible", "filtered-selection", "selected-output-proof"]},
        {id: "control.tab", elementId: "element.toolkit.tab", layer: "control", label: "Tab", contract: "A tab activates view state and its associated panel; it is not a command button.", states: ["selected", "unselected", "focused", "disabled"], supports: ["single-select-tabs", "panel-switching", "keyboard-navigation", "a11y-state"]},

        {id: "cell.path", elementId: "element.toolkit.path-cell", layer: "cell", label: "Path cell", contract: "Repo-relative, filesystem, URL, and breadcrumb paths have stable truncation and reveal behavior.", states: ["short", "deep", "overflow", "root", "highlighted-segment"], supports: ["path-hierarchy", "segment-copy", "long-labels"]},
        {id: "cell.icon-label", elementId: "element.toolkit.name-cell", layer: "cell", label: "Icon + label cell", contract: "Identity labels keep icon, name, badges, and secondary text aligned at all densities.", states: ["normal", "selected", "renaming", "warning", "blocked"], supports: ["primary-field", "preview-target", "resource-kind"]},
        {id: "cell.status", elementId: "element.toolkit.status-cell", layer: "cell", label: "Status cell", contract: "Status values use consistent labels, badges, sorting weights, and warning colors.", states: ["clean", "changed", "untracked", "blocked", "warning", "error"], supports: ["status-sort", "status-filter", "risk-language"]},
        {id: "cell.risk", elementId: "element.toolkit.risk-cell", layer: "cell", label: "Risk cell", contract: "Risk metadata renders as a semantic badge, not arbitrary text.", states: ["low", "medium", "high", "blocked", "unknown"], supports: ["risk-sort", "safety-gate", "audit-scan"]},
        {id: "cell.datetime", elementId: "element.toolkit.datetime-cell", layer: "cell", label: "Datetime cell", contract: "Dates display compactly while keeping absolute time available for inspection.", states: ["today", "recent", "stale", "absolute", "unknown"], supports: ["date-sort", "relative-label", "tooltip-detail"]},
        {id: "cell.reason", elementId: "element.toolkit.reason-cell", layer: "cell", label: "Reason cell", contract: "Explanatory text has clamp/reveal behavior and never consumes the whole row.", states: ["short", "clamped", "expanded", "warning", "blocked"], supports: ["decision-context", "row-explanation", "overflow-law"]},
        {id: "cell.diffstat", elementId: "element.toolkit.diffstat-cell", layer: "cell", label: "Diff stat cell", contract: "Added/changed/deleted counts align and summarize without requiring raw diff text.", states: ["zero", "small", "large", "generated", "unknown"], supports: ["code-review", "change-summary", "sort-by-change"]},
        {id: "cell.action", elementId: "element.toolkit.action-cell", layer: "cell", label: "Action cell", contract: "Row actions live in an explicit action area and cannot masquerade as row selection.", states: ["hidden", "visible", "menu-open", "danger", "blocked"], supports: ["row-actions", "overflow-menu", "safe-commanding"]},

        {id: "collection.list", elementId: "element.toolkit.collection-view", layer: "collection", label: "List", contract: "Linear scan of items with primary label and optional secondary metadata.", states: ["empty", "loading", "normal", "filtered", "selected"], supports: ["single-column", "keyboard-navigation", "row-actions"]},
        {id: "collection.compact-list", elementId: "element.toolkit.collection-view", layer: "collection", label: "Compact list", contract: "Dense operational scan where row height is intentionally constrained.", states: ["dense", "selected", "focused", "warning", "blocked"], supports: ["manage-many", "keyboard-first", "bulk-actions"]},
        {id: "collection.data-table", elementId: "element.toolkit.collection-view", layer: "collection", label: "Data table", contract: "Flat records with typed columns, sorting, filtering, resizing, and bulk selection.", states: ["normal", "sorted", "filtered", "resized", "empty"], supports: ["multi-column-fields", "sort", "filter", "bulk-selection"]},
        {id: "collection.tree", elementId: "element.toolkit.collection-view", layer: "collection", label: "Tree", contract: "Hierarchy where labels are primary and metadata is secondary.", states: ["collapsed", "expanded", "active", "selected", "lazy-loading"], supports: ["hierarchy", "path-context", "keyboard-tree"]},
        {id: "collection.treegrid", elementId: "element.toolkit.collection-view", layer: "collection", label: "Treegrid", contract: "Hierarchy plus typed columns, row states, expansion, and selection semantics.", states: ["normal", "expanded", "selected", "mixed", "blocked", "resized"], supports: ["hierarchy", "multi-column-fields", "tri-state-selection", "resizable-columns"]},
        {id: "collection.icon-grid", elementId: "element.toolkit.collection-view", layer: "collection", label: "Icon grid", contract: "Visual browsing with icon scale, label wrapping, and preview coupling.", states: ["small-icons", "medium-icons", "large-icons", "selected", "previewing"], supports: ["visual-browse", "preview", "spatial-memory"]},
        {id: "collection.column-browser", elementId: "element.toolkit.collection-view", layer: "collection", label: "Column browser", contract: "Depth-by-column navigation with sibling context and inspector/preview coupling.", states: ["root", "drilled", "previewing", "narrow", "overflow"], supports: ["navigate-depth", "path-context", "sibling-scan"]},
        {id: "collection.gallery", elementId: "element.toolkit.collection-view", layer: "collection", label: "Gallery", contract: "Preview-first browsing where selected resource dominates the layout.", states: ["empty", "selected", "preview-loaded", "preview-error"], supports: ["visual-preview", "inspect", "media-browse"]},
        {id: "collection.timeline", elementId: "element.toolkit.collection-view", layer: "collection", label: "Timeline", contract: "Events ordered by time with grouping and expandable evidence.", states: ["live", "paused", "grouped", "selected"], supports: ["audit-history", "temporal-debug", "event-log"]},
        {id: "collection.matrix", elementId: "element.toolkit.collection-view", layer: "collection", label: "Matrix", contract: "Cross-product of subjects and capabilities such as permissions or compatibility.", states: ["normal", "partial", "conflict", "read-only"], supports: ["permission-audit", "coverage-map", "compatibility"]},

        {id: "layout.toolbar", elementId: "element.toolkit.toolbar", layer: "layout", label: "Toolbar", contract: "Commands are grouped by task, risk, and enablement state.", states: ["normal", "overflow", "disabled", "busy"], supports: ["command-grouping", "action-policy", "keyboard-access"]},
        {id: "layout.command-bar", elementId: "element.toolkit.toolbar", layer: "layout", label: "Command bar", contract: "Primary workflow commands show preconditions and consequences before execution.", states: ["ready", "blocked", "requires-preview", "busy"], supports: ["workflow-action", "safety-preview", "status-report"]},
        {id: "layout.split-pane", elementId: "element.toolkit.split-pane", layer: "layout", label: "Split pane", contract: "Resizable panels preserve minimum usable dimensions and keyboard resizing.", states: ["balanced", "left-max", "right-max", "collapsed", "resizing"], supports: ["resizable-panel", "preview-sidecar", "inspector-sidecar"]},
        {id: "layout.inspector-pane", elementId: "element.toolkit.inspector-pane", layer: "layout", label: "Inspector pane", contract: "Selected item metadata is read-only or edit-capable by explicit policy.", states: ["empty", "single-selected", "multi-selected", "blocked", "dirty"], supports: ["selected-detail", "properties", "policy-proof"]},
        {id: "layout.preview-pane", elementId: "element.toolkit.preview-pane", layer: "layout", label: "Preview pane", contract: "Preview content is coupled to selection and isolates unsafe rendering.", states: ["empty", "loading", "preview", "unsupported", "error"], supports: ["preview", "inspect", "read-only-boundary"]},
        {id: "layout.status-bar", elementId: "element.toolkit.status-bar", layer: "layout", label: "Status bar", contract: "Summary state, selection count, filter count, and last action are visible.", states: ["idle", "success", "warning", "error", "busy"], supports: ["feedback", "selected-output-count", "last-action"]},
        {id: "layout.terminal-viewport", elementId: "element.compute.terminal-view", layer: "layout", label: "Terminal viewport", contract: "Prompt, input buffer, stdout/stderr scrollback, and analysis output render as one owned terminal view.", states: ["ready", "typing", "running", "scrolled", "failed"], supports: ["terminal-session", "owned-terminal-scrollback", "prompt-input-output-split"]},
        {id: "layout.tabbed-workspace", elementId: "element.toolkit.tabbed-workspace", layer: "layout", label: "Tabbed workspace", contract: "Notebook shell where a tab strip selects sibling panels while preserving panel model truth.", states: ["active-tab", "inactive-panels-hidden", "route-synced", "keyboard-focus"], supports: ["single-select-tabs", "panel-switching", "route-sync", "preserve-panel-state", "keyboard-navigation"]},
        {id: "layout.tab-list", elementId: "element.toolkit.tab-list", layer: "layout", label: "Tab list", contract: "Tab strip advertises its orientation, active tab, and panel mapping instead of acting as generic navigation.", states: ["horizontal", "wrapped", "focused", "overflow"], supports: ["single-select-tabs", "roving-tabindex", "a11y-state"]},

        {id: "controller.selection", elementId: "element.toolkit.selection-controller", layer: "controller", label: "Selection controller", contract: "The controller owns legal transitions and selected output derivation.", states: ["none", "some", "all", "mixed", "blocked-present"], supports: ["toggle", "range-select", "bulk-select", "explicit-output"]},
        {id: "controller.expansion", elementId: "element.toolkit.expansion-controller", layer: "controller", label: "Expansion controller", contract: "Expansion is view state that never deletes selected model truth.", states: ["collapsed", "expanded", "expand-all", "collapse-all", "lazy"], supports: ["tree-state", "visible-window", "preserve-selection"]},
        {id: "controller.column-sizing", elementId: "element.toolkit.column-sizing-controller", layer: "controller", label: "Column sizing controller", contract: "Widths are bounded view state with presets, drag, keyboard, and reset behavior.", states: ["default", "compact", "wide-path", "custom", "reset"], supports: ["resizable-columns", "min-max", "keyboard-resize"]},
        {id: "controller.sort-filter", elementId: "element.toolkit.sort-filter-controller", layer: "controller", label: "Sort/filter controller", contract: "Typed fields define legal comparators, predicates, and empty-state text.", states: ["unsorted", "sorted", "filtered", "empty-filter", "invalid-filter"], supports: ["field-sort", "facet-filter", "search"]},
        {id: "controller.safety-gate", elementId: "element.toolkit.safety-controller", layer: "controller", label: "Safety gate controller", contract: "Dangerous actions require preconditions, preview, and policy proof.", states: ["allowed", "blocked", "requires-preview", "requires-confirmation"], supports: ["no-click", "no-submit", "no-command-execution"]},
        {id: "controller.terminal-session", elementId: "element.compute.terminal-controller", layer: "controller", label: "Terminal session controller", contract: "Stages commands, routes AI suggestions to input only, and treats Enter/run as an explicit command-execution boundary.", states: ["idle", "input-staged", "suggesting", "requires-user-enter", "running", "blocked"], supports: ["terminal-session", "no-command-execution", "stage-only-until-user-enter"]},
        {id: "controller.view-resolver", elementId: "element.toolkit.view-resolver", layer: "controller", label: "View resolver", contract: "Functional need plus contract requirements determine eligible visualizations.", states: ["resolved", "manual-override", "rejected", "needs-more-data"], supports: ["need-to-view", "capability-match", "explainability"]},
        {id: "controller.tab-state", elementId: "element.toolkit.tab-controller", layer: "controller", label: "Tab state controller", contract: "Owns activeTabId, legal tab ids, route synchronization, and keyboard movement without executing commands.", states: ["active", "fallback-default", "route-synced", "keyboard-moving"], supports: ["single-select-tabs", "panel-switching", "route-sync", "keyboard-navigation"]},

        {id: "pattern.file-basket", elementId: "element.toolkit.contract-pattern", layer: "contract", label: "File basket", contract: "Choose exact files for an operation with hierarchy shortcuts, typed metadata, and blocked rows.", states: ["collecting", "reviewing", "ready", "blocked-present"], supports: ["hierarchical-explicit-files", "selected-output-proof", "safety-gate"]},
        {id: "pattern.file-picker", elementId: "element.toolkit.contract-pattern", layer: "contract", label: "File picker", contract: "Select one or many resources under read/mutation boundaries.", states: ["browse", "search", "selected", "invalid"], supports: ["path-navigation", "preview", "selection"]},
        {id: "pattern.resource-browser", elementId: "element.toolkit.contract-pattern", layer: "contract", label: "Resource browser", contract: "Find, inspect, compare, preview, and act on resources by intent.", states: ["find", "browse", "compare", "inspect", "preview"], supports: ["intent-to-view", "details-pane", "column-browser"]},
        {id: "pattern.terminal-session", elementId: "element.toolkit.contract-pattern", layer: "contract", label: "Terminal session", contract: "A shell terminal is one semantic object with explicit model/controller/view split and no-command-execution proof during MCEL enrichment.", states: ["ready", "input-staged", "suggesting", "running", "complete", "failed"], supports: ["element.compute.terminal", "controller.terminal-session", "layout.terminal-viewport", "no-command-execution"]},
        {id: "pattern.diff-selector", elementId: "element.toolkit.contract-pattern", layer: "contract", label: "Diff selector", contract: "Review changed units and choose what enters a patch/commit.", states: ["unreviewed", "selected", "excluded", "conflict"], supports: ["diffstat", "risk", "explicit-output"]},
        {id: "pattern.process-table", elementId: "element.toolkit.contract-pattern", layer: "contract", label: "Process table", contract: "Observe runtime processes while destructive actions are gated.", states: ["running", "stopped", "busy", "blocked"], supports: ["operational-scan", "status", "no-click-actions"]},
        {id: "pattern.tabbed-workspace", elementId: "element.toolkit.contract-pattern", layer: "contract", label: "Tabbed workspace", contract: "Switch between sibling workspace panels through declared tab state, active panel mapping, and optional route sync.", states: ["active-tab", "route-synced", "keyboard-ready", "panel-state-preserved"], supports: ["single-select-tabs", "panel-switching", "route-sync", "preserve-panel-state", "keyboard-navigation"]},
        {id: "pattern.settings-editor", elementId: "element.toolkit.contract-pattern", layer: "contract", label: "Settings editor", contract: "Edit named configuration with validation, dirty state, and save policy.", states: ["clean", "dirty", "invalid", "saving", "blocked"], supports: ["form-state", "validation", "save-policy"]},
        {id: "pattern.permission-matrix", elementId: "element.toolkit.contract-pattern", layer: "contract", label: "Permission matrix", contract: "Compare principals against capabilities with partial and blocked states.", states: ["allowed", "denied", "partial", "inherited", "blocked"], supports: ["matrix", "tri-state", "audit"]},
        {id: "pattern.log-explorer", elementId: "element.toolkit.contract-pattern", layer: "contract", label: "Log explorer", contract: "Search, filter, group, and inspect streaming events.", states: ["live", "paused", "filtered", "selected"], supports: ["timeline", "search", "preview"]}
      ];

      const VIEW_RECIPES = [
        {
          id: "contract-treegrid",
          label: "Contract treegrid",
          capabilities: ["hierarchy", "multi-column-fields", "typed-cells", "tri-state-selection", "blocked-visible-not-selectable", "selected-output-proof", "interactive-expand-collapse", "resizable-columns", "keyboard-navigation"],
          primitiveIds: ["control.selection.tristate", "control.disclosure", "control.resize-handle", "cell.path", "cell.status", "cell.risk", "cell.reason", "controller.selection", "controller.expansion", "controller.column-sizing"],
          bestFor: ["file-basket", "diff-selector", "resource-browser"]
        },
        {
          id: "data-table",
          label: "Data table",
          capabilities: ["multi-column-fields", "typed-cells", "sort", "filter", "bulk-selection", "resizable-columns", "keyboard-navigation"],
          primitiveIds: ["control.selection.checkbox", "control.sort-indicator", "control.filter-chip", "control.resize-handle", "cell.status", "cell.risk", "controller.sort-filter", "controller.column-sizing"],
          bestFor: ["process-table", "settings-audit", "flat-review"]
        },
        {
          id: "tabbed-data-workspace",
          label: "Tabbed data workspace",
          capabilities: ["single-select-tabs", "panel-switching", "route-sync", "preserve-panel-state", "keyboard-navigation", "a11y-state"],
          primitiveIds: ["layout.tabbed-workspace", "layout.tab-list", "control.tab", "controller.tab-state"],
          bestFor: ["tabbed-workspace", "notebook", "task-manager-data-views"]
        },
        {
          id: "plain-tree",
          label: "Plain tree",
          capabilities: ["hierarchy", "interactive-expand-collapse", "keyboard-navigation"],
          primitiveIds: ["control.disclosure", "cell.icon-label", "controller.expansion"],
          bestFor: ["simple-navigation"]
        },
        {
          id: "column-browser-inspector",
          label: "Column browser + inspector",
          capabilities: ["hierarchy", "path-context", "preview", "inspector", "keyboard-navigation", "selected-output-proof"],
          primitiveIds: ["collection.column-browser", "layout.inspector-pane", "layout.preview-pane", "cell.path", "controller.selection"],
          bestFor: ["navigate-depth", "inspect", "resource-browser"]
        },
        {
          id: "terminal-session-surface",
          label: "Terminal session surface",
          capabilities: ["terminal-model", "terminal-controller", "terminal-viewport", "prompt-input-output-split", "owned-terminal-scrollback", "no-command-execution"],
          primitiveIds: ["layout.terminal-viewport", "controller.terminal-session", "controller.safety-gate"],
          bestFor: ["terminal-session", "command-staging", "shell-output-review"]
        },
        {
          id: "compact-audit-list",
          label: "Compact audit list",
          capabilities: ["typed-cells", "bulk-selection", "status-scan", "keyboard-navigation", "selected-output-proof"],
          primitiveIds: ["collection.compact-list", "control.bulk-selector", "cell.status", "cell.risk", "cell.datetime", "controller.selection"],
          bestFor: ["manage-many", "audit-status"]
        },
        {
          id: "icon-grid",
          label: "Icon grid",
          capabilities: ["visual-browse", "preview", "selection", "keyboard-navigation"],
          primitiveIds: ["collection.icon-grid", "cell.icon-label", "layout.preview-pane", "controller.selection"],
          bestFor: ["media-browse", "visual-browse"]
        }
      ];

      const CONTRACT_PATTERNS = {
        fileBasket: {
          id: "file-basket",
          label: "File basket contract",
          intent: "Select exact safe file paths for an operation while preserving directory shortcut semantics and blocked-row visibility.",
          requires: ["hierarchy", "multi-column-fields", "typed-cells", "tri-state-selection", "blocked-visible-not-selectable", "selected-output-proof", "interactive-expand-collapse", "resizable-columns"],
          fields: ["path", "status", "bucket", "risk", "source", "reason", "modified", "selectable"],
          requiredPrimitives: ["control.selection.tristate", "control.disclosure", "control.resize-handle", "cell.path", "cell.status", "cell.risk", "cell.reason", "controller.selection", "controller.expansion", "controller.column-sizing", "controller.view-resolver"],
          mustReject: ["plain-tree", "title-only-tree", "icon-grid-primary"],
          selection: "hierarchical-explicit-files",
          safety: ["blocked rows visible, not selectable", "selected files are source of truth", "destructive actions require preview"]
        },
        resourceBrowser: {
          id: "resource-browser",
          label: "Resource browser contract",
          intent: "Find, browse, compare, inspect, preview, and organize resources by user need.",
          requires: ["path-context", "selection", "preview", "typed-cells", "keyboard-navigation"],
          fields: ["name", "kind", "path", "modified", "size", "status"],
          requiredPrimitives: ["cell.path", "cell.icon-label", "layout.preview-pane", "layout.inspector-pane", "controller.view-resolver"],
          mustReject: ["title-only-tree"],
          selection: "resource-selection",
          safety: ["mutation requires explicit policy"]
        },
        processTable: {
          id: "process-table",
          label: "Process table contract",
          intent: "Observe runtime state and gate destructive controls.",
          requires: ["multi-column-fields", "typed-cells", "sort", "filter", "status-scan", "safety-gate"],
          fields: ["pid", "name", "status", "cpu", "memory", "uptime", "action"],
          requiredPrimitives: ["collection.data-table", "cell.status", "cell.action", "controller.safety-gate", "controller.sort-filter"],
          mustReject: ["card-only"],
          selection: "row-action-policy",
          safety: ["kill/terminate require no-click proof"]
        },
        terminalSession: {
          id: "terminal-session",
          label: "Terminal session contract",
          intent: "Represent a shell terminal as a single semantic MCEL object with owned state, output feeds, and explicit command execution boundaries.",
          requires: ["terminal-model", "terminal-controller", "terminal-viewport", "prompt-input-output-split", "no-command-execution"],
          fields: ["cwd", "timeout", "prompt", "inputBuffer", "stdout", "stderr", "exitCode", "duration", "suggestion"],
          requiredPrimitives: ["layout.terminal-viewport", "controller.terminal-session", "controller.safety-gate", "pattern.terminal-session"],
          mustReject: ["generic-mounted-region", "loose-text-lines", "run-button-without-policy"],
          selection: "terminal-session-state",
          safety: ["MCEL proof does not run commands", "AI suggestions stage commands only", "Enter/run boundaries are marked as command execution"]
        },
        tabbedWorkspace: {
          id: "tabbed-workspace",
          label: "Tabbed workspace contract",
          intent: "Switch between sibling panels by activeTabId while preserving panel state and never treating tabs as command execution.",
          requires: ["single-select-tabs", "panel-switching", "keyboard-navigation", "a11y-state"],
          fields: ["tabId", "label", "controlsPanelId", "selected", "panelVisible"],
          requiredPrimitives: ["layout.tabbed-workspace", "layout.tab-list", "control.tab", "controller.tab-state"],
          mustReject: ["button-row-as-navigation", "view-mode-controller"],
          selection: "active-tab-view-state",
          safety: ["tab activation is inspect-only view state", "route sync is optional and declared", "panel contents preserve model truth"]
        }
      };

      function clone(value) {
        return JSON.parse(JSON.stringify(value));
      }

      function normalizeList(value) {
        return Array.isArray(value) ? value.filter(Boolean) : [];
      }

      function listPrimitives(filter = {}) {
        return PRIMITIVES.filter((primitive) => {
          if (filter.layer && primitive.layer !== filter.layer) return false;
          if (filter.elementId && primitive.elementId !== filter.elementId) return false;
          if (filter.supports && !normalizeList(filter.supports).every((capability) => primitive.supports.includes(capability))) return false;
          return true;
        }).map(clone);
      }

      function primitivesByLayer() {
        return LAYERS.reduce((accumulator, layer) => {
          accumulator[layer.id] = listPrimitives({layer: layer.id});
          return accumulator;
        }, {});
      }

      function getPrimitive(id) {
        const primitive = PRIMITIVES.find((candidate) => candidate.id === id);
        return primitive ? clone(primitive) : null;
      }

      function getViewRecipe(id) {
        const recipe = VIEW_RECIPES.find((candidate) => candidate.id === id);
        return recipe ? clone(recipe) : null;
      }

      function evaluateView(contract, view) {
        const required = normalizeList(contract?.requires);
        const capabilities = normalizeList(view?.capabilities);
        const missingCapabilities = required.filter((capability) => !capabilities.includes(capability));
        const requiredPrimitives = normalizeList(contract?.requiredPrimitives);
        const providedPrimitives = normalizeList(view?.primitiveIds);
        const missingPrimitives = requiredPrimitives.filter((primitiveId) => !providedPrimitives.includes(primitiveId));
        const explicitlyRejected = normalizeList(contract?.mustReject).includes(view.id);
        const eligible = missingCapabilities.length === 0 && !explicitlyRejected;
        const capabilityScore = required.length ? (required.length - missingCapabilities.length) / required.length : 1;
        const primitiveScore = requiredPrimitives.length ? (requiredPrimitives.length - missingPrimitives.length) / requiredPrimitives.length : 1;
        const safetyPenalty = explicitlyRejected ? 0.5 : 0;
        const score = Math.max(0, Math.round(((capabilityScore * 0.7) + (primitiveScore * 0.3) - safetyPenalty) * 100));
        const reason = eligible
          ? `Eligible: satisfies ${required.length} required capabilities for ${contract.label || contract.id}.`
          : `Rejected as primary: missing ${missingCapabilities.join(", ") || "no capabilities"}${explicitlyRejected ? "; explicitly rejected by contract" : ""}.`;
        return {
          id: view.id,
          label: view.label,
          eligible,
          score,
          reason,
          missingCapabilities,
          missingPrimitives,
          primitiveIds: providedPrimitives,
          capabilities
        };
      }

      function resolveViews(contract = CONTRACT_PATTERNS.fileBasket) {
        return VIEW_RECIPES
          .map((view) => evaluateView(contract, view))
          .sort((left, right) => Number(right.eligible) - Number(left.eligible) || right.score - left.score || left.label.localeCompare(right.label));
      }

      function buildToolkitReadinessReport() {
        const byLayer = primitivesByLayer();
        const fileBasketResolution = resolveViews(CONTRACT_PATTERNS.fileBasket);
        return {
          version: TOOLKIT_VERSION,
          layerCount: LAYERS.length,
          primitiveCount: PRIMITIVES.length,
          controlPrimitiveCount: byLayer.control.length,
          dataCellPrimitiveCount: byLayer.cell.length,
          collectionPrimitiveCount: byLayer.collection.length,
          controllerPrimitiveCount: byLayer.controller.length,
          contractPatternCount: byLayer.contract.length,
          fileBasketEligibleViewCount: fileBasketResolution.filter((candidate) => candidate.eligible).length,
          fileBasketBestView: fileBasketResolution[0]?.id || "none",
          titleOnlyTreeRejected: fileBasketResolution.some((candidate) => candidate.id === "plain-tree" && !candidate.eligible),
          requiredFileBasketPrimitives: CONTRACT_PATTERNS.fileBasket.requiredPrimitives.length,
          noOneOffControls: PRIMITIVES.some((primitive) => primitive.id === "control.selection.tristate") &&
            PRIMITIVES.some((primitive) => primitive.id === "control.resize-handle") &&
            PRIMITIVES.some((primitive) => primitive.id === "controller.view-resolver")
        };
      }

      global.McelToolkitCore = {
        TOOLKIT_VERSION,
        LAYERS: clone(LAYERS),
        PRIMITIVES: clone(PRIMITIVES),
        VIEW_RECIPES: clone(VIEW_RECIPES),
        CONTRACT_PATTERNS: clone(CONTRACT_PATTERNS),
        listPrimitives,
        primitivesByLayer,
        getPrimitive,
        getViewRecipe,
        evaluateView,
        resolveViews,
        buildToolkitReadinessReport
      };
    })(window);
