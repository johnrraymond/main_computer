"""Exercise the real SLH New Device -> Admin Approval -> Known Device HTTP flow.

Default: isolated, temporary SLH server and fake organizational identities.
Live: explicitly authorized changes to the running loopback SLH (port 8897);
      test worker devices are revoked and their test keys removed by default.

  python -m tools.slh_enrollment_lifecycle verify-isolated
  python -m tools.slh_enrollment_lifecycle verify-live --admin-device admin-pc --execute-mutations

The harness never prints private keys, bearer tokens, or one-time enrollment keys.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
from wsgiref.simple_server import make_server

from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator import public_key_fingerprint, sign_object
from main_computer.slh.ports import DEFAULT_SLH_API_PORT
from tools.slh_authenticator_cli import (
    _default_auth_dir, _request, bootstrap_admin, identity_file, load_identity, save_identity,
)


def _check(condition, gate):
    if not condition:
        raise RuntimeError('SLH_ENROLLMENT_GATE_FAILED: ' + gate)


def _expected_refusal(fn, expected):
    try:
        fn()
    except RuntimeError as exc:
        if '(' + expected + ')' in str(exc):
            return True
        raise
    return False


def _open_authenticated_session(port, identity_dir, device_id):
    private, _ = load_identity(identity_dir, device_id)
    challenge = _request(port, 'POST', '/v1/auth/challenge', {'device_id': device_id})
    result = _request(port, 'POST', '/v1/auth/verify', {
        'device_id': device_id, 'challenge_id': challenge['challenge_id'],
        'signature': sign_object(private, challenge),
    })
    token = result['access_token']
    return token, _request(port, 'GET', '/v1/auth/session', token=token)


def _submit_worker(port, identity_dir, org_id, selected_admin, label):
    device_id = 'slh-test-' + label + '-' + secrets.token_hex(5)
    saved = save_identity(identity_dir, device_id)
    key, public = load_identity(identity_dir, device_id)
    body = {
        'selected_admin': selected_admin, 'device_id': device_id,
        'public_key': public, 'kind': 'worker',
        'device_name': 'Lifecycle Test ' + label,
        'claimed_user': 'SLH Lifecycle Test', 'requested_role': 'worker',
        'client_nonce': secrets.token_urlsafe(32),
    }
    statement = {**body, 'version': 1,
                 'action': 'slh.device.enrollment.request', 'org_id': org_id}
    body['proof'] = sign_object(key, statement)
    response = _request(port, 'POST', '/v1/enrollment/requests', body)
    return {'device_id': device_id, 'request_id': response['request_id'],
            'fingerprint': saved['fingerprint'], 'key_path': saved['identity_file'],
            'approval_method': label}


def verify_lifecycle(*, port, identity_dir, admin_device, retain_devices=False):
    """Run both approval paths through the actual HTTP API; clean up by default."""
    _check(_request(port, 'GET', '/v1/bootstrap/status')['initialized'], 'admin_bootstrapped')
    admin_token, admin_identity = _open_authenticated_session(port, identity_dir, admin_device)
    approved, requests, local_key_paths = [], [], []
    outcomes = []
    cleanup_errors = []
    try:
        _check(admin_identity['role'] == 'admin', 'admin_session_required')
        admin_id = admin_identity['user_id']
        admins = _request(port, 'GET', '/v1/enrollment/admins')['admins']
        _check(any(a['id'] == admin_id for a in admins), 'admin_in_enrollment_directory')
        policy = _request(port, 'GET', '/v1/admin/policies', token=admin_token)
        if policy['physical_inspection_required']:
            raise RuntimeError('PHYSICAL_INSPECTION_REQUIRED: refusing automated live enrollment; '
                               'an administrator must inspect and attest each device')
        org_id = _request(port, 'GET', '/v1/bootstrap/status')['organization_id']

        for mode in ('queue', 'key'):
            record = _submit_worker(port, identity_dir, org_id, admin_id, mode)
            local_key_paths.append(record['key_path'])
            requests.append(record['request_id'])
            rid, device_id = record['request_id'], record['device_id']
            state = _request(port, 'GET', '/v1/enrollment/requests/' + rid + '/status')
            _check(state['status'] == 'pending', mode + '_pending')
            current = _request(port, 'GET', '/v1/admin/enrollments', token=admin_token)['enrollments']
            _check(any(r['id'] == rid and r['status'] == 'pending' for r in current), mode + '_queue_visible')
            _check(_expected_refusal(lambda: _request(port,'POST','/v1/auth/challenge',
                                          {'device_id':device_id}), 'UNKNOWN_OR_REVOKED_DEVICE'),
                   mode + '_cannot_login_before_approval')

            approval_body = {'request_id': rid}
            if mode == 'key':
                issued = _request(port, 'POST', '/v1/admin/enrollments/' + rid + '/keys',
                                  {'ttl_seconds': 300}, token=admin_token)
                approval_body['enrollment_key'] = issued['enrollment_key']
                # Key authorization goes through precisely the same approval endpoint.
                approval = _request(port, 'POST', '/v1/enrollment/approvals', approval_body)
            else:
                approval = _request(port, 'POST', '/v1/enrollment/approvals',
                                    approval_body, token=admin_token)
            _check(approval['status'] == 'APPROVED', mode + '_approved')
            _check(approval['authorized_by'] == admin_id, mode + '_authorized_by_admin')
            approved.append(device_id)

            if mode == 'key':
                _check(_expected_refusal(lambda: _request(port,'POST','/v1/enrollment/approvals',approval_body),
                                         'NOT_PENDING'), 'single_use_replay_denied')

            worker_token, worker_identity = _open_authenticated_session(port, identity_dir, device_id)
            try:
                _check(worker_identity['active'] and worker_identity['role'] == 'worker',
                       mode + '_known_device_login')
                _check(_expected_refusal(lambda: _request(port,'GET','/v1/admin/devices',token=worker_token),
                                         'ADMIN_REQUIRED'), mode + '_no_admin_authority')
            finally:
                _request(port, 'POST', '/v1/auth/logout', {}, token=worker_token)
            registered = _request(port, 'GET', '/v1/admin/devices', token=admin_token)['devices']
            _check(any(d['id'] == device_id and d['fingerprint'] == record['fingerprint']
                       and d['status'] == 'active' for d in registered), mode + '_registered_key_matches')
            outcomes.append({'approval': mode, 'device_id': device_id,
                             'known_device_login': 'PASS', 'admin_privilege_denied': True,
                             'authorized_by': admin_id})

        result = {
            'schema': 'main-computer.slh-enrollment-lifecycle.v1',
            'status': 'PASS', 'mode': 'live-http',
            'org_id': org_id, 'administrator': admin_id,
            'single_approval_endpoint': '/v1/enrollment/approvals',
            'devices': outcomes,
            'cleanup': 'retained' if retain_devices else 'pending_verification',
        }
    finally:
        # Make a best effort to retire all simulated devices and pending requests,
        # even if one test gate fails. Never touch the real administrator identity.
        if not retain_devices:
            for device_id in approved:
                try:
                    _request(port, 'POST', '/v1/admin/devices/' + device_id + '/revoke',
                             {}, token=admin_token)
                except RuntimeError as exc:
                    cleanup_errors.append('revoke:' + device_id + ':' + str(exc))
            for rid in requests:
                try:
                    state = _request(port, 'GET', '/v1/enrollment/requests/' + rid + '/status')
                    if state['status'] == 'pending':
                        _request(port, 'POST', '/v1/admin/enrollments/' + rid + '/deny',
                                 {}, token=admin_token)
                except RuntimeError as exc:
                    cleanup_errors.append('deny:' + rid + ':' + str(exc))
            # Only discard test keys when their corresponding device is known revoked/denied.
            try:
                active = {d['id'] for d in _request(port,'GET','/v1/admin/devices',token=admin_token)['devices']
                          if d['status']=='active'}
                if any(device_id in active for device_id in approved):
                    cleanup_errors.append('test_device_still_active')
                for path in local_key_paths:
                    if Path(path).name.removesuffix('.key.pem') not in active:
                        Path(path).unlink(missing_ok=True)
            except (RuntimeError, OSError) as exc:
                cleanup_errors.append('key_cleanup:' + str(exc))
        try:
            _request(port, 'POST', '/v1/auth/logout', {}, token=admin_token)
        except RuntimeError:
            pass
    if cleanup_errors:
        raise RuntimeError('LIFECYCLE_CLEANUP_FAILED: ' + '; '.join(cleanup_errors))
    if not retain_devices:
        result['cleanup'] = 'verified_revoked'
    return result


def verify_isolated():
    with tempfile.TemporaryDirectory(prefix='slh-enrollment-cycle-') as root:
        directory=Path(root)
        identities=directory/'identities'
        state=directory/'server'
        save_identity(identities,'admin-test')
        server = make_server('127.0.0.1', 0, SLHAPI(SLHAuthority(state)).wsgi_app)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            boot = bootstrap_admin(port=server.server_port, state_dir=state,
                                   identity_dir=identities, device_id='admin-test',
                                   org_id='slh-isolated-test', admin_id='test-admin',
                                   admin_name='Test Administrator')
            _check(boot['status']=='INITIALIZED','bootstrap')
            result=verify_lifecycle(port=server.server_port,identity_dir=identities,
                                    admin_device='admin-test')
            result['mode']='isolated-local-http'
            # Inspect the post-run durable state, proving that no test identities remain active.
            auth=SLHAuthority(state)
            for outcome in result['devices']:
                matching=[x for x in auth.devices() if x['id']==outcome['device_id']]
                _check(len(matching)==1 and matching[0]['status']=='revoked',
                       'cleanup_revoked_'+outcome['approval'])
            result['cleanup']='verified_revoked'
            return result
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('verify-isolated')
    live=sub.add_parser('verify-live')
    live.add_argument('--admin-device',default='admin-pc')
    live.add_argument('--identity-dir',type=Path,default=_default_auth_dir())
    live.add_argument('--port',type=int,default=DEFAULT_SLH_API_PORT,
                      help='Canonical 8897 by default; override for isolated lab only')
    live.add_argument('--execute-mutations',action='store_true',
                      help='Required: create two real audit records and revoke the test devices afterward')
    live.add_argument('--retain-devices',action='store_true',
                      help='Keep the enrolled test devices and their local keys after success')
    args=parser.parse_args(argv)
    try:
        if args.command=='verify-isolated':
            result=verify_isolated()
        else:
            if not args.execute_mutations:
                raise RuntimeError('LIVE_MUTATION_NOT_AUTHORIZED: specify --execute-mutations')
            result=verify_lifecycle(port=args.port,identity_dir=args.identity_dir,
                                    admin_device=args.admin_device, retain_devices=args.retain_devices)
    except (RuntimeError,ValueError,OSError) as exc:
        print(json.dumps({'status':'FAIL','reason':str(exc)},sort_keys=True),file=sys.stderr)
        return 1
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
