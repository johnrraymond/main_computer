#!/usr/bin/env python3
"""Offline Chromium task-manager semantic-pipeline acceptance, no operator actions.
Exercises unchanged Task Manager HTML + actual pinned read-only lab notebook.
Synthetic process records deliberately stress redundancy, long text and priority.
Neither normal-route HTTP nor source-DSL continuity is certified.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

WIDTHS=(320,390,560,1100)
ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'runtime/reports/mcel-task-manager-semantic-pipeline'

def run(output=OUTPUT):
    from playwright.sync_api import sync_playwright
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    results={'schema':'mcel.task-manager-semantic-pipeline-acceptance.v1',
             'generated_utc':datetime.now(timezone.utc).isoformat(),
             'status':'FAIL','source':'synthetic 22-row parity fixture, not live HTTP',
             'operator_actions_executed':False,'cases':[]}
    with sync_playwright() as browser:
        import shutil
        executable=shutil.which('chromium') or shutil.which('chromium-browser')
        chrome=browser.chromium.launch(headless=True,executable_path=executable,args=['--no-sandbox','--disable-dev-shm-usage'] if executable else [])
        for width in WIDTHS:
            page=chrome.new_page(viewport={'width':width,'height':800})
            requests=[];errors=[]
            page.on('request',lambda req: requests.append(req.url))
            page.on('pageerror',lambda exc: errors.append(str(exc)))
            page.set_content(html_fixture(),wait_until='load')
            data=page.evaluate('''() => {
              const root=document.querySelector('#task-manager-app');
              const raw=Array.from({length:22},(_,i)=>({pid:2836+i,name:'python.exe',
                status:'running',cmdline:'C:\\\\Users\\\\subsi\\\\AppData\\\\Local\\\\Programs\\\\Python\\\\python.exe --module main_computer.some_deep_system_process --argument '+i+' --very-long-path=\\\\testing\\\\folder\\\\'+i,
                command_preview:'Server-shortened cmd '+i,memory_rss:152843264+i*1024,memory_human:'old human value',
                is_main_computer:i===1,is_current_process:i===0,created_at:'2026-10-26T14:00:00'}));
              const denied=McelTaskManagerNotebookLab.mount({root,ir:McelTaskManagerTabWitness,search:''});
              const mounted=McelTaskManagerNotebookLab.mount({root,ir:McelTaskManagerTabWitness,search:'?mcel_task_notebook=lab'});
              const published=McelTaskManagerNotebookLab.publish({processes:raw,all_processes:raw,connections:[],hardware:{}},'available',10);
              const panel=root.querySelector('[data-mcel-panel="server-processes"]');
              const semantic=panel?.querySelector('.mcel-semantic-collection');
              const rows=[...(semantic?.querySelectorAll('mcel-row')||[])];
              const frame=semantic?.querySelector('mcel-collection');
              const visible=rows[0]?[...rows[0].children].filter(x=>!x.hidden).map(x=>x.dataset.semanticField):[];
              const hidden=rows[0]?[...rows[0].children].filter(x=>x.hidden).map(x=>x.dataset.semanticField):[];
              const allocation=McelSemanticLayoutInference.evaluateSemanticCollectionLayout(semantic,
                McelSemanticCollectionWorkspace.project(raw,McelTaskManagerProcessContract.processes),McelElementRegistry);
              const full=raw[0].cmdline;
              rows[0]?.click();
              const inspector=semantic?.querySelector('.mcel-semantic-collection__inspector');
              const inspectFull=inspector?.textContent.includes(full);
              const identifier=rows.map(row=>row.dataset.rowId);
              const projection=McelSemanticCollectionWorkspace.project(raw,McelTaskManagerProcessContract.processes);
              const unchanged=raw[0].command_preview==='Server-shortened cmd 0' &&
                raw[0].cmdline===full && projection.source_rows[0].command===full;
              const negative=McelSemanticCollectionWorkspace.project(raw,McelTaskManagerProcessContract.processes);
              const role=negative.source_rows.slice(0,3).map(row=>row.role);
              const stale=McelTaskManagerNotebookLab.publish({processes:[]},'available',9);
              const status=McelTaskManagerNotebookLab.status();
              const baseline=!!root.querySelector('.task-notebook');
              const genericSpec={schema:'mcel.semantic-collection-spec.v1',identity:'fileId',fields:[
                {key:'fileId',from:'fileId',kind:'identifier',tier:'primary',label:'Identifier'},
                {key:'size',from:'sizeBytes',kind:'quantity',tier:'secondary',label:'Size',presentation:{units:'bytes'}},
                {key:'location',from:'location',kind:'long-text',tier:'detail',label:'Location'}]};
              const generic=McelSemanticCollectionWorkspace.project([
                {fileId:'file-one',sizeBytes:2048,location:'C:\\Documents\\ExtremelyLongName'}],genericSpec);
              const domainGeneral=generic.source_rows[0].size===2048 &&
                generic.projection.rows[0].cells.size.display==='2.0 KB';
              const wrong={...McelTaskManagerProcessContract.processes,identity:'missing'};
              let refused=false;try{McelSemanticCollectionWorkspace.project(raw,wrong);}catch(_){refused=true;}
              // Read-only no-identity-collision and semantic disabling control.
              const uncontrolled=document.createElement('div');
              uncontrolled.innerHTML='<table><thead><tr>'+Object.keys(raw[0]).map(k=>'<th>'+k+'</th>').join('')+'</tr></thead></table>';
              const unsemantic=uncontrolled.textContent.includes('memory_human') &&
                uncontrolled.textContent.includes('command_preview');
              return {denied,mounted,published,count:rows.length,ids:identifier,
                allocation:allocation.plan,authority:semantic?.dataset.mcelCollectionLayoutAuthority,
                layoutMode:semantic?.dataset.layoutMode,visible,hidden,inspectFull,unchanged,role,
                oldKeysNotVisible:![...semantic.querySelectorAll('.field-label')].some(node=>
                  ['memory_human','is_current_process','command_preview'].includes(node.textContent)),
                metadata:status,stale,baseline,refused,unsemantic,domainGeneral,
                overflow:document.documentElement.scrollWidth-innerWidth,
                inspectorOverflow:inspector?inspector.scrollWidth-inspector.clientWidth:999,
                controls:[...root.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze')].length};
            }''')
            # Exercise reflow under viewport transitions without republishing.
            refresh=page.evaluate('''() => {
              const h=document.querySelector('.mcel-semantic-collection');
              return {status:h.dataset.mcelCollectionLayoutAuthority,
                widths:[...h.querySelectorAll('mcel-row:first-child mcel-field')].map(f=>({key:f.dataset.semanticField,hidden:f.hidden,width:Math.round(f.getBoundingClientRect().width)}))};
            }''')
            screenshot=output/f'semantic-task-manager-{width}.png'
            page.screenshot(path=str(screenshot),animations='disabled',full_page=False)
            teardown=page.evaluate('''() => {
              const root=document.querySelector('#task-manager-app');
              const before=root.querySelector('.task-notebook').outerHTML;
              const unmounted=McelTaskManagerNotebookLab.unmount(root);
              return {ok:unmounted.ok,legacyUnchanged:before===root.querySelector('.task-notebook').outerHTML,
                remaining:root.querySelectorAll('.task-mcel-notebook-lab-host').length};
            }''')
            ok=(data['denied']['code']=='not-opted-in' and data['mounted']['ok'] and data['published']['ok']
                and data['count']==22 and len(set(data['ids']))==22 and data['inspectFull']
                and data['unchanged'] and data['role']==['Current','Main Computer','Other']
                and data['oldKeysNotVisible'] and data['authority']=='mcel-semantic-layout-inference.v1'
                and data['allocation']['unresolved_deficit_px']==0
                and 'created' in data['hidden'] and 'pid' in data['visible'] and 'name' in data['visible']
                and data['stale']['code']=='stale-snapshot' and data['refused'] and data['unsemantic'] and data['domainGeneral']
                and data['baseline'] and data['controls']==5 and data['overflow']<=2 and data['inspectorOverflow']<=2
                and teardown['ok'] and teardown['legacyUnchanged'] and teardown['remaining']==0
                and not requests and not errors)
            results['cases'].append({'width':width,'status':'PASS' if ok else 'FAIL', 'evidence':data,
                'refresh':refresh,'teardown':teardown,'requests':requests,'errors':errors,
                'screenshot':screenshot.name})
            page.close()
        chrome.close()
    results['status']='PASS' if all(c['status']=='PASS' for c in results['cases']) else 'FAIL'
    (output/'report.json').write_text(json.dumps(results,indent=2,default=str)+'\n',encoding='utf8')
    return results

if __name__=='__main__':
    r=run()
    print('MCEL_TASK_MANAGER_SEMANTIC_PIPELINE_'+r['status'])
    for c in r['cases']: print('width',c['width'],c['status'],c['evidence']['authority'], c['evidence']['visible'])
    print('report:',OUTPUT/'report.json')
    raise SystemExit(0 if r['status']=='PASS' else 1)
