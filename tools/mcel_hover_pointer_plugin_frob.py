"""Read-only browser gate for MCEL local hover diagnostics plugin.

    python -m tools.mcel_hover_pointer_plugin_frob

Uses offline L18-01 semantic fixtures; opt-in recording never transmits events.
"""
from __future__ import annotations
import json
from datetime import datetime,timezone
from pathlib import Path
from playwright.sync_api import sync_playwright
from tools.mcel_lifecycle_taguchi_acid_frob import install,ROOT
from tools.mcel_patch14_7_capability_negotiation_frob import _chrome

OUTPUT=ROOT/'runtime/reports/mcel-hover-pointer-plugin'

def verify(output=OUTPUT):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    report={'schema':'mcel.hover-pointer-plugin-gate.v1','dateUtc':datetime.now(timezone.utc).isoformat(),'cases':[]}
    with sync_playwright() as p:
        browser=_chrome(p,None)
        try:
            for width in (390,1100):
                page=browser.new_page(viewport={'width':width,'height':480},reduced_motion='reduce')
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                case={'width':width,'checks':{}};report['cases'].append(case)
                def check(k,v):case['checks'][k]=bool(v)
                try:
                    install(page)
                    page.add_script_tag(path=str(ROOT/'main_computer/web/applications/scripts/mcel-diagnostic-plugins.js'))
                    page.evaluate("() => document.querySelectorAll(\'.mcel-lifecycle-acid__gallery-options input[type=checkbox]\').forEach(x => x.checked=false)")
                    check('plugin-available-but-opt-in',page.evaluate("""() => {
                      const p=MCELDiagnosticPlugins.list().find(p=>p.id==='mcel-hover-pointer');
                      return p && !p.enabled && p.availability==='ready';}"""))
                    page.locator('#mcel-lifecycle-acid-hover-record').click()
                    page.wait_for_function("MCELDiagnosticPlugins.list().some(x=>x.id==='mcel-hover-pointer'&&x.enabled)")
                    page.locator('[data-case-id="L18-01"] mcel-collection').scroll_into_view_if_needed()
                    row=page.locator('[data-case-id="L18-01"] mcel-row').first
                    row.hover(force=True);page.wait_for_timeout(1220)
                    r=row.bounding_box();page.mouse.move(r['x']+35,r['y']+35)
                    page.wait_for_timeout(80)
                    check('one-real-row-hover-open',page.evaluate("""() =>
                      document.querySelectorAll('[data-case-id="L18-01"] mcel-row:popover-open').length===1 &&
                      !document.querySelector('[data-case-id="L18-01"] mcel-collection').matches(':popover-open')"""))
                    events=page.evaluate('MCELDiagnosticPlugins.exportHoverTrace().events')
                    kinds={e['kind'] for e in events}
                    check('browser-cursor-recorded','browser-pointermove' in kinds)
                    check('host-geometry-plan-recorded','host-geometry-plan' in kinds)
                    check('host-commit-recorded','host-open-committed' in kinds)
                    check('painted-geometry-recorded','paint-frame' in kinds)
                    check('cursor-contains-no-field-text',all('textContent' not in json.dumps(e) for e in events))
                    page.evaluate('''()=>{const row=document.querySelector('mcel-row:popover-open');row.scrollTop=row.scrollHeight-row.clientHeight;}''')
                    page.wait_for_timeout(175)
                    page.mouse.wheel(0,12)
                    page.wait_for_timeout(180)
                    page.mouse.wheel(0,200)
                    page.wait_for_timeout(110)
                    kinds={e['kind'] for e in page.evaluate('MCELDiagnosticPlugins.exportHoverTrace().events')}
                    check('scroll-owner-handoff-recorded','host-scroll-owner-change' in kinds)
                    page.mouse.move(1,1);page.wait_for_timeout(1240)
                    events=page.evaluate('MCELDiagnosticPlugins.exportHoverTrace().events')
                    kinds={e['kind'] for e in events}
                    check('browser-popover-close-recorded',any(e['kind']=='browser-popover-toggle' and e.get('newState')=='closed' for e in events))
                    page.locator('#mcel-lifecycle-acid-hover-record').click()
                    page.wait_for_function("!MCELDiagnosticPlugins.list().some(x=>x.id==='mcel-hover-pointer'&&x.enabled)")
                    check('stop-keeps-exportable-trace',page.evaluate('MCELDiagnosticPlugins.exportHoverTrace().events.length>0'))
                    check('no-browser-errors',not errors)
                    case['eventCount']=len(events);case['kinds']=sorted(kinds)
                except Exception as e:
                    case['error']=f'{type(e).__name__}: {e}'
                finally:page.close()
        finally:browser.close()
    report['status']='PASS' if all(not c.get('error') and all(c['checks'].values()) for c in report['cases']) else 'FAIL'
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('MCEL_HOVER_POINTER_PLUGIN_'+report['status'])
    for case in report['cases']:
        print(case['width'],{k:v for k,v in case['checks'].items() if not v},case.get('error',''),case.get('eventCount'))
    print('report',output/'report.json')
    return report
if __name__=='__main__':
    raise SystemExit(0 if verify()['status']=='PASS' else 1)
