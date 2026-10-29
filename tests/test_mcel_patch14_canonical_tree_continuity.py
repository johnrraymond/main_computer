"""Patch 14.2: opt-in canonical-tree continuity (real renderer, not an invented app)."""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js'
PURE=ROOT/'main_computer/web/applications/scripts/mcel-layout-continuity.js'


def node(script):
    output=subprocess.run(['node','-e',script,str(PURE)],capture_output=True,text=True,cwd=ROOT,timeout=30)
    assert output.returncode==0,output.stderr
    return json.loads(output.stdout)


def test_pure_named_tree_replay_local_repair_and_hard_refusal():
    result=node(r'''
const assert=require('node:assert/strict'),api=require(process.argv[1]);
const tree={schema:'mcel.layout-tree.v1',root:{id:'workspace',role:'group',children:[
  {id:'left',role:'region'},{id:'monitor',role:'group',children:[{id:'inner',role:'region'}]}]}};
const group=(columns)=>({columns,template:'repeat('+columns+', minmax(0, 1fr))',gap:8});
const leaf={columns:0,template:'',gap:0};
const mk=(root,inner,h,fail=[])=>({choices:[group(root),leaf,group(inner),leaf],
   accepted:!fail.length,evidence:{quality:{height:h,emptyCells:0,gridRows:1},failures:fail,records:[]}});
const good=mk(2,1,100),local=mk(2,2,93),global=mk(1,2,88);
const evaluation=(candidates,selected)=>({schema:'mcel.layout-tree-evaluation.v1',candidates,selected,searchTruncated:false});
let initial=api.planTree({tree,evaluation:evaluation([good],good)});
assert.equal(initial.status,'selected');assert.equal(initial.state.decision.regions[0].id,'workspace');
let stable=api.planTree({tree,evaluation:evaluation([good,local,mk(1,2,96)],local),previous:initial.state,policy:{epsilon:8000}});
assert.equal(stable.record.action,'retain');
let force=api.planTree({tree,evaluation:evaluation([mk(2,1,140,['too-narrow']),local,global],global),
    previous:initial.state,policy:{epsilon:8000}});
assert.equal(force.record.action,'commit');
assert.deepEqual(force.record.changedRegions,['monitor']);
assert.equal(force.state.choices[0].columns,2);
const again=api.planTree({tree,evaluation:evaluation([mk(2,1,140,['too-narrow']),local,global],global),
    previous:initial.state,policy:{epsilon:8000}});
assert.equal(api.canonical(force),api.canonical(again));
const bad=api.planTree({tree,evaluation:evaluation([mk(2,1,140,['too-narrow'])],null),previous:initial.state});
assert.equal(bad.status,'unsatisfied');assert.equal(api.canonical(bad.state),api.canonical(initial.state));
const other={...tree,root:{...tree.root,id:'new-root'}};
const reset=api.planTree({tree:other,evaluation:evaluation([good],good),previous:initial.state});
assert.equal(reset.record.reason,'existing-canonical-baseline');
assert.ok(!api.decisionKey(initial.decision).includes('height'));
const badState=JSON.parse(JSON.stringify(initial.state));badState.choices[0].columns=9;
assert.throws(()=>api.planTree({tree,evaluation:evaluation([good],good),previous:badState}),
  /state and choices disagree/);
const edge=mk(1,2,70);edge.evidence.quality.minControlSlackPx=2;
const margin=api.planTree({tree,evaluation:evaluation([good,edge],edge),previous:initial.state,
  policy:{epsilon:0,entryMarginPx:8}});
assert.equal(margin.record.action,'retain');
console.log(JSON.stringify({deterministic:true,localRepair:force.record.changedRegions,
 stable:stable.record.action,blocked:bad.status,regionCount:initial.decision.regions.length}));
''')
    assert result['deterministic'] and result['localRepair']==['monitor']


def test_pure_replay_large_volume_no_unnecessary_tree_switch():
    result=node(r'''
const assert=require('node:assert/strict'),api=require(process.argv[1]);
const tree={schema:'mcel.layout-tree.v1',root:{id:'workspace',role:'group',children:[{id:'content',role:'region'}]}};
const mk=(n,height)=>({choices:[{columns:n,template:'repeat('+n+', minmax(0, 1fr))',gap:8},
  {columns:0,template:'',gap:0}],evidence:{quality:{height,emptyCells:0,gridRows:1},failures:[]},accepted:true});
const a=mk(2,100),b=mk(1,99);let previous=null,changed=0;
for(let i=0;i<1000;i++){
 const candidates=[mk(2,100+(i%9)/100),mk(1,99+(i%7)/100)];
 const chosen=api.planTree({tree,evaluation:{schema:'mcel.layout-tree-evaluation.v1',
  candidates,selected:candidates[1],searchTruncated:false},previous});
 if(previous && chosen.record.action!=='retain')changed++;
 previous=JSON.parse(JSON.stringify(chosen.state));
}
assert.equal(changed,0);
console.log(JSON.stringify({rounds:1000,structuralSwitches:changed,commit:previous.commit}));
''')
    assert result['rounds']==1000 and result['structuralSwitches']==0


def test_chromium_real_canonical_layout_continuity_and_restore():
    from playwright.sync_api import sync_playwright
    from tools.mcel_browser_launch import launch_chromium
    tree={"schema":"mcel.layout-tree.v1","root":{
        "id":"workspace","selector":"#workspace","role":"group","maxColumns":3,"gap":8,
        "children":[
            {"id":"navigation","selector":".navigation","role":"region","minTargetWidth":90},
            {"id":"workbench","selector":".workbench","role":"region","minTargetWidth":90},
            {"id":"inspector","selector":".inspector","role":"region","minTargetWidth":90}
        ]}}
    with sync_playwright() as pw:
        browser,_=launch_chromium(pw,headless=True)
        try:
            page=browser.new_page(viewport={'width':1150,'height':820})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.set_content('''<style>
            #workspace {width:760px;display:grid;gap:8px;padding:0;box-sizing:border-box;}
            .navigation,.workbench,.inspector {min-width:0;box-sizing:border-box;}
            button{width:96%;min-width:0;min-height:38px;white-space:nowrap;overflow:hidden;
              padding:6px;font:14px Arial;}
            .data{overflow-wrap:anywhere;min-height:34px}
            </style><section id="workspace">
            <aside class="navigation"><button id="navbtn">Navigation</button></aside>
            <section class="workbench"><button id="workbtn">Compute</button><div class="data" id="data">one</div></section>
            <section class="inspector"><button>Inspector</button></section>
            </section>''')
            page.add_script_tag(path=str(PURE));page.add_script_tag(path=str(ENGINE))
            initial=page.evaluate('''tree=>{
              const host=document.querySelector('#workspace');
              window.__tree=tree;window.__lab=McelSemanticLayoutInference.mountCanonicalLayoutTree(
                host,tree,{continuity:'lab',observeResize:false,observeMutations:false});
              window.__node=document.querySelector('#workbtn');window.__node.focus();
              const a=window.__lab.getCommittedState();
              const bounds=()=>Object.fromEntries(['navigation','workbench','inspector'].map(id=>{
                 const r=host.querySelector('.'+id).getBoundingClientRect();return [id,[r.x,r.y,r.width]];}));
              window.__bounds=bounds;
              return {status:window.__lab.status,initial:window.__lab.initial.status,
                baseline:window.__lab.initial.evaluation.selected.choices,
                state:a,rects:bounds(),focus:document.activeElement===window.__node,
                proof:host.dataset.mcelCanonicalLayoutProof,markup:host.outerHTML};
            }''',tree)
            assert initial['status']=='pass' and initial['initial']=='pass',initial
            assert initial['state']['decision']['name']=='mcel.layout.tree/workspace'
            assert initial['state']['choices']==initial['baseline']
            # Content changes only; the committed decision and placements do not.
            unchanged=page.evaluate('''()=>{
              document.querySelector('#data').textContent='medium-length value updated';
              const receipt=window.__lab.refresh();
              const host=document.querySelector('#workspace');
              return {status:receipt.status,decision:receipt.continuity,
                 rects:window.__bounds(),state:window.__lab.getCommittedState(),
                 focus:document.activeElement===window.__node,
                 sameNode:window.__node===document.querySelector('#workbtn'),
                 proof:host.dataset.mcelCanonicalLayoutProof};
            }''')
            assert unchanged['status']=='pass',unchanged
            assert unchanged['decision']['action']=='retain',unchanged
            assert unchanged['focus'] and unchanged['sameNode'],unchanged
            assert initial['state']['decision']==unchanged['state']['decision']
            def displacement(x,y):
                return max(abs(x[k][i]-y[k][i]) for k in x for i in (0,1))
            assert displacement(initial['rects'],unchanged['rects'])<=0.25,(initial,unchanged)
            # Force a real feasibility boundary by shrinking the host.
            shrink=page.evaluate('''()=>{
              const host=document.querySelector('#workspace');host.style.width='265px';
              const receipt=window.__lab.refresh();
              return {status:receipt.status,decision:receipt.continuity,
               state:window.__lab.getCommittedState(),rects:window.__bounds(),
               overflow:host.scrollWidth>host.clientWidth+1};
            }''')
            assert shrink['status']=='pass',shrink
            assert shrink['state']['choices'][0]['columns']<initial['state']['choices'][0]['columns'],shrink
            assert not shrink['overflow'],shrink
            previous=page.evaluate('window.__lab.getCommittedState()')
            restored=page.evaluate('''previous=>{
              const host=document.querySelector('#workspace');
              window.__lab.unmount();
              const clean=host.dataset.mcelCanonicalLayoutAuthority||null;
              window.__lab=McelSemanticLayoutInference.mountCanonicalLayoutTree(host,window.__tree,
                {continuity:'lab',observeResize:false,observeMutations:false,
                  maxCandidates:1,previousState:previous});
              return {clean,status:window.__lab.status,state:window.__lab.getCommittedState(),
                evidence:window.__lab.initial.evaluation.candidates.map(c=>c.incumbentMeasured||false)};
            }''',previous)
            assert restored['status']=='pass' and restored['clean'] is None,restored
            assert restored['state']['decision']==previous['decision'],restored
            assert any(restored['evidence']),restored  # incumbent measured despite truncated search
            # A rejected live update may not overwrite the committed state.
            refused=page.evaluate('''()=>{
              const host=document.querySelector('#workspace');
              const prior=window.__lab.getCommittedState();
              host.querySelector('.workbench').remove();
              const receipt=window.__lab.refresh();
              return {status:receipt.status,sameState:JSON.stringify(prior)===
                 JSON.stringify(window.__lab.getCommittedState())};
            }''')
            assert refused['status']=='refused' and refused['sameState'],refused
            assert not errors,errors
            evidence={'schema':'mcel.patch14.2-tree-browser-evidence.v1','status':'PASS',
                'initialColumns':initial['state']['choices'][0]['columns'],
                'narrowColumns':shrink['state']['choices'][0]['columns'],
                'stableDecision':unchanged['decision']['action']=='retain',
                'maxUnnecessaryDisplacementPx':displacement(initial['rects'],unchanged['rects']),
                'focusPreserved':unchanged['focus'],'sameDomNode':unchanged['sameNode'],
                'maxCandidates':1,'incumbentMeasuredBeyondCap':any(restored['evidence']),
                'invalidUpdateRefusedWithoutCommit':refused['sameState'],
                'browserErrors':errors,'serverHosted':False,'productionPromotion':False}
            if p:=os.environ.get('MCEL_PATCH14_TREE_BROWSER_EVIDENCE'):
                dest=Path(p);dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_text(json.dumps(evidence,indent=2)+'\n',encoding='utf-8')
        finally:browser.close()


def test_chromium_opt_in_mutation_batches_to_a_continuity_commit():
    from playwright.sync_api import sync_playwright
    from tools.mcel_browser_launch import launch_chromium
    tree={'schema':'mcel.layout-tree.v1','root':{'id':'workspace','selector':'#workspace',
      'role':'group','maxColumns':2,'children':[
        {'id':'tools','selector':'.tools','role':'region'},
        {'id':'details','selector':'.details','role':'region'}]}}
    with sync_playwright() as pw:
        browser,_=launch_chromium(pw,headless=True)
        try:
            page=browser.new_page()
            page.set_content('''<section id="workspace" style="width:500px;display:grid">
              <div class="tools"><button id="focused">Compute</button></div>
              <div class="details">Before</div></section>''')
            page.add_script_tag(path=str(PURE));page.add_script_tag(path=str(ENGINE))
            state=page.evaluate('''tree=>{
              window.__lab=McelSemanticLayoutInference.mountCanonicalLayoutTree(
                document.querySelector('#workspace'),tree,{continuity:'lab',observeResize:false});
              document.querySelector('#focused').focus();
              return window.__lab.getCommittedState();
            }''',tree)
            assert state and state['commit']==1
            page.evaluate('''()=>{
              document.querySelector('.details').textContent='After first change';
              document.querySelector('.details').textContent='After second change';
            }''')
            page.wait_for_function('''()=>window.__lab.getCommittedState().commit>=2''',timeout=10000)
            observed=page.evaluate('''()=>({state:window.__lab.getCommittedState(),
              action:window.__lab.getLastDecision().action,
              focus:document.activeElement===document.querySelector('#focused')})''')
            assert observed['state']['decision']==state['decision']
            assert observed['action']=='retain' and observed['focus'],observed
        finally:browser.close()
