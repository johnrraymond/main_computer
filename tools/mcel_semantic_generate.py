"""MCEL semantic application generator — offline, component-contract-checked vertical slice.

python tools/mcel_semantic_generate.py --mode generate
python tools/mcel_semantic_generate.py --mode verify
python tools/mcel_semantic_generate.py --mode verify --design mcel_designs/task-manager/design.yaml

Authors edit application semantic YAML and graduated hints, not CSS positions.
Outputs a standalone generated *fixture application*, a canonical MCEL layout,
registered MCEL component instances, a compiled official DSL, and a browser proof.
No live Task Manager changes or consequential operations are possible here.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

try:
    import yaml
except ImportError as exc:
    raise SystemExit('PyYAML is required') from exc

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import mcel_layout_frob as frob
from tools.mcel_presentation_planner import plan_presentation, validate_pattern_registry
from tools.mcel_presentation_state import fixture_state, plan_state
from tools.mcel_task_composition import plan_task_composition

DEFAULT_DESIGN = ROOT/'mcel_designs/task-manager/design.yaml'
DEFAULT_OUTPUT = ROOT/'runtime/reports/mcel-semantic-generation'
SCRIPTS = ROOT/'main_computer/web/applications/scripts'
REGISTRY = SCRIPTS/'mcel-element-registry.js'
ELEMENTS = SCRIPTS/'mcel-elements-core.js'
RESOLVER = SCRIPTS/'mcel-semantic-layout-inference.js'
RUNTIME_JS = Path(__file__).with_name('mcel_semantic_generated_runtime.js')
STRENGTH = {'suggest': 1.0,'prefer': 12.0,'constrain': 0.0,'require': 0.0}
MODES = ('compact_row','table_row','detail_panel','narrow_card')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',', ':')).encode()).hexdigest()


def _safe_id(name):
    value=re.sub('[^a-z0-9_-]+','-',str(name).lower()).strip('-')
    if not value or not re.match(r'^[a-z]',value):
        raise ValueError(f'Invalid semantic identifier: {name!r}')
    return value


def scoped(hint,width):
    scope=hint.get('when','all')
    return scope=='all' or scope=='mobile' and width<=600 or scope=='tablet' and 600<width<1024 or scope=='desktop' and width>=1024


def checked_hints(design):
    nodes={n['id'] for n in design['hierarchy']['children']}
    result=[]
    for i,raw in enumerate(design.get('layout_hints',[])):
        if not isinstance(raw,dict):raise ValueError(f'layout_hints[{i}] must be a mapping')
        hint=dict(raw)
        name=hint.get('id')
        if not isinstance(name,str) or name in {h['id'] for h in result}:raise ValueError('Missing/duplicate hint id')
        if hint.get('target') not in nodes:raise ValueError(f'{name}: undeclared target {hint.get("target")}')
        if hint.get('strength') not in STRENGTH:raise ValueError(f'{name}: unsupported strength')
        if hint.get('when','all') not in ('all','mobile','tablet','desktop'):raise ValueError(f'{name}: unsupported scope')
        prop,value=hint.get('property'),hint.get('value')
        if prop=='position':
            if value!='upper-left':raise ValueError(f'{name}: unsupported position')
        elif prop=='reading_order':
            if not isinstance(value,dict) or set(value)!={'before'} or value['before'] not in nodes or value['before']==hint['target']:
                raise ValueError(f'{name}: invalid reading order')
        elif prop=='available_space':
            if value!='maximize':raise ValueError(f'{name}: unsupported available_space hint')
        elif prop=='prominence':
            if value!='high':raise ValueError(f'{name}: unsupported prominence hint')
        else:raise ValueError(f'{name}: unsupported hint property {prop!r}')
        result.append(hint)
    return result


def _candidate_grid(layout):
    return [line.split() for line in layout['areas']]


def _span(layout,target):
    return sum(value==target for row in _candidate_grid(layout) for value in row)


def _pos(layout,target):
    return frob.positions(layout)[target]


def _holds(layout,hint):
    target=hint['target']; prop=hint['property']
    if prop=='position':return _pos(layout,target)==(0,0)
    if prop=='reading_order':
        return _pos(layout,target)[0]<_pos(layout,hint['value']['before'])[0]
    if prop=='prominence':return _pos(layout,target)[0]==0
    if prop=='available_space':
        return _span(layout,target)>=max(_span(layout,x) for x in frob.positions(layout))
    raise ValueError(prop)


def plan(design,width,override=None):
    """Candidate scoring is generic: YAML target IDs, not Task Manager constants."""
    ids=frob.regions(design)
    hints=checked_hints(design) if override is None else checked_hints({**design,'layout_hints':override})
    applicable=[h for h in hints if scoped(h,width)]
    layouts=frob.patterns(ids,width)
    work=ids['work']  # semantic *role*, not a Task Manager element ID
    scored=[]
    for index,layout in enumerate(layouts):
        decisions=[];reject=[]
        # Default preference: prioritize space for the dominant workspace.
        base=8.0 * (_pos(layout,work)==(0,0)) + 2.0*_span(layout,work)-0.3*len(layout['areas'])
        score=base
        for hint in applicable:
            passed=_holds(layout,hint)
            if hint['strength'] in ('require','constrain') and not passed:reject.append(hint['id'])
            if hint['strength'] in STRENGTH and passed:score+=STRENGTH[hint['strength']]
            decisions.append({'hint':hint['id'],'strength':hint['strength'],'target':hint['target'],
                              'property':hint['property'],'satisfied':passed,
                              'disposition':'satisfied' if passed else 'conflict' if hint['strength'] in ('require','constrain') else 'relaxed',
                              'reason':'candidate geometry fulfills hint' if passed else 'candidate geometry conflicts with hint'})
        scored.append({'index':index,'score':round(score,3),'feasible':not reject,
                       'rejected_hints':reject,'decisions':decisions,'layout':layout,
                       'fingerprint':fingerprint(layout)})
    viable=[c for c in scored if c['feasible']]
    viable.sort(key=lambda c:(-c['score'],c['fingerprint']))
    selected=viable[0] if viable else None
    if selected is None:
        raise ValueError('MCEL_LAYOUT_HINT_CONFLICT: '+','.join(sorted(set(sum([c['rejected_hints'] for c in scored],[])))))
    return {'viewport':width,'selected':selected,'candidates':scored,
            'applicable_hints':[h['id'] for h in applicable],
            'skipped_hints':[h['id'] for h in hints if h not in applicable]}


def validate_design(design):
    if design.get('schema')!='main-computer.mcel.application-design.v1':raise ValueError('Invalid design schema')
    ids=frob.regions(design)
    if len(set(ids.values()))!=6:raise ValueError('Duplicate layout regions')
    checked_hints(design)
    entities=design['semantics']['entities'];rows=design['rows']['types']
    if not rows:raise ValueError('No semantic rows')
    for name,row in rows.items():
        _safe_id(name)
        if row.get('entity') not in entities:raise ValueError(f'{name}: undeclared entity')
        if not row.get('primary'):raise ValueError(f'{name}: no primary fields')
        for field in row['primary']+row.get('summary',[]):_safe_id(field)
    views=next((x.get('views') for x in design['hierarchy']['children'] if x['role']=='primary-content'),None)
    if not views:raise ValueError('No application views')
    names={v['id'] for v in views}
    if len(names)!=len(views):raise ValueError('Duplicate view IDs')
    for view in views:
        for sec in view.get('sections',[]):
            if sec.get('row') and sec['row'] not in rows:raise ValueError(f'Unknown row type {sec["row"]}')
    for width in (390,768,1440):plan(design,width)
    return True


def _component(id,element,label,children=None,**extra):
    return {'id':_safe_id(id),'elementId':element,'label':label,'children':children or [],'props':extra}


def build_component_tree(design):
    """Compose strictly legal registry hierarchies: app→region→panel→field/action/feed."""
    rows=design['rows']['types'];views=[]
    components=[]
    for region in design['hierarchy']['children']:
        parts=[]
        if region['role']=='primary-content':
            views=region.get('views',[])
            tabs=[_component('tab-'+view['id'],'element.core.action',view.get('title',view['id']),
                             intent='select-view',viewId=view['id']) for view in views]
            parts.append(_component('generated-view-tabs','element.core.toolbar','Views',tabs))
            # One semantic row contract creates one MCEL row panel, not one
            # handwritten table per Task Manager view. All row types are covered.
            for rowid,spec in rows.items():
                fields=[_component(f'{rowid}-{field}', 'element.core.field',field,semanticField=field)
                        for field in spec['primary']+spec.get('summary',[])]
                fields.append(_component(f'{rowid}-inspect','element.core.action','Inspect',intent='inspect',risk='read-only'))
                parts.append(_component('row-'+rowid,'element.core.panel',rowid.replace('-',' ').title(),fields,
                                        rowType=rowid,semanticEntity=spec['entity']))
        for entry in region.get('elements',[]):
            entryid=entry['id']
            field=_component('field-'+entryid,'element.core.field',entryid.replace('-',' ').title(),
                             semanticEntity=entry.get('entity'),sourceStatus=entry.get('status','proposed'))
            parts.append(_component('panel-'+entryid,'element.core.panel',entryid.replace('-',' ').title(),[field]))
        for item in region.get('drawers',[]):
            action=_component('inspect-'+item['id'],'element.core.action','Inspect '+item['id'].replace('-',' '),intent='inspect')
            parts.append(_component('panel-'+item['id'],'element.core.panel',item['id'].replace('-',' ').title(),[action]))
        if region['role']=='supporting-tools':
            for intent in design.get('intents',{}).get('consequential',[]):
                action=_component('effect-'+intent['id'],'element.core.action',intent['id'].replace('_',' ').title(),
                                  intent=intent['id'],riskPolicy={'risk':'destructive','proofPolicy':'no-click','blocked':True})
                parts.append(_component('panel-effect-'+intent['id'],'element.core.panel',
                                        intent['id'].replace('_',' ').title(),[action],consequential=True))
        if not parts:
            parts=[_component('panel-'+region['id'],'element.core.panel',region['id'].replace('-',' ').title(),
                              [_component('field-'+region['id'],'element.core.field','Unconfigured')])]
        components.append(_component(region['id'],'element.core.region',region['id'].replace('-',' ').title(),parts,
                                     semanticRole=region['role']))
    return _component('generated-'+design['application']['id'],'element.core.app',design['application']['name'],components)


def serialize_components(tree,out):
    """Load the actual MCEL registry and definitions, enforcing real allowedChildren."""
    node='''const fs=require('node:fs'),vm=require('node:vm');const w={};const scope={window:w};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),scope);
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),scope);
const tree=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
function rec(node){const children=node.children.map(rec);const info=w.McelElementRegistry.get(node.elementId);
if(!info)throw Error('UNREGISTERED_COMPONENT '+node.elementId);
return w.McelElementRegistry.serializeElement(node.elementId,{id:node.id,label:node.label,
  purpose:'MCEL semantic generation', ...node.props, children});}
const instance=rec(tree);process.stdout.write(JSON.stringify({tree:instance,definitions:w.McelElementRegistry.all()}));'''
    inp=out/'component-source.json';inp.write_text(json.dumps(tree,ensure_ascii=False),encoding='utf-8')
    completed=subprocess.run(['node','-e',node,str(REGISTRY),str(ELEMENTS),str(inp)],capture_output=True,text=True,timeout=25,encoding='utf-8')
    if completed.returncode:raise ValueError('MCEL_REGISTRY_REFUSED: '+completed.stderr[:1500])
    result=json.loads(completed.stdout)
    def family(node):
        yield node['elementId']
        for ch in node['children']:yield from family(ch)
    result['counts']=dict(Counter(family(result['tree'])))
    return result


def fixture_records(design):
    """Synthetic, clearly labeled fixture observations; never invent live telemetry."""
    preset={
      'process':{'name':'python.exe','pid':431,'status':'running','memory_human':'294 MB','command_preview':'python worker.py','is_main_computer':'unknown'},
      'python_process':{'script':'worker.py','pid':431,'classification':'unknown','reason':'Ownership unverified','cpu_percent':'not observed'},
      'port':{'port':8765,'protocol':'TCP','bind_address':'127.0.0.1','state':'listening','owning_processes':'PID 431 (fixture)'},
      'connection':{'local':'127.0.0.1:8765','remote':'—','status':'LISTEN','process_name':'python.exe','pid':431},
      'operation':{'intent':'refresh_snapshot','target_key':'local-host','state':'running (fixture)','progress':'unknown'},
      'worker_task':{'task_id':'attempt-12','origin':'remote (fixture)','state':'unknown','network':'testnet'},
      'ai_call':{'provider':'local','model':'example-model','origin':'worker-task','state':'unknown','latency':'not observed'},
      'scheduled_action':{'action':'restart','run_at':'future (fixture)','enabled':False,'note':'stored only'},
      'cpu':{'core_index':0,'percent':'not observed'},
      'gpu':{'device_name':'GPU 0','utilization':'not observed','memory':'unknown'},
      'alert':{'message':'No live monitoring connected','severity':'notice','observed_at':'fixture only'},
    }
    return {name:{'identity':'fixture:'+name,'entity':spec['entity'],'values':{
                  field:preset.get(spec['entity'],{}).get(field,'not observed')
                  for field in spec['primary']+spec.get('summary',[])}}
            for name,spec in design['rows']['types'].items()}


def compile_application(design,tree,out):
    from main_computer.mcel_dsl_compiler import compile_dsl_application
    app=_safe_id(design['application']['id'])
    source='''"use strict";\nconst mcel=require("@mcel/app");\nmodule.exports=mcel.defineApp(''' + json.dumps({'id':app+'-generated-preview','title':design['application']['name']+' (generated fixture)'})+''',({application})=>application.hostBound(app=>{
app.presentation.hostBound("workspace",{route:"/applications/generated-fixture",root:"#generated-root",presentationAuthority:"existing-host-html"});
app.layout.zones(["primary-content"]);
app.layout.tree('''+json.dumps(tree,separators=(',',':'))+''');
app.proof.semanticRuntimeProven(); /* DSL declaration only, not certified runtime proof */
}));\n'''
    path=out/'generated-application.js';path.write_text(source,encoding='utf-8')
    result=compile_dsl_application(path,write_candidate=False)
    return {'valid':result.valid,'diagnostics':list(result.diagnostics),
            'semantic_fingerprint':result.semantic_fingerprint,
            'tree_preserved':bool(result.normalized_ir) and result.normalized_ir['layouts'][0].get('layoutTree')==tree}


def write_preview(design,component_registry,plans,out,presentation=None,state_aware=True,composition=None,design_critic_strategy=None):
    manifest={'application':design['application'],'regions':design['hierarchy']['children'],
              'rows':design['rows']['types'],'entities':design['semantics']['entities'],
              'hints':checked_hints(design),'plans':{
                  str(k):{'variant':v['selected']['layout'],'decisions':v['selected']['decisions'],
                          'candidate_id':v['selected']['index']} for k,v in plans.items()},
              'fixture':fixture_records(design), 'componentTree':component_registry['tree'],
              'definitions':{d['id']:{'renderer':d['renderer'],'riskPolicy':d['riskPolicy']}
                             for d in component_registry['definitions']},
              'notice':'SYNTHETIC FIXTURE — no live data or effects',
              'presentationPlan':presentation,'compositionPlan':composition,'designCriticStrategy':design_critic_strategy or 'A',
              'statePresets':({preset:{'unselected':plan_state(presentation,fixture_state(preset),
                   required_regions={h['target'] for h in checked_hints(design) if h['strength']=='require'}),
                   'selected':plan_state(presentation,fixture_state(preset,selection='fixture-selected'),
                   required_regions={h['target'] for h in checked_hints(design) if h['strength']=='require'})}
                   for preset in ('unavailable','idle','running','failure','unknown','empty')} if presentation and state_aware else None)}
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    # All rendering code is reusable and lives in the shared runtime JS, not in
    # Task Manager-specific HTML markup or a manually positioned screenshot.
    src=RUNTIME_JS.read_text(encoding='utf-8')
    resolver=RESOLVER.read_text(encoding='utf-8')
    payload=json.dumps(manifest,ensure_ascii=False).replace('<','\\u003c').replace('\u2028','\\u2028')
    css='''*{box-sizing:border-box}html{font:15px/1.45 system-ui,sans-serif;background:#edf2f7;color:#1d2d40}
body{margin:0;padding:16px;max-width:1800px;margin-inline:auto}button,input{font:inherit}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin:0 0 16px}header h1{font-size:24px;letter-spacing:-.6px;margin:0}
.fixture-banner{font-size:12px;color:#75410e;background:#fff0d7;padding:7px 10px;border-radius:8px;font-weight:700}
#generated-root{display:grid;gap:12px;min-width:0;width:100%}.region{min-width:0;padding:18px;background:#fff;border:1px solid #d9e1ea;border-radius:14px;box-shadow:0 3px 12px #233d5512;overflow-wrap:anywhere}
.advanced-tools summary{cursor:pointer;font-size:13px;font-weight:700;color:#47627d;padding:8px 0}.advanced-tools[open] summary{margin-bottom:10px}.region h2{font-size:12px;font-weight:800;letter-spacing:1px;text-transform:uppercase;color:#59718a;margin:0 0 14px}.region[data-role="status-and-attention"]{background:#153451;color:white;border-color:#163b60}
.region[data-role="status-and-attention"] h2{color:#92cbed}.region[data-role="primary-content"]{min-height:300px}
.generated-panel{display:block;padding:9px 0;border-top:1px solid #e7ecf2}.region[data-role="status-and-attention"] .generated-panel{border-color:#31526e}
.generated-panel h3{font-size:14px;margin:3px 0}.muted{color:#6d7e8f;font-size:12px}.region[data-role="status-and-attention"] .muted{color:#b3c5d5}
.tag{display:inline-block;border:1px solid #d8e1eb;border-radius:99px;padding:2px 8px;font-size:11px;color:#496275;letter-spacing:.1px}
.region[data-role="status-and-attention"] .tag{border-color:#6c8ba7;color:#d1e9f9}
.tabs{display:flex;flex-wrap:wrap;gap:6px;border-bottom:1px solid #dce4ef;padding:0 0 10px;margin-bottom:12px}.tabs button{border:0;background:transparent;border-radius:8px;padding:7px 12px;color:#47617b;cursor:pointer}
.tabs button[aria-selected="true"]{background:#dcecff;color:#174f92;font-weight:700}.tabs button:focus-visible,.record:focus-visible{outline:3px solid #e1a232;outline-offset:2px}
.generated-panel[hidden],.generated-row[hidden]{display:none!important}.section-label{font-size:12px;font-weight:700;color:#647d93;margin:13px 0 7px}
.record{display:block;width:100%;text-align:left;border:1px solid #e0e7ef;background:#f8fafc;border-radius:9px;padding:10px;margin-bottom:8px;cursor:pointer;color:#21364c}
.record:hover,.record[aria-current="true"]{border-color:#6ba6e4;background:#e8f3fe}.record .title{font-weight:700;display:block}.facts{display:flex;gap:7px 12px;flex-wrap:wrap;align-items:center;font-size:12px;color:#5a6e82;margin-top:4px}.semantic-record-field{display:inline-block!important}
/* Generic readable records: preserve complete text rather than clipping, truncating or expanding the page. */
.record,.record .title,.record .record-entity,.record .facts,.record .semantic-record-field{
  min-width:0;max-width:100%;white-space:normal;overflow-wrap:anywhere;word-break:normal;
}
.record .facts>*{min-width:0;max-width:100%;}

.search{width:100%;min-width:0;padding:10px 12px;border:1px solid #c9d4e0;border-radius:9px}.inspector-details{font-size:13px;overflow-wrap:anywhere}.inspector-details dl{display:grid;grid-template-columns:1fr 1.5fr;gap:7px}.inspector-details dt{color:#62768a}.inspector-details dd{margin:0;font-weight:600}
[disabled]{opacity:.55;cursor:not-allowed}.safe-note{font-size:12px;color:#6d7e8f}.danger-note{font-size:12px;color:#b33e45}.status-pill{font-size:12px;font-weight:700}
@media(max-width:600px){body{padding:8px}.region{padding:12px}header h1{font-size:20px}.inspector-details dl{grid-template-columns:1fr}}
'''
    if composition:
        css += """
/* Generic generated composition patterns; not application-specific geometry. */
body[data-composition="task-aware"] .workspace-composite{
  display:grid;grid-template-columns:minmax(0,1fr);min-width:0;gap:10px;
  padding:12px;background:#fff;border:1px solid #dbe4ed;border-radius:12px;
  box-shadow:0 1px 4px #22384f0d;
}
body[data-composition="task-aware"] .workspace-composite>[data-role]{min-width:0;box-shadow:none;}
body[data-composition="task-aware"] .workspace-composite>[data-role="global-context-and-search"]{
  border:0;border-radius:0;padding:0 0 10px;border-bottom:1px solid #e2eaf2;
}
body[data-composition="task-aware"] .workspace-composite>[data-role="global-context-and-search"] h2{display:none}
body[data-composition="task-aware"] .workspace-composite>[data-role="primary-content"]{
  border:0;padding:4px 0;min-height:160px;
}
body[data-composition="task-aware"] .workspace-composite>[data-role="selection-details"]{
  padding:12px;border:1px solid #e0e8f0;background:#f9fbfd;
}
body[data-composition="task-aware"] #generated-root>[data-role="status-and-attention"]{
  padding:11px 14px;border-left-width:3px;
}
body[data-composition="task-aware"] #generated-root>[data-role="status-and-attention"] h2{margin-bottom:3px}
body[data-composition="task-aware"] #generated-root>[data-role="status-and-attention"] .state-summary{
  font-size:13px;line-height:1.4;margin:3px 0;font-weight:700;
}
body[data-composition="task-aware"] #generated-root>[data-role="status-and-attention"] .state-evidence{font-size:11px;margin:0;color:#61778b}
body[data-composition="task-aware"] #generated-root>[data-role="status-and-attention"] .status-grid{display:none!important}
body[data-composition="task-aware"] .record .record-entity{display:none}
body[data-composition="task-aware"] [data-role="primary-content"] .record-list{align-content:start}
body[data-composition="task-aware"] [data-role="persistent-nonblocking-feedback"][data-state-mode="prominent"]{
  border-left:4px solid #b8604a;background:#fff7f4;
}
body[data-composition="task-aware"] .workspace-composite .record:focus-visible{outline:3px solid #347cb0;outline-offset:2px}
@media(max-width:600px){body[data-composition="task-aware"] .workspace-composite{padding:8px;gap:8px}
 body[data-composition="task-aware"] .workspace-composite>[data-role="selection-details"]{padding:10px}}
"""
    if design_critic_strategy in ('B','C'):
        css += '''
/* Experimental generic visual presentation treatments, never app-specific CSS. */
body[data-design-critic="B"] #generated-root>[data-role="status-and-attention"]{align-self:start}
body[data-design-critic="B"] .workspace-composite>.region{align-self:start}
body[data-design-critic="B"] #generated-root>[data-role="supporting-tools"]{align-self:start}
body[data-design-critic="B"] .state-details{font-size:11px}
/* The shared runtime chooses 1 or 2 tracks from the *actual record-list width*,
   taking the computed record font size into account. No viewport magic number. */
body[data-design-critic="C"] .workspace-composite .record-list{display:grid;grid-template-columns:minmax(0,1fr);gap:6px}
body[data-design-critic="C"] .record{padding:7px}
@media(max-width:600px){body[data-design-critic="B"] [data-role="status-and-attention"]{padding:8px 10px!important}}
'''
    if presentation:
        css += """
body[data-presentation-mode="planned"]{background:#f5f7fb;color:#1b3044}
body[data-presentation-mode="planned"] header{margin-bottom:12px}
body[data-presentation-mode="planned"] header h1{font-size:22px;letter-spacing:-.45px}
body[data-presentation-mode="planned"] .region{padding:14px;border-radius:11px;box-shadow:0 1px 4px #22384f0d}
body[data-presentation-mode="planned"] .region h2{margin:0 0 9px;font-size:11px;letter-spacing:.8px}
body[data-presentation-mode="planned"] [data-role="status-and-attention"]{background:#fff;color:#183047;border-left:4px solid #3978b5;border-color:#d8e2ea;border-left-color:#3978b5}
body[data-presentation-mode="planned"] [data-role="status-and-attention"] h2{color:#49637c}
body[data-presentation-mode="planned"] [data-role="status-and-attention"] .status-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
body[data-presentation-mode="planned"] .status-cell{border:1px solid #e0e8f0;padding:8px 10px;border-radius:8px;min-width:0}
body[data-presentation-mode="planned"] .status-cell h3{font-size:12px;margin:0 0 3px;color:#253e57;font-weight:650}
body[data-presentation-mode="planned"] .status-cell .status-evidence{display:block;font-size:11px;color:#7a8999}
body[data-presentation-mode="planned"] .status-cell>[data-mcel-element-id]{display:none!important}
body[data-presentation-mode="planned"] [data-role="global-context-and-search"] .generated-panel:not(:has(.search)){display:none}
body[data-presentation-mode="planned"] [data-role="global-context-and-search"] .generated-panel{border:0;padding:0}
body[data-presentation-mode="planned"] [data-role="global-context-and-search"] .generated-panel h3{display:none}
body[data-presentation-mode="planned"] [data-role="primary-content"] .generated-panel.record{padding:12px;border:1px solid #e1e8f0;border-radius:8px;background:#fff;box-shadow:none}
body[data-presentation-mode="planned"] [data-role="primary-content"] .record[aria-current="true"]{border-color:#438bcb;background:#edf5ff}
body[data-presentation-mode="planned"] .record .title{font-size:13px;line-height:1.25}
body[data-presentation-mode="planned"] .record .record-entity{display:block;margin:3px 0 0;color:#8392a1;font-size:10px}
body[data-presentation-mode="planned"] .record .facts{gap:4px 10px;font-size:11px;margin-top:5px}
body[data-presentation-mode="planned"] .record .semantic-record-field{font-size:11px;color:#5a6f83}
body[data-presentation-mode="planned"] .record [data-deferred-field="true"]{display:none!important}
body[data-presentation-mode="planned"] .record [data-identity-field="true"]{display:none!important}
body[data-presentation-mode="planned"] .record [data-inspect-action="true"]{display:none!important}
body[data-presentation-mode="planned"] [data-role="selection-details"]>.generated-panel{display:none}
body[data-presentation-mode="planned"] [data-role="selection-details"] .inspector-details h3{font-size:14px;margin:3px 0 10px}
body[data-presentation-mode="planned"] [data-role="selection-details"] .inspector-details dl{display:grid;grid-template-columns:minmax(65px,1fr) minmax(0,1.5fr);gap:6px 10px;margin:0}
body[data-presentation-mode="planned"] [data-role="selection-details"] .inspector-details dt{font-size:11px}
body[data-presentation-mode="planned"] [data-role="selection-details"] .inspector-details dd{font-size:12px;overflow-wrap:anywhere}
body[data-presentation-mode="planned"] [data-role="persistent-nonblocking-feedback"]>.generated-panel{display:none}
body[data-presentation-mode="planned"] .feedback-summary{font-size:12px;line-height:1.5;color:#597086}
body[data-presentation-mode="planned"] [data-role="supporting-tools"]{padding-top:11px;padding-bottom:11px}
body[data-presentation-mode="planned"] [data-role="supporting-tools"] .advanced-tools summary{font-size:12px;padding:2px 0}
body[data-presentation-mode="planned"] [data-role="primary-content"]{min-height:180px}
body[data-presentation-mode="planned"] [data-role="primary-content"] .tabs{position:relative}
body[data-presentation-mode="planned"] [data-role="primary-content"] .tabs [role="tab"]{display:inline-flex;align-items:center;cursor:pointer;border-radius:6px;font-size:12px;padding:7px 10px;min-height:34px;white-space:nowrap;color:#45627d}
body[data-presentation-mode="planned"] [data-role="primary-content"] .tabs [role="tab"][aria-selected="true"]{background:#deedfd;color:#124c83;font-weight:750}
body[data-presentation-mode="planned"] [data-role="primary-content"] .tabs [role="tab"]:focus-visible{outline:2px solid #257cb8;outline-offset:2px}
@media(min-width:601px){body[data-presentation-mode="planned"] .record-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(245px,100%),1fr));gap:8px}
body[data-presentation-mode="planned"] .record-list .record{margin:0}}
@media(max-width:600px){body[data-presentation-mode="planned"] .region{padding:11px}
body[data-presentation-mode="planned"] .status-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
body[data-presentation-mode="planned"] header h1{font-size:19px}
body[data-presentation-mode="planned"] .record-list{display:block}
body[data-presentation-mode="planned"] .record{margin-bottom:7px}}
"""
    if presentation:
        css += """
/* State-aware visibility is computed by the generic planner, not by a domain label. */
#generated-root .region[hidden]{display:none!important}
body[data-presentation-mode="planned"] [data-role="status-and-attention"] .status-grid{display:none}
body[data-presentation-mode="planned"] .state-summary{font-size:13px;font-weight:650;margin:2px 0 5px}
body[data-presentation-mode="planned"] .state-evidence{font-size:11px;color:#607b95}
body[data-presentation-mode="planned"] .state-details{font-size:11px;margin-top:6px}
body[data-presentation-mode="planned"] .state-details summary{cursor:pointer;min-height:24px}
body[data-presentation-mode="planned"] .state-details ul{padding-left:18px;margin:5px 0}
body[data-presentation-mode="planned"] .region[data-state-mode="prominent"]{border-left:4px solid #b44b3f;background:#fff7f4}
body[data-presentation-mode="planned"] [data-role="persistent-nonblocking-feedback"][data-state-mode="normal"]{background:#f0f7ff;border-color:#bbdafa}
body[data-presentation-mode="planned"] [data-role="primary-content"] .record-list{align-content:start}
body[data-presentation-mode="planned"] [data-role="global-context-and-search"]{padding:8px 12px}
body[data-presentation-mode="planned"] [data-role="global-context-and-search"] h2{display:none}
body[data-presentation-mode="planned"] [data-role="global-context-and-search"] .search{padding:7px 10px}
"""
    mode='planned' if presentation else 'baseline'
    page='<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MCEL generated semantic application</title><style>'+css+'</style><body data-presentation-mode="'+mode+'" data-composition="'+('task-aware' if composition else 'none')+'" data-design-critic="'+(design_critic_strategy or 'A')+'"><header><div><h1>'+_escape(design['application']['name'])+'</h1><span class="muted">Generated by MCEL semantic components · read-only fixture</span></div><span class="fixture-banner">SYNTHETIC / NOT LIVE</span></header><main id="generated-root" role="main"></main><script>window.MCEL_GENERATED_MANIFEST='+payload+';</script><script>'+resolver+'</script><script>'+src+'</script></body></html>'
    (out/'preview.html').write_text(page,encoding='utf-8')
    return manifest


def _escape(text):
    import html
    return html.escape(str(text),quote=True)


def registry_counts(node):
    result=Counter([node['elementId']])
    for child in node.get('children',[]): result.update(registry_counts(child))
    return result


def browser_verify(out, design, manifest, chromium_executable=None):
    """Capture full-page visual evidence plus independent browser interaction checks.

    Semantic and older machine Frobs share the same evidence/gallery authority.
    Synthetic stress states never certify live data producers or visual aesthetics.
    """
    from tools.mcel_frob_visual_evidence import capture, write_gallery
    from tools.mcel_browser_launch import launch_chromium
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {'status': 'UNVERIFIED', 'reason': 'Playwright unavailable: '+str(exc)}
    result = {}
    captures = []
    source = (out/'preview.html').read_text(encoding='utf-8')
    views = next((region.get('views',[]) for region in design['hierarchy']['children']
                  if region['role']=='primary-content'), [])
    view_ids = [view['id'] for view in views]
    def snap(page, *, width, view, state, label, filename=None):
        capture_entry = capture(page,out,viewport=width,view=view,state=state,
                                label=label,filename=filename)
        captures.append(capture_entry)
        return capture_entry
    try:
        with sync_playwright() as pw:
            browser,browser_launch = launch_chromium(pw,executable=chromium_executable,headless=True,
                                                      args=['--no-sandbox','--disable-dev-shm-usage'])
            browser_version=browser.version
            try:
                for width in (320,390,768,1440,1920):
                    page = browser.new_page(viewport={'width':width,'height':900},device_scale_factor=1)
                    failures = []
                    page.on('pageerror',lambda error: failures.append(str(error)))
                    try:
                        page.set_content(source,wait_until='load',timeout=15000)
                        page.wait_for_function('window.__MCEL_GENERATED_READY===true',timeout=10000)
                        evidence=page.evaluate('''() => {const root=document.querySelector('#generated-root');
const nodes=[...root.children].filter(x=>!x.hidden&&getComputedStyle(x).display!=="none").map(x=>({id:x.id,x:x.getBoundingClientRect().x,y:x.getBoundingClientRect().y}));
const first=[...nodes].sort((a,b)=>a.y-b.y||a.x-b.x)[0];
return {layout:window.__MCEL_LAYOUT_PROOF,columns:getComputedStyle(root).gridTemplateColumns.split(' ').filter(Boolean).length,
priorityUpperLeft:first.id===window.MCEL_GENERATED_MANIFEST.regions.find(r=>r.role==='status-and-attention').id,
overflow:document.documentElement.scrollWidth>innerWidth+1,
componentCount:document.querySelectorAll('[data-mcel-element-id]').length,
rowCount:document.querySelectorAll('.record').length,
selectedView:document.querySelector('[role="tab"][aria-selected="true"]')?.dataset.view,
fixtureBanner:document.querySelector('.fixture-banner')?.textContent};}''')
                        baseline=snap(page,width=width,view=evidence['selectedView'] or 'default',state='default',
                                      label=f'{width}px · generated application · default',
                                      filename=f'preview-{width}.png')
                        evidence['screenshot']=baseline['screenshot']
                        evidence['screenshot_sha256']=baseline['screenshot_sha256']
                        if manifest.get('compositionPlan'):
                            composed=page.evaluate('''() => {
                              const p=window.__MCEL_COMPOSITION_PROOF;
                              const group=document.querySelector('#mcel-workspace-composite');
                              const role=r=>document.querySelector('[data-role="'+r+'"]');
                              return {proof:p,groupCount:document.querySelectorAll('#mcel-workspace-composite').length,
                                searchInWorkspace:group?.contains(role('global-context-and-search')),
                                collectionInWorkspace:group?.contains(role('primary-content')),
                                inspectorInWorkspace:group?.contains(role('selection-details')),
                                statusOutsideWorkspace:!group?.contains(role('status-and-attention')),
                                defaultInspectorHidden:role('selection-details')?.hidden,
                                mounted:document.querySelectorAll('[data-mcel-element-id]').length,
                                label:role('primary-content')?.querySelector('h2')?.textContent};
                            }''')
                            evidence['composition']=composed
                        evidence['full_page']=True
                        evidence['pixel_height']=baseline['pixel_height']
                        # Work with genuine runtime tabs, search, selection and details.
                        tabs=page.locator('[role="tab"]')
                        if tabs.count()>=2:
                            tabs.nth(1).click()
                            evidence['tab_switch']=tabs.nth(1).get_attribute('aria-selected')=='true'
                        elif tabs.count()==1:
                            evidence['tab_switch']=tabs.first.get_attribute('aria-selected')=='true'
                        else:
                            evidence['tab_switch']=False
                        row=page.locator('.record:visible').first
                        if row.count():
                            row.click()
                            evidence['inspector_selection']=bool(page.locator('#selected-entity').inner_text().strip())
                            if manifest.get('compositionPlan'):
                                evidence['inspect_task']=page.evaluate('window.__MCEL_COMPOSITION_PROOF.task')
                        else:
                            evidence['inspector_selection']=False
                        search=page.locator('#global-search')
                        search.fill('unlikely-search-string-101010')
                        evidence['search_filters']=page.locator('.record:visible').count()==0
                        if manifest.get('compositionPlan'):
                            evidence['search_task']=page.evaluate('window.__MCEL_COMPOSITION_PROOF.task')
                        search.fill('')
                        evidence['destructive_disabled']=page.locator('button[data-consequential]').count()>0 and all(
                            page.locator('button[data-consequential]').nth(i).is_disabled()
                            for i in range(page.locator('button[data-consequential]').count()))
                        evidence['advanced_collapsed']=not page.locator('details.advanced-tools').is_visible() or not page.locator('details.advanced-tools').get_attribute('open')

                        # All authored application views at useful review widths.
                        if width in (390,1440):
                            for view in view_ids:
                                tab=page.locator('[role="tab"][data-view="'+view+'"]')
                                if tab.count()!=1:raise ValueError('Missing generated tab: '+view)
                                tab.click()
                                if tab.get_attribute('aria-selected')!='true':
                                    raise ValueError('Generated view did not activate: '+view)
                                snap(page,width=width,view=view,state='view',
                                     label=f'{width}px · {view} view')
                            process=next((view for view in view_ids if view=='processes'),view_ids[0])
                            page.locator('[role="tab"][data-view="'+process+'"]').click()
                            visible=page.locator('.record:visible').first
                            if visible.count():
                                visible.click()
                                snap(page,width=width,view=process,state='inspector_selected',
                                     label=f'{width}px · {process} · inspected item')
                            search.fill('unlikely-search-string-101010')
                            if page.locator('.record:visible').count():
                                raise ValueError('Search empty visual state failed')
                            snap(page,width=width,view=process,state='search_empty',
                                 label=f'{width}px · {process} · search with no results')
                            search.fill('')
                            details=page.locator('details.advanced-tools')
                            if details.count():
                                details.first.evaluate('(el)=>{el.open=true}')
                                snap(page,width=width,view=process,state='advanced_open',
                                     label=f'{width}px · {process} · optional tools expanded')
                                details.first.evaluate('(el)=>{el.open=false}')
                            state_cases=[]
                            for kind in ('crowded','long_text','idle','unavailable','operation_pending','operation_failure','operation_verified_failure','unknown','empty'):
                                value=page.evaluate('(kind)=>window.__MCEL_SET_VISUAL_FIXTURE(kind)',kind)
                                if value.get('state')!=kind or not value.get('synthetic'):
                                    raise ValueError('Visual stress fixture did not apply: '+kind)
                                if manifest.get('statePresets'):
                                    checked=page.evaluate('''() => {
                                      const proof=window.__MCEL_STATE_PROOF;
                                      const regionStates=Object.entries(proof?.regions||{});
                                      const hidden=regionStates.filter(([,r])=>r.mode==='absent').map(([name])=>name);
                                      const ids=hidden.map(name=>proof.regions[name].id);
                                      const root=document.getElementById('generated-root');
                                      const css=getComputedStyle(root).gridTemplateAreas;
                                      const failed=hidden.filter(name=>{
                                        const el=root.querySelector('[data-role="'+name+'"]');
                                        return !el?.hidden || el.getBoundingClientRect().height>1 || css.includes(el.id);
                                      });
                                      return {state:proof.sourceState, operation:proof.operation,
                                        task:window.__MCEL_COMPOSITION_PROOF?.task||null,
                                        attention:proof.summary.message, hidden, failed,
                                        visibleAlerts:proof.regions['persistent-nonblocking-feedback'].mode};
                                    }''')
                                    expected={'operation_pending':'running','operation_failure':'unknown',
                                        'operation_verified_failure':'failed','idle':'none','unknown':'unknown',
                                        'empty':'none','unavailable':'none','crowded':'none','long_text':'none'}[kind]
                                    if checked['operation']['state']!=expected or checked['failed']:
                                        raise ValueError('State policy refuses inaccurate/occupied region: '+str(checked))
                                    if kind=='operation_failure' and checked['operation']['verified']:
                                        raise ValueError('Unverified failure was promoted to verified final outcome')
                                    if manifest.get('compositionPlan'):
                                        if kind=='operation_verified_failure' and checked['task']!='recover':
                                            raise ValueError('Verified failure did not prioritize recovery')
                                        if kind in ('unknown','operation_failure','operation_pending') and checked['task']!='monitor':
                                            raise ValueError('Unknown/active operation did not prioritize monitoring')
                                    if kind=='operation_verified_failure' and (not checked['operation']['verified'] or checked['visibleAlerts']!='prominent'):
                                        raise ValueError('Verified failure was hidden or demoted')
                                    if kind in ('idle','unavailable','empty') and 'persistent-nonblocking-feedback' not in checked['hidden']:
                                        raise ValueError('Inactive feedback must relinquish layout space')
                                    if kind=='unknown' and 'unknown' not in checked['attention'].lower():
                                        raise ValueError('Unreconciled outcome misrepresented')
                                    state_cases.append({'fixture':kind,**checked})
                                snap(page,width=width,view=process,state=kind,
                                     label=f'{width}px · {process} · SYNTHETIC {kind.replace("_"," ")}')
                                page.evaluate('window.__MCEL_SET_VISUAL_FIXTURE("default")')

                        if manifest.get('statePresets') and width in (390,1440):
                            evidence['state_cases']=state_cases
                        # Verify every deferred field has an actual inspectable interaction
                        # path, not merely an entry in the compressed JSON plan.
                        if manifest.get('presentationPlan'):
                            disclosure=page.evaluate('''() => {
                              const plan=window.MCEL_GENERATED_MANIFEST.presentationPlan;
                              const search=document.querySelector('#global-search');
                              if(search){search.value='';search.dispatchEvent(new Event('input',{bubbles:true}));}
                              const tabs=[...document.querySelectorAll('[role="tab"]')];
                              const rows=[...document.querySelectorAll('.record')];
                              const failures=[];
                              for(const row of rows){
                                const tab=tabs.find(t=>t.dataset.view===row.dataset.view);
                                if(tab)tab.click();
                                row.click();
                                const inspector=document.querySelector('#selected-entity');
                                const visible=inspector&&getComputedStyle(inspector).display!=='none';
                                const fields=plan.rows[row.dataset.rowType].all_fields;
                                const dts=[...inspector.querySelectorAll('dt')].map(n=>n.dataset.semanticField||n.textContent.replace(/ /g,'_'));
                                for(const f of fields)if(!dts.includes(f))failures.push(row.dataset.rowType+':'+f);
                                if(!visible)failures.push('inspector-not-visible:'+row.dataset.rowType);
                              }
                              return {checked:rows.length,failures,semanticsPreserved:plan.semantic_preservation,
                                  renderedPatterns:[...new Set(rows.map(r=>r.dataset.pattern))],
                                  visibleFieldNodes: [...document.querySelectorAll('.record .semantic-record-field')]
                                     .filter(e=>getComputedStyle(e).display!=='none').length};
                            }''')
                            evidence['presentation_disclosure']=disclosure
                            state_evidence=page.evaluate('''() => {
                                const proof=window.__MCEL_STATE_PROOF;
                                const regions=[...document.querySelectorAll('#generated-root>.region')];
                                const invisible=regions.filter(r=>r.hidden);
                                const declared=proof?.regions||{};
                                const mismatches=regions.filter(r=>r.hidden !== (declared[r.dataset.role]?.mode==='absent'));
                                return {status:proof?.status,synthetic:proof?.synthetic,
                                  hiddenIds:invisible.map(r=>r.id), mismatches:mismatches.map(r=>r.id),
                                  activeRegionAreas:getComputedStyle(document.querySelector('#generated-root')).gridTemplateAreas,
                                  suppressedOccupied:invisible.some(r=>r.getBoundingClientRect().height>1),
                                  restoredSelected:!!document.querySelector('#selected-entity')};
                            }''')
                            if manifest.get('statePresets'):evidence['state_policy']=state_evidence
                            if manifest.get('compositionPlan'):
                                evidence['task_policy']=page.evaluate('''() => ({
                                  proof:window.__MCEL_COMPOSITION_PROOF,
                                  regionIntegrity:Object.keys(window.MCEL_GENERATED_MANIFEST.compositionPlan.role_ids)
                                    .every(r=>document.querySelector('[data-role="'+r+'"]')),
                                  nestedArea:getComputedStyle(document.querySelector('#mcel-workspace-composite')).gridTemplateAreas
                                })''')
                            # Restore a predictable, user-visible process view.
                            first_view=next((v for v in view_ids if v=='processes'),view_ids[0])
                            page.locator('[role="tab"][data-view="'+first_view+'"]').click()
                        evidence['browser_errors']=failures
                        checks={'runtime_mounted':evidence['layout']['status']=='pass',
                                'all_rows_present':evidence['rowCount']==len(design['rows']['types']),
                                'registered_components':evidence['componentCount']==sum(registry_counts(manifest['componentTree']).values()),
                                'no_overflow':not evidence['overflow'],
                                'mobile_one_column':width>600 or evidence['columns']==1,
                                'layout_hints':evidence['priorityUpperLeft'],
                                'tab_switch':evidence['tab_switch'],
                                'inspector_selection':evidence['inspector_selection'],
                                'search_filters':evidence['search_filters'],
                                'destructive_disabled':evidence['destructive_disabled'],
                                'advanced_collapsed':evidence['advanced_collapsed'],
                                'no_js_errors':not failures}
                        if manifest.get('statePresets'):
                            checks['state_transitions_verified']=width not in (390,1440) or len(evidence.get('state_cases',[]))==9
                            checks['state_layout_in_sync']=(evidence['state_policy']['status']=='pass'
                                and evidence['state_policy']['synthetic'] is True
                                and not evidence['state_policy']['mismatches']
                                and not evidence['state_policy']['suppressedOccupied'])
                        if manifest.get('compositionPlan'):
                            c=evidence['composition']
                            checks['composite_structure']=(c['proof'] is not None and c['proof']['status']=='pass'
                                and c['groupCount']==1 and c['searchInWorkspace'] and c['collectionInWorkspace']
                                and c['inspectorInWorkspace'] and c['statusOutsideWorkspace']
                                and c['defaultInspectorHidden'])
                            checks['task_modes']=(evidence.get('inspect_task')=='inspect'
                                and evidence.get('search_task')=='search')
                            checks['natural_labels']=(c['label'] not in ('Items','Process Row','Stock Row'))
                            checks['task_region_integrity']=evidence['task_policy']['regionIntegrity']
                        if manifest.get('presentationPlan'):
                            checks['deferred_fields_inspectable']=not evidence['presentation_disclosure']['failures']
                            checks['presentation_semantics_preserved']=evidence['presentation_disclosure']['semanticsPreserved']
                            checks['presentation_rows_accounted_for']=evidence['presentation_disclosure']['checked']==len(design['rows']['types'])
                            checks['presentation_pattern_rendered']=evidence['presentation_disclosure']['renderedPatterns']==[
                                'compact-list' if width<=600 else 'comparison-grid']
                        result[str(width)]={'status':'PASS' if all(checks.values()) else 'FAIL',
                                            'checks':checks,'evidence':evidence}
                    finally:
                        page.close()
            finally:
                browser.close()
        provenance={'application':design['application']['id'],
                    'design_sha256':fingerprint(design),
                    'browser':'Chromium','browser_version':browser_version,'browser_launch':browser_launch,
                    'scope':'offline synthetic fixture', 'full_page':True}
        gallery=write_gallery(out,captures,title='MCEL generated application — visual review',provenance=provenance)
        return {'status':'PASS' if all(x['status']=='PASS' for x in result.values()) else 'FAIL',
                'viewports':result,
                'visual_evidence':{'status':'CAPTURED_NOT_REVIEWED','count':gallery['count'],
                                   'gallery':'gallery.html','manifest':'visual-manifest.json',
                                   'review_template':'visual-review-template.csv',
                                   'full_page':True,'states':sorted({x['state'] for x in captures}),
                                   'views':view_ids}}
    except Exception as exc:
        return {'status':'UNVERIFIED','reason':f'Chromium/visual evidence unavailable: {type(exc).__name__}: {exc}',
                'completed_viewports':result, 'captured_images':len(captures)}


def run(design_path,out,verify=False,presentation_mode="planned",design_critic_strategy=None,chromium_executable=None):
    out.mkdir(parents=True,exist_ok=True)
    design=yaml.safe_load(design_path.read_text(encoding='utf-8'))
    validate_design(design)
    if presentation_mode not in ('planned','baseline','previous','state_previous'):raise ValueError('Unknown presentation mode')
    presentation=plan_presentation(design) if presentation_mode in ('planned','previous','state_previous') else None
    composition=plan_task_composition(design,presentation) if presentation_mode=='planned' else None
    plans={width:plan(design,width) for width in (390,768,1440)}
    tree=build_component_tree(design)
    registry=serialize_components(tree,out)
    if presentation:
        validate_pattern_registry(presentation,{x['id'] for x in registry['definitions']})
        (out/'presentation-plan.json').write_text(json.dumps(presentation,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    ids=frob.regions(design)
    # Generated placements are an MCEL canonical layout-tree source, not handwritten CSS.
    layout_tree={'schema':'mcel.layout-tree.v1','root':{
        'id':'generated-root','selector':'#generated-root','role':'group','gap':12,
        'variants':[plans[1440]['selected']['layout'],plans[768]['selected']['layout'],plans[390]['selected']['layout']],
        'children':[{'id':node,'selector':'#'+node,'role':'region','verifyControls':False} for node in ids.values()]}}
    (out/'layout-tree.json').write_text(json.dumps(layout_tree,indent=2)+'\n',encoding='utf-8')
    compiled=compile_application(design,layout_tree,out)
    if not compiled['valid'] or not compiled['tree_preserved']:raise ValueError('MCEL DSL refused generated layout: '+str(compiled))
    if composition:(out/'composition-plan.json').write_text(json.dumps(composition,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    manifest=write_preview(design,registry,plans,out,presentation,state_aware=presentation_mode in ('planned','state_previous'),composition=composition,design_critic_strategy=design_critic_strategy)
    browser=browser_verify(out,design,manifest,chromium_executable=chromium_executable) if verify else {'status':'UNVERIFIED','reason':'Run --mode verify to obtain browser evidence'}
    report={'schema':'mcel.semantic-generation-probe.v1','status':browser['status'],
            'application':design['application']['id'],
            'design_sha256':fingerprint(design),'layout_hints':checked_hints(design),
            'candidate_results':{str(k):{'selected':v['selected'],'candidate_count':len(v['candidates'])} for k,v in plans.items()},
            'component_counts':registry['counts'], 'registry_definition_count':len(registry['definitions']),
            'presentation':{'mode':presentation_mode,'plan':presentation['stats'] if presentation else None,
                            'quality':'UNREVIEWED','state_policy':'mcel.presentation-state.v1' if presentation_mode in ('planned','state_previous') else None,
                            'composition':{'schema':composition['schema'],'tasks':composition['tasks'],'authoring':composition['authoring']} if composition else None},
            'dsl_compiler':compiled,'browser':browser,
            'scope':'Offline synthetic fixture, registered MCEL component contracts, canonical layout, generated read-only behavior',
            'unverified':['Live data source connectivity','Live worker or AI operation feeds','Operational authorization and effects','Human approval of appearance','Deployment/parity'],
            'artifacts':['preview.html','manifest.json','layout-tree.json','generated-application.js']+(['presentation-plan.json'] if presentation else [])+(['composition-plan.json'] if composition else []) + (['gallery.html','visual-manifest.json','visual-review-template.csv'] if browser['status']=='PASS' else [])}
    (out/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('MCEL_SEMANTIC_GENERATION_'+report['status'])
    print('registered_components:',sum(registry['counts'].values()),'row_types:',len(design['rows']['types']),'dsl_compiler:',compiled['valid'])
    print('browser:',browser['status'],'report:',out/'report.json')
    return 0 if report['status']=='PASS' or not verify and compiled['valid'] else 1


def compare(design_path,out):
    """Run real baseline and planned Frob lanes; produce independently hashed before/after PNGs."""
    from tools.mcel_frob_visual_evidence import write_gallery, verify_capture
    import copy
    baseline=out/'before';planned=out/'after'
    for mode,target in (('state_previous',baseline),('planned',planned)):
        code=run(design_path,target,verify=True,presentation_mode=mode)
        if code:return code
    comparison=out/'comparison'
    comparison.mkdir(parents=True,exist_ok=True)
    entries=[]
    metrics={}
    for mode,target in (('before',baseline),('after',planned)):
        visual=json.loads((target/'visual-manifest.json').read_text(encoding='utf-8'))
        selected=[entry for entry in visual['captures'] if
                  entry['state']=='default' or entry['state'] in ('long_text','idle','unavailable','operation_pending','operation_failure','operation_verified_failure','unknown') and entry['viewport'] in (390,1440)]
        for entry in selected:
            verify_capture(target,entry)
            item=copy.deepcopy(entry)
            src=target/item['screenshot'];name=f'{mode}-{item["screenshot"]}'
            shutil.copyfile(src,comparison/name)
            item['screenshot']=name
            item['id']=f'{mode}-{item["id"]}'
            item['label']=mode.upper()+' · '+item['label']
            item['state']=f'{mode}-{item["state"]}'
            entries.append(item)
        rep=json.loads((target/'report.json').read_text(encoding='utf-8'))
        metrics[mode]={'browser':rep['browser']['status'],
                       'row_types':len(rep['component_counts']) if not rep.get('presentation') else rep['browser']['viewports']['390']['evidence']['rowCount'],
                       'mounted_components':sum(rep['component_counts'].values()),
                       'presentation':rep.get('presentation')}
    # Group matched state and viewport, before directly next to after.
    entries.sort(key=lambda e:(e['viewport'],e['state'].replace('before-','').replace('after-',''),0 if e['id'].startswith('before-') else 1))
    manifest=write_gallery(comparison,entries,title='MCEL presentation planner — before / after',
        provenance={'design_sha256':fingerprint(yaml.safe_load(design_path.read_text(encoding='utf-8'))),
                    'scope':'Same semantic source and fixture, Chromium full-page comparisons; appearance unreviewed'})
    report={'schema':'mcel.presentation-comparison.v1','status':'CAPTURED_NOT_REVIEWED',
            'comparison_count':len(entries),'gallery':'comparison/gallery.html',
            'before_report':'before/report.json','after_report':'after/report.json',
            'metrics':metrics,'visual_quality':'UNREVIEWED',
            'controlled_change':'Previous state-aware composition versus task-aware nested presentation; unchanged semantic YAML, fixture and 153 MCEL component instances',
            'composition_evidence':{'before':None,'after':metrics['after']['presentation'].get('composition')},
            'limitations':['Human visual quality has not been approved',
                           'Records are synthetic, not live service observations',
                           'Inventory holdout verification is independent and is not automatically part of this comparison']}
    (out/'comparison-report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('MCEL_PRESENTATION_COMPARISON_CAPTURED_NOT_REVIEWED',len(entries),'full-page screenshots')
    print('gallery:',comparison/'gallery.html')
    return 0


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=['generate','verify','compare'],default='verify')
    p.add_argument('--presentation',choices=['planned','previous','baseline'],default='planned')
    p.add_argument('--design',type=Path,default=DEFAULT_DESIGN)
    p.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    a=p.parse_args(argv)
    try:
        if a.mode=='compare':
            return compare(a.design.resolve(),a.output.resolve())
        return run(a.design.resolve(),a.output.resolve(),verify=a.mode=='verify',presentation_mode=a.presentation)
    except (OSError,ValueError,KeyError,IndexError,subprocess.SubprocessError) as exc:
        print('MCEL_SEMANTIC_GENERATION_REFUSED:',str(exc),file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
