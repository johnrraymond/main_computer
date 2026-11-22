"""Browser proof for the native Worker presentation, not a static mock DOM.

The rendered page is compiled from its real semantic source and shares the
production MCEL renderer. Only backend observation is mocked; the mount, form
hydration, selection and responsive layout are exercised in Chromium.
"""
from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from main_computer.mcel_worker_application import render_worker_page

ROOT = Path(__file__).resolve().parents[1]

OBSERVATION_STUB = r'''() => {
  window.workerVisualRequests = [];
  let selectedNetwork='none',hubChoice='none';
  window.fetch=async (url, options={}) => {
    window.workerVisualRequests.push({url:String(url),method:options.method||'GET'});
    if(String(url).endsWith('/api/applications/worker/settings') && options.method==='POST'){
      const body=JSON.parse(options.body); hubChoice=body.mcelHubChoice;
      return {ok:true,status:200,json:async()=>({ok:true})};
    }
    if(!String(url).endsWith('/api/applications/worker/mcel-observation'))
      throw Error('Unexpected Worker request: '+url);
    return {ok:true,status:200,json:async()=>({
      ok:true,schema:'mcel.worker.observation.v1',
      destinations:[
        {network:'none',label:'None'},
        {network:'mainnet',label:'Mainnet'},
        {network:'testnet',label:'Testnet'},
        {network:'test',label:'Local Test'},
        {network:'dev',label:'Local Dev'},
        {network:'slh',label:'SLH/SEH'},
      ],
      hubChoice,
      destination:{selectedNetwork,connectionStatus:'disconnected',hub:'',chainId:'',
        requestedRing:3,assignedRing:'',workerId:'',explanation:'',slhEnrollment:selectedNetwork==='slh'?'NOT_VERIFIED':'NOT_APPLICABLE'},
      slhTarget:{hubUrl:'https://slh.example:8899',authenticationState:'NOT_VERIFIED'},
      runtime:{state:'READY',phase:'not_accepting',reason:'',activeJobs:0,allowedToAccept:false},
      policy:{sellerEnabled:true,availabilityMode:'ai_idle',remoteEnabled:false,remoteMode:'ask-when-busy'}
    })};
  };
}'''


def test_worker_layout_preserves_navigation_radio_semantics_and_responsive_structure():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as driver:
        browser_path = shutil.which('chromium') or shutil.which('chromium-browser')
        try:
            browser = driver.chromium.launch(headless=True, **(
                {'executable_path': browser_path} if browser_path else {}))
        except Exception as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        try:
            page = browser.new_page(viewport={'width': 740, 'height': 370})
            errors = []
            page.on('pageerror', lambda exc: errors.append(str(exc)))
            page.evaluate(OBSERVATION_STUB)
            page.set_content(render_worker_page(ROOT))
            page.wait_for_function("() => window.__mcelWorker?.app.readState('state:destinations')?.length===6")
            nav = page.locator('[data-mcel-purpose="navigation"] [data-mcel-mode-tabs] button')
            assert [item.inner_text() for item in nav.all()] == ['Hub Choice\nNone', 'Worker', 'Requester']
            radios = page.locator('[data-mcel-radio-choices] input[type="radio"]')
            assert radios.count() == 6
            assert [r.input_value() for r in radios.all()] == ['none','mainnet','testnet','test','dev','slh']
            assert radios.nth(0).is_checked()
            assert page.locator('input[name="sellerEnabled"]').is_visible() is False
            assert page.locator('input[name="hubUrl"]').is_visible() is False
            assert all(req['method'] == 'GET' for req in page.evaluate('window.workerVisualRequests'))

            compact = page.evaluate('''() => {
              const nav=document.querySelector('[data-mcel-natural-scroller="navigation"]');
              const work=document.querySelector('[data-mcel-natural-scroller="workspace"]');
              const inspector=document.querySelector('[data-mcel-natural-scroller="inspection"]');
              return {inspectorFlows:inspector.parentElement===work,
                inspectorScroll:getComputedStyle(inspector).overflowY,
                workspaceHeight:work.clientHeight,
                documentOverflow:document.documentElement.scrollWidth-innerWidth,
                firstRegion:work.querySelector(':scope > [data-mcel-region]')?.getAttribute('data-mcel-purpose'),
                navTravel:nav.scrollHeight-nav.clientHeight};
            }''')
            assert compact['inspectorFlows'] is True, compact
            assert compact['inspectorScroll'] == 'visible', compact
            assert compact['workspaceHeight'] > 300, compact
            assert compact['documentOverflow'] <= 1, compact
            assert compact['firstRegion'] == 'workspace', compact
            assert page.locator('[data-mcel-radio-choices]').is_visible()
            assert compact['navTravel'] <= 1, compact

            radios.nth(5).check()
            page.wait_for_function("() => window.workerVisualRequests.some(r=>r.method==='POST')")
            page.wait_for_function("() => window.__mcelWorker.app.readState('state:slh-target')?.hubUrl==='https://slh.example:8899'")
            assert nav.nth(0).inner_text()=='Hub Choice\nSLH/SEH'
            assert page.locator('input[name="hubUrl"]').is_visible()
            assert page.locator('input[name="hubUrl"]').input_value() == 'https://slh.example:8899'
            page.locator('[data-mcel-purpose="navigation"] [data-value="worker"]').click()
            page.wait_for_function("() => window.__mcelWorker.app.readState('state:policy')?.availabilityMode==='ai_idle'")
            assert page.locator('input[name="sellerEnabled"]').is_checked()
            assert page.locator('input[name="remoteEnabled"]').is_visible() is False
            assert page.locator('select[name="availabilityMode"]').input_value() == 'ai_idle'
            page.locator('[data-mcel-purpose="navigation"] [data-value="requester"]').click()
            assert page.locator('input[name="remoteEnabled"]').is_visible()
            page.locator('[data-mcel-purpose="navigation"] [data-value="worker"]').click()

            page.set_viewport_size({'width': 1280, 'height': 720})
            page.wait_for_function('''() => document.querySelector('[data-mcel-natural-scroller="inspection"]')
               ?.parentElement?.hasAttribute('data-mcel-natural-layout')''')
            page.set_viewport_size({'width': 740, 'height': 370})
            page.wait_for_function('''() => document.querySelector('[data-mcel-natural-scroller="inspection"]')
               ?.parentElement?.getAttribute('data-mcel-zone')==='workspace' ''')
            page.locator('[data-mcel-purpose="navigation"] [data-value="hub-choice"]').click()
            assert page.locator('[data-mcel-radio-choices] input[value="slh"]').is_checked()
            assert len([r for r in page.evaluate('window.workerVisualRequests') if r['method']=='POST'])==1
            assert errors == [], errors
        finally:
            browser.close()
