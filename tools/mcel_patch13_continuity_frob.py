"""Patch 13 Stage 4: read-only canonical generation/DOM continuity acceptance.

Offline browser tests are executed against the real existing MCEL v2 consumer.
Hosted verification is separately required and is never inferred from offline
success. No Task Manager actions or host writes are issued by this Frob.

  python -m tools.mcel_patch13_continuity_frob
  python -m tools.mcel_patch13_continuity_frob --host-url "http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab"
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import unittest
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from main_computer.mcel_knowledge.live_adoption import build_live_shadow
from main_computer.mcel_knowledge.continuity import with_process_generation_evidence
from main_computer.task_manager import TaskManagerService
from tests.test_mcel_patch13_continuity import BASE, SCOPE, Patch13ContinuityTests, ISSUER, make
from tools.mcel_patch13_live_adoption_frob import _chrome, _request, SHADOW_SCRIPT, offline_browser as stage3_browser
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'runtime/reports/mcel-patch13-continuity.json'


def offline(chromium_path=None):
    from playwright.sync_api import sync_playwright
    checks={};details={}
    old={'processes':[BASE, dict(BASE,pid=813,memory_rss=None,memory_availability='unavailable')]}
    changed={'processes':[dict(BASE,name='python-new',memory_rss=1024),old['processes'][1]]}
    reused={'processes':[dict(changed['processes'][0],created_at='2026-10-27T17:30:00-07:00'),old['processes'][1]]}
    lost={'processes':[changed['processes'][1]]}
    with sync_playwright() as pw:
        browser=_chrome(pw,chromium_path)
        try:
            page=browser.new_page(viewport={'width':1100,'height':800});errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.set_content(html_fixture(),wait_until='domcontentloaded')
            page.evaluate('''() => {const prior=URLSearchParams.prototype.get;
              URLSearchParams.prototype.get=function(key){return key==='mcel_semantic_shadow'?'lab':prior.call(this,key)};
            }''')
            page.add_script_tag(path=str(SHADOW_SCRIPT))
            def capture(snap, *, issuer=ISSUER, source='win32-cim-creation-date'):
                shadow=make(snap['processes'],issuer=issuer,source=source)
                return page.evaluate('''({shadow,snap}) => McelTaskManagerSemanticShadow.capture(shadow,snap)''',
                                     {'shadow':shadow,'snap':snap})
            a=capture(old)
            checks['producer-proof-gated-and-read-only']=a.get('status')=='shadow-only' and a.get('continuity')=='evidence-eligible'
            controls=page.locator('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze').count()
            anchor=page.locator('.mcel-task-semantic-shadow mcel-row').first
            first_id=anchor.get_attribute('data-row-id')
            page.evaluate('''(id) => {
              const root=document.querySelector('.mcel-task-semantic-shadow');
              window.__mcelAnchor=root.querySelector('mcel-row[data-row-id="'+CSS.escape(id)+'"]');
              window.__mcelAnchor.focus();
              McelTaskManagerSemanticShadow.inspect(id);
            }''',first_id)
            r=capture(changed)
            stable=page.evaluate('''() => {
              const root=document.querySelector('.mcel-task-semantic-shadow');
              const id=window.__mcelAnchor?.dataset.rowId;
              const fresh=id&&root.querySelector('mcel-row[data-row-id="'+CSS.escape(id)+'"]');
              return {sameNode:fresh===window.__mcelAnchor,focus:document.activeElement===window.__mcelAnchor,
                inspected:root.querySelector('.mcel-semantic-collection__inspector')?.textContent||'',id,
                rows:root.querySelectorAll('mcel-row').length};
            }''')
            checks['unchanged-generation-retains-dom-focus-and-selection']=(r.get('continuity')=='observed-retention' and
                r.get('reconciliation',{}).get('retained')==2 and stable['sameNode'] and stable['focus'] and
                first_id in stable['inspected'] and 'python-new' in stable['inspected'])
            checks['retention-is-read-only-and-other-controls-unchanged']=(controls==5 and
                page.locator('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze').count()==5 and
                r.get('effects')==[])
            replacement=capture(reused)
            swapped=page.evaluate('''() => {
              const root=document.querySelector('.mcel-task-semantic-shadow');
              const id=window.__mcelAnchor?.dataset.rowId;
              const fresh=id&&root.querySelector('mcel-row[data-row-id="'+CSS.escape(id)+'"]');
              return {sameNode:fresh===window.__mcelAnchor,
                inspector:root.querySelector('.mcel-semantic-collection__inspector')?.textContent||''};
            }''')
            checks['pid-reuse-replaces-dom-clears-selection']=(replacement.get('reconciliation',{}).get('added')==1 and
                replacement.get('reconciliation',{}).get('removed')==1 and
                replacement.get('reconciliation',{}).get('retained')==1 and not swapped['sameNode'] and
                'absent from this observation or changed identity' in swapped['inspector'])
            # Inspect remaining generation; a bounded-list drop is not a process death claim.
            rows=page.locator('.mcel-task-semantic-shadow mcel-row');rows.last.click()
            capture(lost)
            checks['bounded-observation-disappearance-does-not-claim-process-ended']=(
                'ended' not in page.locator('.mcel-task-semantic-shadow .mcel-semantic-collection__inspector').inner_text().lower())
            # Simulate a server-process restart: same PID/start must NOT preserve node.
            initial=capture(old)
            restart=capture(old,issuer='b'*32)
            checks['server-instance-change-blocks-retention']=(initial.get('status')=='shadow-only' and
                restart.get('reconciliation',{}).get('retained')==0)
            invalid=capture({'processes':[dict(BASE,pid=1,created_at='invalid'),dict(BASE,pid=813)]})
            checks['per-record-quarantine-preserves-valid-other-rows']=(invalid.get('status')=='shadow-only' and
                invalid.get('quarantined')==1 and invalid.get('accepted')==1)
            prior_count=page.locator('.mcel-task-semantic-shadow mcel-row').count()
            refused=page.evaluate('''(snap) => McelTaskManagerSemanticShadow.capture(null,snap)''',lost)
            checks['refusal-keeps-last-known']=(refused.get('status')=='refused' and
                page.locator('.mcel-task-semantic-shadow mcel-row').count()==prior_count)
            details.update({'initial':a,'updated':r,'stableEvidence':stable,
                            'pidReused':replacement,'displacedEvidence':swapped,
                            'restart':restart,'browserErrors':errors})
            checks['no-browser-errors']=not errors
            page.close()
        finally:
            browser.close()
    return {'status':'PASS' if checks and all(checks.values()) else 'FAIL','checks':checks,'evidence':details}


def hosted(host_url,chromium_path=None):
    from playwright.sync_api import sync_playwright
    parsed=urlsplit(host_url)
    if parsed.scheme not in ('http','https') or not parsed.netloc:
        raise ValueError('Invalid hosted Task Manager page URL')
    base=f'{parsed.scheme}://{parsed.netloc}'
    checks={};evidence={}
    response=_request(base,'/api/applications/task/overview',
                      {'query':'','limit':24,'include_all':False,'include_connections':True,'mcel_semantic_shadow':True})
    source=response.get('_mcel_semantic_shadow',{})
    observation=source.get('observations',{}).get('task-manager.processes',{})
    proof=source.get('processGenerationEvidence',{})
    rows=observation.get('collection',{}).get('rows',[])
    checks['host-served-production-generation-evidence']=(source.get('ok') is True and
        proof.get('status')=='eligible-read-only' and proof.get('qualifiedRows',0)>0 and
        proof.get('qualifiedRows')==len(rows) and
        observation.get('collection',{}).get('spec',{}).get('reconciliation',{}).get('generationFrom')=='mcelGeneration' and
        observation.get('parity',{}).get('ok') is True and not source.get('continuityCertified'))
    producer=response.get('_mcel_task_process_producer',{})
    evidence['source']={'count':len(response.get('processes',[])), 'qualified':proof.get('qualifiedRows'),
                        'sourceKind':proof.get('sourceKind'), 'parity':observation.get('parity',{}).get('ok'),
                        'producerStatus':producer.get('status','not-reported'),
                        'producerElapsedMs':producer.get('elapsedMs'),
                        'producerTimeoutSeconds':producer.get('timeoutSeconds'),
                        'discoveredCount':producer.get('discoveredCount'),
                        'matchingCount':producer.get('matchingCount'),
                        'producerReason':producer.get('reason')}
    params=dict(parse_qsl(parsed.query,keep_blank_values=True))
    params['mcel_semantic_shadow']='lab';params['mcel_task_notebook']='lab'
    url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(params),''))
    try:
        with sync_playwright() as pw:
            browser=_chrome(pw,chromium_path)
            try:
                page=browser.new_page(viewport={'width':1100,'height':800});errors=[]
                page.on('pageerror',lambda err:errors.append(str(err)))
                page.goto(url,wait_until='domcontentloaded',timeout=25000)
                page.wait_for_function('''() => McelTaskManagerSemanticShadow?.status()?.mounted===true''',timeout=24000)
                record=page.evaluate('''async () => {
                  const root=document.querySelector('.mcel-task-semantic-shadow');
                  const before=McelTaskManagerSemanticShadow.status();
                  const row=root.querySelector('mcel-row');
                  if(!row)return {error:'no-row',before,sourceCount:before.sourceCount??0};
                  const id=row.dataset.rowId;
                  row.focus();McelTaskManagerSemanticShadow.inspect(id);
                  await refreshTaskManager();
                  const after=McelTaskManagerSemanticShadow.status();
                  const fresh=root.querySelector('mcel-row[data-row-id="'+CSS.escape(id)+'"]');
                  const legacy=McelTaskManagerNotebookLab.status();
                  return {before,after,retainedNode:row===fresh,focusRetained:document.activeElement===row,
                    inspectionRetained:(root.querySelector('.mcel-semantic-collection__inspector')?.textContent||'').includes(id),
                    legacyMounted:!!legacy.mounted,legacyReceiptOk:legacy.lastReceipt?.ok===true,
                    controls:document.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze').length,
                    visibleRows:root.querySelectorAll('mcel-row').length};
                }''')
                checks['hosted-browser-retains-same-generation-dom-focus-inspector']=(record.get('retainedNode') and
                    record.get('focusRetained') and record.get('inspectionRetained') and
                    record.get('after',{}).get('reconciliation',{}).get('retained',0)>0 and
                    record.get('after',{}).get('continuity')=='observed-retention')
                checks['hosted-legacy-lifecycle-and-controls-unchanged']=(record.get('legacyMounted') and
                    record.get('legacyReceiptOk') and record.get('controls')==5 and not errors)
                evidence['browser']={**record,'errors':errors}
            finally:
                browser.close()
    except Exception as exc:
        checks['hosted-browser-retains-same-generation-dom-focus-inspector']=False
        checks['hosted-legacy-lifecycle-and-controls-unchanged']=False
        evidence['browser']={'status':'BLOCKED','reason':str(exc)[:400]}
    return {'status':'PASS' if all(checks.values()) else 'INCOMPLETE',
            'checks':checks,'evidence':evidence,
            'certification':'read-only canonical continuity in hosted lab only' if all(checks.values()) else 'NOT_CERTIFIED',
            'productionPromotion':False}


def evaluate(host_url=None,chromium_path=None):
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Patch13ContinuityTests)
    unit=unittest.TestResult();suite.run(unit)
    try: browser=offline(chromium_path)
    except Exception as exc: browser={'status':'BLOCKED','reason':str(exc)[:400]}
    try: prior=stage3_browser(chromium_path)
    except Exception as exc: prior={'status':'BLOCKED','reason':str(exc)[:400]}
    real=TaskManagerService(ROOT).snapshot(limit=5,include_connections=False)
    shadow=build_live_shadow(real,profile='task-manager',request_scope={'query':'','limit':5,'include_all':False})
    producer=shadow['ok'] and shadow['observations']['task-manager.processes']['parity']['ok']
    host={'status':'NOT_RUN','reason':'No --host-url provided; Windows source and DOM certification not claimed'}
    if host_url:
        try:host=hosted(host_url,chromium_path)
        except Exception as exc:host={'status':'BLOCKED','reason':str(exc)[:400]}
    status='PASS' if unit.wasSuccessful() and browser['status']=='PASS' and prior['status']=='PASS' and producer else 'INCOMPLETE'
    if host_url and host['status']!='PASS':status='INCOMPLETE'
    return {'schema':'mcel.patch13-continuity-acceptance.v1','status':status,
      'unitTests':'PASS' if unit.wasSuccessful() else 'FAIL', 'unitCount':unit.testsRun,
      'unitFailures':[str(i)+': '+msg for i,msg in unit.failures+unit.errors],
      'offlineBrowser':browser,'previousStage3Browser':prior,
      'realLocalTaskProducer':'PASS' if producer else 'FAIL', 'realLocalProcessCount':len(real.get('processes',[])),
      'hosted':host,'productionPromotion':False,'runtimeEffectsExecuted':False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host-url');p.add_argument('--chromium-path')
    p.add_argument('--report',type=Path,default=DEFAULT)
    args=p.parse_args(argv)
    result=evaluate(args.host_url,args.chromium_path)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,indent=2,default=str)+'\n',encoding='utf-8')
    print('MCEL_PATCH13_CONTINUITY_'+result['status'])
    print('tests='+result['unitTests'],result['unitCount'],
          'browser='+result['offlineBrowser']['status'],
          'stage3='+result['previousStage3Browser']['status'],
          'hosted='+result['hosted']['status'])
    print('productionPromotion=NO')
    print('report:',args.report)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
