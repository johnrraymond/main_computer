#!/usr/bin/env python3
"""Patch 07A: real two-application semantic planner, opt-in browser evidence.

No claimed labor savings, beauty score, or new domain authority. Preserves Wave 4/5.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from tools.mcel_promise_wave5_frob import HTML, SCRIPTS, TRANSPORT
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'runtime/reports/mcel-promise-wave7a'
WIDTHS = (320, 390, 560, 1100, 1272)
PROFILES = ('operational', 'analytical')
STYLE = ROOT / 'main_computer/web/applications/styles/mcel-semantic-application-shell.css'
JS_ROOT = ROOT / 'main_computer/web/applications/scripts'

# Only transport is mocked: this exercises the actual File Explorer semantic
# adapter, authored source IR, bridge, and shared shell against 181 entries.
MANY_ENTRIES = '''() => {
  const roots=[{id:'workspace',label:'Workspace'},{id:'archive',label:'Archive'}];
  const entries=Array.from({length:181},(_,i)=>({kind:'file',
    name:'record-'+String(i).padStart(3,'0')+'.txt',
    relative_path:'record-'+String(i).padStart(3,'0')+'.txt',bytes:i+1}));
  FileExplorerSemanticAdapter.setTransport(async (path,payload)=>{
    if(path.endsWith('/roots'))return {ok:true,roots};
    if(path.endsWith('/list'))return {ok:true,root_id:payload.root_id,
      relative_path:payload.relative_path||'', count:entries.length,entries};
    if(path.endsWith('/search'))return {ok:true,query:payload.query,count:0,results:[]};
    if(path.endsWith('/read'))return {ok:true,readable:true,encoding:'utf-8',
      content:'Fixture preview',entry:{name:payload.relative_path,kind:'file',relative_path:payload.relative_path}};
    throw Error('unknown operation');
  });
}'''

FILE_CASE = '''async ({profile,planner}) => {
  const root=document.getElementById('file-explorer-app');
  const mounted=McelFileExplorerCandidate.mount(root,{presentation:profile,planner});
  const deadline=performance.now()+3500;
  while((McelFileExplorerCandidate.status()?.domain?.entries||[]).length!==181 && performance.now()<deadline)
    await new Promise(resolve=>setTimeout(resolve,15));
  const shell=root.querySelector('.mcel-application-shell');
  const rowRegion=root.querySelector('[data-mcel-region="region:entries"]');
  const frame=rowRegion?.querySelector('.mcel-application-shell__table-scroll');
  const rows=[...rowRegion.querySelectorAll('tbody tr')];
  const focused=rows[5]?.querySelector('[data-mcel-row-select]');
  focused?.focus();
  const oldKey=rows[5]?.dataset.mcelRowKey;
  const originalRole=rowRegion?.dataset.mcelRole;
  const originalProminence=rowRegion?.dataset.mcelProminence;
  const initialHeight=frame?.getBoundingClientRect().height;
  const initialRowHeight=rows[5]?.getBoundingClientRect().height;
  const oldNode=rows[5];
  const select=root.querySelector('[data-mcel-presentation-picker]');
  const other=profile==='analytical'?'operational':'analytical';
  select.value=other;select.dispatchEvent(new Event('change',{bubbles:true}));
  const switchedPlan=rowRegion?.dataset.mcelPlanRole;
  select.value=profile;select.dispatchEvent(new Event('change',{bubbles:true}));
  const retained=oldNode===rowRegion.querySelectorAll('tbody tr')[5] && document.activeElement===focused;
  const before=McelFileExplorerCandidate.status().version;
  root.querySelector('[data-mcel-shell-action="refresh-directory"]').click();
  while(McelFileExplorerCandidate.status().version<=before && performance.now()<deadline)
    await new Promise(resolve=>setTimeout(resolve,15));
  const after=McelFileExplorerCandidate.status();
  const rowCount=rowRegion.querySelectorAll('tbody tr').length;
  const stillFocused=document.activeElement===focused;
  const result={mounted:mounted.ok,planner:shell?.dataset.mcelPlanner,
    profile:shell?.dataset.mcelPresentation,rows:rowCount,
    legacyHidden:root.querySelector('.file-explorer-shell')?.hidden,
    receipt:after.operation?.phase,readOnly:after.domain?.readOnly,
    role:originalRole,prominence:originalProminence,
    planRole:rowRegion?.dataset.mcelPlanRole,planDensity:rowRegion?.dataset.mcelPlanDensity,
    planExtent:rowRegion?.dataset.mcelPlanExtent,switchedPlan,retained,stillFocused,
    rowKeyPreserved:rowRegion.querySelectorAll('tbody tr')[5]?.dataset.mcelRowKey===oldKey,
    frameHeight:Math.round(initialHeight),rowHeight:Math.round(initialRowHeight),
    visibleRows:Math.floor(initialHeight/(initialRowHeight||1)),
    docWidth:document.documentElement.scrollWidth,viewport:innerWidth};
  return result;
}'''

TASK_CASE = '''({profile,planner}) => {
  const root=document.getElementById('task-manager-app');
  const search='?mcel_task_notebook=application&mcel_design='+profile+
    (planner==='task-aware'?'&mcel_planner=task-aware':'');
  const first=McelTaskManagerNotebookLab.optInHost(root,search);
  const proc=(pid)=>({pid,name:'monitor-'+pid,created_at:'stable',memory_rss:100,
    memory_availability:'present',status:'running',status_availability:'present',cmdline:'program'});
  const snapshot={server:{pid:11,running:true},overview:{main_computer_process_count:181,
    connection_count:0,schedule_count:1},hardware:{cpu:{overall_percent:3},gpu:{overall_percent:0}},
    processes:Array.from({length:181},(_,i)=>proc(1000+i)),all_processes:[],connections:[],
    schedules:[{action:'monitor',run_at:'tomorrow',note:'Read only'}],aiBrief:'Monitoring'};
  const published=McelTaskManagerNotebookLab.publish(snapshot,'available',1);
  const shell=root.querySelector('.mcel-application-shell');
  const workspace=shell?.querySelector('[data-mcel-region="region:notebook"]');
  const role=workspace?.dataset.mcelRole, prominence=workspace?.dataset.mcelProminence;
  const select=shell?.querySelector('[data-mcel-presentation-picker]');
  const switched=profile==='operational'?'analytical':'operational';
  select.value=switched;select.dispatchEvent(new Event('change',{bubbles:true}));
  const same=workspace===shell.querySelector('[data-mcel-region="region:notebook"]');
  select.value=profile;select.dispatchEvent(new Event('change',{bubbles:true}));
  return {mounted:first.ok,published:published.ok,
    planner:shell?.dataset.mcelPlanner,role,prominence,
    planRole:workspace?.dataset.mcelPlanRole,
    same,profile:shell?.dataset.mcelPresentation,
    docWidth:document.documentElement.scrollWidth,viewport:innerWidth};
}'''

def hosted_url(url, app, profile):
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query))
    query['mcel_planner']='task-aware';query['mcel_design']=profile
    query['mcel_task_notebook' if app=='task-manager' else 'mcel_file_explorer'] = (
        'application' if app=='task-manager' else 'candidate')
    return urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(query),parsed.fragment))


def run(output=OUT,task_manager_url=None,file_explorer_url=None):
    from playwright.sync_api import sync_playwright
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    report={'schema':'mcel.promise-wave7a-task-aware-planner.v1',
       'generatedUtc':datetime.now(timezone.utc).isoformat(),
       'offline':'FAIL','hosted':'NOT_REQUESTED','cases':[], 'hostCases':[],
       'humanVisualReview':'NOT_ACCEPTED',
       'comparativeLaborAccounting':'NOT_APPLICABLE',
       'sourceChanges':False, 'authorityChanges':False}
    chromium=shutil.which('chromium') or shutil.which('chromium-browser')
    kw={'headless':True}
    if chromium:kw.update(executable_path=chromium,args=['--no-sandbox','--disable-dev-shm-usage'])
    with sync_playwright() as p:
        browser=p.chromium.launch(**kw)
        try:
            for app in ('file-explorer','task-manager'):
                for profile in PROFILES:
                    for width in WIDTHS:
                        metrics={}
                        for planner in ('legacy','task-aware'):
                            page=browser.new_page(viewport={'width':width,'height':820},reduced_motion='reduce')
                            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                            if app=='file-explorer':
                                page.set_content(HTML)
                                page.add_style_tag(content=STYLE.read_text(encoding='utf-8'))
                                for name in SCRIPTS:page.add_script_tag(content=(JS_ROOT/name).read_text(encoding='utf-8'))
                                page.evaluate(TRANSPORT)
                                page.evaluate(MANY_ENTRIES)
                                data=page.evaluate(FILE_CASE,{'profile':profile,'planner':planner})
                            else:
                                page.set_content(html_fixture())
                                data=page.evaluate(TASK_CASE,{'profile':profile,'planner':planner})
                            if width in (390,1100) and planner=='task-aware':
                                page.screenshot(path=str(output/f'{app}-{profile}-{width}.png'),full_page=True)
                            if app=='file-explorer':
                                page.evaluate('''() => McelFileExplorerCandidate.destroy()''')
                                data['restored']=page.evaluate('''() => !document.querySelector('#file-explorer-app .file-explorer-shell')?.hidden''')
                            data['browserErrors']=errors
                            metrics[planner]=data
                            page.close()
                        old=metrics['legacy'];new=metrics['task-aware']
                        checks={'baseline-functional':old['mounted'] and old['docWidth']<=width+2 and not old['browserErrors'],
                          'candidate-functional':new['mounted'] and new['docWidth']<=width+2 and not new['browserErrors'],
                          'no-regression-roles':new['role']==old['role'] and new['prominence']==old['prominence'],
                          'opt-in':old['planner']=='legacy' and new['planner']=='task-aware',
                          'switch-without-remount':new['same'] if app=='task-manager' else new['retained'] and new['stillFocused'],
                          'meaningful-role':bool(new['planRole']),
                          'expected-profile':new['profile']==profile}
                        if app=='file-explorer':checks.update({
                          'source-entries-parity':old['rows']==new['rows']==181,
                          'read-receipts':new['receipt']=='completed' and new['readOnly'] is True,
                          'identity-preserved':new['rowKeyPreserved'],
                          'rollback':new['restored'],
                          'more-usable-primary-collection':new['visibleRows']>old['visibleRows']})
                        else:checks['snapshot-published']=old['published'] and new['published']
                        report['cases'].append({'app':app,'profile':profile,'width':width,
                          'status':'PASS' if all(checks.values()) else 'FAIL',
                          'checks':checks,'legacy':old,'candidate':new})
            if task_manager_url or file_explorer_url:
                report['hosted']='PASS'
                for app,url in [('task-manager',task_manager_url),('file-explorer',file_explorer_url)]:
                    if not url:continue
                    for profile in PROFILES:
                        page=browser.new_page(viewport={'width':1100,'height':820})
                        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                        case={'app':app,'profile':profile,'status':'BLOCKED','checks':{},'browserErrors':errors}
                        try:
                            response=page.goto(hosted_url(url,app,profile),wait_until='domcontentloaded',timeout=20000)
                            page.wait_for_function('''() => document.querySelector('.mcel-application-shell[data-mcel-planner="task-aware"]')!==null''',timeout=13000)
                            if app=='file-explorer':page.wait_for_function('''() => McelFileExplorerCandidate.status().domain?.phase==='ready' ''',timeout=13000)
                            data=page.evaluate('''() => {
                              const shell=document.querySelector('.mcel-application-shell');
                              const entries=document.querySelector('[data-mcel-region="region:entries"]');
                              return {planner:shell?.dataset.mcelPlanner,
                                profile:shell?.dataset.mcelPresentation,
                                enhancedRegions:shell?.querySelectorAll('[data-mcel-plan-role]').length,
                                regions:shell?.querySelectorAll('[data-mcel-region]').length,
                                rootCount:document.querySelectorAll('[data-mcel-region="region:roots"] tbody tr').length,
                                displayedEntries:entries?.querySelectorAll('tbody tr').length,
                                sourceEntries:window.McelFileExplorerCandidate?.status()?.domain?.entries?.length,
                                documentWidth:document.documentElement.scrollWidth,viewport:innerWidth};
                            }''')
                            checks={'http200':response is not None and response.status==200,
                              'candidate-enabled':data['planner']=='task-aware',
                              'profile':data['profile']==profile,
                              'all-regions-enhanced':data['regions']>0 and data['regions']==data['enhancedRegions'],
                              'no-document-overflow':data['documentWidth']<=1102,
                              'no-errors':not errors}
                            if app=='file-explorer':checks['entry-parity']=data['displayedEntries']==data['sourceEntries']
                            case.update(checks=checks,evidence=data,status='PASS' if all(checks.values()) else 'FAIL')
                        except Exception as exc:case['reason']=str(exc)[:600]
                        finally:page.close()
                        report['hostCases'].append(case)
                report['hosted']='PASS' if all(c['status']=='PASS' for c in report['hostCases']) else 'FAIL'
        finally:browser.close()
    report['offline']='PASS' if all(c['status']=='PASS' for c in report['cases']) else 'FAIL'
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('MCEL_PROMISE_WAVE7A_'+report['offline'],'hosted',report['hosted'])
    for case in report['cases']:
        if case['status']!='PASS':print(case['app'],case['profile'],case['width'],'FAIL',
            [name for name,val in case['checks'].items() if not val])
    for case in report['hostCases']:
        if case['status']!='PASS':print('host',case['app'],case['profile'],case['status'],case.get('reason',''))
    print('report',output/'report.json')
    return 0 if report['offline']=='PASS' and report['hosted'] in ('PASS','NOT_REQUESTED') else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=OUT)
    parser.add_argument('--task-manager-url')
    parser.add_argument('--file-explorer-url')
    args=parser.parse_args()
    raise SystemExit(run(args.output,args.task_manager_url,args.file_explorer_url))
