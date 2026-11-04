"""The paired semantic editor executes the real SCM kernel on an open document.

This is a bounded document-scoped lifecycle. It does not prove the original
workbench's layout gates or substitute for governed server-side source saves.
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]


def _port_page(browser):
    page=browser.new_page()
    for f in (
        'main_computer/web/applications/scripts/mcel-scm.js',
        'main_computer/web/applications/scripts/code-editor-scm-manifest.js',
        'main_computer/web/applications/scripts/mcel-semantic-read-ports.js',
    ):
        page.add_script_tag(content=(ROOT/f).read_text())
    page.evaluate('''() => {
      const sum=s=>String(Array.from(s).reduce((n,c)=>n+c.charCodeAt(0),0)%256).padStart(2,'0').repeat(32);
      Object.defineProperty(window.crypto,'subtle',{configurable:true,value:{digest:async(_,buf)=>
        Uint8Array.from(sum(new TextDecoder().decode(buf)).match(/.{2}/g),v=>parseInt(v,16)).buffer}});
      window.__requestLog=[];
      const fetcher=async(url,opts)=>{
        window.__requestLog.push(url);
        const result=url.endsWith('/editor/files')
          ? {ok:true,path:'',entries:[{path:'example.txt',name:'example.txt',kind:'file'}]}
          : {ok:true,files:[{path:'example.txt',content:'hello world'}]};
        return {ok:true,status:200,json:async()=>result};
      };
      window.__port=McelSemanticReadPorts.create({appId:'semantic-code-editor',fetcher});
      window.__req=(intent,args={})=>{
        const ops={
          'list-files':['source-reader','list','listSourceFiles'],
          'open-source':['source-reader','read','readSource'],
          ...Object.fromEntries([
            ['mount','mountStudioRuntime'],['validate','validateStudioRuntime'],
            ['repair','repairStudioRuntime'],['serialize','serializeStudioRuntime'],
            ['evidence','readStudioRuntimeEvidence'],['replay','replayStudioRuntime']
          ].map(([name,method])=>['studio-runtime-'+name,['scm-runtime',name,method]]))
        };
        const [cap,op,method]=ops[intent];
        return {appId:'semantic-code-editor',intentId:'intent:'+intent,
          effectId:'effect:'+intent+'.request',capabilityId:'capability:'+cap,
          operation:op,runtimeMethod:method,risk:'external-read',arguments:args};
      };
    }''')
    return page


def test_paired_application_exposes_scm_kernel_lifecycle_without_layout_authoring():
    ir=compile_semantic_app('code-editor')
    assert len(ir['provenance']['frontend']['sourceFiles'])==2
    assert ir['layouts']==[]
    intents={x['id']:x for x in ir['intents']}
    for op in ('mount','validate','serialize','repair','evidence','replay'):
        assert intents[f'intent:studio-runtime-{op}']['risk']=='external-read'
    html=render_semantic_page('code-editor')
    assert 'function createComponentInstance' in html
    assert 'function createDefaultInstance' in html
    assert 'McelSemanticOperationalRuntime.mount' in html


def test_real_scm_mount_validation_repair_serialization_evidence_and_replay():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=_port_page(browser)
            values=page.evaluate('''async()=>{
              const invoke=(name,args)=>window.__port.invoke(window.__req(name,args));
              const args={path:'example.txt',text:'hello world'};
              const unauth=await window.__port.authorize(window.__req('studio-runtime-mount',args));
              await invoke('list-files',{});
              await invoke('open-source',{path:args.path});
              const run=async name=>JSON.parse(await invoke('studio-runtime-'+name,args));
              const mount=await run('mount');
              const validate=await run('validate');
              const repair=await run('repair');
              const serialize=await run('serialize');
              const evidence=await run('evidence');
              const replay=await run('replay');
              return {unauth,mount,validate,repair,serialize,evidence,replay,
                calls:window.__requestLog};
            }''')
            assert values['unauth'] is False
            assert values['mount']['scmRuntimeKernel']=='executed'
            assert values['mount']['scope']=='opened-document-in-memory'
            assert values['validate']['evidence'][-1]['phase']=='effect-commit'
            assert values['repair']['evidence'][-1]['phase']=='repair-commit'
            assert values['serialize']['evidence'][-1]['phase']=='serialize-commit'
            assert values['serialize']['serializationSucceeded'] is True
            assert 'hello world' in values['serialize']['serialized']
            assert values['evidence']['operationCount']==4
            assert values['replay']['deterministicStateMatch'] is True
            assert values['replay']['replayedOperations']==3
            assert values['calls']==['/api/applications/editor/files','/api/applications/editor/read']
            assert values['replay']['liveProductionLayoutGates']=='unverified'
        finally:
            browser.close()


def test_draft_change_and_document_close_revoke_scm_session():
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=_port_page(browser)
            result=page.evaluate('''async()=>{
              const req=window.__req,port=window.__port;
              await port.invoke(req('list-files',{}));
              await port.invoke(req('open-source',{path:'example.txt'}));
              const current={path:'example.txt',text:'hello world'};
              await port.invoke(req('studio-runtime-mount',current));
              const staleDraft=await port.authorize(req('studio-runtime-validate',{
                path:'example.txt',text:'different'}));
              port.closeDocument('example.txt');
              const afterClose=await port.authorize(req('studio-runtime-replay',current));
              let denied=false;
              try{await port.invoke(req('studio-runtime-evidence',current));}
              catch(e){denied=String(e).includes('request-denied');}
              return {staleDraft,afterClose,denied,calls:window.__requestLog};
            }''')
            assert result['staleDraft'] is False
            assert result['afterClose'] is False
            assert result['denied'] is True
            assert result['calls']==['/api/applications/editor/files','/api/applications/editor/read']
        finally:
            browser.close()


def test_other_apps_cannot_use_kernel_intents():
    for name in ['file-manager','task-manager']:
        ir=compile_semantic_app(name)
        assert 'capability:scm-runtime' not in {x['id'] for x in ir['capabilities']}
        assert 'intent:studio-runtime-repair' not in {x['id'] for x in ir['intents']}
        assert 'function createComponentInstance' not in render_semantic_page(name)


def test_generated_editor_executes_document_scoped_kernel_actions():
    # Exercise generated action dispatch, shared state, capability port and SCM
    # kernel together, rather than certifying the port in isolation.
    from tests.test_mcel_semantic_editor_migration import _start_page
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            page=_start_page(browser)
            page.get_by_role('button',name='List Files').click()
            page.locator('[data-record-key="README.md"] [data-select-key]').click()
            page.wait_for_function('window.__fakeMonaco?.path === "README.md"')
            result=page.evaluate('''async()=>{
              const app=window.__mcelWorkbench.app;
              const mount=await app.execute('intent:studio-runtime-mount',{});
              const validate=await app.execute('intent:studio-runtime-validate',{});
              const replay=await app.execute('intent:studio-runtime-replay',{});
              return {mount:mount.disposition,validate:validate.disposition,
                replay:replay.disposition,
                state:JSON.parse(app.readState('state:scm-runtime-result')),
                urls:window.__networkCalls.map(c=>c.url)};
            }''')
            assert result['mount']=='completed'
            assert result['validate']=='completed'
            assert result['replay']=='completed'
            assert result['state']['deterministicStateMatch'] is True
            assert result['urls']==['/api/applications/editor/files','/api/applications/editor/read']
        finally:
            browser.close()
