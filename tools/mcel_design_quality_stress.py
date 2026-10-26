"""Adversarial, browser-measured validation of the MCEL *experimental* quality policy.

No critic review is consulted, no policy weights are fitted, and production MCEL
selection is unchanged. Stress actual canonical A/B/C candidates with synthetic
fixture mutations. Report hard violations, marginal winners, unsupported tasks,
and winner sensitivity rather than certifying attractiveness.

 python tools/mcel_design_quality_stress.py --mode run
 python tools/mcel_design_quality_stress.py --mode evaluate --review-root runtime/reports/mcel-layout-frob/design-review
 python tools/mcel_layout_frob.py --mode design-quality-stress
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from collections import Counter
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from tools import mcel_design_quality_frob as quality
from tools import mcel_design_quality_validity as validity
from tools import mcel_design_critic_frob as critic
from tools.mcel_browser_launch import launch_chromium

SCHEMA = 'mcel.design-quality-stress.v3'
POLICY = 'mcel.experimental-quality-stress.v1'
TIE_MARGIN = 0.02  # preregistered absolute-utility indifference band, not aesthetic truth

@dataclass(frozen=True)
class Condition:
    id: str
    width: int
    fixture: str = 'default'
    font_scale: float = 1.0
    rail: str = 'original'
    selection: str = 'none'

# Deliberately include both single-axis and compound perturbations. No result
# is assumed a PASS merely because the browser mounted a valid DOM.
CONDITIONS = (
    Condition('browse-mobile', 390),
    Condition('browse-desktop', 1440),
    Condition('narrow-320', 320),
    Condition('tablet-768', 768),
    Condition('crowded-mobile', 390, 'crowded'),
    Condition('long-text-desktop', 1440, 'long_text'),
    Condition('rail-heavy-desktop', 1440, 'default', 1, 'dense'),
    Condition('rail-heavy-mobile', 390, 'default', 1, 'dense'),
    Condition('large-type-mobile', 390, 'default', 1.5),
    Condition('large-type-desktop', 1440, 'default', 1.5),
    Condition('inspect-mobile', 390, 'default', 1, 'original', 'selected'),
    Condition('inspect-desktop', 1440, 'default', 1, 'original', 'selected'),
    Condition('combined-adversarial-mobile', 390, 'long_text', 1.5, 'dense', 'selected'),
    Condition('failure-mobile', 390, 'operation_verified_failure'),
    Condition('failure-desktop', 1440, 'operation_verified_failure'),
    Condition('failure-large-type', 1440, 'operation_verified_failure', 1.5),
    Condition('uncertain-mobile', 390, 'unknown'),
    Condition('uncertain-desktop', 1440, 'unknown'),
)

# Synthetic perturbations never rewrite model truth, alter MCEL element IDs, or
# introduce live actions. The rail mutation is explicitly research-only content.
_ADVERSARIAL_GEOMETRY_JS = r'''()=>{
 const rows=[...document.querySelectorAll('.record')].filter(el=>!el.hidden&&el.getClientRects().length);
 const r=el=>el.getBoundingClientRect();
 let pairs=0;
 for(let i=0;i<rows.length;i++)for(let j=i+1;j<rows.length;j++){
  const a=r(rows[i]),b=r(rows[j]);
  if(Math.min(a.right,b.right)-Math.max(a.left,b.left)>2 &&
     Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>2)pairs++;
 }
 return {overlapping_record_pairs:pairs,visible_records:rows.length,
         document_width:document.documentElement.scrollWidth,viewport_width:innerWidth};
}'''

STRESS_JS = r'''({fontScale,rail,selection})=>{
 const root=document.querySelector('#generated-root');
 if(!root)throw Error('Missing generated root');
 let inserted=0, scaled=0;
 if(rail==='dense'){
  const tools=root.querySelector('[data-role="supporting-tools"]');
  if(!tools)throw Error('Missing supporting-tools region');
  const fixture=document.createElement('p');fixture.dataset.stressFixture='dense-rail';
  fixture.textContent='Synthetic tools density: several additional available operations and configuration controls. Review permissions, schedule, activity history, refresh controls and data sources.';
  tools.append(fixture); inserted=1;
 }else if(rail!=='original')throw Error('Unknown rail occupancy fixture');
 if(fontScale!==1){
  if(!(fontScale>=1&&fontScale<=2))throw Error('Invalid font scale');
  // Pixel sizes are common in the existing generated UI. Font inheritance
  // alone would not stress those; increase actual computed text sizes.
  for(const el of root.querySelectorAll('h1,h2,h3,p,span,dt,dd,small,li,label,button,input,summary,.record')){
   const size=parseFloat(getComputedStyle(el).fontSize);
   if(Number.isFinite(size)&&size>0){el.style.fontSize=(size*fontScale)+'px';scaled++;}
  }
 }
 if(selection==='selected'){
  const row=[...root.querySelectorAll('.record')].find(r=>!r.hidden&&r.getClientRects().length);
  if(!row)throw Error('No record to select');row.click();
  const details=root.querySelector('#selected-entity');
  if(!details||!details.textContent.trim())throw Error('Selection did not reveal inspector');
 }else if(selection!=='none')throw Error('Unknown selection fixture');
 return {fontScale,rail,selection,inserted,scaled,
   selectedDetailsVisible:!!root.querySelector('#selected-entity')?.textContent.trim(),
   actualStressRail:!!root.querySelector('[data-stress-fixture="dense-rail"]')};
}'''


def classify_context(condition):
    if condition.fixture == 'operation_verified_failure': return 'recover'
    if condition.selection == 'selected': return 'inspect'
    if condition.fixture == 'unknown': return None  # existing v1 has no uncertainty objective
    return 'browse'


def classify_selection(measured, condition, *, margin=TIE_MARGIN):
    """Abstain on v1-unsupported contexts or narrow experimental score gaps."""
    feasible = [case for case in measured if case['hard_status']=='PASS']
    if not feasible:
        return {'status':'REFUSED','winner':None,'reason':'No feasible candidate','margin':None}
    task = classify_context(condition)
    if task in (None, 'inspect'):
        return {'status':'UNSUPPORTED_TASK','winner':None,
                'reason':('The v1 quality policy has no objective for inspection' if task=='inspect' else 'The v1 quality policy has no objective for unknown outcomes'), 'margin':None}
    feasible = sorted(feasible,key=lambda r:(-r['utility'],r['source_sha256']))
    if len(feasible)==1:
        return {'status':'SINGLE_FEASIBLE','winner':feasible[0]['candidate'],
                'reason':'Only one candidate survived hard constraints; no quality preference established','margin':None}
    gap=round(feasible[0]['utility']-feasible[1]['utility'],6)
    if gap < margin:
        return {'status':'NEAR_TIE','winner':None,'top_candidates':[feasible[0]['candidate'],feasible[1]['candidate']],
                'reason':'Utility margin below preregistered indifference threshold','margin':gap}
    return {'status':'DECISIVE_EXPERIMENTAL','winner':feasible[0]['candidate'],
            'runner_up':feasible[1]['candidate'],'reason':'Feasible candidates; utility margin exceeds threshold',
            'margin':gap}


def verify_unknown(measurement):
    problems=[]
    if measurement.get('operation_state') != 'unknown':problems.append('unknown_outcome_falsified')
    if measurement.get('failure_text_visible'):problems.append('unverified_failure_presented_as_verified')
    return problems


def sensitivity(selections):
    """Show reversals rather than assuming context-invariant preferences."""
    controls={s['condition']:{'status':s['status'],'winner':s.get('winner')} for s in selections}
    refs={390:controls.get('browse-mobile'),1440:controls.get('browse-desktop')}
    diagnostics=[]
    for s in selections:
        width=s['width'];base=refs.get(width)
        if not base or s['condition'] in ('browse-mobile','browse-desktop'):continue
        change=(s.get('winner')!=base.get('winner')) if s.get('winner') and base.get('winner') else None
        diagnostics.append({'condition':s['condition'],'baseline':('browse-mobile' if width==390 else 'browse-desktop'),
                            'baseline_winner':base.get('winner'),'current_winner':s.get('winner'),
                            'winner_reversal':change,'comparison_supported':change is not None})
    return diagnostics


def write_gallery(out,report):
    cards=[]
    validity_by={s['condition']:s for s in report['measurement_validity']['scenarios']}
    by={(r['condition'],r['candidate']):r for r in report['measurements']}
    for decision in report['selections']:
        items=[]
        for cid in 'ABC':
            row=by[decision['condition'],cid]
            img='screenshots/'+row['screenshot']
            findings=', '.join(row['hard_reasons']) if row['hard_reasons'] else 'No observed hard violations'
            items.append('<article><h3>'+cid+'</h3><p>Hard '+row['hard_status']+' · utility '+str(row['utility'])+'</p><a href="'+html.escape(img,quote=True)+'"><img loading="lazy" src="'+html.escape(img,quote=True)+'" alt="'+html.escape(row['condition']+' '+cid)+'"></a><p>'+html.escape(findings)+'</p></article>')
        audit=validity_by[decision['condition']]
        eq=', '.join('/'.join(g) for g in audit['spatial_equivalence_groups']) or 'none'
        cards.append('<section><h2>'+html.escape(decision['condition'])+' · '+html.escape(decision['status'])+'</h2><p>Winner: '+html.escape(str(decision.get('winner')))+' · '+html.escape(str(decision['reason']))+' · structural equivalence: '+html.escape(eq)+'</p><div class="triple">'+''.join(items)+'</div></section>')
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MCEL optimizer stress evidence</title><style>body{font:14px/1.5 system-ui;max-width:1440px;margin:20px auto;padding:0 20px;background:#f5f7fa;color:#203040}.triple{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:14px}article{padding:12px;background:white;border:1px solid #c5d2e0;border-radius:8px;min-width:0}img{max-width:100%;height:auto;max-height:480px;object-fit:contain;object-position:top}article p{font-size:12px}section{margin:32px 0}h2{font-size:18px}</style><h1>MCEL design-quality measurement validity — v3</h1><p>Experimental scoring is unchanged; winners are withheld when fixtures are ineffective, candidates geometrically identical, objectives unsupported, or scoring causality confounded. All screenshots are synthetic; human approval pending.</p><p><a href="validity-report.json">Measurement validity report (JSON)</a> · <a href="stress-report.json">Full experiment report (JSON)</a></p>'''+''.join(cards)+'</html>'
    (out/'stress-gallery.html').write_text(page,encoding='utf-8')


def archive(out,report):
    entries=['stress-report.json','validity-report.json','stress-gallery.html']+[str(f.relative_to(out)).replace(os.sep,'/') for f in sorted((out/'screenshots').glob('*.png'))]
    with zipfile.ZipFile(out/'stress-review.zip','w',zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        for name in entries:z.write(out/name,name)
    with zipfile.ZipFile(out/'stress-review.zip') as z:
        if z.testzip():raise ValueError('Corrupted stress evidence archive')
        if len([n for n in z.namelist() if n.endswith('.png')])!=len(report['measurements']):
            raise ValueError('Missing stress screenshots')


def run_stress(review_root,output,*,conditions=CONDITIONS,chromium_executable=None):
    """Measure perturbations against frozen candidate HTML in new Chromium pages."""
    from playwright.sync_api import sync_playwright
    review_root=quality._find_review_root(review_root)
    output=Path(output).resolve();(output/'screenshots').mkdir(parents=True,exist_ok=True)
    packet, _=critic.open_packet(review_root)
    registered_count=packet['component_count']
    sources={cid:(review_root/f'generated-{cid}'/'preview.html').read_text(encoding='utf-8') for cid in 'ABC'}
    digests={cid:quality.digest_bytes(sources[cid].encode('utf-8')) for cid in 'ABC'}
    if len(conditions)!=len({c.id for c in conditions}):raise ValueError('Duplicate stress condition id')
    measurements=[];browser_info={}
    with sync_playwright() as pw:
        browser,launch=launch_chromium(pw,executable=chromium_executable,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
        browser_info={'version':browser.version,'launch':launch}
        try:
            for condition in conditions:
                if condition.width<320 or condition.width>2560:raise ValueError('Invalid stress viewport')
                if condition.fixture not in ('default','crowded','long_text','operation_verified_failure','unknown'):
                    raise ValueError('Unsupported stress fixture')
                for cid in 'ABC':
                    page=browser.new_page(viewport={'width':condition.width,'height':quality.VIEWPORT_HEIGHT},device_scale_factor=1)
                    errors=[]
                    page.on('pageerror',lambda error,errors=errors:errors.append(str(error)))
                    try:
                        page.set_content(sources[cid],wait_until='load',timeout=30000)
                        page.wait_for_function('window.__MCEL_GENERATED_READY === true',timeout=15000)
                        if condition.fixture!='default':
                            state=page.evaluate('(v)=>window.__MCEL_SET_VISUAL_FIXTURE(v)',condition.fixture)
                            if state.get('state')!=condition.fixture:raise ValueError('Fixture mutation refused')
                        stress=page.evaluate(STRESS_JS,{'fontScale':condition.font_scale,'rail':condition.rail,
                                                       'selection':condition.selection})
                        page.wait_for_timeout(90)
                        observed=quality.browser_measure(page,registered_count)
                        geometric=page.evaluate(_ADVERSARIAL_GEOMETRY_JS)
                        rendered=page.evaluate(validity.RENDER_WITNESS_JS)
                        reasons=quality.feasibility(observed, 'operation_verified_failure' if condition.fixture=='operation_verified_failure' else 'default')
                        if geometric['overlapping_record_pairs']:
                            reasons.append('record_cards_overlap')
                        if condition.fixture=='unknown':reasons+=verify_unknown(observed)
                        if condition.selection=='selected' and not stress['selectedDetailsVisible']:
                            reasons.append('selected_details_inaccessible')
                        if condition.rail=='dense' and not stress['actualStressRail']:
                            reasons.append('rail_mutation_not_applied')
                        if condition.font_scale>1 and stress['scaled']<1:
                            reasons.append('text_scale_not_applied')
                        if condition.fixture in ('crowded','long_text') and observed['visual_state']!=condition.fixture:
                            reasons.append('fixture_not_applied')
                        if errors:reasons.append('javascript_errors')
                        task=classify_context(condition)
                        metrics=quality.quality_metrics(observed,'operation_verified_failure' if task=='recover' else 'default')
                        utility=quality.score_candidate(metrics,task) if task in ('browse','recover') else None
                        filename=f'{condition.id}-{cid}.png'
                        dest=output/'screenshots'/filename
                        page.screenshot(path=str(dest),full_page=True,timeout=20000)
                        row={'condition':condition.id,'candidate':cid,'viewport':condition.width,'fixture':condition.fixture,
                             'font_scale':condition.font_scale,'rail':condition.rail,'selection':condition.selection,
                             'task':task,'source_sha256':digests[cid],
                             'hard_status':'PASS' if not reasons else 'FAIL','hard_reasons':reasons,
                             'utility':utility,'quality_dimensions':metrics,
                             'observed':observed,'adversarial_geometry':geometric,'stress_witness':stress,'render_witness':rendered,'errors':errors,
                             'screenshot':filename,'screenshot_sha256':quality.digest_bytes(dest.read_bytes())}
                        measurements.append(row)
                        print('STRESS_CASE',condition.id,cid,row['hard_status'],utility,flush=True)
                    finally:
                        page.close()
        finally:browser.close()
    validity_report=validity.audit(measurements,conditions)
    validity_by={s['condition']:s for s in validity_report['scenarios']}
    selections=[]
    for c in conditions:
        matching=[r for r in measurements if r['condition']==c.id]
        if {r['candidate'] for r in matching}!={'A','B','C'}:raise ValueError('Incomplete adversarial comparison')
        raw_decision=classify_selection(matching,c)
        decision=validity.guarded_selection(raw_decision,validity_by[c.id],matching)
        selections.append({'condition':c.id,'width':c.width,'fixture':c.fixture,'task':classify_context(c),
                           **decision,'original_unvalidated_selection':raw_decision})
    outcomes=Counter(d['status'] for d in selections)
    hard=Counter(r['hard_status'] for r in measurements)
    status='STRESS_COMPLETED_WITH_DEFECTS' if hard['FAIL'] or outcomes['NEAR_TIE'] or outcomes['UNSUPPORTED_TASK'] or outcomes['REFUSED'] else 'STRESS_COMPLETED'
    report={'schema':SCHEMA,'status':status,'policy_version':POLICY,'evaluated_optimizer_policy':quality.POLICY_VERSION,
            'production_policy_changed':False,'human_approval':'PENDING',
            'packet_sha256':quality.canonical_hash(packet),'browser':browser_info,'source_sha256':digests,
            'scenario_contract':[asdict(c) for c in conditions],
            'margin_policy':{'near_tie_absolute_utility':TIE_MARGIN,'near_tie_means_no_winner':True},
            'measurements':measurements,'selections':selections,'sensitivity':sensitivity(selections),
            'measurement_validity':validity_report,
            'counts':{'measurements':len(measurements),'hard':dict(hard),'selections':dict(outcomes)},
            'limitations':['Synthetic data/rail/text alterations are test fixtures, not real service data',
                           'A/B/C originate in one composition family; no independent human approval',
                           'Quality policy weights unchanged and not tuned against this stress run',
                           'Unknown-outcome ranking unsupported by v1 (explicit abstention)',
                           'Measured font growth does not certify accessibility at system zoom',
                           'Design appearance cannot be certified from geometric scores alone']}
    quality.jwrite(output/'stress-report.json',report)
    quality.jwrite(output/'validity-report.json',validity_report)
    write_gallery(output,report);archive(output,report)
    print('MCEL_DESIGN_QUALITY_STRESS_'+status)
    print('cases:',len(measurements),'hard:',dict(hard),'selections:',dict(outcomes))
    print('measurement_validity:',validity_report['status'],validity_report['counts'])
    print('report:',output/'stress-report.json')
    print('gallery:',output/'stress-gallery.html')
    print('review_zip:',output/'stress-review.zip')
    return report


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--mode',choices=('run','evaluate'),default='run')
    ap.add_argument('--design',type=Path,default=critic.generator.DEFAULT_DESIGN)
    ap.add_argument('--output',type=Path,default=ROOT/'runtime/reports/mcel-layout-frob/design-quality-stress')
    ap.add_argument('--review-root',type=Path)
    ap.add_argument('--chromium-executable')
    args=ap.parse_args(argv)
    output=args.output.resolve();review_root=(args.review_root or output/'design-review').resolve()
    try:
        if args.mode=='run':
            result=critic.build(args.design,review_root,chromium_executable=args.chromium_executable)
            if result['status']!='PASS':raise ValueError('Candidate generation failed browser proof')
        report=run_stress(review_root,output,chromium_executable=args.chromium_executable)
        return 0 if report['status'].startswith('STRESS_COMPLETED') else 1
    except (ValueError,RuntimeError,OSError,TimeoutError) as exc:
        print('MCEL_DESIGN_QUALITY_STRESS_REFUSED:',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
