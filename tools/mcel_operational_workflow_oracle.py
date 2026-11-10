"""Frozen MCEL operational-workflow oracle V1, independent of app rendering policy.

This tool does not create an SLH service, sign in, mutate accounts, or infer a
lifecycle stage from a button click. Browser fixtures live in the test suite.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tests/fixtures/mcel_oracles/operational-workflows/oracle.v1.json"
GENERIC = ROOT / "tests/fixtures/mcel_oracles/operational-workflows/generic_application.js"
SCHEMA = "mcel.operational-workflow-oracle.v1"
# A frozen contract describes its original source path; relocating bytes must not
# rewrite the historical oracle or invalidate its SHA256-locked baseline.
FROZEN_GENERIC_REF = "mcel_oracles/operational-workflows/generic_application.js"
CONSEQUENTIAL = frozenset({
    "intent:start-local", "intent:bootstrap-administrator",
    "intent:configure-organization", "intent:activate-organization",
    "intent:login-administrator", "intent:logout-administrator",
    "intent:rename-worker",
})


class OracleContractError(ValueError):
    pass


def read_contract(path: Path = SPEC) -> dict:
    """Validate independent expectations; reject a weakened or malformed oracle."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise OracleContractError("wrong-schema")
    policy = data.get("implementation_policy", {})
    for name in ("action_eligibility_is_not_authorization", "no_manual_widget_layout",
                 "no_app_specific_runtime_conditionals", "browser_compatible_navigation",
                 "no_automatic_windows_boot_startup"):
        if policy.get(name) is not True:
            raise OracleContractError("weakened-policy:" + name)
    if policy.get("stale_or_unknown") != "fail-closed-for-consequential-operations":
        raise OracleContractError("unsafe-unknown-state")
    cases = data.get("scenarios")
    if not isinstance(cases, list) or len(cases) < 9:
        raise OracleContractError("incomplete-state-matrix")
    ids = [c.get("id") for c in cases]
    if len(set(ids)) != len(ids) or not all(isinstance(x, str) and x for x in ids):
        raise OracleContractError("duplicate-scenario")
    required = {"local-offline", "local-uninitialized", "organization-unconfigured",
                "organization-disabled", "running-unauthenticated-screenshot",
                "running-authenticated", "organization-failed", "status-unverified",
                "observation-stale"}
    if not required.issubset(ids):
        raise OracleContractError("missing-required-state")
    for case in cases:
        obs = case.get("observation")
        if not isinstance(obs, dict) or set(obs) != {"local", "initialized", "organization", "authenticated", "fresh"}:
            raise OracleContractError("invalid-observation:" + case["id"])
        if obs["local"] not in ("RUNNING", "UNREACHABLE", "UNVERIFIED") or type(obs["fresh"]) is not bool:
            raise OracleContractError("invalid-evidence:" + case["id"])
        for key in ("authenticated", "initialized"):
            if obs[key] is not None and type(obs[key]) is not bool:
                raise OracleContractError("untyped-evidence:" + case["id"])
        if not isinstance(obs["organization"], str) or not obs["organization"]:
            raise OracleContractError("invalid-organization:" + case["id"])
        if not isinstance(case.get("primary"), str) or not case["primary"].startswith("intent:"):
            raise OracleContractError("invalid-primary:" + case["id"])
        competing = case.get("not_competing")
        if not isinstance(competing, list) or not competing or len(competing) != len(set(competing)):
            raise OracleContractError("invalid-competing:" + case["id"])
        if case["primary"] in competing or not all(x in CONSEQUENTIAL for x in competing):
            raise OracleContractError("contradictory-action:" + case["id"])
        if not obs["fresh"] and any(x not in competing for x in CONSEQUENTIAL):
            raise OracleContractError("stale-must-refuse-consequential:" + case["id"])
    if data["shared_input"] != {"semantic_identity": "administrator-device-id",
            "field_name": "adminDeviceId", "max_simultaneously_visible_editors": 1,
            "applicability_scope": "same-authenticated-human-identity-not-auto-authorization"}:
        raise OracleContractError("shared-input-contract-changed")
    nav = data.get("navigation", {})
    if nav != {"overview": "/applications/slh", "worker_bank": "/applications/slh/workers",
               "require_real_hrefs": True, "modified_clicks_native": True}:
        raise OracleContractError("navigation-contract-changed")
    if data.get("generic_control", {}).get("fixture") != FROZEN_GENERIC_REF:
        raise OracleContractError("generic-control-missing")
    pairs = {(x["from"], x["to"], x["on"]) for x in data.get("transitions", [])}
    if not {
        ("local-offline", "local-uninitialized", "intent:start-local"),
        ("local-uninitialized", "organization-unconfigured", "intent:bootstrap-administrator"),
        ("organization-unconfigured", "organization-disabled", "intent:configure-organization"),
        ("organization-disabled", "running-unauthenticated-screenshot", "intent:activate-organization"),
        ("running-unauthenticated-screenshot", "running-authenticated", "intent:login-administrator"),
        ("running-authenticated", "running-unauthenticated-screenshot", "intent:logout-administrator"),
    }.issubset(pairs):
        raise OracleContractError("missing-transition")
    return data


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def visible_intents(page) -> list[str]:
    """Use rendered accessibility, not DOM presence, CSS class, pixel geometry."""
    roots = page.locator('#mcel-workbench [data-intent]')
    result: set[str] = set()
    for index in range(roots.count()):
        item = roots.nth(index)
        if item.is_visible() and not item.is_disabled() and item.get_attribute("data-intent"):
            result.add(item.get_attribute("data-intent"))
    return sorted(result)


def evaluate_visible_case(contract: dict, case_id: str, visible: list[str]) -> dict:
    case = next(x for x in contract["scenarios"] if x["id"] == case_id)
    visible = sorted(set(visible))
    competing = sorted(set(visible).intersection(case["not_competing"]))
    primary_present = case["primary"] in visible
    return {
        "scenario": case_id,
        "authoritative_observation": case["observation"],
        "expected_primary": case["primary"],
        "visible_intents": visible,
        "primary_present": primary_present,
        "competing_intents": competing,
        "status": "PASS" if primary_present and not competing else "FAIL",
    }


def inspect_compiled_sources(root: Path = ROOT) -> dict:
    """Read-only cross-application probe; never changes compiler or application."""
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    from main_computer.mcel_slh_application import compile_slh_application

    ir, composition = compile_slh_application(root)
    nodes = ir["surfaces"][0]["nodes"]
    states = {s["id"]: s for s in ir["states"]}
    observed_gate_nodes = [n["id"] for n in nodes if
        isinstance(n.get("visibleWhen"), dict) and
        states.get(n["visibleWhen"].get("state", {}).get("ref"), {}).get("authority") == "derived"]
    generic = compile_dsl_application(root / GENERIC.relative_to(ROOT))
    return {
        "slh": {
            "status": "PASS" if observed_gate_nodes else "FAIL",
            "backend_conditioned_surface_nodes": observed_gate_nodes,
            "explanation": "SLH must use backend-derived operational applicability, not only local view selection",
        },
        "generic_app": {
            "status": "PASS" if generic.valid else "FAIL",
            "diagnostics": ([x.get("code") for x in generic.diagnostics] if not generic.valid else []),
            "explanation": "A non-SLH semantic app must compile a derived-state-conditioned consequential action",
        },
        "slh_ir_hash": ir.get("fingerprints", {}).get("semantic"),
        "composition_policy": composition.get("policy"),
    }



def capture_browser_baseline(contract: dict, *, screenshots_dir: Path | None = None) -> dict:
    """Snapshot the generated UI under fixed, inert network fixtures.

    Uses precisely the browser helper that the unmodified red/green test suite
    exercises. Never calls the real SLH service or supplies a production token.
    """
    import importlib.util
    import shutil
    from datetime import datetime, timezone
    from playwright.sync_api import sync_playwright

    path = ROOT / 'tests/test_mcel_slh_workflow_browser_oracle.py'
    spec = importlib.util.spec_from_file_location('_mcel_oracle_browser_fixtures', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if screenshots_dir:
        screenshots_dir.mkdir(parents=True, exist_ok=True)
    report = {'captured_at_utc': datetime.now(timezone.utc).isoformat(),
              'source': 'inert-browser-fixture-not-live-slH', 'scenarios': [],
              'screenshot_basis': 'diagnostic-only', 'mutations_performed': False}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            for case in contract['scenarios']:
                case_id = case['id']
                initial = (case_id if case_id != 'running-authenticated'
                           else 'running-unauthenticated-screenshot')
                transitions = {'login':'running-authenticated'} if case_id == 'running-authenticated' else None
                page, errors = module.browser_page(browser, initial, transitions=transitions)
                try:
                    if case_id == 'running-authenticated':
                        # V4-B's identity is selected from LMS-SLH, not typed in a form.
                        page.locator('button[data-intent="intent:login-administrator"]').click()
                        page.get_by_text('Organization administrator authenticated', exact=True).wait_for(timeout=15000)
                    check = evaluate_visible_case(contract, case_id, visible_intents(page))
                    check['observed_at'] = page.evaluate('''()=>window.oracleFixture.cases[window.oracleFixture.current].overview.observedAt''')
                    check['errors'] = list(errors)
                    if case_id == 'running-unauthenticated-screenshot':
                        check['simultaneously_visible_admin_device_id_inputs'] = page.locator(
                            'form[data-intent] input[name="adminDeviceId"]:visible').count()
                    if screenshots_dir and case_id in {'running-unauthenticated-screenshot', 'observation-stale'}:
                        shot = screenshots_dir / (case_id + '.png')
                        page.screenshot(path=str(shot), full_page=True)
                        check['diagnostic_screenshot'] = shot.name
                    report['scenarios'].append(check)
                finally:
                    page.close()
            # The same programmatic stale-state attempt as the strict browser test.
            stale, errors = module.browser_page(browser, 'observation-stale')
            try:
                receipt = stale.evaluate('''async()=>window.__mcelSlh.app.execute("intent:activate-organization",
                    {adminDeviceId:"admin-pc"})''')
                posts = stale.evaluate('''()=>window.oracleFixture.requests.filter(x=>x.method==="POST")''')
                report['stale_authority_gate'] = {
                    'status': 'PASS' if not posts and receipt.get('disposition') in
                                        ('failed','refused-before-attempt') else 'FAIL',
                    'post_requests_attempted': len(posts),
                    'receipt_disposition': receipt.get('disposition'),
                    'errors': errors,
                }
            finally:
                stale.close()
        finally:
            browser.close()
    report['passing_scenarios'] = sum(x['status'] == 'PASS' for x in report['scenarios'])
    report['failing_scenarios'] = sum(x['status'] != 'PASS' for x in report['scenarios'])
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only MCEL operational-workflow oracle source baseline")
    parser.add_argument("--report", type=Path, help="Write reproducible JSON report; defaults to stdout only")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero if any source or browser acceptance gate fails")
    parser.add_argument("--browser", action="store_true", help="Capture inert Chromium state-to-interaction baseline")
    parser.add_argument("--screenshots-dir", type=Path, help="Optional diagnostic Chromium screenshots; no pixel acceptance")
    args = parser.parse_args(argv)
    contract = read_contract()
    result = {
        "schema": "mcel.operational-workflow-baseline.v1",
        "oracle_sha256": digest(SPEC),
        "generic_fixture_sha256": digest(GENERIC),
        "source_checks": inspect_compiled_sources(),
        "browser_and_backend": "Run tests/test_mcel_slh_workflow_browser_oracle.py and existing disposable first-run acceptance",
        "mutations_performed": False,
    }
    if args.browser:
        result["browser_baseline"] = capture_browser_baseline(contract, screenshots_dir=args.screenshots_dir)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    failed = any(v["status"] != "PASS" for v in result["source_checks"].values()
                 if isinstance(v, dict) and "status" in v)
    if args.browser:
        failed = (failed or result["browser_baseline"]["failing_scenarios"] > 0 or
                  result["browser_baseline"]["stale_authority_gate"]["status"] != "PASS")
    return int(args.strict and failed)


if __name__ == "__main__":
    raise SystemExit(main())
