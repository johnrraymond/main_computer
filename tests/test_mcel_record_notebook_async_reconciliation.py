"""Shared record-notebook reconciliation, exercised in a real Chromium renderer.

Every server reply here is a mocked boundary: no live SLH is contacted or mutated.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page


MOCK = r'''() => {
  window.notebookCalls=[];
  window.pendingResponses=[];
  window.delayPending=false;
  window.pendingReject=false;
  window.identity='one';
  window.lifecycleState='running';
  window.fetch=async(url,opts={})=>{
    const body=opts.body?JSON.parse(opts.body):{};
    const op=body.operation||body.action||'';
    const reply=(result,ok=true)=>({ok,json:async()=>result});
    if(url==='/api/applications/slh/identities')return reply({
      schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'one',
      identities:[{deviceId:'one',label:'One',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'},
        {deviceId:'two',label:'Two',fingerprint:'b'.repeat(64),defaultStatus:'ADDITIONAL'}],explanation:''});
    if(url==='/api/applications/slh/observe?view=overview')return reply({
      ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
      overview:{organizationState:window.lifecycleState,organizationEndpoint:'127.0.0.1:8899'}});
    if(url==='/api/applications/slh/observe?view=workers')return reply({
      schema:'mcel.slh.first-slice-observation.v1',view:'workers',
      workerSummary:{observationState:'AUTHENTICATED'}});
    if(url==='/api/applications/slh/setup/action'){
      window.notebookCalls.push(op);
      if(op==='authenticate')return reply({outcome:'AUTHENTICATED',access_token:'t'.repeat(40),
        device_id:body.arguments.adminDeviceId});
      if(op==='logout')return reply({outcome:'SIGNED_OUT',deviceId:''});
    }
    if(url==='/api/applications/slh/seh/deployment' && op==='inspect')return reply({ok:true,
      result:{generation:33,selectedController:'coolify-c',applicationUuid:'',
        deploymentStatus:'UNDEPLOYED',controllers:[{identity:'coolify-c'}]}});
    if(url==='/api/applications/slh/seh/deployment' && op==='operation-state')return reply({ok:true,
      result:{schema:'main-computer.seh-management-operation-snapshot.v1',state:'IDLE',busy:false,operation:null}});
    if(url==='/api/applications/slh/identity-notebook'){
      window.notebookCalls.push(op);
      if(op==='read-pending'){
        if(window.delayPending) return new Promise(resolve=>window.pendingResponses.push(resolve));
        if(window.pendingReject)return reply({ok:false,error:{code:'INSPECTION_REQUIRED'}},false);
        return reply({ok:true,result:[{id:'a'.repeat(32),identity:'Test Worker',
          role:'worker',requestedAt:'2026-11-17'}]});
      }
      if(op==='read-enrolled')return reply({ok:true,result:[
        {id:'one',identity:'One',role:'admin',createdAt:'2026-11-17'}]});
      if(op==='read-trash')return reply({ok:true,result:[]});
      if(op==='read-retention')return reply({ok:true,result:{enabled:false}});
    }
    return reply({ok:false,error:{code:'UNAVAILABLE'}},false);
  };
  window.replyPending=(index,identity='Test Worker')=>{
    const deliver=window.pendingResponses[index];
    if(!deliver)throw Error('missing deferred read '+index);
    deliver({ok:true,json:async()=>({ok:true,result:[{id:'a'.repeat(32),identity,
      role:'worker',requestedAt:'2026-11-17'}]})});
  };
}'''


def _launch(browser):
    page=browser.new_page(viewport={"width":1150,"height":850})
    page.on("pageerror",lambda error:print("PAGE ERROR",error))
    page.evaluate(MOCK)
    page.set_content(render_slh_page())
    page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
    return page


def _calls(page,op):
    return page.evaluate('(name) => window.notebookCalls.filter(x=>x===name).length',op)


def _status(page):
    return page.locator('[data-mcel-notebook-status]').inner_text()


def _pending_loaded(page):
    page.wait_for_function("() => window.__mcelSlh.app.readState('state:pending-enrollments')?.length===1")
    page.wait_for_function("() => document.querySelector('[data-mcel-notebook-scroll] [data-mcel-record-row]')?.textContent.includes('Test Worker')")


def test_initial_pending_tab_hydrates_after_identity_and_target_restore_without_click():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_launch(browser)
            # The unrelated top-of-page UI must not authenticate on mount.
            assert _calls(page,'authenticate')==0
            assert _calls(page,'read-pending')==0
            # Startup activates the mounted notebook; entering the viewport,
            # without clicking Pending, triggers its protected read.
            page.locator('[data-mcel-notebook-tab="pending"]').scroll_into_view_if_needed()
            _pending_loaded(page)
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]').textContent==='' ")
            # The SLH application explicitly requests one ordered snapshot of
            # all identity tabs. No auth callback spawns a second read flight.
            page.wait_for_function("() => window.notebookCalls.includes('read-retention')")
            assert _calls(page,'authenticate')==1
            assert _calls(page,'read-pending')==1
            assert _calls(page,'read-enrolled')==1
            assert _calls(page,'read-trash')==1
            assert _calls(page,'read-retention')==1
            # Idempotent auth events neither clear records nor refetch.
            page.evaluate('''() => {
              const root=document.querySelector('[data-mcel-notebook]');
              window.stableRow=root.querySelector('[data-mcel-notebook-scroll] [data-mcel-record-row]');
              window.stableAction=window.stableRow.querySelector('[data-mcel-notebook-action]');
              const app=window.__mcelSlh.app;
              app.setNotebookAuthorization(true,{scope:'records'});
              app.setNotebookAuthorization(true,{scope:'records'});
            }''')
            assert _calls(page,'read-pending')==1
            assert _calls(page,'read-enrolled')==1
            assert page.evaluate('''() => {
              const row=document.querySelector('[data-mcel-notebook-scroll] [data-mcel-record-row]');
              return row===window.stableRow && row.querySelector('[data-mcel-notebook-action]')===window.stableAction;
            }''')
            assert _status(page)==''
        finally:browser.close()


def test_out_of_order_protected_reads_cannot_publish_stale_data_or_superseded_error():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_launch(browser)
            page.locator('[data-mcel-notebook-tab="pending"]').click()
            _pending_loaded(page)
            page.evaluate('''() => {
              window.delayPending=true;
              window.oldRead=window.__mcelSlh.app.execute('intent:read-organization-pending');
            }''')
            page.wait_for_function('() => window.pendingResponses.length===1')
            page.evaluate('''() => {
              window.newRead=window.__mcelSlh.app.execute('intent:read-organization-pending');
            }''')
            page.wait_for_function('() => window.pendingResponses.length===2')
            page.evaluate("() => window.replyPending(1,'Test Worker')")
            assert page.evaluate('async () => (await window.newRead).disposition')=='completed'
            page.evaluate("() => window.replyPending(0,'Obsolete Worker')")
            assert page.evaluate('async () => (await window.oldRead).disposition')=='superseded'
            assert page.evaluate("() => window.__mcelSlh.app.readState('state:pending-enrollments')[0].identity")=='Test Worker'
            assert 'superseded' not in _status(page)
            assert page.locator('[data-mcel-notebook-scroll] [data-mcel-record-row]').count()==1
        finally:browser.close()


def test_pending_read_obsoleted_by_identity_change_cannot_reveal_old_records():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_launch(browser)
            page.locator('[data-mcel-notebook-tab="pending"]').click()
            _pending_loaded(page)
            page.evaluate('''() => {
              window.delayPending=true;
              window.oldRead=window.__mcelSlh.app.execute('intent:read-organization-pending');
            }''')
            page.wait_for_function('() => window.pendingResponses.length===1')
            page.evaluate("async () => await window.__mcelSlh.app.execute('intent:choose-identity',{key:'two'})")
            assert page.evaluate("() => window.__mcelSlh.app.readState('state:pending-enrollments')") is None
            page.evaluate("() => window.replyPending(0,'Obsolete Worker')")
            assert page.evaluate('async () => (await window.oldRead).disposition')=='superseded'
            assert page.evaluate("() => window.__mcelSlh.app.readState('state:pending-enrollments')") is None
            assert page.locator('[data-mcel-notebook-scroll] [data-mcel-record-row]').count()==0
            assert 'superseded' not in _status(page)
        finally:browser.close()


def test_current_failed_protected_read_reports_real_failure_and_recovers():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_launch(browser)
            page.locator('[data-mcel-notebook-tab="pending"]').click()
            _pending_loaded(page)
            page.evaluate('window.pendingReject=true')
            page.locator('[data-mcel-notebook-tab="pending"]').click()
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]').textContent.includes('INSPECTION_REQUIRED')")
            assert page.evaluate("() => window.__mcelSlh.app.readState('state:pending-enrollments')") is None
            page.evaluate('window.pendingReject=false')
            page.locator('[data-mcel-notebook-tab="pending"]').click()
            _pending_loaded(page)
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]').textContent==='' ")
        finally:browser.close()


def test_tab_switch_during_pending_read_does_not_overwrite_current_tab():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_launch(browser)
            page.locator('[data-mcel-notebook-tab="pending"]').click()
            _pending_loaded(page)
            page.evaluate('window.delayPending=true')
            page.locator('[data-mcel-notebook-tab="pending"]').click()
            page.wait_for_function('() => window.pendingResponses.length===1')
            page.locator('[data-mcel-notebook-tab="enrolled"]').click()
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-tab=\"enrolled\"]').getAttribute('aria-selected')==='true'")
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-scroll] [data-mcel-record-row]')?.textContent.includes('One')")
            page.evaluate("() => window.replyPending(0,'Obsolete Worker')")
            page.wait_for_timeout(80)
            assert page.locator('[data-mcel-notebook-tab="enrolled"]').get_attribute('aria-selected')=='true'
            assert 'Obsolete Worker' not in page.locator('[data-mcel-notebook-scroll]').inner_text()
            assert 'superseded' not in _status(page)
        finally:browser.close()


def test_newer_protected_read_failure_not_erased_by_superseded_notebook_refresh():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_launch(browser)
            page.locator('[data-mcel-notebook-tab="pending"]').click()
            _pending_loaded(page)
            page.evaluate('window.delayPending=true')
            page.locator('[data-mcel-notebook-tab="pending"]').click()
            page.wait_for_function('() => window.pendingResponses.length===1')
            page.evaluate('''() => {
              window.newRead=window.__mcelSlh.app.execute('intent:read-organization-pending');
            }''')
            page.wait_for_function('() => window.pendingResponses.length===2')
            page.evaluate('''() => window.pendingResponses[1]({ok:false,
              json:async()=>({ok:false,error:{code:'INSPECTION_REQUIRED'}})})''')
            assert page.evaluate('async () => (await window.newRead).disposition')=='failed'
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]').textContent.includes('INSPECTION_REQUIRED')")
            page.evaluate("() => window.replyPending(0,'Obsolete Worker')")
            page.wait_for_timeout(80)
            assert 'INSPECTION_REQUIRED' in _status(page)
            assert page.evaluate("() => window.__mcelSlh.app.readState('state:pending-enrollments')") is None
            assert page.locator('[data-mcel-notebook-scroll] [data-mcel-record-row]').count()==0
        finally:browser.close()


def test_explicit_deselection_skips_startup_authentication_and_notebook_hydration():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={"width":1150,"height":850})
            page.evaluate(MOCK)
            page.evaluate('''() => {
              Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
                _store:{'mcel.slh.selected-identity.v2.local':'__EXPLICIT_NONE_V3__'},
                getItem(k){return this._store[k]??null},
                setItem(k,v){this._store[k]=v}
              }});
            }''')
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:local-identity-status')?.state==='AVAILABLE'")
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='' ")
            page.wait_for_timeout(100)
            assert _calls(page,'authenticate')==0
            assert _calls(page,'read-pending')==0
            assert page.locator('[data-mcel-notebook-scroll] [data-mcel-record-row]').count()==0
        finally:browser.close()


def test_initial_pending_hydration_does_not_wait_for_user_click_after_reload():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=_launch(browser)
            page.locator('[data-mcel-notebook-tab="pending"]').scroll_into_view_if_needed()
            _pending_loaded(page)
            assert page.locator('[data-mcel-notebook-tab="pending"]').get_attribute('aria-selected')=='true'
            assert _calls(page,'read-pending')==1
            # A second mount is a new page load. The already-selected tab
            # must hydrate again without carrying DOM state from the first.
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            page.locator('[data-mcel-notebook-tab="pending"]').scroll_into_view_if_needed()
            _pending_loaded(page)
            assert _calls(page,'read-pending')==2
        finally:browser.close()


def test_application_waits_for_running_service_before_attempting_protected_authentication():
    """An identity selection or visible notebook is not an authority prerequisite."""
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={"width":1150,"height":850})
            page.evaluate(MOCK)
            page.evaluate("window.lifecycleState='stopped'")
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            page.locator('[data-mcel-notebook-tab="pending"]').scroll_into_view_if_needed()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='stopped'")
            assert _calls(page,'authenticate')==0
            assert _calls(page,'read-pending')==0
            page.evaluate("() => { window.lifecycleState='running';window.dispatchEvent(new Event('focus')); }")
            _pending_loaded(page)
            assert _calls(page,'authenticate')==1
            assert _calls(page,'read-pending')==1
            assert 'authority-denied' not in _status(page)
        finally:browser.close()


def test_login_callback_does_not_spawn_competing_protected_reads():
    """The first request authenticates; its callback must not supersede it."""
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page=browser.new_page(viewport={"width":1150,"height":850})
            page.evaluate(MOCK)
            page.evaluate('window.delayPending=true')
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            page.locator('[data-mcel-notebook-tab="pending"]').scroll_into_view_if_needed()
            page.wait_for_function('() => window.pendingResponses.length===1')
            assert _calls(page,'authenticate')==1
            assert _calls(page,'read-pending')==1
            assert _calls(page,'read-enrolled')==0
            assert _calls(page,'read-trash')==0
            assert _status(page).startswith('Loading Pending')
            page.evaluate("() => {window.delayPending=false;window.replyPending(0,'Test Worker');}")
            _pending_loaded(page)
            page.wait_for_function("() => window.notebookCalls.includes('read-retention')")
            assert _calls(page,'read-pending')==1
            assert _calls(page,'read-enrolled')==1
            assert _calls(page,'read-trash')==1
            assert _calls(page,'read-retention')==1
            assert 'authority-denied' not in _status(page)
            assert 'superseded' not in _status(page)
        finally:browser.close()


def test_notebook_loading_policy_is_application_owned_not_renderer_inferred():
    root=Path(__file__).resolve().parents[1]
    shared=(root/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text()
    slh=(root/'main_computer/mcel_slh_application.py').read_text()
    assert 'session.visibilityObserver' not in shared
    assert 'new global.IntersectionObserver' not in shared
    assert 'options.refresh===true' in shared
    assert 'new IntersectionObserver(entries' in slh
    assert 'diagnosticState.lifecycleRefresh==="completed"' in slh
    assert 'notebookId:identityNotebookId,all:true' in slh
    assert 'app.setNotebookAuthorization(!!authenticated,{{scope:"records",refresh:false}})' in slh
