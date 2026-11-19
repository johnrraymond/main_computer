"""Real-effect SLH acceptance with explicit, tightly scoped mutation authority.

Isolated acceptance uses the real loopback HTTP API and a disposable organization.
Live acceptance creates/approves/revokes test worker, requester and administrator
identities on the real loopback ORG-SLH, and optionally cycles the canonical SEH
through the existing verified mainnet deploy/undeploy harness. No mock Coolify
receipt is counted as deployment success.

Preflight (never mutate):
    python -m tools.mcel_slh_dangerous_functional_acceptance --mode preflight --scope seh --network mainnet
Isolated real HTTP identity mutation (disposable state):
    python -m tools.mcel_slh_dangerous_functional_acceptance --mode isolated
Real organization identity mutations, cleaned up:
    python -m tools.mcel_slh_dangerous_functional_acceptance --mode live --scope identities --execute-mutations --acknowledge-real-mutations
Real canonical SEH cycle (requires verified UUID from preflight):
    python -m tools.mcel_slh_dangerous_functional_acceptance --mode live --scope seh --network mainnet --expect-seh-uuid UUID --execute-mutations --acknowledge-real-mutations

An initially deployed SEH is REFUSED unless --cycle-active-seh is explicitly
specified. In that mode, the test intentionally undeploys the existing app and
redeploys a replacement, returning the service to a deployed state if successful;
the Coolify UUID can change. Never use this on a service that cannot tolerate an
outage. A partially failed remote mutation is not automatically retried/deleted.

Live mutations must be run by the operator in the target environment. This
module never activates live mutations during pytest collection or import.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from typing import Callable
from wsgiref.simple_server import make_server, WSGIRequestHandler

from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator import sign_object
from main_computer.slh.authenticator_store import LocalIdentityStore
from main_computer.slh.local_authenticator_api import LocalAuthenticatorAPI
from tools.slh_authenticator_cli import _default_auth_dir, _request, bootstrap_admin, load_identity, save_identity
from tools.slh_enrollment_lifecycle import _open_authenticated_session
from tools.slh_seh_connector import service_identity

class _QuietHandler(WSGIRequestHandler):
    def log_message(self, format, *args):
        pass


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / 'runtime/reports/mcel-slh-dangerous-functional/report.json'


class AcceptanceError(RuntimeError):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise AcceptanceError(code)


def expect_refusal(operation: Callable[[], object], codes: tuple[str, ...], gate: str) -> None:
    try:
        operation()
    except RuntimeError as exc:
        if any('(' + code + ')' in str(exc) for code in codes):
            return
        raise AcceptanceError(gate + '_UNEXPECTED_REFUSAL') from None
    raise AcceptanceError(gate + '_UNEXPECTED_SUCCESS')


def _open_lms_authenticated_session(port: int, device_id: str,
                                    *, expected_fingerprint: str | None = None,
                                    challenge: dict | None = None) -> tuple[str, dict]:
    """Authenticate through the running LMS-SLH signer, without reading key files.

    Return a verified session. The caller must log it out. A failed session
    verification must not leave an authenticated session behind.
    """
    identity = _request(port, 'GET',
                        '/v1/local/authenticator/identities/' + device_id)
    fingerprint = identity.get('fingerprint')
    require(identity.get('device_id') == device_id and
            identity.get('usable') is True and
            isinstance(fingerprint, str) and len(fingerprint) == 64 and
            all(letter in '0123456789abcdef' for letter in fingerprint),
            'LMS_ADMIN_IDENTITY_UNVERIFIED')
    if expected_fingerprint is not None:
        require(fingerprint == expected_fingerprint,
                'LMS_ADMIN_IDENTITY_FINGERPRINT_CHANGED')
    if challenge is None:
        challenge = _request(port, 'POST', '/v1/auth/challenge',
                             {'device_id': device_id})
    require(isinstance(challenge, dict) and
            isinstance(challenge.get('challenge_id'), str) and
            bool(challenge['challenge_id']), 'LMS_ADMIN_CHALLENGE_UNVERIFIED')
    signed = _request(port, 'POST',
                      '/v1/local/authenticator/identities/' + device_id + '/sign',
                      {'challenge': challenge})
    require(signed.get('device_id') == device_id and
            signed.get('fingerprint') == fingerprint and
            isinstance(signed.get('signature'), str) and
            bool(signed['signature']), 'LMS_ADMIN_SIGNATURE_UNVERIFIED')
    verified = _request(port, 'POST', '/v1/auth/verify', {
        'device_id': device_id, 'challenge_id': challenge['challenge_id'],
        'signature': signed['signature'],
    })
    token = verified.get('access_token')
    require(isinstance(token, str) and bool(token),
            'LMS_ADMIN_AUTH_RECEIPT_UNVERIFIED')
    try:
        session = _request(port, 'GET', '/v1/auth/session', token=token)
        require(session.get('device_id') == device_id and
                session.get('active') is True,
                'LMS_ADMIN_SESSION_UNVERIFIED')
    except BaseException:
        _request(port, 'POST', '/v1/auth/logout', {}, token=token)
        raise
    return token, session


def discover_admin_device(*, port: int, identity_dir: Path | None = None,
                          source: str = 'filesystem') -> str:
    """Find an administrator by verified signed session and admin directory.

    Normal live operation uses source='lms', the same local Authenticator
    service that supplies MCEL's identity rail. An explicitly supplied
    --identity-dir selects the legacy filesystem signer for fixture testing.
    There is intentionally no silent fallback between the two authorities.
    """
    require(source in ('lms', 'filesystem'), 'UNKNOWN_IDENTITY_SOURCE')
    status = _request(port, 'GET', '/v1/bootstrap/status')
    require(status.get('initialized') is True, 'ORGANIZATION_NOT_INITIALIZED')
    require(isinstance(status.get('service_fingerprint'), str) and
            len(status['service_fingerprint']) == 64, 'SERVICE_IDENTITY_UNVERIFIED')
    directory = _request(port, 'GET', '/v1/enrollment/admins').get('admins')
    require(isinstance(directory, list) and bool(directory) and
            all(isinstance(entry, dict) and isinstance(entry.get('id'), str)
                and entry['id'] for entry in directory),
            'ADMIN_DIRECTORY_UNVERIFIED')
    admin_user_ids = {entry['id'] for entry in directory}
    if source == 'lms':
        local = _request(port, 'GET', '/v1/local/authenticator/identities')
        require(isinstance(local.get('invalid_identities'), list) and
                not local['invalid_identities'],
                'LMS_LOCAL_IDENTITY_STORE_UNVERIFIED')
    else:
        require(identity_dir is not None, 'EXPLICIT_IDENTITY_DIRECTORY_REQUIRED')
        local = LocalIdentityStore(identity_dir).list()
    identities = local.get('identities')
    require(isinstance(identities, list) and bool(identities),
            ('NO_LMS_IDENTITIES_FOR_ADMIN_DISCOVERY' if source == 'lms'
             else 'NO_LOCAL_IDENTITIES_FOR_ADMIN_DISCOVERY'))
    require(len(identities) <= 128 and all(
        isinstance(item, dict) and isinstance(item.get('device_id'), str) and
        bool(item['device_id']) and item.get('usable') is True and
        isinstance(item.get('fingerprint'), str) and len(item['fingerprint']) == 64
        for item in identities), 'ADMIN_IDENTITY_INVENTORY_UNVERIFIED')
    require(len({item['device_id'] for item in identities}) == len(identities),
            'DUPLICATE_ADMIN_IDENTITY_INVENTORY')
    default_id = local.get('default_device_id')
    # An LMS default designates an identity, not a verified administrator.
    # Only a signed, current organization session grants that status.
    identities = sorted(identities, key=lambda item: item['device_id'] != default_id)
    approved: list[str] = []
    for identity in identities:
        device_id = identity['device_id']
        token: str | None = None
        try:
            try:
                challenge = _request(port, 'POST', '/v1/auth/challenge',
                                     {'device_id': device_id})
            except RuntimeError as exc:
                if '(UNKNOWN_OR_REVOKED_DEVICE)' in str(exc):
                    continue
                raise
            if source == 'lms':
                token, session = _open_lms_authenticated_session(
                    port, device_id, expected_fingerprint=identity['fingerprint'],
                    challenge=challenge)
            else:
                key, _ = load_identity(identity_dir, device_id)
                verified = _request(port, 'POST', '/v1/auth/verify', {
                    'device_id': device_id,
                    'challenge_id': challenge['challenge_id'],
                    'signature': sign_object(key, challenge),
                })
                token = verified.get('access_token')
                require(isinstance(token, str) and bool(token),
                        'ADMIN_DISCOVERY_AUTH_RECEIPT_UNVERIFIED')
                session = _request(port, 'GET', '/v1/auth/session', token=token)
                require(session.get('device_id') == device_id,
                        'ADMIN_DISCOVERY_SESSION_DEVICE_MISMATCH')
            if session.get('role') == 'admin':
                require(session.get('active') is True and
                        session.get('user_id') in admin_user_ids,
                        'ADMIN_DISCOVERY_DIRECTORY_MISMATCH')
                approved.append(device_id)
        finally:
            if token is not None:
                logout = _request(port, 'POST', '/v1/auth/logout', {}, token=token)
                require(logout.get('status') == 'LOGGED_OUT',
                        'ADMIN_DISCOVERY_SESSION_CLEANUP_UNVERIFIED')
        if device_id == default_id and device_id in approved:
            return device_id
    require(bool(approved), 'NO_AUTHORIZED_LOCAL_ADMIN_DEVICE')
    require(len(approved) == 1, 'MULTIPLE_ADMIN_DEVICES_REQUIRE_EXPLICIT_SELECTION')
    return approved[0]


def submit_identity(port: int, identity_dir: Path, admin_id: str,
                    device_id: str, role: str) -> str:
    """Submit real, signed HTTP enrollment using an exclusive temporary P-256 key."""
    require(role in ('worker', 'requester', 'admin'), 'UNSUPPORTED_ROLE')
    save_identity(identity_dir, device_id)  # exclusive create, never replace
    key, public = load_identity(identity_dir, device_id)
    fingerprint = _request(port, 'GET', '/v1/bootstrap/status').get('service_fingerprint')
    require(isinstance(fingerprint, str) and len(fingerprint) == 64, 'SERVICE_IDENTITY_UNVERIFIED')
    body = {'selected_admin': admin_id, 'device_id': device_id, 'public_key': public,
            'kind': 'administrator' if role == 'admin' else role,
            'device_name': 'SLH Functional Acceptance ' + role,
            'claimed_user': 'Temporary SLH Functional Acceptance',
            'requested_role': role, 'client_nonce': secrets.token_urlsafe(32)}
    proof = sign_object(key, {**body, 'version': 1,
                              'action': 'slh.device.enrollment.request',
                              'audience': 'slh://' + fingerprint})
    receipt = _request(port, 'POST', '/v1/enrollment/requests', {**body, 'proof': proof})
    rid = receipt.get('request_id')
    require(receipt.get('status') == 'PENDING_ADMIN' and isinstance(rid, str)
            and len(rid) == 32, 'PENDING_RECEIPT_UNVERIFIED')
    return rid


def identity_cycle(*, port: int, admin_identity_dir: Path | None, admin_device: str,
                   admin_source: str = 'filesystem', event: Callable[[str, dict], None] | None = None,
                   authorize_mutations: bool = False) -> dict:
    """Mutate the real organization, but only records generated in this run.

    Ephemeral private keys live in a temporary directory and never enter the
    installation's long-lived local identity inventory. The ORGANIZATION'S
    authoritative enrollment, privileges, revocation, restore, permanent removal,
    and audit are real. Any cleanup failure is a hard failure, not PASS.
    """
    require(authorize_mutations, 'EXPLICIT_IDENTITY_MUTATION_APPROVAL_REQUIRED')
    record = event or (lambda stage, data: None)
    status = _request(port, 'GET', '/v1/bootstrap/status')
    require(status.get('initialized') is True, 'ORGANIZATION_NOT_INITIALIZED')
    admin_token, session = (
        _open_lms_authenticated_session(port, admin_device)
        if admin_source == 'lms' else
        _open_authenticated_session(port, admin_identity_dir, admin_device)
    )
    if session.get('role') != 'admin' or session.get('active') is not True:
        try:
            _request(port, 'POST', '/v1/auth/logout', {}, token=admin_token)
        finally:
            raise AcceptanceError('ADMIN_SESSION_REQUIRED')
    admin_id = session['user_id']
    all_created: list[dict] = []  # keys may exist before an enrollment receipt does
    cleanup_errors: list[str] = []
    baseline: set[str] | None = None
    baseline_trash: set[str] | None = None
    passed: list[str] = []
    failure: BaseException | None = None
    try:
        policies = _request(port, 'GET', '/v1/admin/policies', token=admin_token)
        require(policies.get('physical_inspection_required') is False,
                'PHYSICAL_INSPECTION_REQUIRES_HUMAN_ATTESTATION')
        admins = _request(port, 'GET', '/v1/enrollment/admins')['admins']
        require(any(a['id'] == admin_id for a in admins), 'ADMIN_DIRECTORY_MISMATCH')
        baseline = {d['id'] for d in _request(port, 'GET', '/v1/admin/devices', token=admin_token)['devices']}
        baseline_trash = {d['id'] for d in _request(port, 'GET', '/v1/admin/devices/trash', token=admin_token)['devices']}
        require(admin_device in baseline, 'ADMIN_DEVICE_NOT_REGISTERED')
        with tempfile.TemporaryDirectory(prefix='mcel-slh-acceptance-keys-') as tmp:
            keys = Path(tmp)

            def make(role: str) -> dict:
                device_id = 'slhacc-' + role + '-' + secrets.token_hex(8)
                entry = {'id': device_id, 'role': role, 'request': None,
                         'approved': False, 'removed': False}
                all_created.append(entry)
                record('identity.reserved', {'device_id': device_id, 'role': role})
                entry['request'] = submit_identity(port, keys, admin_id, device_id, role)
                record('identity.pending', {'device_id': device_id, 'request_id': entry['request']})
                require(_request(port, 'GET', '/v1/enrollment/requests/' + entry['request'] + '/status')
                        .get('status') == 'pending', 'IDENTITY_NOT_PENDING')
                return entry

            def approve(entry: dict, signer: str) -> str:
                require(entry['request'] is not None, 'ENROLLMENT_REQUEST_MISSING')
                receipt = _request(port, 'POST', '/v1/enrollment/approvals',
                                   {'request_id': entry['request']}, token=signer)
                # The approval may have committed even if HTTP response is lost.
                # Cleanup below always searches for each generated device ID.
                require(receipt.get('status') == 'APPROVED' and
                        receipt.get('device_id') == entry['id'], 'APPROVAL_RECEIPT_UNVERIFIED')
                entry['approved'] = True
                record('identity.approved', {'device_id': entry['id'], 'role': entry['role']})
                return receipt['user_id']

            worker = make('worker')
            expect_refusal(lambda: _request(port, 'POST', '/v1/auth/challenge',
                                            {'device_id': worker['id']}),
                           ('UNKNOWN_OR_REVOKED_DEVICE',), 'PENDING_LOGIN')
            approve(worker, admin_token)
            worker_token, who = _open_authenticated_session(port, keys, worker['id'])
            require(who.get('role') == 'worker' and who.get('active'), 'WORKER_LOGIN_FAILED')
            expect_refusal(lambda: _request(port, 'GET', '/v1/admin/devices', token=worker_token),
                           ('ADMIN_REQUIRED',), 'WORKER_ESCALATION')
            require(_request(port, 'POST', '/v1/admin/devices/' + worker['id'] + '/revoke',
                             {}, token=admin_token).get('status') == 'REVOKED',
                    'WORKER_REVOKE_FAILED')
            expect_refusal(lambda: _request(port, 'GET', '/v1/auth/session', token=worker_token),
                           ('SESSION_INVALID', 'SESSION_REVOKED'), 'REVOKED_BEARER')
            require(_request(port, 'POST', '/v1/admin/devices/' + worker['id'] + '/reactivate',
                             {}, token=admin_token).get('status') == 'REACTIVATED',
                    'WORKER_REACTIVATION_FAILED')
            expect_refusal(lambda: _request(port, 'GET', '/v1/auth/session', token=worker_token),
                           ('SESSION_INVALID', 'SESSION_REVOKED'), 'OLD_BEARER_REACTIVATED')
            worker_token2, who2 = _open_authenticated_session(port, keys, worker['id'])
            require(who2.get('role') == 'worker', 'WORKER_FRESH_LOGIN_FAILED')
            _request(port, 'POST', '/v1/auth/logout', {}, token=worker_token2)
            passed.append('worker_enroll_revoke_reactivate_session_invalidation')

            requester = make('requester')
            approve(requester, admin_token)
            requester_token, req_who = _open_authenticated_session(port, keys, requester['id'])
            require(req_who.get('role') == 'requester', 'REQUESTER_ROLE_WRONG')
            expect_refusal(lambda: _request(port, 'GET', '/v1/admin/devices', token=requester_token),
                           ('ADMIN_REQUIRED',), 'REQUESTER_ESCALATION')
            _request(port, 'POST', '/v1/auth/logout', {}, token=requester_token)
            passed.append('requester_enroll_and_authorization')

            second_admin = make('admin')
            approve(second_admin, admin_token)
            new_admin_token, who_admin = _open_authenticated_session(port, keys, second_admin['id'])
            require(who_admin.get('role') == 'admin', 'NEW_ADMIN_ROLE_WRONG')
            _request(port, 'GET', '/v1/admin/devices', token=new_admin_token)
            admins2 = _request(port, 'GET', '/v1/enrollment/admins')['admins']
            require(any(a['id'] == who_admin['user_id'] for a in admins2),
                    'NEW_ADMIN_NOT_IN_DIRECTORY')
            # Prove that the new administrator holds real approval authority.
            delegated = make('worker')
            receipt_user = approve(delegated, new_admin_token)
            require(isinstance(receipt_user, str) and receipt_user,
                    'DELEGATED_ADMIN_APPROVAL_FAILED')
            _request(port, 'POST', '/v1/auth/logout', {}, token=new_admin_token)
            passed.append('admin_enroll_elevation_and_delegated_approval')

            rejected = make('requester')
            require(_request(port, 'POST',
                             '/v1/admin/enrollments/' + rejected['request'] + '/deny',
                             {}, token=admin_token).get('status') == 'DENIED',
                    'PENDING_DENIAL_FAILED')
            require(_request(port, 'GET',
                             '/v1/enrollment/requests/' + rejected['request'] + '/status')
                    .get('status') == 'denied', 'DENIAL_NOT_DURABLE')
            expect_refusal(lambda: _request(port, 'POST', '/v1/auth/challenge',
                                            {'device_id': rejected['id']}),
                           ('UNKNOWN_OR_REVOKED_DEVICE',), 'REJECTED_LOGIN')
            passed.append('enrollment_denial')

            audit = _request(port, 'GET', '/v1/admin/audit', token=admin_token)['events']
            actions = {x['action'] for x in audit}
            require({'enrollment.request', 'enrollment.approved', 'enrollment.denied',
                     'device.revoked', 'device.reactivated'} <= actions,
                    'AUDIT_ACTIONS_MISSING')
            passed.append('durable_audit')
    except BaseException as exc:
        failure = exc
    finally:
        # Clean only device IDs generated here. Never empty the real Trash,
        # toggle an organization's retention policy, or touch the existing admin.
        for entry in reversed(all_created):
            did = entry['id']
            try:
                registered = next((d for d in _request(port, 'GET', '/v1/admin/devices',
                                                       token=admin_token)['devices']
                                   if d['id'] == did), None)
                if registered is not None:
                    if registered['status'] == 'active':
                        _request(port, 'POST', '/v1/admin/devices/' + did + '/revoke',
                                 {}, token=admin_token)
                    result = _request(port, 'POST', '/v1/admin/devices/' + did + '/remove',
                                      {'confirm': 'REMOVE_DEVICE'}, token=admin_token)
                    require(result.get('status') == 'REMOVED', 'CLEANUP_REMOVE_UNVERIFIED')
                    entry['removed'] = True
                    record('identity.removed', {'device_id': did})
                elif entry['request']:
                    state = _request(port, 'GET', '/v1/enrollment/requests/' +
                                     entry['request'] + '/status')
                    if state['status'] == 'pending':
                        _request(port, 'POST',
                                 '/v1/admin/enrollments/' + entry['request'] + '/deny',
                                 {}, token=admin_token)
            except (RuntimeError, ValueError, OSError, KeyError) as exc:
                cleanup_errors.append(did + ':' + type(exc).__name__)
        try:
            if baseline is not None:
                remaining = {d['id'] for d in _request(port, 'GET', '/v1/admin/devices',
                                                      token=admin_token)['devices']}
                current_trash = {d['id'] for d in _request(port, 'GET', '/v1/admin/devices/trash',
                                                          token=admin_token)['devices']}
                if remaining != baseline or current_trash != baseline_trash:
                    cleanup_errors.append('BASELINE_INVENTORY_CHANGED')
            _request(port, 'POST', '/v1/auth/logout', {}, token=admin_token)
        except (RuntimeError, ValueError, OSError, KeyError) as exc:
            cleanup_errors.append('SESSION_OR_INVENTORY:' + type(exc).__name__)
    result = {'status': 'PASS' if failure is None and not cleanup_errors else 'INCOMPLETE',
              'lane': 'real-http-mutating', 'administrator_device': admin_device,
              'proved': passed, 'created': [{k: item[k] for k in ('id', 'role', 'removed')}
                                          for item in all_created],
              'cleanup': 'VERIFIED_REMOVED' if not cleanup_errors else 'FAILED',
              'cleanup_errors': cleanup_errors}
    if failure is not None:
        result['failed_gate'] = str(failure) if isinstance(failure, AcceptanceError) else type(failure).__name__
    return result


def canonical_local_identity_cycle(*, port: int, admin_identity_dir: Path,
                                   admin_device: str, event=None,
                                   disposable_authority: bool = False) -> dict:
    """Real LMS-owned creation/signing on a DISPOSABLE local Authenticator.

    No corresponding local identity DELETE endpoint exists. For that reason
    this proof is restricted to the isolated disposable service, never the
    user's permanent Authenticator store.
    """
    require(disposable_authority, 'CANONICAL_LOCAL_CREATION_REQUIRES_DISPOSABLE_AUTHORITY')
    from tools.slh_authenticator_cli import request_enrollment
    record = event or (lambda stage, data: None)
    token, admin = _open_authenticated_session(port, admin_identity_dir, admin_device)
    device_id = None
    approved = False
    try:
        require(admin['role'] == 'admin', 'CANONICAL_ADMIN_REQUIRED')
        result = request_enrollment(port=port, label='Disposable Canonical Admin',
                                    role='admin', admin_id=admin['user_id'])
        require(result.get('status') == 'PENDING_ADMIN', 'CANONICAL_REQUEST_FAILED')
        device_id = result['device_id']
        record('canonical.identity.pending', {'device_id': device_id,
                                               'request_id': result['request_id']})
        receipt = _request(port, 'POST', '/v1/enrollment/approvals',
                           {'request_id': result['request_id']}, token=token)
        require(receipt.get('status') == 'APPROVED', 'CANONICAL_APPROVAL_FAILED')
        approved = True
        challenge = _request(port, 'POST', '/v1/auth/challenge', {'device_id': device_id})
        signature = _request(port, 'POST',
                             '/v1/local/authenticator/identities/' + device_id + '/sign',
                             {'challenge': challenge})
        require(signature.get('device_id') == device_id and
                isinstance(signature.get('signature'), str), 'CANONICAL_SIGN_FAILED')
        logged = _request(port, 'POST', '/v1/auth/verify',
                          {'device_id': device_id, 'challenge_id': challenge['challenge_id'],
                           'signature': signature['signature']})
        fresh_token = logged['access_token']
        try:
            who = _request(port, 'GET', '/v1/auth/session', token=fresh_token)
            require(who.get('role') == 'admin', 'CANONICAL_ADMIN_ROLE_FAILED')
            _request(port, 'GET', '/v1/admin/devices', token=fresh_token)
        finally:
            _request(port, 'POST', '/v1/auth/logout', {}, token=fresh_token)
        return {'status': 'PASS', 'lane': 'isolated-real-lms-authenticator-http',
                'proof': 'local create -> signed pending -> approved admin -> signed login'}
    finally:
        if device_id is not None:
            observed = next((d for d in _request(port, 'GET', '/v1/admin/devices',
                                                token=token)['devices']
                             if d['id'] == device_id), None)
            if observed:
                if observed['status'] == 'active':
                    _request(port, 'POST', '/v1/admin/devices/' + device_id + '/revoke',
                             {}, token=token)
                _request(port, 'POST', '/v1/admin/devices/' + device_id + '/remove',
                         {'confirm': 'REMOVE_DEVICE'}, token=token)
            elif not approved:
                pending = next((i for i in _request(port, 'GET', '/v1/admin/enrollments',
                                                    token=token)['enrollments']
                                if i['device_id'] == device_id and i['status'] == 'pending'), None)
                if pending:
                    _request(port, 'POST', '/v1/admin/enrollments/' + pending['id'] + '/deny',
                             {}, token=token)
        _request(port, 'POST', '/v1/auth/logout', {}, token=token)


def seh_arguments(operation: str, *, root: Path, local_port: int, admin_device: str | None):
    # Reuse canonical operator authority; do not recreate the Coolify writer.
    import seh_mutate_harness as existing
    tokens = [operation, '--network', 'mainnet', '--runtime-state-root', str(root),
              '--local-port', str(local_port)]
    if admin_device:
        tokens.extend(('--admin-device', admin_device))
    if operation != 'inspect':
        tokens += ['--execute-mutations', '--yes-i-know-this-mutates-seh']
    return existing._parser().parse_args(tokens)


def seh_cycle(*, expected_uuid: str, state_root: Path, local_port: int = 8897,
              admin_device: str | None = None, cycle_active: bool = False,
              invoke: Callable[[object], dict] | None = None,
              event: Callable[[str, dict], None] | None = None,
              authorize_mutations: bool = False) -> dict:
    """Real canonical SEH mutations with pinned target, mandatory result checks.

    Existing active deployment requires explicit --cycle-active-seh because its
    Coolify application must be destroyed and recreated. On partial failure,
    never blindly delete/replace the application while identity is ambiguous.
    """
    require(authorize_mutations, 'EXPLICIT_SEH_MUTATION_APPROVAL_REQUIRED')
    import re
    import seh_mutate_harness as existing
    record = event or (lambda stage, data: None)
    call = invoke or existing.run
    require(isinstance(expected_uuid, str) and
            re.fullmatch(r'[A-Za-z0-9_-]{8,64}', expected_uuid) is not None,
            'EXPECTED_UUID_REQUIRED')
    require(1 <= local_port <= 65535, 'INVALID_PORT')

    def perform(op: str) -> dict:
        receipt = call(seh_arguments(op, root=state_root, local_port=local_port,
                                     admin_device=admin_device))
        require(isinstance(receipt, dict), 'INVALID_SEH_RECEIPT')
        # Record at every irreversible boundary. This callback stores no tokens.
        record('seh.' + op, {'status': receipt.get('status'),
                             'application_uuid': receipt.get('application_uuid')})
        return receipt

    first = perform('inspect')
    require(first.get('status') == 'READ_ONLY' and first.get('network') == 'mainnet'
            and first.get('registered') is True, 'CANONICAL_TARGET_UNVERIFIED')
    deployment = first.get('deployment') or {}
    require(isinstance(deployment, dict) and
            deployment.get('application_uuid') == expected_uuid, 'SEH_UUID_MISMATCH')
    initially_undeployed = deployment.get('undeployed') is True
    require(initially_undeployed or cycle_active,
            'ACTIVE_SEH_CYCLE_REQUIRES_EXPLICIT_OUTAGE_CONSENT')
    phases = []
    current_uuid = expected_uuid
    try:
        if not initially_undeployed:
            deleted = perform('undeploy-seh')
            require(deleted.get('status') == 'SEH_UNDEPLOYED' and
                    deleted.get('application_removed_verified') is True,
                    'INITIAL_UNDEPLOY_NOT_VERIFIED')
            after_undeploy = perform('inspect')
            require(after_undeploy.get('deployment', {}).get('undeployed') is True and
                    after_undeploy['deployment'].get('application_uuid') == expected_uuid,
                    'INITIAL_TOMBSTONE_UNVERIFIED')
            phases.append('undeploy_existing')
        deployed = perform('deploy-seh')
        current_uuid = deployed.get('application_uuid')
        require(deployed.get('status') == 'CONNECTED_VERIFIED' and
                deployed.get('deployment_remote_mutation') is True and
                isinstance(current_uuid, str) and current_uuid,
                'LIVE_DEPLOY_OR_CONNECTION_NOT_VERIFIED')
        live_state = perform('inspect')
        require(live_state.get('deployment', {}).get('application_uuid') == current_uuid and
                live_state['deployment'].get('undeployed') is not True,
                'DEPLOYED_RECORD_NOT_VERIFIED')
        phases.append('deploy_and_activate')
        if initially_undeployed:
            deleted = perform('undeploy-seh')
            require(deleted.get('status') == 'SEH_UNDEPLOYED' and
                    deleted.get('application_removed_verified') is True,
                    'FINAL_UNDEPLOY_NOT_VERIFIED')
            finish = perform('inspect')
            require(finish.get('deployment', {}).get('application_uuid') == current_uuid and
                    finish['deployment'].get('undeployed') is True,
                    'FINAL_TOMBSTONE_UNVERIFIED')
            phases.append('undeploy_and_verify_tombstone')
        else:
            phases.append('restored_deployed_state_with_new_coolify_uuid')
        return {'status': 'PASS', 'lane': 'live-mainnet-coolify-mutation',
                'initial_uuid': expected_uuid, 'final_uuid': current_uuid,
                'initially_undeployed': initially_undeployed, 'phases': phases}
    except BaseException as exc:
        # Inspect for diagnosis only: on an unknown partial remote mutation the
        # tool must not destroy any deployment as an alleged "cleanup".
        result = {'status': 'INCOMPLETE', 'lane': 'live-mainnet-coolify-mutation',
                  'initial_uuid': expected_uuid, 'last_known_uuid': current_uuid,
                  'phases': phases,
                  'failed_gate': str(exc) if isinstance(exc, AcceptanceError) else type(exc).__name__,
                  'automatic_remote_cleanup': 'REFUSED_ON_UNVERIFIED_STATE'}
        try:
            observed = perform('inspect')
            result['observed_uuid'] = (observed.get('deployment') or {}).get('application_uuid')
            result['observed_undeployed'] = (observed.get('deployment') or {}).get('undeployed')
        except Exception:
            result['inspection'] = 'UNAVAILABLE'
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--mode', choices=('isolated', 'preflight', 'live'), default='preflight')
    parser.add_argument('--scope', choices=('identities', 'seh', 'all'), default='all')
    parser.add_argument('--admin-device', help='Optional explicit P-256 administrator device; otherwise discover a verified local admin')
    parser.add_argument('--identity-dir', type=Path,
                        help='Explicit legacy key directory for fixtures; otherwise use running LMS-SLH Authenticator')
    parser.add_argument('--local-port', type=int, default=8897)
    parser.add_argument('--network', choices=('mainnet',), help='Explicit canonical network for real SEH mutation')
    parser.add_argument('--expect-seh-uuid', help='Exact existing canonical UUID from read-only inspection')
    parser.add_argument('--cycle-active-seh', action='store_true',
                        help='Explicitly permit outage and replacement of a currently deployed SEH')
    parser.add_argument('--execute-mutations', action='store_true')
    parser.add_argument('--acknowledge-real-mutations', action='store_true')
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    if not 1 <= args.local_port <= 65535:
        parser.error('invalid local port')
    if args.mode == 'live':
        if not (args.execute_mutations and args.acknowledge_real_mutations):
            parser.error('live requires BOTH --execute-mutations and --acknowledge-real-mutations')
        if args.scope in ('seh', 'all') and (args.network != 'mainnet' or not args.expect_seh_uuid):
            parser.error('live SEH mutation requires --network mainnet and --expect-seh-uuid')
    elif args.execute_mutations or args.acknowledge_real_mutations or args.cycle_active_seh:
        parser.error('mutation approvals and active-SEH cycling are permitted in live mode only')
    if args.scope not in ('seh', 'all') and (args.expect_seh_uuid or args.network):
        parser.error('SEH target fields require --scope seh or --scope all')
    if args.mode == 'isolated' and args.scope == 'seh':
        parser.error('isolated mode cannot certify real SEH deployment; use live with a verified Coolify target')
    if args.scope == 'identities' and args.cycle_active_seh:
        parser.error('--cycle-active-seh requires SEH scope')
    if args.scope in ('seh', 'all') and args.network not in (None, 'mainnet'):
        parser.error('canonical SEH only supports mainnet')

    target = args.report if args.report.is_absolute() else ROOT / args.report
    target.parent.mkdir(parents=True, exist_ok=True)
    output = {'schema': 'mcel.slh.dangerous-functional.v1',
              'mode': args.mode, 'scope': args.scope,
              'safety': {'real_mutation_approval': args.mode == 'live',
                         'nonowned_identity_mutation': 'FORBIDDEN',
                         'blind_seh_cleanup': 'FORBIDDEN'},
              'groups': {}, 'events': [], 'status': 'INCOMPLETE'}

    def checkpoint() -> None:
        target.write_text(json.dumps(output, sort_keys=True, indent=2) + '\n', encoding='utf-8')

    def event(stage: str, details: dict) -> None:
        # Preserve a non-secret recovery trail before another consequential step.
        output['events'].append({'stage': stage, **details})
        checkpoint()
        print('SLH_DANGEROUS', stage, details.get('status', ''), flush=True)

    checkpoint()
    try:
        if args.mode == 'isolated':
            with tempfile.TemporaryDirectory(prefix='mcel-slh-real-http-') as tmp:
                state = Path(tmp) / 'authority'
                identities = Path(tmp) / 'initial-admin'
                service_identity(state, create=True)
                save_identity(identities, 'slh-acceptance-root')
                authority = SLHAuthority(state)
                api = SLHAPI(authority, local_authenticator=LocalAuthenticatorAPI(Path(tmp) / 'authenticator'))
                server = make_server('127.0.0.1', 0, api.wsgi_app, handler_class=_QuietHandler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    bootstrap_admin(port=server.server_port, state_dir=state,
                                    identity_dir=identities, device_id='slh-acceptance-root',
                                    admin_id='slh-acceptance-root-admin',
                                    admin_name='Disposable Acceptance Root')
                    output['groups']['identities'] = identity_cycle(
                        port=server.server_port, admin_identity_dir=identities,
                        admin_device='slh-acceptance-root', event=event,
                        authorize_mutations=True)
                    output['groups']['canonical-local-authenticator'] = canonical_local_identity_cycle(
                        port=server.server_port, admin_identity_dir=identities,
                        admin_device='slh-acceptance-root', event=event,
                        disposable_authority=True)
                    # On disposable authority only: prove the final administrator
                    # cannot be revoked after all test identities are removed.
                    token, who = _open_authenticated_session(server.server_port, identities, 'slh-acceptance-root')
                    try:
                        require(who.get('role') == 'admin', 'DISPOSABLE_ROOT_MISSING')
                        expect_refusal(
                            lambda: _request(server.server_port, 'POST',
                                '/v1/admin/devices/slh-acceptance-root/revoke', {}, token=token),
                            ('LAST_ADMIN_PROTECTED',), 'LAST_ADMIN_SAFETY')
                    finally:
                        _request(server.server_port, 'POST', '/v1/auth/logout', {}, token=token)
                    output['groups']['last-admin-protection'] = {
                        'status': 'PASS', 'lane': 'isolated-real-http'}
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
                    server.server_close()
            output['groups']['seh'] = {'status': 'NOT_RUN',
                'reason': 'Real Coolify deployment requires explicit live target; no simulated success substituted'}
        elif args.mode == 'preflight':
            if args.scope in ('seh', 'all'):
                import seh_mutate_harness
                inspection = seh_mutate_harness.run(seh_arguments('inspect', root=ROOT / 'runtime/state',
                                                                  local_port=args.local_port,
                                                                  admin_device=args.admin_device))
                output['groups']['seh'] = {'status': 'READ_ONLY',
                     'network': inspection.get('network'), 'registered': inspection.get('registered'),
                     'application_uuid': (inspection.get('deployment') or {}).get('application_uuid'),
                     'undeployed': (inspection.get('deployment') or {}).get('undeployed'),
                     'inspection_status': inspection.get('status')}
            if args.scope in ('identities', 'all'):
                status = _request(args.local_port, 'GET', '/v1/bootstrap/status')
                output['groups']['identities'] = {'status': 'READ_ONLY',
                                                  'initialized': status.get('initialized') is True}
        else:
            # Resolve the administrator only AFTER both explicit live-mutation
            # approvals are present. No inference from a key name or label.
            # Explicit --admin-device retains its original meaning.
            admin_device = args.admin_device
            if args.scope in ('identities', 'all') and admin_device is None:
                admin_device = discover_admin_device(
                    port=args.local_port, identity_dir=args.identity_dir,
                    source='filesystem' if args.identity_dir is not None else 'lms')
                output['administrator_discovery'] = {
                    'status': 'VERIFIED', 'device_id': admin_device,
                    'basis': ('explicit-filesystem-signed-session-and-admin-directory'
                              if args.identity_dir is not None else
                              'lms-local-authenticator-signed-session-and-admin-directory')}
                checkpoint()
            # Isolated identity failures block SEH work in --scope all.
            if args.scope in ('identities', 'all'):
                output['groups']['identities'] = identity_cycle(
                    port=args.local_port, admin_identity_dir=args.identity_dir,
                    admin_device=admin_device,
                    admin_source='filesystem' if args.identity_dir is not None else 'lms',
                    event=event,
                    authorize_mutations=True)
                checkpoint()
                require(output['groups']['identities']['status'] == 'PASS',
                        'IDENTITY_CYCLE_UNVERIFIED_REFUSING_SEH')
            if args.scope in ('seh', 'all'):
                try:
                    output['groups']['seh'] = seh_cycle(
                        expected_uuid=args.expect_seh_uuid,
                        state_root=ROOT / 'runtime/state', local_port=args.local_port,
                        admin_device=admin_device, cycle_active=args.cycle_active_seh,
                        event=event, authorize_mutations=True)
                except (AcceptanceError, RuntimeError, OSError, ValueError) as exc:
                    output['groups']['seh'] = {
                        'status': 'INCOMPLETE',
                        'failed_gate': (str(exc) if isinstance(exc, AcceptanceError)
                                        else type(exc).__name__),
                    }
                    raise
        statuses = [group['status'] for group in output['groups'].values()]
        output['status'] = ('READ_ONLY' if args.mode == 'preflight' else
                            'PASS_ISOLATED_IDENTITY' if args.mode == 'isolated' and
                            all(output['groups'][k]['status'] == 'PASS' for k in ('identities', 'canonical-local-authenticator', 'last-admin-protection')) else
                            'PASS_LIVE_MUTATIONS' if args.mode == 'live' and
                            statuses and all(s == 'PASS' for s in statuses) else 'INCOMPLETE')
    except (AcceptanceError, RuntimeError, OSError, ValueError) as exc:
        output['status'] = 'INCOMPLETE'
        output['failed_gate'] = str(exc) if isinstance(exc, AcceptanceError) else type(exc).__name__
    except KeyboardInterrupt:
        output['status'] = 'INCOMPLETE'
        output['failed_gate'] = 'INTERRUPTED_CHECK_REPORT_FOR_PARTIAL_MUTATIONS'
    finally:
        checkpoint()
    print('SLH_DANGEROUS_OVERALL', output['status'],
          {name: group['status'] for name, group in output['groups'].items()},
          'report:', str(target), flush=True)
    return 0 if output['status'] in ('READ_ONLY', 'PASS_ISOLATED_IDENTITY', 'PASS_LIVE_MUTATIONS') else 4


if __name__ == '__main__':
    raise SystemExit(main())
