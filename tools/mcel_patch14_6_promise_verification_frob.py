"""Patch 14.6: existing Task Manager / one-profile MCEL promise verification.

Offline, read-only, reproducible experiment. The semantic and blinded-order arms
use the SAME v2 renderer, single universal profile, region tree, field set,
field tiers, value formatting, inspector and fixture. The only arm difference
is canonical-semantic field ordering versus preregistered hash ordering.

    python -m tools.mcel_patch14_6_promise_verification_frob
    python -m tools.mcel_patch14_6_promise_verification_frob --output runtime/reports/check-146

Measures an observable field-acquisition *proxy*, NOT participant time-to-answer,
accuracy, aesthetic superiority, or authorization for production promotion.
Neither the operational Task Manager JS nor network/service endpoints are run.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from main_computer.mcel_knowledge import load_catalog
from main_computer.mcel_knowledge.ir_bridge import compile_knowledge_ir, resolve_knowledge
from main_computer.mcel_knowledge.universal_layout import PROFILE, compose
from tools.mcel_patch14_visual_quality_frob import FIXTURES, stress_snapshot
from tools.mcel_patch13_live_adoption_frob import _chrome
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'main_computer/web/applications/scripts'
CONTINUITY = SCRIPTS / 'mcel-layout-continuity.js'
LAB = SCRIPTS / 'mcel-universal-semantic-layout-lab.js'
FIXTURE = FIXTURES / 'task_manager_snapshot.v1.json'
OUTPUT = ROOT / 'runtime/reports/mcel-patch14-6-promise-verification'
OBS = 'task-manager.processes'
PURPOSE = 'monitor'
WIDTHS = (390, 1440)
STATES = ('normal', 'stressed')
TASKS = ('name', 'state', 'resident_memory')
SCHEMA = 'mcel.patch14.6-promise-verification.v1'
CONTROL_SEED = 'mcel-14.6-task-manager-blind-order-v1'  # locked independently of outcomes


def canonical(x: Any) -> str:
    return json.dumps(x, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def sha(value: Any) -> str:
    if isinstance(value, bytes):
        b = value
    else:
        b = canonical(value).encode('utf-8')
    return hashlib.sha256(b).hexdigest()


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def scenario(state: str, *, catalog: dict | None = None, purpose: str = PURPOSE, source: dict | None = None) -> dict:
    if state not in STATES:
        raise ValueError('Unsupported fixed experiment state')
    snapshot = deepcopy(source) if source is not None else json.loads(FIXTURE.read_text('utf-8'))
    if state == 'stressed':
        snapshot = stress_snapshot(snapshot, OBS)
    cat = catalog if catalog is not None else load_catalog()
    ir = compile_knowledge_ir('promise-verification', observation_ids=[OBS], catalog=cat)
    view = resolve_knowledge(snapshot, ir, catalog=cat)
    plan = compose(view, OBS, purpose=purpose, catalog=cat)
    assert plan['profile'] == PROFILE and plan['readOnly'] and plan['productionPromotion'] is False
    return {'source':snapshot, 'view':view, 'plan':plan, 'sourceSha256':sha(snapshot),
            'catalogSha256':sha({'concepts':cat['concepts'], 'observation':cat['observations'][OBS]})}


def blind_control(semantic: dict) -> dict:
    """Exact same universal renderer and same fields/tiers: nonsemantic order only.

    This is NOT the original v2 baseline or a claim about a production policy.
    The control is deterministic and cannot select a best-performing permutation.
    """
    control = deepcopy(semantic)
    spec = control['collection']['spec']
    # Preserve identical record identities and all types; never remove any field.
    ordered = sorted((f for f in spec['fields'] if f['key'] != spec['identity']),
                     key=lambda f: sha((CONTROL_SEED, f['key'])))
    identity = next(f for f in spec['fields'] if f['key'] == spec['identity'])
    spec['fields'] = ordered + [identity]
    control['decision']['fields'] = [f['key'] for f in spec['fields']]
    control['decision']['semanticSignature'] = 'sha256:' + sha({'control':CONTROL_SEED,
         'fieldOrder':control['decision']['fields'], 'base':semantic['decision']['semanticSignature']})
    control['commit']['decision'] = deepcopy(control['decision'])
    return control


def semantic_retyping_control(source: dict) -> dict:
    """Hold the *source bytes* fixed, change only declared meaning of 'state'.

    A typed state becomes uninterpreted mc.text, leaving its raw values unchanged.
    This synthetic retyping is an experimental intervention, NOT a claim that a
    production Task Manager state is text or permission to alter the catalog.
    """
    original = scenario('normal', source=source)
    catalog = deepcopy(load_catalog())
    entry = next(p for p in catalog['observations'][OBS]['properties'] if p['name'] == 'state')
    assert entry['type'] == 'mc.process.state'
    entry['type'] = 'mc.text'
    mutated = scenario('normal', catalog=catalog, source=source)
    prior, after = original['plan'], mutated['plan']
    # Source equivalence is a crucial identification constraint: meaning, not
    # data content, must be responsible for the adaptation.
    return {
        'sourceByteEquivalent':original['sourceSha256'] == mutated['sourceSha256'],
        'declaredIntervention':{'property':'state','from':'mc.process.state','to':'mc.text'},
        'semanticSignatureChanged':prior['decision']['semanticSignature'] != after['decision']['semanticSignature'],
        'fieldOrderChanged':prior['decision']['fields'] != after['decision']['fields'],
        'stateCountRemoved':any(f['property']=='state' for f in prior['summaryFacts']) and
                            not any(f['property']=='state' for f in after['summaryFacts']),
        'beforeFields':prior['decision']['fields'], 'afterFields':after['decision']['fields'],
        'beforeSummary':prior['summaryFacts'], 'afterSummary':after['summaryFacts'],
        'beforePlan':prior, 'afterPlan':after,
    }


def adaptation_checks(source: dict) -> dict:
    base=scenario('normal',source=source)
    original=base['plan']
    content=deepcopy(source)
    content['processes'][0]['name']='a-renamed-process-with-identical-semantic-shape'
    churn=scenario('normal',source=content)['plan']
    focused=scenario('normal',source=source,purpose='inspect')['plan']
    changed=semantic_retyping_control(source)
    return {
        'contentChangesWithoutDecisionChange': original['decision']==churn['decision'],
        'contentChangeActuallyObserved':original['collection']['rows'][0]['name']!=churn['collection']['rows'][0]['name'],
        'purposeChangedSignature':original['decision']['semanticSignature']!=focused['decision']['semanticSignature'],
        'purposeChangesRegionSet':[r['id'] for r in original['regions']]!=[r['id'] for r in focused['regions']],
        'retainedProfile':all(p['profile']==PROFILE for p in (original,churn,focused,changed['afterPlan'])),
        'semanticRetyping':{k:v for k,v in changed.items() if not k.endswith('Plan')},
        'plans':{'normal':original,'content':churn,'inspect':focused,'retyped':changed['afterPlan']}
    }


# The probe operates the REAL collection and universal DOM mounted inside the
# existing offline Task Manager HTML; no fake tables or computed-only previews.
BROWSER_PROBE = r'''({plan, tasks}) => {
  const host=document.createElement('section');
  host.id='patch146-probe';host.style.cssText='width:100%;max-width:100%;min-width:0';
  document.querySelector('#task-manager-app').append(host);
  const instance=McelUniversalSemanticLayoutLab.mount(host,plan,{lab:true});
  const all=[...host.querySelectorAll('mcel-row')];
  if(!all.length)throw Error('NO_RENDERED_ROWS');
  const first=all[0];
  const fields=[...first.querySelectorAll('mcel-field')].map((cell,index)=>({
     key:cell.dataset.semanticField,index,
     visible:!cell.hidden&&getComputedStyle(cell).display!=='none',
     value:cell.querySelector('.field-value')?.textContent||'',
  }));
  const rowValues=McelSemanticValueContract.rowValues(plan.collection.rows[0],plan.collection.spec);
  const acquisitions=tasks.map(key=>{
    const field=fields.find(x=>x.key===key);
    const contract=plan.collection.spec.fields.find(x=>x.key===key);
    const expected=contract && rowValues.fields[key] &&
      McelSemanticValueContract.display(rowValues.fields[key],contract);
    const visible=fields.filter(x=>x.visible);
    return {key,found:!!field,visible:!!field?.visible,rank:field?field.index+1:null,
       visibleRank:field?.visible?visible.findIndex(x=>x.key===key)+1:null,
       firstThree:visible.slice(0,3).some(x=>x.key===key),
       renderedValue:field?.value||null,expectedValue:expected||null,
       displayAccurate:!!field && field.value===expected};
  });
  const rejectedWrites=(()=>{
    const invalid=structuredClone(plan);invalid.collection.noOperations=false;
    try{McelUniversalSemanticLayoutLab.validate(invalid);return false;}catch(_){return true;}
  })();
  const rejectedPromotion=(()=>{
    const invalid=structuredClone(plan);invalid.productionPromotion=true;
    try{McelUniversalSemanticLayoutLab.validate(invalid);return false;}catch(_){return true;}
  })();
  const target=all[Math.min(1,all.length-1)];
  target.focus();target.dispatchEvent(new KeyboardEvent('keydown',
    {key:'Enter',bubbles:true,cancelable:true}));
  const inspector=host.querySelector('.mcel-semantic-collection__inspector');
  const exact=inspector.querySelector('details.mcel-semantic-collection__exact-evidence');
  const pairs=[...exact.querySelectorAll('.mcel-semantic-collection__raw-values dt')].map(dt=>
    [dt.textContent,dt.nextElementSibling.textContent]);
  const record=Object.fromEntries(pairs);
  const evidence=exact.querySelector('details.mcel-semantic-collection__exact-semantics');
  const semanticPairs=[...evidence.querySelectorAll('dt')].map(dt=>
    [dt.textContent,dt.nextElementSibling.textContent]);
  const expected=plan.collection.rows[Math.min(1,all.length-1)];
  const expectedValues=McelSemanticValueContract.rowValues(expected,plan.collection.spec);
  const actualSemantic=new Map(semanticPairs);
  const provenanceConsistent=Object.entries(expectedValues.fields).every(([key,item])=>{
    const shown=actualSemantic.get(key);
    const source=item.source.path||item.dependencies.join(', ')||'derived';
    return typeof shown==='string' && shown===item.availability+' / '+item.freshness+
      (item.conflict.length?' / conflicting':'')+' / '+source;
  });
  const before={collapsed:!exact.open,internalProof:!!record.mcelCanonicalRecord,
    selectedCorrectly:instance.collection.inspectedId===expected.mcelIdentity,
    keyboardFocusPreserved:document.activeElement===target,
    sourceIdentityMatches:record.mcelIdentity===expected.mcelIdentity,
    exactRecordMatches:JSON.stringify(JSON.parse(record.mcelCanonicalRecord))===JSON.stringify(expected.mcelCanonicalRecord),
    canonicalRecord:JSON.parse(record.mcelCanonicalRecord),
    semanticPairs:semanticPairs,provenanceConsistent,rawKeys:Object.keys(record),
    humanFactsVisible:inspector.querySelectorAll('.mcel-semantic-collection__readable-facts dt').length,
    disclosureControl:exact.querySelector('summary')?.textContent,
    rejectsUnauthorizedOperations:rejectedWrites,rejectsProductionPromotion:rejectedPromotion,
    rawHiddenVisually:exact.getBoundingClientRect().height < 75,
    regions:host.querySelectorAll('.mcel-universal-composition__region').length,
    originalControlCount:document.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze').length,
    documentOverflow:document.documentElement.scrollWidth>innerWidth+2,
    observedRows:all.length,
    visibleFields:fields.filter(x=>x.visible).map(x=>x.key)
  };
  exact.querySelector('summary').click();
  before.disclosureOpens=exact.open;
  evidence.querySelector('summary').click();
  before.provenanceOpens=evidence.open;
  // Uses the already-mounted universal API. Structural decisions MUST NOT be
  // silently changed by update() without a remount.
  const structural=instance.update({...plan,decision:{...plan.decision,semanticSignature:'sha256:structural-change'}});
  before.structuralRefused=structural.ok===false&&structural.reason==='structural-change-requires-explicit-remount';
  instance.destroy();host.remove();
  return {acquisitions,proof:before,cleanup:!host.isConnected};
}'''


# Observe actual DOM update() and rebuild contracts (not simulated data math).
ADAPTATION_PROBE = r'''({plans})=>{
  const host=document.createElement('section');host.id='patch146-adapt';
  host.style.cssText='width:100%;max-width:100%;min-width:0';
  document.querySelector('#task-manager-app').append(host);
  const mounted=McelUniversalSemanticLayoutLab.mount(host,plans.normal,{lab:true});
  const firstDecision=host.querySelector('[data-profile]')?.dataset.semanticSignature;
  const content=mounted.update(plans.content);
  const refreshed=host.querySelector('mcel-row mcel-field[data-semantic-field="name"] .field-value')?.textContent;
  const structural=mounted.update(plans.retyped);
  const purpose=mounted.update(plans.inspect);
  mounted.destroy();
  const remount=McelUniversalSemanticLayoutLab.mount(host,plans.retyped,{lab:true});
  const structure=host.querySelector('[data-profile]')?.dataset.semanticSignature;
  const retypedFields=[...host.querySelector('mcel-row').children].map(el=>el.dataset.semanticField);
  const summary=host.querySelector('#semantic-observed-status')?.textContent||'';
  remount.destroy();host.remove();
  return {contentAccepted:content.ok===true&&content.decisionUnchanged===true,
    sourceChangedInDOM:refreshed==='a-renamed-process-with-identical-semantic-shape',
    beforeSignature:firstDecision,afterSignature:structure,
    structuralRefused:structural.reason==='structural-change-requires-explicit-remount',
    purposeRefused:purpose.reason==='structural-change-requires-explicit-remount',
    remountDifferent:structure!==firstDecision,
    retypedFieldOrder:retypedFields,
    stateSummaryRemoved:!summary.includes('state: '),
    cleanup:!host.isConnected,
    overflow:document.documentElement.scrollWidth>innerWidth+2};
}'''


def acquire_cost(acquisition: dict) -> tuple[int, int]:
    """Lower = easier *mechanical visual search*, not actual human performance.

    Field disclosed without a row selection is better than hidden; within that,
    scan rank is a weak ordinal proxy. Never combine into a fabricated time.
    """
    if not acquisition['found']:
        return (2, 1000)
    if not acquisition['visible']:
        return (1, acquisition['rank'])
    return (0, acquisition['visibleRank'])


def verify(output: Path = OUTPUT, *, screenshots: bool = True) -> dict:
    from playwright.sync_api import sync_playwright
    source=json.loads(FIXTURE.read_text('utf-8'))
    adapt=adaptation_checks(source)
    report={
        'schema':SCHEMA,'generatedUtc':datetime.now(timezone.utc).isoformat(),
        'engineeringStatus':'FAIL','promiseVerdict':'NOT_ESTABLISHED',
        'profile':PROFILE,'source':'Existing Task Manager HTML + registered MCEL components + synthetic fixture',
        'taskManagerHtml':'main_computer/web/applications/apps/task-manager.html',
        'fixture':'tools/fixtures/mcel_computer_knowledge/'+FIXTURE.name,
        'fixtureSha256':sha(FIXTURE.read_bytes()),
        'preregistration':{'arms':['semantic','blind-order'],'states':list(STATES),
            'widths':list(WIDTHS),'fields':list(TASKS),'controlSeed':CONTROL_SEED,
            'qualityMetric':'compare visible-before-hidden, then visible ordinal rank; no timing claim',
            'qualityGate':'report observed wins/losses; do not require a favorable outcome for engineering PASS',
            'engineeringGate':'zero browser errors/requests, matched raw evidence, collapsed disclosure, '
                'correct keyboard selection, exact recovery, no overflow, semantic intervention '
                'causes remount and content-only changes do not, no effects'},
        'adaptation':{k:v for k,v in adapt.items() if k!='plans'},
        'cases':[],'adaptationBrowser':[], 'errors':[],
        'limitations':[
            'No participant time-to-answer, user errors or independent blind usability ratings measured',
            'One existing application, two snapshot densities, two viewport widths; no full production route',
            'Blinded field order is a deterministic nonsemantic counterfactual, not the production baseline',
            'Known name/state/memory tasks measure mechanical presentation access only',
            'Twelve comparisons reuse three target fields; they are not independent human trials',
            'Retyping is a deliberate synthetic semantic intervention; not production catalog mutation',
        ],'operatorActionsExecuted':False,'productionPromotion':False}
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            for state in STATES:
                case=scenario(state)
                arms={'semantic':case['plan'],'blind-order':blind_control(case['plan'])}
                assert all(p['profile']==PROFILE for p in arms.values())
                # Enforce equal field sets, tiers, exact records, regions, budgets.
                sem=arms['semantic'];blind=arms['blind-order']
                match=(sorted((f['key'],f['kind'],f['tier']) for f in sem['collection']['spec']['fields']) ==
                       sorted((f['key'],f['kind'],f['tier']) for f in blind['collection']['spec']['fields']) and
                       sem['collection']['rows']==blind['collection']['rows'] and
                       sem['tree']==blind['tree'] and sem['regions']==blind['regions'] and
                       sem['summaryFacts']==blind['summaryFacts'])
                for width in WIDTHS:
                    page=browser.new_page(viewport={'width':width,'height':850},reduced_motion='reduce')
                    requests=[];errors=[]
                    page.on('request', lambda r: requests.append(r.url))
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    try:
                        page.set_content(html_fixture(),wait_until='load')
                        page.add_script_tag(path=str(CONTINUITY))
                        page.add_script_tag(path=str(LAB))
                        comparisons={}
                        for arm,plan in arms.items():
                            result=page.evaluate(BROWSER_PROBE,{'plan':plan,'tasks':list(TASKS)})
                            source_row=plan['collection']['rows'][min(1,len(plan['collection']['rows'])-1)]
                            proof=result['proof']
                            # Full canonical object equality including availability,
                            # exact raw values and provenance; not substring evidence.
                            exact=(all(v['displayAccurate'] for v in result['acquisitions']) and
                                   proof['canonicalRecord']==source_row['mcelCanonicalRecord'] and
                                   proof['sourceIdentityMatches'] and proof['selectedCorrectly'] and
                                   proof['internalProof'] and proof['collapsed'] and
                                   proof['disclosureOpens'] and proof['provenanceOpens'] and
                                   proof['rawHiddenVisually'] and proof['humanFactsVisible']>0 and
                                   proof['provenanceConsistent'] and
                                   proof['keyboardFocusPreserved'] and proof['structuralRefused'] and
                                   proof['rejectsUnauthorizedOperations'] and proof['rejectsProductionPromotion'] and
                                   proof['originalControlCount']==5 and proof['regions']==len(plan['regions']) and
                                   proof['observedRows']==len(plan['collection']['rows']) and
                                   not proof['documentOverflow'] and result['cleanup'])
                            # Exclude enormous canonical object from report, but
                            # retain its cryptographic digest and exact booleans.
                            proof['canonicalRecordSha256']=sha(proof.pop('canonicalRecord'))
                            comparisons[arm]={'acquisitions':result['acquisitions'],
                                              'proof':proof,'exactEvidencePassed':exact,
                                              'fieldOrder':[f['key'] for f in plan['collection']['spec']['fields']]}
                            if screenshots and state=='normal':
                                # Capture only the mounted MCEL subcomponent in its
                                # actual host, never a fake/HTML-only screenshot.
                                target=page.evaluate('''plan=>{
                                  const el=document.createElement('section');el.id='patch146-shot';
                                  el.style.cssText='width:100%;max-width:100%;min-width:0';
                                  document.querySelector('#task-manager-app').append(el);
                                  window.__patch146shot=McelUniversalSemanticLayoutLab.mount(el,plan,{lab:true});
                                  el.querySelector('mcel-row')?.click();return true;}''',plan)
                                if target:
                                    fn=f'{state}-{width}-{arm}.png'
                                    page.locator('#patch146-shot').screenshot(path=str(output/fn),animations='disabled',timeout=30000)
                                    comparisons[arm]['screenshot']=fn
                                    page.evaluate('''()=>{window.__patch146shot.destroy();document.querySelector('#patch146-shot').remove();delete window.__patch146shot;}''')
                        measures=[]
                        for task in TASKS:
                            semrec=next(x for x in comparisons['semantic']['acquisitions'] if x['key']==task)
                            ctrrec=next(x for x in comparisons['blind-order']['acquisitions'] if x['key']==task)
                            a,b=acquire_cost(semrec),acquire_cost(ctrrec)
                            measures.append({'task':task,'semanticCost':list(a),'blindCost':list(b),
                                             'comparison':'semantic-win' if a<b else 'blind-win' if b<a else 'tie'})
                        ok=match and all(c['exactEvidencePassed'] for c in comparisons.values()) and not requests and not errors
                        report['cases'].append({'state':state,'width':width,'status':'PASS' if ok else 'FAIL',
                           'equivalentControls':match,'sourceSha256':case['sourceSha256'],
                           'arms':comparisons,'retrieval':measures,
                           'networkRequests':requests,'browserErrors':errors})
                        if state=='normal':
                            result=page.evaluate(ADAPTATION_PROBE,{'plans':adapt['plans']})
                            report['adaptationBrowser'].append({'width':width,**result})
                    except Exception as exc:
                        report['errors'].append(f'{state}/{width}: {type(exc).__name__}: {exc}')
                        report['cases'].append({'state':state,'width':width,'status':'FAIL','error':str(exc)})
                    finally:
                        page.close()
        finally:
            browser.close()
    measures=[t for c in report['cases'] for t in c.get('retrieval',[])]
    counts={name:sum(t['comparison']==name for t in measures)
            for name in ('semantic-win','blind-win','tie')}
    report['retrievalSummary']={'comparisons':len(measures),**counts,
       'interpretation':'Ordinal scripted acquisition proxy; no human timing or user-error observation'}
    struct=adapt['semanticRetyping']
    a=all([adapt['contentChangesWithoutDecisionChange'],adapt['contentChangeActuallyObserved'],
      adapt['purposeChangedSignature'],adapt['purposeChangesRegionSet'],adapt['retainedProfile'],
      struct['sourceByteEquivalent'],struct['semanticSignatureChanged'],struct['fieldOrderChanged'],
      struct['stateCountRemoved']])
    browseradapt=all(c['contentAccepted'] and c['sourceChangedInDOM'] and c['structuralRefused']
      and c['purposeRefused'] and c['remountDifferent'] and c['stateSummaryRemoved']
      and c['cleanup'] and not c['overflow'] for c in report['adaptationBrowser'])
    passed=(len(report['cases'])==len(STATES)*len(WIDTHS) and
      all(c['status']=='PASS' for c in report['cases']) and a and browseradapt and
      len(report['adaptationBrowser'])==len(WIDTHS) and not report['errors'])
    report['engineeringStatus']='PASS' if passed else 'FAIL'
    if passed:
        report['promiseVerdict']='DIRECTIONAL_PROXY_ADVANTAGE' if counts['semantic-win']>counts['blind-win'] else 'NO_MEASURED_PROXY_ADVANTAGE'
    # Even a positive proxy cannot be described as proven interface superiority.
    save(output/'report.json',report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--no-screenshots',action='store_true')
    args=parser.parse_args(argv)
    try:
        result=verify(args.output,screenshots=not args.no_screenshots)
    except Exception as e:
        args.output.mkdir(parents=True,exist_ok=True)
        result={'schema':SCHEMA,'engineeringStatus':'FAIL','error':f'{type(e).__name__}: {e}',
                'operatorActionsExecuted':False,'productionPromotion':False}
        save(args.output/'report.json',result)
    print('MCEL_PATCH14_6_ENGINEERING_'+result['engineeringStatus'])
    print('MCEL_PATCH14_6_PROMISE_'+result.get('promiseVerdict','NOT_ESTABLISHED'))
    print('report:',args.output/'report.json')
    if 'retrievalSummary' in result:print('scripted retrieval:',result['retrievalSummary'])
    if 'error' in result:print('error:',result['error'])
    return 0 if result['engineeringStatus']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
