"""Native Document Editor candidate: isolated HTTP storage and real browser composition."""
from pathlib import Path
from types import SimpleNamespace
from http import HTTPStatus
import json
import hashlib
import ast
from urllib.parse import urlsplit
import pytest
from main_computer.mcel_document_candidate_host import render_document_candidate, compile_document_candidate
from main_computer.mcel_document_candidate_drafts import handle_candidate_draft
from main_computer.mcel_application_packages import build_application_package_catalog
from tools.mcel_phase2b_browser import launch_semantic_browser, BrowserUnavailable

ROOT=Path(__file__).resolve().parents[1]

class Handler:
    def __init__(self,root): self.server=SimpleNamespace(debug_root=root)
    # Stand-in for the incumbent path authority, not for candidate persistence.
    def _docs_drafts_root(self): return (self.server.debug_root/'runtime/document_editor_drafts').resolve()
    def _docs_draft_path(self,path):
        root=self._docs_drafts_root()
        if not path:return root/'scratchpad.json',''
        pretty=self.server.debug_root/'pretty_docs';source=(pretty/path).resolve()
        if not source.is_relative_to(pretty) or not source.is_file():raise ValueError('Invalid Pretty Docs path')
        rel=source.relative_to(pretty).as_posix()
        return root/'pretty_docs'/(hashlib.sha256(rel.encode()).hexdigest()+'.json'),rel
    def _read_json(self): return self.body
    def _send_json(self,value,status=HTTPStatus.OK): self.response=(status,value)
    def run(self,body,write=False):
        self.body=body;handle_candidate_draft(self,write=write);return self.response


def test_candidate_drafts_isolated_and_conflict_checked(tmp_path):
    (tmp_path/'pretty_docs').mkdir();(tmp_path/'pretty_docs/test.md').write_text('source')
    h=Handler(tmp_path)
    legacy,_=h._docs_draft_path('test.md');legacy.parent.mkdir(parents=True);legacy.write_text('{"html":"incumbent"}')
    assert h.run({'path':'test.md'})[1]['exists'] is False
    body={'path':'test.md','expected_token':'','html':'<p>first</p>','layout':{}}
    code,receipt=h.run(body,True);assert code==200 and receipt['token']
    assert h.run({**body,'html':'stale'},True)[0]==409
    assert h.run({'path':'test.md'})[1]['html']=='<p>first</p>'
    assert h.run({**body,'expected_token':receipt['token'],'html':'<p>second</p>'},True)[0]==200
    assert json.loads(legacy.read_text())['html']=='incumbent'
    assert (tmp_path/'pretty_docs/test.md').read_text()=='source'
    assert h.run({'path':'../test.md'})[0]==400
    assert h.run({'path':''})[1]['exists'] is False


def test_canonical_package_and_candidate_route():
    ir=compile_document_candidate();assert ir['application']['appId']=='document'
    assert build_application_package_catalog(ROOT).ok
    page=render_document_candidate();assert 'Document Editor ready' in page
    assert 'document-app.js' not in page
    assert '"appId":"document"' in page and '"eligible":true' in page
    assert 'candidate-draft/write' in page
    from main_computer.mcel_applications_registry import read_registry
    assert next(a for a in read_registry(ROOT)['apps'] if a['id']=='document')['mount']=='semantic'
    # Route selection is pure; extract it without booting unrelated game demos.
    tree=ast.parse((ROOT/'main_computer/viewport_route_dispatch.py').read_text(encoding='utf-8'))
    nodes=[n for n in tree.body if (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'_SEMANTIC_CANONICAL_APPLICATIONS','_SEMANTIC_TASK_TABS'} for t in n.targets)) or (isinstance(n,ast.FunctionDef) and n.name=='_semantic_canonical_route')]
    ns={'urlsplit':urlsplit};exec(compile(ast.Module(body=nodes,type_ignores=[]),'route-selection','exec'),ns)
    for path in ('/applications/document','/apps/document','/app/document'):
        assert ns['_semantic_canonical_route'](path)==('document',None)

STUB='''<script>
window.calls=[];window.confirm=()=>true;window.storage={};window.legacy={};window.aiChanges=null;window.aiWait=null;
window.fetch=async(url,opts)=>{const a=JSON.parse(opts.body);calls.push([url,a]);let d={ok:true};
if(url.endsWith('/files'))d.documents=[{path:'sample.md',title:'Sample',kind:'markdown'},{path:'other.md',title:'Other',kind:'markdown'},...Object.keys(storage).filter(p=>p.startsWith('new:')).map(path=>({path,title:storage[path].metadata?.name||'Untitled Document',kind:'draft'}))];
else if(url.endsWith('/candidate-draft/read'))Object.assign(d,{path:a.path,exists:!!storage[a.path],token:storage[a.path]?.token||'',...storage[a.path]});
else if(url.endsWith('/draft/read'))Object.assign(d,{path:a.path,exists:!!legacy[a.path],...legacy[a.path]});
else if(url.endsWith('/read'))Object.assign(d,{path:a.path,title:a.path,kind:'markdown',content:'# Sample\\nHello world',content_hash:'abc',mtime:1});
else if(url.endsWith('/candidate-draft/write')){storage[a.path]={html:a.html,layout:a.layout,metadata:a.metadata,token:'token-'+calls.length};Object.assign(d,{path:a.path,...storage[a.path]});}
else if(url.endsWith('/ai')){if(window.aiWait)await window.aiWait;d.suggestion=a.preferred_operation==='transform_document'?{operation:'transform_document',changes:window.aiChanges||{},rationale:'test'}:{operation:'replace_selection',replacement_text:'revised',rationale:'test'};}
else if(url.endsWith('/export/pdf'))return new Response('%PDF-1.4 mock',{status:200,headers:{'Content-Type':'application/pdf'}});
else throw Error('Unexpected endpoint '+url);
return new Response(JSON.stringify(d),{status:200,headers:{'Content-Type':'application/json'}});};
</script>'''

@pytest.fixture(scope='module')
def browser():
    pw=pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as p:
        try: b,_=launch_semantic_browser(p)
        except BrowserUnavailable as exc: pytest.skip(str(exc))
        try: yield b
        finally: b.close()

@pytest.fixture
def page(browser):
    page=browser.new_page(viewport={'width':1440,'height':1000});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(render_document_candidate().replace('</head>',STUB+'</head>'))
    page.wait_for_timeout(500)
    assert 'ready' in page.locator('#mcel-workbench-status').inner_text()
    page.evaluate('window.app=__mcelWorkbench.app;window.s=app.getDocumentSurface("surface-node:document.draft")')
    yield page
    page.close();assert not errors,errors


def execute(page, name, args=None, success=True):
    receipt=page.evaluate('([name,args])=>app.execute("intent:"+name,args)',[name,args or {}])
    assert (receipt['disposition']=='completed')==success, receipt
    return receipt


def open_sample(page):
    execute(page,'list-documents')
    row=page.locator('[data-record-key="sample.md"]');assert 'Sample' in row.inner_text()
    row.get_by_role('button').click()
    execute(page,'open-document')
    assert 'Hello world' in page.evaluate('s.getState().html')


def select_text(page):
    page.evaluate('''()=>{const p=document.querySelector('.mc-page-content p');p.closest('.mc-page-content').focus();const r=document.createRange();r.selectNodeContents(p);getSelection().removeAllRanges();getSelection().addRange(r);}''')


def test_open_edit_save_reopen_and_no_chrome_serialization(page):
    open_sample(page)
    assert page.locator('.mcel-document-surface-element').count()==1
    assert page.locator('.mc-page-content [data-mcel-color-state]').count()==0
    assert page.evaluate('getComputedStyle(document.querySelector(".mc-page-content h1")).webkitTextFillColor')!='rgba(0, 0, 0, 0)'
    select_text(page);page.get_by_role('button',name='Bold',exact=True).click()
    assert '<b>Hello world</b>' in page.evaluate('s.getState().html') or '<strong>Hello world</strong>' in page.evaluate('s.getState().html')
    execute(page,'open-scratchpad',success=False)
    execute(page,'save-candidate-draft')
    saved=page.evaluate('storage["sample.md"].html')
    execute(page,'open-scratchpad');assert not page.evaluate('s.getState().canUndo')
    execute(page,'open-document');assert page.evaluate('s.getState().html')==saved
    assert not page.evaluate('calls.some(([url])=>url.endsWith("/docs/draft/write"))')


def test_ai_proposal_and_stale_revision(page):
    open_sample(page);select_text(page);execute(page,'lock-selection')
    before=page.evaluate('s.getState().html');execute(page,'request-ai-proposal',{'instruction':'Revise'})
    assert page.evaluate('s.getState().html')==before
    execute(page,'apply-ai-proposal');assert 'revised' in page.evaluate('s.getState().html')
    select_text(page);execute(page,'lock-selection');execute(page,'request-ai-proposal',{'instruction':'Revise'})
    page.evaluate('s.mutate({operation:"append",expectedRevision:s.getState().revision,html:"<p>new</p>"})')
    execute(page,'apply-ai-proposal',success=False)


def test_unsupported_import_does_not_change_document(page):
    open_sample(page);before=page.evaluate('s.getState()')
    page.evaluate('storage["other.md"]={token:"other",html:"<p style=\\"color:red\\">unsafe fidelity</p>"}')
    page.locator('[data-record-key="other.md"] button').click()
    receipt=execute(page,'open-document',success=False)
    assert 'IMPORT_UNSUPPORTED' in receipt['reason']
    assert page.evaluate('s.getState()')==before


def test_pdf_payload_and_math_roundtrip(page):
    open_sample(page)
    assert page.evaluate('s.insertMath("x^2","paragraph")')
    execute(page,'save-candidate-draft');saved=page.evaluate('s.getState().html')
    execute(page,'open-scratchpad');execute(page,'open-document');assert page.evaluate('s.getState().html')==saved
    execute(page,'export-pdf')
    assert page.evaluate('calls.at(-1)[1].pages.length')>=1


def test_workspace_scroll_outline_and_chrome_boundary(page):
    page.set_viewport_size({'width':1270,'height':590})
    page.evaluate("s.loadDocument({expectedRevision:s.getState().revision,html:Array.from({length:8},(_,i)=>'<h1>Chapter '+(i+1)+'</h1>'+('<p>'+'Long manuscript paragraph. '.repeat(12)+'</p>').repeat(5)).join('')})")
    page.wait_for_function("document.querySelectorAll('.mc-page').length > 2")
    canvas=page.locator('.document-canvas'); bar=page.locator('.mcel-document-surface-toolbar'); ruler=page.locator('.mcel-document-ruler')
    assert canvas.evaluate('e=>e.clientHeight > 250 && e.scrollHeight > e.clientHeight')
    assert page.evaluate('document.documentElement.scrollHeight===document.documentElement.clientHeight')
    assert page.locator('[data-mcel-zone="workspace"]').evaluate('e=>e.scrollHeight===e.clientHeight')
    before=bar.bounding_box();ruler_before=ruler.bounding_box()
    canvas.evaluate('e=>e.scrollTop=e.scrollHeight/2');assert canvas.evaluate('e=>e.scrollTop')>0
    assert bar.bounding_box()==before and ruler.bounding_box()==ruler_before
    assert page.locator('[data-document-outline] button').count()==8
    page.locator('[data-document-outline] button').first.click();assert canvas.evaluate('e=>e.scrollTop')<100
    page.locator('[data-document-outline] button').last.click();assert canvas.evaluate('e=>e.scrollTop')>100
    old=canvas.bounding_box()['width'];page.get_by_role('button',name='Library / outline').click();assert canvas.bounding_box()['width']>old
    old=canvas.bounding_box()['width'];page.get_by_role('button',name='Document AI',exact=True).click();assert canvas.bounding_box()['width']>old
    page.wait_for_function("getComputedStyle(document.querySelector('[data-mcel-tng-stationary-glass]')).clipPath.includes('evenodd')")
    before=page.evaluate('s.getState().html');page.get_by_role('button',name='Zoom in',exact=True).click();assert page.evaluate('s.getState().html')==before
    assert page.locator('.mc-page').first.evaluate('e=>getComputedStyle(e).backgroundColor')=='rgb(255, 254, 250)'


def test_full_state_proposal_validated_and_bound_to_ui(page):
    open_sample(page)
    before=page.evaluate('s.getState()')
    page.evaluate("""window.aiChanges={html:'<h1>New</h1><p><b>Body</b></p>',metadata:{name:'Changed',date:'1929-09-04',type:'memorandum',author:'Author',description:'Description'},layout:{layout:{mode:'custom',preset:null,custom:{widthPx:900,heightPx:1200},margins:{top:72,right:80,bottom:88,left:64}},view:{mode:'endless',zoom:1.25,showPageBreaks:false}}}""")
    page.get_by_role('button',name='Lock Entire Document',exact=True).click()
    page.wait_for_function("app.readState('state:proposal').startsWith('Locked: entire document')")
    form=page.locator('[data-mcel-node="surface-node:document.ai-instruction"] form');form.locator('input,textarea').first.fill('Transform');form.locator('button[type=submit]').click()
    page.wait_for_function("app.readState('state:proposal').startsWith('Proposed document changes:')")
    assert page.evaluate('s.getState()')==before
    assert page.evaluate('calls.at(-1)[1].document.layout')==before['layout']
    page.get_by_role('button',name='Apply AI Proposal',exact=True).click();page.wait_for_function("app.readState('state:proposal').startsWith('Document state applied')")
    assert page.evaluate('s.getState().layout.layout.custom.widthPx')==900
    assert page.evaluate('s.getState().layout.view.zoom')==1.25
    assert page.evaluate('__mcelWorkbench.getDocumentMetadata().date')=='1929-09-04'
    assert page.locator('[data-document-metadata] [name=name]').input_value()=='Changed'
    assert page.evaluate('app.readState("state:draft")')==page.evaluate('s.getState().html')
    execute(page,'save-candidate-draft');execute(page,'open-scratchpad');execute(page,'open-document')
    assert page.evaluate('__mcelWorkbench.getDocumentMetadata().name')=='Changed'
    assert page.evaluate('s.getState().layout.layout.custom.widthPx')==900
    execute(page,'lock-document');execute(page,'request-ai-proposal',{'instruction':'Transform'})
    page.evaluate('s.setLayout({...s.getState().layout,view:{mode:"paged",zoom:1,showPageBreaks:true}})')
    execute(page,'apply-ai-proposal',success=False)
    page.evaluate("window.aiChanges={metadata:{name:'Unsafe'},html:'<script>alert(1)</script>'}")
    execute(page,'lock-document');execute(page,'request-ai-proposal',{'instruction':'Invalid'},success=False)
    assert page.evaluate('__mcelWorkbench.getDocumentMetadata().name')=='Changed'


def test_new_document_ui_blank_and_queued_creation(page):
    page.get_by_role('button',name='New Document',exact=True).click()
    dialog=page.locator('[data-document-new-dialog]')
    dialog.get_by_role('button',name='OK',exact=True).click();dialog.wait_for(state='hidden')
    assert page.evaluate('Object.keys(storage).filter(p=>p.startsWith("new:")).length')==1
    assert not page.evaluate('calls.some(([url])=>url.endsWith("/ai"))')
    page.evaluate("()=>{window.aiChanges={metadata:{name:'Generated title'},html:'<h1>Generated</h1>'};window.aiWait=new Promise(r=>window.releaseAI=r);}")
    page.get_by_role('button',name='New Document',exact=True).click()
    dialog.locator('[name=prompt]').fill('Write a document about banking')
    dialog.get_by_role('button',name='Current',exact=True).click()
    dialog.get_by_role('button',name='OK',exact=True).click();dialog.wait_for(state='hidden')
    page.wait_for_function("calls.some(([url])=>url.endsWith('/ai'))")
    assert page.evaluate('Object.keys(storage).filter(p=>p.startsWith("new:")).length')==2
    context=page.evaluate("calls.find(([url])=>url.endsWith('/ai'))[1].document.creation")
    assert context['defaults']==context['values'] and context['explicit']==['date']
    assert context['prompt']=='Write a document about banking'
    assert page.locator('[data-document-creation-status]').inner_text().startswith('AI creation proposal queued')
    page.evaluate('releaseAI()')
    page.wait_for_function("document.querySelector('[data-document-creation-status]').textContent.startsWith('AI creation proposal ready')")
    execute(page,'apply-ai-proposal')
    assert page.evaluate('__mcelWorkbench.getDocumentMetadata().name')=='Generated title'
    assert 'Generated' in page.evaluate('s.getState().html')
    execute(page,'save-candidate-draft');execute(page,'list-documents')
    assert page.get_by_text('Generated title',exact=True).count()>=1


def test_new_storage_identity_and_metadata(tmp_path):
    from main_computer.mcel_document_candidate_drafts import created_documents
    h=Handler(tmp_path);path='new:'+'a'*32
    data={'path':path,'expected_token':'','html':'<p></p>','layout':{},'metadata':{'name':'Untitled Document','date':'1929-09-04'}}
    code,created=h.run(data,True);assert code==200
    code,updated=h.run({**data,'expected_token':created['token'],'metadata':{'name':'Renamed'}},True);assert code==200
    assert updated['created_at']==created['created_at']
    assert created_documents(h)==[{'path':path,'title':'Renamed','kind':'draft'}]
    assert h.run({'path':path})[1]['metadata']=={'name':'Renamed'}
    assert h.run({**data,'metadata':{'id':'malicious'}},True)[0]==409
    assert h.run({**data,'expected_token':updated['token'],'metadata':{'id':'malicious'}},True)[0]==400
