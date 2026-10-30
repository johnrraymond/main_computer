"""Chromium regression: scrollbar is truly adjacent in rendered pixels, and live
telemetry doesn't remount unchanged rows or disrupt a pointer on the rail.

    python -m tools.mcel_hover_live_geometry_frob
"""
from __future__ import annotations
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_lifecycle_taguchi_acid_frob import install, ROOT
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome
OUTPUT=ROOT/'runtime/reports/mcel-hover-live-geometry'

def verify():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    report={'schema':'mcel.hover-live-geometry.v1','cases':[]}
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for width in (390,1100):
                page=browser.new_page(viewport={'width':width,'height':700},reduced_motion='reduce')
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                case={'width':width,'checks':{}};report['cases'].append(case)
                try:
                    install(page)
                    page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts/mcel-diagnostic-plugins.js'))
                    page.evaluate('MCELDiagnosticPlugins.clearHoverTrace()')
                    page.evaluate('MCELDiagnosticPlugins.enable("mcel-hover-pointer")')
                    page.wait_for_function('MCELDiagnosticPlugins.exportHoverTrace().recording')
                    list_=page.locator('[data-case-id="L18-01"] mcel-collection')
                    list_.scroll_into_view_if_needed()
                    row=page.locator('[data-case-id="L18-01"] mcel-row').first
                    row.hover(force=True);page.wait_for_timeout(1220)
                    getgeom='''()=>{
                      const r=document.querySelector('[data-case-id="L18-01"] mcel-row');
                      const track=document.querySelector('.mcel-unclip-row-scroll-track:popover-open');
                      const anchor=document.querySelector('.mcel-unclip-layout-anchor');
                      const rect=e=>e?.getBoundingClientRect().toJSON();
                      return {open:r.matches(':popover-open'),row:rect(r),rail:rect(track),
                        railHidden:track?.hidden,anchor:rect(anchor),
                        sourceChildren:[...r.querySelectorAll('mcel-field')].map(rect),
                        rowId:r.dataset.rowId,rows:[...r.parentElement.querySelectorAll('mcel-row')].map(e=>e.dataset.rowId)};
                    }'''
                    a=page.evaluate(getgeom)
                    case['start']={k:v for k,v in a.items() if k not in ['sourceChildren','rows']}
                    case['checks']['original-popover-open']=a['open']
                    case['checks']['rail-physically-adjacent']=bool(a['rail'] and not a['railHidden'] and
                        abs(a['rail']['left']-a['row']['right'])<.6 and
                        abs(a['rail']['top']-a['row']['top'])<.6 and
                        abs(a['rail']['height']-a['row']['height'])<.6)
                    # The pointer MUST remain associated with the same card when
                    # crossing its edge into the external scrollbar.
                    page.mouse.move(a['row']['right']+min(5,a['rail']['width']/2),
                        a['rail']['top']+min(36,a['rail']['height']/3))
                    page.wait_for_timeout(1150)
                    case['checks']['rail-is-part-of-pointer-region']=page.evaluate(getgeom)['open']
                    # Two independent three-second telemetry ticks while the
                    # actual mouse remains on the external scrollbar.
                    page.wait_for_timeout(6300)
                    b=page.evaluate(getgeom)
                    events=page.evaluate('MCELDiagnosticPlugins.exportHoverTrace().events')
                    from collections import Counter
                    kinds=Counter(e['kind'] for e in events)
                    case['eventKinds']=dict(kinds)
                    case['checks']['same-original-row-still-open']=b['open'] and b['rowId']==a['rowId']
                    case['checks']['original-fields-stable']=len(a['sourceChildren'])==len(b['sourceChildren']) and all(
                        all(abs(q[k]-w[k])<.75 for k in ['x','y','width','height'])
                        for q,w in zip(a['sourceChildren'],b['sourceChildren']))
                    case['checks']['rail-remains-adjacent']=bool(b['rail'] and
                        abs(b['rail']['left']-b['row']['right'])<.6)
                    case['checks']['value-only-updates-reuse-mounted-layout']=kinds['host-stable-value-reconciliation']>=1
                    case['checks']['no-row-detach-on-stable-telemetry']=kinds['dom-row-detached']==0
                    case['checks']['no-browser-errors']=not errors
                    case['errors']=errors
                    page.screenshot(path=str(OUTPUT/f'live-hover-{width}.png'))
                except Exception as e:
                    case['error']=repr(e)
                finally: page.close()
        finally: browser.close()
    report['pass']=all(c['checks'] and all(c['checks'].values()) and 'error' not in c for c in report['cases'])
    (OUTPUT/'report.json').write_text(json.dumps(report,indent=2))
    for c in report['cases']:
        print(c['width'],c['checks'],c.get('error',''))
    print('MCEL_HOVER_LIVE_GEOMETRY_'+('PASS' if report['pass'] else 'FAIL'))
    return report['pass']
if __name__=='__main__':
    raise SystemExit(0 if verify() else 2)
