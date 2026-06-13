    (function (global) {
      "use strict";

      const GIT_TOOLS_PACK_VERSION = "0.2.0";

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

      function actionLike(record) {
        return ["button", "summary"].includes(record?.tag) || record?.role === "button" || record?.tag === "a";
      }

      function addPurpose(record, blackboard, purpose, role, contract, ruleId, extra = {}) {
        const ev = blackboard.addEvidence(record, "git-tools-domain", purpose, ruleId).id;
        blackboard.ensureComponent(record, {
          kind: extra.kind || (contract === "component.action" ? "action" : "panel"),
          role,
          purpose,
          contract,
          risk: extra.risk || record.risk || "none",
          proofPolicy: extra.proofPolicy || record.proofPolicy || "inspect-only",
          evidence: [ev]
        }, ruleId);
        blackboard.explain(record, `Git Tools domain pack recognized ${purpose}.`, [record.sourceSelector, record.text || record.signature], ruleId);
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
          label: record.text || record.domId || record.sourceSelector,
          risk: config.risk,
          proofPolicy: config.proofPolicy,
          contract: config.contract,
          purpose: config.purpose,
          evidence: [ev]
        }, ruleId);
        return true;
      }

      const gitToolsDomainPack = {
        id: "git-tools-domain",
        version: GIT_TOOLS_PACK_VERSION,
        description: "Git Tools-specific semantic and proof-policy knowledge pack",
        rules: [
          {
            id: "git-tools.detect-root",
            phase: "purpose-inference",
            priority: 100,
            when(record, blackboard) {
              return record?.element === blackboard?.rootNode || record.domId === "git-tools-app";
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "git-tools.root", "git-tools-root", "component.root", "git-tools.detect-root", {
                kind: "root",
                risk: "none",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "git-tools.detect-project-selection",
            phase: "purpose-inference",
            priority: 92,
            when(record) {
              return /(project|repository|repo selector|git-project|working tree)/.test(source(record)) && !actionLike(record);
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "git-tools.project-selection", "project-selection", "component.panel", "git-tools.detect-project-selection", {
                kind: "panel",
                risk: "none",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "git-tools.detect-git-start",
            phase: "purpose-inference",
            priority: 90,
            when(record) {
              return /(git-tools-start|start git tools|open git tools|repository operations)/.test(source(record)) && !/(gitea|server)/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "git-tools.start", "git-tools-start", "component.workflow", "git-tools.detect-git-start", {
                kind: "workflow",
                risk: "operational",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "git-tools.detect-gitea-server-control",
            phase: "purpose-inference",
            priority: 88,
            when(record) {
              return /(gitea|server).*(start|stop|restart|status|control)|server-control|gitea-server/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "git-tools.gitea-server-control", "gitea-server-control", "component.workflow", "git-tools.detect-gitea-server-control", {
                kind: "workflow",
                risk: "operational",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "git-tools.detect-status-report",
            phase: "purpose-inference",
            priority: 86,
            when(record) {
              return /(status|report|output|activity|log|diagnostic|result)/.test(source(record)) && !actionLike(record);
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "git-tools.status-report", "status-report", "component.status-feed", "git-tools.detect-status-report", {
                kind: "status-feed",
                risk: "none",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "git-tools.detect-publish-workflow",
            phase: "purpose-inference",
            priority: 84,
            when(record) {
              return /(publish|push|mirror|remote|gitea workflow|deploy)/.test(source(record)) && !actionLike(record);
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "git-tools.gitea-publish-workflow", "gitea-publish-workflow", "component.workflow", "git-tools.detect-publish-workflow", {
                kind: "workflow",
                risk: "remote-mutation",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "git-tools.detect-operation-activity",
            phase: "purpose-inference",
            priority: 82,
            when(record) {
              return /(operation|activity|progress|queue|job|history)/.test(source(record)) && !actionLike(record);
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "git-tools.operation-activity", "operation-activity", "component.status-feed", "git-tools.detect-operation-activity", {
                kind: "status-feed",
                risk: "none",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "git-tools.detect-remote-configuration",
            phase: "purpose-inference",
            priority: 80,
            when(record) {
              return /(remote|origin|mirror|credential|token|gitea host|repository url)/.test(source(record)) && !actionLike(record);
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "git-tools.remote-configuration", "remote-configuration", "component.panel", "git-tools.detect-remote-configuration", {
                kind: "panel",
                risk: "credential-network-mutation",
                proofPolicy: "inspect-only"
              });
              return true;
            }
          },
          {
            id: "git-tools.detect-manual-console",
            phase: "purpose-inference",
            priority: 78,
            when(record) {
              return /(manual command|manual console|command console|run command|shell command|terminal)/.test(source(record));
            },
            apply(record, blackboard) {
              addPurpose(record, blackboard, "git-tools.manual-console", "manual-console", "component.console", "git-tools.detect-manual-console", {
                kind: "console",
                risk: "command-execution",
                proofPolicy: "no-command-execution"
              });
              blackboard.addRisk(record, "command-execution", "no-command-execution", "manual command surfaces are inspect-only", "git-tools.detect-manual-console");
              return true;
            }
          },
          {
            id: "git-tools.risk.start-server",
            phase: "risk-classification",
            priority: 100,
            when(record) {
              return actionLike(record) && /(start).*(server|gitea)|(server|gitea).*(start)/.test(source(record));
            },
            apply(record, blackboard) {
              return classifyAction(record, blackboard, {
                purpose: "git-tools.gitea-server-control.start",
                role: "start-server",
                risk: "operational",
                proofPolicy: "no-click",
                contract: "component.operational-action",
                reason: "starting a local server is operational and not activated by proof"
              }, "git-tools.risk.start-server");
            }
          },
          {
            id: "git-tools.risk.stop-server",
            phase: "risk-classification",
            priority: 99,
            when(record) {
              return actionLike(record) && /(stop|shutdown).*(server|gitea)|(server|gitea).*(stop|shutdown)/.test(source(record));
            },
            apply(record, blackboard) {
              return classifyAction(record, blackboard, {
                purpose: "git-tools.gitea-server-control.stop",
                role: "stop-server",
                risk: "destructive",
                proofPolicy: "no-click",
                contract: "component.destructive-action",
                reason: "stopping a server is destructive to running local state"
              }, "git-tools.risk.stop-server");
            }
          },
          {
            id: "git-tools.risk.restart-server",
            phase: "risk-classification",
            priority: 98,
            when(record) {
              return actionLike(record) && /(restart|reload).*(server|gitea)|(server|gitea).*(restart|reload)/.test(source(record));
            },
            apply(record, blackboard) {
              return classifyAction(record, blackboard, {
                purpose: "git-tools.gitea-server-control.restart",
                role: "restart-server",
                risk: "operational",
                proofPolicy: "no-click",
                contract: "component.operational-action",
                reason: "restarting a server is operational and not activated by proof"
              }, "git-tools.risk.restart-server");
            }
          },
          {
            id: "git-tools.risk.kill-pid",
            phase: "risk-classification",
            priority: 97,
            when(record) {
              return actionLike(record) && /(kill|terminate).*(pid|process)|(pid|process).*(kill|terminate)/.test(source(record));
            },
            apply(record, blackboard) {
              return classifyAction(record, blackboard, {
                purpose: "git-tools.process-control.kill-pid",
                role: "kill-pid",
                risk: "process-destructive",
                proofPolicy: "no-click",
                contract: "component.destructive-action",
                reason: "PID termination is destructive and proof-blocked"
              }, "git-tools.risk.kill-pid");
            }
          },
          {
            id: "git-tools.risk.publish",
            phase: "risk-classification",
            priority: 96,
            when(record) {
              return actionLike(record) && /(publish|deploy)/.test(source(record));
            },
            apply(record, blackboard) {
              return classifyAction(record, blackboard, {
                purpose: "git-tools.remote-mutation.publish",
                role: "publish",
                risk: "remote-mutation",
                proofPolicy: "no-submit",
                contract: "component.remote-mutation-action",
                reason: "publishing mutates remote state"
              }, "git-tools.risk.publish");
            }
          },
          {
            id: "git-tools.risk.push",
            phase: "risk-classification",
            priority: 95,
            when(record) {
              return actionLike(record) && /\bpush\b/.test(source(record));
            },
            apply(record, blackboard) {
              return classifyAction(record, blackboard, {
                purpose: "git-tools.remote-mutation.push",
                role: "push",
                risk: "remote-mutation",
                proofPolicy: "no-submit",
                contract: "component.remote-mutation-action",
                reason: "git push mutates remote state"
              }, "git-tools.risk.push");
            }
          },
          {
            id: "git-tools.risk.mirror",
            phase: "risk-classification",
            priority: 94,
            when(record) {
              return actionLike(record) && /\bmirror\b/.test(source(record));
            },
            apply(record, blackboard) {
              return classifyAction(record, blackboard, {
                purpose: "git-tools.remote-mutation.mirror",
                role: "mirror",
                risk: "remote-mutation",
                proofPolicy: "no-submit",
                contract: "component.remote-mutation-action",
                reason: "mirroring mutates remote repository state"
              }, "git-tools.risk.mirror");
            }
          },
          {
            id: "git-tools.risk.configure-remote",
            phase: "risk-classification",
            priority: 93,
            when(record) {
              return actionLike(record) && /(remote|origin|credential|token|url|configure)/.test(source(record)) && /(save|set|apply|configure|create|update)/.test(source(record));
            },
            apply(record, blackboard) {
              return classifyAction(record, blackboard, {
                purpose: "git-tools.remote-configuration.configure",
                role: "configure-remote",
                risk: "credential-network-mutation",
                proofPolicy: "no-submit",
                contract: "component.remote-mutation-action",
                reason: "remote configuration may update credentials or network targets"
              }, "git-tools.risk.configure-remote");
            }
          },
          {
            id: "git-tools.risk.manual-command",
            phase: "risk-classification",
            priority: 92,
            when(record) {
              return actionLike(record) && /(manual command|run command|execute|terminal|shell)/.test(source(record));
            },
            apply(record, blackboard) {
              return classifyAction(record, blackboard, {
                purpose: "git-tools.manual-console.run-command",
                role: "run-manual-command",
                risk: "command-execution",
                proofPolicy: "no-command-execution",
                contract: "component.console",
                reason: "manual commands are never executed by Supercut proof"
              }, "git-tools.risk.manual-command");
            }
          },
          {
            id: "git-tools.audit-proof-policy",
            phase: "audit",
            priority: 50,
            when(record, blackboard) {
              return record.index === 0 && blackboard.actions.length > 0;
            },
            apply(record, blackboard) {
              blackboard.actions
                .filter((action) => ["start-server", "stop-server", "restart-server", "kill-pid", "publish", "push", "mirror", "configure-remote", "run-manual-command"].includes(action.role))
                .forEach((action) => {
                  if (!["no-click", "no-submit", "no-command-execution"].includes(action.proofPolicy)) {
                    blackboard.addViolation("git-tools-proof-policy", blackboard.recordById(action.recordId), `${action.role} missing blocking proof policy`, "error");
                  }
                });
              return true;
            }
          }
        ]
      };

      global.McelSupercutPacksGitTools = {
        GIT_TOOLS_PACK_VERSION,
        gitToolsDomainPack
      };

      global.McelSupercutRegistry?.registerPack?.(gitToolsDomainPack);
    })(window);
