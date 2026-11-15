"""The operator, not LMS observation or connectivity, owns SLH modal dismissal."""
from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'main_computer/web/applications'
IDENTITY_A = 'mcel-seh-interrupted-operation-123456'
IDENTITY_B = 'mcel-seh-other-operation-123456789'


def _page(browser):
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    page.set_content('''<!doctype html><html><body>
      <main id="slh" style="position:absolute;left:150px;top:50px;width:900px;height:670px">
        <section data-mcel-zone="workspace" style="height:100%;width:100%">
          <button id="work">Workspace</button>
        </section>
      </main></body></html>''')
    page.add_style_tag(content=(WEB / 'styles/mcel-semantic-modal-subprogram.css').read_text())
    page.add_script_tag(path=str(WEB / 'scripts/mcel-semantic-modal-subprogram.js'))
    page.add_script_tag(path=str(WEB / 'scripts/mcel-semantic-slh-management-modal.js'))
    page.evaluate('''() => {
      window.obs={state:'IDLE',busy:false,operation:null};
      window.unavailable=false;
      window.backend=async(action,args)=>{
        if(window.unavailable)throw new Error('SERVICE_CONNECTION_LOST');
        if(action==='operation-state')return window.obs;
        if(action==='operation-status')return window.receipt;
        return {status:'REFUSED'};
      };
      window.manager=McelSlhManagementModal.create({host:document.querySelector('#slh'),request:window.backend});
    }''')
    return page


def test_busy_modal_survives_service_failure_idle_without_receipt_and_recovery():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page = _page(browser)
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.evaluate('''async id=>{
              window.obs={state:'BUSY',busy:true,operation:{operation_id:id,
                action:'deploy-seh',stage:'HARNESS_EXECUTING',phase:'RUNNING',cancelable:false}};
              await window.manager.start();
            }''', IDENTITY_A)
            dialog = page.locator('[data-mcel-lms-operation-modal]')
            assert dialog.is_visible()
            assert IDENTITY_A in dialog.inner_text()
            page.evaluate('''async()=>{window.unavailable=true; await window.manager.refresh();}''')
            assert dialog.is_visible(), 'Failed observation must never close the modal'
            assert 'unavailable' in dialog.inner_text().lower()
            assert dialog.get_attribute('data-mcel-blocking') == 'false'
            assert page.locator('#work').evaluate('(el)=>el.inert') is False
            page.evaluate('''async()=>{
              window.unavailable=false;
              window.obs={state:'IDLE',busy:false,operation:null};
              await window.manager.refresh();
            }''')
            assert dialog.is_visible(), 'IDLE without a terminal receipt must not dismiss'
            assert 'unverified' in dialog.inner_text().lower()
            # A matching terminal receipt can update the open view, not dismiss it.
            page.evaluate('''async id=>{
              window.obs={state:'IDLE',busy:false,operation:null,last_operation:{
                operation_id:id,action:'deploy-seh',phase:'RECORDED',stage:'DONE',
                result:{status:'REPLACEMENT_QUEUED_NOT_VERIFIED'}}};
              await window.manager.refresh();
            }''', IDENTITY_A)
            assert dialog.is_visible()
            assert 'REPLACEMENT_QUEUED_NOT_VERIFIED' in dialog.inner_text()
            assert not errors, errors
        finally:
            browser.close()


def test_manual_dismissal_stays_closed_across_idle_failure_and_same_operation_reappearance():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page = _page(browser)
            page.evaluate('''async id=>{
              window.obs={state:'BUSY',busy:true,operation:{operation_id:id,
                action:'deploy-seh',phase:'RUNNING',stage:'EXECUTING'}};
              await window.manager.start();
            }''', IDENTITY_A)
            dialog = page.locator('[data-mcel-lms-operation-modal]')
            assert dialog.is_visible()
            dialog.get_by_role('button', name='Dismiss').click()
            assert dialog.is_hidden()
            page.evaluate('''async id=>{
              window.obs={state:'IDLE',busy:false,operation:null};
              await window.manager.refresh();
              window.unavailable=true;
              await window.manager.refresh();
              window.unavailable=false;
              window.obs={state:'BUSY',busy:true,operation:{operation_id:id,
                action:'deploy-seh',phase:'RUNNING',stage:'EXECUTING'}};
              await window.manager.refresh();
            }''', IDENTITY_A)
            assert dialog.is_hidden(), 'Same operation must not reopen after manual closure'
            page.locator('[data-mcel-lms-operation-indicator] button').click()
            assert dialog.is_visible(), 'Explicit View operation must reopen'
            page.keyboard.press('Escape')
            assert dialog.is_hidden(), 'Escape is an explicit user dismissal'
            page.evaluate('''async id=>{
              window.obs={state:'BUSY',busy:true,operation:{operation_id:id,
                action:'activate-seh',phase:'RUNNING',stage:'EXECUTING'}};
              await window.manager.refresh();
            }''', IDENTITY_B)
            assert dialog.is_visible(), 'New operation is distinct from dismissed operation'
        finally:
            browser.close()


def test_disconnect_pauses_monitor_and_preserves_dialog_log_until_reconnect():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page = _page(browser)
            page.evaluate('''async()=>{
              window.manager.begin('intent:seh-deployment-deploy','Deploy SEH');
              await window.manager.start();
              window.manager.pause();
            }''')
            dialog = page.locator('[data-mcel-lms-operation-modal]')
            assert dialog.is_visible()
            assert 'connection interrupted' in dialog.inner_text().lower()
            assert 'Submitting to LMS' in page.locator('[data-mcel-lms-operation-results]').inner_text()
            assert 'awaiting reconnection' in page.locator('[data-mcel-lms-operation-results]').inner_text()
            assert dialog.get_attribute('data-mcel-blocking') == 'false'
            page.evaluate('''async()=>{await window.manager.start();}''')
            assert dialog.is_visible()
            dialog.get_by_role('button', name='Dismiss').click()
            assert dialog.is_hidden()
            page.evaluate('''async()=>{window.manager.pause(); await window.manager.start();}''')
            assert dialog.is_hidden()
            page.locator('[data-mcel-lms-operation-indicator] button').click()
            assert dialog.is_visible()
        finally:
            browser.close()


def test_network_disconnect_uses_pause_not_teardown():
    code = (ROOT / 'main_computer/mcel_slh_application.py').read_text()
    assert 'else managementOverlay?.pause();' in code
    assert 'else managementOverlay?.stop();' not in code
    controller = (WEB / 'scripts/mcel-semantic-slh-management-modal.js').read_text()
    assert 'hide()' not in controller
    assert 'close.addEventListener("click",()=>modal.dismiss())' in controller
