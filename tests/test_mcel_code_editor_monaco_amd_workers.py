"""AMD-specific Monaco worker contract (not ESM bundle filenames)."""
from pathlib import Path

from playwright.sync_api import sync_playwright
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT=Path(__file__).resolve().parents[1]
ADAPTER=ROOT/'main_computer/web/applications/scripts/code-editor-monaco-adapter.js'


def _load_fake_amd(page, *, fail_local=False):
    page.set_content('<!doctype html><html><head></head><body></body></html>')
    page.evaluate('''(failLocal)=>{
      window.__amdBase='';window.__amdScripts=[];
      window.require=(ids,onOk,onErr)=>{
        if(failLocal && window.__amdBase.startsWith('/applications/'))
          return onErr(Error('synthetic-local-unavailable'));
        window.monaco={editor:{}};
        onOk();
      };
      window.require.config=({paths})=>window.__amdBase=paths.vs;
      const append=document.head.append.bind(document.head);
      document.head.append=(script)=>{
        if(script.tagName!=='SCRIPT')return append(script);
        window.__amdScripts.push(script.src);
        queueMicrotask(()=>script.onload());
        return script;
      };
    }''', fail_local)
    page.add_script_tag(content=ADAPTER.read_text(encoding='utf8'))


def test_amd_local_editor_and_language_workers_share_worker_main():
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            _load_fake_amd(page)
            result=page.evaluate('''async()=>{
                const loaded=await window.MainComputerMonacoAdapter.load({allowCdn:false});
                return {loaded,
                 base:window.__amdBase,
                 urls:['editorWorkerService','json','css','typescript','javascript'].map(label=>
                     window.MonacoEnvironment.getWorkerUrl('any/module',label))};
            }''')
            assert result['loaded']['ok'] is True
            assert result['loaded']['source']=='local-vendor'
            assert result['base']=='/applications/vendor/monaco-editor/min/vs'
            assert result['urls']==[result['base']+'/base/worker/workerMain.js']*5
            assert all('ts.worker' not in url and 'editor.worker' not in url for url in result['urls'])
        finally:browser.close()


def test_amd_cdn_fallback_uses_cross_origin_safe_bootstrap():
    with sync_playwright() as pw:
        browser,_=launch_semantic_browser(pw)
        try:
            page=browser.new_page()
            _load_fake_amd(page,fail_local=True)
            result=page.evaluate('''async()=>{
                const loaded=await window.MainComputerMonacoAdapter.load({allowCdn:true});
                const url=window.MonacoEnvironment.getWorkerUrl('any/module','typescript');
                return {loaded,url,decoded:decodeURIComponent(url.split(',')[1])};
            }''')
            assert result['loaded']['ok'] is True
            assert result['loaded']['source']=='cdn'
            assert result['url'].startswith('data:text/javascript;charset=utf-8,')
            assert '/base/worker/workerMain.js' in result['decoded']
            assert 'self.MonacoEnvironment={baseUrl:' in result['decoded']
            assert 'importScripts(' in result['decoded']
            assert 'ts.worker.js' not in result['decoded']
        finally:browser.close()


def test_monaco_asset_witness_only_reports_paths_and_response_status():
    from tools.mcel_hosted_signal_cutover_acceptance import _monaco_resource_evidence
    assert _monaco_resource_evidence('http://127.0.0.1:8765/api/editor/read?token=secret',result='response',status=200) is None
    result=_monaco_resource_evidence('http://127.0.0.1:8765/applications/vendor/monaco-editor/min/vs/base/worker/workerMain.js?token=SECRET',result='response',status=404)
    assert result=={'path':'/applications/vendor/monaco-editor/min/vs/base/worker/workerMain.js',
                    'result':'response','httpStatus':404}
    assert 'SECRET' not in str(result)
    result=_monaco_resource_evidence('https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs/editor/editor.main.js',result='failed')
    assert result['result']=='failed'
    assert 'editor.main.js' in result['path']
