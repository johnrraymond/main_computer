"""Read-only live Code Editor signal cutover witness, including reversible runtime switch.

Checks the exact canonical native-child HTML served by the running localhost
host, then tests the built-in incumbent fallback and signals re-enable in a
separate browser session. No Save, Aider, Mathics, or source write is issued.

  python -m tools.mcel_code_editor_signal_cutover_smoke --host-url http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit

from main_computer.mcel_semantic_other_apps_host import render_semantic_page
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_v1_four_app_hosted_acceptance import validate_loopback_base

ROOT=Path(__file__).resolve().parents[1]
NATIVE_PATH='/applications/code-editor?mcel-shell-child=1'

def _expected_configuration(html: str, *, mode: str):
    integration='signalSchedulerIntegration:true'
    enabled='signalSchedulerStartEnabled:'+('true' if mode=='signals' else 'false')
    if html.count(integration)!=1 or html.count(enabled)!=1:
        raise RuntimeError('SOURCE_NOT_IN_EXPECTED_CODE_EDITOR_MODE')

def _exact_served_html(base: str, repo: Path, *, mode: str):
    expected=render_semantic_page('code-editor',repo,promoted=True)
    _expected_configuration(expected,mode=mode)
    try:
        with urlopen(base+NATIVE_PATH,timeout=20) as response:
            status=response.status
            actual=response.read()
    except (OSError,HTTPError) as exc:
        raise RuntimeError('HOSTED_NATIVE_CODE_EDITOR_UNAVAILABLE:'+type(exc).__name__) from exc
    if status!=200 or actual!=expected.encode('utf8'):
        raise RuntimeError('HOSTED_CODE_EDITOR_SOURCE_MISMATCH expected='+hashlib.sha256(expected.encode('utf8')).hexdigest()[:16]
                           +' observed='+hashlib.sha256(actual).hexdigest()[:16])
    return hashlib.sha256(actual).hexdigest(), actual

def verify(base: str, repo: Path=ROOT, *, expected_mode: str='signals') -> dict:
    """No remote route or unexpected consequential request is authorized."""
    if expected_mode not in ('signals','incumbent'):
        raise ValueError('INVALID_EXPECTED_MODE')
    base=validate_loopback_base(base)
    digest,verified_document=_exact_served_html(base,repo,mode=expected_mode)
    from playwright.sync_api import sync_playwright
    result={'schema':'mcel.code-editor-production-signal-witness.v1',
            'servedHash':digest,'expectedMode':expected_mode}
    with sync_playwright() as playwright:
        browser,source=launch_semantic_browser(playwright)
        try:
            ctx=browser.new_context(service_workers='block',viewport={'width':1272,'height':900})
            try:
                forbidden=[]
                def guard(route,request):
                    parsed=urlsplit(request.url)
                    if request.method=='GET':
                        route.continue_();return
                    # File inventory is a read-only capability implemented as POST.
                    if (request.method=='POST' and parsed.netloc==urlsplit(base).netloc
                        and parsed.scheme=='http' and parsed.path=='/api/applications/editor/files'):
                        route.continue_();return
                    forbidden.append(request.method+':'+parsed.path[:100])
                    route.abort()
                ctx.route('**/*',guard)
                page=ctx.new_page()
                errors=[]
                page.on('pageerror',lambda exc:errors.append(type(exc).__name__))
                # HTML was fetched with a real loopback HTTP GET and compared
                # byte-for-byte against the canonical promoted output. Mount
                # those exact verified bytes in an isolated browser. This
                # avoids relying on browser automation policies for localhost
                # navigation; it does not substitute a test candidate.
                page.set_content(verified_document.decode('utf8'),wait_until='domcontentloaded')
                page.wait_for_function('()=>!!window.__mcelWorkbench?.app',timeout=20000)
                status=page.locator('#mcel-workbench-status').inner_text(timeout=7000)
                if status!='Semantic application ready':
                    raise RuntimeError('HOSTED_CODE_EDITOR_MOUNT_NOT_READY')
                read_report=lambda: page.evaluate('window.__mcelWorkbench.app.signalCandidateReport()')
                initial=read_report()
                expected='integrated-opt-in' if expected_mode=='signals' else 'disabled'
                if initial['mode']!=expected or bool(initial.get('integrationEnabled'))!=(expected_mode=='signals'):
                    raise RuntimeError('PRODUCTION_SCHEDULER_START_MISMATCH')
                if expected_mode=='signals':
                    rolled=page.evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("incumbent")')
                    after_rollback=read_report()
                    if rolled['status']!='ROLLED_BACK' or after_rollback['mode']!='disabled':
                        raise RuntimeError('INCUMBENT_RUNTIME_ROLLBACK_FAILED')
                    restored=page.evaluate('window.__mcelWorkbench.app.setSignalSchedulingMode("certified")')
                    after_restore=read_report()
                    if restored['status']!='ENABLED' or after_restore['mode']!='integrated-opt-in':
                        raise RuntimeError('SIGNALS_RUNTIME_REENABLE_FAILED')
                    result['runtimeRollback']='PASS'
                    result['runtimeReenable']='PASS'
                else:
                    # Permanent rollback is server-side default and must not
                    # automatically re-enable signals in this witness.
                    result['runtimeRollback']='NOT_NEEDED_ALREADY_INCUMBENT'
                    result['runtimeReenable']='NOT_RUN'
                if forbidden:raise RuntimeError('UNAUTHORIZED_BROWSER_REQUEST:'+','.join(forbidden[:3]))
                if errors:raise RuntimeError('BROWSER_PAGEERROR_COUNT:'+str(len(errors)))
                if read_report()['fallbacks']:
                    raise RuntimeError('SIGNAL_SCHEDULER_UNSAFE_FALLBACK')
                result.update(status='PASS',browser=source,initialMode=initial['mode'],
                    finalMode=read_report()['mode'],pageErrors=0,blockedRequests=0)
                return result
            finally:ctx.close()
        finally:browser.close()

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host-url',default='http://127.0.0.1:8765')
    p.add_argument('--expect-mode',choices=('signals','incumbent'),default='signals')
    p.add_argument('--repo',type=Path,default=ROOT)
    args=p.parse_args(argv)
    try:
        r=verify(args.host_url,args.repo.resolve(),expected_mode=args.expect_mode)
        print('MCEL_CODE_EDITOR_CUTOVER',r['status'],'initial='+r['initialMode'],
              'final='+r['finalMode'],'rollback='+r['runtimeRollback'],
              'reenable='+r['runtimeReenable'],'pageErrors=0',flush=True)
        return 0
    except Exception as exc:
        print('MCEL_CODE_EDITOR_CUTOVER BLOCKED reason='+type(exc).__name__+':'+str(exc).splitlines()[0][:220],flush=True)
        return 1

if __name__=='__main__':raise SystemExit(main())
