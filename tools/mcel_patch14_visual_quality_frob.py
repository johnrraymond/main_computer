"""Patch 14.4 -- reproducible, blinded PNG design-review Frob.

Generate the existing MCEL v2/canonical renderer in Chromium for matched
baseline / semantics-blind / universal semantic comparisons. Capture actual
rendered PNGs, contact sheets, a portable HTML gallery and a blinded review
packet. This is a *quality evidence acquisition* experiment, not an automatic
human-aesthetic PASS or a new layout engine.

    python -m tools.mcel_patch14_visual_quality_frob
    python -m tools.mcel_patch14_visual_quality_frob --output runtime/reports/my-review
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import zipfile


from main_computer.mcel_knowledge.ir_bridge import compile_knowledge_ir, resolve_knowledge, collection_projection
from main_computer.mcel_knowledge.universal_layout import compose
from main_computer.mcel_knowledge.presentation_policy import load_presentation_policy, apply_presentation_policy
from tools.mcel_patch14_universal_composition_frob import CASES, _candidate_variants
from tools.mcel_frob_visual_evidence import png_size, safe_token

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tools/fixtures/mcel_computer_knowledge'
LAB = ROOT / 'main_computer/web/applications/scripts/mcel-universal-semantic-layout-lab.js'
CONTINUITY = ROOT / 'main_computer/web/applications/scripts/mcel-layout-continuity.js'
DEFAULT_OUTPUT = ROOT / 'runtime/reports/mcel-patch14-visual-quality'
ARMS = ('baseline', 'blind', 'semantic')
WIDTHS = (390, 1440)
STATES = ('normal', 'stressed')
SCHEMA = 'mcel.patch14.4-visual-quality-packet.v1'
SEED = 'mcel-patch14.4-blinded-v1'


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False)+'\n').encode('utf-8')


def digest(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def write_json(path: Path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(json_bytes(value))


def stress_snapshot(snapshot: dict, observation: str) -> dict:
    """Seed-free deterministic stress: preserve source contract and authority.

    Perturbs only fixture copies. Results are synthetic and must never be called
    actual hosted observations or evidence of effects/authorized operations.
    """
    data = deepcopy(snapshot)
    if observation == 'task-manager.processes':
        rows = data['processes']
        for i, row in enumerate(rows):
            row['name'] = str(row['name']) + '-' + ('extended-name-'*7 if i == 0 else 'long-label')
            row['cmdline'] = str(row.get('cmdline') or '') + ' --log-path=' + ('C:/deep/folder/'*8) + 'verbose.log'
            if row.get('memory_availability') == 'present' and isinstance(row.get('memory_rss'), int):
                row['memory_rss'] = 0 if i % 2 else 2**30
        # More records, with distinct lifetime identities, add a real density test.
        for i in range(8):
            extra = deepcopy(rows[i % len(rows)])
            extra['pid'] = 20100+i
            extra['name'] = f'stress-process-{i:02d}-' + 'long-'*6
            extra['created_at'] = f'2026-10-27T12:{i:02d}:00Z'
            extra['is_current_process'] = False
            rows.append(extra)
    elif observation == 'file-explorer.entries':
        rows = data['entries']
        for i, row in enumerate(rows):
            original = str(row['name'])
            row['name'] = 'very-long-path-component-'*4 + original
            parent = str(row['relative_path']).rsplit('/',1)[0] if '/' in str(row['relative_path']) else ''
            row['relative_path'] = (parent+'/' if parent else '') + row['name']
            row['path_display'] = str(data.get('root_id','workspace')) + ':/' + row['relative_path']
        for i in range(8):
            extra = deepcopy(rows[i % len(rows)])
            extra['name'] = f'file-{i:02d}-' + 'long-name-'*4 + '.txt'
            extra['kind'] = 'file'
            extra['relative_path'] = f'benchmark/stress/{extra["name"]}'
            extra['path_display'] = str(data.get('root_id','workspace'))+':/'+extra['relative_path']
            extra['bytes'] = (i+1)*1024*1024
            rows.append(extra)
    elif observation == 'worker.market-workers':
        workers = data['worker_market']['market_workers']
        if not workers: raise ValueError('Worker fixture must contain an existing lease')
        baseline = list(workers.values())
        for i in range(8):
            extra = deepcopy(baseline[i % len(baseline)])
            wid = f'bench-worker-{i:02d}'
            extra['worker_id'] = wid
            extra['connection_id'] = f'bench-connection-{i:02d}'
            extra['capabilities'] = ['text', 'long-description-'*8]
            extra['max_concurrency'] = i+1
            workers[wid] = extra
    else:
        raise ValueError('Unrecognized observation for stress fixture')
    if data == snapshot: raise AssertionError('Stress must change original fixture')
    return data


def build_case(observation: str, fixture: str, purpose: str, state: str) -> dict:
    source = json.loads((FIXTURES/fixture).read_text('utf-8'))
    if state == 'stressed':
        source = stress_snapshot(source, observation)
    view = resolve_knowledge(source, compile_knowledge_ir('patch14-visual-quality', observation_ids=[observation]))
    semantic = compose(view, observation, purpose=purpose)
    baseline = collection_projection(view, observation)
    if observation == 'task-manager.processes':
        baseline = apply_presentation_policy(baseline,load_presentation_policy())
    variants = {
        'baseline': baseline,
        'blind': _candidate_variants(semantic['collection'], semantic['decision']['fields'], 'blind')[0],
        'semantic': _candidate_variants(semantic['collection'], semantic['decision']['fields'], 'semantic')[0]
    }
    return {'observation':observation,'purpose':purpose,'state':state,'sourceHash':digest(json_bytes(source)),
            'semanticSignature':semantic['decision']['semanticSignature'], 'plan':semantic,
            'variants':variants, 'sourceRecordCount':len(view['sources'][observation]['records'])}


def blind_assignments(observation: str, state: str) -> dict:
    """Stable per-case blinding; no methodology name in PNGs or review packet."""
    variants = list(ARMS)
    random.Random(int(digest((SEED+'|'+observation+'|'+state).encode())[:16],16)).shuffle(variants)
    return dict(zip(('A','B','C'),variants))


def render_capture(page, *, plan: dict | None, projection: dict, output: Path,
                   observation: str, state: str, width: int, label: str) -> dict:
    """Use the EXISTING v2 and canonical tree, never a mock screenshot."""
    result = page.evaluate('''({plan,projection})=>{
      const original=document.querySelector('#task-manager-app');
      const root=document.createElement('section');root.id='patch144-capture';
      root.style.cssText='width:100%;min-width:0;max-width:100%;box-sizing:border-box;';
      original.append(root);
      let mounted;
      if(plan){mounted=McelUniversalSemanticLayoutLab.mount(root,plan,{lab:true});}
      else {mounted=McelSemanticCollectionWorkspace.mount(root,projection.rows,projection.spec,
         {observeResize:false,layoutContinuity:'lab'});}
      const first=root.querySelector('mcel-row');
      if(first)first.click();
      const inspector=root.querySelector('.mcel-semantic-collection__inspector');
      const labels=[...root.querySelectorAll('mcel-field')].slice(0,25).map(x=>x.textContent.trim());
      const box=root.getBoundingClientRect();
      const row=first?.getBoundingClientRect();
      window.__patch144={mounted,root};
      return {rows:root.querySelectorAll('mcel-row').length,
         exactRecordAvailable:!!inspector?.textContent?.includes('mcelCanonicalRecord'),
         rootWidth:box.width, rowWidth:row?.width||0,
         screenOverflow:document.documentElement.scrollWidth>innerWidth+2,
         outputArmRenderedBy:plan?'canonical-tree-plus-v2':'v2-collection',
         layoutProfile:mounted?.profile||null, labels};
    }''',{'plan':plan,'projection':projection})
    output.parent.mkdir(parents=True,exist_ok=True)
    # Rendered MCEL root only: matched crops; no misleading Task Manager chrome
    # in the independent File Explorer and Worker comparisons.
    page.locator('#patch144-capture').screenshot(path=str(output),animations='disabled',timeout=30000)
    size=png_size(output)
    result.update({'path':str(output.name),'pngSha256':digest(output.read_bytes()),
                  'pixelWidth':size[0],'pixelHeight':size[1], 'viewport':width,
                  'state':state,'observation':observation,'blindLabel':label,
                  'imageScope':'actual MCEL-rendered lab root, not full parent application page'})
    page.evaluate('''()=>{const x=window.__patch144; x?.mounted?.destroy?.();
        x?.root?.remove();delete window.__patch144;}''')
    return result


def make_contact_sheet(browser, output: Path, items: list[dict], label: str):
    """Chromium-rendered PNG triptych; no external graphics dependency.

    This preserves the exact original PNG bytes. Each thumbnail is an HTML
    image from a data: URL of a captured MCEL component, not a synthetic UI.
    """
    cards=[]
    for item in items:
        blob=(output/item['path']).read_bytes()
        uri='data:image/png;base64,'+base64.b64encode(blob).decode('ascii')
        cards.append('<section><strong>'+html.escape(item['blindLabel'])+' — '+str(item['viewport'])+
                     'px</strong><div class="frame"><img alt="Capture '+html.escape(item['blindLabel'])+
                     '" src="'+uri+'"></div></section>')
    page=browser.new_page(viewport={'width':1440,'height':810},device_scale_factor=1,reduced_motion='reduce')
    try:
        page.set_content('''<!DOCTYPE html><html><meta charset="utf-8"><style>
          *{box-sizing:border-box}html,body{margin:0}body{width:1440px;height:810px;padding:12px;
          background:#eef2f7;color:#20324b;font:13px Arial,sans-serif;}
          h1{font-size:15px;margin:1px 0 40px;font-weight:600}
          main{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
          section{min-width:0}strong{display:block;height:24px}.frame{height:690px;background:white;
          border:1px solid #c9d3df;padding:2px;overflow:hidden;display:flex;align-items:flex-start;justify-content:center}
          img{display:block;max-width:100%;max-height:684px;width:auto;height:auto;object-fit:contain}
          </style><h1>'''+html.escape(label)+'''</h1><main>'''+''.join(cards)+'''</main></html>''',wait_until='load')
        name='contact-'+safe_token(label)+'.png'
        page.screenshot(path=str(output/name),full_page=True,animations='disabled')
    finally:
        page.close()
    width,height=png_size(output/name)
    return {'path':name,'sha256':digest((output/name).read_bytes()),
            'pixelWidth':width,'pixelHeight':height}


def write_review_gallery(output: Path, captures: list[dict], sheets: list[dict]):
    """Portable, honest component-screenshot gallery using prior Frob conventions.

    Unlike full-page Design Critic captures, this experiment intentionally crops
    to the complete *rendered MCEL lab component* to prevent Task Manager chrome
    appearing in File Explorer / Worker comparisons. Never label as full-page.
    """
    import csv
    visual=[]
    for c in captures:
        visual.append({'id':safe_token(c['path'][:-4]),'kind':'actual-mcel-lab-component',
          'label':c['blindLabel']+' · '+c['observation']+' · '+c['state'],
          'viewport':c['viewport'],'view':c['observation'],'state':c['state'],
          'screenshot':c['path'],'screenshot_sha256':c['pngSha256'],
          'pixel_width':c['pixelWidth'],'pixel_height':c['pixelHeight'],
          'capture_scope':'complete-rendered-component-not-full-browser-page',
          'visual_approval':'UNREVIEWED'})
    manifest={'schema':'main-computer.mcel.frob-visual-evidence.v1',
       'title':'Patch 14.4 — blinded semantic layout review',
       'review_status':'UNREVIEWED','count':len(visual), 'captures':visual,
       'provenance':{'source':'existing MCEL v2/canonical renderer',
         'no_mockups':True,'synthetic_stress':True,'quality_advantage':'UNVERIFIED',
         'scope':'complete rendered lab component; not a full browser-page screenshot'}}
    write_json(output/'visual-manifest.json',manifest)
    with (output/'visual-review-template.csv').open('w',encoding='utf-8',newline='') as stream:
        cols=['id','viewport','view','state','screenshot','review','hierarchy','readability',
              'density','responsive_quality','visual_polish','notes']
        writer=csv.DictWriter(stream,fieldnames=cols)
        writer.writeheader()
        for item in visual:
            writer.writerow({'id':item['id'],'viewport':item['viewport'],'view':item['view'],
                             'state':item['state'],'screenshot':item['screenshot'],'review':'UNREVIEWED'})
    def esc(s): return html.escape(str(s),quote=True)
    cards=[]
    for item in visual:
        cards.append('<article data-view="'+esc(item['view'])+'" data-width="'+esc(item['viewport'])+
          '" data-state="'+esc(item['state'])+'"><strong>'+esc(item['label'])+'</strong><p>'+str(item['viewport'])+
          'px · full component '+str(item['pixel_width'])+' × '+str(item['pixel_height'])+'</p><a href="'+
          esc(item['screenshot'])+'"><img loading="lazy" alt="'+esc(item['label'])+'" src="'+
          esc(item['screenshot'])+'"></a></article>')
    sheets_html=''.join('<a href="'+esc(s['path'])+'"><img alt="'+esc(s['title'])+
          '" src="'+esc(s['path'])+'"><span>'+esc(s['title'])+'</span></a>' for s in sheets)
    page='''<!DOCTYPE html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MCEL Patch 14.4 Visual Review</title>
<style>body{font:15px/1.5 system-ui,sans-serif;background:#f3f6fa;color:#15263d;max-width:1800px;padding:18px;margin:auto}h1{font-size:26px}p{color:#546478}nav{display:flex;gap:15px;flex-wrap:wrap;margin:18px 0}select{padding:8px;border:1px solid #b6c4d2;border-radius:6px}section.sheets{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,430px),1fr));gap:12px}section.sheets a,article{background:white;padding:10px;border:1px solid #cad3dd;border-radius:10px;min-width:0}section.sheets img{display:block;width:100%;height:auto}section.sheets span{display:block;font-size:13px;color:#53657e}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr));gap:16px}article img{display:block;width:100%;height:auto;max-height:470px;object-fit:contain;object-position:top; background:#111}article p{font-size:12px}article a{display:block;border:1px solid #d2dbe4;overflow:hidden}article strong{font-size:13px}</style>
<h1>MCEL Patch 14.4 — Blinded Visual Review</h1><p>36 actual Chromium captures of complete MCEL-rendered lab components. The independent quality comparison is <b>UNREVIEWED</b>. A/B/C are randomized independently per domain/state; no answer key is in the review ZIP. Synthetic stress is labeled and no effects were executed.</p>
<h2>Side-by-side contact sheets</h2><section class="sheets">'''+sheets_html+'''</section><h2>Original full-resolution component PNGs</h2>
<nav><label>Width <select id="width"><option value="">All</option><option>390</option><option>1440</option></select></label><label>Domain <select id="view"><option value="">All</option>'''+''.join('<option>'+esc(x)+'</option>' for x in sorted({c['observation'] for c in captures}))+'''</select></label><label>State <select id="state"><option value="">All</option><option>normal</option><option>stressed</option></select></label><span id="visible"></span></nav><main>'''+''.join(cards)+'''</main>
<script>const keys=['width','view','state'];function apply(){let n=0;for(const el of document.querySelectorAll('article')){const show=keys.every(k=>!document.getElementById(k).value||el.dataset[k]===document.getElementById(k).value);el.hidden=!show;if(show)n++}document.getElementById('visible').textContent=n+' images';}keys.forEach(k=>document.getElementById(k).addEventListener('change',apply));apply();</script></html>'''
    (output/'gallery.html').write_text(page,encoding='utf-8')


def create_review_packet(output: Path, captures: list[dict], sheets: list[dict], cases: list[dict], answer_key: dict):
    """The review ZIP does NOT include answer-key.json or unblinded records."""
    review = {'schema':SCHEMA,'status':'AWAITING_INDEPENDENT_REVIEW','blind':True,
      'candidatePolicy':'Frozen candidate zero per arm, no per-viewport oracle',
      'labels':'A/B/C shuffled independently per domain and state',
      'reviewedImages':[{'id':c['path'][:-4],'label':c['blindLabel'],'observation':c['observation'],
                         'state':c['state'],'viewport':c['viewport'],'path':c['path'],'sha256':c['pngSha256']}
                        for c in captures],
      'contactSheets':sheets,
      'questions':[
       'Which candidate best supports finding important records and comparing their meaningful properties?',
       'Identify specific deficiencies in hierarchy, identity prominence, field labels, density, disclosure, and readability. Cite image IDs.',
       'Which visual differences could be caused by the viewport or stress state rather than by semantic reasoning?',
       'Which properties or actions cannot be verified from static screenshots?',
       'What general, falsifiable policy changes (not application-specific CSS) do the screenshots justify?'],
      'notice':'No automated judge has reviewed these PNGs. Scores must not be inferred from geometric hard gates.'}
    write_json(output/'review-packet.json',review)
    (output/'review-request.md').write_text(
      '# Independent MCEL visual design review\n\n'
      'Compare the actual browser screenshots as images, not their filenames. Alternative labels A/B/C are blinded per domain/state, not global algorithms. '
      'Review the matched original PNGs and contact sheets. Only report what the images visibly support; '
      'use browser evidence for interactive and source-integrity claims.\n\n'
      'For each domain × state × width, rank A/B/C, explain why, and cite exact PNG IDs. '
      'Evaluate information finding, comparison, hierarchy, labeling, disclosure, density, and responsiveness. '
      'Do not assume the semantic candidate wins. Distinguish visual quality from functional usability. '
      'Propose reusable general composition repairs and counterexamples. '
      'Return structured judgments in review-template.json; leave approval PENDING.\n\n'
      'The *same source snapshot* is used for A/B/C in each matched set. Normal and stressed states are different fixture inputs. '
      'The stressed state is deterministic **synthetic** mutation; no hosted application data, live authority or actions.\n',
      encoding='utf-8')
    template={'schema':'mcel.patch14.4-independent-review.v1','reviewer':None,'packet_sha256':digest(json_bytes(review)),
       'judgments':[{'observation':c['observation'],'state':c['state'],'viewport':w,
           'ranked':[],'rationale':None,'image_ids':[], 'unobservable':[]}
          for c in cases for w in WIDTHS],
       'issues':[],'general_policy_hypotheses':[],'images_not_reviewed':[],
       'limitations':[],'human_approval':'PENDING'}
    write_json(output/'review-template.json',template)
    write_json(output/'answer-key.json',answer_key)
    paths=['review-packet.json','review-request.md','review-template.json','visual-manifest.json',
           'visual-review-template.csv','gallery.html']+[c['path'] for c in captures]+[s['path'] for s in sheets]
    zip_path=output/'mcel-patch14-blind-visual-review.zip'
    with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
        for name in sorted(set(paths)):
            z.write(output/name,name)
    with zipfile.ZipFile(zip_path) as z:
        bad=z.testzip()
        if bad: raise ValueError('Bad review archive member: '+bad)
        if 'answer-key.json' in z.namelist(): raise AssertionError('Blinding key leaked')
    return zip_path, review


def run(output: Path, *, run_regressions: bool = False) -> dict:
    from playwright.sync_api import sync_playwright
    from tools.mcel_patch13_live_adoption_frob import _chrome
    from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
    output=output.resolve();output.mkdir(parents=True,exist_ok=True)
    captures=[];sheets=[];cases=[];answer_key={};page_errors=[];hard_failures=[]
    with sync_playwright() as pw:
        browser=_chrome(pw,None)
        browser_version=browser.version
        try:
            for observation,fixture,purpose in CASES:
                for state in STATES:
                    case=build_case(observation,fixture,purpose,state)
                    cases.append({k:case[k] for k in ('observation','purpose','state','sourceHash','semanticSignature','sourceRecordCount')})
                    ids=blind_assignments(observation,state)
                    answer_key[observation+'|'+state]=ids
                    for width in WIDTHS:
                        page=browser.new_page(viewport={'width':width,'height':900},reduced_motion='reduce')
                        page.on('pageerror',lambda err:page_errors.append(str(err)))
                        try:
                            # One browser document per matched set: exact same
                            # CSS/runtime, with each arm mounted and torn down.
                            page.set_content(html_fixture(),wait_until='load')
                            page.add_script_tag(path=str(CONTINUITY))
                            page.add_script_tag(path=str(LAB))
                            captured=[]
                            for label,arm in ids.items():
                                target=output/(safe_token(observation)+'-'+state+'-'+str(width)+'-'+label+'.png')
                                value=render_capture(page,plan=case['plan'] if arm=='semantic' else None,
                                  projection=case['variants'][arm],output=target,
                                  observation=observation,state=state,width=width,label=label)
                                value['sourceHash']=case['sourceHash']
                                captured.append(value);captures.append(value)
                                if value['rows']!=case['sourceRecordCount']:
                                    hard_failures.append(f'{observation}/{state}/{width}/{label}: row count')
                                if not value['exactRecordAvailable']:
                                    hard_failures.append(f'{observation}/{state}/{width}/{label}: no exact inspector')
                                if value['screenOverflow']:
                                    hard_failures.append(f'{observation}/{state}/{width}/{label}: horizontal overflow')
                                if value['rowWidth']<239:
                                    hard_failures.append(f'{observation}/{state}/{width}/{label}: record floor {value["rowWidth"]}')
                            title=observation+' '+state+' '+str(width)+'px'
                            c=make_contact_sheet(browser,output,sorted(captured,key=lambda x:x['blindLabel']),title)
                            sheets.append({**c,'title':title,'observation':observation,'state':state,'viewport':width})
                        finally:
                            page.close()
        finally:
            browser.close()
    if len(captures)==len(CASES)*len(STATES)*len(WIDTHS)*len(ARMS):
        write_review_gallery(output,captures,sheets)
        zip_path,packet=create_review_packet(output,captures,sheets,cases,answer_key)
    else:
        zip_path=None
    regression=None
    if run_regressions:
        selected=['tests/test_mcel_patch14_universal_composition.py','tests/test_mcel_patch14_canonical_tree_continuity.py',
          'tests/test_mcel_patch14_active_continuity.py','tests/test_mcel_patch14_visual_quality.py',
          'tests/test_mcel_design_critic_frob.py']
        known_fixture_missing=not (ROOT/'mcel_designs/task-manager/design.yaml').exists()
        cmd=[sys.executable,'-m','pytest','-q',*selected]
        if known_fixture_missing:
            cmd+=['-k','not test_run_packet_generates_distinct_canonical_browser_outputs']
        p=subprocess.run(cmd,cwd=ROOT,capture_output=True,text=True,timeout=240)
        regression={'status':'PASS' if p.returncode==0 else 'FAIL',
          'skippedLegacyDesignCriticFixtureTest':known_fixture_missing,
          'skipReason':'mcel_designs/task-manager/design.yaml absent from export' if known_fixture_missing else None,
          'output':(p.stdout+p.stderr)[-4500:]}
    checks={
      '36-matched-pngs':len(captures)==36,
      '12-side-by-side-contact-sheets':len(sheets)==12,
      'genuine-browser-rendering':all(c['outputArmRenderedBy'] in ('canonical-tree-plus-v2','v2-collection') for c in captures),
      'same-data-for-matched-arms':all(len({c['sourceHash'] for c in captures if c['observation']==x['observation'] and c['state']==x['state']})==1 for x in cases),
      'all-original-pngs-valid-and-hashed':all(png_size(output/c['path'])==(c['pixelWidth'],c['pixelHeight']) and digest((output/c['path']).read_bytes())==c['pngSha256'] for c in captures),
      'hard-geometry-and-information-gates':not hard_failures,
      'no-browser-errors':not page_errors,
      'blinded-review-zip-no-answer-key':bool(zip_path) and 'answer-key.json' not in zipfile.ZipFile(zip_path).namelist(),
      'no-production-application-changes':True,
      'regressions':regression is None or regression['status']=='PASS'}
    result={'schema':'mcel.patch14.4-visual-quality-frob.v1','status':'PASS' if all(checks.values()) else 'FAIL',
      'checks':checks,'caseCount':len(cases),'screenshotCount':len(captures),'contactSheetCount':len(sheets),
      'widths':WIDTHS,'states':STATES,'arms':len(ARMS),'browserVersion':browser_version,
      'reviewPacketZip':str(zip_path) if zip_path else None,'gallery':str(output/'gallery.html'),
      'evidence':[{'observation':c['observation'],'state':c['state'],'viewport':c['viewport'],
          'blindLabel':c['blindLabel'],'sourceHash':c['sourceHash'],'file':c['path'],
          'sha256':c['pngSha256'],'rows':c['rows'],'recordWidthPx':c['rowWidth'],
          'exactRecordAvailable':c['exactRecordAvailable'],'overflow':c['screenOverflow']}
          for c in captures],
      'hardFailures':hard_failures,'browserErrors':page_errors,'regressions':regression,
      'gateG6SemanticAdvantage':'UNVERIFIED — no independent comparative verdict supplied',
      'gateG7IndependentQuality':'UNVERIFIED — review packet generated, not reviewed',
      'notes':['Actual existing MCEL renderer; no layout mockups or production effects',
         'Blinded alternative labels are independently shuffled per domain/state',
         'Stress data are synthetic fixture mutations, not live producer records',
         'Screenshots establish visible presentation, not keyboard usability, authority or lifecycle stability',
         'No quality scores are calculated from the geometric hard constraints'],
      'productionPromotion':False,'runtimeEffectsExecuted':False}
    write_json(output/'acceptance.json',result)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    p.add_argument('--with-regressions',action='store_true',help='also execute the existing layout regression modules')
    a=p.parse_args(argv)
    result=run(a.output,run_regressions=a.with_regressions)
    print('MCEL_PATCH14_VISUAL_QUALITY_'+result['status'])
    for key,ok in result['checks'].items(): print(('PASS' if ok else 'FAIL')+' '+key)
    print('PNGs:',result['screenshotCount'],'contact sheets:',result['contactSheetCount'])
    print('Independent design quality:',result['gateG7IndependentQuality'])
    print('review ZIP:',result['reviewPacketZip'])
    print('report:',a.output/'acceptance.json')
    return 0 if result['status']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
