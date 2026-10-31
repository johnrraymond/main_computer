"""L18-01 hover anchor order, scrollport and content-height stability gate.

    python -m tools.mcel_hover_anchor_order_frob

Reproduces the 2026-10-30 real-browser trace: continuity-order rows 4+
used to receive an anchor in the wrong flex slot, creating a card above the
viewport and suppressing its scrollbar. The regression gate uses the real
MCEL Lab fixture in Chromium; it does not alter production state.
"""
from __future__ import annotations
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_lifecycle_taguchi_acid_frob import install, ROOT
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

OUTPUT=ROOT/'runtime/reports/mcel-hover-anchor-order'

def verify():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    report={'schema':'mcel.hover-anchor-order.v1','cases':[]}
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for width in (390,1100):
                page=browser.new_page(viewport={'width':width,'height':588},reduced_motion='reduce')
                errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
                checks={};case={'width':width,'checks':checks,'errors':errors};report['cases'].append(case)
                try:
                    install(page)
                    page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts/mcel-diagnostic-plugins.js'))
                    page.evaluate('''async()=>{await MCELDiagnosticPlugins.enable('mcel-hover-pointer')}''')
                    row=page.locator('[data-case-id="L18-01"] mcel-row').nth(4)
                    row.scroll_into_view_if_needed()
                    before=page.evaluate('''()=>{const r=document.querySelectorAll('[data-case-id="L18-01"] mcel-row')[4];
                      return {row:r.getBoundingClientRect().toJSON(),order:getComputedStyle(r).order,
                        listScrollTop:r.parentElement.scrollTop, pageScrollTop:document.documentElement.scrollTop};}''')
                    case['before']=before
                    row.hover(force=True)
                    page.wait_for_timeout(1220)
                    expr='''()=>{const row=document.querySelector('[data-case-id="L18-01"] mcel-row:popover-open');
                        const anchor=document.querySelector('.mcel-unclip-layout-anchor');
                        const rail=document.querySelector('.mcel-unclip-row-scroll-track:popover-open');
                        const list=document.querySelector('[data-case-id="L18-01"] mcel-collection');
                        const pane=row?.querySelector('.mcel-unclip-card');
                        const rect=el=>el?.getBoundingClientRect().toJSON()||null;
                        return {open:!!row,row:rect(row),anchor:rect(anchor),rail:rect(rail),
                          railHidden:rail?.hidden,order:anchor?getComputedStyle(anchor).order:null,
                          scrollTop:row?.scrollTop,scrollHeight:row?.scrollHeight,
                          clientHeight:row?.clientHeight,detailHeight:pane?.querySelector('.mcel-semantic-collection__detail-content')?.scrollHeight,
                          listScrollTop:list.scrollTop,pageScrollTop:document.documentElement.scrollTop};}'''
                    a=page.evaluate(expr);case['open']=a
                    checks['same-row-promoted']=a['open'] and a['row'] is not None
                    checks['flex-order-copied']=a['order']==before['order']
                    checks['anchor-preserves-real-record-slot']=a['anchor'] is not None and abs(a['row']['top']-a['anchor']['top'])<1
                    checks['viewport-capped-and-scrollable']=a['row']['bottom']<=589 and a['scrollHeight']>a['clientHeight']+30
                    checks['physical-rail-present-and-flush']=bool(a['rail'] and not a['railHidden'] and abs(a['rail']['left']-a['row']['right'])<.65)
                    # Source value-only updates occur every three seconds.
                    page.wait_for_timeout(3450)
                    b=page.evaluate(expr);case['afterTelemetry']=b
                    checks['telemetry-keeps-origin-and-scroll-extent']=b['open'] and abs(b['row']['top']-a['row']['top'])<1 and abs(b['scrollHeight']-a['scrollHeight'])<=1
                    checks['telemetry-keeps-anchor-and-rail']=bool(b['rail'] and abs(b['anchor']['top']-b['row']['top'])<1 and abs(b['rail']['left']-b['row']['right'])<.65)
                    # A one-pixel document scroll must follow the anchor, not
                    # unconditionally dismiss the active presentation.
                    page.evaluate('''()=>window.scrollBy(0,1)''')
                    page.wait_for_timeout(120)
                    c=page.evaluate(expr);case['afterPageScroll']=c
                    checks['page-scroll-follows-not-dismisses']=c['open'] and abs(c['row']['top']-c['anchor']['top'])<1 and abs(c['rail']['left']-c['row']['right'])<.65
                    # Wheel within the record must consume row space before
                    # the parent list scrolls (until boundary + threshold).
                    page.mouse.move(c['row']['left']+35,c['row']['top']+50)
                    list_before=c['listScrollTop']
                    page.mouse.wheel(0,160)
                    page.wait_for_timeout(170)
                    d=page.evaluate(expr);case['afterWheel']=d
                    checks['wheel-scrolls-row-not-parent']=d['open'] and d['scrollTop']>=140 and d['listScrollTop']==list_before
                    # After reaching the row bottom, a deliberate additional
                    # 15%-threshold gesture should hand off to the list.
                    page.mouse.wheel(0,2000)
                    page.wait_for_timeout(240)
                    page.mouse.wheel(0,110)
                    page.wait_for_timeout(130)
                    handoff=page.evaluate(expr);case['afterHandoff']=handoff
                    checks['boundary-wheel-hands-off-to-parent']=handoff['listScrollTop']>list_before and handoff['scrollTop']>0
                    checks['handoff-keeps-row-and-anchor-aligned']=handoff['open'] and abs(handoff['anchor']['top']-handoff['row']['top'])<1 and abs(handoff['rail']['left']-handoff['row']['right'])<.65
                    checks['no-browser-errors']=not errors
                    trace=page.evaluate('MCELDiagnosticPlugins.exportHoverTrace()')
                    case['hostReplans']=sum(e['kind']=='host-geometry-replan' for e in trace['events'])
                    case['hostRepairs']=sum(e['kind']=='host-follow-repair' for e in trace['events'])
                    case['traceAlerts']=trace.get('summary',{}).get('invariantAlerts',[])
                    page.screenshot(path=str(OUTPUT/f'anchor-order-{width}.png'))
                except Exception as ex:case['exception']=repr(ex)
                finally:page.close()
        finally:browser.close()
    report['pass']=all(c['checks'] and all(c['checks'].values()) and 'exception' not in c for c in report['cases'])
    (OUTPUT/'report.json').write_text(json.dumps(report,indent=2))
    for c in report['cases']:print(c['width'],c['checks'],c.get('exception',''))
    print('MCEL_HOVER_ANCHOR_ORDER_'+('PASS' if report['pass'] else 'FAIL'))
    return report['pass']

if __name__=='__main__':raise SystemExit(0 if verify() else 1)
