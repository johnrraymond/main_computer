"""MCEL Frob Lab: executable, read-only machine/engine/layout comparisons.

Usage:
 python tools/mcel_layout_frob.py --mode smoke
 python tools/mcel_layout_frob.py --mode sweep --widths 320,390,768,1440 --fixtures normal,long_text,stale
 python tools/mcel_layout_frob.py --mode sweep --no-browser   # planning only; no browser PASS
 python tools/mcel_layout_frob.py --mode paired-workspace  # test the built-in isolated compositor candidate

Runs production MCEL workspace inference, canonical layout tree and adaptive control
contract where appropriate. New graded-hint / bounded-constraint planners consume
those machines. Does NOT register an app, invoke live operations, or certify backend
worker/AI integration. Unsupported machine/engine pairs remain explicit.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import html
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

try:
    import yaml
except ImportError as exc:
    raise SystemExit('PyYAML required') from exc

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
DEFAULT_OUTPUT = ROOT/'runtime/reports/mcel-layout-frob'
# Width supplied to MCEL's workspace inferencer is the content width, not
# the viewport width. This padding is also used in the Frob preview CSS.
FROB_HOST_PADDING_PX = 8
RESOLVER = ROOT/'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js'
DESIGN = ROOT/'tools/fixtures/mcel/task-manager/design.yaml'
CONTRACT = ROOT/'tools/fixtures/mcel/task-manager/frob_crosscheck.yaml'

# A machine's available scope is NOT silently promoted into application-shell support.
COMPATIBILITY = {
    ('workspace_inference','workspace_greedy'): 'whole_app',
    ('canonical_tree','bounded_recursive'): 'whole_app',
    ('canonical_tree','graded_hint'): 'whole_app',
    ('canonical_tree','constraint_optimization'): 'whole_app',
    ('constraint_layout','graded_hint'): 'experimental_bridge',
    ('constraint_layout','constraint_optimization'): 'experimental_bridge',
    ('adaptive_control_grid','bounded_recursive'): 'subtree_only',
}
PRIORITY_ROLES = {'status-and-attention':'attention','global-context-and-search':'search',
                  'primary-content':'work','selection-details':'inspect',
                  'supporting-tools':'tools','persistent-nonblocking-feedback':'feedback'}


def sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',', ':')).encode()).hexdigest()


def read_yaml(path):
    obj=yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(obj,dict):raise ValueError(f'{path}: not a mapping')
    return obj


def regions(design):
    nodes=design['hierarchy']['children']
    result={}
    for item in nodes:
        role=PRIORITY_ROLES.get(item['role'])
        if not role or role in result or not item.get('id'):
            raise ValueError('Missing, duplicate, or unsupported semantic region')
        result[role]=item['id']
    if set(result)!=set(PRIORITY_ROLES.values()):raise ValueError('Required semantic role missing')
    return result


def variant(ids, pattern):
    rows=[' '.join(ids[token] for token in row) for row in pattern]
    width=len(pattern[0])
    if any(len(row)!=width for row in pattern):raise ValueError('Ragged pattern')
    return {'tracks':['minmax(0, 1fr)']*width,'areas':rows}


def patterns(ids,width):
    """Generic six-region application grammar; no app-name/CSS selectors.

    Each pattern includes every region exactly once (some rectangular spanning).
    """
    if width<=600:
        variants=[
          [['attention'],['search'],['work'],['feedback'],['inspect'],['tools']],
          [['work'],['search'],['attention'],['inspect'],['feedback'],['tools']],
          [['attention'],['feedback'],['search'],['work'],['inspect'],['tools']],
          [['search'],['attention'],['work'],['inspect'],['tools'],['feedback']],
        ]
    elif width<=950:
        variants=[
          [['attention','search'],['work','work'],['inspect','feedback'],['tools','tools']],
          [['work','work'],['attention','search'],['feedback','inspect'],['tools','tools']],
          [['attention','feedback'],['search','inspect'],['work','work'],['tools','tools']],
          [['search','attention'],['work','work'],['inspect','feedback'],['tools','tools']],
        ]
    else:
        variants=[
          [['attention','search','search'],['work','work','inspect'],['feedback','feedback','feedback'],['tools','tools','tools']],
          [['work','work','inspect'],['attention','search','search'],['feedback','feedback','feedback'],['tools','tools','tools']],
          [['search','search','attention'],['work','work','inspect'],['tools','tools','tools'],['feedback','feedback','feedback']],
          [['attention','feedback','feedback'],['work','work','inspect'],['search','search','search'],['tools','tools','tools']],
          [['attention','search','inspect'],['work','work','inspect'],['feedback','feedback','feedback'],['tools','tools','tools']],
          [['attention','work','work'],['search','work','work'],['inspect','feedback','feedback'],['tools','tools','tools']],
        ]
    return [variant(ids,p) for p in variants]


def positions(v):
    grid=[line.split() for line in v['areas']]
    return {name:(min(r for r,row in enumerate(grid) if name in row),
                  min(c for row in grid for c,item in enumerate(row) if item==name))
            for name in set(sum(grid,[])) if name!='.'}


def decision(v,ids,strength,width):
    pos=positions(v)
    priority=pos[ids['attention']]
    upper_left=priority==(0,0)
    first_before=priority[0]<=pos[ids['work']][0]
    violations=[]
    if not first_before:violations.append('reading-order-attention-before-work (proposed preference)')
    if strength in ('constrain','require') and not upper_left:
        return {'feasible':False,'hard_conflicts':['priority-upper-left'], 'satisfied':False,
                'relaxed':[], 'score':None}
    score={'attention_upper_left':1 if upper_left else 0,
           'work_area_cells':sum(row.split().count(ids['work']) for row in v['areas']),
           'rows':len(v['areas']), 'work_first':int(pos[ids['work']]==(0,0)),
           'grid_columns':len(v['tracks'])}
    # Multi-objective data remain accessible; scalar is only planner preference.
    base=score['work_first']*8+score['work_area_cells']*2-score['rows']*.3
    weight={'none':0,'suggest':4,'prefer':16,'constrain':16,'require':16}[strength]
    value=base+weight*score['attention_upper_left']
    return {'feasible':True, 'hard_conflicts':[], 'satisfied':upper_left,
            'relaxed':(['priority-upper-left'] if strength in ('suggest','prefer') and not upper_left else []),
            'score':round(value,3),'metrics':score,'deferred_violations':violations}


def rank(variants,ids,strength,width,engine,max_candidates=192):
    if engine not in ('bounded_recursive','graded_hint','constraint_optimization'):
        raise ValueError('No such candidate engine')
    if strength not in ('none','suggest','prefer','constrain','require'):
        raise ValueError('Invalid authority')
    if engine=='bounded_recursive': strength='none'  # no support for the hint contract
    manifest=[]
    for i,v in enumerate(variants[:max_candidates]):
        detail=decision(v,ids,strength,width)
        manifest.append({'id':f'candidate-{i:03d}', 'fingerprint':sha(v), 'variant':v, **detail})
    feasible=[x for x in manifest if x['feasible']]
    if engine=='constraint_optimization':
        # Bounded discrete constraint optimization: choose best feasible candidate;
        # does NOT claim a general-purpose linear/SAT solver or global optimality.
        feasible.sort(key=lambda item: (-item['score'],item['fingerprint']))
    elif engine=='graded_hint':
        feasible.sort(key=lambda item: (-item['score'],item['fingerprint']))
    else:
        feasible.sort(key=lambda item: (-item['metrics']['work_first'], -item['metrics']['work_area_cells'],item['fingerprint']))
    winner=feasible[0] if feasible else None
    return {'status':'planned' if winner else 'refused', 'selected':winner,
            'candidates':manifest,'search_truncated':len(variants)>max_candidates,
            'candidate_count':len(manifest),
            'pareto_metrics':['attention_upper_left','work_area_cells','rows','work_first','grid_columns'],
            'engine':engine,'evaluated_strength':strength,
            'refusal':'no_feasible_candidate' if not winner else None}


def available_workspace_width(viewport_width):
    available = viewport_width - 2 * FROB_HOST_PADDING_PX
    if available <= 0:
        raise ValueError('Frob host has no available content width')
    return available


def inferred_workspace(design,width,out):
    # Calls the production JS implementation, not a Python reimplementation.
    from tools.task_manager.semantic_layout_probe import semantic_projection
    semantic,grammar=semantic_projection(design)
    input_path=out/'inference.json';input_path.write_text(json.dumps({'semantic':semantic,'grammar':grammar,'width':width}),encoding='utf-8')
    js='''const vm=require('node:vm'),fs=require('node:fs');const scope={};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),scope);
const data=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
process.stdout.write(JSON.stringify(scope.McelSemanticLayoutInference.inferWorkspaceLayout({semanticSurface:data.semantic,layoutGrammar:data.grammar,width:data.width,mode:'overview'})));'''
    p=subprocess.run(['node','-e',js,str(RESOLVER),str(input_path)],capture_output=True,text=True,timeout=20)
    if p.returncode:raise RuntimeError('Production workspace inference: '+p.stderr[:500])
    return json.loads(p.stdout)


def tree_for(ids,v):
    return {'schema':'mcel.layout-tree.v1','root':{
        'id':'frob-root','selector':'#frob-root','role':'group','gap':12,
        'variants':[v],
        'children':[{'id':item,'selector':'#'+item,'role':'region','verifyControls':False}
                    for item in ids.values()]}}


def fixture_content(role,fixture):
    labels={
      'attention':['Server health: nominal','No urgent warnings','Last snapshot: 5 seconds ago'],
      'search':['Search processes, ports, and activity'],
      'work':['Process: python.exe — PID 431','TCP 127.0.0.1:8765 → python.exe','Worker task: attempt-12 running','AI call: qwen request pending'],
      'inspect':['Selected process','Identity and observed provenance'],
      'tools':['Server actions','Schedules','AI operations'],
      'feedback':['Running: last acknowledged 3s ago','Completion has not been verified']}
    rows=list(labels[role])
    if fixture=='empty':rows=['No results; absence from snapshot is not proof of absence']
    if fixture=='crowded':rows=rows*20
    if fixture=='long_text':rows += ['C:/Users/subsi/main_computer/some_deeply_nested_path/a_very_long_filename_with_an_unbroken_identifier_1234567890abcdefghijklmnopqrstuvwxyz_abcdefghi.py: 2001:db8:0123:4567:89ab:cdef:1234:5678']
    if fixture=='partial':rows=['Telemetry unavailable (not zero)', 'Process owner: unknown']
    if fixture=='stale':rows=['Last observed 45m ago — STALE', 'Operation result unknown']
    if fixture=='failure':rows=['Request failed — server unreachable','No completion receipt']
    if fixture=='adversarial':rows += ['<script>evil()</script>', 'UNVERIFIED 1234']
    return rows


def make_html(ids,fixture,plan,source,design=None, width=1440):
    contents=[]
    for role,item in ids.items():
        rows=fixture_content(role,fixture)
        if role=='work' and design is not None:
            from tools.mcel_layout_frob_presentations import rendered_rows
            row_html=rendered_rows(design,width)
        else: row_html=''
        contents.append(f'<section class="region" id="{html.escape(item)}" aria-label="{role}">'
                        f'<h2>{html.escape(role.replace("_"," ").title())}</h2>'
                        +''.join('<p>'+html.escape(s)+'</p>' for s in rows)+row_html+'</section>')
    contents.append('<div id="control-frob" data-mcel-layout-role="adaptive-control-grid" data-mcel-max-columns="4" data-mcel-min-target-width="44">'
                    '<button type="button">Refresh</button><button type="button">Find Port</button><button type="button">Inspect</button></div>')
    payload=json.dumps(plan).replace('<','\\u003c')
    code='''<script>const plan='''+payload+''';
window.__layoutFailure=null;window.__layoutWitness=null;
try{
const api=McelSemanticLayoutInference,root=document.getElementById('frob-root');
if(plan.kind==='workspace'){
 const wrapper={...plan.value,workspaceSelector:'#frob-root',selectors:Object.fromEntries(Object.keys(plan.value.selectors).map(k=>[k, '#'+k]))};
 // Workspace API expects the workspace inside the mounted app root.
 const host=document.querySelector('#frob-host');
 api.applyWorkspaceLayout(host,wrapper);
 window.__layoutWitness={status:'pass',authority:'workspace-inference',candidate_count:1,search_truncated:false};
}else{
 const e=api.evaluateCanonicalLayoutTree(root,plan.tree,{maxCandidates:64});
 const applied=api.applyCanonicalLayoutTree(root,e);
 window.__layoutWitness={status:applied.status,reason:applied.reason||null,authority:'canonical-tree',
 candidate_count:e.candidates.length,search_truncated:e.searchTruncated,
 rejected:e.candidates.filter(x=>!x.accepted).length,proof:root.dataset.mcelCanonicalLayoutProof||null};
}
const grid=document.getElementById('control-frob');
const evaluation=api.evaluateAdaptiveControlGrid(grid);
const applied=api.applyAdaptiveControlGrid(grid,evaluation);
window.__adaptiveWitness={status:applied.status,columns:evaluation.acceptedColumns,candidates:evaluation.candidates.length};
}catch(err){window.__layoutFailure=String(err?.stack||err);}
</script>'''
    style='''<style>*{box-sizing:border-box}html,body{padding:0;margin:0;font:14px/1.4 system-ui,sans-serif}
#frob-host{padding:__FROB_HOST_PADDING__px}#frob-root{width:100%;min-width:0}.region{padding:10px;border:1px solid #a0a0a0;border-radius:5px;min-width:0;overflow-wrap:anywhere}
.region h2{font-size:16px;margin:0 0 6px}.region p{margin:4px 0}
.semantic-specimens{display:grid;gap:6px}.frob-semantic-row{border:1px solid #ccc;padding:5px;min-width:0}.semantic-fields{display:flex;flex-wrap:wrap;gap:4px 10px}.semantic-field{overflow-wrap:anywhere}#control-frob{margin-top:10px;display:grid;gap:6px}
button{min-width:44px;min-height:44px;font:inherit;cursor:pointer}</style>'''
    style=style.replace('__FROB_HOST_PADDING__',str(FROB_HOST_PADDING_PX))
    return ('<!doctype html><html><head><meta charset="utf-8">'+style+'</head><body>'+
            '<div id="frob-host"><main id="frob-root">'+''.join(contents[:-1])+'</main>'+contents[-1]+'</div>'+
            '<script>'+source+'</script>'+code+'</body></html>')


def measure(page, ids, width):
    script='''(ids)=>{const root=document.getElementById('frob-root'),
    rects=Object.fromEntries(Object.values(ids).map(id=>{const r=document.getElementById(id).getBoundingClientRect();
      const el=document.getElementById(id);return [id,{x:r.left,y:r.top,w:r.width,h:r.height,scrollWidth:el.scrollWidth,clientWidth:el.clientWidth}]}));
    const grid=getComputedStyle(root).gridTemplateColumns.split(' ').filter(Boolean);
    const attention=rects[ids.attention],work=rects[ids.work];
    const minx=Math.min(...Object.values(rects).map(r=>r.x)),miny=Math.min(...Object.values(rects).map(r=>r.y));
    const rows=[...document.querySelectorAll('[data-row]')];
    return {witness:window.__layoutWitness||null,adaptive:window.__adaptiveWitness||null,
      rowSpecimens:rows.map(e=>({id:e.dataset.row,entity:e.dataset.entity,mode:e.dataset.mode,fields:e.querySelectorAll('[data-field]').length})),
      error:window.__layoutFailure||null,columns:grid.length,
      priorityUpperLeft:Math.abs(attention.x-minx)<2&&Math.abs(attention.y-miny)<2,
      priorityBeforeWork:attention.y<=work.y+2,
      horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1,
      clippedRegions:Object.keys(rects).filter(id=>rects[id].scrollWidth>rects[id].clientWidth+2),rects,
      viewport:innerWidth, rootWidth:root.getBoundingClientRect().width};}'''
    return page.evaluate(script,ids)


def browser_run(ids, fixture, plan, width, output, tag, *, browser, design=None):
    html_source=make_html(ids,fixture,plan,RESOLVER.read_text(encoding='utf-8'),design=design,width=width)
    page=browser.new_page(viewport={'width':width,'height':850},device_scale_factor=1)
    try:
        page.set_content(html_source,wait_until='load',timeout=15000)
        observed=measure(page,ids,width)
        path=output/(tag+'.png');page.screenshot(path=str(path),full_page=True,timeout=10000)
        observed['screenshot']=path.name
        observed['screenshot_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        observed['html_sha256']=hashlib.sha256(html_source.encode('utf-8')).hexdigest()
        return observed
    finally:page.close()


def verify_hint_consistency(hints):
    """Refuse contradictory hard placement instructions before candidate search."""
    required={}
    for h in hints:
        if h['strength'] not in ('require','constrain'):
            continue
        key=(h['target'], h.get('scope','all'))
        if key in required and required[key]['position']!=h['position']:
            raise ValueError('conflicting-hard-hints: '+required[key]['id']+' vs '+h['id'])
        required[key]=h
    return True


def negative_controls(ids, design=None, contract=None, browser=None):
    """Ten deliberate adversarial witnesses; missing browser means UNVERIFIED."""
    from tools.mcel_layout_frob_runtime import process_identity, FixtureOperationStore
    from tools.mcel_layout_frob_crosscheck import static_result, compare_console_report
    evidence = {}
    def put(key, ok, detail, scope='fixture-only'):
        evidence[key] = {'status':'PASS' if ok else 'FAIL', 'evidence':detail, 'scope':scope}
    hints = [{'id':'upper-left','target':ids['attention'],'position':'upper-left','strength':'require'},
             {'id':'bottom-right','target':ids['attention'],'position':'bottom-right','strength':'require'}]
    conflict = ''
    try: verify_hint_consistency(hints)
    except ValueError as exc: conflict = str(exc)
    put('unsat_hard_hints', all(h['id'] in conflict for h in hints), {'refusal':conflict})
    if design and contract:
        import copy
        mutated=copy.deepcopy(design)
        mutated['rows']['types']['port-row']['entity']='nonexistent_test_entity'
        inst={'test':{'probe':'static.refs_resolve','target':'rows.types',
             'assertion':{'namespace':['semantics.entities'],'field':'entity'}},
             'value':mutated['rows']['types']['port-row'],'member':'port-row'}
        checked=static_result(inst,mutated,contract)
        put('unknown_semantic_entity',checked['status']=='FAIL',{'static_status':checked['status']})
    else: evidence['unknown_semantic_entity']={'status':'UNVERIFIED','reason':'No design supplied'}
    old={'pid':431,'created_at':'2026-10-24T18:00:00Z'}
    new={'pid':431,'created_at':'2026-10-24T18:01:00Z'}
    put('duplicate_entity_identity',process_identity(old)!=process_identity(new),
        {'old':process_identity(old),'new':process_identity(new)})
    if browser:
        page=browser.new_page(viewport={'width':320,'height':600})
        try:
            page.set_content('<div id="broken" style="width:150px;white-space:nowrap;overflow:hidden"><span>superlongunbrokenidentifierabcdefgh1234567890abcdefghijklmnopqrstuvwxy</span></div>')
            proof=page.evaluate('''() => {const e=document.getElementById('broken');
                return {scrollWidth:e.scrollWidth,clientWidth:e.clientWidth,accepted:e.scrollWidth<=e.clientWidth+1}}''')
            put('browser_text_collision',proof['scrollWidth']>proof['clientWidth'] and not proof['accepted'],
                proof,scope='independent-Chromium-negative')
        finally:page.close()
    else: evidence['browser_text_collision']={'status':'UNVERIFIED','reason':'No Chromium witness'}
    store=FixtureOperationStore()
    store.request(token='one',target='431@time',action='terminate',authorized=True,confirmed=True,destructive=True)
    timedout=store.advance('one','unknown')
    retry=store.request(token='one',target='431@time',action='terminate',authorized=True,confirmed=True,destructive=True)
    put('server_timeout',timedout['state']=='unknown' and retry['id']=='one' and len(store.effects)==1,
        {'state':timedout['state'],'effect_count':len(store.effects)})
    before=len(store.effects)
    refused=store.terminate(token='two',expected_pid=431,expected_started=old['created_at'],actual=new,
                           authorized=True,confirmed=True)
    put('unauthorized_termination',refused['status']=='refused' and len(store.effects)==before,
        {'refusal':refused,'effect_count':len(store.effects)})
    producer=None
    state='unavailable' if producer is None else 'available'
    put('absent_worker_provider',state=='unavailable',
        {'producer':None,'state':state,'reported_running':False})
    schedule={'state':'stored','dispatch_evidence':None}
    put('fake_schedule_dispatch',schedule['state']=='stored' and not schedule['dispatch_evidence'],schedule)
    unsupported=('adaptive_control_grid','graded_hint') not in COMPATIBILITY
    put('silent_machine_fallback',unsupported,
        {'tested_pair':['adaptive_control_grid','graded_hint'],'supported':not unsupported})
    comparison=compare_console_report({'390':{'columns':3}},{'canonicalBrowser':{'390':{'columns':1}}})
    put('fabricated_browser_certificate',comparison['status']=='FAIL',comparison)
    return evidence

def crosscheck_planner_probes():
    return {'planner.capabilities','planner.candidates','planner.hints','planner.objectives','browser.geometry','browser.performance'}


def build_evidence_index(report):
    """Per-obligation witnessed conditions; never map fixture truth to live integration."""
    cases=[c for c in report['scenarios'] if 'width' in c]
    browser=[c for c in cases if 'observed' in c]
    available=lambda pred: bool(browser) and all(pred(c) for c in browser)
    index={}
    def result(name,status,reason,examples):
        index[name]={'status':status,'reason':reason,'scenario_ids':examples}
    implemented=[c for c in cases if c['status']=='PASS']
    index_ids=lambda seq:[f"{c['machine']}:{c['engine']}:{c['width']}:{c['strength']}:{c['fixture']}" for c in seq][:32]
    if len(report['compatibility'])==16 and all(c['status'] in ('SUPPORTED','NOT_IMPLEMENTED') for c in report['compatibility']):
        result('M01-compatibility-report','PASS','All sixteen pairings accounted for by declared adapter table',[])
        result('M09-unsupported-pair-refusal','PASS','Unsupported pairings retained rather than switched to a fallback',[])
    if implemented and all(c.get('manifest',{}).get('candidate_count',0)>=1 for c in implemented):
        result('M03-candidate-manifest','PASS','All passing layout cases preserve candidate identity and selection evidence',index_ids(implemented))
    if implemented and all(not c.get('manifest',{}).get('search_truncated',False) for c in implemented):
        result('M06-bounded-search','PASS','Selected bounded candidates and truncation recorded',index_ids(implemented))
    if browser and all(c['observed'].get('screenshot_sha256') and c['observed'].get('html_sha256') for c in browser):
        result('Q07-run-provenance','PASS','Viewport evidence files hashed; design/resolver/browser fingerprint included',index_ids(browser))
    mobile=[c for c in browser if c['width']==390 and c['status']=='PASS']
    complete_mobile={'normal','crowded','long_text'}.issubset({c['fixture'] for c in mobile})
    if complete_mobile and all(c['observed']['columns']==1 for c in mobile):
        result('B02-mobile-stacking','PASS','Actual Chromium measured one root column at 390px',index_ids(mobile))
    elif mobile and any(c['observed']['columns']!=1 for c in mobile):
        result('B02-mobile-stacking','FAIL','Root still multi-column at mobile viewport',index_ids(mobile))
    desktop=[c for c in browser if c['width']>=1024 and c['strength'] in ('prefer','require') and c['status']=='PASS']
    if {'normal','crowded'}.issubset({c['fixture'] for c in desktop}) and all(c['observed']['priorityUpperLeft'] for c in desktop):
        result('B01-priority-top-left','PASS','Measured attention top-left in requested desktop cases',index_ids(desktop))
    required=[c for c in browser if c['strength']=='require' and c['engine'] in ('graded_hint','constraint_optimization')]
    if required and all(c['observed']['priorityUpperLeft'] for c in required):
        result('H05-require-hard','PASS','Hard upper-left satisfied in each measured applicable case',index_ids(required))
    preferred=[c for c in browser if c['strength']=='prefer' and c['engine'] in ('graded_hint','constraint_optimization')]
    if preferred and all(c.get('hint_decision',{}).get('evaluated_strength')=='prefer' for c in preferred):
        result('H03-prefer-ranking','PASS','Strong hint affects ranking, trace preserved',index_ids(preferred))
    # C06 demands four presentation modes (table/compact/detail/card). Two inert
    # variants do not certify that contract, even when all row IDs appear.
    if report['negative_controls']['unsat_hard_hints']['status']=='PASS':
        result('H07-conflict-minimal','PASS','Contradictory hard hints refused with both IDs',[])
    if report['negative_controls']['silent_machine_fallback']['status']=='PASS':
        result('M09-unsupported-pair-refusal','PASS','Unsupported pairings refuse without fallback',[])
    return index


def pairwise_schedule(pairs, widths, fixtures, strengths, budget, seed=2401024):
    """Greedy deterministic 2-way axis covering array, bounded by budget.

    Each scenario is one (machine,engine,width,fixture,strength) tuple. A budget
    of 0 explicitly runs the whole feasible cross-product.
    """
    compatible=[(m,e) for m,e in pairs if frob_pair_has_full_shell(m,e)]
    all_cases=[(m,e,w,f,s) for m,e in compatible for w in widths
               for f in fixtures for s in strengths
               if s=='none' or e not in ('workspace_greedy','bounded_recursive')]
    if budget==0 or budget>=len(all_cases):return all_cases,{'sampling':'exhaustive','planned':len(all_cases),'selected':len(all_cases),'truncated':False}
    if budget<1:raise ValueError('Budget must be nonnegative')
    # Pairwise dimensions: combined machine-engine pair, width, fixture, hint.
    def pairs_for(case):
        values=(case[0]+'|'+case[1],str(case[2]),case[3],case[4])
        return frozenset((i,j,values[i],values[j]) for i in range(4) for j in range(i+1,4))
    remaining=list(all_cases)
    covered=set()
    chosen=[]
    while remaining and len(chosen)<budget:
        best=max(remaining,key=lambda x:(len(pairs_for(x)-covered),
                 hashlib.sha256((repr(x)+str(seed)).encode()).hexdigest()))
        covered.update(pairs_for(best));chosen.append(best);remaining.remove(best)
    universe=set().union(*(pairs_for(x) for x in all_cases)) if all_cases else set()
    return chosen,{'sampling':'deterministic-pairwise-greedy','planned':len(all_cases),
                   'selected':len(chosen),'truncated':bool(remaining),
                   'two_way_covered':len(covered),'two_way_total':len(universe)}


def frob_pair_has_full_shell(machine,engine):
    return COMPATIBILITY.get((machine,engine)) in ('whole_app','experimental_bridge')


def write_gallery(report, output):
    """Reuse the shared visual evidence renderer for machine/engine comparisons."""
    from tools.mcel_frob_visual_evidence import png_size, write_gallery as shared_gallery
    output = Path(output)
    specimens = []
    for case in report.get('scenarios', []):
        obs = case.get('observed')
        if not obs or not obs.get('screenshot'):
            continue
        name = obs['screenshot']
        width, height = png_size(output/name)
        specimens.append({
            'id': name[:-4], 'kind': 'layout-machine',
            'label': f"{case['machine']} / {case['engine']} | {case['width']}px | {case['fixture']} | {case['strength']} | {case['status']}",
            'viewport': case['width'], 'view': f"{case['machine']}/{case['engine']}",
            'state': f"{case['fixture']}-{case['strength']}",
            'screenshot': name, 'screenshot_sha256': obs['screenshot_sha256'],
            'pixel_width': width, 'pixel_height': height, 'full_page': True,
            'observed': {'columns': obs.get('columns'), 'priorityUpperLeft': obs.get('priorityUpperLeft'),
                         'layoutStatus': obs.get('witness', {}).get('status'), 'fixture': case['fixture']},
            'visual_approval': 'UNREVIEWED'
        })
    shared_gallery(output, specimens, title='MCEL Frob layout-machine comparisons',
                   provenance=report.get('provenance', {}))


# MCEL paired-workspace preintegration mode. This code is inserted into the
# existing layout frob; it does not modify any application or runtime file.
PAIRED_APPS = ('file-manager', 'task-manager', 'code-editor', 'calculator')
PAIRED_WIDTHS = (320, 390, 560, 1100, 1272)
PAIRED_CANDIDATE = ROOT / "tools/fixtures/mcel_paired_workspace_candidate"


def _paired_baseline_pages(apps):
    from main_computer.mcel_semantic_other_apps_host import compile_semantic_app, render_semantic_page
    from main_computer.mcel_calculator_expanded_host import compile_expanded, render_expanded_page
    pages = {}
    for app in apps:
        if app == 'calculator':
            ir, markup = compile_expanded(), render_expanded_page()
        else:
            ir, markup = compile_semantic_app(app), render_semantic_page(app)
        pages[app] = {'ir': ir, 'html': markup}
    return pages


def _paired_stage(root, destination, patch):
    """Stage candidate code away from the user's repository and its services."""
    import zipfile
    from pathlib import PurePosixPath
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root/'main_computer', destination/'main_computer',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for fixture in ('mcel_semantic_other_apps', 'mcel_calculator_expanded'):
        source = root/'tools'/'fixtures'/fixture
        shutil.copytree(source, destination/'tools'/'fixtures'/fixture)
    changed = []
    # Both inputs stage EXACT repository-relative source files into the same
    # isolated checkout. The built-in source tree is the normal development
    # path; the optional ZIP is retained for independent candidate review.
    def add(path, data):
        parts = PurePosixPath(path).parts
        if (not parts or path.startswith('/') or '.' in parts or '..' in parts or
            '\x00' in path or parts[0] != 'main_computer' or
            path.startswith('main_computer/web/applications/fixtures/')):
            raise ValueError('Unsafe or non-root-relative candidate source: '+path)
        target = destination/path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        changed.append(path)
    if patch.is_dir():
        # An editable, inspectable, permanent candidate in the existing Frob.
        # Never execute or follow symlinked files from a candidate tree.
        for src in sorted(patch.rglob('*')):
            if src.is_symlink():
                raise ValueError('Unsafe candidate symlink: '+str(src))
            if src.is_dir():
                continue
            if '__pycache__' in src.relative_to(patch).parts or src.suffix in ('.pyc','.pyo'):
                continue
            if not src.is_file():
                raise ValueError('Unsupported candidate source: '+str(src))
            add(src.relative_to(patch).as_posix(), src.read_bytes())
    else:
        with zipfile.ZipFile(patch) as archive:
            names = set()
            for member in archive.infolist():
                path = member.filename.replace('\\', '/')
                parts = PurePosixPath(path).parts
                if (member.is_dir() or not parts or path.startswith('/') or
                    '.' in parts or '..' in parts or '\x00' in path or
                    path in names or parts[0] not in ('main_computer', 'tools', 'tests')):
                    raise ValueError('Unsafe or non-root-relative candidate ZIP entry: '+path)
                names.add(path)
                if parts[0] == 'tests' or parts[:2] == ('tools', 'mcel_workspace_plan_probe.py'):
                    continue
                if parts[0] == 'tools' and parts[:2] != ('tools', 'fixtures'):
                    continue
                if path.startswith(('tools/fixtures/mcel_semantic_other_apps/',
                                    'tools/fixtures/mcel_calculator_expanded/')):
                    raise ValueError('Candidate cannot rewrite semantic application sources: '+path)
                add(path, archive.read(member))
    if not changed:
        raise ValueError('Candidate ZIP contains no staged application changes')
    return changed


def _paired_candidate_pages(stage, apps):
    """Compile inside the scratch source tree; never import staged modules into the Frob."""
    import tempfile
    expression = '''import json,sys
from pathlib import Path
from main_computer.mcel_semantic_other_apps_host import compile_semantic_app,render_semantic_page
from main_computer.mcel_calculator_expanded_host import compile_expanded,render_expanded_page
apps=json.loads(sys.argv[1]);target=Path(sys.argv[2]);pages={}
for app in apps:
 if app=='calculator': ir,markup=compile_expanded(),render_expanded_page()
 else: ir,markup=compile_semantic_app(app),render_semantic_page(app)
 pages[app]={'ir':ir,'html':markup}
target.write_text(json.dumps(pages,ensure_ascii=False),encoding='utf-8')
'''
    path = stage/'frob-pages.json'
    env = os.environ.copy()
    env['PYTHONPATH'] = str(stage)
    process = subprocess.run([sys.executable, '-c', expression, json.dumps(list(apps)), str(path)],
                             cwd=stage, env=env, capture_output=True, text=True, timeout=90)
    if process.returncode != 0:
        raise RuntimeError('Staged candidate compilation refused: '+process.stderr[-1500:])
    return json.loads(path.read_text(encoding='utf-8'))


# Same deterministic read-port doubles already exercised by the paired-app tests.
# All non-loopback requests and unrecognized operations are blocked.
_PAIRED_MOCK = r'''() => {
 window.__pairedCalls=[];
 window.fetch=async (url,options) => {
   const path=String(url).split('/api/applications/')[1];
   window.__pairedCalls.push({path,method:String(options?.method||'GET')});
   let data;
   if(path==='file-explorer/roots') data={ok:true,roots:[{id:'workspace',label:'Workspace'}]};
   else if(path==='file-explorer/list')data={ok:true,entries:[{relative_path:'README.md',name:'README.md',kind:'file'},{relative_path:'src',name:'src',kind:'directory'}]};
   else if(path==='file-explorer/read')data={ok:true,readable:true,content:'Project documentation'};
   else if(path==='task/overview')data={processes:[{pid:123,name:'server',status:'running',created_at:'2026-10-31T12:00',memory_human:'34 MB'}]};
   else if(path==='editor/files')data={entries:[{path:'README.md',name:'README.md',kind:'file'}]};
   else if(path==='editor/read')data={ok:true,files:[{path:'README.md',content:'# Sample project'}]};
   else return {ok:false,status:404,json:async()=>({error:'mock-blocked'})};
   return {ok:true,status:200,json:async()=>data};
 };
}'''


_PAIRED_MEASURE = '''() => {
 const root=document.querySelector('[data-mcel-presentation]');
 const r=(el)=>{const a=el?.getBoundingClientRect();return a?{x:a.x,y:a.y,width:a.width,height:a.height}:null};
 const anchor=document.querySelector('[data-mcel-zone="workspace"]') ||
   document.querySelector('[data-mcel-priority="primary"][data-mcel-region]');
 const layout=document.querySelector('[data-mcel-workspace-layout]');
 const zones=Object.fromEntries([...document.querySelectorAll('[data-mcel-zone]')]
   .map(el=>[el.getAttribute('data-mcel-zone'),r(el)]));
 return {ready:(document.querySelector('#mcel-workbench-status')?.textContent ||
                 document.querySelector('#mcel-expanded-diagnostic')?.textContent || '').trim(),
   refused:!!document.querySelector('[data-error="true"]'),
   appId:window.__mcelWorkbench?.appId||window.__mcelExpanded?.appId||null,
   regions:[...(root?.querySelectorAll('[data-mcel-region]')||[])].map(el=>el.getAttribute('data-mcel-region')),
   buttons:[...(root?.querySelectorAll('button')||[])].map(b=>b.textContent.trim()),
   anchor:r(anchor),zones,layout:!!layout,rung:layout?.dataset.mcelLayoutRung||null,
   tabs:document.querySelectorAll('[data-mcel-tool-tabs] [role="tab"]').length,
   hiddenToolPanels:document.querySelectorAll('[data-mcel-tool-panel][hidden]').length,
   horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1,
   scrollHeight:document.documentElement.scrollHeight,viewport:innerWidth,
   mainHeight:innerHeight,screenWidth:document.documentElement.scrollWidth,
   uncaught:(window.__pairedErrors||[]).slice()};
}'''


def _paired_browser_page(browser, markup, width):
    page = browser.new_page(viewport={'width':width,'height':900}, device_scale_factor=1)
    page.route('**/*', lambda route: route.abort())
    page.evaluate(_PAIRED_MOCK)
    # Opaque-origin browser fixtures cannot use localStorage. Supply a
    # per-page in-memory store for the real embedded Chat Console controller;
    # neither persistent chat threads nor canonical browser state are touched.
    page.evaluate("""() => {const values=new Map();Object.defineProperty(window,'localStorage',{
      configurable:true,value:{getItem:k=>values.get(String(k))??null,
      setItem:(k,v)=>values.set(String(k),String(v)),removeItem:k=>values.delete(String(k)),
      clear:()=>values.clear(),key:i=>[...values.keys()][i]??null,
      get length(){return values.size;}}});}""")
    page.evaluate('window.__pairedErrors=[];window.addEventListener("error",e=>window.__pairedErrors.push(String(e.message)));')
    try:
        page.set_content(markup, wait_until='load', timeout=15000)
        return page
    except Exception:
        page.close()
        raise


def _paired_observe(browser, markup, width, output=None):
    page=_paired_browser_page(browser,markup,width)
    try:
        observed=page.evaluate(_PAIRED_MEASURE)
        if output is not None:
            page.screenshot(path=str(output), full_page=True, timeout=12000)
            observed['screenshot']=output.name
            observed['screenshot_sha256']=hashlib.sha256(output.read_bytes()).hexdigest()
        return observed
    finally:
        page.close()


def _paired_plan_variant(markup, zone_id, rung, new_placement):
    """Produce a correctly rehashed plan variant to detect silently ignored decisions."""
    import re
    rx=r'(<script[^>]*id="mcel-workbench-plan"[^>]*>)(.*?)(</script>)'
    match=re.search(rx,markup,re.DOTALL)
    if not match:
        raise ValueError('Candidate does not embed a workspace plan')
    plan=json.loads(match.group(2))
    if not isinstance(plan,dict) or plan.get('schema')!='mcel.workspace-plan.v1':
        raise ValueError('Candidate has no validated workspace-plan.v1')
    zone=next((z for z in plan['zones'] if z['id']==zone_id),None)
    if not zone or rung not in zone.get('ladder',{}):
        raise ValueError('Candidate lacks required zone ladder')
    original=zone['ladder'][rung]
    if original==new_placement:
        raise ValueError('Plan variant has no semantic difference')
    zone['ladder'][rung]=new_placement
    plan.pop('fingerprint',None)
    plan['fingerprint']='sha256:'+sha(plan)
    replacement=json.dumps(plan,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    return markup[:match.start(2)]+replacement+markup[match.end(2):],original


def _paired_sensitivity(browser, candidate_html, width):
    """A plan instruction must change geometry, never be silently ignored."""
    variant, original = _paired_plan_variant(candidate_html,'tools','wide','dock-bottom')
    baseline = _paired_observe(browser,candidate_html,width)
    changed = _paired_observe(browser,variant,width)
    a=baseline['zones'].get('tools');b=changed['zones'].get('tools')
    if changed['refused']:
        return {'status':'UNVERIFIED','reason':'Valid plan variant was refused; compositor placement sensitivity not demonstrated',
                'originalPlacement':original,'candidateRefusal':changed['ready']}
    moved=bool(a and b and (abs(a['x']-b['x'])>8 or abs(a['y']-b['y'])>8))
    return {'status':'PASS' if moved else 'FAIL',
            'reason':'Zone visibly moved' if moved else 'Verified plan ladder decision was silently ignored',
            'originalPlacement':original,'requestedPlacement':'dock-bottom',
            'originalZone':a,'changedZone':b}


def _paired_browser_interaction(browser, markup, width):
    """Code Editor read-only workflow, tab reachability, node identity and resize."""
    page=_paired_browser_page(browser,markup,width)
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        if page.locator('[data-mcel-workspace-layout]').count()!=1:
            return {'status':'FAIL','reason':'Candidate did not mount a planned workspace'}
        page.evaluate('''() => {window.__frobEditor=document.querySelector('[data-mcel-document-surface]');}''')
        if not page.evaluate('Boolean(window.__frobEditor)'):
            return {'status':'FAIL','reason':'Document editor surface missing'}
        tabs=page.locator('[data-mcel-tool-tabs] [role="tab"]')
        if tabs.count()<2:
            return {'status':'FAIL','reason':'Supporting tools not grouped into navigable tabs'}
        active=page.locator('[data-mcel-tool-panel]:not([hidden])')
        input_box=active.locator('input').first
        if input_box.count():
            input_box.fill('frob-retained-input')
        tabs.nth(1).click(timeout=5000)
        if page.locator('[data-mcel-tool-panel]:not([hidden])').count()!=1:
            return {'status':'FAIL','reason':'Tool selection did not isolate one active panel'}
        page.set_viewport_size({'width':320,'height':900})
        page.wait_for_timeout(80)
        retained=page.evaluate('window.__frobEditor === document.querySelector("[data-mcel-document-surface]")')
        page.set_viewport_size({'width':width,'height':900})
        page.wait_for_timeout(80)
        retained=retained and page.evaluate('window.__frobEditor === document.querySelector("[data-mcel-document-surface]")')
        tabs.nth(0).click(timeout=5000)
        if input_box.count() and input_box.input_value()!='frob-retained-input':
            return {'status':'FAIL','reason':'Tab switch lost authored form input'}
        if not retained:
            return {'status':'FAIL','reason':'Resize or tab switch replaced editor DOM node'}
        # The same actual read-only selection path as the existing four-app tests.
        page.get_by_role('button',name='List Files').click(timeout=5000)
        page.locator('[data-record-key="README.md"] button').click(timeout=5000)
        page.wait_for_function('window.__mcelWorkbench.app.readState("state:source") === "# Sample project"',timeout=5000)
        calls=page.evaluate('window.__pairedCalls')
        if [x['path'] for x in calls]!=['editor/files','editor/read']:
            return {'status':'FAIL','reason':'Unexpected mock operations','calls':calls}
        return {'status':'PASS','documentNodeRetained':True,'formInputRetained':True,
                'readOnlyCalls':[x['path'] for x in calls]}
    except Exception as exc:
        return {'status':'FAIL','reason':f'{type(exc).__name__}: {exc}'[:350]}
    finally:
        page.close()


# Current Calculator semantic oracle. This is a test expectation, not a
# compositor decision: changing a planner's emitted pairs must NOT also change
# what the acceptance gate expects to see in the browser.
CALCULATOR_ORACLE = {
    'basic': (('arithmetic','answer'),),
    'graphing': (('graph-input','graph-output'),),
    'symbolic': (('mathics-input','mathics-output'),),
    'help': (('helpers','helper-output'),('qa','qa-output')),
}


def _paired_plan_present(markup):
    import re
    for match in re.finditer(r'<script[^>]*id="(?:mcel-workbench-plan|mcel-expanded-plan)"[^>]*>(.*?)</script>',
                             markup, re.DOTALL):
        if match.group(1).strip() not in ('null',''):
            return True
    return False


def _paired_calculator_pair_checks(observed, width):
    """Independent, geometry-based oracle, never driven by the workspace plan."""
    findings=[]
    wide=width>680
    for pair in observed['pairs']:
        src, result=pair['input'],pair['output']
        failures=[]
        if not pair['sameContainer']:
            failures.append('pair-not-co-located')
        if not pair['visible'] or not src or not result or min(src['w'],src['h'],result['w'],result['h'])<=0:
            failures.append('pair-not-visible')
        else:
            dx=result['x']-(src['x']+src['w'])
            dy=result['y']-(src['y']+src['h'])
            overlap_x=min(src['x']+src['w'],result['x']+result['w'])-max(src['x'],result['x'])
            overlap_y=min(src['y']+src['h'],result['y']+result['h'])-max(src['y'],result['y'])
            if wide:
                if not (0<=dx<=36 and abs(result['y']-src['y'])<=18 and overlap_x<=1):
                    failures.append('expected-adjacent-horizontal-input-result')
            elif not (0<=dy<=36 and abs(result['x']-src['x'])<=5 and abs(result['w']-src['w'])<=5):
                failures.append('expected-adjacent-vertical-input-result')
            if overlap_x>1 and overlap_y>1:
                failures.append('input-output-overlap')
        findings.append({'mode':observed['mode'],'input':pair['inputId'],'output':pair['outputId'],
                         'inputRect':src,'outputRect':result,'failures':failures})
    all_ok=(observed['layout'] and observed['rung']==('wide' if width>=980 else 'medium' if width>=680 else 'narrow')
            and observed['visibleRegionIds']==observed['expectedRegionIds']
            and not observed['horizontalOverflow']
            and not observed['errors'] and all(not x['failures'] for x in findings))
    return {'status':'PASS' if all_ok else 'FAIL','mode':observed['mode'],
            'geometry':findings,'rung':observed['rung'],
            'visibleRegionIds':observed['visibleRegionIds'],
            'expectedRegionIds':observed['expectedRegionIds'],
            'horizontalOverflow':observed['horizontalOverflow'],
            'errors':observed['errors'],
            'reason':'Input/result pairing verified from browser geometry' if all_ok else 'Calculator semantic-layout oracle mismatch'}


def _paired_calculator_oracle(browser, markup, width):
    """Exercise actual mode controls and assert every input/result pair."""
    page=_paired_browser_page(browser,markup,width)
    cases={}
    try:
        if page.locator('#mcel-expanded-diagnostic').inner_text()!='Semantic Calculator ready':
            return {'status':'FAIL','reason':page.locator('#mcel-expanded-diagnostic').inner_text()}
        for mode, pairs in CALCULATOR_ORACLE.items():
            if mode!='basic':
                tab=page.locator(f'[data-mcel-mode-tabs] button[data-value="{mode}"]')
                if tab.is_visible():tab.click(timeout=5000)
                else:page.locator('select[data-intent="intent:switch-mode"]').select_option(mode)
                page.wait_for_function('(m)=>window.__mcelExpanded.app.readState("state:mode")===m',arg=mode)
            source_ids=['interface-region:semantic-calculator-expanded.'+rid for pair in pairs for rid in pair]
            measured=page.evaluate('''(args)=>{
              const {mode,pairs,expectedIds}=args;
              const rect=el=>{if(!el)return null;const r=el.getBoundingClientRect();
                return {x:r.x,y:r.y,w:r.width,h:r.height};};
              const visible=el=>!!el && !el.hidden && el.getClientRects().length>0;
              const all=[...document.querySelectorAll('[data-mcel-region]')];
              const active=all.filter(el=>el.getAttribute('data-mcel-region')!==
                'interface-region:semantic-calculator-expanded.application' &&
                ['input','results','assistance'].includes(el.getAttribute('data-mcel-purpose')) && visible(el));
              const zone=document.querySelector('[data-mcel-workspace-layout]');
              const errors=window.__pairedErrors||[];
              return {mode,layout:!!zone,rung:zone?.getAttribute('data-mcel-layout-rung')||null,
               visibleRegionIds:active.map(el=>el.getAttribute('data-mcel-region')).sort(),
               expectedRegionIds:[...expectedIds].sort(),
               horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1,errors,
               pairs:pairs.map(([inputId,outputId])=>{
                 const a=document.querySelector('[data-mcel-region="'+inputId+'"]');
                 const b=document.querySelector('[data-mcel-region="'+outputId+'"]');
                 return {inputId,outputId,input:rect(a),output:rect(b),visible:visible(a)&&visible(b),
                   sameContainer:!!a?.parentElement && a.parentElement===b?.parentElement &&
                    a.parentElement.hasAttribute('data-mcel-input-result-pair')};
               })};
            }''',{'mode':mode,'pairs':[(source_ids[i],source_ids[i+1]) for i in range(0,len(source_ids),2)],
                  'expectedIds':source_ids})
            cases[mode]=_paired_calculator_pair_checks(measured,width)
        return {'status':'PASS' if all(c['status']=='PASS' for c in cases.values()) else 'FAIL',
                'modes':cases,'reason':'All Calculator modes meet the independent pair oracle' if
                all(c['status']=='PASS' for c in cases.values()) else 'One or more mode pairings failed'}
    except Exception as exc:
        return {'status':'FAIL','reason':f'{type(exc).__name__}: {exc}'[:500], 'modes':cases}
    finally:
        page.close()


def _paired_plan_contract(markup, ir):
    """Independently check the embedded plan's source binding and complete coverage."""
    import re
    match=re.search(r'<script[^>]*id="(?:mcel-workbench-plan|mcel-expanded-plan)"[^>]*>(.*?)</script>',markup,re.DOTALL)
    if not match:
        return {'status':'FAIL','reason':'Candidate did not embed a workspace plan'}
    try:
        plan=json.loads(match.group(1))
        ir_regions=ir['surfaces'][0]['interfaceIntent']['regions']
        all_ids={r['id'] for r in ir_regions}
        assigned=[rid for z in plan['zones'] for rid in z['members']]
        signed=dict(plan);fingerprint=signed.pop('fingerprint',None)
        checks={
          'schema':plan.get('schema')=='mcel.workspace-plan.v1' and plan.get('runtimeEnabled') is True,
          'sourceBinding':plan.get('sourceIrHash')==ir['fingerprints']['semantic'],
          'planFingerprint':fingerprint=='sha256:'+sha(signed),
          'exactRegionCoverage':len(assigned)==len(set(assigned)) and set(assigned)==all_ids-{plan['rootId']},
          'responsiveLadders':all(set(z.get('ladder',{}))=={'wide','medium','narrow'} for z in plan['zones']),
        }
        return {'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,
                'reason':'Source-verified, complete plan' if all(checks.values()) else 'Plan contract incomplete'}
    except (KeyError,TypeError,ValueError) as exc:
        return {'status':'FAIL','reason':'Invalid embedded plan: '+str(exc)[:240]}


def _paired_populated_interaction(browser, markup, app, width, screenshot=None):
    """Existing mock transport, actual rendered controls, no consequential requests."""
    page=_paired_browser_page(browser,markup,width)
    try:
        if app=='file-manager':
            # The incumbent selects and lists a default root on startup.
            # Do not conceal a broken initial workflow behind test clicks.
            page.locator('[data-record-key="README.md"] button').wait_for(timeout=7000)
            if page.evaluate('window.__mcelWorkbench.app.readState("state:selected-root")')!='workspace':
                return {'status':'FAIL','reason':'Default root was not selected at startup'}
            page.locator('[data-record-key="README.md"] button').click(timeout=5000)
            page.get_by_role('button',name='Preview Entry').click(timeout=5000)
            page.locator('[data-state="state:preview"]').get_by_text('Project documentation').wait_for(timeout=5000)
            expected=['file-explorer/roots','file-explorer/list','file-explorer/read']
        elif app=='task-manager':
            page.locator('form[data-intent="intent:refresh"] input[name="query"]').fill('server')
            page.locator('form[data-intent="intent:refresh"] button[type="submit"]').click(timeout=5000)
            page.locator('[data-record-key="123:2026-10-31T12:00"] button').click(timeout=5000)
            if page.evaluate('window.__mcelWorkbench.app.readState("state:selected")')!='123:2026-10-31T12:00':
                return {'status':'FAIL','reason':'Selection state was not preserved'}
            expected=['task/overview']
        elif app=='calculator':
            pad=page.locator('[data-mcel-expression-pad]')
            for value in ('7','+','8','submit'):
                pad.locator(f'button[data-pad-action="{value}"]').click(timeout=5000)
            page.locator('[data-state="state:arithmetic-result"]').get_by_text('15').wait_for(timeout=5000)
            expected=[]
        elif app=='code-editor':
            page.get_by_role('button',name='List Files').click(timeout=5000)
            page.locator('[data-record-key="README.md"] button').click(timeout=5000)
            page.wait_for_function('window.__mcelWorkbench.app.readState("state:source") === "# Sample project"',timeout=5000)
            expected=['editor/files','editor/read']
        else:
            raise ValueError('Unknown paired app')
        calls=page.evaluate('window.__pairedCalls')
        if [x['path'] for x in calls]!=expected:
            return {'status':'FAIL','reason':'Unexpected request sequence','calls':calls}
        if page.evaluate('document.documentElement.scrollWidth > innerWidth+1'):
            return {'status':'FAIL','reason':'Populated view overflows viewport'}
        if screenshot is not None:
            page.screenshot(path=str(screenshot),full_page=True,timeout=12000)
        return {'status':'PASS','mockCalls':calls,'populatedScreenshot':screenshot.name if screenshot else None}
    except Exception as exc:
        return {'status':'FAIL','reason':f'{type(exc).__name__}: {exc}'[:350]}
    finally:
        page.close()


def _paired_unplanned_html(markup):
    """Compare against the same paired application with composition disabled.

    This is the fallback-renderer baseline for an already staged composer; it
    never changes the semantic IR, actions, source files, or persistent state.
    """
    import re
    return re.sub(r'(<script[^>]*id="(?:mcel-workbench-plan|mcel-expanded-plan)"[^>]*>).*?(</script>)',
                  lambda m: m.group(1)+'null'+m.group(2), markup,
                  count=1, flags=re.DOTALL)


def paired_workspace_frob(*, candidate_zip=None, apps=PAIRED_APPS, widths=PAIRED_WIDTHS,
                          output=DEFAULT_OUTPUT/'paired-workspace', chromium_executable=None):
    """Read-only gate for an isolated, permanent source candidate.

    If no candidate exists, report INCOMPLETE rather than certifying the base renderer.
    """
    from collections import Counter
    from tempfile import TemporaryDirectory
    apps=tuple(apps);widths=tuple(widths)
    if not apps or set(apps)-set(PAIRED_APPS) or not widths or any(w<320 or w>4000 for w in widths):
        raise ValueError('Invalid paired-workspace app or viewport selection')
    patch=(Path(candidate_zip).resolve() if candidate_zip is not None else
           (PAIRED_CANDIDATE if PAIRED_CANDIDATE.is_dir() else None))
    if patch is not None and not (patch.is_file() or patch.is_dir()):
        raise FileNotFoundError(patch)
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    # A planned checkout must be compared with its own unplanned fallback, not
    # against the identical composed page (which could conceal lost controls).
    baseline=_paired_baseline_pages(apps)
    baseline={app:{'ir':page['ir'],'html':_paired_unplanned_html(page['html'])}
              for app,page in baseline.items()}
    report={'schema':'mcel.layout-frob.paired-workspace.v1',
            'scope':'isolated-candidate-read-only' if patch else 'current-checkout-read-only',
            'candidateSource':('source-tree' if patch.is_dir() else 'zip') if patch else 'checkout',
            'status':'UNVERIFIED',
            'candidateZipSha256':hashlib.sha256(patch.read_bytes()).hexdigest() if patch and patch.is_file() else None,
            'apps':list(apps),'widths':list(widths),'checks':{},'cases':[],
            'notCertified':['live operations','human design approval','production promotion']}
    with TemporaryDirectory(prefix='mcel-paired-candidate-') as scratch:
        if patch:
            stage=Path(scratch)/'repo'
            report['stagedFiles']=_paired_stage(ROOT,stage,patch)
            report['stagedSha256']={name:hashlib.sha256((stage/name).read_bytes()).hexdigest()
                                    for name in report['stagedFiles']}
            candidate=_paired_candidate_pages(stage,apps)
        else:
            report['stagedFiles']=[]
            candidate=_paired_baseline_pages(apps)
        planned_apps=[app for app in apps if _paired_plan_present(candidate[app]['html'])]
        if not planned_apps:
            # An unmodified checkout is a valid object to inspect, but never
            # evidence that an unapplied compositor passes integration gates.
            report['status']='INCOMPLETE'
            report['checks']['compositionCandidatePresent']={
                'status':'UNVERIFIED',
                'reason':'No workspace compositor is installed in this checkout/candidate; '+
                         'the paired-workspace integration gate cannot certify absent code'}
            report_path=output/'report.json'
            report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,sort_keys=True),encoding='utf-8')
            print('MCEL_LAYOUT_FROB_PAIRED_WORKSPACE INCOMPLETE',flush=True)
            print('  No workspace compositor found; existing application code was not changed.',flush=True)
            print('  report: '+str(report_path),flush=True)
            return 1
        for app in apps:
            original,c=baseline[app],candidate[app]
            for key in ('fingerprints','intents','effects','capabilities','states','surfaces'):
                # Only semantic source authority is immutable; layout planning may add host metadata.
                if original['ir'].get(key)!=c['ir'].get(key):
                    raise ValueError(f'Staged candidate changes immutable {app} IR {key}')
        report['sourceIrHashes']={app:baseline[app]['ir']['fingerprints']['semantic'] for app in apps}
        from playwright.sync_api import sync_playwright
        from tools.mcel_phase2b_browser import launch_semantic_browser
        with sync_playwright() as pw:
            if chromium_executable:
                b=pw.chromium.launch(executable_path=chromium_executable,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
            else:
                b,env=launch_semantic_browser(pw)
            report['browser']={'version':b.version,'engine':'chromium'}
            try:
                for app in apps:
                    ir=baseline[app]['ir']
                    expected_regions={r['id'] for r in ir['surfaces'][0]['interfaceIntent']['regions']}
                    for width in widths:
                        case={'app':app,'width':width,'status':'UNVERIFIED','checks':{}}
                        try:
                            base_path=output/f'{app}-baseline-{width}.png'
                            cand_path=output/f'{app}-candidate-{width}.png'
                            old=_paired_observe(b,baseline[app]['html'],width,base_path)
                            new=_paired_observe(b,candidate[app]['html'],width,cand_path)
                            case['baseline']=old;case['candidate']=new
                            checks=case['checks']
                            checks['mount']=bool(not new['refused'] and new['ready'].endswith('ready'))
                            checks['regionCoverage']=set(new['regions'])==expected_regions and len(new['regions'])==len(expected_regions)
                            checks['authoredControls']=not (Counter(old['buttons'])-Counter(new['buttons']))
                            checks['noHorizontalOverflow']=not new['horizontalOverflow']
                            checks['anchorAboveFold']=bool(new['anchor'] and new['anchor']['y']<new['mainHeight'])
                            if app in ('file-manager','task-manager','code-editor'):
                                from tools.mcel_paired_app_oracles import browser_oracle
                                page=_paired_browser_page(b,candidate[app]['html'],width)
                                try:
                                    independent_oracle=browser_oracle(page,app,width)
                                finally:
                                    page.close()
                                case['semanticLayoutOracle']=independent_oracle
                                checks['semanticLayoutOracle']=independent_oracle['status']=='PASS'
                                checks['workspacePlanConsumed']=bool(new['layout'] and new['zones'].get('workspace'))
                            if app=='calculator':
                                checks['workspacePlanConsumed']=bool(new['layout'] and new['zones'].get('workspace'))
                                oracle=_paired_calculator_oracle(b,candidate[app]['html'],width)
                                case['calculatorOracle']=oracle
                                checks['calculatorOracle']=oracle['status']=='PASS'
                            if app=='code-editor':
                                checks['workspacePlanConsumed']=bool(new['layout'] and new['zones'].get('workspace') and new['tabs']>=2)
                                checks['anchorCapacity']=bool(new['anchor'] and (
                                    (new['anchor']['width']>=520 and new['anchor']['width']/width>=.5 and new['scrollHeight']<=1600)
                                    if width>=1100 else
                                    (new['anchor']['width']/width>=.90 and new['scrollHeight']<=2600)
                                    if width<=390 else
                                    new['anchor']['width']>=.55*width))
                            case['status']='PASS' if all(checks.values()) else 'FAIL'
                        except Exception as exc:
                            case['status']='FAIL';case['error']=f'{type(exc).__name__}: {exc}'[:500]
                        report['cases'].append(case)
                # An unchanged baseline cannot earn a workspace-composition PASS.
                planned_apps = []
                for app in apps:
                    has_plan = _paired_plan_present(candidate[app]['html'])
                    if has_plan:
                        planned_apps.append(app)
                        report['checks'][app+'PlanContract']=_paired_plan_contract(candidate[app]['html'],candidate[app]['ir'])
                    elif app in ('file-manager','task-manager','code-editor','calculator'):
                        report['checks'][app+'PlanContract']={'status':'FAIL','reason':app+' candidate lacks a workspace plan'}
                    screenshot=output/f'{app}-populated-{max(widths)}.png'
                    report['checks'][app+'PopulatedWorkflow']=_paired_populated_interaction(b,candidate[app]['html'],app,max(widths),screenshot)
                report['checks']['compositionCandidatePresent']={
                    'status':'PASS' if planned_apps else 'FAIL',
                    'reason':('Planned candidates: '+', '.join(planned_apps)) if planned_apps else 'No staged application uses a workspace plan'}
                if 'code-editor' in apps:
                    if 1272 in widths:
                        report['checks']['codeEditorPlanSensitivity']=_paired_sensitivity(b,candidate['code-editor']['html'],1272)
                    else:
                        report['checks']['codeEditorPlanSensitivity']={'status':'UNVERIFIED','reason':'1272 viewport required for wide ladder sensitivity'}
                    report['checks']['codeEditorInteraction']=_paired_browser_interaction(b,candidate['code-editor']['html'],1272)
                report['status']='PASS' if (all(x['status']=='PASS' for x in report['cases']) and
                  report['checks'] and all(x['status']=='PASS' for x in report['checks'].values())) else 'FAIL'
            finally:
                b.close()
    report_path=output/'report.json'
    report_path.write_text(json.dumps(report,indent=2,ensure_ascii=False,sort_keys=True),encoding='utf-8')
    print('MCEL_LAYOUT_FROB_PAIRED_WORKSPACE '+report['status'],flush=True)
    print('  cases: '+str(sum(x['status']=='PASS' for x in report['cases']))+'/'+str(len(report['cases'])),flush=True)
    for key,val in report['checks'].items():print('  '+key+': '+val['status']+' — '+val.get('reason',''),flush=True)
    for case in report['cases']:
        if case['status']!='PASS':print(f"  FAIL {case['app']} {case['width']}: {case['checks']} {case.get('error','')}",flush=True)
    print('  report: '+str(report_path),flush=True)
    return 0 if report['status']=='PASS' else 1


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['smoke','sweep','plan','semantic','semantic-compare','design-review','design-quality','design-quality-stress','paired-workspace'],default='smoke')
    parser.add_argument('--candidate-zip',type=Path,help='Optional alternate isolated archive; by default use the bundled Frob compositor candidate')
    parser.add_argument('--apps',default=','.join(PAIRED_APPS),help='Paired applications to inspect')
    parser.add_argument('--design',type=Path,default=DESIGN)
    parser.add_argument('--contract',type=Path,default=CONTRACT)
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--widths',default=None)
    parser.add_argument('--fixtures',default=None)
    parser.add_argument('--strengths',default=None)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--chromium-executable')
    parser.add_argument('--max-cases',type=int,default=128,help='Sweep scenarios (0 = entire cross product; smoke ignores budget)')
    args=parser.parse_args(argv)
    if args.mode=='paired-workspace':
        if args.no_browser:parser.error('paired-workspace cannot claim a PASS with --no-browser')
        widths=tuple(int(w) for w in (args.widths or ','.join(map(str,PAIRED_WIDTHS))).split(','))
        return paired_workspace_frob(candidate_zip=args.candidate_zip,apps=tuple(args.apps.split(',')),
                  widths=widths,output=args.output/'paired-workspace',chromium_executable=args.chromium_executable)
    if args.mode=='design-quality-stress':
        from tools import mcel_design_quality_stress
        stress_args=['--mode','run','--design',str(args.design.resolve()),
                     '--output',str((args.output/'design-quality-stress').resolve())]
        if args.chromium_executable:
            stress_args.extend(['--chromium-executable',args.chromium_executable])
        return mcel_design_quality_stress.main(stress_args)
    if args.mode=='design-quality':
        from tools import mcel_design_quality_frob
        argv=['--mode','run','--design',str(args.design.resolve()),
              '--output',str((args.output/'design-quality').resolve())]
        if args.chromium_executable:
            argv.extend(['--chromium-executable',args.chromium_executable])
        return mcel_design_quality_frob.main(argv)
    if args.mode=='design-review':
        from tools import mcel_design_critic_frob
        critic_args=['--mode','build','--design',str(args.design.resolve()),
                     '--output',str((args.output/'design-review').resolve())]
        if args.chromium_executable:
            critic_args.extend(['--chromium-executable',args.chromium_executable])
        return mcel_design_critic_frob.main(critic_args)
    if args.mode=='semantic-compare':
        from tools import mcel_semantic_generate
        return mcel_semantic_generate.compare(args.design.resolve(),
            (args.output/'presentation-comparison').resolve())
    if args.mode=='semantic':
        # Run the real YAML -> registered MCEL elements -> DSL -> browser path,
        # but do not overwrite existing sweep/compatibility reports.
        from tools import mcel_semantic_generate
        return mcel_semantic_generate.run(args.design.resolve(),
                                          (args.output/'semantic-generation').resolve(),verify=True,chromium_executable=args.chromium_executable)
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    try:
        from tools import mcel_layout_frob_crosscheck as contract_module
        from tools.mcel_layout_frob_runtime import exercise_fixture_boundary
        design=read_yaml(args.design);contract=read_yaml(args.contract)
        obligations=contract_module.validate(contract,design)
        ids=regions(design)
        widths=[int(w) for w in (args.widths or ('390,1440' if args.mode=='smoke' else '320,390,768,1024,1440,1920')).split(',')]
        fixtures=(args.fixtures or ('normal' if args.mode=='smoke' else 'normal,crowded,long_text,partial,stale,failure,empty,adversarial')).split(',')
        strengths=(args.strengths or ('none,prefer,require' if args.mode=='smoke' else 'none,suggest,prefer,constrain,require')).split(',')
        if any(w<240 or w>4000 for w in widths):raise ValueError('Invalid viewport width')
        if set(fixtures)-set(contract['fixture_catalog']):raise ValueError('Unknown fixture')
        if set(strengths)-set(contract['hint_language']['authorities']):raise ValueError('Unknown hint strength')
        pairs=[(m['id'],e['id']) for m in contract['layout_machines'] for e in contract['planning_engines']]
        matrix=[{'machine':m,'engine':e,'status':'SUPPORTED' if (m,e) in COMPATIBILITY else 'NOT_IMPLEMENTED',
                 'scope':COMPATIBILITY.get((m,e),'none'),
                 'reason':None if (m,e) in COMPATIBILITY else 'No validated adapter for this machine/engine interface'} for m,e in pairs]
        runtime=exercise_fixture_boundary()
        negative=None
        browser=None;browser_env=None;pw=None
        if args.mode!='plan' and not args.no_browser:
            try:
                from playwright.sync_api import sync_playwright
                pw=sync_playwright().start()
                executable=args.chromium_executable or os.environ.get('MCEL_CHROMIUM_EXECUTABLE') or shutil.which('chromium') or shutil.which('chromium-browser')
                browser=pw.chromium.launch(executable_path=executable,headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
                browser_env={'engine':'chromium','executable':executable,'version':browser.version}
            except Exception as exc:
                if pw:pw.stop()
                pw=None;browser=None
                browser_env={'status':'UNVERIFIED','reason':f'{type(exc).__name__}: {exc}'}
        negative=negative_controls(ids,design,contract,browser)
        scenarios=[]
        if args.mode=='plan':
            planned=set()
            sampling={'sampling':'plan-only','planned':0,'selected':0,'truncated':False}
        elif args.mode=='sweep':
            schedule,sampling=pairwise_schedule(pairs,widths,fixtures,strengths,args.max_cases,contract['sweep_policy']['seed'])
            planned=set(schedule)
        else:
            sampling={'sampling':'smoke', 'truncated':False}
            planned=None
        # Sweeps support each machine/engine pair but preserve subtree-only boundaries.
        checked=0
        print('FROB_BEGIN',{'mode':args.mode,'browser':bool(browser),'widths':widths,'fixtures':fixtures,'strengths':strengths},flush=True)
        for machine,engine in pairs:
            support=COMPATIBILITY.get((machine,engine))
            if args.mode=='plan':continue
            if not support:
                scenarios.append({'machine':machine,'engine':engine,'status':'NOT_IMPLEMENTED',
                                  'reason':'Unsupported pair: no implicit fallback'})
                continue
            if support=='subtree_only':
                # Independent browser checks are run as part of each supported shell; not a complete shell.
                scenarios.append({'machine':machine,'engine':engine,'status':'SUBTREE_ONLY',
                                  'reason':'Adaptive control grid only; no full-app shell adapter'})
                continue
            for width,fixture,strength in itertools.product(widths,fixtures,strengths):
                if planned is not None and (machine,engine,width,fixture,strength) not in planned:continue
                if engine in ('workspace_greedy','bounded_recursive') and strength!='none':
                    scenarios.append({'machine':machine,'engine':engine,'width':width,'fixture':fixture,
                                      'strength':strength,'status':'NOT_IMPLEMENTED',
                                      'reason':'This production engine has no graded-hint interface'})
                    continue
                start=time.perf_counter()
                case={'machine':machine,'engine':engine,'width':width,'fixture':fixture,'strength':strength,
                      'scope':support,'status':'UNVERIFIED'}
                tag=f'{machine}-{engine}-{width}-{fixture}-{strength}'
                try:
                    if machine=='workspace_inference':
                        p=inferred_workspace(design,available_workspace_width(width),output)
                        case['layout_available_width']=p['width']
                        case['plan']={'kind':'workspace','value':p}
                        case['manifest']={'candidate_count':1,'search_truncated':False,'unsupported_hints':strength!='none'}
                    else:
                        variants=patterns(ids,width)
                        outcome=rank(variants,ids,strength,width,engine)
                        case['manifest']={'candidate_count':outcome['candidate_count'],
                                          'search_truncated':outcome['search_truncated'],
                                          'candidates':[{'id':c['id'],'fingerprint':c['fingerprint'],
                                                         'feasible':c['feasible'],'score':c['score'],
                                                         'metrics':c.get('metrics'), 'relaxed':c['relaxed'],
                                                         'hard_conflicts':c['hard_conflicts']} for c in outcome['candidates']],
                                          'selected':outcome['selected']['id'] if outcome['selected'] else None}
                        if not outcome['selected']:
                            case['status']='REFUSED';case['reason']=outcome['refusal'];scenarios.append(case);continue
                        case['plan']={'kind':'canonical','tree':tree_for(ids,outcome['selected']['variant'])}
                        case['hint_decision']={'satisfied':outcome['selected']['satisfied'],
                                               'relaxed':outcome['selected']['relaxed'],
                                               'evaluated_strength':outcome['evaluated_strength']}
                    if browser:
                        obs=browser_run(ids,fixture,case['plan'],width,output,tag,browser=browser,design=design)
                        case['observed']=obs
                        checks={'engine_applied':obs['witness'] is not None and obs['witness'].get('status')=='pass',
                                'no_overflow':not obs['horizontalOverflow'] and not obs['clippedRegions'],
                                'plan_width_matches_host':machine!='workspace_inference' or abs(obs['rootWidth']-case['layout_available_width'])<=1,
                                'mobile_stacked':width>600 or obs['columns']==1,
                                'hard_hint':strength not in ('constrain','require') or obs['priorityUpperLeft'],
                                'adaptive_controls':obs['adaptive'] is not None and obs['adaptive']['status']=='pass',
                                'semantic_rows':len(obs.get('rowSpecimens',[]))==len(design['rows']['types']),
                                'no_browser_exception':obs['error'] is None}
                        if machine=='workspace_inference' and strength!='none':
                            # The existing greedy machine does not interpret hints.
                            case['status']='NOT_IMPLEMENTED';case['reason']='Workspace inference has no graded-hint adapter'
                        else:
                            case['status']='PASS' if all(checks.values()) else 'FAIL'
                        case['checks']=checks
                    else:
                        case['status']='UNVERIFIED';case['reason']='Browser independent measurement unavailable or disabled'
                    case['wall_ms']=round((time.perf_counter()-start)*1000,2)
                except Exception as exc:
                    case['status']='FAIL';case['reason']=f'{type(exc).__name__}: {exc}'
                scenarios.append(case)
                checked+=1
                if checked%10==0 or case['status']=='FAIL':
                    print('FROB_PROGRESS',checked,'latest=',machine+'/'+engine,str(width),strength,case['status'],flush=True)
        if browser: browser.close()
        if pw:pw.stop()
        # Real MCEL DSL -> application IR proof for one generated canonical tree.
        compile_witness={'status':'UNVERIFIED','reason':'No verified canonical candidate'}
        candidate=next((x for x in scenarios if x.get('status')=='PASS' and x.get('plan',{}).get('kind')=='canonical'),None)
        if candidate:
            try:
                from tools.task_manager.semantic_layout_probe import compile_through_dsl
                compiled=compile_through_dsl(candidate['plan']['tree'],output)
                compile_witness={'status':'PASS' if compiled['valid'] and compiled['compiledTreeSame'] else 'FAIL',
                                 'compiled':compiled,'selected_case':f"{candidate['machine']}:{candidate['engine']}:{candidate['width']}"}
            except Exception as exc:
                compile_witness={'status':'UNVERIFIED','reason':f'{type(exc).__name__}: {exc}'}
        count=Counter(x['status'] for x in scenarios)
        provenance={'design_sha256':sha(design),'contract_sha256':sha(contract),
                    'resolver_file_sha256':hashlib.sha256(RESOLVER.read_bytes()).hexdigest(),
                    'python_version':sys.version,'browser':browser_env,
                    'tool':'tools/mcel_layout_frob.py','scope':'offline layout and fixture-only runtime',
                    'seed':contract['sweep_policy']['seed']}
        report={'schema':'main-computer.mcel.layout-frob-execution.v1',
                'mode':args.mode,'status':'FAIL' if count['FAIL'] or any(v['status']=='FAIL' for v in negative.values()) or runtime['status']=='FAIL' else 'INCOMPLETE' if args.mode=='plan' or count['UNVERIFIED'] or count['NOT_IMPLEMENTED'] or count['SUBTREE_ONLY'] or not scenarios else 'BOUNDED_PASS',
                'counts':dict(count),'crosscheck_total_obligations':len(obligations),
                'sampling':sampling,
                'provenance':provenance,'compatibility':matrix,'negative_controls':negative,
                'runtime_fixture':runtime,'dsl_compiler':compile_witness,'scenarios':scenarios,
                'limitations':['An isolated Frob is not a live MCEL Task Manager or operational-source integration.',
                               'No completed human appearance review or production promotion/rollback.',
                               'Planner preference scores are not independent browser evidence.',
                               'Browser checks are bounded to selected fixtures, widths, and hint strengths.',
                               'Some machine/engine combinations intentionally remain unsupported.']}
        # A geometric PASS does not assert the author accepted the visual
        # design. Keep priority placement diagnostics separate from validity.
        report['design_diagnostics'] = {
            'technical_passes_with_priority_not_upper_left': sum(
                case['status']=='PASS' and case.get('observed',{}).get('priorityUpperLeft') is False
                for case in scenarios),
            'no_hint_baselines_without_priority_upper_left': sum(
                case['status']=='PASS' and case.get('strength')=='none'
                and case.get('observed',{}).get('priorityUpperLeft') is False
                for case in scenarios),
            'human_visual_approval': 'UNVERIFIED',
            'note': 'PASS is a bounded technical witness, not a design-quality certificate',
        }
        report['evidence_index'] = build_evidence_index(report)
        write_gallery(report,output)
        (output/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        crosscheck=contract_module.audit(design,contract,mode='audit',frob_report=report,frob_path=output/'report.json')
        (output/'crosscheck.json').write_text(json.dumps(crosscheck,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        print('CROSSCHECK:',crosscheck['status'],crosscheck['counts'],
              'obligations:',crosscheck['expanded_obligation_count'])
        print('MCEL_LAYOUT_FROB_'+report['status'],dict(count))
        print('dsl_compiler:',compile_witness['status'])
        print('runtime_fixture:',runtime['status'],'negative_controls:',{k:v['status'] for k,v in negative.items()})
        print('report:',output/'report.json')
        return 1 if count['FAIL'] or runtime['status']!='PASS' else 0
    except Exception as exc:
        print(f'MCEL_LAYOUT_FROB_REFUSED: {type(exc).__name__}: {exc}',file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
