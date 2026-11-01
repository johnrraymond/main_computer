"""Real Chromium interaction tests for Phase 2B semantic IR renderer/runtime.

The capability callbacks are explicit host test ports, not production backend proof.
"""
import json
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright
from main_computer.mcel_dsl_compiler import compile_dsl_application
from tools.mcel_phase2b_browser import BrowserUnavailable, launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'


def ir(name):
    p = ROOT / 'tools/fixtures/mcel_phase2_reference_apps' / name / 'application.js'
    report = compile_dsl_application(p)
    assert report.valid, report.diagnostics
    return report.normalized_ir


def operations_ir():
    report = compile_dsl_application(ROOT / 'tools/fixtures/mcel_phase2_operations/application.js')
    assert report.valid, report.diagnostics
    return report.normalized_ir


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p:
        try:
            b, _source = launch_semantic_browser(p)
        except BrowserUnavailable as exc:
            pytest.skip(str(exc))
        try:
            yield b
        finally:
            b.close()


def page_for(browser, application, setup):
    page = browser.new_page()
    page.set_content('<!doctype html><html><body><div id="app"></div></body></html>')
    page.add_script_tag(path=str(SCRIPT))
    page.evaluate('''({ir,setup}) => {
       window.__calls = []; window.__auth = [];
       const authorize = req => {
         window.__auth.push(req);
         if (setup === 'deny' || req.risk === 'source-write' || req.risk === 'consequential') return false;
         return true;
       };
       const invoke = async req => {
         window.__calls.push(req);
         if (setup === 'bad-result') return 'wrong-type';
         if (setup === 'failure') throw Error('backend-unavailable');
         if (req.runtimeMethod === 'evaluateExpression') {
           const match = /^(-?\\d+)\\s*\\+\\s*(-?\\d+)$/.exec(req.arguments.expression);
           if (!match) throw Error('unsupported-expression');
           return Number(match[1]) + Number(match[2]);
         }
         if (req.runtimeMethod === 'listDirectory') return [{id:'f1',name:'Readme'},{id:'f2',name:'Notes'}];
         if (req.runtimeMethod === 'readPreview') return 'Preview for ' + req.arguments.entryId;
         throw Error('test-port-does-not-support:' + req.runtimeMethod);
       };
       window.__instance = McelSemanticOperationalRuntime.mount(
         document.getElementById('app'),ir,{capabilities:{authorize,invoke}});
    }''', {'ir':application, 'setup': setup})
    return page


def test_calculator_real_click_compute_result_and_receipt(browser):
    page = page_for(browser,ir('calculator'),'normal')
    try:
        assert page.locator('[data-mcel-region]').count() == 5
        assert page.locator('[data-mcel-purpose="input"]').count() == 1
        page.locator('input[name="expression"]').fill('12 + 8')
        page.locator('form[data-intent="intent:compute"] button').click()
        page.locator('[data-state="state:result"]').get_by_text('20').wait_for()
        assert page.evaluate('window.__calls[0].capabilityId') == 'capability:arithmetic'
        assert page.evaluate('window.__auth[0].effectId') == 'effect:compute.request'
        assert page.evaluate('window.__instance.readState("state:result")') == 20
        assert page.locator('li[data-disposition="completed"]').count() == 1
        page.locator('select[data-intent="intent:switch-mode"]').select_option('graphing')
        page.wait_for_function('window.__instance.readState("state:mode") === "graphing"')
        assert page.locator('li[data-disposition="completed"]').count() == 2
    finally: page.close()


def test_no_capability_authority_means_no_call_and_no_state_write(browser):
    page=page_for(browser,ir('calculator'),'deny')
    try:
        page.locator('input[name="expression"]').fill('3 + 2')
        page.locator('form button').click()
        page.locator('li[data-disposition="refused-before-attempt"]').wait_for()
        assert page.evaluate('window.__calls.length') == 0
        assert page.evaluate('window.__instance.readState("state:result")') is None
    finally: page.close()


def test_wrong_result_type_never_publishes(browser):
    page=page_for(browser,ir('calculator'),'bad-result')
    try:
        page.locator('input[name="expression"]').fill('3 + 2')
        page.locator('form button').click()
        page.locator('li[data-disposition="failed"]').wait_for()
        assert page.evaluate('window.__instance.readState("state:result")') is None
    finally: page.close()


def test_backend_error_does_not_claim_success(browser):
    page=page_for(browser,ir('calculator'),'failure')
    try:
        page.locator('input[name="expression"]').fill('3 + 2')
        page.locator('form button').click()
        page.locator('li[data-disposition="failed"]').wait_for()
        assert page.locator('li[data-disposition="completed"]').count() == 0
    finally:page.close()


def test_file_manager_list_select_preview_and_out_of_collection_refusal(browser):
    page=page_for(browser,operations_ir(),'normal')
    try:
        page.locator('button[data-intent="intent:list-directory"]').click()
        page.locator('[data-collection="surface-node:phase2-operations.entries"] [data-record-key="f1"]').wait_for()
        assert page.locator('[data-collection="surface-node:phase2-operations.entries"] [data-record-key]').count()==2
        page.locator('[data-collection="surface-node:phase2-operations.entries"] [data-select-key="f2"]').click()
        page.wait_for_function('window.__instance.readState("state:selected-entry") === "f2"')
        page.locator('button[data-intent="intent:read-preview"]').click()
        page.locator('[data-state="state:preview"]').get_by_text('Preview for f2').wait_for()
        assert page.evaluate('window.__calls.at(-1).arguments.entryId') == 'f2'
        page.evaluate('window.__instance.execute("intent:select-entry", {key:"invented-file"})')
        page.locator('li[data-disposition="refused-before-attempt"]').wait_for()
        assert page.evaluate('window.__instance.readState("state:selected-entry")') == 'f2'
    finally:page.close()


def test_no_implicit_capability_and_no_invented_auth_on_all_four(browser):
    for name in ('file-manager','task-manager','code-editor','calculator'):
        page=browser.new_page()
        try:
            page.set_content('<div id="app"></div>');page.add_script_tag(path=str(SCRIPT))
            data=ir(name)
            assert page.evaluate('''(ir)=>{
              const el=document.querySelector('#app');
              const instance=McelSemanticOperationalRuntime.mount(el,ir);
              return el.querySelectorAll('[data-mcel-region]').length > 1 &&
               el.getAttribute('data-mcel-semantic-operational') === ir.application.appId;
            }''',data)
        finally:page.close()


def test_preflight_rejects_fabricated_effect_without_touching_dom(browser):
    data=ir('calculator')
    data['effects'][0]['risk']='consequential'
    page=browser.new_page()
    try:
        page.set_content('<div id="app">ORIGINAL</div>');page.add_script_tag(path=str(SCRIPT))
        outcome=page.evaluate('''ir=>{
            const root=document.querySelector('#app');
            try {McelSemanticOperationalRuntime.mount(root,ir);return 'BAD';}
            catch(e){return {message:String(e),html:root.innerHTML};}
        }''',data)
        assert 'invalid-capability' in outcome['message']
        assert outcome['html']=='ORIGINAL'
    finally:page.close()


def test_real_existing_calculator_core_integrates_without_host_bound_ui(browser):
    """Run the existing production Calculator evaluator, not a mocked math stub."""
    page=browser.new_page()
    try:
        page.set_content('<div id="app"></div>')
        folder=ROOT/'main_computer/web/applications/scripts'
        page.add_script_tag(path=str(folder/'calculator-core.js'))
        page.add_script_tag(path=str(folder/'mcel-calculator-core-capability-port.js'))
        page.add_script_tag(path=str(SCRIPT))
        page.evaluate('''ir => {
          window.__instance = McelSemanticOperationalRuntime.mount(
            document.querySelector('#app'),ir,
            {capabilities:McelCalculatorCoreCapabilityPort.create()});
        }''',ir('calculator'))
        page.locator('input[name="expression"]').fill('2+3*4')
        page.locator('form button').click()
        page.locator('[data-state="state:result"]').get_by_text('14').wait_for()
        assert page.evaluate('window.__instance.readState("state:result")')==14
        assert page.locator('li[data-disposition="completed"]').count()==1
        page.locator('input[name="expression"]').fill('1/2')
        page.locator('form button').click()
        page.locator('li[data-disposition="failed"]').wait_for()
        assert page.evaluate('window.__instance.readState("state:result")')==14
    finally:page.close()


def test_no_html_injection_through_untrusted_capability_response(browser):
    page=browser.new_page()
    try:
        page.set_content('<div id="app"></div>');page.add_script_tag(path=str(SCRIPT))
        page.evaluate('''ir=>{
          window.__instance=McelSemanticOperationalRuntime.mount(document.querySelector('#app'),ir,{
            capabilities:{authorize:async()=>true,invoke:async req=>
              req.runtimeMethod === 'listDirectory'
              ? [{id:'safe',name:'<img src=x onerror=alert(1)>'}]
              : '<script>alert(1)</script>'}
          });
        }''', operations_ir())
        page.locator('button[data-intent="intent:list-directory"]').click()
        page.locator('[data-record-field="name"]').get_by_text('<img src=x onerror=alert(1)>').first.wait_for()
        assert page.locator('img').count()==0
        assert page.locator('[data-collection] script').count()==0
    finally:page.close()


def test_disposed_runtime_ignores_inflight_results(browser):
    page=browser.new_page()
    try:
        page.set_content('<div id="app"></div>');page.add_script_tag(path=str(SCRIPT))
        page.evaluate('''ir=>{
          window.__settle=null;
          window.__instance=McelSemanticOperationalRuntime.mount(document.querySelector('#app'),ir,{
            capabilities:{authorize:async()=>true,
              invoke:async()=>new Promise(resolve=>{window.__settle=resolve;})}
          });
        }''',ir('calculator'))
        page.locator('input[name="expression"]').fill('4+4')
        page.locator('form button').click()
        page.wait_for_function('typeof window.__settle === "function"')
        page.evaluate('window.__instance.unmount();window.__settle(8)')
        page.wait_for_timeout(30)
        assert page.locator('[data-state="state:result"]').count()==0
        assert page.evaluate('window.__instance.readState("state:result")') is None
    finally:page.close()
