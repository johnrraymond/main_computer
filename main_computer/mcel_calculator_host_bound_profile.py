"""Calculator host-bound MCEL profile.

This module centralizes the Calculator-specific facts consumed by the generic
host-bound MCEL projection, observation, parity, evidence, and promotion tools.
The wrappers keep their historical entry-point names, while this profile is the
single source for Calculator routes, files, intent payloads, report labels, and
compatibility diagnostics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from main_computer.mcel_dsl_compiler import DEFAULT_CANDIDATE_ROOT
from main_computer.mcel_host_bound_browser_observation import (
    HostBoundBrowserObservationProfile,
    HostBoundModuleExport,
)
from main_computer.mcel_host_bound_candidate_evidence import HostBoundCandidateEvidenceProfile
from main_computer.mcel_host_bound_candidate_projection import HostBoundProjectionProfile
from main_computer.mcel_host_bound_ir_native_proof import HostBoundIrNativeProofProfile
from main_computer.mcel_host_bound_promotion_rehearsal import HostBoundPromotionProfile
from main_computer.mcel_host_bound_runtime_parity import (
    HostBoundRetiredArtifact,
    HostBoundRuntimeParityProfile,
)
from main_computer.mcel_projection_profiles.calculator_shadow_v1 import (
    EXPECTED_INTENTS,
    PROFILE_ID,
    project_calculator_ir,
)


APP_ID = "calculator"
DEFAULT_DSL_SOURCE = Path("mcel_apps/calculator/application.js")
DEFAULT_PACKAGE_ROOT = Path("mcel_apps/calculator")
DEFAULT_CANDIDATE_EVIDENCE_REPORT_ROOT = Path("runtime/reports/mcel-compiler-candidates")
DEFAULT_PROMOTION_REPORT_ROOT = Path("runtime/reports/mcel-application-promotions/calculator/rehearsals")

ROUTE = "/applications/calculator"
ROOT_SELECTOR = "#calculator-app"
RUNTIME_FACADE = "MainComputerCalculatorRuntime"
PROJECTION_PROFILE = PROFILE_ID
PROMOTED_TRUTH_STATUS = "semantic-runtime-proven"

HOST_HTML_PATH = Path("main_computer/web/applications/apps/calculator.html")
CSS_PATH = Path("main_computer/web/applications/styles/calculator.css")
SCRIPT_PATHS = (
    Path("main_computer/web/applications/scripts/dom-bindings/calculator.js"),
    Path("main_computer/web/applications/scripts/mcel-semantic-adapter-toolkit.js"),
    Path("main_computer/web/applications/scripts/calculator-core.js"),
    Path("main_computer/web/applications/scripts/calculator-view-model.js"),
    Path("main_computer/web/applications/scripts/calculator-capabilities.js"),
    Path("main_computer/web/applications/scripts/calculator.js"),
)
MODULE_EXPORTS = (
    HostBoundModuleExport("domain", "applications/mcel-packages/calculator/contracts/domain.js", "CalculatorDomain"),
    HostBoundModuleExport("intents", "applications/mcel-packages/calculator/contracts/intents.js", "CalculatorIntents"),
    HostBoundModuleExport("surface", "applications/mcel-packages/calculator/contracts/surface.js", "CalculatorSurface"),
    HostBoundModuleExport("layout", "applications/mcel-packages/calculator/contracts/layout.js", "CalculatorLayout"),
    HostBoundModuleExport("observation", "applications/mcel-packages/calculator/contracts/observation.js", "CalculatorObservation"),
    HostBoundModuleExport("acceptance", "applications/mcel-packages/calculator/contracts/acceptance.js", "CalculatorAcceptance"),
)

INTENT_PAYLOADS: Mapping[str, Mapping[str, Any]] = {
    "switchMode": {"mode": "graphing"},
    "enterToken": {"token": "7"},
    "clearExpression": {},
    "evaluateExpression": {"expression": "2+3*4"},
    "drawGraph": {"expression": "x^2", "range": {"xMin": -2, "xMax": 2, "yMin": -1, "yMax": 5}},
    "resetGraph": {},
    "askModelForExpression": {"prompt": "two plus three"},
    "askModelForGraphExpression": {"prompt": "graph x squared"},
    "askModelForMathicsExpression": {"prompt": "integrate x"},
    "evaluateMathics": {"expression": "Integrate[x, x]"},
    "askResultQuestion": {"question": "What is the current result?"},
}
LOCAL_INTENTS = frozenset({"switchMode", "enterToken", "clearExpression", "evaluateExpression", "drawGraph", "resetGraph"})
CAPABILITY_INTENTS = frozenset(set(INTENT_PAYLOADS) - LOCAL_INTENTS)
LOCAL_LANES = frozenset({"local-ui", "local-arithmetic", "local-graph"})

RETIRED_ARTIFACTS = (
    HostBoundRetiredArtifact(
        "legacyAdapterRetired",
        Path("main_computer/web/applications/scripts/calculator-semantic-adapter.js"),
    ),
    HostBoundRetiredArtifact(
        "legacySurfaceRetired",
        Path("main_computer/web/applications/scripts/mcel-calculator-surface.js"),
    ),
)

PROMOTION_BOUNDARY = (
    "mcel_apps/calculator/mcel.app.json",
)

BROWSER_OBSERVATION_SCRIPT = r"""async ({operationPrefix}) => {
  const clone = (value) => value === undefined ? null : JSON.parse(JSON.stringify(value));
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const intentPayloads = {
    switchMode: {mode: "graphing"},
    enterToken: {token: "7"},
    clearExpression: {},
    evaluateExpression: {expression: "2+3*4"},
    drawGraph: {expression: "x^2", range: {xMin: -2, xMax: 2, yMin: -1, yMax: 5}},
    resetGraph: {},
    askModelForExpression: {prompt: "two plus three"},
    askModelForGraphExpression: {prompt: "graph x squared"},
    askModelForMathicsExpression: {prompt: "integrate x"},
    evaluateMathics: {expression: "Integrate[x, x]"},
    askResultQuestion: {question: "What is the current result?"}
  };
  const localIntentNames = new Set(["switchMode", "enterToken", "clearExpression", "evaluateExpression", "drawGraph", "resetGraph"]);
  const capabilityIntentNames = new Set(Object.keys(intentPayloads).filter((name) => !localIntentNames.has(name)));
  const requests = [];
  window.fetch = async (url, options = {}) => {
    const request = {
      url: String(url),
      method: String(options.method || "GET").toUpperCase(),
      body: String(options.body || "")
    };
    requests.push(request);
    let payload = {ok: true};
    const target = String(url);
    if (target === "/api/chat") {
      const body = (() => { try { return JSON.parse(request.body || "{}"); } catch (_) { return {}; } })();
      const prompt = String(body.prompt || "").toLowerCase();
      payload = {ok: true, content: prompt.includes("graph") || prompt.includes("f(x)") ? "x^2" : "2+3"};
    } else if (target === "/api/applications/calculator/mathics/ask") {
      payload = {ok: true, expression: "Integrate[x, x]"};
    } else if (target === "/api/applications/calculator/mathics/evaluate") {
      payload = {ok: true, result_text: "x^2 / 2"};
    } else if (target === "/api/applications/calculator/qa") {
      payload = {ok: true, answer: "The current result is 14."};
    }
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: {"Content-Type": "application/json"}
    });
  };
  const mount = window.McelHostBoundApplicationRuntime.getMount("calculator");
  const runtime = window.MainComputerCalculatorRuntime;
  const catalogRecord = window.McelApplicationPackages.getPackage("calculator");
  const generatedBindings = mount.modules.adapter.bindings;
  const root = document.querySelector("#calculator-app");

  function domSnapshot() {
    const text = (selector) => document.querySelector(selector)?.textContent?.trim() || "";
    const value = (selector) => document.querySelector(selector)?.value || "";
    const mode = root?.classList?.contains("graphing-active") ? "graphing" : "basic";
    return {
      mode,
      expression: value("#calculator-display"),
      result: text("#calculator-result"),
      graphExpression: value("#calculator-graph-expression"),
      graphStatus: text("#calculator-graph-status"),
      mathicsExpression: value("#calculator-mathics-expression"),
      mathicsOutput: text("#calculator-mathics-output"),
      mathicsStatus: text("#calculator-mathics-evaluation-status"),
      qaAnswer: text("#calculator-qa-answer"),
      qaStatus: text("#calculator-qa-status"),
      hostBoundStatus: root?.dataset?.mcelHostBoundStatus || "",
      hostBoundApp: root?.dataset?.mcelHostBoundApp || ""
    };
  }

  async function settle() {
    await sleep(80);
  }

  async function resetFor(intentName) {
    try { runtime.clearExpression(); } catch (_) {}
    try { runtime.resetGraph(); } catch (_) {}
    try { runtime.switchMode({mode: "basic"}); } catch (_) {}
    if (intentName === "evaluateMathics") {
      const input = document.querySelector("#calculator-mathics-expression");
      if (input) input.value = "Integrate[x, x]";
    }
    await settle();
  }

  function focusedComparable(intentName, snapshot) {
    if (intentName === "switchMode") return {mode: snapshot.mode, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    if (intentName === "enterToken" || intentName === "clearExpression" || intentName === "evaluateExpression" || intentName === "askModelForExpression") {
      return {expression: snapshot.expression, result: snapshot.result, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    }
    if (intentName === "drawGraph" || intentName === "resetGraph" || intentName === "askModelForGraphExpression") {
      return {graphExpression: snapshot.graphExpression, graphStatus: snapshot.graphStatus, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    }
    if (intentName === "askModelForMathicsExpression") {
      return {mathicsExpression: snapshot.mathicsExpression, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    }
    if (intentName === "evaluateMathics") {
      return {mathicsExpression: snapshot.mathicsExpression, mathicsOutput: snapshot.mathicsOutput, mathicsStatus: snapshot.mathicsStatus, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    }
    if (intentName === "askResultQuestion") {
      return {qaAnswer: snapshot.qaAnswer, qaStatus: snapshot.qaStatus, hostBoundStatus: snapshot.hostBoundStatus, hostBoundApp: snapshot.hostBoundApp};
    }
    return snapshot;
  }

  const intentResults = [];
  const requestCounts = {};
  const startRequests = () => requests.length;
  const finishRequests = (index) => requests.slice(index);

  for (const [intentName, payload] of Object.entries(intentPayloads)) {
    await resetFor(intentName);
    const generatedStart = startRequests();
    let generatedResult = null;
    let generatedError = null;
    try {
      generatedResult = await mount.invoke(intentName, clone(payload));
    } catch (error) {
      generatedError = {message: error?.message || String(error), name: error?.name || "Error"};
    }
    await settle();
    const generatedSnapshot = domSnapshot();
    const generatedRequests = finishRequests(generatedStart);

    const generatedComparable = focusedComparable(intentName, generatedSnapshot);
    const binding = generatedBindings[intentName] || {};
    const networkRequestCount = generatedRequests.length;
    requestCounts[intentName] = networkRequestCount;
    const parityChecks = {
      noGeneratedError: generatedError === null,
      runtimeMethod: typeof runtime?.[binding.runtimeMethod] === "function",
      declaredBinding: binding.runtimeMethod === intentName,
      hostBoundMounted: generatedSnapshot.hostBoundStatus === "mounted" && generatedSnapshot.hostBoundApp === "calculator",
      localProviderFree: localIntentNames.has(intentName) ? networkRequestCount === 0 : true,
      capabilityObserved: capabilityIntentNames.has(intentName) ? networkRequestCount > 0 : true
    };
    const passed = Object.values(parityChecks).every(Boolean);
    intentResults.push({
      intentName,
      status: passed ? "pass" : "fail",
      payload: clone(payload),
      lane: binding.executionBinding || "",
      runtimeMethod: binding.runtimeMethod || "",
      generated: {
        ok: !generatedError,
        result: clone(generatedResult),
        error: generatedError,
        snapshot: generatedSnapshot,
        comparable: generatedComparable
      },
      parity: {
        status: passed ? "pass" : "fail",
        checks: parityChecks
      },
      network: {
        requestCount: networkRequestCount,
        generatedRequestCount: generatedRequests.length,
        urls: generatedRequests.map((entry) => entry.url)
      }
    });
  }

  const localProviderFree = intentResults
    .filter((entry) => localIntentNames.has(entry.intentName))
    .every((entry) => entry.network.requestCount === 0);
  const capabilityObserved = intentResults
    .filter((entry) => capabilityIntentNames.has(entry.intentName))
    .every((entry) => entry.network.requestCount > 0);
  const allPassed = intentResults.every((entry) => entry.status === "pass");
  return {
    schema: "mcel.calculator-browser-observation.browser-result.v1",
    freshChromiumObservation: true,
    generatedAdapterMounted: !!mount?.active,
    legacyAdapterMounted: false,
    runtimeFacadeAvailable: !!runtime,
    semanticFingerprint: catalogRecord?.semanticFingerprint || "",
    sourceBindingFingerprint: catalogRecord?.sourceBindingFingerprint || "",
    intentResults,
    checks: {
      localProviderFree,
      capabilityObserved,
      allPassed
    },
    network: {
      requestCountByIntent: requestCounts,
      totalRequestCount: requests.length,
      capabilityRequestCount: Object.entries(requestCounts).filter(([name]) => capabilityIntentNames.has(name)).reduce((total, [, count]) => total + count, 0)
    }
  };
}"""


def build_calculator_projection_profile() -> HostBoundProjectionProfile:
    return HostBoundProjectionProfile(
        app_id=APP_ID,
        projection_profile=PROJECTION_PROFILE,
        project_ir=project_calculator_ir,
        default_dsl_source=DEFAULT_DSL_SOURCE,
        default_package_root=DEFAULT_PACKAGE_ROOT,
        report_schema="mcel.calculator-authoritative-projection-report.v1",
        version="mcel-calculator-host-bound-projection-v1",
        unsupported_ir_code="MCEL_CALCULATOR_SHADOW_PROJECTION_UNSUPPORTED_IR",
        drift_code="MCEL_CALCULATOR_SHADOW_PROJECTION_DRIFT",
        drift_summary="Existing Calculator candidate files differ from deterministic projection.",
        legacy_live_app_changed_key="liveCalculatorChanged",
    )


def build_calculator_browser_observation_profile() -> HostBoundBrowserObservationProfile:
    return HostBoundBrowserObservationProfile(
        app_id=APP_ID,
        report_schema="mcel.calculator-browser-parity-observation.v1",
        report_version="mcel-calculator-browser-parity-observation-v1",
        page_title="Calculator MCEL browser parity",
        route=ROUTE,
        root_selector=ROOT_SELECTOR,
        runtime_facade=RUNTIME_FACADE,
        host_html_path=HOST_HTML_PATH,
        css_path=CSS_PATH,
        script_paths=SCRIPT_PATHS,
        module_exports=MODULE_EXPORTS,
        adapter_route="applications/mcel-packages/calculator/contracts/adapter.js",
        adapter_export="CalculatorAdapter",
        adapter_id="calculator.dsl-authoritative-adapter.v1",
        intent_payloads=INTENT_PAYLOADS,
        local_intents=LOCAL_INTENTS,
        browser_observation_script=BROWSER_OBSERVATION_SCRIPT,
        unavailable_code="MCEL_CALCULATOR_BROWSER_OBSERVATION_UNAVAILABLE",
        check_failed_code="MCEL_CALCULATOR_BROWSER_CHECK_FAILED",
        effect_accounting_schema="mcel.calculator-browser-effect-accounting.v1",
        source_tree_fingerprint_salt="calculator-browser-observation-source-v1",
    )


def build_calculator_runtime_parity_profile(
    browser_observation_runner: Callable[..., Any] | None,
) -> HostBoundRuntimeParityProfile:
    return HostBoundRuntimeParityProfile(
        app_id=APP_ID,
        default_dsl_source=DEFAULT_DSL_SOURCE,
        project_ir=project_calculator_ir,
        expected_intents=EXPECTED_INTENTS,
        local_lanes=LOCAL_LANES,
        route=ROUTE,
        root_selector=ROOT_SELECTOR,
        runtime_facade=RUNTIME_FACADE,
        report_schema="mcel.calculator-generated-adapter-authority.v1",
        report_version="mcel-calculator-generated-adapter-authority-v1",
        browser_probe_schema="mcel.calculator-browser-authority-probe.v1",
        manifest_path=DEFAULT_PACKAGE_ROOT / "mcel.app.json",
        retired_artifacts=RETIRED_ARTIFACTS,
        capability_accounting_schema="mcel.calculator-capability-accounting.v1",
        dsl_invalid_code="MCEL_CALCULATOR_AUTHORITY_DSL_INVALID",
        check_failed_code="MCEL_CALCULATOR_AUTHORITY_CHECK_FAILED",
        authority_live_changed_key="liveCalculatorChanged",
        legacy_semantic_adapter_live_key="legacySemanticAdapterRemainsLive",
        legacy_semantic_adapter_retired_key="legacySemanticAdapterRetired",
        published_as_second_app=False,
        browser_observation_runner=browser_observation_runner,
    )


def build_calculator_ir_native_proof_profile(
    run_browser_parity_probe: Callable[[Path, bool, str], Mapping[str, Any]],
) -> HostBoundIrNativeProofProfile:
    return HostBoundIrNativeProofProfile(
        app_id=APP_ID,
        run_browser_parity_probe=run_browser_parity_probe,
        report_schema="mcel.calculator-ir-native-authoritative-proof.v1",
        report_version="mcel-calculator-ir-native-authoritative-proof-v1",
        scenario_prefix="calculator.authoritative",
        parity_failure_message="Calculator generated-adapter authority evidence did not pass.",
        convergence_failure_message="Calculator IR proof did not converge",
    )


def build_calculator_candidate_evidence_profile(
    *,
    project_candidate: Callable[..., Any],
    run_generated_adapter_parity: Callable[..., Any],
    run_ir_native_proof: Callable[..., Mapping[str, Any]],
    run_browser_parity_probe: Callable[..., Mapping[str, Any]],
) -> HostBoundCandidateEvidenceProfile:
    return HostBoundCandidateEvidenceProfile(
        app_id=APP_ID,
        default_dsl_source=DEFAULT_DSL_SOURCE,
        default_package_root=DEFAULT_PACKAGE_ROOT,
        default_candidate_root=DEFAULT_CANDIDATE_ROOT,
        project_candidate=project_candidate,
        run_generated_adapter_parity=run_generated_adapter_parity,
        run_ir_native_proof=run_ir_native_proof,
        run_browser_parity_probe=run_browser_parity_probe,
        report_schema="mcel.calculator-candidate-evidence-report.v1",
        report_version="mcel-calculator-authority-evidence-v1",
        report_filename="mcel-calculator-candidate-evidence-report.json",
        report_markdown_filename="mcel-calculator-candidate-evidence-report.md",
        report_title="Calculator Candidate Evidence",
        live_authority="existing-html-calculator-runtime",
        live_authority_changed_key="liveCalculatorChanged",
        legacy_semantic_adapter_authority_key="legacySemanticAdapterRemainsLive",
        stage_failed_code="MCEL_CALCULATOR_CANDIDATE_STAGE_FAILED",
        dsl_invalid_code="MCEL_CALCULATOR_CANDIDATE_DSL_INVALID",
        projection_invalid_code="MCEL_CALCULATOR_CANDIDATE_PROJECTION_INVALID",
    )


def build_calculator_promotion_profile(
    *,
    project_candidate: Callable[..., Any],
    run_candidate_evidence: Callable[..., Any],
) -> HostBoundPromotionProfile:
    return HostBoundPromotionProfile(
        app_id=APP_ID,
        default_dsl_source=DEFAULT_DSL_SOURCE,
        default_package_root=DEFAULT_PACKAGE_ROOT,
        default_candidate_root=DEFAULT_CANDIDATE_ROOT,
        default_evidence_report_root=DEFAULT_CANDIDATE_EVIDENCE_REPORT_ROOT,
        project_candidate=project_candidate,
        run_candidate_evidence=run_candidate_evidence,
        report_root=DEFAULT_PROMOTION_REPORT_ROOT,
        report_schema="mcel.calculator-promotion-rehearsal-report.v1",
        report_version="mcel-calculator-promotion-rehearsal-v1",
        report_filename="mcel-calculator-promotion-rehearsal-report.json",
        report_markdown_filename="mcel-calculator-promotion-rehearsal-report.md",
        report_title="Calculator Promotion Rehearsal",
        execution_report_schema="mcel.calculator-promotion-execution-report.v1",
        execution_report_version="mcel-calculator-authority-finalization-v1",
        rollback_report_schema="mcel.calculator-promotion-rollback-result.v1",
        rollback_report_version="mcel-calculator-authority-finalization-v1",
        plan_schema="mcel.application-promotion-plan.v1",
        plan_id="mcel-calculator-promotion-rehearsal-v1",
        promotion_boundary=PROMOTION_BOUNDARY,
        promoted_truth_status=PROMOTED_TRUTH_STATUS,
        derived_artifact_authority_after=PROJECTION_PROFILE,
        projection_profile=PROJECTION_PROFILE,
        browser_evidence_schema="mcel.calculator-browser-parity-observation.v1",
        not_authoritative_code="MCEL_CALCULATOR_NOT_AUTHORITATIVE",
        rollback_requires_transaction_code="MCEL_CALCULATOR_ROLLBACK_REQUIRES_PATCH_UNDO",
        dsl_invalid_code="MCEL_CALCULATOR_PROMOTION_DSL_INVALID",
        projection_invalid_code="MCEL_CALCULATOR_PROMOTION_PROJECTION_INVALID",
        evidence_invalid_code="MCEL_CALCULATOR_PROMOTION_EVIDENCE_INVALID",
        stage_failed_code="MCEL_CALCULATOR_PROMOTION_REHEARSAL_STAGE_FAILED",
        no_op_code="MCEL_CALCULATOR_PROMOTION_NOOP",
        rollback_failed_code="MCEL_CALCULATOR_PROMOTION_ROLLBACK_FAILED",
        workspace_invalid_code="MCEL_CALCULATOR_PROMOTED_WORKSPACE_INVALID",
    )
