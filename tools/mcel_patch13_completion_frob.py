"""Patch 13 final acceptance: compatible existing presentation + authoring economy.

This is a read-only, explicitly opt-in *test* of existing v2 collection
machinery, NOT a new production UI, layout engine, or authority. Host optional:
  python -m tools.mcel_patch13_completion_frob
  python -m tools.mcel_patch13_completion_frob --host-url \
    "http://127.0.0.1:8765/applications/task-manager/server-processes?mcel_task_notebook=lab"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import unittest
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from main_computer.mcel_knowledge.authoring_audit import authoring_audit
from main_computer.mcel_knowledge.live_adoption import build_live_shadow
from main_computer.mcel_knowledge.presentation_policy import load_presentation_policy
from tests.test_mcel_patch13_completion import Patch13CompletionTests
from tests.test_mcel_patch13_live_shadow import BASE, SCOPE
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
from tools.mcel_patch13_live_adoption_frob import _chrome, _request

ROOT=Path(__file__).resolve().parents[1]
DEFAULT=ROOT/'runtime/reports/mcel-patch13-completion.json'
POLICY=load_presentation_policy()
WIDTHS=(320,390,560,1100)


def _project(snapshot, request_scope):
    output=build_live_shadow(snapshot,profile='task-manager',request_scope=request_scope,
                             presentation_policy=POLICY)
    observed=output.get('observations',{}).get('task-manager.processes',{})
    if not output['ok'] or observed.get('status')!='shadow-only':
        raise ValueError('Semantic source refused: '+str(observed.get('reason','parity failure')))
    return observed['collection'], observed['parity']


def _browser(snap, scope, *, host_url=None, expect_sample=True):
    from playwright.sync_api import sync_playwright
    projection, parity=_project(snap,scope)
    spec=projection['spec']
    checks={};cases=[];errors=[]
    with sync_playwright() as playwright:
        browser=_chrome(playwright,None)
        try:
            for width in WIDTHS:
                page=browser.new_page(viewport={'width':width,'height':800},reduced_motion='reduce')
                page.on('pageerror',lambda err:errors.append(str(err)))
                try:
                    if host_url:
                        parsed=urlsplit(host_url)
                        params=dict(parse_qsl(parsed.query,keep_blank_values=True))
                        params.update({'mcel_semantic_shadow':'lab','mcel_semantic_presentation':'compat',
                                       'mcel_task_notebook':'lab'})
                        test_url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(params),''))
                        page.goto(test_url,wait_until='domcontentloaded',timeout=25000)
                        page.wait_for_selector('#task-manager-app',timeout=12000)
                        page.wait_for_function('''() => window.McelTaskManagerSemanticShadow?.status()?.mounted===true''',timeout=15000)
                    else:
                        page.set_content(html_fixture(),wait_until='load')
                    if not host_url:
                        # Only the private offline browser fixture needs query-string
                        # emulation; the hosted page receives real URL parameters.
                        page.evaluate('''() => {const prior=URLSearchParams.prototype.get;
                          URLSearchParams.prototype.get=function(k){
                            if(k==='mcel_semantic_shadow')return 'lab';
                            if(k==='mcel_semantic_presentation')return 'compat';
                            return prior.call(this,k);
                          };}''')
                        page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts/mcel-task-manager-semantic-shadow.js'))
                    optIn=page.evaluate('''() => ({enabled:window.McelTaskManagerSemanticShadow?.enabled===true,
                      compatibility:window.McelTaskManagerSemanticShadow?.compatibility===true})''')
                    before=page.evaluate('''() => ({controls:[...document.querySelectorAll(
                      '#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze')]
                      .map(e=>e.outerHTML), incumbent:document.querySelector('.task-notebook')?.outerHTML})''')
                    result=page.evaluate('''({rows,spec}) => {
                      const app=document.getElementById('task-manager-app');
                      const host=document.createElement('section');host.className='mcel-patch13-compat-test';
                      host.setAttribute('data-test-only','true');app.append(host);
                      const workspace=McelSemanticCollectionWorkspace.mount(host,rows,spec,{observeResize:false});
                      const fields=[...host.querySelectorAll('mcel-row:first-child [data-semantic-field]')]
                        .map(n=>({key:n.dataset.semanticField,
                        label:n.querySelector('.field-label')?.textContent,
                        value:n.querySelector('.field-value')?.textContent}));
                      const from=(key)=>[...host.querySelectorAll('[data-semantic-field="'+key+'"] .field-value')].map(x=>x.textContent);
                      const first=host.querySelector('mcel-row');
                      if(first){first.click();}
                      const exact=host.querySelector('.mcel-semantic-collection__inspector')?.textContent||'';
                      const legacy=window.McelTaskManagerProcessContract?.processes;
                      const aliases={memory:'resident_memory',status:'state',created:'created_at'};
                      const legacyShape=legacy?.fields?.map(f=>({key:aliases[f.key]||f.key,label:f.label,tier:f.tier,kind:f.kind}));
                      const currentShape=spec.fields.filter(f=>f.key!=='mcelIdentity').map(f=>
                        ({key:f.key,label:f.label,tier:f.tier,kind:f.kind}));
                      return {width:innerWidth,docWidth:document.documentElement.scrollWidth,
                        rows:host.querySelectorAll('mcel-row').length,fields,roles:from('role'),
                        memory:from('resident_memory'),commands:from('command'),
                        exactSourceInspector:exact,
                        legacyShape:legacyShape||null,currentShape,
                        operationControls:[...document.querySelectorAll(
                        '#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze')]
                        .map(e=>e.outerHTML),legacyUnchanged:document.querySelector('.task-notebook')?.outerHTML,
                        extraCanonicalIdentity:!!spec.fields.find(f=>f.key==='mcelIdentity'),
                        projectionReadOnly:true};
                    }''',{'rows':projection['rows'],'spec':spec})
                    cases.append({'width':width,'rows':result['rows'],
                                  'noHorizontalOverflow':result['docWidth']<=width+2,
                                  'labOptIn':bool(optIn['enabled'] and optIn['compatibility']),
                                  'domainFieldsMatchLegacy':result['legacyShape']==result['currentShape'],
                                  'identityExtraField':result['extraCanonicalIdentity'],
                                  'controlsUnchanged':before['controls']==result['operationControls'],
                                  'legacyUnchanged':before['incumbent']==result['legacyUnchanged'],
                                  'roles':result['roles'],'memory':result['memory'],
                                  'commands':result['commands'],
                                  'inspectorHasFullCommand':'very-long '*15 in result['exactSourceInspector'],
                                  'noEffects':True})
                finally:
                    page.close()
        finally:
            browser.close()
    checks['source-parity']=parity['ok'] and parity['canonicalRecordsUnchanged']
    checks['compatibility-opt-in-explicit']=all(c['labOptIn'] for c in cases)
    checks['four-existing-responsive-widths']=len(cases)==4 and all(c['noHorizontalOverflow'] for c in cases)
    checks['seven-domain-fields-and-display-semantics-match-legacy']=all(c['domainFieldsMatchLegacy'] for c in cases)
    if expect_sample:
        checks['roles-and-measured-zero-vs-unavailable']=all(c['roles']==['Current','Main Computer','Other'] and
                                                              c['memory'][:2]==['0 B','Unavailable'] for c in cases)
        checks['command-disclosure-with-exact-inspector']=all(
            c['commands'] and len(c['commands'][0])<=46 and '…' in c['commands'][0] and
            c['inspectorHasFullCommand'] for c in cases)
    else:
        checks['hosted-presentation-values-type-safe']=all(c['rows']>0 and len(c['roles'])==c['rows']
            and len(c['memory'])==c['rows'] for c in cases)
    checks['existing-controls-and-incumbent-unmodified']=all(c['controlsUnchanged'] and c['legacyUnchanged'] for c in cases)
    checks['canonical-identity-difference-disclosed']=all(c['identityExtraField'] for c in cases)
    checks['no-browser-errors']=not errors
    return {'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,
            'widths':cases,'pageErrors':errors,
            'scope':'hosted-lab-injection-only' if host_url else 'offline-fixture-existing-app',
            'pixelEquivalent':False,
            'productionPromotion':False}


def _sample():
    return {'processes':[
        dict(BASE,pid=812,is_current_process=True,cmdline='python '+'very-long '*25),
        dict(BASE,pid=813,memory_rss=None,memory_availability='unavailable',is_main_computer=True),
        dict(BASE,pid=814)
    ]}


def _host_snapshot(url):
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or parsed.hostname not in ('127.0.0.1','localhost','::1'):
        raise ValueError('Hosted validation requires localhost Task Manager URL')
    from tools.mcel_patch13_live_adoption_frob import _request
    base=f'{parsed.scheme}://{parsed.netloc}'
    response=_request(base,'/api/applications/task/overview',
                      {'query':'','limit':24,'include_all':False,'include_connections':True,
                       'mcel_semantic_shadow':True,'mcel_semantic_presentation':'compat'})
    return response


def run(*, hosted_url=None):
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Patch13CompletionTests)
    runner=unittest.TextTestRunner(stream=__import__('io').StringIO(),verbosity=1)
    unit=runner.run(suite)
    report={'schema':'mcel.patch13-completion-acceptance.v1',
            'status':'INCOMPLETE',
            'unitTests':'PASS' if unit.wasSuccessful() else 'FAIL',
            'unitCount':unit.testsRun,'unitFailures':[str(x[1])[:600] for x in unit.failures+unit.errors],
            'authoring':authoring_audit(POLICY),
            'productionPromotion':False,
            'presentationPolicyOptInOnly':True,
            'runtimeEffectsExecuted':False}
    try:
        report['offlineBrowser']=_browser(_sample(),SCOPE)
    except Exception as ex:
        report['offlineBrowser']={'status':'BLOCKED','reason':f'{type(ex).__name__}: {ex}'}
    report['hosted']={'status':'NOT_RUN','reason':'No --host-url provided'}
    if hosted_url:
        try:
            served=_host_snapshot(hosted_url)
            snap={'processes':served.get('processes',[])}
            if not snap['processes']:
                raise ValueError('No hosted process records; source observation cannot certify')
            col,parity=_project(snap,{'query':'','limit':24,'include_all':False})
            served_observation=served.get('_mcel_semantic_shadow',{}).get('observations',{}).get('task-manager.processes',{})
            served_projection=served_observation.get('collection',{})
            served_compat=(served_observation.get('status')=='shadow-only' and
                           served_projection.get('presentationCompatibility',{}).get('status')=='explicit-application-overlay' and
                           served_observation.get('parity',{}).get('ok') is True)
            host_browser=_browser(snap,{'query':'','limit':24,'include_all':False},host_url=hosted_url,
                                  expect_sample=False)
            host_checks=host_browser.get('checks',{})
            result=all(host_checks.values()) and bool(parity['ok']) and served_compat
            report['hosted']={'status':'PASS' if result else 'FAIL','sourceCount':len(snap['processes']),
                              'sameResponseParity':bool(parity['ok']),
                              'servedCompatibilityOverlay':bool(served_compat),
                              'browser':host_browser,'hostIndependentChecks':host_checks,
                              'note':'Read-only test injection into hosted page; not production UI promotion'}
        except Exception as exc:
            report['hosted']={'status':'INCOMPLETE','reason':f'{type(exc).__name__}: {exc}'}
    # Offline completed is PASS even though optional host gate is unrun; host
    # failure never gets silently turned into PASS when --host-url is supplied.
    report['status']=('PASS' if report['unitTests']=='PASS' and
        report['offlineBrowser']['status']=='PASS' and
        report['hosted']['status'] in ('NOT_RUN','PASS') else 'INCOMPLETE')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-url')
    parser.add_argument('--report',type=Path,default=DEFAULT)
    opts=parser.parse_args()
    result=run(hosted_url=opts.host_url)
    opts.report.parent.mkdir(parents=True,exist_ok=True)
    opts.report.write_text(json.dumps(result,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf-8')
    print('MCEL_PATCH13_COMPLETION_'+result['status'])
    print('unitTests='+result['unitTests'], 'count='+str(result['unitCount']),
          'offlineBrowser='+result['offlineBrowser']['status'],
          'hosted='+result['hosted']['status'])
    print('legacyDeclarations='+str(result['authoring']['legacyDeclarationInstances']),
          'overlayDeclarations='+str(result['authoring']['overlayDeclarationInstances']),
          'productionPromotion=NO')
    print('report:',opts.report)
    return 0 if result['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
