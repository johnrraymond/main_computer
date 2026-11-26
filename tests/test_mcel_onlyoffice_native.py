"""Native route, MCEL capability dispatch and isolated-editor bridge acceptance."""
from pathlib import Path
import ast
from urllib.parse import urlsplit
import pytest
from main_computer.mcel_onlyoffice_application import render_onlyoffice_page
from main_computer.mcel_applications_registry import read_registry
from main_computer.mcel_scaffolding.package_validator import validate_package_path
from tools.mcel_phase2b_browser import launch_semantic_browser, BrowserUnavailable

ROOT=Path(__file__).resolve().parents[1]


def test_native_route_and_package():
    assert validate_package_path(ROOT/'mcel_apps/onlyoffice').ok
    assert next(x for x in read_registry(ROOT)['apps'] if x['id']=='onlyoffice')['mount']=='semantic'
    tree=ast.parse((ROOT/'main_computer/viewport_route_dispatch.py').read_text(encoding='utf-8'))
    nodes=[n for n in tree.body if (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in {'_SEMANTIC_CANONICAL_APPLICATIONS','_SEMANTIC_TASK_TABS'} for t in n.targets)) or (isinstance(n,ast.FunctionDef) and n.name=='_semantic_canonical_route')]
    ns={'urlsplit':urlsplit};exec(compile(ast.Module(body=nodes,type_ignores=[]),'route-selection','exec'),ns)
    for prefix in ('applications','app','apps'):
        assert ns['_semantic_canonical_route']('/'+prefix+'/onlyoffice')==('onlyoffice',None)
    page=render_onlyoffice_page(ROOT)
    assert 'mcel-onlyoffice-root' in page and 'mountHardenedOnlyOffice' not in page and 'onlyofficeLoadApiScript' not in page
    assert '"appId":"onlyoffice"' in page and '"eligible":true' in page
    spa=(ROOT/'main_computer/web/applications.html').read_text(encoding='utf-8')
    assert 'applications/scripts/onlyoffice-app.js' not in spa and 'applications/apps/onlyoffice.html' not in spa


@pytest.fixture(scope='module')
def browser():
    pw=pytest.importorskip('playwright.sync_api')
    with pw.sync_playwright() as p:
        try:b,_=launch_semantic_browser(p)
        except BrowserUnavailable as exc:pytest.skip(str(exc))
        try:yield b
        finally:b.close()


STUB='''<script>
for(const name of ['localStorage','sessionStorage']){const values=new Map();Object.defineProperty(window,name,{value:{getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k),clear:()=>values.clear(),key:i=>[...values.keys()][i]??null,get length(){return values.size;}}});}
window.calls=[];window.books=[{path:'Probe.xlsx',bytes:12},{path:'Book.xlsx',bytes:12},{path:'Book2.xlsx',bytes:12}];window.badConfig=false;window.saveError=0;
window.fetch=async(url,opts)=>{const a=JSON.parse(opts.body||'{}');calls.push([url,a]);let d={ok:true};
if(url.endsWith('/files'))d.files=books;
else if(url.endsWith('/status'))Object.assign(d,{server_probe:{ok:true},public_url:'https://onlyoffice.main-computer.localhost:18086'});
else if(url.endsWith('/config'))Object.assign(d,{hardened_editor_origin:!badConfig,public_url:'https://onlyoffice.main-computer.localhost:18086',config:{document:{key:'shared'}}});
else if(url.endsWith('/create')||url.endsWith('/upload')){if(books.some(b=>b.path.toLowerCase()===a.path.toLowerCase()))return {ok:false,status:400,json:async()=>({ok:false,error:'OVERWRITE_REQUIRES_EXPLICIT_SESSION_RECOVERY'})};books.push({path:a.path,bytes:15});d.path=a.path;}
else if(url.endsWith('/force-save'))d.command_response={error:saveError};
else throw Error('Unexpected endpoint '+url);
return {ok:true,json:async()=>d};};
</script>'''


def assert_workspace_geometry(page, advanced=False):
    def region(name):
        box=page.locator(f'[data-mcel-region="interface-region:onlyoffice.{name}"]').bounding_box()
        assert box and box['width']>0 and box['height']>0, (name,box)
        return box
    editor=region('editor')
    root=page.locator('#mcel-onlyoffice-root').bounding_box()
    host=page.locator('#onlyoffice-editor-host').bounding_box()
    assert abs(root['y']) <= 1 and abs(root['height']-page.viewport_size['height']) <= 1, root
    assert page.locator('[data-mcel-viewport-height]').count()==1
    assert abs(editor['y']-root['y']) <= 1, (editor,root)
    assert abs(editor['height']-root['height']) <= 1
    assert abs(host['y']-root['y']) <= 1, (host,root)
    assert abs(host['height']-root['height']) <= 1, (host,root)
    iframe=page.locator('#onlyoffice-editor-host > iframe')
    if iframe.count():
        box=iframe.bounding_box()
        assert abs(box['y']-root['y']) <= 1 and abs(box['height']-root['height']) <= 1, (box,root)
    for name in ('header','library','activity'):
        panel=region(name)
        assert panel['x']+panel['width'] <= editor['x']+1, (name,panel,editor)
    if advanced:
        panel=region('advanced')
        assert editor['x']+editor['width'] <= panel['x']+1
    else:
        assert not page.locator('[data-mcel-region="interface-region:onlyoffice.advanced"]').is_visible()
        assert abs(editor['x']+editor['width']-root['x']-root['width']) <= 1


def test_native_browser_controls_and_port(browser):
    page=browser.new_page(viewport={'width':1400,'height':900})
    try:
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        html=render_onlyoffice_page(ROOT).replace('</head>',STUB+'</head>')
        # Mock only the external spreadsheet editor transport; real MCEL runtime,
        # real capability port, real UI controls and API dispatch remain active.
        html=html.replace('McelOnlyOfficePort.create(()=>workspace?.host,()=>workspace?.picker.files[0],{onStatus:', '''McelOnlyOfficePort.create(()=>workspace?.host,()=>workspace?.picker.files[0],{mount:(host,data)=>{const f=document.createElement('iframe');f.title='Mock isolated editor';f.srcdoc='<p>Mock isolated editor</p>';host.append(f);return {ready:Promise.resolve(),destroy:()=>f.remove(),attachComment:async text=>{window.comment=text;return 'Attached';}};},onStatus:''')
        page.set_content(html,wait_until='domcontentloaded')
        page.wait_for_function("window.__mcelWorkbench?.app.readState('state:server-status')?.startsWith('Document Server reachable')")
        assert_workspace_geometry(page)
        page.set_viewport_size({'width':980,'height':560})
        assert_workspace_geometry(page)
        page.set_viewport_size({'width':1400,'height':900})
        assert page.locator('#onlyoffice-editor-host').bounding_box()['height']>300
        page.get_by_role('button',name='Probe.xlsx',exact=True).click()
        page.wait_for_function("window.__mcelWorkbench.app.readState('state:active-workbook')==='Probe.xlsx'")
        assert page.locator('#onlyoffice-editor-host > iframe').is_visible()
        assert_workspace_geometry(page)
        for node in ('server-status','refresh-server-status','save-now','reload-editor','open-workbook','upload-workbook'):
            assert page.locator(f'[data-mcel-node="surface-node:onlyoffice.{node}"]').count()==0
        for label in ('Refresh Server Status','Save Now','Reload Editor','Open Workbook','Upload Workbook'):
            assert page.get_by_role('button',name=label,exact=True).count()==0
        result=page.evaluate("async()=>await __mcelWorkbench.app.execute('intent:save-now')")
        assert result['disposition']=='completed'
        page.wait_for_function("window.__mcelWorkbench.app.readState('state:operation-result').startsWith('Force-save requested')")
        result=page.evaluate("async()=>{window.saveError=3;return await __mcelWorkbench.app.execute('intent:save-now')}")
        assert result['disposition']!='completed'
        result=page.evaluate("async()=>{window.badConfig=true;return await __mcelWorkbench.app.execute('intent:reload-editor')}")
        assert result['disposition']!='completed'
        assert page.locator('#onlyoffice-editor-host > iframe').is_visible()
        page.evaluate('window.badConfig=false')
        page.get_by_role('button',name='Advanced',exact=True).click()
        page.wait_for_function("document.querySelector('#onlyoffice-advanced-chat-panel')?.textContent?.length>0")
        assert page.locator('#onlyoffice-advanced-chat-panel').is_visible()
        assert_workspace_geometry(page, advanced=True)
        page.evaluate("window.originalEditor=document.querySelector('#onlyoffice-editor-host').firstElementChild")
        page.set_viewport_size({'width':980,'height':560})
        assert_workspace_geometry(page, advanced=True)
        page.set_viewport_size({'width':640,'height':480})
        assert_workspace_geometry(page, advanced=True)
        page.set_viewport_size({'width':980,'height':560})
        page.get_by_role('button',name='Advanced',exact=True).click()
        assert_workspace_geometry(page)
        page.get_by_role('button',name='Advanced',exact=True).click()
        assert_workspace_geometry(page, advanced=True)
        assert page.evaluate("originalEditor===document.querySelector('#onlyoffice-editor-host').firstElementChild")
        page.set_viewport_size({'width':1400,'height':900})
        assert page.locator('#onlyoffice-advanced-chat-notebook').count()==1, page.locator('#onlyoffice-advanced-chat-panel').inner_text()
        result=page.evaluate("async()=>await __mcelWorkbench.app.execute('intent:attach-code',{language:'python',code:'x=1'})")
        assert result['disposition']=='completed'
        assert 'Workbook: Probe.xlsx' in page.evaluate('window.comment')
        assert 'x=1' in page.evaluate('window.comment')
        name=page.locator('[data-mcel-node="surface-node:onlyoffice.new-workbook"] input')
        assert name.input_value()==''
        name.fill('Book.xlsx')
        page.get_by_role('button',name='New XLSX',exact=True).click()
        page.wait_for_function("document.querySelector('#onlyoffice-status').textContent.includes('Workbook already exists')")
        assert not any(url.endswith('/create') for url,a in page.evaluate('window.calls'))
        name.fill('')
        page.get_by_role('button',name='New XLSX',exact=True).click()
        page.wait_for_function("__mcelWorkbench.app.readState('state:active-workbook')==='Book3.xlsx'")
        assert page.evaluate("calls.filter(x=>x[0].endsWith('/create')).map(x=>x[1])")==[{'path':'Book3.xlsx'}]
        assert name.input_value()==''
        page.get_by_role('button',name='Book2.xlsx',exact=True).click()
        page.wait_for_function("__mcelWorkbench.app.readState('state:active-workbook')==='Book2.xlsx'")
        assert page.evaluate("calls.filter(x=>x[0].endsWith('/config')).at(-1)[1].path")=='Book2.xlsx'
        picker=page.locator('#onlyoffice-upload-file')
        assert picker.count()==1 and not picker.is_visible()
        import_button=page.get_by_role('button',name='Import',exact=True)
        assert import_button.bounding_box()['y'] < page.get_by_role('button',name='Advanced',exact=True).bounding_box()['y']
        with page.expect_file_chooser() as cancelled:
            import_button.click()
        cancelled.value.set_files([])
        assert not any(url.endswith('/upload') for url,a in page.evaluate('window.calls'))
        with page.expect_file_chooser() as chooser:
            import_button.click()
        chooser.value.set_files({'name':'Uploaded.xlsx','mimeType':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','buffer':b'example'})
        page.wait_for_function("__mcelWorkbench.app.readState('state:active-workbook')==='Uploaded.xlsx'")
        assert page.evaluate("calls.filter(x=>x[0].endsWith('/upload')).map(x=>x[1])")==[{'path':'Uploaded.xlsx','content_base64':'ZXhhbXBsZQ=='}]
        assert picker.input_value()==''
        assert import_button.is_enabled()
        assert any(x['path']=='Uploaded.xlsx' for x in page.evaluate("__mcelWorkbench.app.readState('state:workbooks')"))
        assert page.evaluate("typeof window.DocsAPI")=='undefined'
        assert not errors,errors
    finally:page.close()


def test_bridge_rejects_nonisolated_origins(browser):
    page=browser.new_page()
    try:
        page.set_content('<div id="host"></div>')
        page.add_script_tag(content=(ROOT/'main_computer/web/applications/scripts/mcel-onlyoffice-port.js').read_text(encoding='utf-8'))
        assert page.evaluate("""()=>{const host=document.querySelector('#host');return [
          {hardened_editor_origin:false,public_url:'https://onlyoffice.main-computer.localhost:18086'},
          {hardened_editor_origin:true,public_url:'http://onlyoffice.main-computer.localhost:18086'},
          {hardened_editor_origin:true,public_url:'https://onlyoffice.main-computer.localhost.evil:18086'},
          {hardened_editor_origin:true,public_url:'https://onlyoffice.main-computer.localhost:18086/path'},
          {hardened_editor_origin:true,public_url:'https://user@onlyoffice.main-computer.localhost:18086'}
        ].every(data=>{try{McelOnlyOfficePort.mountEditor(host,data);return false;}catch(e){return host.children.length===0;}})}""")
    finally:page.close()
