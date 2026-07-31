from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from main_computer.mcel_node_runtime import resolve_node_executable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "main_computer" / "web" / "applications" / "scripts"
REGISTRY = SCRIPTS / "mcel-domain-adapter-registry.js"
ADAPTER = SCRIPTS / "calculator-semantic-adapter.js"
CALCULATOR = SCRIPTS / "calculator.js"
REQUIREMENTS = SCRIPTS / "mcel-requirements-registry.js"
SHELL = ROOT / "main_computer" / "web" / "applications.html"


def run_node_json(script: str) -> dict:
    node = resolve_node_executable()
    if not node:
        pytest.skip("node is unavailable; Calculator semantic-adapter tests cannot run")
    completed = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def adapter_prelude(body: str) -> str:
    return textwrap.dedent(
        f"""
        const registry = require({json.dumps(str(REGISTRY))});
        const adapter = require({json.dumps(str(ADAPTER))});
        {body}
        """
    )


def test_calculator_adapter_is_loaded_before_truth_consumers_and_live_actions_use_it() -> None:
    shell = SHELL.read_text(encoding="utf-8")
    registry_include = "<!-- @include applications/scripts/mcel-domain-adapter-registry.js -->"
    toolkit_include = "<!-- @include applications/scripts/mcel-semantic-adapter-toolkit.js -->"
    adapter_include = "<!-- @include applications/scripts/calculator-semantic-adapter.js -->"
    truth_include = "<!-- @include applications/scripts/mcel-app-truth-gate.js -->"

    for include in (registry_include, toolkit_include, adapter_include, truth_include):
        assert include in shell

    assert shell.index(registry_include) < shell.index(toolkit_include)
    assert shell.index(toolkit_include) < shell.index(adapter_include)
    assert shell.index(adapter_include) < shell.index(truth_include)

    source = CALCULATOR.read_text(encoding="utf-8")
    assert "window.CalculatorSemanticAdapter" in source
    assert "window.MainComputerCalculatorRuntime" in source
    assert "executeCalculatorSemanticIntent" in source
    for intent_id in (
        "switchMode",
        "enterToken",
        "clearExpression",
        "evaluateExpression",
        "drawGraph",
        "resetGraph",
        "askModelForExpression",
        "askModelForGraphExpression",
        "askModelForMathicsExpression",
        "evaluateMathics",
        "askResultQuestion",
    ):
        assert f'"{intent_id}"' in source


def test_calculator_adapter_proves_full_lane_explicit_scope() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            const readiness = registry.evaluateAdapterReadiness("calculator");
            const coverage = adapter.getIntentCoverage();
            const recovery = adapter.getRecoveryCoverage();
            process.stdout.write(JSON.stringify({
              readiness,
              coverage,
              recovery,
              registered: registry.listAdapters(),
              intents: adapter.listIntents()
            }));
            """
        )
    )

    readiness = result["readiness"]
    assert readiness["adapterId"] == "calculator-domain-adapter"
    assert readiness["adapterVersion"] == "calculator-semantic-adapter-v1"
    assert readiness["runtimeCoreReady"] is True
    assert readiness["fullApplicationSemanticReady"] is True
    assert readiness["semanticRuntimeReady"] is True
    assert readiness["semanticRuntimeScope"] == "calculator-compute-and-helper-lanes-v1"
    assert readiness["adapterExecutable"] is True
    assert readiness["stateMachineReady"] is True
    assert readiness["actionPlannerReady"] is True
    assert readiness["capabilityProviderReady"] is True
    assert readiness["recoveryReady"] is True
    assert readiness["recoveryCoverageReady"] is True
    assert readiness["intentCoverageAuditReady"] is True
    assert readiness["intentCoverageReady"] is True
    assert readiness["executableIntentCount"] == 11
    assert readiness["preflightOnlyIntentCount"] == 0
    assert readiness["declaredOnlyIntentCount"] == 0
    assert readiness["prohibitedIntentCount"] == 0
    assert readiness["totalIntentCount"] == 11
    assert readiness["missingSemantics"] == []
    assert readiness["missingApplicationSemantics"] == []

    assert result["registered"] == [
        {
            "id": "calculator-domain-adapter",
            "appId": "calculator",
            "version": "calculator-semantic-adapter-v1",
            "kind": "multi-lane-calculation-domain-adapter",
        }
    ]

    coverage = result["coverage"]
    assert coverage["fullApplicationSemanticReady"] is True
    assert coverage["verification"]["passed"] is True
    assert coverage["prohibitedIntentIds"] == []
    assert coverage["excludedPlannedIntentIds"] == []
    assert all(entry["status"] == "executable" for entry in coverage["entries"])
    assert all(entry["mutates"] is False for entry in coverage["entries"])
    assert {
        entry["lane"] for entry in coverage["entries"]
    } >= {
        "local-arithmetic",
        "local-graph",
        "model-arithmetic",
        "model-graph",
        "model-mathics",
        "mathics",
        "model-result-qa",
    }

    recovery = result["recovery"]
    assert recovery["coverageReady"] is True
    assert recovery["unverifiedFailureClasses"] == []
    assert set(recovery["requiredFailureClasses"]) == set(recovery["coveredFailureClasses"])


def test_calculator_adapter_executes_distinct_lanes_and_emits_non_mutating_receipts() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            const calls = [];
            adapter.resetState();
            adapter.setRuntimeBindings({
              switchMode(payload) {
                calls.push({method: "switchMode", payload});
                return {ok: true, mode: payload.mode};
              },
              enterToken(payload) {
                calls.push({method: "enterToken", payload});
                return {ok: true, expression: payload.token, result: "ready"};
              },
              clearExpression(payload) {
                calls.push({method: "clearExpression", payload});
                return {ok: true, expression: "0", result: "ready"};
              },
              evaluateExpression(payload) {
                calls.push({method: "evaluateExpression", payload});
                return {ok: true, expression: payload.expression, value: 4, result: "4"};
              },
              drawGraph(payload) {
                calls.push({method: "drawGraph", payload});
                return {
                  ok: true,
                  expression: payload.expression,
                  range: payload.range,
                  finiteCount: 640,
                  statusText: "graphed x^2"
                };
              },
              resetGraph(payload) {
                calls.push({method: "resetGraph", payload});
                return {
                  ok: true,
                  expression: "",
                  range: {xMin: -10, xMax: 10, yMin: -5, yMax: 5},
                  statusText: "ready"
                };
              },
              askModelForExpression(payload) {
                calls.push({method: "askModelForExpression", payload});
                return {ok: true, expression: "18/(10+0.08)", result: "1.7857142857142858"};
              },
              askModelForGraphExpression(payload) {
                calls.push({method: "askModelForGraphExpression", payload});
                return {ok: true, expression: "10+0.08*x", statusText: "model graph ready"};
              },
              askModelForMathicsExpression(payload) {
                calls.push({method: "askModelForMathicsExpression", payload});
                return {ok: true, expression: "Solve[18 == 10 + .08 x, x]"};
              },
              evaluateMathics(payload) {
                calls.push({method: "evaluateMathics", payload});
                return {ok: true, expression: payload.expression, output: "{{x -> 100}}", statusText: "ready"};
              },
              askResultQuestion(payload) {
                calls.push({method: "askResultQuestion", payload});
                return {ok: true, answer: "The break-even point is 100 uses.", statusText: "ready"};
              }
            });

            (async () => {
              const results = [];
              results.push(await adapter.executeIntent("switchMode", {mode: "graphing"}));
              results.push(await adapter.executeIntent("enterToken", {token: "2"}));
              results.push(await adapter.executeIntent("clearExpression", {}));
              results.push(await adapter.executeIntent("evaluateExpression", {expression: "2+2"}));
              results.push(await adapter.executeIntent("drawGraph", {
                expression: "x^2",
                range: {xMin: -10, xMax: 10, yMin: -5, yMax: 100}
              }));
              results.push(await adapter.executeIntent("resetGraph", {}));
              results.push(await adapter.executeIntent("askModelForExpression", {prompt: "break even arithmetic"}));
              results.push(await adapter.executeIntent("askModelForGraphExpression", {prompt: "graph monthly cost"}));
              results.push(await adapter.executeIntent("askModelForMathicsExpression", {prompt: "solve the break even equation"}));
              results.push(await adapter.executeIntent("evaluateMathics", {expression: "Solve[18 == 10 + .08 x, x]"}));
              results.push(await adapter.executeIntent("askResultQuestion", {question: "What is the break-even point?"}));
              process.stdout.write(JSON.stringify({
                calls,
                results,
                state: adapter.getState(),
                receipts: adapter.listReceipts(),
                evidence: adapter.mapEvidence(),
                objects: adapter.listObjects()
              }));
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : error);
              process.exit(1);
            });
            """
        )
    )

    assert len(result["calls"]) == 11
    assert all(execution["status"] == "pass" for execution in result["results"])
    assert [call["method"] for call in result["calls"]] == [
        "switchMode",
        "enterToken",
        "clearExpression",
        "evaluateExpression",
        "drawGraph",
        "resetGraph",
        "askModelForExpression",
        "askModelForGraphExpression",
        "askModelForMathicsExpression",
        "evaluateMathics",
        "askResultQuestion",
    ]

    state = result["state"]
    assert state["phase"] == "ready"
    assert state["mode"] == "graphing"
    assert state["arithmetic"]["result"]
    assert state["graph"]["expression"] == "10+0.08*x"
    assert state["mathics"]["output"] == "{{x -> 100}}"
    assert state["qa"]["answer"] == "The break-even point is 100 uses."

    receipts = result["receipts"]
    assert len(receipts) == 11
    assert all(receipt["mutationAllowed"] is False for receipt in receipts)
    assert all(receipt["mutationAttempted"] is False for receipt in receipts)
    assert all(receipt["hiddenMutationDetected"] is False for receipt in receipts)
    assert {receipt["lane"] for receipt in receipts} >= {
        "local-arithmetic",
        "local-graph",
        "model-arithmetic",
        "model-graph",
        "model-mathics",
        "mathics",
        "model-result-qa",
    }
    assert any(item["receiptBacked"] is True for item in result["evidence"])
    assert {item["id"] for item in result["objects"]} >= {
        "calculator-state",
        "arithmetic-expression",
        "graph-surface",
        "mathics-expression",
        "result-question",
    }


def test_calculator_adapter_blocks_invalid_or_mutating_requests_before_runtime() -> None:
    result = run_node_json(
        adapter_prelude(
            """
            let calls = 0;
            adapter.resetState();
            adapter.setRuntimeBindings({
              switchMode() { calls += 1; return {ok: true, mode: "basic"}; },
              enterToken() { calls += 1; return {ok: true}; },
              clearExpression() { calls += 1; return {ok: true}; },
              evaluateExpression() { calls += 1; return {ok: true}; },
              drawGraph() { calls += 1; return {ok: true}; },
              resetGraph() { calls += 1; return {ok: true}; },
              askModelForExpression() { calls += 1; return {ok: true}; },
              askModelForGraphExpression() { calls += 1; return {ok: true}; },
              askModelForMathicsExpression() { calls += 1; return {ok: true}; },
              evaluateMathics() { calls += 1; return {ok: true}; },
              askResultQuestion() { calls += 1; return {ok: true}; }
            });

            (async () => {
              const missingExpression = await adapter.executeIntent("evaluateExpression", {expression: ""});
              const invalidRange = await adapter.executeIntent("drawGraph", {
                expression: "x",
                range: {xMin: 10, xMax: -10, yMin: -5, yMax: 5}
              });
              const hiddenMutation = await adapter.executeIntent("evaluateExpression", {
                expression: "2+2",
                command: "git commit"
              });
              const unsupported = await adapter.executeIntent("writeFile", {filePath: "answer.txt"});
              process.stdout.write(JSON.stringify({
                calls,
                missingExpression,
                invalidRange,
                hiddenMutation,
                unsupported,
                receipts: adapter.listReceipts(),
                recoveryCoverage: adapter.getRecoveryCoverage()
              }));
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : error);
              process.exit(1);
            });
            """
        )
    )

    assert result["calls"] == 0
    assert result["missingExpression"]["status"] == "blocked"
    assert result["missingExpression"]["preflight"]["blockers"][0]["code"] == "expression-required"
    assert result["invalidRange"]["status"] == "blocked"
    assert "graph-range-invalid" in {
        item["code"] for item in result["invalidRange"]["preflight"]["blockers"]
    }
    assert result["hiddenMutation"]["status"] == "blocked"
    assert "hidden-mutation-prohibited" in {
        item["code"] for item in result["hiddenMutation"]["preflight"]["blockers"]
    }
    assert result["unsupported"]["status"] == "blocked"
    assert "unsupported-intent" in {
        item["code"] for item in result["unsupported"]["preflight"]["blockers"]
    }
    assert all(receipt["mutationAttempted"] is False for receipt in result["receipts"])
    assert result["recoveryCoverage"]["coverageReady"] is True


def test_truth_gate_withholds_calculator_semantic_runtime_without_binding_audit() -> None:
    surface = SCRIPTS / "mcel-app-surface-registry.js"
    truth_gate = SCRIPTS / "mcel-app-truth-gate.js"
    result = run_node_json(
        textwrap.dedent(
            f"""
            const fs = require("fs");
            const vm = require("vm");
            const requirementsRegistry = require({json.dumps(str(REQUIREMENTS))});
            const domainAdapterRegistry = require({json.dumps(str(REGISTRY))});
            require({json.dumps(str(ADAPTER))});
            const surfaceSandbox = {{console}};
            surfaceSandbox.window = surfaceSandbox;
            vm.runInNewContext(
              fs.readFileSync({json.dumps(str(surface))}, "utf8"),
              surfaceSandbox,
              {{filename: "mcel-app-surface-registry.js"}}
            );
            const appSurfaceRegistry = surfaceSandbox.McelAppSurfaceRegistry;
            const gate = require({json.dumps(str(truth_gate))});
            const runtimeEvidence = {{
              schema: "mcel-runtime-flog-report-v2",
              generatedAt: "2026-07-27T22:00:00Z",
              results: [{{
                app: "calculator",
                status: "pass",
                timestamp: "2026-07-27T22:00:00Z",
                appSurfacePolicyScope: {{
                  status: "pass",
                  failedLayerIds: [],
                  unavailableLayerIds: [],
                  requiredLayerStatuses: {{
                    "semantic-surface": "pass",
                    "layout-grammar": "pass",
                    "runtime-ownership": "pass",
                    "runtime-visual-fit": "pass",
                    "diagnostic-no-throw": "pass"
                  }}
                }},
                appSurfaceConformance: {{
                  appId: "calculator",
                  status: "pass",
                  policyFailedLayerIds: [],
                  policyUnavailableLayerIds: [],
                  layers: [
                    {{id: "semantic-surface", status: "pass"}},
                    {{id: "layout-grammar", status: "pass"}},
                    {{id: "runtime-ownership", status: "pass"}},
                    {{id: "runtime-visual-fit", status: "pass"}},
                    {{id: "diagnostic-no-throw", status: "pass"}}
                  ]
                }}
              }}]
            }};
            const acceptanceEvidence = {{
              calculator: {{
                appId: "calculator",
                status: "pass",
                testCount: 3,
                timestamp: "2026-07-27T22:00:00Z"
              }}
            }};
            const truth = gate.evaluateAppTruth("calculator", {{
              requirementsRegistry,
              domainAdapterRegistry,
              appSurfaceRegistry,
              runtimeEvidence,
              acceptanceEvidence,
              now: "2026-07-27T23:00:00Z"
            }});
            process.stdout.write(JSON.stringify(truth));
            """
        )
    )

    assert result["requirements"]["contractComplete"] is True
    assert result["requirements"]["intentCount"] == 11
    assert result["adapter"]["registered"] is True
    assert result["adapter"]["runtimeCoreReady"] is True
    assert result["adapter"]["fullApplicationSemanticReady"] is True
    assert result["adapter"]["totalIntentCount"] == 11
    assert result["evidence"]["runtime"]["policyPassed"] is True
    assert result["claims"]["runtimeSurfaceProven"] is True
    assert result["claims"]["acceptanceProven"] is True
    assert result["adapter"]["operationalSemanticRuntimeReady"] is False
    assert result["adapter"]["runtimeBindingReady"] is False
    assert result["claims"]["semanticRuntimeProven"] is False
    assert result["overallStatus"] == "runtime-proven"
    assert "runtime-binding-not-proven" in result["findingCodes"]
    assert "missing-domain-adapter" not in result["findingCodes"]
    assert "required-intent-not-executable" not in result["findingCodes"]
