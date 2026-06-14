    (function registerTaskManagerMcelAdapter(global) {
      "use strict";

      const ENRICHMENT_STYLE_ID = "mcel-lab-canonical-task-manager-enrichment-style";
      const ENRICHMENT_CLASS = "mcel-canonical-task-manager-enriched";
      const SUPERCUT_TRANSLATOR = "mcel-supercut-task-manager-v0.2";
      const SUPERCUT_MAX_COMPONENTS = 260;

      const REGION_ENRICHMENT = [
        {selector: "#task-manager-app", role: "operator-console", kind: "app", layout: "sidebar-workspace", fitContext: "root"},
        {selector: ".task-manager-shell", role: "sidebar-workspace-shell", kind: "layout", layout: "sidebar-workspace", fitContext: "structural"},
        {selector: ".task-manager-sidebar", role: "command-status-rail", kind: "region", region: "sidebar", fitContext: "constrained", widthPolicy: "compact-fixed"},
        {selector: ".task-manager-detail", role: "primary-workspace", kind: "region", region: "workspace", fitContext: "expansive", widthPolicy: "fluid"}
      ];

      const COMPONENT_ENRICHMENT = [
        {selector: ".task-overview-card", role: "status-summary-card", kind: "card", fit: "wrap-text-no-scroll-trap"},
        {selector: "#task-manager-status", role: "status-line", kind: "text", fit: "wrap-status"},
        {selector: "#task-manager-server", role: "server-snapshot-text", kind: "text", fit: "wrap-no-scroll-trap"},
        {selector: ".task-controls-card", role: "command-control-card", kind: "card", fit: "compact-control-card"},
        {selector: ".task-controls-card .task-inline-grid", role: "control-group", kind: "form", fit: "compact-controls", layoutPolicy: "primary-full-row-secondary-checkbox-grid"},
        {selector: ".task-inline-check", role: "checkbox-control", kind: "control", fit: "fixed-input-shrink-label"},
        {selector: ".task-schedule-card", role: "deferred-command-form", kind: "form", fit: "compact-form"},
        {selector: ".task-schedule-card label", role: "field-row", kind: "form-field", fit: "compact-field-row"},
        {selector: ".task-schedule-list", role: "schedule-feed", kind: "feed", fit: "bounded-list"},
        {selector: ".task-notebook", role: "tabbed-data-feed", kind: "feed", fit: "expansive-scroll"},
        {selector: ".task-tab-button", role: "feed-tab", kind: "navigation", fit: "wrap-tabs"},
        {selector: ".task-grid-scroll", role: "data-grid-scrollport", kind: "scrollport", fit: "intentional-scroll"},
        {selector: ".task-table", role: "data-feed-table", kind: "table", fit: "expansive-table"},
        {selector: ".task-table td code", role: "command-preview", kind: "text-preview", fit: "single-line-ellipsis"},
        {selector: ".task-row-actions", role: "action-cell", kind: "actions", fit: "compact-action-cell"},
        {selector: ".task-ai-toolbar", role: "ai-prompt-toolbar", kind: "ai", fit: "prompt-action-row"},
        {selector: "#task-ai-output", role: "ai-analysis-output", kind: "ai", fit: "wrap-intentional-scroll"}
      ];

      const FIELD_ENRICHMENT = [
        {control: "#task-query", role: "process-filter", priority: "primary"},
        {control: "#task-limit", role: "row-limit", priority: "primary"},
        {control: "#task-include-connections", role: "include-connections", priority: "secondary"},
        {control: "#task-auto-refresh", role: "auto-refresh", priority: "secondary"},
        {control: "#task-schedule-action", role: "scheduled-action", priority: "primary"},
        {control: "#task-schedule-when", role: "scheduled-time", priority: "primary"},
        {control: "#task-schedule-note", role: "schedule-note", priority: "primary"},
        {control: "#task-ai-prompt", role: "ai-prompt", priority: "primary"}
      ];

      const PANEL_LENS = [
        {selector: ".task-overview-card", role: "overview", label: "MCEL: overview", kind: "state"},
        {selector: ".task-controls-card", role: "audited-command-zone", label: "MCEL: audited command zone", kind: "actions"},
        {selector: ".task-schedule-card", role: "scheduler", label: "MCEL: scheduler", kind: "mutation"},
        {selector: ".task-notebook", role: "live-data-notebook", label: "MCEL: live data notebook", kind: "feed"},
        {selector: "#task-panel-processes", role: "server-process-feed", label: "MCEL: server process feed", kind: "feed"},
        {selector: "#task-panel-all-processes", role: "all-process-feed", label: "MCEL: all process feed", kind: "feed"},
        {selector: "#task-panel-connections", role: "connection-feed", label: "MCEL: connection feed", kind: "feed"},
        {selector: "#task-panel-hardware", role: "hardware-feed", label: "MCEL: hardware feed", kind: "feed"},
        {selector: ".task-ai-toolbar", role: "ai-command-surface", label: "MCEL: AI command surface", kind: "ai"},
        {selector: "[data-widget-label=\"Task AI Brief\"]", role: "ai-brief", label: "MCEL: AI operations brief", kind: "ai"}
      ];

      const ACTION_LENS = [
        {selector: "#task-refresh", risk: "safe", role: "refresh-query", label: "safe refresh"},
        {selector: "#task-server-shutdown", risk: "destructive", role: "server-shutdown", label: "destructive server command"},
        {selector: "#task-server-start", risk: "operational", role: "server-start", label: "operational server command"},
        {selector: "#task-server-restart", risk: "disruptive", role: "server-restart", label: "disruptive server command"},
        {selector: "#task-schedule-create", risk: "deferred-mutation", role: "schedule-create", label: "scheduled operation"},
        {selector: "#task-schedules-refresh", risk: "safe", role: "schedule-refresh", label: "safe schedule refresh"},
        {selector: "#task-ai-analyze", risk: "analysis", role: "ai-analysis", label: "AI analysis request"},
        {selector: "[data-task-action=\"terminate-pid\"]", risk: "process-destructive", role: "terminate-pid", label: "process termination"},
        {selector: "[data-task-action=\"kill-pid\"]", risk: "process-destructive", role: "kill-pid", label: "process kill"}
      ];

      function ensureEnrichmentStyle(doc) {
        if (!doc?.head) return false;
        let style = doc.getElementById(ENRICHMENT_STYLE_ID);
        if (style) return true;
        style = doc.createElement("style");
        style.id = ENRICHMENT_STYLE_ID;
        style.textContent = `
          body[data-mcel-task-enrichment="active"] [data-mcel-role],
          body[data-mcel-task-enrichment="active"] [data-mcel-region],
          body[data-mcel-task-enrichment="active"] [data-mcel-fit] {
            box-sizing: border-box !important;
            min-width: 0 !important;
            max-width: 100%;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-layout-region="sidebar-workspace-shell"] {
            display: grid !important;
            grid-template-columns: var(--mcel-task-sidebar-width, 300px) minmax(0, 1fr) !important;
            gap: 8px !important;
            align-items: stretch !important;
            overflow: hidden !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-region="command-status-rail"] {
            width: var(--mcel-task-sidebar-width, 300px) !important;
            max-width: var(--mcel-task-sidebar-width, 300px) !important;
            display: flex !important;
            flex-direction: column !important;
            gap: 8px !important;
            overflow: auto !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-region="primary-workspace"] {
            min-width: 0 !important;
            display: grid !important;
            grid-template-rows: minmax(0, 1fr) auto minmax(120px, 18vh) !important;
            gap: 8px !important;
            overflow: hidden !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-fit="wrap-no-scroll-trap"] {
            max-height: none !important;
            height: auto !important;
            overflow: visible !important;
            white-space: pre-wrap !important;
            overflow-wrap: anywhere !important;
            word-break: break-word !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-fit="compact-controls"] {
            display: grid !important;
            grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) !important;
            gap: 7px !important;
            align-items: stretch !important;
            width: 100% !important;
            min-width: 0 !important;
            max-width: 100% !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-control-priority="primary"] {
            grid-column: 1 / -1 !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-fit="fixed-input-shrink-label"] {
            display: grid !important;
            grid-template-columns: 16px minmax(0, 1fr) !important;
            align-items: center !important;
            gap: 6px !important;
            width: 100% !important;
            min-width: 0 !important;
            max-width: 100% !important;
            overflow: hidden !important;
            white-space: nowrap !important;
            text-overflow: ellipsis !important;
            line-height: 1.1 !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-fit="fixed-input-shrink-label"] input[type="checkbox"] {
            width: 16px !important;
            height: 16px !important;
            min-width: 16px !important;
            max-width: 16px !important;
            margin: 0 !important;
            justify-self: start !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-fit="compact-field-row"] {
            display: grid !important;
            grid-template-columns: minmax(76px, 0.35fr) minmax(0, 1fr) !important;
            gap: 8px !important;
            align-items: center !important;
            width: 100% !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-fit="compact-field-row"] input,
          body[data-mcel-task-enrichment="active"] [data-mcel-fit="compact-field-row"] select,
          body[data-mcel-task-enrichment="active"] [data-mcel-fit="compact-field-row"] textarea {
            width: 100% !important;
            min-width: 0 !important;
            max-width: 100% !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-fit="single-line-ellipsis"] {
            display: block !important;
            width: 100% !important;
            max-width: 100% !important;
            min-width: 0 !important;
            max-height: none !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            white-space: nowrap !important;
            word-break: normal !important;
            overflow-wrap: normal !important;
            line-height: 1.25 !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-fit="compact-action-cell"] {
            min-width: 0 !important;
            max-width: 100% !important;
            width: 100% !important;
            grid-template-columns: minmax(0, 1fr) !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-fit="compact-action-cell"] button,
          body[data-mcel-task-enrichment="active"] [data-mcel-fit="compact-action-cell"] .task-pill {
            width: 100% !important;
            min-width: 0 !important;
            max-width: 100% !important;
            white-space: normal !important;
          }

          body[data-mcel-task-enrichment="active"] [data-mcel-fit="prompt-action-row"] {
            min-width: 0 !important;
          }
        `;
        doc.head.appendChild(style);
        return true;
      }

      function applyElementEnrichment(element, definition, options = {}) {
        if (!element || !definition) return false;
        const enrichedBy = options.enrichedBy || "task-manager-adapter";
        const source = options.source || "legacy-dom-reader";
        element.setAttribute("data-mcel-role", definition.role);
        element.setAttribute("data-mcel-kind", definition.kind || "surface");
        if (definition.fit) element.setAttribute("data-mcel-fit", definition.fit);
        if (definition.fitContext) element.setAttribute("data-mcel-fit-context", definition.fitContext);
        if (definition.layout) element.setAttribute("data-mcel-layout", definition.layout);
        if (definition.layoutPolicy) element.setAttribute("data-mcel-layout-policy", definition.layoutPolicy);
        if (definition.region) element.setAttribute("data-mcel-region", definition.role);
        if (definition.widthPolicy) element.setAttribute("data-mcel-width-policy", definition.widthPolicy);
        element.setAttribute("data-mcel-enriched", enrichedBy);
        element.setAttribute("data-mcel-enrichment-source", source);
        return true;
      }

      function runTaskManagerSupercutTranslation(doc, root, options = {}) {
        if (!doc?.body || !root || !global.McelSupercut?.translateRuntime) {
          return {
            active: false,
            translator: SUPERCUT_TRANSLATOR,
            taggedElementCount: 0,
            componentCount: 0,
            executableComponentCount: 0,
            originalPointCount: 0,
            originalPoints: [],
            rectificationRounds: [],
            cssObjectCatalog: [],
            runtimeChanges: [],
            message: "MCEL Supercut translator unavailable or Task Manager root missing"
          };
        }
        return global.McelSupercut.translateRuntime({
          document: doc,
          root,
          rootSelector: options.rootSelector || "#task-manager-app",
          app: "task-manager",
          specimenId: "task-manager",
          reason: options.reason || "task-manager-supercut-translation",
          generatedBy: options.generatedBy || "task-manager-mcel-supercut-adapter",
          translator: SUPERCUT_TRANSLATOR,
          maxComponents: SUPERCUT_MAX_COMPONENTS,
          packs: options.packs || ["core-html", "core-action-risk", "task-manager-domain"],
          mode: "tag-and-audit",
          rounds: 3
        });
      }

      function clearTaskManagerSupercutTranslation(doc, rootSelector = "#task-manager-app") {
        return Boolean(global.McelSupercut?.clearRuntime?.({
          document: doc,
          rootSelector
        }));
      }

      function summarizeSupercutRewritePreview(supercut = {}) {
        const summary = supercut.rewritePreviewSummary || {};
        const rewritePreview = supercut.rewritePreview || [];
        if (summary && Object.keys(summary).length) return summary;
        return rewritePreview.reduce((memo, node) => {
          if (node.contract === "component.root") memo.root += 1;
          else if (node.contract === "component.region") memo.regions += 1;
          else if (node.contract === "component.panel") memo.panels += 1;
          else if (node.contract === "component.toolbar") memo.toolbars += 1;
          else if (node.contract === "component.field") memo.fields += 1;
          else if (["component.action", "component.operational-action", "component.destructive-action", "component.remote-mutation-action"].includes(node.contract)) memo.actions += 1;
          else if (node.contract === "component.status-feed") memo.statusFeeds += 1;
          else if (node.contract === "component.console") memo.consoles += 1;
          else if (node.contract === "component.workflow") memo.workflows += 1;
          else memo.unknown += 1;
          return memo;
        }, {
          root: 0,
          regions: 0,
          panels: 0,
          toolbars: 0,
          fields: 0,
          actions: 0,
          statusFeeds: 0,
          consoles: 0,
          workflows: 0,
          unknown: 0
        });
      }

      function countSupercutProofPolicies(supercut = {}) {
        return (supercut.rewritePreview || []).reduce((memo, node) => {
          const key = node.proofPolicy || "inspect-only";
          memo[key] = (memo[key] || 0) + 1;
          return memo;
        }, {});
      }

      function nearestControlLabel(control) {
        if (!control) return null;
        return control.closest?.("label") || control.parentElement;
      }

      function buildEnrichmentModel(doc, root, options = {}) {
        const reason = options.reason || "build-enrichment";
        const generatedBy = options.generatedBy || "task-manager-mcel-adapter";
        const regions = REGION_ENRICHMENT.map((definition) => {
          const element = doc?.querySelector?.(definition.selector) || null;
          return {
            selector: definition.selector,
            role: definition.role,
            kind: definition.kind,
            region: definition.region || definition.role,
            fitContext: definition.fitContext || "",
            widthPolicy: definition.widthPolicy || "",
            layout: definition.layout || "",
            present: Boolean(element),
            inferredFrom: definition.selector === ".task-manager-sidebar"
              ? ["dom-class", "first-shell-child", "constrained-width", "control/status/form-descendants"]
              : definition.selector === ".task-manager-detail"
                ? ["dom-class", "second-shell-child", "expansive-data-workspace-descendants"]
                : ["dom-contract-selector"]
          };
        });

        const components = COMPONENT_ENRICHMENT.map((definition) => {
          const elements = Array.from(doc?.querySelectorAll?.(definition.selector) || []);
          return {
            selector: definition.selector,
            role: definition.role,
            kind: definition.kind,
            fit: definition.fit || "",
            layoutPolicy: definition.layoutPolicy || "",
            count: elements.length,
            present: elements.length > 0
          };
        });

        const fields = FIELD_ENRICHMENT.map((definition) => {
          const control = doc?.querySelector?.(definition.control) || null;
          return {
            selector: definition.control,
            role: definition.role,
            priority: definition.priority,
            controlTag: control?.tagName?.toLowerCase?.() || "",
            present: Boolean(control)
          };
        });

        const actions = ACTION_LENS.map((definition) => {
          const count = Array.from(doc?.querySelectorAll?.(definition.selector) || []).length;
          return {
            selector: definition.selector,
            role: definition.role,
            risk: definition.risk,
            label: definition.label,
            count,
            present: count > 0
          };
        });

        return {
          app: "task-manager",
          kind: "operator-console",
          layout: "sidebar-workspace",
          rootSelector: options.rootSelector || "#task-manager-app",
          rootPresent: Boolean(root),
          regions,
          components,
          fields,
          actions,
          generatedBy,
          reason,
          builtAt: new Date().toISOString(),
          laws: [
            "structural containers preserve app geometry",
            "constrained regions use compact leaf-control fit policies",
            "checkbox controls reserve a fixed input slot and shrinkable label slot",
            "status text avoids accidental internal scroll traps",
            "command previews clip intentionally on one line",
            "destructive action surfaces are classified but never executed"
          ]
        };
      }

      function collectEnrichmentViolations(doc, root) {
        if (!doc || !root) return [{law: "root-present", status: "failed", message: "Task Manager root unavailable"}];
        const violations = [];
        const constrainedRegion = root.querySelector?.("[data-mcel-region=\"command-status-rail\"]");
        const constrainedWidth = constrainedRegion?.getBoundingClientRect?.().width || 0;

        Array.from(root.querySelectorAll?.("[data-mcel-fit], [data-mcel-role]") || []).forEach((element) => {
          const rect = element.getBoundingClientRect?.();
          const parentRect = element.parentElement?.getBoundingClientRect?.();
          if (!rect || rect.width <= 0 || rect.height <= 0) return;
          const fit = element.getAttribute("data-mcel-fit") || "";
          const role = element.getAttribute("data-mcel-role") || "";
          const intentionalOverflow = ["intentional-scroll", "expansive-scroll", "expansive-table", "single-line-ellipsis", "wrap-intentional-scroll"].includes(fit);
          if (!intentionalOverflow && element.scrollWidth - element.clientWidth > 1) {
            violations.push({
              law: "horizontal-containment",
              role,
              fit,
              id: element.id || "",
              selector: element.getAttribute("data-mcel-enrichment-selector") || "",
              delta: Math.ceil(element.scrollWidth - element.clientWidth)
            });
          }
          if (parentRect && rect.right > parentRect.right + 1 && !intentionalOverflow) {
            violations.push({
              law: "parent-boundary",
              role,
              fit,
              id: element.id || "",
              selector: element.getAttribute("data-mcel-enrichment-selector") || "",
              delta: Math.ceil(rect.right - parentRect.right)
            });
          }
        });

        Array.from(root.querySelectorAll?.("[data-mcel-fit=\"fixed-input-shrink-label\"]") || []).forEach((element) => {
          const checkbox = element.querySelector?.("input[type=\"checkbox\"]");
          const rect = element.getBoundingClientRect?.();
          if (!checkbox) {
            violations.push({law: "checkbox-slot", role: "checkbox-control", status: "failed", message: "missing checkbox input"});
          }
          if (constrainedWidth && rect?.width && rect.width > constrainedWidth) {
            violations.push({law: "checkbox-containment", role: "checkbox-control", status: "failed", width: Math.ceil(rect.width), constrainedWidth: Math.ceil(constrainedWidth)});
          }
        });

        return violations.slice(0, 32);
      }

      function createUnavailableReport(options = {}) {
        const reason = options.reason || "enrichment";
        const message = options.message || "document unavailable";
        return {
          app: "task-manager",
          rootSelector: options.rootSelector || "#task-manager-app",
          enrichmentActive: false,
          rootPresent: false,
          enrichedElementCount: 0,
          layoutLawStatus: "unavailable",
          violations: [{law: options.law || "document", status: "failed", message}],
          destructiveActionsExecuted: false,
          safetyClaim: "enrichment reads and annotates the specimen DOM; it never clicks Task Manager controls",
          reason,
          appliedAt: new Date().toISOString()
        };
      }

      function applyTaskManagerMcelSemantics(options = {}) {
        const doc = options.document || global.document;
        const reason = options.reason || "enrichment";
        const rootSelector = options.rootSelector || "#task-manager-app";
        const route = options.route || "";
        const enrichedBy = options.enrichedBy || (options.mode === "lab-specimen" ? "task-manager-lab" : "task-manager-adapter");
        const source = options.source || "legacy-dom-reader";
        const generatedBy = options.generatedBy || (options.mode === "lab-specimen" ? "mcel-lab-legacy-dom-enrichment" : "task-manager-mcel-adapter");
        const proofSurface = options.proofSurface || (options.mode === "lab-specimen" ? "canonical-app-specimen" : "task-manager-app");
        const sidebarWidth = options.sidebarWidth || "300px";

        if (!doc?.body) {
          return createUnavailableReport({
            rootSelector,
            reason,
            law: "document",
            message: "Task Manager document unavailable"
          });
        }

        ensureEnrichmentStyle(doc);
        const root = doc.querySelector?.(rootSelector) || null;
        doc.documentElement?.setAttribute?.("data-mcel-task-enrichment", "active");
        doc.body.setAttribute("data-mcel-task-enrichment", "active");
        doc.body.classList.add(ENRICHMENT_CLASS);
        doc.body.style.setProperty("--mcel-task-sidebar-width", sidebarWidth);

        let enrichedElementCount = 0;
        if (root) {
          root.setAttribute("data-mcel-app", "task-manager");
          root.setAttribute("data-mcel-kind", "operator-console");
          root.setAttribute("data-mcel-layout", "sidebar-workspace");
          root.setAttribute("data-mcel-enriched", enrichedBy);
          root.setAttribute("data-mcel-enrichment-source", source);
          root.setAttribute("data-mcel-enrichment-state", "active");
          root.setAttribute("data-mcel-proof-surface", proofSurface);
          root.setAttribute("data-mcel-component-id", "canonical.task-manager.root");
          enrichedElementCount += 1;
        }

        REGION_ENRICHMENT.forEach((definition) => {
          Array.from(doc.querySelectorAll?.(definition.selector) || []).forEach((element) => {
            applyElementEnrichment(element, definition, {enrichedBy, source});
            element.setAttribute("data-mcel-enrichment-selector", definition.selector);
            if (definition.kind === "layout") {
              element.setAttribute("data-mcel-layout-region", definition.role);
            }
            if (definition.region) {
              element.setAttribute("data-mcel-region-kind", definition.region);
              element.setAttribute("data-mcel-region", definition.role);
            }
            enrichedElementCount += 1;
          });
        });

        COMPONENT_ENRICHMENT.forEach((definition) => {
          Array.from(doc.querySelectorAll?.(definition.selector) || []).forEach((element) => {
            applyElementEnrichment(element, definition, {enrichedBy, source});
            element.setAttribute("data-mcel-enrichment-selector", definition.selector);
            enrichedElementCount += 1;
          });
        });

        FIELD_ENRICHMENT.forEach((definition) => {
          const control = doc.querySelector?.(definition.control) || null;
          if (!control) return;
          control.setAttribute("data-mcel-control-role", definition.role);
          control.setAttribute("data-mcel-control-priority", definition.priority);
          control.setAttribute("data-mcel-enrichment-selector", definition.control);
          const label = nearestControlLabel(control);
          if (label) {
            label.setAttribute("data-mcel-role", "field-control");
            label.setAttribute("data-mcel-kind", "control");
            label.setAttribute("data-mcel-control-role", definition.role);
            label.setAttribute("data-mcel-control-priority", definition.priority);
            label.setAttribute("data-mcel-enriched", enrichedBy);
          }
          enrichedElementCount += label ? 2 : 1;
        });

        ACTION_LENS.forEach((action) => {
          Array.from(doc.querySelectorAll?.(action.selector) || []).forEach((element) => {
            element.setAttribute("data-mcel-role", "action-surface");
            element.setAttribute("data-mcel-action-role", action.role);
            element.setAttribute("data-mcel-action-risk", action.risk);
            element.setAttribute("data-mcel-action-label", action.label);
            element.setAttribute("data-mcel-mutates", action.risk === "safe" || action.risk === "analysis" ? "false" : "potential");
            element.setAttribute("data-mcel-enriched", enrichedBy);
            element.setAttribute("data-mcel-enrichment-selector", action.selector);
            enrichedElementCount += 1;
          });
        });

        const supercut = runTaskManagerSupercutTranslation(doc, root, {reason, rootSelector, generatedBy});
        if (supercut.active) {
          enrichedElementCount += supercut.taggedElementCount || 0;
        }

        const model = buildEnrichmentModel(doc, root, {reason, rootSelector, generatedBy, supercut});
        const violations = collectEnrichmentViolations(doc, root);
        return {
          ...model,
          route,
          enrichmentActive: Boolean(root),
          enrichedElementCount,
          regionCount: model.regions.filter((item) => item.present).length,
          componentCount: model.components.reduce((total, item) => total + item.count, 0),
          fieldCount: model.fields.filter((item) => item.present).length,
          actionControlCount: model.actions.reduce((total, item) => total + item.count, 0),
          riskControlCount: model.actions.filter((item) => item.present && !["safe", "analysis"].includes(item.risk)).reduce((total, item) => total + item.count, 0),
          supercutActive: Boolean(supercut.active),
          supercutTranslator: supercut.translator || SUPERCUT_TRANSLATOR,
          supercutTaggedElementCount: supercut.taggedElementCount || 0,
          supercutComponentCount: supercut.componentCount || 0,
          supercutExecutableCount: supercut.executableComponentCount || 0,
          supercutOriginalPointCount: supercut.originalPointCount || 0,
          supercutOriginalPoints: supercut.originalPoints || [],
          supercutRoundCount: supercut.rectificationRounds?.length || 0,
          supercutRectificationRounds: supercut.rectificationRounds || [],
          supercutCssObjectCount: supercut.cssObjectCatalog?.length || 0,
          supercutRuntimeChanges: supercut.runtimeChanges || [],
          supercutArchitectureStatus: supercut.architectureStatus || "legacy",
          supercutPacksLoaded: supercut.packsLoaded || [],
          supercutPacksLoadedCount: supercut.packsLoaded?.length || 0,
          supercutRulesFired: supercut.rulesFired || 0,
          supercutBlackboardRecordCount: supercut.blackboardRecordCount || supercut.blackboard?.records?.length || 0,
          supercutRewritePreview: supercut.rewritePreview || [],
          supercutRewritePreviewCount: supercut.rewritePreview?.length || 0,
          supercutRewritePreviewSummary: summarizeSupercutRewritePreview(supercut),
          supercutExplanationsReady: supercut.explanationsReady || supercut.explanations?.length || 0,
          supercutUnsafeActionsBlocked: supercut.unsafeActionsBlocked || 0,
          supercutProofPolicyCounts: countSupercutProofPolicies(supercut),
          supercutRuleTrace: supercut.ruleTrace || [],
          supercutSourceMutations: supercut.sourceMutations || 0,
          supercutRuntimeSourceMutations: supercut.runtimeSourceMutations || 0,
          fitLawCount: model.components.filter((item) => item.fit).length + (supercut.cssObjectCatalog?.length || 0),
          layoutLawStatus: violations.length ? "warning" : "ready",
          violations,
          enrichmentStyleId: ENRICHMENT_STYLE_ID,
          overlayMode: "semantic enrichment with role/fit attributes; layout repair comes from MCEL fit policies, not text-specific selectors",
          destructiveActionsExecuted: false,
          safetyClaim: "MCEL enrichment reads, annotates, and applies role-based fit policies; it does not click server control, PID termination, or schedule actions",
          appliedAt: new Date().toISOString()
        };
      }

      function clearTaskManagerMcelSemantics(doc, options = {}) {
        if (!doc?.body) return false;
        const rootSelector = options.rootSelector || "#task-manager-app";
        doc.documentElement?.removeAttribute?.("data-mcel-task-enrichment");
        doc.body.removeAttribute("data-mcel-task-enrichment");
        doc.body.classList.remove(ENRICHMENT_CLASS);
        doc.body.style.removeProperty("--mcel-task-sidebar-width");
        if (options.removeStyle !== false) {
          doc.getElementById(ENRICHMENT_STYLE_ID)?.remove?.();
        }
        clearTaskManagerSupercutTranslation(doc, rootSelector);
        Array.from(doc.querySelectorAll?.("[data-mcel-enriched], [data-mcel-enrichment-source], [data-mcel-enrichment-selector], [data-mcel-role], [data-mcel-kind], [data-mcel-fit], [data-mcel-fit-context], [data-mcel-layout], [data-mcel-layout-policy], [data-mcel-layout-region], [data-mcel-region], [data-mcel-region-kind], [data-mcel-width-policy], [data-mcel-control-role], [data-mcel-control-priority], [data-mcel-action-role], [data-mcel-action-risk], [data-mcel-action-label], [data-mcel-mutates], [data-mcel-supercut], [data-mcel-supercut-purpose], [data-mcel-supercut-contract], [data-mcel-supercut-proof-policy], [data-mcel-supercut-rewrite-tag]") || []).forEach((element) => {
          [
            "data-mcel-enriched",
            "data-mcel-enrichment-source",
            "data-mcel-enrichment-selector",
            "data-mcel-role",
            "data-mcel-kind",
            "data-mcel-fit",
            "data-mcel-fit-context",
            "data-mcel-layout",
            "data-mcel-layout-policy",
            "data-mcel-layout-region",
            "data-mcel-region",
            "data-mcel-region-kind",
            "data-mcel-width-policy",
            "data-mcel-control-role",
            "data-mcel-control-priority",
            "data-mcel-action-role",
            "data-mcel-action-risk",
            "data-mcel-action-label",
            "data-mcel-mutates",
            "data-mcel-supercut",
            "data-mcel-supercut-purpose",
            "data-mcel-supercut-contract",
            "data-mcel-supercut-proof-policy",
            "data-mcel-supercut-rewrite-tag"
          ].forEach((attribute) => element.removeAttribute(attribute));
        });
        const root = doc.querySelector?.(rootSelector) || null;
        if (root) {
          [
            "data-mcel-app",
            "data-mcel-enrichment-state",
            "data-mcel-proof-surface",
            "data-mcel-component-id"
          ].forEach((attribute) => root.removeAttribute(attribute));
        }
        return true;
      }

      global.TaskManagerMcel = {
        ENRICHMENT_STYLE_ID,
        ENRICHMENT_CLASS,
        REGION_ENRICHMENT,
        COMPONENT_ENRICHMENT,
        FIELD_ENRICHMENT,
        PANEL_LENS,
        ACTION_LENS,
        ensureEnrichmentStyle,
        applyElementEnrichment,
        nearestControlLabel,
        buildEnrichmentModel,
        collectEnrichmentViolations,
        createUnavailableReport,
        runTaskManagerSupercutTranslation,
        clearTaskManagerSupercutTranslation,
        summarizeSupercutRewritePreview,
        countSupercutProofPolicies,
        applyTaskManagerMcelSemantics,
        clearTaskManagerMcelSemantics
      };
    })(window);
