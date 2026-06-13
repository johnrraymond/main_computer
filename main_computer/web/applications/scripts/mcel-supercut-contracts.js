    (function (global) {
      "use strict";

      const CONTRACT_VERSION = "0.2.0";
      const CONTRACTS = Object.freeze({
        "component.root": Object.freeze({
          id: "component.root",
          label: "Application root",
          expectedRole: "root application boundary",
          rewriteTag: "mcel-app",
          riskDefault: "none",
          proofPolicy: "inspect-only",
          layoutExpectation: "single top-level semantic runtime surface",
          requiredEvidence: ["root-node"]
        }),
        "component.region": Object.freeze({
          id: "component.region",
          label: "Major region",
          expectedRole: "landmark or major named app region",
          rewriteTag: "mcel-region",
          riskDefault: "none",
          proofPolicy: "inspect-only",
          layoutExpectation: "few coarse regions; do not classify every wrapper as a region",
          requiredEvidence: ["landmark-or-region-signal"]
        }),
        "component.panel": Object.freeze({
          id: "component.panel",
          label: "Panel",
          expectedRole: "bounded content or workflow panel",
          rewriteTag: "mcel-panel",
          riskDefault: "none",
          proofPolicy: "inspect-only",
          layoutExpectation: "self-contained card, details group, workflow pane, or form block",
          requiredEvidence: ["panel-signal"]
        }),
        "component.toolbar": Object.freeze({
          id: "component.toolbar",
          label: "Toolbar",
          expectedRole: "cluster of related action controls",
          rewriteTag: "mcel-toolbar",
          riskDefault: "safe",
          proofPolicy: "inspect-only",
          layoutExpectation: "wraps safely without forcing nested scrollbars",
          requiredEvidence: ["multiple-controls"]
        }),
        "component.field": Object.freeze({
          id: "component.field",
          label: "Field",
          expectedRole: "data entry or selection control",
          rewriteTag: "mcel-field",
          riskDefault: "safe",
          proofPolicy: "inspect-only",
          layoutExpectation: "label and control remain paired",
          requiredEvidence: ["field-control"]
        }),
        "component.action": Object.freeze({
          id: "component.action",
          label: "Action",
          expectedRole: "safe or inspectable user action",
          rewriteTag: "mcel-action",
          riskDefault: "safe",
          proofPolicy: "inspect-only",
          layoutExpectation: "visible action surface with explicit risk policy",
          requiredEvidence: ["tag:button", "action-text"]
        }),
        "component.status-feed": Object.freeze({
          id: "component.status-feed",
          label: "Status feed",
          expectedRole: "status, output, report, log, or activity surface",
          rewriteTag: "mcel-status-feed",
          riskDefault: "none",
          proofPolicy: "inspect-only",
          layoutExpectation: "bounded output surface; wrap or scroll intentionally",
          requiredEvidence: ["status-or-output-signal"]
        }),
        "component.console": Object.freeze({
          id: "component.console",
          label: "Console",
          expectedRole: "manual or generated command/output surface",
          rewriteTag: "mcel-console",
          riskDefault: "command-execution",
          proofPolicy: "no-command-execution",
          layoutExpectation: "monospace command surface remains inert under proof",
          requiredEvidence: ["console-signal"]
        }),
        "component.workflow": Object.freeze({
          id: "component.workflow",
          label: "Workflow",
          expectedRole: "ordered operation or progressive disclosure flow",
          rewriteTag: "mcel-workflow",
          riskDefault: "operational",
          proofPolicy: "inspect-only",
          layoutExpectation: "steps stay grouped and inspectable",
          requiredEvidence: ["workflow-signal"]
        }),
        "component.operational-action": Object.freeze({
          id: "component.operational-action",
          label: "Operational action",
          expectedRole: "runtime operation trigger",
          rewriteTag: "mcel-action",
          riskDefault: "operational",
          proofPolicy: "no-click",
          layoutExpectation: "action remains visible but proof does not activate it",
          requiredEvidence: ["tag:button", "action-text"]
        }),
        "component.destructive-action": Object.freeze({
          id: "component.destructive-action",
          label: "Destructive action",
          expectedRole: "destructive or irreversible operation trigger",
          rewriteTag: "mcel-action",
          riskDefault: "destructive",
          proofPolicy: "no-click",
          layoutExpectation: "action remains visible and sidecar-audited only",
          requiredEvidence: ["tag:button", "destructive-action-text"]
        }),
        "component.remote-mutation-action": Object.freeze({
          id: "component.remote-mutation-action",
          label: "Remote mutation action",
          expectedRole: "push, publish, mirror, remote, or network mutation trigger",
          rewriteTag: "mcel-action",
          riskDefault: "remote-mutation",
          proofPolicy: "no-submit",
          layoutExpectation: "proof may inspect but never submit remote mutations",
          requiredEvidence: ["remote-mutation-text"]
        }),
        "component.unknown": Object.freeze({
          id: "component.unknown",
          label: "Unknown component",
          expectedRole: "unclassified but measured legacy element",
          rewriteTag: "mcel-unknown",
          riskDefault: "unknown",
          proofPolicy: "inspect-only",
          layoutExpectation: "unknowns are counted so rule gaps remain visible",
          requiredEvidence: ["fallback-record"]
        })
      });

      const RISK_POLICY = Object.freeze({
        none: {risk: "none", proofPolicy: "inspect-only", blocked: false},
        safe: {risk: "safe", proofPolicy: "inspect-only", blocked: false},
        analysis: {risk: "analysis", proofPolicy: "inspect-only", blocked: false},
        operational: {risk: "operational", proofPolicy: "no-click", blocked: true},
        destructive: {risk: "destructive", proofPolicy: "no-click", blocked: true},
        "process-destructive": {risk: "process-destructive", proofPolicy: "no-click", blocked: true},
        "server-control": {risk: "server-control", proofPolicy: "no-click", blocked: true},
        "remote-mutation": {risk: "remote-mutation", proofPolicy: "no-submit", blocked: true},
        "credential-network-mutation": {risk: "credential-network-mutation", proofPolicy: "no-submit", blocked: true},
        "command-execution": {risk: "command-execution", proofPolicy: "no-command-execution", blocked: true},
        unknown: {risk: "unknown", proofPolicy: "inspect-only", blocked: false}
      });

      function clone(value) {
        return JSON.parse(JSON.stringify(value));
      }

      function getContract(id) {
        return CONTRACTS[id] || CONTRACTS["component.unknown"];
      }

      function allContracts() {
        return clone(CONTRACTS);
      }

      function riskPolicy(risk) {
        return RISK_POLICY[risk] || RISK_POLICY.unknown;
      }

      function proofPolicyForRisk(risk) {
        return riskPolicy(risk).proofPolicy;
      }

      function contractForKind(kind, risk) {
        if (risk === "command-execution") return getContract("component.console");
        if (["destructive", "process-destructive"].includes(risk)) return getContract("component.destructive-action");
        if (["remote-mutation", "credential-network-mutation"].includes(risk)) return getContract("component.remote-mutation-action");
        if (["operational", "server-control"].includes(risk)) return getContract("component.operational-action");
        if (kind === "root") return getContract("component.root");
        if (kind === "region") return getContract("component.region");
        if (kind === "panel") return getContract("component.panel");
        if (kind === "toolbar") return getContract("component.toolbar");
        if (kind === "field") return getContract("component.field");
        if (kind === "action") return getContract("component.action");
        if (kind === "status-feed") return getContract("component.status-feed");
        if (kind === "console") return getContract("component.console");
        if (kind === "workflow") return getContract("component.workflow");
        return getContract("component.unknown");
      }

      global.McelSupercutContracts = {
        CONTRACT_VERSION,
        CONTRACTS,
        RISK_POLICY,
        allContracts,
        getContract,
        riskPolicy,
        proofPolicyForRisk,
        contractForKind
      };
    })(window);
