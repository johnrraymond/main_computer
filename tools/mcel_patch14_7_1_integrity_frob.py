"""Patch 14.7.1: independent browser checks of negotiated presentation integrity.

Run: python -m tools.mcel_patch14_7_1_integrity_frob
Runs read-only offline Task Manager fixtures. Never promotes production routes.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_patch14_6_promise_verification_frob import scenario
from tools.mcel_patch14_7_capability_negotiation_frob import SCRIPTS, _chrome
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'runtime/reports/mcel-patch14-7-1-integrity'
SCHEMA='mcel.patch14.7.1-contract-integrity.v1'

BROWSER=r'''async ({plan})=>{
 const node=document.createElement('div');document.querySelector('#task-manager-app').append(node);
 const task=structuredClone(plan);
 task.collection.spec.sourceFields.push('verifiedGen');
 task.collection.spec.reconciliation={generationFrom:'verifiedGen'};
 task.collection.rows.forEach(r=>r.verifiedGen='G1');
 const copied=[];
 Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async s=>{copied.push(s);}}});
 const m=McelUniversalSemanticLayoutLab.mount(node,task,{lab:true,presentationNegotiation:true});
 const rows=[...node.querySelectorAll('mcel-row')];
 const id=rows[0].dataset.rowId;
 const selection=m.collection.request(id,'onSelect');
 const inline=rows[0].querySelector('[data-mcel-presentation-slot="inline-detail"]');
 const layoutValid=!!m.collection.layout;
 const treeValid=m.tree.refresh().status==='pass';
 // Forged auxiliary content must continue to be rejected, even in negotiated mode.
 const fake=document.createElement('section');fake.dataset.mcelPresentationSlot='inline-detail';
 fake.dataset.sourceRowId='wrong-row';fake.dataset.presentation='inline-expand';
 fake.className='mcel-semantic-collection__inline-detail';
 rows[0].append(fake);
 const rejectsForged=m.collection.layout==null;
 fake.remove();
 const validAfterForged=!!m.collection.layout;
 const updated=structuredClone(task);
 updated.collection.rows[0].name='Updated process 14.7.1';
 const update=m.update(updated);
 const name1=inline?.querySelector('h3')?.textContent||'';
 const rawButton=inline?.querySelector('[aria-label="Copy complete source record"]');
 rawButton?.click();await Promise.resolve();await Promise.resolve();
 const copyUpdated=copied.at(-1)===JSON.stringify(updated.collection.rows[0],null,2);
 const selectionRange=document.createRange();
 const heading=inline.querySelector('h3');selectionRange.selectNodeContents(heading);
 const browserSelection=document.getSelection();browserSelection.removeAllRanges();browserSelection.addRange(selectionRange);
 const updated2=structuredClone(updated);
 updated2.collection.rows[0].name='Newest process 14.7.1';
 const updateWhileSelecting=m.update(updated2);
 const deferred=inline.querySelector('.mcel-semantic-collection__detail-content')?.dataset.updatePending==='true' &&
   inline.querySelector('h3').textContent===name1;
 browserSelection.removeAllRanges();
 document.dispatchEvent(new Event('selectionchange'));
 await new Promise(resolve=>setTimeout(resolve,20));
 const refreshedAfterSelection=inline.querySelector('h3')?.textContent?.includes('Newest process 14.7.1') &&
   !inline.querySelector('.mcel-semantic-collection__detail-content')?.dataset.updatePending;
 const hover=m.collection.request(id,'onHover');
 const pane=m.collection.floatingCard;
 const updated3=structuredClone(updated2);
 updated3.collection.rows[0].name='Floating process 14.7.1';
 const overlayUpdate=m.update(updated3);
 const overlayRetained=!!pane && pane===m.collection.floatingCard &&
   pane.querySelector('h3')?.textContent?.includes('Floating process 14.7.1') &&
   !!pane.querySelector('[aria-label="Expand inline"]');
 const noErrors=!!m.collection.layout && m.tree.refresh().status==='pass';
 m.destroy();node.remove();

 // Denial after one committed expansion must leave that presentation unchanged.
 const block=document.createElement('div');document.querySelector('#task-manager-app').append(block);
 const capabilities={allowResize:true,allowScroll:true,allowOverlay:true};
 const policy={schema:'mcel.element-presentation-policy.v1',events:{onSelect:[
  {mode:'inline-expand',requires:['geometry.child-resize','scroll.reveal']},
  {mode:'floating-over-source',requires:['overlay.anchor-source']}
 ]}};
 const q=McelUniversalSemanticLayoutLab.mount(block,task,{lab:true,presentationNegotiation:true,
  capabilities,presentationPolicy:policy});
 const qRows=[...block.querySelectorAll('mcel-row')];
 const firstId=qRows[0].dataset.rowId,lastId=qRows.at(-1).dataset.rowId;
 const initial=q.collection.request(firstId,'onSelect');
 capabilities.allowScroll=false;capabilities.allowOverlay=false;
 const denied=q.collection.request(lastId,'onSelect');
 const deniedKeepsCommitted=initial.ok && !denied.ok &&
   q.collection.expandedId===firstId && q.collection.inspectedId===firstId &&
   !!qRows[0].querySelector('.mcel-semantic-collection__inline-detail') &&
   !qRows.at(-1).querySelector('.mcel-semantic-collection__inline-detail') &&
   !q.collection.floatingCard;
 q.destroy();block.remove();

 // Force the scroll *execution* to fail after DOM staging, not just assessment.
 const block2=document.createElement('div');document.querySelector('#task-manager-app').append(block2);
 const f=McelUniversalSemanticLayoutLab.mount(block2,task,{lab:true,presentationNegotiation:true});
 const list=block2.querySelector('mcel-collection'), fRows=[...list.querySelectorAll('mcel-row')];
 f.collection.request(fRows[0].dataset.rowId,'onSelect');
 Object.defineProperty(list,'scrollTop',{configurable:true,get(){return 0;},set(_v){}});
 const attempted=f.collection.request(fRows.at(-1).dataset.rowId,'onSelect');
 const fallbackConsistent=attempted.ok && attempted.mode==='floating-over-source' &&
   attempted.attempts[0].status==='execution-failed' &&
   f.collection.expandedId===null && f.collection.inspectedId===fRows.at(-1).dataset.rowId &&
   !list.querySelector('.mcel-semantic-collection__inline-detail') &&
   !!f.collection.floatingCard;
 f.destroy();block2.remove();

 // Structural anchoring is measured against the original visible child.
 const container=document.createElement('div');
 container.style.cssText='height:150px;width:240px;overflow:auto;display:block';
 for(let i=0;i<6;i++){
   const r=document.createElement('div');r.dataset.rowId='row'+i;
   r.style.cssText='height:90px;min-height:90px;box-sizing:border-box';r.textContent='Row '+i;container.append(r);
 }
 document.querySelector('#task-manager-app').append(container);
 const sh=McelPresentationHosts.createScrollHost(container);
 container.scrollTop=125;
 const anchor=sh.captureAnchor();
 const original=[...container.children].find(x=>x.dataset.rowId===anchor.id);
 const before=original.getBoundingClientRect().top;
 const extra=document.createElement('div');extra.dataset.rowId='extra';
 extra.style.cssText='height:65px;min-height:65px';container.prepend(extra);
 const restored=sh.restoreAnchor(anchor);
 const anchorStable=restored.ok && Math.abs(original.getBoundingClientRect().top-before)<2;
 sh.destroy();container.remove();
 return {selection:selection.ok,inline:!!inline,layoutValid,treeValid,rejectsForged,validAfterForged,
  verifiedUpdate:update.ok,nameUpdated:name1.includes('Updated process 14.7.1'),copyUpdated,
  deferred,updateWhileSelecting:updateWhileSelecting.ok,refreshedAfterSelection,
  hoverOpened:hover.ok,overlayUpdated:overlayUpdate.ok,overlayRetained,noErrors,
  deniedKeepsCommitted,fallbackConsistent,anchorStable};
}'''


def verify(output:Path=OUTPUT)->dict:
    output.mkdir(parents=True,exist_ok=True)
    report={'schema':SCHEMA,'generatedUtc':datetime.now(timezone.utc).isoformat(),
            'readOnly':True,'productionPromotion':False,'cases':[]}
    with sync_playwright() as playwright:
        browser=_chrome(playwright,None)
        try:
            for state in ('normal','stressed'):
                for width in (390,1440):
                    page=browser.new_page(viewport={'width':width,'height':850},reduced_motion='reduce')
                    errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    try:
                        page.set_content(html_fixture(),wait_until='load')
                        for path in ('mcel-presentation-negotiation.js','mcel-presentation-hosts.js',
                                     'mcel-layout-continuity.js','mcel-universal-semantic-layout-lab.js'):
                            page.add_script_tag(path=str(SCRIPTS/path))
                        result=page.evaluate(BROWSER,{'plan':scenario(state)['plan']})
                        result.update(state=state,width=width,browserErrors=errors)
                        result['status']='PASS' if not errors and all(v is True for k,v in result.items()
                                             if k not in ('state','width','browserErrors','status')) else 'FAIL'
                        report['cases'].append(result)
                    finally:page.close()
        finally:browser.close()
    report['status']='PASS' if all(c['status']=='PASS' for c in report['cases']) else 'FAIL'
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('MCEL_PATCH14_7_1_'+('ENGINEERING_PASS' if report['status']=='PASS' else 'ENGINEERING_FAIL'))
    for case in report['cases']:
        print(f"{case['state']} {case['width']}: {case['status']}")
        if case['status']!='PASS': print('FAILURES', {k:v for k,v in case.items() if v is False or k=='browserErrors'})
    print('report:',output/'report.json')
    return report

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    args=parser.parse_args()
    raise SystemExit(0 if verify(args.output)['status']=='PASS' else 1)
