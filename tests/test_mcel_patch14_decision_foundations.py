"""Patch 14.0: deterministic decisions and metrics do not change the live renderer."""
from __future__ import annotations
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'main_computer/web/applications/scripts/mcel-semantic-layout-inference.js'
CONTINUITY = ROOT / 'main_computer/web/applications/scripts/mcel-layout-continuity.js'


def node(js: str):
    result = subprocess.run(['node', '-e', js, str(CONTINUITY), str(ENGINE)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_pure_selector_canonical_naming_and_replay():
    result = node(r'''
const assert=require('node:assert/strict'); const api=require(process.argv[1]);
const name=api.stableName({concept:'mc.process',purpose:'monitor',family:'collection',region:'workspace'});
const make=(mode)=>api.makeDecision({name,family:'collection',mode,fields:[{property:'mc.process.pid'}],regions:[{id:'workspace'}]});
const a=make('wide'),b=make('compact');
const ca={decision:a,quality:4.01},cb={decision:b,quality:4};
let initial=api.choose({candidates:[ca,cb]});
let events=[];for(let i=0;i<60;i++) events.push({observation:{revision:i},candidates:[
 {decision:a,quality:4.01+(i%2)/1000}, {decision:b,quality:4}]});
const p={epsilon:15,version:api.POLICY_VERSION};
let first=api.replay(events, {schema:api.STATE_SCHEMA,policy:api.POLICY_VERSION,decision:a},p);
let second=api.replay(events, {schema:api.STATE_SCHEMA,policy:api.POLICY_VERSION,decision:a},p);
assert.equal(first.serialized, second.serialized);
assert.equal(first.history.filter(x=>x.record.change==='retained').length,60);
const keys=api.decisionKey(a);
assert.ok(!keys.includes('allocated_px'));
assert.ok(!keys.includes('100px'));
assert.notEqual(api.decisionKey(a),api.decisionKey(b));
assert.ok(name.includes('mc.process'));
const hashOrder=api.canonical({z:1,a:2})===api.canonical({a:2,z:1});
assert.equal(hashOrder,true);
assert.equal(api.compareCodeUnits('a','b'),-1);
const incomplete=api.choose({candidates:[cb],incumbent:first.state,policy:{searchTruncated:true}});
assert.equal(incomplete.status,'incomplete');
const refuse=api.choose({candidates:[{...cb,failures:['overflow']}],incumbent:first.state});
assert.equal(refuse.status,'unsatisfied');
const different=api.choose({candidates:[ca,{...cb,quality:1}],incumbent:first.state,policy:p});
assert.equal(different.decision.mode,'compact');
assert.equal(different.record.reason,'material-quality-improvement');
assert.throws(()=>api.makeDecision({name:'n',family:'collection',mode:'m',fields:[{x:NaN}]}), /non-finite/);
assert.throws(()=>api.choose({candidates:[ca,ca]}),/duplicate/);
console.log(JSON.stringify({name,rounds:first.history.length,retained:60,canonical:hashOrder,
  incomplete:incomplete.status,refused:refuse.status,changed:different.decision.mode}));
''')
    assert result['rounds'] == 60 and result['retained'] == 60
    assert result['changed'] == 'compact'


def test_engine_exports_separated_projections_and_locale_independent_tiebreak():
    result = node(r'''
const assert=require('node:assert/strict'); const fs=require('node:fs');const vm=require('node:vm');
const script=fs.readFileSync(process.argv[2],'utf8'),context={};vm.runInNewContext(script,context);
const api=context.McelSemanticLayoutInference;
const contract={field_contracts:[{key:'pid',semanticPropertyId:'mc.process.pid'},{key:'rss',semanticPropertyId:'mc.process.rss'}],
 layout_link:{semantic_layout_id:'mcel.layout/process/monitor'}};
const ev={status:'candidate-verified',host_width_px:560,plan:{mode:'table',visible_keys:['pid'],disclosed_keys:['rss'],
 available_px:500,intrinsic_px:140,deficit_px:0,allocated_px:{pid:100},content_minimum_px:{pid:70,rss:70},
 recovered_room_available_px:360}};
const a=api.separateSemanticCollectionLayout(ev,contract);
const b=api.separateSemanticCollectionLayout({...ev,plan:{...ev.plan,allocated_px:{pid:145},
 content_minimum_px:{pid:140,rss:120}}},contract);
assert.deepEqual(JSON.parse(JSON.stringify(a.decision)),JSON.parse(JSON.stringify(b.decision)));
assert.notDeepEqual(JSON.parse(JSON.stringify(a.metrics)),JSON.parse(JSON.stringify(b.metrics)));
assert.equal(a.decision.portable,true);
const renamed=api.separateSemanticCollectionLayout(
  {...ev,plan:{...ev.plan,visible_keys:['process_id'],disclosed_keys:['memory']}},
  {field_contracts:[{key:'process_id',semanticPropertyId:'mc.process.pid'},
                    {key:'memory',semanticPropertyId:'mc.process.rss'}],layout_link:{semantic_layout_id:'mcel.layout/process/monitor'}});
assert.deepEqual(JSON.parse(JSON.stringify(a.decision)),JSON.parse(JSON.stringify(renamed.decision)));
assert.notDeepEqual(JSON.parse(JSON.stringify(a.metrics.sourceBindings)),
  JSON.parse(JSON.stringify(renamed.metrics.sourceBindings)));
const noRef=api.separateSemanticCollectionLayout(ev,{field_contracts:[{key:'pid'}],layout_link:{}});
assert.equal(noRef.decision.portable,false);assert.equal(noRef.decision.name,null);
const tree={root:{id:'workspace',role:'group',children:[{id:'task',role:'region',children:[]}]}};
const winner={choices:[{columns:1,template:'repeat(1,1fr)',gap:8},{columns:0,template:'',gap:0}],
 evidence:{quality:{height:120,emptyCells:0,gridRows:1},failures:[],records:[]}};
const split=api.separateCanonicalTreeLayout(tree,winner);
assert.equal(split.decision.name,'mcel.layout.tree/workspace');
assert.ok(!JSON.stringify(split.decision).includes('height'));
assert.equal(split.metrics.quality.height,120);
assert.equal(script.includes('JSON.stringify(a.choices).localeCompare'),false);
console.log(JSON.stringify({sameDecision:true,metricChanged:true,portable:a.decision.portable,tree:split.decision.name}));
''')
    assert result['sameDecision'] and result['metricChanged']


def test_no_production_continuity_policy_activation():
    source = ENGINE.read_text(encoding='utf-8')
    pure = CONTINUITY.read_text(encoding='utf-8')
    assert 'McelLayoutContinuity.choose(' not in source
    assert 'function applySemanticCollectionLayout' in source
    assert 'function applyCanonicalLayoutTree' in source
    assert 'requestAnimationFrame' not in pure and 'document.' not in pure
    assert 'localeCompare' not in pure


def test_golden_browser_layout_unchanged():
    from tools.mcel_representation_layout_contract import page_for_layout, project_layout
    from tools.mcel_text_shrinker_adapter import load_flat_file
    from playwright.sync_api import sync_playwright
    from tools.mcel_browser_launch import launch_chromium
    fixture = load_flat_file(ROOT / 'tools/fixtures/mcel_text_shrinker_widgets.json')
    golden = {case['width']: case for case in json.loads(
      (ROOT / 'tools/fixtures/mcel_patch14_0_legacy_golden.json').read_text(encoding='utf-8'))}
    assert all(case['identical'] for case in golden.values())
    with sync_playwright() as pw:
        browser,_=launch_chromium(pw,headless=True)
        try:
            for width in (320,390,560,1100):
                rendered=page_for_layout(project_layout(fixture['widgets']['downloads'],fixture['value_sets'],
                    viewport_px=width,enabled=True),'downloads','after')
                page=browser.new_page(viewport={'width':width,'height':800})
                try:
                    page.set_content(rendered,wait_until='load')
                    summary=page.evaluate('''() => {
                      const host=document.querySelector('#panel');
                      const rows=[...host.querySelectorAll('mcel-row')];
                      const api=window.McelSemanticLayoutInference;
                      const ev=api.evaluateSemanticCollectionLayout(host,window.MCEL_INFORMATION_CONTRACT,window.McelElementRegistry);
                      const split=api.separateSemanticCollectionLayout(ev,window.MCEL_INFORMATION_CONTRACT);
                      return {mode:host.dataset.layoutMode,authority:host.dataset.mcelCollectionLayoutAuthority,
                        status:ev.status,decision:split.decision,metrics:split.metrics,
                        track:rows[0]?.style.gridTemplateColumns,rows:rows.length};
                    }''')
                    assert summary['status']=='candidate-verified',summary
                    assert summary['mode'] in ('table','stacked')
                    assert summary['authority']=='mcel-semantic-layout-inference.v1'
                    assert summary['decision']['mode']==summary['mode']
                    assert summary['metrics']['host_width_px'] > 0
                    assert summary['rows'] == golden[width]['rows']
                    assert summary['mode'] == golden[width]['mode']
                finally:page.close()
        finally:browser.close()
