"""MCEL 14.7.6: pixel-preserving hover, append-only extension, and scroll ownership.

Real Chromium against the existing offline Task Manager semantic fixture.
Does not mutate production Task Manager or operate real processes.
"""
from __future__ import annotations
import argparse,io,json
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright
from tools.mcel_patch14_7_capability_negotiation_frob import SCRIPTS,_chrome
from tools.mcel_task_manager_notebook_lab_acceptance import html_fixture
from tools.mcel_patch14_6_promise_verification_frob import scenario

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/'runtime/reports/mcel-hover-unclip-scroll-handoff'
SCHEMA='mcel.hover-unclip-scroll-handoff.v1'

HANDOFF=r'''async ()=>{
  const panel=document.createElement('div');panel.style.cssText='height:160px;width:200px;overflow:auto';
  const spacer=document.createElement('div');spacer.style.height='1000px';panel.append(spacer);document.body.append(panel);
  const ext=document.createElement('div');ext.style.cssText='height:160px;width:180px;overflow:auto';
  const contents=document.createElement('div');contents.style.height='640px';ext.append(contents);document.body.append(ext);
  let at=0;const native=ext.scrollTop;
  const scrollHost=McelPresentationHosts.createScrollHost(panel);
  const handoff=McelPresentationHosts.createScrollHandoff(ext,scrollHost,{handoffThresholdPercent:15,momentumGuardMs:140});
  const wheel=dy=>{const ev=new WheelEvent('wheel',{deltaY:dy,deltaMode:0,bubbles:true,cancelable:true});
    const accepted=ext.dispatchEvent(ev);return {canceled:!accepted,owner:handoff.state.owner,progress:handoff.state.overscroll,child:ext.scrollTop,parent:panel.scrollTop};};
  const inside=wheel(60);const childMoved=inside.child>native && inside.parent===0 && inside.canceled;
  ext.scrollTop=ext.scrollHeight-ext.clientHeight;
  const first=wheel(18);const held=first.owner==='extension' && first.parent===0;
  await new Promise(resolve=>setTimeout(resolve,160));
  const afterQuiet=wheel(10);const pending=afterQuiet.owner==='extension' && afterQuiet.parent===0;
  await new Promise(resolve=>setTimeout(resolve,160));
  const afterThreshold=wheel(16);const transferred=afterThreshold.owner==='parent' && afterThreshold.parent>0;
  const continued=wheel(35);const forwarded=continued.owner==='parent' && continued.parent>afterThreshold.parent;
  const reverse=wheel(-18);const released=reverse.owner==='extension' && reverse.parent===continued.parent && reverse.canceled;
  handoff.destroy();scrollHost.destroy();panel.remove();ext.remove();
  return {childMoved,held,pending,transferred,forwarded,released,threshold:handoff.state.thresholdPixels,
    states:{first,afterQuiet,afterThreshold,continued,reverse}};
}'''

VISIBLE=r'''async ({plan,width})=>{
 const host=document.createElement('div');document.querySelector('#task-manager-app').append(host);
 const m=McelUniversalSemanticLayoutLab.mount(host,plan,{lab:true,presentationNegotiation:true,sourceRetirement:true});
 const row=host.querySelector('mcel-row'),list=host.querySelector('mcel-collection');
 row.scrollIntoView({block:'center'});
 const original=row.getBoundingClientRect(),originalScroll=list.scrollTop;
 const sibling=row.nextElementSibling,siblingBefore=sibling?.getBoundingClientRect();
 const req=m.collection.request(m.collection.ids[0],'onHover');
 const pane=m.collection.floatingCard;
 const extension=pane?.querySelector('.mcel-unclip-extension-viewport');
 const track=pane?.querySelector('.mcel-unclip-exterior-track');
 const after=row.getBoundingClientRect();
 const sourceExact=req.ok && pane?.dataset.mcelUnclip==='true' && pane.parentElement===row &&
   Math.abs(after.left-original.left)<.5 && Math.abs(after.top-original.top)<.5 &&
   Math.abs(after.width-original.width)<.5 && after.height>original.height+20;
 const appendOnly=!!extension && pane.parentElement===row &&
   getComputedStyle(pane).position==='relative' &&
   Math.abs(pane.getBoundingClientRect().top-(original.bottom-1))<3 &&
   pane.getBoundingClientRect().bottom<after.bottom;
 const noReflow=!!sibling && Math.abs(sibling.getBoundingClientRect().top-siblingBefore.top)<.5 &&
   Math.abs(after.width-original.width)<.5 && list.scrollTop===originalScroll;
 const sourceOwnsHover=!!pane && row.contains(pane) && !row.querySelector('.mcel-unclip-mirror');
 const originalInTopLayer=row.matches(':popover-open') && row.parentElement===list &&
   !!row.mcelPopoverAnchor && row.mcelPopoverAnchor.parentElement===list &&
   row.mcelPopoverAnchor.childElementCount===0;
 list.scrollTop+=20;
 await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
 const r=row.getBoundingClientRect(),pr=pane.getBoundingClientRect();
 const followed=Math.abs(pr.left-r.left-parseFloat(getComputedStyle(row).paddingLeft))<.75 &&
   Math.abs(pr.top-(r.top+original.height-1))<3;
 const value=row.querySelector('mcel-field .field-value');const selection=document.getSelection();
 const range=document.createRange();range.selectNodeContents(value);selection.removeAllRanges();selection.addRange(range);
 const selectedText=String(selection);const copyable=selectedText.length>0 && row.contains(selection.anchorNode);
 selection.removeAllRanges();
 const validated=!!m.collection.layout && m.tree.refresh().status==='pass';
 const showScrollbar=track?.hidden===false;
 const trackOutside=showScrollbar?(track.getBoundingClientRect().right<=r.left+1 ||
   track.getBoundingClientRect().left>=r.right-1):true;
 const state={sourceExact,appendOnly,noReflow,followed,copyable,validated,showScrollbar,trackOutside,sourceOwnsHover,
   originalInTopLayer,position:{before:original.toJSON(),after:after.toJSON(),extension:pr.toJSON()},
   viewport:{height:extension.clientHeight,scrollHeight:extension.scrollHeight},
   trackPlacement:pane.dataset.externalTrackPlacement};
 m.destroy();host.remove();return state;
}'''

def pixel_delta(page,plan)->dict:
    """Compare only the PRE-EXISTING rectangle; expanding the real card is required."""
    page.evaluate('''({plan})=>{const h=document.createElement('div');document.querySelector('#task-manager-app').append(h);
      const m=McelUniversalSemanticLayoutLab.mount(h,plan,{lab:true,presentationNegotiation:true});
      const row=h.querySelector('mcel-row');row.scrollIntoView({block:'center'});window.__pixel={h,m,row};}''',{'plan':plan})
    before=Image.open(io.BytesIO(page.locator('mcel-row').first.screenshot())).convert('RGB')
    page.evaluate("()=>__pixel.m.collection.request(__pixel.m.collection.ids[0],'onHover')")
    after=Image.open(io.BytesIO(page.locator('mcel-row').first.screenshot())).convert('RGB')
    grew=after.height>before.height and after.width==before.width
    if not grew:
        result={'sameSize':False,'meanAbsoluteDifference':None,'fractionDifferent':1.0,'cardExpanded':False}
    else:
        # The captured region itself is fixed-size and unchanged; only the
        # bottom ADDITION grows. This deliberately rejects a separate panel.
        a=np.asarray(before,dtype=np.int16);b=np.asarray(after.crop((0,0,before.width,before.height)),dtype=np.int16)
        result={'sameSize':True,'meanAbsoluteDifference':float(np.mean(np.abs(a-b))),
                'fractionDifferent':float(np.mean(np.any(a!=b,axis=2))),'maxAbsoluteDifference':int(np.max(np.abs(a-b))),
                'fractionLargeDifference':float(np.mean(np.any(np.abs(a-b)>8,axis=2))),'cardExpanded':True}
    page.evaluate('''()=>{__pixel.m.destroy();__pixel.h.remove();delete window.__pixel;}''')
    return result

def verify(output=OUTPUT):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    report={'schema':SCHEMA,'generatedUtc':datetime.now(timezone.utc).isoformat(),
        'readOnly':True,'productionPromotion':False,'cases':[],'errors':[]}
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for width in (390,1440):
                for density in ('normal','stressed'):
                    page=browser.new_page(viewport={'width':width,'height':850},reduced_motion='reduce')
                    errors=[];page.on('pageerror',lambda exc:errors.append(str(exc)))
                    try:
                        page.set_content(html_fixture(),wait_until='load')
                        for f in ('mcel-presentation-negotiation.js','mcel-presentation-hosts.js',
                                  'mcel-layout-continuity.js','mcel-universal-semantic-layout-lab.js'):
                            page.add_script_tag(path=str(SCRIPTS/f))
                        plan=scenario(density)['plan']
                        geometry=page.evaluate(VISIBLE,{'plan':plan,'width':width})
                        handoff=page.evaluate(HANDOFF)
                        pixels=pixel_delta(page,plan)
                        ok=(not errors and all(geometry[k] for k in ('sourceExact','appendOnly','noReflow',
                            'followed','copyable','validated','trackOutside','sourceOwnsHover','originalInTopLayer')) and
                            all(handoff[k] for k in ('childMoved','held','pending','transferred','forwarded','released')) and
                            pixels['sameSize'] and pixels['cardExpanded'] and pixels['meanAbsoluteDifference']<3.0 and
                            pixels['fractionLargeDifference']<0.001)
                        report['cases'].append({'width':width,'density':density,'status':'PASS' if ok else 'FAIL',
                            'geometry':geometry,'handoff':handoff,'pixels':pixels,'browserErrors':errors})
                        if errors:report['errors'].extend(errors)
                    except Exception as e:report['errors'].append(f'{width} {density}: {e}')
                    finally:page.close()
        finally:browser.close()
    report['status']='PASS' if len(report['cases'])==4 and all(c['status']=='PASS' for c in report['cases']) and not report['errors'] else 'FAIL'
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('MCEL_HOVER_UNCLIP_SCROLL_HANDOFF_'+report['status'])
    for c in report['cases']:
        print(c['width'],c['density'],c['status'],'pixel delta',round(c['pixels']['meanAbsoluteDifference'] or 0,3),
              'scroll handoff',c['handoff']['transferred'])
    for error in report['errors']:print('ERROR',error)
    print('report:',output/'report.json')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=OUTPUT)
    args=parser.parse_args();raise SystemExit(0 if verify(args.output)['status']=='PASS' else 1)
