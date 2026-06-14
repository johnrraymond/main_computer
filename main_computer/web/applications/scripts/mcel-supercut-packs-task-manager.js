    (function (global) {
      "use strict";

      const TASK_MANAGER_PACK_VERSION = "0.2.0";

      const KNOWN_RISKY_CONTROLS = Object.freeze({
        "task-server-shutdown": {
          purpose: "task-manager.server-control.shutdown",
          role: "server-shutdown",
          risk: "destructive",
          proofPolicy: "no-click",
          contract: "component.destructive-action",
          reason: "server shutdown is destructive to the running local service"
        },
        "task-server-start": {
          purpose: "task-manager.server-control.start",
          role: "server-start",
          risk: "operational",
          proofPolicy: "no-click",
          contract: "component.operational-action",
          reason: "server start is operational and proof-blocked"
        },
        "task-server-restart": {
          purpose: "task-manager.server-control.restart",
          role: "server-restart",
          risk: "operational",
          proofPolicy: "no-click",
          contract: "component.operational-action",
          reason: "server restart is disruptive and proof-blocked"
        },
        "task-schedule-create": {
          purpose: "task-manager.schedule.create",
          role: "schedule-create",
          risk: "operational",
          proofPolicy: "no-click",
          contract: "component.operational-action",
          reason: "schedule creation changes future task execution state"
        }
      });

      function source(record) {
        return [
          record?.tag || "",
          record?.domId || "",
          record?.classes?.join?.(" ") || "",
          record?.role || "",
          record?.componentId || "",
          record?.directText || "",
          record?.signature || ""
        ].join(" ").toLowerCase();
      }

      function fullSource(record) {
        return [
          source(record),
          record?.text || "",
          record?.element?.getAttribute?.("data-mcel-action-role") || "",
          record?.element?.getAttribute?.("data-mcel-action-risk") || "",
          record?.element?.getAttribute?.("data-task-action") || "",
          record?.element?.getAttribute?.("data-task-delete") || "",
          record?.element?.getAttribute?.("data-task-run") || "",
          record?.element?.getAttribute?.("data-task-action-name") || ""
        ].join(" ").toLowerCase();
      }

      function isTaskManagerRoot(record, blackboard) {
        return record?.element === blackboard?.rootNode || record?.index === 0 || record?.domId === "task-manager-app";
      }

      function inTaskManager(record, blackboard) {
        return Boolean(
          blackboard?.specimenId === "task-manager" ||
          blackboard?.rootNode?.id === "task-manager-app" ||
          record?.element?.closest?.("#task-manager-app")
        );
      }

      function nonRootTaskSurface(record, blackboard) {
        return inTaskManager(record, blackboard) && !isTaskManagerRoot(record, blackboard);
      }

      function executableControl(record) {
        const tag = record?.tag || "";
        const role = record?.role || "";
        if (tag === "summary") return false;
        return tag === "button" || tag === "a" || role === "button";
      }

      function configForRecord(record) {
        if (!record) return null;
        if (record.domId && KNOWN_RISKY_CONTROLS[record.domId]) return KNOWN_RISKY_CONTROLS[record.domId];
        const action = record.element?.getAttribute?.("data-task-action") || "";
        if (action === "kill") {
          return {
            purpose: "task-manager.process-control.kill-pid",
            role: "kill-pid",
            risk: "process-destructive",
            proofPolicy: "no-click",
            contract: "component.destructive-action",
            reason: "PID kill action is destructive and proof-blocked"
          };
        }
        if (action === "terminate" || action === "terminate-pid") {
          return {
            purpose: "task-manager.process-control.terminate-pid",
            role: "terminate-pid",
            risk: "process-destructive",
            proofPolicy: "no-click",
            contract: "component.destructive-action",
            reason: "PID termination is destructive and proof-blocked"
          };
        }
        if (record.element?.hasAttribute?.("data-task-delete")) {
          return {
            purpose: "task-manager.schedule.delete",
            role: "schedule-delete",
            risk: "destructive",
            proofPolicy: "no-click",
            contract: "component.destructive-action",
            reason: "schedule deletion mutates deferred task state"
          };
        }
        if (record.element?.hasAttribute?.("data-task-run")) {
          return {
            purpose: "task-manager.schedule.run-now",
            role: "schedule-run-now",
            risk: "operational",
            proofPolicy: "no-click",
            contract: "component.operational-action",
            reason: "running a scheduled task is operational and proof-blocked"
          };
        }
        return null;
      }

      function addPurpose(record, blackboard, purpose, role, contract, ruleId, extra = {}) {
        const ev = blackboard.addEvidence(record, "task-manager-domain", purpose, ruleId).id;
        blackboard.ensureComponent(record, {
          kind: extra.kind || (contract === "component.action" ? "action" : "panel"),
          role,
          purpose,
          contract,
          risk: extra.risk || record.risk || "none",
          proofPolicy: extra.proofPolicy || record.proofPolicy || "inspect-only",
          evidence: [ev]
        }, ruleId);
        blackboard.explain(record, `Task Manager domain pack recognized ${purpose}.`, [record.sourceSelector, record.directText || record.signature], ruleId);
        return ev;
      }

      function classifyAction(record, blackboard, config, ruleId) {
        const ev = addPurpose(record, blackboard, config.purpose, config.role, config.contract, ruleId, {
          kind: "action",
          risk: config.risk,
          proofPolicy: config.proofPolicy
        });
        blackboard.addRisk(record, config.risk, config.proofPolicy, config.reason, ruleId);
        blackboard.addAction(record, {
          role: config.role,
          label: record.directText || record.text || record.domId || record.sourceSelector,
          risk: config.risk,
          proofPolicy: config.proofPolicy,
          contract: config.contract,
          purpose: config.purpose,
          evidence: [ev]
        }, ruleId);
        return true;
      }

      const taskManagerDomainPack = {
        id: "task-manager-domain",
        version: TASK_MANAGER_PACK_VERSION,
        description: "Task Manager-specific semantic and proof-policy knowledge pack",
        rules: [
          {
            id: "task-manager.detect-root",
            phase: "purpose-inference",
            priority: 100,
            when(record, blackboard) {
              return isTaskManagerRoot(record, blackboard);
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "task-manager.root", "task-manager-root", "component.root", "task-manager.detect-root", {
                kind: "root",
                risk: "none",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "task-manager.detect-server-control",
            phase: "purpose-inference",
            priority: 92,
            when(record, blackboard) {
              return nonRootTaskSurface(record, blackboard) && !executableControl(record) && /(task-controls-card|server|shutdown|restart|start)/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "task-manager.server-control", "server-control", "component.workflow", "task-manager.detect-server-control", {
                kind: "workflow",
                risk: "operational",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "task-manager.detect-process-feed",
            phase: "purpose-inference",
            priority: 90,
            when(record, blackboard) {
              return nonRootTaskSurface(record, blackboard) && !executableControl(record) && /(process|pid|task-panel-processes|task-panel-all-processes|task-process-table|task-all-process-table)/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "task-manager.process-feed", "process-feed", "component.status-feed", "task-manager.detect-process-feed", {
                kind: "status-feed",
                risk: "none",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "task-manager.detect-connection-feed",
            phase: "purpose-inference",
            priority: 88,
            when(record, blackboard) {
              return nonRootTaskSurface(record, blackboard) && !executableControl(record) && /(connection|port|task-panel-connections|task-connection-table)/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "task-manager.connection-feed", "connection-feed", "component.status-feed", "task-manager.detect-connection-feed", {
                kind: "status-feed",
                risk: "none",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "task-manager.detect-schedule-workflow",
            phase: "purpose-inference",
            priority: 86,
            when(record, blackboard) {
              return nonRootTaskSurface(record, blackboard) && !executableControl(record) && /(schedule|deferred|task-schedule)/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "task-manager.schedule-workflow", "schedule-workflow", "component.workflow", "task-manager.detect-schedule-workflow", {
                kind: "workflow",
                risk: "operational",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "task-manager.detect-ai-analysis",
            phase: "purpose-inference",
            priority: 82,
            when(record, blackboard) {
              return nonRootTaskSurface(record, blackboard) && !executableControl(record) && /(ai|analysis|brief|prompt)/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "task-manager.ai-analysis", "ai-analysis", "component.panel", "task-manager.detect-ai-analysis", {
                kind: "panel",
                risk: "analysis",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "task-manager.risk.known-controls",
            phase: "risk-classification",
            priority: 105,
            when(record, blackboard) {
              return inTaskManager(record, blackboard) && executableControl(record) && Boolean(configForRecord(record));
            },
            apply(record, blackboard) {
              return classifyAction(record, blackboard, configForRecord(record), "task-manager.risk.known-controls");
            }
          },
          {
            id: "task-manager.risk.server-lifecycle",
            phase: "risk-classification",
            priority: 96,
            when(record, blackboard) {
              return inTaskManager(record, blackboard) && executableControl(record) && /(shutdown|restart|start).*(server)|(server).*(shutdown|restart|start)/.test(fullSource(record));
            },
            apply(record, blackboard) {
              const text = fullSource(record);
              const config = /shutdown|stop/.test(text)
                ? {
                  purpose: "task-manager.server-control.shutdown",
                  role: "server-shutdown",
                  risk: "destructive",
                  proofPolicy: "no-click",
                  contract: "component.destructive-action",
                  reason: "server shutdown is proof-blocked"
                }
                : {
                  purpose: /restart/.test(text) ? "task-manager.server-control.restart" : "task-manager.server-control.start",
                  role: /restart/.test(text) ? "server-restart" : "server-start",
                  risk: "operational",
                  proofPolicy: "no-click",
                  contract: "component.operational-action",
                  reason: "server lifecycle action is operational and proof-blocked"
                };
              return classifyAction(record, blackboard, config, "task-manager.risk.server-lifecycle");
            }
          },
          {
            id: "task-manager.risk.pid-control",
            phase: "risk-classification",
            priority: 94,
            when(record, blackboard) {
              return inTaskManager(record, blackboard) && executableControl(record) && /(kill|terminate).*(pid|process)|(pid|process).*(kill|terminate)|data-task-action/.test(fullSource(record));
            },
            apply(record, blackboard) {
              const config = /kill/.test(fullSource(record))
                ? {
                  purpose: "task-manager.process-control.kill-pid",
                  role: "kill-pid",
                  risk: "process-destructive",
                  proofPolicy: "no-click",
                  contract: "component.destructive-action",
                  reason: "PID kill is proof-blocked"
                }
                : {
                  purpose: "task-manager.process-control.terminate-pid",
                  role: "terminate-pid",
                  risk: "process-destructive",
                  proofPolicy: "no-click",
                  contract: "component.destructive-action",
                  reason: "PID termination is proof-blocked"
                };
              return classifyAction(record, blackboard, config, "task-manager.risk.pid-control");
            }
          },
          {
            id: "task-manager.risk.schedule-mutation",
            phase: "risk-classification",
            priority: 90,
            when(record, blackboard) {
              return inTaskManager(record, blackboard) && executableControl(record) && /(schedule|delete|run-now|run task|add schedule)/.test(fullSource(record));
            },
            apply(record, blackboard) {
              const destructive = /delete|remove/.test(fullSource(record));
              return classifyAction(record, blackboard, {
                purpose: destructive ? "task-manager.schedule.delete" : "task-manager.schedule.mutate",
                role: destructive ? "schedule-delete" : "schedule-mutation",
                risk: destructive ? "destructive" : "operational",
                proofPolicy: "no-click",
                contract: destructive ? "component.destructive-action" : "component.operational-action",
                reason: "schedule mutation is proof-blocked"
              }, "task-manager.risk.schedule-mutation");
            }
          },
          {
            id: "task-manager.audit-proof-policy",
            phase: "audit",
            priority: 50,
            when(record, blackboard) {
              return record.index === 0 && blackboard.actions.length > 0 && inTaskManager(record, blackboard);
            },
            apply(record, blackboard) {
              blackboard.actions
                .filter((action) => ["server-shutdown", "server-start", "server-restart", "kill-pid", "terminate-pid", "schedule-create", "schedule-delete", "schedule-run-now", "schedule-mutation"].includes(action.role))
                .forEach((action) => {
                  if (!["no-click", "no-submit", "no-command-execution"].includes(action.proofPolicy)) {
                    blackboard.addViolation("task-manager-proof-policy", blackboard.recordById(action.recordId), `${action.role} missing blocking proof policy`, "error");
                  }
                });
              return true;
            }
          }
        ]
      };

      global.McelSupercutPacksTaskManager = {
        TASK_MANAGER_PACK_VERSION,
        KNOWN_RISKY_CONTROLS,
        taskManagerDomainPack
      };

      global.McelSupercutRegistry?.registerPack?.(taskManagerDomainPack);
    })(window);
