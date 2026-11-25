"""Paired source -> compiler -> operational runtime -> shared document element."""
import copy
from pathlib import Path
import pytest
from main_computer.mcel_dsl_compiler import compile_dsl_application
from tools.mcel_phase2b_browser import launch_semantic_browser, BrowserUnavailable

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'tools/fixtures/mcel_document_surface_native/application.js'
WEB=ROOT/'main_computer/web/applications'
LEFT='surface-node:document-surface-native-probe.left'
RIGHT='surface-node:document-surface-native-probe.right'
SCRIPTS=('mcel-document-engine.js','mcel-element-registry.js','mcel-elements-core.js',
         'mcel-document-surface-element.js','mcel-semantic-operational-runtime.js')

@pytest.fixture(scope='module')
def ir():
    report=compile_dsl_application(SOURCE)
    assert report.valid, report.diagnostics
    return report.normalized_ir

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
    p=browser.new_page(viewport={'width':1280,'height':900})
    errors=[]
    p.on('pageerror',lambda error: errors.append(str(error)))
    p.set_content('<div id="app">ORIGINAL</div>')
    for css in ('document.css','mcel-document-surface-element.css'):
        p.add_style_tag(path=str(WEB/'styles'/css))
    for script in SCRIPTS: p.add_script_tag(path=str(WEB/'scripts'/script))
    try: yield p
    finally:
        p.close()
        assert not errors,errors

def mount(page,ir):
    page.evaluate('''({ir,left,right})=>{
      window.events=[];window.calls=[];window.auth=[];
      const root=document.querySelector('#app');
      root.addEventListener('mcel:document-surface-change',e=>events.push(e.detail));
      window.app=McelSemanticOperationalRuntime.mount(root,ir,{capabilities:{
        authorize:req=>{auth.push(req);return true},
        invoke:async req=>{calls.push(req);return 'Observed'}}});
      window.a=app.getDocumentSurface(left);window.b=app.getDocumentSurface(right);
    }''',{'ir':ir,'left':LEFT,'right':RIGHT})

def test_compiled_contract(ir):
    nodes={n['id']:n for n in ir['surfaces'][0]['nodes']}
    assert nodes[LEFT]['nodeKind']=='document-surface'
    assert nodes[LEFT]['elementId']=='element.authoring.document-surface'
    assert nodes[LEFT]['contentState']=={'ref':'state:left-html'}
    assert nodes[RIGHT]['contentState']=={'ref':'state:right-html'}
    assert nodes[LEFT]['modeState']=={'ref':'state:page-mode'}
    assert 'modeState' not in nodes[RIGHT]
    assert len(ir['effects'])==1  # only explicit inspection, no implicit document I/O

@pytest.mark.parametrize('old,new,code',[
    ('value:right,label','value:left,label','MCEL_DSL_DOCUMENT_SURFACE_VALUE_SHARED'),
    ('value:left,mode,label','value:result,mode,label','MCEL_DSL_DOCUMENT_SURFACE_VALUE_INVALID'),
    ('value:left,mode,label','value:left,mode:left,label','MCEL_DSL_DOCUMENT_SURFACE_MODE_INVALID'),
    ('value:left,mode,label','value:left,mode,storage:true,label','MCEL_DSL_UNKNOWN_OPTION'),
    ('status:[dsl.surface.action','status:[dsl.surface.form("competing-writer",{fields:{html:text},fieldState:{html:left}}),dsl.surface.action','MCEL_DSL_DOCUMENT_SURFACE_VALUE_WRITER_CONFLICT'),
])
def test_invalid_source(tmp_path,old,new,code):
    s=SOURCE.read_text();assert old in s
    f=tmp_path/'application.js';f.write_text(s.replace(old,new))
    (tmp_path/'application.html').write_text(SOURCE.with_suffix('.html').read_text())
    result=compile_dsl_application(f)
    assert not result.valid
    assert code in str(result.diagnostics),result.diagnostics

def test_native_edit_and_governed_read(page,ir):
    mount(page,ir)
    assert page.locator('.mcel-document-surface-element').count()==2
    assert page.evaluate('a.getState().html')==page.evaluate('app.readState("state:left-html")')
    initial_b=page.evaluate('b.getState()')
    page.evaluate('''()=>{
      const node=document.querySelector('[data-mcel-rich-document-host] strong');
      const range=document.createRange();range.selectNodeContents(node.firstChild);
      getSelection().removeAllRanges();getSelection().addRange(range);window.anchor=a.getSelection();
      a.mutate({operation:'replace-selection',expectedRevision:anchor.revision,anchor,text:'article'});
    }''')
    assert '<strong>article</strong>' in page.evaluate('app.readState("state:left-html")')
    assert page.evaluate('events.at(-1).nodeId')==LEFT
    assert page.evaluate('b.getState()')==initial_b
    with pytest.raises(Exception,match='DOCUMENT_SURFACE_REVISION_CONFLICT'):
        page.evaluate("a.mutate({operation:'append',expectedRevision:anchor.revision,html:'<p>stale</p>'})")
    page.evaluate('''()=>{const el=document.querySelectorAll('.mc-page-content')[1];el.focus();const range=document.createRange();range.selectNodeContents(el);range.collapse(false);getSelection().removeAllRanges();getSelection().addRange(range)}''')
    page.keyboard.type(' manual')
    assert 'manual' in page.evaluate('app.readState("state:right-html")')
    assert 'manual' not in page.evaluate('app.readState("state:left-html")')
    assert page.evaluate('calls.length')==0
    page.evaluate('app.execute("intent:inspect")')
    assert page.evaluate('calls[0].arguments.html')==page.evaluate('a.getState().html')
    assert page.evaluate('auth[0].capabilityId')=='capability:inspector'
    assert page.evaluate('app.readState("state:inspection")')=='Observed'
    assert page.evaluate('a.undo().html')==page.evaluate('app.readState("state:left-html")')
    assert page.evaluate('a.redo().html')==page.evaluate('app.readState("state:left-html")')

def test_layout_state_and_lifecycle(page,ir):
    mount(page,ir)
    original=page.evaluate('a.getState()')
    page.evaluate('app.execute("intent:page-mode",{choice:"endless"})')
    assert page.evaluate('a.getState().layout.view.mode')=='endless'
    assert page.evaluate('b.getState().layout.view.mode')=='paged'
    assert page.evaluate('a.getState().revision')==original['revision']
    assert page.evaluate('a.getState().html')==original['html']
    with pytest.raises(Exception,match='DOCUMENT_SURFACE_MODE_STATE_OWNED'):
        page.evaluate('''()=>{const l=a.getState().layout;l.view.mode='paged';a.setLayout(l)}''')
    page.evaluate('app.unmount()')
    assert page.locator('.mcel-document-surface-element').count()==0
    assert page.evaluate('(id)=>app.getDocumentSurface(id)',LEFT) is None
    with pytest.raises(Exception,match='DOCUMENT_SURFACE_DESTROYED'): page.evaluate('a.getState()')
    mount(page,ir)
    assert page.evaluate('a.getState().revision')==0

def test_preflight_refuses_bad_ir_and_missing_renderer(page,ir):
    wrong=copy.deepcopy(ir)
    nodes={n['id']:n for n in wrong['surfaces'][0]['nodes']}
    nodes[RIGHT]['contentState']=nodes[LEFT]['contentState']
    with pytest.raises(Exception,match='document-surface-value-shared'):
        mount(page,wrong)
    assert page.locator('#app').inner_text()=='ORIGINAL'
    page.evaluate('delete window.McelElementRegistry')
    with pytest.raises(Exception,match='document-surface-renderer-missing'): mount(page,ir)
    assert page.locator('#app').inner_text()=='ORIGINAL'


def test_initial_content_is_sanitized_before_state_publication(page,ir):
    data=copy.deepcopy(ir)
    state=next(s for s in data['states'] if s['id']=='state:left-html')
    state['initial']='<p>Safe<script>window.injection=1</script><strong>text</strong></p>'
    mount(page,data)
    assert page.evaluate('typeof window.injection')=='undefined'
    assert page.evaluate('app.readState("state:left-html")')=='<p>Safe<strong>text</strong></p>'
    assert page.evaluate('events.length')==0

def test_compiled_surface_is_recognized_by_color_law(page,ir):
    for script in ('mcel-contract.js','mcel-law-registry.js','mcel-style-law.js','mcel-semantic-color-presentation.js'):
        page.add_script_tag(path=str(WEB/'scripts'/script))
    plan=page.evaluate('ir=>McelSemanticColorPresentation.validate({ir,law:McelLabStyleLaw,inference:true})',ir)
    assert plan['status']=='valid',plan
    assert next(d for d in plan['declarations'] if d['id']==LEFT)['color']['role']=='surface.panel'


def test_standalone_compiled_preview(browser):
    from tools.mcel_document_surface_native_preview import render_preview
    page=browser.new_page()
    errors=[]
    page.on('pageerror',lambda error: errors.append(str(error)))
    try:
        page.set_content(render_preview())
        assert page.locator('#diagnostic').inner_text()=='Native MCEL probe mounted'
        assert page.locator('.mcel-document-surface-element').count()==2
        page.evaluate('nativeDocumentProbe.execute("intent:inspect")')
        assert 'HTML characters in memory' in page.evaluate('nativeDocumentProbe.readState("state:inspection")')
        assert not errors,errors
    finally: page.close()
