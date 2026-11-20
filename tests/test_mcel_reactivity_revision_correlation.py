"""Adversarial revision-correlation tests. Attribution is never a causal proof."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_revision_correlation_parallel_pending_unknown_async_and_receipts(tmp_path):
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    script = r'''
const fs=require('fs'),vm=require('vm');
let watch=null;
class MutationObserver{
 constructor(fn){this.fn=fn;watch=this;}
 observe(){} disconnect(){} takeRecords(){return [];}
 trigger(count){this.fn(Array.from({length:count},()=>({type:'attributes'})));}
}
const doc={activeElement:null};
const w={MutationObserver,document:doc,addEventListener(){},dispatchEvent(){},
 setTimeout,clearTimeout,performance};
const g={window:w,document:doc,setTimeout,clearTimeout,performance};
const base=process.env.REPO;
for(const file of ['mcel-diagnostic-plugins.js','mcel-reactivity-shadow-plugin.js'])
 vm.runInNewContext(fs.readFileSync(base+'/main_computer/web/applications/scripts/'+file,'utf8'),g);
const root={nodeType:1,tagName:'DIV',children:[],ownerDocument:doc,contains(el){return el===this;},
 hidden:false,disabled:false,checked:false,readOnly:false,scrollTop:0,scrollLeft:0,
 getAttribute(){return null;},getBoundingClientRect(){return {x:0,y:0,width:10,height:10}}};
(async()=>{
 const reg=w.MCELDiagnosticPlugins,oracle=w.MCELReactivityShadowOracle;
 await reg.enable('mcel-reactivity-shadow');
 const one=oracle.beginOperation(root,'slh','intent:refresh','capability','external-read','monitor-poll');
 oracle.operationPhase(root,one,'invoking');
 const two=oracle.beginOperation(root,'slh','intent:second','capability','external-read');
 watch.trigger(3); // overlapping operations: never claim one owner
 oracle.finishOperation(root,two);
 watch.trigger(4); // unique in flight: temporal candidate only
 oracle.invalidate(root,'async-commit',one); // exact instrumented producer
 const state=new Map([['state:status','started']]);
 oracle.beforeNotify(root,'slh',state,0);
 const c=oracle.capture(root,'display','status',root);
 c.read('state:status');c.finish();oracle.afterNotify(root);
 oracle.operationReceipt(root,'intent:refresh','completed');
 oracle.finishOperation(root,one);
 watch.trigger(5); // no pending operation: unassociated
 const result=(await reg.run('mcel-reactivity-shadow')).result;
 const s=result.apps[0];
 const event=s.events.find(e=>e.type==='consumer');
 console.log(JSON.stringify({schema:s.correlation.schema,nextRevision:s.correlation.nextRevision,
   pending:s.correlation.async.singlePending,ambiguous:s.correlation.async.ambiguousPending,
   noPending:s.correlation.async.noPending,linked:s.correlation.async.receiptMatches,
   op:s.correlation.operations.find(x=>x.revision===one),
   commit:s.correlation.invalidations.find(x=>x.source==='async-commit'),
   event,certified:result.certified,changed:result.productionSchedulerChanged,
   invoked:result.applicationOperationsInvoked}));
 await reg.disable('mcel-reactivity-shadow');
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    source=tmp_path/'causal_oracle.cjs'
    source.write_text(script,encoding='utf-8')
    result=subprocess.run(['node',str(source)],cwd=ROOT,capture_output=True,text=True,
                          timeout=25,env={**os.environ,'REPO':str(ROOT)})
    assert result.returncode==0,result.stderr
    record=json.loads(result.stdout)
    assert record['schema']=='mcel.reactivity.revision-correlation.v1'
    assert record['nextRevision']==2
    assert (record['ambiguous'],record['pending'],record['noPending'])==(3,4,5)
    assert record['linked']==1 and record['op']['receipts']==1
    assert record['op']['origin']=='monitor-poll' and record['op']['notifications']==1
    assert record['commit']=={'source':'async-commit','revision':1,
                              'provenance':'instrumented-operation'}
    assert record['event']['revisionCandidate']==1
    assert record['event']['correlation']=='temporal-only'
    assert record['event']['changedStateIds']==[]  # first notification has no previous state
    assert record['certified'] is False and record['changed'] is False and record['invoked']==0


def test_two_functional_runners_publish_revision_linkage():
    slh=(ROOT/'tools/mcel_slh_functional_shadow.py').read_text()
    task=(ROOT/'tools/mcel_task_manager_functional_shadow.py').read_text()
    runtime=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text()
    assert 'operationRevisions' in slh and 'receiptLinks' in slh
    assert 'operationRevisions' in task and 'monitorPollRevisions' in task
    assert '"monitor-poll"' in runtime and 'beginOperation?.(' in runtime
    assert "'async-commit',shadowRevision" in runtime
