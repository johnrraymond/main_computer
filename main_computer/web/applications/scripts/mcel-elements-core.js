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
        def("element.core.mvc-model", "MVC Model", "core", "mvc-model", "Contract-bearing state and data model that declares fields, identity, invariants, user intent, and safety promises before a view is selected.", {
          htmlTag: "mcel-mvc-model",
          riskPolicy: analysis,
          stateModel: {required: ["intent", "fields", "records"], optional: ["hierarchy", "selectionContract", "safetyContract", "viewRequirements"]},
          dataModel: {fieldTypes: ["path", "enum", "risk", "text", "datetime", "boolean"], identity: "stable record id"},
          interactionModel: {mutatesDom: false, ownsTruth: true},
          decoderHints: ["model", "schema", "fields", "records", "contract", "data source"],
          supersedes: ["anonymous DOM state", "title-only payload"]
        }),
        def("element.core.mvc-controller", "MVC Controller", "core", "mvc-controller", "Command and state-transition boundary that enforces selection rules, sorting, filtering, safety gates, and selected-output derivation.", {
          htmlTag: "mcel-mvc-controller",
          riskPolicy: safe,
          actionPolicy: {dispatch: "inspect-only", toggleSelect: "inspect-only", toggleExpand: "inspect-only", resizeColumn: "inspect-only", sort: "inspect-only", filter: "inspect-only", unsafeMutation: "no-submit"},
          stateModel: {required: ["commands", "currentState"], optional: ["derivedSelection", "expandedNodeIds", "columnWidths", "disabledActions", "viewResolver"]},
          interactionModel: {input: "view events", output: "validated state transitions", commands: ["toggleFile", "toggleDirectoryShortcut", "expand", "collapse", "resizeColumn", "selectAllEligible", "clearSelection"]},
          decoderHints: ["controller", "dispatch", "selection", "sort", "filter", "resolver", "commands"],
          supersedes: ["event handler soup", "inline checkbox side effects"]
        }),
        def("element.core.mvc-view", "MVC View", "core", "mvc-view", "Capability-declared renderer that can only visualize a model when it satisfies the contract required by user intent, fields, and selection semantics.", {
          htmlTag: "mcel-mvc-view",
          riskPolicy: safe,
          stateModel: {required: ["capabilities", "boundModel", "controller"], optional: ["viewMode", "density", "explainability"]},
          interactionModel: {renders: "model snapshot", emits: "controller commands", rejects: "unsupported contracts"},
          accessibility: {requiresDeclaredRoles: true},
          decoderHints: ["view", "renderer", "capabilities", "treegrid", "details", "columns", "eligible view"],
          supersedes: ["slimed one-off DOM view", "view that invents business rules"]
        }),

        def("element.compute.terminal", "Terminal", "compute", "terminal-session", "First-class semantic shell object that owns cwd, prompt/input, scrollback, output feeds, and the command execution boundary instead of treating the terminal as loose text nodes.", {
          htmlTag: "mcel-terminal",
          allowedChildren: ["element.compute.terminal-session-model", "element.compute.terminal-controller", "element.compute.terminal-view", "element.core.status-feed"],
          riskPolicy: noCommand,
          proofPolicy: "no-command-execution",
          commandPolicy: "stage-only-until-user-enter",
          scrollPolicy: "owned-terminal-scrollback",
          stateModel: {required: ["cwd", "prompt", "inputBuffer", "scrollback", "executionBoundary"], optional: ["shell", "history", "timeoutSeconds", "busy", "exitStatus", "lastCommand"]},
          interactionModel: {typeText: "stage-input", pasteCommand: "stage-input", pressEnter: "no-command-execution", sendCtrlC: "safe-interrupt", ownsTruth: true},
          accessibility: {role: "terminal", liveRegion: "polite log"},
          dataModel: {fieldTypes: ["cwd", "command", "stdout", "stderr", "exit-code", "duration", "prompt"], identity: "terminal session"},
          decoderHints: ["terminal", "xterm", "shell", "prompt", "cwd", "stdout", "stderr", "command", "scrollback"],
          supersedes: ["xterm div plus text lines", "black rectangle with glyphs", "generic mounted app region"]
        }),
        def("element.compute.terminal-session-model", "Terminal Session Model", "compute", "terminal-session-model", "Model element for terminal state: cwd, timeout, prompt/input buffer, history, scrollback, exit status, and output streams.", {
          htmlTag: "mcel-terminal-model",
          riskPolicy: analysis,
          proofPolicy: "inspect-only",
          stateModel: {required: ["cwd", "timeoutSeconds", "inputBuffer", "scrollback"], optional: ["prompt", "shell", "history", "lastExitCode", "lastDuration"]},
          dataModel: {fieldTypes: ["cwd", "timeout", "input-buffer", "history", "stdout", "stderr", "exit-code"], identity: "terminal runtime state"},
          interactionModel: {mutatesDom: false, ownsTruth: true},
          decoderHints: ["terminal state", "cwd", "timeout", "buffer", "prompt", "history", "scrollback"],
          supersedes: ["hidden globals", "anonymous local state", "text-only terminal memory"]
        }),
        def("element.compute.terminal-controller", "Terminal Controller", "compute", "terminal-controller", "Controller boundary that stages input, separates AI suggestion from execution, and makes every command-running affordance explicit.", {
          htmlTag: "mcel-terminal-controller",
          riskPolicy: noCommand,
          proofPolicy: "no-command-execution",
          actionPolicy: {stageCommand: "inspect-only", suggestCommand: "inspect-only", runCommand: "requires-user-enter", pasteCommand: "stage-only", clear: "safe"},
          stateModel: {required: ["executionBoundary", "commandPolicy"], optional: ["suggestionSource", "interruptState", "disabledReason"]},
          interactionModel: {input: "user keystrokes and staged commands", output: "validated terminal state transition", ownsTruth: true},
          decoderHints: ["run command", "Enter", "paste", "AI suggest", "copy command", "clear terminal"],
          supersedes: ["raw click handler", "generic button action", "unlabeled command execution"]
        }),
        def("element.compute.terminal-view", "Terminal View", "compute", "terminal-view", "Read-only xterm/scrollback surface with prompt and output feeds linked back to the terminal session model.", {
          htmlTag: "mcel-terminal-view",
          riskPolicy: safe,
          proofPolicy: "inspect-only",
          scrollPolicy: "owned-terminal-scrollback",
          stateModel: {required: ["visibleRows", "scrollback", "prompt"], optional: ["selection", "analysisPanel", "lastFailure"]},
          interactionModel: {renders: "terminal session model", emits: "controller keystroke commands", rejects: "business logic"},
          accessibility: {role: "log", ownsInteractiveInput: true},
          decoderHints: ["xterm", "terminal output", "prompt", "scrollback", "stdout", "stderr"],
          supersedes: ["generic div", "loose line list", "black rectangle"]
        }),

                def("element.toolkit.foundation-token", "Toolkit Foundation Token", "toolkit", "foundation-token", "Shared MCEL primitive for density, focus, hit target, state, overflow, motion, and contrast rules.", {
          htmlTag: "mcel-toolkit-token",
          riskPolicy: inspectOnly,
          stateModel: {required: ["tokenId", "states"], optional: ["density", "contrast", "motion", "overflow"]},
          interactionModel: {mutatesDom: false, ownsTruth: false},
          decoderHints: ["foundation token", "density", "focus ring", "hit target", "overflow law"],
          supersedes: ["ad hoc CSS magic number", "untracked state color", "tiny debug glyph"]
        }),
        def("element.toolkit.selection-control", "Toolkit Selection Control", "toolkit", "selection-control", "Reusable checkbox and tri-state selector with real states, keyboard semantics, and controller-owned selection truth.", {
          htmlTag: "mcel-selection-control",
          riskPolicy: safe,
          stateModel: {required: ["checkedState", "disabled"], optional: ["mixed", "blocked", "focused", "pressed"]},
          interactionModel: {click: "dispatch-toggle", keyboard: "Space Enter", ownsTruth: false},
          accessibility: {role: "checkbox", ariaChecked: ["false", "true", "mixed"]},
          decoderHints: ["checkbox", "tri-state", "mixed", "blocked visible not selectable", "bulk selection"],
          supersedes: ["checkbox-ish debug glyph", "button pretending to be checkbox"]
        }),
        def("element.toolkit.disclosure-control", "Toolkit Disclosure Control", "toolkit", "disclosure-control", "Reusable expand/collapse affordance that distinguishes branch, leaf, loading, disabled, and keyboard focus states.", {
          htmlTag: "mcel-disclosure-control",
          riskPolicy: safe,
          stateModel: {required: ["expanded", "leaf"], optional: ["loading", "disabled", "focused"]},
          interactionModel: {toggle: "inspect-only", ownsTruth: false},
          accessibility: {ariaExpanded: true, keyboard: ["Enter", "Space", "ArrowRight", "ArrowLeft"]},
          decoderHints: ["expander", "chevron", "disclosure", "tree branch", "leaf row"],
          supersedes: ["dot expander", "dead chevron", "leaf button"]
        }),
        def("element.toolkit.resize-handle", "Toolkit Resize Handle", "toolkit", "resize-handle", "Reusable edge-grip resize primitive with pointer, touch, keyboard, min/max, and reset behavior.", {
          htmlTag: "mcel-resize-handle",
          riskPolicy: safe,
          stateModel: {required: ["target", "min", "max", "value"], optional: ["active", "focused", "preset"]},
          interactionModel: {pointerDrag: "view-state-only", keyboard: "ArrowLeft ArrowRight Home End", ownsTruth: false},
          accessibility: {role: "separator", ariaOrientation: "vertical", ariaValueNow: true},
          decoderHints: ["resize", "column boundary", "edge grip", "split pane grip", "keyboard resizable"],
          supersedes: ["header blob", "tiny resize pill", "invisible drag zone"]
        }),
        def("element.toolkit.sort-indicator", "Toolkit Sort Indicator", "toolkit", "sort-indicator", "Reusable field sort state that connects header UI to typed model comparators.", {
          htmlTag: "mcel-sort-indicator",
          riskPolicy: safe,
          stateModel: {required: ["fieldId", "direction"], optional: ["priority", "disabled"]},
          interactionModel: {toggleSort: "inspect-only", ownsTruth: false},
          accessibility: {ariaSort: ["none", "ascending", "descending", "other"]},
          decoderHints: ["sort", "ascending", "descending", "column header"]
        }),
        def("element.toolkit.filter-chip", "Toolkit Filter Chip", "toolkit", "filter-chip", "Reusable visible filter predicate with remove, locked, negated, and invalid states.", {
          htmlTag: "mcel-filter-chip",
          riskPolicy: safe,
          stateModel: {required: ["fieldId", "predicate"], optional: ["negated", "locked", "invalid"]},
          interactionModel: {removeFilter: "inspect-only", ownsTruth: false},
          decoderHints: ["filter", "facet", "chip", "predicate", "clear filter"]
        }),
        def("element.toolkit.command-button", "Toolkit Command Button", "toolkit", "command-button", "Reusable command affordance that declares risk, enablement reason, policy, and preview preconditions.", {
          htmlTag: "mcel-command-button",
          riskPolicy: analysis,
          stateModel: {required: ["commandId", "enabled", "risk"], optional: ["busy", "blockedReason", "requiresPreview"]},
          interactionModel: {dispatch: "controller-owned", ownsTruth: false},
          decoderHints: ["command", "button", "action", "safety gate", "preview required"],
          supersedes: ["naked onclick button", "unexplained disabled button"]
        }),
        def("element.toolkit.drag-handle", "Toolkit Drag Handle", "toolkit", "drag-handle", "Reusable drag/reorder affordance with explicit drop policy and selection separation.", {
          htmlTag: "mcel-drag-handle",
          riskPolicy: noSubmit,
          stateModel: {required: ["dragState", "dropPolicy"], optional: ["dropTarget", "blockedReason"]},
          interactionModel: {dragStart: "inspect-only", drop: "no-submit"},
          decoderHints: ["drag", "reorder", "move", "drop blocked", "drop allowed"]
        }),
        def("element.toolkit.bulk-selector", "Toolkit Bulk Selector", "toolkit", "bulk-selector", "Reusable bulk selection summary for none, some, visible, filtered, all eligible, and blocked-present states.", {
          htmlTag: "mcel-bulk-selector",
          riskPolicy: safe,
          stateModel: {required: ["eligibleCount", "selectedCount"], optional: ["visibleCount", "filteredCount", "blockedCount"]},
          interactionModel: {selectAllEligible: "inspect-only", clearSelection: "inspect-only", ownsTruth: false},
          decoderHints: ["bulk select", "select all eligible", "filtered selection", "selected output"]
        }),

        def("element.toolkit.path-cell", "Toolkit Path Cell", "toolkit", "data-cell", "Typed path renderer with segment truncation, reveal policy, copy semantics, and hierarchy awareness.", {
          htmlTag: "mcel-path-cell",
          riskPolicy: inspectOnly,
          dataModel: {fieldType: "path", identity: "path string"},
          stateModel: {required: ["path"], optional: ["overflow", "highlightSegment", "root"]},
          decoderHints: ["path", "repo-relative", "breadcrumb", "long path", "segment"],
          supersedes: ["raw path text jammed into title"]
        }),
        def("element.toolkit.name-cell", "Toolkit Name Cell", "toolkit", "data-cell", "Typed icon+label renderer that keeps resource identity, badges, and secondary labels aligned.", {
          htmlTag: "mcel-name-cell",
          riskPolicy: inspectOnly,
          dataModel: {fieldType: "name", identity: "resource id"},
          decoderHints: ["name", "icon label", "resource label", "badge"]
        }),
        def("element.toolkit.status-cell", "Toolkit Status Cell", "toolkit", "data-cell", "Typed status renderer with semantic badge, sort weight, filter key, and warning language.", {
          htmlTag: "mcel-status-cell",
          riskPolicy: inspectOnly,
          dataModel: {fieldType: "status", allowedValues: ["clean", "changed", "untracked", "blocked", "warning", "error"]},
          decoderHints: ["status", "changed", "untracked", "blocked", "warning"]
        }),
        def("element.toolkit.risk-cell", "Toolkit Risk Cell", "toolkit", "data-cell", "Typed risk renderer that makes low, medium, high, and blocked states scan correctly.", {
          htmlTag: "mcel-risk-cell",
          riskPolicy: inspectOnly,
          dataModel: {fieldType: "risk", allowedValues: ["low", "medium", "high", "blocked", "unknown"]},
          decoderHints: ["risk", "low", "medium", "high", "blocked"]
        }),
        def("element.toolkit.datetime-cell", "Toolkit Datetime Cell", "toolkit", "data-cell", "Typed datetime renderer with compact relative label and absolute inspection detail.", {
          htmlTag: "mcel-datetime-cell",
          riskPolicy: inspectOnly,
          dataModel: {fieldType: "datetime"},
          decoderHints: ["date", "time", "modified", "relative time"]
        }),
        def("element.toolkit.reason-cell", "Toolkit Reason Cell", "toolkit", "data-cell", "Typed reason/message renderer with clamp, reveal, warning, and blocked states.", {
          htmlTag: "mcel-reason-cell",
          riskPolicy: inspectOnly,
          dataModel: {fieldType: "text"},
          decoderHints: ["reason", "message", "explanation", "clamped text"],
          supersedes: ["unbounded title suffix", "metadata soup"]
        }),
        def("element.toolkit.diffstat-cell", "Toolkit Diff Stat Cell", "toolkit", "data-cell", "Typed change-count renderer for added, modified, deleted, generated, and unknown change summaries.", {
          htmlTag: "mcel-diffstat-cell",
          riskPolicy: inspectOnly,
          dataModel: {fieldType: "diffstat"},
          decoderHints: ["diff", "added", "modified", "deleted", "change count"]
        }),
        def("element.toolkit.action-cell", "Toolkit Action Cell", "toolkit", "data-cell", "Typed row-action renderer that separates row commands from row selection.", {
          htmlTag: "mcel-action-cell",
          riskPolicy: analysis,
          dataModel: {fieldType: "action-policy"},
          decoderHints: ["row action", "overflow menu", "danger action", "blocked action"]
        }),

        def("element.toolkit.collection-view", "Toolkit Collection View", "toolkit", "collection-view", "Capability-declaring list, table, tree, treegrid, icon grid, column browser, gallery, timeline, or matrix view.", {
          htmlTag: "mcel-collection-view",
          riskPolicy: analysis,
          stateModel: {required: ["viewId", "capabilities"], optional: ["density", "selection", "sort", "filter", "group", "virtualWindow"]},
          interactionModel: {renders: "model snapshot", emits: "controller commands", rejects: "unsupported contract"},
          decoderHints: ["list", "table", "treegrid", "icon grid", "column browser", "gallery", "timeline", "matrix"],
          supersedes: ["view mode picked by taste", "title-only tree for tabular data"]
        }),
        def("element.toolkit.tabbed-workspace", "Toolkit Tabbed Workspace", "toolkit", "tabbed-workspace", "Notebook/workspace shell where sibling panels are switched by tab state rather than command execution.", {
          htmlTag: "mcel-tabbed-workspace",
          riskPolicy: safe,
          allowedChildren: ["element.toolkit.tab-list", "element.toolkit.tab-panel", "element.toolkit.tab-controller"],
          layoutLaws: ["tabs-are-view-state-not-command-buttons", "one-active-tab-controls-one-panel", "hidden-panels-preserve-model-truth", "tab-strip-wraps-without-horizontal-scroll", "no-illegal-nested-scrollbars"],
          stateModel: {required: ["activeTabId", "tabs", "panels"], optional: ["routeSync", "disabledTabIds", "preservePanelState"]},
          interactionModel: {selectTab: "view-state-only", keyboard: ["ArrowLeft", "ArrowRight", "Home", "End", "Enter", "Space"], ownsTruth: false},
          accessibility: {role: "tablist + tab + tabpanel", requiredAttributes: ["aria-selected", "aria-controls", "aria-labelledby"]},
          dataModel: {requires: ["tab id", "tab label", "controlled panel id", "active tab state", "panel visibility"]},
          decoderHints: ["notebook", "tablist", "tabs", "tabpanel", "aria-selected", "aria-controls", "data-task-tab"],
          supersedes: ["buttons pretending to be tabs", "one-off tab strip", "view buttons with hidden panel coupling"]
        }),
        def("element.toolkit.tab-list", "Toolkit Tab List", "toolkit", "tab-list", "Ordered strip of tabs that owns focus movement but not the panel data model.", {
          htmlTag: "mcel-tab-list",
          riskPolicy: safe,
          allowedChildren: ["element.toolkit.tab"],
          stateModel: {required: ["tabIds", "activeTabId"], optional: ["orientation", "overflow"]},
          interactionModel: {rovingFocus: "inspect-only", select: "delegates-to-tab-controller"},
          accessibility: {role: "tablist", requiredAttributes: ["aria-label"]},
          decoderHints: ["tablist", "tab strip", "notebook tabs", "sheet tabs"]
        }),
        def("element.toolkit.tab", "Toolkit Tab", "toolkit", "tab", "Single selectable view-state affordance that activates exactly one associated panel.", {
          htmlTag: "mcel-tab",
          riskPolicy: safe,
          stateModel: {required: ["tabId", "selected", "controlsPanelId"], optional: ["disabled", "route"]},
          interactionModel: {activate: "view-state-only", click: "select-tab", keyboard: ["Enter", "Space"]},
          accessibility: {role: "tab", requiredAttributes: ["aria-selected", "aria-controls"]},
          decoderHints: ["role tab", "aria-selected", "aria-controls", "active tab", "data-task-tab"],
          supersedes: ["navigation button used as tab", "command button that only hides panels"]
        }),
        def("element.toolkit.tab-panel", "Toolkit Tab Panel", "toolkit", "tab-panel", "Panel controlled by a tab; inactive visibility is view state while panel contents preserve model truth.", {
          htmlTag: "mcel-tab-panel",
          riskPolicy: inspectOnly,
          scrollPolicy: "panel-owned-scroll-if-declared",
          stateModel: {required: ["panelId", "labelledByTabId", "visible"], optional: ["preservesState", "loaded"]},
          accessibility: {role: "tabpanel", requiredAttributes: ["aria-labelledby"]},
          decoderHints: ["tabpanel", "aria-labelledby", "hidden panel", "data-task-panel"]
        }),
        def("element.toolkit.tab-controller", "Toolkit Tab Controller", "toolkit", "tab-controller", "MVC controller for tab activation, route synchronization, keyboard movement, and active panel derivation.", {
          htmlTag: "mcel-tab-controller",
          riskPolicy: safe,
          stateModel: {required: ["activeTabId", "legalTabIds"], optional: ["routeSync", "defaultTabId", "lastActivatedAt"]},
          interactionModel: {activateTab: "view-state-only", syncRoute: "inspect-only", rejectUnknownTab: "fallback-default"},
          decoderHints: ["setTaskNotebookTab", "normalizedTaskNotebookTab", "taskNotebookTabFromPath", "sync route", "activeTabId"],
          supersedes: ["ad hoc panel toggler", "unmodelled route tab coupling"]
        }),
        def("element.toolkit.toolbar", "Toolkit Toolbar", "toolkit", "layout", "Command grouping shell that reports task grouping, overflow, risk, and enablement.", {
          htmlTag: "mcel-toolkit-toolbar",
          riskPolicy: safe,
          allowedChildren: ["element.toolkit.command-button", "element.toolkit.filter-chip", "element.toolkit.bulk-selector"],
          decoderHints: ["toolbar", "command bar", "action group", "overflow commands"]
        }),
        def("element.toolkit.split-pane", "Toolkit Split Pane", "toolkit", "layout", "Resizable panel pair with minimum sizes, keyboard resizing, and collapse states.", {
          htmlTag: "mcel-split-pane",
          riskPolicy: safe,
          allowedChildren: ["element.toolkit.resize-handle", "element.toolkit.inspector-pane", "element.toolkit.preview-pane"],
          stateModel: {required: ["primarySize", "secondarySize"], optional: ["collapsed", "resizing"]},
          decoderHints: ["split pane", "resizable panel", "sidecar", "inspector layout"]
        }),
        def("element.toolkit.inspector-pane", "Toolkit Inspector Pane", "toolkit", "layout", "Selected resource details panel with read-only/edit policy and multi-select states.", {
          htmlTag: "mcel-inspector-pane",
          riskPolicy: analysis,
          scrollPolicy: "owned-inspector-scroll",
          stateModel: {required: ["selectedId"], optional: ["properties", "dirty", "blockedReason"]},
          decoderHints: ["inspector", "details pane", "properties", "selected item"]
        }),
        def("element.toolkit.preview-pane", "Toolkit Preview Pane", "toolkit", "layout", "Preview panel coupled to selection with loading, unsupported, error, and read-only boundary states.", {
          htmlTag: "mcel-toolkit-preview",
          riskPolicy: inspectOnly,
          scrollPolicy: "owned-preview-scroll",
          stateModel: {required: ["selectedId"], optional: ["loading", "unsupported", "error"]},
          decoderHints: ["preview", "read-only preview", "selected resource preview"]
        }),
        def("element.toolkit.status-bar", "Toolkit Status Bar", "toolkit", "layout", "Summary shell for selected counts, filter counts, warnings, and last controller action.", {
          htmlTag: "mcel-status-bar",
          riskPolicy: inspectOnly,
          stateModel: {required: ["message"], optional: ["selectedCount", "filterCount", "warningCount", "lastAction"]},
          decoderHints: ["status bar", "selection summary", "last action", "message"]
        }),

        def("element.toolkit.selection-controller", "Toolkit Selection Controller", "toolkit", "controller", "MVC controller primitive for legal selection transitions and explicit selected-output derivation.", {
          htmlTag: "mcel-selection-controller",
          riskPolicy: safe,
          stateModel: {required: ["selectedIds"], optional: ["anchorId", "eligibleIds", "blockedIds"]},
          interactionModel: {toggle: "inspect-only", range: "inspect-only", bulk: "inspect-only", ownsTruth: true},
          decoderHints: ["selection controller", "selected output", "range select", "bulk select"]
        }),
        def("element.toolkit.expansion-controller", "Toolkit Expansion Controller", "toolkit", "controller", "MVC controller primitive for tree expansion view state that preserves model selection truth.", {
          htmlTag: "mcel-expansion-controller",
          riskPolicy: safe,
          stateModel: {required: ["expandedIds"], optional: ["loadingIds", "visibleWindow"]},
          interactionModel: {expand: "inspect-only", collapse: "inspect-only", ownsTruth: false},
          decoderHints: ["expansion controller", "expand all", "collapse all", "visible rows"]
        }),
        def("element.toolkit.column-sizing-controller", "Toolkit Column Sizing Controller", "toolkit", "controller", "MVC controller primitive for column widths, presets, min/max constraints, and keyboard resizing.", {
          htmlTag: "mcel-column-sizing-controller",
          riskPolicy: safe,
          stateModel: {required: ["columnWidths"], optional: ["preset", "resizingColumnId", "minMax"]},
          interactionModel: {resize: "view-state-only", reset: "inspect-only"},
          decoderHints: ["column sizing", "columnWidths", "wide path", "compact columns", "keyboard resize"]
        }),
        def("element.toolkit.sort-filter-controller", "Toolkit Sort/Filter Controller", "toolkit", "controller", "MVC controller primitive for typed sorting, filtering, searching, and empty filtered state.", {
          htmlTag: "mcel-sort-filter-controller",
          riskPolicy: safe,
          stateModel: {required: ["sort", "filters"], optional: ["query", "facets", "emptyReason"]},
          interactionModel: {sort: "inspect-only", filter: "inspect-only", search: "inspect-only"},
          decoderHints: ["sort controller", "filter controller", "search", "facet"]
        }),
        def("element.toolkit.safety-controller", "Toolkit Safety Gate Controller", "toolkit", "controller", "MVC controller primitive for blocked actions, preview preconditions, confirmation, and risk policy proof.", {
          htmlTag: "mcel-safety-controller",
          riskPolicy: noClick,
          stateModel: {required: ["policy"], optional: ["blockedReason", "previewRequired", "confirmationRequired"]},
          interactionModel: {approve: "no-click", submit: "no-submit", execute: "no-command-execution"},
          decoderHints: ["safety gate", "blocked action", "requires preview", "no-click", "no-submit"]
        }),
        def("element.toolkit.view-resolver", "Toolkit View Resolver", "toolkit", "view-resolver", "Contract-to-visualization resolver that scores, accepts, rejects, and explains eligible view recipes.", {
          htmlTag: "mcel-view-resolver",
          riskPolicy: analysis,
          stateModel: {required: ["contract", "viewCapabilities"], optional: ["scores", "manualOverride", "rejectedViews"]},
          interactionModel: {resolve: "inspect-only", manualOverride: "inspect-only", ownsTruth: false},
          decoderHints: ["view resolver", "needs-to-visualization", "capability match", "title-only tree rejected"],
          supersedes: ["view picked by taste", "one-off visualization choice"]
        }),
        def("element.toolkit.contract-pattern", "Toolkit Contract Pattern", "toolkit", "contract-pattern", "Reusable user-job contract such as file basket, picker, browser, diff selector, process table, settings editor, permission matrix, or log explorer.", {
          htmlTag: "mcel-contract-pattern",
          riskPolicy: analysis,
          stateModel: {required: ["intent", "fields", "requires"], optional: ["selection", "safety", "requiredPrimitives"]},
          dataModel: {fieldTypes: ["path", "status", "risk", "datetime", "text", "enum", "action-policy"]},
          decoderHints: ["file basket", "resource browser", "diff selector", "permission matrix", "log explorer", "contract pattern"],
          supersedes: ["slimed per-app widget", "uncontracted DOM view"]
        }),

        def("element.concern.catalog", "Concern Catalog", "concern", "catalog", "Canonical MCEL vocabulary for user-facing responsibilities such as file basket, resource browser, deploy preflight, execution cell, worker routing, output renderer, and process table.", {
          htmlTag: "mcel-concern-catalog",
          riskPolicy: analysis,
          stateModel: {required: ["concernId", "family", "purpose"], optional: ["mvcSplit", "contractPattern"]},
          interactionModel: {mutatesDom: false, ownsTruth: false},
          decoderHints: ["concern catalog", "responsibility vocabulary", "user-facing concern", "contract pattern"],
          supersedes: ["random tag cloud", "ad hoc feature bucket"]
        }),
        def("element.concern.detector", "Concern Detector", "concern", "detector", "Source-aware analyzer that reads project files, collects evidence, scores concerns, and returns contract candidates before visualization is chosen.", {
          htmlTag: "mcel-concern-detector",
          riskPolicy: analysis,
          stateModel: {required: ["projectId", "rules", "evidence"], optional: ["confidence", "detectedConcernCount"]},
          interactionModel: {input: "path/source map", output: "concern map", mutatesDom: false},
          decoderHints: ["concern detector", "source scan", "evidence", "confidence", "project code"],
          supersedes: ["manual vibe check", "style-only audit"]
        }),
        def("element.concern.boundary-map", "Concern Boundary Map", "concern", "boundary-map", "Line-range and role map that shows where a concern crosses model, controller, view, safety, and view-gap boundaries in real code.", {
          htmlTag: "mcel-concern-boundary-map",
          riskPolicy: analysis,
          stateModel: {required: ["ranges", "roles"], optional: ["file", "lineStart", "lineEnd", "boundaryHealth"]},
          interactionModel: {navigateToRange: "inspect-only", mutatesDom: false},
          decoderHints: ["line range", "boundary", "model", "controller", "view", "safety", "view gap"],
          supersedes: ["single warning with no location"]
        }),
        def("element.concern.contract-gap", "Concern Contract Gap", "concern", "contract-gap", "Finding that a page already performs a responsibility but lacks the declared MCEL contract needed to make its data, actions, selection, and safety promises explicit.", {
          htmlTag: "mcel-concern-contract-gap",
          riskPolicy: analysis,
          stateModel: {required: ["concernId", "gapLevel", "reason"], optional: ["confidence", "slimeSignals"]},
          interactionModel: {mutatesDom: false, ownsTruth: false},
          decoderHints: ["missing contract", "contract gap", "slime score", "view-gap", "unsafe coupling"],
          supersedes: ["ugly UI complaint without actionable contract"]
        }),
        def("element.concern.mvc-split", "Concern MVC Split", "concern", "mvc-split", "Recommended model/controller/view allocation for a detected concern, including the user contract that the view must satisfy.", {
          htmlTag: "mcel-concern-mvc-split",
          riskPolicy: analysis,
          stateModel: {required: ["model", "controller", "view", "contract"], optional: ["safety", "toolkit"]},
          interactionModel: {mutatesDom: false, ownsTruth: false},
          decoderHints: ["MVC split", "model", "controller", "view", "contract", "responsibility"],
          supersedes: ["render function owns everything"]
        }),
        def("element.concern.replacement-plan", "Concern Replacement Plan", "concern", "replacement-plan", "Bridge from detected concern to MCEL contract pattern, required toolkit primitives, eligible visualizations, and rejected title-only/view-slime implementations.", {
          htmlTag: "mcel-concern-replacement-plan",
          riskPolicy: analysis,
          stateModel: {required: ["concernId", "recommendedContract", "recommendedToolkit"], optional: ["rejectedViews", "priority"]},
          interactionModel: {mutatesDom: false, ownsTruth: false},
          decoderHints: ["replacement plan", "toolkit primitives", "eligible view", "rejected view", "contract-first"],
          supersedes: ["one-off widget patch"]
        }),
        def("element.concern.project-workbench", "Project Concern Workbench", "concern", "project-workbench", "Turns source-detected concerns into ranked Main Computer migration work orders with target contracts, MVC splits, first safe patches, and proof obligations.", {
          htmlTag: "mcel-project-concern-workbench",
          riskPolicy: analysis,
          stateModel: {required: ["projectId", "workOrders", "migrationQueue"], optional: ["coverageByApp", "coverageByContract", "recommendedNextPatch"]},
          interactionModel: {input: "concern report", output: "migration work orders", mutatesDom: false, ownsTruth: false},
          decoderHints: ["project concern workbench", "migration queue", "work orders", "first safe patch", "project integration spine"],
          supersedes: ["MCEL gallery with no project migration path", "manual UI debt spreadsheet"]
        }),
        def("element.concern.work-order", "Concern Work Order", "concern", "work-order", "Actionable replacement order for one real app concern, including current failure, target contract, required toolkit, eligible views, rejected views, and proof plan.", {
          htmlTag: "mcel-concern-work-order",
          riskPolicy: analysis,
          stateModel: {required: ["concernId", "targetContract", "priority", "firstSafeMigration"], optional: ["sourceFile", "lineEvidence", "testsNeeded"]},
          interactionModel: {mutatesDom: false, ownsTruth: false, handoff: "developer-migration"},
          decoderHints: ["work order", "target contract", "first safe migration", "proof obligations", "line evidence"],
          supersedes: ["vague refactor note", "one-off patch request"]
        }),
        def("element.concern.migration-queue", "Concern Migration Queue", "concern", "migration-queue", "Priority-ordered queue of MCEL project migrations ranked by contract gap, risk, MVC tangling, and toolkit readiness.", {
          htmlTag: "mcel-concern-migration-queue",
          riskPolicy: analysis,
          stateModel: {required: ["rankedWorkOrders"], optional: ["priorityScore", "recommendedNextPatch", "coverage"]},
          interactionModel: {mutatesDom: false, ownsTruth: false},
          decoderHints: ["priority queue", "migration roadmap", "recommended next patch", "contract gap severity"],
          supersedes: ["random next component", "nibbling without roadmap"]
        }),
        def("element.concern.proof-plan", "Concern Proof Plan", "concern", "proof-plan", "Test and proof obligations required before replacing a slimed UI region with an MCEL contract implementation.", {
          htmlTag: "mcel-concern-proof-plan",
          riskPolicy: analysis,
          stateModel: {required: ["testsNeeded", "proofObligations"], optional: ["migrationPhase", "acceptanceCriteria"]},
          interactionModel: {mutatesDom: false, ownsTruth: false},
          decoderHints: ["proof plan", "tests needed", "acceptance criteria", "safe migration"],
          supersedes: ["visual-only demo", "claim of correctness without proof"]
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
            "element.resource.tree-drag-drop-boundary",
            "element.resource.view-contract",
            "element.resource.selection-contract",
            "element.resource.contract-treegrid",
            "element.resource.file-basket-model"
          ],
          scrollPolicy: "owns-tree-viewport-scroll",
          layoutLaws: ["tree-owns-one-scrollport", "active-row-remains-visible", "indentation-preserves-readable-labels", "long-names-ellipsis-not-overlap", "side-pane-does-not-steal-tree-scrollport", "no-illegal-nested-scrollbars"],
          actionPolicy: {
            select: "inspect-only",
            preview: "inspect-only",
            expand: "read-boundary",
            collapse: "inspect-only",
            keyboardNavigate: "inspect-only",
            changeView: "inspect-only",
            sort: "inspect-only",
            group: "inspect-only",
            togglePreviewPane: "inspect-only",
            toggleDetailsPane: "inspect-only",
            drag: "no-submit",
            drop: "no-submit",
            delete: "no-click",
            rename: "no-submit",
            move: "no-submit"
          },
          stateModel: {
            required: ["activeNodeId", "expandedNodeIds", "selectedNodeIds"],
            optional: ["focusedNodeId", "visibleWindow", "rootId", "path", "viewMode", "sortKey", "groupingMode", "previewPaneOpen", "detailsPaneOpen", "mvcModel", "viewContract", "selectionContract", "eligibleViewIds"],
            selection: "single-or-multi-explicit",
            expansion: "stable-id-set",
            viewModes: ["extra-large-icons", "large-icons", "medium-icons", "small-icons", "list", "details", "tiles", "content", "finder-gallery", "finder-column", "gnome-grid", "gnome-list", "dolphin-split", "thunar-compact"]
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
            rowPayload: ["name", "kind", "relativePath", "pathDisplay", "bytes", "mtime", "category", "icon", "thumbnail", "previewSnippet", "statusDecorations"],
            contractPayload: ["fields", "fieldTypes", "selectionContract", "safetyContract", "requiredViewCapabilities", "selectedOutput"]
          },
          migrationHints: {
            fromWunderbaum: ["node.key -> stable resource id", "node.type -> branch/leaf kind", "node.data.fileExplorerEntry -> resource payload", "activate/select/click -> preview", "dblclick/Enter on directory -> safe navigation"],
            replace: ["Wunderbaum constructor", "wb-node-list", "wb-list-container", "file-explorer-wunderbaum-host"]
          },
          proofFixtures: ["selecting a file previews metadata only", "expanding a folder does not write", "delete/move/rename/drop are absent or blocked", "changing view mode preserves active selection and preview target", "long resource names truncate instead of overlapping adjacent panes"],
          presentationModes: ["explorer-sidebar", "ide-project-tree", "details-treegrid", "miller-columns", "outline-tree", "accessibility-proof", "extra-large-icons", "large-icons", "medium-icons", "small-icons", "list", "tiles", "content", "finder-gallery", "finder-column-inspector", "gnome-grid", "gnome-list", "dolphin-split-details", "thunar-compact", "details-pane", "preview-pane"],
          viewPatterns: ["dense navigation tree", "project/file tree with decorations", "hierarchical details table", "column browser", "semantic outline", "keyboard/selection proof", "thumbnail/icon grid", "compact list", "tile cards", "content rows", "Finder gallery", "Finder columns with inspector", "GNOME grid", "GNOME list", "Dolphin split details", "Thunar compact columns", "details side pane", "preview side pane"],
          densityModes: ["compact", "comfortable", "touch", "icon-small", "icon-medium", "icon-large", "icon-extra-large", "finder-gallery", "gnome-adwaita", "dolphin-split", "thunar-compact"],
          decoderHints: ["tree", "directory", "roots", "folder", "file", "wunderbaum", "wb-node-list", "wb-list-container", "file-explorer-wunderbaum-host", "Finder", "Gallery", "Column View", "GNOME Files", "Nautilus", "Dolphin", "Thunar"],
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
        def("element.resource.view-contract", "Resource View Contract", "resource", "view-contract", "Model-owned contract that declares required fields, field types, user intent, safety guarantees, and required visualization capabilities before MCEL offers a view.", {
          allowedChildren: ["element.core.mvc-model", "element.core.mvc-controller", "element.core.mvc-view", "element.resource.selection-contract", "element.resource.contract-treegrid"],
          riskPolicy: analysis,
          stateModel: {
            required: ["intent", "fields", "selectionContract", "safetyContract", "requiredViewCapabilities"],
            optional: ["eligibleViews", "rejectedViews", "explanation"]
          },
          dataModel: {
            fields: ["path", "status", "bucket", "risk", "source", "reason", "modified", "selectable"],
            fieldTypes: ["path", "enum", "risk", "text", "datetime", "boolean"],
            selectedOutput: "explicit-file-paths"
          },
          interactionModel: {choosesView: "by-capability", rejectsUnsupportedViews: true},
          accessibility: {announces: ["chosen view reason", "rejected view reason"]},
          decoderHints: ["contract", "view requirements", "fields", "columns", "user intent", "view resolver"],
          supersedes: ["view-first widget", "title-only tree", "slimed together file basket"]
        }),
        def("element.resource.selection-contract", "Resource Selection Contract", "resource", "selection-contract", "Hierarchical selection rule set that defines exactly what a selected row means, including directory shortcuts, mixed state, blocked rows, and explicit selected-file output.", {
          riskPolicy: safe,
          stateModel: {
            required: ["mode", "selectedOutput", "selectableKinds", "blockedRowsVisible"],
            optional: ["mixedDirectoryState", "directoryBehavior", "blockedReason"]
          },
          dataModel: {
            mode: "hierarchical-explicit-files",
            output: "explicit-file-paths",
            directoryBehavior: "directory toggles selectable descendant files",
            blockedRowsVisible: true,
            blockedRowsSelectable: false
          },
          interactionModel: {toggleFile: "derive explicit file path set", toggleDirectory: "derive descendant file path set", blockedToggle: "no-op with explanation"},
          accessibility: {role: "treegrid selection model", requiredStates: ["aria-selected", "aria-disabled"]},
          decoderHints: ["selection", "checkbox", "tri-state", "mixed", "selected files", "blocked row"],
          supersedes: ["DOM checkbox truth", "folder checkbox with unclear output"]
        }),
        def("element.resource.contract-treegrid", "Contract Treegrid View", "resource", "contract-treegrid", "Hierarchy-plus-details renderer for contract-first resource baskets that can show many typed columns while preserving tri-state selection and explicit selected-output proof.", {
          allowedChildren: ["element.resource.tree-branch", "element.resource.tree-leaf", "element.resource.resource-row"],
          riskPolicy: safe,
          scrollPolicy: "owns-treegrid-scroll",
          layoutLaws: ["multi-column-fields-do-not-collapse-into-title", "treegrid-owns-one-scrollport", "selection-column-remains-visible", "selection-controls-look-like-checkboxes", "blocked-rows-visible-not-selectable", "resizable-columns-use-edge-grips", "resizable-columns-stay-inside-treegrid", "no-illegal-nested-scrollbars"],
          actionPolicy: {select: "inspect-only", expand: "inspect-only", collapse: "inspect-only", resizeColumn: "inspect-only", sort: "inspect-only", filter: "inspect-only", preview: "inspect-only", delete: "no-click", rename: "no-submit", move: "no-submit"},
          stateModel: {required: ["columns", "rows", "selectionContract"], optional: ["expandedNodeIds", "columnWidths", "sortKey", "filter", "selectedOutputPreview"]},
          interactionModel: {click: "dispatch-to-controller", keyboard: ["ArrowKeys", "Space", "Enter", "column-resize-keyboard-nudge"], pointer: ["column-resize-edge-grip"], rejected: ["business-rule-in-view"]},
          accessibility: {role: "treegrid", requiredAttributes: ["aria-level", "aria-selected", "aria-disabled"]},
          dataModel: {requires: ["hierarchy", "multi-column metadata", "field type metadata", "tri-state selection", "legible checkbox controls", "blocked rows visible, not selectable", "selected output preview", "interactive expand/collapse", "resizable columns", "keyboard resizable columns"]},
          decoderHints: ["treegrid", "details", "columns", "resizable columns", "edge resize grip", "checkbox selection", "expand collapse", "file basket", "contract-first", "explicit-file-paths"],
          supersedes: ["single title column", "Wunderbaum title metadata smear"]
        }),
        def("element.resource.file-basket-model", "File Basket Model Adapter", "resource", "file-basket-model", "Pure contract model for Task Manager/Git file baskets: fields, repo-relative identity, hierarchy, selectable state, blocked reason, and selected-output proof before any renderer is replaced.", {
          allowedChildren: ["element.resource.view-contract", "element.resource.selection-contract", "element.resource.contract-treegrid"],
          riskPolicy: analysis,
          scrollPolicy: "no-owned-scroll",
          layoutLaws: ["model-does-not-render", "typed-fields-remain-structured", "blocked-rows-visible-not-selectable", "selected-output-is-explicit-file-paths"],
          stateModel: {required: ["fields", "rows", "hierarchy", "selectionContract", "safetyContract"], optional: ["defaultSelectedPaths", "blockedPaths", "viewContract", "invalidCandidates"]},
          interactionModel: {mutatesDom: false, ownsTruth: true, handoff: "controller-selection"},
          dataModel: {requires: ["path", "status", "bucket", "risk", "reason", "modified", "blockedReason"]},
          decoderHints: ["file basket model", "pure adapter", "first safe migration", "selected output", "blocked rows", "contract boundary"],
          supersedes: ["view-specific selection scraping", "typed metadata flattened into node title"]
        }),
        def("element.resource.view-mode-controller", "Resource View Mode Controller", "resource", "view-controller", "Cross-platform view menu that switches Windows Explorer icon/list/details/tiles/content, macOS Finder gallery/columns, GNOME/KDE/XFCE Linux layouts, sort/group state, and side-pane visibility without mutating resources.", {
          riskPolicy: safe,
          actionPolicy: {
            extraLargeIcons: "inspect-only",
            largeIcons: "inspect-only",
            mediumIcons: "inspect-only",
            smallIcons: "inspect-only",
            list: "inspect-only",
            details: "inspect-only",
            tiles: "inspect-only",
            content: "inspect-only",
            finderGallery: "inspect-only",
            finderColumn: "inspect-only",
            gnomeGrid: "inspect-only",
            gnomeList: "inspect-only",
            dolphinSplit: "inspect-only",
            thunarCompact: "inspect-only",
            sort: "inspect-only",
            group: "inspect-only",
            toggleDetailsPane: "inspect-only",
            togglePreviewPane: "inspect-only"
          },
          stateModel: {required: ["viewMode"], optional: ["iconSize", "sortKey", "groupBy", "detailsPaneOpen", "previewPaneOpen", "density", "platformStyle", "splitPaneOpen", "inspectorOpen"]},
          interactionModel: {mouse: ["select-view-mode", "toggle-pane"], keyboard: ["Alt+V", "ArrowKeys", "Enter", "Space"]},
          accessibility: {role: "toolbar", announces: ["current view mode", "pane visibility"]},
          decoderHints: ["View", "Extra large icons", "Large icons", "Medium icons", "Small icons", "List", "Details", "Tiles", "Content", "Finder Gallery", "Finder Columns", "GNOME Grid", "GNOME List", "Dolphin Split", "Thunar Compact", "Preview pane", "Details pane"],
          supersedes: ["Explorer View menu", "Finder view picker", "GNOME Files view switcher", "Dolphin split/details view", "Thunar compact view", "one-off grid/list toggles"]
        }),
        def("element.resource.icon-grid", "Resource Icon Grid", "resource", "icon-grid", "Thumbnail/icon resource browser with selectable items, stable preview coupling, and no-mutation context.", {
          allowedChildren: ["element.resource.resource-row", "element.core.preview-pane"],
          riskPolicy: safe,
          scrollPolicy: "owned-icon-grid-scroll",
          layoutLaws: ["icons-wrap-without-horizontal-scroll", "label-clamp-preserves-hit-target", "selection-ring-does-not-shift-layout"],
          stateModel: {required: ["viewMode", "selectedNodeIds"], optional: ["iconSize", "visibleWindow", "previewNodeId"]},
          interactionModel: {click: "select-preview", dblclick: "preview-or-open-directory-read-only", keyboard: ["ArrowKeys", "Home", "End", "Enter"]},
          decoderHints: ["extra large icons", "large icons", "medium icons", "small icons", "thumbnail", "gallery", "tile grid", "Finder Gallery", "GNOME grid", "Adwaita grid"],
          supersedes: ["Explorer icon view", "Finder icon/gallery view", "GNOME Files grid", "thumbnail grid", "handmade file card grid"]
        }),
        def("element.resource.details-pane", "Resource Details Pane", "resource", "details-pane", "Read-only side pane for selected resource metadata, provenance, preview policy, and blocked mutation summary.", {
          riskPolicy: inspectOnly,
          proofPolicy: "inspect-only",
          scrollPolicy: "owned-details-pane-scroll",
          stateModel: {required: ["selectedNodeId"], optional: ["properties", "policy", "provenance", "previewState"]},
          interactionModel: {click: "inspect-only", editProperties: "no-submit"},
          decoderHints: ["Details pane", "properties", "metadata", "preview", "selected file", "info pane"],
          supersedes: ["Explorer details pane", "preview/details sidebar", "ad hoc inspector panel"]
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
