#!/usr/bin/env python3
"""Operator-level SEH Coolify deployment harness.

Like mother_mutate_harness.py, fdb_mutate_harness.py, and hub_mutate_harness.py,
operators select a logical target and mutation intent. The harness orchestrates
existing SEH Control stages; it never manages Coolify API tokens itself.

Examples:
    python seh_mutate_harness.py list --network mainnet
    python seh_mutate_harness.py inspect
    python seh_mutate_harness.py diagnostic
    python seh_mutate_harness.py activate-seh --execute-mutations --yes-i-know-this-mutates-seh
    python seh_mutate_harness.py undeploy-seh  # preflight, no mutation
    # After disconnecting and settling work, execute the approved deletion:
    python seh_mutate_harness.py undeploy-seh --execute-mutations --yes-i-know-this-mutates-seh

    The canonical activation command is SLH-owned. It enables the existing
    deployment and the resident SLH outbound supervisor, and verifies the
    authenticated tunnel. Legacy name-based deployment commands remain for
    historical compatibility only; they do not complete activation.

    New SEHs use https://external.greatlibrary.io by default. An already
    registered SEH retains its verified public origin and Coolify identity.

A Coolify deploy trigger is QUEUED, not proof of public HTTPS/WSS readiness.
No mutation occurs without both approval switches.
"""
from __future__ import annotations

import argparse
from contextvars import ContextVar
import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from tools.seh_control.commands import SEHControl
from tools.seh_control.identity import SEHIdentityError, ident
from tools.seh_control.git_deployment import DEFAULT_PUBLIC_URL, SEHGitError


class HarnessError(ValueError):
    pass


class _OperatorProgress:
    """Live CLI-only progress on stderr; stdout remains a single JSON receipt.

    The heartbeat also covers slow calls inside Coolify and the resident SLH,
    where the harness cannot observe substeps until the call returns. Never
    format arbitrary exception text, configuration, credentials, or responses.
    """

    def __init__(self) -> None:
        self.started = time.monotonic()
        self._last_message = self.started
        self._stage = 'starting'
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._heartbeat, daemon=True,
                                        name='seh-harness-progress')

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)

    @staticmethod
    def _safe(value: object) -> str:
        # Status codes, UUIDs, stage names and counters only; never print raw
        # HTTP bodies, exception messages, endpoints or private configuration.
        if type(value) is bool:
            return str(value).lower()
        if type(value) is int and 0 <= value <= 10**20:
            return str(value)
        if type(value) is str and 0 < len(value) <= 80 and re.fullmatch(r'[A-Za-z0-9_.:-]+', value):
            return value
        return 'unverified'

    def emit(self, stage: str, state: str, **fields: object) -> None:
        safe_stage = self._safe(stage)
        safe_status = self._safe(state)
        suffix = ' '.join(f'{key}={self._safe(value)}' for key, value in fields.items()
                          if key in ('application_uuid', 'build_status', 'recovery_status',
                                     'coolify_status', 'phase', 'attempts', 'connected',
                                     'remote_mutation', 'status'))
        now = time.monotonic()
        with self._lock:
            self._stage = safe_stage
            self._last_message = now
            print(f'[SEH +{now - self.started:.1f}s] {safe_stage}: {safe_status}' +
                  (f' {suffix}' if suffix else ''), file=sys.stderr, flush=True)

    def _heartbeat(self) -> None:
        while not self._stop.wait(5):
            now = time.monotonic()
            with self._lock:
                if now - self._last_message < 10:
                    continue
                self._last_message = now
                print(f'[SEH +{now - self.started:.1f}s] {self._stage}: still_waiting',
                      file=sys.stderr, flush=True)


_ACTIVE_PROGRESS: ContextVar[_OperatorProgress | None] = ContextVar(
    'seh_harness_progress', default=None)


def _progress(stage: str, state: str, **fields: object) -> None:
    reporter = _ACTIVE_PROGRESS.get()
    if reporter is not None:
        reporter.emit(stage, state, **fields)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    p.add_argument('operation', choices=('list', 'inspect', 'migrate-state', 'reconcile-metadata', 'deploy-seh', 'bind-slh', 'diagnostic', 'activate-seh', 'verify-resident-admin', 'recover-runtime', 'redeploy-runtime', 'undeploy-seh'))
    p.add_argument('--approve-generation', type=int, help='Exact Mother generation, optional for activation')
    p.add_argument('--admin-device', help='Override automatic verified resident administrator selection')
    p.add_argument('--local-port', type=int, default=8897, help='Resident SLH loopback port')
    p.add_argument('--activation-timeout', type=float, default=45.0, help='Maximum seconds to verify resident connectivity')
    p.add_argument('--build-timeout', type=float, default=900.0,
                   help='Maximum seconds to verify the existing SEH build and public runtime')
    p.add_argument('--network', choices=('mainnet', 'testnet', 'test', 'dev'), default='mainnet')
    p.add_argument('--seh', help='Legacy SEH selection (mutation operations only; never inspect/diagnostic)')
    p.add_argument('--host', help='Verified Coolify controller; optional for an existing active SEH identity')
    p.add_argument('--slh-id', help='Optional SLH association metadata; not required for deployment')
    p.add_argument('--public-url', help='Public HTTPS origin; defaults for a new SEH only')
    p.add_argument('--project-name', default='My first project')
    p.add_argument('--environment-name', help='Defaults to <network>-hubs')
    p.add_argument('--runtime-state-root', type=Path, default=Path('runtime/state'))
    p.add_argument('--report', type=Path, help='Optional JSON receipt path')
    p.add_argument('--replace-existing-trust', action='store_true',
                   help='Explicitly authorize replacing the key already trusted by this SEH')
    p.add_argument('--slh-state-dir', type=Path,
                   help='Location of the existing local SLH signing key; never creates one')
    p.add_argument('--execute-mutations', action='store_true')
    p.add_argument('--yes-i-know-this-mutates-seh', action='store_true')
    return p


def _origin(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or
            parts.port or parts.path not in ('', '/') or parts.query or parts.fragment or
            parts.hostname.lower() in ('localhost', '127.0.0.1')):
        raise HarnessError('--public-url must be a public hostname-only HTTPS origin')
    return value.rstrip('/')


def _existing(control: SEHControl, network: str, seh: str) -> dict | None:
    result = control.list(network)
    rows = result.get('sehs')
    if not isinstance(rows, list):
        raise HarnessError('SEH registry did not return a valid identity list')
    return next((row for row in rows if row.get('seh') == seh), None)


def _single_read_only_target(control: SEHControl, network: str) -> tuple[dict | None, dict]:
    """Resolve zero or one *legacy* SEH from a verified, read-only Mother view.

    Do not choose a name, pick the first item, read keys, or mutate private
    state. The named registry is only a migration input until Patch 2.
    """
    listing = control.list(network)
    if listing.get('status') == 'READ_ONLY' and 'deployment' in listing:
        canonical = listing['deployment']
        if not isinstance(canonical, dict):
            raise HarnessError('Invalid canonical deployment')
        return canonical, listing
    rows = listing.get('sehs')
    if listing.get('status') != 'READ_ONLY' or not isinstance(rows, list):
        raise HarnessError('Verified SEH registry inspection was unavailable')
    if len(rows) > 1:
        raise HarnessError('Multiple legacy SEH records exist; refuse automatic selection until reconciled')
    if rows and (not isinstance(rows[0], dict) or not isinstance(rows[0].get('seh'), str)):
        raise HarnessError('Legacy SEH registration is malformed; refusing selection')
    return (rows[0] if rows else None), listing


def _record_step(events: list[dict], stage: str, status: str, detail: dict | None = None) -> None:
    item = {'stage': stage, 'status': status}
    if detail:
        # Only selected public evidence is allowed in the operator receipt.
        for key in ('generation', 'plan_digest', 'application_uuid', 'public_url', 'coolify_status',
                    'dns_ready', 'ready', 'remote_mutation', 'created', 'git_revision'):
            if key in detail:
                item[key] = detail[key]
    events.append(item)
    _progress(stage, status, application_uuid=item.get('application_uuid'))



def _resident_failure_diagnostic(observed: dict) -> dict:
    """Bounded nonsecret readback. No URL, credential or exception text."""
    codes = {
        'SEH_LOCAL_CONNECTION_DISABLED', 'SEH_CANONICAL_DEPLOYMENT_DISABLED',
        'SEH_LOCAL_SIGNING_IDENTITY_CHANGED', 'SEH_RESOLVED_CONNECTION_DISABLED',
        'SEH_LOCAL_CANONICAL_BINDING_FAILED', 'SEH_CONFIGURATION_RESOLUTION_FAILED',
        'SEH_OUTBOUND_AUTH_REJECTED', 'SEH_OUTBOUND_ENDPOINT_NOT_FOUND',
        'SEH_OUTBOUND_SERVICE_UNAVAILABLE', 'SEH_OUTBOUND_CONNECT_FAILED',
        'SEH_OUTBOUND_SUPERVISOR_FAILED',
    }
    stages = {'RESOLVING_CONFIGURATION', 'CONNECTING_OUTBOUND_WSS',
              'VERIFYING_LOCAL_BINDING'}
    code = observed.get('resident_outbound_failure_code')
    stage = observed.get('resident_outbound_failure_stage')
    result = {'schema': 'main-computer.seh-causal-diagnostic.v1',
              'source': 'resident-outbound-supervisor',
              'error_code': (code if type(code) is str and code in codes
                             else 'SEH_OUTBOUND_OUTCOME_UNVERIFIED'),
              'failed_stage': (stage if type(stage) is str and stage in stages
                               else 'RESIDENT_OUTBOUND_VERIFICATION'),
              'resident_child_observed': observed.get('resident_child_observed') is True}
    for key, source in (('attempts', 'resident_outbound_attempts'),
                        ('failures', 'resident_outbound_failures'),
                        ('failure_attempt', 'resident_outbound_failure_attempt'),
                        ('failure_unix_ns', 'resident_outbound_failure_unix_ns')):
        value = observed.get(source)
        if type(value) is int and 0 <= value <= 10**20:
            result[key] = value
    status = observed.get('resident_outbound_last_http_status')
    if type(status) is int and 100 <= status <= 599:
        result['http_status'] = status
    cls = observed.get('resident_outbound_last_error_class')
    if type(cls) is str and cls in ('ValueError', 'TimeoutError', 'ConnectionError', 'OSError',
               'SSLError', 'InvalidStatus', 'ConnectionClosedError',
               'InvalidHandshake', 'RuntimeError', 'KeyError', 'TypeError'):
        result['exception_class'] = cls
    return result


def _verified_resident_administrator(store, explicit_device: str | None, request) -> tuple[str, str]:
    """Return a signed, current resident administrator session, or refuse.

    An ordinary local key is not evidence of administrator authority. Verify
    every candidate against the resident ORG-SLH before selecting one. The
    designated local default wins only if it is itself a verified admin; if
    there is no eligible default, require exactly one verified administrator.
    All unselected bearer sessions are logged out, even on failure.
    """
    inventory = store.list()
    identities = inventory.get('identities')
    if (type(identities) is not list or not identities or len(identities) > 128
            or type(inventory.get('invalid_identities')) is not list
            or inventory['invalid_identities']):
        raise HarnessError('Resident administrator identity inventory unavailable or invalid')
    ids = [item.get('device_id') for item in identities if type(item) is dict]
    if (len(ids) != len(identities) or any(type(i) is not str or not i for i in ids)
            or len(set(ids)) != len(ids)):
        raise HarnessError('Resident administrator identity inventory invalid')
    default = inventory.get('default_device_id')
    if explicit_device:
        if explicit_device not in ids:
            raise HarnessError('Requested administrator is absent from resident identity store')
        candidates = [explicit_device]
    else:
        candidates = sorted(ids, key=lambda i: (i != default, i))

    approved: list[tuple[str, str]] = []
    try:
        for device in candidates:
            token = None
            try:
                try:
                    challenge = request('POST', '/v1/auth/challenge', {'device_id': device})
                except HarnessError as exc:
                    # A locally held key need not be enrolled in this organization.
                    # A transport failure or unexpected refusal must never be
                    # treated as a negative administrator check.
                    if not explicit_device and 'UNKNOWN_OR_REVOKED_DEVICE' in str(exc):
                        continue
                    raise
                if type(challenge) is not dict or not challenge.get('challenge_id'):
                    raise HarnessError('Resident administrator challenge unverified')
                signed = store.sign(device, challenge)
                verified = request('POST', '/v1/auth/verify', {
                    'device_id': device, 'challenge_id': challenge['challenge_id'],
                    'signature': signed['signature'],
                })
                token = verified.get('access_token') if type(verified) is dict else None
                if type(token) is not str or not token:
                    raise HarnessError('Resident administrator authentication unverified')
                session = request('GET', '/v1/auth/session', token=token)
                if (type(session) is not dict or session.get('device_id') != device
                        or session.get('active') is not True
                        or type(session.get('user_id')) is not str
                        or not session['user_id']):
                    raise HarnessError('Resident administrator session unverified')
                if session.get('role') == 'admin':
                    approved.append((device, token))
                    token = None  # Ownership transferred to approved list.
                    if not explicit_device and device == default:
                        return approved.pop()
            finally:
                if token:
                    logout = request('POST', '/v1/auth/logout', {}, token=token)
                    if type(logout) is not dict or logout.get('status') != 'LOGGED_OUT':
                        raise HarnessError('Resident administrator discovery logout unverified')
        if len(approved) != 1:
            raise HarnessError(
                'No verified resident administrator identity' if not approved
                else 'Multiple verified administrators without an eligible default; pass --admin-device')
        return approved.pop()
    finally:
        for _, bearer in approved:
            logout = request('POST', '/v1/auth/logout', {}, token=bearer)
            if type(logout) is not dict or logout.get('status') != 'LOGGED_OUT':
                raise HarnessError('Resident administrator discovery logout unverified')


def _activate_via_resident_slh(args: argparse.Namespace, *, state_dir_override=None) -> dict:
    """One signed-admin invocation; only the resident SLH mutates its service.

    Authenticates to the resident SLH and never handles its signing key,
    Mother writes, Coolify mutations, or a standalone connector directly.
    """
    from http.client import HTTPConnection
    import time
    from main_computer.slh.authenticator_store import LocalIdentityStore, IdentityStoreError
    from main_computer.slh.storage import slh_state_dir
    from main_computer.slh.ports import DEFAULT_SLH_API_HOST

    if any((args.seh, args.host, args.slh_id, args.public_url,
            args.slh_state_dir, args.replace_existing_trust)):
        raise HarnessError('SEH activation uses the sole canonical SLH deployment; no target or key options')
    if args.network != 'mainnet':
        raise HarnessError('Resident SEH activation currently supports the verified mainnet deployment only')
    if args.execute_mutations != args.yes_i_know_this_mutates_seh:
        raise HarnessError('Both mutation approvals are required together')
    if not 1 <= args.local_port <= 65535 or not 0 < args.activation_timeout <= 180:
        raise HarnessError('Invalid local port or activation timeout')

    # Activation signs with the resident LMS identity, not the per-user AppData store.
    store = LocalIdentityStore((Path(state_dir_override) if state_dir_override is not None
                                else slh_state_dir()) / "identities")
    # This request uses the pinned loopback address, no proxy or redirects.
    # API tokens only exist in memory for this command and are logged out.
    def request(method, path, body=None, token=None):
        payload = None if body is None else json.dumps(body).encode('utf-8')
        headers = {'Content-Type': 'application/json',
                   'Host': f'{DEFAULT_SLH_API_HOST}:{args.local_port}'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        connection = HTTPConnection(DEFAULT_SLH_API_HOST, args.local_port, timeout=40)
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            # Read the complete response up to an explicit limit. The former
            # read(16384) silently truncated longer JSON receipts and made a
            # valid response indistinguishable from a server-side exception.
            raw = response.read(65537)
            content_type = (response.getheader('Content-Type', '')
                            if hasattr(response, 'getheader') else '')
            if 300 <= response.status < 400:
                raise HarnessError('SLH local service redirected the request; refusing')
            is_mutating_activation = (method == 'POST' and path == '/v1/local/seh/activate'
                                      and isinstance(body, dict) and body.get('execute') is True)
            if len(raw) > 65536:
                if is_mutating_activation:
                    return {'status': 'INCOMPLETE', 'failed_stage': 'resident_response_oversized',
                            'resident_http_status': response.status, 'remote_mutation': 'UNKNOWN'}
                raise HarnessError('SLH local service response exceeds 65536 bytes'
                                   f' (HTTP {response.status})')
            try:
                result = json.loads(raw)
            except (UnicodeDecodeError, ValueError):
                if is_mutating_activation:
                    return {'status': 'INCOMPLETE', 'failed_stage': 'resident_non_json_response',
                            'resident_http_status': response.status,
                            'resident_response_kind': ('HTML' if 'html' in content_type.lower()
                                                       else 'NON_JSON'),
                            'remote_mutation': 'UNKNOWN'}
                raise HarnessError('SLH local service returned non-JSON response'
                                   f' (HTTP {response.status})') from None
            if not isinstance(result, dict):
                if is_mutating_activation:
                    return {'status': 'INCOMPLETE', 'failed_stage': 'resident_invalid_response',
                            'resident_http_status': response.status,
                            'remote_mutation': 'UNKNOWN'}
                raise HarnessError('SLH local service returned an invalid response')
            if response.status >= 400:
                # A failed mutating request may already have created a Coolify
                # application. Preserve the server's structured uncertainty;
                # never turn it into a preflight refusal inviting blind retry.
                if (is_mutating_activation and result.get('status') == 'INCOMPLETE'
                        and isinstance(result.get('failed_stage'), str)):
                    return {**result, 'resident_http_status': response.status}
                error = result.get('error') or {}
                code = error.get('code') if isinstance(error, dict) else None
                raise HarnessError('Resident SLH refused activation: ' +
                                   (code if isinstance(code, str) else f'HTTP_{response.status}'))
            return result
        except OSError as exc:
            raise HarnessError('Resident SLH localhost service is unavailable: ' + type(exc).__name__) from None
        finally:
            connection.close()

    token = None
    try:
        _progress('resident.auth', 'verifying_administrator')
        device, token = _verified_resident_administrator(store, args.admin_device, request)
        _progress('resident.activation', 'requesting' if args.execute_mutations else 'preflight')
        result = request('POST', '/v1/local/seh/activate', {
            'execute': bool(args.execute_mutations),
            'approve_generation': args.approve_generation}, token=token)
        if result.get('status') != 'ACTIVATION_STARTED':
            _progress('resident.activation', 'returned', status=result.get('status'))
            return result
        _progress('resident.connection', 'awaiting_authenticated_tunnel')
        last_connection_observation = None
        last_connection_progress = 0.0
        deadline = time.monotonic() + args.activation_timeout
        runtime_checked = result.get('remote_recovery') == 'REPLACEMENT_QUEUED_NOT_VERIFIED'
        runtime_recovery_evidence = None
        while True:
            try:
                observed = request('GET', '/v1/local/seh/status', token=token)
            except HarnessError:
                # Mother may already be committed. A lost local readback is
                # incomplete activation, never a clean preflight refusal.
                return {**result, 'status': 'INCOMPLETE',
                        'failed_stage': 'resident_status_unavailable',
                        'outbound_tunnel_authenticated': False}
            connection_observation = (
                observed.get('status'), observed.get('resident_outbound_phase'),
                observed.get('resident_outbound_attempts'))
            if (connection_observation != last_connection_observation or
                    time.monotonic() - last_connection_progress >= 10):
                _progress('resident.connection', 'observed',
                          status=observed.get('status'),
                          phase=observed.get('resident_outbound_phase'),
                          attempts=observed.get('resident_outbound_attempts'))
                last_connection_observation = connection_observation
                last_connection_progress = time.monotonic()
            if (observed.get('status') == 'CONNECTED_VERIFIED' and
                    observed.get('outbound_tunnel_authenticated') is True and
                    observed.get('private_requests_available') is True and
                    observed.get('pinky_sweeper_available') is True):
                _progress('resident.connection', 'verified', connected=True)
                return {**result, 'status': 'CONNECTED_VERIFIED',
                        'outbound_tunnel_authenticated': True,
                        'private_requests_available': True,
                        'pinky_sweeper_available': True,
                        'pinky_sweeper_running': observed.get('pinky_sweeper_running', False),
                        'pinky_sweep_last_attempt_ns': observed.get('pinky_sweep_last_attempt_ns'),
                        'pinky_sweep_last_error': observed.get('pinky_sweep_last_error'),
                        'pinky_sweep_last_confirmed': observed.get('pinky_sweep_last_confirmed', False)}
            # The signed administrator has already authorized activation.
            # Check missing remote runtime once; when Docker was deliberately
            # removed, recover the same verified Coolify UUID, never create an
            # SEH or change its signing-key trust. A queued rebuild is NOT a
            # verified resident connection.
            if not runtime_checked and args.execute_mutations:
                runtime_checked = True
                _progress('resident.runtime_recovery', 'checking')
                from tools.seh_control.git_deployment import recover_canonical_runtime
                try:
                    runtime_recovery_evidence = recover_canonical_runtime(
                        args.runtime_state_root, args.network, execute=True)
                except (SEHGitError, OSError, ValueError) as exc:
                    # The resident supervisor may still connect independently;
                    # do not replace its real WSS outcome with a guess from the
                    # Coolify management plane.
                    runtime_recovery_evidence = {
                        'status': 'PREFLIGHT_UNVERIFIED',
                        'error_class': type(exc).__name__}
                if runtime_recovery_evidence.get('status') in (
                        'REPLACEMENT_QUEUED_NOT_VERIFIED', 'DEPLOYMENT_IN_PROGRESS',
                        'INCOMPLETE'):
                    return {**result, 'status': 'INCOMPLETE',
                            'failed_stage': 'coolify_runtime_recovery',
                            'remote_recovery': runtime_recovery_evidence.get('status'),
                            'runtime_observation': runtime_recovery_evidence.get('runtime_observation'),
                            'remote_mutation': runtime_recovery_evidence.get('remote_mutation', False),
                            'outbound_tunnel_authenticated': False}
            if time.monotonic() >= deadline:
                _progress('resident.connection', 'timeout')
                diagnostic = _resident_failure_diagnostic(observed)
                return {**result, 'status': 'INCOMPLETE',
                        'failed_stage': 'resident_outbound_wss_verification',
                        'outbound_tunnel_authenticated': False,
                        'resident_status': observed.get('status', 'UNAVAILABLE'),
                        'resident_activation_error': observed.get('resident_activation_error'),
                        'resident_child_observed': observed.get('resident_child_observed'),
                        'resident_outbound_phase': observed.get('resident_outbound_phase'),
                        'resident_outbound_attempts': observed.get('resident_outbound_attempts'),
                        'resident_outbound_connections': observed.get('resident_outbound_connections'),
                        'resident_outbound_failures': observed.get('resident_outbound_failures'),
                        'resident_outbound_last_error_class': observed.get('resident_outbound_last_error_class'),
                        'resident_outbound_last_http_status': observed.get('resident_outbound_last_http_status'),
                        'resident_outbound_failure_code': diagnostic['error_code'],
                        'resident_outbound_failure_stage': diagnostic['failed_stage'],
                        'causal_diagnostic': diagnostic,
                        'runtime_recovery_status': (runtime_recovery_evidence or {}).get('status'),
                        'runtime_recovery_error_class': (runtime_recovery_evidence or {}).get('error_class')}
            time.sleep(0.3)
    finally:
        if token:
            try:
                request('POST', '/v1/auth/logout', {}, token=token)
            except HarnessError:
                pass


def _disconnect_via_resident_slh(args: argparse.Namespace, *, state_dir: Path,
                                 verify_only: bool = False) -> dict:
    """Signed local administrator Stop; never edit the connection bit directly.

    Only the resident SLH may authorize the persistent disconnect. The public
    relay's disconnected readback is independently required before deletion.
    """
    from http.client import HTTPConnection
    from main_computer.slh.authenticator_store import LocalIdentityStore
    from main_computer.slh.ports import DEFAULT_SLH_API_HOST

    identities = LocalIdentityStore(state_dir / 'identities')

    def request(method, path, body=None, token=None):
        conn = HTTPConnection(DEFAULT_SLH_API_HOST, args.local_port, timeout=40)
        try:
            headers = {'Content-Type': 'application/json',
                       'Host': f'{DEFAULT_SLH_API_HOST}:{args.local_port}'}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            conn.request(method, path, body=json.dumps(body).encode('utf-8') if body is not None else None,
                         headers=headers)
            response = conn.getresponse()
            raw = response.read(65537)
            if len(raw) > 65536 or response.status >= 300 and response.status < 400:
                raise HarnessError('Resident SLH disconnect response could not be verified')
            try:
                result = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise HarnessError('Resident SLH disconnect returned non-JSON response') from None
            if not isinstance(result, dict):
                raise HarnessError('Resident SLH disconnect returned invalid response')
            if response.status >= 400:
                error = result.get('error')
                code = error.get('code') if isinstance(error, dict) else None
                raise HarnessError('Resident SLH refused disconnect: ' +
                                   (code if isinstance(code, str) else f'HTTP_{response.status}'))
            return result
        except OSError as exc:
            raise HarnessError('Resident SLH unavailable for disconnect: ' +
                               type(exc).__name__) from None
        finally:
            conn.close()

    token = None
    try:
        _progress('resident.disconnect', 'verifying_administrator')
        device, token = _verified_resident_administrator(identities, args.admin_device, request)
        if verify_only:
            logout = request('POST', '/v1/auth/logout', {}, token=token)
            if type(logout) is not dict or logout.get('status') != 'LOGGED_OUT':
                raise HarnessError('Resident administrator preflight logout unverified')
            token = None
            return {'status': 'VERIFIED_ADMIN_SESSION', 'device_id': device,
                    'remote_mutation': False, 'session_closed': True}
        _progress('resident.disconnect', 'requesting')
        result = request('POST', '/v1/local/seh/disconnect', {}, token=token)
        if result.get('status') != 'DISCONNECTION_REQUESTED' or result.get('connection_enabled') is not False:
            raise HarnessError('Resident SLH did not acknowledge disconnection')
        _progress('resident.disconnect', 'acknowledged')
        return result
    finally:
        if token:
            try:
                request('POST', '/v1/auth/logout', {}, token=token)
            except HarnessError:
                pass


def _wait_for_verified_build(args: argparse.Namespace, control: SEHControl,
                             state_dir: Path, application_uuid: str,
                             initial: dict) -> dict:
    """Poll one receipt-bound UUID; never submit another build on timeout."""
    import time
    from tools.seh_control.git_deployment import reconcile_pending_recovery
    deadline = time.monotonic() + args.build_timeout
    last = initial
    last_progress = None
    last_progress_at = 0.0
    _progress('build.verify', 'polling', application_uuid=application_uuid)
    while True:
        observed = reconcile_pending_recovery(control.root, args.network,
                                              state_dir=state_dir, execute=False)
        if observed is None:
            return {'status': 'INCOMPLETE', 'failed_stage': 'missing_recovery_receipt',
                    'application_uuid': application_uuid, 'remote_mutation': initial.get('remote_mutation', False)}
        if observed.get('application_uuid') not in (None, application_uuid):
            raise HarnessError('Recovery UUID changed while waiting for build')
        last = observed
        state = (observed.get('status'), observed.get('build_status'))
        if state != last_progress or time.monotonic() - last_progress_at >= 10:
            _progress('build.verify', 'observed', status=state[0], build_status=state[1])
            last_progress, last_progress_at = state, time.monotonic()
        if observed.get('status') == 'REPLACEMENT_BUILD_VERIFIED':
            _progress('build.verify', 'verified', application_uuid=application_uuid)
            return observed
        if observed.get('status') != 'REPLACEMENT_QUEUED_NOT_VERIFIED':
            return {'status': 'INCOMPLETE', 'failed_stage': 'recovery_build_unverified',
                    'application_uuid': application_uuid, 'recovery_status': observed.get('status'),
                    'recovery_failed_stage': observed.get('failed_stage'),
                    'remote_mutation': initial.get('remote_mutation', False)}
        if time.monotonic() >= deadline:
            _progress('build.verify', 'timeout', application_uuid=application_uuid)
            return {'status': 'INCOMPLETE', 'failed_stage': 'recovery_build_wait_timeout',
                    'application_uuid': application_uuid,
                    'build_status': last.get('build_status'),
                    'remote_mutation': initial.get('remote_mutation', False)}
        time.sleep(min(2.0, max(0.01, deadline - time.monotonic())))


def _wait_for_runtime(args: argparse.Namespace, control: SEHControl, state_dir: Path,
                      application_uuid: str, deadline: float, initial: dict) -> dict | None:
    """Require exact Coolify UUID health and canonical public health, no changes."""
    import time
    from tools.seh_control.git_deployment import (
        _canonical_selection, _registered_application, _client,
        _public_runtime_probe, DEFAULT_PUBLIC_URL)
    last_status = None
    last_runtime_progress = None
    last_runtime_at = 0.0
    _progress('runtime.verify', 'polling', application_uuid=application_uuid)
    while True:
        record, controller, _, _ = _canonical_selection(control.root, args.network)
        if record['deployment']['application_uuid'] != application_uuid or record['deployment'].get('undeployed'):
            raise HarnessError('Canonical deployment changed while verifying runtime')
        app = _registered_application(_client(controller), application_uuid)
        if app is None:
            return {'status': 'INCOMPLETE', 'failed_stage': 'registered_application_disappeared',
                    'application_uuid': application_uuid,
                    'remote_mutation': initial.get('remote_mutation', False)}
        last_status = str(app.get('status') or '')
        if last_status != last_runtime_progress or time.monotonic() - last_runtime_at >= 10:
            _progress('runtime.verify', 'observed', coolify_status=last_status)
            last_runtime_progress, last_runtime_at = last_status, time.monotonic()
        if any(flag in last_status.lower() for flag in ('exited', 'dead', 'failed')):
            return {'status': 'INCOMPLETE', 'failed_stage': 'replacement_runtime_terminal_failure',
                    'application_uuid': application_uuid, 'coolify_status': last_status,
                    'remote_mutation': initial.get('remote_mutation', False)}
        if 'running' in last_status.lower() and 'healthy' in last_status.lower():
            try:
                public = _public_runtime_probe(DEFAULT_PUBLIC_URL)
            except (OSError, ValueError, RuntimeError, SEHGitError):
                public = {'status': 'PUBLIC_RUNTIME_UNVERIFIED'}
            if isinstance(public, dict) and public.get('status') == 'PUBLIC_RUNTIME_PRESENT':
                _progress('runtime.verify', 'public_runtime_present')
                return None
        if time.monotonic() >= deadline:
            _progress('runtime.verify', 'timeout')
            return {'status': 'INCOMPLETE', 'failed_stage': 'replacement_runtime_wait_timeout',
                    'application_uuid': application_uuid, 'coolify_status': last_status,
                    'remote_mutation': initial.get('remote_mutation', False)}
        time.sleep(min(2.0, max(0.01, deadline - time.monotonic())))


def _canonical_deploy(args: argparse.Namespace, control: SEHControl) -> dict:
    """One operator action: restore once, wait for readiness, activate and verify."""
    import time
    from tools.seh_control import deployment_state as ds
    from tools.seh_control import identity as identity_state
    from tools.seh_control.git_deployment import (
        restore_deleted_canonical_application, _undeploy_receipt,
        reconcile_pending_recovery, _canonical_selection)
    from main_computer.slh.storage import slh_state_dir
    from main_computer.slh.organization_state import read_config
    if any((args.seh, args.host, args.slh_id, args.public_url, args.replace_existing_trust,
            args.approve_generation)):
        raise HarnessError('Canonical SEH deploy accepts no target or signing overrides')
    if args.execute_mutations != args.yes_i_know_this_mutates_seh:
        raise HarnessError('Both mutation approvals are required together')
    if args.network != 'mainnet':
        raise HarnessError('Canonical SEH deployment supports verified mainnet only')
    if not 1 <= args.local_port <= 65535 or not 0 < args.activation_timeout <= 180 or not 0 < args.build_timeout <= 3600:
        raise HarnessError('Invalid local port or readiness timeout')
    state_dir = Path(args.slh_state_dir or slh_state_dir())
    _progress('deploy.identity', 'checking_canonical_record')
    _, _, document = identity_state.read(control.root, args.network)
    identity = identity_state.deployment_record(document, args.network)
    if identity is None or not identity['deployment'].get('application_uuid'):
        raise HarnessError('Canonical SEH deployment history is required for deploy')
    current = ds.selected(control.root, args.network, identity)
    uuid = current['deployment']['application_uuid']
    tombstoned = current['deployment'].get('undeployed') is True
    _progress('deploy.target', 'previously_undeployed' if tombstoned else 'registered',
              application_uuid=uuid)
    if tombstoned:
        receipt = _undeploy_receipt(control.root, args.network, uuid)
        if receipt is None or receipt['phase'] != 'VERIFIED' or receipt['identity_binding'] != ds.identity_binding(current):
            raise HarnessError('Verified intentional undeploy receipt required before replacement creation')
        if read_config(state_dir).get('seh_enabled') is True:
            raise HarnessError('Local outbound connection must be disabled before replacement creation')
        _progress('deploy.replacement', 'preflight' if not args.execute_mutations else 'creating')
        created = restore_deleted_canonical_application(control.root, args.network,
                            execute=args.execute_mutations, state_dir=state_dir)
        if not args.execute_mutations:
            return created
        if created.get('status') != 'REPLACEMENT_QUEUED_NOT_VERIFIED':
            return created if created.get('status') != 'PLAN_ONLY' else {
                **created, 'status': 'INCOMPLETE', 'failed_stage': 'replacement_not_submitted'}
        uuid = created['application_uuid']
        _progress('deploy.replacement', 'submitted', application_uuid=uuid)
    else:
        _progress('deploy.recovery', 'inspecting', application_uuid=uuid)
        observed = reconcile_pending_recovery(control.root, args.network,
                                             state_dir=state_dir, execute=False)
        if observed and observed.get('status') == 'PLAN_ONLY' and args.execute_mutations:
            # Existing UUID and durable recovery receipt only: the recovery
            # reconciler decides whether a build may be submitted safely.
            _progress('deploy.recovery', 'reconciling_receipt')
            observed = reconcile_pending_recovery(control.root, args.network,
                                                 state_dir=state_dir, execute=True)
        if observed and observed.get('status') not in (
                'REPLACEMENT_BUILD_VERIFIED', 'REPLACEMENT_QUEUED_NOT_VERIFIED'):
            return observed
        created = observed or {'status': 'REGISTERED', 'application_uuid': uuid, 'remote_mutation': False}
        if not args.execute_mutations:
            return {'status': 'PLAN_ONLY', 'action': 'RESUME_OR_VERIFY_CANONICAL_DEPLOYMENT',
                    'application_uuid': uuid, 'recovery_status': created.get('status'),
                    'remote_mutation': False}
    try:
        if created.get('status') == 'REPLACEMENT_QUEUED_NOT_VERIFIED':
            finished = _wait_for_verified_build(args, control, state_dir, uuid, created)
            if finished.get('status') != 'REPLACEMENT_BUILD_VERIFIED':
                return finished
        # Build and public runtime are separate proofs. Do not activate from a
        # queued build or infer WSS readiness from Coolify's queued receipt.
        timeout_at = time.monotonic() + args.build_timeout
        not_ready = _wait_for_runtime(args, control, state_dir, uuid, timeout_at, created)
        if not_ready:
            return not_ready
        # Reuse the existing signed administrator activation gate. It alone can
        # enable a verified receipt-bound replacement and its outbound relay.
        activation_args = argparse.Namespace(**vars(args))
        activation_args.slh_state_dir = None
        preview_args = argparse.Namespace(**vars(activation_args))
        preview_args.execute_mutations = False
        preview_args.yes_i_know_this_mutates_seh = False
        _progress('deploy.activation', 'preflight')
        preview = _activate_via_resident_slh(preview_args, state_dir_override=state_dir)
        if preview.get('status') != 'PLAN_ONLY':
            return {**preview,
                    'status': ('INCOMPLETE' if created.get('remote_mutation') is True
                               else preview.get('status', 'INCOMPLETE')),
                    'activation_preflight_status': preview.get('status'),
                    'remote_mutation': created.get('remote_mutation', False),
                    'failed_stage': preview.get('failed_stage', 'activation_preflight')}
        _progress('deploy.activation', 'executing')
        live = _activate_via_resident_slh(activation_args, state_dir_override=state_dir)
        combined = {**live, 'application_uuid': uuid,
                    'remote_mutation': (created.get('remote_mutation') is True or
                                        live.get('remote_mutation') is True),
                    'deployment_remote_mutation': created.get('remote_mutation', False)}
        if created.get('remote_mutation') is True and live.get('status') not in ('CONNECTED_VERIFIED',):
            combined['status'] = 'INCOMPLETE'
            combined.setdefault('failed_stage', 'activation_after_deployment')
            combined['activation_status'] = live.get('status')
        _progress('deploy.activation', 'returned', status=combined.get('status'))
        return combined
    except (SEHGitError, HarnessError, OSError, ValueError, RuntimeError) as exc:
        # CREATE/BUILD may already have completed. Do not emit a clean REFUSED
        # that invites an operator to create a second application.
        return {'status': 'INCOMPLETE', 'failed_stage': 'deploy_readiness_or_activation',
                'error_type': type(exc).__name__, 'application_uuid': uuid,
                'remote_mutation': created.get('remote_mutation', False)}


def _canonical_undeploy(args: argparse.Namespace, control: SEHControl) -> dict:
    """One operator action: verify owner, disconnect, verify absence, delete."""
    import time
    from tools.seh_control.git_deployment import undeploy_canonical_runtime
    from main_computer.slh.storage import slh_state_dir
    from main_computer.slh.organization_state import read_config
    if any((args.seh, args.host, args.slh_id, args.public_url,
            args.replace_existing_trust, args.approve_generation)):
        raise HarnessError('Canonical SEH undeploy accepts no alias, controller or signing overrides')
    if args.execute_mutations != args.yes_i_know_this_mutates_seh:
        raise HarnessError('Both mutation approvals are required together')
    if (not 1 <= args.local_port <= 65535 or not 0 < args.build_timeout <= 3600
            or not 0 < args.activation_timeout <= 180):
        raise HarnessError('Invalid local port or readiness timeout')
    state_dir = Path(args.slh_state_dir or slh_state_dir())
    _progress('undeploy.preflight', 'checking')
    preview = undeploy_canonical_runtime(control.root, args.network,
                                        execute=False, state_dir=state_dir)
    _progress('undeploy.preflight', 'returned', status=preview.get('status'))
    if not args.execute_mutations:
        if preview.get('status') == 'PLAN_ONLY' and preview.get('action') == 'DELETE_CANONICAL_SEH_APPLICATION':
            return {**preview, 'action': 'DISCONNECT_VERIFY_AND_DELETE_CANONICAL_SEH',
                    'ready_for_automatic_undeploy': True,
                    'disconnect_required': not preview.get('outbound_disconnected_verified')}
        return preview
    if preview.get('status') == 'PLAN_ONLY' and preview.get('action') == 'VERIFY_PRIOR_UNDEPLOY':
        _progress('undeploy.prior_removal', 'verifying')
        return undeploy_canonical_runtime(control.root, args.network,
                                          execute=True, state_dir=state_dir)
    if preview.get('status') != 'PLAN_ONLY' or preview.get('action') != 'DELETE_CANONICAL_SEH_APPLICATION':
        return preview
    if not preview.get('outbound_disconnected_verified'):
        if read_config(state_dir).get('seh_enabled') is True:
            _disconnect_via_resident_slh(args, state_dir=state_dir)
        deadline = time.monotonic() + args.activation_timeout
        _progress('undeploy.disconnect', 'verifying')
        last_undeploy_at = 0.0
        while True:
            ready = undeploy_canonical_runtime(control.root, args.network,
                                              execute=False, state_dir=state_dir)
            if (ready.get('status') == 'PLAN_ONLY' and
                    ready.get('action') == 'DELETE_CANONICAL_SEH_APPLICATION' and
                    ready.get('outbound_disconnected_verified') is True):
                _progress('undeploy.disconnect', 'verified')
                break
            if ready.get('status') != 'PLAN_ONLY':
                return ready
            if time.monotonic() - last_undeploy_at >= 10:
                _progress('undeploy.disconnect', 'awaiting_verification')
                last_undeploy_at = time.monotonic()
            if time.monotonic() >= deadline:
                _progress('undeploy.disconnect', 'timeout')
                return {'status': 'INCOMPLETE', 'failed_stage': 'outbound_disconnection_unverified',
                        'application_uuid': preview.get('application_uuid'), 'remote_mutation': False}
            time.sleep(min(1, max(0.01, deadline - time.monotonic())))
    _progress('undeploy.delete', 'executing', application_uuid=preview.get('application_uuid'))
    result = undeploy_canonical_runtime(control.root, args.network, execute=True, state_dir=state_dir)
    _progress('undeploy.delete', 'returned', status=result.get('status'))
    return result


def run(args: argparse.Namespace, *, control: SEHControl | None = None,
        slh_public_key_provider=None) -> dict:
    _progress('harness.dispatch', 'routing', status=args.operation)
    if args.operation == 'activate-seh':
        return _activate_via_resident_slh(args)
    if args.operation == 'verify-resident-admin':
        if (args.execute_mutations or args.yes_i_know_this_mutates_seh
                or args.seh or args.host or args.slh_id or args.public_url
                or args.replace_existing_trust):
            raise HarnessError('Resident administrator verification is read-only')
        from main_computer.slh.storage import slh_state_dir
        return _disconnect_via_resident_slh(
            args, state_dir=Path(args.slh_state_dir or slh_state_dir()), verify_only=True)
    control = control or SEHControl(args.runtime_state_root)
    network = args.network
    if args.operation == 'list':
        return control.list(network)
    if args.operation == 'deploy-seh' and not args.seh:
        return _canonical_deploy(args, control)
    if args.operation == 'undeploy-seh':
        return _canonical_undeploy(args, control)
    if args.operation == 'redeploy-runtime':
        if any((args.seh, args.host, args.slh_id, args.public_url,
                args.replace_existing_trust, args.slh_state_dir,
                args.admin_device, args.approve_generation)):
            raise HarnessError('Canonical SEH redeployment accepts no identity, controller or signing overrides')
        if args.execute_mutations != args.yes_i_know_this_mutates_seh:
            raise HarnessError('Both mutation approvals are required together')
        from tools.seh_control.git_deployment import redeploy_canonical_runtime
        _progress('runtime.redeploy', 'executing' if args.execute_mutations else 'preflight')
        return redeploy_canonical_runtime(control.root, network,
                execute=bool(args.execute_mutations and args.yes_i_know_this_mutates_seh))
    if args.operation == 'recover-runtime':
        # A missing Docker container is NOT an unregistered SEH. Recover only
        # the already-registered Coolify application, never a name or alias.
        if any((args.seh, args.host, args.slh_id, args.public_url,
                args.replace_existing_trust, args.slh_state_dir)):
            raise HarnessError('Runtime recovery accepts no SEH name, controller or identity override')
        if args.execute_mutations != args.yes_i_know_this_mutates_seh:
            raise HarnessError('Both mutation approvals are required together')
        from tools.seh_control.git_deployment import recover_canonical_runtime
        _progress('runtime.recover', 'executing' if args.execute_mutations else 'preflight')
        return recover_canonical_runtime(control.root, network,
                execute=bool(args.execute_mutations and args.yes_i_know_this_mutates_seh))
    if args.operation == 'reconcile-metadata':
        # Patch 3 is a UUID-bound metadata reconciliation, not an SEH deploy.
        if any((args.seh, args.host, args.slh_id, args.public_url,
                args.replace_existing_trust, args.slh_state_dir)):
            raise HarnessError('Metadata reconciliation accepts no SEH name, host or identity input')
        if args.execute_mutations != args.yes_i_know_this_mutates_seh:
            raise HarnessError('Both mutation approvals are required together')
        from tools.seh_control.git_deployment import reconcile_metadata
        _progress('metadata.reconcile', 'executing' if args.execute_mutations else 'preflight')
        return reconcile_metadata(control.root, network,
                 execute=bool(args.execute_mutations and args.yes_i_know_this_mutates_seh))
    if args.operation == 'migrate-state':
        if any((args.seh, args.host, args.slh_id, args.public_url,
                args.replace_existing_trust, args.slh_state_dir)):
            raise HarnessError('Local Mother migration accepts no SEH name, host, or SLH key input')
        if not (args.execute_mutations and args.yes_i_know_this_mutates_seh):
            raise HarnessError('Mother migration requires both explicit mutation approvals')
        if args.approve_generation is None:
            raise HarnessError('Mother migration requires --approve-generation from inspect')
        _progress('mother.migrate', 'executing')
        return control.migrate_single(network, args.approve_generation,
                                      'seh-single-migration-' + uuid.uuid4().hex[:18])
    if args.operation in ('inspect', 'diagnostic'):
        if args.seh or args.host or args.slh_id or args.public_url:
            raise HarnessError('inspect and diagnostic use the verified SLH deployment; omit target-selection flags')
        _progress('harness.inspect', 'reading')
        existing, listing = _single_read_only_target(control, network)
        if args.operation == 'inspect':
            if existing is None:
                return {'status': 'READ_ONLY', 'network': network,
                        'generation': listing.get('generation'), 'registered': False,
                        'deployment': None}
            # The obsolete registry key is needed internally until migration,
            # but is not a new operator-visible identity.
            return {'status': 'READ_ONLY', 'network': network,
                    'generation': listing.get('generation'), 'registered': True,
                    **{key: value for key, value in existing.items() if key != 'seh'}}
        if existing is None:
            return {'status': 'REFUSED', 'operation': 'diagnostic', 'network': network,
                    'reason': 'No SEH deployment is registered for this SLH',
                    'mutation_performed': False}
        if 'seh' not in existing:
            # Canonical SLH-owned connection diagnostic: no name, key creation,
            # state writes or Coolify mutation. A successful resolution proves
            # target/trust inputs, NOT that the resident WSS tunnel is online.
            from main_computer.slh.storage import slh_state_dir
            from main_computer.slh.seh_canonical_connection import (
                resolve_canonical_connection, SEHConnectionResolutionError)
            try:
                resolved = resolve_canonical_connection(
                    args.slh_state_dir or slh_state_dir(),
                    mother_root=control.root, network=network,
                    require_enabled=False)
            except (SEHConnectionResolutionError, SEHGitError, SEHIdentityError,
                    OSError, ValueError) as exc:
                return {'status': 'REFUSED', 'operation': 'diagnostic',
                        'network': network, 'remote_mutation': False,
                        'mutation_performed': False,
                        'error_type': type(exc).__name__,
                        'reason': str(exc) if isinstance(exc, (
                            SEHConnectionResolutionError, SEHGitError, SEHIdentityError))
                        else type(exc).__name__}
            return {'status': 'READ_ONLY', 'operation': 'diagnostic',
                    'network': network, 'remote_mutation': False,
                    'mutation_performed': False,
                    'application_uuid': existing['deployment']['application_uuid'],
                    'deployment_enabled': existing['deployment'].get('enabled') is True,
                    'seh_url': resolved.seh_url,
                    'signing_key_trust': 'MATCHED',
                    'resident_tunnel': 'NOT_OBSERVED_BY_THIS_DIAGNOSTIC'}
        seh = ident(existing['seh'], 'legacy SEH registry key')
    else:
        if not args.seh:
            raise HarnessError('--seh remains required for legacy mutation operations')
        seh = ident(args.seh, 'seh')
        existing = _existing(control, network, seh)
    active = (existing or {}).get('active_identity')
    identities = (existing or {}).get('identities') or {}
    active_source = (identities.get(active) or {}).get('copied_from')
    # Existing SEH identities own a copy of their Coolify controller. The
    # alias may be "primary"; it is not the Coolify controller name.
    if args.host:
        host = ident(args.host, 'Coolify controller')
    elif existing and isinstance(active_source, str) and active_source.startswith(
            f'networks.{network}.coolify.controllers.'):
        host = active_source.rsplit('.', 1)[-1]
    else:
        raise HarnessError('--host is required when no existing active Coolify controller can be verified')
    if not host.startswith('coolify-'):
        raise HarnessError('--host must name an existing coolify-* controller')
    source = f'networks.{network}.coolify.controllers.{host}'
    if existing:
        for field, selected in (('slh_id', args.slh_id),):
            if selected and selected != existing.get(field):
                raise HarnessError(f'Existing SEH {field} differs from explicit request')
        deployment = existing.get('deployment') or {}
        if active_source != source and (deployment.get('application_uuid') or deployment.get('service_uuid')):
            raise HarnessError('Deployed SEH belongs to another Coolify controller; use a new SEH identity')
        if host in identities and identities[host].get('copied_from') != source:
            raise HarnessError('Controller alias collision; refusing to replace existing Coolify identity')
        if not active_source and host not in identities and (deployment.get('application_uuid') or
                                                             deployment.get('service_uuid')):
            raise HarnessError('Deployed SEH has no verified controller binding; recovery is required')
    else:
        if args.slh_id:
            ident(args.slh_id, 'slh_id')
    prior_url = ((existing or {}).get('deployment') or {}).get('public_url')
    if args.public_url:
        public_url = _origin(args.public_url)
        if prior_url and _origin(prior_url) != public_url:
            raise HarnessError('Existing SEH public URL differs; deploy-seh will not silently retarget it')
    elif prior_url:
        public_url = _origin(prior_url)
    else:
        # Never override the existing application's verified domain with the
        # default; a missing existing URL requires read-only discovery.
        public_url = None if existing else _origin(DEFAULT_PUBLIC_URL)
    if args.operation == 'diagnostic':
        if not existing or not (existing.get('deployment') or {}).get('application_uuid'):
            raise HarnessError('Trust diagnostic requires an existing registered SEH application')
        # A diagnostic never creates a signing identity and never needs the
        # private key when it is unavailable. It can still report remote IDs.
        local_key = None
        local_key_state = 'UNAVAILABLE'
        try:
            if slh_public_key_provider is not None:
                local_key = slh_public_key_provider(slh_id=existing.get('slh_id'))
            else:
                from tools.slh_seh_connector import default_state, service_identity
                _, local_key = service_identity(args.slh_state_dir or default_state(), create=False)
            local_key_state = 'READABLE' if isinstance(local_key, str) and bool(local_key) else 'UNAVAILABLE'
        except (OSError, ValueError, RuntimeError):
            pass
        try:
            result = control.diagnostic_slh_trust(
                network, seh, slh_id=existing.get('slh_id'), public_key_b64=local_key)
            return {'controller': host, 'local_key_state': local_key_state,
                    **{key: value for key, value in result.items() if key != 'seh'}}
        except (SEHIdentityError, SEHGitError, HarnessError) as exc:
            return {'status': 'REFUSED', 'operation': 'diagnostic', 'network': network,
                    'controller': host, 'error_type': type(exc).__name__,
                    'reason': str(exc), 'mutation_performed': False}

    if args.operation == 'bind-slh':
        if not existing or not (existing.get('deployment') or {}).get('application_uuid'):
            raise HarnessError('Bind requires an existing registered SEH application')
        slh = args.slh_id or existing.get('slh_id')
        if not slh:
            raise HarnessError('Bind requires an approved SLH signing identity')
        if existing.get('slh_id') and slh != existing['slh_id']:
            raise HarnessError('SLH binding differs from existing Mother SEH association')
        stage = 'slh-trust-binding'
        try:
            if slh_public_key_provider is not None:
                public_key = slh_public_key_provider(slh_id=slh)
            else:
                from tools.slh_seh_connector import default_state, service_identity
                _, public_key = service_identity(args.slh_state_dir or default_state(), create=False)
            _progress('trust.bind', 'executing' if args.execute_mutations else 'preflight')
            receipt = control.bind_slh(network, seh, slh_id=slh,
                                       public_key_b64=public_key,
                                       replace_existing=args.replace_existing_trust,
                                       execute=bool(args.execute_mutations and args.yes_i_know_this_mutates_seh))
            return {'controller': host, 'operation': 'bind-slh', **receipt}
        except (SEHIdentityError, SEHGitError, HarnessError, OSError, ValueError) as exc:
            return {'status': 'REFUSED', 'network': network, 'seh': seh, 'controller': host,
                    'failed_stage': stage, 'error_type': type(exc).__name__,
                    'reason': str(exc) if isinstance(exc, (SEHIdentityError, SEHGitError, HarnessError))
                    else type(exc).__name__, 'ready': False, 'public_wss_verified': False}

    if not (args.execute_mutations and args.yes_i_know_this_mutates_seh):
        return {'status': 'PLAN_ONLY', 'network': network, 'seh': seh, 'controller': host,
                'public_url': public_url, 'registered': existing is not None,
                'public_url_discovery': ('REQUIRED_ON_EXECUTION' if public_url is None else 'NOT_REQUIRED'),
                'remote_mutation': False, 'reason': 'Both --execute-mutations and '
                '--yes-i-know-this-mutates-seh are required for all changes'}

    events: list[dict] = []
    op = 'seh-harness-' + uuid.uuid4().hex[:18]
    result: dict = {'status': 'INCOMPLETE', 'network': network, 'seh': seh,
                    'controller': host, 'public_url': public_url, 'operation_id': op,
                    'steps': events, 'public_wss_verified': False, 'ready': False}
    stage = 'public-url'
    try:
        if public_url is None:
            # Only trust an existing registered application on the selected
            # controller. Coolify must expose one unambiguous HTTPS origin;
            # neither a localhost URL nor DEFAULT_PUBLIC_URL is an approval.
            registered_uuid = (existing.get('deployment') or {}).get('application_uuid')
            if not registered_uuid:
                raise HarnessError('Existing SEH has no application UUID or approved public URL; pass --public-url')
            observed = control.observed_public_origin(network, seh)
            if observed.get('application_uuid') != registered_uuid:
                raise HarnessError('Observed Coolify application UUID differs from registered SEH identity')
            candidate = observed.get('public_url')
            if not candidate:
                raise HarnessError('Coolify did not reveal a single approved public HTTPS hostname; pass --public-url')
            public_url = _origin(candidate)
            result['public_url'] = public_url
            _record_step(events, 'public-url-discovery', 'COOLIFY_APPLICATION_DOMAIN_OBSERVED', observed)
        stage = 'registration'
        if not existing:
            receipt = control.change('reserve', network, seh, None, op + '-reserve',
                                     slh_id=args.slh_id)
            _record_step(events, 'reserve', 'COMPLETED', receipt)
            existing = _existing(control, network, seh)
            if not existing:
                raise HarnessError('Reserved SEH identity not observable in verified state')
        else:
            _record_step(events, 'reserve', 'ALREADY_REGISTERED')
        stage = 'controller'
        identities = existing.get('identities') or {}
        active = existing.get('active_identity')
        active_source = (identities.get(active) or {}).get('copied_from')
        selected_alias = active if active_source == source else host
        if selected_alias not in identities:
            receipt = control.change('coolify-copy', network, seh, None, op + '-copy',
                                     alias=host, source=source)
            _record_step(events, 'coolify-copy', 'COMPLETED', receipt)
            existing = _existing(control, network, seh)
        else:
            _record_step(events, 'coolify-copy', 'EXISTING_VERIFIED')
        if existing.get('active_identity') != selected_alias:
            receipt = control.change('coolify-select', network, seh, None, op + '-select', alias=selected_alias)
            _record_step(events, 'coolify-select', 'COMPLETED', receipt)
        else:
            _record_step(events, 'coolify-select', 'ALREADY_SELECTED')
        # Provision the Coolify infrastructure independently of any SLH state.
        # Existing remote SLH trust is left untouched. A new relay is unbound
        # and refuses all tunnels until a separate, authenticated binding step.
        stage = 'prepare'
        prepared = control.prepare_git(network, seh, project_name=args.project_name,
                                       environment_name=args.environment_name, public_url=public_url)
        if prepared.get('status') != 'PREPARED':
            raise HarnessError('SEH Git deployment did not produce a prepared plan')
        _record_step(events, 'prepare', 'COMPLETED', prepared)
        stage = 'preflight'
        preflight = control.preflight_git(network, seh)
        if preflight.get('status') not in ('CREATE_ELIGIBLE', 'EXISTING_APPLICATION_VERIFIED'):
            raise HarnessError('Preflight does not authorize deployment')
        if preflight.get('plan_digest') != prepared.get('plan_digest'):
            raise HarnessError('Preflight plan digest differs from prepared identity')
        _record_step(events, 'preflight', preflight['status'], preflight)
        stage = 'deploy'
        deployed = control.deploy_git(network, seh, operation_id=op + '-deploy')
        _record_step(events, 'deploy', deployed.get('status', 'UNKNOWN'), deployed)
        if (deployed.get('status') != 'DEPLOY_QUEUED' or not deployed.get('application_uuid') or
                deployed.get('plan_digest') != prepared.get('plan_digest')):
            raise HarnessError('Remote deploy receipt was incomplete or inconsistent')
        result['application_uuid'] = deployed['application_uuid']
        stage = 'status'
        observed = control.status_git(network, seh)
        _record_step(events, 'status', observed.get('status', 'UNKNOWN'), observed)
        if observed.get('application_uuid') != deployed['application_uuid']:
            raise HarnessError('Post-deploy observed application does not match the receipt')
        result.update(status='DEPLOY_QUEUED_NOT_VERIFIED',
                      public_wss_verified=False, ready=False,
                      coolify_status=observed.get('coolify_status'),
                      dns_ready=bool(observed.get('dns_ready')))
    except (SEHIdentityError, SEHGitError, HarnessError, OSError, ValueError) as exc:
        # Exception descriptions from SEH adapters are constrained; never print private records.
        _record_step(events, stage, 'FAILED')
        result.update(status='REFUSED', failed_stage=stage, error_type=type(exc).__name__,
                      reason=str(exc) if isinstance(exc, (SEHIdentityError, SEHGitError, HarnessError))
                      else type(exc).__name__)
        if isinstance(exc, SEHGitError) and exc.git_command:
            result['git_repair_command'] = exc.git_command
    return result



def _powershell_argument(value: object) -> str:
    """Quote an operator-supplied CLI value as one literal PowerShell argument."""
    raw = str(value)
    if re.fullmatch(r'[A-Za-z0-9_./:\\-]+', raw):
        return raw
    return "'" + raw.replace("'", "''") + "'"


def _deploy_retry_command(args: argparse.Namespace, *, require_replace: bool = False) -> str:
    """Reconstruct the original logical target without copying credentials."""
    tokens = ['python', r'.\seh_mutate_harness.py', args.operation]
    for attr, flag in (
        ('network', '--network'),
        ('seh', '--seh'),
        ('host', '--host'),
        ('slh_id', '--slh-id'),
        ('public_url', '--public-url'),
    ):
        value = getattr(args, attr, None)
        if value:
            tokens.extend((flag, _powershell_argument(value)))
    for attr, flag, default in (
        ('project_name', '--project-name', 'My first project'),
        ('environment_name', '--environment-name', None),
        ('runtime_state_root', '--runtime-state-root', Path('runtime/state')),
    ):
        value = getattr(args, attr, None)
        if value is not None and value != default:
            tokens.extend((flag, _powershell_argument(value)))
    if args.operation == 'bind-slh':
        if args.replace_existing_trust or require_replace:
            tokens.append('--replace-existing-trust')
        if args.slh_state_dir:
            tokens.extend(('--slh-state-dir', _powershell_argument(args.slh_state_dir)))
    tokens.extend(('--execute-mutations', '--yes-i-know-this-mutates-seh'))
    return ' '.join(tokens)


def _print_next_operator_command(args: argparse.Namespace, result: dict) -> None:
    """Show the Hub-style, copyable next step without breaking JSON stdout."""
    if args.operation == 'diagnostic' and result.get('issues'):
        print('\nSEH trust diagnostic findings (read-only):', file=sys.stderr)
        for issue in result['issues']:
            print('  ' + issue, file=sys.stderr)
        return
    if args.operation == 'activate-seh':
        reason = result.get('git_source_reason')
        if isinstance(reason, str) and reason.strip():
            print('\nSEH Git source preflight: ' + reason.strip(), file=sys.stderr)
        git_command = result.get('git_repair_command')
        if isinstance(git_command, str) and git_command.strip():
            print('\nSEH_GIT_COMMIT_PUSH_COMMAND: (PowerShell; copy and paste)',
                  file=sys.stderr)
            print(git_command.strip(), file=sys.stderr, flush=True)
        return
    if args.operation == 'undeploy-seh':
        # Presentation only. Running undeploy a second time here previously
        # raised NameError after a legitimate refusal, and risked repeating a
        # consequential operation after successful execution.
        if result.get('status') == 'PLAN_ONLY' and (
                result.get('ready_for_undeploy') is True or
                result.get('ready_for_automatic_undeploy') is True):
            command = ['python', r'.\seh_mutate_harness.py', 'undeploy-seh',
                       '--network', args.network]
            if args.runtime_state_root != Path('runtime/state'):
                command.extend(('--runtime-state-root', _powershell_argument(args.runtime_state_root)))
            if args.slh_state_dir is not None:
                command.extend(('--slh-state-dir', _powershell_argument(args.slh_state_dir)))
            command.extend(('--execute-mutations', '--yes-i-know-this-mutates-seh'))
            print('\nCanonical SEH undeploy plan accepted. Execution will stop the outbound '
                  'connection, verify disconnection, then remove the exact application. '
                  'Confirm work is settled before authorizing:', file=sys.stderr)
            print(' '.join(command), file=sys.stderr, flush=True)
        return
    if args.operation == 'redeploy-runtime':
        if result.get('status') == 'PLAN_ONLY':
            print('\nCanonical SEH redeployment plan verified. To force an in-place rebuild:',
                  file=sys.stderr)
            print(r'python .\seh_mutate_harness.py redeploy-runtime '
                  '--network mainnet --execute-mutations --yes-i-know-this-mutates-seh',
                  file=sys.stderr, flush=True)
        return
    if args.operation == 'recover-runtime':
        if result.get('status') == 'PLAN_ONLY':
            print('\nRuntime unavailable. To request a replacement of the existing UUID:',
                  file=sys.stderr)
            print(r'python .\seh_mutate_harness.py recover-runtime '
                  '--execute-mutations --yes-i-know-this-mutates-seh',
                  file=sys.stderr, flush=True)
        elif result.get('status') == 'REPLACEMENT_QUEUED_NOT_VERIFIED':
            print('Coolify replacement queued. The SLH reconnects outbound automatically; '
                  'queued is not connected.', file=sys.stderr, flush=True)
        return
    if args.operation not in ('deploy-seh', 'bind-slh'):
        return
    git_command = result.get('git_repair_command')
    status = result.get('status')
    if status == 'REFUSED' and isinstance(git_command, str) and git_command.strip():
        # Preserve existing PowerShell output ordering for pre-mutation refusals.
        print('\n=== Next SEH command (PowerShell) ===', file=sys.stderr)
        print('After publishing the SEH source, rerun:', file=sys.stderr)
        print(_deploy_retry_command(args), file=sys.stderr)
        print('\nSEH_GIT_COMMIT_PUSH_COMMAND: (PowerShell; copy and paste)',
              file=sys.stderr)
        print(git_command.strip(), file=sys.stderr, flush=True)
    elif status == 'INCOMPLETE' and isinstance(git_command, str) and git_command.strip():
        print('\nSEH_GIT_COMMIT_PUSH_COMMAND: (PowerShell; copy and paste)',
              file=sys.stderr)
        print(git_command.strip(), file=sys.stderr)
        # An INCOMPLETE run may have a receipt-backed replacement even when
        # remote_mutation is False (resumed adoption). Never infer a new create
        # is safe from that single field.
        print('The deployment is incomplete and may already have a replacement. '
              'Inspect the registered application before another mutation; '
              'do not create a second application.', file=sys.stderr)
        print(r'python .\seh_mutate_harness.py inspect --network ' + args.network,
              file=sys.stderr, flush=True)
    elif status == 'INCOMPLETE':
        operation = result.get('failed_operation')
        code = result.get('error_code')
        if isinstance(operation, str) and isinstance(code, str):
            print('\nSEH_DEPLOY_FAILURE: ' + code + ' [' + operation + ']', file=sys.stderr)
            reason = result.get('reason')
            if isinstance(reason, str) and reason.strip():
                print('SEH_FAILURE_REASON: ' + reason.strip(), file=sys.stderr)
            domain = result.get('error_domain')
            if domain == 'LOCAL_SEH_STATE':
                print('Recovery: inspect the local SEH deployment-state path, permissions, '
                      'and concurrent writes. A Git push will not repair a local-state failure.',
                      file=sys.stderr)
            elif domain == 'COOLIFY':
                print('Recovery: inspect the existing Coolify application configuration, '
                      'ownership, placement, and trust before any further mutation. '
                      'A Git push is not indicated by this failure.', file=sys.stderr)
            else:
                print('Recovery: inspect the canonical SEH/controller identity and state '
                      'before any further mutation. No Git repair is indicated.', file=sys.stderr)
        else:
            print('\nSEH deployment incomplete after possible mutation. '
                  'Inspect the existing application before retrying.', file=sys.stderr)
        print(r'python .\seh_mutate_harness.py inspect --network ' + args.network,
              file=sys.stderr, flush=True)
    elif result.get('status') == 'PLAN_ONLY':
        print('\n=== Next SEH command (PowerShell) ===', file=sys.stderr)
        print('Stopping before live mutation. Rerun with mutation authorization:', file=sys.stderr)
        print(_deploy_retry_command(args, require_replace=result.get('replacement_requires_ack', False)),
              file=sys.stderr, flush=True)
    elif args.operation == 'bind-slh' and result.get('status') == 'TRUST_DEPLOY_QUEUED_NOT_VERIFIED':
        # This legacy operator does not activate the resident SLH supervisor.
        # Never prescribe a second, standalone connector as if it completed deployment.
        print('SEH trust redeploy queued, not verified. Resident SLH auto-connection '
              'is not configured here; operational acceptance remains incomplete.',
              file=sys.stderr, flush=True)

def _seh_refusal_code(exc: Exception) -> str | None:
    """Classify known SEH recovery refusals; never echo arbitrary exception text."""
    if isinstance(exc, SEHGitError) and str(exc) in (
        'Coolify SEH application topology changed during recovery',
        'Coolify inventory still lists deleted UUID; refusing ambiguous recreation',
        'Coolify has multiple possible SEH applications; refusing duplicate creation',
        'Former Coolify UUID reappeared; refusing creation',
    ):
        return 'SEH_RECOVERY_TOPOLOGY_CONFLICT'
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    reporter = _OperatorProgress()
    token = _ACTIVE_PROGRESS.set(reporter)
    reporter.start()
    try:
        _progress('harness', 'started', status=args.operation)
        try:
            result = run(args)
        except (HarnessError, SEHIdentityError, SEHGitError, OSError, ValueError) as exc:
            result = {'status': 'REFUSED', 'error_type': type(exc).__name__,
                      'reason': str(exc) if isinstance(exc, (HarnessError, SEHIdentityError, SEHGitError))
                      else type(exc).__name__}
            code = _seh_refusal_code(exc)
            if code is not None:
                result['error_code'] = code
            if isinstance(exc, SEHGitError) and exc.git_command:
                result['git_repair_command'] = exc.git_command
        _progress('harness', 'finished', status=result.get('status'))
    finally:
        reporter.stop()
        _ACTIVE_PROGRESS.reset(token)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    _print_next_operator_command(args, result)
    status = result.get('status')
    if status in ('REFUSED', 'INCOMPLETE'):
        return 2
    # Nonzero until the requested live operation is fully verified. Scripts
    # must not equate an accepted Coolify deploy with an authenticated tunnel.
    if status in ('DEPLOY_QUEUED_NOT_VERIFIED', 'TRUST_DEPLOY_QUEUED_NOT_VERIFIED',
                  'REPLACEMENT_QUEUED_NOT_VERIFIED', 'DEPLOYMENT_IN_PROGRESS'):
        return 3
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
