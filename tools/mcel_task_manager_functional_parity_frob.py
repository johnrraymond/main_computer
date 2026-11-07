"""Task Manager incumbent-contract vs paired-semantic functional acceptance.

The incumbent's checked-in UI/handler contracts are the independent oracle.
The paired semantic UI runs in a real Chromium browser against an entirely
in-memory, request-logging transport. Consequential requests are *simulated*,
never sent to a real process, controller, schedule store, or host. Reports do
NOT certify real Windows operational parity or canonical promotion.

Usage: python -u -m tools.mcel_task_manager_functional_parity_frob
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]
REPORT = Path('runtime/reports/mcel-task-manager-functional-parity/report.json')
INCUMBENT_HTML = Path('main_computer/web/applications/apps/task-manager.html')
INCUMBENT_JS = Path('main_computer/web/applications/scripts/task-manager.js')
INCUMBENT_OPERATIONS = Path('main_computer/web/applications/scripts/terminal.js')
CANDIDATE_JS = Path('tools/fixtures/mcel_semantic_other_apps/task-manager/application.js')
PORT_JS = Path('main_computer/web/applications/scripts/mcel-semantic-read-ports.js')
RUNTIME_JS = Path('main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js')

@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    markup: tuple[str, ...]
    incumbent: tuple[str, ...]
    intents: tuple[str, ...]
    next_evidence: str = 'Compare real Windows-host process/telemetry snapshots and observed target identities'

FEATURES = (
    Feature('monitor-server','Server process monitoring',('task-process-table',),('runTaskManagerRefresh','include_all: false'),('intent:refresh',)),
    Feature('monitor-all','All-process monitoring',('task-all-process-table',),('include_all: true',),('intent:refresh-all',)),
    Feature('connections','Network connections',('task-connection-table',),('renderTaskConnections',),('intent:refresh-connections',)),
    Feature('hardware','CPU/GPU telemetry',('task-hardware-table',),('renderTaskHardware',),('intent:refresh-hardware',)),
    Feature('monitor-options','Search, limit, connection toggle',('task-query','task-limit','task-include-connections'),('requestBase',),('intent:refresh',)),
    Feature('notebook','Four selectable monitoring views',('task-notebook-tabs',),('setTaskNotebookTab',),('intent:switch-view',)),
    Feature('auto-refresh','Polling and pause/resume',('task-auto-refresh',),('scheduleTaskManagerAutoRefresh',),('intent:refresh',)),
    Feature('selection','Observed process identity and generation',('task-process-table',),('expected_created_at',),('intent:choose-process','intent:choose-all-process')),
    Feature('terminate','Confirmed termination, correct PID and generation',('task-process-table',),('terminate_pid',),('intent:terminate-process',),
            'Verify a test-owned, disposable process on the actual Windows controller'),
    Feature('kill','Confirmed forced termination, correct PID and generation',('task-all-process-table',),('kill_pid',),('intent:kill-process',),
            'Verify a test-owned, disposable process on the actual Windows controller'),
    Feature('server-status','Read-only server status',('task-manager-server',),('server_status',),('intent:server-status',)),
    Feature('server-lifecycle','Confirmed start, restart and shutdown',('task-server-start','task-server-restart','task-server-shutdown'),('server_start','server_restart','server_shutdown'),
            ('intent:start-server','intent:restart-server','intent:shutdown-server'),
            'Use a disposable controller and verify server identity, post-state and deferred receipts'),
    Feature('schedule-list','Schedule inventory',('task-schedule-list',),('refreshTaskManager',),('intent:list-schedules',)),
    Feature('schedule-create','Create governed schedule',('task-schedule-create',),('async function createTaskSchedule(',),('intent:create-schedule',)),
    Feature('schedule-delete','Delete selected schedule with revision',('task-schedule-list',),('async function deleteTaskSchedule(',),('intent:delete-schedule',)),
    Feature('schedule-run','Run recorded schedule',('task-schedule-list',),('data-task-run',),('intent:run-schedule',)),
    Feature('ai','Explicit AI operational brief',('task-ai-prompt','task-ai-output'),('async function askTaskManagerAi(',),('intent:task-ai',),
            'Verify real provider and exact snapshot context; generated text must not authorize effects'),
    Feature('authority','Local CSRF and explicit mutation confirmation',('task-manager-app',),('async function runTaskAction(',),('intent:terminate-process','intent:restart-server'),
            'Verify independent authority session and refusal of unauthenticated remote calls'),
    Feature('receipts','Effect receipts bind action and target',('task-manager-status',),('taskManagerRequest',),('intent:terminate-process','intent:run-schedule')),
)


def _text(repo:Path, relative:Path)->str:
    return (repo / relative).read_text(encoding='utf-8')


def source_inventory(repo:Path, *, overrides:dict[str,str]|None=None)->dict[str,Any]:
    source={str(path):_text(repo,path) for path in (INCUMBENT_HTML,INCUMBENT_JS,INCUMBENT_OPERATIONS,CANDIDATE_JS,PORT_JS,RUNTIME_JS)}
    source.update(overrides or {})
    ir=compile_semantic_app('task-manager',repo)
    ids={i['id'] for i in ir.get('intents',[])}
    html=source[str(INCUMBENT_HTML)]
    incumbent=source[str(INCUMBENT_JS)]+'\n'+source[str(INCUMBENT_OPERATIONS)]
    features={}
    for feature in FEATURES:
        missing_dom=[key for key in feature.markup if not re.search(r'\bid\s*=\s*["\']'+re.escape(key)+r'["\']',html)]
        missing_legacy=[key for key in feature.incumbent if key not in incumbent]
        missing_semantic=[key for key in feature.intents if key not in ids]
        features[feature.key]={'label':feature.label,
            'status':'UNVERIFIED' if missing_dom or missing_legacy else 'MISSING' if missing_semantic else 'SOURCE_COVERED',
            'incumbent':{'missingDomIds':missing_dom,'missingHandlerSignatures':missing_legacy},
            'candidate':{'missingIntentIds':missing_semantic},
            'requiredNextEvidence':feature.next_evidence,'browserScenarios':[]}
    return {'features':features,'declaredIntentIds':sorted(ids),'sourceEvidence':{
        'sha256':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in source.items()},
        'candidateIrFingerprint':hashlib.sha256(json.dumps(ir,sort_keys=True).encode()).hexdigest(),
        'semanticIntentCount':len(ids),
        'layoutFree':not bool(ir.get('layouts')),
        'capabilityRisks':sorted({c['risk'] for c in ir.get('capabilities',[])})}}

# The in-memory transport deliberately implements a contract oracle independent
# of the candidate. All requests, including attempted consequential actions, are
# observed. Nothing reaches HTTP or an OS process/service.
FIXTURE_JS=r'''() => {
 window.__taskRequests=[];window.__taskEffects=[];window.__taskRefusals=[];
 window.__taskIntervals=[];window.setInterval=(fn,ms)=>{window.__taskIntervals.push({fn,ms});return 913;};
 window.clearInterval=()=>{};
 window.__taskCase={schedules:[{id:'sc-1',action:'server_restart',run_at:'2026-11-07T12:00:00+00:00',note:'original',revision:'r1'}],
  observedBirth:'2026-11-05T12:00:00.000+00:00',pid:43121,allowMutations:true};
 window.confirm=()=>window.__taskCase.allowMutations;
 window.fetch=async (path,options={})=>{
   const uri=String(path), body=JSON.parse(options.body||'{}');
   const type=String(options.method||'GET').toUpperCase();
   window.__taskRequests.push({path:uri,method:type,body,headers:options.headers||{}});
   const result=(payload,code=200)=>({ok:code<400,status:code,json:async()=>payload});
   if(uri==='/api/applications/task/authority/session' && type==='GET')return result({ok:true,csrf:'isolated-local-token'});
   if(type!=='POST' || !uri.startsWith('/api/applications/task/')){
     window.__taskRefusals.push({uri,reason:'unknown-request'});return result({ok:false,error:'not-isolated-route'},404);
   }
   const pid=window.__taskCase.pid,birth=window.__taskCase.observedBirth;
   if(uri.endsWith('/overview'))return result({ok:true,
     server:{running:true,pid:pid,port:8765,heartbeat_running:true},
     overview:{main_computer_process_count:1,connection_count:1,schedule_count:window.__taskCase.schedules.length},
     processes:[{pid,name:'disposable-task-process',status:'running',created_at:birth,memory_human:'2 MB',memory_rss:2097152,
       memory_availability:'present',status_availability:'present',is_main_computer:true,is_current_process:false}],
     connections:[{pid,process_name:'disposable-task-process',status:'ESTABLISHED',local:'127.0.0.1:10001',remote:'127.0.0.1:8765'}],
     hardware:{summary:'CPU 12%, GPU 3%',updated_at:'2026-11-05T12:00:00Z',
       cpu:{available:true,overall_percent:12,per_core:[{index:0,label:'CPU0',percent:12}]},
       gpu:window.__taskCase.gpuMissing ? {available:false,overall_percent:null,devices:[]} :
         {available:true,overall_percent:3,devices:[{index:0,name:'test-gpu',utilization_percent:3,memory_used_mb:10,memory_total_mb:100,temperature_c:42}]}}});
   if(uri.endsWith('/schedules'))return result({ok:true,schedules:window.__taskCase.schedules});
   if(uri.endsWith('/ai'))return result({ok:true,content:'Isolated analysis for '+body.instruction});
   const mutating=['/action','/schedule/create','/schedule/delete','/schedule/run'].some(s=>uri.endsWith(s));
   if(mutating && (!options.headers || options.headers['X-MCEL-Task-CSRF']!=='isolated-local-token'))
     return result({ok:false,error:'csrf-required'},403);
   if(uri.endsWith('/action')){
     if(body.confirm!==true || !window.__taskCase.allowMutations)return result({ok:false,error:'confirmation-required'},403);
     if(['terminate_pid','kill_pid'].includes(body.action)){
       if(body.pid!==pid || body.expected_created_at!==birth)return result({ok:false,error:'generation-changed'},409);
     }
     if(!['terminate_pid','kill_pid','server_start','server_restart','server_shutdown'].includes(body.action))
       return result({ok:false,error:'unsupported-action'},400);
     window.__taskEffects.push({kind:'action',action:body.action,pid:body.pid??null});
     return result({ok:true,action:body.action,pid:body.pid,verified_created_at:birth,
       message:'simulated only',deferred:['server_restart','server_shutdown'].includes(body.action)});
   }
   if(uri.endsWith('/schedule/create')){
     if(!['server_status','server_start','server_restart','server_shutdown'].includes(body.action))
       return result({ok:false,error:'not-allowed'},400);
     const schedule={id:'sc-'+(window.__taskCase.schedules.length+1),action:body.action,
       run_at:body.run_at,note:body.note||'',revision:'r'+(window.__taskCase.schedules.length+1)};
     window.__taskCase.schedules.push(schedule);window.__taskEffects.push({kind:'schedule-create',id:schedule.id});
     return result({ok:true,schedule});
   }
   if(uri.endsWith('/schedule/delete')){
     const old=window.__taskCase.schedules.find(s=>s.id===body.schedule_id);
     if(!old || old.revision!==body.expected_revision)return result({ok:false,error:'revision-changed'},409);
     window.__taskCase.schedules=window.__taskCase.schedules.filter(s=>s.id!==old.id);
     window.__taskEffects.push({kind:'schedule-delete',id:old.id});return result({ok:true,schedules:window.__taskCase.schedules});
   }
   if(uri.endsWith('/schedule/run')){
     const old=window.__taskCase.schedules.find(s=>s.id===body.schedule_id);
     if(!old || old.revision!==body.expected_revision || old.action!==body.expected_action ||
       body.confirm!==true || !String(body.request_id||'').startsWith('manual_'))
       return result({ok:false,error:'schedule-generation-changed'},409);
     window.__taskEffects.push({kind:'schedule-run',id:old.id,action:old.action});
     return result({ok:true,action:old.action,schedule_revision:old.revision,manual_invocation:true,outcome:'accepted-deferred'});
   }
   window.__taskRefusals.push({uri,reason:'unhandled-endpoint'});
   return result({ok:false,error:'unhandled-endpoint'},404);
 };
}'''


def _scenario(cases:list, feature:str, id:str, expected:Any, observed:Any, match:bool, note:str=''):
    cases.append({'feature':feature,'id':id,'expected':expected,'observed':observed,
                  'status':'PASS' if match else 'MISMATCH','note':note or None,'mockedHttp':True})


def _requests(page, suffix:str)->list[dict]:
    return page.evaluate('(s)=>window.__taskRequests.filter(r=>r.path.endsWith(s))',suffix)


def _show_workflow(page, locator):
    # The verified workspace composer may place supporting tools in exclusive
    # tabs.  Navigate to the owner without changing the semantic operation,
    # target, authorization, or confirmation under test.
    panel=locator.evaluate('el=>el.closest("[data-mcel-tool-panel]")?.id||null')
    if panel:
        page.locator(f'[data-mcel-tool-tabs] [aria-controls="{panel}"]').click()


def _click(page, intent:str):
    target=page.locator(f'button[data-intent="intent:{intent}"]')
    _show_workflow(page,target)
    target.click()


def _await_receipt(page,intent:str,disposition:str='completed'):
    page.wait_for_function('''({i,d})=>window.__mcelWorkbench?.app?.receipts().some(x=>x.intentId===i && x.disposition===d)''',
                           arg={'i':'intent:'+intent,'d':disposition},timeout=6500)


def browser_comparison(repo:Path)->dict:
    from playwright.sync_api import sync_playwright
    cases=[]; errors=[]; backend='unavailable'
    with sync_playwright() as pw:
      browser,backend=launch_semantic_browser(pw)
      page=browser.new_page(viewport={'width':1100,'height':900})
      page.set_default_timeout(7000)
      page.on('pageerror',lambda e:errors.append(str(e)))
      page.on('dialog',lambda dialog:dialog.accept() if page.evaluate('window.__taskCase.allowMutations') else dialog.dismiss())
      try:
        page.evaluate(FIXTURE_JS)
        page.set_content(render_semantic_page('task-manager',repo))
        status=page.locator('#mcel-workbench-status').inner_text()
        _scenario(cases,'notebook','semantic-candidate-mount','Semantic candidate ready',status,status=='Semantic candidate ready')
        if status!='Semantic candidate ready':return {'status':'INCOMPLETE','cases':cases,'pageErrors':errors,'backend':backend}
        _scenario(cases,'authority','no-implicit-mutation',0,len(page.evaluate('window.__taskEffects')),
                  not page.evaluate('window.__taskEffects.length'))
        _scenario(cases,'notebook','four-views',4,page.locator('[data-mcel-mode-tabs] button').count(),
                  page.locator('[data-mcel-mode-tabs] button').count()==4)
        page.wait_for_timeout(300)
        startup_calls=len(_requests(page,'/overview'))
        _scenario(cases,'monitor-server','automatic-first-snapshot',True,startup_calls>0,
                  startup_calls>0,'Opening the Task Manager must load a first monitoring snapshot without a click or five-second delay.')
        # Exercise the *real* scheduled callback under a virtual clock. The
        # browser is never left idle for five seconds and no effect is allowed.
        intervals=page.evaluate('window.__taskIntervals.map(v=>v.ms)')
        _scenario(cases,'auto-refresh','declared-five-second-poll',[5000],intervals,5000 in intervals)
        if 5000 in intervals:
            initial=len(_requests(page,'/overview'))
            page.evaluate('async()=>await window.__taskIntervals.find(v=>v.ms===5000).fn()')
            polled=len(_requests(page,'/overview'))
            before=page.evaluate('window.__taskEffects.length')
            toggle=page.locator('[data-mcel-refresh-toggle]')
            toggle.click()
            page.evaluate('async()=>await window.__taskIntervals.find(v=>v.ms===5000).fn()')
            paused=len(_requests(page,'/overview'))
            toggle.click()
            page.evaluate('async()=>await window.__taskIntervals.find(v=>v.ms===5000).fn()')
            resumed=len(_requests(page,'/overview'))
            _scenario(cases,'auto-refresh','read-only-poll-pause-resume',
                      {'polled':True,'paused':True,'resumed':True,'effects':0},
                      {'polled':polled>initial,'paused':paused==polled,'resumed':resumed>paused,
                       'effects':page.evaluate('window.__taskEffects.length')-before},
                      polled>initial and paused==polled and resumed>paused and
                      page.evaluate('window.__taskEffects.length')==before)
        # Monitoring forms must use the same incumbent contract, with scoped options.
        form=page.locator('form[data-intent="intent:refresh"]')
        form.locator('[name=query]').fill('disposable')
        form.locator('[name=limit]').fill('17')
        form.locator('[name=includeConnections]').fill('false')
        form.locator('button[type=submit]').click()
        _await_receipt(page,'refresh')
        request=_requests(page,'/overview')[-1]['body']
        expected={'query':'disposable','limit':17,'include_all':False,'include_connections':False}
        _scenario(cases,'monitor-options','filtered-monitor-request',expected,
                  {k:request.get(k) for k in expected},all(request.get(k)==v for k,v in expected.items()))
        key='43121:2026-11-05T12:00:00.000+00:00'
        row=page.locator(f'[data-record-key="{key}"]:visible').first
        row.wait_for()
        _scenario(cases,'monitor-server','observed-process-generation',key,row.get_attribute('data-record-key'),
                  row.get_attribute('data-record-key')==key)
        # Security: forging a selection cannot authorize a PID mutation.
        forged=page.evaluate('''()=>{const p=McelSemanticReadPorts.create({appId:'semantic-task-manager'});
          return p.authorize({appId:'semantic-task-manager',intentId:'intent:terminate-process',
            effectId:'effect:terminate-process.request',capabilityId:'capability:task-control',
            operation:'terminate',runtimeMethod:'terminateObservedProcess',risk:'consequential',
            arguments:{selected:'43121:forged'}})}''')
        _scenario(cases,'authority','forged-generation-rejected',False,forged,forged is False)
        # Selected observed identity carries PID AND birth time. No PID-only effects.
        row.locator('button[data-select-key]').click()
        selected=page.evaluate('window.__mcelWorkbench.app.readState("state:selected")')
        _scenario(cases,'selection','observed-selection',key,selected,selected==key)
        _click(page,'terminate-process'); _await_receipt(page,'terminate-process','command-returned')
        action=_requests(page,'/action')[-1]['body']
        expected={'action':'terminate_pid','pid':43121,'expected_created_at':'2026-11-05T12:00:00.000+00:00','confirm':True,'mcel_semantic_guarded':True}
        _scenario(cases,'terminate','generation-bound-terminate',expected,
                  {k:action.get(k) for k in expected},all(action.get(k)==v for k,v in expected.items()) and bool(action.get('request_id')))
        # Re-use of consumed evidence must be refused without making another request.
        old=len(_requests(page,'/action'))
        _click(page,'terminate-process')
        page.wait_for_timeout(100)
        _scenario(cases,'authority','consumed-target-refuses-repeat',old,len(_requests(page,'/action')),
                  len(_requests(page,'/action'))==old)
        for mode,intent,feature in (('all-processes','refresh-all','monitor-all'),('connections','refresh-connections','connections'),('cpu-gpu','refresh-hardware','hardware')):
            page.locator(f'[data-mcel-mode-tabs] button[data-value="{mode}"]').click()
            page.locator(f'form[data-intent="intent:{intent}"] button[type=submit]').click()
            _await_receipt(page,intent)
            _scenario(cases,feature,'view-'+mode,mode,page.evaluate('window.__mcelWorkbench.app.readState("state:view")'),
                      page.evaluate('window.__mcelWorkbench.app.readState("state:view")')==mode)
        _scenario(cases,'connections','connection-row-observed',1,
                  page.evaluate('window.__mcelWorkbench.app.readState("state:connections").length'),
                  page.evaluate('window.__mcelWorkbench.app.readState("state:connections").length')==1)
        hw=page.evaluate('window.__mcelWorkbench.app.readState("state:hardware")')
        _scenario(cases,'hardware','cpu-gpu-telemetry',{'cpuPercent':12,'gpuPercent':3},
                  {'cpuPercent':hw.get('cpuPercent'),'gpuPercent':hw.get('gpuPercent')},
                  hw.get('cpuPercent')==12 and hw.get('gpuPercent')==3)
        # The data existed in state but was previously an unreadable JSON blob
        # inside a 170px results column. Require readable gauges and an actual
        # primary workspace; otherwise functional parity is not sufficient.
        telemetry=page.evaluate('''() => {
          const node=document.querySelector('[data-mcel-node="surface-node:semantic-task-manager.hardware-overview"]');
          const region=node?.closest('[data-mcel-region]');
          return { values:Object.fromEntries([...document.querySelectorAll('[data-mcel-metric]')]
            .map(card=>[card.dataset.mcelMetric,card.querySelector('[data-mcel-metric-value]')?.textContent])),
            meters:document.querySelectorAll('[data-mcel-metric-meter]').length,
            rawJson:!!node?.querySelector('output')?.textContent?.trim().startsWith('{'),
            regionPurpose:region?.dataset.mcelPurpose,
            width:region?.getBoundingClientRect().width||0,
            gpuDetails:!!node?.querySelector('[data-mcel-measurement-detail="gpuDevices"][open]')};
        }''')
        expected_telemetry={'cpuPercent':'12%','gpuPercent':'3%'}
        _scenario(cases,'hardware','visible-cpu-gpu-measurements',expected_telemetry,telemetry['values'],
                  telemetry['values']==expected_telemetry and telemetry['meters']==2 and
                  not telemetry['rawJson'] and telemetry['gpuDetails'])
        _scenario(cases,'hardware','telemetry-uses-primary-workspace',True,
                  telemetry['regionPurpose']=='workspace' and telemetry['width']>=400,
                  telemetry['regionPurpose']=='workspace' and telemetry['width']>=400)
        # Unavailable readings must not be disguised as zero-percent usage.
        page.evaluate('window.__taskCase.gpuMissing=true')
        page.locator('form[data-intent="intent:refresh-hardware"] button[type=submit]').click()
        page.wait_for_function('window.__mcelWorkbench.app.readState("state:hardware")?.gpuPercentAvailable === false')
        gpu_missing=page.locator('[data-mcel-metric="gpuPercent"] [data-mcel-metric-value]').inner_text()
        _scenario(cases,'hardware','unavailable-gpu-is-not-zero','Unavailable',gpu_missing,
                  gpu_missing=='Unavailable' and page.locator('[data-mcel-metric="gpuPercent"] [data-mcel-metric-meter]').count()==0)
        page.evaluate('window.__taskCase.gpuMissing=false')
        # Kill a selected process from all-processes view, rather than relying
        # on hidden stale state from the server-processes view.
        page.locator('[data-mcel-mode-tabs] button[data-value="all-processes"]').click()
        page.locator('form[data-intent="intent:refresh-all"] button[type=submit]').click()
        _await_receipt(page,'refresh-all')
        page.locator(f'[data-record-key="{key}"]:visible').first.locator('button[data-select-key]').click()
        _click(page,'kill-process');_await_receipt(page,'kill-process','command-returned')
        kill=_requests(page,'/action')[-1]['body']
        _scenario(cases,'kill','generation-bound-kill','kill_pid',kill.get('action'),
                  kill.get('action')=='kill_pid' and kill.get('pid')==43121 and kill.get('expected_created_at')=='2026-11-05T12:00:00.000+00:00')
        _click(page,'server-status');_await_receipt(page,'server-status')
        _scenario(cases,'server-status','read-does-not-mutate',2,len(page.evaluate('window.__taskEffects')),
                  len(page.evaluate('window.__taskEffects'))==2)
        # Denied confirmation must never reach the backend.
        page.evaluate('window.__taskCase.allowMutations=false')
        old=len(_requests(page,'/action'))
        _click(page,'shutdown-server');page.wait_for_timeout(120)
        _scenario(cases,'authority','dismissed-confirmation-has-no-effect',old,len(_requests(page,'/action')),
                  old==len(_requests(page,'/action')))
        page.evaluate('window.__taskCase.allowMutations=true')
        for intent,action in (('start-server','server_start'),('restart-server','server_restart'),('shutdown-server','server_shutdown')):
            _click(page,intent);_await_receipt(page,intent,'command-returned' if intent=='start-server' else 'accepted-deferred')
            actual=_requests(page,'/action')[-1]['body']
            _scenario(cases,'server-lifecycle','controller-'+action,action,actual.get('action'),
                      actual.get('action')==action and actual.get('confirm') is True and bool(actual.get('request_id')))
        # Explicit AI is advisory, and not permitted to trigger OS effects.
        effects_before=page.evaluate('window.__taskEffects.length')
        ai=page.locator('form[data-intent="intent:task-ai"]')
        _show_workflow(page,ai)
        ai.locator('[name=instruction]').fill('Summarize the observed process')
        ai.locator('button[type=submit]').click();_await_receipt(page,'task-ai')
        ai_body=_requests(page,'/ai')[-1]['body']
        _scenario(cases,'ai','explicit-advisory-ai',
                  {'instruction':'Summarize the observed process','effectsDelta':0},
                  {'instruction':ai_body.get('instruction'),'effectsDelta':page.evaluate('window.__taskEffects.length')-effects_before},
                  ai_body.get('instruction')=='Summarize the observed process' and page.evaluate('window.__taskEffects.length')==effects_before)
        # Schedule list, selection, exact revision and action verification.
        _click(page,'list-schedules');_await_receipt(page,'list-schedules')
        selected_id='sc-1'
        page.locator('[data-record-key="sc-1"] button[data-select-key]').click()
        _scenario(cases,'schedule-list','schedule-selected',selected_id,
                  page.evaluate('window.__mcelWorkbench.app.readState("state:selected-schedule")'),
                  page.evaluate('window.__mcelWorkbench.app.readState("state:selected-schedule")')==selected_id)
        _click(page,'run-schedule');_await_receipt(page,'run-schedule','accepted-deferred')
        s_run=_requests(page,'/schedule/run')[-1]['body']
        exp={'schedule_id':'sc-1','expected_revision':'r1','expected_action':'server_restart','confirm':True}
        _scenario(cases,'schedule-run','recorded-schedule-revision',exp,{k:s_run.get(k) for k in exp},
                  all(s_run.get(k)==v for k,v in exp.items()) and str(s_run.get('request_id','')).startswith('manual_'))
        # Revision changed on the backend after read must prevent execution.
        before=page.evaluate('window.__taskEffects.length')
        page.evaluate('window.__taskCase.schedules[0].revision="r2"')
        _click(page,'run-schedule');_await_receipt(page,'run-schedule','failed')
        _scenario(cases,'authority','stale-schedule-revision-refused',before,page.evaluate('window.__taskEffects.length'),
                  page.evaluate('window.__taskEffects.length')==before)
        _click(page,'delete-schedule');_await_receipt(page,'delete-schedule','failed')
        _scenario(cases,'schedule-delete','stale-schedule-delete-refused',before,page.evaluate('window.__taskEffects.length'),
                  page.evaluate('window.__taskEffects.length')==before)
        # Refresh observation, then delete with current revision.
        _click(page,'list-schedules');_await_receipt(page,'list-schedules')
        page.locator('[data-record-key="sc-1"] button[data-select-key]').click()
        _click(page,'delete-schedule');_await_receipt(page,'delete-schedule','recorded')
        _scenario(cases,'schedule-delete','revision-bound-delete',0,
                  page.evaluate('window.__taskCase.schedules.length'),
                  page.evaluate('window.__taskCase.schedules.length')==0)
        form=page.locator('form[data-intent="intent:create-schedule"]')
        _show_workflow(page,form)
        form.locator('[name=action]').fill('server_start')
        form.locator('[name=runAt]').fill('2026-11-07T12:00:00+00:00')
        form.locator('[name=note]').fill('isolated operator request')
        form.locator('button[type=submit]').click();_await_receipt(page,'create-schedule','recorded')
        _scenario(cases,'schedule-create','schedule-created-with-scope',1,
                  page.evaluate('window.__taskCase.schedules.length'),
                  page.evaluate('window.__taskCase.schedules.length')==1)
        mutation_calls=[r for r in page.evaluate('window.__taskRequests') if any(r['path'].endswith(k) for k in ('/action','/schedule/create','/schedule/delete','/schedule/run','/ai'))]
        _scenario(cases,'authority','local-csrf-all-mutations',True,
                  all(r['headers'].get('X-MCEL-Task-CSRF')=='isolated-local-token' for r in mutation_calls),
                  all(r['headers'].get('X-MCEL-Task-CSRF')=='isolated-local-token' for r in mutation_calls))
        _scenario(cases,'receipts','operation-receipts-present',True,
                  bool(page.evaluate('window.__mcelWorkbench.app.receipts().filter(r=>r.intentId.startsWith("intent:") && r.disposition==="completed").length')),
                  bool(page.evaluate('window.__mcelWorkbench.app.receipts().filter(r=>r.disposition==="completed").length')))
      except Exception as exc:
        try:
          debug=page.evaluate('''() => ({requests:window.__taskRequests, effects:window.__taskEffects, receipts:window.__mcelWorkbench?.app?.receipts(), status:document.querySelector('#mcel-workbench-status')?.textContent})''')
        except Exception as debug_exc:debug={'error':str(debug_exc)}
        cases.append({'id':'browser-exception','feature':'harness','status':'ERROR','error':str(exc),'debug':debug})
      finally:
        page.close();browser.close()
    return {'status':'PASS' if cases and all(c['status']=='PASS' for c in cases) and not errors else 'INCOMPLETE',
            'cases':cases,'pageErrors':errors,'backend':backend,
            'httpTransport':'in-memory-fake-only; no OS/service/production request'}


def run(repo:Path,report_path:Path)->int:
    report={'schema':'mcel.task-manager-functional-parity-frob.v1','scope':'isolated-browser-and-source-read-only-to-production',
            'canonicalRoute':'/applications/task-manager','candidateRoute':'/applications/mcel-semantic-task-manager',
            'promotionAuthorized':False,'liveWindowsParity':'UNVERIFIED','designApproval':'UNVERIFIED',
            'evidenceLimits':['Incumbent DOM/handler source is verified but not executed in this browser run',
              'Browser expectations are incumbent-source contracts, not simultaneous hosted incumbent observations',
              'All network responses and consequential effects are simulated in memory; no Windows process/server modified',
              'Live AI, real telemetry, exact Windows process birth time, durable schedule store and host authority are not certified',
              'Canonical promotion and human approval remain separate gates']}
    try:
      evidence=source_inventory(repo)
      report.update(evidence)
      browser=browser_comparison(repo)
      report['browser']=browser
      for row in browser['cases']:
        feature=report['features'].get(row['feature'])
        if feature:feature['browserScenarios'].append(row['id'])
      for feature in report['features'].values():
        if feature['status']=='SOURCE_COVERED':
          checks=[row for row in browser['cases'] if row['id'] in feature['browserScenarios']]
          feature['status']='BROKEN' if any(x['status']!='PASS' for x in checks) else 'PASS_ISOLATED' if checks else 'UNVERIFIED'
      mismatches=[c for c in browser['cases'] if c['status']!='PASS']
      report['summary']={'featureCount':len(FEATURES),'browserCases':len(browser['cases']),
          'browserMismatches':len(mismatches),'missingFeatures':[k for k,v in report['features'].items() if v['status']=='MISSING'],
          'brokenFeatures':[k for k,v in report['features'].items() if v['status']=='BROKEN'],
          'unverifiedFeatures':[k for k,v in report['features'].items() if v['status']=='UNVERIFIED'],
          'byStatus':{status:sum(v['status']==status for v in report['features'].values()) for status in ('PASS_ISOLATED','MISSING','BROKEN','UNVERIFIED')}}
      report['status']='PASS_ISOLATED' if browser['status']=='PASS' and report['summary']['byStatus']['PASS_ISOLATED']==len(FEATURES) else 'INCOMPLETE'
    except Exception as exc:
      report['status']='INCOMPLETE';report['fatalError']=str(exc)
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    print('MCEL_TASK_MANAGER_FUNCTIONAL_PARITY '+report['status'])
    for item in FEATURES:print(f'  {item.key}: {report.get("features",{}).get(item.key,{}).get("status","UNVERIFIED")}')
    summary=report.get('summary',{})
    print(f'  browser: {summary.get("browserCases",0)} cases; mismatches: {summary.get("browserMismatches","unknown")}')
    if 'fatalError' in report:print('  error: '+report['fatalError'])
    print('  Windows live operations: UNVERIFIED; no production mutation or promotion')
    print('  report: '+str(report_path))
    return 0 if report['status']=='PASS_ISOLATED' else 1


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,default=REPORT)
    args=parser.parse_args(argv)
    return run(ROOT,ROOT/args.report if not args.report.is_absolute() else args.report)

if __name__=='__main__':raise SystemExit(main())
