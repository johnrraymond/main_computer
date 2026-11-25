"""Hosted MCEL signal pre-cutover gate: actual loopback routes and real read APIs.

This runner DOES NOT promote schedulers, change shared application code, mutate
files, invoke Aider/model providers, or impersonate an authenticated browser.
Both Playwright tabs fetch canonical application HTML from the existing server;
only the candidate tab receives a strictly fingerprinted, in-memory opt-in.
The reference tab receives the original bytes. Actual same-origin backends are
used for bounded read operations, with a deny-by-default route firewall.

Run on the *same Windows machine* as Main Computer:
  python -m tools.mcel_hosted_signal_cutover_acceptance --host-url https://dev.main-computer.localhost:8875

Requires isolated certifications already completed. Does not constitute full
live-write/Mathics/Aider acceptance; an explicit future gate is still needed.
"""
from __future__ import annotations
from tools.mcel_functional_auth import ORIGIN, secure_target, new_context

import argparse
from base64 import b64encode
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from main_computer.mcel_calculator_expanded_host import render_expanded_page
from main_computer.mcel_applications_shell_host import render_applications_shell_candidate
from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_v1_four_app_hosted_acceptance import validate_loopback_base
from tools.mcel_four_app_live_readonly_frob import request_decision
from tools.mcel_code_editor_functional_parity_frob import reveal as reveal_editor_control
from tools import mcel_hosted_cutover_disposable as disposable
from tools.mcel_signal_differential_acceptance import SNAPSHOT, PIXELS, _diff

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = Path('runtime/reports/mcel-hosted-signal-cutover/report.json')
APPS = ('file-manager', 'calculator', 'code-editor')
ROUTES = {
    'file-manager': '/applications/file-explorer',
    'calculator': '/applications/calculator',
    'code-editor': '/applications/code-editor',
}
# The promoted MCEL parent shell owns ordinary /applications/<app> URLs.
# Its native iframe is served by the same route with this *public, supported*
# child discriminator (see viewport_route_dispatch._is_mcel_native_shell_child).
# Checking the parent as though it were an app yields three identical 200s.
NATIVE_CHILD_QUERY = 'mcel-shell-child=1'


def native_child_route(app: str) -> str:
    return ROUTES[app] + '?' + NATIVE_CHILD_QUERY
DEPENDENCIES = ('/applications/chat-console', '/applications/browser')
ISOLATED_REPORTS={
 'file-manager':'runtime/reports/mcel-file-manager-full-functional/report.json',
 'calculator':'runtime/reports/mcel-calculator-full-functional/report.json',
 'code-editor':'runtime/reports/mcel-code-editor-full-functional/report.json',
}
APPLICATION_SOURCES={
 'file-manager':'tools/fixtures/mcel_semantic_other_apps/file-manager/application.js',
 'calculator':'tools/fixtures/mcel_calculator_expanded/application.js',
 'code-editor':'mcel_apps/code-editor/application.js',
}
RUNTIME_SOURCE='main_computer/web/applications/scripts/mcel-semantic-operational-runtime.js'
GATE = re.compile(r'const integratedAllowed=!isolatedSignalTest && options\.signalSchedulerIntegration===true &&\s*\([^;]{30,900}?\);')
CALC_SNAPSHOT = (SNAPSHOT.replace("getElementById('mcel-workbench')", "getElementById('mcel-expanded-root')")
                  .replace('window.__mcelSlh?.app||window.__mcelWorkbench?.app', 'window.__mcelExpanded?.app'))

# The report deliberately contains no contents, full DOM, file names, source
# text, Mathics result bodies, HTTP authentication tokens, or browser cookies.

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _semantic_identity(page: str) -> str | None:
    """Inspect the actual served IR, not a page title or HTTP-200 assumption."""
    for tag_id in ('mcel-workbench-ir', 'mcel-expanded-ir'):
        pattern = (r'<script\b(?=[^>]*\bid=["\\\']' + re.escape(tag_id) +
                   r'["\\\'])[^>]*>(.*?)</script>')
        # Attribute order is not significant; compiled IR is a JSON script.
        match = re.search(pattern, page, re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        try:
            return json.loads(match.group(1)).get('application', {}).get('appId')
        except (ValueError, TypeError, AttributeError):
            return None
    return None


def _host_document_diagnostic(app: str, expected: str, *, status: int | None,
                              body: bytes | None, content_type: str='',
                              parent_shell: bytes | None=None) -> dict:
    """Non-secret preflight. An unexpected 200 is NOT proof of the MCEL app."""
    expected_bytes=expected.encode('utf-8')
    result={'httpStatus':status,'expectedSha256':hashlib.sha256(expected_bytes).hexdigest(),
            'expectedBytes':len(expected_bytes),'contentType':content_type[:90],
            'observedSha256':hashlib.sha256(body).hexdigest() if body is not None else None,
            'observedBytes':len(body) if body is not None else None,
            'servedAppId':None,'status':'BLOCKED'}
    if status is None or body is None:
        result['reason']='HOST_DOCUMENT_FETCH_FAILED'
        return result
    if status!=200:
        result['reason']='HOST_DOCUMENT_NON_200_OR_REDIRECT'
        return result
    # Decode without logging contents; a 200 authentication page must fail closed.
    try:
        page=body.decode('utf-8')
    except UnicodeDecodeError:
        result['reason']='HOST_DOCUMENT_NOT_UTF8'
        return result
    identity=_semantic_identity(page)
    result['servedAppId']=identity
    if body==expected_bytes:
        result.update(status='PASS_EXACT',reason='CANONICAL_BYTES_MATCH')
        return result
    identities={'file-manager':'semantic-file-manager',
                'calculator':'semantic-calculator-expanded','code-editor':'code-editor'}
    result['reason']=('PARENT_SHELL_RETURNED_FOR_NATIVE_CHILD'
                      if parent_shell is not None and body==parent_shell else
                      'HOSTED_APP_SOURCE_DRIFT' if identity==identities[app] else
                      'NONCANONICAL_RESPONSE_OR_AUTH_PAGE')
    return result


def _preflight_parent_shell(browser, base: str, repo: Path, apps,
                            *, storage_state=None) -> dict:
    """Prove each ordinary app URL still returns the exact promoted MCEL shell."""
    shell=render_applications_shell_candidate(repo,canonical_promoted=True).encode('utf-8')
    expected_sha=hashlib.sha256(shell).hexdigest()
    ctx=new_context(browser, base, service_workers='block',storage_state=storage_state)
    out={}
    try:
        for app in apps:
            entry={'expectedSha256':expected_sha,'expectedBytes':len(shell),
                   'observedSha256':None,'observedBytes':None,'httpStatus':None,
                   'status':'BLOCKED','reason':'PARENT_SHELL_FETCH_FAILED'}
            try:
                resp=ctx.request.get(base+ROUTES[app],max_redirects=0,timeout=20000,
                                     headers={'Cache-Control':'no-store'})
                body=resp.body()
                entry.update(httpStatus=resp.status, observedSha256=hashlib.sha256(body).hexdigest(),
                             observedBytes=len(body))
                if resp.status==200 and body==shell:
                    entry.update(status='PASS_EXACT',reason='PROMOTED_PARENT_SHELL_BYTES_MATCH')
                elif resp.status!=200:
                    entry['reason']='PARENT_SHELL_NON_200_OR_REDIRECT'
                else:
                    entry['reason']='PARENT_SHELL_SOURCE_DRIFT_OR_AUTH_PAGE'
            except Exception as exc:
                entry['errorClass']=type(exc).__name__
            out[app]=entry
            print('MCEL_HOSTED_SHELL_PREFLIGHT',app,entry['status'],entry['reason'],
                  'http='+str(entry['httpStatus']),
                  'expected='+str(entry['expectedSha256']),
                  'observed='+str(entry['observedSha256']),flush=True)
        return out
    finally:
        ctx.close()


def _preflight_host_documents(browser, base: str, repo: Path, apps,
                              *, storage_state=None) -> dict:
    """Read the *native child* document, not the promoted parent shell."""
    ctx=new_context(browser, base, service_workers='block',storage_state=storage_state)
    out={}
    parent_shell=render_applications_shell_candidate(repo,canonical_promoted=True).encode('utf-8')
    try:
        for app in apps:
            try:
                resp=ctx.request.get(base+native_child_route(app),max_redirects=0,timeout=20000,
                    headers={'Cache-Control':'no-store'})
                info=_host_document_diagnostic(app,expected_html(app,repo),status=resp.status,
                      body=resp.body(),content_type=resp.headers.get('content-type',''),
                      parent_shell=parent_shell)
            except Exception as exc:
                info=_host_document_diagnostic(app,expected_html(app,repo),status=None,body=None)
                info['errorClass']=type(exc).__name__
            out[app]=info
            print('MCEL_HOSTED_PREFLIGHT',app,info['status'],info['reason'],
                  'http='+str(info['httpStatus']),
                  'expected='+str(info['expectedSha256']),
                  'observed='+str(info['observedSha256']),flush=True)
        return out
    finally:
        ctx.close()

def prior_isolated_certifications(repo:Path,apps=APPS) -> dict:
    result={}
    for app in apps:
        path=repo/ISOLATED_REPORTS[app]
        if not path.is_file():
            result[app]={'status':'MISSING_REPORT','verified':False}
            continue
        try:
            report=json.loads(path.read_text(encoding='utf-8'))
            app_hash=hashlib.sha256((repo/APPLICATION_SOURCES[app]).read_bytes()).hexdigest()
            runtime_hash=hashlib.sha256((repo/RUNTIME_SOURCE).read_bytes()).hexdigest()
            fingerprint=report.get('fingerprints',{})
            good=(report.get('status')=='PASS_ISOLATED_FUNCTIONAL_WITH_SIGNALS' and
                  fingerprint.get('applicationSha256')==app_hash and
                  fingerprint.get('runtimeSha256')==runtime_hash)
            result[app]={'status':'VERIFIED' if good else 'REPORT_OR_SOURCE_MISMATCH',
                         'verified':good,'reportedStatus':report.get('status'),
                         'sourceSha256Matches':fingerprint.get('applicationSha256')==app_hash,
                         'runtimeSha256Matches':fingerprint.get('runtimeSha256')==runtime_hash}
        except (ValueError,OSError,TypeError):
            result[app]={'status':'UNREADABLE_REPORT','verified':False}
    return result


def expected_html(app: str, repo: Path = ROOT) -> str:
    if app == 'calculator':
        return render_expanded_page(repo, promoted=True)
    return render_semantic_page(app, repo, promoted=True)


def instrument_candidate(app: str, original: str) -> str:
    """Exact, nonpersistent HTML instrumentation; fail closed on drift."""
    if app not in APPS:
        raise ValueError('UNSUPPORTED_APP')
    if app == 'calculator':
        needle = "capabilities:port,presentation:'expert'"
        if original.count(needle) != 1 or original.count('</head>') != 1:
            raise ValueError('CALCULATOR_MOUNT_CONTRACT_DRIFT')
        result = original.replace(needle, needle +
             ',signalSchedulerIntegration:true,signalSchedulerStartEnabled:true', 1)
        return result.replace('</head>',
            '<script>window.__MCEL_CALCULATOR_SIGNAL_ACCEPTANCE__=true;</script></head>', 1)
    if original.count('signalSchedulerIntegration:false') != 1 or original.count('signalSchedulerStartEnabled:false') != 1:
        raise ValueError('HOST_DEFAULT_NOT_INCUMBENT')
    result = (original.replace('signalSchedulerIntegration:false', 'signalSchedulerIntegration:true', 1)
                      .replace('signalSchedulerStartEnabled:false', 'signalSchedulerStartEnabled:true', 1))
    if app == 'code-editor':
        matches = list(GATE.finditer(result))
        if len(matches) != 1 or result.count('<head>') != 1:
            raise ValueError('EDITOR_RUNTIME_GATE_DRIFT')
        match = matches[0]
        code = match.group()
        code = code[:-2] + '||(ir.application.appId==="code-editor" && global.__MCEL_CODE_EDITOR_SIGNAL_ACCEPTANCE__===true));'
        result = result[:match.start()] + code + result[match.end():]
        result = result.replace('<head>',
               '<head><script>window.__MCEL_CODE_EDITOR_SIGNAL_ACCEPTANCE__=true;</script>', 1)
    return result


def _app_handle(app: str) -> str:
    return '__mcelExpanded' if app == 'calculator' else '__mcelWorkbench'


def _safe_request(app: str, base: str, url: str, method: str, body: str | None,
                  *, allow_editor_read_history: bool=False,
                  disposable_write_path: str | None=None) -> str:
    # Existing read-allowlist is the authority for File Explorer + Code Editor.
    # Calculator operates locally here; Mathics initialization can be costly,
    # so its POSTs remain blocked until a distinct provider acceptance lane.
    if app=='code-editor' and urlsplit(url).path=='/api/applications/editor/project/file/save':
        return disposable.write_decision(base,url,method,body,disposable_write_path)
    if (app=='code-editor' and urlsplit(url).path=='/api/applications/editor/read'
            and not allow_editor_read_history):
        return 'deny-editor-read-has-session-history-side-effect'
    decision = request_decision(app, base, url, method, body)
    if decision.startswith('allow-'):
        return decision
    return decision


def _guard_context(ctx, app: str, base: str, *, candidate: bool, original_html: str,
                   firewall: dict, allow_editor_read_history: bool=False,
                   disposable_write_path: str | None=None,
                   parent_shell: bytes | None=None):
    """No non-GET effects other than declared read-only POSTs."""
    route_path = ROUTES[app]

    def guard(route, request):
        parsed = urlsplit(request.url)
        same = (parsed.scheme, parsed.netloc) == (urlsplit(base).scheme, urlsplit(base).netloc)
        if (same and request.method == 'GET' and parsed.path == route_path
                and parsed.query == NATIVE_CHILD_QUERY):
            if request.resource_type != 'document':
                firewall['blocked'].append('UNEXPECTED_MAIN_ROUTE_RESOURCE_TYPE')
                route.abort(); return
            try:
                response = route.fetch(max_redirects=0, timeout=20000)
                content = response.text()
                if response.status != 200 or content != original_html:
                    info=_host_document_diagnostic(app,original_html,status=response.status,
                         body=response.body(),content_type=response.headers.get('content-type',''),
                         parent_shell=parent_shell)
                    firewall['documentDrift']=info
                    firewall['blocked'].append('SERVED_CANONICAL_HTML_MISMATCH_'+info['reason'])
                    route.abort(); return
                firewall['servedHashes'].append(_sha(content))
                result = instrument_candidate(app, content) if candidate else content
                route.fulfill(response=response, body=result, headers={
                    **{k: v for k, v in response.headers.items()
                       if k.lower() not in ('content-length', 'content-encoding', 'etag')},
                    'cache-control': 'no-store',
                })
            except Exception as exc:
                firewall['blocked'].append('DOCUMENT_FETCH_FAILED_' + type(exc).__name__)
                route.abort()
            return
        # A child-mounted acceptance session must never silently navigate to
        # the parent shell, an authentication page or any unrelated document.
        if request.resource_type == 'document':
            firewall['blocked'].append('DOCUMENT_NAVIGATION_OUTSIDE_EXACT_NATIVE_CHILD')
            route.abort()
            return
        decision = _safe_request(app, base, request.url, request.method, request.post_data,
                                 allow_editor_read_history=allow_editor_read_history,
                                 disposable_write_path=disposable_write_path)
        if decision.startswith('allow-'):
            if parsed.path.startswith('/api/'):
                firewall['requests'].append({'method': request.method, 'path': parsed.path,
                                             'status': 'REQUESTED'})
            route.continue_()
        else:
            firewall['blocked'].append('DENIED_' + decision)
            route.abort()

    ctx.route('**/*', guard)
    # ServiceWorkers and WebSockets must never bypass the acceptance firewall.
    if not hasattr(ctx, 'route_web_socket'):
        raise RuntimeError('PLAYWRIGHT_WEBSOCKET_GUARD_UNAVAILABLE')
    ctx.route_web_socket('**/*', lambda ws: (firewall['blocked'].append('WEBSOCKET_DENIED'), ws.close()))


def _settle(page):
    page.evaluate('''async()=>{await document.fonts.ready;
      await new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)));}''')
    page.mouse.move(0, 0)
    page.wait_for_timeout(90)


def _sanitize_report(report: dict) -> dict:
    return {k: report.get(k) for k in ('mode', 'integrationEnabled', 'integratedSupported', 'fallbacks')}


def _snapshot(page, app):
    return page.evaluate(CALC_SNAPSHOT if app == 'calculator' else SNAPSHOT)



def _is_file_manager_mtime_only_difference(differences: int, samples: list[dict]) -> bool:
    """Only a complete, precisely known entry-mtime delta is retryable.

    Other state, contents, metadata (including file size), layout and focus
    differences remain hard blockers. A truncated diff sample is not proof.
    """
    return (0 < differences <= len(samples) and
            all(re.fullmatch(r"state\.state:entries\[\d+\]\.mtime", item.get('path', ''))
                for item in samples))


def _refresh_file_manager_live_listing(page) -> None:
    """Use the *real* mounted read capability; do not replay/mock HTTP data."""
    before = _receipt_count(page, 'file-manager', 'intent:browse')
    # The initial root is already selected and was resolved by the live host.
    if not page.evaluate("() => !!window.__mcelWorkbench.app.readState('state:selected-root')"):
        raise RuntimeError('HOSTED_FILE_MANAGER_ROOT_NOT_SELECTED')
    page.evaluate("""async () => {
        const result=await window.__mcelWorkbench.app.execute('intent:browse');
        return result?.disposition||null;
    }""")
    page.wait_for_function("""n=>window.__mcelWorkbench.app.receipts()
        .filter(x=>x.intentId==='intent:browse').length===n+1""",arg=before,timeout=20000)
    disposition=page.evaluate("""()=>window.__mcelWorkbench.app.receipts()
        .filter(x=>x.intentId==='intent:browse').at(-1)?.disposition""")
    if disposition!='completed':
        raise RuntimeError('HOSTED_FILE_MANAGER_REFRESH_NOT_COMPLETED')


def _stabilize_file_manager_live_listing(pages: dict, max_resamples: int=3) -> dict:
    """Re-observe a changing directory, never discard a semantic difference.

    The host's directory listing includes real filesystem mtimes. Reads made
    at different instants can disagree when a workspace directory changes.
    Retry only that narrowly identified case; if the discrepancy persists,
    retain the failure rather than normalizing it away.
    """
    evidence={'status':'NOT_NEEDED','liveResamples':0,'differencePathsBefore':[],
              'differencePathsAfter':[]}
    for attempt in range(max_resamples+1):
        for page in pages.values():_settle(page)
        left=_snapshot(pages['incumbent'],'file-manager')
        right=_snapshot(pages['signals'],'file-manager')
        count,samples=_diff(left,right)
        paths=[item['path'] for item in samples]
        if attempt==0:evidence['differencePathsBefore']=paths
        evidence['differencePathsAfter']=paths
        if count==0:
            evidence['status']='PASS_LIVE_REOBSERVATION' if attempt else 'NOT_NEEDED'
            return evidence
        if not _is_file_manager_mtime_only_difference(count,samples):
            evidence['status']='BLOCKED_NON_METADATA_DIFFERENCE'
            return evidence
        if attempt>=max_resamples:
            evidence['status']='BLOCKED_LIVE_METADATA_DID_NOT_CONVERGE'
            return evidence
        # Read from the actual backend in both separately mounted app contexts.
        # This is not a retry of a denied write or a replay of a cached response.
        for page in pages.values():_refresh_file_manager_live_listing(page)
        evidence['liveResamples']+=1
    return evidence

def _snapshot_difference_diagnostics(left: dict, right: dict, samples: list[dict]) -> list[dict]:
    '''Report location and digests, never source text or private cell contents.

    This identifies differing static UI labels without logging their contents.
    Large Monaco subtree differences are represented by paths/row counts only.
    '''
    result=[]
    for sample in samples[:8]:
        path=sample.get('path','')
        entry={'path':path}
        found=re.fullmatch(r'rows\[(\d+)\]\.(text|value)',path)
        if found:
            idx=int(found.group(1))
            for role,snapshot in [('incumbent',left),('signals',right)]:
                rows=snapshot.get('rows',[])
                if idx>=len(rows):continue
                item=rows[idx]
                raw=item.get(found.group(2))
                if not isinstance(raw,str):continue
                entry[role]={'length':len(raw),'sha256':_sha(raw),
                    'tag':item.get('tag'), 'nodePath':item.get('path'),
                    'id':item.get('attrs',{}).get('id'),
                    'visible':item.get('visible')}
                # Resolve nearest named ancestor from a recorded DOM path.
                path0=item.get('path','')
                by_path={r.get('path'):r for r in rows}
                while path0 and path0!='0':
                    path0=path0.rsplit('/',1)[0]
                    parent=by_path.get(path0,{})
                    attrs=parent.get('attrs',{})
                    named=next((f'{key}={str(value)[:100]}' for key,value in attrs.items()
                                if key=='id' or key.startswith('data-mcel-document-') or
                                key in ('data-mcel-embedded-chat','data-mcel-tabpanel')) ,None)
                    if named:
                        entry[role]['nearestNamedAncestor']=named
                        break
        result.append(entry)
    return result


def _await_editor_monaco_ready(page, path: str, timeout: int=30000) -> dict:
    '''A source-read receipt is not proof that the async native editor mounted.'''
    try:
        page.wait_for_function('''path=>{
            const info=window.MainComputerMonacoAdapter?.inspect?.();
            const host=document.querySelector('[data-mcel-document-surface]');
            const label=document.querySelector('[data-mcel-document-label]');
            const status=document.querySelector('[data-mcel-document-dirty]');
            return info?.mounted===true && info.path===path &&
                label?.textContent===path && host?.dataset.monacoOutcome==='pass' &&
                status?.textContent?.startsWith('Opened: '+path);
        }''',arg=path,timeout=timeout)
    except Exception as exc:
        detail=page.evaluate('''()=>({mounted:!!window.MainComputerMonacoAdapter?.inspect?.().mounted,
            actualPath:window.MainComputerMonacoAdapter?.inspect?.().path||'',
            monacoOutcome:document.querySelector('[data-mcel-document-surface]')?.dataset.monacoOutcome||'',
            status:document.querySelector('[data-mcel-document-dirty]')?.textContent?.slice(0,100)||'',
            label:document.querySelector('[data-mcel-document-label]')?.textContent?.slice(0,100)||''})''')
        raise RuntimeError('HOSTED_NATIVE_MONACO_NOT_READY:'+json.dumps(detail,sort_keys=True)) from exc
    result=page.evaluate('''()=>({mounted:window.MainComputerMonacoAdapter.inspect().mounted,
        path:window.MainComputerMonacoAdapter.inspect().path,
        readOnly:window.MainComputerMonacoAdapter.inspect().readOnly,
        text:window.__CE_MONACO_MODEL__?.getValue?.()??null})''')
    if not result.get('mounted') or result.get('path')!=path or not isinstance(result.get('text'),str):
        raise RuntimeError('HOSTED_NATIVE_MONACO_DOCUMENT_IDENTITY_NOT_VERIFIED')
    return {'mounted':True,'path':path,'readOnly':result['readOnly'],
            'contentSha256':_sha(result['text']),'contentBytes':len(result['text'].encode('utf8'))}


def _select_and_read(page, app):
    if app == 'file-manager':
        # Known canonical README is read only if it is actually observed.
        item = page.locator('[data-record-key="README.md"] button')
        if item.count() == 0:
            return 'README_NOT_IN_SELECTED_ROOT'
        item.first.click()
        page.get_by_role('button', name='Preview Entry').click()
        page.wait_for_function("()=>window.__mcelWorkbench.app.readState('state:preview')?.readable===true", timeout=15000)
        return 'OBSERVED_FILE_PREVIEW'
    if app == 'code-editor':
        item = page.locator('[data-record-key="README.md"] button')
        if item.count() == 0:
            return 'README_NOT_IN_REPO_INVENTORY'
        before = page.evaluate('''()=>window.__mcelWorkbench.app.receipts()
            .filter(x=>x.intentId==='intent:open-source').length''')
        item.first.click()
        page.wait_for_function('''n=>window.__mcelWorkbench.app.receipts()
            .filter(x=>x.intentId==='intent:open-source').length>n''', arg=before, timeout=15000)
        outcome = page.evaluate("window.__mcelWorkbench.app.receipts().filter(x=>x.intentId==='intent:open-source').at(-1)?.disposition")
        if outcome != 'completed':
            raise RuntimeError('REAL_EDITOR_SOURCE_OPEN_DID_NOT_COMPLETE')
        return 'OBSERVED_SOURCE_OPEN'
    return 'NOT_APPLICABLE'


# Browser wrapper holds the ACTUAL returned fetch response before MCEL can
# settle its operation; no fake response, no injected backend error.
HOLD_WRAPPER = '''() => {
  const original=window.fetch.bind(window);
  window.__hostHoldNext=null;
  window.__hostHeld=[];
  // Bounded, content-free live write trace. The browser may be waiting for
  // confirmation, authorization, the intercepted request, or the server.
  // Do not infer any one of these from the Save button's disabled state.
  window.__hostSaveTrace=[];
  const saveRecord=(phase,extra={})=>{
    window.__hostSaveTrace.push({phase,...extra});
    if(window.__hostSaveTrace.length>24)window.__hostSaveTrace.shift();
  };
  document.addEventListener('mcel:parity-transport',event=>{
    const d=event.detail||{};
    if(d.endpoint==='/api/applications/editor/project/file/save')
      saveRecord('port-'+d.phase,{requestId:d.requestId||null,
        httpStatus:d.status||null,outcome:d.outcome||null});
  });
  document.addEventListener('click',event=>{
    const save=event.target?.closest?.('[data-mcel-document-save]');
    if(save)saveRecord('native-save-click',{disabledAtClick:!!save.disabled});
  },true);
  window.__hostRelease=()=>{const pending=window.__hostHeld.splice(0);
    pending.forEach(done=>done());return pending.length;};
  window.fetch=async(...args)=>{
    const isSave=String(args[0])==='/api/applications/editor/project/file/save';
    if(isSave)saveRecord('fetch-start');
    let response;
    try{response=await original(...args);}
    catch(error){if(isSave)saveRecord('fetch-rejected',{errorClass:String(error?.name||'Error').slice(0,40)});throw error;}
    if(isSave)saveRecord('fetch-response',{httpStatus:response?.status||null});
    const match=window.__hostHoldNext;
    if(match && String(args[0]).endsWith(match)){
      window.__hostHoldNext=null;
      await new Promise(done=>window.__hostHeld.push(done));
    }
    return response;
  };
}'''


def _receipt_count(page, app, intent):
    return page.evaluate('''([handle,intent])=>window[handle].app.receipts()
        .filter(r=>r.intentId===intent).length''', [_app_handle(app), intent])


def _exercise_pending(pages, app, record):
    """Observe live backend responses withheld across runtime rollback."""
    if app == 'file-manager':
        endpoint = '/api/applications/file-explorer/search'
        intent = 'intent:search'
        def trigger(page):
            query=page.locator('form[data-intent="intent:search"] input[name="query"]')
            query.fill('README')
            page.locator('form[data-intent="intent:search"] button[type="submit"]').click()
    elif app == 'code-editor':
        endpoint = '/api/applications/editor/files'
        intent = 'intent:list-files'
        def trigger(page):
            page.locator('button[data-intent="intent:list-files"], form[data-intent="intent:list-files"] button[type="submit"]').first.click()
    else:
        return {'status': 'NOT_TESTED_PROVIDER_LANE_REQUIRED'}
    earlier = {}
    for role,page in pages.items():
        earlier[role] = _receipt_count(page, app, intent)
        page.evaluate('(endpoint)=>window.__hostHoldNext=endpoint', endpoint)
        trigger(page)
        page.wait_for_function('()=>window.__hostHeld.length===1', timeout=20000)
    record('live-read-response-held')
    rolled = pages['signals'].evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("incumbent")')
    if rolled.get('status') != 'ROLLED_BACK':
        raise RuntimeError('INFLIGHT_ROLLBACK_REFUSED')
    record('rollback-while-real-read-pending')
    for role,page in pages.items():
        if page.evaluate('window.__hostRelease()') != 1:
            raise RuntimeError('INFLIGHT_RELEASE_COUNT_INVALID')
        page.wait_for_function('''([intent,n])=>window.__mcelWorkbench.app.receipts()
           .filter(r=>r.intentId===intent).length===n+1''', arg=[intent,earlier[role]], timeout=20000)
        outcome=page.evaluate('''intent=>window.__mcelWorkbench.app.receipts()
            .filter(r=>r.intentId===intent).at(-1)?.disposition''', intent)
        if outcome != 'completed':
            raise RuntimeError('INFLIGHT_READ_DID_NOT_COMPLETE')
    record('real-read-completes-once-after-rollback')
    reenabled = pages['signals'].evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("certified")')
    if reenabled.get('status') != 'ENABLED':
        raise RuntimeError('SIGNAL_REENABLE_REFUSED')
    record('signal-reenable-full-reconciliation')
    return {'status': 'PASS_REAL_READ_PENDING_ROLLBACK',
            'backend': endpoint, 'receiptDeltaEach': 1}


def _visit_dependency_routes(browser, base, *, storage_state=None):
    ctx = new_context(browser, base, service_workers='block', storage_state=storage_state)
    try:
        output={}
        for path in DEPENDENCIES:
            try:
                response=ctx.request.get(base+path, max_redirects=0, timeout=10000)
                output[path]={'httpStatus':response.status, 'served':response.status==200,
                              'bodySha256':hashlib.sha256(response.body()).hexdigest() if response.status==200 else None}
            except Exception as exc:
                output[path]={'httpStatus':None, 'served':False, 'errorClass':type(exc).__name__}
        return output
    finally:
        ctx.close()


def _single_app(browser, app, base, repo, storage_state=None,
                allow_editor_read_history=False,write_fixture=None) -> dict:
    expected = expected_html(app, repo)
    parent_shell=render_applications_shell_candidate(repo,canonical_promoted=True).encode('utf-8')
    roles=('incumbent','signals')
    data={'status':'BLOCKED','failures':[], 'stages':[], 'requests':{},
          'targetRoute':ROUTES[app], 'candidateBrowserOnly':True,
          'hostDocumentSha256':_sha(expected), 'backendTransport':'ACTUAL_LOOPBACK_NO_MOCKS',
          'liveWrites':'NOT_ATTEMPTED', 'hostedMathicsAndAI':'NOT_TESTED',
          'editorReadContextSideEffect':'AUTHORIZED' if allow_editor_read_history else 'BLOCKED_NOT_AUTHORIZED',
          'requiredReadApi':{}, 'pendingRollback':'NOT_TESTED',
          'promotionAuthorized':False,'disposableWrite':'NOT_TESTED'}
    contexts={};pages={};firewalls={};errors={k:[] for k in roles}
    skip_total=0;skip_counter=0
    try:
        for role in roles:
            ctx = new_context(browser, base, viewport={'width':1272,'height':900}, device_scale_factor=1,
                                      service_workers='block',accept_downloads=False,storage_state=storage_state)
            contexts[role]=ctx
            firewall={'blocked':[],'requests':[],'responses':[],'servedHashes':[]}
            firewalls[role]=firewall
            _guard_context(ctx, app, base, candidate=role=='signals', original_html=expected,
                           firewall=firewall,allow_editor_read_history=allow_editor_read_history,
                           disposable_write_path=write_fixture['paths'][role] if write_fixture else None,
                           parent_shell=parent_shell)
            # The read port binds window.fetch during app mount. Its holder MUST
            # be installed before any inline HTML runs, not after the mount.
            ctx.add_init_script('('+HOLD_WRAPPER+')();')
            page=ctx.new_page()
            pages[role]=page
            page.set_default_timeout(12000)
            page.on('pageerror',lambda exc,r=role:errors[r].append(type(exc).__name__))
            page.on('response',lambda response,f=firewall: f['responses'].append({
                'method':response.request.method,'path':urlsplit(response.url).path,'status':response.status})
                if urlsplit(response.url).path.startswith('/api/') else None)
            response=page.goto(base+native_child_route(app),wait_until='domcontentloaded',timeout=25000)
            if response is None or response.status!=200:
                raise RuntimeError('HOSTED_CANONICAL_ROUTE_NOT_200')
            key=_app_handle(app)
            page.wait_for_function('handle=>!!window[handle]?.app',arg=key,timeout=18000)
            status='#mcel-expanded-diagnostic' if app=='calculator' else '#mcel-workbench-status'
            expected_status='Semantic Calculator ready' if app=='calculator' else 'Semantic application ready'
            if page.locator(status).inner_text(timeout=6000)!=expected_status:
                raise RuntimeError('HOST_MOUNT_NOT_READY')
            served=page.evaluate('''id=>{const el=document.querySelector(id);
                return JSON.parse(el?.textContent||'null')?.application?.appId||null}''',
                '#mcel-expanded-ir' if app=='calculator' else '#mcel-workbench-ir')
            if served != {'file-manager':'semantic-file-manager','calculator':'semantic-calculator-expanded',
                          'code-editor':'code-editor'}[app]:
                raise RuntimeError('HOSTED_APP_ID_DRIFT')
        reports={role:page.evaluate(f'window.{_app_handle(app)}.app.signalCandidateReport()')
                 for role,page in pages.items()}
        data['initialModes']={k:_sanitize_report(v) for k,v in reports.items()}
        if reports['incumbent'].get('mode')!='disabled' or reports['incumbent'].get('integrationEnabled'):
            raise RuntimeError('INCUMBENT_NOT_DISABLED')
        if reports['signals'].get('mode')!='integrated-opt-in' or not reports['signals'].get('integrationEnabled'):
            raise RuntimeError('HOSTED_CANDIDATE_DID_NOT_ENABLE')
        refused=pages['incumbent'].evaluate(f'window.{_app_handle(app)}.app.setSignalSchedulingMode("certified")')
        if refused['status']!='REFUSED':raise RuntimeError('INCUMBENT_SCHEDULER_GUARD_FAILED')
        def record(phase):
            nonlocal skip_total,skip_counter
            for page in pages.values():_settle(page)
            left=_snapshot(pages['incumbent'],app)
            right=_snapshot(pages['signals'],app)
            differences,samples=_diff(left,right)
            # Native app mounts have asynchronous secondary UI effects.
            # Re-observe only while a *complete* byte-for-byte semantic
            # comparison is still failing; never normalize or discard a delta.
            settled_after=0
            if differences and app=='calculator':
                for _ in range(3):
                    pages['incumbent'].wait_for_timeout(350)
                    for page in pages.values():_settle(page)
                    left=_snapshot(pages['incumbent'],app)
                    right=_snapshot(pages['signals'],app)
                    differences,samples=_diff(left,right)
                    settled_after+=1
                    if differences==0:break
            png_a=pages['incumbent'].screenshot(animations='disabled',caret='hide')
            png_b=pages['signals'].screenshot(animations='disabled',caret='hide')
            pixels=pages['incumbent'].evaluate(PIXELS,{'a':b64encode(png_a).decode('ascii'),
                                                      'b':b64encode(png_b).decode('ascii')})
            material=pixels['count']>512 or pixels['maximum']>24
            current=right['signal'].get('statistics',{}).get('skips',0)
            if current<skip_counter:skip_counter=0
            skip_total+=current-skip_counter;skip_counter=current
            stage={'stage':phase,'status':'PASS' if differences==0 and not material else 'FAIL',
                   'semanticDifferences':differences,
                   'differencePaths':[v['path'] for v in samples[:8]],
                   'differenceDiagnostics':(_snapshot_difference_diagnostics(left,right,samples) if differences else []),
                   'fullSnapshotConvergenceRetries':settled_after,
                   'pixelDifferences':pixels['count'],'pixelMaxDelta':pixels['maximum'],
                   'candidateMode':right['signal'].get('mode'),
                   'candidateSkipCounter':current}
            data['stages'].append(stage)
            print('MCEL_HOSTED_STAGE',app,phase,stage['status'],
                  'differences='+str(differences),'skips='+str(current),
                  *(['paths='+','.join(stage['differencePaths'])] if differences else []),flush=True)
            if stage['differenceDiagnostics']:
                # Local diagnostic fingerprints only, never file/chat contents.
                print('MCEL_HOSTED_DIFF_FINGERPRINT',app,phase,
                      json.dumps(stage['differenceDiagnostics'],sort_keys=True,separators=(',',':')),
                      flush=True)
        if app=='file-manager':
            for page in pages.values():
                page.wait_for_function('''()=>{
                  const a=window.__mcelWorkbench.app;
                  return !!a.readState('state:selected-root')&&
                    a.receipts().some(x=>x.intentId==='intent:browse'&&x.disposition==='completed');
                }''',timeout=25000)
            data['rootListingStabilization']=_stabilize_file_manager_live_listing(pages)
            if data['rootListingStabilization']['status'].startswith('BLOCKED'):
                data['failures'].append('INITIAL_LIVE_LISTING_DIFFERENCE_UNVERIFIED')
            if data['rootListingStabilization']['liveResamples'] or data['rootListingStabilization']['status'].startswith('BLOCKED'):
                witness=data['rootListingStabilization']
                print('MCEL_HOSTED_FILE_MANAGER_REOBSERVE',witness['status'],
                      'resamples='+str(witness['liveResamples']),
                      'before='+','.join(witness['differencePathsBefore']),
                      'after='+','.join(witness['differencePathsAfter']),flush=True)
        record('canonical-host-mount')
        if app=='calculator':
            for page in pages.values():
                form=page.locator('form[data-intent="intent:evaluate"]')
                form.locator('input[name="expression"]').fill('7*(3+2)')
                form.locator('button[type="submit"]').click()
                page.wait_for_function("()=>window.__mcelExpanded.app.readState('state:arithmetic-result')==='35'")
            record('real-host-arithmetic')
            rolled=pages['signals'].evaluate('window.__mcelExpanded.app.setSignalSchedulingMode("incumbent")')
            if rolled.get('status')!='ROLLED_BACK':
                raise RuntimeError('HOSTED_CALCULATOR_ROLLBACK_REFUSED')
            record('live-rollback-local-math')
            for page in pages.values():
                page.locator('form[data-intent="intent:evaluate"] input[name="expression"]').fill('9+9')
                page.locator('form[data-intent="intent:evaluate"] button[type="submit"]').click()
                page.wait_for_function("()=>window.__mcelExpanded.app.readState('state:arithmetic-result')==='18'")
            record('arithmetic-after-rollback')
            resumed=pages['signals'].evaluate('window.__mcelExpanded.app.setSignalSchedulingMode("certified")')
            if resumed.get('status')!='ENABLED':
                raise RuntimeError('HOSTED_CALCULATOR_REENABLE_REFUSED')
            record('calculator-signal-reenabled')
        elif app=='file-manager':
            for page in pages.values():
                observed=_select_and_read(page,app)
                if observed!='OBSERVED_FILE_PREVIEW':raise RuntimeError(observed)
            record('hosted-file-preview')
            data['pendingRollback']=_exercise_pending(pages,app,record)['status']
        else:
            for page in pages.values():
                controls=page.locator('button[data-intent="intent:list-files"],form[data-intent="intent:list-files"] button[type="submit"]')
                if controls.count()==0:raise RuntimeError('HOSTED_FILE_INVENTORY_CONTROL_MISSING')
                reveal_editor_control(page, controls.first)
                controls.first.click()
                page.locator('[data-record-key="README.md"] button').first.wait_for(timeout=18000)
            record('hosted-source-list')
            if allow_editor_read_history:
                for page in pages.values():
                    if _select_and_read(page,app)!='OBSERVED_SOURCE_OPEN':
                        raise RuntimeError('HOSTED_SOURCE_OPEN_NOT_OBSERVED')
                # Both browsers must finish the asynchronous native Monaco
                # mount before comparing its thousands of DOM children.
                data['nativeMonacoReadiness']={role:_await_editor_monaco_ready(page,'README.md')
                    for role,page in pages.items()}
                if (data['nativeMonacoReadiness']['incumbent']['contentSha256'] !=
                    data['nativeMonacoReadiness']['signals']['contentSha256']):
                    raise RuntimeError('HOSTED_MONACO_SOURCE_CONTENT_DIFFERENTIAL')
                record('hosted-source-open')
                data['nativeMonaco']={role:details['mounted'] for role,details in
                                      data['nativeMonacoReadiness'].items()}
            else:
                data['sourceOpen']='BLOCKED_BY_DEFAULT_EDITOR_READ_RECORDS_AIDER_CONTEXT'
            data['pendingRollback']=_exercise_pending(pages,app,record)['status']
            if write_fixture is not None:
                data['disposableWrite']=disposable.exercise_write(pages,write_fixture,firewalls=firewalls)
                if data['disposableWrite']=='PASS_DISPOSABLE_SOURCE_WRITE_ROLLBACK':
                    data['liveWrites']='PASS_DISPOSABLE_ONLY'
                if data['disposableWrite']!='PASS_DISPOSABLE_SOURCE_WRITE_ROLLBACK':
                    data['failures'].append('DISPOSABLE_WRITE_LANE_FAILED')
        final=pages['signals'].evaluate(f'window.{_app_handle(app)}.app.signalCandidateReport()')
        data['endMode']=_sanitize_report(final)
        data['actualSkips']=skip_total
        data['unsafeFallbacks']=final.get('fallbacks',0)+final.get('statistics',{}).get('unsafeFallbacks',0)
        for role,firewall in firewalls.items():
            data['requests'][role]={'actualApiResponses':[
                {k:v[k] for k in ('method','path','status')} for v in firewall['responses'] if v['status']!=200],
                'readOnlyApiSuccesses':sum(x['status']==200 for x in firewall['responses']),
                'blockedReasons':list(firewall['blocked']),
                'hostedHtmlHashes':firewall['servedHashes']}
            data['failures'].extend(firewall['blocked'])
        expected_reads={
            'file-manager':{'/api/applications/file-explorer/roots','/api/applications/file-explorer/list',
                            '/api/applications/file-explorer/read','/api/applications/file-explorer/search'},
            'calculator':set(),
            'code-editor':({'/api/applications/editor/files','/api/applications/editor/read'}
                           if allow_editor_read_history else {'/api/applications/editor/files'}),
        }[app]
        for path in expected_reads:
            data['requiredReadApi'][path]=all(any(x['path']==path and x['status']==200
                             for x in f['responses']) for f in firewalls.values())
        if not all(data['requiredReadApi'].values()):data['failures'].append('REQUIRED_LIVE_READ_API_MISSING')
        if any(errors.values()):data['failures'].append('BROWSER_PAGEERROR')
        if any(x['status']!='PASS' for x in data['stages']):data['failures'].append('HOSTED_SCHEDULER_DIFFERENTIAL')
        if skip_total<1:data['failures'].append('NO_ACTUAL_SKIPS')
        if data['unsafeFallbacks']:data['failures'].append('SIGNAL_UNSAFE_FALLBACK')
        if data['endMode']['mode']!='integrated-opt-in':data['failures'].append('CANDIDATE_NOT_REENABLED')
        if not all(f['servedHashes']==[_sha(expected)] for f in firewalls.values()):
            data['failures'].append('SERVER_CONTENT_PROVENANCE_MISSING')
        if data['pendingRollback']!='PASS_REAL_READ_PENDING_ROLLBACK' and app!='calculator':
            data['failures'].append('INFLIGHT_HOSTED_ROLLBACK_NOT_VERIFIED')
        data['status']='PASS_HOSTED_READONLY_WITH_SIGNALS' if not data['failures'] else 'BLOCKED'
    except Exception as exc:
        for role,firewall in firewalls.items():
            if firewall.get('documentDrift'):
                data.setdefault('browserDocumentDrift',{})[role]=firewall['documentDrift']
                data['failures'].append('BROWSER_DOCUMENT_'+firewall['documentDrift']['reason'])
        if app=='code-editor' and write_fixture is not None:
            data['saveNetworkWitness']={
                role:{'requests':sum(r['path']=='/api/applications/editor/project/file/save'
                                       for r in firewall['requests']),
                      'responses':[r['status'] for r in firewall['responses']
                                   if r['path']=='/api/applications/editor/project/file/save'],
                      'blockedReasons':[r for r in firewall['blocked']
                                        if 'disposable' in r.lower() or 'write' in r.lower()]}
                for role,firewall in firewalls.items()}
        data['failures'].append(type(exc).__name__+':'+str(exc).splitlines()[0][:160])
    finally:
        if 'requestGuard' not in data:
            data['requestGuard']={role:{'blocked':list(f['blocked']),'servedHashes':list(f['servedHashes']),
                                        'readApiCount':len(f['responses'])} for role,f in firewalls.items()}
        for ctx in contexts.values():
            try:ctx.close()
            except Exception:
                data['status']='BLOCKED'
                data['promotionAuthorized']=False
                data['failures'].append('DISPOSABLE_AUTH_CLEANUP_UNVERIFIED')
    return data


def run(host_url: str, repo: Path=ROOT, apps=APPS, *, storage_state: Path | None=None,
        allow_editor_read_history: bool=False, authorize_disposable_writes: bool=False,
        report: Path=DEFAULT, preflight_only: bool=False) -> dict:
    base = validate_loopback_base(secure_target(host_url))
    if not apps or len(set(apps))!=len(apps) or any(a not in APPS for a in apps):
        raise ValueError('INVALID_APPLICATION_SELECTION')
    if authorize_disposable_writes and ('code-editor' not in apps or not allow_editor_read_history):
        raise ValueError('DISPOSABLE_WRITES_REQUIRE_CODE_EDITOR_AND_EXPLICIT_EDITOR_READ_HISTORY')
    if storage_state is not None:
        if not storage_state.is_file():raise ValueError('BROWSER_STORAGE_STATE_NOT_FOUND')
        storage_state = str(storage_state.resolve())
    # These paths are local work products only. Do not run on a remote host.
    result={'schema':'mcel.hosted-signal-cutover.v1',
      'generatedAt':datetime.now(timezone.utc).isoformat(),'host':base,
      'scope':'ACTUAL_LOOPBACK_HOST_READONLY_BROWSER_DIFFERENTIAL',
      'report':'CUTOVER_NOT_AUTHORIZED', 'apps':{},'dependencies':{},
      'candidateMode':'TEST_ONLY_BROWSER_IN_MEMORY_HTML_PATCH',
      'productionPolicyChanged':False, 'productionCutoverAuthorized':False,
      'liveSourceWrites':'NOT_TESTED', 'liveAider':'NOT_TESTED',
      'editorReadHistorySideEffectAuthorized':allow_editor_read_history,
      'disposableSourceWritesAuthorized':authorize_disposable_writes,
      'liveMathicsAndAI':'NOT_TESTED', 'nativeMonacoVerified':False,
      'readOnlyGate':'INCOMPLETE','status':'BLOCKED',
      'isolatedCertifications':prior_isolated_certifications(repo,apps)}
    for app, evidence in result['isolatedCertifications'].items():
        print('MCEL_HOSTED_ISOLATED_CERT',app,evidence['status'],
              'runtime-hash-match='+str(evidence.get('runtimeSha256Matches')),
              'source-hash-match='+str(evidence.get('sourceSha256Matches')),flush=True)
    with sync_playwright() as pw:
        browser,source=launch_semantic_browser(pw)
        result['browser']=source
        try:
            before=_visit_dependency_routes(browser,base,storage_state=storage_state)
            result['parentShellPreflight']=_preflight_parent_shell(
                browser,base,repo,apps,storage_state=storage_state)
            result['hostDocumentPreflight']=_preflight_host_documents(
                browser,base,repo,apps,storage_state=storage_state)
            write_scope=None
            try:
                if authorize_disposable_writes and not preflight_only and all(
                        result['hostDocumentPreflight'][a]['status']=='PASS_EXACT' and
                        result['parentShellPreflight'][a]['status']=='PASS_EXACT' for a in apps):
                    disposable.verify_workspace(browser,base,repo,storage_state=storage_state)
                    folder=(repo/'runtime/reports').resolve()
                    if not folder.is_dir():raise ValueError('REPORT_DIRECTORY_REQUIRED_FOR_TEST_FILES')
                    write_scope=tempfile.TemporaryDirectory(prefix='mcel-hosted-signal-',dir=str(folder))
                    fixture=disposable.create_fixture(repo,Path(write_scope.name))
                else:
                    fixture=None
                for app in apps:
                    probe=result['hostDocumentPreflight'][app]
                    shell_probe=result['parentShellPreflight'][app]
                    if shell_probe['status']!='PASS_EXACT':
                        result['apps'][app]={'status':'BLOCKED','stages':[],
                            'failures':[shell_probe['reason']],
                            'actualSkips':0,'promotionAuthorized':False,
                            'targetRoute':ROUTES[app], 'parentShellPreflight':shell_probe,
                            'hostPreflight':probe}
                    elif probe['status']!='PASS_EXACT':
                        result['apps'][app]={'status':'BLOCKED','stages':[],
                            'failures':['HOST_DOCUMENT_'+probe['reason']],
                            'actualSkips':0,'promotionAuthorized':False,
                            'targetRoute':ROUTES[app], 'parentShellPreflight':shell_probe,
                            'hostPreflight':probe}
                    elif preflight_only:
                        result['apps'][app]={'status':'NOT_RUN_PREFLIGHT_ONLY','stages':[],
                            'failures':[],'actualSkips':0,'promotionAuthorized':False,
                            'targetRoute':ROUTES[app], 'parentShellPreflight':shell_probe,
                            'hostPreflight':probe}
                    else:
                        result['apps'][app]=_single_app(browser,app,base,repo,storage_state=storage_state,
                            allow_editor_read_history=allow_editor_read_history,
                            write_fixture=fixture if app=='code-editor' else None)
                        result['apps'][app]['hostPreflight']=probe
                        result['apps'][app]['parentShellPreflight']=shell_probe
                    print('MCEL_HOSTED_APP',app,result['apps'][app]['status'],flush=True)
            except Exception as exc:
                for app in apps:
                    if app not in result['apps']:
                        result['apps'][app]={'status':'BLOCKED',
                            'failures':['DISPOSABLE_PREFLIGHT_'+type(exc).__name__],
                            'promotionAuthorized':False}
            finally:
                if write_scope is not None:write_scope.cleanup()
            after=_visit_dependency_routes(browser,base,storage_state=storage_state)
        finally:
            browser.close()
    if 'code-editor' in result['apps']:
        editor=result['apps']['code-editor']
        if editor.get('liveWrites')=='PASS_DISPOSABLE_ONLY':
            result['liveSourceWrites']='PASS_DISPOSABLE_ONLY'
        result['nativeMonacoVerified']=(bool(editor.get('nativeMonaco')) and
            all(editor['nativeMonaco'].values()))
    for path in DEPENDENCIES:
        a=before.get(path,{});b=after.get(path,{})
        result['dependencies'][path]={
            'beforeHttp':a.get('httpStatus'),'afterHttp':b.get('httpStatus'),
            'unchangedBody':a.get('bodySha256') is not None and a.get('bodySha256')==b.get('bodySha256'),
            'verified':a.get('served') and b.get('served') and a.get('bodySha256')==b.get('bodySha256'),
        }
    if (len(result['apps'])==len(apps) and
        all(x['status']=='PASS_HOSTED_READONLY_WITH_SIGNALS' for x in result['apps'].values()) and
        all(d['verified'] for d in result['dependencies'].values())):
        result['readOnlyGate']='PASS_HOSTED_READONLY'
        if all(v['verified'] for v in result['isolatedCertifications'].values()):
            result['status']='PASS_PRECUTOVER_READONLY_NOT_FULLY_CERTIFIED'
    out=report if report.is_absolute() else repo/report
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,sort_keys=True,ensure_ascii=False)+'\n',encoding='utf-8')
    result['reportPath']=str(out)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-url',default=ORIGIN,help='http://127.0.0.1:<port> (live Main Computer)')
    parser.add_argument('--app',choices=APPS,action='append',help='Subset; default all three')
    parser.add_argument('--storage-state',type=Path,help='Existing browser authentication state; never included in reports')
    parser.add_argument('--allow-editor-read-history',action='store_true',
                        help='Opt in to Code Editor source open, which adds an Aider context/history entry')
    parser.add_argument('--authorize-disposable-writes',action='store_true',
        help='Opt in to source writes ONLY to newly generated temporary fixture files; requires --allow-editor-read-history')
    parser.add_argument('--preflight-only',action='store_true',
        help='Read-only route/content SHA diagnostic; no app mounts or scheduler switching')
    parser.add_argument('--report',type=Path,default=DEFAULT)
    args=parser.parse_args(argv)
    result=run(args.host_url,apps=tuple(args.app or APPS),storage_state=args.storage_state,
               allow_editor_read_history=args.allow_editor_read_history,
               authorize_disposable_writes=args.authorize_disposable_writes,report=args.report,
               preflight_only=args.preflight_only)
    for app,entry in result['apps'].items():
        print('MCEL_HOSTED_RESULT',app,entry['status'],'skips='+str(entry.get('actualSkips',0)),
              'failures='+','.join(entry['failures']),flush=True)
    print('MCEL_HOSTED_OVERALL',result['status'],'report:',result['reportPath'],flush=True)
    print('MCEL_HOSTED_CUTOVER BLOCKED live-source-writes/live-Aider/live-Mathics still unverified',flush=True)
    if args.preflight_only:
        return 0 if all(x['status']=='PASS_EXACT' for x in result['hostDocumentPreflight'].values()) and all(
            x['status']=='PASS_EXACT' for x in result['parentShellPreflight'].values()) else 1
    return 0 if result['status']=='PASS_PRECUTOVER_READONLY_NOT_FULLY_CERTIFIED' else 1

if __name__=='__main__':raise SystemExit(main())
