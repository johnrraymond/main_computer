"""Shared SLH target disclosures and persistent local identity, without remote admin grants.

The browser probe mounts production semantic HTML under isolated local fetch fakes.
There are no SLH service starts, certificates, or real network requests.
"""
from __future__ import annotations

import shutil

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page, ROUTES
from main_computer.mcel_slh_target_navigation import TARGET_ROUTES


def _mountable_browser(page):
    # Browser policy in some isolated environments prohibits HTTP navigation;
    # test production page mounts while simulating the same-tab storage boundary.
    page.evaluate('''()=>{
      const held={};Object.defineProperty(window,'sessionStorage',{configurable:true,
        value:{getItem:k=>held[k]||null,setItem:(k,v)=>{held[k]=v},removeItem:k=>{delete held[k]}}});
      window.networkCalls=[];window.nonIdentityLocalCalls=[];
      window.fetch=async(url,options={})=>{
        if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
          schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'dev-default',
          identities:[
            {deviceId:'dev-default',label:'Default identity',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'},
            {deviceId:'dev-alt',label:'Other identity',fingerprint:'b'.repeat(64),defaultStatus:'ADDITIONAL'}
          ],explanation:'Local keys discovered; administrator role not verified'})};
        if(url==='/api/applications/slh/network/inspect'){
          window.networkCalls.push({url,method:options.method,headers:options.headers,body:options.body});
          const q=JSON.parse(options.body);
          return {ok:true,json:async()=>({endpoint:q.address+':'+q.port,tlsSha256:q.tlsSha256,
            organizationState:'RUNNING',authenticationState:'NOT_AUTHENTICATED',
            observedAt:'2026-11-11T12:00:00Z',explanation:'Read-only pinned TLS inspection'})};
        }
        window.nonIdentityLocalCalls.push(url);
        throw Error('Unexpected local operation:'+url);
      };
    }''')


def _wait_identities(page):
    page.wait_for_function('''()=>window.__mcelSlh?.app.readState('state:local-identities')?.length===2''')


def test_compiled_source_has_one_app_two_navigation_regions_and_independent_states():
    ir,composition=compile_slh_application()
    assert ir['application']['appId']=='slh'
    assert {'state:selected-target','state:selected-local-identity','state:local-identities',
            'state:network-observations'} <= {s['id'] for s in ir['states']}
    assert {'intent:select-target','intent:choose-identity','intent:inspect-network-slh'} <= {
        i['id'] for i in ir['intents']}
    regions=[r for r in composition['regions'] if r['purpose']=='navigation']
    assert [r['id'] for r in regions]==[
        'interface-region:generated-navigation-observation-query',
        'interface-region:generated-navigation-identities']
    assert [m['ref'] for m in regions[0]['members']]==['surface-node:slh.network-slh-target']
    assert 'surface-node:slh.available-local-identities' in [m['ref'] for m in regions[1]['members']]
    assert ROUTES['/applications/slh/network']=='network'
    assert ROUTES['/applications/slh/network/workers']=='network-worker-bank'
    for url in TARGET_ROUTES.values():
        assert f'href="{url}"' in render_slh_page('network')


def test_local_and_network_have_same_semantic_application_but_different_disclosures():
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1200,'height':760})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            _mountable_browser(page)
            page.set_content(render_slh_page('network'))
            _wait_identities(page)
            assert page.locator('[data-mcel-semantic-operational="slh"]').count()==1
            assert page.locator('[data-slh-target=network]').get_attribute('aria-selected')=='true'
            assert page.locator('[data-slh-target-context=network]').count()==1
            assert page.locator('[data-slh-target-context=local]').count()==0
            assert page.locator('[data-mcel-region="interface-region:generated-navigation-observation-query"]:visible').count()==1
            assert page.locator('[data-mcel-region="interface-region:generated-navigation-identities"]:visible').count()==1
            assert page.locator('form[data-intent="intent:inspect-network-slh"]:visible').count()==1
            assert page.locator('button[data-intent="intent:login-administrator"]:visible').count()==0
            assert page.locator('[data-select-key="dev-default"]:visible').count()==1
            assert page.evaluate('window.__mcelSlh.app.readState("state:selected-local-identity")')=='dev-default'
            assert page.evaluate('window.nonIdentityLocalCalls')==[]
            # A new semantic route is a new mount, not a different app or identity store.
            page.set_content(render_slh_page('overview'))
            _wait_identities(page)
            assert page.locator('[data-mcel-semantic-operational="slh"]').count()==1
            assert page.locator('[data-slh-target-context=local]').count()==1
            assert page.get_by_text('Organization session not established',exact=True).count()==1
            assert page.locator('[data-slh-target-context=local]').get_by_text('About local administration').count()==1
            assert page.locator('form[data-intent="intent:inspect-network-slh"]:visible').count()==0
            assert page.locator('[data-select-key="dev-default"]:visible').count()==1
            assert errors==[],errors
        finally:
            browser.close()


def test_selected_identity_survives_target_change_and_remote_pin_stays_distinct():
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1200,'height':760})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            _mountable_browser(page)
            page.set_content(render_slh_page('overview'))
            _wait_identities(page)
            page.locator('[data-select-key="dev-alt"]').click()
            page.wait_for_function('''()=>window.__mcelSlh.app.readState('state:selected-local-identity')==='dev-alt' ''')
            page.evaluate('window.dispatchEvent(new Event("pagehide"))')
            page.evaluate('window.nonIdentityLocalCalls=[]')
            page.set_content(render_slh_page('network'))
            _wait_identities(page)
            assert page.evaluate('window.__mcelSlh.app.readState("state:selected-local-identity")')=='dev-alt'
            assert page.evaluate('window.nonIdentityLocalCalls')==[]
            form=page.locator('form[data-intent="intent:inspect-network-slh"]')
            form.locator('input[name="address"]').fill('192.168.1.20')
            form.locator('input[name="tlsSha256"]').fill('f'*64)
            form.locator('button[type="submit"]').click()
            page.wait_for_function('''()=>window.__mcelSlh.app.readState('state:network-observations')?.length===1''')
            assert page.evaluate('window.__mcelSlh.app.readState("state:selected-local-identity")')=='dev-alt'
            assert page.evaluate('window.__mcelSlh.app.readState("state:network-observations")[0].tlsSha256')=='f'*64
            requests=page.evaluate('window.networkCalls')
            assert len(requests)==1 and requests[0]['method']=='POST'
            assert not any(key.lower()=='authorization' for key in requests[0]['headers'])
            assert 'NOT_AUTHENTICATED' in page.locator('#mcel-workbench').inner_text()
            assert errors==[],errors
        finally:
            browser.close()


def test_network_explicitly_refuses_local_setup_even_when_called_from_app_api():
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page()
            _mountable_browser(page)
            page.set_content(render_slh_page('network'))
            _wait_identities(page)
            assert page.evaluate('''async()=>{
              try {await window.__mcelSlh.app.execute('intent:login-administrator');return false}
              catch(error) {return String(error.message).includes('unknown-intent')}
            }''')
            assert page.evaluate('window.nonIdentityLocalCalls')==[]
        finally:
            browser.close()
