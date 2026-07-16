from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WEB_APP = PROJECT_ROOT / "main_computer/web/applications"


def run_node_json(script: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_registry_is_loaded_before_planner_in_applications_shell() -> None:
    shell = (PROJECT_ROOT / "main_computer/web/applications.html").read_text(encoding="utf-8")

    registry_include = "<!-- @include applications/scripts/mcel-domain-adapter-registry.js -->"
    planner_include = "<!-- @include applications/scripts/mcel-specimen-planner.js -->"

    assert registry_include in shell
    assert planner_include in shell
    assert shell.index(registry_include) < shell.index(planner_include)


def test_registry_requires_adapter_methods_before_authorizing_executable_readiness() -> None:
    registry_path = WEB_APP / "scripts" / "mcel-domain-adapter-registry.js"
    planner_path = WEB_APP / "scripts" / "mcel-specimen-planner.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const registrySource = fs.readFileSync({json.dumps(str(registry_path))}, "utf8");
        const plannerSource = fs.readFileSync({json.dumps(str(planner_path))}, "utf8");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(registrySource, sandbox, {{filename: "mcel-domain-adapter-registry.js"}});
        vm.runInNewContext(plannerSource, sandbox, {{filename: "mcel-specimen-planner.js"}});
        const registry = sandbox.McelDomainAdapterRegistry;
        const planner = sandbox.McelSpecimenPlanner;
        registry.registerAdapter({{
          id: "git-tools-incomplete-test-adapter",
          appId: "git-tools",
          version: "test",
          getState() {{ return {{branch: "main"}}; }},
          listIntents() {{ return [{{id: "refreshStatus"}}]; }},
          executeIntent() {{ return {{status: "blocked"}}; }},
          buildReceipt() {{ return {{receiptId: "test"}}; }},
          listObjects() {{ return [{{id: "repo"}}]; }},
          mapEvidence() {{ return []; }},
          classifyFailure() {{ return {{class: "unknown"}}; }},
          buildRecoveryOptions() {{ return []; }}
        }});
        const plan = planner.planFor("git-tools");
        const readiness = planner.semanticReadinessForPlan(plan);
        console.log(JSON.stringify({{
          registryVersion: registry.REGISTRY_VERSION,
          requiredAuthority: readiness.requiredSemanticRuntimeAuthority,
          authority: readiness.semanticRuntimeAuthority,
          registryProofPresent: readiness.registryProofPresent,
          registryProofAuthorized: readiness.registryProofAuthorized,
          status: readiness.semanticRuntimeStatus,
          ready: readiness.semanticRuntimeReady,
          adapterExecutable: readiness.adapterExecutable,
          stateMachineReady: readiness.stateMachineReady,
          actionPlannerReady: readiness.actionPlannerReady,
          capabilityProviderReady: readiness.capabilityProviderReady,
          recoveryReady: readiness.recoveryReady,
          recoveryClassifierPresent: readiness.recoveryClassifierPresent,
          recoveryCoverageReady: readiness.recoveryCoverageReady,
          missing: readiness.missingSemantics
        }}));
        """
    )
    result = run_node_json(script)

    assert result["registryVersion"] == "mcel-domain-adapter-registry-v4"
    assert result["requiredAuthority"] == "mcel-domain-adapter-registry"
    assert result["authority"] == "mcel-domain-adapter-registry"
    assert result["registryProofPresent"] is True
    assert result["registryProofAuthorized"] is True
    assert result["status"] == "domain-enrichment-only"
    assert result["ready"] is False
    assert result["adapterExecutable"] is True
    assert result["stateMachineReady"] is True
    assert result["actionPlannerReady"] is False
    assert result["capabilityProviderReady"] is True
    assert result["recoveryReady"] is False
    assert result["recoveryClassifierPresent"] is True
    assert result["recoveryCoverageReady"] is False
    assert result["missing"] == ["actionPlannerReady", "recoveryReady"]


def test_registry_can_authorize_readiness_only_for_a_complete_domain_adapter_shape() -> None:
    registry_path = WEB_APP / "scripts" / "mcel-domain-adapter-registry.js"
    planner_path = WEB_APP / "scripts" / "mcel-specimen-planner.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const registrySource = fs.readFileSync({json.dumps(str(registry_path))}, "utf8");
        const plannerSource = fs.readFileSync({json.dumps(str(planner_path))}, "utf8");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(registrySource, sandbox, {{filename: "mcel-domain-adapter-registry.js"}});
        vm.runInNewContext(plannerSource, sandbox, {{filename: "mcel-specimen-planner.js"}});
        const registry = sandbox.McelDomainAdapterRegistry;
        const planner = sandbox.McelSpecimenPlanner;
        registry.registerAdapter({{
          id: "git-tools-complete-test-adapter",
          appId: "git-tools",
          version: "test",
          getState() {{ return {{branch: "main", dirty: false}}; }},
          listObjects() {{ return [{{id: "repo", kind: "git-repository"}}]; }},
          listIntents() {{ return [{{id: "refreshStatus", risk: "safe-read"}}]; }},
          preflightIntent() {{ return {{status: "approved", intentId: "refreshStatus"}}; }},
          executeIntent() {{ return {{status: "ok", intentId: "refreshStatus"}}; }},
          buildReceipt() {{ return {{receiptId: "receipt-refreshStatus", status: "ok"}}; }},
          classifyFailure() {{ return {{class: "none"}}; }},
          buildRecoveryOptions() {{ return []; }},
          getRecoveryCoverage() {{
            return {{
              source: "test-verified-recovery-coverage",
              verificationMode: "derived-runtime-audit",
              classificationReady: true,
              guidanceReady: true,
              coverageReady: true,
              requiredFailureClasses: ["test-failure"],
              coveredFailureClasses: ["test-failure"],
              unverifiedFailureClasses: [],
              verification: {{
                passed: true,
                checks: {{
                  definitionsComplete: true,
                  guidanceComplete: true
                }}
              }}
            }};
          }},
          getIntentCoverage() {{
            return {{
              source: "test-derived-intent-coverage",
              verificationMode: "derived-intent-coverage-audit",
              semanticRuntimeScope: "safe-read-complete",
              fullApplicationSemanticReady: true,
              requiredIntentIds: ["refreshStatus"],
              entries: [{{
                intentId: "refreshStatus",
                label: "Refresh status",
                risk: "safe-read",
                status: "executable",
                executionBinding: "test.refresh"
              }}],
              verification: {{passed: true}}
            }};
          }},
          mapEvidence() {{ return [{{receiptId: "receipt-refreshStatus"}}]; }}
        }});
        const plan = planner.planFor("git-tools");
        const readiness = planner.semanticReadinessForPlan(plan);
        const snapshot = registry.snapshotSemanticRuntime("git-tools", plan);
        console.log(JSON.stringify({{
          authority: readiness.semanticRuntimeAuthority,
          status: readiness.semanticRuntimeStatus,
          ready: readiness.semanticRuntimeReady,
          runtimeCoreReady: readiness.runtimeCoreReady,
          fullApplicationSemanticReady: readiness.fullApplicationSemanticReady,
          semanticRuntimeScope: readiness.semanticRuntimeScope,
          adapterKind: readiness.adapterKind,
          missing: readiness.missingSemantics,
          stateSnapshotAvailable: snapshot.stateSnapshotAvailable,
          intentListAvailable: snapshot.intentListAvailable,
          stateBranch: snapshot.state.branch,
          firstIntent: snapshot.intents[0].id
        }}));
        """
    )
    result = run_node_json(script)

    assert result["authority"] == "mcel-domain-adapter-registry"
    assert result["status"] == "executable-semantic-workbench"
    assert result["ready"] is True
    assert result["runtimeCoreReady"] is True
    assert result["fullApplicationSemanticReady"] is True
    assert result["semanticRuntimeScope"] == "safe-read-complete"
    assert result["adapterKind"] == "executable-semantic-workbench"
    assert result["missing"] == []
    assert result["stateSnapshotAvailable"] is True
    assert result["intentListAvailable"] is True
    assert result["stateBranch"] == "main"
    assert result["firstIntent"] == "refreshStatus"


def test_registry_rejects_boolean_only_recovery_coverage_claim() -> None:
    registry_path = WEB_APP / "scripts" / "mcel-domain-adapter-registry.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        const registry = sandbox.McelDomainAdapterRegistry;
        registry.registerAdapter({{
          id: "boolean-only",
          appId: "git-tools",
          getState() {{ return {{}}; }},
          listObjects() {{ return []; }},
          listIntents() {{ return []; }},
          preflightIntent() {{ return {{}}; }},
          executeIntent() {{ return {{}}; }},
          buildReceipt() {{ return {{}}; }},
          classifyFailure() {{ return {{}}; }},
          buildRecoveryOptions() {{ return []; }},
          mapEvidence() {{ return []; }},
          getRecoveryCoverage() {{
            return {{
              source: "unverified-boolean",
              coverageReady: true,
              requiredFailureClasses: ["test-failure"],
              coveredFailureClasses: ["test-failure"]
            }};
          }}
        }});
        console.log(JSON.stringify(
          registry.evaluateAdapterReadiness("git-tools")
        ));
        """
    )
    readiness = run_node_json(script)
    assert readiness["recoveryClassifierPresent"] is True
    assert readiness["recoveryCoverageReady"] is False
    assert readiness["recoveryReady"] is False
    assert readiness["semanticRuntimeReady"] is False
    assert readiness["recoveryCoverageValidation"]["checks"]["derivedAudit"] is False


def test_registry_separates_runtime_core_from_full_application_intent_coverage() -> None:
    registry_path = WEB_APP / "scripts" / "mcel-domain-adapter-registry.js"
    script = textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(registry_path))}, "utf8"),
          sandbox,
          {{filename: "mcel-domain-adapter-registry.js"}}
        );
        const registry = sandbox.McelDomainAdapterRegistry;
        registry.registerAdapter({{
          id: "partial-intent-coverage",
          appId: "git-tools",
          getState() {{ return {{}}; }},
          listObjects() {{ return []; }},
          listIntents() {{ return []; }},
          preflightIntent() {{ return {{}}; }},
          executeIntent() {{ return {{}}; }},
          buildReceipt() {{ return {{}}; }},
          classifyFailure() {{ return {{}}; }},
          buildRecoveryOptions() {{ return []; }},
          mapEvidence() {{ return []; }},
          getRecoveryCoverage() {{
            return {{
              source: "test-recovery",
              verificationMode: "derived-runtime-audit",
              classificationReady: true,
              guidanceReady: true,
              coverageReady: true,
              requiredFailureClasses: ["test-failure"],
              coveredFailureClasses: ["test-failure"],
              unverifiedFailureClasses: [],
              verification: {{passed: true}}
            }};
          }},
          getIntentCoverage() {{
            return {{
              source: "test-intent-coverage",
              verificationMode: "derived-intent-coverage-audit",
              semanticRuntimeScope: "safe-read-partial",
              fullApplicationSemanticReady: false,
              requiredIntentIds: ["refreshStatus", "inspectWorkingTree"],
              entries: [
                {{
                  intentId: "refreshStatus",
                  label: "Refresh status",
                  risk: "safe-read",
                  status: "executable",
                  executionBinding: "test.refresh"
                }},
                {{
                  intentId: "inspectWorkingTree",
                  label: "Inspect working tree",
                  risk: "safe-read",
                  status: "declared-only",
                  executionBinding: "not-registered"
                }}
              ],
              verification: {{passed: true}}
            }};
          }}
        }});
        console.log(JSON.stringify(
          registry.evaluateAdapterReadiness("git-tools")
        ));
        """
    )
    readiness = run_node_json(script)

    assert readiness["runtimeCoreReady"] is True
    assert readiness["fullApplicationSemanticReady"] is False
    assert readiness["semanticRuntimeReady"] is False
    assert readiness["semanticRuntimeScope"] == "safe-read-partial"
    assert readiness["executableIntentCount"] == 1
    assert readiness["declaredOnlyIntentCount"] == 1
    assert readiness["preflightOnlyIntentCount"] == 0
    assert readiness["prohibitedIntentCount"] == 0
    assert readiness["blockedIntentCount"] == 1
    assert readiness["totalIntentCount"] == 2
    assert readiness["intentCoverageAuditReady"] is True
    assert readiness["intentCoverageReady"] is False
    assert readiness["missingApplicationSemantics"] == ["inspectWorkingTree"]
    assert readiness["adapterKind"] == "scope-limited-executable-semantic-workbench"
