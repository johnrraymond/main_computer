"""Trace-derived L18-01 hover miscompute regression.

    python -m tools.mcel_hover_miscompute_frob

Assertions: stale/offscreen hover activation must not promote a row, a host
must refuse an offscreen row even when called directly, and fractional-pixel
CSSOM rounding must not cause repeated height rewrites on scroll.
"""
from __future__ import annotations
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_lifecycle_taguchi_acid_frob import install,ROOT
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
OUT=ROOT/'runtime/reports/mcel-hover-miscompute'

def verify():
    OUT.mkdir(parents=True,exist_ok=True)
    report={'schema':'mcel.hover-miscompute.v1','cases':[]}
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for width in (390,1100,1272):
                page=browser.new_page(viewport={'width':width,'height':588},reduced_motion='reduce')
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                case={'width':width,'checks':{},'errors':errors}
                report['cases'].append(case)
                try:
                    install(page)
                    page.evaluate('''()=>{
                      window.__miscomputeHostEvents=[];
                      addEventListener('mcel-hover-trace',ev=>{
                        const d=ev.detail||{};
                        if(['open-committed','open-refused-offscreen','follow-repair'].includes(d.kind))
                          window.__miscomputeHostEvents.push({kind:d.kind,rowId:d.rowId,reason:d.reason,
                            changed:d.extra?.changed||[]});
                      });
                    }''')
                    list_=page.locator('[data-case-id="L18-01"] mcel-collection')
                    row=page.locator('[data-case-id="L18-01"] mcel-row').nth(0)
                    row.scroll_into_view_if_needed()
                    row.hover(force=True)
                    page.wait_for_timeout(1210)
                    snap=page.evaluate('''()=>{
                      const row=document.querySelector('[data-case-id="L18-01"] mcel-row');
                      const list=document.querySelector('[data-case-id="L18-01"] mcel-collection');
                      const rail=document.querySelector('.mcel-unclip-row-scroll-track:popover-open');
                      return {open:row.matches(':popover-open'),row:row.getBoundingClientRect().toJSON(),
                        list:list.getBoundingClientRect().toJSON(),rail:rail?.getBoundingClientRect().toJSON(),
                        railHidden:rail?.hidden};
                    }''')
                    case['validHover']=snap
                    ck=case['checks']
                    ck['valid-original-row-opens']=bool(snap['open'])
                    ck['wide-row-exceeds-scroll-view']=snap['row']['width']>snap['list']['width']+10
                    ck['rail-flush']=bool(snap['rail'] and not snap['railHidden'] and
                       abs(snap['rail']['left']-snap['row']['right'])<1)
                    # Scroll a couple of pixels while open, with a fractional
                    # row height. The follow host may update top, but it must not
                    # write the same height repeatedly due to CSSOM rounding.
                    page.evaluate('''()=>{const c=document.querySelector('[data-case-id="L18-01"] mcel-collection');
                       c.scrollTop+=1;}''')
                    page.wait_for_timeout(130)
                    page.evaluate('''()=>{const c=document.querySelector('[data-case-id="L18-01"] mcel-collection');
                       c.scrollTop+=1;}''')
                    page.wait_for_timeout(130)
                    heightRepairs=page.evaluate('''()=>window.__miscomputeHostEvents.filter(e=>e.kind==='follow-repair'&&
                         e.changed.some(c=>c.property==='height')).length''')
                    case['heightRepairCount']=heightRepairs
                    ck['no-repeat-rounded-height-writes']=heightRepairs==0
                    # The parent's first 100px transferred wheel must update
                    # the top-layer row and its rail IN THE SAME WHEEL EVENT,
                    # not 100px later in a requestAnimationFrame callback.
                    handoff=page.evaluate('''()=>{
                      const list=document.querySelector('[data-case-id="L18-01"] mcel-collection');
                      const row=list.querySelector('mcel-row');
                      row.scrollTop=row.scrollHeight-row.clientHeight;
                      const parentBefore=list.scrollTop;
                      row.dispatchEvent(new WheelEvent('wheel',{
                        bubbles:true,cancelable:true,deltaY:100,deltaMode:0
                      }));
                      const anchor=row.mcelPopoverAnchor;
                      const rail=document.querySelector('.mcel-unclip-row-scroll-track:popover-open');
                      const rr=row.getBoundingClientRect(),ar=anchor?.getBoundingClientRect(),tr=rail?.getBoundingClientRect();
                      return {transferred:list.scrollTop-parentBefore,
                        rowAnchorGap:ar?rr.top-ar.top:null,railRowGap:tr?tr.top-rr.top:null};
                    }''')
                    case['synchronousHandoff']=handoff
                    ck['parent-wheel-handoff-took-effect']=handoff['transferred']>=50
                    ck['handoff-synchronizes-row-and-anchor']=handoff['rowAnchorGap'] is not None and abs(handoff['rowAnchorGap'])<.75
                    ck['handoff-synchronizes-scrollbar']=handoff['railRowGap'] is not None and abs(handoff['railRowGap'])<.75
                    page.keyboard.press('Escape')
                    page.wait_for_timeout(60)
                    # The same gallery row is initially truly visible, then
                    # scrolled entirely out of the scroll view before its 1s
                    # activation timer can fire. No stale hover is allowed.
                    other=page.locator('[data-case-id="L18-01"] mcel-row').nth(4)
                    other.scroll_into_view_if_needed()
                    other.hover(force=True)
                    off=page.evaluate('''()=>{
                      const l=document.querySelector('[data-case-id="L18-01"] mcel-collection');
                      const r=l.querySelectorAll('mcel-row')[4];
                      l.scrollTop+=Math.max(550,r.getBoundingClientRect().height+300);
                      return {offscreen:r.getBoundingClientRect().bottom<=l.getBoundingClientRect().top,
                              rowId:r.dataset.rowId};}''')
                    page.wait_for_timeout(1200)
                    noStale=page.evaluate('''()=>{
                      const r=document.querySelectorAll('[data-case-id="L18-01"] mcel-row')[4];
                      return !r.matches(':popover-open') &&
                      !window.__miscomputeHostEvents.some(e=>e.kind==='open-committed'&&e.rowId===r.dataset.rowId);
                    }''')
                    case['offscreen']=off
                    ck['source-truly-scrolled-out-before-timer']=off['offscreen']
                    ck['stale-hover-cannot-open-offscreen-row']=noStale
                    # Also exercise direct host API, so a stale caller other
                    # than the gallery timer cannot promote an invisible row.
                    direct=page.evaluate('''()=>{
                      const l=document.querySelector('[data-case-id="L18-01"] mcel-collection');
                      const r=l.querySelectorAll('mcel-row')[4];
                      const host=McelPresentationHosts.createOverlayHost(document);
                      try{const result=host.openUnclip(r,document.createElement('div'),{},l,null);
                        return {ok:result.ok,reason:result.reason};}
                      catch(error){return {ok:null,error:String(error)};}
                      finally{host.destroy();}
                    }''')
                    case['directOffscreenRequest']=direct
                    ck['host-rejects-offscreen-request']=direct['ok'] is False and direct['reason']=='source-outside-visible-scroll-view'
                    ck['no-browser-errors']=not errors
                except Exception as exc:
                    case['exception']=str(exc)
                finally:
                    page.close()
        finally:
            browser.close()
    report['pass']=all(c['checks'] and all(c['checks'].values()) and 'exception' not in c for c in report['cases'])
    (OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    for c in report['cases']:
        print(c['width'],c['checks'],c.get('exception',''))
    print('MCEL_HOVER_MISCOMPUTE_'+('PASS' if report['pass'] else 'FAIL'))
    return report['pass']
if __name__=='__main__':raise SystemExit(0 if verify() else 1)
