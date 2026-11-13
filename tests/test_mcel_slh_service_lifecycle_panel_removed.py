"""MCEL removal oracle: the legacy local Service Lifecycle panel is absent.

Backend authority, first-run and authentication controllers are not altered.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / 'tests/fixtures/mcel_oracles/seh-management-oracle.v1.json').read_text())
REMOVED_NODES = {
    'surface-node:slh.login-administrator',
    'surface-node:slh.logout-administrator',
}
REMOVED_INTENTS = {
    'intent:login-administrator',
    'intent:logout-administrator',
}


def test_removal_is_semantic_and_does_not_delete_backend_operations():
    ir, composition = compile_slh_application()
    assert CONTRACT['composition']['local_service_lifecycle_panel'] == 'absent'
    assert CONTRACT['composition']['legacy_login_controls_unrendered'] is True
    assert CONTRACT['composition']['local_configuration_reset_control'] == 'absent'
    regions = ir['surfaces'][0]['interfaceIntent']['regions']
    assert not any(r['id'] == 'interface-region:slh.service-lifecycle' for r in regions)
    assert not REMOVED_NODES.intersection(n['id'] for n in ir['surfaces'][0]['nodes'])
    assert not any(REMOVED_NODES.intersection(m['ref'] for m in r['members'])
                   for r in composition['regions'])
    assert REMOVED_INTENTS <= {intent['id'] for intent in ir['intents']}
    assert {'surface-node:slh.organization-enablement','surface-node:slh.start-stop-org-slh'} <= {node['id'] for node in ir['surfaces'][0]['nodes']}
    assert {'surface-node:slh.local-seh-lifecycle', 'surface-node:slh.network-seh-lifecycle'} <= {
        node['id'] for node in ir['surfaces'][0]['nodes']
    }


@pytest.mark.parametrize('view', ['overview', 'network', 'remote-seh'])
def test_panel_and_all_three_buttons_absent(view):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True,
                                    executable_path=shutil.which('chromium') or None,
                                    args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width': 1100, 'height': 780})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.evaluate('''() => {
                window.calls=[];
                window.fetch=async (url,options={}) => {
                    window.calls.push({url,method:options.method||'GET'});
                    if(url.endsWith('/identities'))return {ok:true,json:async()=>({
                      schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'one',
                      identities:[{deviceId:'one',label:'Default',fingerprint:'a'.repeat(64),
                        defaultStatus:'DEFAULT'}], explanation:'available'})};
                    if(url.includes('observe?'))return {ok:true,json:async()=>({
                      ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
                      overview:{organizationState:'running',organizationEndpoint:'127.0.0.1:8899'}})};
                    return {ok:false,json:async()=>({error:'UNAVAILABLE'})};
                };
            }''')
            page.set_content(render_slh_page(view), wait_until='domcontentloaded')
            page.wait_for_function('() => !!window.__mcelSlh?.app', timeout=10000)
            assert page.get_by_text('Service Lifecycle', exact=True).count() == 0
            assert page.locator('[data-mcel-purpose="commands"]:has([data-intent="intent:disable-organization"])').count() == 0
            for label in ('Disable Organization', 'Login Administrator', 'Logout Administrator'):
                assert page.get_by_role('button', name=label).count() == 0
            for intent in REMOVED_INTENTS:
                assert page.locator(f'button[data-intent="{intent}"]').count() == 0
            for intent in ('toggle-organization-enablement','toggle-organization'):
                control = page.locator(f'button[data-intent="intent:{intent}"]')
                assert control.count()==1
                assert control.is_visible() is (view=='overview')
            assert page.get_by_role('button',name='Reset inputs').count()==0
            assert page.locator('[data-mcel-notebook]').count() == 1
            assert all(call['method'] == 'GET' for call in page.evaluate('window.calls'))
            assert not errors, errors
        finally:
            browser.close()
