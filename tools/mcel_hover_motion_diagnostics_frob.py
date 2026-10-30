"""L18 hover motion diagnostics gate (instrumentation only).

    python -m tools.mcel_hover_motion_diagnostics_frob

Verifies the plugin detects a deliberately mispositioned external scrollbar,
correlates real pointer/wheel/host decisions and preserves the original row.
The induced style change is confined to the disposable Chromium fixture.
"""
from __future__ import annotations
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_lifecycle_taguchi_acid_frob import install, ROOT
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

OUTPUT=ROOT/'runtime/reports/mcel-hover-motion-diagnostics'

def verify():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    report={'schema':'mcel.hover-motion-diagnostics-gate.v1','cases':[]}
    with sync_playwright() as playwright:
        browser=_chrome(playwright,None)
        try:
            for width in (390,1100):
                page=browser.new_page(viewport={'width':width,'height':650},reduced_motion='reduce')
                errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                c={'width':width,'checks':{}};report['cases'].append(c)
                def check(name,value):c['checks'][name]=bool(value)
                try:
                    install(page)
                    page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts/mcel-diagnostic-plugins.js'))
                    page.evaluate('''async()=>{await MCELDiagnosticPlugins.enable('mcel-hover-pointer')}''')
                    page.locator('[data-case-id="L18-01"] mcel-collection').scroll_into_view_if_needed()
                    row=page.locator('[data-case-id="L18-01"] mcel-row').first
                    row.hover(force=True);page.wait_for_timeout(1170)
                    geom=page.evaluate('''()=>{
                      const row=document.querySelector('[data-case-id="L18-01"] mcel-row:popover-open');
                      const rail=document.querySelector('.mcel-unclip-row-scroll-track:popover-open');
                      if(!row||!rail)return null;
                      const r=row.getBoundingClientRect(),t=rail.getBoundingClientRect();
                      return {rowLeft:r.left,rowRight:r.right,rowTop:r.top,railLeft:t.left,
                        railWidth:t.width,railTop:t.top,rowId:row.dataset.rowId};
                    }''')
                    check('row-and-rail-open',geom is not None)
                    if geom is None:raise RuntimeError('Row did not open')
                    check('rail-flush-before-test',abs(geom['railLeft']-geom['rowRight'])<.6)
                    page.mouse.move(geom['rowLeft']+35,geom['rowTop']+30)
                    # Move only the scrollbar within the disposable test page.
                    page.evaluate('''()=>{const t=document.querySelector('.mcel-unclip-row-scroll-track:popover-open');
                      t.dataset.diagnosticOriginalLeft=t.style.left;
                      t.style.left=(parseFloat(t.style.left)+22)+'px';}''')
                    page.wait_for_timeout(400)
                    events=page.evaluate('MCELDiagnosticPlugins.exportHoverTrace().events')
                    warnings=[e for e in events if e['kind']=='geometry-invariant-warning']
                    check('22px-rail-failure-detected',any(e.get('reason')=='rail-disconnected' and abs(e.get('railGap',0))>20 for e in warnings))
                    page.evaluate('''()=>{const t=document.querySelector('.mcel-unclip-row-scroll-track:popover-open');
                      t.style.left=t.dataset.diagnosticOriginalLeft;delete t.dataset.diagnosticOriginalLeft;}''')
                    page.wait_for_timeout(180)
                    page.mouse.wheel(0,235)
                    page.wait_for_timeout(160)
                    # Start an overscroll attempt at the actual row boundary.
                    page.evaluate('''()=>{const r=document.querySelector('mcel-row:popover-open');
                      if(r)r.scrollTop=Math.max(0,r.scrollHeight-r.clientHeight);}''')
                    page.wait_for_timeout(180)
                    page.mouse.wheel(0,300)
                    page.wait_for_timeout(200)
                    data=page.evaluate('MCELDiagnosticPlugins.exportHoverTrace()')
                    kinds={e['kind'] for e in data['events']}
                    check('pointer-hit-region-tracked','pointer-hit-region-change' in kinds)
                    check('wheel-before-after-correlated','wheel-before' in kinds and 'wheel-after' in kinds)
                    ids={e['seq'] for e in data['events'] if e['kind']=='wheel-before'}
                    afterIds={e['seq'] for e in data['events'] if e['kind']=='wheel-after'}
                    check('wheel-sequences-join',bool(ids&afterIds))
                    check('handoff-decision-with-reason',any(e['kind']=='host-wheel-arbitration' and e.get('extra',{}).get('reason')
                                                             in ('row-scroll-consumed','boundary-arbitration','parent-owned-forward') for e in data['events']))
                    check('changed-geometry-recorded','geometry-delta' in kinds)
                    check('automatic-summary-recorded',data.get('summary',{}).get('eventCounts',{}).get('geometry-invariant-warning',0)>0)
                    check('no-field-value-leak',all('textContent' not in json.dumps(e) for e in data['events']))
                    check('browser-errors-absent',not errors)
                    c['counts']={k:data['summary']['eventCounts'].get(k,0) for k in
                      ['paint-frame','geometry-delta','geometry-invariant-warning','wheel-before','wheel-after','host-wheel-arbitration']}
                    c['errorMessages']=errors
                    (OUTPUT/f'trace-{width}.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
                except Exception as error:c['exception']=repr(error)
                finally:page.close()
        finally:browser.close()
    report['pass']=all(c['checks'] and all(c['checks'].values()) and 'exception' not in c for c in report['cases'])
    (OUTPUT/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    for case in report['cases']:print(case['width'],case['checks'],case.get('exception',''))
    print('MCEL_HOVER_MOTION_DIAGNOSTICS_'+('PASS' if report['pass'] else 'FAIL'))
    return report['pass']
if __name__=='__main__':raise SystemExit(0 if verify() else 1)
