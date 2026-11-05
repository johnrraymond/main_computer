"""In-memory SCM kernel fixtures. Legacy DOM/Monaco fixtures remain unverified."""
from playwright.sync_api import sync_playwright
from tools.mcel_phase2b_browser import launch_semantic_browser
from tests.test_mcel_semantic_editor_scm_runtime_lifecycle import _port_page
from tests.test_mcel_semantic_editor_proof_workflow import JS_SETUP


def test_kernel_regression_run_replays_in_isolation_without_network_or_source_write():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=_port_page(browser)
            page.evaluate(JS_SETUP)
            result=page.evaluate('''async()=>{
              const a={path:'example.txt',text:'hello world'};
              const report=JSON.parse(await window.__port.invoke(window.__proofReq('studio-regression-run',a)));
              const proof=JSON.parse(await window.__port.invoke(window.__proofReq('studio-proof-export',{...a,filter:'all',key:''})));
              return {report,proof,calls:window.__requestLog};
            }''')
            report=result['report']
            assert report['kind']=='mcel-semantic-scm-isolated-kernel-regressions'
            assert report['scope']=='isolated-in-memory-kernel'
            assert report['passed']==5 and report['failed']==0, report
            assert report['ok'] is True
            assert all(x['deterministic'] and x['noUnexpectedMutation'] for x in report['results'])
            assert {x['disposition'] for x in report['results']}=={'PASS','BLOCKED'}
            assert report['legacyDomFixtures']=='not-executed'
            assert report['liveMonacoFixtures']=='unverified'
            assert result['proof']['lastKernelRegressions']['passed']==5
            assert result['calls']==['/api/applications/editor/files','/api/applications/editor/read']
        finally:
            browser.close()
