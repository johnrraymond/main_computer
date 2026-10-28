"""Patch 13 stage 3: read-only same-response live adoption acceptance.

Without --host-url, a real local TaskManagerService producer and browser fixture
are exercised. Hosted parity, File Explorer and Worker routes, and Patch 10
hosted lifecycle remain explicit NOT_RUN until a local Main Computer host is
supplied; they are never silently promoted to PASS.

Examples:
 python -m tools.mcel_patch13_live_adoption_frob
 python -m tools.mcel_patch13_live_adoption_frob --host-url "http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import unittest
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen

from main_computer.mcel_knowledge.live_adoption import build_live_shadow
from main_computer.task_manager import TaskManagerService
from tests.test_mcel_patch13_live_shadow import BASE, SCOPE, LiveStage3Tests
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'runtime/reports/mcel-patch13-live-adoption.json'
SHADOW_SCRIPT=ROOT/'main_computer/web/applications/scripts/mcel-task-manager-semantic-shadow.js'


def _chrome(playwright, specified):
    path=specified or shutil.which('chromium') or shutil.which('google-chrome')
    if path:
        return playwright.chromium.launch(headless=True,executable_path=path,args=['--no-sandbox'])
    return playwright.chromium.launch(headless=True)


def offline_browser(chromium_path=None):
    from playwright.sync_api import sync_playwright
    source={'processes':[BASE,dict(BASE,pid=813,memory_rss=None,memory_availability='unavailable')]}
    second={'processes':[dict(BASE, name='python-new',memory_rss=1024),source['processes'][1]]}
    cases={}
    with sync_playwright() as pw:
        browser=_chrome(pw,chromium_path)
        try:
            page=browser.new_page(viewport={'width':1100,'height':800})
            errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.set_content(html_fixture(),wait_until='domcontentloaded')
            page.add_script_tag(path=str(SHADOW_SCRIPT))
            cases['default-not-opted-in']=page.evaluate('McelTaskManagerSemanticShadow.enabled') is False
            cases['no-default-visual-or-control-changes']=page.locator('.mcel-task-semantic-shadow').count()==0
            page.close()
            page=browser.new_page(viewport={'width':1100,'height':800})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.set_content(html_fixture(),wait_until='domcontentloaded')
            # Test harness only: about:blank has no query string, so emulate
            # exactly the opt-in key without requiring external navigation.
            page.evaluate('''() => { const prior=URLSearchParams.prototype.get;
              URLSearchParams.prototype.get=function(key){return key==='mcel_semantic_shadow'?'lab':prior.call(this,key)};
            }''')
            page.add_script_tag(path=str(SHADOW_SCRIPT))
            before_controls=page.locator('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze').count()
            def capture(snapshot):
                shadow=build_live_shadow(snapshot,profile='task-manager',request_scope=SCOPE)
                return page.evaluate('''(data) => McelTaskManagerSemanticShadow.capture(data.shadow,data.snapshot)''',
                                     {'snapshot':snapshot,'shadow':shadow})
            first=capture(source)
            cases['existing-v2-consumer-mount']=first.get('status')=='shadow-only' and first.get('parity') and page.locator('.mcel-task-semantic-shadow mcel-row').count()==2
            cases['unavailable-not-fabricated-zero']=('Unavailable' in page.locator('.mcel-task-semantic-shadow').inner_text())
            second_receipt=capture(second)
            cases['actual-second-snapshot-update']=second_receipt.get('status')=='shadow-only' and page.locator('.mcel-task-semantic-shadow mcel-row').count()==2
            cases['continuity-not-claimed']=first.get('continuity')=='gated-unverified' and second_receipt.get('continuity')=='gated-unverified'
            ref=page.evaluate('''(snap) => McelTaskManagerSemanticShadow.capture(null,snap)''',second)
            cases['refused-shadow-keeps-last-known-rows']=ref.get('status')=='refused' and page.locator('.mcel-task-semantic-shadow mcel-row').count()==2
            cases['operational-controls-unmodified']=before_controls==5 and page.locator('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze').count()==5
            cases['no-browser-errors']=not errors
            page.close()
        finally:
            browser.close()
    return {'status':'PASS' if all(cases.values()) else 'FAIL','checks':cases}


def _request(base, path, data=None):
    url=base+path
    if data is not None:
        raw=json.dumps(data).encode('utf-8')
        req=Request(url,data=raw,headers={'Content-Type':'application/json'},method='POST')
    else:
        req=Request(url,method='GET')
    with urlopen(req,timeout=15) as response:
        return json.load(response)


def hosted(host_url,chromium_path=None):
    from playwright.sync_api import sync_playwright
    parsed=urlsplit(host_url)
    if parsed.scheme not in ('http','https') or not parsed.netloc:
        raise ValueError('--host-url must be a valid hosted Task Manager page URL')
    base=f'{parsed.scheme}://{parsed.netloc}'
    checks={}; evidence={}
    response=_request(base,'/api/applications/task/overview',
                      {'query':'','limit':24,'include_all':False,'include_connections':True,'mcel_semantic_shadow':True})
    shadow=response.get('_mcel_semantic_shadow')
    p=shadow.get('observations',{}).get('task-manager.processes',{}) if isinstance(shadow,dict) else {}
    checks['host-real-task-same-response-parity']=(shadow is not None and shadow.get('ok') is True and
        p.get('parity',{}).get('ok') is True and p['parity']['sourceRecords']==len(response.get('processes',[])))
    evidence['task']={'count':len(response.get('processes',[])),'shadow':p.get('status'),
                      'accepted':p.get('parity',{}).get('accepted'),'quarantined':p.get('parity',{}).get('quarantined')}
    try:
        roots=_request(base,'/api/applications/file-explorer/roots',{})
        picked=next((r['id'] for r in roots.get('roots',[]) if r.get('id')),None)
        listing=_request(base,'/api/applications/file-explorer/list',{'root_id':picked,'relative_path':''}) if picked else {}
        result=build_live_shadow(listing,profile='file-explorer')
        obs=result['observations']['file-explorer.entries']
        checks['host-real-file-explorer-list-parity']=(bool(listing.get('ok')) and result['ok'] and
            obs['status']=='shadow-only' and obs['parity']['ok'])
        evidence['fileExplorer']={'count':listing.get('count'),'rootId':picked,'status':obs['status']}
    except Exception as exc:
        checks['host-real-file-explorer-list-parity']=False
        evidence['fileExplorer']={'status':'BLOCKED','reason':str(exc)[:300]}
    try:
        runtime=_request(base,'/api/applications/worker/runtime-status')
        result=build_live_shadow(runtime,profile='worker')
        obs=result['observations']['worker.runtime']
        checks['host-real-worker-runtime-resolved']=(result['ok'] and obs['status']=='canonical-only')
        evidence['worker']={'status':obs['status']}
    except Exception as exc:
        checks['host-real-worker-runtime-resolved']=False
        evidence['worker']={'status':'BLOCKED','reason':str(exc)[:300]}
    params=dict(parse_qsl(parsed.query,keep_blank_values=True));params['mcel_semantic_shadow']='lab';params['mcel_task_notebook']='lab'
    url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(params),''))
    try:
        with sync_playwright() as pw:
            browser=_chrome(pw,chromium_path)
            try:
                page=browser.new_page(viewport={'width':1100,'height':800});errors=[]
                page.on('pageerror',lambda err:errors.append(str(err)))
                page.goto(url,wait_until='domcontentloaded',timeout=20000)
                page.wait_for_function('''() => window.McelTaskManagerSemanticShadow?.status()?.mounted===true''',timeout=18000)
                result=page.evaluate('''async () => {
                  const first=McelTaskManagerSemanticShadow.status();
                  const controls=document.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze').length;
                  await refreshTaskManager();
                  const second=McelTaskManagerSemanticShadow.status();
                  const legacy=McelTaskManagerNotebookLab.status();
                  return {first,second,legacy,controls,rows:document.querySelectorAll('.mcel-task-semantic-shadow mcel-row').length};
                }''')
                checks['host-browser-second-refresh-shadow']=(result['first']['status']=='shadow-only' and
                    result['second']['status']=='shadow-only' and result['controls']==5 and
                    result['second']['accepted']==result['rows'] and not errors)
                checks['host-legacy-patch10-lab-continues']=(result['legacy']['mounted'] and
                     result['legacy']['lastReceipt'].get('ok') is True)
                evidence['browser']={'count':result['rows'],'continuity':result['second']['continuity'],
                     'legacyVersion':result['legacy']['version'],'pageErrors':errors}
            finally:
                browser.close()
    except Exception as exc:
        checks['host-browser-second-refresh-shadow']=False
        checks['host-legacy-patch10-lab-continues']=False
        evidence['browser']={'status':'BLOCKED','reason':str(exc)[:300]}
    return {'status':'PASS' if all(checks.values()) else 'INCOMPLETE',
            'checks':checks,'evidence':evidence,
            'note':'Hosted parity does not certify canonical DOM continuity; promotion remains gated'}


def evaluate(host_url=None,chromium_path=None):
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(LiveStage3Tests)
    result=unittest.TestResult();suite.run(result)
    snapshot=TaskManagerService(ROOT).snapshot(limit=5,include_connections=False)
    read=build_live_shadow(snapshot,profile='task-manager',request_scope={'query':'','limit':5,'include_all':False})
    try:browser=offline_browser(chromium_path)
    except Exception as exc:browser={'status':'BLOCKED','reason':str(exc)[:300],'checks':{}}
    status='PASS' if result.wasSuccessful() and read['ok'] and browser['status']=='PASS' else 'INCOMPLETE'
    if host_url:
        try: host=hosted(host_url,chromium_path)
        except Exception as exc: host={'status':'BLOCKED','reason':str(exc)[:300]}
    else: host={'status':'NOT_RUN','reason':'--host-url not supplied; no claim of hosted integration'}
    if host_url and host['status']!='PASS':status='INCOMPLETE'
    return {'schema':'mcel.patch13-live-adoption-acceptance.v1','status':status,
            'stage':'13.3-read-only-live-shadow','testCount':result.testsRun,
            'unitTests':'PASS' if result.wasSuccessful() else 'FAIL',
            'unitFailures':[str(x)+': '+y for x,y in result.failures+result.errors],
            'realLocalTaskProducer':'PASS' if read['ok'] else 'FAIL',
            'realLocalProcessCount':len(snapshot.get('processes',[])),
            'offlineBrowser':browser,'hosted':host,
            'continuityPromotion':'GATED — no canonical reconciliation without verified source+hosted evidence',
            'productionBehaviorChanged':False,'runtimeEffectsExecuted':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-url',help='Actual running Task Manager page, for hosted acceptance')
    parser.add_argument('--chromium-path',help='Use an installed Chromium/Chrome binary for browser tests')
    parser.add_argument('--report',type=Path,default=DEFAULT)
    args=parser.parse_args(argv)
    report=evaluate(args.host_url,args.chromium_path)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2,default=str)+'\n',encoding='utf-8')
    print('MCEL_PATCH13_LIVE_ADOPTION_'+report['status'])
    print('unitTests='+report['unitTests'], 'focusedTests='+str(report['testCount']))
    print('realTask='+report['realLocalTaskProducer'], 'offlineBrowser='+report['offlineBrowser']['status'],
          'hosted='+report['hosted']['status'])
    print('continuityPromotion=GATED')
    print('report:',args.report)
    return 0 if report['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
