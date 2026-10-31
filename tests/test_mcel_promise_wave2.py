"""Patch02: real DSL capability graph and fail-closed generic runtime contracts."""
from pathlib import Path
import json
import shutil
import subprocess
import pytest
from main_computer.mcel_dsl_compiler import compile_dsl_application
from tools.mcel_task_notebook_candidate_bundle import SOURCE, TARGET, build

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'main_computer/web/applications/scripts/mcel-intent-effect-runtime.js'

def compiled():
    r=compile_dsl_application(SOURCE)
    assert r.valid, r.diagnostics
    return r.normalized_ir

def test_effect_capability_intent_are_declared_and_source_bound():
    ir=compiled()
    assert len(ir['effects'])==len(ir['capabilities'])==1
    cap=ir['capabilities'][0]
    effect=ir['effects'][0]
    intent=next(i for i in ir['intents'] if i['operationKind']=='capability')
    control=next(c for c in ir['surfaces'][0]['semanticSurface']['controls'] if c['role']=='capability-action')
    assert cap['risk']==effect['risk']==intent['risk']==control['risk']=='external-read'
    assert cap['operations']==[{'name':'requestSnapshot','runtimeMethod':'refreshTaskManager','cancellable':False}]
    assert effect['owner']=={'ref':intent['id']}
    assert effect['target']==effect['authority']=={'ref':cap['id']}
    assert intent['effectRefs']==[{'ref':effect['id']}]
    assert intent['writes']==[] and intent['input']==[]
    assert {'operation-receipt','capability-response','visible-outcome'}<=set(effect['requiredEvidence'])
    bundle,meta=build()
    assert TARGET.read_text(encoding='utf-8')==bundle
    assert meta['status']=='new-candidate-not-historical-recovery'

def _node(js, ir):
    node=shutil.which('node')
    if not node:pytest.skip('Node required')
    done=subprocess.run([node,'-e',js,str(ENGINE)],input=json.dumps(ir),text=True,capture_output=True,timeout=15)
    assert done.returncode==0,done.stderr
    return json.loads(done.stdout)

def test_generic_executor_rejects_mutation_undeclared_and_broken_graph():
    script=r'''const fs=require('fs');const engine=require(process.argv[1]);
    const ir=JSON.parse(fs.readFileSync(0,'utf8'));
    const results={};
    function checked(name,mutator){let clone=JSON.parse(JSON.stringify(ir));mutator(clone);
      try{engine.check(clone);results[name]='ACCEPTED';}catch(e){results[name]=String(e.message);}}
    checked('mutation',d=>d.intents.find(x=>x.operationKind==='capability').risk='external-mutation');
    checked('effect',d=>d.effects[0].target.ref='capability:wrong');
    checked('orphan',d=>d.capabilities.push({...d.capabilities[0],id:'capability:extra'}));
    checked('unbound',d=>d.surfaces[0].semanticSurface.controls.find(c=>c.role==='capability-action').intent='unknown');
    process.stdout.write(JSON.stringify(results));'''
    result=_node(script,compiled())
    assert all(value!='ACCEPTED' for value in result.values()),result


def test_executor_is_adapter_independent_and_receipt_gated():
    script=r'''const fs=require('fs'),runtime=require(process.argv[1]);
    const ir=JSON.parse(fs.readFileSync(0,'utf8'));
    (async()=>{
      const action=runtime.check(ir)[0];
      let version=0,calls=0,finish;
      const adapters={[action.capabilityId]:{[action.method]:async()=>{calls++;
        await new Promise(resolve=>{finish=resolve});version++;return {ok:true,delivery:{ok:true,version,status:'available'}};}}};
      const engine=runtime.mount(ir,{adapters,observe:()=>version});
      const promise=engine.dispatch(action.id);
      const duplicate=await engine.dispatch(action.id);
      const unknown=await engine.dispatch('unknown');
      finish();const completed=await promise;
      const stale=runtime.mount(ir,{adapters:{[action.capabilityId]:{[action.method]:async()=>({ok:true,delivery:{ok:true,version,status:'available'}})}},observe:()=>version});
      const declined=await stale.dispatch(action.id);
      const denied=await runtime.mount(ir,{}).dispatch(action.id);
      engine.destroy();stale.destroy();
      process.stdout.write(JSON.stringify({completed,duplicate,unknown,declined,denied,calls}));
    })().catch(e=>{process.stderr.write(String(e));process.exit(1)});'''
    data=_node(script,compiled())
    assert data['completed']['ok'] and data['completed']['afterVersion']>data['completed']['beforeVersion']
    assert data['calls']==1
    assert data['duplicate']['code']=='already-pending'
    assert data['unknown']['code']=='undeclared-intent'
    assert data['declined']['code']=='snapshot-not-advanced'
    assert data['denied']['code']=='capability-unavailable'


def test_no_mutation_backend_or_unconditional_authority_was_added():
    app=(ROOT/'main_computer/web/applications.html').read_text(encoding='utf-8')
    bridge=(ROOT/'main_computer/web/applications/scripts/mcel-task-manager-notebook-bridge.js').read_text(encoding='utf-8')
    assert app.index('mcel-intent-effect-runtime.js') < app.index('mcel-tab-workspace.js')
    assert app.index('mcel-task-manager-notebook-bridge.js') < app.index('applications/scripts/task-manager.js')
    assert 'requestTaskAction(' not in bridge and 'fetch(' not in bridge
    assert 'server_shutdown' not in bridge and 'localStorage' not in bridge
    assert 'requestedMode === "lab"' in bridge and 'lab-operational-authority-refused' in bridge
    assert 'if (requestedMode === "candidate")' in bridge
