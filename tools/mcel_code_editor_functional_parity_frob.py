"""Code Editor incumbent-source vs semantic candidate functional acceptance.

The incumbent contract is independently enumerated from its checked-in UI/core.
The real semantic runtime + capability port execute in Chromium against an
in-memory, deterministic fake HTTP service. No OS writes, Aider jobs, or routes
are invoked. Monaco is replaced by a bounded text-model harness; its native
mount, real provider behavior and actual Windows writes remain UNVERIFIED.

Fail closed: missing browser dependencies cannot become SOURCE_COVERED passes.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT=Path(__file__).resolve().parents[1]
REPORT=Path('runtime/reports/mcel-code-editor-functional-parity/report.json')
LEGACY=Path('main_computer/web/applications/apps/code-editor.html')
LEGACY_JS=Path('main_computer/web/applications/scripts/code-editor.js')
LEGACY_CORE=Path('main_computer/web/applications/scripts/code-editor-core.js')
SOURCE=Path('tools/fixtures/mcel_semantic_other_apps/code-editor/application.js')
PORT=Path('main_computer/web/applications/scripts/mcel-semantic-read-ports.js')
RUNTIME=Path('main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js')

@dataclass(frozen=True)
class Feature:
    key:str
    label:str
    dom:tuple[str,...]
    incumbent:tuple[str,...]
    intents:tuple[str,...]
    live_gate:str='Compare actual Windows-host route, local capability authority and persistent evidence'

FEATURES=(
    Feature('file-inventory','List current workspace and exact relative paths',('file-map-refresh',),('inspectWorkspace(',),('list-files','browse-directory')),
    Feature('source-open','Open an observed file into an editor model',('code-studio-runtime-draft',),('openFile(',),('select-file','open-source')),
    Feature('multi-document','Maintain two independent opened document tabs',('code-editor-app',),('activateDocument(',),('select-file','open-source')),
    Feature('draft-retention','Preserve dirty draft when switching tabs',('code-studio-runtime-draft',),('applyDraftEdit(',),('open-source',)),
    Feature('explicit-save','Write only selected exact document after explicit confirmation',('code-studio-commit-runtime',),('saveFile(',),('save-source',),'Real Windows source save and file receipt'),
    Feature('stale-source','Reject stale baselines and unknown file identities',('code-editor-app',),('buildSaveRequest(',),('open-source','save-source'),'Windows-host concurrent writer test'),
    Feature('save-receipt','Verify source hash, changed file and transaction evidence',('code-editor-app',),('applySavedFile(',),('save-source',),'Real source transaction receipt validation'),
    Feature('discard-close','Discard and close the correct tab with consent',('code-editor-app',),('discardDraft(', 'closeFile('),('open-source',)),
    Feature('review-prepare','Prepare a changed document for explicit approval',('code-editor-transaction-preview',),('buildPatchPreviewRequest(',),('prepare-reviewed-change',)),
    Feature('review-apply','Apply exactly the prepared, bound transaction',('code-editor-transaction-apply',),('applyReviewedPatch(',),('apply-reviewed-change',),'Windows-host reviewed transaction'),
    Feature('aider-preview','Prepare an Aider dry-run without mutation',('aider-output',),('previewAiderPlan(',),('preview-aider-plan',),'Live Aider dry-run provider'),
    Feature('aider-jobs','Read Aider session and job inventory',('aider-output',),('previewAiderPlan(',),('aider-context','aider-jobs'),'Real Aider job authority and session storage'),
    Feature('aider-effect','Scope an explicit asynchronous Aider job to observed clean files',('aider-output',),('previewAiderPlan(',),('run-aider',),'Disposable Aider job and terminal receipt'),
    Feature('documentation','Browse manifest-authorized component documentation',('code-editor-app',),('inspectWorkspace(',),('list-component-docs','choose-component-doc','read-component-doc'),'Live documentation service and isolated script viewport'),
    Feature('scm-definition','Inspect source-of-truth SCM definitions without executing them',('code-editor-app',),('mount(',),('inspect-scm-component-contract','inspect-scm-route-contract')),
    Feature('studio-source','Read-only checks and serialization of selected draft',('code-editor-app',),('editDraft(',),('check-studio-draft','preview-studio-serialization')),
    Feature('studio-kernel','Bounded in-memory SCM lifecycle, proof and replay',('code-editor-app',),('editDraft(',),('studio-runtime-mount','studio-runtime-validate','studio-runtime-evidence','studio-proof-list'),
            'Real hosted SCM lifecycle and live preview remain unverified'),
    Feature('authorization','Prevent forged paths, stale revisions and automatic consequential work',('code-editor-app',),('normalizeProjectPath(',),('save-source','run-aider'),'Remote/session authority boundary and CSRF protections'),
)



def source_inventory(repo:Path, overrides:dict[str,str]|None=None)->dict:
    files=(LEGACY,LEGACY_JS,LEGACY_CORE,SOURCE,PORT,RUNTIME)
    values={str(f):(repo/f).read_text('utf8') for f in files}
    values.update(overrides or {})
    ir=compile_semantic_app('code-editor',repo)
    ids={i['id'] for i in ir['intents']}
    markup=values[str(LEGACY)]
    legacy=values[str(LEGACY_JS)]+'\n'+values[str(LEGACY_CORE)]
    features={}
    for f in FEATURES:
        missing_dom=[x for x in f.dom if 'id="'+x+'"' not in markup and 'id="'+x+'"' not in legacy]
        missing_legacy=[x for x in f.incumbent if 'function '+x not in legacy]
        missing_intents=[x for x in f.intents if 'intent:'+x not in ids]
        features[f.key]={'label':f.label,'status':'UNVERIFIED' if missing_dom or missing_legacy else 'MISSING' if missing_intents else 'SOURCE_COVERED',
                         'incumbent':{'missingDomIds':missing_dom,'missingHandlerSignatures':missing_legacy},
                         'candidate':{'missingIntentIds':missing_intents},'browserScenarios':[],
                         'requiredNextEvidence':f.live_gate}
    return {'features':features,'declaredIntentIds':sorted(ids),'sourceEvidence':{
        'sha256':{str(k):hashlib.sha256(v.encode()).hexdigest() for k,v in values.items()},
        'candidateIrFingerprint':hashlib.sha256(json.dumps(ir,sort_keys=True).encode()).hexdigest(),
        'semanticIntentCount':len(ids),'layoutFree':not bool(ir.get('layouts'))}}

# Mocking external IO only: the actual semantic DSL, renderer and read-port
# enforce authorization. Dummy Monaco implements the minimal onChange contract.
FIXTURE_JS=r'''<script>
window.__editorCalls=[];window.__editorEffects=[];
window.__editorFiles={'src/a.js':'const a=1;\n','src/b.js':'const b=2;\n',
 'src/studio.html':'<main data-mc-component="code-workspace" data-mc-use-case="source-safe-code-editor"><h1 data-mc-field="workspace-title">Editor</h1><section data-mc-component="code-file" data-mc-file-path="src/a.js">A</section></main>',
 'docs/readme.md':'# Documentation\n'};
window.__editorFixture={allowConfirm:true,corruptReceipt:false,externalChange:false,
  aiderTerminal:false,malformedRead:false,prepare:null};
window.confirm=()=>window.__editorFixture.allowConfirm;
// Deterministic 32-byte digest for the fake endpoint only. The production
// port's actual use of crypto.subtle and exact receipt checks is unmodified.
Object.defineProperty(window.crypto,'subtle',{configurable:true,value:{digest:async(_algorithm,buf)=>{
 let h=2166136261;for(const c of new Uint8Array(buf)){h^=c;h=Math.imul(h,16777619)>>>0;}
 const out=new Uint8Array(32);for(let i=0;i<32;i++)out[i]=(h>>((i%4)*8))&255;
 return out.buffer;
}}});
window.__editorHash=async text=>[...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))].map(x=>x.toString(16).padStart(2,'0')).join('');
window.fetch=async(uri,opts={})=>{
 let body={};try{body=JSON.parse(opts.body||'{}')}catch{}
 const path=String(uri),method=String(opts.method||'GET');
 window.__editorCalls.push({path,body,method});
 const reply=(obj,code=200)=>({ok:code<400,status:code,json:async()=>obj});
 if(path.endsWith('/editor/files')){
  const prefix=body.path||'';
  const dirs=prefix===''?['src','docs']:[];
  const entries=[...dirs.map(x=>({path:x,name:x,kind:'dir'})),
   ...Object.keys(window.__editorFiles).filter(x=>prefix && x.startsWith(prefix+'/') && x.slice(prefix.length+1).indexOf('/')===-1)
      .map(x=>({path:x,name:x.split('/').at(-1),kind:'file'}))];
  return reply({path:prefix,entries});
 }
 if(path.endsWith('/editor/read')){
  const files=body.files||[];
  return reply({files:files.map(x=>({path:x,content:window.__editorFiles[x]||''}))});
 }
 if(path.endsWith('/editor/project/file/save')){
  const actual=await window.__editorHash(window.__editorFiles[body.path]||'');
  if(body.expected_before_sha256!==actual)return reply({error:'stale-source'},409);
  if(window.__editorFixture.corruptReceipt){window.__editorFixture.corruptReceipt=false;return reply({schema:'forged-receipt',state:'applied'});}
  const after=await window.__editorHash(body.replacement_text);
  window.__editorFiles[body.path]=body.replacement_text;
  window.__editorEffects.push({kind:'save',path:body.path,after});
  return reply({schema:'mcel-code-editor-file-save-v1',state:'applied',status:'pass',
   handle:'a'.repeat(32),savedPath:body.path,changedFiles:[body.path],
   receipt:{ok:true,rollback_required:false,transaction_id:'b'.repeat(24),files:[{
    path:body.path,operation:'modify',before_sha256:actual,after_sha256:after,written_sha256:after}]}});
 }
 if(path.endsWith('/editor/project/transaction/prepare')){
  const change=body.changes[0];let current=await window.__editorHash(window.__editorFiles[change.path]);
  if(change.expected_before_sha256!==current)return reply({error:'stale-reviewed-source'},409);
  window.__editorFixture.prepare={path:change.path,text:change.replacement_text,before:current,
    after:await window.__editorHash(change.replacement_text)};
  return reply({schema:'mcel-code-editor-project-transaction-v1',state:'prepared',handle:'c'.repeat(32),transaction:{transaction_id:'reviewed-test'}});
 }
 if(path.endsWith('/editor/project/transaction/apply')){
  const prep=window.__editorFixture.prepare;
  if(body.handle!=='c'.repeat(32)||!prep)return reply({error:'no-prepared-transaction'},400);
  window.__editorFiles[prep.path]=prep.text;
  window.__editorEffects.push({kind:'review',path:prep.path});
  return reply({schema:'mcel-code-editor-project-transaction-v1',state:'applied',handle:body.handle,
    changedFiles:[prep.path],receipt:{ok:true,rollback_required:false,files:[{path:prep.path,operation:'modify',
    before_sha256:prep.before,after_sha256:prep.after,written_sha256:prep.after}]}});
 }
 if(path.endsWith('/aider/prepare'))return reply({ok:true,command:['aider','--dry-run',...body.files]});
 if(path.endsWith('/aider/run')){
  window.__editorEffects.push({kind:'aider-accepted',files:body.files});
  return reply({ok:true,accepted:true,job:{id:'job-one',status:'queued'}});
 }
 if(path.endsWith('/aider/jobs'))return reply({activities:window.__editorFixture.aiderTerminal?[{id:'job-one',status:'complete'}]:[{id:'job-one',status:'running'}]});
 if(path.endsWith('/aider/context'))return reply({ok:true,active:{id:'ctx-1',repo_dir:'.'},archives:[{id:'archive-1',repo_dir:'.'}],activities:[]});
 if(path.endsWith('/aider/context/archive') || path.endsWith('/aider/context/reset') || path.endsWith('/aider/context/load'))return reply({ok:true,active:{id:'ctx-2',repo_dir:'.'},archives:[]});
 if(path.endsWith('/component-docs/manifest'))return reply({entries:[{id:'component.one',title:'Component One'}]});
 if(path.endsWith('/component-docs/read'))return reply({id:body.id,exists:true,content_type:'text/html',content:'<h1>Document</h1>'});
 if(path.endsWith('/component-docs/viewport-config'))return reply({config:{default_snap_mode:'balanced',presets:{desktop:{max_width:900,max_height:800}}}});
 return reply({error:'not-isolated-endpoint:'+path},404);
};
</script>'''
MONACO_JS=r'''window.__editorMonacoMounts=[];
window.MainComputerMonacoAdapter={
  mount:async({host,path,value,readOnly,onChange,onReceipt})=>{
    host.replaceChildren();const area=document.createElement('textarea');
    area.dataset.fakeMonacoPath=path;area.value=value;area.readOnly=readOnly;
    area.addEventListener('input',()=>onChange(area.value));
    host.append(area);window.__editorModel=area;
    window.__editorMonacoMounts.push({path,value,readOnly});
    onReceipt?.({effect:'editor.monaco.load',externalOutcome:'mounted'});
    return {ok:true};},
  setModel:({value,readOnly})=>{if(window.__editorModel){window.__editorModel.value=value;window.__editorModel.readOnly=readOnly;}return {ok:true};},
  dispose:()=>{window.__editorModel=null;}
};'''

def candidate_html(repo:Path)->str:
    html=render_semantic_page('code-editor',repo)
    token='const port=McelSemanticReadPorts.create({appId:ir.application.appId});'
    if html.count(token)!=1:raise RuntimeError('Editor port mount contract changed')
    html=html.replace('<head>','<head>'+FIXTURE_JS,1)
    html=html.replace(token,MONACO_JS+'\nconst port=(window.__editorPort=McelSemanticReadPorts.create({appId:ir.application.appId}));',1)
    return html

def case(cases:list,feature:str,name:str,expected:Any,observed:Any,ok:bool,note:str=''):
    cases.append({'feature':feature,'id':name,'expected':expected,'observed':observed,
                  'status':'PASS' if ok else 'MISMATCH','mockedHttp':True,'note':note or None})

def calls(page,suffix):
    return page.evaluate('(suffix)=>window.__editorCalls.filter(e=>e.path.endsWith(suffix))',suffix)

def receipt(page,identifier):
    return page.evaluate('(id)=>window.__mcelWorkbench.app.receipts().filter(x=>x.intentId==="intent:"+id).at(-1)',identifier)

def click(page,name):
    target=page.locator(f'button[data-intent="intent:{name}"]')
    panel=target.evaluate('el=>el.closest("[data-mcel-tool-panel]")?.id||null')
    if panel:page.locator(f'[data-mcel-tool-tabs] [aria-controls="{panel}"]').click()
    target.click()

def ready(page,identifier='open-source'):
    page.wait_for_function('''id=>window.__mcelWorkbench?.app.receipts().some(x=>x.intentId==='intent:'+id && x.disposition==='completed')''',identifier,timeout=8000)

def browser_acceptance(repo:Path)->dict:
    from playwright.sync_api import sync_playwright
    cases=[];errors=[]
    with sync_playwright() as pw:
      browser,backend=launch_semantic_browser(pw)
      page=browser.new_page(viewport={'width':1272,'height':900});page.set_default_timeout(6500)
      page.on('pageerror',lambda e:errors.append(str(e)))
      try:
        page.set_content(candidate_html(repo))
        status=page.locator('#mcel-workbench-status').inner_text()
        case(cases,'source-open','semantic-mount','Semantic candidate ready',status,status=='Semantic candidate ready')
        if status!='Semantic candidate ready':return {'status':'INCOMPLETE','cases':cases,'pageErrors':errors,'backend':backend}
        case(cases,'authorization','no-implicit-effects',0,page.evaluate('window.__editorEffects.length'),not page.evaluate('window.__editorEffects.length'))
        click(page,'list-files');page.locator('[data-record-key="src"] button[data-select-key="src"]').wait_for()
        case(cases,'file-inventory','root-discovery',['docs','src'],sorted(page.locator('[data-record-key]').evaluate_all('a=>a.map(x=>x.dataset.recordKey)')),
             sorted(page.locator('[data-record-key]').evaluate_all('a=>a.map(x=>x.dataset.recordKey)'))==['docs','src'])
        page.locator('[data-record-key="src"] button[data-select-key="src"]').click()
        page.locator('[data-record-key="src/a.js"] button').wait_for()
        case(cases,'file-inventory','directory-scope','src',calls(page,'/editor/files')[-1]['body']['path'],calls(page,'/editor/files')[-1]['body']['path']=='src')
        paths=sorted(page.locator('[data-record-key]').evaluate_all('a=>a.map(x=>x.dataset.recordKey)'))
        case(cases,'file-inventory','exact-files',['src/a.js','src/b.js','src/studio.html'],[x for x in paths if x.startswith('src/')],[x for x in paths if x.startswith('src/')]==['src/a.js','src/b.js','src/studio.html'])
        page.locator('[data-record-key="src/a.js"] button').click()
        page.locator('[data-fake-monaco-path="src/a.js"]').wait_for()
        case(cases,'source-open','observed-file-read','src/a.js',calls(page,'/editor/read')[-1]['body']['files'][0],calls(page,'/editor/read')[-1]['body']['files']==['src/a.js'])
        case(cases,'source-open','source-mount','const a=1;\n',page.locator('[data-fake-monaco-path="src/a.js"]').input_value(),
             page.locator('[data-fake-monaco-path="src/a.js"]').input_value()=='const a=1;\n')
        page.locator('[data-fake-monaco-path="src/a.js"]').fill('const a=3;\n')
        case(cases,'draft-retention','edited-tab-dirty',True,page.locator('[data-mcel-document-dirty]').get_attribute('data-mcel-document-dirty'),
             page.locator('[data-mcel-document-dirty]').get_attribute('data-mcel-document-dirty')=='true')
        page.locator('[data-record-key="src/b.js"] button').click()
        page.locator('[data-fake-monaco-path="src/b.js"]').wait_for()
        page.locator('[data-fake-monaco-path="src/b.js"]').fill('const b=4;\n')
        paths=page.locator('[data-mcel-document-tabs] [data-mcel-tab-path]').evaluate_all('xs=>xs.map(x=>x.dataset.mcelTabPath)')
        case(cases,'multi-document','two-open-tabs',['src/a.js','src/b.js'],paths,paths==['src/a.js','src/b.js'])
        page.locator('[data-mcel-tab-path="src/a.js"]').click()
        page.locator('[data-fake-monaco-path="src/a.js"]').wait_for()
        value=page.locator('[data-fake-monaco-path="src/a.js"]').input_value()
        case(cases,'draft-retention','dirty-a-survives-b','const a=3;\n',value,value=='const a=3;\n')
        enabled=not page.locator('[data-mcel-document-save]').is_disabled()
        case(cases,'explicit-save','switched-tab-writable',True,enabled,enabled)
        page.evaluate('window.__editorFixture.allowConfirm=false')
        count_before=len(calls(page,'/editor/project/file/save'))
        page.locator('[data-mcel-document-save]').click()
        count_after=len(calls(page,'/editor/project/file/save'))
        case(cases,'authorization','cancelled-save-has-no-effect',count_before,count_after,count_before==count_after)
        page.evaluate('window.__editorFixture.allowConfirm=true')
        page.locator('[data-mcel-document-save]').click()
        page.wait_for_function('window.__editorEffects.some(x=>x.kind==="save")')
        saved=calls(page,'/editor/project/file/save')[-1]['body']
        case(cases,'explicit-save','save-exact-target','src/a.js',saved['path'],saved['path']=='src/a.js')
        case(cases,'explicit-save','save-content-binding','const a=3;\n',saved['replacement_text'],saved['replacement_text']=='const a=3;\n')
        files=page.evaluate('window.__editorFiles')
        case(cases,'multi-document','other-dirty-tab-not-written','const b=2;\n',files['src/b.js'],files['src/b.js']=='const b=2;\n')
        case(cases,'save-receipt','save-settled','completed',receipt(page,'save-source')['disposition'],receipt(page,'save-source')['disposition']=='completed')
        page.locator('[data-mcel-tab-path="src/b.js"]').click()
        page.locator('[data-fake-monaco-path="src/b.js"]').wait_for()
        case(cases,'draft-retention','dirty-b-survives-a','const b=4;\n',page.locator('[data-fake-monaco-path="src/b.js"]').input_value(),page.locator('[data-fake-monaco-path="src/b.js"]').input_value()=='const b=4;\n')
        # Actual backend content changes while B is dirty. Switching away and
        # back must never silently authorize B's stale draft for save.
        page.locator('[data-mcel-tab-path="src/a.js"]').click()
        page.locator('[data-fake-monaco-path="src/a.js"]').wait_for()
        page.evaluate('window.__editorFiles["src/b.js"]="const b=99;\\n"')
        page.locator('[data-mcel-tab-path="src/b.js"]').click()
        page.locator('[data-fake-monaco-path="src/b.js"]').wait_for()
        status=page.locator('[data-mcel-document-editor] [role="status"]').inner_text()
        blocked=page.locator('[data-mcel-document-save]').is_disabled()
        case(cases,'stale-source','conflict-on-outside-write',True,blocked and 'changed outside' in status,blocked and 'changed outside' in status)
        case(cases,'stale-source','stale-save-absent',0,len([x for x in calls(page,'/editor/project/file/save') if x['body']['path']=='src/b.js']),
             not [x for x in calls(page,'/editor/project/file/save') if x['body']['path']=='src/b.js'])
        # Closing dirty B must revoke B independently and reactivate A.
        page.locator('[data-mcel-close-tab="src/b.js"]').click()
        page.locator('[data-fake-monaco-path="src/a.js"]').wait_for()
        tabs=page.locator('[data-mcel-tab-path]').evaluate_all('xs=>xs.map(x=>x.dataset.mcelTabPath)')
        case(cases,'discard-close','close-only-target',['src/a.js'],tabs,tabs==['src/a.js'])
        portProbe=page.evaluate('''async()=>{
          const p=window.__editorPort;
          const req=path=>({appId:'semantic-code-editor',intentId:'intent:open-source',
            effectId:'effect:open-source.request',capabilityId:'capability:source-reader',
            operation:'read',runtimeMethod:'readSource',risk:'external-read',arguments:{path}});
          return [await p.authorize(req('../private.txt')),await p.authorize(req('src/secret.js')),
            await p.authorize(req('src/a.js'))];
        }''')
        case(cases,'authorization','forged-file-refusal',[False,False,True],portProbe,portProbe==[False,False,True])
        # A fresh dirty A should be eligible for a reviewed *prepare* which
        # cannot itself write. Explicit apply uses one exact server handle.
        page.locator('[data-fake-monaco-path="src/a.js"]').fill('const a=11;\n')
        before=page.evaluate('window.__editorEffects.length')
        click(page,'prepare-reviewed-change')
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:prepare-reviewed-change" && x.disposition==="completed")')
        prepared=page.evaluate('window.__editorFixture.prepare')
        case(cases,'review-prepare','prepare-exact-current-file','src/a.js',prepared['path'],prepared['path']=='src/a.js')
        case(cases,'review-prepare','prepare-does-not-mutate',before,page.evaluate('window.__editorEffects.length'),page.evaluate('window.__editorEffects.length')==before)
        click(page,'apply-reviewed-change')
        page.wait_for_function('window.__editorEffects.some(x=>x.kind==="review")')
        effects=page.evaluate('window.__editorEffects.filter(x=>x.kind==="review")')
        case(cases,'review-apply','review-apply-only-a',['src/a.js'],[e['path'] for e in effects],[e['path'] for e in effects]==['src/a.js'])
        case(cases,'review-apply','review-content-applied','const a=11;\n',page.evaluate('window.__editorFiles["src/a.js"]'),page.evaluate('window.__editorFiles["src/a.js"]')=='const a=11;\n')
        # Aider preview is source-scoped and has no source mutation.
        form=page.locator('form[data-intent="intent:preview-aider-plan"]')
        form.locator('input[name="instruction"]').fill('Summarize this code')
        form.locator('button[type="submit"]').click()
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:preview-aider-plan" && x.disposition==="completed")')
        case(cases,'aider-preview','aider-dry-run-no-effects',0,len([x for x in page.evaluate('window.__editorEffects') if x['kind'].startswith('aider')]),
             not [x for x in page.evaluate('window.__editorEffects') if x['kind'].startswith('aider')])
        case(cases,'aider-preview','aider-current-file','src/a.js',calls(page,'/aider/prepare')[-1]['body']['files'][0],calls(page,'/aider/prepare')[-1]['body']['files']==['src/a.js'])
        click(page,'aider-context')
        case(cases,'aider-jobs','context-read-only','completed',receipt(page,'aider-context')['disposition'],receipt(page,'aider-context')['disposition']=='completed')
        click(page,'aider-jobs')
        page.wait_for_function('window.__editorCalls.some(x=>x.path.endsWith("/aider/jobs"))')
        case(cases,'aider-jobs','job-observation',True,bool(calls(page,'/aider/jobs')),bool(calls(page,'/aider/jobs')))
        # An explicit Aider job is accepted asynchronously, never equivalent
        # to a completed source write. No task outside the fake service runs.
        form=page.locator('form[data-intent="intent:run-aider"]')
        form.locator('input[name="instruction"]').fill('Make a harmless change')
        form.locator('input[name="files"]').fill('')
        form.locator('button[type="submit"]').click()
        page.wait_for_function('window.__editorEffects.some(x=>x.kind==="aider-accepted")')
        case(cases,'aider-effect','accepted-not-completed','accepted-deferred',receipt(page,'run-aider')['disposition'],
             receipt(page,'run-aider')['disposition']=='accepted-deferred')
        case(cases,'aider-effect','aider-limited-files',['src/a.js'],page.evaluate('window.__editorEffects.find(x=>x.kind==="aider-accepted").files'),
             page.evaluate('window.__editorEffects.find(x=>x.kind==="aider-accepted").files')==['src/a.js'])
        case(cases,'aider-effect','pending-write-revoked','aider-pending',page.evaluate('window.__editorPort.documentWriteStatus("src/a.js")'),
             page.evaluate('window.__editorPort.documentWriteStatus("src/a.js")')=='aider-pending')
        click(page,'aider-jobs')
        page.wait_for_function('window.__editorCalls.filter(x=>x.path.endsWith("/aider/jobs")).length>=2')
        case(cases,'aider-effect','job-remains-pending','aider-pending',page.evaluate('window.__editorPort.documentWriteStatus("src/a.js")'),
             page.evaluate('window.__editorPort.documentWriteStatus("src/a.js")')=='aider-pending')
        page.evaluate('window.__editorFixture.aiderTerminal=true')
        click(page,'aider-jobs')
        page.wait_for_function('window.__editorCalls.filter(x=>x.path.endsWith("/aider/jobs")).length>=3')
        page.wait_for_function('window.__editorPort.documentWriteStatus("src/a.js")!=="aider-pending"')
        case(cases,'aider-effect','completion-requires-reopen','ready',page.evaluate('window.__editorPort.documentWriteStatus("src/a.js")'),
             page.evaluate('window.__editorPort.documentWriteStatus("src/a.js")')=='ready')
        # Manifest-bound docs; returned HTML must remain text, not trusted DOM.
        click(page,'list-component-docs')
        page.locator('[data-record-key="component.one"]').wait_for()
        page.locator('[data-record-key="component.one"] button').click()
        click(page,'read-component-doc')
        page.wait_for_function('window.__mcelWorkbench.app.readState("state:component-doc-content").includes("<h1>")')
        case(cases,'documentation','manifest-scoped-document','component.one',calls(page,'/component-docs/read')[-1]['body']['id'],calls(page,'/component-docs/read')[-1]['body']['id']=='component.one')
        case(cases,'documentation','returned-html-is-inert',0,page.locator('[data-state="state:component-doc-content"] h1').count(),
             page.locator('[data-state="state:component-doc-content"] h1').count()==0)
        # The source-of-truth SCM definitions are read, never executed.
        click(page,'inspect-scm-component-contract');page.wait_for_function('window.__mcelWorkbench.app.readState("state:scm-component-contract").includes("scm-component-definition")')
        case(cases,'scm-definition','component-contract-inspected',True,
             'scm-component-definition' in page.evaluate('window.__mcelWorkbench.app.readState("state:scm-component-contract")'),
             'scm-component-definition' in page.evaluate('window.__mcelWorkbench.app.readState("state:scm-component-contract")'))
        click(page,'inspect-scm-route-contract');page.wait_for_function('window.__mcelWorkbench.app.readState("state:scm-route-contract").includes("scm-route-definition")')
        case(cases,'scm-definition','route-contract-inspected',True,
             'scm-route-definition' in page.evaluate('window.__mcelWorkbench.app.readState("state:scm-route-contract")'),
             'scm-route-definition' in page.evaluate('window.__mcelWorkbench.app.readState("state:scm-route-contract")'))
        # Studio checks must produce explicit bounded source evidence without
        # a write, and SCM lifecycle must remain in memory.
        click(page,'check-studio-draft')
        page.wait_for_function('window.__mcelWorkbench.app.readState("state:studio-source-checks").includes("studio-source-static-checks")')
        source_check=page.evaluate('window.__mcelWorkbench.app.readState("state:studio-source-checks")')
        case(cases,'studio-source','source-check-evidence',True,'studio-source-static-checks' in source_check,'studio-source-static-checks' in source_check)
        click(page,'preview-studio-serialization')
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:preview-studio-serialization")')
        refusal=receipt(page,'preview-studio-serialization')
        case(cases,'studio-source','invalid-source-serialization-refused','failed',refusal['disposition'],refusal['disposition']=='failed')
        click(page,'studio-runtime-mount')
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:studio-runtime-mount")')
        mounted=receipt(page,'studio-runtime-mount')
        case(cases,'studio-kernel','in-memory-scm-mount','completed',mounted['disposition'],mounted['disposition']=='completed')
        if mounted['disposition']=='completed':
            click(page,'studio-runtime-evidence')
            page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:studio-runtime-evidence")')
            proof=receipt(page,'studio-runtime-evidence')
            case(cases,'studio-kernel','scm-evidence-only','completed',proof['disposition'],proof['disposition']=='completed')
            click(page,'studio-proof-list')
            page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:studio-proof-list")')
            prooflist=receipt(page,'studio-proof-list')
            case(cases,'studio-kernel','scm-proof-index','completed',prooflist['disposition'],prooflist['disposition']=='completed')
        else:
            case(cases,'studio-kernel','scm-evidence-only','completed','not-attempted',False)
            case(cases,'studio-kernel','scm-proof-index','completed','not-attempted',False)
        # Context mutations are separate consequential operations. Each is
        # triggered explicitly; neither grants source-file write authority.
        form=page.locator('form[data-intent="intent:load-aider-archive"]')
        form.locator('input[name="archiveId"]').fill('archive-1')
        form.locator('button[type="submit"]').click()
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:load-aider-archive")')
        case(cases,'aider-effect','observed-archive-load','completed',receipt(page,'load-aider-archive')['disposition'],
             receipt(page,'load-aider-archive')['disposition']=='completed')
        click(page,'archive-aider-context')
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:archive-aider-context")')
        case(cases,'aider-effect','context-archive-explicit','completed',receipt(page,'archive-aider-context')['disposition'],
             receipt(page,'archive-aider-context')['disposition']=='completed')
        click(page,'reset-aider-context')
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:reset-aider-context")')
        case(cases,'aider-effect','context-reset-explicit','completed',receipt(page,'reset-aider-context')['disposition'],
             receipt(page,'reset-aider-context')['disposition']=='completed')
        click(page,'read-component-doc-viewport-config')
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:read-component-doc-viewport-config")')
        case(cases,'documentation','viewport-config-observed','completed',receipt(page,'read-component-doc-viewport-config')['disposition'],
             receipt(page,'read-component-doc-viewport-config')['disposition']=='completed')
        form=page.locator('form[data-intent="intent:run-component-doc-script"]')
        form.locator('textarea[name="script"]').fill('vram.fillRect(0,0,2,2,[255,0,0,255]);')
        form.locator('button[type="submit"]').click()
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:run-component-doc-script")',timeout=9000)
        sandbox=receipt(page,'run-component-doc-script')
        case(cases,'documentation','sandboxed-pixel-script','completed',sandbox['disposition'],sandbox['disposition']=='completed')
        # A separate valid document must pass the clean-serialization gate.
        # This is a positive witness, not merely the invalid-source refusal.
        page.locator('[data-record-key="src/studio.html"] button').click()
        page.locator('[data-fake-monaco-path="src/studio.html"]').wait_for()
        click(page,'check-studio-draft')
        page.wait_for_function('window.__mcelWorkbench.app.readState("state:studio-source-checks").includes("src/studio.html")')
        checks=json.loads(page.evaluate('window.__mcelWorkbench.app.readState("state:studio-source-checks")'))
        case(cases,'studio-source','valid-studio-source-check',True,checks['sourceCheckPassed'],checks['sourceCheckPassed'] is True)
        click(page,'preview-studio-serialization')
        page.wait_for_function('window.__mcelWorkbench.app.readState("state:studio-serialized-preview").includes("studio-clean-serialization-preview")')
        serialized=json.loads(page.evaluate('window.__mcelWorkbench.app.readState("state:studio-serialized-preview")'))
        case(cases,'studio-source','valid-clean-serialization',True,
             serialized['kind']=='studio-clean-serialization-preview' and 'code-workspace' in serialized['serialized'] and serialized['sourceMutation']=='none',
             serialized['kind']=='studio-clean-serialization-preview' and 'code-workspace' in serialized['serialized'] and serialized['sourceMutation']=='none')
        # Exercise all in-memory SCM lifecycle and proof intents against a
        # manifest-valid opened source. None may address production HTTP.
        click(page,'studio-runtime-mount')
        page.wait_for_function('window.__mcelWorkbench.app.receipts().filter(x=>x.intentId==="intent:studio-runtime-mount").length>=2')
        case(cases,'studio-kernel','valid-source-kernel-mount','completed',receipt(page,'studio-runtime-mount')['disposition'],
             receipt(page,'studio-runtime-mount')['disposition']=='completed')
        for op in ('validate','serialize','repair','route-enter','route-leave','stage','commit','evidence','replay'):
            click(page,'studio-runtime-'+op)
            page.wait_for_function('(id)=>window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:studio-runtime-"+id)',arg=op)
            outcome=receipt(page,'studio-runtime-'+op)
            case(cases,'studio-kernel','kernel-'+op,'completed',outcome['disposition'],outcome['disposition']=='completed')
        click(page,'studio-proof-list')
        page.wait_for_function('window.__mcelWorkbench.app.receipts().filter(x=>x.intentId==="intent:studio-proof-list").length>=2')
        case(cases,'studio-kernel','valid-source-proof-list','completed',receipt(page,'studio-proof-list')['disposition'],
             receipt(page,'studio-proof-list')['disposition']=='completed')
        proofrow=page.locator('[data-collection$=".studio-proof-index"] [data-record-key]').first
        if proofrow.count():proofrow.locator('button').first.click()
        case(cases,'studio-kernel','proof-selection-observed',True,receipt(page,'studio-proof-select') is not None,
             receipt(page,'studio-proof-select') is not None)
        for op in ('studio-proof-inspect','studio-proof-export','studio-repair-packet'):
            form=page.locator(f'form[data-intent="intent:{op}"]')
            form.locator('input[name="filter"]').fill('all')
            form.locator('button[type="submit"]').click()
            page.wait_for_function('(id)=>window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:"+id)',arg=op)
            outcome=receipt(page,op)
            case(cases,'studio-kernel','proof-'+op,'completed',outcome['disposition'],outcome['disposition']=='completed')
        click(page,'studio-regression-run')
        page.wait_for_function('window.__mcelWorkbench.app.receipts().some(x=>x.intentId==="intent:studio-regression-run")')
        outcome=receipt(page,'studio-regression-run')
        case(cases,'studio-kernel','isolated-kernel-regressions','completed',outcome['disposition'],outcome['disposition']=='completed')
        # A server response that looks like an applied write but carries an
        # invalid receipt cannot grant renewed source-write authority.
        page.locator('[data-fake-monaco-path="src/studio.html"]').fill(
             page.locator('[data-fake-monaco-path="src/studio.html"]').input_value()+'<!-- draft -->')
        page.evaluate('window.__editorFixture.corruptReceipt=true')
        writes_before=len(calls(page,'/editor/project/file/save'))
        page.locator('[data-mcel-document-save]').click()
        page.wait_for_function('(n)=>window.__editorCalls.filter(x=>x.path.endsWith("/editor/project/file/save")).length>n',arg=writes_before)
        page.wait_for_function('window.__editorPort.documentWriteStatus("src/studio.html")==="reopen-required"')
        malformed=receipt(page,'save-source')
        case(cases,'save-receipt','malformed-server-receipt-refused','failed',malformed['disposition'],malformed['disposition']=='failed')
        case(cases,'authorization','bad-receipt-revokes-write','reopen-required',
             page.evaluate('window.__editorPort.documentWriteStatus("src/studio.html")'),
             page.evaluate('window.__editorPort.documentWriteStatus("src/studio.html")')=='reopen-required')
        case(cases,'authorization','malformed-save-changed-no-source',False,
             page.evaluate('window.__editorFiles["src/studio.html"].includes("draft")'),
             not page.evaluate('window.__editorFiles["src/studio.html"].includes("draft")'))
        case(cases,'authorization','no-host-writes-from-docs-and-scm',2,
             len(page.evaluate('window.__editorEffects.filter(x=>x.kind==="save"||x.kind==="review")')),
             len(page.evaluate('window.__editorEffects.filter(x=>x.kind==="save"||x.kind==="review")'))==2)
        return {'status':'PASS' if all(c['status']=='PASS' for c in cases) and not errors else 'MISMATCH',
                'cases':cases,'pageErrors':errors,'backend':backend,
                'httpTransport':'in-memory-fake-only; no real source writes or Aider job',
                'monacoTransport':'bounded text-model harness; native Monaco unverified',
                'observedIntents':page.evaluate('window.__mcelWorkbench.app.receipts().map(x=>x.intentId).filter(Boolean)')}
      except Exception as exc:
        return {'status':'INCOMPLETE','cases':cases,'pageErrors':errors,'backend':backend,'error':str(exc)}
      finally:browser.close()

# These are behavioral witnesses, not mere source-presence checks. They are
# compulsory even if somebody accidentally deletes a browser case while
# changing the Frob. Extra cases are welcome, but omissions fail closed.
MANDATORY_BROWSER_WITNESSES=frozenset({
    'semantic-mount', 'root-discovery', 'directory-scope', 'exact-files',
    'observed-file-read', 'source-mount', 'two-open-tabs',
    'dirty-a-survives-b', 'cancelled-save-has-no-effect',
    'save-exact-target', 'save-content-binding', 'other-dirty-tab-not-written',
    'conflict-on-outside-write', 'stale-save-absent', 'close-only-target',
    'forged-file-refusal', 'prepare-does-not-mutate', 'review-apply-only-a',
    'aider-dry-run-no-effects', 'accepted-not-completed',
    'pending-write-revoked', 'completion-requires-reopen',
    'manifest-scoped-document', 'returned-html-is-inert',
    'component-contract-inspected', 'route-contract-inspected',
    'invalid-source-serialization-refused', 'valid-clean-serialization',
    'valid-source-kernel-mount', 'kernel-commit', 'kernel-replay',
    'proof-selection-observed', 'sandboxed-pixel-script',
    'malformed-server-receipt-refused', 'bad-receipt-revokes-write',
    'malformed-save-changed-no-source', 'no-host-writes-from-docs-and-scm',
})

def run(repo:Path=ROOT)->dict:
    report={'schema':'mcel.code-editor-functional-parity-frob.v1','scope':'isolated-browser-source-contracts-no-production-promotion',
            'candidateRoute':'/applications/mcel-semantic-code-editor','canonicalRoute':'/applications/code-editor',
            'liveWindowsParity':'UNVERIFIED','nativeMonaco':'UNVERIFIED','realAider':'UNVERIFIED',
            'designApproval':'UNVERIFIED','promotionAuthorized':False,
            'evidenceLimits':['Incumbent checked-in source is the inventory oracle, not simultaneously executed in this browser run',
              'All external IO is simulated; no actual source mutation or Aider service',
              'Monaco model is substituted by bounded test textarea',
              'In-memory fake SHA-256 exercises receipt correlation, not cryptographic digest correctness',
              'Live Windows file write, SCM persistence, workspace authority, provider, and human design approval are unverified']}
    report.update(source_inventory(repo))
    try:browser=browser_acceptance(repo)
    except (ImportError,ModuleNotFoundError) as exc:
        browser={'status':'INCOMPLETE','cases':[],'error':str(exc)}
    except Exception as exc:
        browser={'status':'INCOMPLETE','cases':[],'error':str(exc)}
    report['browser']=browser
    report['intentCoverage']={'observed':sorted(set(browser.get('observedIntents',[]))),
        'sourceOnly':sorted(set(report['declaredIntentIds'])-set(browser.get('observedIntents',[])))}
    observed_cases={c['id'] for c in browser.get('cases',[]) if c.get('status')=='PASS'}
    report['missingMandatoryScenarios']=sorted(MANDATORY_BROWSER_WITNESSES-observed_cases)
    for c in browser.get('cases',[]):
        if c['feature'] in report['features']:report['features'][c['feature']]['browserScenarios'].append(c['id'])
    for feat in report['features'].values():
        if feat['status']=='SOURCE_COVERED' and browser['status']=='PASS' and feat['browserScenarios'] and all(c['status']=='PASS' for c in browser['cases'] if c['feature']==next(k for k,v in report['features'].items() if v is feat)):
            feat['status']='PASS_ISOLATED'
        elif feat['status']=='SOURCE_COVERED':feat['status']='UNVERIFIED'
    statuses=[v['status'] for v in report['features'].values()]
    report['status']='PASS_ISOLATED' if browser['status']=='PASS' and all(x=='PASS_ISOLATED' for x in statuses) and not report['intentCoverage']['sourceOnly'] and not report['missingMandatoryScenarios'] else 'INCOMPLETE'
    report['summary']={'featureCount':len(FEATURES),'browserCases':len(browser.get('cases',[])),
       'browserMismatches':sum(c['status']!='PASS' for c in browser.get('cases',[])),
       'byStatus':{kind:statuses.count(kind) for kind in ['PASS_ISOLATED','UNVERIFIED','MISSING','BROKEN']},
       'browserIntents':len(report['intentCoverage']['observed']), 'sourceOnlyIntents':len(report['intentCoverage']['sourceOnly'])}
    return report

def main(argv:list[str]|None=None)->int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--report',type=Path,default=REPORT)
    ap.add_argument('--repo',type=Path,default=ROOT)
    opts=ap.parse_args(argv)
    result=run(opts.repo)
    target=opts.report if opts.report.is_absolute() else opts.repo/opts.report
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf8')
    print('MCEL_CODE_EDITOR_FUNCTIONAL_PARITY '+result['status'])
    for key,value in result['features'].items():print('  '+key+': '+value['status'])
    print('  browser: '+str(result['summary']['browserCases'])+' cases; mismatches: '+str(result['summary']['browserMismatches']))
    if result['browser'].get('error'):print('  error: '+result['browser']['error'])
    print('  Windows source writes, native Monaco and Aider: UNVERIFIED; no production mutation or promotion')
    print('  report: '+str(target))
    return 0 if result['status']=='PASS_ISOLATED' else 2

if __name__=='__main__':raise SystemExit(main())
