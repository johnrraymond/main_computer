"""Genuine Playwright hosted Task Manager probe; never mocks fetch or page content."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .evidence import validate_host_url

WIDTHS=(320,390,560,1100,1272)
ROUTE='/applications/mcel-semantic-task-manager-generated-p3'


def _is_targeted_pid_response(response, host_url: str, pid: int) -> bool:
    """Match the response to *this* UI submission, not a preceding refresh.

    Every Task Manager notebook mode uses the same overview endpoint. Waiting
    for its URL alone can consume an unrelated in-flight response, making a
    live PID seem absent even though the targeted request has not completed.
    """
    if response.url.split('?', 1)[0] != host_url.rstrip('/') + '/api/applications/task/overview':
        return False
    try:
        request = response.request
        if request.method != 'POST':
            return False
        body = request.post_data_json
    except (AttributeError, ValueError, TypeError):
        return False
    return (isinstance(body, dict) and body.get('query') == str(pid)
            and body.get('include_all') is True
            and isinstance(body.get('limit'), int)
            and not isinstance(body.get('limit'), bool)
            and 5 <= body['limit'] <= 100)


def _matching_independent_pid_generation(rows, *, pid: int, os_birth: float) -> list[str]:
    """Verify returned identity against an OS process, not just a truthy date."""
    from .lab import _verified_host_generation
    if not isinstance(rows, list):
        return []
    return [generation for row in rows if isinstance(row, dict)
            and (generation := _verified_host_generation(row, pid=pid,
                                                          observed_epoch=os_birth))]


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
                                if page.locator('[data-mcel-selected-details="'+identity+'"]:visible').count()!=1:
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
                        # Pin an OS-observed generation before the HTTP action.
                        # A PID without this generation is not identity evidence.
                        os_birth=psutil.Process(pid).create_time()
                        page.locator('[data-mcel-mode-tabs] [data-value="all-processes"]').click(timeout=timeout_ms)
                        all_form=page.locator('form[data-intent="intent:refresh-all"]')
                        all_form.locator('[name="query"]').fill(str(pid))
                        info['pidQueryInputValue']=all_form.locator('[name="query"]').input_value()
                        if info['pidQueryInputValue'] != str(pid):
                            raise RuntimeError('pid-query-input-not-set')
                        # A generic `overview` response can be a late response
                        # to a prior view or a periodic refresh. Correlate the
                        # actual POST body with the exact submitted PID.
                        try:
                            with page.expect_response(
                                lambda r: _is_targeted_pid_response(r, host_url, pid),
                                timeout=timeout_ms) as observed:
                                all_form.locator('button[type="submit"]').click(timeout=timeout_ms)
                            pid_response=observed.value
                        except Exception as query_error:
                            info['pidQueryObserved']=False
                            info['pidQueryError']=type(query_error).__name__
                            raise RuntimeError('targeted-pid-query-http-response-not-observed') from query_error
                        info['pidQueryObserved']=True
                        info['pidQueryHttpStatus']=pid_response.status
                        if pid_response.status!=200:
                            raise RuntimeError('targeted-pid-query-http-'+str(pid_response.status))
                        source=pid_response.json()
                        rows=source.get('processes',[]) if isinstance(source,dict) else []
                        verified=_matching_independent_pid_generation(rows,pid=pid,os_birth=os_birth)
                        try:
                            still_same_process=(abs(psutil.Process(pid).create_time()-os_birth)<0.000001)
                        except (psutil.NoSuchProcess,psutil.AccessDenied):
                            still_same_process=False
                        info['osPidObservation']={
                            'expectedPid':pid,
                            'observedProcessCount':len(rows) if isinstance(rows,list) else 0,
                            'hasExpectedGeneration':bool(verified),
                            'osGenerationStable':still_same_process,
                            'correlatedRequest':True,
                        }
                        if not verified or not still_same_process:
                            # Independent read-only producer diagnostics are
                            # never accepted as a substitute for the failed
                            # generated-UI query. Preserve the blocked gate.
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
                                diagnostic_rows=diagnostic_payload.get('processes',[])
                                info['producerDiagnosticMatchingPid']=any(
                                    isinstance(item,dict) and item.get('pid')==pid
                                    for item in diagnostic_rows) if isinstance(diagnostic_rows,list) else False
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


LEGACY_ROUTE='/applications/task-manager'


def _mode_selection_evidence(legacy, generated, view: str, legacy_tab: str) -> dict:
    """Read each application's *actual* selection contract.

    The legacy notebook is an ARIA tablist (aria-selected); the generic MCEL
    generated mode switcher is an ARIA button group (aria-pressed). Both are
    valid, but their state attributes cannot be compared as if identical.
    """
    legacy_button=legacy.locator('[data-task-tab="'+legacy_tab+'"]')
    generated_button=generated.locator('[data-mcel-mode-tabs] [data-value="'+view+'"]')
    return {'name':view,
            'legacy':legacy_button.count()==1 and legacy_button.get_attribute('aria-selected')=='true',
            'generated':generated_button.count()==1 and generated_button.get_attribute('aria-pressed')=='true',
            'legacyAttribute':'aria-selected','generatedAttribute':'aria-pressed'}


def _overview_request(request, host: str, *, query: str, limit: int, include_all: bool | None = None):
    """Only count an HTTP request with the exact operator-selected monitoring inputs.

    Counting request *starts* is necessary when verifying a paused timer:
    responses from requests sent before pause may legitimately arrive later.
    """
    if request.url.split('?', 1)[0] != host + '/api/applications/task/overview':
        return False
    try:
        if request.method != 'POST':
            return False
        body = request.post_data_json
    except (AttributeError, ValueError, TypeError):
        return False
    if not isinstance(body, dict) or body.get('query') != query or body.get('limit') != limit:
        return False
    return include_all is None or body.get('include_all') is include_all


def _overview_response(response, host: str, *, query: str, limit: int, include_all: bool | None = None):
    """Correlate a completed response to the actual submitted overview request."""
    return _overview_request(response.request, host, query=query, limit=limit, include_all=include_all)


def _parity_result(rows, name, status, *, evidence=(), reason='', observations=None):
    from .evidence import STATES
    if status not in STATES or name not in rows:
        raise ValueError('invalid-parity-result')
    rows[name].update({'status': status, 'reason': reason, 'evidence': list(evidence),
                       'observations': observations or {}})


def _parity_totals(rows):
    result = {state.lower(): [key for key, row in rows.items() if row['status'] == state]
              for state in ('PASS', 'FAIL', 'BLOCKED', 'UNVERIFIED')}
    result['status'] = ('FAIL' if result['fail'] else
                        'PASS' if len(result['pass']) == len(rows) else 'BLOCKED')
    return result


def _pointer_click_with_evidence(page, locator, *, label: str, output: Path):
    """Real browser pointer click, with evidence when Playwright refuses it.

    Do not use force=True, DOM click(), or a keyboard substitution. These
    would conceal whether a pointer can actually reach an operator control.
    """
    page.bring_to_front()
    try:
        locator.scroll_into_view_if_needed(timeout=4500)
        locator.click(timeout=6500)
        return None
    except Exception as exc:
        diagnostic={'step':label,'errorType':type(exc).__name__,
                    'callLog':str(exc)[:5000]}
        try:
            diagnostic['visibility']=page.evaluate('() => document.visibilityState')
        except Exception as inner:
            diagnostic['visibilityError']=type(inner).__name__
        try:
            diagnostic['target']=locator.evaluate("""node => {
                const rect=node.getBoundingClientRect();
                const x=Math.max(0,Math.min(innerWidth-1,rect.left+rect.width/2));
                const y=Math.max(0,Math.min(innerHeight-1,rect.top+rect.height/2));
                const hit=document.elementFromPoint(x,y);
                const css=(e)=>e ? {tag:e.tagName,id:e.id||null,
                    className:String(e.className||'').slice(0,180)} : null;
                return {rect:{x:rect.x,y:rect.y,width:rect.width,height:rect.height},
                    viewport:{width:innerWidth,height:innerHeight},point:{x,y},
                    target:css(node),hit:css(hit),targetContainsHit:node.contains(hit),
                    displayed:getComputedStyle(node).display,
                    pointerEvents:getComputedStyle(node).pointerEvents};
            }""",timeout=2000)
        except Exception as inner:
            diagnostic['targetError']=type(inner).__name__+': '+str(inner)[:250]
        filename='pointer-'+''.join(c if c.isalnum() or c in '-_' else '_'
                    for c in label)+'.png'
        try:
            page.screenshot(path=str(output/filename),animations='disabled',timeout=2500)
            diagnostic['screenshot']=filename
        except Exception as inner:
            diagnostic['screenshotError']=type(inner).__name__
        return diagnostic


def _observe_inspection(legacy, generated, host, rows, output):
    '''Exercise real collection actions; a mere process table is not inspection parity.'''
    generated.bring_to_front()
    generated.locator('[data-mcel-mode-tabs] [data-value="server-processes"]').click(timeout=6500)
    legacy.bring_to_front()
    legacy.locator('[data-task-tab="server-processes"]').click(timeout=6500)
    common = generated.locator('[data-record-key]')
    old_rows = legacy.locator('#task-process-table tbody tr')
    if not common.count() or not old_rows.count():
        _parity_result(rows, 'inspection', 'BLOCKED',
                       reason='Neither interface exposed comparable live process rows')
        return
    first = common.first
    key = first.get_attribute('data-record-key')
    button = first.locator('button[data-select-key]')
    if not button.count():
        _parity_result(rows, 'inspection', 'FAIL', reason='Generated server process is not selectable')
        return
    diagnostic=_pointer_click_with_evidence(generated,button,
        label='generated-server-process-select',output=output)
    if diagnostic:
        _parity_result(rows,'inspection','BLOCKED',
            reason='Pointer selection blocked for generated server process',
            evidence=[diagnostic['screenshot']] if diagnostic.get('screenshot') else [],
            observations={'pointer':diagnostic,'serverKey':key})
        return
    generated.locator('[data-mcel-selected-details="'+key+'"]:visible').wait_for(timeout=5000)
    details = generated.locator('[data-mcel-selected-details="'+key+'"]')
    first.locator('details summary').click()
    if not first.locator('details').get_attribute('open') == '':
        _parity_result(rows, 'inspection', 'FAIL', reason='Inline process details did not open')
        return
    # The all-process collection is a separate intent/source and must actually be
    # selectable. Do not infer PASS from the server-process selection.
    generated.locator('[data-mcel-mode-tabs] [data-value="all-processes"]').click()
    legacy.locator('[data-task-tab="all-processes"]').click()
    all_form=generated.locator('form[data-intent="intent:refresh-all"]')
    all_form.locator('[name="query"]').fill('python')
    all_form.locator('[name="limit"]').fill('24')
    with generated.expect_response(
        lambda r:_overview_response(r,host,query='python',limit=24,include_all=True),
        timeout=15000) as all_response:
        all_form.locator('button[type="submit"]').click()
    if all_response.value.status!=200:
        _parity_result(rows,'inspection','FAIL',reason='All-process refresh returned non-200')
        return
    all_rows = generated.locator('[data-record-key]')
    legacy_all = legacy.locator('#task-all-process-table tbody tr')
    if not all_rows.count() or not legacy_all.count():
        _parity_result(rows, 'inspection', 'BLOCKED',
                       reason='No authentic all-process rows exposed in both interfaces')
        return
    all_button = all_rows.first.locator('button[data-select-key]')
    if not all_button.count():
        _parity_result(rows, 'inspection', 'FAIL',
                       reason='Generated all-process collection lacks an operator selection action',
                       observations={'serverSelectionKey': key,
                                     'generatedAllRows': all_rows.count(),
                                     'legacyAllRows': legacy_all.count()})
        return
    selected_key = all_rows.first.get_attribute('data-record-key')
    diagnostic=_pointer_click_with_evidence(generated,all_button,
        label='generated-all-process-select',output=output)
    if diagnostic:
        _parity_result(rows,'inspection','BLOCKED',
            reason='Pointer selection blocked for generated all-process collection',
            evidence=[diagnostic['screenshot']] if diagnostic.get('screenshot') else [],
            observations={'pointer':diagnostic,'allKey':selected_key})
        return
    if not generated.locator('[data-mcel-selected-details="'+selected_key+'"]:visible').count():
        _parity_result(rows, 'inspection', 'FAIL',
                       reason='All-process selection does not resolve to identity details')
        return
    _parity_result(rows, 'inspection', 'PASS',
                   evidence=['Server process and all-process collection selected through real UI',
                             'Selected identity has matching visible detail panel'],
                   observations={'serverKey':key, 'allKey':selected_key})


def _observe_refresh(legacy, generated, host, rows):
    '''Observe two real manual refreshes, then auto-refresh pause and resume.'''
    q='python'
    legacy.locator('[data-task-tab="server-processes"]').click()
    generated.locator('[data-mcel-mode-tabs] [data-value="server-processes"]').click()
    legacy.locator('#task-query').fill(q)
    legacy.locator('#task-limit').fill('24')
    form = generated.locator('form[data-intent="intent:refresh"]')
    form.locator('[name="query"]').fill(q)
    form.locator('[name="limit"]').fill('24')
    observations={}
    for tag,page,control in (('legacy',legacy,legacy.locator('#task-refresh')),
                             ('generated',generated,form.locator('button[type="submit"]'))):
        count=0
        for _ in range(2):
            with page.expect_response(lambda r: _overview_response(r,host,query=q,limit=24,
                                            include_all=False),timeout=15000) as waiting:
                control.click()
            response=waiting.value
            if response.status!=200:
                _parity_result(rows,'refresh','FAIL',reason=tag+' refresh returned HTTP '+str(response.status))
                return
            count+=1
        observations[tag+'ManualRequests']=count
    # Observe *initiated* requests, not only responses. A request launched just
    # before unchecking may finish after pause; that is not timer activity while
    # paused. Register both event kinds before changing the controls so an
    # unrelated response cannot be reclassified as a newly sent request.
    initiated={'paused':{'legacy':0,'generated':0},
               'resumed':{'legacy':0,'generated':0}}
    completed={'paused':{'legacy':0,'generated':0},
               'resumed':{'legacy':0,'generated':0}}
    phase={'value':'transition'}
    def request_watcher(tag):
        def callback(request):
            state=phase['value']
            if state in initiated and _overview_request(request,host,query=q,limit=24,include_all=False):
                initiated[state][tag]+=1
        return callback
    def response_watcher(tag):
        def callback(response):
            state=phase['value']
            if state in completed and _overview_response(response,host,query=q,limit=24,include_all=False):
                completed[state][tag]+=1
        return callback
    request_watchers={tag:request_watcher(tag) for tag in ('legacy','generated')}
    response_watchers={tag:response_watcher(tag) for tag in ('legacy','generated')}
    pages={'legacy':legacy,'generated':generated}
    for tag,page in pages.items():
        page.on('request',request_watchers[tag])
        page.on('response',response_watchers[tag])
    generated_auto=generated.locator('[data-mcel-refresh-toggle]')
    try:
        legacy.locator('#task-auto-refresh').uncheck()
        if generated_auto.get_attribute('aria-pressed')=='true':
            generated_auto.click()
        if legacy.locator('#task-auto-refresh').is_checked() or generated_auto.get_attribute('aria-pressed')!='false':
            _parity_result(rows,'refresh','FAIL',reason='Automatic refresh pause control did not disable both timers',
                           observations=observations)
            return
        # Start the full paused observation window immediately once both
        # controls confirm pause. Prior in-flight responses remain diagnostic
        # only; only *new* request events can fail this gate.
        phase['value']='paused'
        legacy.bring_to_front()
        legacy.wait_for_timeout(5600)
        phase['value']='transition'
        paused=dict(initiated['paused'])
        legacy.locator('#task-auto-refresh').check()
        generated_auto.click()
        if not legacy.locator('#task-auto-refresh').is_checked() or generated_auto.get_attribute('aria-pressed')!='true':
            _parity_result(rows,'refresh','FAIL',reason='Periodic refresh controls would not resume',
                           observations=observations)
            return
        # Chromium may throttle five-second timers on background tabs. Each UI
        # needs its own foreground window; a background-tab timeout is not a
        # demonstrated application parity failure. Keep actual network events
        # and do not synthesize refresh requests or manipulate application state.
        foreground={}
        phase['value']='resumed'
        for tag, page in pages.items():
            page.bring_to_front()
            foreground[tag]=page.evaluate("""() => ({
                visibility: document.visibilityState,
                activeApp: document.body.dataset.activeApp || null
            })""")
            if foreground[tag]['visibility'] != 'visible':
                # If foreground is unavailable, retain BLOCKED evidence rather
                # than counting an inactive-tab timer as broken.
                continue
            page.wait_for_timeout(6500)
        phase['value']='transition'
        active=dict(initiated['resumed'])
        observations['foreground']=foreground
    finally:
        phase['value']='transition'
        for tag,page in pages.items():
            page.remove_listener('request',request_watchers[tag])
            page.remove_listener('response',response_watchers[tag])
    observations.update({'pausedRequests':paused,'resumedRequests':active,
                         'pausedCompletedResponses':dict(completed['paused']),
                         'resumedCompletedResponses':dict(completed['resumed']),
                         'refreshPeriodSeconds':5,'counting':'correlated-http-request-initiation'})
    if any(v>0 for v in paused.values()):
        _parity_result(rows,'refresh','FAIL',reason='Automatic refresh request initiated while paused',
                       observations=observations)
    elif all(v>0 for v in active.values()) and all(
            observations.get('foreground',{}).get(tag,{}).get('visibility')=='visible'
            for tag in ('legacy','generated')):
        _parity_result(rows,'refresh','PASS',
                       evidence=['Two correlated manual requests per UI',
                                 'No new matching requests initiated while paused',
                                 'A new matching periodic request initiated per UI after resume'],
                       observations=observations)
    else:
        _parity_result(rows,'refresh','BLOCKED',
                       reason='Foreground timer request unobserved; inspect activeApp and visibility diagnostics',
                       evidence=['Two correlated manual requests per UI'],observations=observations)


def _observe_responsive(legacy, generated, rows, output):
    """Independently exercise pointer navigation, focus and overflow at five widths.

    A blocked legacy click must not suppress the generated UI's measurements.
    Pointer obstruction never becomes a PASS and is never bypassed with a
    forced click or programmatic tab activation.
    """
    observations = []
    for width in WIDTHS:
        one = {'width': width}
        for tag, page, tab in (
            ('legacy', legacy, '[data-task-tab="all-processes"]'),
            ('generated', generated, '[data-mcel-mode-tabs] [data-value="all-processes"]'),
        ):
            page.bring_to_front()
            page.set_viewport_size({'width': width, 'height': 800})
            button = page.locator(tab)
            try:
                # The inspection scenario can leave all-processes selected.
                # Make an actual round trip via the server-processes control.
                server_tab = ('[data-task-tab="server-processes"]' if tag == 'legacy'
                              else '[data-mcel-mode-tabs] [data-value="server-processes"]')
                back = page.locator(server_tab)
                back.scroll_into_view_if_needed(timeout=6000)
                back.click(timeout=6000)
                button.scroll_into_view_if_needed(timeout=6000)
                button.click(timeout=6000)
                button.focus()
                focus = button.evaluate('(n)=>document.activeElement===n')
                selected = button.get_attribute(
                    'aria-selected' if tag == 'legacy' else 'aria-pressed') == 'true'
                horizontal = page.evaluate(
                    """() => Math.max(0, document.documentElement.scrollWidth-innerWidth)""")
                one[tag] = {'focus': focus, 'selected': selected, 'overflowPx': horizontal,
                            'tabVisible': button.is_visible(),
                            'visibility': page.evaluate('() => document.visibilityState')}
                page.screenshot(path=str(output / (tag + '-' + str(width) + '.png')),
                                full_page=True, animations='disabled')
            except Exception as exc:
                diagnostic = {'errorType': type(exc).__name__, 'error': str(exc)[:550]}
                try:
                    diagnostic['visibility'] = page.evaluate('() => document.visibilityState')
                except Exception as diag_exc:
                    diagnostic['visibilityError'] = str(diag_exc)[:180]
                try:
                    diagnostic['tabBox'] = button.bounding_box(timeout=1500)
                    diagnostic['tabVisible'] = button.is_visible(timeout=1500)
                    diagnostic['tabSelected'] = button.get_attribute(
                        'aria-selected' if tag == 'legacy' else 'aria-pressed', timeout=1500)
                    diagnostic['hitTarget'] = button.evaluate("""n => {
                        const r=n.getBoundingClientRect();
                        const x=Math.max(0,Math.min(innerWidth-1,r.left+r.width/2));
                        const y=Math.max(0,Math.min(innerHeight-1,r.top+r.height/2));
                        const hit=document.elementFromPoint(x,y);
                        return {point:[x,y],element:hit?.tagName||null,
                            id:hit?.id||null,className:String(hit?.className||'').slice(0,160),
                            targetContainsHit:n.contains(hit)};
                    }""")
                except Exception as diag_exc:
                    diagnostic['diagnosticError'] = str(diag_exc)[:180]
                try:
                    page.screenshot(path=str(output / (tag + '-' + str(width) + '-blocked.png')),
                                    animations='disabled', timeout=4000)
                    diagnostic['screenshot'] = tag + '-' + str(width) + '-blocked.png'
                except Exception:
                    pass
                one[tag] = diagnostic
                # Do not stop here: the other interface and other widths still
                # provide independently valuable acceptance evidence.
        observations.append(one)

    blockers = {tag: [row['width'] for row in observations
                     if 'errorType' in row[tag]] for tag in ('legacy', 'generated')}
    completed = {tag: [row['width'] for row in observations
                       if 'errorType' not in row[tag]] for tag in ('legacy', 'generated')}
    evidence = [row[tag]['screenshot'] for row in observations
                for tag in ('legacy', 'generated') if row[tag].get('screenshot')]
    evidence += [tag + '-' + str(width) + '.png'
                 for row in observations for width in (row['width'],)
                 for tag in ('legacy', 'generated') if 'errorType' not in row[tag]]
    details = {'viewports': observations, 'completedWidths': completed,
               'obstructedWidths': blockers}
    if blockers['legacy'] or blockers['generated']:
        issue = [tag + ':' + ','.join(str(w) + 'px' for w in blockers[tag])
                 for tag in ('legacy', 'generated') if blockers[tag]]
        _parity_result(rows, 'responsive', 'BLOCKED',
                       reason='Pointer navigation obstructed (' + '; '.join(issue) +
                       '); both interfaces examined independently',
                       evidence=evidence, observations=details)
        return
    if any(not row[tag]['focus'] or not row[tag]['selected'] or not row[tag]['tabVisible']
           or row[tag]['overflowPx'] > 2 or row[tag]['visibility'] != 'visible'
           for row in observations for tag in ('legacy', 'generated')):
        _parity_result(rows, 'responsive', 'FAIL',
                       reason='Tab selection, focus, visibility or horizontal overflow failed',
                       evidence=evidence, observations=details)
    else:
        _parity_result(rows, 'responsive', 'PASS',
                       reason='Pointer navigation, focus and overflow passed at five widths; human review still required',
                       evidence=evidence, observations=details)


def live_readonly_parity(host_url: str, output: Path):
    '''Compare real interface operations; NEVER submit consequential requests to this host.

    Every workflow is independently accounted for; failures do not become
    unverified, and missing lab/model/controller evidence cannot become PASS.
    '''
    from datetime import datetime, timezone
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    from .evidence import source_ledger, digest
    from main_computer.mcel_semantic_generated_host import render_p3_generated_page
    host=validate_host_url(host_url)
    output.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parents[2]
    ledger=source_ledger(root)
    rows={r['workflow']:{'workflow':r['workflow'],'status':'UNVERIFIED',
           'requirement':r['productionRequirement'],'evidence':[], 'reason':'Not executed'}
          for r in ledger['rows']}
    result={'schema':'mcel.task-manager-live-parity.v2','host':host,
       'evaluatedAt':datetime.now(timezone.utc).isoformat(),
       'sourceHashes':{'legacyMarkup':digest(root/'main_computer/web/applications/apps/task-manager.html'),
          'semanticApplication':digest(root/'mcel_apps/task-manager/application.js'),
          'generatedHost':digest(root/'main_computer/mcel_semantic_generated_host.py'),
          'sharedRenderer':digest(root/'main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js')},
       'network':'Real Playwright page.goto and HTTP requests, never mocked',
       'productionMutations':False,'controllerFidelity':'UNVERIFIED','rows':rows}
    try:
        with sync_playwright() as player:
            browser,_=launch_semantic_browser(player)
            try:
                legacy=browser.new_page(viewport={'width':1100,'height':800})
                generated=browser.new_page(viewport={'width':1100,'height':800})
                problems=[]
                for page,tag in ((legacy,'legacy'),(generated,'generated')):
                    response=page.goto(host+(LEGACY_ROUTE if tag=='legacy' else ROUTE),
                                       wait_until='load',timeout=20000)
                    if response is None or response.status!=200:
                        problems.append(tag+'-route-not-200');continue
                    ready='#task-manager-app' if tag=='legacy' else '#mcel-workbench'
                    if not page.locator(ready).count():
                        problems.append(tag+'-not-mounted');continue
                    if tag=='generated':
                        expected=hashlib.sha256(render_p3_generated_page().encode()).hexdigest()
                        if hashlib.sha256(response.body()).hexdigest()!=expected:
                            problems.append('generated-host-source-mismatch');continue
                    page.screenshot(path=str(output/(tag+'-desktop.png')),full_page=True)
                if problems:
                    result['reason']=';'.join(problems)
                    for name in ('overview-parameters','four-views','refresh','inspection','responsive'):
                        _parity_result(rows,name,'BLOCKED',reason=result['reason'])
                else:
                    captures={}
                    query='python';limit=24
                    def correlated(response):
                        if not _overview_response(response,host,query=query,limit=limit,include_all=False):
                            return False
                        try:return response.request.post_data_json.get('include_connections') is True
                        except (AttributeError,TypeError,ValueError):return False
                    legacy.locator('#task-limit').fill(str(limit))
                    legacy.locator('#task-include-connections').check()
                    legacy.locator('#task-query').fill(query)
                    with legacy.expect_response(correlated,timeout=15000) as pending:
                        legacy.locator('#task-refresh').click()
                    captures['legacyOverviewHttp']=pending.value.status
                    generated.locator('[data-mcel-mode-tabs] [data-value="server-processes"]').click()
                    form=generated.locator('form[data-intent="intent:refresh"]')
                    form.locator('[name="query"]').fill(query)
                    form.locator('[name="limit"]').fill(str(limit))
                    form.locator('[name="includeConnections"]').fill('true')
                    with generated.expect_response(correlated,timeout=15000) as pending:
                        form.locator('button[type="submit"]').click()
                    captures['generatedOverviewHttp']=pending.value.status
                    good=captures['legacyOverviewHttp']==200 and captures['generatedOverviewHttp']==200
                    _parity_result(rows,'overview-parameters','PASS' if good else 'FAIL',
                        evidence=['Two correlated real HTTP overview requests'] if good else [],
                        reason='' if good else 'Correlated overview request returned non-200', observations=captures)
                    views=[]
                    for name,legacy_name in [('server-processes','server-processes'),
                                             ('all-processes','all-processes'),
                                             ('connections','connections'),('cpu-gpu','hardware')]:
                        legacy.locator('[data-task-tab="'+legacy_name+'"]').click()
                        generated.locator('[data-mcel-mode-tabs] [data-value="'+name+'"]').click()
                        views.append({'name':name,
                          'legacy':legacy.locator('[data-task-tab="'+legacy_name+'"]').get_attribute('aria-selected')=='true',
                          'generated':generated.locator('[data-mcel-mode-tabs] [data-value="'+name+'"]').get_attribute('aria-pressed')=='true'})
                    _parity_result(rows,'four-views','PASS' if all(v['legacy'] and v['generated'] for v in views) else 'FAIL',
                        evidence=['Real notebook controls selected all four modes'],observations={'views':views})
                    for name,fn in (('refresh',lambda:_observe_refresh(legacy,generated,host,rows)),
                                    ('inspection',lambda:_observe_inspection(legacy,generated,host,rows,output)),
                                    ('responsive',lambda:_observe_responsive(legacy,generated,rows,output))):
                        try:fn()
                        except Exception as exc:
                            _parity_result(rows,name,'BLOCKED',reason='Scenario could not complete: '+type(exc).__name__+': '+str(exc)[:2500])
                    result['reason']='Read-only UI comparison complete. Consequential workflows require a verified lab host; AI requires explicit operator authorization.'
            finally:browser.close()
    except Exception as exc:
        result['reason']='Browser/transport blocked: '+type(exc).__name__+': '+str(exc)[:350]
    from .evidence import evidence_manifest
    result['artifactManifest']=evidence_manifest(output)
    for name, reason in {
        'process-effects':'No verified lab host with two UI-owned disposable targets',
        'server-lifecycle':'Real PowerShell controller target ownership remains unverified',
        'scheduling':'No isolated HTTP host proved schedule UI persistence and receipts',
        'ai-brief':'No explicitly authorized configured real model invocation',
        'failure-lifecycle':'Consequential refusals and deferred receipts require an isolated UI host',
    }.items():
        if rows[name]['status']=='UNVERIFIED':
            _parity_result(rows,name,'BLOCKED',reason=reason)
    totals=_parity_totals(rows)
    result.update({'passed':totals['pass'],'failed':totals['fail'],
                   'blocked':totals['blocked'],'unverified':totals['unverified'],
                   'status':totals['status']})
    # Do not claim the read-only campaign is complete while any of its five
    # workflows is blocked. Preserve specific transport errors if present.
    if result.get('reason','').startswith('Read-only UI comparison complete.'):
        read_only=('overview-parameters','four-views','refresh','inspection','responsive')
        incomplete=[name for name in read_only if rows[name]['status']!='PASS']
        if incomplete:
            result['reason']=(
                'Read-only UI comparison incomplete: '+', '.join(incomplete)
                + '; consequential and AI workflows require separate evidence'
            )
        else:
            result['reason']='Five read-only UI workflows passed; consequential and AI workflows remain blocked'
    return result
