"""SCM contract inspection is a source-grounded read, not a Studio runtime certificate.

The paired application reads the incumbent's SCM definitions without copying
its markup, grants, UI handler or synthetic runtime evidence.
"""
from __future__ import annotations

import json
from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser


def _page(browser, name='code-editor'):
    page = browser.new_page(viewport={'width': 1100, 'height': 850})
    page.evaluate('''() => {
      window.__requests=[];
      window.fetch=async (uri,opts={})=>{
        window.__requests.push(String(uri));
        if(String(uri).endsWith('/component-docs/viewport-config'))
          return {ok:true,status:200,json:async()=>({ok:true,config:window.__badConfig
            ? {default_snap_mode:'bad',presets:{x:{max_width:5000,max_height:100}}}
            : {default_snap_mode:'docs-wide',presets:{'docs-wide':{max_width:1024,max_height:640}}}})};
        return {ok:false,status:404,json:async()=>({error:'not-mocked'})};
      };
    }''')
    page.set_content(render_semantic_page(name))
    assert page.locator('#mcel-workbench-status').inner_text() == 'Semantic candidate ready'
    return page


def test_studio_contract_is_paired_readonly_and_layout_free():
    ir = compile_semantic_app('code-editor')
    assert len(ir['provenance']['frontend']['sourceFiles']) == 2
    assert not ir['layouts']
    intents = {i['id']: i for i in ir['intents']}
    caps = {c['id']: c for c in ir['capabilities']}
    assert caps['capability:scm-definition']['risk'] == 'external-read'
    for intent in ('inspect-scm-component-contract', 'inspect-scm-route-contract',
                   'read-component-doc-viewport-config'):
        assert intents['intent:'+intent]['risk'] == 'external-read'
        assert 'effect:'+intent+'.request' in {e['id'] for e in ir['effects']}


def test_reads_real_incumbent_scm_definitions_without_network_or_fake_receipts():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = _page(browser)
            result = page.evaluate('''async()=>{
              const app=window.__mcelWorkbench.app;
              const c=await app.execute('intent:inspect-scm-component-contract',{});
              const r=await app.execute('intent:inspect-scm-route-contract',{});
              return {c:c.disposition,r:r.disposition,
                component:JSON.parse(app.readState('state:scm-component-contract')),
                route:JSON.parse(app.readState('state:scm-route-contract'))};
            }''')
            assert result['c'] == result['r'] == 'completed'
            assert result['component']['component'] == 'CodeStudio'
            assert result['component']['contract'] == 'mcel.scm.code-studio.v1'
            assert 'editor.monaco.mount' in result['component']['ownership']['effects']
            assert result['route']['route'] == 'workspace.file'
            assert result['route']['contract'] == 'mcel.scm.route.workspace-file.v1'
            assert result['route']['segments']
            assert result['component']['executionEvidence'] == result['route']['executionEvidence'] == 'none'
            assert page.evaluate('window.__requests.length') == 0
            page.close()
        finally:
            browser.close()


def test_doc_viewport_config_uses_existing_read_backend_and_checks_dimensions():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = _page(browser)
            result = page.evaluate('''async()=>{
              const app=window.__mcelWorkbench.app;
              const r=await app.execute('intent:read-component-doc-viewport-config',{});
              return {outcome:r.disposition,config:JSON.parse(app.readState('state:component-doc-viewport-config'))};
            }''')
            assert result['outcome'] == 'completed'
            assert result['config']['defaultSnapMode'] == 'docs-wide'
            assert result['config']['presets']['docs-wide']['max_width'] == 1024
            assert page.evaluate('window.__requests') == ['/api/applications/component-docs/viewport-config']
            page.evaluate('window.__badConfig=true')
            bad=page.evaluate('''async()=>{
              try{return (await window.__mcelWorkbench.app.execute('intent:read-component-doc-viewport-config',{})).disposition;}
              catch(e){return String(e);}
            }''')
            assert bad != 'completed'
            page.close()
        finally:
            browser.close()


def test_other_apps_do_not_import_studio_module_or_gain_studio_intents():
    for name in ('file-manager','task-manager'):
        ir = compile_semantic_app(name)
        assert 'capability:scm-definition' not in {c['id'] for c in ir['capabilities']}
        page = render_semantic_page(name)
        assert 'mcel.scm.code-studio.v1' not in page
        assert 'MainComputerCodeStudioScm' not in page


def test_studio_source_checks_and_clean_serialization_are_readonly_and_authorized():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = browser.new_page()
            page.add_script_tag(content=(root/'main_computer/web/applications/scripts/mcel-semantic-read-ports.js').read_text())
            data = page.evaluate('''async()=>{
              const sum=s=>String(Array.from(s).reduce((n,c)=>n+c.charCodeAt(0),0)%256).padStart(2,'0').repeat(32);
              Object.defineProperty(window.crypto,'subtle',{configurable:true,value:{digest:async(_,buf)=>
                Uint8Array.from(sum(new TextDecoder().decode(buf)).match(/.{2}/g),v=>parseInt(v,16)).buffer}});
              const source='<main data-mc-component="code-workspace" data-mc-use-case="source-safe-code-editor">'+
                '<h1 data-mc-field="workspace-title">Demo</h1><pre data-mc-component="code-file" data-mc-file-path="src/index.js">hello</pre>'+
                '<aside data-mc-serialize="omit">runtime-only</aside></main>';
              const calls=[];
              const fetcher=async(url,opts)=>{
                calls.push(url);
                const result=url.endsWith('/editor/files')
                  ? {ok:true,path:'',entries:[{path:'README.html',name:'README.html',kind:'file'}]}
                  : {ok:true,files:[{path:'README.html',content:source}]};
                return {ok:true,status:200,json:async()=>result};
              };
              const port=McelSemanticReadPorts.create({appId:'semantic-code-editor',fetcher});
              const spec={
                'list-files':['source-reader','list','listSourceFiles'],
                'open-source':['source-reader','read','readSource'],
                'check-studio-draft':['studio-source-tools','check','checkStudioDraftSource'],
                'preview-studio-serialization':['studio-source-tools','serialize-preview','previewStudioDraftSerialization']
              };
              const req=(intent,args)=>{
                const [cap,op,method]=spec[intent];
                return {appId:'semantic-code-editor',intentId:'intent:'+intent,
                  effectId:'effect:'+intent+'.request',capabilityId:'capability:'+cap,
                  operation:op,runtimeMethod:method,risk:'external-read',arguments:args};
              };
              const before=await port.authorize(req('check-studio-draft',{path:'README.html',text:source}));
              await port.invoke(req('list-files',{}));
              await port.invoke(req('open-source',{path:'README.html'}));
              const check=JSON.parse(await port.invoke(req('check-studio-draft',{path:'README.html',text:source})));
              const serial=JSON.parse(await port.invoke(req('preview-studio-serialization',{path:'README.html',text:source})));
              const invalid=JSON.parse(await port.invoke(req('check-studio-draft',{path:'README.html',text:'<p>bad</p>'})));
              port.closeDocument('README.html');
              const after=await port.authorize(req('preview-studio-serialization',{path:'README.html',text:source}));
              return {before,after,check,serial,invalid,calls};
            }''')
            assert data['before'] is False and data['after'] is False
            assert data['check']['sourceCheckPassed'] is True
            assert data['check']['scmRuntimeGates'] == 'unverified'
            assert data['serial']['scmSerializationGate'] == 'unverified'
            assert data['serial']['sourceMutation'] == 'none'
            assert 'src/index.js' in data['serial']['serialized']
            assert 'runtime-only' not in data['serial']['serialized']
            assert data['invalid']['sourceCheckPassed'] is False
            assert len(data['calls']) == 2
            page.close()
        finally:
            browser.close()
