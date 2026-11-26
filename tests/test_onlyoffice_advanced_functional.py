"""Native MCEL Advanced tools and AI request regression (mocked services).

Exercises the shipped UI and shared chat controller, not provider inference or
real spreadsheet editing. Those are covered by the separate live gate.
"""
from pathlib import Path

import pytest
from main_computer.mcel_onlyoffice_application import render_onlyoffice_page
from tools.mcel_onlyoffice_full_functional_acceptance import (
    CODE, ask_ai, assert_geometry, assert_surface, extract_formula, open_advanced, wait_active,
)
from tools.mcel_phase2b_browser import launch_semantic_browser, BrowserUnavailable

ROOT = Path(__file__).resolve().parents[1]

STUB = '''<script>
for(const name of ['localStorage','sessionStorage']){const values=new Map();Object.defineProperty(window,name,{value:{getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,String(v)),removeItem:k=>values.delete(k),clear:()=>values.clear(),key:i=>[...values.keys()][i]??null,get length(){return values.size;}}});}
window.aiRequests=[];window.comments=[];
window.fetch=async(url,opts={})=>{
  const p=String(url).split('?')[0],a=JSON.parse(opts.body||'{}');let d={ok:true};
  if(p.endsWith('/onlyoffice/files'))d.files=[{path:'Probe.xlsx',bytes:128}];
  else if(p.endsWith('/onlyoffice/status'))Object.assign(d,{server_probe:{ok:true},public_url:'https://onlyoffice.main-computer.localhost:18086'});
  else if(p.endsWith('/onlyoffice/config'))Object.assign(d,{hardened_editor_origin:true,public_url:'https://onlyoffice.main-computer.localhost:18086',config:{document:{key:'probe',title:a.path}}});
  else if(p.endsWith('/ai/capacity'))Object.assign(d,{available_now:true,busy:false,active_run_count:0,max_local_concurrency:1});
  else if(p.endsWith('/cell/evaluate')){aiRequests.push(a);d.output_cell={id:'mock-model-output-'+aiRequests.length,type:'output',source_cell_id:a.cell.id,status:'ok',parts:[{kind:'markdown',content:'`=SUM(A1:A10)`'}]};}
  else if(p.endsWith('/activity/snapshot'))d.events=[];
  else if(p.endsWith('/activity/ollama-ps'))d.models=[];
  else return {ok:false,status:404,json:async()=>({ok:false,error:'Unexpected fixture route'})};
  return {ok:true,status:200,json:async()=>d};
};
</script>'''


def test_advanced_notebook_ai_and_code_attachment():
    from playwright.sync_api import sync_playwright
    html = render_onlyoffice_page(ROOT).replace('</head>',STUB+'</head>')
    original='McelOnlyOfficePort.create(()=>workspace?.host,()=>workspace?.picker.files[0],{onStatus:'
    assert html.count(original)==1
    html=html.replace(original,'''McelOnlyOfficePort.create(()=>workspace?.host,()=>workspace?.picker.files[0],{mount:(host,data)=>{const f=document.createElement('iframe');f.srcdoc='<p>Mock editor</p>';host.append(f);return {ready:Promise.resolve(),destroy:()=>f.remove(),attachComment:async text=>{window.comments.push(text);return 'Attached';}};},onStatus:''')
    with sync_playwright() as pw:
        try:browser,_=launch_semantic_browser(pw)
        except BrowserUnavailable as exc:pytest.skip(str(exc))
        try:
            page=browser.new_page(viewport={'width':1440,'height':900})
            page.set_content(html,wait_until='domcontentloaded')
            page.wait_for_function("window.__mcelWorkbench?.app.readState('state:server-status')?.startsWith('Document Server reachable')")
            assert_surface(page)
            page.get_by_role('button',name='Probe.xlsx',exact=True).click()
            wait_active(page,'Probe.xlsx')
            page.evaluate("window.editorBefore=document.querySelector('#onlyoffice-editor-host > iframe')")
            open_advanced(page)
            assert_geometry(page)
            page.locator(CODE+' select').select_option('python')
            page.locator(CODE+' textarea').fill('total = 42')
            page.get_by_role('button',name='Attach code to selected cells',exact=True).click()
            page.wait_for_function('window.comments.length === 1')
            assert 'Workbook: Probe.xlsx' in page.evaluate('window.comments[0]')
            assert 'Language: python' in page.evaluate('window.comments[0]')
            prompt='Give the formula that sums the ten numbers in column A.'
            text=ask_ai(page,prompt,output_pattern=r'=SUM\(A1:A10\)',timeout=15000)
            assert extract_formula(text)=='=SUM(A1:A10)'
            requests=page.evaluate('window.aiRequests')
            assert len(requests)==1 and requests[0]['cell']['source']==prompt
            assert requests[0]['cell']['type']=='ai'
            assert page.evaluate("editorBefore===document.querySelector('#onlyoffice-editor-host > iframe')")
            page.get_by_role('button',name='Advanced',exact=True).click()
            assert_geometry(page)
            assert page.evaluate("editorBefore===document.querySelector('#onlyoffice-editor-host > iframe')")
        finally:browser.close()


def test_live_gate_fixture_and_scope(tmp_path):
    from tools.mcel_onlyoffice_full_functional_acceptance import (
        ORIGIN, download_path, run, validate_host, write_fixture, xlsx_values,
    )
    p=tmp_path/'numbers.xlsx'
    write_fixture(p,list(range(1,11)))
    assert xlsx_values(p.read_bytes())=={f'A{i}':i for i in range(1,11)}
    assert validate_host(ORIGIN)==ORIGIN
    for bad in ('http://127.0.0.1:8765','https://safe.main-computer.localhost:8875',ORIGIN+'/other','https://example.com'):
        with pytest.raises(ValueError):validate_host(bad)
    with pytest.raises(ValueError):run(ORIGIN,tmp_path/'report.json')
    assert not (tmp_path/'report.json').exists()
    with pytest.raises(AssertionError):extract_formula('=42')
    config={'config':{'document':{'url':'http://host.docker.internal:8765/api/applications/onlyoffice/file?path=numbers.xlsx&cap=fixture'}}}
    assert download_path(config,'numbers.xlsx')=='/api/applications/onlyoffice/file?path=numbers.xlsx&cap=fixture'
    with pytest.raises(AssertionError):download_path(config,'other.xlsx')
