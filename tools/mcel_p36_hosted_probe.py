"""Genuine Playwright hosted Task Manager probe; never mocks fetch or page content."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.mcel_p36_evidence import validate_host_url

WIDTHS=(320,390,560,1100,1272)
ROUTE='/applications/mcel-semantic-task-manager-generated-p3'


def probe(host_url:str, out:Path, timeout_ms=12000):
    from playwright.sync_api import sync_playwright
    from main_computer.mcel_semantic_generated_host import render_p3_generated_page
    from tools.mcel_phase2b_browser import launch_semantic_browser
    host_url=validate_host_url(host_url)
    out.mkdir(parents=True,exist_ok=True)
    result={'route':ROUTE,'host':host_url,'widths':{},'network':[],
            'transport':'Playwright page.goto() real network; no route intercept or fetch replacement'}
    expected=hashlib.sha256(render_p3_generated_page().encode('utf-8')).hexdigest()
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        try:
            for width in WIDTHS:
                page=browser.new_page(viewport={'width':width,'height':800},accept_downloads=False)
                errors=[]; responses=[]
                page.on('pageerror',lambda e:errors.append(str(e)[:300]))
                page.on('response',lambda r:responses.append({'path':r.url.split('?')[0].split(host_url)[-1],
                        'status':r.status,'method':r.request.method}) if r.url.startswith(host_url) else None)
                info={'width':width,'errors':errors}
                try:
                    response=page.goto(host_url+ROUTE,wait_until='load',timeout=timeout_ms)
                    if not response or response.status!=200:raise RuntimeError('hosted-route-not-200')
                    served=page.content()
                    # page.content may reserialize markup: use raw HTTP body instead.
                    source=page.locator('#mcel-generated-data').text_content()
                    actual=page.evaluate('''() => ({mounted:!!window.__mcelP3,
                      appId:window.__mcelP3?.appId||null,
                      fingerprint:window.__mcelP3?.composition?.fingerprint||null,
                      refusal:document.getElementById('mcel-workbench-status')?.dataset.error||null})''')
                    info.update(actual)
                    if not actual['mounted'] or actual['appId']!='semantic-task-manager-generated-p3' or actual['refusal']:
                        raise RuntimeError('generated-runtime-not-mounted')
                    embedded=json.loads(source)
                    if embedded['composition']['fingerprint']!=actual['fingerprint']:
                        raise RuntimeError('embedded-composition-mismatch')
                    info['irAppId']=embedded['ir']['application']['appId']
                    info['compositionFingerprint']=actual['fingerprint']
                    # A server-rendered source hash is the exact contract for catching a stale server build.
                    raw=response.body()
                    info['hostPageSha256']=hashlib.sha256(raw).hexdigest()
                    info['expectedPageSha256']=expected
                    if info['hostPageSha256']!=expected:raise RuntimeError('STALE_HOST:host-page-source-does-not-match-checkout')
                    info['tabCount']=page.locator('[data-mcel-mode-tabs] button').count()
                    if info['tabCount']!=4:raise RuntimeError('not-four-views')
                    # Four real HTTP observations, one per semantic notebook mode.
                    views=(('server-processes','refresh'),('all-processes','refresh-all'),
                           ('connections','refresh-connections'),('cpu-gpu','refresh-hardware'))
                    info['views']={}
                    for view,intent in views:
                        if view!='server-processes':
                            page.locator('[data-mcel-mode-tabs] [data-value="'+view+'"]').click(timeout=timeout_ms)
                        form=page.locator('form[data-intent="intent:'+intent+'"]')
                        with page.expect_response(lambda r: '/api/applications/task/overview' in r.url,timeout=timeout_ms) as awaiting:
                            form.locator('button[type="submit"]').click(timeout=timeout_ms)
                        r=awaiting.value
                        page.wait_for_timeout(250)
                        info['views'][view]={'httpStatus':r.status,
                              'request':'POST /api/applications/task/overview',
                              'visible':form.is_visible(),
                              'rows':page.locator('[data-record-key]').count()}
                        if r.status!=200:raise RuntimeError('host-overview-http-'+str(r.status))
                        if view=='server-processes':
                            info['selectionContinuity']='UNVERIFIED'
                            rows=page.locator('[data-record-key]')
                            if rows.count():
                                first=rows.first
                                identity=first.get_attribute('data-record-key')
                                first.locator('summary').click()
                                first.locator('[data-select-key]').click()
                                page.wait_for_timeout(120)
                                if page.locator('[data-mcel-selected-details="'+identity+'"]').count()!=1:
                                    raise RuntimeError('selected-details-not-reflected')
                                info['selectionContinuity']='SELECTED_AND_EXPANDED'
                            else:
                                raise RuntimeError('no-real-server-process-to-exercise-selection')
                    info['renderedProcessRows']=info['views']['server-processes']['rows']
                    refresh_toggle=page.locator('[data-mcel-refresh-toggle]')
                    if refresh_toggle.count()!=1:raise RuntimeError('automatic-monitoring-control-unavailable')
                    refresh_toggle.click(timeout=timeout_ms)
                    if refresh_toggle.get_attribute('aria-pressed')!='false':
                        raise RuntimeError('monitor-pause-failed')
                    refresh_toggle.click(timeout=timeout_ms)
                    if refresh_toggle.get_attribute('aria-pressed')!='true':
                        raise RuntimeError('monitor-resume-failed')
                    info['monitorPauseResume']='PASS'

                    page.screenshot(path=str(out/f'viewport-{width}.png'),full_page=True,animations='disabled')
                    info['horizontalOverflowPx']=page.evaluate('''() => Math.max(0,document.documentElement.scrollWidth-innerWidth)''')
                    info['pageErrorCount']=len(errors)
                    info['overviewHttpStatuses']=[r['status'] for r in responses if r['path'].startswith('/api/applications/task/overview')]
                    if len(info['overviewHttpStatuses'])<4 or not all(code==200 for code in info['overviewHttpStatuses']):
                        raise RuntimeError('four-real-overview-http-requests-not-seen')
                    if info['horizontalOverflowPx']>2:raise RuntimeError('horizontal-content-overflow')
                    if width==1100:
                        # Independent same-machine PID evidence is not obtained
                        # from the rendered DOM alone. A separate observation
                        # must prove an OS process known to the running tester.
                        import os
                        import psutil
                        pid=os.getpid()
                        page.locator('[data-mcel-mode-tabs] [data-value="all-processes"]').click(timeout=timeout_ms)
                        all_form=page.locator('form[data-intent="intent:refresh-all"]')
                        all_form.locator('[name="query"]').fill(str(pid))
                        with page.expect_response(lambda r:'/api/applications/task/overview' in r.url,timeout=timeout_ms) as observed:
                            all_form.locator('button[type="submit"]').click(timeout=timeout_ms)
                        source=observed.value.json()
                        matching=[v for v in source.get('processes',[]) if v.get('pid')==pid and v.get('created_at')]
                        if not matching or not psutil.pid_exists(pid):
                            info['osPidObservation']={
                                'expectedPid':pid,'expectedPidExists':psutil.pid_exists(pid),
                                'observedProcessCount':len(source.get('processes',[])),
                                'hasExpectedGeneration':bool(matching),
                            }
                            # An opt-in read-only diagnostic request exposes the
                            # real producer state without granting authority or
                            # pretending the missing observation succeeded.
                            try:
                                diagnostic_response=page.request.post(
                                    host_url+'/api/applications/task/overview',
                                    data={'query':str(pid),'include_all':True,
                                          'include_connections':False,'limit':100,
                                          'mcel_semantic_shadow':True},timeout=8000)
                                diagnostic_payload=diagnostic_response.json()
                                info['producerDiagnostic']=diagnostic_payload.get(
                                    '_mcel_task_process_producer',{})
                                info['producerDiagnosticHttpStatus']=diagnostic_response.status
                            except Exception as diagnostic_error:
                                info['producerDiagnosticError']=type(diagnostic_error).__name__
                            raise RuntimeError('live-process-identity-not-confirmed-by-independent-os-pid')
                        info['osPidConfirmed']=True
                        info['osPid']=pid
                        info['creationTimestampPresent']=True
                        # A harmless server-status action with no CSRF must
                        # fail, even though it has no destructive side effect.
                        refused=page.request.post(host_url+'/api/applications/task/action',
                            data={'action':'server_status','confirm':False},
                            headers={'Origin':host_url,'Content-Type':'application/json'})
                        info['unauthenticatedActionStatus']=refused.status
                        if refused.status!=403:raise RuntimeError('mutation-without-csrf-was-not-refused')
                    if errors:raise RuntimeError('browser-page-error')
                    if info['horizontalOverflowPx']>2:raise RuntimeError('horizontal-content-overflow')
                    if width==1100:
                        info['liveIdentityRows']=[v[:100] for v in page.locator('[data-record-key]').evaluate_all('(els)=>els.map(x=>x.dataset.recordKey)')[:15]]
                    # The full browser scenario is exercised again by the controlled P3 suite.
                    info['passed']=True
                except Exception as exc:
                    info['passed']=False;info['reason']=str(exc)[:500]
                    try:page.screenshot(path=str(out/f'failure-{width}.png'),full_page=True,timeout=2000)
                    except Exception:pass
                finally:
                    result['widths'][str(width)]=info
                    result['network']+=responses[:30]
                    page.close()
        finally:browser.close()
    result['passed']=all(x.get('passed') for x in result['widths'].values())
    return result
