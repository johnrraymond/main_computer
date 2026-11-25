"""Real Chromium acceptance of Worker's three-view navigation and Hub radio choice.

Uses the actual generated MCEL application and capability port. Only the local
server response is stubbed; all selection and typed operation code is real.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from main_computer.mcel_worker_application import compile_worker_application, render_worker_page

ROOT = Path(__file__).resolve().parents[1]

MOCK_LOCAL_BACKEND = r'''() => {
  window.workerRequests=[]; window.workerFailNextHubWrite=false;
  const state={network:'testnet',hubChoice:'testnet',ring:3,hubUrl:'https://configured.example:8899',
    sellerEnabled:true,availabilityMode:'ai_idle',remoteEnabled:false,remoteMode:'never'};
  window.fetch=async (url,opts={}) => {
    const address=String(url), method=opts.method||'GET';
    window.workerRequests.push({address,method,body:opts.body?JSON.parse(opts.body):null});
    if(method==='POST'){
      const body=JSON.parse(opts.body);
      if(address.endsWith('/network-session')){
        state.network=body.network;
        state.ring=Number(body.requested_ring);
      }else if(address.endsWith('/settings')){
        if(body.changed_fields.includes('mcelHubChoice')){
          if(window.workerFailNextHubWrite){
            window.workerFailNextHubWrite=false;
            return {ok:false,status:500,json:async()=>({ok:false})};
          }
          state.hubChoice=body.mcelHubChoice;
        }
        if(body.changed_fields.includes('managedSLHUrl'))state.hubUrl=body.managedSLHUrl;
        if(body.changed_fields.includes('sellerEnabled')){
          state.sellerEnabled=body.settings.sellerEnabled;
          state.availabilityMode=body.settings.sellerAvailabilityMode;
          state.remoteEnabled=body.settings.remoteEnabled;
          state.remoteMode=body.settings.remoteMode;
        }
      }else throw Error('unexpected write '+address);
      return {ok:true,status:200,json:async()=>({ok:true})};
    }
    if(!address.endsWith('/mcel-observation'))throw Error('unexpected read '+address);
    return {ok:true,status:200,json:async()=>({
      ok:true,schema:'mcel.worker.observation.v1',
      destinations:[['none','None'],['mainnet','Mainnet'],['testnet','Testnet'],
        ['test','Local Test'],['dev','Local Dev'],['slh','SLH/SEH']]
        .map(([network,label])=>({network,label})),
      hubChoice:state.hubChoice,
      destination:{selectedNetwork:state.network,connectionStatus:'disconnected',hub:'',chainId:'',
        requestedRing:state.ring,assignedRing:'',workerId:'',explanation:'',
        slhEnrollment:state.network==='slh'?'NOT_VERIFIED':'NOT_APPLICABLE'},
      slhTarget:{hubUrl:state.hubUrl,authenticationState:'NOT_VERIFIED'},
      runtime:{state:'READY',phase:'not_accepting',reason:'',activeJobs:0,allowedToAccept:false},
      policy:{sellerEnabled:state.sellerEnabled,availabilityMode:state.availabilityMode,
        remoteEnabled:state.remoteEnabled,remoteMode:state.remoteMode}
    })};
  };
}'''


def test_semantic_navigation_and_target_contract():
    ir, plan = compile_worker_application(ROOT)
    intents = {i['id']: i for i in ir['intents']}
    assert set(intents) >= {'intent:switch-view', 'intent:choose-network',
                            'intent:select-destination', 'intent:save-slh-target'}
    assert intents['intent:switch-view']['risk'] == 'local-state'
    assert intents['intent:choose-network']['risk'] == 'local-state'
    assert intents['intent:save-slh-target']['risk'] == 'consequential'
    assert intents['intent:commit-hub-choice']['confirmationRequired'] is False
    assert intents['intent:select-destination']['confirmationRequired'] is True
    assert not any(n.get('nodeKind') == 'form' and n.get('submitLabel') == 'Apply Hub choice'
                   for n in ir['surfaces'][0]['nodes'])
    nav, = (node for node in ir['surfaces'][0]['nodes'] if node['id'] ==
            'surface-node:worker.navigation-hub-worker-requester')
    assert nav['navigationSubtitle']['state']['ref'] == 'state:selected-network'
    assert nav['navigationSubtitle']['forValue'] == 'hub-choice'
    assert any(n.get('selectionPresentation') == 'radio' for n in ir['surfaces'][0]['nodes'])
    assert [r['recipe'] for r in plan['regions']].index('navigation') < [r['recipe'] for r in plan['regions']].index('collection')
    assert ir['layouts'] == []


def test_hub_choices_and_slh_target_are_separate_from_navigation_and_authorization():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        chrome = shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser = driver.chromium.launch(headless=True, **({'executable_path': chrome} if chrome else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page = browser.new_page(viewport={'width': 740, 'height': 440})
            page.set_default_timeout(4000)

            errors=[]
            page.on('pageerror',lambda exc: errors.append(str(exc)))
            page.on('dialog',lambda dialog: dialog.accept())
            page.evaluate(MOCK_LOCAL_BACKEND)

            page.set_content(render_worker_page(ROOT))
            page.wait_for_function('''() => window.__mcelWorker?.app.readState('state:selected-network')==='testnet' ''')
            nav=page.locator('[data-mcel-purpose="navigation"] [data-mcel-mode-tabs] button')
            assert [button.inner_text() for button in nav.all()] == ['Hub Choice\nTestnet','Worker','Requester']
            assert page.get_by_role('button',name='Apply Hub choice').count()==0
            radios=page.locator('[data-mcel-radio-choices] input[type="radio"]')
            assert [radio.input_value() for radio in radios.all()] == ['none','mainnet','testnet','test','dev','slh']
            assert radios.nth(2).is_checked()
            assert page.locator('#worker-multisession-card').is_visible()
            assert not page.locator('input[name="hubUrl"]').is_visible()
            assert all(req['method']=='GET' for req in page.evaluate('window.workerRequests'))

            page.locator('[data-mcel-radio-choices] input[value="slh"]').check()
            assert nav.nth(0).inner_text()=='Hub Choice\nSLH/SEH'
            assert not page.locator('#worker-multisession-card').is_visible()
            page.wait_for_function('''() => window.workerRequests.some(r=>r.method==='POST' &&
              r.address.endsWith('/settings') && r.body.mcelHubChoice==='slh')''')
            page.wait_for_function('''() => window.__mcelWorker?.app.readState('state:slh-target')?.hubUrl==='https://configured.example:8899' ''')
            assert nav.nth(0).inner_text()=='Hub Choice\nSLH/SEH'
            writes=page.evaluate('window.workerRequests.filter(r=>r.method==="POST")')
            assert len(writes)==1
            assert writes[0]['body']=={'mcelHubChoice':'slh','changed_fields':['mcelHubChoice']}
            assert not any(req['method']=='POST' and req['address'].endswith('/network-session') for req in page.evaluate('window.workerRequests'))
            assert page.locator('input[name="hubUrl"]').is_visible()
            assert page.locator('input[name="hubUrl"]').input_value()=='https://configured.example:8899'
            # Radio choice persists without changing Worker runtime network session.
            # Invalid/unverified endpoints cannot reach the settings mutation.
            invalid = page.evaluate('''async () => await window.__mcelWorker.app.execute(
              'intent:save-slh-target',{hubUrl:'http://insecure.invalid'})''')
            assert invalid['disposition'] != 'completed'
            assert len(page.evaluate('window.workerRequests.filter(r=>r.method==="POST")'))==1
            page.locator('input[name="hubUrl"]').fill('https://new-hub.example:8899')

            page.get_by_role('button',name='Save SLH/SEH target').click()

            page.wait_for_function('''() => window.__mcelWorker.app.readState('state:slh-target')?.hubUrl==='https://new-hub.example:8899' ''')
            writes=page.evaluate('window.workerRequests.filter(r=>r.method==="POST")')
            assert len(writes)==2
            assert writes[1]['body']=={'managedSLHUrl':'https://new-hub.example:8899','changed_fields':['managedSLHUrl']}
            # Navigation preserves the Hub choice without a duplicate write.

            page.locator('[data-mcel-purpose="navigation"] [data-value="worker"]').click()
            page.wait_for_function('''() => window.__mcelWorker.app.readState('state:policy')?.availabilityMode==='ai_idle' ''')
            assert page.locator('input[name="sellerEnabled"]').is_visible()
            assert not page.locator('input[name="hubUrl"]').is_visible()
            page.locator('[data-mcel-purpose="navigation"] [data-value="requester"]').click()
            assert not page.locator('input[name="sellerEnabled"]').is_visible()
            assert page.evaluate("window.__mcelWorker.app.readState('state:worker-view')")=='requester'
            page.locator('[data-mcel-purpose="navigation"] [data-value="hub-choice"]').click()
            assert page.locator('[data-mcel-radio-choices] input[value="slh"]').is_checked()
            assert len([req for req in page.evaluate('window.workerRequests')
                        if req['method']=='POST' and req['address'].endswith('/network-session')])==0
            assert nav.nth(0).inner_text()=='Hub Choice\nSLH/SEH'
            # A refused backend mutation restores the authoritative choice.
            page.evaluate('window.workerFailNextHubWrite=true')
            page.locator('[data-mcel-radio-choices] input[value="mainnet"]').click()
            page.wait_for_function('''() => document.querySelector('#mcel-workbench-status')
              ?.textContent.includes('Hub change refused') ''')
            assert page.locator('[data-mcel-radio-choices] input[value="slh"]').is_checked()
            assert nav.nth(0).inner_text()=='Hub Choice\nSLH/SEH'
            assert errors==[],errors
        finally:
            browser.close()


def test_worker_ring_config_remains_separate_from_automatic_hub_selection():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        chrome = shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser = driver.chromium.launch(headless=True, **({'executable_path': chrome} if chrome else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page = browser.new_page(viewport={'width': 740, 'height': 440})
            page.on('dialog', lambda dialog: dialog.accept())
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.evaluate(MOCK_LOCAL_BACKEND)
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function("() => window.__mcelWorker?.app.readState('state:selected-network')==='testnet'")
            assert page.get_by_role('button', name='Apply Hub choice').count() == 0
            assert not page.locator('input[name="requestedRing"]').is_visible()
            page.locator('[data-mcel-purpose="navigation"] [data-value="worker"]').click()
            assert page.locator('input[name="requestedRing"]').is_visible()
            assert page.get_by_role('button',name='Update worker ring').is_visible()
            page.locator('input[name="requestedRing"]').fill('2')
            page.get_by_role('button',name='Update worker ring').click()
            page.wait_for_function('''() => window.workerRequests.some(r=>r.method==='POST' &&
              r.address.endsWith('/network-session') && r.body.network==='testnet' &&
              r.body.requested_ring==='2')''')
            page.wait_for_function('''() => window.__mcelWorker.app.readState('state:destination')?.requestedRing===2''')
            assert page.locator('[data-mcel-purpose="navigation"] [data-value="hub-choice"]').inner_text() == 'Hub Choice\nTestnet'
            assert errors == [], errors
        finally:
            browser.close()


def test_rapid_radio_changes_commit_last_choice_without_duplicate_concurrent_mutations():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        chrome = shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser = driver.chromium.launch(headless=True, **({'executable_path': chrome} if chrome else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page = browser.new_page(viewport={'width': 740, 'height': 440})
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.evaluate(MOCK_LOCAL_BACKEND)
            page.evaluate('''() => {
              const normal=window.fetch;
              window.fetch=async (url,opts={}) => {
                if(String(url).endsWith('/settings') && opts.method==='POST')
                  await new Promise(resolve=>setTimeout(resolve,150));
                return normal(url,opts);
              };
            }''')
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function("() => window.__mcelWorker?.app.readState('state:selected-network')==='testnet'")
            page.evaluate('''() => {
              document.querySelector('input[type="radio"][value="mainnet"]').click();
              document.querySelector('input[type="radio"][value="slh"]').click();
            }''')
            page.wait_for_function('''() => window.workerRequests.some(r=>
              r.method==='POST' && r.address.endsWith('/settings') &&
              r.body.mcelHubChoice==='slh')''')
            page.wait_for_timeout(300)  # allow any queued operation to settle
            assert page.evaluate("window.__mcelWorker.app.readState('state:selected-network')") == 'slh'
            assert page.locator('[data-mcel-purpose="navigation"] [data-value="hub-choice"]').inner_text() == 'Hub Choice\nSLH/SEH'
            writes = page.evaluate("window.workerRequests.filter(r=>r.method==='POST').map(r=>r.body.mcelHubChoice)")
            assert writes in (['slh'], ['mainnet','slh']), writes
            assert errors == [], errors
        finally:
            browser.close()


def test_none_to_mainnet_persists_and_wallet_panel_stays_inside_hub_workarea():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        chrome = shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser = driver.chromium.launch(headless=True, **({'executable_path': chrome} if chrome else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page = browser.new_page(viewport={'width': 740, 'height': 440})
            page.set_default_timeout(4000)
            page.evaluate(MOCK_LOCAL_BACKEND.replace(
                "network:'testnet',hubChoice:'testnet'", "network:'none',hubChoice:'none'"))
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function(
                "() => window.__mcelWorker?.app.readState('state:selected-network')==='none'")
            nav = page.locator('[data-mcel-purpose="navigation"] [data-value="hub-choice"]')
            assert nav.inner_text() == 'Hub Choice\nNone'
            page.locator('[data-mcel-radio-choices] input[value="mainnet"]').check()
            assert nav.inner_text() == 'Hub Choice\nMainnet'
            page.wait_for_function('''() => window.workerRequests.some(r =>
                r.method==='POST' && r.body?.mcelHubChoice==='mainnet') &&
                window.__mcelWorker.app.readState('state:selected-network')==='mainnet' &&
                window.__mcelWorker.app.readState('state:saved-hub-choice')==='mainnet' ''')
            page.wait_for_timeout(100)
            assert page.locator('input[value="mainnet"]').is_checked()
            assert nav.inner_text() == 'Hub Choice\nMainnet'
            wallet = page.locator('#mcel-wallet-staging')
            assert wallet.is_visible()
            assert wallet.evaluate('''node => Boolean(node.closest('[data-mcel-region]')) &&
                Boolean(node.closest('#mcel-workbench'))''')
            assert not wallet.evaluate("node => node.parentElement === document.body")
            assert not page.locator('.mcel-worker-hub-selection-status').get_attribute('data-error') == 'true'
            assert errors == [], errors
        finally:
            browser.close()


def test_refused_hub_choice_has_visible_diagnostic_in_hub_workarea():
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        chrome = shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser = driver.chromium.launch(headless=True, **({'executable_path': chrome} if chrome else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page = browser.new_page(viewport={'width': 740, 'height': 440})
            page.set_default_timeout(4000)
            page.evaluate(MOCK_LOCAL_BACKEND.replace(
                "network:'testnet',hubChoice:'testnet'", "network:'none',hubChoice:'none'"))
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function(
                "() => window.__mcelWorker?.app.readState('state:selected-network')==='none'")
            page.evaluate('window.workerFailNextHubWrite=true')
            page.locator('[data-mcel-radio-choices] input[value="mainnet"]').click()
            page.wait_for_function('''() => document.querySelector('.mcel-worker-hub-selection-status')
                ?.textContent.includes('Hub change refused')''')
            assert page.locator('input[value="none"]').is_checked()
            diagnostic = page.locator('.mcel-worker-hub-selection-status')
            assert diagnostic.is_visible()
            assert diagnostic.get_attribute('data-error') == 'true'
            assert diagnostic.inner_text().startswith('Hub change refused; previous choice retained:')
        finally:
            browser.close()


def test_radio_collection_keeps_full_focus_outline_inside_scrollport():
    """A focused edge choice must not lose its gold outline to overflow:auto."""
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        chrome = shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser = driver.chromium.launch(headless=True, **({'executable_path': chrome} if chrome else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            for width in (740, 1100):
                page = browser.new_page(viewport={'width': width, 'height': 850})
                page.evaluate(MOCK_LOCAL_BACKEND)
                page.set_content(render_worker_page(ROOT))
                page.wait_for_function("window.__mcelWorker?.app.readState('state:selected-network')==='testnet'")
                for value in ('none', 'slh'):
                    radio = page.locator(f'[data-mcel-radio-choices] input[value="{value}"]')
                    radio.focus()
                    evidence = radio.evaluate('''input => {
                        const row = input.closest('[data-mcel-radio-choice]');
                        const grid = row.parentElement;
                        const r = row.getBoundingClientRect(), g = grid.getBoundingClientRect();
                        const s = getComputedStyle(row);
                        return {extent: parseFloat(s.outlineWidth) + parseFloat(s.outlineOffset),
                            style: s.outlineStyle,
                            clearance: [r.left-g.left-grid.clientLeft, r.top-g.top-grid.clientTop,
                                g.left+grid.clientLeft+grid.clientWidth-r.right,
                                g.top+grid.clientTop+grid.clientHeight-r.bottom]};
                    }''')
                    assert evidence['style'] == 'solid', evidence
                    assert evidence['extent'] > 0, evidence
                    assert min(evidence['clearance']) >= evidence['extent'] - 0.05, evidence
                page.close()
        finally:
            browser.close()


def test_selected_radio_has_complete_perimeter_without_focus():
    """Selection owns all four edges; record separators must not break the frame."""
    pw = pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as driver:
        chrome = shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser = driver.chromium.launch(headless=True, **({'executable_path': chrome} if chrome else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page = browser.new_page(viewport={'width': 1100, 'height': 850})
            page.evaluate(MOCK_LOCAL_BACKEND)
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function("window.__mcelWorker?.app.readState('state:selected-network')==='testnet'")
            group = page.locator('[data-mcel-radio-choices]')
            for value in ('testnet', 'slh', 'none', 'mainnet', 'test', 'dev'):
                radio = group.locator(f'input[value="{value}"]')
                if not radio.is_checked():
                    radio.check()
                    page.wait_for_function('''value => window.__mcelWorker.app.readState('state:saved-hub-choice')===value''', arg=value)
                page.evaluate('document.activeElement.blur()')
                evidence = group.locator('[data-mcel-radio-choice]').evaluate_all('''rows => rows.map(row => {
                    const s = getComputedStyle(row), input = row.querySelector('input');
                    const sides = ['Top', 'Right', 'Bottom', 'Left'];
                    const probe = document.createElement('span');
                    probe.style.color = 'var(--mcel-color-accent,#68b8c9)';
                    row.append(probe);
                    const accent = getComputedStyle(probe).color;
                    probe.remove();
                    return {value: input.value, checked: input.checked, selected: row.getAttribute('aria-selected'),
                        focused: row.matches(':focus-within'), accent,
                        colors: sides.map(side => s['border'+side+'Color']),
                        widths: sides.map(side => parseFloat(s['border'+side+'Width'])),
                        styles: sides.map(side => s['border'+side+'Style'])};
                })''')
                assert [row['value'] for row in evidence if row['checked']] == [value]
                for row in evidence:
                    assert row['selected'] == str(row['checked']).lower(), row
                    assert not row['focused'], row
                    assert len(set(row['colors'])) == 1, row
                    assert len(set(row['widths'])) == 1 and row['widths'][0] > 0, row
                    assert row['styles'] == ['solid'] * 4, row
                    if row['checked']:
                        assert row['colors'] == [row['accent']] * 4, row
                    else:
                        assert row['colors'][0] != row['accent'], row
        finally:
            browser.close()
