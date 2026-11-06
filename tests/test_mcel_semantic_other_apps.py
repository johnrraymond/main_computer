"""Real paired-source MCEL interfaces driven by controlled endpoint responses.

Network results are mocked for deterministic contract tests. This does not
claim a live Windows service or functional parity with the full legacy apps.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from main_computer.mcel_phase2c_candidate_host import CandidateHostError
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT=Path(__file__).resolve().parents[1]
APPS=("file-manager","task-manager","code-editor")

@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b,_=launch_semantic_browser(p)
        try:yield b
        finally:b.close()

def opening(browser,app,width=1100):
    page=browser.new_page(viewport={"width":width,"height":850})
    called=[]
    page.evaluate("""() => {
       window.__networkCalls=[];
       window.fetch=async (url,options) => {
         window.__networkCalls.push({url,options});
         const path=String(url).split('/api/applications/')[1];
         let result;
         if(path==='file-explorer/roots') result={ok:true,roots:window.__emptyRoots ? [] : [{id:'workspace',label:'Workspace'}]};
         else if(path==='file-explorer/list')result={ok:true,entries:[{relative_path:'README.md',name:'README.md',kind:'file'},{relative_path:'src',name:'src',kind:'directory'}]};
         else if(path==='file-explorer/read')result={ok:true,readable:true,content:'Project documentation'};
         else if(path==='task/overview')result={processes:[{pid:123,name:'server',status:'running',created_at:'2026-10-31T12:00',memory_human:'34 MB'}]};
         else if(path==='editor/files')result={entries:[{path:'README.md',name:'README.md',kind:'file'},{path:'src',name:'src',kind:'dir'}]};
         else if(path==='editor/read')result={ok:true,files:[{path:'README.md',content:'# Sample project'}]};
         else return {ok:false,status:404,json:async()=>({error:'not found'})};
         return {ok:true,status:200,json:async()=>result};
       };
    }""")
    page.set_content(render_semantic_page(app))
    assert page.locator("#mcel-workbench-status").inner_text()=="Semantic candidate ready"
    return page,called

@pytest.mark.parametrize("app",APPS)
def test_ir_is_layout_free_and_risk_scoped(app):
    ir=compile_semantic_app(app)
    assert ir["layouts"]==[]
    assert {e["risk"] for e in ir["effects"]} == ({"external-read", "source-write", "consequential"} if app=="code-editor" else {"external-read", "consequential"} if app=="task-manager" else {"external-read"})
    assert len(ir["provenance"]["frontend"]["sourceFiles"])==2
    assert ir["surfaces"][0]["interfaceIntent"]["hierarchy"]
    assert 'data-mcel-presentation="expert"' not in (ROOT/f'tools/fixtures/mcel_semantic_other_apps/{app}/application.html').read_text()

@pytest.mark.parametrize("app",APPS)
def test_reject_unexpected_authority_in_ir(app,monkeypatch):
    import main_computer.mcel_semantic_other_apps_host as host
    original=host.compile_dsl_application
    def altered(*args,**kwargs):
        report=original(*args,**kwargs)
        ir=copy.deepcopy(report.normalized_ir)
        next(c for c in ir['capabilities'] if c['risk']=='external-read')['risk']='source-write'
        from dataclasses import replace
        return replace(report,normalized_ir=ir)
    monkeypatch.setattr(host,'compile_dsl_application',altered)
    with pytest.raises(CandidateHostError,match='risk'):
        host.compile_semantic_app(app)

@pytest.mark.parametrize("width",[320,390,560,1100,1272])
@pytest.mark.parametrize("app",APPS)
def test_responsive_browser_interaction(browser,app,width):
    page,called=opening(browser,app,width)
    try:
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if app=="file-manager":
            page.locator('[data-record-key="README.md"]').wait_for()
            assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected-root")')=='workspace' 
            page.locator('[data-record-key="README.md"] button').click()
            page.get_by_role('button',name='Preview Entry').click()
            page.locator('[data-state="state:preview"]').get_by_text('Project documentation').wait_for()
            assert page.evaluate("window.__networkCalls.length")==3
        elif app=="task-manager":
            page.locator('form[data-intent="intent:refresh"] input[name="query"]').fill('server')
            page.locator('form[data-intent="intent:refresh"] button[type="submit"]').click()
            page.locator('[data-collection] [data-record-field="name"]').get_by_text('server').wait_for()
            page.locator('[data-record-key="123:2026-10-31T12:00"] button').click()
            assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected")')=='123:2026-10-31T12:00'
            # Startup now performs an initial read-only overview. The explicit
            # operator query must remain distinct from that startup snapshot.
            queries=page.evaluate('''()=>window.__networkCalls.filter(c=>String(c.url).endsWith('/task/overview'))
              .map(c=>JSON.parse(c.options.body||'{}').query||'')''')
            assert queries.count('server')==1  # Fast operator input can preempt the startup poll
        else:
            page.get_by_role('button',name='List Files').click()
            page.locator('[data-record-key="README.md"]').wait_for()
            page.locator('[data-record-key="README.md"] button').click()
            # Selecting a document performs the declared source read automatically.
            page.wait_for_function('window.__mcelWorkbench.app.readState("state:source") === "# Sample project"')
            assert page.locator('[data-mcel-document-surface]').count() == 1
            assert page.evaluate("window.__networkCalls.length")==2
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('[data-mcel-activity] li').count()>0
    finally:page.close()

def test_no_external_authority_without_selected_entry(browser):
    page,called=opening(browser,'file-manager')
    try:
        page.locator('[data-record-key="README.md"]').wait_for()
        before=page.evaluate('window.__networkCalls.length')
        assert before==2  # Startup roots/list are safe, read-only operations.
        result=page.evaluate('''async()=>window.__mcelWorkbench.app.execute('intent:preview-entry')''')
        assert result['disposition']=='refused-before-attempt'
        assert page.evaluate('window.__networkCalls.length')==before
        # Port refuses forged source-write requests even when caller bypasses UI.
        admitted=page.evaluate('''async()=>McelSemanticReadPorts.create({appId:"semantic-file-manager"}).authorize({
          appId:'semantic-file-manager',intentId:'intent:preview-entry',effectId:'effect:preview-entry.request',
          capabilityId:'capability:filesystem-read',operation:'preview',runtimeMethod:'readPreview',
          risk:'source-write',arguments:{rootId:'workspace',relativePath:'README.md'}})''')
        assert admitted is False
    finally:page.close()


def test_empty_roots_refresh_retracts_previous_directory_authority(browser):
    page,_=opening(browser,'file-manager')
    try:
        page.locator('[data-record-key="README.md"]').wait_for()
        page.evaluate('window.__emptyRoots = true')
        result=page.evaluate("""async()=>window.__mcelWorkbench.app.execute('intent:load-roots')""")
        assert result['disposition']=='completed'
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected-root")')==''
        assert page.locator('[data-record-key="README.md"]').count()==0
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:entries")') is None
        assert page.get_by_role('button',name='Browse',exact=True).is_disabled()
    finally:page.close()


def test_refresh_of_same_root_revokes_stale_preview(browser):
    page,_=opening(browser,'file-manager')
    try:
        page.locator('[data-record-key="README.md"]').wait_for()
        page.locator('[data-record-key="README.md"] button').click()
        page.get_by_role('button',name='Preview Entry').click()
        page.locator('[data-state="state:preview"]').get_by_text('Project documentation').wait_for()
        page.get_by_role('button',name='Load Roots').click()
        page.locator('[data-record-key="README.md"]').wait_for()
        page.wait_for_function('window.__mcelWorkbench.app.readState("state:preview") === undefined')
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected-entry")')==''
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected-root")')=='workspace'
        assert page.get_by_role('button',name='Preview Entry').is_disabled()
    finally:page.close()

def test_no_app_specific_layout_css():
    css=(ROOT/'main_computer/web/applications/styles/mcel-semantic-workbench.css').read_text()
    for term in ('file-manager','task-manager','code-editor','semantic-file-manager','semantic-code-editor'):
        assert term not in css
    assert 'data-mcel-purpose="navigation"' in css

def test_port_scoped_to_exact_application_and_rejects_stale_selection(browser):
    page,_=opening(browser,'file-manager')
    try:
        # Port cannot be retargeted to another app by forging the request body.
        result=page.evaluate('''async()=>{
          const port=McelSemanticReadPorts.create({appId:'semantic-file-manager'});
          return port.authorize({appId:'semantic-code-editor',intentId:'intent:list-files',
            effectId:'effect:list-files.request',capabilityId:'capability:source-reader',
            operation:'list',runtimeMethod:'listSourceFiles',risk:'external-read',arguments:{}});
        }''')
        assert result is False
        page.locator('[data-record-key="README.md"]').wait_for()
        assert page.evaluate('window.__mcelWorkbench.app.readState("state:selected-root")')=='workspace' 
        # A selected path which was not in the served directory cannot be read.
        result=page.evaluate('''async()=>{
          const port=McelSemanticReadPorts.create({appId:'semantic-file-manager'});
          return port.authorize({appId:'semantic-file-manager',intentId:'intent:preview-entry',
            effectId:'effect:preview-entry.request',capabilityId:'capability:filesystem-read',
            operation:'preview',runtimeMethod:'readPreview',risk:'external-read',
            arguments:{rootId:'workspace',relativePath:'unlisted-secret.txt'}});
        }''')
        assert result is False
    finally:page.close()
