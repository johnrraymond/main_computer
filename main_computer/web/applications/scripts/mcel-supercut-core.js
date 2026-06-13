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

      function buildRewritePreview(blackboard) {
        (blackboard?.components || []).forEach((component) => {
          blackboard.addRewriteNode(component);
        });
        return blackboard.rewritePreview;
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
          : ["core-html", "core-action-risk", "git-tools-domain"];
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
        blackboard.metrics.unsafeActionsBlocked = blackboard.actions.filter((action) => action.blocked).length;
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
