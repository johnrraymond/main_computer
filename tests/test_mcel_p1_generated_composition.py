"""P1 generated hierarchy: source, authority, browser, and hostile controls."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import re
import json

import pytest
from main_computer.mcel_dsl_compiler import compile_dsl_application
from main_computer.mcel_knowledge.interface_intent import RELATIONS, PURPOSES
from main_computer.mcel_knowledge.semantic_composition import synthesize_composition, verify_composition
from main_computer.mcel_semantic_generated_host import compile_generated_candidate, render_generated_page, GeneratedHostRefusal
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'tools/fixtures/mcel_p1_task_monitor/application.js'
RUNTIME=ROOT/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'
DSL=ROOT/'main_computer/mcel_dsl_runtime.js'


def test_semantic_purpose_roundtrips_and_rejects_unknown(tmp_path):
    ir, composition = compile_generated_candidate()
    assert ir['surfaces'][0]['purpose']=='monitor'
    assert ir['layouts']==[] and 'interfaceIntent' not in ir['surfaces'][0]
    assert not SOURCE.with_name('application.html').exists()
    assert 'layout' not in SOURCE.read_text().lower()
    assert composition['sourceIrHash']==ir['fingerprints']['semantic']
    invalid=tmp_path/'application.js'
    invalid.write_text(SOURCE.read_text().replace('purpose:"monitor"','purpose:"invented"'))
    report=compile_dsl_application(invalid)
    assert not report.valid
    assert 'MCEL_DSL_SURFACE_PURPOSE_INVALID' in str(report.diagnostics)
    altered=deepcopy(ir);altered['surfaces'][0]['purpose']='invented'
    with pytest.raises(ValueError):synthesize_composition(altered)


def test_js_python_relationship_vocabulary_parity():
    content=DSL.read_text()
    block=re.search(r'const INTERFACE_RELATIONSHIPS = new Set\(\[(.*?)\]\);',content,re.S)
    assert block
    assert set(re.findall(r'"([a-z-]+)"',block.group(1)))==RELATIONS
    block=re.search(r'const INTERFACE_PURPOSES = new Set\(\[(.*?)\]\);',content,re.S)
    assert block
    assert set(re.findall(r'"([a-z-]+)"',block.group(1)))==PURPOSES


def test_pure_derived_composition_and_negative_controls():
    ir,plan=compile_generated_candidate()
    verify_composition(ir,plan)
    assert plan==synthesize_composition(ir)
    assert [r['recipe'] for r in plan['regions']]==['root','controls','queries','collection','selection-echo','results','evidence']
    assert not any(r['purpose']=='inspection' for r in plan['regions'])
    assert all(not r['tasks'] for r in plan['regions'])  # purpose cannot enable automatic polling
    # Names do not drive structural selection; the unique sources/effects do.
    renamed=deepcopy(ir)
    renamed['application']['appId']='something-unrelated'
    renamed['application']['title']='Unrelated title'
    # Complete alpha renaming of source node/state identifiers and their refs.
    renamed=json.loads(json.dumps(renamed).replace('semantic-task-monitor-generated','other-author').replace('process-list','generic-items').replace('processes','items'))
    other=synthesize_composition(renamed)
    assert [(r['recipe'],r['ordinal'],r['purpose']) for r in other['regions']] == [
        (r['recipe'],r['ordinal'],r['purpose']) for r in plan['regions']]
    # A changed task goal must change a meaningful organizational decision.
    changed=deepcopy(ir);changed['surfaces'][0]['purpose']='inspect'
    inspection=synthesize_composition(changed)
    assert [r['recipe'] for r in inspection['regions']] != [r['recipe'] for r in plan['regions']]
    # Deleting the verified selection relationship must not produce an inspector.
    no_select=deepcopy(ir)
    next(n for n in no_select['surfaces'][0]['nodes'] if n['nodeKind']=='collection' and n.get('selectionIntent')).pop('selectionIntent')
    no_select['intents']=[i for i in no_select['intents'] if 'collectionSelection' not in i]
    without=synthesize_composition(no_select)
    assert 'selection-echo' not in [r['recipe'] for r in without['regions']]
    # An old IR hash, an invented member or additional effects cannot be admitted.
    stale=deepcopy(plan);stale['sourceIrHash']='sha256:old'
    with pytest.raises(ValueError):verify_composition(ir,stale)
    writing=deepcopy(ir);writing['capabilities'][0]['risk']='source-write'
    with pytest.raises(ValueError):synthesize_composition(writing)
    with_layout=deepcopy(ir);with_layout['layouts']=[{'id':'layout:forged'}]
    with pytest.raises(ValueError):synthesize_composition(with_layout)


def test_refuse_html_auto_pair(tmp_path):
    import shutil
    dest=tmp_path/'tools/fixtures/mcel_p1_task_monitor'
    dest.mkdir(parents=True)
    shutil.copy2(SOURCE,dest/'application.js')
    (dest/'application.html').write_text('<main></main>')
    with pytest.raises(GeneratedHostRefusal,match='paired HTML'):
        compile_generated_candidate(tmp_path)


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b,_=launch_semantic_browser(pw)
        try:yield b
        finally:b.close()


@pytest.mark.parametrize('width',[320,390,560,1100,1272])
def test_generated_runtime_authentic_contract_and_responsive_browser(browser,width):
    page=browser.new_page(viewport={'width':width,'height':820})
    try:
        page.evaluate('''() => {
          window.__requests=[];
          window.fetch=async (url,opts)=>{
            window.__requests.push({url,body:JSON.parse(opts.body)});
            return {ok:true,status:200,json:async()=>({processes:[
              {pid:130,name:'worker',status:'running',created_at:'2026-10-31T22:00:00Z',memory_human:'9 MB'},
              {pid:131,name:'agent',status:'sleeping',created_at:'2026-10-31T22:01:00Z',memory_human:'4 MB'}]})};
          };
        }''')
        page.set_content(render_generated_page())
        assert page.locator('#mcel-workbench-status').inner_text()=='P2 generated monitor ready'
        assert page.locator('[data-mcel-region]').count()==7
        assert page.locator('[data-mcel-natural-layout]').count()==1
        assert page.locator('[data-mcel-natural-scroller="navigation"]').count()==1
        assert page.locator('[data-mcel-natural-scroller="workspace"]').count()==1
        assert page.locator('[data-mcel-purpose="workspace"] [data-collection]').count()==3
        assert page.locator('[data-mcel-purpose="inspection"]').count()==0
        assert page.locator('[data-mcel-auto-refresh]').count()==0
        assert page.evaluate('window.__requests.length')==0
        page.locator('form[data-intent="intent:refresh"] input[name="query"]').fill('worker')
        page.locator('form[data-intent="intent:refresh"] button[type=submit]').click()
        page.locator('[data-record-key="130:2026-10-31T22:00:00Z"]').wait_for()
        page.locator('[data-record-key="130:2026-10-31T22:00:00Z"] button').click()
        assert page.evaluate('window.__mcelP1.app.readState("state:selected")')=='130:2026-10-31T22:00:00Z'
        assert page.evaluate('window.__requests.length')==1
        req=page.evaluate('window.__requests[0]')
        assert req['url']=='/api/applications/task/overview' and req['body']['query']=='worker'
        assert req['body']['include_all'] is False
        assert page.locator('[data-mcel-activity] li').count()==1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+2')
        # Resize re-evaluates geometry through the shared inference engine;
        # neither the composition fingerprint nor the selected identity changes.
        if width == 1100:
            before=page.evaluate('window.__mcelP1.composition.fingerprint')
            page.set_viewport_size({'width':390,'height':820})
            page.wait_for_function('document.documentElement.scrollWidth <= innerWidth+2')
            assert page.evaluate('window.__mcelP1.composition.fingerprint')==before
            assert page.evaluate('window.__mcelP1.app.readState("state:selected")')=='130:2026-10-31T22:00:00Z'
            assert page.locator('[data-mcel-natural-layout]').count()==1
        # No authority via a forged consequential effect.
        assert page.evaluate('''async()=>McelSemanticReadPorts.create({appId:'semantic-task-monitor-generated'}).authorize({
            appId:'semantic-task-monitor-generated',intentId:'intent:terminate-process',
            effectId:'effect:terminate-process.request',capabilityId:'capability:task-control',
            operation:'terminate',runtimeMethod:'terminateObservedProcess',risk:'consequential',arguments:{selected:'130'}})''') is False
    finally:page.close()


def test_runtime_rejects_forged_generated_composition(browser):
    page=browser.new_page()
    try:
        page.set_content(render_generated_page())
        assert page.locator('#mcel-workbench-status').inner_text()=='P2 generated monitor ready'
        assert page.evaluate('''()=>{
          const data=JSON.parse(document.getElementById('mcel-generated-data').textContent);
          const root=document.createElement('div');document.body.append(root);
          data.composition.regions.find(r=>r.recipe==='collection').members=[{ref:'surface-node:unknown'}];
          try{McelSemanticOperationalRuntime.mount(root,data.ir,{composition:data.composition});return false;}
          catch(e){return !root.hasAttribute('data-mcel-semantic-operational') && !root.children.length;}
        }''')
    finally:page.close()
