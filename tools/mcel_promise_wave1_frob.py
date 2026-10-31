#!/usr/bin/env python3
"""Wave 0/1A acceptance: *new* authored DSL -> compiler -> browser IR -> real-host
notebook slot, read-only snapshots, identity/selection lifecycle and rollback.

The offline browser uses real repository host markup and actual MCEL renderers;
its producer snapshots are deterministic, *not* proof of real network telemetry.
An optional --host-url checks actual Task Manager snapshots and an actual refresh.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
from tools.mcel_task_notebook_candidate_bundle import SOURCE,TARGET,build

OUTPUT=ROOT/'runtime/reports/mcel-promise-wave1'
WIDTHS=(320,390,560,1100,1272)

def run(output=OUTPUT,host_url=None):
    from playwright.sync_api import sync_playwright
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    report={'schema':'mcel.promise-wave1-source-bound-candidate.v1',
            'createdUtc':datetime.now(timezone.utc).isoformat(),
            'sourceRecovered':False,'sourceStatus':'new-dsl-candidate-not-original-phase3',
            'candidateOptInOnly':True,'effectAuthority':'read-only-local-selection',
            'offline':'BLOCKED','hosted':'BLOCKED','hostReason':'No running real host URL provided',
            'cases':[],'operatorActionsExecuted':False,'sourceChecks':{}}
    try:
        expected, metadata=build()
        report['sourceChecks']={'compiler':'PASS','browserBundleMatchesSource':TARGET.read_text()==expected,
                                'sourceSha256':metadata['sourceSha256'],
                                'semanticFingerprint':metadata['semanticFingerprint']}
        if not report['sourceChecks']['browserBundleMatchesSource']:
            raise ValueError('Candidate browser IR not derived from current DSL')
    except Exception as exc:
        report['offline']='FAIL';report['sourceChecks']={'error':str(exc)}
        (output/'report.json').write_text(json.dumps(report,indent=2)+'\n');return report
    browser_executable=shutil.which('chromium') or shutil.which('chromium-browser')
    with sync_playwright() as p:
      opts={'headless':True}
      if browser_executable: opts.update(executable_path=browser_executable,args=['--no-sandbox','--disable-dev-shm-usage'])
      browser=p.chromium.launch(**opts)
      try:
        for width in WIDTHS:
          page=browser.new_page(viewport={'width':width,'height':800},reduced_motion='reduce')
          errors=[];network=[]
          page.on('pageerror',lambda e:errors.append(str(e)))
          page.on('request',lambda req:network.append(req.url))
          page.set_content(html_fixture(),wait_until='load')
          evidence=page.evaluate(r'''() => {
            const root=document.querySelector('#task-manager-app');
            const legacy=root.querySelector('.task-notebook');
            const origin=legacy.outerHTML;
            const controls=[...root.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze')].map(n=>n.outerHTML);
            const failIfEnabled=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=invalid');
            const bad=McelTaskManagerNotebookLab.mount({root,ir:McelTaskManagerTabWitness,search:'?mcel_task_notebook=candidate'});
            const mounted=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=candidate');
            const built=McelTaskManagerNotebookLab.status();
            const panel=root.querySelector('[data-mcel-panel="server-processes"]');
            const process=(pid,gen,mem,availability) => ({pid,name:'worker-'+pid,created_at:gen,
              is_current_process:false,is_main_computer:false,memory_rss:mem,memory_availability:availability,
              status:'running',status_availability:'present',cmdline:'/bin/worker'});
            const initial=[process(101,'g101',0,'present'),process(102,'g102',null,'unavailable'),
              process(103,'g103',2048,'present')];
            const snapshot={processes:initial,all_processes:initial,connections:[],hardware:{}};
            const first=McelTaskManagerNotebookLab.publish(snapshot,'available',1,
              {streams:{processes:'available',all_processes:'available',connections:'empty',hardware:'empty'}});
            const collection=panel.querySelector('mcel-collection');
            const getRows=()=>[...collection.querySelectorAll('mcel-row')];
            const idsBefore=getRows().map(n=>n.dataset.rowId).sort();
            const current=collection.querySelector('mcel-row[data-row-id="101"]');
            const absent=collection.querySelector('mcel-row[data-row-id="102"]');
            const field=row=>row?.querySelector('[data-semantic-field="memory"] .field-value')?.textContent;
            const zero=field(current),unknown=field(absent);
            current?.setAttribute('tabindex','0');current?.focus();
            const changed=[process(103,'g103',3072,'present'),process(101,'g101',4096,'present'),
              process(104,'g104',null,'unavailable')];
            const second=McelTaskManagerNotebookLab.publish({processes:changed,all_processes:changed,
              connections:[],hardware:{}},'available',2,{streams:{processes:'available',all_processes:'available',connections:'empty',hardware:'empty'}});
            const next=collection.querySelector('mcel-row[data-row-id="101"]');
            const stable=next===current;
            const focus=document.activeElement===current;
            const idsAfter=getRows().map(n=>n.dataset.rowId).sort();
            const selected=McelTaskManagerNotebookLab.status().selected;
            const click=root.querySelector('[data-mcel-tab="connections"]');click.click();
            const selectedConnection=click.getAttribute('aria-selected')==='true';
            const badPublish=McelTaskManagerNotebookLab.publish({processes:initial},'available',1);
            const badMode=McelTaskManagerNotebookLab.mount({root,ir:McelTaskManagerTabWitness,search:'?mcel_task_notebook=candidate'});
            const legacyHidden=legacy.hidden&&legacy.hasAttribute('inert')&&legacy.getAttribute('aria-hidden')==='true'&&getComputedStyle(legacy).display==='none';
            const position={holder:root.querySelector('.task-mcel-notebook-lab-host')?.getBoundingClientRect().width,
              parent:root.querySelector('.task-detail-grid')?.getBoundingClientRect().width};
            const noDuplicateAccessibleNotebook=root.querySelectorAll('.task-notebook[hidden]').length===1 && root.querySelectorAll('.mcel-tab-workspace').length===1;
            const rollback=McelTaskManagerNotebookLab.unmount(root);
            return {originPreserved:legacy.outerHTML===origin,controlsPreserved:JSON.stringify(controls)===JSON.stringify([...root.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze')].map(n=>n.outerHTML)),
              candidateMissingWrongIr:bad.code,notOptedIn:failIfEnabled.code,mounted,source:built.source,
              mode:built.mode,first,second,idsBefore,idsAfter,zero,unknown,stable,focus,selected,
              selectedConnection,badPublish,badMode,legacyHidden,position,rollback,
              legacyRestored:!legacy.hidden&&!legacy.hasAttribute('inert')&&!legacy.hasAttribute('aria-hidden'),
              afterUnmount:root.querySelectorAll('.task-mcel-notebook-lab-host').length,
              noDuplicateAccessibleNotebook};
          }''')
          checks={
            'opt-in-refusal':evidence['notOptedIn']=='not-opted-in',
            'source-identity-must-match':evidence['candidateMissingWrongIr']=='source-bound-candidate-missing',
            'source-bound-mode':evidence['mode']=='source-bound-candidate' and evidence['source']['sourceSha256']==metadata['sourceSha256'],
            'mounted':evidence['mounted']['ok'] is True,
            'single-accessible-notebook':evidence['legacyHidden'] and evidence['noDuplicateAccessibleNotebook'],
            'first-snapshot':evidence['first']['ok'] is True and evidence['idsBefore']==['101','102','103'],
            'missing-versus-zero':evidence['zero']=='0 B' and evidence['unknown']=='Unavailable',
            'second-snapshot':evidence['second']['ok'] is True and evidence['idsAfter']==['101','103','104'],
            'stable-row-node':evidence['stable'],
            'focus-continuity':evidence['focus'],
            'local-selection':evidence['selectedConnection'] and evidence['selected']=='server-processes',
            'stale-rejected':evidence['badPublish']['code']=='stale-snapshot',
            'no-double-mount':evidence['badMode']['code']=='already-mounted',
            'no-clipped-host':evidence['position']['holder']>0 and evidence['position']['holder']<=evidence['position']['parent']+1,
            'exact-rollback':evidence['rollback']['ok'] and evidence['originPreserved'] and evidence['legacyRestored'] and evidence['afterUnmount']==0 and evidence['controlsPreserved'],
            'no-effect-or-network':not errors and not network,
          }
          report['cases'].append({'width':width,'status':'PASS' if all(checks.values()) else 'FAIL',
                                  'checks':checks,'evidence':evidence,'pageErrors':errors,'networkRequests':network})
          # Capture the visible generated candidate, not the legacy rollback state.
          page.set_content(html_fixture(),wait_until='load')
          visual=page.evaluate('''() => {
            const root=document.querySelector('#task-manager-app');
            const mounted=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=candidate');
            const rows=[{pid:101,name:'sample-worker',created_at:'synthetic-g1',memory_rss:0,
              memory_availability:'present',status:'running',status_availability:'present',cmdline:'synthetic',
              is_current_process:false,is_main_computer:false}];
            const published=McelTaskManagerNotebookLab.publish({processes:rows,all_processes:rows,
              connections:[],hardware:{}},'available',1);
            const generated=root.querySelector('.mcel-tab-workspace');
            const rect=generated?.getBoundingClientRect();
            return {mounted:mounted.ok,published:published.ok,visible:!!rect&&rect.width>0&&rect.height>0,
              originalHidden:root.querySelector('.task-notebook').hidden,
              generatedWidth:rect?.width||0,generatedHeight:rect?.height||0,
              documentWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth};
          }''')
          screenshot=output/f'candidate-{width}.png';page.screenshot(path=str(screenshot),animations='disabled')
          report['cases'][-1]['visual']=visual
          report['cases'][-1]['screenshot']=str(screenshot)
          if not all([visual['mounted'],visual['published'],visual['visible'],visual['originalHidden'],
                      visual['documentWidth']<=visual['viewportWidth']+2]):
              report['cases'][-1]['status']='FAIL'
          page.close()
        report['offline']='PASS' if all(c['status']=='PASS' for c in report['cases']) else 'FAIL'
        if host_url:
          live=browser.new_page(viewport={'width':1100,'height':800})
          errors=[];live.on('pageerror',lambda e:errors.append(str(e)))
          try:
            from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
            url=urlsplit(host_url)
            params=dict(parse_qsl(url.query));params['mcel_task_notebook']='candidate'
            target=urlunsplit((url.scheme,url.netloc,url.path,urlencode(params),url.fragment))
            response=live.goto(target,wait_until='domcontentloaded',timeout=15000)
            live.wait_for_function('''() => window.McelTaskManagerNotebookLab?.status()?.mode==='source-bound-candidate' &&
               typeof taskManagerSnapshotCache!=='undefined' && Array.isArray(taskManagerSnapshotCache?.processes) &&
               McelTaskManagerNotebookLab.status().lastReceipt?.ok===true''',timeout=25000)
            before=live.evaluate('''() => ({ids:taskManagerSnapshotCache.processes.map(r=>String(r.pid)).sort(),
              rendered:[...document.querySelectorAll('[data-mcel-panel="server-processes"] mcel-row')].map(n=>n.dataset.rowId).sort(),
              version:McelTaskManagerNotebookLab.status().version,source:McelTaskManagerNotebookLab.status().source,
              hidden:document.querySelector('.task-notebook').hidden})''')
            refresh=live.evaluate('''async()=>{const first=McelTaskManagerNotebookLab.status().version;
              await refreshTaskManager();const deadline=performance.now()+12000;
              while(McelTaskManagerNotebookLab.status().version<=first&&performance.now()<deadline)
                await new Promise(done=>setTimeout(done,70));
              return {version:McelTaskManagerNotebookLab.status().version,
                ids:taskManagerSnapshotCache.processes.map(r=>String(r.pid)).sort(),
                rendered:[...document.querySelectorAll('[data-mcel-panel="server-processes"] mcel-row')].map(n=>n.dataset.rowId).sort(),
                receipt:McelTaskManagerNotebookLab.status().lastReceipt}}''')
            hostedChecks={'http-success':response is not None and response.status==200,
              'source-identity':before['source']['sourceSha256']==metadata['sourceSha256'],
              'cold-data-parity':before['ids']==before['rendered'],'legacy-hidden':before['hidden'],
              'second-live-refresh':refresh['version']>before['version'],
              'refreshed-data-parity':refresh['ids']==refresh['rendered'],
              'receipt':refresh['receipt']['ok'] is True,'browser-errors':not errors}
            report['hosted']='PASS' if all(hostedChecks.values()) else 'FAIL'
            report['hostEvidence']={'url':target,'checks':hostedChecks,'before':before,'refresh':refresh,'pageErrors':errors}
          except Exception as exc:
            report['hosted']='BLOCKED';report['hostReason']=f'{type(exc).__name__}: {exc}'[:800]
          finally:live.close()
      finally: browser.close()
    (output/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return report

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--host-url',default=None)
    args=parser.parse_args(argv)
    result=run(args.output,host_url=args.host_url)
    print('MCEL_PROMISE_WAVE1_'+result['offline'])
    for item in result['cases']:
        print('width',item['width'],item['status'],[k for k,v in item['checks'].items() if not v])
    print('hosted',result['hosted'],result.get('hostReason',''))
    print('report',args.output/'report.json')
    return 1 if result['offline']!='PASS' or result['hosted']=='FAIL' else 0

if __name__=='__main__':raise SystemExit(main())
