"""Patch 14.3: one universal profile, equal-budget blind ablation, actual Chromium.

Run: python -m tools.mcel_patch14_universal_composition_frob
No live service, effects, user preferences or production promotion. A PASS is an
*engineering* PASS, not a certified aesthetic / independent quality advantage.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from main_computer.mcel_knowledge.ir_bridge import compile_knowledge_ir, resolve_knowledge, collection_projection
from main_computer.mcel_knowledge.universal_layout import compose, PROFILE, canonical
from main_computer.mcel_knowledge.presentation_policy import load_presentation_policy, apply_presentation_policy

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=ROOT/'tools/fixtures/mcel_computer_knowledge'
REPORT=ROOT/'runtime/reports/mcel-patch14-universal-composition.json'
LAB=ROOT/'main_computer/web/applications/scripts/mcel-universal-semantic-layout-lab.js'
CONTINUITY=ROOT/'main_computer/web/applications/scripts/mcel-layout-continuity.js'
CASES=(('task-manager.processes','task_manager_snapshot.v1.json','monitor'),
       ('file-explorer.entries','expanded_computer_snapshot.v1.json','navigate'),
       ('worker.market-workers','expanded_computer_snapshot.v1.json','monitor'))
BUDGET=4  # preregistered; no per-test oracle selection
WIDTHS=(320,560,1100)
TESTS=['tests/test_mcel_patch14_universal_composition.py',
       'tests/test_mcel_patch14_canonical_tree_continuity.py',
       'tests/test_mcel_patch14_active_continuity.py',
       'tests/test_mcel_patch14_decision_foundations.py',
       'tests/test_mcel_knowledge_ir_bridge.py',
       'tests/test_mcel_patch13_completion.py']


def _input(observation, filename):
    source=json.loads((FIXTURES/filename).read_text('utf-8'))
    bridge=compile_knowledge_ir('universal-frob',observation_ids=[observation])
    view=resolve_knowledge(source,bridge)
    return view


def _candidate_variants(base, order, arm):
    """Same four-order budget. Blind ordering uses hash, not semantic classes.

    No quality metric is consulted to select the candidate. The semantic policy
    selects variant zero, as does the blind frozen policy. Nonselected variants
    constitute best-of-N upper-bound candidates, not the policy's performance.
    """
    fields={f['key']:f for f in base['spec']['fields']}
    keys=[k for k in order if k!='mcelIdentity' and k in fields]
    if arm=='blind':keys=sorted(keys,key=lambda k:hashlib.sha256(k.encode('utf-8')).hexdigest())
    variants=[]
    for offset in range(BUDGET):
        perm=keys[offset:]+keys[:offset]
        opt=deepcopy(base)
        opt['spec']['fields']=[deepcopy(fields['mcelIdentity'])]+[deepcopy(fields[k]) for k in perm]
        variants.append(opt)
    return variants


def browser_ablation(cases):
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch13_live_adoption_frob import _chrome
    from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
    evidence=[];errors=[];screenshots=[]
    review=ROOT/'runtime/reports/mcel-patch14-universal-review'
    review.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        try:
            for case in cases:
                for width in WIDTHS:
                    page=browser.new_page(viewport={'width':width,'height':840},reduced_motion='reduce')
                    page.on('pageerror',lambda err:errors.append(str(err)))
                    try:
                        page.set_content(html_fixture(),wait_until='load')
                        page.add_script_tag(path=str(CONTINUITY))
                        page.add_script_tag(path=str(LAB))
                        for arm in ('baseline','blind','semantic'):
                            plans=case['arms'][arm]
                            # Every candidate is rendered and measured with the SAME
                            # existing v2 collection and canonical layout machinery.
                            for index, projection in enumerate(plans):
                                args={'arm':arm,'index':index,'plan':case['plan'] if arm=='semantic' and index==0 else None,
                                      'projection':projection,
                                      'capture':width==560 and index==0}
                                measured=page.evaluate('''({arm,index,plan,projection,capture})=>{
                                  const target=document.createElement('section');target.id='universal-frob-lab';
                                  target.style.cssText='width:100%;max-width:100%;min-width:0';
                                  document.querySelector('#task-manager-app').append(target);
                                  let obj;
                                  if(plan){
                                    obj=McelUniversalSemanticLayoutLab.mount(target,plan,{lab:true});
                                  }else{
                                    obj=McelSemanticCollectionWorkspace.mount(target,projection.rows,
                                      projection.spec,{observeResize:false,layoutContinuity:'lab'});
                                  }
                                  const first=target.querySelector('mcel-row');
                                  const rect=first?.getBoundingClientRect();
                                  first?.click();
                                  const inspector=target.querySelector('.mcel-semantic-collection__inspector');
                                  const metric={arm,index,rows:target.querySelectorAll('mcel-row').length,
                                    recordWidthPx:rect?+rect.width.toFixed(2):null,
                                    firstRecordY:rect?+rect.top.toFixed(2):null,
                                    documentOverflow:document.documentElement.scrollWidth>innerWidth+2,
                                    exactInspector:!!inspector?.textContent?.includes('mcelCanonicalRecord'),
                                    activeProfile:obj.profile||null,
                                    regionCount:plan?target.querySelectorAll('.mcel-universal-composition__region').length:0};
                                  if(capture){window.__compositionReview={obj,target};}
                                  else {obj.destroy();target.remove();}
                                  return metric;
                                }''',args)
                                measured['width']=width
                                if args['capture']:
                                    destination=review/(case['observation'].replace('.','-')+'-'+arm+'.png')
                                    page.locator('#universal-frob-lab').screenshot(path=str(destination),timeout=12000)
                                    screenshots.append(str(destination.relative_to(ROOT)))
                                    page.evaluate('''()=>{window.__compositionReview.obj.destroy();
                                      window.__compositionReview.target.remove();delete window.__compositionReview;}''')
                                evidence.append({'observation':case['observation'],**measured})
                    finally:page.close()
        finally:browser.close()
    return {'status':'PASS' if not errors else 'FAIL','records':evidence,'pageErrors':errors,'reviewScreenshots':screenshots,
      'note':'Browser geometric and information-integrity measurements only; no human-calibrated quality score'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=REPORT)
    args=parser.parse_args(argv)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    data=[]
    for obs,file,purpose in CASES:
        view=_input(obs,file)
        semantic=compose(view,obs,purpose=purpose)
        baseline=collection_projection(view,obs)
        if obs=='task-manager.processes':
            baseline=apply_presentation_policy(baseline,load_presentation_policy())
        sem=_candidate_variants(semantic['collection'],semantic['decision']['fields'],'semantic')
        blind=_candidate_variants(semantic['collection'],semantic['decision']['fields'],'blind')
        # The candidate generator is permitted to propose alternatives, but the
        # frozen policy always selects index 0. It never picks best per cell.
        data.append({'observation':obs,'purpose':purpose,'plan':semantic,
          'arms':{'baseline':[baseline],'blind':blind,'semantic':sem},
          'candidateBudget':{'semantic':len(sem),'blind':len(blind)},
          'frozenSelected':{'semantic':0,'blind':0},
          'semanticSignature':semantic['decision']['semanticSignature']})
    # Retain ALL candidate definitions for independent review, not just a
    # favorable screenshot or the selected layout.
    review=ROOT/'runtime/reports/mcel-patch14-universal-review'
    review.mkdir(parents=True,exist_ok=True)
    manifest=[{'observation':c['observation'],'purpose':c['purpose'],
      'profile':c['plan']['profile'],'policy':c['plan']['decision']['policy'],
      'capabilities':c['plan']['capabilities'], 'regions':c['plan']['regions'],
      'summaryFacts':c['plan']['summaryFacts'],
      'candidates':{arm:[{'index':i,'selected':i==c['frozenSelected'].get(arm),
        'fields':[f['key'] for f in p['spec']['fields']],
        'tiers':[f['tier'] for f in p['spec']['fields']]}
        for i,p in enumerate(projections)] for arm,projections in c['arms'].items()},
      'selectionNote':'All candidates retained; fixed policy uses candidate zero'}
      for c in data]
    (review/'candidate-manifest.json').write_text(
      json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    (review/'INDEPENDENT_REVIEW.md').write_text(
      '# Patch 14.3 blind comparison packet\n\n'
      'Images show the actual original MCEL v2 renderer, not mocked screenshots.\n'
      'For each domain compare baseline, blind candidate zero, and semantic\n'
      'candidate zero (same 560px viewport and identical source-shaped records).\n'
      'All alternatives and field tiers are recorded in candidate-manifest.json.\n\n'
      'Reviewer tasks: find key records; distinguish unavailable from zero;\n'
      'recover full raw properties; evaluate reading order, hierarchy,\n'
      'readability, technical clutter, and information-retrieval effort.\n'
      'Collect pairwise judgments without revealing arm names where practical.\n'
      'Do not mistake a read-only operation declaration for authority.\n\n'
      'These fixtures are known to the experimental design, not sealed holdouts.\n'
      'No independent judge has evaluated this packet. Engineering PASS does\n'
      'NOT certify superior quality or prove that one universal profile suffices.\n',
      encoding='utf-8')
    browser=browser_ablation(data)
    selected=[t for t in TESTS if (ROOT/t).is_file()]
    missing=sorted(set(TESTS)-set(selected))
    run=subprocess.run([sys.executable,'-m','pytest','-q',*selected],cwd=ROOT,capture_output=True,text=True,timeout=240)
    records=browser['records']
    budget=all(c['candidateBudget']=={'semantic':BUDGET,'blind':BUDGET} for c in data)
    selected_metrics=[m for m in records if m['index']==0]
    hard_ok=all(not m['documentOverflow'] and m['rows']>0 and m['exactInspector'] for m in records)
    # Intentional independent quality status: no human/critic labels supplied.
    checks={
      'single-universal-profile':all(c['plan']['profile']==PROFILE for c in data),
      'actual-three-domain-semantic-projections':all(c['plan']['collection']['rows'] for c in data),
      'equal-search-budget':budget,
      'frozen-selector-no-per-cell-oracle':all(c['frozenSelected']=={'semantic':0,'blind':0} for c in data),
      'existing-v2-and-canonical-renderer':all(m['rows']>0 for m in selected_metrics),
      'browser-no-overflow-source-inspection':hard_ok,
      'minimum-comparable-record-width':all(m['recordWidthPx'] is not None and m['recordWidthPx']>=240-1 for m in records),
      'no-browser-errors':browser['status']=='PASS',
      'existing-layout-and-semantic-regressions':run.returncode==0 and not missing,
      'no-runtime-effects':all(c['plan']['readOnly'] and not c['plan']['productionPromotion'] for c in data),
    }
    status='PASS' if all(checks.values()) else 'FAIL'
    report={'schema':'mcel.patch14.3-universal-composition-frob.v1','status':status,
      'scope':'experimental one-profile semantic composition; no runtime production adoption',
      'policy':'mcel.universal-composer-policy.v1','profile':PROFILE,
      'checks':checks,'cases':[{k:v for k,v in c.items() if k not in ('plan','arms')} for c in data],
      'comparison':{'equalBudgetPerArm':BUDGET,'widths':WIDTHS,
         'currentBaseline':'Task Manager: existing authored v2 compatibility overlay; other domains: generic v2 baseline',
         'blindControl':'hash-ordered field candidates, semantic meaning not used in ranking',
         'semanticControl':'type-and-purpose-ordered fields, same candidate count',
         'selectedPolicy':'fixed candidate 0 in both arms; never oracle-selected per case',
         'bestOfN':'not scored; retained browser records permit independent later review',
         'semanticQualityAdvantage':'UNVERIFIED — independent quality evaluator not calibrated',
         'heldOutQuality':'UNVERIFIED — this is source-domain transfer, not sealed unknown apps',
         'architectureGenerality':'PASS' if checks['actual-three-domain-semantic-projections'] else 'FAIL'},
      'browserEvidence':browser,
      'reviewArtifacts':{'screenshots':browser.get('reviewScreenshots',[]),
       'status':'AVAILABLE_FOR_INDEPENDENT_REVIEW; not independently judged'},
      'gates':{'G0-existing-architecture':'PASS' if checks['existing-v2-and-canonical-renderer'] else 'FAIL',
       'G1-composition':'PASS' if checks['single-universal-profile'] else 'FAIL',
       'G2-cross-domain':'PASS' if checks['actual-three-domain-semantic-projections'] else 'FAIL',
       'G3-determinism':'PASS' if checks['frozen-selector-no-per-cell-oracle'] else 'FAIL',
       'G4-stability':'PARTIAL — composition commitment tested; real source generation not certified here',
       'G5-correctness':'PASS' if hard_ok and checks['minimum-comparable-record-width'] else 'FAIL',
       'G6-semantic-value':'UNVERIFIED — browser metrics alone do not establish design quality',
       'G7-independent-judge':'UNVERIFIED — no blind human/critic judgments',
       'G8-authoring-economy':'PARTIAL — no application-authored region assignments, no total system LOC claim'},
      'testOutput':(run.stdout+'\n'+run.stderr)[-7000:],
      'missingTests':missing,'productionPromotion':False,'runtimeEffectsExecuted':False,
      'knownLimitations':[
        'The one-profile lab composes read-only collection/summary/inspector; it cannot yet infer all full application controls.',
        'File Explorer parent-child hierarchy is not verified in the catalog; navigation remains a collection.',
        'Worker data supplies candidate-only lease relationships; no authorized action is constructed.',
        'Independent usability and human-calibrated scores are not available; semantic superiority is NOT established.',
        'No hosted Windows Task Manager acceptance; no production promotion.',
        'Layout policy is frozen before evaluation, but held-out domains were known to the experiment design.',
      ]}
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf-8')
    print('MCEL_PATCH14_UNIVERSAL_COMPOSITION_'+status)
    for k,v in checks.items():print(('PASS' if v else 'FAIL')+' '+k)
    print('G6 semantic advantage:',report['gates']['G6-semantic-value'])
    print('G7 independent quality:',report['gates']['G7-independent-judge'])
    print('report:',args.output)
    print(run.stdout[-1100:])
    return 0 if status=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
