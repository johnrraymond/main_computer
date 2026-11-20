"""Dangerous, browser-driven SLH acceptance against an explicitly approved LOCAL host.

    python -m tools.dangerous_mutation --mode preflight
    python -m tools.dangerous_mutation --mode live --execute-mutations --acknowledge-real-mutations

The runner independently executes canonical read-only SEH inspection, then clicks
MCEL's real Deploy / Redeploy and Undeploy buttons. It verifies the durable LMS
receipt, modal and canonical Mother readback. It never substitutes a direct
mutation-harness invocation for a browser action. Existing deployments may be
replaced only with --cycle-active-seh; ambiguous deployment state refuses.

No screenshots, pixel baselines, generated blobs or secrets are stored.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from typing import Callable

from main_computer.mcel_slh_application import compile_slh_application
from main_computer.mcel_slh_seh_deployment import execute_seh_deployment
from tools.mcel_slh_dangerous_functional_acceptance import seh_arguments
from tools.mcel_slh_live_readonly_frob import SLH_ROUTE, _ANONYMOUS_INIT_SCRIPT
from tools.mcel_v1_four_app_hosted_acceptance import validate_loopback_base, json_fingerprint
from tools.mcel_phase2b_browser import launch_semantic_browser

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = Path('runtime/reports/mcel-slh-dangerous-mutation/report.json')
DEFAULT_RECONCILE_REPORT = Path('runtime/reports/mcel-slh-dangerous-mutation/reconciliation.json')
NOTEBOOK = '[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]'
ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{15,127}\Z')
UUID_PATTERN = re.compile(r'[A-Za-z0-9_-]{8,64}\Z')
READ_DEPLOY = frozenset(('inspect', 'operation-state', 'operation-status'))


class MutationRefused(RuntimeError):
    """Fail closed before destructive writes or when their outcome is unverified."""


def ensure(ok: bool, code: str) -> None:
    if not ok:
        raise MutationRefused(code)


def _read_canonical(state_root: Path, local_port: int) -> dict:
    # Import does NOT invoke deploy/undeploy. Only the canonical inspect operation.
    import seh_mutate_harness
    value = seh_mutate_harness.run(seh_arguments(
        'inspect', root=state_root, local_port=local_port, admin_device=None))
    ensure(type(value) is dict and value.get('status') == 'READ_ONLY' and
           value.get('network') == 'mainnet' and value.get('registered') is True,
           'CANONICAL_DEPLOYMENT_NOT_REGISTERED')
    deployment = value.get('deployment')
    ensure(type(deployment) is dict and
           type(deployment.get('application_uuid')) is str and
           UUID_PATTERN.fullmatch(deployment['application_uuid']) is not None,
           'CANONICAL_APPLICATION_UUID_UNVERIFIED')
    return value


def verify_resident_admin(*, local_port: int, state_root: Path) -> dict:
    """Read-only signed authority gate before any dangerous browser click.

    Uses the same canonical signer/selection path as the SEH mutation harness.
    This is NOT a substitute for the separate page and LMS inspections.
    """
    import seh_mutate_harness
    args = seh_mutate_harness._parser().parse_args([
        'verify-resident-admin', '--network', 'mainnet',
        '--runtime-state-root', str(state_root), '--local-port', str(local_port),
    ])
    try:
        result = seh_mutate_harness.run(args)
    except seh_mutate_harness.HarnessError as exc:
        # Only fixed classification codes enter durable reports. Never persist
        # arbitrary exception text or authentication-response payloads.
        reason = str(exc)
        code = ('RESIDENT_ADMIN_SELECTION_AMBIGUOUS'
                if 'Multiple verified administrators' in reason else
                'RESIDENT_ADMIN_NOT_VERIFIED'
                if 'No verified resident administrator' in reason else
                'RESIDENT_ADMIN_TRANSPORT_UNAVAILABLE'
                if 'localhost service is unavailable' in reason else
                'RESIDENT_ADMIN_PREFLIGHT_REFUSED')
        raise MutationRefused(code) from None
    ensure(type(result) is dict and result.get('status') == 'VERIFIED_ADMIN_SESSION'
           and result.get('session_closed') is True and result.get('remote_mutation') is False
           and type(result.get('device_id')) is str and bool(result['device_id']),
           'RESIDENT_ADMIN_AUTHORITY_UNVERIFIED')
    return {'status': 'VERIFIED', 'device_id': result['device_id'],
            'mutation_submitted': False}


def canonical_info(record: dict) -> dict:
    value = record.get('deployment') or {}
    return {'uuid': value.get('application_uuid'),
            'undeployed': value.get('undeployed'),
            'generation': record.get('generation')}


def read_lms_deployment() -> dict:
    # LMS/SLH inspection is independently authoritative for the live page.
    return execute_seh_deployment('inspect', {}, target='local')


def preflight(*, state_root: Path, local_port: int,
              expect_uuid: str | None = None,
              inspect: Callable[[], dict] | None = None,
              lms: Callable[[], dict] = read_lms_deployment) -> dict:
    record = (inspect or (lambda: _read_canonical(state_root, local_port)))()
    canonical = canonical_info(record)
    if expect_uuid is not None:
        ensure(canonical['uuid'] == expect_uuid, 'PINNED_SEH_UUID_CHANGED')
    status = lms()
    ensure(type(status) is dict and type(status.get('deploymentStatus')) is str,
           'LMS_DEPLOYMENT_INSPECTION_UNAVAILABLE')
    ensure(status.get('applicationUuid') == canonical['uuid'],
           'LMS_CANONICAL_UUID_MISMATCH')
    ensure(status.get('generation') == canonical['generation'],
           'LMS_CANONICAL_GENERATION_MISMATCH')
    ensure(bool(status.get('selectedController')), 'LMS_CONTROLLER_NOT_SELECTED')
    # A stale Mother tombstone must never override a verified live runtime, or
    # vice versa. Either mismatch requires human investigation, not a cycle.
    phase = status['deploymentStatus']
    runtime_present = phase in (
        'CONNECTED_VERIFIED', 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED',
        'PUBLIC_RUNTIME_PRESENT_SLH_DISCONNECTED')
    runtime_absent = phase in ('UNDEPLOYED', 'SEH_UNDEPLOYED')
    ensure(not (canonical['undeployed'] is True and runtime_present),
           'CANONICAL_TOMBSTONE_CONTRADICTS_LIVE_RUNTIME')
    ensure(not (canonical['undeployed'] is False and runtime_absent),
           'CANONICAL_ACTIVE_CONTRADICTS_LMS_TOMBSTONE')
    operations = execute_seh_deployment('operation-state', {}, target='local') if inspect is None else {
        'state': 'IDLE', 'busy': False}  # deterministic pure-function tests
    ensure(type(operations) is dict and operations.get('state') == 'IDLE' and
           operations.get('busy') is False, 'LMS_HAS_ACTIVE_MANAGEMENT_OPERATION')
    # Unknown is not deployed=False. A registration or an unavailable runtime
    # does not prove that destructive replacement is safe.
    # A legacy canonical record can omit undeployed altogether. In that case
    # require an independent LMS runtime-present phase before treating it as
    # active. Mere registration is NOT enough.
    # A canonical non-tombstone establishes registration, not a running
    # service. Only a second authoritative runtime-present observation permits
    # destruction/replacement of an existing application.
    mode = ('tombstone' if canonical['undeployed'] is True else
            'active' if runtime_present else 'unknown')
    return {'status': 'READ_ONLY', 'network': 'mainnet', 'application_uuid': canonical['uuid'],
            'generation': canonical['generation'], 'undeployed': canonical['undeployed'],
            'state': mode, 'state_basis': ('canonical' if canonical['undeployed'] is not None
                                           else 'lms-verified-runtime' if runtime_present else 'ambiguous'),
            'deployment_phase': status['deploymentStatus'],
            'selected_controller': status.get('selectedController'), 'lms_idle': True}


def cycle_plan(observed: dict, *, cycle_active: bool) -> list[str]:
    ensure(observed.get('status') == 'READ_ONLY' and observed.get('lms_idle') is True,
           'PREFLIGHT_NOT_VERIFIED')
    if observed.get('state') == 'tombstone':
        return ['deploy', 'undeploy']
    if observed.get('state') == 'active':
        ensure(cycle_active, 'ACTIVE_SEH_REPLACEMENT_REQUIRES_CYCLE_ACTIVE_SEH')
        return ['undeploy', 'deploy']
    raise MutationRefused('AMBIGUOUS_SEH_STATE_NO_DESTRUCTIVE_CYCLE')


class BrowserGate:
    """Exact origin, method, body and one-use operation allowlist."""
    def __init__(self, base: str):
        self.base = base
        self.armed: str | None = None
        self.mutations: list[dict] = []
        self.blocked: list[dict] = []
        self.on_mutation: Callable[[], None] | None = None
        self.last_receipt: dict | None = None

    def arm(self, operation: str) -> None:
        ensure(operation in ('deploy', 'undeploy') and self.armed is None,
               'MUTATION_GATE_NOT_IDLE')
        self.armed = operation

    def classify(self, url: str, method: str, body_text: str | None) -> str:
        parsed, base = urlsplit(url), urlsplit(self.base)
        if ((parsed.scheme, parsed.netloc) != (base.scheme, base.netloc) or
                parsed.username or parsed.password or parsed.fragment):
            return 'DENY_ORIGIN'
        if method == 'GET':
            if parsed.path == '/applications/slh/local' and parsed.query == 'mcel-shell-child=1':
                return 'ALLOW_DOCUMENT'
            if not parsed.query and parsed.path.startswith(('/assets/', '/static/', '/applications/')):
                return 'ALLOW_ASSET'
            if parsed.path == '/favicon.ico' and not parsed.query:
                return 'ALLOW_ASSET'
            if parsed.path == '/api/applications/slh/identities' and not parsed.query:
                return 'ALLOW_READ'
            if parsed.path == '/api/applications/slh/observe' and parsed.query == 'view=overview':
                return 'ALLOW_READ'
            return 'DENY_READ'
        if method != 'POST' or parsed.query or parsed.path != '/api/applications/slh/seh/deployment':
            return 'DENY_OPERATION'
        try:
            body = json.loads(body_text or '')
        except (ValueError, TypeError):
            return 'DENY_BODY'
        if not isinstance(body, dict) or body.get('target') != 'local' or body.get('sessionTicket') != '' or not isinstance(body.get('arguments'), dict):
            return 'DENY_TARGET'
        operation = body.get('operation')
        if operation in READ_DEPLOY:
            if set(body) != {'target', 'sessionTicket', 'operation', 'arguments'}:
                return 'DENY_READ_FIELDS'
            args = body['arguments']
            if operation in ('inspect', 'operation-state') and args == {}:
                return 'ALLOW_READ'
            if operation == 'operation-status' and set(args) == {'operationId'} and isinstance(args['operationId'], str) and ID_PATTERN.fullmatch(args['operationId']):
                return 'ALLOW_READ'
            return 'DENY_READ_ARGUMENTS'
        if operation in ('deploy', 'undeploy'):
            if (operation != self.armed or body['arguments'] != {} or
                    set(body) != {'target', 'sessionTicket', 'operation', 'arguments', 'operationId'} or
                    type(body.get('operationId')) is not str or ID_PATTERN.fullmatch(body['operationId']) is None):
                return 'DENY_MUTATION'
            self.armed = None  # single-use even if server never responds
            self.mutations.append({'operation': operation, 'operation_id': body['operationId']})
            if self.on_mutation is not None:
                self.on_mutation()
            return 'ALLOW_MUTATION'
        return 'DENY_OPERATION'

    def intercept(self, route, request) -> None:
        decision = self.classify(request.url, request.method, request.post_data)
        if decision.startswith('ALLOW_'):
            route.continue_()
        else:
            if len(self.blocked) < 25:
                self.blocked.append({'method': request.method,
                                     'path': urlsplit(request.url).path[:100],
                                     'reason': decision})
            route.abort()


def _read_page(page) -> dict:
    return page.evaluate('''() => {
       const app=window.__mcelSlh?.app;
       const notebook=document.querySelector('[data-mcel-operation-notebook="surface-node:slh.local-seh-lifecycle"]');
       return {mounted:!!app, target:document.body.dataset.mcelSlhTarget,
         selected:app?.readState('state:selected-target'),
         deployment:app?.readState('state:seh-deployment-status'),
         tab:notebook?.querySelector('[data-mcel-operation-tab="deployment"]')?.getAttribute('aria-selected'),
         modalVisible:!!document.querySelector('[data-mcel-lms-operation-modal]:not([hidden])'),
         modalText:document.querySelector('[data-mcel-lms-operation-modal]')?.textContent?.slice(0,10000)||''};
    }''')


def _safe_page_observation(page) -> dict:
    """Public identifiers and lifecycle state only; never export credentials."""
    try:
        state = _read_page(page)
        deployment = state.get('deployment')
        if not isinstance(deployment, dict):
            deployment = {}
        return {'mounted': bool(state.get('mounted')),
                'target': state.get('target'),
                'selected_target': state.get('selected'),
                'deployment_observed': bool(deployment),
                'application_uuid': deployment.get('applicationUuid'),
                'generation': deployment.get('generation'),
                'selected_controller': deployment.get('selectedController'),
                'deployment_phase': deployment.get('deploymentStatus'),
                'tab_selected': state.get('tab')}
    except Exception:
        return {'inspection': 'PAGE_UNAVAILABLE'}


def browser_open(page, baseline: dict) -> dict:
    """Read settled local Operations state before authorizing any click.

    The native SLH page restores its target asynchronously *after* identity
    discovery. A read dispatched immediately after mount may be refused before
    Operations authorization is established; that is not a UUID mismatch.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    page.goto(baseline['host'] + SLH_ROUTE, wait_until='domcontentloaded', timeout=20000)
    ensure(page.locator('#mcel-generated-data').count() == 1, 'NATIVE_SLH_PAGE_NOT_LOADED')
    served = json.loads(page.locator('#mcel-generated-data').text_content(timeout=5000))
    ir, _ = compile_slh_application()
    ensure(json_fingerprint(served.get('ir')) == json_fingerprint(ir), 'SERVED_SLH_IR_MISMATCH')
    page.wait_for_function('() => !!window.__mcelSlh?.app', timeout=15000)
    ensure(page.evaluate('document.body.dataset.mcelSlhTarget') == 'local',
           'WRONG_PAGE_TARGET')
    try:
        page.wait_for_function("() => window.__mcelSlh?.app?.readState('state:selected-target') === 'local'",
                               timeout=20000)
    except PlaywrightTimeoutError:
        raise MutationRefused('PAGE_LOCAL_TARGET_NOT_READY') from None
    # Selecting the tab is safe. It cannot arm the dangerous mutation gate.
    page.locator(NOTEBOOK + ' [data-mcel-operation-tab="deployment"]').click(timeout=10000)
    for attempt in range(4):
        response = page.evaluate("""async () => {
          const receipt = await window.__mcelSlh.app.execute('intent:read-seh-deployment');
          const deployment = window.__mcelSlh.app.readState('state:seh-deployment-status');
          return {disposition:receipt?.disposition ?? 'ABSENT', observed:deployment ?? null};
        }""")
        disposition = response.get('disposition')
        observed = response.get('observed')
        if disposition == 'completed' and isinstance(observed, dict):
            # A completed but conflicting observation is genuine evidence of
            # disagreement; never retry or overwrite it to get a passing result.
            ensure(observed.get('applicationUuid') == baseline['application_uuid'],
                   'PAGE_CANONICAL_UUID_MISMATCH')
            ensure(observed.get('generation') == baseline.get('generation'),
                   'PAGE_CANONICAL_GENERATION_MISMATCH')
            ensure(observed.get('selectedController') == baseline.get('selected_controller'),
                   'PAGE_SELECTED_CONTROLLER_MISMATCH')
            ensure(observed.get('deploymentStatus') == baseline['deployment_phase'],
                   'PAGE_DEPLOYMENT_PHASE_MISMATCH')
            state = _read_page(page)
            ensure(state['mounted'] and state['target'] == 'local' and
                   state['selected'] == 'local', 'WRONG_PAGE_TARGET')
            return {'application_uuid': observed['applicationUuid'],
                    'generation': observed['generation'],
                    'selected_controller': observed['selectedController'],
                    'deployment_phase': observed['deploymentStatus'],
                    'attempts': attempt + 1, 'read_disposition': 'completed'}
        # Retry only absent/incomplete reads, never a conflicting completed read.
        if attempt < 3:
            page.wait_for_timeout(500)
    raise MutationRefused('PAGE_DEPLOYMENT_READ_UNAVAILABLE')

def verify_seh_page_read_only(*, base: str, pre: dict) -> dict:
    """Check the real SLH page with a browser gate that cannot arm mutations."""
    from playwright.sync_api import sync_playwright
    evidence = {'status': 'INCOMPLETE', 'submitted_operations': []}
    with sync_playwright() as pw:
        browser, source = launch_semantic_browser(pw)
        evidence['browser'] = source
        try:
            context = browser.new_context(viewport={'width': 1280, 'height': 900},
                                          service_workers='block', accept_downloads=False)
            context.add_init_script(_ANONYMOUS_INIT_SCRIPT)
            gate = BrowserGate(base)  # never armed; all mutation requests denied
            context.route('**/*', gate.intercept)
            context.route_web_socket('**/*', lambda ws: ws.close())
            page = context.new_page()
            page.on('dialog', lambda dialog: dialog.dismiss())
            try:
                observed = browser_open(page, dict(pre, host=base))
                ensure(not gate.mutations and not gate.blocked,
                       'READ_ONLY_PAGE_REQUEST_GATE_VIOLATION')
                evidence['page_preflight'] = observed
                evidence['status'] = 'PASS_READ_ONLY_UI'
            except BaseException as exc:
                evidence['failed_gate'] = (str(exc) if isinstance(exc, MutationRefused)
                                           else type(exc).__name__)
                evidence['page_observation'] = _safe_page_observation(page)
                evidence['blocked_requests'] = gate.blocked
            finally:
                context.close()
        finally:
            browser.close()
    return evidence

def wait_receipt(page, operation_id: str, *, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last_reason = 'OPERATION_RECEIPT_NOT_SEEN'
    while time.monotonic() < deadline:
        try:
            receipt = execute_seh_deployment('operation-status', {'operationId': operation_id}, target='local')
            ensure(type(receipt) is dict and receipt.get('operation_id') == operation_id,
                   'LMS_RECEIPT_ID_MISMATCH')
            if receipt.get('phase') in ('RECORDED', 'CANCELED'):
                return receipt
            last_reason = 'LMS_OPERATION_STILL_PENDING'
        except MutationRefused:
            raise
        except (OSError, ValueError, RuntimeError):
            last_reason = 'LMS_RECEIPT_TEMPORARILY_UNAVAILABLE'
        page.wait_for_timeout(1000)
    raise MutationRefused(last_reason + '_TIMEOUT')


def safe_receipt_summary(receipt: dict) -> dict:
    """Allowlisted, bounded LMS outcome evidence. Never persist stream output or secrets."""
    ensure(type(receipt) is dict, 'LMS_RECEIPT_INVALID')
    result = receipt.get('result')
    result = result if type(result) is dict else {}
    causal = receipt.get('causal_diagnostic')
    causal = causal if type(causal) is dict else {}
    # Operation IDs and enum/code fields only. Drop everything else, including
    # free-form messages and journal stdout/stderr which may reveal credentials.
    fields = (
        ('operation_id', receipt.get('operation_id')),
        ('phase', receipt.get('phase')),
        ('stage', receipt.get('stage')),
        ('action', receipt.get('action')),
        ('result_status', result.get('status')),
        ('error_code', result.get('error_code')),
        ('error_type', result.get('error_type')),
        ('remote_mutation', result.get('remote_mutation')),
        ('causal_error_code', causal.get('error_code')),
    )
    return {key: value for key, value in fields
            if isinstance(value, str) and 0 < len(value) <= 128 and
            re.fullmatch(r'[A-Za-z0-9_.-]+', value)}


def reconcile_recorded_operation(*, source_report: Path,
                                 state_root: Path, local_port: int,
                                 receipt_lookup: Callable[[str, dict], dict] | None = None,
                                 inspect: Callable[[Path, int], dict] = _read_canonical,
                                 lms: Callable[[], dict] = read_lms_deployment) -> dict:
    """Read only. Reconcile exact previously submitted LMS operation and SEH state.

    No browser, operation submission, cancellation or retry is permitted here.
    A receipt is evidence of the harness result, not proof of live Coolify state.
    """
    report = {'schema': 'mcel.slh.dangerous-mutation-reconciliation.v1',
              'status': 'READ_ONLY_UNVERIFIED', 'mutation_submitted': False,
              'source_report': str(source_report)}
    try:
        previous = json.loads(source_report.read_text(encoding='utf-8'))
        ensure(type(previous) is dict and
               previous.get('schema') == 'mcel.slh.dangerous-mutation.v1' and
               previous.get('scope') in ('seh', 'all') and
               previous.get('mode') == 'live', 'PRIOR_LIVE_SEH_REPORT_REQUIRED')
        seh = (previous.get('groups') or {}).get('seh') or {}
        mutations = seh.get('submitted_operations')
        ensure(type(mutations) is list and mutations, 'NO_SUBMITTED_OPERATION_TO_RECONCILE')
        entry = mutations[-1]
        ensure(type(entry) is dict and entry.get('operation') in ('deploy', 'undeploy') and
               type(entry.get('operation_id')) is str and
               ID_PATTERN.fullmatch(entry['operation_id']) is not None,
               'INVALID_SUBMITTED_OPERATION_ID')
        operation_id = entry['operation_id']
        report['operation'] = entry['operation']
        report['operation_id'] = operation_id
        lookup = receipt_lookup or (lambda op, args: execute_seh_deployment(op, args, target='local'))
        receipt = lookup('operation-status', {'operationId': operation_id})
        ensure(type(receipt) is dict and receipt.get('operation_id') == operation_id,
               'LMS_RECEIPT_ID_MISMATCH')
        summary = safe_receipt_summary(receipt)
        report['lms_receipt'] = summary
        # No success language until independently verified against both sources.
        canonical = canonical_info(inspect(state_root, local_port))
        report['canonical'] = {'uuid': canonical.get('uuid'),
                               'generation': canonical.get('generation'),
                               'undeployed': canonical.get('undeployed')}
        live = lms()
        report['lms_deployment'] = {
            'application_uuid': live.get('applicationUuid'),
            'generation': live.get('generation'),
            'selected_controller': live.get('selectedController'),
            'deployment_phase': live.get('deploymentStatus'),
        }
        expected = 'SEH_UNDEPLOYED' if entry['operation'] == 'undeploy' else 'CONNECTED_VERIFIED'
        report['receipt_expected_status'] = expected
        report['receipt_matches_expected'] = (
            summary.get('phase') == 'RECORDED' and summary.get('result_status') == expected)
        report['authoritative_effect_verified'] = bool(
            report['receipt_matches_expected'] and
            ((entry['operation'] == 'undeploy' and
              canonical.get('undeployed') is True and
              live.get('deploymentStatus') in ('UNDEPLOYED', 'SEH_UNDEPLOYED')) or
             (entry['operation'] == 'deploy' and
              canonical.get('undeployed') is not True and
              live.get('applicationUuid') == canonical.get('uuid') and
              live.get('deploymentStatus') in
                  ('CONNECTED_VERIFIED', 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED'))))
        report['status'] = 'READ_ONLY_EVIDENCE_CAPTURED'
    except BaseException as exc:
        report['failed_gate'] = str(exc) if isinstance(exc, MutationRefused) else type(exc).__name__
    return report


def browser_action(page, gate: BrowserGate, operation: str, *, timeout: float) -> dict:
    label = 'Deploy / Redeploy' if operation == 'deploy' else 'Undeploy'
    ensure(not gate.armed, 'PREVIOUS_OPERATION_GATE_STILL_ARMED')
    button = page.locator(NOTEBOOK + ' [data-mcel-operation-action=' + json.dumps(label) + ']')
    ensure(button.count() == 1 and button.is_visible() and button.is_enabled(),
           'PAGE_ACTION_UNAVAILABLE_' + operation.upper())
    gate.arm(operation)
    start_count = len(gate.mutations)
    with page.expect_response(lambda r:
            urlsplit(r.url).path == '/api/applications/slh/seh/deployment' and
            r.request.method == 'POST' and
            json.loads(r.request.post_data or '{}').get('operation') == operation,
            timeout=min(int(timeout * 1000), 180000)) as response_info:
        button.click(timeout=15000)
        # The submission UI must appear immediately on click (not after LMS completes).
        ensure(page.locator('[data-mcel-lms-operation-modal]').is_visible(),
               'PAGE_OPERATION_MODAL_NOT_OPENED')
    response = response_info.value
    ensure(response.ok, 'PAGE_MUTATION_HTTP_REJECTED')
    value = response.json()
    ensure(type(value) is dict and value.get('ok') is True and
           type(value.get('result')) is dict, 'PAGE_MUTATION_ACK_INVALID')
    ack = value['result']
    ensure(len(gate.mutations) == start_count + 1 and gate.armed is None,
           'PAGE_MUTATION_DISPATCH_COUNT_INVALID')
    identifier = gate.mutations[-1]['operation_id']
    ensure(ack.get('operationId') == identifier and
           ack.get('outcome') in ('accepted-deferred','CONNECTED_VERIFIED','SEH_UNDEPLOYED'),
           'PAGE_LMS_ACK_ID_MISMATCH')
    receipt = wait_receipt(page, identifier, timeout=timeout)
    gate.last_receipt = safe_receipt_summary(receipt)
    expected = 'CONNECTED_VERIFIED' if operation == 'deploy' else 'SEH_UNDEPLOYED'
    ensure(receipt.get('phase') == 'RECORDED' and
           type(receipt.get('result')) is dict and
           receipt['result'].get('status') == expected,
           'PAGE_LMS_OUTCOME_UNVERIFIED_' + operation.upper())
    # The page must itself project the same durable outcome in its modal.
    page.wait_for_function('''expected => {
       const modal=document.querySelector('[data-mcel-lms-operation-modal]:not([hidden])');
       return !!modal && modal.textContent.includes(expected);
    }''', arg=expected, timeout=20000)
    ensure(not gate.blocked, 'UNEXPECTED_BROWSER_REQUEST_BLOCKED')
    result = {'operation': operation, 'operation_id': identifier,
              'phase': receipt['phase'], 'status': expected,
              'ui_modal_confirmed': True, 'page_http_confirmed': True}
    return result


def execute_browser_cycle(*, base: str, pre: dict, state_root: Path, local_port: int,
                          cycle_active: bool, timeout: float = 900.0,
                          read_canonical: Callable[[Path, int], dict] = _read_canonical,
                          checkpoint: Callable[[dict], None] | None = None) -> dict:
    from playwright.sync_api import sync_playwright
    steps = cycle_plan(pre, cycle_active=cycle_active)
    report = {'status': 'INCOMPLETE', 'steps': [], 'browser_requests': [],
              'initial_uuid': pre['application_uuid'], 'initial_state': pre['state']}
    expected_uuid = pre['application_uuid']
    expected_generation = pre.get('generation')
    expected_controller = pre.get('selected_controller')
    with sync_playwright() as pw:
        browser, source = launch_semantic_browser(pw)
        report['browser'] = source
        gate = BrowserGate(base)
        def mutation_checkpoint():
            report['submitted_operations'] = list(gate.mutations)
            if checkpoint:
                checkpoint(report)
        gate.on_mutation = mutation_checkpoint
        try:
            context = browser.new_context(viewport={'width': 1280, 'height': 900},
                                          service_workers='block', accept_downloads=False)
            context.add_init_script(_ANONYMOUS_INIT_SCRIPT)
            context.route('**/*', gate.intercept)
            context.route_web_socket('**/*', lambda ws: ws.close())
            page = context.new_page()
            page.on('dialog', lambda dialog:dialog.accept())
            errors = []
            page.on('pageerror', lambda error:errors.append(type(error).__name__))
            try:
                page_pre = dict(pre, host=base)
                page_read = browser_open(page, page_pre)
                if isinstance(page_read, dict):
                    report['page_preflight'] = page_read
                    if checkpoint:
                        checkpoint(report)
                for index, action in enumerate(steps):
                    # Re-read the authoritative record BEFORE EVERY destructive click.
                    current = read_canonical(state_root, local_port)
                    record = canonical_info(current)
                    ensure(record['uuid'] == expected_uuid, 'CANONICAL_TARGET_CHANGED_BEFORE_CLICK')
                    ensure(record['generation'] == expected_generation, 'CANONICAL_GENERATION_CHANGED_BEFORE_CLICK')
                    ensure(record['undeployed'] is True if action == 'deploy' else record['undeployed'] is not True,
                           'CANONICAL_STAGE_STATE_CHANGED')
                    live_now = read_lms_deployment()
                    ensure(live_now.get('applicationUuid') == expected_uuid and
                           live_now.get('generation') == expected_generation and
                           live_now.get('selectedController') == expected_controller,
                           'LMS_TARGET_CHANGED_BEFORE_CLICK')
                    op_state = execute_seh_deployment('operation-state', {}, target='local')
                    ensure(op_state.get('state') == 'IDLE' and op_state.get('busy') is False,
                           'LMS_OPERATION_BUSY_BEFORE_CLICK')
                    if index:
                        page.evaluate("async () => await window.__mcelSlh.app.execute('intent:read-seh-deployment')")
                        page.locator(NOTEBOOK + ' [data-mcel-operation-tab="deployment"]').click()
                    receipt = browser_action(page, gate, action, timeout=timeout)
                    # When the mutation is recorded, inspect again, not through the
                    # browser's potentially stale cached semantic projection.
                    updated = canonical_info(read_canonical(state_root, local_port))
                    if action == 'undeploy':
                        ensure(updated['undeployed'] is True, 'UNDEPLOY_TOMBSTONE_NOT_VERIFIED')
                    else:
                        ensure(updated['undeployed'] is not True, 'DEPLOY_STILL_TOMBSTONED')
                        direct = read_lms_deployment()
                        ensure(direct.get('applicationUuid') == updated['uuid'] and
                               direct.get('deploymentStatus') in (
                                   'CONNECTED_VERIFIED', 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED'),
                               'DEPLOY_LMS_RUNTIME_NOT_VERIFIED')
                    if action == 'undeploy':
                        ensure(updated['uuid'] == expected_uuid, 'UNDEPLOY_TARGET_CHANGED')
                        ensure(receipt['status'] == 'SEH_UNDEPLOYED', 'UNDEPLOY_NOT_CONFIRMED')
                    else:
                        ensure(type(updated['uuid']) is str and UUID_PATTERN.fullmatch(updated['uuid']) is not None,
                               'DEPLOYED_UUID_UNVERIFIED')
                        expected_uuid = updated['uuid']
                    # The displayed semantic state must reconcile independently
                    # of the modal. A completed receipt alone is insufficient.
                    projection = page.evaluate("async () => await window.__mcelSlh.app.execute('intent:read-seh-deployment')")
                    ensure(projection.get('disposition') == 'completed',
                           'PAGE_DEPLOYMENT_REFRESH_INCOMPLETE')
                    observed_ui = _read_page(page).get('deployment') or {}
                    ensure(observed_ui.get('applicationUuid') == updated['uuid'],
                           'PAGE_POST_MUTATION_UUID_NOT_RECONCILED')
                    ensure(observed_ui.get('deploymentStatus') == read_lms_deployment().get('deploymentStatus'),
                           'PAGE_POST_MUTATION_STATUS_NOT_RECONCILED')
                    receipt['observed_uuid'] = updated['uuid']
                    receipt['observed_undeployed'] = updated['undeployed']
                    expected_generation = updated['generation']
                    receipt['page_state_reconciled'] = True
                    report['steps'].append(receipt)
                    if checkpoint:
                        checkpoint(report)
                    if index + 1 < len(steps):
                        # The modal is operator-dismissed; do not navigate away
                        # until the durable receipt has been seen on the page.
                        dismiss = page.locator('[data-mcel-lms-operation-modal] button', has_text='Dismiss')
                        if dismiss.count() and dismiss.is_visible():
                            dismiss.click()
                ensure(not errors, 'PAGE_JAVASCRIPT_ERROR')
                ensure(not gate.blocked, 'BROWSER_REQUEST_OUTSIDE_EXPLICIT_ALLOWLIST')
                ensure([x['operation'] for x in gate.mutations] == steps,
                       'BROWSER_OPERATION_SEQUENCE_INVALID')
                report['status'] = 'PASS_LIVE_UI_MUTATIONS'
                report['final_uuid'] = expected_uuid
                report['final_undeployed'] = steps[-1] == 'undeploy'
                return report
            except BaseException as exc:
                report['failed_gate'] = str(exc) if isinstance(exc, MutationRefused) else type(exc).__name__
                report['submitted_operations'] = list(gate.mutations)
                report['blocked_requests'] = gate.blocked
                if gate.last_receipt is not None:
                    report['lms_receipt'] = gate.last_receipt
                report['page_observation'] = _safe_page_observation(page)
                if gate.mutations:
                    try:
                        report['post_failure_inspection'] = canonical_info(read_canonical(state_root,local_port))
                    except Exception:
                        report['post_failure_inspection'] = 'UNAVAILABLE'
                report['automatic_remote_cleanup'] = 'FORBIDDEN'
                if checkpoint:
                    checkpoint(report)
                raise
            finally:
                context.close()
        finally:
            browser.close()


class IdentityBrowserGate(BrowserGate):
    """One-use grant for an exact test-created row action. No bulk operations."""
    READS = frozenset(('read-pending','read-enrolled','read-trash','read-retention'))
    ACTIONS = frozenset(('accept','reject','delete','reactivate','remove'))

    def __init__(self, base: str, admin_device: str):
        super().__init__(base)
        self.admin_device = admin_device
        self.row_grant: tuple[str, str] | None = None
        self.row_mutations: list[dict] = []

    def arm_row(self, operation: str, record_id: str) -> None:
        ensure(operation in self.ACTIONS and self.row_grant is None and
               type(record_id) is str and bool(record_id), 'IDENTITY_ROW_GRANT_INVALID')
        self.row_grant = (operation, record_id)

    def classify(self, url: str, method: str, body_text: str | None) -> str:
        parts, host = urlsplit(url), urlsplit(self.base)
        if ((parts.scheme,parts.netloc) != (host.scheme,host.netloc) or
            parts.username or parts.password or parts.fragment):
            return 'DENY_ORIGIN'
        if method == 'GET' and parts.path == '/api/applications/slh/observe' and parts.query == 'view=workers':
            return 'ALLOW_READ'
        if method == 'POST' and not parts.query:
            try:
                body = json.loads(body_text or '')
            except (ValueError, TypeError):
                return 'DENY_BODY'
            if parts.path == '/api/applications/slh/seh/lifecycle':
                if body == {'target':'local','sessionTicket':'','operation':'status'}:
                    return 'ALLOW_CONNECTION_STATUS_READ'
                return 'DENY_CONNECTION_MUTATION'
            if parts.path == '/api/applications/slh/setup/action':
                if body == {'action': 'authenticate', 'arguments': {'adminDeviceId': self.admin_device}}:
                    return 'ALLOW_ADMIN_AUTH'
                if body == {'action': 'logout', 'arguments': {}}:
                    return 'ALLOW_AUTH_CLEANUP'
                return 'DENY_IDENTITY_AUTH'
            if parts.path == '/api/applications/slh/identity-notebook':
                if type(body) is not dict or set(body) != {'target','sessionTicket','operation','arguments'} or \
                        body.get('target') != 'local' or body.get('sessionTicket') != '' or \
                        type(body.get('arguments')) is not dict:
                    return 'DENY_IDENTITY_TARGET'
                operation, args = body['operation'], body['arguments']
                if operation in self.READS and args == {}:
                    return 'ALLOW_IDENTITY_READ'
                if operation in self.ACTIONS and set(args) == {'recordId'} and \
                        self.row_grant == (operation,args['recordId']):
                    self.row_grant = None
                    self.row_mutations.append({'operation':operation,'record_id':args['recordId']})
                    return 'ALLOW_IDENTITY_MUTATION'
                return 'DENY_IDENTITY_MUTATION'
        return super().classify(url,method,body_text)


def _identity_browser_state(page, admin_device: str) -> dict:
    """Non-sensitive, bounded UI readiness evidence. Never collect a device list or tokens."""
    return page.evaluate("""adminId => {
      const app=window.__mcelSlh?.app;
      const localIds=app?.readState('state:local-identities');
      const pending=app?.readState('state:pending-enrollments');
      const lifecycle=app?.readState('state:organization-lifecycle');
      const startup=window.__mcelSlh?.diagnostics?.()?.startup;
      const notebook=document.querySelector('[data-mcel-notebook]');
      return {mounted:!!app,
        target:app?.readState('state:selected-target')||null,
        identityDirectoryState:startup?.identityDirectory||null,
        identityRefresh:startup?.identityRefresh||null,
        identityStatus:startup?.identityStatus||null,
        lifecycleRefresh:startup?.lifecycleRefresh||null,
        organizationState:lifecycle?.state||null,
        identitiesLoaded:Array.isArray(localIds),
        adminInDirectory:Array.isArray(localIds)&&localIds.some(x=>x.deviceId===adminId),
        identityRailReady:!!document.querySelector('[data-select-key="'+CSS.escape(adminId)+'"]'),
        adminSelected:app?.readState('state:selected-local-identity')===adminId,
        notebookPresent:!!notebook,
        pendingTabSelected:notebook?.querySelector('[data-mcel-notebook-tab="pending"]')?.getAttribute('aria-selected')==='true',
        pendingLoaded:Array.isArray(pending),
        pendingCount:Array.isArray(pending)?pending.length:null,
        receiptSummaries:(app?.receipts?.()||[]).slice(-6).map(r=>({
          disposition:String(r.disposition||r.status||'unknown').slice(0,40),
          errorClass:/^[A-Z][A-Z0-9_]+(?::[A-Z][A-Z0-9_]+)?$/.test(String(r.reason||'')) ? r.reason : null})),
        notebookStatus:notebook?.querySelector('[data-mcel-notebook-status]')?.textContent?.trim()?.slice(0,120)||null,
        status:(document.getElementById('mcel-workbench-status')?.textContent||'').slice(0,100)
      };
    }""", admin_device)


def _identity_ui_readiness_gate(page, admin_device: str, *, timeout_ms: int = 18000) -> None:
    """Refuse BEFORE enrolling anything unless the native UI can see the signer."""
    page.wait_for_function("""adminId => {
      const app=window.__mcelSlh?.app;
      const ids=app?.readState('state:local-identities');
      const state=app?.readState('state:organization-lifecycle');
      const startup=window.__mcelSlh?.diagnostics?.()?.startup;
      return Array.isArray(ids) && ids.some(x=>x.deviceId===adminId) &&
        app?.readState('state:selected-target')==='local' &&
        startup?.identityDirectory==='AVAILABLE' && state?.state==='running' &&
        !!document.querySelector('[data-select-key="'+CSS.escape(adminId)+'"]');
    }""", arg=admin_device, timeout=timeout_ms)


def identity_ui_cycle(*, base: str, port: int, admin_device: str,
                      checkpoint: Callable[[dict], None] | None = None,
                      page_loader: Callable[[object], None] | None = None) -> dict:
    """Real HTTP enrollments, real MCEL clicks, authoritative role and cleanup checks.

    Generated private keys remain temporary and outside the running LMS
    Authenticator. Only generated devices/request IDs can be mutated. The UI is
    the sole path for approval/rejection/delete/reactivation/remove. HTTP
    cleanup is emergency-only and always verifies pre-existing inventories.
    """
    import secrets
    import tempfile
    from playwright.sync_api import sync_playwright
    from tools.mcel_slh_dangerous_functional_acceptance import (
        _open_lms_authenticated_session, _request, submit_identity)

    report = {'status':'INCOMPLETE', 'stage':'preparing', 'steps':[],
              'created':[], 'cleanup':'NOT_STARTED', 'cleanup_errors':[]}
    record = checkpoint or (lambda partial:None)
    token, admin = _open_lms_authenticated_session(port, admin_device)
    accepted = {}
    created = []
    gate = IdentityBrowserGate(base, admin_device)
    before_enrolled = before_trash = None
    try:
        ensure(admin.get('role') == 'admin' and admin.get('active') is True,
               'SELECTED_ADMIN_NOT_VERIFIED')
        admin_user = admin['user_id']
        before_enrolled = {d['id'] for d in _request(port,'GET','/v1/admin/devices',token=token)['devices']}
        before_trash = {d['id'] for d in _request(port,'GET','/v1/admin/devices/trash',token=token)['devices']}
        with tempfile.TemporaryDirectory(prefix='mcel-ui-mutation-keys-') as keys_dir:
            keys = Path(keys_dir)
            with sync_playwright() as pw:
                browser, origin = launch_semantic_browser(pw)
                report['browser'] = origin
                try:
                    context = browser.new_context(viewport={'width':1280,'height':900},
                                                  service_workers='block',accept_downloads=False)
                    context.add_init_script(_ANONYMOUS_INIT_SCRIPT)
                    context.route('**/*',gate.intercept)
                    context.route_web_socket('**/*',lambda ws:ws.close())
                    page = context.new_page()
                    page.on('dialog',lambda dialog:dialog.accept())
                    errors = []
                    page.on('pageerror',lambda e:errors.append(type(e).__name__))
                    try:
                        report['stage']='loading-native-slh-page'
                        record(report)
                        if page_loader is None:
                            page.goto(base+SLH_ROUTE,wait_until='domcontentloaded',timeout=20000)
                        else:
                            page_loader(page)
                        ensure(page.locator('#mcel-generated-data').count() == 1,'SLH_IDENTITY_PAGE_MISSING')
                        data=json.loads(page.locator('#mcel-generated-data').text_content())
                        expected_ir,_=compile_slh_application()
                        ensure(json_fingerprint(data['ir']) == json_fingerprint(expected_ir),
                               'SLH_IDENTITY_IR_MISMATCH')
                        page.wait_for_function('()=>!!window.__mcelSlh?.app',timeout=15000)
                        report['stage']='waiting-for-verified-ui-prerequisites'
                        record(report)
                        _identity_ui_readiness_gate(page,admin_device)
                        report['stage']='selecting-administrator-in-ui'
                        record(report)
                        page.locator('[data-select-key='+json.dumps(admin_device)+']').click(timeout=15000)
                        page.wait_for_function('''id=>window.__mcelSlh?.app.readState('state:selected-local-identity')===id''',
                                               arg=admin_device,timeout=10000)
                        report['stage']='opening-pending-notebook'
                        record(report)
                        area=page.locator('[data-mcel-notebook]')
                        area.locator('[data-mcel-notebook-tab="pending"]').click(timeout=10000)
                        report['stage']='creating-enrollments-after-ui-ready'
                        record(report)
                        page.wait_for_function("() => Array.isArray(window.__mcelSlh?.app.readState('state:pending-enrollments'))",timeout=15000)
                        for role in ('worker','admin','requester'):
                            ident = 'slhui-' + role + '-' + secrets.token_hex(8)
                            # Record the generated ID before creating its signed enrollment;
                            # cleanup must know it even if acknowledgement is lost.
                            entry = {'id':ident, 'role':role, 'request_id':None}
                            created.append(entry)
                            report['created'].append({'device_id':ident,'role':role})
                            record(report)
                            entry['request_id'] = submit_identity(port,keys,admin_user,ident,role)
                            record(report)
                        # Only a declared read intent is invoked programmatically.
                        # Every consequential identity action below still comes from a button click.
                        report['stage']='refreshing-pending-after-enrollment'
                        record(report)
                        refresh=page.evaluate("async()=>await window.__mcelSlh.app.execute('intent:read-organization-pending')")
                        ensure(refresh.get('disposition')=='completed', 'PENDING_REFRESH_AFTER_ENROLLMENT_FAILED')
                        for entry in created:
                            rid=entry['request_id']
                            report['stage']='waiting-for-pending-'+entry['role']
                            record(report)
                            row=area.locator('[data-record-key='+json.dumps(rid)+']')
                            row.wait_for(state='visible',timeout=20000)
                            op='reject' if entry['role']=='requester' else 'accept'
                            label='Reject' if op=='reject' else 'Accept'
                            report['stage']='clicking-'+op+'-'+entry['role']
                            record(report)
                            gate.arm_row(op,rid)
                            before=len(gate.row_mutations)
                            row.locator('[data-mcel-notebook-action='+json.dumps(label)+']').click(timeout=10000)
                            report['stage']='awaiting-'+op+'-settlement-'+entry['role']
                            record(report)
                            page.wait_for_function('''id=>!document.querySelector('[data-mcel-notebook-scroll] [data-record-key="'+id+'"]')''',
                                                   arg=rid,timeout=15000)
                            ensure(len(gate.row_mutations)==before+1 and gate.row_grant is None,
                                   'IDENTITY_PAGE_MUTATION_NOT_DISPATCHED')
                            if op=='reject':
                                state=_request(port,'GET','/v1/enrollment/requests/'+rid+'/status')
                                ensure(state.get('status')=='denied', 'PAGE_REJECT_NOT_DURABLE')
                            else:
                                devices=_request(port,'GET','/v1/admin/devices',token=token)['devices']
                                match=next((d for d in devices if d['id']==entry['id']),None)
                                ensure(match is not None and match.get('role')==entry['role'] and
                                       match.get('status')=='active','PAGE_APPROVAL_ROLE_NOT_DURABLE')
                                accepted[entry['id']]=entry['role']
                            report['steps'].append({'ui_operation':op,'device_id':entry['id'],
                                                    'role':entry['role'],'backend_verified':True})
                            record(report)
                        # Exercise Delete -> Reactivate -> Delete -> Remove on
                        # only the generated worker. The generated administrator
                        # is removed by the same page, without touching the root.
                        for entry in created[:2]:
                            did=entry['id']
                            flow=['delete','reactivate','delete','remove'] if entry['role']=='worker' else ['delete','remove']
                            for op in flow:
                                report['stage']='clicking-'+op+'-'+entry['role']
                                record(report)
                                tab='trash' if op in ('reactivate','remove') else 'enrolled'
                                area.locator('[data-mcel-notebook-tab='+json.dumps(tab)+']').click()
                                row=area.locator('[data-record-key='+json.dumps(did)+']')
                                row.wait_for(state='visible',timeout=20000)
                                gate.arm_row(op,did)
                                before=len(gate.row_mutations)
                                button=row.locator('[data-mcel-notebook-action='+json.dumps(op.title())+']')
                                button.click(timeout=10000)
                                page.wait_for_function('''id=>!document.querySelector('[data-mcel-notebook-scroll] [data-record-key="'+id+'"]')''',
                                                       arg=did,timeout=15000)
                                ensure(len(gate.row_mutations)==before+1 and gate.row_grant is None,
                                       'IDENTITY_ROW_OPERATION_NOT_DISPATCHED')
                                active={d['id']:d for d in _request(port,'GET','/v1/admin/devices',token=token)['devices']
                                        if d.get('status')=='active'}
                                trash={d['id']:d for d in _request(port,'GET','/v1/admin/devices/trash',token=token)['devices']}
                                if op=='delete':
                                    ensure(did not in active and did in trash,'PAGE_DELETE_NOT_VERIFIED')
                                elif op=='reactivate':
                                    ensure(did in active and did not in trash,'PAGE_REACTIVATE_NOT_VERIFIED')
                                else:
                                    ensure(did not in active and did not in trash,'PAGE_REMOVE_NOT_VERIFIED')
                                report['steps'].append({'ui_operation':op,'device_id':did,'backend_verified':True})
                                record(report)
                        ensure(not errors and not gate.blocked,'IDENTITY_PAGE_ERROR_OR_FORBIDDEN_REQUEST')
                        ensure(len(gate.row_mutations)==9, 'IDENTITY_UI_OPERATION_COUNT_MISMATCH')
                        report['stage']='ui-complete'
                    except BaseException:
                        try: report['ui_at_failure']=_identity_browser_state(page,admin_device)
                        except Exception: pass
                        report['blocked_requests']=list(gate.blocked)
                        report['page_errors']=list(errors)
                        report['submitted_ui_mutations']=[x['operation'] for x in gate.row_mutations]
                        raise
                    finally:
                        try:
                            # Only cleanup of the browser-owned temporary session.
                            page.evaluate("async()=>await window.__mcelSlh?.app.execute('intent:choose-identity',{key:''})")
                        except Exception:
                            pass
                        context.close()
                finally:
                    browser.close()
    except BaseException as exc:
        report['failed_gate']=(str(exc) if isinstance(exc,MutationRefused) else type(exc).__name__)
        if isinstance(exc,MutationRefused): report['reason']=str(exc)
        else: report['failure_diagnostic']=str(exc).splitlines()[0][:240]
        if gate.blocked: report['blocked_requests']=list(gate.blocked)
    finally:
        # Exactly generated IDs only. This fallback is explicitly recorded and
        # cannot be confused with a successful browser UI mutation.
        cleanup_errors=[]
        for entry in reversed(created):
            did=entry['id']
            try:
                active={d['id']:d for d in _request(port,'GET','/v1/admin/devices',token=token)['devices']}
                trash={d['id']:d for d in _request(port,'GET','/v1/admin/devices/trash',token=token)['devices']}
                if did in active or did in trash:
                    if did in active and active[did].get('status')=='active':
                        _request(port,'POST','/v1/admin/devices/'+did+'/revoke',{},token=token)
                    _request(port,'POST','/v1/admin/devices/'+did+'/remove',{'confirm':'REMOVE_DEVICE'},token=token)
                    report['steps'].append({'emergency_cleanup':did,'via':'authoritative-http'})
                elif did not in accepted:
                    # Even when enrollment POST lost its acknowledgement, the
                    # generated device ID may own a pending server-side request.
                    # Search ONLY for this generated ID and deny any orphan;
                    # never touch pre-existing enrollment requests.
                    rows=_request(port,'GET','/v1/admin/enrollments',token=token)['enrollments']
                    for pending in rows:
                        if pending.get('device_id')==did and pending.get('status')=='pending':
                            _request(port,'POST','/v1/admin/enrollments/'+pending['id']+'/deny',{},token=token)
                            report['steps'].append({'emergency_cleanup':did,
                                                    'via':'deny-orphaned-enrollment'})
            except Exception as exc:
                cleanup_errors.append({'id':did,'error':type(exc).__name__})
        try:
            enrolled={d['id'] for d in _request(port,'GET','/v1/admin/devices',token=token)['devices']}
            trash={d['id'] for d in _request(port,'GET','/v1/admin/devices/trash',token=token)['devices']}
            if before_enrolled is not None and (enrolled != before_enrolled or trash != before_trash):
                cleanup_errors.append({'error':'BASELINE_INVENTORY_CHANGED'})
            outstanding={item.get('device_id') for item in
                         _request(port,'GET','/v1/admin/enrollments',token=token)['enrollments']
                         if item.get('status')=='pending'}
            if any(item['id'] in outstanding for item in created):
                cleanup_errors.append({'error':'GENERATED_ENROLLMENT_REMAINS_PENDING'})
            _request(port,'POST','/v1/auth/logout',{},token=token)
        except Exception as exc:
            cleanup_errors.append({'error':'CLEANUP_VERIFICATION_FAILED_'+type(exc).__name__})
        report['cleanup_errors']=cleanup_errors
        report['cleanup']='VERIFIED' if not cleanup_errors else 'INCOMPLETE'
        report['status']=('PASS_LIVE_IDENTITY_UI_MUTATIONS' if report['stage']=='ui-complete'
                          and not cleanup_errors and
                          not any('emergency_cleanup' in s for s in report['steps'])
                          else 'INCOMPLETE')
        record(report)
    return report

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--mode', choices=('preflight', 'live', 'reconcile'), default='preflight')
    parser.add_argument('--scope', choices=('seh','identities','all'), default='all')
    parser.add_argument('--host-url', default='http://127.0.0.1:8765')
    parser.add_argument('--local-port', type=int, default=8897)
    parser.add_argument('--network', choices=('mainnet',), default='mainnet')
    parser.add_argument('--runtime-state-root', type=Path, default=ROOT / 'runtime/state')
    parser.add_argument('--expect-seh-uuid', help='Optional extra pin; normally discovered by internal inspect')
    parser.add_argument('--verify-page', action='store_true',
                        help='In preflight, verify actual SLH deployment UI without arming mutations')
    parser.add_argument('--cycle-active-seh', action='store_true',
                        help='Authorize an outage to undeploy and replace an existing SEH')
    parser.add_argument('--execute-mutations', action='store_true')
    parser.add_argument('--acknowledge-real-mutations', action='store_true')
    parser.add_argument('--timeout', type=float, default=900.0)
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT)
    parser.add_argument('--reconcile-report', type=Path, default=DEFAULT_RECONCILE_REPORT,
                        help='Read-only reconciliation output; does not replace prior live report')
    args = parser.parse_args(argv)
    if args.mode == 'live' and not (args.execute_mutations and args.acknowledge_real_mutations):
        parser.error('live mode requires BOTH --execute-mutations and --acknowledge-real-mutations')
    if args.mode != 'live' and (args.execute_mutations or args.acknowledge_real_mutations or args.cycle_active_seh):
        parser.error('destructive approval flags are valid only in live mode')
    if args.verify_page and (args.mode != 'preflight' or args.scope == 'identities'):
        parser.error('--verify-page requires preflight mode with SEH scope')
    if args.cycle_active_seh and args.scope == 'identities':
        parser.error('--cycle-active-seh requires SEH scope')
    if args.mode == 'reconcile' and (args.scope != 'seh' or args.verify_page):
        parser.error('reconcile requires --scope seh without --verify-page')
    if args.expect_seh_uuid and args.scope == 'identities':
        parser.error('--expect-seh-uuid requires SEH scope')
    if not 1 <= args.local_port <= 65535 or not 5 <= args.timeout <= 7200:
        parser.error('invalid port or timeout')
    base = validate_loopback_base(args.host_url)
    path = args.report if args.report.is_absolute() else ROOT / args.report
    path.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == 'reconcile':
        destination = (args.reconcile_report if args.reconcile_report.is_absolute()
                       else ROOT / args.reconcile_report)
        if destination.resolve() == path.resolve():
            parser.error('reconciliation output must not overwrite its source live report')
        result = reconcile_recorded_operation(source_report=path,
                      state_root=args.runtime_state_root, local_port=args.local_port)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n',
                               encoding='utf-8')
        print('SLH_DANGEROUS_MUTATION_RECONCILE', result['status'],
              result.get('lms_receipt', {}), 'report:', destination, flush=True)
        return 0 if result['status'] == 'READ_ONLY_EVIDENCE_CAPTURED' else 1
    output = {'schema': 'mcel.slh.dangerous-mutation.v1',
              'mode': args.mode, 'scope': args.scope, 'status': 'INCOMPLETE', 'host': base,
              'events': [], 'mutation_approval': args.mode == 'live',
              'safety': {'mutation_authority': 'UI_ONLY', 'blind_cleanup': 'FORBIDDEN'}}
    def save(_: dict | None = None) -> None:
        path.write_text(json.dumps(output, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    save()
    try:
        # Read-only preflights are integrated. No manual inspect command or
        # manually supplied administrator device is required.
        pre = None
        admin_device = None
        if args.scope in ('seh','all'):
            pre = preflight(state_root=args.runtime_state_root, local_port=args.local_port,
                            expect_uuid=args.expect_seh_uuid)
            output['preflight'] = pre
            save()
            # A valid UUID is not proof that a resident administrator can sign.
            # Verify the exact harness prerequisite before arming browser writes.
            admin_gate = verify_resident_admin(local_port=args.local_port,
                                               state_root=args.runtime_state_root)
            output['resident_admin_preflight'] = admin_gate
            save()
        if args.scope in ('identities','all'):
            from tools.mcel_slh_dangerous_functional_acceptance import discover_admin_device
            admin_device = discover_admin_device(port=args.local_port,source='lms')
            output['administrator_discovery'] = {'status':'VERIFIED','device_id':admin_device}
            save()
        if args.mode == 'preflight':
            if args.verify_page:
                evidence = verify_seh_page_read_only(base=base, pre=pre)
                output['page'] = evidence
                save()
                ensure(evidence['status'] == 'PASS_READ_ONLY_UI',
                       evidence.get('failed_gate','READ_ONLY_SEH_PAGE_NOT_VERIFIED'))
            output['status'] = 'READ_ONLY'
        else:
            # All destructive SEH gates must pass BEFORE even the identity
            # phase begins, so an ambiguous SEH cannot cause partial acceptance.
            if pre is not None:
                cycle_plan(pre, cycle_active=args.cycle_active_seh)
            output['groups'] = {}
            if admin_device is not None:
                def update_identity(partial):
                    output['groups']['identities'] = partial
                    save()
                identities = identity_ui_cycle(base=base,port=args.local_port,
                                               admin_device=admin_device,
                                               checkpoint=update_identity)
                output['groups']['identities'] = identities
                if identities['status'] != 'PASS_LIVE_IDENTITY_UI_MUTATIONS':
                    output['failed_stage'] = identities.get('stage','unknown')
                    output['identity_failed_gate'] = identities.get('failed_gate','UNKNOWN')
                save()
                ensure(identities['status'] == 'PASS_LIVE_IDENTITY_UI_MUTATIONS',
                       'LIVE_IDENTITY_UI_ACCEPTANCE_INCOMPLETE')
            if pre is not None:
                def update_seh(partial):
                    output['groups']['seh'] = partial
                    save()
                seh = execute_browser_cycle(base=base,pre=pre,
                         state_root=args.runtime_state_root,local_port=args.local_port,
                         cycle_active=args.cycle_active_seh,timeout=args.timeout,
                         checkpoint=update_seh)
                output['groups']['seh'] = seh
                save()
                ensure(seh['status']=='PASS_LIVE_UI_MUTATIONS',
                       'LIVE_SEH_UI_ACCEPTANCE_INCOMPLETE')
            output['status'] = 'PASS_LIVE_UI_MUTATIONS'
    except BaseException as exc:
        output['failed_gate'] = (str(exc) if isinstance(exc, MutationRefused) else
                                 type(exc).__name__)
    finally:
        save()
    if output.get('failed_stage'):
        print('SLH_DANGEROUS_MUTATION_IDENTITY_UI', output['failed_stage'],
              output.get('identity_failed_gate','UNKNOWN'), flush=True)
    print('SLH_DANGEROUS_MUTATION', output['status'],
          {k: v for k, v in output.get('preflight', {}).items()
           if k in ('application_uuid', 'state', 'deployment_phase')},
          'report:', path, flush=True)
    return 0 if output['status'] in ('READ_ONLY', 'PASS_LIVE_UI_MUTATIONS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
