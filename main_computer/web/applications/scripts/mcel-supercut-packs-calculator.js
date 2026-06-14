    (function (global) {
      "use strict";

      const CALCULATOR_PACK_VERSION = "0.2.0";

      function source(record) {
        return [
          record?.tag || "",
          record?.domId || "",
          record?.classes?.join?.(" ") || "",
          record?.role || "",
          record?.componentId || "",
          record?.directText || "",
          record?.signature || "",
          record?.element?.getAttribute?.("data-calc-action") || "",
          record?.element?.getAttribute?.("data-calc-key") || "",
          record?.element?.getAttribute?.("data-calc-graph-token") || "",
          record?.element?.getAttribute?.("data-calc-graph-template") || "",
          record?.element?.getAttribute?.("data-mathics-example") || ""
        ].join(" ").toLowerCase();
      }

      function isCalculatorRoot(record, blackboard) {
        return record?.element === blackboard?.rootNode || record?.index === 0 || record?.domId === "calculator-app";
      }

      function inCalculator(record, blackboard) {
        return Boolean(
          blackboard?.specimenId === "calculator" ||
          blackboard?.rootNode?.id === "calculator-app" ||
          record?.element?.closest?.("#calculator-app")
        );
      }

      function nonRootCalculatorSurface(record, blackboard) {
        return inCalculator(record, blackboard) && !isCalculatorRoot(record, blackboard);
      }

      function executableControl(record) {
        const tag = record?.tag || "";
        const role = record?.role || "";
        if (tag === "summary") return false;
        return tag === "button" || tag === "a" || role === "button";
      }

      function addPurpose(record, blackboard, purpose, role, contract, ruleId, extra = {}) {
        const ev = blackboard.addEvidence(record, "calculator-domain", purpose, ruleId).id;
        blackboard.ensureComponent(record, {
          kind: extra.kind || (contract === "component.action" ? "action" : "panel"),
          role,
          purpose,
          contract,
          risk: extra.risk || record.risk || "none",
          proofPolicy: extra.proofPolicy || record.proofPolicy || "inspect-only",
          evidence: [ev]
        }, ruleId);
        blackboard.explain(record, `Calculator domain pack recognized ${purpose}.`, [record.sourceSelector, record.directText || record.signature], ruleId);
        return ev;
      }

      function safeActionConfig(record) {
        const text = source(record);
        if (!executableControl(record)) return null;
        if (/calculator\.mode-|calculator\.mode\./.test(text)) {
          return {purpose: "calculator.mode-switch", role: "mode-switch"};
        }
        if (/calculator\.key\.digit|data-calc-key="[0-9]"|\bdigit\b/.test(text)) {
          return {purpose: "calculator.keypad.digit-entry", role: "digit-entry"};
        }
        if (/calculator\.key\.(add|subtract|multiply|divide|percent|decimal|open-paren|close-paren)|data-calc-key|operator/.test(text)) {
          return {purpose: "calculator.keypad.operator-entry", role: "operator-entry"};
        }
        if (/calculator\.key\.(clear|backspace)|data-calc-action|clear|back/.test(text)) {
          return {purpose: "calculator.keypad.edit", role: /clear/.test(text) ? "clear" : "backspace"};
        }
        if (/calculator\.key\.equals|equals|evaluate expression/.test(text)) {
          return {purpose: "calculator.keypad.evaluate", role: "evaluate"};
        }
        if (/calculator\.graphing\.(draw|reset|function|token)|data-calc-graph/.test(text)) {
          return {purpose: /reset/.test(text) ? "calculator.graphing.reset-view" : "calculator.graphing.local-graph-action", role: /reset/.test(text) ? "reset-view" : "graphing-action"};
        }
        if (/calculator\.(prompt|qa|mathics).*(ask|model)|ask model|ask about results/.test(text)) {
          return {purpose: "calculator.analysis.ask-model", role: "analysis-action", risk: "analysis"};
        }
        if (/calculator\.mathics\.(evaluate|clear|example)|data-mathics-example|mathics/.test(text)) {
          return {purpose: /clear/.test(text) ? "calculator.mathics.clear" : "calculator.mathics.evaluate", role: /clear/.test(text) ? "clear" : "evaluate"};
        }
        if (/calculator/.test(text)) {
          return {purpose: "calculator.local-action", role: "calculator-action"};
        }
        return null;
      }

      function localCalculatorActionConfig(record) {
        if (!executableControl(record)) return null;
        return safeActionConfig(record) || {
          purpose: "calculator.local-action",
          role: "calculator-action",
          risk: "safe"
        };
      }

      function classifySafeAction(record, blackboard, config, ruleId) {
        const risk = config.risk || "safe";
        const ev = addPurpose(record, blackboard, config.purpose, config.role, "component.action", ruleId, {
          kind: "action",
          risk,
          proofPolicy: "inspect-only"
        });
        const action = blackboard.addAction(record, {
          role: config.role,
          label: record.directText || record.text || record.domId || record.sourceSelector,
          risk,
          proofPolicy: "inspect-only",
          contract: "component.action",
          purpose: config.purpose,
          evidence: [ev]
        }, ruleId);
        return action;
      }

      function neutralizeCalculatorAction(record, blackboard, config, ruleId) {
        const action = classifySafeAction(record, blackboard, config, ruleId);
        const component = blackboard.components.find((item) => item.recordId === record.id);
        const risk = config.risk || "safe";
        if (component) {
          component.kind = "action";
          component.role = config.role;
          component.purpose = config.purpose;
          component.originalPurpose = config.purpose;
          component.contract = "component.action";
          component.risk = risk;
          component.proofPolicy = "inspect-only";
          component.rewriteTag = "mcel-action";
        }
        if (action) {
          action.risk = risk;
          action.proofPolicy = "inspect-only";
          action.contract = "component.action";
          action.blocked = false;
          action.purpose = config.purpose;
        }
        record.kind = "action";
        record.purpose = config.purpose;
        record.contract = "component.action";
        record.risk = risk;
        record.proofPolicy = "inspect-only";
        record.rewriteTag = "mcel-action";
        blackboard.risks
          .filter((riskItem) => riskItem.recordId === record.id)
          .forEach((riskItem) => {
            riskItem.risk = risk;
            riskItem.proofPolicy = "inspect-only";
            riskItem.blocked = false;
            riskItem.reason = "calculator-domain resolved this as a local non-destructive calculator control";
            riskItem.ruleId = ruleId;
          });
        return true;
      }

      const calculatorDomainPack = {
        id: "calculator-domain",
        version: CALCULATOR_PACK_VERSION,
        description: "Calculator-specific semantic and safe-local-action knowledge pack",
        rules: [
          {
            id: "calculator.detect-root",
            phase: "purpose-inference",
            priority: 100,
            when(record, blackboard) {
              return isCalculatorRoot(record, blackboard);
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "calculator.root", "calculator-root", "component.root", "calculator.detect-root", {
                kind: "root",
                risk: "none",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "calculator.detect-shell",
            phase: "purpose-inference",
            priority: 92,
            when(record, blackboard) {
              return nonRootCalculatorSurface(record, blackboard) && !executableControl(record) && /(calculator-shell|calculator-app-shell|calculator-workspace|calculator-pane)/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "calculator.shell", "calculator-shell", "component.panel", "calculator.detect-shell", {
                kind: "panel",
                risk: "none",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "calculator.detect-mode-toolbar",
            phase: "purpose-inference",
            priority: 90,
            when(record, blackboard) {
              return nonRootCalculatorSurface(record, blackboard) && !executableControl(record) && /(mode|toolbar|calculator-mode)/.test(source(record)) && Number(record.controlCount || 0) >= 1;
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "calculator.mode-toolbar", "mode-toolbar", "component.toolbar", "calculator.detect-mode-toolbar", {
                kind: "toolbar",
                risk: "safe",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "calculator.detect-display",
            phase: "purpose-inference",
            priority: 88,
            when(record, blackboard) {
              return nonRootCalculatorSurface(record, blackboard) && !executableControl(record) && /(display|history|result|output|status)/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "calculator.display", "display", "component.status-feed", "calculator.detect-display", {
                kind: "status-feed",
                risk: "none",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "calculator.detect-keypad",
            phase: "purpose-inference",
            priority: 86,
            when(record, blackboard) {
              return nonRootCalculatorSurface(record, blackboard) && !executableControl(record) && /(keypad|calculator-key|operator|equals)/.test(source(record)) && Number(record.controlCount || 0) >= 4;
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "calculator.keypad", "keypad", "component.toolbar", "calculator.detect-keypad", {
                kind: "toolbar",
                risk: "safe",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "calculator.detect-graphing-panel",
            phase: "purpose-inference",
            priority: 84,
            when(record, blackboard) {
              return nonRootCalculatorSurface(record, blackboard) && !executableControl(record) && /(graph|function|plot|canvas)/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "calculator.graphing-panel", "graphing-panel", "component.panel", "calculator.detect-graphing-panel", {
                kind: "panel",
                risk: "none",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "calculator.detect-mathics-panel",
            phase: "purpose-inference",
            priority: 82,
            when(record, blackboard) {
              return nonRootCalculatorSurface(record, blackboard) && !executableControl(record) && /(mathics|symbolic|expression|examples)/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "calculator.mathics-panel", "mathics-panel", "component.panel", "calculator.detect-mathics-panel", {
                kind: "panel",
                risk: "analysis",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "calculator.detect-local-actions",
            phase: "risk-classification",
            priority: 110,
            when(record, blackboard) {
              return inCalculator(record, blackboard) && Boolean(safeActionConfig(record));
            },
            apply(record, blackboard) {
              return Boolean(classifySafeAction(record, blackboard, safeActionConfig(record), "calculator.detect-local-actions"));
            }
          },
          {
            id: "calculator.rectify-local-actions",
            phase: "audit",
            priority: 80,
            when(record, blackboard) {
              return inCalculator(record, blackboard) && Boolean(safeActionConfig(record));
            },
            apply(record, blackboard) {
              return neutralizeCalculatorAction(record, blackboard, safeActionConfig(record), "calculator.rectify-local-actions");
            }
          },
          {
            id: "calculator.rectify-all-local-controls",
            phase: "audit",
            priority: 90,
            when(record, blackboard) {
              return inCalculator(record, blackboard) && Boolean(localCalculatorActionConfig(record));
            },
            apply(record, blackboard) {
              return neutralizeCalculatorAction(record, blackboard, localCalculatorActionConfig(record), "calculator.rectify-all-local-controls");
            }
          },
          {
            id: "calculator.audit-no-blocked-local-actions",
            phase: "audit",
            priority: 50,
            when(record, blackboard) {
              return isCalculatorRoot(record, blackboard) && inCalculator(record, blackboard);
            },
            apply(record, blackboard) {
              blackboard.actions
                .filter((action) => String(action.purpose || "").startsWith("calculator."))
                .forEach((action) => {
                  if (action.blocked || ["no-click", "no-submit", "no-command-execution"].includes(action.proofPolicy)) {
                    blackboard.addViolation("calculator-local-action-policy", blackboard.recordById(action.recordId), `${action.role} should remain inspect-only local calculator action`, "warning");
                  }
                });
              return true;
            }
          }
        ]
      };

      global.McelSupercutPacksCalculator = {
        CALCULATOR_PACK_VERSION,
        calculatorDomainPack
      };

      global.McelSupercutRegistry?.registerPack?.(calculatorDomainPack);
    })(window);
