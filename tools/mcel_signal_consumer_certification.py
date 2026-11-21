"""Patch 3: individual signal-consumer class certification in isolated test fixtures.

Proves actual isolated skips, differential DOM/semantic/receipt parity and a
separate deterministic adversarial contract for each experimental consumer
class. It NEVER authorizes live scheduling or asserts real-host certification.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from tools.mcel_signal_differential_acceptance import (
    EXPERIMENTAL_KINDS, ROOT, run_differential,
)

DEFAULT = Path('runtime/reports/mcel-signal-certification/report.json')
ADVERSARIAL_PHASES = (
    'bootstrap', 'unrelated-state', 'changed-active-input', 'change-branch',
    'retired-branch-input', 'changed-new-branch', 'external-invalidation',
    'unknown-invalidation', 'malformed-invalidation', 'duplicate-revision',
    'async-consumer-fallback', 'render-error', 'render-recovery',
    'rebind-and-dispose', 'stale-revision-rejected', 'release',
)
BROWSER_STRESS_PHASES = (
    'fault-observation', 'observation-recovery', 'transport-failure',
    'transport-recovery', 'remount-and-refresh',
)

# Deliberately an independent executable specification of consumer behavior:
# the incumbent renders on every revision while the candidate may suppress
# idempotent rendering only when the observed dependency contract is closed.
# The isolated candidate engine is the same checked-in runtime as the browser.
ADVERSARIAL_JS = r'''
const fs=require('fs'),vm=require('vm');
const context={};vm.runInNewContext(fs.readFileSync(process.env.MCEL_RUNTIME,'utf8'),context);
const create=context.McelSemanticOperationalRuntime.createSignalCandidate;
const classes=['condition','display','selected-record','typed-record'];
const output={schema:'mcel.signal-consumer-adversarial.v1',classes:{}};
for(const kind of classes){
  const phases=[],state=new Map([['state:mode','first'],['state:a','alpha'],
    ['state:b','beta'],['state:other',0],['state:secret','SENSITIVE_CANARY_NEVER_EXPORT']]);
  const source=({condition:'clock',display:'theme','selected-record':'selection',
    'typed-record':'receipts'})[kind];
  let reference=null,candidate=null,revision=0,executions=0,baselineExecutions=0;
  const graph=create({isolated:true,getState:key=>state.get(key)});
  const record=(label,passed,evidence={})=>phases.push({phase:label,status:passed?'PASS':'FAIL',...evidence});
  const draw=(read,external)=>{
    const branch=read('state:mode'),value=read(branch==='first'?'state:a':'state:b');
    external(source);
    switch(kind){
      case 'condition':return {hidden:value==='beta'};
      case 'display':return {text:String(value)};
      case 'selected-record':return {selected:{id:branch,label:value}};
      case 'typed-record':return {record:{type:'fixture',current:value}};
    }
  };
  const render=()=>{executions++;candidate=draw(k=>graph.read(k),x=>graph.trackExternalRead(x));};
  const incumbent=()=>{baselineExecutions++;reference=draw(k=>state.get(k),()=>{});};
  const key=kind+'/certification';
  let dispose=graph.register(key,render,{safeToSkip:true,externalContracts:[source]});
  function step(label,changed=[],external=[],other={}){
    revision++;incumbent();const before=executions;
    const r=graph.flush({revision,changedStateIds:changed,externalInvalidations:external,...other});
    const equal=JSON.stringify(candidate)===JSON.stringify(reference);
    const supposedSkip=other.expectedSkip===true;
    const skip=r.skipped.includes(key),run=r.executed.includes(key);
    const good=equal&&(supposedSkip?skip&&!run:run&&!skip);
    record(label,good,{equal,executed:run,skipped:skip,reason:r.reasons[key]});
    if(executions-before!==(run?1:0))throw Error('EXECUTION_ACCOUNTING:'+kind+':'+label);
  }
  step('bootstrap',['state:mode','state:a','state:b']);
  step('unrelated-state',['state:other'],[],{expectedSkip:true});
  state.set('state:a','ALPHA');step('changed-active-input',['state:a']);
  state.set('state:mode','second');step('change-branch',['state:mode']);
  state.set('state:a','obsolete');step('retired-branch-input',['state:a'],[],{expectedSkip:true});
  state.set('state:b','BETA');step('changed-new-branch',['state:b']);
  step('external-invalidation',[],[source]);
  step('unknown-invalidation',[],[],{unknown:true});
  step('malformed-invalidation','INVALID',[]);
  revision++;incumbent();let duplicate=graph.flush({revision,changedStateIds:[],externalInvalidations:[source]});
  incumbent();const again=graph.flush({revision,changedStateIds:[],externalInvalidations:[source]});
  record('duplicate-revision',duplicate.executed.includes(key)&&
    again.duplicate===true&&again.skipped.includes(key)&&
    JSON.stringify(candidate)===JSON.stringify(reference),{deduplicated:again.duplicate});
  let asyncRenders=0;
  graph.register(kind+'/async',()=>{asyncRenders++;graph.read('state:secret');return Promise.resolve('late');},
    {safeToSkip:true});
  const asyncFirst=graph.flush({revision:++revision,changedStateIds:['state:secret']});
  const asyncSecond=graph.flush({revision:++revision,changedStateIds:[]});
  record('async-consumer-fallback',asyncFirst.executed.includes(kind+'/async')&&
    asyncSecond.executed.includes(kind+'/async')&&asyncRenders===2);
  let fail=true,attempts=0;
  graph.register(kind+'/throw',()=>{attempts++;graph.read('state:a');
    if(fail){fail=false;throw Error('INJECTED_RENDER_ERROR');}},
  {safeToSkip:true});
  let refused=false;
  try{graph.flush({revision:++revision,changedStateIds:['state:a']});}
  catch(e){refused=String(e).includes('INJECTED_RENDER_ERROR');}
  record('render-error',refused);
  const retry=graph.flush({revision:++revision,changedStateIds:[]});
  const stable=graph.flush({revision:++revision,changedStateIds:[]});
  record('render-recovery',retry.executed.includes(kind+'/throw')&&
    stable.skipped.includes(kind+'/throw')&&attempts===2);
  const beforeRebind=graph.report();
  const latestDispose=graph.register(key,()=>{executions++;candidate=draw(k=>graph.read(k),x=>graph.trackExternalRead(x));},
    {safeToSkip:true,externalContracts:[source]});
  dispose(); // A disposer from the old generation cannot delete the new one.
  const firstRebound=graph.flush({revision:++revision,changedStateIds:[]});
  const graphAfter=graph.report();
  // Disposal must remove the old generation; the new generation begins
  // unobserved and cannot inherit any previously certified dependencies.
  record('rebind-and-dispose',firstRebound.executed.includes(key)&&
    graphAfter.consumers.find(c=>c.id===key)?.observed===true&&
    beforeRebind.consumerCount===3);
  let staleRefused=false;
  try{graph.flush({revision:revision-1,changedStateIds:[]});}
  catch(e){staleRefused=String(e).includes('REVISION_INVALID');}
  record('stale-revision-rejected',staleRefused);
  latestDispose();graph.release();
  let released=false;
  try{graph.flush({revision:revision+1});}catch(e){released=String(e).includes('DISPOSED');}
  record('release',released&&graph.report().consumerCount===0);
  const trace=graphAfter;
  const privateTrace=!JSON.stringify(trace).includes('SENSITIVE_CANARY_NEVER_EXPORT');
  const passed=phases.length===16&&phases.every(p=>p.status==='PASS')&&
    privateTrace&&trace.certified===false&&trace.productionSchedulerChanged===false;
  output.classes[kind]={status:passed?'PASS':'FAIL',phases,privacyPreserved:privateTrace,
    productionSchedulerChanged:false,productionCertified:false,
    referenceExecutions:baselineExecutions,candidateExecutions:executions};
}
console.log(JSON.stringify(output));
'''


def run_adversarial_probes(repo: Path = ROOT) -> dict:
    node = shutil.which('node')
    if not node:
        return {'status': 'BLOCKED', 'reason': 'NODE_NOT_AVAILABLE', 'classes': {}}
    runtime = repo/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'
    with tempfile.TemporaryDirectory(prefix='mcel-signal-cert-') as temp:
        script=Path(temp)/'adversarial.cjs'
        script.write_text(ADVERSARIAL_JS,encoding='utf8')
        result=subprocess.run([node,str(script)],cwd=repo,env={**os.environ,'MCEL_RUNTIME':str(runtime)},
                              capture_output=True,text=True,timeout=40)
    if result.returncode:
        return {'status':'BLOCKED','reason':'NODE_ADVERSARIAL_FAILURE',
                'stderr':result.stderr[-3000:],'stdout':result.stdout[-1000:],'classes':{}}
    try:
        report=json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError,IndexError):
        return {'status':'BLOCKED','reason':'INVALID_NODE_REPORT','classes':{}}
    report['status']='PASS' if all(report.get('classes',{}).get(k,{}).get('status')=='PASS'
                                   for k in EXPERIMENTAL_KINDS) else 'BLOCKED'
    return report


def certify(*,repo:Path=ROOT,apps=('slh','task-manager'),kinds=EXPERIMENTAL_KINDS,
            candidate_mode='isolated') -> dict:
    if candidate_mode not in ('isolated','integrated'):
        raise ValueError('INVALID_CANDIDATE_MODE')
    if not kinds or any(kind not in EXPERIMENTAL_KINDS for kind in kinds):
        raise ValueError('INVALID_CERTIFICATION_CLASS')
    if not apps or any(app not in ('slh','task-manager') for app in apps):
        raise ValueError('INVALID_CERTIFICATION_APPLICATION')
    probes=run_adversarial_probes(repo)
    class_results=[]
    for kind in kinds:
        probe=probes.get('classes',{}).get(kind)
        applications=[]
        for app in apps:
            try:
                result=run_differential(app,repo,candidate_kinds=(kind,),stress=True,
                    require_skips=False,candidate_mode=candidate_mode)
                observed=result['candidate'].get('observedConsumersByClass',{}).get(kind,0)
                skipped=result['candidate'].get('actualSkips',0)
                phases=[stage['phase'] for stage in result['stages']]
                missing=[phase for phase in BROWSER_STRESS_PHASES if phase not in phases]
                if result['status']!='PASS_DIFFERENTIAL' or missing:
                    status='BLOCKED'
                    failures=list(result.get('failures',[])) + (['MISSING_STRESS_PHASES:'+','.join(missing)] if missing else [])
                elif observed==0:
                    status='NOT_APPLICABLE';failures=[]
                elif skipped==0:
                    status='BLOCKED';failures=['NO_EXERCISED_SKIPS_FOR_CLASS']
                else:
                    status='PASS_INTEGRATED' if candidate_mode=='integrated' else 'PASS_ISOLATED'
                    failures=[]
                applications.append({'app':app,'status':status,'observedConsumers':observed,
                    'actualSkips':skipped,'failures':failures,'stages':result.get('stages',[]),
                    'boundaries':result.get('boundaries',{}),'browser':result.get('browser'),
                    'errors':result.get('errors',{})})
            except Exception as exc:
                applications.append({'app':app,'status':'BLOCKED','observedConsumers':0,
                    'actualSkips':0,'failures':[type(exc).__name__+': '+str(exc)[:1000]]})
        relevant=[x for x in applications if x['status']!='NOT_APPLICABLE']
        passing='PASS_INTEGRATED' if candidate_mode=='integrated' else 'PASS_ISOLATED'
        certified=(bool(probe) and probe.get('status')=='PASS' and bool(relevant)
                   and all(x['status']==passing for x in relevant)
                   and len(applications)==len(apps))
        not_applicable=(bool(probe) and probe.get('status')=='PASS' and not relevant
                        and len(applications)==len(apps)
                        and all(x['status']=='NOT_APPLICABLE' for x in applications))
        class_results.append({'class':kind,
            'status':('PASS_INTEGRATED_CLASS' if candidate_mode=='integrated' else 'PASS_ISOLATED_CLASS') if certified else 'NOT_APPLICABLE_CLASS' if not_applicable else 'BLOCKED',
            'adversarial':probe or {'status':'BLOCKED','reason':probes.get('reason','MISSING_ADVERSARIAL_PROBE')},
            'applications':applications,'productionCertified':False})
    good='PASS_INTEGRATED_CLASS' if candidate_mode=='integrated' else 'PASS_ISOLATED_CLASS'
    all_good=(any(x['status']==good for x in class_results) and
              all(x['status'] in (good,'NOT_APPLICABLE_CLASS') for x in class_results))
    return {'schema':'mcel.signal-consumer-certification.v1',
        'generatedAt':datetime.now(timezone.utc).isoformat(),
        'status':('PASS_INTEGRATED_CERTIFICATION' if candidate_mode=='integrated' else
                  'PASS_ISOLATED_CERTIFICATION') if all_good else 'BLOCKED',
        'candidateMode':candidate_mode,
        'classes':class_results,
        'boundaries':{'productionSchedulerChanged':False,
            'productionCutoverAuthorized':False,'realHostAcceptance':'NOT_TESTED',
            'strictPixelParityCertified':False,
            'scope':('NORMAL_APPLICATION_MOUNT_WITH_ISOLATED_CHROMIUM_FIXTURES'
                     if candidate_mode=='integrated' else 'ISOLATED_CHROMIUM_AND_NODE_ADVERSARIAL_FIXTURES')},
        'adversarialStatus':probes.get('status','BLOCKED')}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',choices=('slh','task-manager','both'),default='both')
    parser.add_argument('--candidate-mode',choices=('isolated','integrated'),default='isolated')
    parser.add_argument('--class',dest='kind',choices=EXPERIMENTAL_KINDS+('all',),default='all')
    parser.add_argument('--output',type=Path,default=DEFAULT)
    args=parser.parse_args(argv)
    apps=('slh','task-manager') if args.app=='both' else (args.app,)
    kinds=EXPERIMENTAL_KINDS if args.kind=='all' else (args.kind,)
    report=certify(apps=apps,kinds=kinds,candidate_mode=args.candidate_mode)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf8')
    for entry in report['classes']:
        print('MCEL_SIGNAL_CLASS',entry['class'],entry['status'],
          ' '.join(f"{x['app']}={x['status']}:{x['actualSkips']}" for x in entry['applications']),flush=True)
    print('MCEL_SIGNAL_CONSUMER_CERTIFICATION',report['status'],'report:',args.output,flush=True)
    return 0 if report['status'] in ('PASS_ISOLATED_CERTIFICATION','PASS_INTEGRATED_CERTIFICATION') else 1

if __name__=='__main__':
    raise SystemExit(main())
