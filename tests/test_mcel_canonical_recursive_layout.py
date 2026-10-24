"""Patch 12: one canonical layout tree from the DSL through browser execution."""
from __future__ import annotations

from main_computer.mcel_calculator_host_projection import project as project_calculator_host

import json
import os
from pathlib import Path
import shutil

import pytest

from main_computer.mcel_dsl_compiler import compile_dsl_application

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js'
CALC_HTML = ROOT / 'main_computer/web/applications/apps/calculator.html'
CALC_CSS = ROOT / 'main_computer/web/applications/styles/calculator.css'
DETECTOR = ROOT / 'main_computer/web/applications/scripts/mcel-self-diagnosis.js'


def tree(root="#app"):
    return {"schema": "mcel.layout-tree.v1", "root": {
        "id":"workspace", "selector":root, "role":"group", "maxColumns": 3, "gap": 8,
        "children": [
            {"id":"navigation", "selector":".side", "role":"region"},
            {"id":"primary", "selector":".inner", "role":"group", "maxColumns": 2, "children":[
                {"id":"tools", "selector":".tools", "role":"control-grid", "maxColumns":3, "minTargetWidth":40},
                {"id":"results", "selector":".results", "role":"region"}
            ]}
        ]}}


def dsl(tree_data):
    return '''"use strict";
const mcel = require("@mcel/app");
module.exports = mcel.defineApp({id:"layout-demo",title:"Layout Demo"},
  ({application}) => application.hostBound(app => {
    app.presentation.hostBound("workspace",{route:"/applications/layout-demo",root:"#app",presentationAuthority:"existing-host-html"});
    app.layout.zones(["main"]);
    app.layout.tree(''' + json.dumps(tree_data,ensure_ascii=True) + ''');
    app.proof.semanticRuntimeProven();
  }));
'''


def compile_source(tmp_path, value):
    source=tmp_path / 'application.js'
    source.write_text(dsl(value),encoding='utf-8')
    return compile_dsl_application(source)


def _browser():
    sync=pytest.importorskip('playwright.sync_api')
    binary=(os.environ.get('MCEL_CHROMIUM_EXECUTABLE') or os.environ.get('CHROMIUM_EXECUTABLE')
            or shutil.which('chromium') or shutil.which('chromium-browser'))
    with sync.sync_playwright() as playwright:
        kwargs={'headless':True}
        if binary: kwargs.update({'executable_path':binary,'args':['--no-sandbox','--disable-dev-shm-usage']})
        browser=playwright.chromium.launch(**kwargs)
        try: yield browser
        finally: browser.close()


def test_canonical_layout_tree_compiles_and_binds_semantics(tmp_path):
    first=compile_source(tmp_path,tree())
    assert first.valid,first.diagnostics
    layout=first.normalized_ir['layouts'][0]
    assert layout['layoutTree']==tree()
    other=tree();other['root']['children'][1]['children'][0]['maxColumns']=4
    second=compile_source(tmp_path,other)
    assert second.valid,second.diagnostics
    assert first.semantic_fingerprint!=second.semantic_fingerprint


@pytest.mark.parametrize('bad',[
    lambda t:t['root'].update({'role':'table'}),
    lambda t:t['root']['children'][0].update({'id':'primary'}),
    lambda t:t['root'].update({'maxColumns':0}),
    lambda t:t['root'].update({'gap':-2}),
    lambda t:t['root'].update({'phantom':True}),
    lambda t:t['root']['children'][0].update({'children':[{'id':'child','role':'region','selector':'.child'}]}),
])
def test_malformed_semantic_layout_tree_refuses_compile(tmp_path,bad):
    value=tree();bad(value)
    report=compile_source(tmp_path,value)
    assert not report.valid
    assert any(d['code']=='MCEL_IR_EXECUTABLE_LAYOUT_INVALID' for d in report.diagnostics),report.diagnostics


def test_recursive_unknown_three_region_browser_layout_and_rollback(tmp_path):
    ir=compile_source(tmp_path,tree()).normalized_ir
    for browser in _browser():
        page=browser.new_page()
        page.set_content('''<style>
          #app{display:grid;width:530px;grid-template-columns:80px 1fr;gap:6px;padding:8px;box-sizing:border-box}
          .inner {display:grid;grid-template-columns:1fr 1fr;gap:6px;min-width:0}
          .tools{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));min-width:0;gap:6px}
          .side,.results{min-width:0}
          button{min-width:0;min-height:36px;padding:8px;font:700 13px Arial;white-space:normal;overflow-wrap:anywhere;overflow:hidden}
          </style><div id="app"><div class="side">Navigation</div><div class="inner">
           <div class="tools"><button>longitude</button><button>latitude</button><button>render</button><button>verify</button></div>
           <div class="results">Results</div></div></div>''')
        page.add_script_tag(path=str(SCRIPT))
        result=page.evaluate('''ir => {
          const host=document.querySelector('#app');const api=window.McelSemanticLayoutInference;
          const before=host.outerHTML;
          const evaluation=api.evaluateCanonicalLayoutTree(host,ir);
          const readOnly=host.outerHTML===before;
          const selected=evaluation.selected;
          const applied=api.applyCanonicalLayoutTree(host,evaluation);
          const proof=host.dataset.mcelCanonicalLayoutProof;
          const after=host.outerHTML!==before;
          const layout=getComputedStyle(host.querySelector('.tools')).gridTemplateColumns;
          api.clearCanonicalLayoutTree(host);
          return {status:evaluation.status,choices:evaluation.candidates.length,readOnly,
           selected:!!selected,applied:applied.status,quality:applied.quality,proof,after,
           restored:host.outerHTML===before,layout,mutationAuthorized:evaluation.sourceMutationAuthorized};
        }''',ir)
        assert result['readOnly'] and result['choices']>=2,result
        assert result['selected'] and result['status']=='candidate-verified',result
        assert result['applied']=='pass' and result['proof']=='observed-only',result
        assert result['quality']['height']>0 and result['after'],result
        assert result['restored'] and result['mutationAuthorized'] is False,result


def test_real_calculator_markup_uses_recursive_ir_tree_no_html_contract_read():
    value={"schema":"mcel.layout-tree.v1","root":{
      "id":"keypad","selector":"#calculator-scientific-keypad","role":"group","maxColumns":2,
      "children":[
        {"id":"range","selector":".calculator-graph-controls","role":"region"},
        {"id":"functions","selector":".calculator-scientific-functions","role":"control-grid",
         "maxColumns":3,"minTargetWidth":40}
      ]}}
    css=CALC_CSS.read_text(encoding='utf-8')
    html=project_calculator_host(ROOT)
    for browser in _browser():
        page=browser.new_page(viewport={'width':1465,'height':882})
        page.set_content('<style>'+css+'</style>'+html)
        page.add_script_tag(path=str(SCRIPT))
        page.add_script_tag(path=str(DETECTOR))
        result=page.evaluate('''tree=>{
          const root=document.querySelector('#calculator-app');root.style.display='grid';root.style.width='1150px';
          root.querySelector('#calculator-graphing-panel').hidden=false;
          const host=root.querySelector('#calculator-scientific-keypad');host.style.width='406.6px';
          const api=window.McelSemanticLayoutInference;
          const cssBefore=document.querySelector('style').textContent;
          const before=host.style.cssText;
          const evaluation=api.evaluateCanonicalLayoutTree(host,tree);
          const applied=api.applyCanonicalLayoutTree(host,evaluation);
          const after=window.McelSelfDiagnosis._private.detectAtomicControlLabelSplits(host,{appId:'calculator'});
          const cols=getComputedStyle(host.querySelector('.calculator-scientific-functions')).gridTemplateColumns;
          api.clearCanonicalLayoutTree(host);
          return {status:applied.status,quality:applied.quality,after:after.map(x=>x.controlId),
            cols:cols.split(' ').length,restored:host.style.cssText===before,
            cssUnchanged:document.querySelector('style').textContent===cssBefore,
            available:evaluation.candidates.length};
        }''',value)
        assert result['status']=='pass' and result['after']==[],result
        assert result['cols']==3 and result['quality']['emptyCells']==0,result
        assert result['cssUnchanged'] and result['restored'],result


def test_three_sibling_regions_and_nested_grid_with_no_app_specific_rules():
    value={"schema":"mcel.layout-tree.v1","root":{
        "id":"dashboard","selector":"#dashboard","role":"group","maxColumns":3,
        "children":[
          {"id":"nav","selector":".nav","role":"region"},
          {"id":"activity","selector":".activity","role":"control-grid","maxColumns":3,"minTargetWidth":40},
          {"id":"inspector","selector":".inspector","role":"region"}
        ]}}
    for browser in _browser():
        page=browser.new_page()
        page.set_content('''<style>
          #dashboard{display:grid;width:380px;gap:8px;padding:8px;box-sizing:border-box;
            grid-template-columns:repeat(3,minmax(0,1fr))}
          .nav,.activity,.inspector{min-width:0}
          .activity{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px}
          button{min-width:0;min-height:38px;padding:8px;font:700 13px Arial;
            white-space:normal;overflow-wrap:anywhere;overflow:hidden}
          </style><div id="dashboard"><aside class="nav">Folder navigation</aside>
          <div class="activity"><button>Import</button><button>Export</button><button>Inspect</button>
          <button>Deploy</button><button>Review</button><button>Analyze</button></div>
          <div class="inspector">Detailed inspector</div></div>''')
        page.add_script_tag(path=str(SCRIPT))
        result=page.evaluate('''tree => {
          const app=document.querySelector('#dashboard'),api=window.McelSemanticLayoutInference;
          const initial=app.outerHTML;
          const e=api.evaluateCanonicalLayoutTree(app,tree);
          const originalAfterEvaluate=app.outerHTML===initial;
          const applied=api.applyCanonicalLayoutTree(app,e);
          const tracks=getComputedStyle(app).gridTemplateColumns;
          api.clearCanonicalLayoutTree(app);
          return {status:applied.status,tracks,quality:applied.quality,
            candidateCount:e.candidates.length,restored:app.outerHTML===initial,originalAfterEvaluate,
              was:initial,now:app.outerHTML};
        }''',value)
        assert result['status']=='pass' and result['restored'],result
        assert result['candidateCount']>3 and result['quality']['height']>0,result


def test_invalid_dom_ownership_and_unsatisfied_width_refuse_without_changes():
    value=tree()
    for browser in _browser():
        page=browser.new_page()
        page.set_content('''<div id="app"><div class="side"></div><div class="inner">
          <div class="tools"><button>impossiblylongatomicfunctionlabel</button></div>
          <div class="results"></div></div></div>''')
        page.add_script_tag(path=str(SCRIPT))
        report=page.evaluate('''tree=>{
          const el=document.querySelector('#app'),api=window.McelSemanticLayoutInference;
          el.style.width='85px';const before=el.outerHTML;
          const evaluated=api.evaluateCanonicalLayoutTree(el,tree);
          const applied=api.applyCanonicalLayoutTree(el,evaluated);
          let invalidSelectorRefused=false;
          const broken=structuredClone(tree);
          broken.root.children[1].children[1].selector='.missing';
          try {api.evaluateCanonicalLayoutTree(el,broken)} catch(e){invalidSelectorRefused=true;}
          return{status:evaluated.status,applied:applied.status,
            unchanged:el.outerHTML===before,invalidSelectorRefused};
        }''',value)
        assert report['status']=='unsatisfied' and report['applied']=='refused',report
        assert report['unchanged'] and report['invalidSelectorRefused'],report


def test_canonical_layout_mount_responds_to_resize_without_source_rewrite():
    canonical={"schema":"mcel.layout-tree.v1","root":{
       "id":"root","role":"group","selector":"#adaptive","maxColumns":2,
       "children":[
         {"id":"sidebar","role":"region","selector":".left"},
         {"id":"actions","role":"control-grid","selector":".right","maxColumns":3,"minTargetWidth":40}
       ]}}
    for browser in _browser():
        page=browser.new_page()
        page.set_content('''<style>#adaptive{display:grid;width:400px;grid-template-columns:1fr 1fr;gap:6px}
         .right{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:4px;min-width:0}
         button{min-width:0;min-height:40px;padding:7px;font:700 13px Arial;
           overflow:hidden;overflow-wrap:anywhere;white-space:normal}</style>
         <div id="adaptive"><div class="left">History</div><div class="right">
           <button>Compute</button><button>Verify</button><button>Inspect</button>
           <button>Export</button><button>Import</button><button>Review</button></div></div>''')
        page.add_script_tag(path=str(SCRIPT))
        initial=page.evaluate('''tree=>{
           const host=document.querySelector('#adaptive');
           return McelSemanticLayoutInference.mountCanonicalLayoutTree(host,tree);
        }''',canonical)
        assert initial['status']=='pass',initial
        page.evaluate("document.querySelector('#adaptive').style.width='700px'")
        page.wait_for_timeout(180)
        result=page.evaluate('''()=>{
          const host=document.querySelector('#adaptive'),engine=McelSemanticLayoutInference;
          const proof=host.dataset.mcelCanonicalLayoutProof;
          const before=host.style.width;
          engine.unmountCanonicalLayoutTree(host);
          return {proof,width:host.style.width,unchanged:host.style.width===before,
            detached:!host.dataset.mcelCanonicalLayoutAuthority};
        }''')
        assert result['proof'] in ('pass','observed-only'),result
        assert result['detached'] and result['width'] == '',result
