"""Bounded Code Editor source authority, Aider handoff, and Monaco mount races.

No production services are contacted. Tests use browser JS and deterministic
HTTP/Monaco stand-ins to exercise the actual semantic port and runtime.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

from tools.mcel_phase2b_browser import launch_semantic_browser
from main_computer.mcel_semantic_other_apps_host import render_semantic_page

ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-read-ports.js'
ADAPTER = ROOT / 'main_computer/web/applications/scripts/code-editor-monaco-adapter.js'


def _make_page(browser, script=PORT):
    page = browser.new_page(viewport={'width': 1100, 'height': 800})
    page.add_script_tag(content=script.read_text(encoding='utf-8'))
    return page


JS_PORT_SETUP = '''() => {
  // Deterministic crypto sufficient to exercise token and receipt transitions.
  // The generated value is not used outside these isolated mocks.
  const hash=s=>String(Array.from(s).reduce((n,c)=>n+c.charCodeAt(0),0)%256)
    .padStart(2,'0').repeat(32);
  Object.defineProperty(window.crypto,'subtle',{configurable:true,value:{
    digest:async (_alg,bytes)=>Uint8Array.from(hash(new TextDecoder().decode(bytes))
      .match(/.{2}/g),v=>parseInt(v,16)).buffer
  }});
  const spec={
    'list-files':['source-reader','list','listSourceFiles','external-read'],
    'open-source':['source-reader','read','readSource','external-read'],
    'save-source':['source-writer','save','saveSource','source-write'],
    'run-aider':['aider-control','run','runAider','consequential'],
    'aider-jobs':['aider-context','jobs','readJobs','external-read']
  };
  window.__req=(intent,args)=>{
    const [cap,op,method,risk]=spec[intent];
    return {appId:'semantic-code-editor',intentId:'intent:'+intent,
      effectId:'effect:'+intent+'.request',capabilityId:'capability:'+cap,
      operation:op,runtimeMethod:method,risk,arguments:args};
  };
  window.__hash=hash;
  window.__reply=payload=>({ok:true,status:200,json:async()=>payload});
}'''


def test_close_during_inflight_source_save_does_not_restore_authority():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = _make_page(browser)
            page.evaluate(JS_PORT_SETUP)
            value = page.evaluate('''async () => {
              let releaseSave;
              const fetcher=async(url,opts)=>{
                const body=JSON.parse(opts?.body||'{}');
                if(url.endsWith('/editor/files'))return __reply({ok:true,path:'',entries:[
                  {path:'README.md',name:'README.md',kind:'file'}]});
                if(url.endsWith('/editor/read'))return __reply({ok:true,files:[
                  {path:'README.md',content:'before'}]});
                if(url.endsWith('/editor/project/file/save'))return new Promise(resolve=>{
                  releaseSave=()=>resolve(__reply({ok:true,
                    schema:'mcel-code-editor-file-save-v1',status:'pass',state:'applied',
                    handle:'a'.repeat(32),savedPath:'README.md',changedFiles:['README.md'],
                    receipt:{ok:true,rollback_required:false,transaction_id:'b'.repeat(24),files:[{
                      operation:'modify',path:'README.md',before_sha256:__hash('before'),
                      after_sha256:__hash('after'),written_sha256:__hash('after')}]}}));
                });
                throw Error(url);
              };
              const port=McelSemanticReadPorts.create({appId:'semantic-code-editor',fetcher});
              await port.invoke(__req('list-files',{}));
              await port.invoke(__req('open-source',{path:'README.md'}));
              const save=port.invoke(__req('save-source',{path:'README.md',text:'after'}));
              await Promise.resolve();
              port.closeDocument('README.md');
              releaseSave();
              const receipt=await save;
              const authorized=await port.authorize(__req('save-source',{path:'README.md',text:'third'}));
              return {receipt,authorized,status:port.documentWriteStatus('README.md')};
            }''')
            assert value == {'receipt': 'README.md', 'authorized': False, 'status': 'reopen-required'}
        finally:
            browser.close()


def test_aider_job_interlocks_every_file_until_terminal_status_and_reopen():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = _make_page(browser)
            page.evaluate(JS_PORT_SETUP)
            result = page.evaluate('''async () => {
              let status='running';let submitted=null;
              const fetcher=async(url,opts)=>{
                const body=JSON.parse(opts?.body||'{}');
                if(url.endsWith('/editor/files'))return __reply({ok:true,path:'',entries:[
                  {path:'README.md',name:'README.md',kind:'file'},
                  {path:'NOTES.md',name:'NOTES.md',kind:'file'}]});
                if(url.endsWith('/editor/read'))return __reply({ok:true,files:body.files.map(path=>
                  ({path,content:'before'}))});
                if(url.endsWith('/aider/run')){
                  submitted=body;
                  return __reply({ok:true,accepted:true,job:{id:'job-1'}});
                }
                if(url.endsWith('/aider/jobs'))return __reply({ok:true,
                  activities:[{id:'job-1',status}]});
                throw Error(url);
              };
              const port=McelSemanticReadPorts.create({appId:'semantic-code-editor',fetcher});
              await port.invoke(__req('list-files',{}));
              for(const path of ['README.md','NOTES.md'])
                await port.invoke(__req('open-source',{path}));
              const accepted=await port.invoke(__req('run-aider',{
                path:'README.md',instruction:'Fix both',files:'README.md,NOTES.md'}));
              const blocked={};
              for(const path of ['README.md','NOTES.md']){
                blocked[path]=[
                  await port.authorize(__req('open-source',{path})),
                  await port.authorize(__req('save-source',{path,text:'draft'})),
                  port.documentWriteStatus(path)];
              }
              await port.invoke(__req('aider-jobs',{}));
              const stillBlocked=await port.authorize(__req('open-source',{path:'README.md'}));
              status='complete';await port.invoke(__req('aider-jobs',{}));
              const canReopen=await port.authorize(__req('open-source',{path:'README.md'}));
              const cannotWriteBeforeReread=await port.authorize(__req('save-source',{path:'NOTES.md',text:'draft'}));
              await port.invoke(__req('open-source',{path:'README.md'}));
              const writeAfterRead=await port.authorize(__req('save-source',{path:'README.md',text:'draft'}));
              return {accepted,blocked,stillBlocked,canReopen,cannotWriteBeforeReread,
                writeAfterRead,submitted};
            }''')
            assert result['accepted'].startswith('Aider job accepted (not completed): job-1')
            assert result['blocked'] == {'README.md': [False, False, 'aider-pending'],
                                         'NOTES.md': [False, False, 'aider-pending']}
            assert result['stillBlocked'] is False
            assert result['canReopen'] is True
            assert result['cannotWriteBeforeReread'] is False
            assert result['writeAfterRead'] is True
            assert result['submitted']['files'] == ['README.md', 'NOTES.md']
            assert result['submitted']['dry_run'] is False
        finally:
            browser.close()


def test_real_monaco_adapter_discards_superseded_async_mount():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page = _make_page(browser, ADAPTER)
            result=page.evaluate('''async () => {
              const models=new Map();let creates=[];
              window.monaco={Uri:{parse:s=>s},editor:{
                getModel:uri=>models.get(uri)||null,
                createModel:(value,lang,uri)=>{
                  const model={value,getValue(){return this.value;},setValue(v){this.value=v;},
                    onDidChangeContent:()=>({dispose(){}})};
                  models.set(uri,model);return model;
                },
                create:(host,opts)=>{
                  creates.push(opts.model.getValue());
                  const node=document.createElement('div');node.className='monaco-editor';
                  host.append(node);
                  return {layout(){},dispose(){node.remove();},setModel(){},updateOptions(){}};
                }
              }};
              const host=document.createElement('div');document.body.append(host);
              const first=MainComputerMonacoAdapter.mount({host,path:'a.js',value:'first'});
              const second=MainComputerMonacoAdapter.mount({host,path:'b.js',value:'second'});
              const receipts=await Promise.all([first,second]);
              return {outcomes:receipts.map(x=>x.externalOutcome),creates,
                path:MainComputerMonacoAdapter.inspect().path,
                mounted:host.querySelectorAll('.monaco-editor').length};
            }''')
            assert result=={'outcomes':['superseded-mount','model-mounted'],
                            'creates':['second'],'path':'b.js','mounted':1}
        finally:
            browser.close()


def test_aider_ui_refuses_dirty_draft_before_http_submission():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page=browser.new_page(viewport={'width':1100,'height':840})
            page.evaluate('''() => {
              window.__aiderRuns=[];
              window.fetch=async(url,opts)=>{
                const path=String(url),body=JSON.parse(opts?.body||'{}');
                if(path.endsWith('/editor/files'))return {ok:true,status:200,json:async()=>({
                  ok:true,path:'',entries:[{path:'README.md',name:'README.md',kind:'file'}]})};
                if(path.endsWith('/editor/read'))return {ok:true,status:200,json:async()=>({
                  ok:true,files:[{path:body.files[0],content:'source'}]})};
                if(path.endsWith('/aider/run')){
                  window.__aiderRuns.push(body);
                  return {ok:true,status:200,json:async()=>({ok:true,accepted:true,job:{id:'job-1'}})};
                }
                throw Error('unexpected '+path);
              };
            }''')
            page.set_content(render_semantic_page('code-editor'))
            page.evaluate('''() => {
              const bytes=Uint8Array.from('ab'.repeat(32).match(/.{2}/g),x=>parseInt(x,16));
              Object.defineProperty(window.crypto,'subtle',{configurable:true,value:{digest:async()=>bytes.buffer}});
              window.MainComputerMonacoAdapter={
                mount:async opts=>{
                  opts.host.replaceChildren();const editor=document.createElement('div');
                  editor.className='monaco-editor';opts.host.append(editor);
                  window.__editor={...opts};return {ok:true,externalOutcome:'model-mounted'};
                },setModel:()=>({ok:true}),dispose:()=>({ok:true})};
            }''')
            page.get_by_role('button',name='List Files').click()
            page.locator('[data-record-key="README.md"] [data-select-key]').click()
            page.wait_for_function('window.__editor?.path==="README.md"')
            page.evaluate("() => window.__editor.onChange('local unsaved changes')")
            page.on('dialog',lambda dialog:dialog.accept())
            form=page.locator('form[data-intent="intent:run-aider"]')
            form.locator('input[name="instruction"]').fill('Refactor')
            form.locator('button[type="submit"]').click()
            page.wait_for_function('window.__mcelWorkbench.app.receipts().some(r=>r.intentId==="intent:run-aider")')
            receipts=page.evaluate('window.__mcelWorkbench.app.receipts().filter(r=>r.intentId==="intent:run-aider")')
            assert receipts[-1]['disposition']=='refused-before-attempt'
            assert 'unsaved' in receipts[-1]['reason']
            assert page.evaluate('window.__aiderRuns') == []
        finally:
            browser.close()


def test_generated_editor_aider_acceptance_blocks_then_reopens_after_terminal_receipt():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page=browser.new_page(viewport={'width':1100,'height':840})
            page.evaluate('''() => {
              window.__jobStatus='running';window.__sourceText='before';window.__aiderRuns=[];
              window.fetch=async(url,opts)=>{
                const path=String(url),body=JSON.parse(opts?.body||'{}');
                let payload;
                if(path.endsWith('/editor/files'))payload={ok:true,path:'',entries:[
                  {path:'README.md',name:'README.md',kind:'file'}]};
                else if(path.endsWith('/editor/read'))payload={ok:true,files:body.files.map(path=>
                  ({path,content:window.__sourceText}))};
                else if(path.endsWith('/aider/run')){
                  window.__aiderRuns.push(body);
                  payload={ok:true,accepted:true,job:{id:'job-1'}};
                }else if(path.endsWith('/aider/jobs'))payload={ok:true,activities:[
                  {id:'job-1',status:window.__jobStatus}]};
                else throw Error('unexpected '+path);
                return {ok:true,status:200,json:async()=>payload};
              };
            }''')
            page.set_content(render_semantic_page('code-editor'))
            page.evaluate('''() => {
              const bytes=Uint8Array.from('ab'.repeat(32).match(/.{2}/g),x=>parseInt(x,16));
              Object.defineProperty(window.crypto,'subtle',{configurable:true,value:{digest:async()=>bytes.buffer}});
              window.MainComputerMonacoAdapter={
                mount:async opts=>{
                  opts.host.replaceChildren();const editor=document.createElement('div');
                  editor.className='monaco-editor';opts.host.append(editor);
                  window.__editor={...opts};return {ok:true,externalOutcome:'model-mounted'};
                },setModel:opts=>{window.__editor={...window.__editor,...opts};return {ok:true}},
                dispose:()=>({ok:true})};
            }''')
            page.get_by_role('button',name='List Files').click()
            page.locator('[data-record-key="README.md"] [data-select-key]').click()
            page.wait_for_function('window.__editor?.path==="README.md"')
            page.on('dialog',lambda d:d.accept())
            form=page.locator('form[data-intent="intent:run-aider"]')
            form.locator('input[name="instruction"]').fill('Refactor source')
            form.locator('button[type="submit"]').click()
            page.wait_for_function('''() => window.__mcelWorkbench.app.receipts().some(r=>
              r.intentId==='intent:run-aider' && r.disposition==='accepted-deferred')''')
            assert len(page.evaluate('window.__aiderRuns'))==1
            assert page.evaluate('window.__editor.readOnly') is True
            assert page.locator('[data-mcel-document-save]').is_disabled()
            assert 'Aider job pending' in page.locator('[data-mcel-document-dirty]').inner_text()
            denied=page.evaluate("async()=>window.__mcelWorkbench.app.execute('intent:open-source')")
            assert denied['disposition']=='refused-before-attempt'
            page.evaluate("window.__sourceText='after';window.__jobStatus='complete'")
            page.get_by_role('button',name='Aider Jobs').click()
            page.wait_for_function('window.__editor?.value==="after"')
            assert page.evaluate('window.__editor.readOnly') is False
            assert page.evaluate('window.__mcelWorkbench.app.readState("state:source")')=='after'
            assert page.evaluate('window.__mcelWorkbench.app.receipts().filter(r=>r.intentId==="intent:open-source").at(-1).disposition')=='completed'
        finally:
            browser.close()


def test_malformed_save_receipt_revokes_previous_document_write_authority():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page=_make_page(browser)
            page.evaluate(JS_PORT_SETUP)
            result=page.evaluate('''async()=>{
              const fetcher=async(url,opts)=>{
                const body=JSON.parse(opts?.body||'{}');
                if(url.endsWith('/editor/files'))return __reply({ok:true,path:'',entries:[
                  {path:'README.md',name:'README.md',kind:'file'}]});
                if(url.endsWith('/editor/read'))return __reply({ok:true,files:[
                  {path:'README.md',content:'before'}]});
                if(url.endsWith('/editor/project/file/save'))return __reply({ok:true,
                  schema:'mcel-code-editor-file-save-v1',state:'applied',status:'pass',
                  savedPath:'README.md',receipt:{ok:true}});
                throw Error(url);
              };
              const port=McelSemanticReadPorts.create({appId:'semantic-code-editor',fetcher});
              await port.invoke(__req('list-files',{}));
              await port.invoke(__req('open-source',{path:'README.md'}));
              let error='';
              try{await port.invoke(__req('save-source',{path:'README.md',text:'after'}));}
              catch(e){error=String(e.message);}
              const writeAfter=await port.authorize(__req('save-source',{path:'README.md',text:'again'}));
              const status=port.documentWriteStatus('README.md');
              await port.invoke(__req('open-source',{path:'README.md'}));
              const writeAfterReopen=await port.authorize(__req('save-source',{path:'README.md',text:'again'}));
              return {error,writeAfter,status,writeAfterReopen};
            }''')
            assert result=={'error':'MCEL_READ_PORT:save-receipt-invalid','writeAfter':False,
                            'status':'reopen-required','writeAfterReopen':True}
        finally:
            browser.close()


def test_unverifiable_aider_acceptance_quarantines_all_selected_source_files():
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            page=_make_page(browser)
            page.evaluate(JS_PORT_SETUP)
            result=page.evaluate('''async()=>{
              const fetcher=async(url,opts)=>{
                const body=JSON.parse(opts?.body||'{}');
                if(url.endsWith('/editor/files'))return __reply({ok:true,path:'',entries:[
                  {path:'README.md',name:'README.md',kind:'file'},
                  {path:'NOTES.md',name:'NOTES.md',kind:'file'}]});
                if(url.endsWith('/editor/read'))return __reply({ok:true,files:body.files.map(path=>
                  ({path,content:'before'}))});
                if(url.endsWith('/aider/run'))return __reply({ok:true,accepted:true,job:{}});
                if(url.endsWith('/aider/jobs'))return __reply({ok:true,activities:[]});
                throw Error(url);
              };
              const port=McelSemanticReadPorts.create({appId:'semantic-code-editor',fetcher});
              await port.invoke(__req('list-files',{}));
              for(const path of ['README.md','NOTES.md'])await port.invoke(__req('open-source',{path}));
              let error='';
              try{await port.invoke(__req('run-aider',{path:'README.md',instruction:'Improve both',
                files:'README.md,NOTES.md'}));}catch(e){error=String(e.message);}
              await port.invoke(__req('aider-jobs',{}));
              const states=[];
              for(const path of ['README.md','NOTES.md'])states.push({path,
                status:port.documentWriteStatus(path),
                open:await port.authorize(__req('open-source',{path})),
                save:await port.authorize(__req('save-source',{path,text:'next'}))});
              return {error,states};
            }''')
            assert result=={'error':'MCEL_READ_PORT:aider-job-receipt-invalid',
                            'states':[{'path':'README.md','status':'aider-outcome-unverified',
                                       'open':False,'save':False},
                                      {'path':'NOTES.md','status':'aider-outcome-unverified',
                                       'open':False,'save':False}]}
        finally:
            browser.close()
