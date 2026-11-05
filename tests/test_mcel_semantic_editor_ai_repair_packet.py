"""A repair proposal is a bounded read artifact, never a mutation authority."""
from playwright.sync_api import sync_playwright
from tools.mcel_phase2b_browser import launch_semantic_browser
from tests.test_mcel_semantic_editor_scm_runtime_lifecycle import _port_page
from tests.test_mcel_semantic_editor_proof_workflow import JS_SETUP


def test_repair_proposal_grounded_without_provider_or_filesystem_effect():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=_port_page(browser)
            page.evaluate(JS_SETUP)
            result=page.evaluate('''async()=>{
              const a={path:'example.txt',text:'hello world'};
              const proposal=JSON.parse(await window.__port.invoke(window.__proofReq('studio-repair-packet',{...a,filter:'all',key:''})));
              const badDraft=await window.__port.authorize(window.__proofReq('studio-repair-packet',{...a,text:'changed',filter:'all',key:''}));
              return {proposal,badDraft,calls:window.__requestLog};
            }''')
            proposal=result['proposal']
            assert proposal['execution']=='NOT_EXECUTED'
            assert proposal['aiProviderCalled'] is False
            assert proposal['sourceMutation']=='none'
            assert proposal['packet']['sourceBaseline']
            assert 'Do not treat unverified legacy DOM' in proposal['prompt']
            assert result['badDraft'] is False
            assert result['calls']==['/api/applications/editor/files','/api/applications/editor/read']
        finally:
            browser.close()


def test_other_app_cannot_claim_code_studio_proof_authority():
    from main_computer.mcel_semantic_other_apps_host import compile_semantic_app
    for app in ('file-manager','task-manager'):
        ir=compile_semantic_app(app)
        assert 'capability:scm-proof' not in {c['id'] for c in ir['capabilities']}
        assert not any(i['id'].startswith('intent:studio-proof') for i in ir['intents'])
