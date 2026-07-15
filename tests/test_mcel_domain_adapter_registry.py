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
          missing: readiness.missingSemantics
        }}));
        """
    )
    result = run_node_json(script)

    assert result["registryVersion"] == "mcel-domain-adapter-registry-v1"
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
    assert result["recoveryReady"] is True
    assert result["missing"] == ["actionPlannerReady"]


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
          mapEvidence() {{ return [{{receiptId: "receipt-refreshStatus"}}]; }}
        }});
        const plan = planner.planFor("git-tools");
        const readiness = planner.semanticReadinessForPlan(plan);
        const snapshot = registry.snapshotSemanticRuntime("git-tools", plan);
        console.log(JSON.stringify({{
          authority: readiness.semanticRuntimeAuthority,
          status: readiness.semanticRuntimeStatus,
          ready: readiness.semanticRuntimeReady,
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
    assert result["adapterKind"] == "executable-semantic-workbench"
    assert result["missing"] == []
    assert result["stateSnapshotAvailable"] is True
    assert result["intentListAvailable"] is True
    assert result["stateBranch"] == "main"
    assert result["firstIntent"] == "refreshStatus"
