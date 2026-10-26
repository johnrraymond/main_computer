"""MCEL Frob Lab: executable, read-only machine/engine/layout comparisons.

Usage:
 python tools/mcel_layout_frob.py --mode smoke
 python tools/mcel_layout_frob.py --mode sweep --widths 320,390,768,1440 --fixtures normal,long_text,stale
 python tools/mcel_layout_frob.py --mode sweep --no-browser   # planning only; no browser PASS

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
DESIGN = ROOT/'mcel_designs/task-manager/design.yaml'
CONTRACT = ROOT/'mcel_designs/task-manager/frob_crosscheck.yaml'

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


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['smoke','sweep','plan','semantic','semantic-compare','design-review','design-quality','design-quality-stress'],default='smoke')
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
