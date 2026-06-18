    (function registerTerminalMcelAdapter(global) {
      "use strict";

      const ENRICHMENT_STYLE_ID = "mcel-lab-canonical-terminal-enrichment-style";
      const ENRICHMENT_CLASS = "mcel-canonical-terminal-enriched";
      const BODY_ENRICHMENT_ATTRIBUTE = "data-mcel-terminal-enrichment";
      const SUPERCUT_TRANSLATOR = "mcel-supercut-terminal-v0.1";
      const SUPERCUT_MAX_COMPONENTS = 180;

      const TERMINAL_SESSION_CONTRACT = Object.freeze({
        id: "terminal.session",
        elementId: "element.compute.terminal",
        concern: "concern.terminal-session",
        contract: "pattern.terminal-session",
        controllerElementId: "element.compute.terminal-controller",
        modelElementId: "element.compute.terminal-session-model",
        viewElementId: "element.compute.terminal-view",
        proofPolicy: "no-command-execution",
        commandPolicy: "stage-only-until-user-enter",
        inputPolicy: "stdin-buffer-is-controller-owned",
        outputPolicy: "stdout-stderr-scrollback-are-read-only-feeds",
        safetyClaim: "terminal MCEL enrichment identifies the shell, cwd, prompt/input, scrollback, AI staging, and command execution boundary without sending keys or running commands",
        laws: [
          "terminal-is-one-semantic-object-not-loose-text-lines",
          "command-execution-requires-explicit-user-enter",
          "cwd-timeout-and-buffer-are-terminal-state",
          "stdout-stderr-and-analysis-are-output-feeds",
          "AI suggestion stages text only and does not run"
        ]
      });

      const REGION_ENRICHMENT = [
        {selector: "#terminal-app", role: "terminal-object", kind: "semantic-terminal", elementId: "element.compute.terminal", contract: "pattern.terminal-session", layout: "terminal-with-analysis", fitContext: "root"},
        {selector: ".terminal-shell", role: "terminal-shell", kind: "terminal-shell", elementId: "element.compute.terminal-view", contract: "pattern.terminal-session", layout: "shell-workspace", fitContext: "interactive-shell"},
        {selector: ".terminal-controls", role: "terminal-session-state", kind: "toolbar", elementId: "element.compute.terminal-session-model", contract: "pattern.terminal-session", fitContext: "session-state"},
        {selector: ".terminal-ai-command", role: "terminal-command-staging", kind: "command-staging", elementId: "element.compute.terminal-controller", contract: "pattern.terminal-session", fitContext: "stage-only"},
        {selector: "#terminal-xterm", role: "terminal-viewport", kind: "terminal-viewport", elementId: "element.compute.terminal-view", contract: "pattern.terminal-session", fitContext: "xterm-surface"},
        {selector: ".terminal-analysis-panel", role: "terminal-failure-analysis", kind: "analysis-feed", elementId: "element.core.status-feed", contract: "pattern.terminal-session", fitContext: "read-only-sidecar"}
      ];

      const COMPONENT_ENRICHMENT = [
        {selector: "#terminal-analysis", role: "failure-analysis-output", kind: "analysis-feed", fit: "bounded-read-only-scroll"},
        {selector: ".terminal-hint", role: "terminal-keystroke-policy", kind: "policy-text", fit: "wrap-text"},
        {selector: ".terminal-ai-status", role: "ai-staging-status", kind: "status-feed", fit: "wrap-status"},
        {selector: ".terminal-controls label", role: "session-state-field", kind: "field", fit: "compact-field-row"},
        {selector: ".terminal-ai-command label", role: "command-intent-field", kind: "field", fit: "prompt-field"}
      ];

      const FIELD_ENRICHMENT = [
        {control: "#terminal-cwd", role: "cwd", stateField: "cwd", priority: "primary"},
        {control: "#terminal-timeout", role: "timeout-seconds", stateField: "timeout_s", priority: "secondary"},
        {control: "#terminal-ai-prompt", role: "ai-command-intent", stateField: "staged_command_intent", priority: "primary"}
      ];

      const ACTION_LENS = [
        {selector: "#terminal-ai-suggest", risk: "analysis", role: "stage-command-suggestion", label: "stage AI command suggestion"},
        {selector: "#terminal-xterm", risk: "command-execution", role: "terminal-stdin-enter-boundary", label: "xterm input and Enter execution boundary"},
        {selector: ".terminal-analysis-tools button", risk: "safe", role: "toggle-analysis-rendering", label: "toggle rendered/raw analysis"}
      ];

      function ensureEnrichmentStyle(doc) {
        if (!doc?.head) return false;
        let style = doc.getElementById(ENRICHMENT_STYLE_ID);
        if (style) return true;
        style = doc.createElement("style");
        style.id = ENRICHMENT_STYLE_ID;
        style.textContent = `
          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-terminal-object="true"] {
            min-width: 0 !important;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-role="terminal-shell"],
          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-role="terminal-viewport"] {
            min-width: 0 !important;
            overflow: hidden !important;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-terminal-role="terminal-viewport"] {
            isolation: isolate !important;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-command-policy="no-command-execution"] {
            outline-offset: 2px;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-fit="compact-field-row"] {
            display: grid !important;
            grid-template-columns: minmax(96px, 0.38fr) minmax(0, 1fr) !important;
            gap: 8px !important;
            align-items: center !important;
            min-width: 0 !important;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-fit="compact-field-row"] input {
            min-width: 0 !important;
            width: 100% !important;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-fit="prompt-field"] textarea {
            min-height: 4rem !important;
            resize: vertical !important;
          }

          body[${BODY_ENRICHMENT_ATTRIBUTE}="active"] [data-mcel-fit="bounded-read-only-scroll"] {
            overflow: auto !important;
            max-height: 100% !important;
          }
        `;
        doc.head.appendChild(style);
        return true;
      }

      function cleanText(value) {
        return String(value || "").replace(/\s+/g, " ").trim();
      }

      function nearestControlLabel(control) {
        if (!control) return null;
        return control.closest?.("label") || control.parentElement;
      }

      function setIfValue(element, name, value) {
        if (!element || value === undefined || value === null || value === "") return false;
        element.setAttribute(name, String(value));
        return true;
      }

      function applyElementEnrichment(element, definition, options = {}) {
        if (!element || !definition) return false;
        const enrichedBy = options.enrichedBy || "terminal-adapter";
        const source = options.source || "terminal-mcel-adapter";
        setIfValue(element, "data-mcel-role", definition.role);
        setIfValue(element, "data-mcel-kind", definition.kind || "surface");
        setIfValue(element, "data-mcel-terminal-role", definition.role);
        setIfValue(element, "data-mcel-element-id", definition.elementId);
        setIfValue(element, "data-mcel-contract", definition.contract || TERMINAL_SESSION_CONTRACT.contract);
        setIfValue(element, "data-mcel-concern", TERMINAL_SESSION_CONTRACT.concern);
        setIfValue(element, "data-mcel-layout", definition.layout);
        setIfValue(element, "data-mcel-fit-context", definition.fitContext);
        setIfValue(element, "data-mcel-fit", definition.fit);
        element.setAttribute("data-mcel-terminal-session", TERMINAL_SESSION_CONTRACT.id);
        element.setAttribute("data-mcel-enriched", enrichedBy);
        element.setAttribute("data-mcel-enrichment-source", source);
        return true;
      }

      function terminalInputBufferEstimate(doc) {
        const xtermRows = Array.from(doc?.querySelectorAll?.("#terminal-xterm .xterm-rows > div") || []);
        const lastRow = cleanText(xtermRows.at(-1)?.textContent || "");
        return lastRow.includes(">") ? lastRow.split(">").slice(1).join(">").trim() : "";
      }

      function buildTerminalSessionModel(doc, root, options = {}) {
        const cwd = doc?.querySelector?.("#terminal-cwd")?.value || ".";
        const timeout = Number(doc?.querySelector?.("#terminal-timeout")?.value || 15);
        const aiPrompt = doc?.querySelector?.("#terminal-ai-prompt")?.value || "";
        const xtermSurface = doc?.querySelector?.("#terminal-xterm") || null;
        const analysisText = cleanText(doc?.querySelector?.("#terminal-analysis")?.textContent || "");
        const controls = Array.from(root?.querySelectorAll?.("button, input, textarea, [role='button']") || []);
        const rootText = cleanText(root?.textContent || "");

        return {
          ...TERMINAL_SESSION_CONTRACT,
          app: "terminal",
          kind: "terminal-session",
          rootSelector: options.rootSelector || "#terminal-app",
          rootPresent: Boolean(root),
          cwd,
          timeoutSeconds: Number.isFinite(timeout) ? timeout : 15,
          aiCommandIntentPresent: Boolean(aiPrompt.trim()),
          xtermSurfacePresent: Boolean(xtermSurface),
          inputBufferEstimate: terminalInputBufferEstimate(doc),
          visibleControlCount: controls.length,
          hasFailureAnalysis: Boolean(analysisText && analysisText !== "No terminal failure yet."),
          rootTextEvidence: rootText.toLowerCase().includes("enter runs") && rootText.toLowerCase().includes("working directory"),
          commandExecutionBoundary: {
            event: "Enter in xterm",
            backendEndpoint: "/api/applications/terminal/run",
            policy: "no-command-execution-during-mcel-proof",
            userActionRequired: true
          },
          aiStagingBoundary: {
            control: "#terminal-ai-suggest",
            endpoint: "/api/applications/terminal/suggest",
            policy: "stage-command-only",
            runsCommand: false
          },
          generatedBy: options.generatedBy || "terminal-mcel-adapter",
          reason: options.reason || "terminal-enrichment",
          builtAt: new Date().toISOString()
        };
      }

      function summarizeSupercutRewritePreview(supercut = {}) {
        const summary = {root: 0, panels: 0, toolbars: 0, fields: 0, actions: 0, statusFeeds: 0, unknown: 0};
        (supercut.rewritePreview || []).forEach((node) => {
          const tag = String(node.proposedTag || "");
          if (tag.includes("app")) summary.root += 1;
          else if (tag.includes("panel") || tag.includes("region")) summary.panels += 1;
          else if (tag.includes("toolbar")) summary.toolbars += 1;
          else if (tag.includes("field")) summary.fields += 1;
          else if (tag.includes("action")) summary.actions += 1;
          else if (tag.includes("feed") || tag.includes("log")) summary.statusFeeds += 1;
          else summary.unknown += 1;
        });
        return summary;
      }

      function runTerminalSupercutTranslation(doc, root, options = {}) {
        if (!doc?.body || !root || !global.McelSupercut?.translateRuntime) {
          return {
            active: false,
            translator: SUPERCUT_TRANSLATOR,
            taggedElementCount: 0,
            componentCount: 0,
            executableComponentCount: 0,
            originalPointCount: 0,
            packsLoaded: [],
            rulesFired: 0,
            unsafeActionsBlocked: 0,
            sourceMutations: 0,
            runtimeSourceMutations: 0,
            rewritePreview: []
          };
        }
        try {
          const result = global.McelSupercut.translateRuntime({
            document: doc,
            root,
            rootSelector: options.rootSelector || "#terminal-app",
            app: "terminal",
            specimenId: "terminal",
            packs: ["core-html", "core-action-risk", "terminal-domain"],
            mode: "terminal-mcel-read-only",
            rounds: 3,
            maxComponents: SUPERCUT_MAX_COMPONENTS,
            reason: options.reason || "terminal-mcel-adapter"
          }) || {};
          return {
            ...result,
            active: Boolean(result.active || result.architectureStatus === "ready" || result.rewritePreview?.length),
            translator: SUPERCUT_TRANSLATOR,
            taggedElementCount: result.taggedElementCount || result.componentCount || 0
          };
        } catch (error) {
          return {
            active: false,
            translator: SUPERCUT_TRANSLATOR,
            architectureStatus: "error",
            message: error?.message || "Terminal Supercut translation failed",
            rewritePreview: []
          };
        }
      }

      function collectEnrichmentViolations(doc, root) {
        if (!doc || !root) return [{law: "root-present", status: "failed", message: "Terminal root unavailable"}];
        const violations = [];
        const terminalObject = root.getAttribute("data-mcel-element-id") === TERMINAL_SESSION_CONTRACT.elementId;
        const xtermSurface = doc.querySelector?.("#terminal-xterm");
        const cwd = doc.querySelector?.("#terminal-cwd");
        const runEndpointMentioned = cleanText(root.textContent || "").toLowerCase().includes("enter runs");
        if (!terminalObject) {
          violations.push({law: "terminal-object", status: "failed", message: "root is not stamped as element.compute.terminal"});
        }
        if (!xtermSurface) {
          violations.push({law: "terminal-viewport", status: "failed", message: "xterm surface missing"});
        }
        if (!cwd) {
          violations.push({law: "terminal-session-state", status: "failed", message: "cwd state field missing"});
        }
        if (!runEndpointMentioned) {
          violations.push({law: "execution-boundary-visible", status: "warning", message: "visible Enter-runs policy text missing"});
        }
        Array.from(root.querySelectorAll?.("[data-mcel-command-policy='no-command-execution']") || []).forEach((element) => {
          if (element.tagName?.toLowerCase?.() === "button" && /run|execute/i.test(element.textContent || "")) {
            violations.push({law: "no-run-button", status: "warning", role: element.getAttribute("data-mcel-terminal-role") || "button"});
          }
        });
        return violations.slice(0, 24);
      }

      function createUnavailableReport(options = {}) {
        const reason = options.reason || "terminal-enrichment";
        return {
          app: "terminal",
          rootSelector: options.rootSelector || "#terminal-app",
          enrichmentActive: false,
          rootPresent: false,
          terminalObjectReady: false,
          terminalSessionContract: TERMINAL_SESSION_CONTRACT,
          enrichedElementCount: 0,
          regionCount: 0,
          componentCount: 0,
          fieldCount: 0,
          actionControlCount: 0,
          riskControlCount: 0,
          fitLawCount: 0,
          layoutLawStatus: "unavailable",
          violations: [{law: options.law || "document", status: "failed", message: options.message || "Terminal document unavailable"}],
          destructiveActionsExecuted: false,
          safetyClaim: TERMINAL_SESSION_CONTRACT.safetyClaim,
          reason,
          appliedAt: new Date().toISOString()
        };
      }

      function applyTerminalMcelSemantics(options = {}) {
        const doc = options.document || global.document;
        const rootSelector = options.rootSelector || "#terminal-app";
        const reason = options.reason || "terminal-enrichment";
        const route = options.route || "";
        const enrichedBy = options.enrichedBy || (options.mode === "lab-specimen" ? "terminal-lab" : "terminal-adapter");
        const source = options.source || "terminal-mcel-adapter";
        const generatedBy = options.generatedBy || (options.mode === "lab-specimen" ? "mcel-lab-terminal-object-enrichment" : "terminal-mcel-adapter");

        if (!doc?.body) {
          return createUnavailableReport({
            rootSelector,
            reason,
            law: "document",
            message: "Terminal document unavailable"
          });
        }

        ensureEnrichmentStyle(doc);
        const root = doc.querySelector?.(rootSelector) || null;
        doc.documentElement?.setAttribute?.(BODY_ENRICHMENT_ATTRIBUTE, "active");
        doc.body.setAttribute(BODY_ENRICHMENT_ATTRIBUTE, "active");
        doc.body.classList.add(ENRICHMENT_CLASS);

        let enrichedElementCount = 0;
        if (root) {
          root.setAttribute("data-mcel-app", "terminal");
          root.setAttribute("data-mcel-kind", "terminal-session");
          root.setAttribute("data-mcel-element-id", TERMINAL_SESSION_CONTRACT.elementId);
          root.setAttribute("data-mcel-contract", TERMINAL_SESSION_CONTRACT.contract);
          root.setAttribute("data-mcel-concern", TERMINAL_SESSION_CONTRACT.concern);
          root.setAttribute("data-mcel-terminal-object", "true");
          root.setAttribute("data-mcel-terminal-session", TERMINAL_SESSION_CONTRACT.id);
          root.setAttribute("data-mcel-terminal-proof-policy", TERMINAL_SESSION_CONTRACT.proofPolicy);
          root.setAttribute("data-mcel-command-policy", TERMINAL_SESSION_CONTRACT.commandPolicy);
          root.setAttribute("data-mcel-enriched", enrichedBy);
          root.setAttribute("data-mcel-enrichment-source", source);
          root.setAttribute("data-mcel-enrichment-state", "active");
          root.setAttribute("data-mcel-proof-surface", options.proofSurface || "terminal-session");
          root.setAttribute("data-mcel-component-id", "canonical.terminal.root");
          enrichedElementCount += 1;
        }

        REGION_ENRICHMENT.forEach((definition) => {
          Array.from(doc.querySelectorAll?.(definition.selector) || []).forEach((element) => {
            applyElementEnrichment(element, definition, {enrichedBy, source});
            element.setAttribute("data-mcel-enrichment-selector", definition.selector);
            if (definition.role === "terminal-viewport") {
              element.setAttribute("data-mcel-terminal-viewport", "xterm");
              element.setAttribute("data-mcel-command-policy", "no-command-execution");
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
          control.setAttribute("data-mcel-terminal-state-field", definition.stateField);
          control.setAttribute("data-mcel-control-priority", definition.priority);
          control.setAttribute("data-mcel-enrichment-selector", definition.control);
          control.setAttribute("data-mcel-enriched", enrichedBy);
          const label = nearestControlLabel(control);
          if (label) {
            label.setAttribute("data-mcel-role", "terminal-state-field");
            label.setAttribute("data-mcel-kind", "field");
            label.setAttribute("data-mcel-terminal-role", definition.role);
            label.setAttribute("data-mcel-enriched", enrichedBy);
          }
          enrichedElementCount += label ? 2 : 1;
        });

        ACTION_LENS.forEach((action) => {
          Array.from(doc.querySelectorAll?.(action.selector) || []).forEach((element) => {
            element.setAttribute("data-mcel-role", "terminal-action-boundary");
            element.setAttribute("data-mcel-action-role", action.role);
            element.setAttribute("data-mcel-action-risk", action.risk);
            element.setAttribute("data-mcel-action-label", action.label);
            element.setAttribute("data-mcel-command-policy", action.risk === "command-execution" ? "no-command-execution" : "inspect-only");
            element.setAttribute("data-mcel-mutates", action.risk === "safe" || action.risk === "analysis" ? "false" : "potential");
            element.setAttribute("data-mcel-enriched", enrichedBy);
            element.setAttribute("data-mcel-enrichment-selector", action.selector);
            enrichedElementCount += 1;
          });
        });

        const supercut = runTerminalSupercutTranslation(doc, root, {reason, rootSelector, generatedBy});
        if (supercut.active) {
          enrichedElementCount += supercut.taggedElementCount || 0;
        }

        const model = buildTerminalSessionModel(doc, root, {reason, rootSelector, generatedBy});
        const violations = collectEnrichmentViolations(doc, root);
        const presentRegions = REGION_ENRICHMENT.filter((definition) => doc.querySelector?.(definition.selector));
        const presentComponents = COMPONENT_ENRICHMENT.filter((definition) => doc.querySelector?.(definition.selector));
        const presentFields = FIELD_ENRICHMENT.filter((definition) => doc.querySelector?.(definition.control));
        const presentActions = ACTION_LENS.filter((definition) => doc.querySelector?.(definition.selector));
        return {
          ...model,
          route,
          enrichmentActive: Boolean(root),
          terminalObjectReady: Boolean(root && root.getAttribute("data-mcel-element-id") === TERMINAL_SESSION_CONTRACT.elementId),
          terminalSessionContract: TERMINAL_SESSION_CONTRACT,
          regions: REGION_ENRICHMENT.map((definition) => ({...definition, present: Boolean(doc.querySelector?.(definition.selector))})),
          components: COMPONENT_ENRICHMENT.map((definition) => ({...definition, present: Boolean(doc.querySelector?.(definition.selector)), count: Array.from(doc.querySelectorAll?.(definition.selector) || []).length})),
          fields: FIELD_ENRICHMENT.map((definition) => ({...definition, present: Boolean(doc.querySelector?.(definition.control))})),
          actions: ACTION_LENS.map((definition) => ({...definition, present: Boolean(doc.querySelector?.(definition.selector)), count: Array.from(doc.querySelectorAll?.(definition.selector) || []).length})),
          enrichedElementCount,
          regionCount: presentRegions.length,
          componentCount: presentComponents.length,
          fieldCount: presentFields.length,
          actionControlCount: presentActions.length,
          riskControlCount: presentActions.filter((action) => !["safe", "analysis"].includes(action.risk)).length,
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
          supercutSourceMutations: supercut.sourceMutations || 0,
          supercutRuntimeSourceMutations: supercut.runtimeSourceMutations || 0,
          fitLawCount: REGION_ENRICHMENT.length + COMPONENT_ENRICHMENT.length + FIELD_ENRICHMENT.length + ACTION_LENS.length + (supercut.cssObjectCatalog?.length || 0),
          layoutLawStatus: violations.length ? "warning" : "ready",
          violations,
          enrichmentStyleId: ENRICHMENT_STYLE_ID,
          overlayMode: "semantic terminal object enrichment with command-execution boundary; no keystrokes or commands are sent",
          destructiveActionsExecuted: false,
          safetyClaim: TERMINAL_SESSION_CONTRACT.safetyClaim,
          appliedAt: new Date().toISOString()
        };
      }

      function clearTerminalMcelSemantics(doc, options = {}) {
        if (!doc?.body) return false;
        const rootSelector = options.rootSelector || "#terminal-app";
        doc.documentElement?.removeAttribute?.(BODY_ENRICHMENT_ATTRIBUTE);
        doc.body.removeAttribute(BODY_ENRICHMENT_ATTRIBUTE);
        doc.body.classList.remove(ENRICHMENT_CLASS);
        if (options.removeStyle !== false) {
          doc.getElementById(ENRICHMENT_STYLE_ID)?.remove?.();
        }
        global.McelSupercut?.clearRuntime?.({document: doc, rootSelector});
        Array.from(doc.querySelectorAll?.("[data-mcel-enriched], [data-mcel-enrichment-source], [data-mcel-enrichment-selector], [data-mcel-role], [data-mcel-kind], [data-mcel-fit], [data-mcel-element-id], [data-mcel-contract], [data-mcel-concern], [data-mcel-terminal-object], [data-mcel-terminal-session], [data-mcel-terminal-role], [data-mcel-terminal-proof-policy], [data-mcel-command-policy], [data-mcel-terminal-state-field], [data-mcel-terminal-viewport], [data-mcel-control-role], [data-mcel-control-priority], [data-mcel-action-role], [data-mcel-action-risk], [data-mcel-action-label], [data-mcel-mutates], [data-mcel-supercut], [data-mcel-supercut-purpose], [data-mcel-supercut-contract], [data-mcel-supercut-proof-policy], [data-mcel-supercut-rewrite-tag]") || []).forEach((element) => {
          [
            "data-mcel-enriched",
            "data-mcel-enrichment-source",
            "data-mcel-enrichment-selector",
            "data-mcel-role",
            "data-mcel-kind",
            "data-mcel-fit",
            "data-mcel-element-id",
            "data-mcel-contract",
            "data-mcel-concern",
            "data-mcel-terminal-object",
            "data-mcel-terminal-session",
            "data-mcel-terminal-role",
            "data-mcel-terminal-proof-policy",
            "data-mcel-command-policy",
            "data-mcel-terminal-state-field",
            "data-mcel-terminal-viewport",
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

      global.TerminalMcel = {
        ENRICHMENT_STYLE_ID,
        ENRICHMENT_CLASS,
        BODY_ENRICHMENT_ATTRIBUTE,
        REGION_ENRICHMENT,
        COMPONENT_ENRICHMENT,
        FIELD_ENRICHMENT,
        ACTION_LENS,
        TERMINAL_SESSION_CONTRACT,
        ensureEnrichmentStyle,
        applyElementEnrichment,
        nearestControlLabel,
        buildTerminalSessionModel,
        buildEnrichmentModel: buildTerminalSessionModel,
        collectEnrichmentViolations,
        createUnavailableReport,
        runTerminalSupercutTranslation,
        summarizeSupercutRewritePreview,
        applyTerminalMcelSemantics,
        applyCanonicalMcelSemantics: applyTerminalMcelSemantics,
        clearTerminalMcelSemantics,
        clearCanonicalMcelSemantics: clearTerminalMcelSemantics
      };
    })(window);
