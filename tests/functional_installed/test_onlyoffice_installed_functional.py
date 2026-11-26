"""Installed MCEL UI + real isolated backend + deterministic AI subprocess.

The isolated viewport fixture is deliberately HTTP; the external editor bridge
is emulated on its own HTTPS origin. This is not hardened live certification.
Run the separate live acceptance command for real Docker and real model output.
"""
from __future__ import annotations

from urllib.parse import quote

import pytest

from tools.mcel_onlyoffice_full_functional_acceptance import (
    API, CODE, assert_geometry, assert_surface, ask_ai, create, extract_formula,
    import_book, open_advanced, open_app, select_book, xlsx_values,
)

pytestmark = [pytest.mark.installed_functional, pytest.mark.onlyoffice_functional]
DOCS = 'https://onlyoffice.main-computer.localhost:18086'

# A real cross-origin iframe, using the same origin/source/channel protocol as
# the production host. No DocsAPI shim in the MCEL page and no synthetic
# callback that can write a different workbook. The only fake is DocumentServer.
BRIDGE = '''<!doctype html><meta charset="utf-8"><body>Fixture spreadsheet editor
<script>
const q=new URLSearchParams(location.hash.slice(1));
const channel=q.get('channel'), origin=q.get('parent');
window.comments=[];window.config=null;
const send=(type,extra={})=>parent.postMessage({type,channel,...extra},origin);
window.addEventListener('message',e=>{
  if(e.source!==parent||e.origin!==origin||e.data?.channel!==channel)return;
  if(e.data.type==='mc-oo-config'){
    window.config=e.data.config;send('mc-oo-document-ready');
  } else if(e.data.type==='mc-oo-comment'){
    window.comments.push(e.data.text);
    send('mc-oo-comment-result',{requestId:e.data.requestId,message:'Fixture comment attached'});
  }
});
send('mc-oo-host-ready');
</script>'''


@pytest.fixture
def native_app(playwright_page, viewport_app):
    page = playwright_page
    # Keep the real installed storage/config handler in the path. Adapt only
    # the unpromoted fixture's editor-origin envelope, explicitly test-only.
    def config(route):
        response = route.fetch()
        data = response.json()
        if response.ok and data.get('ok'):
            data.update(hardened_editor_origin=True, public_url=DOCS)
        route.fulfill(response=response, json=data)
    page.route(viewport_app.base_url + API + 'config', config)
    page.route(DOCS + '/mc-editor-host.html*', lambda route: route.fulfill(status=200, content_type='text/html',body=BRIDGE))
    app = open_app(page,viewport_app.base_url,shell=False)
    assert_surface(app)
    assert_geometry(app)
    return page,app


def bytes_for(page, base, name):
    response = page.request.get(base + API + 'file?path=' + quote(name))
    assert response.ok
    return response.body()


def test_installed_onlyoffice_workbook_lifecycle(native_app, viewport_app, uploaded_xlsx):
    page,app=native_app
    name='FunctionalGoldenPath.xlsx'
    a=create(page,app,name,hardened=False)
    raw=bytes_for(page,viewport_app.base_url,name)
    assert xlsx_values(raw)=={}
    b=import_book(page,app,uploaded_xlsx,hardened=False)
    assert a['config']['document']['key']!=b['config']['document']['key']
    assert bytes_for(page,viewport_app.base_url,uploaded_xlsx.name)==uploaded_xlsx.read_bytes()
    select_book(page,app,name,hardened=False)
    assert bytes_for(page,viewport_app.base_url,name)==raw
    select_book(page,app,uploaded_xlsx.name,hardened=False)
    assert bytes_for(page,viewport_app.base_url,uploaded_xlsx.name)==uploaded_xlsx.read_bytes()
    assert_geometry(app)


def test_installed_onlyoffice_advanced_code_addons_and_embedded_ai_chat(native_app, fake_ollama_server):
    page,app=native_app
    name='FunctionalAdvancedAndAi.xlsx'
    create(page,app,name,hardened=False)
    editor=app.locator('#onlyoffice-editor-host > iframe').element_handle().content_frame()
    open_advanced(app)
    assert editor==app.locator('#onlyoffice-editor-host > iframe').element_handle().content_frame()
    for language,source in [('javascript','const total = 42;'),('python','total = 42'),('basic','PRINT 42')]:
        app.locator(CODE+' select').select_option(language)
        app.locator(CODE+' textarea').fill(source)
        app.get_by_role('button',name='Attach code to selected cells',exact=True).click()
        editor.wait_for_function("n => window.comments.length === n", arg=['javascript','python','basic'].index(language)+1)
        app.wait_for_function("__mcelWorkbench.app.readState('state:operation-result')==='Fixture comment attached'")
        comments=editor.evaluate('window.comments')
        assert 'Language: '+language in comments[-1]
        assert 'Workbook: '+name in comments[-1]
        assert source in comments[-1]
    assert len(editor.evaluate('window.comments'))==3
    count=len(fake_ollama_server.requests)
    prompt='Reply with ONLYOFFICE_AI_OK and nothing else.'
    text=ask_ai(app,prompt,output_pattern='ONLYOFFICE_AI_OK',timeout=90000)
    assert 'ONLYOFFICE_AI_OK' in text
    requests=fake_ollama_server.requests[count:]
    assert any(r.get('model')=='functional-fast' and r.get('stream') is True and
               prompt in '\n'.join(str(m.get('content','')) for m in r.get('messages',[])) for r in requests), 'Actual AI subprocess did not reach fixture provider'
    # A spreadsheet-specific second request, not just a chat readiness check.
    count=len(fake_ollama_server.requests)
    prompt='Return the formula for A11 that sums A1:A10. ONLYOFFICE_FORMULA_SUM_A1_A10'
    text=ask_ai(app,prompt,output_pattern=r'=SUM\(A1:A10\)',timeout=90000)
    assert extract_formula(text)=='=SUM(A1:A10)'
    assert any(prompt in '\n'.join(str(m.get('content','')) for m in r.get('messages',[])) for r in fake_ollama_server.requests[count:])
    # Code attachment is not execution. Real formula entry and durable save are
    # certified only by the separate actual-editor acceptance lane.
