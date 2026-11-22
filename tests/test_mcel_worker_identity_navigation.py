"""Native Worker identity selection uses the same read-only local LMS inventory as SLH.

Identity selection is not remote authentication, enrollment, or administrator approval.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from main_computer.mcel_worker_application import compile_worker_application, render_worker_page
from tests.test_mcel_worker_hub_navigation import MOCK_LOCAL_BACKEND

ROOT = Path(__file__).resolve().parents[1]

IDENTITIES_MOCK = r'''() => {
  window.identityReadCount=0;
  window.identityUnavailable=false;
  const oldFetch=window.fetch;
  window.fetch=async (url,options={})=>{
    if(String(url)==='/api/applications/slh/identities'){
      window.identityReadCount++;
      const available=!window.identityUnavailable;
      return {ok:true,status:200,json:async()=>({
        schema:'mcel.slh.identities.v4b',state:available?'AVAILABLE':'UNAVAILABLE',
        defaultDeviceId:available?'device-001':'',
        explanation:available?'':'LMS identity directory unavailable',
        identities:available?[
          {deviceId:'device-001',label:'Default identity',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'},
          {deviceId:'device-002',label:'Identity Test 001',fingerprint:'b'.repeat(64),defaultStatus:'ADDITIONAL'}
        ]:[]})};
    }
    return oldFetch(url,options);
  };
}'''


def test_identity_semantics_preserve_worker_authority():
    ir, _ = compile_worker_application(ROOT)
    states = {s['id']: s for s in ir['states']}
    assert states['state:selected-local-identity']['authority'] == 'renderer-local'
    surface, = ir['surfaces']
    nodes = {n['id']: n for n in surface['nodes']}
    identities = nodes['surface-node:worker.worker-local-identities']
    assert identities['nodeKind'] == 'collection'
    assert identities['fields'] == ['label']
    assert identities['selectionIntent'] == {'ref':'intent:choose-identity'}
    assert identities.get('credentialDisclosure') is None
    assert nodes['surface-node:worker.slh-selected-local-identity']['visibleWhen']
    intentions = {i['id']:i for i in ir['intents']}
    assert intentions['intent:choose-identity']['risk']=='local-state'
    for action in ('refresh-identities', 'refresh-identity-status'):
        assert intentions['intent:'+action]['risk']=='external-read'
    assert not any('enroll' in x['id'] or 'admin-approve' in x['id'] for x in ir['intents'])
    assert {c['id'] for c in ir['capabilities']} == {
        'capability:worker-observer', 'capability:worker-hub-choice-control',
        'capability:worker-destination-control', 'capability:worker-policy-control',
        'capability:worker-slh-target-control', 'capability:worker-marketplace-observer',
        'capability:worker-marketplace-settings', 'capability:worker-marketplace-actions'
    }


def test_worker_identity_inventory_is_guarded_and_separate_from_worker_observation():
    code=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-worker-ports.js').read_text()
    assert '/api/applications/slh/identities' in code
    assert 'credentials:"same-origin"' in code
    assert 'identity-directory-unavailable' in code
    assert 'identity-directory-' in code
    assert 'fingerprint:row.fingerprint' not in code
    assert 'result.identities.map(row=>({' in code
    assert 'accessStatus:"SLH/SEH access not verified"' in code
    route=(ROOT/'main_computer/viewport_route_dispatch.py').read_text()
    assert 'route_path == "/api/applications/slh/identities"' in route
    assert '_hard_halt_client_is_local(self)' in route


def test_browser_identity_choice_visible_in_worker_navigation_and_not_an_enrollment():
    pw=pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        chrome=shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser=driver.chromium.launch(headless=True,**({'executable_path':chrome} if chrome else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page=browser.new_page(viewport={'width':740,'height':520})
            page.set_default_timeout(8000)
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('dialog',lambda d:d.accept())
            page.evaluate(MOCK_LOCAL_BACKEND)
            page.evaluate(IDENTITIES_MOCK)
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function("() => window.__mcelWorker?.app.readState('state:local-identities')?.length===2")
            rail=page.locator('[data-mcel-zone="navigator"]')
            names=rail.locator('[data-mcel-node="surface-node:worker.worker-local-identities"] button[data-select-key]')
            assert names.all_text_contents()==['Default identity','Identity Test 001']
            assert rail.locator('[data-mcel-mode-tabs] button').all_text_contents()==[
                'Hub ChoiceTestnet','Worker','Requester'] or len(rail.locator('[data-mcel-mode-tabs] button').all())==3
            assert 'fingerprint' not in rail.inner_text().lower()
            assert 'For Admin Use Only' not in page.content()
            names.nth(1).click()
            page.wait_for_function("() => window.__mcelWorker.app.readState('state:selected-local-identity')==='device-002'")
            stored=page.evaluate("() => { try { return sessionStorage.getItem('mcel.worker.selected-identity.v1'); } catch(_) { return null; } }")
            assert stored in (None,'device-002')
            assert not any(r['method']=='POST' for r in page.evaluate('window.workerRequests'))
            # Selecting the SLH radio displays identity context but does not
            # create a session, enrollment request, or administrative approval.
            page.locator('[data-mcel-radio-choices] input[value="slh"]').check()
            page.wait_for_function("() => window.__mcelWorker.app.readState('state:selected-network')==='slh'")
            assert page.locator('[data-mcel-node="surface-node:worker.slh-selected-local-identity"]').is_visible()
            assert page.locator('[data-mcel-node="surface-node:worker.slh-selected-local-identity"]').inner_text().find('device-002')>=0
            writes=page.evaluate('window.workerRequests.filter(r=>r.method===\'POST\')')
            assert all(r['address'].endswith('/settings') and r['body'].get('changed_fields')==['mcelHubChoice'] for r in writes)
            assert errors==[],errors
            # No horizontal overflow from the added identity selector.
            bounds=rail.evaluate('e=>({rail:e.getBoundingClientRect().right,child:e.querySelector("[data-collection]").getBoundingClientRect().right})')
            assert bounds['child']<=bounds['rail']+1
        finally:
            browser.close()


def test_unavailable_identity_directory_is_not_presented_as_empty_verified_inventory():
    pw=pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        chrome=shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser=driver.chromium.launch(headless=True,**({'executable_path':chrome} if chrome else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page=browser.new_page()
            page.evaluate(MOCK_LOCAL_BACKEND)
            page.evaluate(IDENTITIES_MOCK)
            page.evaluate('window.identityUnavailable=true')
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function('() => window.__mcelWorker?.app && window.identityReadCount>=2')
            assert page.evaluate("window.__mcelWorker.app.readState('state:local-identity-status')?.state")=='UNAVAILABLE'
            assert page.locator('[data-mcel-node="surface-node:worker.worker-local-identities"] [data-record-key]').count()==0
            assert not any(r['method']=='POST' for r in page.evaluate('window.workerRequests'))
        finally:
            browser.close()


def test_worker_navigation_uses_separate_slh_style_identity_region_with_contained_details():
    ir, composition = compile_worker_application(ROOT)
    navigation = [r for r in composition['regions'] if r['recipe'] == 'navigation']
    assert len(navigation) == 2
    assert 'app-navigation' in navigation[0]['id']
    assert 'identities' in navigation[1]['id']
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        chrome = shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser = driver.chromium.launch(headless=True,**({'executable_path':chrome} if chrome else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            for width in (740,1120):
                page = browser.new_page(viewport={'width':width,'height':520})
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('dialog',lambda d:d.accept())
                page.evaluate(MOCK_LOCAL_BACKEND)
                page.evaluate(IDENTITIES_MOCK)
                page.set_content(render_worker_page(ROOT))
                page.wait_for_function('() => window.__mcelWorker?.app.readState("state:local-identities")?.length === 2')
                nav=page.locator('[data-mcel-zone="navigator"] > [data-mcel-purpose="navigation"]')
                assert nav.count()==2
                assert nav.nth(0).locator('h2').inner_text().lower()=='app navigation'
                assert nav.nth(1).locator('h2').inner_text().lower()=='identities'
                assert nav.nth(0).locator('[data-mcel-mode-tabs] button').count()==3
                entries=nav.nth(1).locator('[data-record-key]')
                assert entries.count()==2
                assert entries.nth(0).locator('summary').inner_text()=='More details'
                entries.nth(0).locator('summary').click()
                assert entries.nth(0).locator('[data-mcel-detail-fields]').is_visible()
                assert 'SLH/SEH access not verified' in entries.nth(0).inner_text()
                assert 'fingerprint' not in nav.nth(1).inner_text().lower()
                assert 'Passkey' not in nav.nth(1).inner_text()
                assert 'For Admin Use Only' not in page.content()
                boundary=nav.nth(1).evaluate('e=>{const p=e.getBoundingClientRect();const d=e.querySelector("[data-mcel-inline-details]").getBoundingClientRect();return {left:p.left,right:p.right,dl:d.left,dr:d.right}}')
                assert boundary['dl']>=boundary['left']-1,boundary
                assert boundary['dr']<=boundary['right']+1,boundary
                entries.nth(1).locator('button[data-select-key]').click()
                page.wait_for_function('() => window.__mcelWorker.app.readState("state:selected-local-identity")==="device-002"')
                assert errors==[],errors
                page.close()
        finally:
            browser.close()
