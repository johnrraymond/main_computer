#!/usr/bin/env python3
"""Patch 04: genuine dual-design, one-IR, usable generated application acceptance.

Offline measurements use the browser-delivered *existing* IR, not a fabricated DSL.
Hosted measurements use the real localhost Task Manager. Aesthetic review and
labor comparisons are separately reported; neither is manufactured from geometry.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
from tools.mcel_task_notebook_candidate_bundle import SOURCE, TARGET, build
from tools.mcel_promise_wave3_frob import source_process_parity

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT/'runtime/reports/mcel-promise-wave4'
WIDTHS = (320, 390, 560, 1100, 1272)
PROFILES = ('operational','analytical')


def source_status():
    """Do not reconstruct missing author-owned source from the compiled artifact."""
    if not TARGET.is_file():
        return {'status':'FAIL','reason':'browser-delivered candidate missing'}
    artifact = TARGET.read_bytes()
    result = {'status':'BLOCKED','reason':'authored DSL source omitted from provided export',
              'browserArtifactSha256':hashlib.sha256(artifact).hexdigest(),
              'sourcePath':str(SOURCE.relative_to(ROOT))}
    if SOURCE.is_file():
        try:
            expected, meta = build()
            result.update(status='PASS' if expected.encode()==artifact else 'FAIL',
                          reason='compiled source matches browser artifact' if expected.encode()==artifact else 'source-bound artifact mismatch',
                          sourceSha256=meta['sourceSha256'])
        except Exception as exc:
            result.update(status='FAIL',reason=str(exc))
    return result


def authoring_economy():
    """Observable inputs, not ungrounded claims about developer labor/time."""
    candidate = TARGET.read_text(encoding='utf-8')
    # Compiled artifact is emitted from the original semantic source. This is
    # evidence of declarations present, not the count of author decisions.
    match = re.search(r'  ir: (\{.*\})\s*\n\}\);',candidate,re.S)
    metrics = {'sourceAvailable':SOURCE.is_file(), 'additionalAppSemanticDefinitionsForSecondDesign':0,
               'additionalAppSpecificCSSForSecondDesign':0,
               'additionalDomainAdaptersForSecondDesign':0,
               'designSelectionValues':['operational','analytical'],
               'humanEffortComparison':'UNVERIFIED', 'firstRenderAestheticJudgment':'UNVERIFIED',
               'status':'PARTIAL'}
    if match:
        ir=json.loads(match.group(1));shell=next(s['semanticSurface']['applicationShell'] for s in ir['surfaces']
            if 'applicationShell' in s.get('semanticSurface',{}))
        regions=shell['regions']
        metrics.update(semanticRegionCount=len(regions),
            semanticFieldCount=sum(len(r.get('fields',[])) for r in regions),
            semanticReadBindingCount=sum(len(r.get('items',[])) for r in regions),
            declaredShellActionCount=sum(len(r.get('actions',[])) for r in regions),
            authorOwnedIRUnchanged=True)
    if SOURCE.is_file():
        metrics['authoredSourceLines']=len(SOURCE.read_text(encoding='utf-8').splitlines())
    return metrics


def run(output:Path=OUTPUT, host_url:str|None=None):
    from playwright.sync_api import sync_playwright
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    report={'schema':'mcel.promise-wave4-intelligent-design.v1',
            'generatedUtc':datetime.now(timezone.utc).isoformat(),
            'source':source_status(),'economy':authoring_economy(),
            'offline':'FAIL','hosted':'BLOCKED','hostReason':'localhost not requested',
            'humanVisualReview':'UNVERIFIED','humanLaborComparison':'UNVERIFIED',
            'profiles':list(PROFILES),'widths':list(WIDTHS),'cases':[],
            'operatorMutationsAttempted':False,
            'admittedOperations':['external-read:refresh-process-data']}
    executable=shutil.which('chromium') or shutil.which('chromium-browser')
    with sync_playwright() as playwright:
        kwargs={'headless':True}
        if executable:kwargs.update(executable_path=executable,args=['--no-sandbox','--disable-dev-shm-usage'])
        browser=playwright.chromium.launch(**kwargs)
        try:
            for profile in PROFILES:
                for width in WIDTHS:
                    page=browser.new_page(viewport={'width':width,'height':800},reduced_motion='reduce')
                    errors=[];requests=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.on('request',lambda req:requests.append(req.url))
                    page.set_content(html_fixture(),wait_until='load')
                    result=page.evaluate('''async profile => {
                      const root=document.getElementById('task-manager-app');
                      let calls=0;
                      const proc=(pid,mem)=>({pid,name:'operator-'+pid,created_at:'stable-id',
                        memory_rss:mem,memory_availability:'present',status:'running',status_availability:'present',
                        cmdline:'/bin/program',is_current_process:false,is_main_computer:false});
                      const snapshot=mem=>({server:{running:true,pid:200},
                        overview:{main_computer_process_count:2,connection_count:0,schedule_count:1},
                        schedules:[{action:'check',run_at:'future',note:'read-only'}],
                        hardware:{cpu:{overall_percent:16},gpu:{overall_percent:4}},
                        processes:[proc(101,mem),proc(102,0)],all_processes:[proc(101,mem)],connections:[],
                        aiBrief:'Read only'});
                      window.refreshTaskManager=async()=>{calls++;
                        McelTaskManagerNotebookLab.publish(snapshot(200),'available',undefined,
                         {streams:{processes:'available',all_processes:'available',connections:'empty',hardware:'available'}});};
                      const mounted=McelTaskManagerNotebookLab.optInHost(root,
                        '?mcel_task_notebook=application&mcel_design='+profile);
                      const shell=root.querySelector('.mcel-application-shell');
                      if(!shell)return {mounted,failed:'shell-not-mounted'};
                      const candidate=McelTaskManagerNotebookCandidate;
                      const sourceIdentity=candidate.metadata.sourceSha256;
                      const before=McelTaskManagerNotebookLab.publish(snapshot(100),'available',1);
                      const row=shell.querySelector('[data-mcel-panel="server-processes"] mcel-row[data-row-id="101"]');
                      const schedule=shell.querySelector('[data-mcel-region="region:schedules"] tbody tr');
                      const select=shell.querySelector('[data-mcel-presentation-picker]');
                      const regionNodes=[...shell.querySelectorAll('[data-mcel-region]')];
                      const roles=regionNodes.map(r=>r.dataset.mcelRole);
                      const widthBefore=document.documentElement.scrollWidth;
                      const primary=regionNodes.filter(r=>r.dataset.mcelProminence==='primary');
                      const firstPositions={main:shell.querySelector('.mcel-application-shell__main').getBoundingClientRect().left,
                        sidebar:shell.querySelector('.mcel-application-shell__sidebar').getBoundingClientRect().left};
                      row.tabIndex=0;row.focus();
                      shell.querySelector('[data-mcel-field="query"]').value='operator';
                      shell.querySelector('[data-mcel-field="query"]').dispatchEvent(new Event('input',{bubbles:true}));
                      const formBefore=McelTaskManagerNotebookLab.status().applicationShell.form;
                      const refresh=shell.querySelector('[data-mcel-shell-action="refresh"]');
                      const beforeVersion=McelTaskManagerNotebookLab.status().version;
                      refresh.click();
                      const deadline=performance.now()+2000;
                      while(McelTaskManagerNotebookLab.status().operation?.pending && performance.now()<deadline)
                        await new Promise(resolve=>setTimeout(resolve,10));
                      const after=McelTaskManagerNotebookLab.status();
                      const sameRow=row===shell.querySelector('[data-mcel-panel="server-processes"] mcel-row[data-row-id="101"]');
                      const sameSchedule=schedule===shell.querySelector('[data-mcel-region="region:schedules"] tbody tr');
                      const oldProfile=shell.dataset.mcelPresentation;
                      select.value=profile==='analytical'?'operational':'analytical';
                      select.dispatchEvent(new Event('change',{bubbles:true}));
                      const switched=shell.dataset.mcelPresentation;
                      const preserved=row===shell.querySelector('[data-mcel-panel="server-processes"] mcel-row[data-row-id="101"]')
                        && document.activeElement===row;
                      const formAfter=McelTaskManagerNotebookLab.status().applicationShell.form;
                      const changesSameSource=McelTaskManagerNotebookCandidate.metadata.sourceSha256===sourceIdentity;
                      const denied=[...shell.querySelectorAll('[data-mcel-shell-action^="server-"]')].every(b=>b.disabled);
                      // Restore selected presentation so screenshot checks the requested profile.
                      select.value=profile;select.dispatchEvent(new Event('change',{bubbles:true}));
                      const layout=document.querySelector('.mcel-application-shell__layout');
                      const columns=getComputedStyle(layout).gridTemplateColumns;
                      const background=getComputedStyle(shell).backgroundColor;
                      const geometry={width:innerWidth,documentWidth:document.documentElement.scrollWidth,
                        shellWidth:shell.getBoundingClientRect().width,initialDocumentWidth:widthBefore,
                        mainLeft:Math.round(firstPositions.main),sidebarLeft:Math.round(firstPositions.sidebar),
                        background,columns};
                      return {mounted:mounted.ok, before:before.ok,profile:oldProfile,switched,
                        sourceIdentity,changesSameSource,regions:regionNodes.length,roles,primary:primary.length,
                        calls,afterVersion:after.version,beforeVersion,phase:after.operation?.phase,
                        sameRow,sameSchedule,preserved,formPreserved:formAfter.query===formBefore.query,
                        denied,geometry};
                    }''',profile)
                    geometry=result.get('geometry',{})
                    checks={
                        'shell-mounted':result.get('mounted') is True and result.get('before') is True,
                        'expected-presentation':result.get('profile')==profile,
                        'seven-semantic-regions':result.get('regions')==7,
                        'semantic-role-inference':'workspace' in result.get('roles',[]) and result.get('primary')==1,
                        'same-author-owned-IR':result.get('changesSameSource') is True,
                        'operational-receipt':result.get('calls')==1 and result.get('phase')=='completed' and result.get('afterVersion',0)>result.get('beforeVersion',0),
                        'stable-live-rows':result.get('sameRow') is True and result.get('sameSchedule') is True,
                        'switch-without-remount':result.get('preserved') is True and result.get('formPreserved') is True and result.get('switched')!=profile,
                        'machine-operations-not-admitted':result.get('denied') is True,
                        'no-document-horizontal-overflow':geometry.get('documentWidth',1e9)<=width+2 and geometry.get('shellWidth',1e9)<=width+2,
                        'wide-composition-or-mobile-stack': (geometry.get('mainLeft',-1)>geometry.get('sidebarLeft',1e9)
                          if profile=='operational' else geometry.get('mainLeft',1e9)<geometry.get('sidebarLeft',-1)) if width>=1100
                          else abs(geometry.get('mainLeft',1e9)-geometry.get('sidebarLeft',-1))<=2,
                        'no-console-errors-or-network':not errors and not requests}
                    case={'profile':profile,'width':width,'status':'PASS' if all(checks.values()) else 'FAIL',
                          'checks':checks,'geometry':geometry,'browserErrors':errors,'networkRequests':requests}
                    report['cases'].append(case)
                    if width in (390,1100):
                        page.screenshot(path=str(output/f'{profile}-{width}.png'),animations='disabled',full_page=True)
                    page.close()
            # Confirm the two modes change actual computed style rather than mere labels.
            contrast=[]
            for width in WIDTHS:
                a=next(c for c in report['cases'] if c['width']==width and c['profile']=='operational')
                b=next(c for c in report['cases'] if c['width']==width and c['profile']=='analytical')
                contrast.append({'width':width,'distinctBackground':a['geometry']['background']!=b['geometry']['background'],
                    'distinctWideLayout':True if width<1100 else
                        a['geometry']['mainLeft']>a['geometry']['sidebarLeft'] and
                        b['geometry']['mainLeft']<b['geometry']['sidebarLeft']})
            report['designContrast']=contrast
            # Different application, same public shell and presentation selection.
            page=browser.new_page(viewport={'width':920,'height':720});page.set_content(html_fixture())
            report['independentApp']=page.evaluate('''() => {
              const ir={application:{title:'Field Journal'},intents:[],surfaces:[{
                semanticSurface:{applicationShell:{schema:'mcel.semantic-application-shell.v1',
                  layout:'sidebar-workspace',id:'journal',label:'Field Journal',regions:[
                    {id:'region:summary',zone:'sidebar',kind:'summary',title:'Overview',items:[{id:'count',label:'Entries',path:'counts.entries'}]},
                    {id:'region:records',zone:'sidebar',kind:'collection',title:'Records',path:'records',columns:['title','status']},
                    {id:'region:notebook',zone:'main',kind:'slot',slot:'notebook',title:'Journal'}
                  ]}}}]};
              const host=document.createElement('div'),slot=document.createElement('div');
              slot.textContent='Journal records';document.body.append(host);
              const app=McelSemanticApplicationShell.mount(host,ir,{slots:{notebook:slot},presentation:'analytical'});
              const a=app.update({counts:{entries:2},records:[{title:'First',status:'open'}]});
              const s=host.querySelector('.mcel-application-shell');
              const node=host.querySelector('[data-mcel-region="region:notebook"]');
              const b=app.setPresentation('operational');
              const good=a.ok && b.ok && app.getState().presentation==='operational' &&
                host.querySelector('[data-mcel-region="region:notebook"]')===node &&
                host.querySelector('[data-mcel-binding="count"]').textContent==='2';
              app.destroy();return {good,unmounted:host.childElementCount===0};
            }''')
            page.close()
            # Content stress must actually affect rendered output, not a test-only flag.
            page=browser.new_page(viewport={'width':320,'height':800});page.set_content(html_fixture())
            report['longText']=page.evaluate('''() => {
              const root=document.getElementById('task-manager-app');
              const mounted=McelTaskManagerNotebookLab.optInHost(root,'?mcel_task_notebook=application&mcel_design=analytical');
              const long='unbroken'+('X'.repeat(1600));
              const data={server:{pid:long,running:true},overview:{},processes:[],all_processes:[],connections:[],
                hardware:{},schedules:[{action:'audit',run_at:'future',note:long}],aiBrief:long};
              const published=McelTaskManagerNotebookLab.publish(data,'available',1);
              const shell=root.querySelector('.mcel-application-shell');
              const binding=shell.querySelector('[data-mcel-binding="pid"]')?.textContent;
              const note=shell.querySelector('[data-mcel-region="region:schedules"] td:last-child')?.textContent;
              const width=document.documentElement.scrollWidth;
              return {mounted:mounted.ok,published:published.ok,contentChanged:binding===long && note===long,
                documentWidth:width,viewport:innerWidth,scrollOwner:shell.querySelector('.mcel-application-shell__table-scroll')?.scrollWidth>0};
            }''')
            page.close()
            report['offline']='PASS' if all(c['status']=='PASS' for c in report['cases']) and \
                all(c['distinctBackground'] and c['distinctWideLayout'] for c in report['designContrast']) and \
                report['independentApp']['good'] and report['independentApp']['unmounted'] and \
                report['longText']['mounted'] and report['longText']['published'] and \
                report['longText']['contentChanged'] and report['longText']['documentWidth']<=322 else 'FAIL'
            if host_url:
                report['hosted']='PASS';report['hostCases']=[]
                for profile in PROFILES:
                    page=browser.new_page(viewport={'width':1100,'height':800});errors=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    parsed=urlsplit(host_url);query=dict(parse_qsl(parsed.query));
                    query.update(mcel_task_notebook='application',mcel_design=profile)
                    url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(query),parsed.fragment))
                    try:
                        response=page.goto(url,wait_until='domcontentloaded',timeout=20000)
                        page.wait_for_function('''() => window.McelTaskManagerNotebookLab?.status()?.mode==='semantic-application'
                          && window.McelTaskManagerNotebookLab?.status()?.version>0''',timeout=25000)
                        result=page.evaluate('''async() => {
                          const shell=document.querySelector('.mcel-application-shell');
                          const before=McelTaskManagerNotebookLab.status();
                          const click=shell.querySelector('[data-mcel-shell-action="refresh"]');click.click();
                          const limit=performance.now()+25000;
                          while(McelTaskManagerNotebookLab.status().operation?.pending && performance.now()<limit)
                            await new Promise(resolve=>setTimeout(resolve,80));
                          const after=McelTaskManagerNotebookLab.status();
                          const source=typeof taskManagerSnapshotCache==='undefined'?null:taskManagerSnapshotCache;
                          const processPanel=shell.querySelector('[data-mcel-panel="server-processes"]');
                          const allPanel=shell.querySelector('[data-mcel-panel="all-processes"]');
                          const rows=panel=>panel.querySelectorAll('mcel-row').length;
                          const empty=panel=>panel.querySelector('.mcel-tab-workspace__notice')?.textContent||'';
                          return {profile:shell.dataset.mcelPresentation,before:before.version,after:after.version,
                            phase:after.operation?.phase,oldHidden:document.querySelector('.task-manager-shell').hidden,
                            sourceProcesses:Array.isArray(source?.processes)?source.processes.length:null,
                            processes:rows(processPanel),serverEmpty:empty(processPanel),
                            sourceAllProcesses:Array.isArray(source?.all_processes)?source.all_processes.length:null,
                            allProcesses:rows(allPanel),allEmpty:empty(allPanel),
                            docWidth:document.documentElement.scrollWidth,viewport:innerWidth};
                        }''')
                        checks={'http200':response is not None and response.status==200,
                          'correct-profile':result['profile']==profile,'legacy-hidden':result['oldHidden'],
                          'operation-completed':result['phase']=='completed' and result['after']>result['before'],
                          'source-process-parity':source_process_parity(result['sourceProcesses'],result['processes'],result['serverEmpty']),
                          'source-all-process-parity':source_process_parity(result['sourceAllProcesses'],result['allProcesses'],result['allEmpty']),
                          'no-overflow':result['docWidth']<=result['viewport']+2,'no-browser-errors':not errors}
                        case={'profile':profile,'status':'PASS' if all(checks.values()) else 'FAIL',
                              'checks':checks,'evidence':result,'browserErrors':errors}
                        page.screenshot(path=str(output/f'hosted-{profile}.png'),animations='disabled',full_page=True)
                    except Exception as exc:
                        case={'profile':profile,'status':'BLOCKED','reason':str(exc),'browserErrors':errors}
                    finally:
                        page.close()
                    report['hostCases'].append(case)
                report['hosted']='PASS' if all(c['status']=='PASS' for c in report['hostCases']) else \
                   'FAIL' if any(c['status']=='FAIL' for c in report['hostCases']) else 'BLOCKED'
        finally:
            browser.close()
    report['acceptance']='PASS' if report['offline']=='PASS' and report['hosted']=='PASS' and \
        report['source']['status']=='PASS' and report['humanVisualReview']=='PASS' and \
        report['humanLaborComparison']=='PASS' else 'INCOMPLETE'
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('MCEL_PROMISE_WAVE4_'+report['offline'],'hosted',report['hosted'],
          'full-acceptance',report['acceptance'],'source',report['source']['status'])
    for c in report['cases']:
        print(c['profile'],c['width'],c['status'],[k for k,v in c['checks'].items() if not v])
    print('independent app',report['independentApp'],'long text',report['longText'])
    print('report',output/'report.json')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--host-url')
    args=parser.parse_args()
    result=run(args.output,args.host_url)
    raise SystemExit(0 if result['offline']=='PASS' and result['source']['status']!='FAIL'
        and (not args.host_url or result['hosted']=='PASS') else 1)
