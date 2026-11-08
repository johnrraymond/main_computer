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


LEGACY_ROUTE='/applications/task-manager?mcel-shell-child=1'


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


def _stable_process_identity(before, after, *, producer_rows=None):
    """Pin a PID plus *creation timestamp*, never just an unstable row index.

    Producer correlation is mandatory for a newly queried all-process list.
    No candidate substitution is allowed after selecting a key.
    """
    seen=set(after)
    valid=[]
    for key in before:
        if not isinstance(key,str) or key not in seen:
            continue
        raw_pid,sep,created_at=key.partition(':')
        if not sep or not raw_pid.isdecimal() or not created_at or created_at=='unknown':
            continue
        pid=int(raw_pid)
        if producer_rows is not None and not any(
            isinstance(item,dict) and item.get('pid')==pid and
            str(item.get('created_at') or '')==created_at for item in producer_rows
        ):
            continue
        valid.append(key)
    return valid[0] if valid else None


def _visible_process_keys(page):
    return page.locator('[data-record-key]:visible').evaluate_all(
        '(nodes) => nodes.map(node => node.getAttribute("data-record-key"))')


def _pin_visible_process(page, *, producer_rows=None):
    """Two genuine DOM observations; do not click a different process on retry."""
    before=_visible_process_keys(page)
    page.wait_for_timeout(180)
    after=_visible_process_keys(page)
    return _stable_process_identity(before,after,producer_rows=producer_rows)


def _inspection_selection(page, key, *, label, output):
    """Use a key-specific locator, preserving failure if the process disappeared."""
    row=page.locator('[data-record-key='+json.dumps(key)+']:visible').first
    button=row.locator('button[data-select-key='+json.dumps(key)+']')
    if not button.count():
        return {'step':label,'errorType':'MissingSelectionButton','processKey':key}
    diagnostic=_pointer_click_with_evidence(page,button,label=label,output=output)
    if diagnostic:
        diagnostic['processKey']=key
    return diagnostic


def _observe_inspection(legacy, generated, host, rows, output):
    """Real, generation-pinned pointer selection with both monitoring timers paused."""
    legacy_auto=legacy.locator('#task-auto-refresh')
    generated_auto=generated.locator('[data-mcel-refresh-toggle]')
    legacy_original=legacy_auto.is_checked()
    generated_original=generated_auto.get_attribute('aria-pressed')=='true'
    observations={'refreshPaused':False}
    try:
        # Pausing the timers is part of the operator-visible UI contract, not
        # a DOM mutation or an application-level shortcut.
        legacy.bring_to_front()
        if legacy_original:legacy_auto.uncheck()
        generated.bring_to_front()
        if generated_original:generated_auto.click(timeout=6500)
        if legacy_auto.is_checked() or generated_auto.get_attribute('aria-pressed')!='false':
            _parity_result(rows,'inspection','BLOCKED',reason='Could not pause automatic monitoring for identity inspection')
            return
        observations['refreshPaused']=True
        generated.locator('[data-mcel-mode-tabs] [data-value="server-processes"]').click(timeout=6500)
        legacy.locator('[data-task-tab="server-processes"]').click(timeout=6500)
        # A fresh workflow page does not inherit the overview queries made by
        # other scenarios. Acquire its own authentic server-process evidence.
        server_form=generated.locator('form[data-intent="intent:refresh"]')
        server_form.locator('[name="query"]').fill('')
        server_form.locator('[name="limit"]').fill('100')
        with generated.expect_response(
            lambda r:_overview_response(r,host,query='',limit=100,include_all=False),
            timeout=15000) as server_pending:
            server_form.locator('button[type="submit"]').click(timeout=6500)
        if server_pending.value.status!=200:
            _parity_result(rows,'inspection','FAIL',reason='Server-process refresh returned non-200',observations=observations)
            return
        legacy.locator('#task-query').fill('')
        legacy.locator('#task-limit').fill('100')
        with legacy.expect_response(
            lambda r:_overview_response(r,host,query='',limit=100,include_all=False),
            timeout=15000) as legacy_pending:
            legacy.locator('#task-refresh').click(timeout=6500)
        if legacy_pending.value.status!=200:
            _parity_result(rows,'inspection','FAIL',reason='Legacy server-process refresh returned non-200',observations=observations)
            return
        generated.locator('[data-record-key]:visible').first.wait_for(state='visible',timeout=6500)
        if not legacy.locator('#task-process-table tbody tr').count():
            _parity_result(rows,'inspection','BLOCKED',reason='Legacy server process list unavailable',observations=observations)
            return
        key=_pin_visible_process(generated)
        if key is None:
            _parity_result(rows,'inspection','BLOCKED',reason='Stable PID-and-creation-time server row unavailable',observations=observations)
            return
        observations['serverKey']=key
        diagnostic=_inspection_selection(generated,key,label='generated-server-process-select',output=output)
        if diagnostic:
            _parity_result(rows,'inspection','BLOCKED',reason='Pointer selection blocked for pinned server process',
                evidence=[diagnostic['screenshot']] if diagnostic.get('screenshot') else [],
                observations={**observations,'pointer':diagnostic})
            return
        details=generated.locator('[data-mcel-selected-details='+json.dumps(key)+']:visible').first
        details.wait_for(state='visible',timeout=5000)
        server_row=generated.locator('[data-record-key='+json.dumps(key)+']:visible').first
        server_row.locator('details summary').click(timeout=6500)
        if server_row.locator('details').get_attribute('open')!='':
            _parity_result(rows,'inspection','FAIL',reason='Inline server process details did not open',observations=observations)
            return
        generated.locator('[data-mcel-mode-tabs] [data-value="all-processes"]').click(timeout=6500)
        legacy.locator('[data-task-tab="all-processes"]').click(timeout=6500)
        all_form=generated.locator('form[data-intent="intent:refresh-all"]')
        all_form.locator('[name="query"]').fill('python')
        all_form.locator('[name="limit"]').fill('24')
        with generated.expect_response(
            lambda r:_overview_response(r,host,query='python',limit=24,include_all=True),
            timeout=15000) as pending:
            all_form.locator('button[type="submit"]').click(timeout=6500)
        source=pending.value
        if source.status!=200:
            _parity_result(rows,'inspection','FAIL',reason='All-process refresh returned non-200',observations=observations)
            return
        payload=source.json()
        producer_rows=payload.get('processes') if isinstance(payload,dict) else None
        if not isinstance(producer_rows,list):
            _parity_result(rows,'inspection','BLOCKED',reason='All-process producer identity unavailable',observations=observations)
            return
        if not legacy.locator('#task-all-process-table tbody tr').count():
            _parity_result(rows,'inspection','BLOCKED',reason='Legacy all-process rows unavailable',observations=observations)
            return
        all_key=_pin_visible_process(generated,producer_rows=producer_rows)
        if all_key is None:
            _parity_result(rows,'inspection','BLOCKED',reason='Stable, producer-verified all-process identity unavailable',observations=observations)
            return
        observations['allKey']=all_key
        diagnostic=_inspection_selection(generated,all_key,label='generated-all-process-select',output=output)
        if diagnostic:
            _parity_result(rows,'inspection','BLOCKED',reason='Pointer selection blocked for pinned all-process identity',
                evidence=[diagnostic['screenshot']] if diagnostic.get('screenshot') else [],
                observations={**observations,'pointer':diagnostic})
            return
        selected=generated.locator('[data-mcel-selected-details='+json.dumps(all_key)+']:visible').first
        selected.wait_for(state='visible',timeout=5000)
        # The visible detail must describe the same PID and birth timestamp
        # whose producer row authenticated the selected collection record.
        pid,birth=all_key.split(':',1)
        visible_details=selected.inner_text(timeout=5000)
        if pid not in visible_details or birth not in visible_details:
            _parity_result(rows,'inspection','FAIL',reason='Selected details do not reflect pinned PID and creation time',observations=observations)
            return
        _parity_result(rows,'inspection','PASS',
            evidence=['Real pointer selections on stable PID-and-creation-time records',
                      'All-process row independently correlated with live overview response'],
            observations=observations)
    finally:
        # Leave the UI as it was. Fresh pages are also closed by the caller.
        try:
            if legacy_original and not legacy_auto.is_checked():legacy_auto.check(timeout=6500)
            if generated_original and generated_auto.get_attribute('aria-pressed')=='false':
                generated_auto.click(timeout=6500)
        except Exception:
            # Do not conceal the original result; independent pages are closed.
            pass


def _legacy_refresh_probe(page, action):
    """Observe the legacy refresh gate without issuing any application request.

    The temporary wrapper forwards original arguments, receiver, and returned
    Promise unchanged; its purpose is to observe coalesced invocations that
    cannot produce a second HTTP request. It is removed in `restore`.
    """
    if action not in ('install','paused','resumed','transition','snapshot','restore'):
        raise ValueError('invalid-refresh-probe-action')
    return page.evaluate("""() => {
        const action = ACTION_TOKEN;
        const key = '__mcelParityRefreshObservation';
        let probe = window[key];
        if(action === 'install' && !probe) {
            const original = window.refreshTaskManager;
            probe = {phase:'transition',calls:{paused:0,resumed:0,transition:0},
                     supported:typeof original==='function',original};
            if(probe.supported) {
                const wrapped=function(...args) {
                    if(probe.calls[probe.phase]!==undefined)probe.calls[probe.phase]++;
                    return Reflect.apply(original,this,args);
                };
                window.refreshTaskManager=wrapped;
                probe.wrapped=wrapped;
            }
            window[key]=probe;
        }
        if(!probe)return {supported:false,reason:'probe-not-installed'};
        if(['paused','resumed','transition'].includes(action))probe.phase=action;
        // Resolve global lexical `let` declarations in the page's main
        // world. Indirect window.eval cannot reliably see these bindings.
        const result={supported:probe.supported,phase:probe.phase,
            calls:{...probe.calls},
            timerArmed:typeof taskManagerAutoTimer!=='undefined' && !!taskManagerAutoTimer,
            inFlight:typeof taskManagerRefreshPromise!=='undefined' && !!taskManagerRefreshPromise,
            queued:typeof taskManagerRefreshQueued!=='undefined' && taskManagerRefreshQueued===true,
            activeApp:typeof currentApp!=='undefined' ? currentApp : null,
            autoChecked:document.querySelector('#task-auto-refresh')?.checked??null,
            visibility:document.visibilityState};
        if(action==='restore') {
            if(probe.supported && window.refreshTaskManager===probe.wrapped)
                window.refreshTaskManager=probe.original;
            delete window[key];
        }
        return result;
    }""".replace('ACTION_TOKEN',json.dumps(action)))


def _legacy_refresh_classification(observations):
    """Explain missing HTTP activity without equating invocation with delivery."""
    if observations['resumedRequests']['legacy']:
        return 'http-request-observed'
    samples=observations.get('legacyTimer',{})
    before=samples.get('beforeResumed') or {}
    after=samples.get('afterResumed') or {}
    if after.get('activeApp')!='task-manager':
        return 'legacy-app-not-active'
    if after.get('autoChecked') is False or after.get('timerArmed') is False:
        return 'legacy-auto-timer-not-armed'
    if after.get('supported') and after.get('calls',{}).get('resumed',0)>0:
        if before.get('inFlight') or after.get('inFlight') or before.get('queued') or after.get('queued'):
            return 'legacy-refresh-invoked-but-coalesced'
        return 'legacy-refresh-invoked-without-correlated-http'
    if before.get('inFlight') or after.get('inFlight') or before.get('queued') or after.get('queued'):
        return 'legacy-refresh-pending-or-queued'
    if after.get('supported'):
        return 'legacy-refresh-function-not-invoked'
    return 'legacy-timer-instrumentation-unavailable'


def _legacy_refresh_manual_drain(page):
    """Establish a quiet legacy request boundary before testing automatic pause.

    The two manual clicks can overlap the legacy application's two-stage
    overview fetch and leave a coalesced refresh queued. Count neither the
    pending manual request nor its delayed replay as an automatic timer tick.
    An undrained request is a BLOCKED precondition, never a refresh PASS.
    """
    page.bring_to_front()
    idle = """() =>
        (typeof taskManagerRefreshPromise === 'undefined' || !taskManagerRefreshPromise) &&
        (typeof taskManagerRefreshQueued === 'undefined' || !taskManagerRefreshQueued)
    """
    try:
        page.wait_for_function(idle, timeout=18000, polling=100)
        # The legacy coalescer posts a zero-delay callback after clearing its
        # Promise. Permit that callback to run before testing a second boundary.
        page.wait_for_timeout(300)
        page.wait_for_function(idle, timeout=7000, polling=100)
        observation = page.evaluate("""() => ({
            inFlight: typeof taskManagerRefreshPromise !== 'undefined' && !!taskManagerRefreshPromise,
            queued: typeof taskManagerRefreshQueued !== 'undefined' && taskManagerRefreshQueued === true,
            autoChecked: document.querySelector('#task-auto-refresh')?.checked ?? null
        })""")
        observation['settled'] = (observation.get('autoChecked') is False
                                  and not observation.get('inFlight')
                                  and not observation.get('queued'))
        return observation
    except Exception as exc:
        return {'settled': False, 'errorType': type(exc).__name__,
                'error': str(exc)[:500]}


def _observe_refresh(legacy, generated, host, rows):
    """Real manual and timer-driven HTTP, with independent pause and coalescing evidence."""
    q='python'
    legacy.bring_to_front()
    legacy.locator('[data-task-tab="server-processes"]').click()
    generated.bring_to_front()
    generated.locator('[data-mcel-mode-tabs] [data-value="server-processes"]').click()
    legacy.locator('#task-query').fill(q)
    legacy.locator('#task-limit').fill('24')
    form=generated.locator('form[data-intent="intent:refresh"]')
    form.locator('[name="query"]').fill(q)
    form.locator('[name="limit"]').fill('24')
    observations={}
    # Disable the automatic timers BEFORE testing manual refresh. Otherwise
    # manual clicks can coalesce with timer traffic and dispatch their queued
    # replay during the subsequent pause window, producing a false failure.
    generated_auto=generated.locator('[data-mcel-refresh-toggle]')
    legacy.bring_to_front()
    legacy.locator('#task-auto-refresh').uncheck()
    generated.bring_to_front()
    if generated_auto.get_attribute('aria-pressed')=='true': generated_auto.click()
    if (legacy.locator('#task-auto-refresh').is_checked() or
            generated_auto.get_attribute('aria-pressed')!='false'):
        _parity_result(rows,'refresh','FAIL',
            reason='Automatic refresh controls did not pause',observations=observations)
        return
    for tag,page,control in (('legacy',legacy,legacy.locator('#task-refresh')),
                             ('generated',generated,form.locator('button[type="submit"]'))):
        count=0
        for _ in range(2):
            with page.expect_response(lambda r:_overview_response(
                    r,host,query=q,limit=24,include_all=False),timeout=15000) as pending:
                control.click()
            response=pending.value
            if response.status!=200:
                _parity_result(rows,'refresh','FAIL',reason=tag+' refresh returned HTTP '+str(response.status))
                return
            count+=1
        observations[tag+'ManualRequests']=count
    # The legacy refresh fetches two overviews, and a second manual click can
    # queue a third refresh. A successful first HTTP response does not mean
    # the request Promise or queued replay is complete. Test a clean window.
    observations['manualDrain']=_legacy_refresh_manual_drain(legacy)
    if not observations['manualDrain'].get('settled'):
        _parity_result(rows,'refresh','BLOCKED',
            reason='Legacy manual refresh still in flight or queued before pause window',
            observations=observations)
        return
    initiated={'paused':{'legacy':0,'generated':0},'resumed':{'legacy':0,'generated':0}}
    completed={'paused':{'legacy':0,'generated':0},'resumed':{'legacy':0,'generated':0}}
    outstanding={tag:set() for tag in ('legacy','generated')}
    phase={'value':'transition'}
    def request_watcher(tag):
        def callback(request):
            if not _overview_request(request,host,query=q,limit=24,include_all=False):return
            outstanding[tag].add(id(request))
            state=phase['value']
            if state in initiated:initiated[state][tag]+=1
        return callback
    def response_watcher(tag):
        def callback(response):
            if not _overview_response(response,host,query=q,limit=24,include_all=False):return
            outstanding[tag].discard(id(response.request))
            state=phase['value']
            if state in completed:completed[state][tag]+=1
        return callback
    request_watchers={tag:request_watcher(tag) for tag in ('legacy','generated')}
    response_watchers={tag:response_watcher(tag) for tag in ('legacy','generated')}
    pages={'legacy':legacy,'generated':generated}
    legacy_timer={}
    for tag,page in pages.items():
        page.on('request',request_watchers[tag])
        page.on('response',response_watchers[tag])
    try:
        legacy_timer['installed']=_legacy_refresh_probe(legacy,'install')
        # Both timers were paused before the manual tests, and the legacy
        # manual queue was drained before installing request listeners.
        # Start counting only after this verified quiescent boundary.
        phase['value']='paused'
        legacy_timer['beforePaused']=_legacy_refresh_probe(legacy,'paused')
        legacy.bring_to_front()
        legacy.wait_for_timeout(5600)
        phase['value']='transition'
        legacy_timer['afterPaused']=_legacy_refresh_probe(legacy,'transition')
        paused=dict(initiated['paused'])
        legacy.bring_to_front()
        legacy.locator('#task-auto-refresh').check()
        generated.bring_to_front()
        generated_auto.click()
        if not legacy.locator('#task-auto-refresh').is_checked() or generated_auto.get_attribute('aria-pressed')!='true':
            _parity_result(rows,'refresh','FAIL',reason='Periodic refresh controls would not resume',observations=observations)
            return
        foreground={}
        phase['value']='resumed'
        legacy_timer['beforeResumed']=_legacy_refresh_probe(legacy,'resumed')
        for tag,page in pages.items():
            page.bring_to_front()
            foreground[tag]=page.evaluate("""() => ({
                visibility:document.visibilityState,
                activeApp:document.body.dataset.activeApp || null
            })""")
            if foreground[tag]['visibility']!='visible':continue
            page.wait_for_timeout(6500)
            if tag=='legacy':legacy_timer['afterResumed']=_legacy_refresh_probe(legacy,'snapshot')
        # If the real legacy refresh was invoked but could not start a request
        # because another one is active, allow one additional timer window.
        # No tick or coalesced Promise is ever counted as a successful HTTP.
        if initiated['resumed']['legacy']==0 and _legacy_refresh_classification({
                'resumedRequests':initiated['resumed'],'legacyTimer':legacy_timer
            }) in ('legacy-refresh-invoked-but-coalesced','legacy-refresh-pending-or-queued'):
            legacy.bring_to_front()
            legacy.wait_for_timeout(5600)
            legacy_timer['afterExtendedWindow']=_legacy_refresh_probe(legacy,'snapshot')
        phase['value']='transition'
        legacy_timer['afterResumed']=_legacy_refresh_probe(legacy,'transition')
        active=dict(initiated['resumed'])
        observations['foreground']=foreground
        observations['outstandingCorrelatedRequests']={tag:len(outstanding[tag]) for tag in pages}
    finally:
        phase['value']='transition'
        try:legacy_timer['restored']=_legacy_refresh_probe(legacy,'restore')
        except Exception as exc:legacy_timer['restoreError']=type(exc).__name__
        for tag,page in pages.items():
            page.remove_listener('request',request_watchers[tag])
            page.remove_listener('response',response_watchers[tag])
    observations.update({'pausedRequests':paused,'resumedRequests':active,
                         'pausedCompletedResponses':dict(completed['paused']),
                         'resumedCompletedResponses':dict(completed['resumed']),
                         'refreshPeriodSeconds':5,'counting':'correlated-http-request-initiation',
                         'legacyTimer':legacy_timer})
    observations['legacyResumeClassification']=_legacy_refresh_classification(observations)
    if any(v>0 for v in paused.values()):
        _parity_result(rows,'refresh','FAIL',reason='Automatic refresh request initiated while paused',observations=observations)
    elif all(v>0 for v in active.values()) and all(
            observations.get('foreground',{}).get(tag,{}).get('visibility')=='visible'
            for tag in ('legacy','generated')):
        _parity_result(rows,'refresh','PASS',
            evidence=['Two correlated manual requests per UI',
                      'No new matching requests initiated while paused',
                      'A genuine periodic HTTP request initiated per UI after resume'],
            observations=observations)
    else:
        _parity_result(rows,'refresh','BLOCKED',
            reason='Periodic HTTP unverified: '+observations['legacyResumeClassification'],
            evidence=['Two correlated manual requests per UI'],observations=observations)


def _observe_responsive(legacy, generated, rows, output):
    """Evaluate generated usability independently; never conceal legacy defects.

    A comparison may be incomplete even when the generated application meets
    all five functional acceptance widths. The two statuses remain explicit.
    """
    observations=[]
    for width in WIDTHS:
        one={'width':width}
        for tag,page,all_selector in (
            ('legacy',legacy,'[data-task-tab="all-processes"]'),
            ('generated',generated,'[data-mcel-mode-tabs] [data-value="all-processes"]'),
        ):
            page.bring_to_front()
            page.set_viewport_size({'width':width,'height':800})
            server_selector=('[data-task-tab="server-processes"]' if tag=='legacy'
                             else '[data-mcel-mode-tabs] [data-value="server-processes"]')
            step='server-processes'; selector=server_selector
            target=page.locator(selector)
            try:
                for step,selector in (('server-processes',server_selector),
                                      ('all-processes',all_selector)):
                    target=page.locator(selector)
                    target.scroll_into_view_if_needed(timeout=6000)
                    target.click(timeout=6000)
                    attribute='aria-selected' if tag=='legacy' else 'aria-pressed'
                    if target.get_attribute(attribute)!='true':
                        raise RuntimeError('clicked-tab-not-selected:'+step)
                target.focus()
                focus=target.evaluate('(n)=>document.activeElement===n')
                selected=target.get_attribute(attribute)=='true'
                horizontal=page.evaluate(
                    '() => Math.max(0,document.documentElement.scrollWidth-innerWidth)')
                one[tag]={'focus':focus,'selected':selected,'overflowPx':horizontal,
                          'tabVisible':target.is_visible(),
                          'visibility':page.evaluate('() => document.visibilityState'),
                          'completedSteps':['server-processes','all-processes']}
                filename=tag+'-'+str(width)+'.png'
                page.screenshot(path=str(output/filename),full_page=True,animations='disabled')
            except Exception as exc:
                diagnostic={'errorType':type(exc).__name__,'error':str(exc)[:1100],
                            'failedStep':step,'failedSelector':selector}
                try:diagnostic['visibility']=page.evaluate('() => document.visibilityState')
                except Exception as inner:diagnostic['visibilityError']=type(inner).__name__
                try:
                    diagnostic['tabBox']=target.bounding_box(timeout=1500)
                    diagnostic['tabVisible']=target.is_visible(timeout=1500)
                    diagnostic['tabSelected']=target.get_attribute(
                        'aria-selected' if tag=='legacy' else 'aria-pressed',timeout=1500)
                    diagnostic['hitTarget']=target.evaluate("""node => {
                        const r=node.getBoundingClientRect();
                        const x=Math.max(0,Math.min(innerWidth-1,r.left+r.width/2));
                        const y=Math.max(0,Math.min(innerHeight-1,r.top+r.height/2));
                        const hit=document.elementFromPoint(x,y);
                        return {point:[x,y],element:hit?.tagName||null,
                            id:hit?.id||null,className:String(hit?.className||'').slice(0,160),
                            targetContainsHit:node.contains(hit)};
                    }""")
                except Exception as inner:diagnostic['diagnosticError']=str(inner)[:250]
                try:
                    filename=tag+'-'+str(width)+'-blocked.png'
                    page.screenshot(path=str(output/filename),animations='disabled',timeout=4000)
                    diagnostic['screenshot']=filename
                except Exception:pass
                one[tag]=diagnostic
        observations.append(one)
    blocked={tag:[row['width'] for row in observations if 'errorType' in row[tag]]
             for tag in ('legacy','generated')}
    completed={tag:[row['width'] for row in observations if 'errorType' not in row[tag]]
               for tag in ('legacy','generated')}
    evidence=[row[tag]['screenshot'] for row in observations
              for tag in ('legacy','generated') if row[tag].get('screenshot')]
    evidence += [tag+'-'+str(row['width'])+'.png' for row in observations
                 for tag in ('legacy','generated') if 'errorType' not in row[tag]]
    def passes(tag):
        return not blocked[tag] and all(
            row[tag].get('focus') and row[tag].get('selected') and
            row[tag].get('tabVisible') and row[tag].get('overflowPx',float('inf'))<=2 and
            row[tag].get('visibility')=='visible'
            for row in observations)
    generated_status=('BLOCKED' if blocked['generated'] else
                      'PASS' if passes('generated') else 'FAIL')
    legacy_status=('BLOCKED' if blocked['legacy'] else
                   'PASS' if passes('legacy') else 'FAIL')
    details={'viewports':observations,'completedWidths':completed,'obstructedWidths':blocked,
             'generatedAcceptance':generated_status,'legacyComparison':legacy_status,
             'comparisonComplete':generated_status=='PASS' and legacy_status=='PASS'}
    if generated_status!='PASS':
        _parity_result(rows,'responsive',generated_status,
            reason='Generated responsive navigation '+generated_status.lower()+
                   '; legacy comparison '+legacy_status.lower(),
            evidence=evidence,observations=details)
    else:
        # This is generated *functional* acceptance, not a claim that the
        # legacy comparison completed. Record the independent legacy gate.
        reason=('Generated responsive functional acceptance passed at all five widths; '
                'legacy comparison '+('passed' if legacy_status=='PASS' else
                                      'incomplete ('+legacy_status.lower()+')'))
        _parity_result(rows,'responsive','PASS',reason=reason,
            evidence=evidence,observations=details)


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
                    # Do not leave the two initial pages polling in the background
                    # during isolated read-only scenarios.
                    legacy.close()
                    generated.close()
                    # A previously executed workflow must never leave a tab,
                    # pending process refresh, or timer phase in the next one.
                    # Reopen the *actual* hosted pages; do not mock any route.
                    for name in ('refresh','inspection','responsive'):
                        pair=[]
                        try:
                            for tag,route in (('legacy',LEGACY_ROUTE),('generated',ROUTE)):
                                page=browser.new_page(viewport={'width':1100,'height':800})
                                pair.append(page)
                                response=page.goto(host+route,wait_until='load',timeout=20000)
                                if response is None or response.status!=200:
                                    raise RuntimeError(tag+'-isolated-route-not-200')
                                if tag=='generated' and hashlib.sha256(response.body()).hexdigest()!=expected:
                                    raise RuntimeError('generated-isolated-host-source-mismatch')
                                ready='#task-manager-app' if tag=='legacy' else '#mcel-workbench'
                                if not page.locator(ready).count():
                                    raise RuntimeError(tag+'-isolated-app-not-mounted')
                            standalone_legacy,standalone_generated=pair
                            if name=='refresh':
                                _observe_refresh(standalone_legacy,standalone_generated,host,rows)
                            elif name=='inspection':
                                _observe_inspection(standalone_legacy,standalone_generated,host,rows,output)
                            else:
                                _observe_responsive(standalone_legacy,standalone_generated,rows,output)
                        except Exception as exc:
                            _parity_result(rows,name,'BLOCKED',reason='Scenario could not complete: '+type(exc).__name__+': '+str(exc)[:2500])
                        finally:
                            for page in pair:
                                page.close()
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
