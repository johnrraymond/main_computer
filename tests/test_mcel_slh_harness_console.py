"""MCEL modal consumes real, durable LMS harness output, not invented steps."""
from __future__ import annotations

from pathlib import Path
import shutil


def test_modal_displays_stream_labeled_harness_output_and_reconnects():
    from playwright.sync_api import sync_playwright
    script = Path(__file__).resolve().parents[1] / 'main_computer' / 'web' / 'applications' / 'scripts' / 'mcel-semantic-slh-management-modal.js'
    engine = script.with_name('mcel-semantic-modal-subprogram.js')
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width':950,'height':650})
            page.set_content('<main id="a"><section data-mcel-zone="workspace"></section></main><main id="b"><section data-mcel-zone="workspace"></section></main>')
            page.add_script_tag(path=str(engine))
            page.add_script_tag(path=str(script))
            page.evaluate('''() => {
              window.busyOperation={operation_id:'mcel-seh-harness-output-12345678',
                action:'deploy-seh',phase:'RUNNING',stage:'PREFLIGHT_EXECUTING',
                cancelable:false,output:[
                  {seq:1,stream:'stdout',text:'SEH_PROGRESS: preflight complete'},
                  {seq:2,stream:'stderr',text:'HarnessError: safe diagnostic'}]};
              window.backendState={state:'BUSY',busy:true,operation:window.busyOperation};
              window.backendReceipt={...window.busyOperation,phase:'RECORDED',stage:'HARNESS_RETURNED',
                result:{status:'REPLACEMENT_QUEUED_NOT_VERIFIED'},output:[
                  ...window.busyOperation.output,
                  {seq:3,stream:'stdout',text:'SEH_PROGRESS: replacement queued'}]};
              window.requests=async(op)=>op==='operation-state'?window.backendState:window.backendReceipt;
              window.first=window.McelSlhManagementModal.create({host:document.querySelector('#a'),request:window.requests});
              window.first.start();
            }''')
            dialog=page.locator('[data-mcel-lms-operation-modal]').nth(0)
            output=dialog.locator('[data-mcel-lms-operation-results]')
            page.wait_for_function("() => document.querySelectorAll('[data-mcel-lms-operation-results]')[0]?.textContent.includes('preflight complete')")
            assert 'Harness stdout · SEH_PROGRESS: preflight complete' in output.inner_text()
            assert 'Harness stderr · HarnessError: safe diagnostic' in output.inner_text()
            before=output.inner_text().count('Harness stdout ·')
            page.evaluate('() => window.first.refresh()')
            assert output.inner_text().count('Harness stdout ·')==before
            # An independent new connection to the same LMS reconstructs the
            # same stdout/stderr without the initiating browser's local log.
            page.evaluate('''() => {
              window.second=window.McelSlhManagementModal.create({host:document.querySelector('#b'),request:window.requests});
              window.second.start();
            }''')
            page.wait_for_function("() => document.querySelectorAll('[data-mcel-lms-operation-results]')[1]?.textContent.includes('safe diagnostic')")
            # The old connection gets its completion including the final line.
            page.evaluate('''() => {
              window.backendState={state:'IDLE',busy:false,operation:null};
              window.first.track('mcel-seh-harness-output-12345678');
              return window.first.refresh();
            }''')
            page.wait_for_function("() => document.querySelectorAll('[data-mcel-lms-operation-results]')[0]?.textContent.includes('replacement queued')")
            assert output.inner_text().count('replacement queued')==1
            page.evaluate('() => {window.first.stop();window.second.stop();}')
        finally:
            browser.close()
