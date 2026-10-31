#!/usr/bin/env python3
"""Promise closure Patch 03: generated whole-shell, shared composition, opt-in rollback.

Offline Chromium exercises source-bound IR and controlled producer-shaped snapshots.
Hosted mode proves whether the existing localhost Task Manager works; no machine
mutating actions are admitted. Does not claim server controls or AI are operational.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from urllib.parse import parse_qsl,urlencode,urlsplit,urlunsplit
import shutil
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
from tools.mcel_task_notebook_candidate_bundle import build,TARGET

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'runtime/reports/mcel-promise-wave3'
WIDTHS=(320,390,560,1100,1272)

def source_process_parity(source_count, rendered_count, empty_message):
 """An empty source is valid; silent dropped records or absent sources are not."""
 if type(source_count) is not int or source_count < 0:
  return False
 if type(rendered_count) is not int or rendered_count < 0:
  return False
 expected=min(500,source_count)  # The read-side projection caps at 500 records.
 return rendered_count==expected and (expected>0 or "No data for this view" in empty_message)

def run(output=OUTPUT,host_url=None):
 from playwright.sync_api import sync_playwright
 output=Path(output);output.mkdir(parents=True,exist_ok=True)
 built,meta=build()
 report={'schema':'mcel.promise-wave3-whole-shell.v1','sourceSha256':meta['sourceSha256'],
  'sourceBound':TARGET.read_text(encoding='utf8')==built,'offline':'FAIL',
  'hosted':'BLOCKED','hostReason':'localhost host not supplied','cases':[],
  'operatorMutationsAttempted':False,'admittedOperations':['external-read:refresh-process-data']}
 executable=shutil.which('chromium') or shutil.which('chromium-browser')
 with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,**({'executable_path':executable,'args':['--no-sandbox','--disable-dev-shm-usage']} if executable else {}))
  try:
   for width in WIDTHS:
    page=browser.new_page(viewport={'width':width,'height':800},reduced_motion='reduce')
    errors=[];requests=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('request',lambda r:requests.append(r.url))
    page.set_content(html_fixture(),wait_until='load')
    result=page.evaluate('''async () => {
     const root=document.getElementById('task-manager-app');
     const legacy=root.querySelector(':scope > .task-manager-shell');
     const before=legacy.outerHTML;
     let calls=0;
     const process=(pid,mem)=>({pid,name:'worker-'+pid,created_at:'stable-id',memory_rss:mem,
      memory_availability:'present',status:'running',status_availability:'present',cmdline:'/bin/w',
      is_current_process:false,is_main_computer:false});
     const data=(mem)=>({server:{running:true,pid:212},overview:{main_computer_process_count:2,
      connection_count:1,schedule_count:1},hardware:{cpu:{overall_percent:8},gpu:{overall_percent:3}},
      schedules:[{action:'server_status',run_at:'future',note:'test'}],
      processes:[process(101,mem),process(102,0)],all_processes:[process(101,mem)],connections:[]});
     const bad=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=unknown');
     window.refreshTaskManager=async()=>{
      calls++;McelTaskManagerNotebookLab.publish(data(200),'available',undefined,
       {streams:{processes:'available',all_processes:'available',connections:'empty',hardware:'available'}});
     };
     const mounted=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=application');
     const shell=root.querySelector('[data-mcel-generated="semantic-application-shell-v1"]');
     const generatedRegions=[...shell.querySelectorAll('[data-mcel-region]')].map(n=>n.dataset.mcelRegion);
     const start=McelTaskManagerNotebookLab.publish(data(100),'available',1);
     const scheduleRow=shell.querySelector('[data-mcel-region="region:schedules"] tbody tr');
     const row=shell.querySelector('[data-mcel-panel="server-processes"] mcel-row[data-row-id="101"]');
     row.setAttribute('tabindex','0');row.focus();
     const focusBefore=document.activeElement===row;
     shell.querySelector('[data-mcel-field="query"]').value='worker';
     shell.querySelector('[data-mcel-field="query"]').dispatchEvent(new Event('input',{bubbles:true}));
     shell.querySelector('[data-mcel-field="limit"]').value='35';
     shell.querySelector('[data-mcel-field="limit"]').dispatchEvent(new Event('input',{bubbles:true}));
     const changed=McelTaskManagerNotebookLab.status().applicationShell.form;
     const refresh=shell.querySelector('[data-mcel-shell-action="refresh"]');
     const noOperations=[...shell.querySelectorAll('[data-mcel-shell-action^="server-"]')]
       .every(b=>b.disabled && b.title.includes('Not admitted'));
     const prior=McelTaskManagerNotebookLab.status().version;
     refresh.click();
     const limit=performance.now()+2000;
     while(McelTaskManagerNotebookLab.status().operation?.pending && performance.now()<limit)
      await new Promise(r=>setTimeout(r,15));
     const after=McelTaskManagerNotebookLab.status();
     const newer=shell.querySelector('[data-mcel-panel="server-processes"] mcel-row[data-row-id="101"]');
     const scheduleStable=scheduleRow===shell.querySelector('[data-mcel-region="region:schedules"] tbody tr');
     const identity=row===newer && document.activeElement===row;
     shell.querySelector('[data-mcel-tab="hardware"]').click();
     const selected=shell.querySelector('[data-mcel-tab="hardware"]').getAttribute('aria-selected');
     const geometry={shellWidth:shell.getBoundingClientRect().width,
      docWidth:document.documentElement.scrollWidth,viewport:innerWidth,
      sidebarTop:shell.querySelector('.mcel-application-shell__sidebar').getBoundingClientRect().top,
      mainTop:shell.querySelector('.mcel-application-shell__main').getBoundingClientRect().top};
     const summary=shell.querySelector('[data-mcel-binding="pid"]')?.textContent;
     const schedules=shell.querySelectorAll('[data-mcel-region="region:schedules"] tbody tr').length;
     const operation=after.operation;
     const roll=McelTaskManagerNotebookLab.unmount(root);
     const restored=legacy.outerHTML===before && !legacy.hidden && !legacy.hasAttribute('inert') &&
      !root.querySelector('.mcel-application-shell');
     const candidate=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=candidate');
     const isolated=!!root.querySelector('[data-mcel-intent="refresh-process-data-control"]') &&
       !root.querySelector('.mcel-application-shell');
     McelTaskManagerNotebookLab.unmount(root);
     return {bad,mounted,start,generatedRegions,focusBefore,changed,
      formHost:{query:root.querySelector('#task-query').value,limit:root.querySelector('#task-limit').value},
      noOperations, calls,prior,afterVersion:after.version,operation,identity,scheduleStable,selected,
      geometry,summary,schedules,roll,restored,candidate,isolated};
    }''')
    geom=result['geometry']
    checks={
      'source-bound':report['sourceBound'],
      'opt-in-only':result['bad']['code']=='not-opted-in',
      'generated-shell-mount':result['mounted']['code']=='mounted-semantic-application' and result['start']['ok'],
      'seven-semantic-regions':len(result['generatedRegions'])==7,
      'host-filter-binding':result['changed']['query']=='worker' and result['formHost']['query']=='worker' and result['formHost']['limit']=='35',
      'monitor-data-composition':result['summary']=='212' and result['schedules']==1,
      'generated-refresher-real-receipt':result['calls']==1 and result['afterVersion']>result['prior'] and result['operation']['phase']=='completed',
      'stable-row-identity-and-focus':result['identity'] and result['focusBefore'],
      'unchanged-schedule-identity':result['scheduleStable'],
      'semantic-navigation':result['selected']=='true',
      'unadmitted-controls-inert':result['noOperations'],
      'responsive-width':geom['docWidth']<=width+2 and geom['shellWidth']<=width+2,
      'legacy-exact-rollback':result['roll']['ok'] and result['restored'],
      'previous-notebook-candidate-unchanged':result['candidate']['ok'] and result['isolated'],
      'no-browser-or-network-errors':not errors and not requests,
    }
    report['cases'].append({'width':width,'status':'PASS' if all(checks.values()) else 'FAIL',
      'checks':checks,'geometry':geom,'operation':result['operation'],
      'browserErrors':errors,'networkRequests':requests})
    page.close()
   # An unrelated second semantic source must use exactly the same shell engine.
   page=browser.new_page();page.set_content(html_fixture())
   independent=page.evaluate('''() => {
     const ir={application:{title:'Independent Notes'},intents:[],
       surfaces:[{semanticSurface:{applicationShell:{schema:'mcel.semantic-application-shell.v1',
        id:'notes',label:'Notes',layout:'sidebar-workspace',regions:[
         {id:'region:status',zone:'sidebar',kind:'summary',title:'Status',items:[{id:'count',label:'Items',path:'counts.items'}]},
         {id:'region:notebook',zone:'main',kind:'slot',title:'Notes',slot:'notebook'}]}}}]};
     const host=document.createElement('div'),slot=document.createElement('div');slot.textContent='Independent';
     document.body.append(host);
     const app=McelSemanticApplicationShell.mount(host,ir,{slots:{notebook:slot}});
     const updated=app.update({counts:{items:9}}),text=host.querySelector('[data-mcel-binding="count"]').textContent;
     app.destroy();return {updated,text,destroyed:host.childElementCount===0};
   }''')
   report['independentApp']=independent
   report['offline']='PASS' if report['sourceBound'] and all(c['status']=='PASS' for c in report['cases']) and independent['updated']['ok'] and independent['text']=='9' and independent['destroyed'] else 'FAIL'
   page.close()
   # A live host with no matching processes must render a truthful empty state,
   # and subsequently recover when records become available.
   empty_page=browser.new_page();empty_page.set_content(html_fixture(),wait_until='load')
   empty_case=empty_page.evaluate("""() => {
     const root=document.getElementById('task-manager-app');
     const mounted=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=application');
     const make=(processes)=>({server:{running:true,pid:123},overview:{},
       processes,all_processes:processes,connections:[],hardware:{},schedules:[]});
     const first=McelTaskManagerNotebookLab.publish(make([]),'available',1);
     const panel=root.querySelector('[data-mcel-panel="server-processes"]');
     const emptyRows=panel.querySelectorAll('mcel-row').length;
     const emptyMessage=panel.querySelector('.mcel-tab-workspace__notice')?.textContent||'';
     const process={pid:123,name:'visible-recovery',created_at:'stable',memory_rss:0,
       memory_availability:'present',status:'running',status_availability:'present',cmdline:'/bin/test',
       is_current_process:false,is_main_computer:false};
     const second=McelTaskManagerNotebookLab.publish(make([process]),'available',2);
     const recoveredRows=panel.querySelectorAll('mcel-row').length;
     McelTaskManagerNotebookLab.unmount(root);
     return {mounted:mounted.ok,first:first.ok,second:second.ok,
       emptyRows,emptyMessage,recoveredRows};
   }""")
   report['emptySnapshot']=empty_case
   report['offline']='PASS' if report['offline']=='PASS' and empty_case['mounted'] and \
     empty_case['first'] and empty_case['second'] and empty_case['emptyRows']==0 and \
     'No data for this view' in empty_case['emptyMessage'] and empty_case['recoveredRows']==1 else 'FAIL'
   empty_page.close()
   # Visual reference is captured from a fresh *mounted* generated application,
   # never after the legacy rollback exercised in the behavioral case.
   for width in (390,1100):
    visual=browser.new_page(viewport={'width':width,'height':800},reduced_motion='reduce')
    visual.set_content(html_fixture(),wait_until='load')
    visual.evaluate('''() => {
      const root=document.getElementById('task-manager-app');
      McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=application');
      const proc={pid:222,name:'worker-visible',created_at:'stable',memory_rss:1024,
        memory_availability:'present',status:'running',status_availability:'present',cmdline:'/bin/worker',
        is_current_process:false,is_main_computer:false};
      McelTaskManagerNotebookLab.publish({server:{running:true,pid:222},
        overview:{main_computer_process_count:1,connection_count:1,schedule_count:1},
        hardware:{cpu:{overall_percent:16},gpu:{overall_percent:3}},
        schedules:[{action:'server_status',run_at:'future',note:'read-only'}],
        processes:[proc],all_processes:[proc],connections:[]},'available',1);
    }''')
    visual.screenshot(path=str(output/f'application-{width}.png'))
    visual.close()
   if host_url:
    page=browser.new_page(viewport={'width':1100,'height':800});errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    try:
     parsed=urlsplit(host_url);q=dict(parse_qsl(parsed.query));q['mcel_task_notebook']='application';
     url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(q),parsed.fragment))
     response=page.goto(url,wait_until='domcontentloaded',timeout=20000)
     page.wait_for_function('''() => window.McelTaskManagerNotebookLab?.status()?.mode==='semantic-application' &&
       window.McelTaskManagerNotebookLab?.status()?.version>0''',timeout=25000)
     result=page.evaluate('''async () => {
      const before=McelTaskManagerNotebookLab.status(),shell=document.querySelector('.mcel-application-shell');
      const refresh=shell.querySelector('[data-mcel-shell-action="refresh"]');
      refresh.click();const limit=performance.now()+25000;
      while(McelTaskManagerNotebookLab.status().operation?.pending && performance.now()<limit)
       await new Promise(r=>setTimeout(r,80));
      const after=McelTaskManagerNotebookLab.status();
      // Validate observation parity, NOT non-emptiness. A valid live host can
      // report zero matching processes; a missing/incorrect source must not pass.
      const source=(typeof taskManagerSnapshotCache === 'undefined') ? null : taskManagerSnapshotCache;
      const serverPanel=shell.querySelector('[data-mcel-panel="server-processes"]');
      const allPanel=shell.querySelector('[data-mcel-panel="all-processes"]');
      const emptyMessage=panel=>panel?.querySelector('.mcel-tab-workspace__notice')?.textContent||'';
      return {before:before.version,after:after.version,operation:after.operation,
       mounted:!!shell,oldHidden:document.querySelector('.task-manager-shell').hidden,
       processes:serverPanel?.querySelectorAll('mcel-row').length??null,
       allProcesses:allPanel?.querySelectorAll('mcel-row').length??null,
       sourceProcesses:Array.isArray(source?.processes)?source.processes.length:null,
       sourceAllProcesses:Array.isArray(source?.all_processes)?source.all_processes.length:null,
       serverEmptyMessage:emptyMessage(serverPanel),allEmptyMessage:emptyMessage(allPanel),
       sourceStreams:after.streams,renderReceipts:after.lastReceipt?.renderReceipts||{}};
     }''')
     checks={'http200':response is not None and response.status==200,'mounted':result['mounted'],
       'old-shell-hidden':result['oldHidden'],'version-advanced':result['after']>result['before'],
       'operation-completed':result['operation']['phase']=='completed',
       'process-source-parity':source_process_parity(result['sourceProcesses'],result['processes'],
         result['serverEmptyMessage']),
       'all-process-source-parity':source_process_parity(result['sourceAllProcesses'],result['allProcesses'],
         result['allEmptyMessage']),
       'no-errors':not errors}
     report['hosted']='PASS' if all(checks.values()) else 'FAIL'
     report['host']={'url':url,'checks':checks,'evidence':result,'browserErrors':errors}
    except Exception as e:
     report['hosted']='BLOCKED';report['hostReason']=str(e)
    finally:page.close()
  finally:browser.close()
 (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 print('MCEL_PROMISE_WAVE3_'+report['offline'], 'hosted',report['hosted'])
 for case in report['cases']:
  print(case['width'], case['status'], [k for k,v in case['checks'].items() if not v],case['geometry'])
 print('report',output/'report.json')
 if host_url and report['hosted']=='FAIL':
  print('host failures:', ', '.join(k for k,v in report['host']['checks'].items() if not v))
  print('source / rendered server processes:',
        report['host']['evidence']['sourceProcesses'],'/',report['host']['evidence']['processes'])
  print('source / rendered all processes:',
        report['host']['evidence']['sourceAllProcesses'],'/',report['host']['evidence']['allProcesses'])
 return report

if __name__=='__main__':
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--output',type=Path,default=OUTPUT)
 ap.add_argument('--host-url',default=None)
 args=ap.parse_args()
 result=run(args.output,args.host_url)
 raise SystemExit(0 if result['offline']=='PASS' and (not args.host_url or result['hosted']=='PASS') else 1)
