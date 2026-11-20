"""Patch 2: application-owned notebook recovery and typed SLH authentication.

All service boundaries are mocked; these tests never contact or mutate a live SLH.
"""
from __future__ import annotations

import shutil

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page
from tests.test_mcel_record_notebook_async_reconciliation import MOCK, _calls, _pending_loaded


def _browser(pw):
    return pw.chromium.launch(headless=True,
                              executable_path=shutil.which("chromium") or None,
                              args=["--no-sandbox"])


def _controlled_visibility(page):
    page.evaluate('''() => {
      window.IntersectionObserver=class {
        constructor(callback){this.callback=callback;window.testNotebookObserver=this;}
        observe(node){this.node=node;}
        disconnect(){}
        emit(visible){this.callback([{target:this.node,isIntersecting:visible}]);}
      };
    }''')


def test_retryable_failure_waits_for_real_reactivation_not_repeated_visible_notification():
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page = browser.new_page(viewport={"width": 1150, "height": 700})
            page.evaluate(MOCK)
            page.evaluate("window.pendingReject=true")
            _controlled_visibility(page)
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='running'")
            page.evaluate("() => window.testNotebookObserver.emit(true)")
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]')?.textContent.includes('INSPECTION_REQUIRED')")
            assert _calls(page, "read-pending") == 1
            page.evaluate("() => window.testNotebookObserver.emit(true)")
            page.wait_for_timeout(100)
            assert _calls(page, "read-pending") == 1, "Repeated visible callback caused an unbounded retry"
            page.evaluate('''() => {
              window.pendingReject=false;
              window.testNotebookObserver.emit(false);
              window.testNotebookObserver.emit(true);
            }''')
            _pending_loaded(page)
            assert _calls(page, "read-pending") == 2
        finally:
            browser.close()


def test_real_credential_refusal_does_not_retry_on_visibility_or_focus():
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page = browser.new_page(viewport={"width": 1150, "height": 700})
            page.evaluate(MOCK)
            page.evaluate('''() => {
              window.deniedAuthentications=0;
              const original=window.fetch;
              window.fetch=async(url,options={})=>{
                const body=options.body?JSON.parse(options.body):{};
                if(String(url)==='/api/applications/slh/setup/action' && body.action==='authenticate'){
                  window.deniedAuthentications++;
                  return {ok:false,status:403,json:async()=>({error:{code:'ADMIN_REFUSED'}})};
                }
                return original(url,options);
              };
            }''')
            _controlled_visibility(page)
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='running'")
            page.evaluate("() => window.testNotebookObserver.emit(true)")
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]')?.textContent.includes('AUTHORIZATION_REFUSED:ADMIN_REFUSED')")
            assert page.evaluate("() => window.deniedAuthentications") == 1
            page.evaluate('''() => {
              window.testNotebookObserver.emit(false);
              window.testNotebookObserver.emit(true);
              window.dispatchEvent(new Event('focus'));
              window.dispatchEvent(new Event('online'));
            }''')
            # Longer than the first transient retry interval: a genuine
            # credential refusal must never enter the automatic retry lane.
            page.wait_for_timeout(2200)
            assert page.evaluate("() => window.deniedAuthentications") == 1, (
                "A credential refusal must not cause automatic reauthentication"
            )
            assert page.evaluate("() => window.__mcelSlh.app.readState('state:pending-enrollments')") is None
        finally:
            browser.close()


def test_authentication_transport_outage_recovers_on_notebook_reactivation():
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page = browser.new_page(viewport={"width": 1150, "height": 700})
            page.evaluate(MOCK)
            page.evaluate('''() => {
              window.authTransportDown=true;
              window.authAttempts=0;
              const original=window.fetch;
              window.fetch=async(url,options={})=>{
                const body=options.body?JSON.parse(options.body):{};
                if(String(url)==='/api/applications/slh/setup/action' && body.action==='authenticate'){
                  window.authAttempts++;
                  if(window.authTransportDown)throw Error('MOCK_TRANSPORT_DOWN');
                }
                return original(url,options);
              };
            }''')
            _controlled_visibility(page)
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='running'")
            page.evaluate("() => window.testNotebookObserver.emit(true)")
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]')?.textContent.includes('AUTHENTICATION_TRANSPORT_UNAVAILABLE')")
            assert page.evaluate("() => window.authAttempts") == 1
            page.evaluate('''() => {
              window.authTransportDown=false;
              window.testNotebookObserver.emit(false);
              window.testNotebookObserver.emit(true);
            }''')
            _pending_loaded(page)
            assert page.evaluate("() => window.authAttempts") == 2
            assert page.locator('[data-mcel-notebook-status]').inner_text() == ''
        finally:
            browser.close()


def test_service_503_recovers_automatically_without_focus_tab_or_manual_refresh():
    """A temporary LMS authentication failure must repair itself once LMS responds."""
    with sync_playwright() as pw:
        browser = _browser(pw)
        try:
            page = browser.new_page(viewport={"width": 1150, "height": 700})
            page.evaluate(MOCK)
            page.evaluate('''() => {
              window.authServiceDown=true;
              window.authAttempts=0;
              const original=window.fetch;
              window.fetch=async(url,options={})=>{
                const body=options.body?JSON.parse(options.body):{};
                if(String(url)==='/api/applications/slh/setup/action' && body.action==='authenticate'){
                  window.authAttempts++;
                  if(window.authServiceDown)return {
                    ok:false,status:503,json:async()=>({error:{code:'LMS_TEMPORARILY_UNAVAILABLE'}})
                  };
                }
                return original(url,options);
              };
            }''')
            _controlled_visibility(page)
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='running'")
            page.evaluate("() => window.testNotebookObserver.emit(true)")
            page.wait_for_function("() => document.querySelector('[data-mcel-notebook-status]')?.textContent.includes('AUTHENTICATION_SERVICE_UNAVAILABLE')")
            assert page.evaluate("() => window.authAttempts") == 1
            # The operator does nothing: no focus, viewport reactivation, tab
            # click, or other manual request is allowed to trigger recovery.
            page.evaluate("() => {window.authServiceDown=false}")
            _pending_loaded(page)
            page.wait_for_function("() => window.authAttempts === 2", timeout=5000)
            assert page.locator('[data-mcel-notebook-status]').inner_text() == ''
        finally:
            browser.close()
