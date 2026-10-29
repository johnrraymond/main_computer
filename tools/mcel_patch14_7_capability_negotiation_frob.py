"""Patch 14.7: real Task Manager universal-capability acceptance (read-only).

    python -m tools.mcel_patch14_7_capability_negotiation_frob

Tests the actual MCEL v2 Task Manager collection inside the existing Task Manager
HTML fixture, not a mock process table or production operational controls.
No network, operator effects, or production promotion. Uses existing composer.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import json
from playwright.sync_api import sync_playwright
from tools.mcel_patch14_6_promise_verification_frob import scenario, save
from tools.mcel_patch13_live_adoption_frob import _chrome
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'main_computer/web/applications/scripts'
OUTPUT=ROOT/'runtime/reports/mcel-patch14-7-capability-negotiation'
SCHEMA='mcel.patch14.7-capability-negotiation.v1'
WIDTHS=(390,1440)
STATES=('normal','stressed')

# Exercise the real universal collection and semantics rather than a CSS facsimile.
PROBE=r'''async ({plan})=>{
  const root=document.querySelector('#task-manager-app');
  const host=document.createElement('div');host.id='patch147-root';root.append(host);
  const mounted=McelUniversalSemanticLayoutLab.mount(host,plan,{lab:true,presentationNegotiation:true});
  const list=host.querySelector('mcel-collection');
  const rows=[...host.querySelectorAll('mcel-row')];
  const actual=rows[0],id=actual.dataset.rowId;
  const first=plan.collection.rows[0];
  const initialHeight=actual.getBoundingClientRect().height;
  const summary=host.querySelector('#semantic-selected-details');
  const select=mounted.collection.request(id,'onSelect');
  const inline=actual.querySelector('.mcel-semantic-collection__inline-detail');
  const exact=inline?.querySelector('.mcel-semantic-collection__exact-evidence');
  const originalRecord=exact?.querySelector('.mcel-semantic-collection__raw-values');
  const dt=originalRecord&&[...originalRecord.querySelectorAll('dt')];
  const raw=dt && Object.fromEntries(dt.map(x=>[x.textContent,x.nextElementSibling.textContent]));
  const copy=inline?.querySelector('[aria-label="Copy complete source record"]');
  copy?.click();await Promise.resolve();await Promise.resolve();
  const copyMatches=window.__patch147copied===JSON.stringify(first,null,2);
  const inlineProof={ok:select.ok,mode:select.mode,expanded:!!inline,
    grew:actual.getBoundingClientRect().height>initialHeight+10,
    detailsInsideList:list.contains(inline),noExternalDetails:!summary?.querySelector('.mcel-semantic-collection__inspector'),
    canonicalMatch:raw?.mcelIdentity===first.mcelIdentity &&
      JSON.stringify(JSON.parse(raw?.mcelCanonicalRecord||'null'))===JSON.stringify(first.mcelCanonicalRecord),
    copyMatches,copyResult:copy?.dataset.copyResult,
    evidenceCollapsed:!!exact&&!exact.open};
  const selected=actual;
  const changed=structuredClone(plan);
  changed.collection.rows[0].name='changed-name-same-identity';
  const applied=mounted.update(changed);
  const continuity={accepted:applied.ok===true,
     // No generation token in the Task Manager fixture: fail closed on identity continuity.
     refusesUnprovenIdentity:host.querySelector('mcel-row')!==selected,
     noStaleInline:!host.querySelector('.mcel-semantic-collection__inline-detail'),
     originalSourceImmutable:plan.collection.rows[0].name!=='changed-name-same-identity',
     scrollUnchanged:Number.isFinite(list.scrollTop)};
  // Return to initial source, with the same structure/signature.
  const restore=mounted.update(plan);
  const current=host.querySelector('mcel-row');
  current.scrollIntoView({block:'center'});
  const base=current.getBoundingClientRect().height;
  const floating=mounted.collection.request(id,'onHover');
  const pane=mounted.collection.floatingCard;
  const pos=pane?.getBoundingClientRect(),src=current.getBoundingClientRect();
  const floatProof={ok:floating.ok,mode:floating.mode,overlayOutsideScroll:!!pane&&!list.contains(pane),
    isSourceAnchored:!!pos && pos.right>=src.left && pos.left<=src.right &&
      pos.bottom>=src.top && pos.top<=src.bottom,
    listGeometryStable:Math.abs(current.getBoundingClientRect().height-base)<1,
    copyControls:!!pane?.querySelector('[aria-label="Copy complete source record"]'),
    selectable:!!pane&&getComputedStyle(pane).userSelect==='text',
    restoreAccepted:restore.ok===true};
  const leave=new PointerEvent('pointerleave',{bubbles:false});
  pane?.dispatchEvent(leave);
  const openBeforeScroll=!!mounted.collection.floatingCard;
  list.dispatchEvent(new WheelEvent('wheel',{bubbles:true}));
  const fading=!!mounted.collection.floatingCard?.classList.contains('mcel-negotiated-floating-card--fade');
  await new Promise(resolve=>setTimeout(resolve,180));
  const fade={openBeforeScroll,fading,closedByUserScroll:!mounted.collection.floatingCard};
  mounted.destroy();host.remove();
  const originalControls=root.querySelectorAll('#task-server-shutdown,#task-server-start,#task-server-restart,#task-schedule-create,#task-ai-analyze').length;
  return {inlineProof,continuity,floatProof,fade,originalControls,
    cleanup:!document.querySelector('#patch147-root')&&!document.querySelector('.mcel-negotiated-floating-card'),
    overflow:document.documentElement.scrollWidth>innerWidth+2};
}'''

FALLBACK=r'''({plan})=>{
  const root=document.querySelector('#task-manager-app');
  const cases=[];
  for(const [label,capabilities,policy,expect] of [
    ['scroll-denied-floating',{allowScroll:false},{},'floating-over-source'],
    ['scroll-overlay-denied-inline',{allowScroll:false,allowOverlay:false},{},'inline-expand'],
    ['all-denied',{allowScroll:false,allowOverlay:false,allowResize:false},{},null],
    ['resize-denied-floating',{allowResize:false},{},'floating-over-source'],
    ['element-accepts-no-scroll',{allowScroll:false},{schema:'mcel.element-presentation-policy.v1',events:{
       onSelect:[{mode:'inline-expand',requires:['geometry.child-resize']}] }},'inline-expand']
  ]){
    const el=document.createElement('div');root.append(el);
    const mount=McelUniversalSemanticLayoutLab.mount(el,plan,{lab:true,presentationNegotiation:true,
      capabilities,presentationPolicy:policy.schema?policy:undefined});
    const id=mount.collection.ids[0];
    const receipt=mount.collection.request(id,'onSelect');
    const row=el.querySelector('mcel-row');
    const attempts=receipt.attempts;
    cases.push({label,ok:(receipt.mode||null)===expect && receipt.ok===(expect!==null),
      actual:receipt.mode||null,expected:expect,
      firstDenied:attempts[0]?.status==='denied',
      attemptedModes:attempts.map(x=>x.mode),
      geometry:!!row.querySelector('.mcel-semantic-collection__inline-detail'),
      floating:!!mount.collection.floatingCard});
    mount.destroy();el.remove();
  }
  const elementPolicy=McelPresentationNegotiation.defaultPolicy;
  const rejectsInvalid=(()=>{try{McelPresentationNegotiation.validate({schema:elementPolicy.schema,
    events:{onSelect:[{mode:'inline-expand',requires:['geometry.child-resize','evil.capability']}]}});return false;}
    catch(e){return true;}})();
  const source=root.querySelectorAll('mcel-row').length;
  return {cases,rejectsInvalid,cleanup:!document.querySelector('.mcel-negotiated-floating-card'),
    productionRowsUnchanged:source===0};
}'''


LIFECYCLE=r'''async ({plan})=>{
  const root=document.querySelector('#task-manager-app');
  const host=document.createElement('div');root.append(host);
  const policy=structuredClone(McelPresentationNegotiation.defaultPolicy);
  policy.events.onHover[0].lifecycle.pointerLeaveDelayMs=75;
  const instance=McelUniversalSemanticLayoutLab.mount(host,plan,{lab:true,presentationNegotiation:true,
    presentationPolicy:policy});
  const row=host.querySelector('mcel-row'),id=row.dataset.rowId;
  row.focus();row.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
  const keyboardSelection=!!row.querySelector('.mcel-semantic-collection__inline-detail') &&
    instance.collection.expandedId===id;
  const hover=instance.collection.request(id,'onHover');
  const floating=instance.collection.floatingCard;
  const heading=floating?.querySelector('h3')?.firstChild;
  const range=document.createRange();range.selectNodeContents(heading);
  const selection=document.getSelection();selection.removeAllRanges();selection.addRange(range);
  const nativeSelection=!!selection.toString() && selection.anchorNode?.isConnected;
  floating.dispatchEvent(new PointerEvent('pointerleave'));
  await new Promise(resolve=>setTimeout(resolve,125));
  const selectionProtected=instance.collection.floatingCard===floating;
  selection.removeAllRanges();
  floating.dispatchEvent(new PointerEvent('pointerleave'));
  await new Promise(resolve=>setTimeout(resolve,125));
  const timerDismissed=instance.collection.floatingCard===null;
  instance.collection.request(id,'onHover');
  document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  const escapeDismissed=instance.collection.floatingCard===null;
  instance.collection.request(id,'onHover');
  const inlineAction=instance.collection.floatingCard?.querySelector('[aria-label="Expand inline"]');
  inlineAction?.click();
  const floatingToInline=!!inlineAction && !instance.collection.floatingCard &&
    !!row.querySelector('.mcel-semantic-collection__inline-detail');
  const collapsed=instance.collection.request(id,'onSelect');
  const shrinking=collapsed.mode==='collapsed' &&
    !row.querySelector('.mcel-semantic-collection__inline-detail');
  instance.collection.request(id,'onHover');
  const stale=instance.collection.floatingCard;
  const updated=instance.update(plan);
  const staleSourceDismissed=!!stale && updated.ok && !instance.collection.floatingCard;
  instance.destroy();host.remove();
  // Neutral semantic item policy, proving protocol is not tied to process fields.
  const provider={assess:()=>({ok:true})};
  const policy2={schema:McelPresentationNegotiation.SCHEMA,events:{onSelect:[
    {mode:'inline-expand',requires:['geometry.child-resize','scroll.reveal']},
    {mode:'floating-over-source',requires:['overlay.anchor-source']}]}};
  const neutral=McelPresentationNegotiation.negotiate(policy2,'onSelect',{
    'geometry.child-resize':provider,
    'overlay.anchor-source':provider}, {},()=>({ok:true}));
  return {keyboardSelection,hoverSucceeded:hover.ok,nativeSelection,selectionProtected,
    timerDismissed,escapeDismissed,floatingToInline,shrinking,staleSourceDismissed,
    neutralFallback:neutral.ok&&neutral.mode==='floating-over-source'&&
       neutral.attempts[0].status==='denied',cleanup:!host.isConnected};
}'''


SCROLL_FOCUS=r'''({plan})=>{
  const el=document.createElement('div');document.querySelector('#task-manager-app').append(el);
  const mount=McelUniversalSemanticLayoutLab.mount(el,plan,{lab:true,presentationNegotiation:true});
  const list=el.querySelector('mcel-collection'),row=[...list.querySelectorAll('mcel-row')].at(-1);
  list.scrollTop=0;const before=list.scrollTop;
  const result=mount.collection.request(row.dataset.rowId,'onSelect');
  const a=list.getBoundingClientRect(),b=row.getBoundingClientRect();
  const oversized=b.height>a.height-8;
  const revealed=oversized ? b.top>=a.top-12 && b.top<=a.top+12 :
    b.top>=a.top-12 && b.bottom<=a.bottom+12;
  const proof={mode:result.mode,oversized,revealed,scrollBefore:before,
    scrollAfter:list.scrollTop,scrollAdjusted:list.scrollTop!==before};
  mount.destroy();el.remove();return proof;
}'''


def verify(output:Path=OUTPUT,screenshots:bool=True)->dict:
    output.mkdir(parents=True,exist_ok=True)
    report={'schema':SCHEMA,'generatedUtc':datetime.now(timezone.utc).isoformat(),
        'readOnly':True,'productionPromotion':False,'operatorActionsExecuted':False,
        'cases':[],'fallback':[],'lifecycle':[],'pointer':[],'scrollFocus':[],'errors':[],
        'limitations':['Offline, read-only snapshot in existing Task Manager HTML; not the live production route',
          'Programmatic browser interactions do not measure human usability',
          'Browser clipboard is stubbed only to verify payload; OS clipboard permission not implied']}
    with sync_playwright() as playwright:
        chrome=_chrome(playwright,None)
        try:
            for state in STATES:
                plan=scenario(state)['plan']
                for width in WIDTHS:
                    page=chrome.new_page(viewport={'width':width,'height':850},reduced_motion='reduce')
                    errors=[];network=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.on('request',lambda r:network.append(r.url))
                    try:
                        page.set_content(html_fixture(),wait_until='load')
                        page.add_script_tag(path=str(SCRIPTS/'mcel-presentation-negotiation.js'))
                        page.add_script_tag(path=str(SCRIPTS/'mcel-presentation-hosts.js'))
                        page.add_script_tag(path=str(SCRIPTS/'mcel-layout-continuity.js'))
                        page.add_script_tag(path=str(SCRIPTS/'mcel-universal-semantic-layout-lab.js'))
                        page.evaluate('''() => Object.defineProperty(navigator,'clipboard',{
                           configurable:true,value:{writeText:async s=>{window.__patch147copied=s;}}})''')
                        result=page.evaluate(PROBE,{'plan':plan})
                        fb=page.evaluate(FALLBACK,{'plan':plan})
                        life=page.evaluate(LIFECYCLE,{'plan':plan})
                        scroll=page.evaluate(SCROLL_FOCUS,{'plan':plan})
                        proof=result['inlineProof'];cont=result['continuity'];floatp=result['floatProof']
                        good=(all((proof[k] for k in ('ok','expanded','grew','detailsInsideList','noExternalDetails',
                          'canonicalMatch','copyMatches','evidenceCollapsed'))) and proof['mode']=='inline-expand'
                          and proof['copyResult']=='success' and all(cont.values())
                          and all(floatp[k] for k in ('ok','overlayOutsideScroll','isSourceAnchored',
                            'listGeometryStable','copyControls','selectable','restoreAccepted'))
                          and floatp['mode']=='floating-over-source'
                          and all(result['fade'].values()) and result['originalControls']==5
                          and result['cleanup'] and not result['overflow'] and
                          all(x['ok'] for x in fb['cases']) and fb['rejectsInvalid'] and
                          fb['cleanup'] and fb['productionRowsUnchanged'] and all(life.values()) and
                          scroll['mode']=='inline-expand' and scroll['revealed'] and not network and not errors)
                        result.update(state=state,width=width,status='PASS' if good else 'FAIL',
                            networkRequests=network,browserErrors=errors)
                        report['cases'].append(result)
                        report['fallback'].append({'state':state,'width':width,**fb})
                        report['lifecycle'].append({'state':state,'width':width,**life})
                        report['scrollFocus'].append({'state':state,'width':width,**scroll})
                        if state=='normal':
                            page.evaluate('''p=>{const el=document.createElement('div');el.id='patch147-pointer';
                              document.querySelector('#task-manager-app').append(el);
                              window.__patch147pointer=McelUniversalSemanticLayoutLab.mount(el,p,
                                {lab:true,presentationNegotiation:true});}''',plan)
                            first=page.locator('#patch147-pointer mcel-row').first
                            first.scroll_into_view_if_needed()
                            bounds=first.bounding_box()
                            page.mouse.move(bounds['x']+12,bounds['y']+12)
                            pointer=page.evaluate('''()=>{
                              const row=document.querySelector('#patch147-pointer mcel-row');
                              const pane=document.querySelector('.mcel-negotiated-floating-card');
                              const r=row.getBoundingClientRect(),p=pane?.getBoundingClientRect();
                              const x=r.left+12,y=r.top+12;
                              return {opened:!!pane,underPointer:!!p&&x>=p.left&&x<=p.right&&y>=p.top&&y<=p.bottom,
                                displayedAsOverlay:!!pane&&!row.contains(pane)};}''')
                            if screenshots:
                                page.screenshot(path=str(output/f'hover-normal-{width}.png'),animations='disabled')
                            page.keyboard.press('Escape')
                            pointer['escapeCloses']=page.locator('.mcel-negotiated-floating-card').count()==0
                            page.evaluate('''()=>{window.__patch147pointer.destroy();
                              document.querySelector('#patch147-pointer').remove();delete window.__patch147pointer;}''')
                            report['pointer'].append({'state':state,'width':width,**pointer})
                            if not all(pointer.values()):
                                report['cases'][-1]['status']='FAIL'
                        if screenshots and state=='normal':
                            # A separate evidence mount: do not modify production Task Manager.
                            page.evaluate('''p=>{const host=document.createElement('section');host.id='patch147-shot';
                              document.querySelector('#task-manager-app').append(host);
                              window.__patch147shot=McelUniversalSemanticLayoutLab.mount(host,p,
                                {lab:true,presentationNegotiation:true});
                              window.__patch147shot.collection.request(window.__patch147shot.collection.ids[0],'onSelect');}''',plan)
                            page.locator('#patch147-shot').screenshot(path=str(output/f'inline-{state}-{width}.png'),
                                animations='disabled')
                            page.evaluate('''()=>{window.__patch147shot.destroy();
                               document.querySelector('#patch147-shot').remove();delete window.__patch147shot;}''')
                    except Exception as e:
                        report['errors'].append(f'{state}/{width}: {type(e).__name__}: {e}')
                        report['cases'].append({'state':state,'width':width,'status':'FAIL','error':str(e)})
                    finally:page.close()
        finally:chrome.close()
    report['engineeringStatus']='PASS' if len(report['cases'])==len(STATES)*len(WIDTHS) and all(
      x['status']=='PASS' for x in report['cases']) and not report['errors'] else 'FAIL'
    save(output/'report.json',report)
    return report


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    parser.add_argument('--no-screenshots',action='store_true')
    args=parser.parse_args()
    try:r=verify(args.output,screenshots=not args.no_screenshots)
    except Exception as e:
        r={'schema':SCHEMA,'engineeringStatus':'FAIL','error':f'{type(e).__name__}: {e}'}
        save(args.output/'report.json',r)
    print('MCEL_PATCH14_7_ENGINEERING_'+r['engineeringStatus'])
    print('report:',args.output/'report.json')
    if 'error' in r:print('error:',r['error'])
    for c in r.get('cases',[]):print(c['state'],c['width'],c['status'],c.get('error',''))
    return 0 if r['engineeringStatus']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
