#!/usr/bin/env python3
"""Patch 02: source-bound intent -> injected read capability -> observed data -> receipt.

Offline browser checks use deterministic synthetic producer-shaped data; they do
NOT establish that the real Task Manager HTTP endpoint was called. --host-url
performs the actual host click and data reconciliation without machine mutation.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
from tools.mcel_task_notebook_candidate_bundle import build, TARGET

OUTPUT = Path(__file__).resolve().parents[1] / 'runtime/reports/mcel-promise-wave2'
WIDTHS = (390, 1100, 1272)


def run(output=OUTPUT, host_url=None):
    from playwright.sync_api import sync_playwright
    output=Path(output);output.mkdir(parents=True, exist_ok=True)
    expected,meta=build()
    report={'schema':'mcel.promise-wave2-operational-receipt.v1',
        'sourceBound':TARGET.read_text(encoding='utf-8')==expected,
        'offline':'FAIL','hosted':'BLOCKED','hostReason':'localhost host not supplied',
        'cases':[], 'host':{},'sourceSha256':meta['sourceSha256'],
        'realHostCalled':False,'machineMutationsRequested':False}
    executable=shutil.which('chromium') or shutil.which('chromium-browser')
    with sync_playwright() as p:
      browser=p.chromium.launch(headless=True, **({'executable_path':executable,'args':['--no-sandbox','--disable-dev-shm-usage']} if executable else {}))
      try:
        for width in WIDTHS:
          page=browser.new_page(viewport={'width':width,'height':800},reduced_motion='reduce')
          errors=[];requests=[]
          page.on('pageerror',lambda e:errors.append(str(e)))
          page.on('request',lambda r:requests.append(r.url))
          page.set_content(html_fixture(),wait_until='load')
          evidence=page.evaluate(r'''async () => {
            const root=document.getElementById('task-manager-app');
            let calls=0,behavior='success', release=null;
            const process=(pid,mem)=>({pid,name:'worker-'+pid,created_at:'stable-g',memory_rss:mem,
              memory_availability:'present',status:'running',status_availability:'present',cmdline:'/bin/test',
              is_current_process:false,is_main_computer:false});
            const rows=[process(101,0),process(102,2048)];
            const data={processes:rows,all_processes:rows,connections:[],hardware:{}};
            window.refreshTaskManager=async()=>{
              calls++;
              if(behavior==='failed')throw Error('synthetic upstream unavailable');
              if(behavior==='blocked')await new Promise(resolve=>{release=resolve});
              if(behavior!=='stale')McelTaskManagerNotebookLab.publish(
                {processes:[process(101,3072),process(102,2048)],all_processes:rows,
                  connections:[],hardware:{}},'available',undefined,
                {streams:{processes:'available',all_processes:'available',connections:'empty',hardware:'empty'}});
            };
            const bad=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=other');
            const mounted=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=candidate');
            const initial=McelTaskManagerNotebookLab.publish(data,'available',1);
            const button=root.querySelector('[data-mcel-intent="refresh-process-data-control"]');
            const collection=root.querySelector('[data-mcel-panel="server-processes"] mcel-collection');
            const before=collection.querySelector('mcel-row[data-row-id="101"]');
            before.setAttribute('tabindex','0');before.focus();
            const actionEnabledBefore=!button.disabled;
            async function settled(){const limit=performance.now()+2500;
              while(McelTaskManagerNotebookLab.status().operation?.pending && performance.now()<limit)
                await new Promise(r=>setTimeout(r,15));
              return McelTaskManagerNotebookLab.status().operation;}
            button.click();await settled();
            const after=collection.querySelector('mcel-row[data-row-id="101"]');
            const completed=McelTaskManagerNotebookLab.status().operation;
            const stable=before===after && document.activeElement===after;
            behavior='stale';button.click();await settled();
            const stale=McelTaskManagerNotebookLab.status().operation;
            behavior='failed';button.click();await settled();
            const failed=McelTaskManagerNotebookLab.status().operation;
            behavior='blocked';button.click();await Promise.resolve();
            const pending=McelTaskManagerNotebookLab.status().operation;
            button.click();
            const callsPending=calls;
            const runtime=McelIntentEffectRuntime.mount(McelTaskManagerNotebookCandidate.ir,{});
            const invalid=await runtime.dispatch('not-declared');
            const denied=await runtime.dispatch('refresh-process-data-control');
            runtime.destroy();
            const rollback=McelTaskManagerNotebookLab.unmount(root);
            release?.();await new Promise(r=>setTimeout(r,35));
            const reverted=!root.querySelector('.task-mcel-notebook-lab-host')&&
              !root.querySelector('.task-notebook').hasAttribute('inert');
            const old=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=lab');
            const labButtonCount=root.querySelectorAll('[data-mcel-intent]').length;
            const lab=McelTaskManagerNotebookLab.status();
            McelTaskManagerNotebookLab.unmount(root);
            return {bad,mounted,initial,actionVisible:!!button,actionEnabled:actionEnabledBefore,
              completed,stable,stale,failed,pending,callsPending,calls,denied,invalid,
              rollback,reverted,old,labButtonCount,lab,
              originalHeader:root.querySelector('.task-notebook')?.outerHTML.slice(0,100)};
          }''')
          checks={'source-bound':report['sourceBound'],'opt-in-only':evidence['bad']['code']=='not-opted-in',
             'mounted':evidence['mounted']['ok'] and evidence['initial']['ok'],
             'generated-action-enabled':evidence['actionVisible'] and evidence['actionEnabled'],
             'real-receipt-gated':evidence['completed']['phase']=='completed' and
              evidence['completed']['receipt']['beforeVersion']==1 and
              evidence['completed']['receipt']['afterVersion']==2,
             'identity-and-focus':evidence['stable'],
             'stale-refused':evidence['stale']['phase']=='failed' and evidence['stale']['receipt']['code']=='snapshot-not-advanced',
             'upstream-failure':evidence['failed']['phase']=='failed' and evidence['failed']['receipt']['code']=='capability-failed',
             'single-flight':evidence['pending']['pending'] and evidence['callsPending']==4,
             'undeclared-refused':evidence['invalid']['code']=='undeclared-intent' and evidence['denied']['code']=='capability-unavailable',
             'rollback-during-flight':evidence['reverted'] and evidence['rollback']['ok'],
             'independent-lab-inert':evidence['old']['ok'] and evidence['labButtonCount']==0,
             'zero-network-or-page-errors':not errors and not requests}
          report['cases'].append({'width':width,'checks':checks,'status':'PASS' if all(checks.values()) else 'FAIL',
             'evidence':evidence,'browserErrors':errors,'networkRequests':requests})
          page.close()
        report['offline']='PASS' if report['sourceBound'] and all(c['status']=='PASS' for c in report['cases']) else 'FAIL'
        if host_url:
          page=browser.new_page(viewport={'width':1100,'height':800});errors=[];response=None
          page.on('pageerror',lambda e:errors.append(str(e)))
          try:
            url=urlsplit(host_url);params=dict(parse_qsl(url.query));params['mcel_task_notebook']='candidate'
            target=urlunsplit((url.scheme,url.netloc,url.path,urlencode(params),url.fragment))
            response=page.goto(target,wait_until='domcontentloaded',timeout=20000)
            page.wait_for_function('''() => window.McelTaskManagerNotebookLab?.status()?.version>0 &&
               document.querySelector('[data-mcel-intent="refresh-process-data-control"]:not(:disabled)')''',timeout=25000)
            evidence=page.evaluate('''async()=>{
              const before=McelTaskManagerNotebookLab.status(),
                button=document.querySelector('[data-mcel-intent="refresh-process-data-control"]');
              button.click();const end=performance.now()+20000;
              while(McelTaskManagerNotebookLab.status().operation?.pending && performance.now()<end)
                await new Promise(r=>setTimeout(r,80));
              const after=McelTaskManagerNotebookLab.status();
              return {beforeVersion:before.version,afterVersion:after.version,
                operation:after.operation,delivery:after.lastReceipt,
                parity:JSON.stringify((taskManagerSnapshotCache?.processes||[]).map(r=>String(r.pid)).sort())===
                JSON.stringify([...document.querySelectorAll('[data-mcel-panel="server-processes"] mcel-row')].map(r=>r.dataset.rowId).sort()),
                legacyHidden:document.querySelector('.task-notebook')?.hidden};
            }''')
            checks={'http200':response is not None and response.status==200,
              'operation-completed':evidence['operation']['phase']=='completed',
              'version-advanced':evidence['afterVersion']>evidence['beforeVersion'],
              'snapshot-parity':evidence['parity'],'legacy-hidden':evidence['legacyHidden'],
              'browser-errors':not errors}
            report['hosted']='PASS' if all(checks.values()) else 'FAIL'
            report['realHostCalled']=True
            report['host']={'url':target,'checks':checks,'evidence':evidence,'browserErrors':errors}
          except Exception as exc:
            report['hosted']='FAIL' if response is not None and response.status==200 else 'BLOCKED'
            report['hostReason']=f'{type(exc).__name__}: {exc}'[:600]
            if response is not None:report['host']={'httpStatus':response.status,'browserErrors':errors}
          finally:page.close()
      finally:browser.close()
    (output/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return report


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,default=OUTPUT)
    ap.add_argument('--host-url',default=None)
    args=ap.parse_args(argv)
    result=run(args.output,args.host_url)
    print('MCEL_PROMISE_WAVE2_'+result['offline'])
    for case in result['cases']:
      print(case['width'],case['status'],[key for key,success in case['checks'].items() if not success])
    print('hosted',result['hosted'],result.get('hostReason',''))
    print('report',args.output/'report.json')
    return 1 if result['offline']!='PASS' or result['hosted']=='FAIL' else 0

if __name__=='__main__':raise SystemExit(main())
