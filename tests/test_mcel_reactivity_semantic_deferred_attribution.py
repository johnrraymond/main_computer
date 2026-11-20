"""Shadow-only causal categories: exact revisions, independent UI, and unexplained.

The reporter must never upgrade pending-operation time proximity into proof.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_exact_deferred_context_and_three_way_attribution(tmp_path):
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    source=r'''
const vm=require('vm'),fs=require('fs');
let observer;
class MutationObserver {
 constructor(callback){this.callback=callback;this.records=[];observer=this;}
 observe(){} disconnect(){}
 takeRecords(){return this.records.splice(0);}
 add(type,name,count){for(let i=0;i<count;i++)this.records.push({type,attributeName:name,oldValue:'PRIVATE'});}
 flush(){const records=this.takeRecords();if(records.length)this.callback(records);}
}
const doc={activeElement:null};
const w={MutationObserver,document:doc,addEventListener(){},dispatchEvent(){},setTimeout,clearTimeout,performance};
const g={window:w,document:doc,setTimeout,clearTimeout,performance};
for (const f of ['mcel-diagnostic-plugins.js','mcel-reactivity-shadow-plugin.js'])
 vm.runInNewContext(fs.readFileSync(process.env.REPO+'/main_computer/web/applications/scripts/'+f,'utf8'),g);
const root={nodeType:1,tagName:'DIV',children:[],ownerDocument:doc,contains(el){return el===this;},getAttribute(){return null;}};
(async()=>{
 const plugins=w.MCELDiagnosticPlugins,oracle=w.MCELReactivityShadowOracle;
 await plugins.enable('mcel-reactivity-shadow');
 const a=oracle.beginOperation(root,'semantic-task-manager','intent:A','capability','external-read');
 const b=oracle.beginOperation(root,'semantic-task-manager','intent:B','capability','external-read');
 // A simultaneous pending operation is not enough to establish ownership.
 oracle.beforeNotify(root,'semantic-task-manager',new Map(),0);
 const pendingOnly=oracle.captureSchedule(root,'unproven-defer');
 oracle.afterNotify(root);
 const pendingToken=oracle.beginMutationSource(root,'unproven-defer',null,pendingOnly);
 observer.add('attributes','data-mcel-form-presentation-kind',2);
 oracle.endMutationSource(root,pendingToken);
 // An explicitly carried operation ID establishes an exact operation link
 // despite concurrent in-flight operations.
 oracle.beforeNotify(root,'semantic-task-manager',new Map(),0,a);
 const proven=oracle.captureSchedule(root,'operation-linked-defer');
 oracle.afterNotify(root);
 oracle.finishOperation(root,a);
 const exactToken=oracle.beginMutationSource(root,'operation-linked-defer',null,proven);
 observer.add('attributes','data-mcel-color-state',3);
 oracle.endMutationSource(root,exactToken);
 const independent=oracle.beginMutationSource(root,'semantic-form-law');
 observer.add('attributes','data-mcel-form-profile',4);
 oracle.endMutationSource(root,independent);
 // Mutation without an observed callback stays unexplained, including when
 // a pending request offers a tempting temporal candidate.
 observer.add('attributes','data-mcel-not-a-secret',5);
 observer.flush();
 oracle.finishOperation(root,b);
 const report=(await plugins.run('mcel-reactivity-shadow')).result;
 const app=report.apps[0],p=app.correlation.producers;
 const details=app.events.filter(x=>x.type==='async-dom-batch');
 console.log(JSON.stringify({p,details,temporal:app.correlation.async,
   certified:report.certified,changed:report.productionSchedulerChanged,
   invoked:report.applicationOperationsInvoked}));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    path=tmp_path/'probe.cjs';path.write_text(source)
    result=subprocess.run(['node',str(path)],cwd=ROOT,capture_output=True,text=True,
                          timeout=30,env={**os.environ,'REPO':str(ROOT)})
    assert result.returncode==0,result.stderr
    d=json.loads(result.stdout)
    p=d['p']
    assert (p['operationLinked'],p['independentUi'],p['unexplained'])==(3,6,5)
    assert (p['identified'],p['unclassified'])==(9,5)
    assert p['partitionInvariant'] is True and p['classificationInvariant'] is True
    assert p['unclassifiedAttributeNames']=={'data-mcel-not-a-secret':5}
    assert [e['classification'] for e in d['details']]==[
        'identified-independent-ui','operation-linked','identified-independent-ui','unexplained']
    assert [e['exactOperationRevision'] for e in d['details']]==[None,1,None,None]
    assert [e['correlation'] for e in d['details']]==['ambiguous']+['temporal-only']*3
    assert d['certified'] is False and d['changed'] is False and d['invoked']==0
    assert 'PRIVATE' not in result.stdout


def test_shared_semantic_markers_and_deferred_reconciliation_are_instrumented():
    scripts=ROOT/'main_computer/web/applications/scripts'
    form=(scripts/'mcel-semantic-form-presentation-law.js').read_text()
    icon=(scripts/'mcel-semantic-icon-core.js').read_text()
    chrome=(scripts/'mcel-semantic-chrome-presentation-law.js').read_text()
    runtime=(scripts/'mcel-semantic-operational-runtime.js').read_text()
    assert "beginMutationSource?.(root,'semantic-form-law')" in form
    assert "'semantic-icon-core'" in icon and 'state.refresh(scheduled)' in icon
    assert "captureSchedule?.(" in icon
    assert "'semantic-chrome-materials'" in chrome
    assert 'notify(receiptRevision)' in runtime and 'notify(shadowRevision)' in runtime
    assert 'taskRefreshPeriodMs = 5000' in runtime


def test_functional_runners_reject_false_three_way_partition():
    from tests.test_mcel_task_manager_functional_shadow import _plugin,_stages,_refresh
    from tools.mcel_task_manager_functional_shadow import evaluate
    from tests.test_mcel_slh_functional_shadow import _sample, _stages as slh_stages, _actions
    from tools.mcel_slh_functional_shadow import evaluate_shadow
    task=_plugin()
    task['apps'][0]['correlation']['producers']['classificationInvariant']=False
    result=evaluate(task,_stages(),{'status':'PASS'},[],_refresh())
    assert result['status']=='FAIL'
    assert 'CAUSAL_CLASSIFICATION_PARTITION_FAILED' in result['failures']
    slh=_sample()
    slh['apps'][0]['correlation']['producers']['classificationInvariant']=False
    result=evaluate_shadow(slh,slh_stages(),[],_actions())
    assert result['status']=='FAIL'
    assert 'CAUSAL_CLASSIFICATION_PARTITION_FAILED' in result['failures']
