"""14.7.3: source disappearance, visible retirement, native-selection protection.

Offline Chromium against existing Task Manager semantic plan. No production mutation.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
from datetime import datetime,timezone
from playwright.sync_api import sync_playwright
from tools.mcel_patch14_6_promise_verification_frob import scenario
from tools.mcel_patch14_7_capability_negotiation_frob import SCRIPTS,_chrome
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'runtime/reports/mcel-patch14-7-3-retirement'
SCHEMA='mcel.patch14.7.3-element-retirement-verification.v1'

BROWSER=r'''async ({plan})=>{
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const root=document.createElement('div');document.querySelector('#task-manager-app').append(root);
  const task=structuredClone(plan);
  task.collection.spec.sourceFields.push('verifiedGen');
  task.collection.spec.reconciliation={generationFrom:'verifiedGen'};
  task.collection.rows.forEach((r,i)=>r.verifiedGen='generation:'+i);
  const policy={schema:'mcel.element-retirement-policy.v1',visible:'timed',offscreen:'remove',
    delayMs:120,pauseWhen:['text-selected'],onPause:'reset',maxRetained:50};
  const m=McelUniversalSemanticLayoutLab.mount(root,task,{lab:true,presentationNegotiation:true,
    sourceRetirement:true,retirementPolicy:policy});
  const rows=[...root.querySelectorAll('mcel-row')],first=rows[0],id=first.dataset.rowId;
  // Exact old value remains selectable when the source stops reporting this row.
  const value=first.querySelector('mcel-field .field-value');
  const selection=document.getSelection(),range=document.createRange();
  range.selectNodeContents(value);selection.removeAllRanges();selection.addRange(range);
  const preservedText=String(selection);
  const absent=structuredClone(task);absent.collection.rows.splice(0,1);
  const result=m.update(absent),oldRetained=first.isConnected &&
    first.dataset.mcelRetirementState==='protected' &&
    first.dataset.mcelSourceStatus==='retired' &&
    first.dataset.rowId!==id &&
    m.collection.ids.every(v=>v!==id) &&
    m.collection.retirement.ids.length===1 &&
    String(selection)===preservedText;
  const retiredKey=first.dataset.rowId;
  const layoutDuringRetirement=!!m.collection.layout && m.tree.refresh().status==='pass';
  await sleep(190);
  const protectedPastDeadline=first.isConnected && m.collection.retirement.ids.includes(retiredKey);
  // A new record may reuse a formerly held identifier. It must be distinct.
  const reused=structuredClone(absent),brandNew=structuredClone(task.collection.rows[0]);
  brandNew.verifiedGen='completely-new-generation';brandNew.name='REUSED ID';
  reused.collection.rows.unshift(brandNew);
  const newResult=m.update(reused),newNode=[...root.querySelectorAll('mcel-row')]
    .find(n=>n.dataset.rowId===id);
  const identitySeparated=newResult.ok && newNode && newNode!==first &&
    first.isConnected && first.dataset.mcelRetirementState==='protected' &&
    first.dataset.mcelSourceStatus==='retired' &&
    m.collection.ids.includes(id) && m.collection.retirement.ids.includes(retiredKey);
  const copied=[];
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>copied.push(text)}});
  const archivalHover=m.collection.request(retiredKey,'onHover');
  m.collection.floatingCard?.querySelector('[aria-label="Copy complete source record"]')?.click();
  await Promise.resolve();await Promise.resolve();
  const archivalCopySafe=archivalHover.ok &&
    copied.at(-1)===JSON.stringify(task.collection.rows[0],null,2) &&
    !copied.at(-1).includes('REUSED ID');
  const selectionAfterReuse=String(selection),retirementBeforeRelease=m.collection.retirement.entries[0];
  selection.removeAllRanges();document.dispatchEvent(new Event('selectionchange'));
  await sleep(60);
  const resetNotInstant=retirementBeforeRelease.visibleAtRetirement?first.isConnected:!first.isConnected;
  await sleep(160);
  const resetEventuallyRemoved=!first.isConnected && m.collection.retirement.ids.length===0 &&
    !!m.collection.layout && m.tree.refresh().status==='pass' && newNode.isConnected;
  m.destroy();root.remove();

  // A selection-held old value may already be pending before disappearance.
  // Retirement must preserve the newest authoritative snapshot while allowing
  // the user to finish copying the old selected evidence.
  const another=document.createElement('div');document.querySelector('#task-manager-app').append(another);
  const old=structuredClone(task);
  const selectedId=old.collection.rows[0].mcelIdentity||null;
  const extended={...policy,delayMs:1000,offscreen:'timed'};
  const other=McelUniversalSemanticLayoutLab.mount(another,old,{lab:true,presentationNegotiation:true,
    sourceRetirement:true,retirementPolicy:extended});
  const tracked=another.querySelector('mcel-row'),trackedId=tracked.dataset.rowId;
  const oldState=tracked.querySelector('mcel-field[data-semantic-field="state"] .field-value');
  const earlier=oldState.textContent;
  const oldRange=document.createRange();oldRange.selectNodeContents(oldState);
  selection.removeAllRanges();selection.addRange(oldRange);
  const changed=structuredClone(old);changed.collection.rows[0].state='NEWEST-STATE';
  changed.collection.rows[0].mcelCanonicalRecord.properties.state.raw='NEWEST-STATE';
  const changedReceipt=other.update(changed);
  const wasPending=changedReceipt.ok&&oldState.textContent===earlier &&
    oldState.closest('mcel-field').dataset.updatePending==='true';
  const disappeared=structuredClone(changed);disappeared.collection.rows.splice(0,1);
  const gone=other.update(disappeared),archivedId=tracked.dataset.rowId;
  const pendingDuringRetirement=gone.ok && tracked.isConnected &&
    tracked.dataset.mcelSourceStatus==='retired' && oldState.textContent===earlier &&
    !!other.collection.layout && other.collection.ids.every(x=>x!==trackedId);
  selection.removeAllRanges();document.dispatchEvent(new Event('selectionchange'));
  await sleep(60);
  const archivedCaughtUp=oldState.textContent==='NEWEST-STATE' &&
    !oldState.closest('mcel-field').dataset.updatePending &&
    tracked.isConnected && other.collection.retirement.ids.includes(archivedId) &&
    !!other.collection.layout;
  other.destroy();another.remove();

  // A separate direct-engine test can control a clock without real waiting.
  const box=document.createElement('div');box.style.cssText='height:250px;overflow:auto';
  document.body.append(box);
  function timer(mode){
    let clock=0,protect=false,removed=false;
    const item=document.createElement('div');item.textContent='snapshot';box.append(item);
    const mgr=McelElementRetirement.create({doc:document,list:box,now:()=>clock,
      visibility:()=>({top:0,bottom:-10,available:100}),interactionFlags:()=>protect?['focused']:[],
      policy:{schema:'mcel.element-retirement-policy.v1',visible:'timed',offscreen:'remove',
        delayMs:100,pauseWhen:['focused'],onPause:'reset',maxRetained:10},
      onRemove:()=>{removed=true;item.remove();}});
    mgr.retire('x',item,{policy:{schema:'mcel.element-retirement-policy.v1',
      visible:'timed',offscreen:'remove',delayMs:100,pauseWhen:['focused'],
      onPause:mode,maxRetained:10}});clock=60;protect=true;mgr.tick();
    clock=120;mgr.tick();const held=!removed;
    protect=false;mgr.tick();clock=149;mgr.tick();const before=removed;
    clock=161;mgr.tick();const after=removed;
    clock=221;mgr.tick();const final=removed;
    mgr.destroy();item.remove();
    return {held,before,after,final};
  }
  const reset=timer('reset'),resume=timer('resume'),continuous=timer('continue');
  box.remove();
  const resetClock=reset.held&&!reset.before&&!reset.after&&reset.final;
  const resumeClock=resume.held&&!resume.before&&resume.after;
  const continueClock=continuous.held&&continuous.before&&continuous.after;
  // Policy validation must refuse undocumented timer behaviors.
  let invalidPolicyRejected=false;
  try{McelElementRetirement.validate({...policy,onPause:'magical'});}catch(_){invalidPolicyRejected=true;}
  return {disappearanceApplied:result.ok,oldRetained,layoutDuringRetirement,protectedPastDeadline,
    identitySeparated,archivalCopySafe,selectionAfterReuse,retirementBeforeRelease,resetNotInstant,resetEventuallyRemoved,
    wasPending,pendingDuringRetirement,archivedCaughtUp,
    resetClock,resumeClock,continueClock,invalidPolicyRejected};
}'''

def verify(output:Path=OUTPUT)->dict:
    output.mkdir(parents=True,exist_ok=True)
    report={'schema':SCHEMA,'generatedUtc':datetime.now(timezone.utc).isoformat(),
            'productionPromotion':False,'readOnly':True,'cases':[]}
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for state in ('normal','stressed'):
                for width in (390,1440):
                    page=browser.new_page(viewport={'width':width,'height':850},reduced_motion='reduce')
                    errors=[];page.on('pageerror',lambda exc:errors.append(str(exc)))
                    try:
                        page.set_content(html_fixture(),wait_until='load')
                        for name in ('mcel-presentation-negotiation.js','mcel-presentation-hosts.js',
                                     'mcel-layout-continuity.js','mcel-universal-semantic-layout-lab.js'):
                            page.add_script_tag(path=str(SCRIPTS/name))
                        data=page.evaluate(BROWSER,{'plan':scenario(state)['plan']})
                        data.update(state=state,width=width,browserErrors=errors)
                        data['status']='PASS' if not errors and all(v is True for k,v in data.items()
                            if k not in ('state','width','browserErrors','status','selectionAfterReuse','retirementBeforeRelease')) else 'FAIL'
                        report['cases'].append(data)
                    finally:page.close()
        finally:browser.close()
    report['status']='PASS' if all(case['status']=='PASS' for case in report['cases']) else 'FAIL'
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('MCEL_PATCH14_7_3_'+('ENGINEERING_PASS' if report['status']=='PASS' else 'ENGINEERING_FAIL'))
    for case in report['cases']:
        print(case['state'],case['width'],case['status'],{k:v for k,v in case.items() if v is False})
        if case['browserErrors']:print('BROWSER_ERRORS',case['browserErrors'])
    print('report:',output/'report.json')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=OUTPUT)
    args=parser.parse_args();raise SystemExit(0 if verify(args.output)['status']=='PASS' else 1)
