"""Verify canonical Worker identity selection against the actual local LMS.

Read-only: no identity creation, authentication, enrollment, wallet, or settings
mutation. The separate disposable SLH/SEH enrollment tests verify authority.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from tools.mcel_phase2b_browser import launch_semantic_browser

from tools.mcel_functional_auth import ORIGIN, new_context
PARENT = ORIGIN + '/applications/worker'
CHILD = PARENT + '?mcel-shell-child=1'
IDENTITIES = '/api/applications/slh/identities'
REPORT = Path('runtime/reports/mcel-worker-identity-live/report.json')
APPROVAL_REPORT = Path('runtime/reports/mcel-worker-approval-path/report.json')
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')


def allow_read(url, method):
    parsed = urlsplit(url)
    if method != 'GET' or (parsed.scheme, parsed.netloc) != ('https', urlsplit(ORIGIN).netloc):
        return False
    if parsed.fragment:
        return False
    return (url in (PARENT, CHILD) or
            (not parsed.query and (parsed.path == IDENTITIES or
            parsed.path.startswith(('/applications/', '/assets/', '/static/', '/api/applications/worker/')) or
            parsed.path == '/favicon.ico')))



class ProbeFailure(RuntimeError):
    """Static, non-secret failure reason suitable for a diagnostic report."""


def url_class(url):
    # Do not log URL queries, fragments, paths, or credentials from redirects.
    if url == PARENT:
        return 'canonical-parent'
    if url == CHILD:
        return 'canonical-child'
    try:
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.netloc) in (('https', urlsplit(ORIGIN).netloc), ('http','127.0.0.1:8765')):
            return 'other-local-route'
        if parsed.scheme == 'https' and parsed.hostname in ('127.0.0.1', 'localhost', 'mc.localhost', 'main-computer.localhost'):
            return 'local-https-route'
    except ValueError:
        pass
    return 'other-origin-or-browser-error'


def verify_canonical_surface(page, result):
    checks = result['checks']
    result['stage'] = 'canonical-http'
    response = page.goto(PARENT, wait_until='domcontentloaded')
    result['canonicalHttpStatus'] = response.status if response is not None else None
    result['canonicalUrlClass'] = url_class(page.url)
    if response is None:
        raise ProbeFailure('canonical-no-http-response')
    if response.status in (401, 403):
        raise ProbeFailure('canonical-access-denied')
    if response.status != 200:
        raise ProbeFailure('canonical-http-not-200')
    if page.url != PARENT:
        raise ProbeFailure('canonical-unexpected-url')
    checks['canonicalHttp200'] = True
    result['stage'] = 'canonical-parent-marker'
    page.locator('body[data-mcel-shell-canonical-promoted="true"]').wait_for(state='attached')
    checks['canonicalParentMarker'] = True
    result['stage'] = 'canonical-frame-element'
    element = page.locator('#mcel-shell-frame').element_handle()
    if element is None:
        raise ProbeFailure('canonical-frame-element-missing')
    checks['canonicalFrameElement'] = True
    result['stage'] = 'canonical-frame-attachment'
    frame = element.content_frame()
    if frame is None:
        raise ProbeFailure('canonical-child-missing')
    checks['canonicalFrameAttached'] = True
    result['stage'] = 'canonical-child-url'
    frame.wait_for_url(CHILD)
    checks['canonicalChildUrl'] = True
    result['stage'] = 'canonical-child-marker'
    frame.locator('body[data-mcel-worker-phase="promoted"]').wait_for(state='attached')
    checks['canonicalShellAndChild'] = True
    return frame

def verify_enrollment_entry(ir, result):
    """First integration gate: a real request-only capability on the Worker.

    `enrollWorker` is the method named in the existing Worker truth test;
    `requestWorkerEnrollment` is the explicit request-only spelling. Do not
    treat Hub selection, settings writes, or marketplace offer registration
    as enrollment. A declaration is only a prerequisite, never a completed
    enrollment or evidence of backend authority.
    """
    result['stage'] = 'worker-enrollment-request'
    if ir.get('application', {}).get('appId') != 'worker':
        raise ProbeFailure('worker-semantic-ir-mismatch')
    methods = {'enrollWorker', 'requestWorkerEnrollment'}
    capabilities = {c['id']: c for c in ir.get('capabilities', [])}
    effects = {e['id']: e for e in ir.get('effects', [])}
    declared = set()
    for intent in ir.get('intents', []):
        method = intent.get('runtimeMethod')
        if (method not in methods or intent.get('operationKind') != 'capability'
                or intent.get('risk') != 'consequential'):
            continue
        for ref in intent.get('effectRefs', []):
            effect = effects.get(ref.get('ref'), {})
            cap = capabilities.get(effect.get('target', {}).get('ref'), {})
            if (effect.get('owner', {}).get('ref') == intent.get('id') and
                    effect.get('effectKind') == 'capability-request' and any(
                        op.get('runtimeMethod') == method and
                        op.get('name') == intent.get('operation') and
                        op.get('risk') == 'consequential'
                        for op in cap.get('operations', []))):
                declared.add(intent['id'])
    result['checks']['workerEnrollmentCapabilityDeclared'] = bool(declared)
    if not declared:
        raise ProbeFailure('worker-enrollment-request-not-implemented')
    nodes = [node for surface in ir.get('surfaces', [])
             for node in surface.get('nodes', [])]
    bound = any(n.get('nodeKind') in ('control', 'form') and
                n.get('intent', {}).get('ref') in declared for n in nodes)
    result['checks']['workerEnrollmentControlDeclared'] = bound
    if not bound:
        raise ProbeFailure('worker-enrollment-control-not-bound')
    # The caller must still exercise the actual signed request and approval.
    return declared


def run(expected_device=None, *, approval_path=False, authorize_disposable_enrollment=False):
    if approval_path and authorize_disposable_enrollment:
        from tools.mcel_worker_approval_live import run_approval
        return run_approval()
    from playwright.sync_api import sync_playwright
    result = {'schema': 'mcel.worker.identity-live.v1', 'status': 'UNVERIFIED',
              'stage': 'browser-launch', 'checks': {}, 'blockedRequests': 0,
              'scope': ('canonical-worker-approval-path-entry-read-only' if approval_path
                        else 'canonical-worker-local-identity-selection-only')}
    checks = result['checks']
    try:
        with sync_playwright() as p:
            browser, _ = launch_semantic_browser(p)
            try:
                result['stage'] = 'gateway-authentication'
                ctx = new_context(browser, ORIGIN, service_workers='block')
                def route_request(route, request):
                    if allow_read(request.url, request.method):
                        route.continue_()
                    else:
                        result['blockedRequests'] += 1
                        if request.is_navigation_request():
                            result['blockedNavigationClass'] = url_class(request.url)
                        if request.method != 'GET':
                            result['blockedMutation'] = True
                        route.abort()
                ctx.route('**/*', route_request)
                page = ctx.new_page()
                page.set_default_timeout(20000)
                frame = verify_canonical_surface(page, result)
                result['stage'] = 'lms-identity-inventory'
                frame.wait_for_function("() => window.__mcelWorker?.app.readState('state:local-identity-status')?.state === 'AVAILABLE'", timeout=45000)
                inventory = frame.evaluate("async () => {const r=await fetch('/api/applications/slh/identities',{credentials:'same-origin',cache:'no-store'}); if(!r.ok)throw Error('identity-read'); const v=await r.json(); return {schema:v.schema,state:v.state,defaultDeviceId:v.defaultDeviceId,ids:v.identities.map(x=>x.deviceId).sort()}}")
                if inventory['schema'] != 'mcel.slh.identities.v4b' or inventory['state'] != 'AVAILABLE':
                    raise ProbeFailure('lms-unverified')
                selected = expected_device or inventory['defaultDeviceId']
                if not ID.fullmatch(selected) or selected not in inventory['ids']:
                    raise ProbeFailure('requested-identity-unavailable')
                frame.wait_for_function("ids => {const rows=window.__mcelWorker.app.readState('state:local-identities'); return Array.isArray(rows)&&JSON.stringify(rows.map(x=>x.deviceId).sort())===JSON.stringify(ids)}", arg=inventory['ids'])
                checks['lmsInventoryMatchesWorker'] = True
                result['identityCount'] = len(inventory['ids'])
                result['stage'] = 'identity-selection'
                frame.locator('[data-mcel-node="surface-node:worker.worker-local-identities"] button[data-select-key="'+selected+'"]').click()
                frame.wait_for_function("id => window.__mcelWorker.app.readState('state:selected-local-identity')===id", arg=selected)
                checks['localIdentitySelected'] = True
                observed = frame.evaluate("id => window.__mcelWorker.app.readState('state:local-identities').find(x=>x.deviceId===id)?.accessStatus", selected)
                if observed != 'SLH/SEH access not verified':
                    raise ProbeFailure('local-selection-misrepresented-as-authentication')
                checks['selectionDoesNotClaimEnrollment'] = True
                if result.get('blockedMutation'):
                    raise ProbeFailure('unexpected-mutation-attempt')
                checks['noMutationAttempt'] = True
                if approval_path:
                    payload = json.loads(frame.locator('#mcel-generated-data').text_content())
                    verify_enrollment_entry(payload['ir'], result)
                    # A declaration cannot establish a successful lifecycle.
                    raise ProbeFailure('worker-enrollment-execution-not-exercised')
                result['stage'] = 'complete'
                result['status'] = 'PASS'
            finally:
                try:
                    if 'ctx' in locals(): ctx.close()
                finally:
                    browser.close()
    except Exception as exc:
        result['status'] = 'UNVERIFIED'
        # No raw exception text, identity record, token, or browser payload.
        result['errorClass'] = type(exc).__name__
        if isinstance(exc, ProbeFailure):
            result['errorReason'] = str(exc)
            if approval_path and result['stage'] == 'worker-enrollment-request':
                if str(exc) in ('worker-enrollment-request-not-implemented',
                                'worker-enrollment-control-not-bound'):
                    result['status'] = 'BLOCKED'
        else:
            result['errorReason'] = result['stage'] + '-operation-failed'
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-device', help='Explicit local identity ID; otherwise select the observed default, without authenticating')
    parser.add_argument('--approval-path', action='store_true',
                        help='Check the first missing Worker-to-SLH/SEH enrollment entry gate; no mutations')
    parser.add_argument('--authorize-disposable-enrollment', action='store_true',
                        help='Exercise actual Worker -> SLH approval and revocation using a new disposable identity')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args(argv)
    if args.expected_device is not None and not ID.fullmatch(args.expected_device):
        parser.error('invalid identity ID')
    if args.authorize_disposable_enrollment and (not args.approval_path or args.expected_device):
        parser.error('disposable enrollment requires --approval-path and no --expected-device')
    result = run(args.expected_device, approval_path=args.approval_path,
                 authorize_disposable_enrollment=args.authorize_disposable_enrollment)
    args.report = args.report or (APPROVAL_REPORT if args.approval_path else REPORT)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.report.with_suffix('.partial')
    temporary.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    temporary.replace(args.report)
    print('WORKER_APPROVAL_PATH' if args.approval_path else 'WORKER_IDENTITY_LIVE', result['status'], 'stage='+result['stage'],
          'reason='+result.get('errorReason', 'verified'),
          'http='+str(result.get('canonicalHttpStatus', 'unavailable')),
          'url-class='+result.get('canonicalUrlClass', 'unavailable'),
          ('scope=approval-disposable-local' if args.authorize_disposable_enrollment else 'scope=approval-entry-read-only' if args.approval_path else 'scope=selection-only'), 'report='+str(args.report), flush=True)
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
