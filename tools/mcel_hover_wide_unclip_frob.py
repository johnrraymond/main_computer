"""L18-01 real-browser horizontal-unclip regression (390 / 1100 / 1272px).

    python -m tools.mcel_hover_wide_unclip_frob

The original row is wider than its scroll *collection* only while hovered.
Its original fields must retain their positions and dimensions; the new
horizontal area must be hit-testable, scroll ownership and the exterior rail
must survive a source telemetry update, and the collection must not acquire
horizontal overflow. No modified production workload or synthetic DOM clone.
"""
from __future__ import annotations
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_lifecycle_taguchi_acid_frob import install, ROOT
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

OUT = ROOT / 'runtime/reports/mcel-hover-wide-unclip'
SNAP = r'''()=>{
  const list=document.querySelector('[data-case-id="L18-01"] mcel-collection');
  const row=document.querySelectorAll('[data-case-id="L18-01"] mcel-row')[4];
  const anchor=document.querySelector('.mcel-unclip-layout-anchor');
  const rail=document.querySelector('.mcel-unclip-row-scroll-track:popover-open');
  const rect=e=>e?.getBoundingClientRect().toJSON()||null;
  return {open:row?.matches(':popover-open')||false, row:rect(row),
    list:rect(list), anchor:rect(anchor), rail:rect(rail), railHidden:rail?.hidden,
    fields:[...(row?.querySelectorAll('mcel-field')||[])].map(rect),
    originalDOMId:row?.dataset.rowId, listScrollWidth:list?.scrollWidth,
    listClientWidth:list?.clientWidth, rowScrollTop:row?.scrollTop,
    rowScrollHeight:row?.scrollHeight, rowClientHeight:row?.clientHeight,
    listScrollTop:list?.scrollTop, pageScrollTop:document.documentElement.scrollTop};
}'''

def verify():
    OUT.mkdir(parents=True,exist_ok=True)
    report={'schema':'mcel.hover-wide-unclip.v1','cases':[]}
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for width in (390,1100,1272):
                page=browser.new_page(viewport={'width':width,'height':700},reduced_motion='reduce')
                errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
                case={'viewport':width,'checks':{},'errors':errors};report['cases'].append(case)
                check=case['checks']
                try:
                    install(page)
                    row=page.locator('[data-case-id="L18-01"] mcel-row').nth(4)
                    row.scroll_into_view_if_needed()
                    before=page.evaluate(SNAP)
                    case['before']={k:v for k,v in before.items() if k!='fields'}
                    row.hover(force=True)
                    page.wait_for_timeout(1220)
                    expanded=page.evaluate(SNAP)
                    case['expanded']={k:v for k,v in expanded.items() if k!='fields'}
                    r=expanded['row'];l=expanded['list'];t=expanded['rail'];anchor=expanded['anchor']
                    check['single-original-row-popover']=expanded['open'] and expanded['originalDOMId']==before['originalDOMId']
                    check['wide-card-exceeds-scroll-view']=r['width']>l['width']+10
                    check['scroll-view-remains-original-width']=abs(l['width']-before['list']['width'])<.7
                    check['no-horizontal-list-overflow']=expanded['listScrollWidth']<=expanded['listClientWidth']+2
                    check['row-origin-and-anchor-stable']=bool(anchor and
                      abs(r['left']-before['row']['left'])<.75 and abs(r['top']-anchor['top'])<.75)
                    check['original-fields-pixel-position-stable']=len(before['fields'])==len(expanded['fields']) and all(
                        all(abs(a[k]-b[k])<.7 for k in ('x','y','width','height'))
                        for a,b in zip(before['fields'],expanded['fields']))
                    check['external-rail-flush-with-wider-card']=bool(t and not expanded['railHidden'] and
                        abs(t['left']-r['right'])<.7 and abs(t['top']-r['top'])<.7)
                    check['expanded-row-fits-visible-browser-width']=r['right']+(t['width'] if t else 0)<=width+1
                    # Pointer must remain owned even in the NEW horizontal band.
                    page.mouse.move((before['row']['right']+r['right'])/2,r['top']+min(65,r['height']/4))
                    page.wait_for_timeout(1130)
                    check['wide-band-inherits-hover-ownership']=page.evaluate(SNAP)['open']
                    # The three-second gallery telemetry tick cannot collapse/reflow the card.
                    page.wait_for_timeout(2600)
                    after=page.evaluate(SNAP)
                    case['afterTelemetry']={k:v for k,v in after.items() if k!='fields'}
                    check['wide-card-persists-across-telemetry']=after['open'] and abs(after['row']['width']-r['width'])<.75
                    check['rail-stays-attached-across-telemetry']=bool(after['rail'] and
                      abs(after['rail']['left']-after['row']['right'])<.7)
                    check['fields-stay-stable-across-telemetry']=all(
                        all(abs(a[k]-b[k])<.7 for k in ('x','y','width','height'))
                        for a,b in zip(expanded['fields'],after['fields']))
                    # Exercise actual wheel and track without converting the row to a clone.
                    page.mouse.move(after['row']['left']+35,after['row']['top']+65)
                    page.mouse.wheel(0,125)
                    page.wait_for_timeout(170)
                    wheel=page.evaluate(SNAP)
                    case['wheel']={k:v for k,v in wheel.items() if k!='fields'}
                    check['wide-card-remains-row-scrollport']=wheel['open'] and wheel['rowScrollTop']>=100
                    check['wide-card-keeps-rail-during-scroll']=bool(wheel['rail'] and
                        abs(wheel['rail']['left']-wheel['row']['right'])<.7)
                    check['no-browser-errors']=not errors
                    page.screenshot(path=str(OUT/f'wide-{width}.png'))
                except Exception as exc:
                    case['exception']=repr(exc)
                finally:page.close()
        finally:browser.close()
    report['pass']=all(c['checks'] and all(c['checks'].values()) and 'exception' not in c for c in report['cases'])
    (OUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    for c in report['cases']:print(c['viewport'],c['checks'],c.get('exception',''))
    print('MCEL_HOVER_WIDE_UNCLIP_'+('PASS' if report['pass'] else 'FAIL'))
    return report['pass']
if __name__=='__main__':raise SystemExit(0 if verify() else 1)
