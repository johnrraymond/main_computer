    (function (global) {
      "use strict";

      const CORE_PACK_VERSION = "0.2.0";

      function source(record) {
        return [
          record?.tag || "",
          record?.domId || "",
          record?.classes?.join?.(" ") || "",
          record?.role || "",
          record?.componentId || "",
          record?.text || "",
          record?.signature || ""
        ].join(" ").toLowerCase();
      }

      function identitySource(record) {
        return [
          record?.tag || "",
          record?.domId || "",
          record?.classes?.join?.(" ") || "",
          record?.role || "",
          record?.componentId || ""
        ].join(" ").toLowerCase();
      }

      function localSource(record) {
        return [
          record?.tag || "",
          record?.domId || "",
          record?.classes?.join?.(" ") || "",
          record?.role || "",
          record?.componentId || "",
          record?.directText || ""
        ].join(" ").toLowerCase();
      }

      function isRoot(record, blackboard) {
        return record?.element === blackboard?.rootNode || record?.index === 0;
      }

      function isAction(record) {
        return ["button", "summary"].includes(record?.tag) || record?.role === "button" || record?.tag === "a";
      }

      function isField(record) {
        return ["input", "select", "textarea", "label"].includes(record?.tag);
      }

      function isStatusFeed(record) {
        const local = localSource(record);
        return ["pre", "output", "code"].includes(record?.tag) || /(status|output|log|activity|report|feed|result|summary)/.test(local);
      }

      function isConsole(record) {
        const local = localSource(record);
        const tag = record?.tag || "";
        const consoleIdentity = /(manual command|manual console|command console|run command|shell command|terminal)/.test(local);
        const commandField = ["input", "textarea"].includes(tag) && /(command|shell|terminal)/.test(local);
        const commandSnippet = ["pre", "code"].includes(tag) && /(manual command|shell command|run command|\$\s|git\s+(push|remote|mirror|fetch|status|log))/.test(record?.directText || "");
        return Boolean(consoleIdentity || commandField || commandSnippet);
      }

      function isToolbar(record) {
        return /(toolbar|actions|button-row|controls|control-row|primary actions)/.test(localSource(record)) && Number(record?.controlCount || 0) >= 2;
      }

      function isWorkflow(record) {
        const local = localSource(record);
        return record?.tag === "details" || /(workflow|wizard|accordion|step|publish|mirror|remote|gitea|git-tools\.start|git-server)/.test(local);
      }

      function isPanel(record) {
        const tag = record?.tag || "";
        if (["header", "footer", "nav", "ul", "ol", "li", "summary", "label", "button", "a", "input", "select", "textarea", "pre", "code", "output"].includes(tag)) {
          return false;
        }
        const identity = identitySource(record);
        const explicitPanelSignal = /(card|panel|pane|shell|widget|form|workflow|group|configuration|setup)/.test(identity);
        const boundedSemanticContainer = ["article", "form", "details"].includes(tag) ||
          (tag === "section" && (record.depth <= 5 || explicitPanelSignal)) ||
          (tag === "div" && explicitPanelSignal);
        const controlDenseContainer = Number(record?.controlCount || 0) >= 3 && Number(record?.childElementCount || 0) >= 2 && record.depth <= 7;
        return Boolean(boundedSemanticContainer || controlDenseContainer);
      }

      function isRegion(record, blackboard) {
        if (isRoot(record, blackboard)) return false;
        const tag = record?.tag || "";
        if (["main", "nav", "aside"].includes(tag)) return record.depth <= 3;
        if (["header", "footer"].includes(tag)) return record.depth <= 2;
        const role = record?.role || "";
        if (["main", "navigation", "banner", "contentinfo", "complementary", "region"].includes(role)) return record.depth <= 4;
        const identity = identitySource(record);
        const isMajorSection = tag === "section" && record.depth <= 3 && /(hero|workspace|workflow|intake|overview|operations|layout|region)/.test(identity);
        return Boolean(isMajorSection);
      }

      function fallbackPurpose(record, prefix = "legacy-html") {
        if (record.componentId) return `component.${global.McelSupercutBlackboard?.slugify?.(record.componentId) || record.componentId}`;
        if (record.domId) return `${prefix}.${global.McelSupercutBlackboard?.slugify?.(record.domId) || record.domId}`;
        return `${prefix}.unknown-purpose`;
      }

      function evidence(record, blackboard, type, value, ruleId) {
        return blackboard.addEvidence(record, type, value || record.signature || record.selector, ruleId).id;
      }

      const coreHtmlPack = {
        id: "core-html",
        version: CORE_PACK_VERSION,
        description: "Generic web developer semantic intake rules for MCEL Supercut v0.2",
        rules: [
          {
            id: "core.intake.root",
            phase: "intake",
            priority: 100,
            when: isRoot,
            apply(record, blackboard) {
              const ev = evidence(record, blackboard, "root-node", record.sourceSelector, "core.intake.root");
              blackboard.ensureComponent(record, {
                kind: "root",
                role: "app-root",
                purpose: record.domId ? `app.${record.domId}` : `${blackboard.specimenId}.root`,
                contract: "component.root",
                risk: "none",
                proofPolicy: "inspect-only",
                evidence: [ev]
              }, "core.intake.root");
              blackboard.explain(record, "Root node anchors the semantic rewrite preview.", [record.sourceSelector], "core.intake.root");
              return true;
            }
          },
          {
            id: "core.detect.regions-disciplined",
            phase: "structure-detection",
            priority: 90,
            when(record, blackboard) {
              return isRegion(record, blackboard);
            },
            apply(record, blackboard) {
              const ev = evidence(record, blackboard, "landmark-or-region-signal", record.signature, "core.detect.regions-disciplined");
              blackboard.addRegion(record, {
                role: record.role || "semantic-region",
                purpose: fallbackPurpose(record),
                evidence: [ev]
              }, "core.detect.regions-disciplined");
              blackboard.explain(record, "Promoted as a coarse region, not a generic wrapper.", [record.tag, record.sourceSelector], "core.detect.regions-disciplined");
              return true;
            }
          },
          {
            id: "core.detect.fields",
            phase: "structure-detection",
            priority: 88,
            when: isField,
            apply(record, blackboard) {
              const ev = evidence(record, blackboard, "field-control", record.signature, "core.detect.fields");
              blackboard.ensureComponent(record, {
                kind: "field",
                role: "field-control",
                purpose: fallbackPurpose(record),
                contract: "component.field",
                risk: "safe",
                proofPolicy: "inspect-only",
                evidence: [ev]
              }, "core.detect.fields");
              return true;
            }
          },
          {
            id: "core.detect.actions",
            phase: "structure-detection",
            priority: 86,
            when: isAction,
            apply(record, blackboard) {
              const ev = evidence(record, blackboard, "action-text", record.text || record.domId || record.sourceSelector, "core.detect.actions");
              blackboard.addAction(record, {
                role: "action-component",
                label: record.text || record.domId || record.sourceSelector,
                risk: "safe",
                proofPolicy: "inspect-only",
                contract: "component.action",
                purpose: fallbackPurpose(record),
                evidence: [ev]
              }, "core.detect.actions");
              return true;
            }
          },
          {
            id: "core.detect.console",
            phase: "structure-detection",
            priority: 84,
            when: isConsole,
            apply(record, blackboard) {
              const ev = evidence(record, blackboard, "console-signal", record.signature, "core.detect.console");
              blackboard.ensureComponent(record, {
                kind: "console",
                role: "command-console",
                purpose: fallbackPurpose(record),
                contract: "component.console",
                risk: "command-execution",
                proofPolicy: "no-command-execution",
                evidence: [ev]
              }, "core.detect.console");
              blackboard.addRisk(record, "command-execution", "no-command-execution", "console-like surface is proof inert", "core.detect.console");
              blackboard.explain(record, "Console-like surface is modeled but command execution is blocked.", [record.sourceSelector], "core.detect.console");
              return true;
            }
          },
          {
            id: "core.detect.status-feeds",
            phase: "structure-detection",
            priority: 80,
            when(record) {
              return !isConsole(record) && isStatusFeed(record);
            },
            apply(record, blackboard) {
              const ev = evidence(record, blackboard, "status-or-output-signal", record.signature, "core.detect.status-feeds");
              blackboard.ensureComponent(record, {
                kind: "status-feed",
                role: "status-feed",
                purpose: fallbackPurpose(record),
                contract: "component.status-feed",
                risk: "none",
                proofPolicy: "inspect-only",
                evidence: [ev]
              }, "core.detect.status-feeds");
              return true;
            }
          },
          {
            id: "core.detect.toolbars",
            phase: "structure-detection",
            priority: 78,
            when: isToolbar,
            apply(record, blackboard) {
              const ev = evidence(record, blackboard, "multiple-controls", `${record.controlCount} controls`, "core.detect.toolbars");
              blackboard.ensureComponent(record, {
                kind: "toolbar",
                role: "toolbar",
                purpose: fallbackPurpose(record),
                contract: "component.toolbar",
                risk: "safe",
                proofPolicy: "inspect-only",
                evidence: [ev]
              }, "core.detect.toolbars");
              return true;
            }
          },
          {
            id: "core.detect.workflows",
            phase: "structure-detection",
            priority: 72,
            when(record) {
              return !isAction(record) && !isField(record) && isWorkflow(record);
            },
            apply(record, blackboard) {
              const ev = evidence(record, blackboard, "workflow-signal", record.signature, "core.detect.workflows");
              blackboard.ensureComponent(record, {
                kind: "workflow",
                role: "workflow",
                purpose: fallbackPurpose(record),
                contract: "component.workflow",
                risk: "operational",
                proofPolicy: "inspect-only",
                evidence: [ev]
              }, "core.detect.workflows");
              return true;
            }
          },
          {
            id: "core.detect.panels",
            phase: "structure-detection",
            priority: 68,
            when(record, blackboard) {
              return !isRoot(record, blackboard) && !record.contract && !isAction(record) && !isField(record) && isPanel(record);
            },
            apply(record, blackboard) {
              const ev = evidence(record, blackboard, "panel-signal", record.signature, "core.detect.panels");
              blackboard.ensureComponent(record, {
                kind: "panel",
                role: "panel",
                purpose: fallbackPurpose(record),
                contract: "component.panel",
                risk: "none",
                proofPolicy: "inspect-only",
                evidence: [ev]
              }, "core.detect.panels");
              return true;
            }
          },
          {
            id: "core.contract.fallback-unknowns",
            phase: "contract-assignment",
            priority: 1,
            when(record) {
              return !record.contract;
            },
            apply(record, blackboard) {
              const ev = evidence(record, blackboard, "fallback-record", record.signature || record.sourceSelector, "core.contract.fallback-unknowns");
              blackboard.ensureComponent(record, {
                kind: "unknown",
                role: "unknown-component",
                purpose: fallbackPurpose(record),
                contract: "component.unknown",
                risk: "unknown",
                proofPolicy: "inspect-only",
                evidence: [ev]
              }, "core.contract.fallback-unknowns");
              return true;
            }
          },
          {
            id: "core.audit.region-discipline",
            phase: "audit",
            priority: 40,
            when(record, blackboard) {
              return record.index === 0 && blackboard.components.length > 0;
            },
            apply(record, blackboard) {
              const ratio = blackboard.regions.length / Math.max(1, blackboard.components.length);
              if (ratio > 0.55) {
                blackboard.addViolation("region-discipline", record, "Too many components were promoted to regions; tighten region rules.", "warning");
              }
              return true;
            }
          }
        ]
      };

      const coreActionRiskPack = {
        id: "core-action-risk",
        version: CORE_PACK_VERSION,
        description: "Generic proof policy and unsafe action blocking rules",
        rules: [
          {
            id: "core.risk.generic-destructive-actions",
            phase: "risk-classification",
            priority: 82,
            when(record) {
              return isAction(record) && /(delete|remove|destroy|terminate|kill|shutdown|stop|reset|drop|wipe)/.test(source(record));
            },
            apply(record, blackboard) {
              blackboard.addRisk(record, "destructive", "no-click", "generic destructive action text", "core.risk.generic-destructive-actions");
              blackboard.addAction(record, {
                role: "destructive-action",
                label: record.text || record.domId || record.sourceSelector,
                risk: "destructive",
                proofPolicy: "no-click",
                contract: "component.destructive-action"
              }, "core.risk.generic-destructive-actions");
              blackboard.explain(record, "Destructive action is visible but proof-blocked.", [record.text || record.sourceSelector], "core.risk.generic-destructive-actions");
              return true;
            }
          },
          {
            id: "core.risk.generic-submit-actions",
            phase: "risk-classification",
            priority: 70,
            when(record) {
              return isAction(record) && /(submit|publish|push|deploy|send|upload|sync|mirror)/.test(source(record));
            },
            apply(record, blackboard) {
              blackboard.addRisk(record, "remote-mutation", "no-submit", "generic submit or remote mutation text", "core.risk.generic-submit-actions");
              blackboard.addAction(record, {
                role: "remote-mutation-action",
                label: record.text || record.domId || record.sourceSelector,
                risk: "remote-mutation",
                proofPolicy: "no-submit",
                contract: "component.remote-mutation-action"
              }, "core.risk.generic-submit-actions");
              return true;
            }
          },
          {
            id: "core.audit.blocked-actions-have-explanations",
            phase: "audit",
            priority: 35,
            when(record, blackboard) {
              return record.index === 0 && blackboard.actions.some((action) => action.blocked);
            },
            apply(record, blackboard) {
              blackboard.actions.filter((action) => action.blocked).forEach((action) => {
                const actionRecord = blackboard.recordById(action.recordId);
                if (!blackboard.explanations.some((item) => item.recordId === action.recordId)) {
                  blackboard.explain(actionRecord, "Unsafe action blocked by Supercut proof policy.", [`${action.risk} → ${action.proofPolicy}`], "core.audit.blocked-actions-have-explanations");
                }
              });
              return true;
            }
          }
        ]
      };

      global.McelSupercutPacksCore = {
        CORE_PACK_VERSION,
        coreHtmlPack,
        coreActionRiskPack
      };

      global.McelSupercutRegistry?.registerPack?.(coreHtmlPack);
      global.McelSupercutRegistry?.registerPack?.(coreActionRiskPack);
    })(window);
