    (function (global) {
      "use strict";

      const PLANNER_VERSION = "0.2.0";

      const APP_PLANS = Object.freeze({
        "task-manager": Object.freeze({
          app: "task-manager",
          label: "Task Manager",
          route: "/applications/task-manager/server-processes?mcel_lab_specimen=task-manager",
          rootSelector: "#task-manager-app",
          status: "domain-proven",
          priority: 10,
          point: "Observe processes, server state, connections, schedules, and risky process/server controls without executing destructive actions.",
          domainPack: "task-manager-domain",
          adapter: "TaskManagerMcel",
          expectedRegions: ["operator-console", "sidebar-workspace-shell", "command-status-rail", "primary-workspace"],
          expectedFeeds: ["process-feed", "connection-feed", "status-feed", "ai-analysis"],
          expectedFields: ["query", "limit", "include-connections", "auto-refresh"],
          expectedActionFamilies: ["safe-refresh", "server-control", "pid-control", "schedule-mutation"],
          knownRiskFamilies: ["server-shutdown", "server-start", "server-restart", "pid-kill", "pid-terminate", "schedule-create", "schedule-delete", "schedule-run-now"],
          neverExecute: ["server shutdown/restart/start", "PID kill/terminate", "schedule mutation"],
          decodeHints: ["task-", "process", "pid", "server", "connection", "schedule", "ai analysis"],
          mountNeeds: ["keep current domain pack", "inspect risk family count", "verify repeated PID rows are counted by family"]
        }),
        "git-tools": Object.freeze({
          app: "git-tools",
          label: "Git Tools",
          route: "/applications/git-tools?mcel_lab_specimen=git-tools",
          rootSelector: "#git-tools-app",
          status: "domain-proven",
          priority: 20,
          point: "Observe repository, local Gitea, remote/mirror/push, and command surfaces without executing Git/Gitea mutations.",
          domainPack: "git-tools-domain",
          adapter: "GitToolsMcel",
          expectedRegions: ["git-operator-console", "git-workflow-shell", "project-intake-region", "progressive-workflow-region", "gitea-workflow-grid"],
          expectedFeeds: ["status-report", "operation-activity", "output-feed"],
          expectedFields: ["project-selection", "remote-configuration", "credential-inputs"],
          expectedActionFamilies: ["safe-refresh", "server-control", "remote-mutation", "mirror", "push", "manual-command"],
          knownRiskFamilies: ["server-start", "server-stop", "server-restart", "remote-configure", "push", "mirror", "manual-command"],
          neverExecute: ["server lifecycle", "remote configuration", "push/mirror", "manual command"],
          decodeHints: ["git-", "gitea", "remote", "origin", "mirror", "push", "command"],
          mountNeeds: ["keep candidate-priority intake", "verify known risky executable controls", "verify details/summary toggles stay safe"]
        }),
        "calculator": Object.freeze({
          app: "calculator",
          label: "Calculator",
          route: "/applications/calculator",
          rootSelector: "#calculator-app",
          status: "domain-ready",
          priority: 30,
          point: "Compute local calculator expressions and graphing state without network, filesystem, or destructive side effects.",
          domainPack: "calculator-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["calculator-shell", "mode-toolbar", "display", "keypad", "graphing-panel"],
          expectedFeeds: ["display", "history", "graph-output"],
          expectedFields: ["expression-input", "mode-select"],
          expectedActionFamilies: ["digit-entry", "operator-entry", "clear", "evaluate", "mode-switch"],
          knownRiskFamilies: [],
          neverExecute: ["external code execution", "network calls"],
          decodeHints: ["calculator", "display", "mode", "keypad", "graph", "evaluate"],
          mountNeeds: ["low-risk domain pack", "keypad/action family grouping", "display/status-feed classification"]
        }),
        "document": Object.freeze({
          app: "document",
          label: "Document Editor",
          route: "/applications/document",
          rootSelector: "#document-app",
          status: "domain-ready",
          priority: 40,
          point: "Edit, paginate, import/export, and optionally AI-assist local documents without losing author intent or executing hidden mutations.",
          domainPack: "document-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["library", "toolbar", "document-canvas", "pagination", "ai-panel"],
          expectedFeeds: ["status", "current-path", "pagination-report"],
          expectedFields: ["title", "body", "search", "ai-prompt"],
          expectedActionFamilies: ["open", "save", "export", "library-refresh", "ai-assist"],
          knownRiskFamilies: ["save-overwrite", "export-file", "ai-network-assist"],
          neverExecute: ["silent overwrite", "silent upload", "network AI action without policy"],
          decodeHints: ["document", "library", "page", "readonly", "export", "ai"],
          mountNeeds: ["contenteditable/source-preservation hints", "document object/law pack", "save/export proof policy"]
        }),
        "spreadsheet": Object.freeze({
          app: "spreadsheet",
          label: "Spreadsheet",
          route: "/applications/spreadsheet",
          rootSelector: "#spreadsheet-app",
          status: "domain-ready",
          priority: 50,
          point: "Edit a workbook grid, formulas, charts, code-runtime cells, and imports/exports without unsafe file or code execution.",
          domainPack: "spreadsheet-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["workbook-shell", "formula-bar", "grid", "chart-panel", "code-runtime-panel"],
          expectedFeeds: ["status", "formula-result", "chart-output", "runtime-log"],
          expectedFields: ["cell-input", "formula-bar", "sheet-name", "import-file"],
          expectedActionFamilies: ["cell-edit", "formula-evaluate", "chart-build", "import-export", "code-runtime"],
          knownRiskFamilies: ["file-import", "file-export", "formula-external-ref", "code-execution"],
          neverExecute: ["untrusted code", "silent file export", "external formula fetch"],
          decodeHints: ["spreadsheet", "formula", "grid", "cell", "chart", "runtime", "xlsx"],
          mountNeeds: ["virtualized-grid recognition", "formula/code execution policy", "import/export family counters"]
        }),
        "onlyoffice": Object.freeze({
          app: "onlyoffice",
          label: "ONLYOFFICE",
          route: "/applications/onlyoffice",
          rootSelector: "#onlyoffice-app",
          status: "domain-ready",
          priority: 60,
          point: "Select or create workbook files and embed an external editor surface with explicit iframe/file-operation boundaries.",
          domainPack: "onlyoffice-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["workbook-library", "file-list", "editor-frame", "upload-panel"],
          expectedFeeds: ["library-status", "current-path", "editor-status"],
          expectedFields: ["upload-file", "file-list-selection"],
          expectedActionFamilies: ["refresh-files", "new-workbook", "upload", "open-editor"],
          knownRiskFamilies: ["file-upload", "workbook-create", "external-editor"],
          neverExecute: ["silent file upload", "external iframe mutation without boundary"],
          decodeHints: ["onlyoffice", "workbook", "upload", "file-list", "iframe"],
          mountNeeds: ["iframe boundary law", "file operation policy", "external editor proof mode"]
        }),
        "terminal": Object.freeze({
          app: "terminal",
          label: "Terminal",
          route: "/applications/terminal",
          rootSelector: "#terminal-app",
          status: "high-risk-domain-ready",
          priority: 70,
          point: "Display and prepare local command/AI terminal work without executing commands during MCEL proof.",
          domainPack: "terminal-domain",
          adapter: "TerminalMcel",
          mcelElementId: "element.compute.terminal",
          contract: "pattern.terminal-session",
          concern: "concern.terminal-session",
          expectedRegions: ["terminal-shell", "cwd-panel", "analysis-panel", "xterm-surface"],
          expectedFeeds: ["terminal-output", "analysis-status", "ai-suggestion"],
          expectedFields: ["cwd", "timeout", "ai-prompt", "command-input"],
          expectedActionFamilies: ["suggest", "copy", "run-command", "clear"],
          knownRiskFamilies: ["command-execution", "cwd-change", "timeout-run"],
          neverExecute: ["shell command", "AI-generated command"],
          decodeHints: ["terminal", "xterm", "cwd", "timeout", "command", "run", "shell"],
          mountNeeds: ["no-command-execution proof policy", "xterm surface contract", "AI suggestion vs execution split"]
        }),
        "chat-console": Object.freeze({
          app: "chat-console",
          label: "Chat Console",
          route: "/applications/chat-console",
          rootSelector: "#chat-console-app",
          status: "domain-ready",
          priority: 80,
          point: "Manage chat/notebook threads, code/runtime cells, and archived state without unexpected execution or data loss.",
          domainPack: "chat-console-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["thread-list", "notebook", "cell-composer", "archive-controls"],
          expectedFeeds: ["notebook-output", "save-status", "cell-result"],
          expectedFields: ["thread-search", "cell-input", "rename-field"],
          expectedActionFamilies: ["new-thread", "archive", "clone", "rename", "run-cell"],
          knownRiskFamilies: ["code-cell-execution", "archive-delete", "thread-rename"],
          neverExecute: ["code/runtime cell", "destructive archive action"],
          decodeHints: ["chat-thread", "notebook", "cell", "archive", "code", "runtime"],
          mountNeeds: ["thread/cell domain pack", "code-cell proof policy", "archive mutation grouping"]
        }),
        "email": Object.freeze({
          app: "email",
          label: "Email",
          route: "/applications/email",
          rootSelector: "#email-app",
          status: "high-risk-domain-ready",
          priority: 90,
          point: "Read, search, compose, and manage email accounts while preventing accidental send, delete, sync, or credential exposure.",
          domainPack: "email-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["mailbox-list", "message-list", "message-view", "compose-panel", "account-settings"],
          expectedFeeds: ["sync-status", "send-status", "message-preview"],
          expectedFields: ["search", "to", "subject", "body", "account-credential"],
          expectedActionFamilies: ["search", "draft", "send", "delete", "sync", "account-connect"],
          knownRiskFamilies: ["send-email", "delete-message", "account-sync", "credential-save"],
          neverExecute: ["send email", "delete email", "credential/network mutation"],
          decodeHints: ["email", "compose", "draft", "send", "sync", "account", "credential"],
          mountNeeds: ["send/delete proof policy", "credential/network mutation contract", "message-list virtualization"]
        }),
        "code-editor": Object.freeze({
          app: "code-editor",
          label: "Code Editor",
          route: "/applications/code-editor",
          rootSelector: "#code-editor-app",
          status: "high-risk-domain-ready",
          priority: 100,
          point: "Inspect and edit project files, Aider context/actions, and output streams without applying patches or running code unintentionally.",
          domainPack: "code-editor-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["file-map", "editor", "aider-context", "aider-actions", "output"],
          expectedFeeds: ["aider-output", "documentation-viewport", "save-status"],
          expectedFields: ["file-search", "instruction-draft", "editor-buffer"],
          expectedActionFamilies: ["open-file", "save-file", "aider-plan", "aider-apply", "run-code"],
          knownRiskFamilies: ["file-write", "patch-apply", "code-execution", "aider-mutation"],
          neverExecute: ["patch apply", "file write", "code run"],
          decodeHints: ["code-editor", "file-map", "aider", "output", "apply", "run"],
          mountNeeds: ["file-write policy", "Aider action split", "editor buffer serialization"]
        }),
        "file-explorer": Object.freeze({
          app: "file-explorer",
          label: "File Explorer",
          route: "/applications/file-explorer",
          rootSelector: "#file-explorer-app",
          status: "domain-ready",
          priority: 110,
          point: "Browse local files read-only, preview paths, and search without delete/write execution.",
          domainPack: "file-explorer-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["roots", "path-bar", "file-list", "preview"],
          expectedFeeds: ["status", "preview"],
          expectedFields: ["path", "search"],
          expectedActionFamilies: ["root-select", "up", "search", "preview"],
          knownRiskFamilies: ["filesystem-read-boundary"],
          neverExecute: ["delete", "write", "move"],
          decodeHints: ["file-explorer", "roots", "path", "list", "preview", "search"],
          mountNeeds: ["read-only filesystem contract", "path boundary hints", "preview/feed classification"]
        }),
        "website-builder": Object.freeze({
          app: "website-builder",
          label: "Website Builder",
          route: "/applications/website-builder",
          rootSelector: "#website-builder-app",
          status: "high-risk-domain-ready",
          priority: 120,
          point: "Manage site manifests and publish lanes while preventing accidental local/remote deployment.",
          domainPack: "website-builder-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["site-list", "manifest-editor", "preview", "publish-controls"],
          expectedFeeds: ["save-status", "publish-status", "site-preview"],
          expectedFields: ["site-name", "metadata", "content-source"],
          expectedActionFamilies: ["save", "preview", "publish-dev", "publish-local", "publish-remote"],
          knownRiskFamilies: ["site-save", "publish-local", "publish-remote", "docker-lane"],
          neverExecute: ["publish", "docker lane mutation", "remote deploy"],
          decodeHints: ["website-builder", "site", "manifest", "publish", "docker", "remote"],
          mountNeeds: ["publish proof policy", "site manifest IR", "preview iframe boundary"]
        }),
        "worker": Object.freeze({
          app: "worker",
          label: "Worker",
          route: "/applications/worker",
          rootSelector: "#worker-app",
          status: "high-risk-domain-ready",
          priority: 130,
          point: "Configure remote/local AI worker marketplace settings without registering, renting, or mutating network state during proof.",
          domainPack: "worker-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["seller-panel", "buyer-policy-panel", "network-hubs", "rental-status"],
          expectedFeeds: ["registration-status", "network-status", "rental-status"],
          expectedFields: ["network-rpc", "chain-id", "price", "policy"],
          expectedActionFamilies: ["select-network", "register-worker", "rent-worker", "save-policy"],
          knownRiskFamilies: ["network-registration", "payment/rental", "credential-network-mutation"],
          neverExecute: ["network registration", "payment/rental", "remote mutation"],
          decodeHints: ["worker", "network", "hub", "rpc", "chain", "register", "rent"],
          mountNeeds: ["network mutation contract", "payment/rental proof policy", "credential boundary"]
        }),
        "wallet": Object.freeze({
          app: "wallet",
          label: "Wallet",
          route: "/applications/wallet",
          rootSelector: "#wallet-app",
          status: "high-risk-domain-ready",
          priority: 140,
          point: "Connect/disconnect wallet providers and inspect account state without signing, sending, or network mutation.",
          domainPack: "wallet-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["connection-card", "account-state", "network-state", "actions"],
          expectedFeeds: ["status-pill", "connection-log", "network-status"],
          expectedFields: ["provider", "network"],
          expectedActionFamilies: ["connect", "disconnect", "switch-network", "sign", "send"],
          knownRiskFamilies: ["wallet-connect", "sign-message", "send-transaction", "network-switch"],
          neverExecute: ["sign", "send transaction", "network switch without policy"],
          decodeHints: ["wallet", "ethers", "connect", "disconnect", "account", "network", "sign"],
          mountNeeds: ["wallet/signing proof policy", "provider boundary", "transaction/signature family counters"]
        }),
        "game-editor": Object.freeze({
          app: "game-editor",
          label: "Game Editor",
          route: "/applications/game-editor",
          rootSelector: "#game-editor-app",
          status: "domain-ready",
          priority: 150,
          point: "Edit project-backed scene state, assets, and previews without destructive project writes during proof.",
          domainPack: "game-editor-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["scene-list", "inspector", "canvas", "asset-panel"],
          expectedFeeds: ["scene-status", "preview"],
          expectedFields: ["scene-name", "property-editor"],
          expectedActionFamilies: ["select-scene", "save-scene", "preview", "asset-import"],
          knownRiskFamilies: ["project-write", "asset-import"],
          neverExecute: ["destructive project write", "asset import without policy"],
          decodeHints: ["game-editor", "scene", "asset", "inspector", "preview"],
          mountNeeds: ["route alias layout-builder", "scene/asset domain pack", "project-write policy"]
        }),
        "webgl": Object.freeze({
          app: "webgl",
          label: "Game Surface",
          route: "/applications/webgl",
          rootSelector: "#webgl-demo",
          status: "domain-ready",
          priority: 160,
          point: "Render a visual/game surface and prove canvas ownership, resize, and animation boundaries.",
          domainPack: "webgl-domain",
          adapter: "planner-generic-adapter",
          expectedRegions: ["canvas-surface", "scene-status"],
          expectedFeeds: ["scene-status"],
          expectedFields: [],
          expectedActionFamilies: ["scene-select", "pause", "resume"],
          knownRiskFamilies: ["gpu-animation-boundary"],
          neverExecute: ["unbounded animation loop", "unsafe GPU/resource churn"],
          decodeHints: ["webgl", "canvas", "scene", "animation"],
          mountNeeds: ["canvas ownership contract", "animation/performance law", "resize proof"]
        }),
        "mcel-lab": Object.freeze({
          app: "mcel-lab",
          label: "MCEL Lab",
          route: "/applications/mcel-lab",
          rootSelector: "#mcel-lab-app",
          status: "domain-ready",
          priority: 900,
          point: "Self-host the MCEL compiler, runtime proof, canonical specimens, and planner without confusing lab chrome for specimen content.",
          domainPack: "mcel-lab-domain",
          adapter: "native",
          expectedRegions: ["source", "editor", "runtime", "canonical-specimen", "proof-output"],
          expectedFeeds: ["compiler-log", "proof-report", "planner-report"],
          expectedFields: ["source-html", "scenario-select", "theme-select"],
          expectedActionFamilies: ["compile", "serialize", "repair", "mount-specimen", "inspect-lens"],
          knownRiskFamilies: ["self-recursion", "specimen-boundary"],
          neverExecute: ["specimen controls outside proof policy"],
          decodeHints: ["mcel-lab", "canonical", "proof", "runtime", "planner"],
          mountNeeds: ["exclude lab chrome from specimen graph", "planner self-audit"]
        })
      });

      function clone(value) {
        return JSON.parse(JSON.stringify(value));
      }

      function normalizeApp(app) {
        return String(app || "task-manager").trim().toLowerCase();
      }

      function allPlans() {
        return Object.values(APP_PLANS)
          .map(clone)
          .sort((left, right) => Number(left.priority || 999) - Number(right.priority || 999));
      }

      function planFor(app, overrides = {}) {
        const key = normalizeApp(app);
        const base = APP_PLANS[key] || {
          app: key,
          label: String(app || "Unknown App"),
          route: `/applications/${key}`,
          rootSelector: `#${key}-app`,
          status: "unknown",
          priority: 500,
          point: "Unknown app; mount read-only first and collect root, regions, fields, actions, feeds, and risky controls.",
          domainPack: `${key}-domain`,
          adapter: "planner-generic-adapter",
          expectedRegions: [],
          expectedFeeds: [],
          expectedFields: [],
          expectedActionFamilies: [],
          knownRiskFamilies: [],
          neverExecute: ["unknown destructive actions"],
          decodeHints: [key],
          mountNeeds: ["read-only discovery pass", "adapter/domain pack planning"]
        };
        return {
          ...clone(base),
          ...clone(overrides || {}),
          app: key
        };
      }

      function plansByStatus(status) {
        return allPlans().filter((plan) => plan.status === status);
      }

      function mountQueue() {
        return allPlans().filter((plan) => !["domain-proven", "native-lab"].includes(plan.status));
      }

      function riskLevel(plan) {
        const status = plan?.status || "";
        if (status.includes("high-risk")) return "high";
        if ((plan?.knownRiskFamilies || []).length >= 3) return "high";
        if ((plan?.knownRiskFamilies || []).length) return "medium";
        return "low";
      }

      function supercutPacksFor(plan) {
        const packs = ["core-html", "core-action-risk"];
        if (plan?.domainPack && plan.domainPack !== "needs-domain-pack") packs.push(plan.domainPack);
        return packs;
      }

      function summaryFor(plan) {
        const safePlan = plan || {};
        const risky = (safePlan.knownRiskFamilies || []).length;
        const needs = (safePlan.mountNeeds || []).slice(0, 3).join("; ");
        return `${safePlan.label || safePlan.app}: ${safePlan.point || "purpose unknown"} Risk families: ${risky}. Next: ${needs || "read-only mount discovery"}.`;
      }

      function inspectMountedDocument(doc, plan = {}) {
        const root = doc?.querySelector?.(plan.rootSelector || `#${plan.app}-app`) || null;
        const controls = root ? Array.from(root.querySelectorAll("button, a[href], input, select, textarea, summary, [role='button']")) : [];
        const feeds = root ? Array.from(root.querySelectorAll("output, pre, code, [aria-live], [role='status'], [role='log']")) : [];
        const editable = root ? Array.from(root.querySelectorAll("[contenteditable='true'], textarea, input")) : [];
        const frames = root ? Array.from(root.querySelectorAll("iframe, canvas")) : [];
        const text = root?.textContent || "";
        const matchedHints = (plan.decodeHints || []).filter((hint) => text.toLowerCase().includes(String(hint).toLowerCase()));
        return {
          app: plan.app || "",
          rootPresent: Boolean(root),
          controlCount: controls.length,
          feedCount: feeds.length,
          editableCount: editable.length,
          embeddedSurfaceCount: frames.length,
          matchedHints,
          evidenceReady: Boolean(root && (controls.length || feeds.length || editable.length || matchedHints.length))
        };
      }

      function toCanonicalOption(plan) {
        return {
          value: plan.app,
          label: plan.label,
          route: plan.route,
          rootSelector: plan.rootSelector,
          point: plan.point,
          status: plan.status
        };
      }

      function plannerSnapshot(currentApp = "") {
        const plans = allPlans();
        const current = currentApp ? planFor(currentApp) : null;
        return {
          version: PLANNER_VERSION,
          current,
          totalPlans: plans.length,
          domainProven: plans.filter((plan) => plan.status === "domain-proven").length,
          plannerReady: plans.filter((plan) => plan.status === "planner-ready").length,
          highRisk: plans.filter((plan) => riskLevel(plan) === "high").length,
          mountQueue: mountQueue().map(toCanonicalOption)
        };
      }


      function selectorForRoot(plan = {}) {
        return plan.rootSelector || `#${plan.app || "app"}-app`;
      }

      function rootIdFor(plan = {}) {
        return selectorForRoot(plan).replace(/^#/, "");
      }

      function requiredIdsFor(plan = {}) {
        return Array.from(new Set([rootIdFor(plan), ...(plan.requiredIds || [])].filter(Boolean)));
      }

      function dangerousSelectorsFor(plan = {}) {
        return Array.from(new Set([
          ...(plan.dangerousSelectors || []),
          ...(plan.knownRiskFamilies || []).flatMap((family) => {
            const token = String(family || "").replace(/[^a-z0-9_-]+/gi, "-");
            return token ? [
              `[id*="${token}"]`,
              `[data-mc-component-id*="${token}"]`,
              `[data-action*="${token}"]`,
              `[data-task-action*="${token}"]`
            ] : [];
          })
        ].filter(Boolean)));
      }

      function planRegionsFor(plan = {}) {
        const rootSelector = selectorForRoot(plan);
        return (plan.expectedRegions || ["root"]).map((role, index) => ({
          selector: index === 0 ? rootSelector : `${rootSelector} [data-mc-component-id*="${role}"], ${rootSelector} [class*="${role}"], ${rootSelector} [aria-label*="${role}"]`,
          role,
          kind: index === 0 ? "root" : "region",
          label: role.replace(/[-_]+/g, " ")
        }));
      }

      function planActionsFor(plan = {}) {
        const rootSelector = selectorForRoot(plan);
        return (plan.expectedActionFamilies || []).map((family) => ({
          selector: `${rootSelector} button, ${rootSelector} a[href], ${rootSelector} [role="button"]`,
          role: family,
          risk: (plan.knownRiskFamilies || []).some((risk) => String(risk).includes(family) || String(family).includes(risk))
            ? "blocked-family"
            : "inspect-only",
          label: family.replace(/[-_]+/g, " ")
        }));
      }

      function createUnavailableReport(options = {}) {
        const plan = planFor(options.app || "", options);
        return {
          app: plan.app || options.app || "",
          rootSelector: options.rootSelector || plan.rootSelector || "",
          enrichmentActive: false,
          rootPresent: false,
          enrichedElementCount: 0,
          regionCount: 0,
          componentCount: 0,
          fieldCount: 0,
          actionControlCount: 0,
          fitLawCount: 0,
          layoutLawStatus: "unavailable",
          violations: [{law: options.law || "planner-generic-adapter", status: "failed", message: options.message || "generic specimen adapter unavailable"}],
          destructiveActionsExecuted: false,
          safetyClaim: `planner adapter reads ${plan.label || plan.app || "specimen"} DOM and never executes controls`,
          reason: options.reason || "planner-unavailable",
          appliedAt: new Date().toISOString()
        };
      }

      function summarizeSupercut(supercut = {}) {
        return {
          supercutActive: Boolean(supercut?.active || supercut?.architectureStatus === "ready" || supercut?.rewritePreview?.length),
          supercutComponentCount: supercut?.executableComponentCount || supercut?.componentCount || supercut?.components?.length || 0,
          supercutOriginalPointCount: supercut?.originalPointCount || supercut?.originalPoints?.length || 0,
          supercutOriginalPoints: supercut?.originalPoints || [],
          supercutRoundCount: supercut?.rectificationRounds?.length || supercut?.roundsCompleted || 0,
          supercutRectificationRounds: supercut?.rectificationRounds || [],
          supercutCssObjectCount: supercut?.cssObjectCatalog?.length || 0,
          supercutRuntimeChanges: supercut?.runtimeChanges || [],
          supercutArchitectureStatus: supercut?.architectureStatus || supercut?.architecture?.status || "legacy",
          supercutPacksLoaded: supercut?.packsLoaded || [],
          supercutPacksLoadedCount: supercut?.packsLoaded?.length || 0,
          supercutRulesFired: supercut?.rulesFired || supercut?.metrics?.rulesFired || 0,
          supercutBlackboardRecordCount: supercut?.blackboardRecordCount || supercut?.blackboard?.records?.length || 0,
          supercutRewritePreview: supercut?.rewritePreview || [],
          supercutRewritePreviewCount: supercut?.rewritePreview?.length || 0,
          supercutRewritePreviewSummary: supercut?.rewritePreviewSummary || {},
          supercutExplanationsReady: supercut?.explanationsReady || supercut?.explanations?.length || 0,
          supercutUnsafeActionsBlocked: supercut?.unsafeActionsBlocked || supercut?.metrics?.unsafeActionsBlocked || 0,
          supercutSourceMutations: supercut?.sourceMutations || 0,
          supercutRuntimeSourceMutations: supercut?.runtimeSourceMutations || 0
        };
      }

      function createGenericAdapter(planInput = {}) {
        const plan = planFor(planInput.app || "", planInput);
        const rootSelector = selectorForRoot(plan);
        return {
          BODY_ENRICHMENT_ATTRIBUTE: `data-mcel-${plan.app || "specimen"}-planner-enrichment`,
          ENRICHMENT_STYLE_ID: `mcel-lab-${plan.app || "specimen"}-planner-enrichment-style`,
          ENRICHMENT_CLASS: `mcel-canonical-${plan.app || "specimen"}-planner-enriched`,
          REGION_ENRICHMENT: planRegionsFor(plan),
          COMPONENT_ENRICHMENT: [],
          FIELD_ENRICHMENT: (plan.expectedFields || []).map((role) => ({role, selector: `${rootSelector} input, ${rootSelector} select, ${rootSelector} textarea, ${rootSelector} label`})),
          PANEL_LENS: planRegionsFor(plan),
          ACTION_LENS: planActionsFor(plan),
          createUnavailableReport,
          applyCanonicalMcelSemantics(options = {}) {
            const doc = options.document || global.document;
            const root = doc?.querySelector?.(options.rootSelector || rootSelector) || null;
            if (!doc?.body || !root) {
              return createUnavailableReport({
                ...options,
                app: plan.app,
                rootSelector: options.rootSelector || rootSelector,
                law: "planner-root",
                message: `${plan.label || plan.app} root not found`
              });
            }
            doc.documentElement?.setAttribute?.(this.BODY_ENRICHMENT_ATTRIBUTE, "active");
            doc.body.setAttribute(this.BODY_ENRICHMENT_ATTRIBUTE, "active");
            doc.body.classList.add(this.ENRICHMENT_CLASS);
            root.setAttribute("data-mcel-enriched", "true");
            root.setAttribute("data-mcel-role", "planner-root");
            root.setAttribute("data-mcel-kind", "canonical-app-specimen");
            root.setAttribute("data-mcel-fit", "purpose-aware");
            root.setAttribute("data-mcel-proof-surface", "planner-read-only");

            const components = Array.from(root.querySelectorAll?.("section, article, aside, main, header, footer, nav, form, details, [data-mc-component-id], [role], [id], [class]") || []);
            const fields = Array.from(root.querySelectorAll?.("input, select, textarea, label") || []);
            const actions = Array.from(root.querySelectorAll?.("button, a[href], [role='button']") || []);
            const feeds = Array.from(root.querySelectorAll?.("output, pre, code, [aria-live], [role='status'], [role='log']") || []);
            const riskHints = (plan.knownRiskFamilies || []).map((risk) => String(risk).toLowerCase());
            const riskControls = actions.filter((element) => {
              const text = [
                element.id || "",
                element.getAttribute?.("data-mc-component-id") || "",
                element.getAttribute?.("data-action") || "",
                element.getAttribute?.("data-task-action") || "",
                element.textContent || "",
                element.getAttribute?.("aria-label") || ""
              ].join(" ").toLowerCase();
              return riskHints.some((risk) => text.includes(risk.replace(/-/g, " ")) || text.includes(risk));
            });

            components.slice(0, 260).forEach((element) => {
              element.setAttribute("data-mcel-enrichment-source", "planner-purpose-aware-intake");
              element.setAttribute("data-mcel-fit-context", plan.app || "specimen");
            });
            actions.forEach((element) => {
              element.setAttribute("data-mcel-action-risk", riskControls.includes(element) ? "potential-risk" : "inspect-only");
              element.setAttribute("data-mcel-mutates", riskControls.includes(element) ? "potential" : "false");
            });

            let supercut = null;
            if (global.McelSupercut?.translateRuntime) {
              try {
                supercut = global.McelSupercut.translateRuntime({
                  document: doc,
                  root,
                  rootSelector: options.rootSelector || rootSelector,
                  app: plan.app || "planner-specimen",
                  specimenId: plan.app || "planner-specimen",
                  packs: supercutPacksFor(plan),
                  mode: "planner-read-only",
                  rounds: 3,
                  maxComponents: 260,
                  reason: options.reason || "planner-generic-adapter"
                });
              } catch (error) {
                supercut = {active: false, architectureStatus: "error", message: error?.message || "Supercut failed"};
              }
            }

            const supercutSummary = summarizeSupercut(supercut || {});
            return {
              app: plan.app,
              route: options.route || plan.route,
              rootSelector: options.rootSelector || rootSelector,
              enrichmentActive: true,
              rootPresent: true,
              planStatus: plan.status,
              point: plan.point,
              regions: planRegionsFor(plan).map((region, index) => ({
                ...region,
                present: index === 0 || Boolean(root.querySelector?.(region.selector.replace(`${rootSelector} `, ""))),
                fitContext: index === 0 ? "root" : "planned"
              })),
              regionCount: plan.expectedRegions?.length || 1,
              componentCount: components.length + 1,
              enrichedElementCount: Math.min(260, components.length + fields.length + actions.length + feeds.length + 1),
              fieldCount: fields.length,
              actionControlCount: actions.length,
              riskControlCount: riskControls.length,
              feedCount: feeds.length,
              riskControls: riskControls.slice(0, 24).map((element) => ({
                selector: element.id ? `#${element.id}` : element.getAttribute?.("data-mc-component-id") || element.tagName?.toLowerCase?.() || "control",
                role: "planned-risk-control",
                risk: "potential-risk",
                label: element.textContent?.trim?.() || element.getAttribute?.("aria-label") || element.id || "control"
              })),
              fitLawCount: (plan.expectedRegions?.length || 1) + fields.length + actions.length + feeds.length,
              layoutLawStatus: "ready",
              violations: [],
              overlayMode: "purpose-aware planner enrichment; no control activation",
              destructiveActionsExecuted: false,
              safetyClaim: `planner reads and annotates ${plan.label || plan.app}; it never executes ${plan.neverExecute?.join(", ") || "application controls"}`,
              ...supercutSummary,
              reason: options.reason || "planner-generic-adapter",
              appliedAt: new Date().toISOString()
            };
          },
          clearCanonicalMcelSemantics(options = {}) {
            const doc = options.document || global.document;
            if (!doc?.body) return false;
            doc.documentElement?.removeAttribute?.(this.BODY_ENRICHMENT_ATTRIBUTE);
            doc.body.removeAttribute(this.BODY_ENRICHMENT_ATTRIBUTE);
            doc.body.classList.remove(this.ENRICHMENT_CLASS);
            Array.from(doc.querySelectorAll?.("[data-mcel-enrichment-source='planner-purpose-aware-intake'], [data-mcel-action-risk], [data-mcel-mutates], [data-mcel-proof-surface]") || []).forEach((element) => {
              element.removeAttribute("data-mcel-enrichment-source");
              element.removeAttribute("data-mcel-action-risk");
              element.removeAttribute("data-mcel-mutates");
              element.removeAttribute("data-mcel-proof-surface");
            });
            global.McelSupercut?.clearRuntime?.({document: doc, rootSelector: options.rootSelector || rootSelector});
            return true;
          }
        };
      }

      function canonicalOptions() {
        return mountQueue().map(toCanonicalOption);
      }

      global.McelSpecimenPlanner = {
        PLANNER_VERSION,
        APP_PLANS,
        allPlans,
        planFor,
        plansByStatus,
        mountQueue,
        riskLevel,
        supercutPacksFor,
        summaryFor,
        inspectMountedDocument,
        toCanonicalOption,
        plannerSnapshot,
        requiredIdsFor,
        dangerousSelectorsFor,
        createGenericAdapter,
        canonicalOptions
      };
    })(window);
