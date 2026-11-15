"""Patch 3: existing LMS causal receipts are rendered and copied by MCEL.

All interactions are local Playwright mocks; no service or mutation is invoked.
"""
from __future__ import annotations

import shutil

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page
from tests.test_mcel_slh_startup_ewg import MOCK


ID = 'mcel-seh-causal-patch3-test-123456789'
DIAG = {
    'schema': 'main-computer.seh-causal-diagnostic.v1',
    'source': 'resident-outbound-supervisor',
    'error_code': 'SEH_CANONICAL_DEPLOYMENT_DISABLED',
    'failed_stage': 'VERIFYING_LOCAL_BINDING',
    'exception_class': 'ValueError',
    'attempts': 25,
    'failures': 25,
    'private_field': 'SHOULD_NEVER_BE_EXPOSED',
}
RECEIPT = {
    'operation_id': ID,
    'action': 'activate-seh',
    'phase': 'RECORDED',
    'stage': 'HARNESS_RETURNED',
    'result': {'status': 'INCOMPLETE', 'remote_mutation': False},
    'causal_diagnostic': DIAG,
    'output': [],
}


def _browser_page(browser, *, incomplete_from_restart=False):
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    page.evaluate(MOCK)
    page.evaluate('''(settings) => {
      window.lmsReceipt = settings.receipt;
      window.managementReads=[];
      window.lmsState = settings.recovered
        ? {state:'IDLE', busy:false, operation:null, last_operation:window.lmsReceipt}
        : {state:'IDLE', busy:false, operation:null, last_operation:null};
      const previous=window.fetch;
      window.fetch=async(url, options={})=>{
        if(url==='/api/applications/slh/seh/deployment'){
          const body=JSON.parse(options.body||'{}');
          window.managementReads.push(body.operation);
          const result=body.operation==='operation-state' ? window.lmsState
            : body.operation==='operation-status' ? window.lmsReceipt
            : {status:'REFUSED'};
          return {ok:true,json:async()=>({ok:true,result})};
        }
        return previous(url,options);
      };
    }''', {'receipt': RECEIPT, 'recovered': incomplete_from_restart})
    page.set_content(render_slh_page())
    page.wait_for_function('''()=>window.__mcelSlh?.diagnostics()?.startup?.lifecycleRefresh!=='pending' &&
        window.__mcelSlh?.diagnostics()?.startup?.lifecycleRefresh!=='not-attempted' ''')
    return page


def test_tracked_operation_shows_cause_and_copies_diagnostic_to_ewg():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=_browser_page(browser)
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.evaluate('''async id=>{
              const monitor=window.__mcelSlh.managementOverlay;
              monitor.begin('intent:seh-deployment-activate','Activate SEH');
              monitor.track(id);
              await monitor.refresh();
            }''', ID)
            dialog=page.locator('[data-mcel-lms-operation-modal]')
            assert dialog.is_visible()
            text=dialog.inner_text()
            assert 'SEH_CANONICAL_DEPLOYMENT_DISABLED' in text
            assert 'VERIFYING_LOCAL_BINDING' in text
            assert 'causal_attempts: 25' in text
            assert 'SHOULD_NEVER_BE_EXPOSED' not in text
            evidence=page.evaluate('window.__mcelSlh.diagnostics()')
            assert evidence['sehManagement']['operationId']==ID
            assert evidence['sehManagement']['causalDiagnostic']['error_code']=='SEH_CANONICAL_DEPLOYMENT_DISABLED'
            assert evidence['sehManagement']['causalDiagnostic']['failed_stage']=='VERIFYING_LOCAL_BINDING'
            assert 'private_field' not in evidence['sehManagement']['causalDiagnostic']
            assert any(f['code']=='slh-seh-operation-causal-failure' for f in evidence['findings'])
            # Reading E/W/G must not fetch another operation receipt or mutate LMS.
            before=page.evaluate('window.managementReads.length')
            page.locator('[data-mcel-diagnostic-title-control]').click()
            page.wait_for_function('window.copies.length>0')
            copied=page.evaluate('window.copies.at(-1)')
            assert copied['diagnosticEvidence']['operational']['sehManagement']['causalDiagnostic']['error_code'] == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
            assert page.evaluate('window.managementReads.length')==before
            assert not errors, errors
        finally:
            browser.close()


def test_restart_recovers_latest_incomplete_diagnosis_without_submitting():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=_browser_page(browser,incomplete_from_restart=True)
            page.wait_for_function('''()=>window.__mcelSlh.diagnostics().sehManagement.causalDiagnostic?.error_code==='SEH_CANONICAL_DEPLOYMENT_DISABLED' ''')
            # The server's IDLE snapshot is non-modal until the operator views it.
            assert page.locator('[data-mcel-lms-operation-modal]').is_hidden()
            page.locator('[data-mcel-lms-operation-indicator] button').click()
            text=page.locator('[data-mcel-lms-operation-modal]').inner_text()
            assert ID in text
            assert 'SEH_CANONICAL_DEPLOYMENT_DISABLED' in text
            assert 'VERIFYING_LOCAL_BINDING' in text
            assert all(x in {'operation-state','inspect'} for x in page.evaluate('window.managementReads'))
            assert 'operation-status' not in page.evaluate('window.managementReads')
        finally:
            browser.close()


def test_dismissal_keeps_evidence_but_does_not_reopen_on_poll():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=_browser_page(browser,incomplete_from_restart=True)
            page.wait_for_function('''()=>window.__mcelSlh.diagnostics().sehManagement.causalDiagnostic!==null''')
            page.locator('[data-mcel-lms-operation-indicator] button').click()
            page.locator('[data-mcel-lms-operation-modal] button',has_text='Dismiss').click()
            assert page.locator('[data-mcel-lms-operation-modal]').is_hidden()
            page.evaluate('''async()=>await window.__mcelSlh.managementOverlay.refresh()''')
            assert page.locator('[data-mcel-lms-operation-modal]').is_hidden()
            assert page.evaluate('window.__mcelSlh.diagnostics().sehManagement.causalDiagnostic.error_code') == 'SEH_CANONICAL_DEPLOYMENT_DISABLED'
        finally:
            browser.close()


def test_invalid_diagnostic_fields_are_not_published_to_ewg_or_modal():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=_browser_page(browser)
            page.evaluate('''async id=>{
              window.lmsReceipt.causal_diagnostic = {
                schema:'main-computer.seh-causal-diagnostic.v1',
                error_code:'TOKEN=secret-credential', failed_stage:'../../../secret',
                private_field:'SHOULD_NEVER_BE_EXPOSED'};
              window.__mcelSlh.managementOverlay.track(id);
              await window.__mcelSlh.managementOverlay.refresh();
            }''', ID)
            record=page.evaluate('window.__mcelSlh.diagnostics().sehManagement')
            assert record['causalDiagnostic'] is None
            assert 'SHOULD_NEVER_BE_EXPOSED' not in page.content()
        finally:
            browser.close()


def test_refused_result_embedded_diagnostic_is_shown_and_not_assigned_to_next_operation():
    """Older status projections can expose the same sanitized cause in result."""
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=_browser_page(browser)
            page.evaluate('''async id=>{
              const original=window.lmsReceipt;
              window.lmsReceipt={...original,causal_diagnostic:undefined,
                result:{status:'REFUSED',causal_diagnostic:{
                  schema:'main-computer.seh-causal-diagnostic.v1',
                  error_code:'SEH_CANONICAL_DEPLOYMENT_DISABLED',
                  failed_stage:'canonical_deployment_activation_gate'}}};
              window.__mcelSlh.managementOverlay.track(id);
              await window.__mcelSlh.managementOverlay.refresh();
            }''', ID)
            assert 'SEH_CANONICAL_DEPLOYMENT_DISABLED' in page.locator('[data-mcel-lms-operation-modal]').inner_text()
            assert page.evaluate('window.__mcelSlh.diagnostics().sehManagement.status')=='REFUSED'
            new_id='mcel-seh-new-activation-other-operation-1234'
            page.evaluate('''async id=>{
              window.lmsState={state:'BUSY',busy:true,operation:{operation_id:id,
                phase:'RUNNING',action:'activate-seh',stage:'ACTIVATION_EXECUTING'}};
              await window.__mcelSlh.managementOverlay.refresh();
            }''', new_id)
            assert page.evaluate('window.__mcelSlh.diagnostics().sehManagement.causalDiagnostic') is None
        finally:
            browser.close()
