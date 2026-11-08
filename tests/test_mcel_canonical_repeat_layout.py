"""P2: generic canonical-tree repeated role, with no Chat Console specialization.

Run against the checked-in real DSL compiler and browser planner; a repeat
node is one IR template regardless of the number of runtime records.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import pytest

from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
PLANNER = ROOT / "main_computer/web/applications/scripts/mcel-semantic-layout-inference.js"


def layout(gap=8):
    return {"schema": "mcel.layout-tree.v1", "root": {
        "id": "application", "selector": "#app", "role": "group", "children": [
            {"id": "notebook", "selector": ".notebook", "role": "group", "children": [
                {"id": "cell-template", "selector": ".cell", "role": "repeat",
                 "keyAttribute": "data-cell-id", "maxColumns": 3, "gap": gap,
                 "verifyControls": False}
            ]}
        ]}}


def compile_tree(tmp_path, tree):
    source = tmp_path / "application.js"
    source.write_text('''"use strict";
const mcel=require("@mcel/app");
module.exports=mcel.defineApp({id:"repeat-demo",title:"Repeat Demo"},
 ({application})=>application.hostBound(app=>{
   app.presentation.hostBound("workspace",{route:"/applications/repeat-demo",root:"#app",presentationAuthority:"existing-host-html"});
   app.layout.zones(["main"]);
   app.layout.tree(''' + json.dumps(tree) + ''');
   app.proof.semanticRuntimeProven();
 }));
''', encoding="utf-8")
    return compile_dsl_application(source)


def browser_context():
    playwright = pytest.importorskip("playwright.sync_api")
    binary = os.environ.get("MCEL_CHROMIUM_EXECUTABLE") or os.environ.get("CHROMIUM_EXECUTABLE") or shutil.which("chromium") or shutil.which("chromium-browser")
    return playwright, binary


def browser_page():
    sync, binary = browser_context()
    with sync.sync_playwright() as p:
        opts = {"headless": True}
        if binary:
            opts.update(executable_path=binary, args=["--no-sandbox", "--disable-dev-shm-usage"])
        browser = p.chromium.launch(**opts)
        try:
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.set_content('''<style>
            #app {width: 600px; box-sizing:border-box; padding:4px}
            .notebook,.cell {min-width:0}
            .cell {border:1px solid #777; box-sizing:border-box; padding:4px}
            .cell > * {min-width:0;box-sizing:border-box}
            .cell button {min-width:40px;min-height:40px}
            </style><div id="app"><div class="notebook"></div></div>''')
            page.add_script_tag(path=str(PLANNER))
            yield page
        finally:
            browser.close()


def test_repeat_schema_compiles_as_single_semantic_template(tmp_path):
    data = layout()
    report = compile_tree(tmp_path, data)
    assert report.valid, report.diagnostics
    assert report.normalized_ir["layouts"][0]["layoutTree"] == data
    changed = compile_tree(tmp_path, layout(gap=32))
    assert changed.valid, changed.diagnostics
    assert report.semantic_fingerprint != changed.semantic_fingerprint


@pytest.mark.parametrize("change", [
    lambda d: d["root"]["children"][0]["children"][0].pop("keyAttribute"),
    lambda d: d["root"]["children"][0]["children"][0].update(keyAttribute="id"),
    lambda d: d["root"]["children"][0]["children"][0].update(keyAttribute="data-Cell-ID"),
    lambda d: d["root"]["children"][0]["children"][0].update(children=[{"id":"nested","role":"region","selector":"div"}]),
    lambda d: d["root"]["children"][0]["children"][0].update(variants=[{"tracks":["1px"],"areas":["cell-template"]}]),
    lambda d: d["root"]["children"][0].update(variants=[{"tracks":["1px"],"areas":["cell-template"]}]),
    lambda d: d["root"].update(keyAttribute="data-cell-id"),
])
def test_invalid_repeat_contract_is_rejected_at_compile(tmp_path, change):
    data = layout()
    change(data)
    result = compile_tree(tmp_path, data)
    assert not result.valid
    assert any(d["code"] == "MCEL_IR_EXECUTABLE_LAYOUT_INVALID" for d in result.diagnostics), result.diagnostics


def test_repeated_layout_empty_to_heterogeneous_and_restore_after_rerender():
    for page in browser_page():
        result = page.evaluate('''tree => {
          const host=document.querySelector('#app');
          const notebook=host.querySelector('.notebook');
          const api=window.McelSemanticLayoutInference;
          const empty=api.mountCanonicalLayoutTree(host,tree,{observeResize:false});
          const emptyDecision=empty.initial.evaluation.decision.regions.find(x=>x.id==='cell-template');
          let clicks=0;
          function make(id,kind){
            const card=document.createElement('section'); card.className='cell';card.dataset.cellId=id;
            const label=document.createElement('label');label.textContent='Cell '+id;
            const editor=document.createElement('textarea');editor.value=kind;
            card.append(label,editor);
            if(kind==='output'){
              const output=document.createElement('pre');output.textContent='Result '+id;card.append(output);
            }
            if(kind==='actions'){
              const actions=document.createElement('button');actions.textContent='Run';
              actions.addEventListener('click',()=>clicks++);card.append(actions);
            }
            return card;
          }
          const a=make('a','actions'),b=make('b','output');
          notebook.append(a,b);
          const populated=empty.refresh();
          const generated=populated.evaluation.generatedCss.find(x=>x.id==='cell-template');
          const record=populated.evaluation.selected.evidence.records.filter(x=>x.templateId==='cell-template');
          const computed=[...notebook.children].map(x=>getComputedStyle(x).gridTemplateColumns);
          const sameDecision=computed.every(value=>value===computed[0]);
          const preserved=[...notebook.children].every((el,i)=>el===[a,b][i]);
          a.querySelector('button').click();
          notebook.prepend(b);
          const reordered=empty.refresh();
          const reorderKeys=reordered.evaluation.generatedCss.find(x=>x.id==='cell-template').instanceKeys;
          a.remove(); const c=make('c','output');notebook.append(c);
          const replaced=empty.refresh();
          const replacedKeys=replaced.evaluation.generatedCss.find(x=>x.id==='cell-template').instanceKeys;
          empty.unmount();
          const styleClear=[b,c].every(el=>!el.hasAttribute('style'));
          return {empty:empty.status,emptyColumns:emptyDecision.columns,populated:populated.status,
            populatedCount:generated.instanceCount,keys:generated.instanceKeys,recordIds:record.map(x=>x.id),
            sameDecision,preserved,clicks,reordered:reordered.status,reorderKeys,
            replaced:replaced.status,replacedKeys,styleClear,rootClear:!host.hasAttribute('style')};
        }''',layout())
        assert result["empty"] == "pass" and result["emptyColumns"] == 0, result
        assert result["populated"] == "pass", result
        assert result["populatedCount"] == 2 and result["keys"] == ["a", "b"], result
        assert result["recordIds"] == ["cell-template[a]", "cell-template[b]"], result
        assert result["sameDecision"] and result["preserved"] and result["clicks"] == 1, result
        assert result["reordered"] == "pass" and result["reorderKeys"] == ["b", "a"], result
        assert result["replaced"] == "pass" and result["replacedKeys"] == ["b", "c"], result
        assert result["styleClear"] and result["rootClear"], result


def test_repeat_layout_one_decision_for_many_instances_and_authored_perturbation():
    for page in browser_page():
        result = page.evaluate('''tree => {
          const host=document.querySelector('#app'),notebook=host.querySelector('.notebook');
          for(let i=0;i<72;i++){
            const cell=document.createElement('section'); cell.className='cell';cell.dataset.cellId=String(i);
            cell.innerHTML=i%2 ? '<div>Reasoning</div><textarea></textarea>' : '<div>Code</div><textarea></textarea><pre>Output</pre>';
            notebook.appendChild(cell);
          }
          const api=window.McelSemanticLayoutInference;
          const before=host.outerHTML;
          const planA=api.evaluateCanonicalLayoutTree(host,tree);
          const untouched=host.outerHTML===before;
          const oneTemplate=planA.decision.regions.filter(x=>x.id==='cell-template').length;
          const appliedA=api.applyCanonicalLayoutTree(host,planA);
          const first=notebook.firstElementChild;
          const gapA=parseFloat(getComputedStyle(first).gap);
          const posA=first.children[1].getBoundingClientRect().left;
          const alternate=structuredClone(tree);alternate.root.children[0].children[0].gap=32;
          const planB=api.evaluateCanonicalLayoutTree(host,alternate);
          const appliedB=api.applyCanonicalLayoutTree(host,planB);
          const gapB=parseFloat(getComputedStyle(first).gap);
          const posB=first.children[1].getBoundingClientRect().left;
          api.clearCanonicalLayoutTree(host);
          return {untouched,oneTemplate,records:planA.selected.evidence.records.filter(x=>x.templateId==='cell-template').length,
            cases:planA.candidates.length,appliedA:appliedA.status,appliedB:appliedB.status,
            gapA,gapB,posA,posB,restored:host.outerHTML===before};
        }''',layout())
        assert result["untouched"] and result["oneTemplate"] == 1, result
        assert result["records"] == 72 and result["cases"] <= 192, result
        assert result["appliedA"] == result["appliedB"] == "pass", result
        assert result["gapA"] == 8 and result["gapB"] == 32 and result["posA"] != result["posB"], result
        assert result["restored"], result


@pytest.mark.parametrize("fault", ["missing-key", "duplicate-key", "alias", "wrong-parent"])
def test_repeat_identity_or_structure_fault_refuses_without_source_mutation(fault):
    for page in browser_page():
        result=page.evaluate('''({tree,fault})=>{
          const host=document.querySelector('#app'),notebook=host.querySelector('.notebook');
          const item=document.createElement('section');item.className='cell';item.dataset.cellId='first';
          item.innerHTML='<div>hello</div><textarea></textarea>';notebook.append(item);
          const second=item.cloneNode(true);
          if(fault==='missing-key') second.removeAttribute('data-cell-id');
          if(fault==='duplicate-key') second.dataset.cellId='first';
          if(fault==='wrong-parent') {second.dataset.cellId='second';item.append(second);}
          else notebook.append(second);
          if(fault==='alias') {second.dataset.cellId='second';tree.root.children[0].children.push({id:'alias',selector:'.cell',role:'repeat',keyAttribute:'data-cell-id'});}
          const old=host.outerHTML;let outcome;
          try {outcome=window.McelSemanticLayoutInference.evaluateCanonicalLayoutTree(host,tree).status;}
          catch(error){outcome=String(error);}
          return {outcome,unchanged:host.outerHTML===old};
        }''',{"tree":layout(),"fault":fault})
        assert result["unchanged"], result
        if fault == "wrong-parent":
            # A repeat template binds direct children only. This single valid
            # direct child is permitted; the nested element stays untouched.
            assert result["outcome"] == "candidate-verified", result
        else:
            assert "repeat layout identity" in result["outcome"], result


def test_repeat_refresh_rejects_duplicate_key_and_revokes_stale_proof():
    for page in browser_page():
        result=page.evaluate('''tree=>{
          const host=document.querySelector('#app'),notebook=host.querySelector('.notebook');
          function add(key){
            const card=document.createElement('section');card.className='cell';card.dataset.cellId=key;
            card.innerHTML='<label>Editor</label><textarea></textarea>';
            notebook.appendChild(card);return card;
          }
          add('one');
          const api=window.McelSemanticLayoutInference;
          const mounted=api.mountCanonicalLayoutTree(host,tree,{observeResize:false});
          const proofBefore=host.dataset.mcelCanonicalLayoutAuthority;
          add('one');
          const bad=mounted.refresh();
          const proofAfter=host.dataset.mcelCanonicalLayoutProof;
          const authorityAfter=host.dataset.mcelCanonicalLayoutAuthority ?? null;
          notebook.lastElementChild.dataset.cellId='two';
          const recovered=mounted.refresh();
          const proofRecovered=host.dataset.mcelCanonicalLayoutAuthority;
          mounted.unmount();
          return {mounted:mounted.status,proofBefore,bad:bad.status,reason:bad.reason,
            proofAfter,authorityAfter,recovered:recovered.status,proofRecovered};
        }''',layout())
        assert result["mounted"] == "pass", result
        assert result["proofBefore"] == "mcel-layout-tree.v1", result
        assert result["bad"] == "refused" and "repeat layout identity" in result["reason"], result
        assert result["proofAfter"] == "fail" and result["authorityAfter"] is None, result
        assert result["recovered"] == "pass" and result["proofRecovered"] == "mcel-layout-tree.v1", result


def test_repeat_layout_can_refresh_at_nested_host_width_without_replacing_items():
    for page in browser_page():
        result=page.evaluate('''tree=>{
          const host=document.querySelector('#app'),notebook=host.querySelector('.notebook');
          const card=document.createElement('section');card.className='cell';card.dataset.cellId='stable';
          const editor=document.createElement('textarea');editor.value='draft unchanged';
          card.append(document.createElement('label'),editor,document.createElement('pre'));
          notebook.append(card);
          const api=window.McelSemanticLayoutInference;
          host.style.width='500px';
          const mounted=api.mountCanonicalLayoutTree(host,tree,{observeResize:false});
          const wide=mounted.initial.evaluation.generatedCss.find(x=>x.id==='cell-template');
          host.style.width='320px';
          const narrow=mounted.refresh();
          const selected=narrow.evaluation.generatedCss.find(x=>x.id==='cell-template');
          const intact=notebook.firstElementChild===card && card.children[1]===editor && editor.value==='draft unchanged';
          const measuredWidth=host.getBoundingClientRect().width;
          mounted.unmount();
          return {wide:mounted.status,narrow:narrow.status,width:measuredWidth,
            keys:selected.instanceKeys,oneTemplate:wide.id===selected.id,intact};
        }''',layout())
        assert result["wide"] == result["narrow"] == "pass", result
        assert result["width"] == 320 and result["keys"] == ["stable"], result
        assert result["oneTemplate"] and result["intact"], result
