"""MCEL 14.7.2: browser-level selection-aware mutation and semantic anchors.

Read-only offline Task Manager semantic fixtures, no production promotion.
Run: python -m tools.mcel_patch14_7_2_selection_continuity_frob
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_patch14_6_promise_verification_frob import scenario
from tools.mcel_patch14_7_capability_negotiation_frob import SCRIPTS, _chrome
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'runtime/reports/mcel-patch14-7-2-selection-continuity'
SCHEMA='mcel.patch14.7.2-selection-continuity-verification.v1'
BROWSER=r'''async ({plan})=>{
  const mountPoint=document.createElement('div');document.querySelector('#task-manager-app').append(mountPoint);
  const task=structuredClone(plan);
  task.collection.spec.sourceFields.push('verifiedGen');
  task.collection.spec.reconciliation={generationFrom:'verifiedGen'};
  task.collection.rows.forEach(row=>row.verifiedGen='G1');
  const copied=[];
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async s=>copied.push(s)}});
  const m=McelUniversalSemanticLayoutLab.mount(mountPoint,task,{lab:true,presentationNegotiation:true});
  const row=mountPoint.querySelector('mcel-row'),id=row.dataset.rowId;
  const start=m.collection.request(id,'onSelect');
  const surface=row.querySelector('.mcel-semantic-collection__detail-content');
  const value=surface.querySelector('[data-mcel-selection-key="value:state"]');
  const heading=surface.querySelector('[data-mcel-selection-key="heading"]');
  const source=surface.querySelector('[data-mcel-selection-key="raw-value:state"]');
  const stateBefore=value.textContent,originalNode=value.firstChild;
  const selection=document.getSelection(),r=document.createRange();r.selectNodeContents(value);
  selection.removeAllRanges();selection.addRange(r);
  const selectedBefore=String(selection);
  const snapshot=McelSelectionContinuity.capture(surface);
  const next=structuredClone(task);
  next.collection.rows[0].state='paused';next.collection.rows[0].name='Updated while selected';
  next.collection.rows[0].mcelCanonicalRecord.properties.state.raw='paused';
  next.collection.rows[0].mcelCanonicalRecord.properties.name.raw='Updated while selected';
  const updated=m.update(next);
  const selectedPreserved=String(selection)===selectedBefore && value.firstChild===originalNode &&
    value.textContent===stateBefore;
  const unrelatedUpdated=heading.textContent.includes('Updated while selected') &&
    source.textContent==='paused';
  const staleIndicated=surface.dataset.updatePending==='true' &&
    !!surface.querySelector('.mcel-semantic-collection__selection-stale');
  const copy=surface.querySelector('[aria-label="Copy complete source record"]');copy.click();
  await Promise.resolve();await Promise.resolve();
  const copiedLatest=copied.at(-1)===JSON.stringify(next.collection.rows[0],null,2);
  const latest=structuredClone(next);latest.collection.rows[0].state='stopped';
  latest.collection.rows[0].mcelCanonicalRecord.properties.state.raw='stopped';
  const nextUpdate=m.update(latest);
  const latestDeferred=nextUpdate.ok && value.textContent===stateBefore &&
    source.textContent==='stopped';
  selection.removeAllRanges();document.dispatchEvent(new Event('selectionchange'));
  await new Promise(resolve=>setTimeout(resolve,15));
  const caughtUp=value.textContent==='stopped' && !surface.dataset.updatePending &&
    !surface.querySelector('.mcel-semantic-collection__selection-stale');
  // The ordinary list row is also selectable, not only its detail card.
  const rowValue=row.querySelector('mcel-field[data-semantic-field="state"] .field-value');
  const rowPrevious=rowValue.textContent,rowOriginalNode=rowValue.firstChild;
  const rowRange=document.createRange();rowRange.selectNodeContents(rowValue);
  selection.removeAllRanges();selection.addRange(rowRange);
  const rowSelection=String(selection);
  const rowChange=structuredClone(latest);rowChange.collection.rows[0].state='waiting';
  rowChange.collection.rows[0].mcelCanonicalRecord.properties.state.raw='waiting';
  const rowUpdate=m.update(rowChange);
  const rowPreserved=rowUpdate.ok && rowValue.firstChild===rowOriginalNode &&
    rowValue.textContent===rowPrevious && String(selection)===rowSelection &&
    rowValue.closest('mcel-field')?.dataset.updatePending==='true' &&
    !!m.collection.layout && m.tree.refresh().status==='pass';
  selection.removeAllRanges();document.dispatchEvent(new Event('selectionchange'));
  await new Promise(resolve=>setTimeout(resolve,15));
  const rowCaughtUp=rowValue.textContent==='waiting' &&
    !rowValue.closest('mcel-field')?.dataset.updatePending && !!m.collection.layout;
  // A forged stale-field flag without an actual native selection is refused.
  const staleField=rowValue.closest('mcel-field'),goodValue=rowValue.textContent;
  rowValue.textContent='forged old data';rowValue.dataset.updatePending='true';
  rowValue.dataset.retainedText='forged old data';rowValue.dataset.pendingCurrent=goodValue;
  staleField.dataset.updatePending='true';
  const forgedStaleRejected=m.collection.layout==null;
  rowValue.textContent=goodValue;
  delete rowValue.dataset.updatePending;delete rowValue.dataset.retainedText;
  delete rowValue.dataset.pendingCurrent;delete staleField.dataset.updatePending;
  const forgedCleaned=!!m.collection.layout;
  // Floating presentations use exactly the same selection mutation service.
  const hover=m.collection.request(id,'onHover'),pane=m.collection.floatingCard;
  const floatingHeading=pane?.querySelector('[data-mcel-selection-key="heading"]');
  const overlayBefore=floatingHeading?.textContent;
  const overlayRange=document.createRange();overlayRange.selectNodeContents(floatingHeading);
  selection.removeAllRanges();selection.addRange(overlayRange);
  const overlaySelection=String(selection),originalHeadingNode=floatingHeading.firstChild;
  const floatChange=structuredClone(rowChange);
  floatChange.collection.rows[0].name='Floating updated during selection';
  floatChange.collection.rows[0].state='ready';
  floatChange.collection.rows[0].mcelCanonicalRecord.properties.name.raw='Floating updated during selection';
  floatChange.collection.rows[0].mcelCanonicalRecord.properties.state.raw='ready';
  const floatUpdated=m.update(floatChange);
  const floatSelectionProtected=hover.ok && floatUpdated.ok && pane===m.collection.floatingCard &&
    floatingHeading.firstChild===originalHeadingNode && floatingHeading.textContent===overlayBefore &&
    String(selection)===overlaySelection &&
    pane.querySelector('[data-mcel-selection-key="value:state"]').textContent==='ready' &&
    !!pane.querySelector('.mcel-semantic-collection__selection-stale');
  selection.removeAllRanges();document.dispatchEvent(new Event('selectionchange'));
  await new Promise(resolve=>setTimeout(resolve,15));
  const floatCaughtUp=floatingHeading.textContent.includes('Floating updated during selection') &&
    !pane.querySelector('.mcel-semantic-collection__selection-stale');
  // Round-trip a selection across an intentionally replaced but semantically identical presentation.
  const roundtripRange=document.createRange();roundtripRange.setStart(heading.firstChild,9);
  roundtripRange.setEnd(heading.firstChild,16);
  selection.removeAllRanges();selection.addRange(roundtripRange);
  const saved=McelSelectionContinuity.capture(surface);
  const clone=surface.cloneNode(true),replacement=surface.parentElement;
  replacement.replaceChild(clone,surface);
  const restored=McelSelectionContinuity.restore(clone,saved);
  const stableTextRestored=!!saved && restored.ok && String(selection)===saved.selectedText;
  const edited=clone.cloneNode(true);
  edited.querySelector('[data-mcel-selection-key="heading"]').textContent='Not the same evidence';
  clone.replaceWith(edited);
  const priorSelection=String(selection);
  const changedDenied=!McelSelectionContinuity.restore(edited,saved).ok &&
    String(selection)===priorSelection;
  // Remapping a uniquely identifiable selected substring under a changed
  // prefix is permitted; ambiguous duplicate text must never be guessed.
  const rebased=clone.cloneNode(true);
  const rebaseHeading=rebased.querySelector('[data-mcel-selection-key="heading"]');
  rebaseHeading.textContent='Added prefix '+saved.anchor.text;
  document.body.append(rebased);
  const rebaseResult=McelSelectionContinuity.restore(rebased,saved);
  const uniqueRebase=rebaseResult.ok && String(selection)===saved.selectedText;
  const duplicate=rebased.cloneNode(true);
  duplicate.querySelector('[data-mcel-selection-key="heading"]').textContent=
    saved.anchor.text+' '+saved.selectedText+' '+saved.selectedText;
  rebased.replaceWith(duplicate);
  const beforeAmbiguity=String(selection);
  const ambiguousRefused=!McelSelectionContinuity.restore(duplicate,saved).ok &&
    String(selection)===beforeAmbiguity;
  duplicate.remove();selection.removeAllRanges();
  // A multi-field native selection should defer both changed leaves, not unrelated ones.
  const multi=edited.cloneNode(true),oldState=multi.querySelector('[data-mcel-selection-key="value:state"]');
  const multiHost=document.createElement('div');multiHost.append(multi);document.body.append(multiHost);
  const newer=multi.cloneNode(true);
  newer.querySelector('[data-mcel-selection-key="value:state"]').textContent='another-state';
  newer.querySelector('[data-mcel-selection-key="value:role"]').textContent='another-role';
  const a=multi.querySelector('[data-mcel-selection-key="value:state"]');
  const b=multi.querySelector('[data-mcel-selection-key="value:role"]');
  const multirange=document.createRange();multirange.setStart(a.firstChild,0);
  multirange.setEnd(b.firstChild,b.textContent.length);
  selection.removeAllRanges();selection.addRange(multirange);
  const multiBefore=String(selection);
  const receipt=McelSelectionContinuity.mutate(multi,newer);
  const multiProtected=receipt.deferred>=2 && a.textContent===oldState.textContent &&
    b.textContent!=='another-role' && String(selection)===multiBefore;
  selection.removeAllRanges();multiHost.remove();
  const layoutValid=!!m.collection.layout && m.tree.refresh().status==='pass';
  m.destroy();mountPoint.remove();
  // Independent semantic-anchor refusal when a record identity changes.
  const separate=document.createElement('div');separate.dataset.mcelSelectionScope='other-record';
  const text=document.createElement('span');text.dataset.mcelSelectionKey='heading';text.textContent=saved?.anchor?.text||'a';
  separate.append(text);document.body.append(separate);
  const identityDenied=!McelSelectionContinuity.restore(separate,saved).ok;
  separate.remove();
  return {opened:start.ok,updated:updated.ok,selectedPreserved,unrelatedUpdated,
    staleIndicated,copiedLatest,latestDeferred,caughtUp,rowPreserved,rowCaughtUp,forgedStaleRejected,forgedCleaned,
    floatSelectionProtected,floatCaughtUp,stableTextRestored,uniqueRebase,ambiguousRefused,
    changedDenied,multiProtected,identityDenied,layoutValid};
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
                    page.on('pageerror',lambda e:errors.append(str(e)))
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
    print('MCEL_PATCH14_7_2_'+('ENGINEERING_PASS' if report['status']=='PASS' else 'ENGINEERING_FAIL'))
    for case in report['cases']:
        print(case['state'],case['width'],case['status'],{k:v for k,v in case.items() if v is False})
        if case['browserErrors']:print('ERRORS',case['browserErrors'])
    print('report:',output/'report.json')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    args=parser.parse_args()
    raise SystemExit(0 if verify(args.output)['status']=='PASS' else 1)
