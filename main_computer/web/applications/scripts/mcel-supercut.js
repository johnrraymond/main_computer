    (function (global) {
      "use strict";

      const SUPERCUT_VERSION = "0.1.0";
      const SUPERCUT_STYLE_ID = "mcel-supercut-runtime-style";
      const SUPERCUT_BODY_ATTRIBUTE = "data-mcel-supercut-runtime";
      const SUPERCUT_ATTRIBUTE = "data-mcel-supercut";
      const SUPERCUT_CLASS = "mcel-supercut-runtime-active";
      const COMPONENT_SELECTOR = [
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "aside",
        "main",
        "form",
        "details",
        "summary",
        "label",
        "button",
        "a[href]",
        "input",
        "select",
        "textarea",
        "output",
        "pre",
        "ul",
        "ol",
        "li",
        "[id]",
        "[class]",
        "[data-mc-component-id]",
        "[data-mc-widget-id]"
      ].join(",");

      const PURPOSE_RULES = [
        {token: "project", originalPoint: "git-tools.project-selection", roleHint: "project-selector"},
        {token: "wizard", originalPoint: "git-tools.guided-page-wizard", roleHint: "guided-workflow"},
        {token: "patch", originalPoint: "git-tools.patch-inventory", roleHint: "patch-workflow"},
        {token: "shim", originalPoint: "git-tools.control-shim", roleHint: "shim-workflow"},
        {token: "console", originalPoint: "git-tools.manual-console", roleHint: "command-console"},
        {token: "server", originalPoint: "git-tools.gitea-server-control", roleHint: "server-control"},
        {token: "gitea", originalPoint: "git-tools.gitea-publish-workflow", roleHint: "gitea-workflow"},
        {token: "remote", originalPoint: "git-tools.remote-configuration", roleHint: "remote-config"},
        {token: "mirror", originalPoint: "git-tools.mirror-publication", roleHint: "mirror-config"},
        {token: "push", originalPoint: "git-tools.repository-publication", roleHint: "publish-action"},
        {token: "operation", originalPoint: "git-tools.operation-activity", roleHint: "operation-feed"},
        {token: "activity", originalPoint: "git-tools.operation-activity", roleHint: "operation-feed"},
        {token: "status", originalPoint: "git-tools.status-report", roleHint: "status-output"},
        {token: "output", originalPoint: "git-tools.output-feed", roleHint: "output-feed"},
        {token: "log", originalPoint: "git-tools.output-feed", roleHint: "output-feed"},
        {token: "action", originalPoint: "git-tools.action-surface", roleHint: "action-surface"}
      ];

      const ACTION_RISK_RULES = [
        {pattern: /(delete|remove|terminate|kill|shutdown|stop)\b/i, risk: "destructive"},
        {pattern: /(restart|reset|cancel)\b/i, risk: "operational"},
        {pattern: /\b(push|publish|mirror|sync)\b/i, risk: "publication-mutation"},
        {pattern: /\b(remote|set-url|configure|apply)\b/i, risk: "repo-config-mutation"},
        {pattern: /\b(run|command|console|exec)\b/i, risk: "command-execution"},
        {pattern: /\b(start|unlock|lock)\b/i, risk: "operational"},
        {pattern: /\b(refresh|inspect|plan|show|copy|preview|dry run)\b/i, risk: "safe"}
      ];

      function normalizeText(value) {
        return String(value || "").replace(/\s+/g, " ").trim();
      }

      function slugify(value) {
        return normalizeText(value)
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-+|-+$/g, "")
          .slice(0, 96) || "component";
      }

      function hashString(value) {
        let hash = 5381;
        const text = String(value || "");
        for (let index = 0; index < text.length; index += 1) {
          hash = ((hash << 5) + hash) ^ text.charCodeAt(index);
        }
        return (hash >>> 0).toString(36);
      }

      function elementSignature(element) {
        if (!element) return "";
        return [
          element.tagName?.toLowerCase?.() || "",
          element.id || "",
          element.className || "",
          element.getAttribute?.("data-mc-component-id") || "",
          element.getAttribute?.("data-mc-widget-id") || "",
          normalizeText(element.getAttribute?.("aria-label") || "")
        ].join(" ");
      }

      function elementTextSample(element, limit = 180) {
        if (!element) return "";
        const label = element.getAttribute?.("aria-label") || element.getAttribute?.("title") || "";
        const text = normalizeText(label || element.textContent || "");
        return text.slice(0, limit);
      }

      function nearestHeadingText(element) {
        let node = element;
        while (node && node.nodeType === 1) {
          const heading = node.querySelector?.(":scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > h6, :scope > .eyebrow, :scope > strong");
          if (heading) return elementTextSample(heading, 96);
          node = node.parentElement;
        }
        return "";
      }

      function componentPath(element, root) {
        if (!element) return "missing";
        const parts = [];
        let node = element;
        while (node && node.nodeType === 1 && node !== root && parts.length < 8) {
          const tag = node.tagName.toLowerCase();
          const id = node.id ? `#${node.id}` : "";
          const cls = !id && node.classList?.length ? `.${Array.from(node.classList).slice(0, 2).map(slugify).join(".")}` : "";
          const siblings = Array.from(node.parentElement?.children || []).filter((child) => child.tagName === node.tagName);
          const index = siblings.length > 1 ? `:nth-of-type(${siblings.indexOf(node) + 1})` : "";
          parts.unshift(`${tag}${id || cls}${index}`);
          node = node.parentElement;
        }
        return parts.join(" > ") || (element.id ? `#${element.id}` : element.tagName?.toLowerCase?.() || "component");
      }

      function controlCount(element) {
        return element?.querySelectorAll?.("button, a[href], input, select, textarea, output")?.length || 0;
      }

      function hasLayoutChildren(element) {
        return (element?.children?.length || 0) >= 2;
      }

      function detectActionRisk(element, tokens) {
        if (!element) return "";
        const tag = element.tagName?.toLowerCase?.() || "";
        if (!["button", "a", "summary"].includes(tag) && element.getAttribute?.("role") !== "button") return "";
        const source = [
          tokens,
          element.id,
          element.className,
          element.getAttribute?.("data-git-server-remote-preset") || "",
          elementTextSample(element, 80)
        ].join(" ");
        const rule = ACTION_RISK_RULES.find((candidate) => candidate.pattern.test(source));
        return rule?.risk || "safe";
      }

      function inferOriginalPoint(element, root) {
        const signature = elementSignature(element);
        const heading = nearestHeadingText(element);
        const text = elementTextSample(element, 120);
        const component = element.getAttribute?.("data-mc-component-id") || element.getAttribute?.("data-mc-widget-id") || "";
        const source = [signature, heading, text, component].join(" ").toLowerCase();
        const rule = PURPOSE_RULES.find((candidate) => source.includes(candidate.token));
        const originalPoint = rule?.originalPoint || (component ? `component.${slugify(component)}` : "legacy-html.unknown-purpose");
        const evidence = [];
        if (element.id) evidence.push(`id:${element.id}`);
        if (component) evidence.push(`component:${component}`);
        if (heading) evidence.push(`heading:${heading}`);
        if (rule) evidence.push(`keyword:${rule.token}`);
        if (element.classList?.length) evidence.push(`class:${Array.from(element.classList).slice(0, 3).join(".")}`);
        if (!evidence.length) evidence.push(`path:${componentPath(element, root)}`);
        return {
          originalPoint,
          roleHint: rule?.roleHint || "",
          evidence
        };
      }

      function inferComponentRole(element, root, purpose) {
        const tag = element.tagName?.toLowerCase?.() || "";
        const signature = elementSignature(element).toLowerCase();
        const controls = controlCount(element);
        if (element === root) return "app-root";
        if (["button", "a", "summary"].includes(tag) || element.getAttribute?.("role") === "button") return "action-component";
        if (["input", "select", "textarea"].includes(tag)) return "field-control";
        if (tag === "label") return "field-shell";
        if (tag === "form" || signature.includes("form") || controls >= 4) return "form-component";
        if (tag === "pre" || tag === "output" || /(output|log|status|dashboard|report|feed|activity)/.test(signature)) return "feed-component";
        if (["ul", "ol"].includes(tag) || /(list|roster|inventory|archive)/.test(signature)) return "collection-component";
        if (tag === "li") return "collection-item";
        if (tag === "details" || /(workflow|accordion|step|wizard)/.test(signature)) return "workflow-component";
        if (/(toolbar|actions|button-row)/.test(signature)) return "toolbar-component";
        if (/(shell|layout|grid|workspace|hero)/.test(signature)) return "layout-component";
        if (/(card|pane|panel|widget)/.test(signature)) return "panel-component";
        if (purpose?.roleHint) return purpose.roleHint;
        return hasLayoutChildren(element) ? "semantic-container" : "content-component";
      }

      function inferFitPolicy(element, role, risk) {
        const signature = elementSignature(element).toLowerCase();
        const tag = element.tagName?.toLowerCase?.() || "";
        if (role === "app-root" || /(shell|workspace|layout)/.test(signature)) return "runtime-shell";
        if (role === "toolbar-component" || /(actions|toolbar|button-row)/.test(signature)) return "toolbar-wrap";
        if (role === "form-component" || role === "field-shell" || /(fields|composer|settings|form)/.test(signature)) return "field-grid";
        if (role === "feed-component" || tag === "pre" || /(output|log|dashboard|report|activity)/.test(signature)) return "scroll-feed";
        if (role === "workflow-component" || /(accordion|workflow|step|wizard)/.test(signature)) return "workflow-stack";
        if (role === "collection-component" || role === "collection-item") return "bounded-collection";
        if (role === "action-component") return risk && risk !== "safe" ? "risk-action" : "inline-action";
        if (role === "panel-component") return "responsive-panel";
        return "responsive-component";
      }

      function buildComponentId(element, root, index, app) {
        const existing = element.getAttribute?.("data-mc-component-id") || element.getAttribute?.("data-mc-widget-id") || "";
        if (existing) return `supercut.${slugify(existing)}`;
        if (element.id) return `supercut.${slugify(app)}.${slugify(element.id)}`;
        const path = componentPath(element, root);
        return `supercut.${slugify(app)}.${index}.${hashString(path)}`;
      }

      function shouldInspectElement(element, root) {
        if (!element || element.nodeType !== 1) return false;
        const tag = element.tagName?.toLowerCase?.() || "";
        if (["script", "style", "template", "svg", "path", "meta", "link"].includes(tag)) return false;
        if (element.closest?.("[data-mcel-supercut-generated=\"true\"]")) return false;
        if (element === root) return true;
        if (element.matches?.(COMPONENT_SELECTOR)) return true;
        return false;
      }

      function inspectHtmlRuntime(options = {}) {
        const doc = options.document || global.document;
        const rootSelector = options.rootSelector || "[data-mc-component-id], body";
        const root = options.root || doc?.querySelector?.(rootSelector) || null;
        const app = options.app || root?.id || "legacy-html";
        if (!doc || !root) {
          return {
            active: false,
            app,
            rootSelector,
            components: [],
            originalPoints: [],
            message: "MCEL Supercut could not inspect runtime because the root was unavailable"
          };
        }

        const candidates = [root, ...Array.from(root.querySelectorAll?.(COMPONENT_SELECTOR) || [])]
          .filter((element, index, all) => shouldInspectElement(element, root) && all.indexOf(element) === index);
        const maxComponents = Math.max(1, Number(options.maxComponents || 260));
        const components = candidates.slice(0, maxComponents).map((element, index) => {
          const purpose = inferOriginalPoint(element, root);
          const role = inferComponentRole(element, root, purpose);
          const risk = detectActionRisk(element, `${purpose.originalPoint} ${purpose.evidence.join(" ")}`);
          const fit = inferFitPolicy(element, role, risk);
          const componentId = buildComponentId(element, root, index, app);
          const children = Array.from(element.children || []).length;
          const controls = controlCount(element);
          const text = elementTextSample(element, 140);
          return {
            componentId,
            tag: element.tagName.toLowerCase(),
            selector: element.id ? `#${element.id}` : componentPath(element, root),
            role,
            originalPoint: purpose.originalPoint,
            evidence: purpose.evidence,
            fit,
            risk,
            executable: true,
            controlCount: controls,
            childCount: children,
            textSample: text,
            slimeSignals: {
              anonymousWrapper: !element.id && !element.getAttribute?.("data-mc-component-id") && ["div", "span"].includes(element.tagName.toLowerCase()),
              deepWrapper: componentPath(element, root).split(">").length > 5,
              denseControls: controls >= 4,
              busyText: text.length > 96
            },
            element
          };
        });

        const originalPoints = Array.from(new Set(components.map((component) => component.originalPoint)));
        return {
          active: true,
          app,
          rootSelector,
          inspectedAt: new Date().toISOString(),
          componentCount: components.length,
          originalPoints,
          components
        };
      }

      function ensureRuntimeStyle(doc) {
        if (!doc?.head) return false;
        let style = doc.getElementById(SUPERCUT_STYLE_ID);
        if (style) return true;
        style = doc.createElement("style");
        style.id = SUPERCUT_STYLE_ID;
        style.setAttribute("data-mcel-supercut-generated", "true");
        style.textContent = `
          body[${SUPERCUT_BODY_ATTRIBUTE}="active"] [${SUPERCUT_ATTRIBUTE}] {
            box-sizing: border-box !important;
            min-width: 0 !important;
            max-width: 100%;
          }

          body[${SUPERCUT_BODY_ATTRIBUTE}="active"] [data-mcel-supercut-fit="runtime-shell"],
          body[${SUPERCUT_BODY_ATTRIBUTE}="active"] [data-mcel-supercut-fit="workflow-stack"],
          body[${SUPERCUT_BODY_ATTRIBUTE}="active"] [data-mcel-supercut-fit="responsive-panel"] {
            min-width: 0 !important;
            overflow-wrap: anywhere !important;
          }

          body[${SUPERCUT_BODY_ATTRIBUTE}="active"] [data-mcel-supercut-fit="toolbar-wrap"] {
            display: flex !important;
            flex-wrap: wrap !important;
            gap: 8px !important;
            align-items: center;
          }

          body[${SUPERCUT_BODY_ATTRIBUTE}="active"] [data-mcel-supercut-fit="field-grid"] {
            min-width: 0 !important;
            overflow-wrap: anywhere !important;
          }

          body[${SUPERCUT_BODY_ATTRIBUTE}="active"] [data-mcel-supercut-fit="scroll-feed"],
          body[${SUPERCUT_BODY_ATTRIBUTE}="active"] [data-mcel-supercut-fit="bounded-collection"] {
            overflow: auto !important;
            scrollbar-gutter: stable;
          }

          body[${SUPERCUT_BODY_ATTRIBUTE}="active"] [data-mcel-supercut-risk]:not([data-mcel-supercut-risk="safe"]) {
            outline: 1px dashed rgba(255, 198, 109, 0.24);
            outline-offset: 2px;
          }
        `;
        doc.head.appendChild(style);
        return true;
      }

      function cssObjectFor(component) {
        return {
          componentId: component.componentId,
          fit: component.fit,
          selector: `[data-mcel-supercut-id="${component.componentId}"]`,
          rules: {
            minWidth: "0",
            maxWidth: "100%",
            overflow: ["scroll-feed", "bounded-collection"].includes(component.fit) ? "auto" : "visible",
            wrap: ["toolbar-wrap", "scroll-feed", "responsive-panel", "field-grid"].includes(component.fit) ? "anywhere" : "normal"
          }
        };
      }

      function tagComponent(component, round, options = {}) {
        const element = component.element;
        if (!element) return false;
        element.setAttribute(SUPERCUT_ATTRIBUTE, "component");
        element.setAttribute("data-mcel-supercut-id", component.componentId);
        element.setAttribute("data-mcel-supercut-role", component.role);
        element.setAttribute("data-mcel-supercut-purpose", component.originalPoint);
        element.setAttribute("data-mcel-supercut-source", component.evidence.join(" | "));
        element.setAttribute("data-mcel-supercut-fit", component.fit);
        element.setAttribute("data-mcel-supercut-executable", "true");
        element.setAttribute("data-mcel-supercut-round", String(round));
        element.setAttribute("data-mcel-supercut-translator", options.translator || "htmlTranslationTool");
        if (component.risk) element.setAttribute("data-mcel-supercut-risk", component.risk);
        if (component.slimeSignals?.anonymousWrapper) element.setAttribute("data-mcel-supercut-slime", "anonymous-wrapper");
        if (component.slimeSignals?.denseControls) element.setAttribute("data-mcel-supercut-density", "dense-controls");
        return true;
      }

      function runRectificationRounds(doc, root, inspection, options = {}) {
        const components = inspection.components || [];
        const rounds = [];
        const structureCount = components.reduce((count, component) => count + (tagComponent(component, 1, options) ? 1 : 0), 0);
        rounds.push({
          round: 1,
          name: "intent-extraction",
          changed: structureCount,
          result: "legacy HTML nodes tagged with mcel-supercut purpose, source evidence, and component identity"
        });

        const fitCount = components.reduce((count, component) => {
          const element = component.element;
          if (!element) return count;
          element.setAttribute("data-mcel-supercut-fit", component.fit);
          element.setAttribute("data-mcel-supercut-round", "2");
          return count + 1;
        }, 0);
        rounds.push({
          round: 2,
          name: "fit-object-synthesis",
          changed: fitCount,
          result: "runtime CSS object policies attached without rewriting source files"
        });

        const executionCount = components.reduce((count, component) => {
          const element = component.element;
          if (!element) return count;
          element.setAttribute("data-mcel-supercut-executable", "true");
          element.setAttribute("data-mcel-supercut-round", "3");
          return count + 1;
        }, 0);
        rounds.push({
          round: 3,
          name: "component-execution-contract",
          changed: executionCount,
          result: "each detected element can be addressed as an executable MCEL component"
        });

        root.setAttribute("data-mcel-supercut-root", "true");
        root.setAttribute("data-mcel-supercut-version", SUPERCUT_VERSION);
        root.setAttribute("data-mcel-supercut-rounds", String(rounds.length));
        return rounds;
      }

      function buildRegistry(root, inspection, rounds, options = {}) {
        const cssObjectCatalog = (inspection.components || []).map(cssObjectFor);
        const serializableComponents = (inspection.components || []).map((component) => ({
          componentId: component.componentId,
          tag: component.tag,
          selector: component.selector,
          role: component.role,
          originalPoint: component.originalPoint,
          evidence: component.evidence,
          fit: component.fit,
          risk: component.risk,
          executable: component.executable,
          controlCount: component.controlCount,
          childCount: component.childCount,
          textSample: component.textSample,
          slimeSignals: component.slimeSignals
        }));
        const registry = {
          version: SUPERCUT_VERSION,
          translator: "htmlTranslationTool",
          app: inspection.app,
          reason: options.reason || "supercut-translate",
          generatedBy: options.generatedBy || "mcel-supercut",
          active: true,
          componentCount: serializableComponents.length,
          executableComponentCount: serializableComponents.filter((component) => component.executable).length,
          originalPointCount: inspection.originalPoints.length,
          originalPoints: inspection.originalPoints,
          components: serializableComponents,
          cssObjectCatalog,
          rectificationRounds: rounds,
          runtimeChanges: [
            "data-mcel-supercut tags applied to inspected runtime elements",
            "mcel-supercut fit attributes attached for CSS object synthesis",
            "runtime style object inserted into the mounted document",
            "addressable component registry attached to the specimen root"
          ]
        };
        try {
          Object.defineProperty(root, "__mcelSupercutRegistry", {
            value: registry,
            configurable: true
          });
        } catch (_error) {
          root.__mcelSupercutRegistry = registry;
        }
        return registry;
      }

      function translateRuntime(options = {}) {
        const doc = options.document || global.document;
        const rootSelector = options.rootSelector || "[data-mc-component-id], body";
        const root = options.root || doc?.querySelector?.(rootSelector) || null;
        if (!doc?.body || !root) {
          return {
            active: false,
            app: options.app || "legacy-html",
            rootSelector,
            taggedElementCount: 0,
            componentCount: 0,
            executableComponentCount: 0,
            originalPointCount: 0,
            rectificationRounds: [],
            cssObjectCatalog: [],
            runtimeChanges: [],
            reason: options.reason || "supercut-translate",
            message: "MCEL Supercut could not translate runtime because the root was unavailable"
          };
        }

        ensureRuntimeStyle(doc);
        doc.documentElement?.setAttribute?.(SUPERCUT_BODY_ATTRIBUTE, "active");
        doc.body.setAttribute(SUPERCUT_BODY_ATTRIBUTE, "active");
        doc.body.classList.add(SUPERCUT_CLASS);

        const inspection = inspectHtmlRuntime({
          document: doc,
          root,
          rootSelector,
          app: options.app || "legacy-html",
          maxComponents: options.maxComponents
        });
        const rounds = runRectificationRounds(doc, root, inspection, options);
        const registry = buildRegistry(root, inspection, rounds, options);
        let architecture = null;
        if (global.McelSupercutCore?.run) {
          try {
            architecture = global.McelSupercutCore.run({
              specimenId: options.specimenId || options.app || "legacy-html",
              rootDocument: doc,
              rootElement: root,
              rootSelector,
              mode: options.mode || "tag-and-audit",
              rounds: options.rounds || 3,
              packs: options.packs || ["core-html", "core-action-risk", "git-tools-domain"],
              maxComponents: options.maxComponents
            });
          } catch (error) {
            architecture = {
              status: "error",
              specimenId: options.specimenId || options.app || "legacy-html",
              message: error?.message || "MCEL Supercut v0.2 architecture failed"
            };
          }
        }
        if (architecture?.status === "ready") {
          registry.architectureVersion = architecture.version || "0.2.0";
          registry.architectureStatus = architecture.status;
          registry.architecture = architecture;
          registry.blackboard = architecture.blackboard || null;
          registry.packsLoaded = architecture.packsLoaded || [];
          registry.rulesFired = architecture.metrics?.rulesFired || 0;
          registry.blackboardRecordCount = architecture.blackboard?.records?.length || architecture.metrics?.nodesScanned || 0;
          registry.rewritePreview = architecture.rewritePreview || [];
          registry.rewritePreviewCount = registry.rewritePreview.length;
          registry.rewritePreviewSummary = architecture.rewritePreviewSummary || {};
          registry.explanations = architecture.explanations || [];
          registry.explanationsReady = architecture.metrics?.explanationsReady || registry.explanations.length;
          registry.unsafeActionsBlocked = architecture.metrics?.unsafeActionsBlocked || 0;
          registry.sourceMutations = architecture.sourceMutations || architecture.metrics?.sourceMutations || 0;
          registry.runtimeSourceMutations = architecture.runtimeSourceMutations || architecture.metrics?.runtimeSourceMutations || 0;
          registry.safetyPolicy = architecture.safetyPolicy || {};
          registry.ruleTrace = architecture.ruleTrace || [];
          registry.metrics = architecture.metrics || {};
          registry.runtimeChanges = [
            ...registry.runtimeChanges,
            "Supercut v0.2 registry loaded core-html, core-action-risk, and Git Tools domain knowledge packs",
            "blackboard records collected facts from multiple rules without source rewriting",
            "rewrite-preview graph emitted for MCEL Lab inspection only"
          ];
          try {
            Object.defineProperty(root, "__mcelSupercutRegistry", {
              value: registry,
              configurable: true
            });
          } catch (_error) {
            root.__mcelSupercutRegistry = registry;
          }
        } else if (architecture) {
          registry.architectureStatus = architecture.status || "error";
          registry.architectureMessage = architecture.message || "MCEL Supercut v0.2 architecture unavailable";
        }
        return {
          ...registry,
          taggedElementCount: registry.componentCount,
          supercutStyleId: SUPERCUT_STYLE_ID,
          bodyAttribute: SUPERCUT_BODY_ATTRIBUTE,
          appliedAt: new Date().toISOString()
        };
      }

      function clearRuntime(options = {}) {
        const doc = options.document || global.document;
        const rootSelector = options.rootSelector || "[data-mc-component-id], body";
        const root = options.root || doc?.querySelector?.(rootSelector) || null;
        if (!doc?.body) return false;
        doc.documentElement?.removeAttribute?.(SUPERCUT_BODY_ATTRIBUTE);
        doc.body.removeAttribute(SUPERCUT_BODY_ATTRIBUTE);
        doc.body.classList.remove(SUPERCUT_CLASS);
        doc.getElementById(SUPERCUT_STYLE_ID)?.remove?.();
        Array.from(doc.querySelectorAll?.(`[${SUPERCUT_ATTRIBUTE}], [data-mcel-supercut-id], [data-mcel-supercut-role], [data-mcel-supercut-purpose], [data-mcel-supercut-source], [data-mcel-supercut-fit], [data-mcel-supercut-executable], [data-mcel-supercut-round], [data-mcel-supercut-translator], [data-mcel-supercut-risk], [data-mcel-supercut-slime], [data-mcel-supercut-density], [data-mcel-supercut-root], [data-mcel-supercut-version], [data-mcel-supercut-rounds]`) || []).forEach((element) => {
          [
            SUPERCUT_ATTRIBUTE,
            "data-mcel-supercut-id",
            "data-mcel-supercut-role",
            "data-mcel-supercut-purpose",
            "data-mcel-supercut-source",
            "data-mcel-supercut-fit",
            "data-mcel-supercut-executable",
            "data-mcel-supercut-round",
            "data-mcel-supercut-translator",
            "data-mcel-supercut-risk",
            "data-mcel-supercut-slime",
            "data-mcel-supercut-density",
            "data-mcel-supercut-root",
            "data-mcel-supercut-version",
            "data-mcel-supercut-rounds"
          ].forEach((attribute) => element.removeAttribute(attribute));
        });
        if (root && Object.prototype.hasOwnProperty.call(root, "__mcelSupercutRegistry")) {
          try {
            delete root.__mcelSupercutRegistry;
          } catch (_error) {
            root.__mcelSupercutRegistry = null;
          }
        }
        return true;
      }

      function executeComponent(rootOrDocument, componentId, command = "describe") {
        const root = rootOrDocument?.querySelector?.("[data-mcel-supercut-root=\"true\"]") || rootOrDocument;
        const registry = root?.__mcelSupercutRegistry || null;
        const component = registry?.components?.find?.((candidate) => candidate.componentId === componentId) || null;
        if (!component) {
          return {
            ok: false,
            command,
            componentId,
            message: "MCEL Supercut component not found"
          };
        }
        if (command === "rectify") {
          const element = root.ownerDocument?.querySelector?.(`[data-mcel-supercut-id="${componentId}"]`);
          if (element) {
            element.setAttribute("data-mcel-supercut-round", "manual-rectify");
            element.setAttribute("data-mcel-supercut-fit", component.fit);
          }
        }
        return {
          ok: true,
          command,
          component,
          cssObject: registry.cssObjectCatalog?.find?.((item) => item.componentId === componentId) || null,
          message: command === "rectify"
            ? "Component fit object re-applied"
            : "Component description returned"
        };
      }

      global.McelSupercut = {
        SUPERCUT_VERSION,
        SUPERCUT_STYLE_ID,
        SUPERCUT_BODY_ATTRIBUTE,
        SUPERCUT_ATTRIBUTE,
        SUPERCUT_CLASS,
        inspectHtmlRuntime,
        translateRuntime,
        clearRuntime,
        executeComponent,
        inferOriginalPoint,
        inferComponentRole,
        inferFitPolicy,
        buildComponentId
      };
    })(window);
