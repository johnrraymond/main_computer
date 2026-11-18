"""A transient tab read must not destroy previously observed semantic values.

Browser network responses are mocked; no resident SLH, SEH, or Coolify is used.
"""
from __future__ import annotations

import shutil

from playwright.sync_api import sync_playwright

from tests.test_mcel_seh_two_action_lifecycle_ui import _setup
from tests.test_mcel_slh_startup_ewg import MOCK
from main_computer.mcel_slh_application import render_slh_page


NOTEBOOK = '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]'


def _browser():
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True,
        executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
    return pw, browser


def _observe(page):
    return page.locator(NOTEBOOK).locator('[data-mcel-operation-field="selectedController"]').inner_text()


def test_transient_failed_read_preserves_verified_fields_but_disables_actions():
    pw, browser = _browser()
    try:
        page, notebook = _setup(browser, 'CONNECTED_VERIFIED')
        assert _observe(page) == 'coolify-c'
        # The backend fixture exposes a mutable observation. An invalid
        # transient response is rejected by the strict schema validator.
        page.evaluate("window.sehUiPhase=null")
        # A tab switch initiates the bounded read that previously deleted
        # state on failure. Both tabs use the same observed deployment record.
        notebook.locator('[data-mcel-operation-tab="configuration"]').click()
        page.wait_for_function('''() => document.querySelector(
          '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] '
          + '[data-mcel-operation-status]')?.textContent.includes('showing last observed values')''')
        assert _observe(page) == 'coolify-c'
        assert notebook.locator('[data-mcel-operation-field="deploymentStatus"]').inner_text() == 'CONNECTED_VERIFIED'
        assert page.evaluate('window.__mcelSlh.app.readState("state:seh-deployment-status").selectedController') == 'coolify-c'
        assert notebook.locator('[data-mcel-operation-form] button[type="submit"]').evaluate_all(
            'items => items.every(item => item.disabled)')

        # A subsequent successful poll, not only a tab click, must clear the
        # warning and restore controls without replacing semantic elements.
        page.evaluate('''() => {
          window.beforeField=document.querySelector(
            '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] '
            + '[data-mcel-operation-field="selectedController"]');
          window.beforeStatus=document.querySelector(
            '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] '
            + '[data-mcel-operation-status]');
        }''')
        page.evaluate("window.sehUiPhase='CONNECTED_VERIFIED'")
        receipt = page.evaluate('''async () => await window.__mcelSlh.app.execute(
          'intent:read-seh-deployment')''')
        assert receipt['disposition'] == 'completed', receipt
        page.wait_for_function('''() => !document.querySelector(
          '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] '
          + '[data-mcel-operation-status]')?.textContent.includes('showing last observed values')''')
        assert page.evaluate('''() => window.beforeField===document.querySelector(
          '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] '
          + '[data-mcel-operation-field="selectedController"]')''')
        assert page.evaluate('''() => window.beforeStatus===document.querySelector(
          '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] '
          + '[data-mcel-operation-status]')''')
        assert _observe(page) == 'coolify-c'
        assert notebook.locator('[data-mcel-operation-form] button[type="submit"]').evaluate_all(
            'items => items.every(item => !item.disabled)')

        # Explicit authorization revocation still clears protected data.
        page.evaluate('''() => window.__mcelSlh.app.setNotebookAuthorization(false,{scope:'operations'})''')
        assert page.evaluate('window.__mcelSlh.app.readState("state:seh-deployment-status")') is None
        assert _observe(page) == 'Unavailable'
        assert notebook.locator('[data-mcel-operation-form] button[type="submit"]').evaluate_all(
            'items => items.every(item => item.disabled)')
    finally:
        browser.close()
        pw.stop()


def test_late_failed_read_cannot_hide_newer_successful_observation():
    pw, browser = _browser()
    try:
        page=browser.new_page(viewport={'width':1280,'height':900})
        page.evaluate(MOCK)
        page.evaluate('''() => {
          window.sehUiPhase='CONNECTED_VERIFIED';
          const previous=window.fetch;
          window.fetch=async(url,options={})=>{
            if(String(url)==='/api/applications/slh/seh/deployment'){
              const {operation}=JSON.parse(options.body||'{}');
              const success=result=>({ok:true,json:async()=>({ok:true,result})});
              if(operation==='inspect')return success({generation:33,
                selectedController:'coolify-c',applicationUuid:'mock-uuid',
                controllers:[{identity:'coolify-c'}],deploymentStatus:window.sehUiPhase});
              if(operation==='operation-state')return success({
                schema:'main-computer.seh-management-operation-snapshot.v1',
                state:'IDLE',busy:false,operation:null,last_operation:null});
            }
            return previous(url,options);
          };
        }''')
        page.evaluate('''() => {
          window.holdOneSehInspect=false;
          window.releaseOldSehInspect=null;
          const fetchBefore=window.fetch;
          window.fetch=async(url,options={})=>{
            if(String(url)==='/api/applications/slh/seh/deployment' &&
                JSON.parse(options.body||'{}').operation==='inspect' && window.holdOneSehInspect){
              window.holdOneSehInspect=false;
              return new Promise(resolve=>{window.releaseOldSehInspect=()=>resolve({
                ok:false,json:async()=>({ok:false,error:{code:'OLD_UNAVAILABLE'}})
              });});
            }
            return fetchBefore(url,options);
          };
        }''')
        page.on('dialog', lambda dialog: dialog.accept())
        page.set_content(render_slh_page())
        page.wait_for_function("()=>!!window.__mcelSlh?.app && window.__mcelSlh.app.readState('state:seh-deployment-status')?.deploymentStatus")
        notebook=page.locator(NOTEBOOK)
        notebook.locator('[data-mcel-operation-tab="deployment"]').click()
        page.wait_for_timeout(200)
        page.evaluate('window.holdOneSehInspect=true')
        notebook.locator('[data-mcel-operation-tab="configuration"]').click()
        page.wait_for_function('typeof window.releaseOldSehInspect === "function"')
        receipt = page.evaluate('''async () => await window.__mcelSlh.app.execute(
          'intent:read-seh-deployment')''')
        assert receipt['disposition'] == 'completed', receipt
        page.evaluate('window.releaseOldSehInspect()')
        page.wait_for_timeout(100)
        assert _observe(page) == 'coolify-c'
        assert 'OLD_UNAVAILABLE' not in notebook.locator('[data-mcel-operation-status]').inner_text()
        assert notebook.locator('[data-mcel-operation-form] button[type="submit"]').evaluate_all(
            'items => items.every(item => !item.disabled)')
    finally:
        browser.close()
        pw.stop()
