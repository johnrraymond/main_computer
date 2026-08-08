"""Counter reference-fixture effect probes.

Counter is the small explicit-package MCEL conformance fixture.  The generic
explicit-package projection/evidence/proof/promotion modules own pipeline
mechanics; this module owns the Counter-specific operation sequence and
effect-accounting expectations shared by those wrappers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping

from main_computer.mcel_counter_reference_fixture_profile import (
    BROWSER_PROBE_SCHEMA,
    EFFECT_REPORT_SCHEMA,
    NODE_PROBE_SCHEMA,
)
from main_computer.mcel_explicit_package_candidate_evidence import (
    ExplicitPackageCandidateEvidenceError,
    _diagnostic,
)
from main_computer.mcel_node_runtime import resolve_node_executable


class CounterCandidateEvidenceError(ExplicitPackageCandidateEvidenceError):
    """Raised when Counter fixture effect evidence cannot be produced truthfully."""


def _run_counter_effect_probe(workspace: Path, operation_prefix: str = "candidate") -> Mapping[str, Any]:
    node = resolve_node_executable()
    if not node:
        raise CounterCandidateEvidenceError("Node.js is required for Counter effect accounting.")
    scm = (workspace / "main_computer/web/applications/scripts/mcel-scm.js").read_text(encoding="utf-8")
    runtime = (workspace / "main_computer/web/applications/scripts/mcel-application-runtime.js").read_text(encoding="utf-8")
    package = workspace / "mcel_apps/contract-counter"
    script = f'''\
"use strict";
const fs = require("fs");
{scm}
{runtime}
async function importContract(filePath) {{
  const source = fs.readFileSync(filePath, "utf8");
  const url = `data:text/javascript;base64,${{Buffer.from(source, "utf8").toString("base64")}}`;
  return import(url);
}}
(async () => {{
  const domain = await importContract({json.dumps(str(package / "contracts/domain.js"))});
  const intents = await importContract({json.dumps(str(package / "contracts/intents.js"))});
  const adapter = await importContract({json.dumps(str(package / "contracts/adapter.js"))});
  const definition = McelApplicationRuntime.defineApplication({{
    appId: "contract-counter",
    domain: domain.ContractCounterDomain,
    intents: intents.ContractCounterIntents,
    adapter: adapter.ContractCounterAdapter
  }});
  const app = McelApplicationRuntime.createApplicationInstance(definition, {{id: {json.dumps(operation_prefix + "-effect-probe")}}});
  function dispatch(operationId, intentId, expectedRevision, payload = {{}}) {{
    const before = app.readState();
    const result = app.dispatch({{operationId, intentId, expectedRevision, payload}});
    const after = app.readState();
    return {{operationId, intentId, expectedRevision, before, result, after}};
  }}
  const operations = [
    dispatch({json.dumps(operation_prefix + "-increment")}, "increment", 0),
    dispatch({json.dumps(operation_prefix + "-stale")}, "increment", 0),
    dispatch({json.dumps(operation_prefix + "-direct-set")}, "direct-set", 1, {{value: 99}}),
    dispatch({json.dumps(operation_prefix + "-reset")}, "reset", 1)
  ];
  process.stdout.write(JSON.stringify({{
    schema: {json.dumps(NODE_PROBE_SCHEMA)},
    appId: "contract-counter",
    initialState: {{count: 0, revision: 0}},
    operations,
    finalState: app.readState(),
    finalRevision: app.revision,
    appliedOperationIds: app.appliedOperationIds
  }}));
}})().catch((error) => {{ console.error(error); process.exit(1); }});
'''
    script_path = workspace / "runtime/state/mcel/counter-candidate-effect-probe.js"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [str(node), str(script_path)],
        cwd=workspace,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        raise CounterCandidateEvidenceError(
            "Counter effect probe failed" + (f": {completed.stderr.strip()}" if completed.stderr else ".")
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CounterCandidateEvidenceError("Counter effect probe returned invalid JSON.") from exc
    if not isinstance(value, Mapping) or value.get("schema") != NODE_PROBE_SCHEMA:
        raise CounterCandidateEvidenceError("Counter effect probe returned the wrong schema.")
    return value


def _run_browser_effect_probe(workspace: Path, headed: bool, operation_prefix: str = "candidate") -> Mapping[str, Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CounterCandidateEvidenceError(
            "Playwright is required for isolated candidate browser effect evidence."
        ) from exc
    from main_computer.mcel_application_observation_runner import _StaticServer

    web_root = workspace / "main_computer" / "web"
    with _StaticServer(web_root) as server, sync_playwright() as playwright:
        launch_attempts: list[dict[str, Any]] = [{"headless": not headed}]
        executable = (
            os.environ.get("MCEL_CHROMIUM_EXECUTABLE")
            or os.environ.get("CHROMIUM_EXECUTABLE")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
            or shutil.which("google-chrome")
            or shutil.which("msedge")
        )
        if executable:
            launch_attempts.append(
                {
                    "headless": not headed,
                    "executable_path": executable,
                    "args": [
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--no-proxy-server",
                        "--proxy-bypass-list=*",
                    ],
                }
            )
        browser = None
        errors: list[str] = []
        for options in launch_attempts:
            try:
                browser = playwright.chromium.launch(**options)
                break
            except PlaywrightError as exc:
                errors.append(str(exc))
        if browser is None:
            raise CounterCandidateEvidenceError(
                "Playwright Chromium is unavailable. " + " | ".join(errors[-2:])
            )
        try:
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                url = f"{server.base_url}/mcel-package-host.html?app=contract-counter&observation=1"
                page.goto(url, wait_until="networkidle")
                page.wait_for_function(
                    "window.McelApplicationPackageHost && "
                    "(window.McelApplicationPackageHost.ready === true || window.McelApplicationPackageHost.error)",
                    timeout=30_000,
                )
                result = _evaluate_browser_effect_probe(page, url, operation_prefix)
            except PlaywrightError as exc:
                raise CounterCandidateEvidenceError(
                    f"Candidate browser effect probe failed: {exc}"
                ) from exc
            result["browser"] = {
                "engine": "playwright-chromium",
                "version": browser.version,
                "headless": not headed,
            }
            return result
        finally:
            browser.close()


def _evaluate_browser_effect_probe(page: Any, url: str, operation_prefix: str = "candidate") -> dict[str, Any]:
    """Run the browser effect probe with all host values passed explicitly."""

    return page.evaluate(
        """async ({pageUrl, operationPrefix}) => {
          const host = window.McelApplicationPackageHost;
          if (host.ready !== true) throw new Error(host.error || "candidate host failed");
          const valueNode = () => host.mount.root.querySelector('[data-mcel-node-id="contract-counter.value"]');
          const visible = () => String(valueNode()?.textContent || "").trim();
          const state = () => host.mount.readState();
          const operations = [];
          async function committed(operationId, intentId, expectedRevision) {
            const before = state();
            const outcome = await host.dispatchAndObserve(intentId, {}, {
              operationId,
              expectedRevision,
              repositoryFingerprint: `${operationPrefix}-browser-effect-probe`,
              capturedAt: new Date(0).toISOString(),
              browser: {engine: "playwright-chromium", version: "candidate-effect-probe"},
              viewport: {width: window.innerWidth, height: window.innerHeight, deviceScaleFactor: window.devicePixelRatio}
            });
            operations.push({operationId, intentId, before, result: outcome.operationResult, after: state(), visible: visible(), observation: outcome.observation});
          }
          function refused(operationId, intentId, expectedRevision, payload = {}) {
            const before = state();
            const outcome = host.dispatch(intentId, payload, {operationId, expectedRevision});
            operations.push({operationId, intentId, before, result: outcome, after: state(), visible: visible()});
          }
          const initial = {state: state(), visible: visible()};
          await committed(`${operationPrefix}-browser-increment`, "increment", 0);
          refused(`${operationPrefix}-browser-stale`, "increment", 0);
          refused(`${operationPrefix}-browser-direct-set`, "direct-set", 1, {value: 99});
          await committed(`${operationPrefix}-browser-reset`, "reset", 1);
          return {schema: "mcel.counter-browser-effect-probe.v1", appId: "contract-counter", url: pageUrl, initial, operations, final: {state: state(), visible: visible()}};
        }""",
        {"pageUrl": url, "operationPrefix": operation_prefix},
    )


def _build_effect_accounting(
    *,
    ir: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    observation: Mapping[str, Any],
    node_probe: Mapping[str, Any],
    browser_probe: Mapping[str, Any],
    operation_prefix: str = "candidate",
) -> dict[str, Any]:
    diagnostics: list[dict[str, Any]] = []
    declared = {
        str(item.get("id")): item
        for item in ir.get("effects") or []
        if isinstance(item, Mapping) and item.get("id")
    }
    expected_ids = {
        "effect:increment.count-write",
        "effect:increment.revision-write",
        "effect:reset.count-write",
        "effect:reset.revision-write",
    }
    if set(declared) != expected_ids:
        diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_DECLARATIONS_INCOMPLETE", "Counter must declare exactly four canonical-write effects.", "$effects"))

    node_ops = {str(item.get("operationId")): item for item in node_probe.get("operations") or []}
    browser_ops = {str(item.get("operationId")): item for item in browser_probe.get("operations") or []}
    required_node = {f"{operation_prefix}-increment", f"{operation_prefix}-stale", f"{operation_prefix}-direct-set", f"{operation_prefix}-reset"}
    required_browser = {f"{operation_prefix}-browser-increment", f"{operation_prefix}-browser-stale", f"{operation_prefix}-browser-direct-set", f"{operation_prefix}-browser-reset"}
    if set(node_ops) != required_node:
        diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_NODE_PROBE_INCOMPLETE", "Node effect probe did not produce all required operations.", "$nodeProbe.operations"))
    if set(browser_ops) != required_browser:
        diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_BROWSER_PROBE_INCOMPLETE", "Browser effect probe did not produce all required operations.", "$browserProbe.operations"))

    instances: list[dict[str, Any]] = []
    operation_specs = [
        (f"{operation_prefix}-increment", f"{operation_prefix}-browser-increment", "increment", "completed"),
        (f"{operation_prefix}-stale", f"{operation_prefix}-browser-stale", "increment", "refused-before-attempt"),
        (f"{operation_prefix}-reset", f"{operation_prefix}-browser-reset", "reset", "completed"),
    ]
    for node_id, browser_id, owner_suffix, disposition in operation_specs:
        node = node_ops.get(node_id) or {}
        browser = browser_ops.get(browser_id) or {}
        before = node.get("before") or {}
        after = node.get("after") or {}
        result = node.get("result") or {}
        browser_before = browser.get("before") or {}
        browser_after = browser.get("after") or {}
        browser_result = browser.get("result") or {}
        if disposition == "completed":
            if result.get("ok") is not True or result.get("status") != "committed":
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_RECEIPT_MISMATCH", f"{node_id} did not commit.", f"$nodeProbe.{node_id}"))
            if browser_result.get("ok") is not True or browser_result.get("status") != "committed":
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_BROWSER_RECEIPT_MISMATCH", f"{browser_id} did not commit.", f"$browserProbe.{browser_id}"))
            observed = browser.get("observation") or {}
            comparison = observed.get("comparison") or {}
            if observed.get("status") != "pass" or comparison.get("surfaceMatches") is not True:
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_VISIBLE_OUTCOME_MISMATCH", f"{browser_id} did not produce a passing visible outcome.", f"$browserProbe.{browser_id}.observation"))
            expected_count = 1 if owner_suffix == "increment" else 0
            if browser_after.get("count") != expected_count or str(browser.get("visible")) != str(expected_count):
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_VISIBLE_VALUE_MISMATCH", f"{browser_id} visible count did not match canonical count.", f"$browserProbe.{browser_id}.visible"))
        else:
            expected_code = "SCM_STALE_REVISION"
            if result.get("ok") is not False or result.get("code") != expected_code or before != after:
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_REFUSAL_MUTATED_STATE", f"{node_id} was not a clean stale-revision refusal.", f"$nodeProbe.{node_id}"))
            if browser_result.get("ok") is not False or browser_result.get("code") != expected_code or browser_before != browser_after:
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_BROWSER_REFUSAL_MUTATED_STATE", f"{browser_id} was not a clean browser stale-revision refusal.", f"$browserProbe.{browser_id}"))
            if str(browser.get("visible")) != str(browser_after.get("count")):
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_REFUSAL_VISIBLE_DRIFT", f"{browser_id} changed visible state during refusal.", f"$browserProbe.{browser_id}.visible"))

        for state_name in ("count", "revision"):
            effect_id = f"effect:{owner_suffix}.{state_name}-write"
            if effect_id not in declared:
                continue
            actual_changed = before.get(state_name) != after.get(state_name)
            if disposition == "completed" and not actual_changed:
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_WRITE_MISSING", f"{effect_id} did not change its target.", f"$effects.{effect_id}"))
            if disposition != "completed" and actual_changed:
                diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_REFUSED_WRITE", f"{effect_id} changed state despite refusal.", f"$effects.{effect_id}"))
            instances.append(
                {
                    "id": f"instance:{node_id}:{effect_id}",
                    "effectId": effect_id,
                    "owner": f"intent:{owner_suffix}",
                    "operationId": node_id,
                    "disposition": disposition,
                    "target": f"state:{state_name}",
                    "before": before.get(state_name),
                    "after": after.get(state_name),
                    "evidence": {
                        "operationReceipt": result,
                        "canonicalReconciliation": {"before": before, "after": after},
                        "visibleOutcome": {
                            "operationId": browser_id,
                            "visible": browser.get("visible"),
                            "before": browser_before,
                            "after": browser_after,
                        },
                    },
                    "status": "closed",
                }
            )

    direct_node = node_ops.get(f"{operation_prefix}-direct-set") or {}
    direct_browser = browser_ops.get(f"{operation_prefix}-browser-direct-set") or {}
    direct_clean = (
        (direct_node.get("result") or {}).get("ok") is False
        and (direct_node.get("result") or {}).get("code") == "INTENT_PROHIBITED"
        and direct_node.get("before") == direct_node.get("after")
        and (direct_browser.get("result") or {}).get("ok") is False
        and (direct_browser.get("result") or {}).get("code") == "INTENT_PROHIBITED"
        and direct_browser.get("before") == direct_browser.get("after")
        and str(direct_browser.get("visible")) == str((direct_browser.get("after") or {}).get("count"))
        and not any((item.get("owner") or {}).get("ref") == "intent:direct-set" for item in declared.values())
    )
    if not direct_clean:
        diagnostics.append(_diagnostic("MCEL_COUNTER_DIRECT_SET_EFFECT_LEAK", "Prohibited direct-set produced or implied a canonical effect.", "$effects.directSet"))

    acceptance_pass = acceptance.get("status") == "pass" and acceptance.get("passed") is True
    observation_pass = observation.get("status") == "pass" and observation.get("ok") is True
    if not acceptance_pass:
        diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_ACCEPTANCE_MISSING", "Candidate acceptance evidence did not pass.", "$acceptance"))
    if not observation_pass:
        diagnostics.append(_diagnostic("MCEL_COUNTER_EFFECT_OBSERVATION_MISSING", "Candidate browser observation did not pass.", "$observation"))

    valid = not diagnostics and len(instances) == 6 and direct_clean
    return {
        "schema": EFFECT_REPORT_SCHEMA,
        "appId": "contract-counter",
        "status": "closed" if valid else "open",
        "valid": valid,
        "declaredEffectCount": len(declared),
        "effectInstanceCount": len(instances),
        "closedEffectInstanceCount": sum(item.get("status") == "closed" for item in instances),
        "unexplainedEffectCount": 0 if valid else len(diagnostics),
        "directSetCanonicalWriteObserved": not direct_clean,
        "instances": instances,
        "diagnostics": diagnostics,
        "authorities": {
            "acceptance": "pass" if acceptance_pass else "fail",
            "browserObservation": "pass" if observation_pass else "fail",
            "nodeEffectProbe": "pass" if set(node_ops) == required_node else "fail",
            "browserEffectProbe": "pass" if set(browser_ops) == required_browser else "fail",
        },
    }
