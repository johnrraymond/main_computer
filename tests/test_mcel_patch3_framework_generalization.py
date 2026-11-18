"""Patch 3: generic semantic notebook and bounded activity history contracts.

Browser calls are mocked; no deployed service or live identity is mutated.
"""
from copy import deepcopy
import shutil

from playwright.sync_api import sync_playwright

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from tests.test_mcel_record_notebook_async_reconciliation import MOCK, _launch


def test_generic_notebook_without_retention_or_trash_has_no_footer():
    ir, composition = compile_slh_application()
    minimal = deepcopy(ir)
    notebook = next(n for n in minimal['surfaces'][0]['nodes'] if n['nodeKind'] == 'record-notebook')
    notebook.pop('retention')
    notebook.pop('empty')
    notebook.pop('messages', None)
    notebook.pop('recordLabel', None)
    notebook['updates'] = {'rows': True, 'status': True}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page = browser.new_page()
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            result = page.evaluate('''data => {
              const host=document.createElement('section'); document.body.append(host);
              const engine=window.McelSemanticOperationalRuntime;
              try {
                const app=engine.mount(host,data.ir,{
                  composition:data.composition,presentation:'expert',
                  capabilities:{authorize:async()=>true,invoke:async()=>[]}});
                const result={
                  notebook:host.querySelectorAll('[data-mcel-notebook]').length,
                  policy:host.querySelectorAll('[data-mcel-notebook-policy]').length,
                  trash:host.querySelectorAll('[data-mcel-notebook-empty]').length,
                  header:host.querySelector('[data-mcel-notebook-tabs]')?.getAttribute('aria-label')};
                app.unmount();return result;
              } finally {host.remove()}
            }''', {'ir':minimal,'composition':composition})
            assert result == {'notebook': 1, 'policy': 0, 'trash': 0,
                              'header': notebook['title']}
        finally:
            browser.close()


def test_receipt_history_is_bounded_with_unique_monotonic_sequences():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None, args=['--no-sandbox'])
        try:
            page = _launch(browser)
            values = page.evaluate('''async()=>{
              const app=window.__mcelSlh.app;
              for(let i=0;i<400;i++){
                const result=await app.execute('intent:choose-identity',{key:'one'});
                if(result.disposition!=='selected')throw Error(result.disposition);
              }
              return app.receipts().map(r=>r.sequence);
            }''')
            assert len(values) == 256
            assert values == sorted(values)
            assert len(set(values)) == len(values)
            assert values[-1] > 400, 'receipt counter restarted after eviction'
        finally:
            browser.close()


def test_code_editor_owns_source_job_policy_not_generic_runtime():
    ir = compile_semantic_app('code-editor')
    editor = next(n for n in ir['surfaces'][0]['nodes'] if n['nodeKind'] == 'document-editor')
    job = editor['externalJob']
    assert job['launchIntent'] == {'ref': 'intent:run-aider'}
    assert job['statusIntent'] == {'ref': 'intent:aider-jobs'}
    assert job['pendingStatus'] == 'aider-pending'
    assert job['unverifiedStatus'] == 'aider-outcome-unverified'
