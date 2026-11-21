"""Patch 1: isolated candidate execution + opt-in incumbent observation.

No production scheduler change or application mutations are authorized here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'


def probe(js: str, tmp_path: Path) -> dict:
    if not shutil.which('node'):
        pytest.skip('Node is unavailable')
    script = f'''
const fs=require('fs'),vm=require('vm');
const context={{}};
vm.runInNewContext(fs.readFileSync(process.env.MCEL_RUNTIME,'utf8'),context);
const create=context.McelSemanticOperationalRuntime.createSignalCandidate;
if(typeof create!=="function")throw Error('Factory is missing');
{js}
'''
    file=tmp_path/'candidate.cjs'
    file.write_text(script,encoding='utf8')
    result=subprocess.run(['node',str(file)],cwd=ROOT,capture_output=True,text=True,timeout=30,
        env={**os.environ,'MCEL_RUNTIME':str(RUNTIME)})
    assert result.returncode==0,result.stderr+'\n'+result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_isolated_signal_execution_is_ordered_and_skips_only_closed_inputs(tmp_path):
    result=probe(r'''
const state=new Map([['state:a',1],['state:b',2],['state:flag',true]]);
const events=[];
const s=create({isolated:true,getState:key=>state.get(key)});
s.register('a',()=>{events.push('a');s.read('state:a');},{safeToSkip:true});
s.register('b',()=>{events.push('b');s.read('state:b');},{safeToSkip:true});
s.register('conditional',()=>{
  events.push('conditional');
  if(s.read('state:flag'))s.read('state:a');else s.read('state:b');
},{safeToSkip:true});
s.register('theme',()=>{events.push('theme');s.trackExternalRead('theme');},
  {safeToSkip:true,externalContracts:['theme']});
s.register('uncontracted',()=>{events.push('uncontracted');s.trackExternalRead('host-local');},
  {safeToSkip:true});
s.register('opaque',()=>{events.push('opaque');},{safeToSkip:true});
const first=s.flush({revision:1,changedStateIds:['state:a','state:b','state:flag'],externalInvalidations:['theme']});
state.set('state:a',3);
const second=s.flush({revision:2,changedStateIds:['state:a']});
const duplicate=s.flush({revision:2,changedStateIds:['state:a']});
state.set('state:b',8);
const newDelta=s.flush({revision:2,changedStateIds:['state:b']});
state.set('state:flag',false);
const changedBranch=s.flush({revision:3,changedStateIds:['state:flag']});
state.set('state:a',9);
const postBranch=s.flush({revision:4,changedStateIds:['state:a']});
const force=s.flush({revision:5,unknown:true});
console.log(JSON.stringify({first,second,duplicate,newDelta,changedBranch,postBranch,force,
  final:s.report(),events}));
''',tmp_path)
    assert result['first']['executed']==['a','b','conditional','theme','uncontracted','opaque']
    assert result['second']['executed']==['a','conditional','uncontracted','opaque']
    assert result['second']['skipped']==['b','theme']
    assert result['duplicate']['duplicate'] is True
    assert result['duplicate']['skipped']==['a','b','conditional','theme']
    assert result['duplicate']['executed']==['uncontracted','opaque']
    assert result['newDelta']['executed']==['b','uncontracted','opaque']
    assert result['changedBranch']['executed']==['conditional','uncontracted','opaque']
    assert result['postBranch']['executed']==['a','uncontracted','opaque']
    assert len(result['force']['executed'])==6
    assert result['final']['productionSchedulerChanged'] is False
    assert result['final']['certified'] is False
    conditional=next(x for x in result['final']['consumers'] if x['id']=='conditional')
    assert conditional['reads']==['state:b','state:flag']
    assert 'host-local' in next(x for x in result['final']['consumers'] if x['id']=='uncontracted')['externalReads']


def test_unknown_external_invalidations_and_error_do_not_create_false_skips(tmp_path):
    result=probe(r'''
let fail=true,n=0;
const s=create({isolated:true});
const gone=s.register('disposable',()=>{n++;s.trackStateRead('state:now');},{safeToSkip:true});
s.register('unsteady',()=>{n++;s.trackStateRead('state:now');if(fail){fail=false;throw Error('one-time render failure');}},{safeToSkip:true});
let refused=false;
try{s.flush({revision:1,changedStateIds:['state:now']});}catch(e){refused=e.message.includes('one-time render failure');}
const after=s.flush({revision:2,changedStateIds:[]});
const malformed=s.flush({revision:3,changedStateIds:'INVALID_INPUT'});
let stale=false;try{s.flush({revision:2,changedStateIds:[]});}catch(e){stale=true;}
gone();
const removed=s.flush({revision:4,changedStateIds:[]});
const before=s.report();s.release();
let disposed=false;try{s.flush({revision:5});}catch(e){disposed=true;}
console.log(JSON.stringify({refused,after,malformed,stale,removed,before,disposed,n}));
''',tmp_path)
    assert result['refused'] is True
    assert result['after']['executed']==['unsteady']
    assert result['after']['skipped']==['disposable']
    assert result['malformed']['executed']==['disposable','unsteady']
    assert result['stale'] is True
    assert result['removed']['executed']==[]
    assert result['removed']['skipped']==['unsteady']
    assert result['before']['consumerCount']==1
    assert result['disposed'] is True


def test_default_observer_never_skips_incumbent_and_does_not_export_values(tmp_path):
    result=probe(r'''
const s=create();let renders=0;
const state=new Map([['state:secret','DO_NOT_EXPORT_SECRET'],['state:counter',1]]);
s.beginNotify(state,null,0);
s.observe('typed-record/item',()=>{renders++;s.trackStateRead('state:secret');});
s.endNotify();
state.set('state:counter',2);
s.beginNotify(state,3,1);
s.observe('typed-record/item',()=>{renders++;s.trackStateRead('state:secret');});
s.endNotify();
const report=s.report();let guarded=false;
try{s.flush({revision:1});}catch(e){guarded=e.message==='MCEL_SIGNAL_CANDIDATE_ISOLATED_ONLY';}
console.log(JSON.stringify({report,renders,guarded,leaks:JSON.stringify(report).includes('DO_NOT_EXPORT_SECRET')}));
''',tmp_path)
    assert result['renders']==2
    assert result['guarded'] is True
    assert result['leaks'] is False
    assert result['report']['mode']=='observe'
    assert result['report']['statistics']['executions']==2
    assert result['report']['history'][0]['unknown'] is True
    assert result['report']['history'][1]['unknown'] is False
    assert result['report']['history'][1]['changedStateKeys']==['state:counter']
    assert result['report']['history'][1]['invalidatedExternalSources']==['clock','receipts']
    assert result['report']['consumers'][0]['skipAuthorized'] is False


def test_runtime_mount_does_not_enable_candidate_or_change_notify_by_default():
    source=RUNTIME.read_text(encoding='utf8')
    assert 'options.signalCandidateDiagnostics===true' in source
    assert 'signalCandidate?signalCandidate.observe(' in source
    assert 'signalCandidate?.trackStateRead(id)' in source
    assert 'signalCandidate?.trackExternalRead(name)' in source
    assert 'signalCandidate?.release()' in source
    assert 'signalCandidateReport:signalSchedulingReport' in source
    assert 'options.signalSchedulerStartEnabled===true' in source
    assert 'options.signalSchedulerIntegration===true' in source
    assert 'for (const [r,el] of regionHosts) shadowObserve(' in source
    assert 'for(const [index,refresh] of [...deselectionControls].entries())' in source
    assert 'signalCandidate.observe(signalConsumerKey(kind,id,targets),invoke,externalSources)' in source


def test_mounted_slh_opt_in_collects_graph_without_skipping(monkeypatch):
    pytest.importorskip('playwright')
    from playwright.sync_api import sync_playwright
    from tests import test_mcel_seh_two_action_lifecycle_ui as fixture

    original=fixture.render_slh_page
    anchor='navigationChrome:document.getElementById("mcel-slh-navigation-context")'
    def with_diagnostics():
        html=original()
        assert html.count(anchor)==1
        return html.replace(anchor,'signalCandidateDiagnostics:true,'+anchor)
    monkeypatch.setattr(fixture,'render_slh_page',with_diagnostics)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,
            executable_path=shutil.which('chromium') or None,args=['--no-sandbox'])
        try:
            page,_=fixture._setup(browser,'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED')
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            app='window.__mcelSlh.app'
            before=page.evaluate(f'() => {app}.signalCandidateReport()')
            response=page.evaluate(f"async()=>await {app}.execute('intent:read-seh-deployment')")
            after=page.evaluate(f'() => {app}.signalCandidateReport()')
            assert response['disposition']=='completed'
            assert before['schema']=='mcel.signal-candidate.v1'
            assert before['mode']=='observe'
            assert after['consumerCount']>=30
            assert after['statistics']['observations']>before['statistics']['observations']
            assert after['statistics']['executions']>before['statistics']['executions']
            assert after['statistics']['skips']==0
            assert not any(c['skipAuthorized'] for c in after['consumers'])
            assert all(x['unknown'] is False for x in after['history'][1:])
            assert after['productionSchedulerChanged'] is False
            assert after['certified'] is False
            assert not errors,errors
        finally:
            browser.close()


def test_nested_observations_preserve_outer_revision_context(tmp_path):
    result=probe(r'''
const s=create();let renders=0;
const state=new Map([['state:one',1]]);
s.beginNotify(state,1);
s.observe('outer',()=>{
  renders++;s.trackStateRead('state:one');
  state.set('state:one',2);s.beginNotify(state,2);
  s.observe('inner',()=>{renders++;s.trackStateRead('state:one');});
  s.endNotify();
  s.trackExternalRead('parent-context');
});
s.endNotify();
const r=s.report();
console.log(JSON.stringify({renders,history:r.history,consumers:r.consumers}));
''',tmp_path)
    assert result['renders']==2
    assert len(result['history'])==2
    assert [entry['exactRevision'] for entry in result['history']]==[2,1]
    assert set(result['consumers'][0]['externalReads'])=={'parent-context'}


def test_rebinding_and_async_consumers_force_conservative_execution(tmp_path):
    result=probe(r'''
const s=create({isolated:true});let x=0;
const stale=s.register('rebound',()=>{x++;s.trackStateRead('state:a');},{safeToSkip:true});
s.register('async',()=>{x++;s.trackStateRead('state:b');return Promise.resolve('ok');},
  {safeToSkip:true});
s.flush({revision:1,changedStateIds:['state:a','state:b']});
const stable=s.flush({revision:2,changedStateIds:[]});
const latest=s.register('rebound',()=>{x++;s.trackStateRead('state:new');},{safeToSkip:true});
stale(); // Disposer for an old registration must NOT remove the new one.
const rebound=s.flush({revision:3,changedStateIds:[]});
const graph=s.report();
latest();
console.log(JSON.stringify({stable,rebound,graph,remaining:s.report().consumerCount,x}));
''',tmp_path)
    assert result['stable']['skipped']==['rebound']
    assert result['stable']['executed']==['async']
    assert result['rebound']['executed']==['rebound','async']
    assert result['remaining']==1
    new=next(c for c in result['graph']['consumers'] if c['id']=='rebound')
    assert new['reads']==['state:new']
    asynchronous=next(c for c in result['graph']['consumers'] if c['id']=='async')
    assert asynchronous['uncertain'] is True
