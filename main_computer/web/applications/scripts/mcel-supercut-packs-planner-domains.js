    (function (global) {
      "use strict";

      const PLANNER_DOMAIN_PACK_VERSION = "0.2.0";

      const PLANNER_DOMAIN_PLANS = Object.freeze([
        {
          app: "document",
          id: "document-domain",
          label: "Document Editor",
          rootId: "document-app",
          rootSelector: "#document-app",
          riskLevel: "medium",
          expectedRegions: ["library", "toolbar", "document-canvas", "pagination", "ai-panel"],
          expectedFeeds: ["status", "current-path", "pagination-report"],
          expectedFields: ["title", "body", "search", "ai-prompt"],
          safeActions: ["open", "refresh", "search", "preview", "format", "paginate"],
          riskyActions: [
            {family: "document.save-overwrite", role: "save-document", risk: "remote-mutation", policy: "no-submit", terms: ["save", "overwrite", "write document"]},
            {family: "document.export-file", role: "export-document", risk: "remote-mutation", policy: "no-submit", terms: ["export", "download", "pdf", "docx"]},
            {family: "document.ai-assist", role: "ai-assist", risk: "analysis", policy: "inspect-only", terms: ["ai", "assist", "summarize", "rewrite"]}
          ],
          decodeHints: ["document", "page", "library", "editor", "pagination", "export", "save", "ai"]
        },
        {
          app: "spreadsheet",
          id: "spreadsheet-domain",
          label: "Spreadsheet",
          rootId: "spreadsheet-app",
          rootSelector: "#spreadsheet-app",
          riskLevel: "medium",
          expectedRegions: ["workbook-shell", "grid", "formula-bar", "chart-panel", "import-export"],
          expectedFeeds: ["formula-result", "validation", "status"],
          expectedFields: ["cell", "formula", "sheet-name", "range"],
          safeActions: ["select", "sort", "filter", "format", "recalculate", "chart"],
          riskyActions: [
            {family: "spreadsheet.import-file", role: "import-file", risk: "remote-mutation", policy: "no-submit", terms: ["import", "xlsx", "csv", "upload"]},
            {family: "spreadsheet.export-file", role: "export-file", risk: "remote-mutation", policy: "no-submit", terms: ["export", "download"]},
            {family: "spreadsheet.formula-runtime", role: "formula-runtime", risk: "command-execution", policy: "no-command-execution", terms: ["runtime", "script", "macro", "execute", "custom function"]}
          ],
          decodeHints: ["spreadsheet", "workbook", "sheet", "cell", "formula", "chart", "csv", "xlsx"]
        },
        {
          app: "onlyoffice",
          id: "onlyoffice-domain",
          label: "ONLYOFFICE",
          rootId: "onlyoffice-app",
          rootSelector: "#onlyoffice-app",
          riskLevel: "medium",
          expectedRegions: ["document-server-frame", "open-panel", "status", "connector"],
          expectedFeeds: ["server-status", "document-status", "load-report"],
          expectedFields: ["document-url", "title", "mode"],
          safeActions: ["open", "preview", "refresh-status"],
          riskyActions: [
            {family: "onlyoffice.document-server", role: "document-server", risk: "server-control", policy: "no-click", terms: ["server", "document server", "connect", "launch"]},
            {family: "onlyoffice.save-export", role: "save-export", risk: "remote-mutation", policy: "no-submit", terms: ["save", "export", "download", "upload"]}
          ],
          decodeHints: ["onlyoffice", "document server", "iframe", "editor", "connector"]
        },
        {
          app: "terminal",
          id: "terminal-domain",
          label: "Terminal",
          rootId: "terminal-app",
          rootSelector: "#terminal-app",
          riskLevel: "high",
          expectedRegions: ["terminal-shell", "command-input", "output-log", "session-toolbar"],
          expectedFeeds: ["terminal-output", "command-history", "status"],
          expectedFields: ["command", "cwd", "session"],
          safeActions: ["copy", "clear-output", "history"],
          riskyActions: [
            {family: "terminal.command-execution", role: "run-command", risk: "command-execution", policy: "no-command-execution", terms: ["run", "execute", "send", "command", "shell", "terminal", "stdin"]},
            {family: "terminal.session-control", role: "session-control", risk: "operational", policy: "no-click", terms: ["start session", "stop session", "restart", "kill"]}
          ],
          decodeHints: ["terminal", "command", "shell", "stdout", "stderr", "cwd", "session"]
        },
        {
          app: "chat-console",
          id: "chat-console-domain",
          label: "Chat Console",
          rootId: "chat-console-app",
          rootSelector: "#chat-console-app",
          riskLevel: "medium",
          expectedRegions: ["conversation", "composer", "tool-call-panel", "attachment-panel"],
          expectedFeeds: ["messages", "status", "tool-output"],
          expectedFields: ["message", "model", "attachment"],
          safeActions: ["draft", "copy", "clear"],
          riskyActions: [
            {family: "chat-console.send-message", role: "send-message", risk: "remote-mutation", policy: "no-submit", terms: ["send", "submit", "message"]},
            {family: "chat-console.tool-call", role: "tool-call", risk: "command-execution", policy: "no-command-execution", terms: ["tool", "function", "execute", "run"]},
            {family: "chat-console.attachment", role: "attachment-upload", risk: "remote-mutation", policy: "no-submit", terms: ["attach", "upload", "file"]}
          ],
          decodeHints: ["chat", "message", "composer", "model", "tool", "attachment"]
        },
        {
          app: "email",
          id: "email-domain",
          label: "Email",
          rootId: "email-app",
          rootSelector: "#email-app",
          riskLevel: "high",
          expectedRegions: ["mailbox", "message-list", "composer", "preview", "account-panel"],
          expectedFeeds: ["mail-status", "send-report", "sync-log"],
          expectedFields: ["to", "cc", "subject", "body", "search"],
          safeActions: ["draft", "search", "open", "archive-preview"],
          riskyActions: [
            {family: "email.send", role: "send-email", risk: "remote-mutation", policy: "no-submit", terms: ["send", "reply", "forward"]},
            {family: "email.delete", role: "delete-email", risk: "destructive", policy: "no-click", terms: ["delete", "trash", "remove"]},
            {family: "email.credential-sync", role: "mail-sync", risk: "credential-network-mutation", policy: "no-submit", terms: ["account", "credential", "login", "sync"]}
          ],
          decodeHints: ["email", "mail", "message", "inbox", "composer", "smtp", "imap"]
        },
        {
          app: "code-editor",
          id: "code-editor-domain",
          label: "Code Editor",
          rootId: "code-editor-app",
          rootSelector: "#code-editor-app",
          riskLevel: "high",
          expectedRegions: ["editor", "file-tabs", "terminal-output", "diagnostics", "preview"],
          expectedFeeds: ["diagnostics", "build-output", "preview-log"],
          expectedFields: ["filename", "code", "search", "command"],
          safeActions: ["format", "search", "preview", "copy"],
          riskyActions: [
            {family: "code-editor.run-code", role: "run-code", risk: "command-execution", policy: "no-command-execution", terms: ["run", "execute", "build", "test", "npm", "python", "command"]},
            {family: "code-editor.save-file", role: "save-file", risk: "remote-mutation", policy: "no-submit", terms: ["save", "write", "overwrite"]},
            {family: "code-editor.package-install", role: "package-install", risk: "credential-network-mutation", policy: "no-submit", terms: ["install", "package", "dependency"]}
          ],
          decodeHints: ["code", "editor", "file", "diagnostic", "build", "run", "terminal"]
        },
        {
          app: "file-explorer",
          id: "file-explorer-domain",
          label: "File Explorer",
          rootId: "file-explorer-app",
          rootSelector: "#file-explorer-app",
          riskLevel: "medium",
          expectedRegions: ["tree", "file-list", "preview", "path-toolbar", "operations"],
          expectedFeeds: ["operation-status", "preview", "selection"],
          expectedFields: ["path", "filename", "search"],
          safeActions: ["open", "preview", "refresh", "select"],
          riskyActions: [
            {family: "file-explorer.delete", role: "delete-file", risk: "destructive", policy: "no-click", terms: ["delete", "remove", "trash"]},
            {family: "file-explorer.move-rename", role: "move-rename", risk: "remote-mutation", policy: "no-submit", terms: ["move", "rename", "copy", "write"]},
            {family: "file-explorer.upload-download", role: "transfer-file", risk: "remote-mutation", policy: "no-submit", terms: ["upload", "download", "import", "export"]}
          ],
          decodeHints: ["file", "folder", "path", "directory", "preview", "explorer"]
        },
        {
          app: "website-builder",
          id: "website-builder-domain",
          label: "Website Builder",
          rootId: "website-builder-app",
          rootSelector: "#website-builder-app",
          riskLevel: "high",
          expectedRegions: ["canvas", "component-palette", "style-panel", "publish-panel", "preview"],
          expectedFeeds: ["build-status", "preview-report", "publish-log"],
          expectedFields: ["title", "slug", "css", "content", "prompt"],
          safeActions: ["preview", "layout", "style", "component-select"],
          riskyActions: [
            {family: "website-builder.publish", role: "publish-site", risk: "remote-mutation", policy: "no-submit", terms: ["publish", "deploy", "upload", "host"]},
            {family: "website-builder.export", role: "export-site", risk: "remote-mutation", policy: "no-submit", terms: ["export", "download", "zip"]},
            {family: "website-builder.ai-generate", role: "ai-generate", risk: "analysis", policy: "inspect-only", terms: ["generate", "ai", "prompt"]}
          ],
          decodeHints: ["website", "builder", "canvas", "publish", "deploy", "component", "style"]
        },
        {
          app: "worker",
          id: "worker-domain",
          label: "Worker",
          rootId: "worker-app",
          rootSelector: "#worker-app",
          riskLevel: "high",
          expectedRegions: ["queue", "job-detail", "worker-status", "runtime-controls", "logs"],
          expectedFeeds: ["job-log", "status", "queue-report"],
          expectedFields: ["job", "payload", "schedule", "worker"],
          safeActions: ["inspect", "refresh", "copy-log"],
          riskyActions: [
            {family: "worker.start-stop", role: "worker-control", risk: "operational", policy: "no-click", terms: ["start", "stop", "restart", "pause", "resume"]},
            {family: "worker.job-mutation", role: "job-mutation", risk: "remote-mutation", policy: "no-submit", terms: ["enqueue", "cancel", "retry", "delete", "schedule"]},
            {family: "worker.execute-job", role: "execute-job", risk: "command-execution", policy: "no-command-execution", terms: ["run", "execute", "dispatch"]}
          ],
          decodeHints: ["worker", "job", "queue", "runtime", "schedule", "log"]
        },
        {
          app: "wallet",
          id: "wallet-domain",
          label: "Wallet",
          rootId: "wallet-app",
          rootSelector: "#wallet-app",
          riskLevel: "high",
          expectedRegions: ["account", "balance", "transaction-composer", "network", "history"],
          expectedFeeds: ["balance-status", "transaction-history", "network-status"],
          expectedFields: ["address", "amount", "network", "memo"],
          safeActions: ["copy-address", "refresh-balance", "view-history"],
          riskyActions: [
            {family: "wallet.send-transaction", role: "send-transaction", risk: "credential-network-mutation", policy: "no-submit", terms: ["send", "transfer", "transaction", "broadcast"]},
            {family: "wallet.sign-approve", role: "sign-approve", risk: "credential-network-mutation", policy: "no-submit", terms: ["sign", "approve", "connect", "authorize"]},
            {family: "wallet.seed-secret", role: "secret-surface", risk: "credential-network-mutation", policy: "no-submit", terms: ["seed", "private key", "secret", "mnemonic"]}
          ],
          decodeHints: ["wallet", "address", "balance", "transaction", "sign", "network"]
        },
        {
          app: "game-editor",
          id: "game-editor-domain",
          label: "Game Editor",
          rootId: "game-editor-app",
          rootSelector: "#game-editor-app",
          riskLevel: "medium",
          expectedRegions: ["scene", "asset-library", "properties", "timeline", "playtest"],
          expectedFeeds: ["build-status", "asset-log", "playtest-output"],
          expectedFields: ["asset-name", "property", "script", "scene-name"],
          safeActions: ["select", "preview", "play-local", "inspect"],
          riskyActions: [
            {family: "game-editor.asset-mutation", role: "asset-mutation", risk: "remote-mutation", policy: "no-submit", terms: ["delete", "remove", "import", "export", "save"]},
            {family: "game-editor.script-runtime", role: "script-runtime", risk: "command-execution", policy: "no-command-execution", terms: ["script", "run", "execute", "build"]}
          ],
          decodeHints: ["game", "scene", "asset", "timeline", "play", "editor"]
        },
        {
          app: "webgl",
          id: "webgl-domain",
          label: "Game Surface",
          rootId: "webgl-demo",
          rootSelector: "#webgl-demo",
          riskLevel: "low",
          expectedRegions: ["canvas", "renderer", "controls", "stats"],
          expectedFeeds: ["fps", "render-status", "debug"],
          expectedFields: ["quality", "seed", "scene"],
          safeActions: ["play", "pause", "reset-camera", "quality"],
          riskyActions: [],
          decodeHints: ["webgl", "canvas", "renderer", "fps", "scene", "graphics"]
        },
        {
          app: "mcel-lab",
          id: "mcel-lab-domain",
          label: "MCEL Lab",
          rootId: "mcel-lab-app",
          rootSelector: "#mcel-lab-app",
          riskLevel: "medium",
          expectedRegions: ["acid-test", "canonical-app-test", "proof-panel", "lens-map", "planner"],
          expectedFeeds: ["proof-report", "layout-health", "specimen-status"],
          expectedFields: ["source", "specimen-select", "root-selector"],
          safeActions: ["inspect", "enrich", "proof", "mount"],
          riskyActions: [
            {family: "mcel-lab.patch-apply", role: "patch-apply", risk: "remote-mutation", policy: "no-submit", terms: ["apply patch", "write", "overwrite", "export"]},
            {family: "mcel-lab.specimen-control", role: "specimen-control", risk: "operational", policy: "inspect-only", terms: ["mount", "refresh", "inspect", "proof"]}
          ],
          decodeHints: ["mcel", "lab", "proof", "specimen", "planner", "lens"]
        }
      ]);

      function normalize(value) {
        return String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
      }

      function slug(value) {
        return normalize(value).replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "surface";
      }

      function source(record) {
        return [
          record?.tag || "",
          record?.domId || "",
          record?.classes?.join?.(" ") || "",
          record?.role || "",
          record?.componentId || "",
          record?.directText || "",
          record?.signature || "",
          record?.element?.getAttribute?.("aria-label") || "",
          record?.element?.getAttribute?.("title") || "",
          record?.element?.getAttribute?.("data-action") || "",
          record?.element?.getAttribute?.("data-command") || "",
          record?.element?.getAttribute?.("data-route") || ""
        ].join(" ").toLowerCase();
      }

      function fullSource(record) {
        return `${source(record)} ${normalize(record?.text || "")}`;
      }

      function actionLike(record) {
        const tag = record?.tag || "";
        if (tag === "summary") return false;
        return tag === "button" || tag === "a" || record?.role === "button" ||
          Boolean(record?.element?.matches?.("button, a[href], [role=\"button\"]"));
      }

      function fieldLike(record) {
        return ["input", "select", "textarea", "label"].includes(record?.tag);
      }

      function feedLike(record, plan) {
        const text = source(record);
        return ["output", "pre", "code"].includes(record?.tag) ||
          ["status", "log", "feed", "report", "output", "history", "diagnostic"].some((term) => text.includes(term)) ||
          (plan.expectedFeeds || []).some((feed) => text.includes(slug(feed)) || text.includes(normalize(feed)));
      }

      function containerLike(record) {
        if (!record || actionLike(record) || fieldLike(record)) return false;
        return ["section", "article", "aside", "main", "header", "footer", "nav", "form", "details", "div"].includes(record.tag) ||
          Number(record.controlCount || 0) > 0;
      }

      function isRoot(record, blackboard, plan) {
        return record?.element === blackboard?.rootNode ||
          record?.index === 0 ||
          record?.domId === plan.rootId ||
          record?.sourceSelector === plan.rootSelector;
      }

      function inPlan(record, blackboard, plan) {
        return Boolean(
          blackboard?.specimenId === plan.app ||
          blackboard?.rootNode?.id === plan.rootId ||
          record?.element?.closest?.(plan.rootSelector)
        );
      }

      function evidence(record, blackboard, plan, value, ruleId) {
        return blackboard.addEvidence(record, `${plan.app}-domain`, value, ruleId).id;
      }

      function addPurpose(record, blackboard, plan, purpose, role, contract, ruleId, extra = {}) {
        const ev = evidence(record, blackboard, plan, purpose, ruleId);
        blackboard.ensureComponent(record, {
          kind: extra.kind || (contract === "component.action" ? "action" : "panel"),
          role,
          purpose,
          contract,
          risk: extra.risk || "none",
          proofPolicy: extra.proofPolicy || "inspect-only",
          evidence: [ev]
        }, ruleId);
        blackboard.explain(record, `${plan.label} domain pack recognized ${purpose}.`, [record.sourceSelector, record.directText || record.signature], ruleId);
        return ev;
      }

      function riskForAction(plan, record) {
        const text = fullSource(record);
        const candidates = plan.riskyActions || [];
        return candidates.find((candidate) =>
          (candidate.terms || []).some((term) => text.includes(normalize(term))) ||
          text.includes(normalize(candidate.family)) ||
          text.includes(normalize(candidate.role))
        ) || null;
      }

      function safePurposeForAction(plan, record) {
        const text = source(record);
        const match = (plan.safeActions || []).find((term) => text.includes(normalize(term)) || text.includes(slug(term)));
        if (match) return `${plan.app}.safe.${slug(match)}`;
        const family = (plan.expectedActionFamilies || []).find((term) => text.includes(normalize(term)) || text.includes(slug(term)));
        if (family) return `${plan.app}.action.${slug(family)}`;
        return `${plan.app}.action.local`;
      }

      function classifyRiskAction(plan, record, blackboard, risky, ruleId) {
        const purpose = risky.family || `${plan.app}.risk.${risky.role || "mutation"}`;
        const ev = addPurpose(record, blackboard, plan, purpose, risky.role || "risky-action", risky.risk === "command-execution" ? "component.console" : (risky.risk === "operational" || risky.risk === "server-control" ? "component.operational-action" : (risky.risk === "destructive" || risky.risk === "process-destructive" ? "component.destructive-action" : "component.remote-mutation-action")), ruleId, {
          kind: "action",
          risk: risky.risk || "remote-mutation",
          proofPolicy: risky.policy || "no-submit"
        });
        blackboard.addRisk(record, risky.risk || "remote-mutation", risky.policy || "no-submit", `${plan.label} ${purpose} is proof-blocked`, ruleId);
        blackboard.addAction(record, {
          role: risky.role || "risky-action",
          label: record.directText || record.text || record.domId || record.sourceSelector,
          risk: risky.risk || "remote-mutation",
          proofPolicy: risky.policy || "no-submit",
          contract: risky.risk === "command-execution" ? "component.console" : (risky.risk === "operational" || risky.risk === "server-control" ? "component.operational-action" : (risky.risk === "destructive" || risky.risk === "process-destructive" ? "component.destructive-action" : "component.remote-mutation-action")),
          purpose,
          evidence: [ev]
        }, ruleId);
        return true;
      }

      function classifySafeAction(plan, record, blackboard, ruleId) {
        const purpose = safePurposeForAction(plan, record);
        const risk = purpose.includes(".ai") || purpose.includes("analysis") ? "analysis" : "safe";
        const ev = addPurpose(record, blackboard, plan, purpose, purpose.split(".").pop() || "safe-action", "component.action", ruleId, {
          kind: "action",
          risk,
          proofPolicy: "inspect-only"
        });
        const action = blackboard.addAction(record, {
          role: purpose.split(".").pop() || "safe-action",
          label: record.directText || record.text || record.domId || record.sourceSelector,
          risk,
          proofPolicy: "inspect-only",
          contract: "component.action",
          purpose,
          evidence: [ev]
        }, ruleId);
        if (action) {
          action.blocked = false;
          action.risk = risk;
          action.proofPolicy = "inspect-only";
        }
        return true;
      }

      function regionPurpose(plan, record) {
        const text = source(record);
        const region = (plan.expectedRegions || []).find((candidate) => text.includes(normalize(candidate)) || text.includes(slug(candidate)));
        if (region) return `${plan.app}.region.${slug(region)}`;
        const hint = (plan.decodeHints || []).find((candidate) => text.includes(normalize(candidate)) || text.includes(slug(candidate)));
        if (hint) return `${plan.app}.surface.${slug(hint)}`;
        return `${plan.app}.surface.${record.domId ? slug(record.domId) : "planned"}`;
      }

      function createPlannerDomainPack(plan) {
        return {
          id: plan.id,
          version: PLANNER_DOMAIN_PACK_VERSION,
          description: `${plan.label} purpose-aware Supercut domain pack`,
          rules: [
            {
              id: `${plan.app}.detect-root`,
              phase: "purpose-inference",
              priority: 100,
              when(record, blackboard) {
                return inPlan(record, blackboard, plan) && isRoot(record, blackboard, plan);
              },
              apply(record, blackboard) {
                addPurpose(record, blackboard, plan, `${plan.app}.root`, `${plan.app}-root`, "component.root", `${plan.app}.detect-root`, {
                  kind: "root",
                  risk: "none",
                  proofPolicy: "inspect-only"
                });
                return true;
              }
            },
            {
              id: `${plan.app}.detect-fields`,
              phase: "purpose-inference",
              priority: 90,
              when(record, blackboard) {
                return inPlan(record, blackboard, plan) && !isRoot(record, blackboard, plan) && fieldLike(record);
              },
              apply(record, blackboard) {
                addPurpose(record, blackboard, plan, `${plan.app}.field.${record.domId ? slug(record.domId) : "input"}`, "field", "component.field", `${plan.app}.detect-fields`, {
                  kind: "field",
                  risk: "safe",
                  proofPolicy: "inspect-only"
                });
                return true;
              }
            },
            {
              id: `${plan.app}.detect-feeds`,
              phase: "purpose-inference",
              priority: 88,
              when(record, blackboard) {
                return inPlan(record, blackboard, plan) && !isRoot(record, blackboard, plan) && !actionLike(record) && feedLike(record, plan);
              },
              apply(record, blackboard) {
                addPurpose(record, blackboard, plan, `${plan.app}.status-feed`, "status-feed", "component.status-feed", `${plan.app}.detect-feeds`, {
                  kind: "status-feed",
                  risk: "none",
                  proofPolicy: "inspect-only"
                });
                return true;
              }
            },
            {
              id: `${plan.app}.detect-risk-actions`,
              phase: "risk-classification",
              priority: 120,
              when(record, blackboard) {
                return inPlan(record, blackboard, plan) && actionLike(record) && Boolean(riskForAction(plan, record));
              },
              apply(record, blackboard) {
                return classifyRiskAction(plan, record, blackboard, riskForAction(plan, record), `${plan.app}.detect-risk-actions`);
              }
            },
            {
              id: `${plan.app}.detect-safe-actions`,
              phase: "risk-classification",
              priority: 110,
              when(record, blackboard) {
                return inPlan(record, blackboard, plan) && actionLike(record) && !riskForAction(plan, record);
              },
              apply(record, blackboard) {
                return classifySafeAction(plan, record, blackboard, `${plan.app}.detect-safe-actions`);
              }
            },
            {
              id: `${plan.app}.detect-regions`,
              phase: "contract-assignment",
              priority: 75,
              when(record, blackboard) {
                return inPlan(record, blackboard, plan) && !isRoot(record, blackboard, plan) && !record.contract && containerLike(record);
              },
              apply(record, blackboard) {
                const purpose = regionPurpose(plan, record);
                const workflow = /(workflow|queue|terminal|console|composer|editor|builder|worker|wallet|publish|runtime|pipeline|session)/.test(source(record));
                addPurpose(record, blackboard, plan, purpose, purpose.split(".").slice(-1)[0], workflow ? "component.workflow" : "component.panel", `${plan.app}.detect-regions`, {
                  kind: workflow ? "workflow" : "panel",
                  risk: workflow && plan.riskLevel === "high" ? "analysis" : "none",
                  proofPolicy: "inspect-only"
                });
                return true;
              }
            },
            {
              id: `${plan.app}.audit-policy`,
              phase: "audit",
              priority: 60,
              when(record, blackboard) {
                return inPlan(record, blackboard, plan) && isRoot(record, blackboard, plan);
              },
              apply(record, blackboard) {
                if (plan.riskLevel === "low") {
                  blackboard.actions
                    .filter((action) => String(action.purpose || "").startsWith(`${plan.app}.`))
                    .forEach((action) => {
                      action.blocked = false;
                      action.risk = action.risk === "analysis" ? "analysis" : "safe";
                      action.proofPolicy = "inspect-only";
                    });
                }
                (plan.riskyActions || []).forEach((risk) => {
                  const found = blackboard.actions.some((action) => action.purpose === risk.family);
                  if (!found && plan.riskLevel === "high") {
                    blackboard.explain(record, `${plan.label} has planned risk family ${risk.family}; no matching executable surface was observed in this mount.`, [risk.terms?.join(", ") || risk.family], `${plan.app}.audit-policy`);
                  }
                });
                return true;
              }
            }
          ]
        };
      }

      const plannerDomainPacks = PLANNER_DOMAIN_PLANS.map(createPlannerDomainPack);

      global.McelSupercutPacksPlannerDomains = {
        PLANNER_DOMAIN_PACK_VERSION,
        PLANNER_DOMAIN_PLANS,
        plannerDomainPacks,
        packFor(app) {
          return plannerDomainPacks.find((pack) => pack.id === `${app}-domain`) || null;
        }
      };

      global.McelSupercutRegistry?.registerPacks?.(plannerDomainPacks);
    })(window);
