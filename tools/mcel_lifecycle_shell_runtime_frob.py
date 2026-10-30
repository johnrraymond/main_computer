"""Verify the *application-shell* MCEL runtime wiring used by the flat L18 gallery.

Unlike the older acid Frob, this reads script includes from applications.html,
so it cannot silently insert modules that the actual application forgot to load.
The browser body is a minimal read-only host; this is not an HTTP deployment test.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from playwright.sync_api import sync_playwright
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome, SCRIPTS
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

ROOT=Path(__file__).resolve().parents[1]
SHELL=ROOT/'main_computer/web/applications.html'
OUT=ROOT/'runtime/reports/mcel-lifecycle-shell-runtime'
SCHEMA='mcel.lifecycle-shell-runtime-verification.v1'
REQUIRED=('mcel-layout-continuity.js','mcel-presentation-negotiation.js',
 'mcel-presentation-hosts.js','mcel-observation-integrity.js',
 'mcel-universal-semantic-layout-lab.js','mcel-lifecycle-acid-fixtures.js',
 'mcel-lifecycle-acid-lab.js')

def shell_includes():
    return re.findall(r'<!--\s*@include\s+applications/scripts/(mcel-[\w-]+\.js)\s*-->',SHELL.read_text('utf-8'))

def verify(output:Path=OUT):
    output.mkdir(parents=True,exist_ok=True)
    includes=shell_includes()
    missing=[name for name in REQUIRED if includes.count(name)!=1]
    if missing: raise AssertionError('APPLICATION_SHELL_MISSING_MCEL_RUNTIME:'+','.join(missing))
    order=[includes.index(name) for name in REQUIRED]
    if order != sorted(order): raise AssertionError('APPLICATION_SHELL_BAD_RUNTIME_ORDER')
    result={'schema':SCHEMA,'generatedUtc':datetime.now(timezone.utc).isoformat(),
      'shell':'main_computer/web/applications.html',
      'requiredIncludes':list(REQUIRED),'cases':[], 'browserErrors':[], 'status':'UNVERIFIED',
      'scope':'Actual application-shell include wiring with offline Task Manager fixture body; not production HTTP'}
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for width in (390,1440):
                page=browser.new_page(viewport={'width':width,'height':900},reduced_motion='reduce')
                errors=[]
                page.on('pageerror',lambda exc:errors.append(str(exc)))
                try:
                    page.set_content(html_fixture(),wait_until='load')
                    # Critical difference from prior Frobs: load *only* modules
                    # that the real application shell actually includes.
                    for name in sorted(REQUIRED,key=lambda n:includes.index(n)):
                        page.add_script_tag(path=str(SCRIPTS/name))
                    case=page.evaluate('''async()=>{
                        const names=['McelLayoutContinuity','McelPresentationNegotiation',
                          'McelPresentationHosts','McelObservationIntegrity',
                          'McelUniversalSemanticLayoutLab','McelLifecycleAcidFixtures',
                          'McelLifecycleAcidLab'];
                        const globals=Object.fromEntries(names.map(n=>[n,!!window[n]]));
                        const gallery=document.createElement('main');document.body.append(gallery);
                        const cases=[];
                        try{
                          for(const test of McelLifecycleAcidLab.EXPERIMENTS){
                            const slot=document.createElement('section');gallery.append(slot);
                            try{
                              const plan=McelLifecycleAcidLab.makePlan(test.settings.data,test.settings.identity);
                              const instance=McelUniversalSemanticLayoutLab.mount(slot,plan,
                                {lab:true,presentationNegotiation:true,sourceRetirement:true});
                              cases.push({id:test.id,ok:true,rows:slot.querySelectorAll('mcel-row').length,
                                sourceIds:instance.collection.ids.length,layout:!!instance.collection.layout,
                                canonical:instance.tree.refresh().status});
                              instance.destroy();
                            }catch(e){cases.push({id:test.id,ok:false,error:String(e)});}
                          }
                        }finally{gallery.remove();}
                        return {globals,cases};
                    }''')
                    case.update(width=width,browserErrors=errors)
                    result['cases'].append(case)
                    result['browserErrors'].extend(errors)
                finally:page.close()
        finally:browser.close()
    result['status']='PASS' if len(result['cases'])==2 and not result['browserErrors'] and all(
      all(case['globals'].values()) and len(case['cases'])==18 and all(
       row['ok'] and row['rows']==row['sourceIds'] and row['rows']>0 and row['layout'] and row['canonical']=='pass'
       for row in case['cases']) for case in result['cases']) else 'FAIL'
    (output/'report.json').write_text(json.dumps(result,indent=2)+'\n','utf-8')
    print('MCEL_LIFECYCLE_SHELL_RUNTIME_'+result['status'])
    for case in result['cases']:
        broken=[row for row in case['cases'] if not row['ok'] or not row.get('layout')]
        print(str(case['width'])+'px',len(case['cases'])-len(broken),'/18 mounted', 'errors:',broken[:2],case['browserErrors'][:2])
    print('report:',output/'report.json')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=OUT)
    args=parser.parse_args()
    raise SystemExit(0 if verify(args.output)['status']=='PASS' else 1)
