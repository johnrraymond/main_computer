"""Local-only parity plugin instrumentation; no live Windows authority exercised."""
import json
from pathlib import Path

import pytest

from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_app_trace_compare import compare, load

ROOT=Path(__file__).resolve().parents[1]


def test_all_application_pages_load_same_local_recorder():
    production=(ROOT/'main_computer/web/applications.html').read_text()
    assert 'mcel-app-parity-trace-plugin.js' in production
    for kind in ('code-editor','file-manager','task-manager'):
        candidate=render_semantic_page(kind)
        assert 'mcel.app-parity-trace.v1' in candidate
        assert 'mcel-diagnostic-plugins.js' not in candidate  # bundled inline
        assert 'mcel-app-parity' in candidate


def test_semantic_runtime_emits_content_free_lifecycle():
    source=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text()
    assert 'traceOperation(id,"dispatch","")' in source
    assert 'traceOperation(intent.id,"settled",disposition)' in source
    assert 'request.arguments' not in source[source.index('function traceOperation'):source.index('function receipt')]


def test_production_code_editor_emits_content_free_lifecycle():
    source=(ROOT/'main_computer/web/applications/scripts/code-editor.js').read_text()
    assert 'emitParity("dispatch", intentName)' in source
    assert 'emitParity("state", "editor-state"' in source


def make_trace(variant,events,app='code-editor'):
    return {'schema':'mcel.app-parity-trace.v1','identity':{'app':app,'variant':variant},'events':events}


def test_comparison_shows_unverified_workflows_and_no_payload():
    p=make_trace('production',[
        {'kind':'backend-request','requestId':1,'endpoint':'/api/applications/editor/read'},
        {'kind':'backend-response','requestId':1,'outcome':'http-ok'},
        {'kind':'backend-request','requestId':2,'endpoint':'/api/applications/aider/run'},
        {'kind':'backend-response','requestId':2,'outcome':'http-failed'},
        {'kind':'ui-state','state':{'documentOpen':True,'monacoPresent':True}},
    ])
    s=make_trace('semantic',[
        {'kind':'backend-request','requestId':1,'endpoint':'/api/applications/editor/read'},
        {'kind':'backend-response','requestId':1,'outcome':'http-ok'},
        {'kind':'ui-state','state':{'documentOpen':False,'monacoPresent':False}},
    ])
    result=compare(p,s)
    assert result['status']=='OBSERVATIONS_ONLY_NOT_PARITY_CERTIFICATION'
    assert [x['coverage'] for x in result['workflows']]==['production-only','both-observed']
    assert result['production']['finalState']['monacoPresent'] is True
    assert result['semantic']['finalState']['documentOpen'] is False
    assert 'payload' not in json.dumps(result)


def test_load_rejects_non_trace(tmp_path):
    file=tmp_path/'invalid.json'
    file.write_text(json.dumps({'schema':'unknown','events':[]}))
    with pytest.raises(ValueError,match='Invalid MCEL'):
        load(file)


def test_comparison_rejects_mismatched_apps():
    with pytest.raises(ValueError,match='different application'):
        compare(make_trace('production',[]),make_trace('semantic',[],app='task-manager'))


def test_trace_is_opt_in_and_redacts_content_and_query():
    src=(ROOT/'main_computer/web/applications/scripts/mcel-app-parity-trace-plugin.js').read_text()
    assert 'get("mcel_trace")==="1"' in src
    assert 'response.json()' not in src
    assert 'body:JSON.stringify' not in src
    assert 'url.pathname' in src
    assert 'removeEventListener' in src


def test_real_browser_parity_plugin_captures_semantic_intents_without_source_text():
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page=browser.new_page(viewport={'width':1100,'height':750})
            page.evaluate('''() => { window.fetch=async () => ({ok:true,status:200,
              json:async()=>({ok:true,entries:[{path:'private-file.txt',name:'private-file.txt',kind:'file'}]})}); }''')
            page.set_content(render_semantic_page('code-editor'))
            assert page.locator('#mcel-workbench-status').inner_text() == 'Semantic candidate ready'
            result=page.evaluate('''async () => window.MCELDiagnosticPlugins.enable('mcel-app-parity')''')
            assert result['status']=='enabled'
            page.get_by_role('button',name='List Files').click()
            page.locator('[data-record-key="private-file.txt"]').wait_for()
            trace=page.evaluate('window.MCELAppParityTrace.exportTrace()')
            kinds={e['kind'] for e in trace['events']}
            assert {'ui-action','backend-request','backend-response','intent','ui-state'} <= kinds
            assert trace['identity']=={'app':'code-editor','variant':'semantic'}
            assert any(e.get('intent')=='intent:list-files' and e.get('phase')=='settled' and e.get('disposition')=='completed'
                       for e in trace['events'])
            assert any(e.get('endpoint')=='/api/applications/editor/files' for e in trace['events'])
            assert 'private-file.txt' not in json.dumps(trace)
            assert 'source-content' not in json.dumps(trace)
            page.evaluate("window.MCELDiagnosticPlugins.disable('mcel-app-parity')")
            page.close()
        finally:
            browser.close()


def test_real_browser_parity_plugin_records_production_api_and_restores_fetch():
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    sources=[(ROOT/'main_computer/web/applications/scripts'/name).read_text()
             for name in ('mcel-diagnostic-early-bootstrap.js','mcel-diagnostic-plugins.js',
                          'mcel-app-parity-trace-plugin.js')]
    html='<html><head>'+''.join('<script>'+src+'</script>' for src in sources)+'''</head><body>
      <div id="code-editor-app"><button id="file-map-refresh">Refresh</button></div></body></html>'''
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=browser.new_page()
            page.evaluate('''() => {window.fetch=async ()=>({ok:true,status:200,
              json:async()=>({ok:true,files:[]})});}''')
            page.set_content(html)
            assert page.evaluate('''async()=> (await MCELDiagnosticPlugins.enable('mcel-app-parity')).status''')=='enabled'
            page.locator('#file-map-refresh').click()
            page.evaluate('''async () => {
              await fetch('/api/applications/editor/files?token=private-secret',
                {method:'POST',body:JSON.stringify({instruction:'confidential prompt'})});
            }''')
            trace=page.evaluate('MCELAppParityTrace.exportTrace()')
            assert trace['identity']['variant']=='production'
            assert any(e.get('kind')=='ui-action' and e.get('target')=='file-map-refresh'
                       for e in trace['events'])
            assert any(e.get('endpoint')=='/api/applications/editor/files' and e['kind']=='backend-response'
                       and e['outcome']=='http-ok' for e in trace['events'])
            assert 'private-secret' not in json.dumps(trace)
            assert 'confidential prompt' not in json.dumps(trace)
            result=page.evaluate('''async()=>{
              const before=window.fetch;
              await MCELDiagnosticPlugins.disable('mcel-app-parity');
              return {recording:MCELAppParityTrace.status().recording,restored:window.fetch!==before};
            }''')
            assert result=={'recording':False,'restored':True}
        finally:browser.close()
