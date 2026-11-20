"""Coverage oracle v2: property-only writes, unknown external producers, bounded evidence.

No service, application mutation, or browser is needed for these adversarial tests.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_shadow_v2_properties_external_producers_and_registry_evidence(tmp_path: Path):
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    script = r'''
const fs=require('fs'),vm=require('vm');
const base=JSON.parse(process.env.MCEL_TEST_ROOT);
const files=['mcel-diagnostic-plugins.js','mcel-reactivity-shadow-plugin.js'];
const trace=[];
class MutationObserver{
 constructor(fn){this.fn=fn;this.active=false;}
 observe(){this.active=true;}
 disconnect(){this.active=false;}
 takeRecords(){return this.active?trace.splice(0):[];}
}
const doc={readyState:'complete',activeElement:null};
const window={MutationObserver,document:doc,addEventListener(){},dispatchEvent(){},
  setTimeout,clearTimeout,performance,location:{pathname:'/applications/slh'}};
const global={window,document:doc,setTimeout,clearTimeout,performance,
  CustomEvent:class CustomEvent{constructor(t,x){this.type=t;this.detail=x.detail}}};
for(const name of files){
 vm.runInNewContext(fs.readFileSync(base+'/main_computer/web/applications/scripts/'+name,'utf8'),global);
}
function element(tag){return {nodeType:1,tagName:tag,hidden:false,disabled:false,checked:false,
 readOnly:false,value:'',scrollTop:0,scrollLeft:0,children:[],
 getAttribute(){return null;},getBoundingClientRect(){return {x:0,y:0,width:100,height:30}}};}
const root=element('DIV'),button=element('BUTTON');root.children.push(button);
root.contains=e=>e===root||e===button;root.ownerDocument=doc;
button.ownerDocument=doc;
(async()=>{
 const reg=window.MCELDiagnosticPlugins,o=window.MCELReactivityShadowOracle;
 reg.configure('mcel-reactivity-shadow',{maxEvents:100,maxNodesPerConsumer:32});
 await reg.enable('mcel-reactivity-shadow');
 const state=new Map([['state:status','idle'],['state:unrelated',0]]);
 function run(kind,id,target,read,external,fn){
  o.beforeNotify(root,'slh',state,0);
  const c=o.capture(root,kind,id,target);
  c.read(read);if(external)c.readExternal(external);
  if(fn)fn();c.finish();o.afterNotify(root);
 }
 run('display','status',button,'state:status');
 state.set('state:unrelated',1);
 run('display','status',button,'state:status',null,()=>{button.disabled=true;});
 const first=o.report().apps[0];
 // Unchanged state must not prove a theme-dependent consumer skippable.
 run('color','palette',root,'state:status','theme');
 state.set('state:unrelated',2);
 run('color','palette',root,'state:status','theme');
 for(let i=3;i<133;i++){
  state.set('state:unrelated',i);
  run('display','status',button,'state:status');
 }
 const report=(await reg.run('mcel-reactivity-shadow')).result;
 const s=report.apps[0];
 const finding=reg.findings().filter(x=>x.pluginId==='mcel-reactivity-shadow' && x.code==='predicted-skip-mutated').length;
 console.log(JSON.stringify({version:report.oracleVersion,certified:report.certified,productionChanged:report.productionSchedulerChanged,
   operations:report.applicationOperationsInvoked,properties:first.stats.propertyChanges,
   contradictions:first.stats.contradictions,fullContradictions:s.stats.contradictions,
   color:s.coverage.consumerSummaries.find(x=>x.consumer==='color/palette'),
   events:s.events.length,evicted:s.eventsEvicted,executions:s.stats.executions,
   findings:finding}));
 await reg.disable('mcel-reactivity-shadow');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    file=tmp_path/'oracle_v2.cjs'
    file.write_text(script)
    result=subprocess.run(['node',str(file)],cwd=ROOT,capture_output=True,text=True,timeout=25,
        env={**os.environ,'MCEL_TEST_ROOT':json.dumps(str(ROOT))})
    assert result.returncode==0,result.stderr
    e=json.loads(result.stdout)
    assert e['version']==2
    assert e['certified'] is False and e['productionChanged'] is False and e['operations']==0
    assert e['properties']>=1 and e['contradictions']==1 and e['fullContradictions']==1
    assert e['color']['lastPrediction']=='unverified'
    assert e['events']==100 and e['evicted']>0 and e['executions']==134
    assert e['findings']>=1


def test_incumbent_runtime_observes_non_state_paths_without_replacing_scheduler():
    runtime=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text()
    oracle=(ROOT/'main_computer/web/applications/scripts/mcel-reactivity-shadow-plugin.js').read_text()
    for kind in ['generated-visibility','tab-switch','deselection-control','semantic-icons','color',
                 'generated-layout','operation-notebook','activity']:
        assert "shadowObserve('"+kind+"'" in runtime
    assert "shadowExternal('clock')" in runtime
    assert "shadowExternal('receipts')" in runtime
    assert "?.invalidate?.(root,'async-commit',shadowRevision)" in runtime
    assert 'productionSchedulerChanged:false' in oracle
    assert 'applicationOperationsInvoked:0' in oracle
    assert 'independent-candidate-renderer' in oracle
