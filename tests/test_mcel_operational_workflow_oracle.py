"""Frozen model + non-SLH generality tests. Deliberately red before MCEL repair."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path, PureWindowsPath

import pytest

from tools import mcel_operational_workflow_oracle as oracle


def test_frozen_contract_has_the_screenshot_and_full_lifecycle():
    data = oracle.read_contract()
    case = next(x for x in data['scenarios'] if x['id'] == 'running-unauthenticated-screenshot')
    assert case['observation'] == {
        'local': 'RUNNING', 'initialized': True, 'organization': 'running',
        'authenticated': False, 'fresh': True,
    }
    assert case['primary'] == 'intent:login-administrator'
    assert 'intent:bootstrap-administrator' in case['not_competing']
    assert data['shared_input']['max_simultaneously_visible_editors'] == 1
    assert len(data['transitions']) == 6


def test_generic_fixture_reference_is_windows_path_independent(monkeypatch):
    """Oracle JSON uses repository-relative slashes even on Windows hosts."""
    root = PureWindowsPath(r"C:\Users\operator\main_computer")
    monkeypatch.setattr(oracle, 'ROOT', root)
    monkeypatch.setattr(
        oracle, 'GENERIC', root / 'mcel_oracles' / 'operational-workflows' / 'generic_application.js',
    )
    assert oracle.read_contract()['generic_control']['fixture'] == (
        'mcel_oracles/operational-workflows/generic_application.js'
    )


def test_oracle_rejects_missing_freshness_safety(tmp_path):
    document = deepcopy(oracle.read_contract())
    document['implementation_policy']['stale_or_unknown'] = 'optimistic'
    path = tmp_path / 'weakened.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(oracle.OracleContractError, match='unsafe-unknown-state'):
        oracle.read_contract(path)


def test_oracle_rejects_stale_consequential_action_escape(tmp_path):
    document = deepcopy(oracle.read_contract())
    case = next(x for x in document['scenarios'] if x['id'] == 'observation-stale')
    case['not_competing'].remove('intent:activate-organization')
    path = tmp_path / 'weakened.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(oracle.OracleContractError, match='stale-must-refuse'):
        oracle.read_contract(path)


def test_evaluation_uses_semantics_not_layout_or_pixels():
    contract = oracle.read_contract()
    expected = 'running-unauthenticated-screenshot'
    good = oracle.evaluate_visible_case(contract, expected, ['intent:login-administrator', 'intent:refresh-health'])
    assert good['status'] == 'PASS'
    bad = oracle.evaluate_visible_case(contract, expected, ['intent:login-administrator', 'intent:bootstrap-administrator'])
    assert bad['status'] == 'FAIL'
    assert bad['competing_intents'] == ['intent:bootstrap-administrator']


def test_slh_has_backend_conditioned_operational_interactions():
    result = oracle.inspect_compiled_sources()['slh']
    assert result['status'] == 'PASS', result


def test_independent_generic_application_compiles_derived_condition():
    # General MCEL acceptance: cannot be an SLH-specific hide-buttons patch.
    result = oracle.inspect_compiled_sources()['generic_app']
    assert result['status'] == 'PASS', result


def test_generic_condition_reacts_to_a_new_backend_observation_in_browser():
    """No exact layout, no SLH identifiers, no application-specific JS policies."""
    import shutil
    from playwright.sync_api import sync_playwright
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    from main_computer.mcel_knowledge.semantic_composition import synthesize_composition

    compiled = compile_dsl_application(oracle.GENERIC)
    assert compiled.valid, compiled.diagnostics
    ir = compiled.normalized_ir
    composition = synthesize_composition(ir, policy='mcel.semantic-structure.p3')
    scripts = ['main_computer/web/applications/scripts/mcel-semantic-layout-inference.js',
               'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js']
    document = '<!doctype html><html><body><main id="fixture"></main>' + ''.join(
        '<script>' + (oracle.ROOT / name).read_text(encoding='utf-8') + '</script>' for name in scripts
    ) + '</body></html>'
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.set_content(document)
            page.evaluate('''({ir,plan}) => {
              window.phaseValue="inactive";
              const port={authorize:async()=>true, invoke:async req=>{
                if(req.operation==="phase")return window.phaseValue;
                if(req.operation==="entries")return [{id:"a",label:"A"}];
                if(req.operation==="enable")return "activated";
                throw Error("unexpected-operation");
              }};
              window.genericApp=McelSemanticOperationalRuntime.mount(
                document.querySelector("#fixture"),ir,{capabilities:port,composition:plan});
            }''', {'ir': ir, 'plan': composition})
            # Before observing the phase, unknown state must not enable mutation.
            assert not page.locator('[data-intent="intent:activate"]').is_visible()
            page.evaluate('''async()=>{
                await window.genericApp.execute("intent:inspect");
                await window.genericApp.execute("intent:inspect-entries");
            }''')
            assert page.locator('[data-intent="intent:activate"]').is_visible()
            page.evaluate('''async()=>{
                window.phaseValue="active";
                await window.genericApp.execute("intent:inspect");
            }''')
            assert not page.locator('[data-intent="intent:activate"]').is_visible()
            assert errors == []
        finally:
            browser.close()


def test_saved_baseline_is_bound_to_this_unchanged_oracle():
    baseline_path = oracle.SPEC.with_name('baseline.v1.json')
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    assert baseline['schema'] == 'mcel.operational-workflow-baseline.v1'
    assert baseline['oracle_sha256'] == oracle.digest(oracle.SPEC)
    assert baseline['generic_fixture_sha256'] == oracle.digest(oracle.GENERIC)
    assert baseline['source_checks']['slh']['status'] == 'FAIL'
    assert baseline['source_checks']['generic_app']['status'] == 'FAIL'
    assert baseline['browser_baseline']['failing_scenarios'] == len(oracle.read_contract()['scenarios'])
    assert baseline['browser_baseline']['stale_authority_gate']['status'] == 'FAIL'
