    (function (global) {
      "use strict";

      const ENRICHMENT_STYLE_ID = "mcel-lab-canonical-git-tools-enrichment-style";
      const ENRICHMENT_CLASS = "mcel-canonical-git-tools-enriched";
      const BODY_ENRICHMENT_ATTRIBUTE = "data-mcel-git-enrichment";

      const REGION_ENRICHMENT = [
        {selector: "#git-tools-app", role: "git-operator-console", kind: "app", layout: "stacked-workflow", fitContext: "root"},
        {selector: ".git-tools-shell", role: "git-workflow-shell", kind: "layout", layout: "stacked-workflow", fitContext: "structural"},
        {selector: ".git-tools-hero", role: "project-intake-region", kind: "region", region: "project-intake", fitContext: "busy-dynamic"},
        {selector: "#git-workflow-accordion", role: "progressive-workflow-region", kind: "region", region: "workflow", fitContext: "busy-dynamic"},
        {selector: "#gitea-workflow-layout", role: "gitea-workflow-grid", kind: "region", region: "workflow-grid", fitContext: "busy-dynamic"}
      ];

      const COMPONENT_ENRICHMENT = [
        {selector: "#git-project-selector-panel", role: "project-selector-panel", kind: "panel", fit: "busy-card"},
        {selector: "#git-project-current", role: "current-project-status", kind: "status", fit: "wrap-status"},
        {selector: "#git-project-next-step", role: "next-step-status", kind: "status", fit: "wrap-status"},
        {selector: "#git-project-dashboard", role: "project-dashboard", kind: "output", fit: "intentional-scroll"},
        {selector: "#git-project-wizard-plan", role: "project-wizard-plan", kind: "output", fit: "intentional-scroll"},
        {selector: "#git-project-list", role: "active-project-list", kind: "list", fit: "bounded-list"},
        {selector: "#git-project-archive-list", role: "archived-project-list", kind: "list", fit: "bounded-list"},
        {selector: "#git-server-pane", role: "git-server-workflow-panel", kind: "panel", fit: "progressive-disclosure"},
        {selector: ".gitea-server-overview", role: "gitea-server-overview", kind: "panel", fit: "busy-card"},
        {selector: ".gitea-publish-workflow", role: "gitea-publish-workflow", kind: "form", fit: "busy-form"},
        {selector: ".gitea-remote-choice-grid", role: "remote-strategy-choice-grid", kind: "input-group", fit: "wrap-choice-cards"},
        {selector: ".gitea-target-settings", role: "remote-target-settings", kind: "form", fit: "progressive-disclosure"},
        {selector: ".gitea-workflow-steps", role: "local-gitea-workflow-steps", kind: "workflow", fit: "step-stack"},
        {selector: ".gitea-workflow-step", role: "local-gitea-workflow-step", kind: "workflow-step", fit: "busy-card"},
        {selector: ".gitea-activity-card", role: "git-operation-activity", kind: "output", fit: "intentional-scroll"},
        {selector: "#git-server-output", role: "git-server-output-log", kind: "output", fit: "wrap-intentional-scroll"},
        {selector: ".gitea-advanced-card", role: "advanced-git-operations", kind: "panel", fit: "progressive-disclosure"},
        {selector: ".git-server-remote-fields", role: "remote-field-grid", kind: "form", fit: "responsive-field-grid"},
        {selector: ".git-tools-actions", role: "git-action-toolbar", kind: "toolbar", fit: "wrap-toolbar"}
      ];

      const FIELD_ENRICHMENT = [
        {control: "#git-project-path", role: "project-path", priority: "primary"},
        {control: "#git-server-remote-mode", role: "remote-mode", priority: "secondary"},
        {control: "#git-server-remote-name", role: "remote-name", priority: "secondary"},
        {control: "#git-server-owner", role: "gitea-owner", priority: "primary"},
        {control: "#git-server-repo", role: "gitea-repository", priority: "primary"},
        {control: "#git-server-remote-protocol", role: "remote-protocol", priority: "secondary"},
        {control: "#git-server-external-remote-name", role: "external-remote-name", priority: "secondary"},
        {control: "#git-server-external-url", role: "external-remote-url", priority: "primary"},
        {control: "#git-server-mirror-url", role: "mirror-url", priority: "primary"},
        {control: "#git-server-mirror-username", role: "mirror-username", priority: "secondary"},
        {control: "#git-server-mirror-password", role: "mirror-token", priority: "sensitive"},
        {control: "#git-server-remote-command", role: "manual-git-command", priority: "dangerous"}
      ];

      const PANEL_LENS = [
        {selector: "#git-project-selector-panel", role: "project-selector", label: "MCEL: project selector", kind: "state"},
        {selector: ".git-project-roster", role: "project-roster", label: "MCEL: project roster", kind: "list"},
        {selector: "#git-workflow-accordion", role: "progressive-workflow", label: "MCEL: progressive workflow", kind: "workflow"},
        {selector: "#git-server-pane", role: "git-server-controls", label: "MCEL: Git/Gitea controls", kind: "actions"},
        {selector: "#gitea-workflow-layout", role: "gitea-workflow-grid", label: "MCEL: Gitea workflow grid", kind: "layout"},
        {selector: ".gitea-server-overview", role: "server-overview", label: "MCEL: server overview", kind: "status"},
        {selector: ".gitea-publish-workflow", role: "publish-workflow", label: "MCEL: publish workflow", kind: "mutation"},
        {selector: ".gitea-activity-card", role: "operation-activity", label: "MCEL: operation activity", kind: "feed"},
        {selector: ".gitea-advanced-card", role: "advanced-operations", label: "MCEL: advanced operations", kind: "mutation"},
        {selector: "#git-server-output", role: "server-output-feed", label: "MCEL: server output feed", kind: "feed"}
      ];

      const ACTION_LENS = [
        {selector: "#git-project-add", risk: "safe", role: "project-add", label: "add local project reference"},
        {selector: "#git-project-rescan", risk: "safe", role: "project-rescan", label: "rescan selected project"},
        {selector: "#git-project-lock", risk: "operational", role: "project-lock", label: "lock selected project"},
        {selector: "#git-project-unlock", risk: "operational", role: "project-unlock", label: "unlock selected project"},
        {selector: "#git-server-open", risk: "external-navigation", role: "open-git-server", label: "open Gitea browser tab"},
        {selector: "#git-server-status-refresh", risk: "safe", role: "refresh-git-server", label: "refresh Git server status"},
        {selector: "#git-server-start", risk: "operational", role: "start-git-server", label: "start local Git server"},
        {selector: "#git-server-restart", risk: "disruptive", role: "restart-git-server", label: "restart local Git server"},
        {selector: "#git-server-stop", risk: "disruptive", role: "stop-git-server", label: "stop local Git server"},
        {selector: "#git-server-logs", risk: "safe", role: "show-git-server-logs", label: "show Git server logs"},
        {selector: "#git-server-use-local", risk: "safe", role: "reset-local-target", label: "reset local Gitea target"},
        {selector: "#git-server-remote-apply-local", risk: "repo-config-mutation", role: "configure-local-remote", label: "create repo and configure remote"},
        {selector: "#git-server-remote-show", risk: "safe", role: "show-remotes", label: "show current remotes"},
        {selector: "#git-server-push-local", risk: "publish-mutation", role: "push-local-gitea", label: "push HEAD to Local Gitea"},
        {selector: "#git-server-operation-cancel", risk: "operational", role: "cancel-git-operation", label: "cancel running Git operation"},
        {selector: "#git-server-operation-refresh", risk: "safe", role: "refresh-operation-log", label: "refresh operation log"},
        {selector: "#git-server-use-external", risk: "repo-config-mutation", role: "use-external-remote", label: "use external remote"},
        {selector: "#git-server-mirror-plan", risk: "safe", role: "plan-push-mirror", label: "plan push mirror"},
        {selector: "#git-server-mirror-setup", risk: "credential-network-mutation", role: "setup-push-mirror", label: "set up push mirror"},
        {selector: "#git-server-remote-add", risk: "repo-config-mutation", role: "add-remote-command", label: "prepare add remote command"},
        {selector: "#git-server-remote-set-url", risk: "repo-config-mutation", role: "set-url-command", label: "prepare set-url command"},
        {selector: "#git-server-remote-push", risk: "publish-mutation", role: "push-head-command", label: "prepare push HEAD command"},
        {selector: "#git-server-remote-fetch", risk: "network-read", role: "fetch-command", label: "prepare fetch command"},
        {selector: "#git-server-remote-run", risk: "command-execution", role: "run-git-command", label: "run manual Git command"},
        {selector: "#git-server-remote-copy-console", risk: "safe", role: "copy-command-to-console", label: "copy command to Git console"}
      ];

      function ensureEnrichmentStyle(doc) {
        if (!doc?.head) return false;
        let style = doc.getElementById(ENRICHMENT_STYLE_ID);
        if (style) return true;
        style = doc.createElement("style");
        style.id = ENRICHMENT_STYLE_ID;
        style.textContent = `
          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-role],
          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-region],
          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-fit] {
            box-sizing: border-box !important;
            min-width: 0 !important;
            max-width: 100%;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-fit="wrap-status"],
          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-fit="wrap-intentional-scroll"] {
            white-space: pre-wrap !important;
            overflow-wrap: anywhere !important;
            word-break: break-word !important;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-fit="wrap-toolbar"] {
            display: flex !important;
            flex-wrap: wrap !important;
            gap: 8px !important;
            min-width: 0 !important;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-fit="responsive-field-grid"] {
            min-width: 0 !important;
            max-width: 100% !important;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-control-priority="primary"] {
            min-width: min(100%, 220px) !important;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-control-priority="sensitive"],
          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-control-priority="dangerous"] {
            outline: 1px dashed rgba(255, 198, 109, 0.22);
            outline-offset: 2px;
          }
        `;
        doc.head.appendChild(style);
        return true;
      }

      function applyElementEnrichment(element, definition, options = {}) {
        if (!element || !definition) return false;
        const enrichedBy = options.enrichedBy || "git-tools-adapter";
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

      function nearestControlLabel(control) {
        if (!control) return null;
        return control.closest?.("label") || control.parentElement;
      }

      function buildEnrichmentModel(doc, root, options = {}) {
        const reason = options.reason || "build-enrichment";
        const generatedBy = options.generatedBy || "git-tools-mcel-adapter";
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
            inferredFrom: definition.selector === "#git-workflow-accordion"
              ? ["dom-id", "progressive-details-sections", "dynamic-git-server-controls"]
              : definition.selector === "#gitea-workflow-layout"
                ? ["dom-id", "busy-workflow-grid", "remote-command-descendants"]
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
          app: "git-tools",
          kind: "repository-operations-console",
          layout: "stacked-progressive-workflow",
          rootSelector: options.rootSelector || "#git-tools-app",
          rootPresent: Boolean(root),
          regions,
          components,
          fields,
          actions,
          generatedBy,
          reason,
          builtAt: new Date().toISOString(),
          laws: [
            "busy dynamic Git/Gitea controls keep progressive disclosure boundaries",
            "remote and mirror fields are classified before layout fit is judged",
            "push, mirror, server lifecycle, and manual command actions are risk-classified but never clicked",
            "large status and output surfaces use intentional scroll/wrap policies",
            "MCEL lens reads the live Git Tools DOM without changing selected project state"
          ]
        };
      }

      function collectEnrichmentViolations(doc, root) {
        if (!doc || !root) return [{law: "root-present", status: "failed", message: "Git Tools root unavailable"}];
        const violations = [];
        Array.from(root.querySelectorAll?.("[data-mcel-fit], [data-mcel-role]") || []).forEach((element) => {
          const rect = element.getBoundingClientRect?.();
          const parentRect = element.parentElement?.getBoundingClientRect?.();
          if (!rect || rect.width <= 0 || rect.height <= 0) return;
          const fit = element.getAttribute("data-mcel-fit") || "";
          const role = element.getAttribute("data-mcel-role") || "";
          const intentionalOverflow = [
            "intentional-scroll",
            "wrap-intentional-scroll",
            "bounded-list",
            "progressive-disclosure",
            "busy-form",
            "busy-card"
          ].includes(fit);
          if (!intentionalOverflow && element.scrollWidth - element.clientWidth > 2) {
            violations.push({
              law: "horizontal-containment",
              role,
              fit,
              id: element.id || "",
              selector: element.getAttribute("data-mcel-enrichment-selector") || "",
              delta: Math.ceil(element.scrollWidth - element.clientWidth)
            });
          }
          if (parentRect && rect.right > parentRect.right + 2 && !intentionalOverflow) {
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
        return violations.slice(0, 32);
      }

      function createUnavailableReport(options = {}) {
        const reason = options.reason || "enrichment";
        const message = options.message || "document unavailable";
        return {
          app: "git-tools",
          rootSelector: options.rootSelector || "#git-tools-app",
          enrichmentActive: false,
          rootPresent: false,
          enrichedElementCount: 0,
          layoutLawStatus: "unavailable",
          violations: [{law: options.law || "document", status: "failed", message}],
          destructiveActionsExecuted: false,
          safetyClaim: "enrichment reads and annotates the Git Tools specimen DOM; it never clicks server, remote, mirror, push, or manual command controls",
          reason,
          appliedAt: new Date().toISOString()
        };
      }

      function applyGitToolsMcelSemantics(options = {}) {
        const doc = options.document || global.document;
        const reason = options.reason || "enrichment";
        const rootSelector = options.rootSelector || "#git-tools-app";
        const route = options.route || "";
        const enrichedBy = options.enrichedBy || (options.mode === "lab-specimen" ? "git-tools-lab" : "git-tools-adapter");
        const source = options.source || "legacy-dom-reader";
        const generatedBy = options.generatedBy || (options.mode === "lab-specimen" ? "mcel-lab-git-tools-legacy-dom-enrichment" : "git-tools-mcel-adapter");
        const proofSurface = options.proofSurface || (options.mode === "lab-specimen" ? "canonical-app-specimen" : "git-tools-app");

        if (!doc?.body) {
          return createUnavailableReport({
            rootSelector,
            reason,
            law: "document",
            message: "Git Tools document unavailable"
          });
        }

        ensureEnrichmentStyle(doc);
        const root = doc.querySelector?.(rootSelector) || null;
        doc.documentElement?.setAttribute?.(BODY_ENRICHMENT_ATTRIBUTE, "active");
        doc.body.setAttribute(BODY_ENRICHMENT_ATTRIBUTE, "active");
        doc.body.classList.add(ENRICHMENT_CLASS);

        let enrichedElementCount = 0;
        if (root) {
          root.setAttribute("data-mcel-app", "git-tools");
          root.setAttribute("data-mcel-kind", "repository-operations-console");
          root.setAttribute("data-mcel-layout", "stacked-progressive-workflow");
          root.setAttribute("data-mcel-enriched", enrichedBy);
          root.setAttribute("data-mcel-enrichment-source", source);
          root.setAttribute("data-mcel-enrichment-state", "active");
          root.setAttribute("data-mcel-proof-surface", proofSurface);
          root.setAttribute("data-mcel-component-id", "canonical.git-tools.root");
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
            element.setAttribute("data-mcel-mutates", ["safe", "network-read"].includes(action.risk) ? "false" : "potential");
            element.setAttribute("data-mcel-enriched", enrichedBy);
            element.setAttribute("data-mcel-enrichment-selector", action.selector);
            enrichedElementCount += 1;
          });
        });

        const model = buildEnrichmentModel(doc, root, {reason, rootSelector, generatedBy});
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
          riskControlCount: model.actions.filter((item) => item.present && !["safe", "network-read"].includes(item.risk)).reduce((total, item) => total + item.count, 0),
          fitLawCount: model.components.filter((item) => item.fit).length,
          layoutLawStatus: violations.length ? "warning" : "ready",
          violations,
          enrichmentStyleId: ENRICHMENT_STYLE_ID,
          overlayMode: "semantic enrichment with role/fit attributes; busy Git/Gitea surfaces stay in their own progressive disclosure panels",
          destructiveActionsExecuted: false,
          safetyClaim: "MCEL enrichment reads and annotates Git Tools; it does not click Gitea server lifecycle, remote mutation, mirror setup, push, or manual command buttons",
          appliedAt: new Date().toISOString()
        };
      }

      function clearGitToolsMcelSemantics(doc, options = {}) {
        if (!doc?.body) return false;
        const rootSelector = options.rootSelector || "#git-tools-app";
        doc.documentElement?.removeAttribute?.(BODY_ENRICHMENT_ATTRIBUTE);
        doc.body.removeAttribute(BODY_ENRICHMENT_ATTRIBUTE);
        doc.body.classList.remove(ENRICHMENT_CLASS);
        if (options.removeStyle !== false) {
          doc.getElementById(ENRICHMENT_STYLE_ID)?.remove?.();
        }
        Array.from(doc.querySelectorAll?.("[data-mcel-enriched], [data-mcel-enrichment-source], [data-mcel-enrichment-selector], [data-mcel-role], [data-mcel-kind], [data-mcel-fit], [data-mcel-fit-context], [data-mcel-layout], [data-mcel-layout-policy], [data-mcel-layout-region], [data-mcel-region], [data-mcel-region-kind], [data-mcel-width-policy], [data-mcel-control-role], [data-mcel-control-priority], [data-mcel-action-role], [data-mcel-action-risk], [data-mcel-action-label], [data-mcel-mutates]") || []).forEach((element) => {
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
            "data-mcel-mutates"
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

      global.GitToolsMcel = {
        ENRICHMENT_STYLE_ID,
        ENRICHMENT_CLASS,
        BODY_ENRICHMENT_ATTRIBUTE,
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
        applyGitToolsMcelSemantics,
        applyTaskManagerMcelSemantics: applyGitToolsMcelSemantics,
        clearGitToolsMcelSemantics
      };
    })(window);
