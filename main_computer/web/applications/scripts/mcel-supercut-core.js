    (function (global) {
      "use strict";

      const CORE_VERSION = "0.2.0";

      function summarizeRewritePreview(rewritePreview) {
        const summary = {
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
        };
        (rewritePreview || []).forEach((node) => {
          if (node.contract === "component.root") summary.root += 1;
          else if (node.contract === "component.region") summary.regions += 1;
          else if (node.contract === "component.panel") summary.panels += 1;
          else if (node.contract === "component.toolbar") summary.toolbars += 1;
          else if (node.contract === "component.field") summary.fields += 1;
          else if (["component.action", "component.operational-action", "component.destructive-action", "component.remote-mutation-action"].includes(node.contract)) summary.actions += 1;
          else if (node.contract === "component.status-feed") summary.statusFeeds += 1;
          else if (node.contract === "component.console") summary.consoles += 1;
          else if (node.contract === "component.workflow") summary.workflows += 1;
          else summary.unknown += 1;
        });
        return summary;
      }

      function isBlockedSurfaceContract(contractId) {
        return [
          "component.action",
          "component.operational-action",
          "component.destructive-action",
          "component.remote-mutation-action",
          "component.console"
        ].includes(contractId);
      }

      function executableSurfaceRecord(record) {
        return ["button", "summary"].includes(record?.tag) ||
          record?.role === "button" ||
          record?.tag === "a" ||
          Boolean(record?.element?.matches?.("button, a[href], summary, [role=\"button\"]"));
      }

      function shouldEnforceBlockingPolicy(component, blackboard) {
        if (!isBlockedSurfaceContract(component?.contract)) return false;
        const record = blackboard?.recordById?.(component.recordId);
        if (!record) return component?.contract !== "component.console";
        return executableSurfaceRecord(record);
      }

      function applySafeRuntimeTags(blackboard) {
        (blackboard?.components || []).forEach((component) => {
          const record = blackboard.recordById?.(component.recordId);
          const element = record?.element;
          if (!element?.setAttribute) return;
          element.setAttribute("data-mcel-supercut-contract", component.contract || "component.unknown");
          element.setAttribute("data-mcel-supercut-proof-policy", component.proofPolicy || "inspect-only");
          element.setAttribute("data-mcel-supercut-rewrite-tag", component.rewriteTag || "mcel-unknown");
          if (component.risk) element.setAttribute("data-mcel-supercut-risk", component.risk);
          if (component.originalPurpose || component.purpose) {
            element.setAttribute("data-mcel-supercut-purpose", component.purpose || component.originalPurpose);
          }
        });
        return true;
      }

      function normalizeComponentsBeforeRewrite(blackboard) {
        const contracts = global.McelSupercutContracts;
        (blackboard?.components || []).forEach((component) => {
          const record = blackboard.recordById?.(component.recordId);
          const isRoot = record?.element === blackboard.rootNode || record?.index === 0 || component.sourceSelector === blackboard.rootSelector;
          if (isRoot) {
            component.kind = "root";
            component.role = component.role || "app-root";
            component.contract = "component.root";
            component.risk = "none";
            component.proofPolicy = "inspect-only";
            component.rewriteTag = "mcel-app";
            if (record) {
              record.kind = component.kind;
              record.contract = component.contract;
              record.risk = component.risk;
              record.proofPolicy = component.proofPolicy;
              record.rewriteTag = component.rewriteTag;
            }
            return;
          }
          const contract = contracts?.getContract?.(component.contract) || null;
          const riskPolicy = contracts?.riskPolicy?.(component.risk || contract?.riskDefault) || null;
          if (contract?.rewriteTag) component.rewriteTag = contract.rewriteTag;
          if (riskPolicy?.blocked && shouldEnforceBlockingPolicy(component, blackboard)) {
            component.proofPolicy = riskPolicy.proofPolicy;
          } else if (contract?.proofPolicy) {
            component.proofPolicy = contract.proofPolicy;
          }
          if (record) {
            record.contract = component.contract;
            record.risk = component.risk;
            record.proofPolicy = component.proofPolicy;
            record.rewriteTag = component.rewriteTag;
          }
        });
        return blackboard?.components || [];
      }

      function buildRewritePreview(blackboard) {
        normalizeComponentsBeforeRewrite(blackboard);
        (blackboard?.components || []).forEach((component) => {
          blackboard.addRewriteNode(component);
        });
        return blackboard.rewritePreview;
      }

      function plannedDomainUnsafeFamilyKey(record, item = {}, blackboard) {
        const specimenId = String(blackboard?.specimenId || blackboard?.rootNode?.id || "").replace(/-app$/, "");
        const purpose = String(item.purpose || "");
        const role = String(item.role || "");
        if (!specimenId || ["task-manager", "git-tools", "calculator"].includes(specimenId)) return "";
        if (purpose.startsWith(`${specimenId}.`)) {
          const parts = purpose.split(".").filter(Boolean);
          if (parts.length >= 3 && ["risk", "action", "safe"].includes(parts[1])) return parts.slice(0, 3).join(".");
          if (parts.length >= 2) return parts.slice(0, Math.min(3, parts.length)).join(".");
        }
        if (role) return `${specimenId}.${role}`;
        const text = [
          record?.domId || "",
          record?.componentId || "",
          record?.directText || "",
          record?.element?.getAttribute?.("aria-label") || ""
        ].join(" ").toLowerCase();
        const family = [
          "send", "delete", "export", "import", "upload", "download", "run", "execute", "command",
          "save", "publish", "deploy", "sign", "approve", "transaction", "install", "start", "stop", "restart"
        ].find((term) => text.includes(term));
        return family ? `${specimenId}.${family}` : "";
      }

      function taskManagerUnsafeFamilyKey(record, item = {}) {
        const element = record?.element || null;
        const domId = record?.domId || "";
        const role = item.role || "";
        const purpose = item.purpose || "";
        const dataAction = element?.getAttribute?.("data-task-action") || "";
        if (dataAction === "kill") return "task-manager.process-control.kill-pid";
        if (dataAction === "terminate" || dataAction === "terminate-pid") return "task-manager.process-control.terminate-pid";
        if (element?.hasAttribute?.("data-task-delete")) return "task-manager.schedule.delete";
        if (element?.hasAttribute?.("data-task-run")) {
          const actionName = element.getAttribute?.("data-task-action-name") || "run-now";
          return `task-manager.schedule.run.${actionName}`;
        }
        if (domId === "task-server-shutdown") return "task-manager.server-control.shutdown";
        if (domId === "task-server-start") return "task-manager.server-control.start";
        if (domId === "task-server-restart") return "task-manager.server-control.restart";
        if (domId === "task-schedule-create") return "task-manager.schedule.create";
        if (role === "kill-pid" || purpose.includes("kill-pid")) return "task-manager.process-control.kill-pid";
        if (role === "terminate-pid" || purpose.includes("terminate-pid")) return "task-manager.process-control.terminate-pid";
        if (role === "server-shutdown" || purpose.includes("server-control.shutdown")) return "task-manager.server-control.shutdown";
        if (role === "server-start" || purpose.includes("server-control.start")) return "task-manager.server-control.start";
        if (role === "server-restart" || purpose.includes("server-control.restart")) return "task-manager.server-control.restart";
        if (role === "schedule-create" || purpose.includes("schedule.create")) return "task-manager.schedule.create";
        if (role === "schedule-delete" || purpose.includes("schedule.delete")) return "task-manager.schedule.delete";
        if (role === "schedule-run-now" || purpose.includes("schedule.run")) return "task-manager.schedule.run-now";
        if (role === "schedule-mutation" || purpose.includes("schedule.mutate")) return "task-manager.schedule.mutate";
        return "";
      }

      function blockedSurfaceMetricKey(record, item = {}, blackboard, mode = "families") {
        if (!record) return item.selector || item.id || "";
        if (mode === "instances") return record.id || item.recordId || item.selector || item.id || "";
        if (blackboard?.specimenId === "task-manager" || blackboard?.rootNode?.id === "task-manager-app") {
          return taskManagerUnsafeFamilyKey(record, item) || (record.id || item.recordId || item.selector || item.id || "");
        }
        const plannedKey = plannedDomainUnsafeFamilyKey(record, item, blackboard);
        if (plannedKey) return plannedKey;
        return record.id || item.recordId || item.selector || item.id || "";
      }

      function countUnsafeBlocked(blackboard, mode = "families") {
        const contracts = global.McelSupercutContracts;
        const blocked = new Set();
        const addIfExecutable = (recordId, item = {}) => {
          const record = blackboard?.recordById?.(recordId);
          if (record && executableSurfaceRecord(record)) {
            const key = blockedSurfaceMetricKey(record, item, blackboard, mode);
            if (key) blocked.add(key);
          }
        };
        (blackboard?.actions || []).forEach((action) => {
          if (action.blocked || contracts?.riskPolicy?.(action.risk)?.blocked) {
            addIfExecutable(action.recordId, action);
          }
        });
        (blackboard?.risks || []).forEach((risk) => {
          const currentAction = (blackboard?.actions || []).find((action) => action.recordId === risk.recordId);
          const currentComponent = (blackboard?.components || []).find((component) => component.recordId === risk.recordId);
          const currentActionBlocked = currentAction && (currentAction.blocked || contracts?.riskPolicy?.(currentAction.risk)?.blocked);
          const currentComponentBlocked = currentComponent && contracts?.riskPolicy?.(currentComponent.risk)?.blocked && shouldEnforceBlockingPolicy(currentComponent, blackboard);
          if (currentAction && !currentActionBlocked) return;
          if (!currentAction && currentComponent && !currentComponentBlocked) return;
          if (risk.blocked || contracts?.riskPolicy?.(risk.risk)?.blocked) {
            addIfExecutable(risk.recordId, risk);
          }
        });
        return blocked.size;
      }

      function run(options = {}) {
        const rootDocument = options.rootDocument || options.document || global.document;
        const rootElement = options.rootElement || options.root || rootDocument?.querySelector?.(options.rootSelector || "body") || null;
        const specimenId = options.specimenId || options.app || rootElement?.id || "legacy-html";
        if (!rootDocument || !rootElement || !global.McelSupercutBlackboard?.createBlackboard || !global.McelSupercutRegistry?.run) {
          return {
            status: "unavailable",
            specimenId,
            roundsCompleted: 0,
            metrics: {
              nodesScanned: 0,
              components: 0,
              regions: 0,
              actionsClassified: 0,
              unsafeActionsBlocked: 0,
              unsafeActionInstancesBlocked: 0,
              rulesFired: 0,
              packsLoaded: 0,
              rewritePreviewNodes: 0,
              violations: 0,
              sourceMutations: 0,
              runtimeSourceMutations: 0
            },
            blackboard: null,
            components: [],
            regions: [],
            actions: [],
            risks: [],
            layoutLaws: [],
            violations: [],
            explanations: [],
            rewritePreview: [],
            rewritePreviewSummary: summarizeRewritePreview([])
          };
        }

        global.McelSupercutRegistry.loadDefaultPacks?.();
        const packs = Array.isArray(options.packs) && options.packs.length
          ? options.packs
          : [
            "core-html",
            "core-action-risk",
            "git-tools-domain",
            "task-manager-domain",
            "calculator-domain",
            ...(global.McelSupercutPacksPlannerDomains?.plannerDomainPacks || []).map((pack) => pack.id)
          ];
        const blackboard = global.McelSupercutBlackboard.createBlackboard({
          rootDocument,
          rootElement,
          rootSelector: options.rootSelector,
          specimenId,
          maxRecords: options.maxRecords || options.maxComponents || 260
        });
        const registryResult = global.McelSupercutRegistry.run(blackboard, {packs});

        buildRewritePreview(blackboard);
        applySafeRuntimeTags(blackboard);
        blackboard.metrics.packsLoaded = registryResult.packsLoaded || blackboard.metrics.packsLoaded || 0;
        blackboard.metrics.rulesFired = registryResult.rulesFired || blackboard.metrics.rulesFired || 0;
        blackboard.metrics.rewritePreviewNodes = blackboard.rewritePreview.length;
        blackboard.metrics.components = blackboard.components.length;
        blackboard.metrics.regions = blackboard.regions.length;
        blackboard.metrics.actionsClassified = blackboard.actions.length;
        blackboard.metrics.unsafeActionInstancesBlocked = countUnsafeBlocked(blackboard, "instances");
        blackboard.metrics.unsafeActionsBlocked = countUnsafeBlocked(blackboard, "families");
        blackboard.metrics.violations = blackboard.violations.length;
        blackboard.metrics.explanationsReady = blackboard.explanations.length;
        blackboard.metrics.sourceMutations = 0;
        blackboard.metrics.runtimeSourceMutations = 0;

        const serializableBlackboard = global.McelSupercutBlackboard.serializableBlackboard(blackboard);
        return {
          status: "ready",
          version: CORE_VERSION,
          specimenId,
          mode: options.mode || "tag-and-audit",
          roundsCompleted: Number(options.rounds || 3),
          packsLoaded: registryResult.packs || [],
          ruleTrace: registryResult.ruleTrace || [],
          metrics: {...blackboard.metrics},
          blackboard: serializableBlackboard,
          components: blackboard.components,
          regions: blackboard.regions,
          actions: blackboard.actions,
          risks: blackboard.risks,
          layoutLaws: blackboard.layoutLaws,
          violations: blackboard.violations,
          explanations: blackboard.explanations,
          rewritePreview: blackboard.rewritePreview,
          rewritePreviewSummary: summarizeRewritePreview(blackboard.rewritePreview),
          sourceMutations: 0,
          runtimeSourceMutations: 0,
          safetyPolicy: {
            sourceRewrite: "disabled",
            destructiveActionExecution: "blocked",
            serverControlClicks: "blocked",
            pidTermination: "blocked",
            remoteMirrorPushManualCommand: "blocked"
          }
        };
      }

      global.McelSupercutCore = {
        CORE_VERSION,
        run,
        summarizeRewritePreview
      };
    })(window);
