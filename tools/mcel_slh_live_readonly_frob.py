"""Strictly allowlisted, actual-host MCEL SLH functional observation.

Without --protected-reads this lane never authenticates. With an explicit opt-in,
creating an ephemeral authentication session is permitted exclusively to READ
Pending/Enrolled/Trash/Retention. The backend is always the authority; this
harness NEVER approves, rejects, deploys, undeploys or writes an identity.

Reports contain counts, status and request classes, never identity records,
request bodies, tokens, keys or backend response text.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_v1_four_app_hosted_acceptance import validate_loopback_base, json_fingerprint
from main_computer.mcel_slh_application import compile_slh_application

# Request the native child document directly. The bare canonical route now
# serves the parent applications shell, which has no SLH semantic payload.
SLH_ROUTE = '/applications/slh/local?mcel-shell-child=1'
_ALLOWED_READS = frozenset(('read-pending', 'read-enrolled', 'read-trash', 'read-retention'))
_ADMIN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
_EXPLICIT_NONE_STORAGE_KEY = 'mcel.slh.selected-identity.v2.local'
_EXPLICIT_NONE_MARKER = '__EXPLICIT_NONE_V3__'
_ANONYMOUS_INIT_SCRIPT = (
    'sessionStorage.setItem(' + json.dumps(_EXPLICIT_NONE_STORAGE_KEY) + ', ' +
    json.dumps(_EXPLICIT_NONE_MARKER) + ');'
)


def validate_admin_id(admin_id: str | None, *, protected_reads: bool) -> str | None:
    """No implicit admin selection; reject unsafe input before opening a browser."""
    if admin_id is not None and not protected_reads:
        raise ValueError('--admin-id requires --protected-reads')
    if admin_id is not None and (not isinstance(admin_id, str) or
                                 _ADMIN_ID.fullmatch(admin_id) is None):
        raise ValueError('--admin-id must be a nonempty local identity ID (max 64 characters)')
    return admin_id


def _failed_authentication_request(requests: list[dict]) -> dict | None:
    """Detect a real authentication HTTP failure without parsing sensitive bodies."""
    return next((entry for entry in requests
                 if entry.get('path') == '/api/applications/slh/setup/action'
                 and entry.get('method') == 'POST'
                 and isinstance(entry.get('status'), int)
                 and entry['status'] >= 400), None)


def request_decision(base: str, url: str, method: str, body_text: str | None = None,
                     *, protected_reads: bool = False) -> str:
    """Fail closed on origin, path, method AND body. No generic API allowlist."""
    u, target = urlsplit(url), urlsplit(base)
    if (u.scheme, u.netloc) != (target.scheme, target.netloc) or u.username or u.password or u.fragment:
        return 'deny-origin'
    path = u.path
    if method == 'GET':
        # This is an exact, non-mutating route selector recognized by the
        # canonical MCEL shell. Arbitrary application query strings remain
        # forbidden; otherwise the harness could traverse untested views.
        if path == '/applications/slh/local' and u.query == 'mcel-shell-child=1':
            return 'allow-native-slh-child'
        if path == '/api/applications/slh/identities' and not u.query:
            return 'allow-public-read'
        if path == '/api/applications/slh/observe' and u.query == 'view=overview':
            return 'allow-public-read'
        if protected_reads and path == '/api/applications/slh/observe' and u.query == 'view=workers':
            return 'allow-protected-read'
        if not u.query and (path.startswith(('/applications/', '/assets/', '/static/')) or path == '/favicon.ico'):
            return 'allow-local-asset'
        return 'deny-get'
    if method != 'POST' or u.query or body_text is None or len(body_text) > 8192:
        return 'deny-method-or-body'
    try:
        body = json.loads(body_text)
    except (ValueError, TypeError):
        return 'deny-invalid-json'
    if not isinstance(body, dict):
        return 'deny-body'
    if path == '/api/applications/slh/seh/deployment' and body in (
        {'target': 'local', 'operation': 'inspect', 'arguments': {}, 'sessionTicket': ''},
        {'target': 'local', 'operation': 'operation-state', 'arguments': {}, 'sessionTicket': ''},
    ):
        return 'allow-public-read'
    if not protected_reads:
        return 'deny-protected-or-mutation'
    if path == '/api/applications/slh/setup/action':
        if (body.get('action') == 'authenticate' and set(body) == {'action', 'arguments'} and
                isinstance(body['arguments'], dict) and
                set(body['arguments']) == {'adminDeviceId'} and
                isinstance(body['arguments']['adminDeviceId'], str) and
                0 < len(body['arguments']['adminDeviceId']) <= 64):
            return 'allow-session-auth'
        if body == {'action': 'logout', 'arguments': {}}:
            return 'allow-session-cleanup'
    if path == '/api/applications/slh/identity-notebook' and (
        set(body) == {'target', 'operation', 'arguments', 'sessionTicket'} and
        body['target'] == 'local' and body['operation'] in _ALLOWED_READS and
        body['arguments'] == {} and body['sessionTicket'] == ''
    ):
        return 'allow-protected-read'
    return 'deny-protected-or-mutation'


def _state(page, admin_id: str | None = None) -> dict:
    """Only non-sensitive derived evidence may leave the browser."""
    return page.evaluate('''expectedAdmin => {
      const app=window.__mcelSlh?.app;
      const notebook=document.querySelector('[data-mcel-notebook="surface-node:slh.organization-identity-notebook"]');
      const deployment=document.querySelector('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]');
      const observed=app?.readState('state:seh-deployment-status');
      const pending=app?.readState('state:pending-enrollments');
      const shown=deployment?.querySelector('[data-mcel-operation-field="deploymentStatus"]')?.textContent?.trim();
      const undeploy=deployment?.querySelector('[data-mcel-operation-action="Undeploy"]');
      const directory=app?.readState('state:local-identities');
      const selected=app?.readState('state:selected-local-identity')||'';
      const target=app?.readState('state:selected-target');
      const lifecycle=app?.readState('state:organization-lifecycle');
      const startup=window.__mcelSlh?.diagnostics?.()?.startup;
      return {
        mounted:!!app && !!document.querySelector('[data-mcel-semantic-operational]'),
        ready:document.getElementById('mcel-workbench-status')?.textContent?.trim()==='Semantic SLH ready',
        pendingLoaded:Array.isArray(pending),
        pendingCount:Array.isArray(pending)?pending.length:null,
        renderedPendingRows:notebook?.querySelectorAll('[data-mcel-notebook-scroll] [data-mcel-record-row]').length??null,
        notebookMessage:notebook?.querySelector('[data-mcel-notebook-status]')?.textContent?.trim()??null,
        deploymentObserved:!!observed && typeof observed.deploymentStatus==='string',
        deploymentStatus:typeof observed?.deploymentStatus==='string'?observed.deploymentStatus:null,
        deploymentShown:typeof shown==='string' && shown===observed?.deploymentStatus,
        eligibleDeployment:[
          'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED',
          'PUBLIC_RUNTIME_PRESENT_SLH_DISCONNECTED',
          'APPLICATION_REGISTERED_NOT_RUNTIME_VERIFIED',
          'APPLICATION_REGISTERED_RUNTIME_UNAVAILABLE',
          'CONNECTED_VERIFIED'].includes(observed?.deploymentStatus),
        deploymentTabSelected:deployment?.querySelector('[data-mcel-operation-tab="deployment"]')?.getAttribute('aria-selected')==='true',
        undeployButtonMounted:!!undeploy,
        undeployDisabled:undeploy?.disabled??null,
        receiptCount:app?.receipts()?.length??null,
        identityDirectoryReady:Array.isArray(directory),
        identityObservationReady:startup?.identityRefresh==='completed' &&
          startup?.identityStatus==='completed' && startup?.identityDirectory==='AVAILABLE',
        lifecycleObservationReady:startup?.lifecycleRefresh==='completed',
        adminInDirectory:Array.isArray(directory) && !!expectedAdmin &&
          directory.some(row=>row.deviceId===expectedAdmin),
        selectedIdentityPresent:!!selected,
        selectedAdminMatches:!!expectedAdmin && selected===expectedAdmin,
        selectedIdentityInDirectory:Array.isArray(directory) &&
          directory.some(row=>row.deviceId===selected),
        localTargetSelected:target==='local',
        organizationRunning:lifecycle?.state==='running',
        pendingTabSelected:notebook?.querySelector('[data-mcel-notebook-tab="pending"]')?.getAttribute('aria-selected')==='true'
      };
    }''', admin_id)


def _protected_request_counts(requests: list[dict]) -> dict:
    """Only transport metadata; never persist bodies, credentials, or records."""
    return {
        'authentication': sum(item.get('method') == 'POST' and
                              item.get('path') == '/api/applications/slh/setup/action'
                              for item in requests),
        'notebook': sum(item.get('method') == 'POST' and
                        item.get('path') == '/api/applications/slh/identity-notebook'
                        for item in requests),
    }


def _pending_observation_ready(snapshot: dict) -> bool:
    """A verified empty Pending queue is not a hydration failure."""
    if snapshot.get('pendingLoaded') is not True:
        return False
    message = snapshot.get('notebookMessage')
    return message == '' or (snapshot.get('pendingCount') == 0 and
                             message == 'No pending records')


def run(host_url: str, report: Path, *, protected_reads: bool = False,
        admin_id: str | None = None) -> dict:
    """Read an actual loopback-hosted SLH, never fulfill/mock browser requests."""
    from playwright.sync_api import sync_playwright

    base = validate_loopback_base(host_url)
    admin_id = validate_admin_id(admin_id, protected_reads=protected_reads)
    result = {'schema': 'mcel.slh.live-functional.v1',
              'status': 'INCOMPLETE', 'scope': 'actual-loopback-read-only',
              'protected_reads_requested': protected_reads,
              'protected_reads': 'NOT_REQUESTED' if not protected_reads else 'INCOMPLETE',
              'live_mutations': 'NOT_PERMITTED',
              'checks': {}, 'requests': [], 'blocked': [], 'page_errors': [],
              'admin_id_supplied': admin_id is not None}
    with sync_playwright() as p:
        browser, source = launch_semantic_browser(p)
        result['browser'] = source
        try:
            context = browser.new_context(viewport={'width': 1272, 'height': 900},
                                          accept_downloads=False, service_workers='block')
            try:
                if protected_reads:
                    # Deterministic anonymous prerequisite: the application
                    # must not restore a preferred identity before the harness
                    # explicitly selects the validated administrator.
                    context.add_init_script(_ANONYMOUS_INIT_SCRIPT)
                def on_route(route, request):
                    decision = request_decision(base, request.url, request.method, request.post_data,
                                                protected_reads=protected_reads)
                    if decision.startswith('allow-'):
                        route.continue_()
                    else:
                        # Do not log bearer headers, request bodies or URLs containing secrets.
                        if len(result['blocked']) < 50:
                            result['blocked'].append({'method': request.method,
                                                      'path': urlsplit(request.url).path[:120],
                                                      'reason': decision})
                        route.abort()
                context.route('**/*', on_route)
                context.route_web_socket('**/*', lambda ws: (
                    result['blocked'].append({'method': 'WEBSOCKET', 'reason': 'forbidden'}), ws.close()))
                page = context.new_page()
                page.on('pageerror', lambda e: result['page_errors'].append(type(e).__name__))
                def on_response(response):
                    path = urlsplit(response.url).path
                    if path.startswith('/api/') and len(result['requests']) < 100:
                        result['requests'].append({'method': response.request.method,
                                                   'path': path[:120], 'status': response.status})
                page.on('response', on_response)
                response = page.goto(base + SLH_ROUTE, wait_until='domcontentloaded', timeout=15000)
                result['checks']['http_200'] = response is not None and response.status == 200
                # Fail with an actionable routing error rather than hanging
                # on a selector if a parent shell was served unexpectedly.
                if page.locator('#mcel-generated-data').count() != 1:
                    raise RuntimeError('Native SLH child document missing; verify mcel-shell-child=1 routing')
                result['checks']['native_slh_child_document'] = True
                served = json.loads(page.locator('#mcel-generated-data').text_content(timeout=8000))
                expected_ir, _ = compile_slh_application()
                result['checks']['same_semantic_ir_as_checkout'] = (
                    json_fingerprint(served['ir']) == json_fingerprint(expected_ir))
                page.wait_for_function('() => !!window.__mcelSlh?.app', timeout=12000)
                page.wait_for_timeout(450)
                # Force only a declared read intent, no app-specific mutation.
                receipt = page.evaluate("async () => await window.__mcelSlh.app.execute('intent:read-seh-deployment')")
                result['checks']['deployment_read_completed'] = receipt.get('disposition') == 'completed'
                # The operation notebook defaults to Configuration. Undeploy
                # exists only on the Deployment tab; inspecting its disabled
                # state before mounting it compares null against a Boolean.
                page.locator(
                    '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] '
                    '[data-mcel-operation-tab="deployment"]'
                ).click(timeout=8000)
                snapshot = _state(page)
                result['checks']['deployment_tab_selected'] = snapshot['deploymentTabSelected']
                result['checks']['undeploy_button_mounted'] = snapshot['undeployButtonMounted']
                result['deployment_evidence'] = {
                    'status': snapshot['deploymentStatus'],
                    'undeploy_disabled': snapshot['undeployDisabled'],
                    'eligible_deployment': snapshot['eligibleDeployment'],
                }
                # Reobserving must update properties in place, not replace the
                # user's focusable semantic elements. No mutation is made.
                continuity = page.evaluate('''async () => {
                  const app=window.__mcelSlh.app;
                  const button=document.querySelector('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] [data-mcel-operation-action="Undeploy"]');
                  const field=document.querySelector('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] [data-mcel-operation-field="deploymentStatus"]');
                  const receipt=await app.execute('intent:read-seh-deployment');
                  return {completed:receipt.disposition==='completed',
                    sameButton:button===document.querySelector('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] [data-mcel-operation-action="Undeploy"]'),
                    sameField:field===document.querySelector('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"] [data-mcel-operation-field="deploymentStatus"]')};
                }''')
                result['checks']['read_reconciles_without_remount'] = all(continuity.values())
                result['checks']['semantic_mounted'] = snapshot['mounted']
                # The same value must drive both status field and button state.
                result['checks']['deployment_observed_and_rendered'] = (
                    snapshot['deploymentObserved'] and snapshot['deploymentShown'])
                result['checks']['undeploy_state_consistent'] = (
                    snapshot['deploymentObserved'] and
                    snapshot['undeployDisabled'] is (not snapshot['eligibleDeployment']))
                result['checks']['bounded_receipts'] = (
                    isinstance(snapshot['receiptCount'], int) and snapshot['receiptCount'] <= 256)
                if protected_reads:
                    # Never use the LMS default or a restored browser identity
                    # as a substitute for an explicitly requested test identity.
                    if admin_id is None:
                        result['failure_stage'] = 'protected-prerequisites'
                        result['failure_code'] = 'ADMIN_ID_REQUIRED'
                        raise RuntimeError('Protected live test requires --admin-id')
                    # The SLH application, not MCEL nor this harness, owns
                    # readiness and the actual protected-read activation.
                    deadline = time.monotonic() + 12
                    while True:
                        prerequisites = _state(page, admin_id)
                        if (prerequisites['identityDirectoryReady'] and
                            prerequisites['identityObservationReady'] and
                            prerequisites['lifecycleObservationReady'] and
                            prerequisites['localTargetSelected'] and
                            prerequisites['organizationRunning']):
                            break
                        if time.monotonic() >= deadline:
                            result['failure_stage'] = 'protected-prerequisites'
                            result['failure_code'] = ('IDENTITY_DIRECTORY_UNAVAILABLE' if not prerequisites['identityDirectoryReady']
                                or not prerequisites['identityObservationReady']
                                else 'LOCAL_TARGET_NOT_READY' if not prerequisites['localTargetSelected']
                                else 'LIFECYCLE_OBSERVATION_UNAVAILABLE' if not prerequisites['lifecycleObservationReady']
                                else 'ORGANIZATION_NOT_RUNNING')
                            raise RuntimeError('SLH protected read prerequisites not ready: '+result['failure_code'])
                        page.wait_for_timeout(125)
                    result['checks']['admin_identity_discovered'] = prerequisites['adminInDirectory']
                    if not prerequisites['adminInDirectory']:
                        result['failure_stage'] = 'protected-prerequisites'
                        result['failure_code'] = 'ADMIN_ID_NOT_IN_VERIFIED_DIRECTORY'
                        raise RuntimeError('Specified administrator identity not present in LMS-SLH directory')
                    result['checks']['anonymous_identity_unselected'] = not prerequisites['selectedIdentityPresent']
                    if not result['checks']['anonymous_identity_unselected']:
                        result['failure_stage'] = 'protected-anonymous-context'
                        result['failure_code'] = 'IDENTITY_SELECTED_BEFORE_EXPLICIT_SELECTION'
                        raise RuntimeError('Browser selected an identity before the explicit test selection')
                    tab = page.locator('[data-mcel-notebook-tab="pending"]')
                    tab.scroll_into_view_if_needed(timeout=8000)
                    # Assert the anonymous state while Pending is visible.
                    # No implicit authentication is allowed on a blank signer.
                    page.wait_for_timeout(500)
                    anonymous = _state(page, admin_id)
                    anonymous_calls = _protected_request_counts(result['requests'])
                    result['checks']['anonymous_does_not_authenticate'] = (
                        not anonymous['selectedIdentityPresent'] and not anonymous['pendingLoaded'] and
                        anonymous_calls['authentication'] == 0 and anonymous_calls['notebook'] == 0)
                    if not result['checks']['anonymous_does_not_authenticate']:
                        result['failure_stage'] = 'protected-anonymous-context'
                        result['failure_code'] = 'UNAUTHORIZED_AUTO_READ'
                        raise RuntimeError('Pending initiated a protected request without an administrator')
                    result['checks']['pending_tab_selected'] = anonymous['pendingTabSelected']
                    if not anonymous['pendingTabSelected']:
                        result['failure_stage'] = 'protected-activation'
                        result['failure_code'] = 'PENDING_TAB_NOT_ACTIVE'
                        raise RuntimeError('Pending notebook tab is not initially selected')
                    # This is the ordinary semantic identity-selection intent.
                    # It neither authenticates nor sends a protected read on
                    # its own. The SLH application must initiate that read
                    # when its declared prerequisites and visibility are met.
                    selection = page.evaluate('''async id => {
                      const receipt=await window.__mcelSlh.app.execute(
                        'intent:choose-identity',{key:id});
                      return {disposition:receipt?.disposition};
                    }''', admin_id)
                    selected = _state(page, admin_id)
                    result['checks']['admin_selected_via_mcel'] = (
                        selection['disposition'] == 'selected' and selected['selectedAdminMatches']
                        and selected['selectedIdentityInDirectory'])
                    if not result['checks']['admin_selected_via_mcel']:
                        result['failure_stage'] = 'protected-prerequisites'
                        result['failure_code'] = 'SEMANTIC_ADMIN_SELECTION_FAILED'
                        raise RuntimeError('MCEL did not select the verified administrator identity')
                    # Distinguish an actual backend authentication failure from
                    # a UI/hydration timeout. The response event records only
                    # method, path and HTTP status (never token or body).
                    deadline = time.monotonic() + 15
                    while True:
                        auth_failure = _failed_authentication_request(result['requests'])
                        if auth_failure:
                            result['protected_reads'] = 'AUTHENTICATION_FAILED'
                            result['failure_stage'] = 'protected-authentication'
                            result['failure_code'] = 'AUTHENTICATION_HTTP_FAILURE'
                            raise RuntimeError('Protected read authentication returned HTTP '
                                               + str(auth_failure['status']))
                        protected = _state(page, admin_id)
                        if _pending_observation_ready(protected):
                            break
                        if time.monotonic() >= deadline:
                            counts = _protected_request_counts(result['requests'])
                            result['protected_request_counts'] = counts
                            if counts['authentication'] == 0:
                                code = 'APPLICATION_DID_NOT_AUTHENTICATE'
                                stage = 'protected-activation'
                            elif counts['notebook'] == 0:
                                code = 'APPLICATION_DID_NOT_READ_PENDING'
                                stage = 'protected-activation'
                            else:
                                code = 'PROTECTED_RECORDS_NOT_RECONCILED'
                                stage = 'protected-reconciliation'
                            result['failure_stage'] = stage
                            result['failure_code'] = code
                            raise RuntimeError('Protected Pending hydration incomplete: '+code)
                        page.wait_for_timeout(125)
                    read = _state(page, admin_id)
                    result['protected_request_counts'] = _protected_request_counts(result['requests'])
                    result['checks']['app_initiated_authentication_and_read'] = (
                        result['protected_request_counts']['authentication'] >= 1 and
                        result['protected_request_counts']['notebook'] >= 1)
                    result['checks']['pending_loaded_without_tab_click'] = read['pendingLoaded']
                    result['checks']['pending_rows_reconciled'] = (
                        read['renderedPendingRows'] == read['pendingCount'])
                    result['checks']['pending_status_clear'] = _pending_observation_ready(read)
                    result['protected_reads'] = ('PASS' if all(result['checks'][key] for key in (
                        'pending_loaded_without_tab_click', 'pending_rows_reconciled', 'pending_status_clear'))
                        else 'INCOMPLETE')
                # No claim that a blocked auto-auth read is a mutation. Still
                # report it, but do not treat optional blocked auth as an error.
                safe_blocks = (not protected_reads and all(
                    item.get('method') != 'WEBSOCKET' and item.get('path') in (
                        '/api/applications/slh/setup/action',
                        '/api/applications/slh/identity-notebook')
                    for item in result['blocked']))
                result['status'] = ('PASS_LIVE_READ_ONLY' if all(result['checks'].values())
                                   and (not protected_reads or result['protected_reads'] == 'PASS')
                                   and not result['page_errors'] and
                                   (not result['blocked'] or safe_blocks)
                                   else 'INCOMPLETE')
            except Exception as exc:
                result['error'] = type(exc).__name__ + ': ' + str(exc).splitlines()[0][:180]
            finally:
                if protected_reads and 'page' in locals():
                    session_started = any(item['path'] == '/api/applications/slh/setup/action'
                                          and item['status'] in (200, 201)
                                          for item in result['requests'])
                    if session_started:
                        try:
                            # No identity mutation: this is the semantic local
                            # deselection operation, which revokes this context's
                            # bearer via the allowlisted logout request.
                            with page.expect_response(lambda r:
                                urlsplit(r.url).path == '/api/applications/slh/setup/action'
                                and r.request.method == 'POST', timeout=5000) as logout:
                                page.evaluate("async () => await window.__mcelSlh.app.execute('intent:choose-identity', {key:''})")
                            result['checks']['session_cleanup'] = 200 <= logout.value.status < 300
                        except Exception:
                            result['checks']['session_cleanup'] = False
                        if not result['checks']['session_cleanup']:
                            result['status'] = 'INCOMPLETE'
                context.close()
        finally:
            browser.close()
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return result


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-url', required=True)
    parser.add_argument('--protected-reads', action='store_true',
                        help='Explicitly allow ephemeral admin authentication and protected READ operations')
    parser.add_argument('--admin-id', help='Explicit LMS-SLH identity for protected read verification; no implicit selection')
    parser.add_argument('--report', type=Path,
                        default=Path('runtime/reports/mcel-slh-live-functional/report.json'))
    args = parser.parse_args()
    try:
        result = run(args.host_url, args.report, protected_reads=args.protected_reads,
                     admin_id=args.admin_id)
    except ValueError as exc:
        parser.error(str(exc))
    print('MCEL_SLH_LIVE_FUNCTIONAL', result['status'], 'report:', args.report)
    return 0 if result['status'] == 'PASS_LIVE_READ_ONLY' else 1


if __name__ == '__main__':
    raise SystemExit(main())
