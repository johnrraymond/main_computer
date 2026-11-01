"use strict";
/* Deterministic contract-level preservation gate; no live source, process, or server mutation. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {webcrypto}=require('node:crypto');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'main_computer/web/applications/scripts/mcel-semantic-read-ports.js'),'utf8');
const req=(app,id,cap,operation,method,args,risk='external-read')=>({appId:`semantic-${app}`,
  intentId:`intent:${id}`,effectId:`effect:${id}.request`,capabilityId:`capability:${cap}`,
  operation,runtimeMethod:method,risk,arguments:args});
const port=(app,fetch,navigate)=>{
 const ctx={crypto:webcrypto,TextEncoder,fetch:async(url,options)=>({ok:true,status:200,
   json:async()=>fetch(url,JSON.parse(options.body||"{}"))})};
 ctx.globalThis=ctx;vm.runInNewContext(source,ctx);
 return ctx.McelSemanticReadPorts.create({appId:`semantic-${app}`,navigate});
};
const sha=async text=>Array.from(new Uint8Array(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(text))))
  .map(v=>v.toString(16).padStart(2,'0')).join('');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
async function editorRace(){
 const pending=new Map(),writes=[];
 const p=port('code-editor',async(url,a)=>{
   if(url.endsWith('/files'))return {entries:['A.js','B.js'].map(v=>({path:v,name:v,kind:'file'}))};
   if(url.endsWith('/read'))return new Promise(resolve=>pending.set(a.files[0],resolve));
   if(url.endsWith('/save')){
     writes.push(a);
     const before=await sha(a.path==='A.js'?'A':'B');
     const after=await sha(a.replacement_text);
     assert.equal(a.expected_before_sha256,before);
     return {schema:'mcel-code-editor-file-save-v1',state:'applied',status:'pass',
       handle:'f'.repeat(32),savedPath:a.path,changedFiles:[a.path],
       receipt:{ok:true,rollback_required:false,transaction_id:'a'.repeat(24),
         files:[{path:a.path,operation:'modify',before_sha256:before,after_sha256:after,written_sha256:after}]}};
   }
   throw Error(url);
 });
 await p.invoke(req('code-editor','list-files','source-reader','list','listSourceFiles',{}));
 const open=id=>req('code-editor','open-source','source-reader','read','readSource',{path:id});
 const save=id=>req('code-editor','save-source','source-writer','save','saveSource',{path:id,text:id[0]+' edited'},'source-write');
 const ra=p.invoke(open('A.js')),rb=p.invoke(open('B.js'));
 await tick();pending.get('B.js')({files:[{path:'B.js',content:'B'}]});await rb;
 assert.equal(await p.authorize(save('B.js')),true);
 pending.get('A.js')({files:[{path:'A.js',content:'A'}]});await ra;
 assert.equal(await p.authorize(save('B.js')),true,'stale A cannot revoke B');
 assert.equal(await p.authorize(save('A.js')),true,'A has independent authority');
 await p.invoke(save('B.js'));await p.invoke(save('A.js'));
 assert.deepEqual(writes.map(v=>v.path),['B.js','A.js']);
 p.closeDocument('B.js');
 assert.equal(await p.authorize(save('A.js')),true,'closing B must not close A');
 assert.equal(await p.authorize(save('B.js')),false);
 return 'editor-per-document-authority';
}
async function fileTree(){
 const navigated=[];
 const listing={'':['alpha','beta','readme.bin'].map(name=>({relative_path:name,name,
   kind:name==='readme.bin'?'file':'directory',bytes:1024,mtime:50,category:'code',
   suggested_app:name==='readme.bin'?'code-editor':null,path_display:'workspace:/'+name})),
  alpha:[{relative_path:'alpha/inner',name:'inner',kind:'directory'}],
  'alpha/inner':[] ,beta:[]};
 const p=port('file-manager',(url,a)=>{
   if(url.endsWith('/roots'))return {roots:[{id:'workspace',label:'Workspace'},{id:'other',label:'Other'}]};
   if(url.endsWith('/list'))return {root_id:a.root_id,relative_path:a.relative_path,entries:listing[a.relative_path]||[]};
   if(url.endsWith('/read'))return {readable:false,reason:'binary',entry:listing[''][2]};
   throw Error(url);
 },route=>{navigated.push(route);return true;});
 const call=(id,op,method,a)=>req('file-manager',id,'filesystem-read',op,method,a);
 const enter=path=>call('enter-directory','enter','openDirectory',{rootId:'workspace',relativePath:path});
 await p.invoke(call('load-roots','roots','listRoots',{}));
 const base=await p.invoke(call('browse','list','listDirectory',{rootId:'workspace'}));
 assert.equal(base[2].bytes,1024);assert.equal(base[2].pathDisplay,'workspace:/readme.bin');
 assert.equal(base[2].suggestedApp,'code-editor');
 assert.equal(await p.authorize(enter('alpha/inner')),false,'cannot guess a nested directory');
 await p.invoke(enter('alpha'));await p.invoke(enter('alpha/inner'));
 assert.equal(await p.authorize(enter('beta')),true,'retained sibling must remain authorized');
 await p.invoke(enter('beta'));
 assert.equal(await p.authorize(enter('../private')),false);
 await p.invoke(enter('')); // reset current file listing to root
 const metadata=await p.invoke(call('preview-entry','preview','readPreview',{rootId:'workspace',relativePath:'readme.bin'}));
 assert.equal(metadata.readable,false);assert.equal(metadata.reason,'binary');assert.equal(metadata.entry.bytes,1024);
 const opening=req('file-manager','open-with','application-navigation','open','openSuggestedApp',
   {rootId:'workspace',relativePath:'readme.bin'});
 assert.equal(await p.authorize(opening),true);
 await p.invoke(opening);assert.deepEqual(navigated,['/applications/code-editor']);
 assert.equal(await p.authorize(req('file-manager','open-with','application-navigation','open','openSuggestedApp',
   {rootId:'workspace',relativePath:'../private'})),false);
 await p.invoke(call('browse','list','listDirectory',{rootId:'other'}));
 assert.equal(await p.authorize(enter('beta')),false,'root change revokes previous evidence');
 return 'file-tree-and-typed-preview';
}
async function taskControls(){
 const calls=[];
 const p=port('task-manager',(url,a)=>{
   calls.push({url,a});
   if(url.endsWith('/overview'))return {processes:[{pid:10,name:'safe',created_at:'2026-10-31T10:00:00+00:00',status:'running',memory_human:'3 MB'},
     {pid:11,name:'server',created_at:'2026-10-31T10:01:00+00:00',is_current_process:true}],
     hardware:{summary:'CPU 25%',cpu:{available:true,overall_percent:25,per_core:[{index:0,label:'CPU 0',percent:20}]},
       gpu:{available:true,overall_percent:33,devices:[{index:0,name:'GPU',utilization_percent:33,memory_used_mb:10}]}}};
   if(url.endsWith('/action'))return {ok:true,action:a.action,pid:a.pid,
     verified_created_at:a.expected_created_at,message:'mocked only'};
   if(url.endsWith('/schedules'))return {schedules:[{id:'s1',action:'server_status'}]};
   if(url.endsWith('/ai'))return {content:'mocked AI'};
   throw Error(url);
 });
 const refresh=req('task-manager','refresh','monitor','observe','observeProcesses',{query:'worker'});
 const rows=await p.invoke(refresh);
 assert.equal(rows[0].createdAt,'2026-10-31T10:00:00+00:00');
 const bad=req('task-manager','kill-process','task-control','kill','killObservedProcess',
   {selected:'10:wrong'},'consequential');
 assert.equal(await p.authorize(bad),false);
 const own=req('task-manager','kill-process','task-control','kill','killObservedProcess',
   {selected:rows[1].processKey},'consequential');
 assert.equal(await p.authorize(own),false,'refuse current server process');
 const terminate=req('task-manager','terminate-process','task-control','terminate','terminateObservedProcess',
   {selected:rows[0].processKey},'consequential');
 assert.equal(await p.authorize(terminate),true);
 await p.invoke(terminate);
 const sent=calls.find(c=>c.url.endsWith('/action'))?.a;
 assert.equal(sent.expected_created_at,rows[0].createdAt);
 assert.equal(sent.mcel_semantic_guarded,true);
 assert.equal(sent.pid,10);
 const hw=await p.invoke(req('task-manager','refresh-hardware','monitor','hardware','observeHardware',{query:''}));
 assert.equal(hw.cpuCores[0].percent,20);assert.equal(hw.gpuDevices[0].utilization,33);
 await p.invoke(req('task-manager','task-ai','monitor','ai','getAiBrief',
   {instruction:'Analyze',query:'',view:'all-processes'}));
 const ai=calls.find(c=>c.url.endsWith('/ai'))?.a;
 assert.equal(ai.include_all,true);assert.equal(ai.query,'worker');
 return 'task-generation-risk-and-ai-context';
}
async function aiderControls(){
 const calls=[],content='function one(){return 1;}';
 let stale=false;
 const p=port('code-editor',async(url,a)=>{
   calls.push({url,a});
   if(url.endsWith('/files'))return {entries:[{path:'one.js',name:'one.js',kind:'file'}]};
   if(url.endsWith('/read'))return {files:a.files.map(file=>({path:file,content:stale?'altered':content}))};
   if(url.endsWith('/context'))return {ok:true,active:{id:'a',repo_dir:'.'},
     archives:[{id:'old',repo_dir:'.'}],activities:[]};
   if(url.endsWith('/jobs'))return {ok:true,activities:[{id:'job1',status:'running'}]};
   if(url.endsWith('/run'))return {ok:true,accepted:true,job:{id:'job1'}};
   if(url.endsWith('/reset'))return {ok:true,active:{id:'b'},archives:[]};
   if(url.endsWith('/load'))return {ok:true,active:{id:'c'},archives:[]};
   throw Error(url);
 });
 const r=(id,cap,op,method,a,risk='external-read')=>req('code-editor',id,cap,op,method,a,risk);
 await p.invoke(r('list-files','source-reader','list','listSourceFiles',{}));
 const read=r('open-source','source-reader','read','readSource',{path:'one.js'});
 await p.invoke(read);
 const context=r('aider-context','aider-context','context','readContext',{});
 const status=JSON.parse(await p.invoke(context));
 assert.equal(status.archives[0].id,'old');
 const jobs=JSON.parse(await p.invoke(r('aider-jobs','aider-context','jobs','readJobs',{})));
 assert.equal(jobs.activities[0].status,'running');
 const badArchive=r('load-aider-archive','aider-control','load','loadAiderArchive',
   {archiveId:'not-observed'},'consequential');
 assert.equal(await p.authorize(badArchive),false);
 const run=r('run-aider','aider-control','run','runAider',
   {path:'one.js',instruction:'Improve',files:''},'consequential');
 stale=true;
 await assert.rejects(()=>p.invoke(run),/aider-source-stale/);
 assert.equal(calls.filter(c=>c.url.endsWith('/run')).length,0);
 stale=false;
 const accepted=await p.invoke(run);
 assert.match(accepted,/accepted \(not completed\): job1/);
 assert.equal(await p.authorize(run),false,'new source read required after Aider acceptance');
 await p.invoke(read);
 assert.equal(await p.authorize(run),true);
 return 'aider-governed-session-and-job';
}
(async()=>{
 const results=[];for(const fn of [editorRace,fileTree,taskControls,aiderControls])results.push(await fn());
 console.log(JSON.stringify({schema:'mcel.migration-preservation-gate.v1',ok:true,checks:results},null,2));
})().catch(error=>{console.error(error.stack||String(error));process.exitCode=1;});
