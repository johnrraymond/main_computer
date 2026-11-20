"""Direct DOM-producer ownership must not be confused with operation causality."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_direct_scheduled_overlapping_and_unclassified_mutation_partition(tmp_path):
    if not shutil.which('node'):
        pytest.skip('Node unavailable')
    source=r'''
const fs=require('fs'),vm=require('vm');
let watch=null;
class MutationObserver{
  constructor(fn){this.fn=fn;this.queue=[];watch=this;}
  observe(){} disconnect(){} takeRecords(){return this.queue.splice(0);}
  add(n){for(let i=0;i<n;i++)this.queue.push({type:'attributes',attributeName:'data-secret',attributeValue:'NEVER_REPORT'});}
  flush(){const a=this.takeRecords();if(a.length)this.fn(a);}
}
const doc={activeElement:null};
const w={MutationObserver,document:doc,addEventListener(){},dispatchEvent(){},
 setTimeout,clearTimeout,performance};
const g={window:w,document:doc,setTimeout,clearTimeout,performance};
for(const f of ['mcel-diagnostic-plugins.js','mcel-reactivity-shadow-plugin.js'])
 vm.runInNewContext(fs.readFileSync(process.env.REPO+'/main_computer/web/applications/scripts/'+f,'utf8'),g);
const root={nodeType:1,tagName:'DIV',children:[],ownerDocument:doc,
 contains(el){return el===this;},getAttribute(){return null;}};
(async()=>{
 await w.MCELDiagnosticPlugins.enable('mcel-reactivity-shadow');
 const o=w.MCELReactivityShadowOracle;
 const rev=o.beginOperation(root,'semantic-task-manager','intent:refresh','capability','external-read','monitor-poll');
 // Pre-existing queued writes must NEVER be stolen by a later producer.
 watch.add(3);
 const direct=o.beginMutationSource(root,'monitor-toggle');
 watch.add(5);o.endMutationSource(root,direct);
 // Schedule inside an actual notification, but don't promote its revision
 // candidate to a proven operation owner.
 o.beforeNotify(root,'semantic-task-manager',new Map(),0);
 const inert=o.beginMutationSource(root,'must-not-nest-in-notify');
 const scheduled=o.captureSchedule(root,'semantic-notify');
 o.afterNotify(root);
 o.finishOperation(root,rev);
 const span=o.beginMutationSource(root,'generated-layout-frame',null,scheduled);
 watch.add(7);o.endMutationSource(root,span);
 watch.add(4);watch.flush();
 const a=o.beginOperation(root,'semantic-task-manager','intent:read-a','capability','external-read');
 const b=o.beginOperation(root,'semantic-task-manager','intent:read-b','capability','external-read');
 watch.add(2);watch.flush();
 o.finishOperation(root,b);o.finishOperation(root,a);
 const report=(await w.MCELDiagnosticPlugins.run('mcel-reactivity-shadow')).result;
 const s=report.apps[0];
 console.log(JSON.stringify({
  producer:s.correlation.producers,
  total:s.stats.asyncMutations,temporals:s.correlation.async,
  scheduled:s.events.find(x=>x.source==='generated-layout-frame'&&x.type==='async-dom-batch'),
  inert:inert===null,certified:report.certified,changed:report.productionSchedulerChanged,
  invoked:report.applicationOperationsInvoked,leaked:JSON.stringify(report).includes('NEVER_REPORT')
 }));
})().catch(e=>{console.error(e);process.exitCode=1});
'''
    file=tmp_path/'producer_probe.cjs';file.write_text(source)
    run=subprocess.run(['node',str(file)],cwd=ROOT,capture_output=True,text=True,
                       timeout=25,env={**os.environ,'REPO':str(ROOT)})
    assert run.returncode==0,run.stderr
    data=json.loads(run.stdout)
    producer=data['producer']
    assert data['total']==21
    assert producer['identified']==12 and producer['unclassified']==9
    assert producer['direct']==5 and producer['scheduled']==7
    assert producer['unclassifiedNoPending']==4
    assert producer['unclassifiedSinglePending']==3
    assert producer['unclassifiedAmbiguousPending']==2
    assert producer['partitionInvariant'] is True
    assert [(s['source'],s['records']) for s in producer['sources']]==[
        ('generated-layout-frame',7),('monitor-toggle',5)]
    assert data['scheduled']['scheduledByNotification']==1
    assert data['scheduled']['scheduledRevisionCandidate']==1
    assert data['scheduled']['exactOperationRevision'] is None
    assert data['temporals']['singlePending']==8
    assert data['temporals']['noPending']==11
    assert data['temporals']['ambiguousPending']==2
    assert data['inert'] is True and not data['leaked']
    assert data['certified'] is False and data['changed'] is False and data['invoked']==0


def test_functional_reports_reject_inconsistent_producer_partition():
    from tests.test_mcel_task_manager_functional_shadow import _plugin, _stages, _refresh
    from tools.mcel_task_manager_functional_shadow import evaluate
    broken=_plugin()
    broken['apps'][0]['correlation']['producers']['partitionInvariant']=False
    result=evaluate(broken,_stages(),{'status':'PASS'},[],_refresh())
    assert result['status']=='FAIL'
    assert 'PRODUCER_PARTITION_INVARIANT_FAILED' in result['failures']


def test_runtime_exposes_explicit_producers_without_changing_refresh_interval():
    text=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js').read_text()
    assert "shadowDOMSource('monitor-toggle'" in text
    assert "shadowDOMSource('monitor-settlement-status'" in text
    assert "shadowDOMSource('generated-layout-frame'" in text
    assert "shadowDOMSource('operation-finalize',refreshColor,shadowRevision)" in text
    assert 'taskRefreshPeriodMs = 5000' in text
