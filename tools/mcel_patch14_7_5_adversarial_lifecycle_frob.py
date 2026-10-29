"""MCEL 14.7.5: deterministic adversarial observation + presentation lifecycle Frob.

Run: python -m tools.mcel_patch14_7_5_adversarial_lifecycle_frob
No production routes, network effects, or operational process controls are used.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_patch14_6_promise_verification_frob import scenario
from tools.mcel_patch14_7_capability_negotiation_frob import SCRIPTS, _chrome
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

SCHEMA='mcel.patch14.7.5-adversarial-lifecycle.v1'
ROOT=Path(__file__).resolve().parents[1]

PROBE=r'''({plan,seed,steps})=>{
 const root=document.querySelector('#task-manager-app');
 const host=document.createElement('div');root.append(host);
 const data=structuredClone(plan);
 data.collection.spec.sourceFields.push('verifiedGen');
 data.collection.spec.reconciliation={generationFrom:'verifiedGen'};
 data.collection.rows.forEach((row,i)=>row.verifiedGen='G'+i);
 const originals=new Map(data.collection.rows.map(row=>[row.mcelIdentity,structuredClone(row)]));
 const current=new Map([...originals].map(([id,row])=>[id,structuredClone(row)]));
 const policy={schema:'mcel.element-retirement-policy.v1',visible:'timed',offscreen:'timed',
   delayMs:120000,pauseWhen:['text-selected','hovered','focused','expanded'],onPause:'reset',maxRetained:50};
 const m=McelUniversalSemanticLayoutLab.mount(host,data,{lab:true,presentationNegotiation:true,
   sourceRetirement:true,retirementPolicy:policy});
 let seq=0,rand=seed>>>0,selectedLeaf=null,selectedValue=null;
 const nextRand=()=>{rand^=rand<<13;rand^=rand>>>17;rand^=rand<<5;return rand>>>0;};
 const events=['partial','partial','complete','selection','native-selection','clear-selection',
   'hover','scroll','duplicate','stale','remove','complete-remove','reappear'];
 const trace=[];let failure=null;
 const snapshot=rows=>{const n=structuredClone(data);n.collection.rows=rows.map(v=>structuredClone(v));return n;};
 const runUpdate=(rows,complete,removals=[],expectedOk=true,forcedSeq=null)=>{
   const observation={schema:'mcel.source-observation.v1',complete,sequence:forcedSeq??++seq,removals};
   const result=m.update(snapshot(rows),{observation});
   if(result.ok!==expectedOk)throw Error('unexpected update result '+JSON.stringify(result));
   if(!expectedOk && result.effects?.length)throw Error('rejected update claimed effects');
   return result;
 };
 // Adopt an explicit ordered baseline without creating new retirements.
 try{
   const init=runUpdate([...current.values()],true);
   trace.push({step:-1,event:'ordered-baseline',result:init.ok});
   for(let i=0;i<steps;i++){
     const kind=events[nextRand()%events.length],ids=[...current.keys()],target=ids[nextRand()%ids.length];
     const record=current.get(target),before=[...current.keys()].sort().join('|');
     let response={ok:true},note='';
     if(kind==='partial'&&record){
       const changed=structuredClone(record);changed.name='update-'+seed+'-'+i;
       if(changed.mcelCanonicalRecord?.properties?.name)
         changed.mcelCanonicalRecord.properties.name.raw=changed.name;
       response=runUpdate([changed],false);
       current.set(target,changed);
     }else if(kind==='complete'){
       response=runUpdate([...current.values()].reverse(),true);
     }else if(kind==='remove'&&current.size>1){
       response=runUpdate([],false,[target]);current.delete(target);
     }else if(kind==='complete-remove'&&current.size>1){
       const remaining=[...current.values()].filter(row=>row.mcelIdentity!==target);
       response=runUpdate(remaining,true);current.delete(target);
     }else if(kind==='reappear'&&current.size<originals.size){
       const missing=[...originals.keys()].find(id=>!current.has(id));
       const revived=structuredClone(originals.get(missing));revived.verifiedGen='revived-'+i;
       response=runUpdate([revived],false);current.set(missing,revived);
     }else if(kind==='duplicate'){
       response=runUpdate([...current.values()],true,[],false,seq);
     }else if(kind==='stale'){
       response=runUpdate([],true,[],false,Math.max(0,seq-1));
     }else if(kind==='selection'&&target){
       document.getSelection()?.removeAllRanges();selectedLeaf=null;selectedValue=null;
       response=m.collection.request(target,'onSelect');
       if(!response.ok)throw Error('selection request refused '+response.reason);
     }else if(kind==='native-selection'&&target){
       const row=[...host.querySelectorAll('mcel-row')].find(r=>r.dataset.rowId===target);
       const leaf=row?.querySelector('mcel-field .field-value');
       if(leaf){const range=document.createRange();range.selectNodeContents(leaf);
         const selection=document.getSelection();selection.removeAllRanges();selection.addRange(range);
         selectedLeaf=leaf;selectedValue=selection.toString();
       }
     }else if(kind==='clear-selection'){
       document.getSelection()?.removeAllRanges();selectedLeaf=null;selectedValue=null;
     }else if(kind==='hover'&&target){
       response=m.collection.request(target,'onHover');
       if(!response.ok)throw Error('hover request refused '+response.reason);
     }else if(kind==='scroll'){
       const list=host.querySelector('mcel-collection');
       list.scrollTop+=24;list.dispatchEvent(new WheelEvent('wheel',{bubbles:true}));
     }else note='skipped';
     // Oracle checks after EACH event, including refused/out-of-order observations.
     const actual=[...m.collection.ids].sort().join('|');
     const expected=[...current.keys()].sort().join('|');
     if(actual!==expected)throw Error('source membership divergence: '+actual+' vs '+expected);
     for(const rec of m.collection.source){
       const row=current.get(rec.id);
       if(!row)throw Error('fabricated source identity '+rec.id);
       if(rec.name!==row.name)throw Error('stale accepted source value for '+rec.id);
     }
     if(selectedLeaf?.isConnected && !document.getSelection()?.isCollapsed &&
       document.getSelection().anchorNode &&
       document.getSelection().containsNode(selectedLeaf,true) &&
       document.getSelection().toString()!==selectedValue)
       throw Error('native selected evidence changed during active selection');
     const retired=m.collection.retirement.ids;
     if(retired.some(id=>!id.startsWith('retired:')))throw Error('invalid retirement identity');
     if(host.querySelectorAll('mcel-row').length!==current.size+retired.length)
       throw Error('presentation membership mismatch');
     if(!m.collection.layout||m.tree.refresh().status!=='pass')
       throw Error('layout authority rejected accepted lifecycle');
     trace.push({step:i,event:kind,note,ok:response.ok,live:current.size,
       retired:retired.length,sourceBefore:before,sourceAfter:expected});
   }
 }catch(error){failure={message:String(error),stack:String(error.stack||''),at:trace.length};}
 const end={live:m.collection.ids.length,retired:m.collection.retirement.ids.length,
   dom:host.querySelectorAll('mcel-row').length};
 document.getSelection()?.removeAllRanges();m.destroy();host.remove();
 return {seed,steps,pass:!failure,failure,end,trace};
}'''

TRANSACTION=r'''()=>{
 const N=McelPresentationNegotiation;
 const policy={schema:N.SCHEMA,events:{onSelect:[
  {mode:'inline-expand',requires:['geometry.child-resize']},
  {mode:'floating-over-source',requires:['overlay.anchor-source']}]}};
 const provider={assess:()=>({ok:true})};
 let mutated=false,rolledBack=0,attempt=0;
 const outcome=N.negotiate(policy,'onSelect',{
  'geometry.child-resize':provider,'overlay.anchor-source':provider},
  {beginTransaction:()=>N.createTransaction()},(choice,{transaction})=>{
    if(attempt++===0){transaction.addUndo(()=>{mutated=false;rolledBack++});mutated=true;
      throw Error('injected post-mutation exception');}
    if(mutated)throw Error('fallback witnessed unrolled state');
    return {ok:true};
  });
 const fail=N.negotiate(policy,'onSelect',{
  'geometry.child-resize':provider,'overlay.anchor-source':provider},
  {beginTransaction:()=>N.createTransaction()},(_,{transaction})=>{
    transaction.addUndo(()=>{throw Error('unwind-failed')});
    throw Error('failure');
  });
 return {ok:outcome.ok,mode:outcome.mode,rolledBack,mutated,
   statuses:outcome.attempts.map(a=>a.status),failClosed:fail.reason==='rollback-failed',
   noFallbackAfterRollbackError:fail.attempts.length===1};
}'''

def verify(output:Path, *, steps:int=55, seeds:tuple[int,...]=(1,7,57)) -> dict:
    output.mkdir(parents=True,exist_ok=True)
    report={'schema':SCHEMA,'generatedUtc':datetime.now(timezone.utc).isoformat(),
      'readOnly':True,'productionPromotion':False,'cases':[],'errors':[],
      'limitations':['Sequential deterministic browser interleavings, not concurrent workers',
        'No virtualized-list mount/unmount host in this opt-in Task Manager laboratory',
        'No production Task Manager or network operations']}
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for state in ('normal','stressed'):
                plan=scenario(state)['plan']
                for width in (390,1440):
                    page=browser.new_page(viewport={'width':width,'height':850},reduced_motion='reduce')
                    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    try:
                        page.set_content(html_fixture(),wait_until='load')
                        for name in ('mcel-presentation-negotiation.js','mcel-presentation-hosts.js',
                                     'mcel-layout-continuity.js','mcel-universal-semantic-layout-lab.js'):
                            page.add_script_tag(path=str(SCRIPTS/name))
                        tx=page.evaluate(TRANSACTION)
                        report['cases'].append({'case':'transaction-exception', 'state':state,'width':width,
                            'pass':tx['ok'] and tx['mode']=='floating-over-source' and tx['rolledBack']==1
                             and not tx['mutated'] and tx['failClosed'] and tx['noFallbackAfterRollbackError'],
                            'result':tx})
                        for seed in seeds:
                            r=page.evaluate(PROBE,{'plan':plan,'seed':seed,'steps':steps})
                            report['cases'].append({'case':'stateful-lifecycle','state':state,'width':width,
                                'seed':seed,'pass':r['pass'],'result':r})
                    except Exception as exc:
                        report['cases'].append({'case':'browser-exception','state':state,'width':width,
                            'pass':False,'reason':str(exc)})
                    finally:
                        report['errors'] += errors
                        page.close()
        finally:browser.close()
    report['status']='PASS' if report['cases'] and all(c['pass'] for c in report['cases']) and not report['errors'] else 'FAIL'
    dest=output/'report.json';dest.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('MCEL_PATCH14_7_5_'+('ENGINEERING_PASS' if report['status']=='PASS' else 'ENGINEERING_FAIL'))
    for c in report['cases']:
        print(c['case'],c['state'],c['width'],c.get('seed',''),
              'PASS' if c['pass'] else 'FAIL',c['result'].get('failure') if not c['pass'] and 'result' in c else '')
    print('report:',dest.resolve())
    return report

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,
        default=ROOT/'runtime/reports/mcel-patch14-7-5-adversarial-lifecycle')
    parser.add_argument('--steps',type=int,default=55)
    args=parser.parse_args()
    if verify(args.output,steps=args.steps)['status']!='PASS':raise SystemExit(2)
if __name__=='__main__':main()
