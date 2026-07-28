from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
APP_SHELL = ROOT / "main_computer" / "web" / "applications.html"
TRUTH_GATE_JS = SCRIPTS / "mcel-app-truth-gate.js"
DOC = ROOT / "pretty_docs" / "mcel-app-truth-gate.md"


def run_node_json(script: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is unavailable; MCEL app truth-gate tests cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def load_truth_gate(body: str) -> str:
    return textwrap.dedent(
        f"""
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(TRUTH_GATE_JS))}, "utf8"),
          sandbox,
          {{filename: "mcel-app-truth-gate.js"}}
        );
        const gate = sandbox.McelAppTruthGate;
        {body}
        """
    )


def fake_registry_prelude(
    *,
    full_semantic_ready: bool = True,
    conformance_required: bool = True,
    contract_complete: bool = True,
) -> str:
    return textwrap.dedent(
        f"""
        const requirementsRegistry = {{
          REGISTRY_VERSION: "requirements-test-v1",
          strictSchemaReady: true,
          getSummary() {{
            return {{valid: true, error_count: 0, registry_version: "requirements-test-v1"}};
          }},
          getAppContract(appId) {{
            if (appId !== "demo") return null;
            return {{
              app: "demo",
              title: "Demo",
              contract_complete: {str(contract_complete).lower()},
              current_runtime_status: "partial",
              target_runtime_status: "full-application-semantic-runtime",
              intent_count: 2,
              mutation_intent_count: 1,
              prohibited_intent_count: 0,
              runtime_check_count: 3,
              block_type_counts: {{
                "mcel-acceptance": 2,
                "mcel-test-binding": 1
              }}
            }};
          }},
          listAppContracts() {{ return [this.getAppContract("demo")]; }}
        }};

        const domainAdapterRegistry = {{
          REGISTRY_VERSION: "adapter-test-v1",
          AUTHORITY: "mcel-domain-adapter-registry",
          evaluateAdapterReadiness(appId) {{
            return {{
              appId,
              registryAdapterPresent: true,
              adapterId: "demo-adapter",
              adapterKind: "application-domain-adapter",
              runtimeCoreReady: true,
              intentCoverageReady: true,
              intentCoverageAuditReady: true,
              fullApplicationSemanticReady: {str(full_semantic_ready).lower()},
              semanticRuntimeReady: {str(full_semantic_ready).lower()},
              semanticRuntimeScope: "full-application",
              executableIntentCount: 2,
              preflightOnlyIntentCount: 0,
              declaredOnlyIntentCount: 0,
              prohibitedIntentCount: 0,
              blockedIntentCount: 0,
              totalIntentCount: 2,
              recoveryReady: true,
              recoveryCoverageReady: true,
              missingSemantics: [],
              missingApplicationSemantics: []
            }};
          }},
          listAdapters() {{ return [{{appId: "demo", id: "demo-adapter"}}]; }}
        }};

        const appSurfaceRegistry = {{
          registryVersion: "surface-test-v1",
          getAppPolicy(appId) {{
            return {{
              appId,
              label: "Demo",
              state: {json.dumps("surface-aware" if conformance_required else "legacy")},
              conformanceRequired: {str(conformance_required).lower()},
              maturity: {json.dumps("semantic-runtime" if conformance_required else "legacy")},
              surfaceId: "demo.surface.primary",
              contractId: "demo.contract.default",
              requiredLayerIds: {json.dumps(["runtime-ownership", "diagnostic-no-throw"] if conformance_required else [])}
            }};
          }},
          listPolicies() {{ return [this.getAppPolicy("demo")]; }}
        }};

        const runtimeEvidence = {{
          schema: "mcel-runtime-flog-report-v2",
          version: "mcel-runtime-flog-v2",
          generatedAt: "2026-07-27T10:00:00Z",
          results: [{{
            app: "demo",
            status: "pass",
            appSurfacePolicyScope: {{
              status: "pass",
              failedLayerIds: [],
              unavailableLayerIds: [],
              requiredLayerStatuses: {{
                "runtime-ownership": "pass",
                "diagnostic-no-throw": "pass"
              }}
            }},
            appSurfaceConformance: {{
              appId: "demo",
              status: "pass",
              policyFailedLayerIds: [],
              policyUnavailableLayerIds: [],
              layers: [
                {{id: "runtime-ownership", status: "pass"}},
                {{id: "diagnostic-no-throw", status: "pass"}}
              ]
            }}
          }}]
        }};

        const acceptanceEvidence = {{
          demo: {{
            appId: "demo",
            status: "pass",
            testCount: 2,
            timestamp: "2026-07-27T10:00:00Z"
          }}
        }};
        """
    )


def test_truth_gate_is_documented_and_loaded_after_its_authorities() -> None:
    assert TRUTH_GATE_JS.exists()
    assert DOC.exists()

    shell = APP_SHELL.read_text(encoding="utf-8")
    assert "mcel-app-truth-gate.js" in shell
    assert shell.index("mcel-domain-adapter-registry.js") < shell.index("mcel-app-truth-gate.js")
    assert shell.index("mcel-app-surface-registry.js") < shell.index("mcel-app-truth-gate.js")
    assert shell.index("mcel-requirements-registry.js") < shell.index("mcel-app-truth-gate.js")
    assert shell.index("mcel-app-truth-gate.js") < shell.index("mcel-self-diagnosis.js")

    source = TRUTH_GATE_JS.read_text(encoding="utf-8")
    assert "mcel.app-truth-gate.v1" in source
    assert "mcel-app-truth-snapshot-v1" in source
    assert "evaluateAppTruth" in source
    assert "buildTruthSnapshot" in source


def test_complete_authorities_and_fresh_evidence_prove_semantic_runtime() -> None:
    script = load_truth_gate(
        fake_registry_prelude()
        + """
        const truth = gate.evaluateAppTruth("demo", {
          requirementsRegistry,
          domainAdapterRegistry,
          appSurfaceRegistry,
          runtimeEvidence,
          acceptanceEvidence,
          now: "2026-07-27T12:00:00Z"
        });
        process.stdout.write(JSON.stringify(truth));
        """
    )
    truth = run_node_json(script)

    assert truth["schema"] == "mcel-app-truth-snapshot-v1"
    assert truth["overallStatus"] == "semantic-runtime-proven"
    assert truth["requirements"]["contractComplete"] is True
    assert truth["adapter"]["fullApplicationSemanticReady"] is True
    assert truth["evidence"]["runtime"]["fresh"] is True
    assert truth["evidence"]["runtime"]["policyPassed"] is True
    assert truth["claims"]["runtimeSurfaceProven"] is True
    assert truth["claims"]["acceptanceProven"] is True
    assert truth["claims"]["semanticRuntimeProven"] is True
    assert truth["findings"] == []


def test_complete_current_scope_ignores_prohibited_and_explicitly_excluded_planned_intents() -> None:
    prelude = fake_registry_prelude()
    prelude += """
    const baseEvaluateAdapterReadiness = domainAdapterRegistry.evaluateAdapterReadiness;
    domainAdapterRegistry.evaluateAdapterReadiness = function(appId) {
      return {
        ...baseEvaluateAdapterReadiness(appId),
        executableIntentCount: 2,
        prohibitedIntentCount: 3,
        blockedIntentCount: 3,
        totalIntentCount: 5,
        intentCoverage: {
          excludedPlannedIntentIds: ["openInOwningApp"]
        },
        intentCoverageValidation: {
          incompleteIntentIds: []
        }
      };
    };
    """
    script = load_truth_gate(
        prelude
        + """
        const truth = gate.evaluateAppTruth("demo", {
          requirementsRegistry,
          domainAdapterRegistry,
          appSurfaceRegistry,
          runtimeEvidence,
          acceptanceEvidence,
          now: "2026-07-27T12:00:00Z"
        });
        process.stdout.write(JSON.stringify(truth));
        """
    )
    truth = run_node_json(script)

    assert truth["overallStatus"] == "semantic-runtime-proven"
    assert truth["claims"]["semanticRuntimeProven"] is True
    assert truth["adapter"]["excludedPlannedIntentIds"] == ["openInOwningApp"]
    assert truth["adapter"]["prohibitedIntentCount"] == 3
    assert "required-intent-not-executable" not in truth["findingCodes"]


def test_incomplete_current_scope_keeps_required_intent_finding() -> None:
    prelude = fake_registry_prelude(full_semantic_ready=False)
    prelude += """
    const baseEvaluateAdapterReadiness = domainAdapterRegistry.evaluateAdapterReadiness;
    domainAdapterRegistry.evaluateAdapterReadiness = function(appId) {
      return {
        ...baseEvaluateAdapterReadiness(appId),
        intentCoverageReady: false,
        fullApplicationSemanticReady: false,
        semanticRuntimeReady: false,
        executableIntentCount: 1,
        declaredOnlyIntentCount: 1,
        blockedIntentCount: 1,
        totalIntentCount: 2,
        semanticRuntimeScope: "partial-demo-v1",
        intentCoverageValidation: {
          incompleteIntentIds: ["saveDemo"]
        },
        missingApplicationSemantics: ["saveDemo"]
      };
    };
    """
    script = load_truth_gate(
        prelude
        + """
        const truth = gate.evaluateAppTruth("demo", {
          requirementsRegistry,
          domainAdapterRegistry,
          appSurfaceRegistry,
          runtimeEvidence,
          acceptanceEvidence,
          now: "2026-07-27T12:00:00Z"
        });
        process.stdout.write(JSON.stringify(truth));
        """
    )
    truth = run_node_json(script)

    assert truth["overallStatus"] == "runtime-proven"
    assert truth["claims"]["semanticRuntimeProven"] is False
    assert "required-intent-not-executable" in truth["findingCodes"]
    finding = next(item for item in truth["findings"] if item["code"] == "required-intent-not-executable")
    assert finding["detail"]["currentScopeIntentCount"] == 2
    assert finding["detail"]["incompleteIntentIds"] == ["saveDemo"]
    assert finding["detail"]["semanticRuntimeScope"] == "partial-demo-v1"


def test_passing_surface_evidence_does_not_overclaim_missing_domain_semantics() -> None:
    prelude = fake_registry_prelude()
    prelude += """
    domainAdapterRegistry.evaluateAdapterReadiness = function(appId) {
      return {
        appId,
        registryAdapterPresent: false,
        adapterKind: "missing-domain-adapter",
        runtimeCoreReady: false,
        intentCoverageReady: false,
        fullApplicationSemanticReady: false,
        semanticRuntimeReady: false,
        executableIntentCount: 0,
        missingSemantics: ["adapterExecutable"]
      };
    };
    domainAdapterRegistry.listAdapters = function() { return []; };
    """
    script = load_truth_gate(
        prelude
        + """
        const truth = gate.evaluateAppTruth("demo", {
          requirementsRegistry,
          domainAdapterRegistry,
          appSurfaceRegistry,
          runtimeEvidence,
          acceptanceEvidence,
          now: "2026-07-27T12:00:00Z"
        });
        process.stdout.write(JSON.stringify(truth));
        """
    )
    truth = run_node_json(script)

    assert truth["overallStatus"] == "runtime-proven"
    assert truth["claims"]["runtimeSurfaceProven"] is True
    assert truth["claims"]["semanticRuntimeProven"] is False
    assert "missing-domain-adapter" in truth["findingCodes"]
    assert "semantic-readiness-overclaimed" not in truth["findingCodes"]


def test_explicit_semantic_overclaim_is_blocked() -> None:
    prelude = fake_registry_prelude(full_semantic_ready=False)
    prelude += """
    runtimeEvidence.results[0].claimedSemanticRuntimeReady = true;
    """
    script = load_truth_gate(
        prelude
        + """
        const truth = gate.evaluateAppTruth("demo", {
          requirementsRegistry,
          domainAdapterRegistry,
          appSurfaceRegistry,
          runtimeEvidence,
          acceptanceEvidence,
          now: "2026-07-27T12:00:00Z"
        });
        process.stdout.write(JSON.stringify(truth));
        """
    )
    truth = run_node_json(script)

    assert truth["overallStatus"] == "blocked"
    assert truth["claims"]["semanticRuntimeProven"] is False
    assert "semantic-readiness-overclaimed" in truth["findingCodes"]


def test_flog_report_generated_at_is_inherited_and_stale_evidence_is_not_proof() -> None:
    script = load_truth_gate(
        fake_registry_prelude()
        + """
        const truth = gate.evaluateAppTruth("demo", {
          requirementsRegistry,
          domainAdapterRegistry,
          appSurfaceRegistry,
          runtimeEvidence,
          acceptanceEvidence,
          now: "2026-08-10T10:00:00Z",
          maxEvidenceAgeMs: 7 * 24 * 60 * 60 * 1000
        });
        process.stdout.write(JSON.stringify(truth));
        """
    )
    truth = run_node_json(script)

    runtime = truth["evidence"]["runtime"]
    assert runtime["timestamp"] == "2026-07-27T10:00:00Z"
    assert runtime["freshness"] == "stale"
    assert runtime["fresh"] is False
    assert truth["claims"]["runtimeSurfaceProven"] is False
    assert truth["overallStatus"] == "verification-incomplete"
    assert "runtime-evidence-stale" in truth["findingCodes"]


def test_failed_required_surface_policy_blocks_truth() -> None:
    prelude = fake_registry_prelude()
    prelude += """
    runtimeEvidence.results[0].status = "fail";
    runtimeEvidence.results[0].appSurfacePolicyScope = {
      status: "fail",
      failedLayerIds: ["runtime-ownership"],
      unavailableLayerIds: [],
      requiredLayerStatuses: {
        "runtime-ownership": "fail",
        "diagnostic-no-throw": "pass"
      }
    };
    """
    script = load_truth_gate(
        prelude
        + """
        const truth = gate.evaluateAppTruth("demo", {
          requirementsRegistry,
          domainAdapterRegistry,
          appSurfaceRegistry,
          runtimeEvidence,
          acceptanceEvidence,
          now: "2026-07-27T12:00:00Z"
        });
        process.stdout.write(JSON.stringify(truth));
        """
    )
    truth = run_node_json(script)

    assert truth["overallStatus"] == "blocked"
    assert truth["claims"]["runtimeSurfaceProven"] is False
    assert "surface-policy-failed" in truth["findingCodes"]
    assert truth["evidence"]["runtime"]["failedLayerIds"] == ["runtime-ownership"]


def test_declared_acceptance_without_evidence_remains_verification_incomplete() -> None:
    script = load_truth_gate(
        fake_registry_prelude()
        + """
        const truth = gate.evaluateAppTruth("demo", {
          requirementsRegistry,
          domainAdapterRegistry,
          appSurfaceRegistry,
          runtimeEvidence,
          now: "2026-07-27T12:00:00Z"
        });
        process.stdout.write(JSON.stringify(truth));
        """
    )
    truth = run_node_json(script)

    assert truth["claims"]["runtimeSurfaceProven"] is True
    assert truth["claims"]["acceptanceProven"] is False
    assert truth["claims"]["verificationComplete"] is False
    assert truth["claims"]["semanticRuntimeProven"] is False
    assert truth["overallStatus"] == "verification-incomplete"
    assert "acceptance-test-missing" in truth["findingCodes"]


def test_snapshot_joins_union_of_known_apps_and_reports_stable_counts() -> None:
    script = load_truth_gate(
        fake_registry_prelude()
        + """
        const originalEvaluateAdapterReadiness = domainAdapterRegistry.evaluateAdapterReadiness;
        domainAdapterRegistry.evaluateAdapterReadiness = function(appId) {
          if (appId === "demo") return originalEvaluateAdapterReadiness(appId);
          return {
            appId,
            registryAdapterPresent: false,
            adapterKind: "missing-domain-adapter",
            runtimeCoreReady: false,
            intentCoverageReady: false,
            fullApplicationSemanticReady: false,
            semanticRuntimeReady: false,
            executableIntentCount: 0,
            missingSemantics: ["adapterExecutable"]
          };
        };
        const legacySurfaceRegistry = {
          registryVersion: "surface-test-v1",
          getAppPolicy(appId) {
            if (appId === "demo") return appSurfaceRegistry.getAppPolicy(appId);
            return {
              appId,
              label: "Legacy",
              state: "legacy",
              conformanceRequired: false,
              maturity: "legacy",
              requiredLayerIds: []
            };
          },
          listPolicies() {
            return [
              appSurfaceRegistry.getAppPolicy("demo"),
              this.getAppPolicy("legacy-app")
            ];
          }
        };
        const snapshot = gate.buildTruthSnapshot({
          appIds: ["explicit-app"],
          requirementsRegistry,
          domainAdapterRegistry,
          appSurfaceRegistry: legacySurfaceRegistry,
          runtimeEvidence,
          acceptanceEvidence,
          now: "2026-07-27T12:00:00Z"
        });
        process.stdout.write(JSON.stringify(snapshot));
        """
    )
    snapshot = run_node_json(script)

    assert snapshot["appIds"] == ["demo", "explicit-app", "legacy-app"]
    assert snapshot["appCount"] == 3
    assert snapshot["statusCounts"]["semantic-runtime-proven"] == 1
    assert sum(snapshot["statusCounts"].values()) == 3
    assert snapshot["findingCounts"]["requirements-contract-missing"] == 2
    by_app = {entry["appId"]: entry for entry in snapshot["apps"]}
    assert set(by_app["legacy-app"]["findingCodes"]) == {
        "requirements-contract-missing",
        "missing-domain-adapter",
        "app-not-enrolled",
    }


def test_actual_registries_produce_component_truth_without_runtime_evidence() -> None:
    script = textwrap.dedent(
        f"""
        const requirementsRegistry = require({json.dumps(str(SCRIPTS / "mcel-requirements-registry.js"))});
        const domainAdapterRegistry = require({json.dumps(str(SCRIPTS / "mcel-domain-adapter-registry.js"))});
        const fs = require("fs");
        const vm = require("vm");
        const sandbox = {{console}};
        sandbox.window = sandbox;
        vm.runInNewContext(
          fs.readFileSync({json.dumps(str(SCRIPTS / "mcel-app-surface-registry.js"))}, "utf8"),
          sandbox
        );
        const gate = require({json.dumps(str(TRUTH_GATE_JS))});
        const truth = gate.evaluateAppTruth("calculator", {{
          requirementsRegistry,
          domainAdapterRegistry,
          appSurfaceRegistry: sandbox.McelAppSurfaceRegistry,
          now: "2026-07-27T12:00:00Z"
        }});
        process.stdout.write(JSON.stringify(truth));
        """
    )
    truth = run_node_json(script)

    assert truth["requirements"]["present"] is True
    assert truth["requirements"]["contractComplete"] is True
    assert truth["adapter"]["registered"] is False
    assert truth["surface"]["conformanceRequired"] is True
    assert truth["claims"]["semanticRuntimeProven"] is False
    assert truth["overallStatus"] == "verification-incomplete"
    for code in [
        "missing-domain-adapter",
        "runtime-evidence-missing",
        "acceptance-test-missing",
    ]:
        assert code in truth["findingCodes"]
