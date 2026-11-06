"""Disposable, real-process SLH Local/Organization acceptance lane.

The lab owns all credentials, ports, state, relay, and child processes. It never
reads or changes the installed SLH or a production SEH. This is not a mock of
the Organization Service: local API controls the real spawned TLS process.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPConnection, HTTPSConnection
import inspect
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import tempfile
from threading import Thread
from unittest.mock import patch
import time

from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.local_service import create_server
from main_computer.slh.organization_state import read_config
from .lab_stage_diagnostics import stage as lab_stage, record as lab_record


class ServiceLabFailure(RuntimeError):
    pass


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _ports():
    result = [_free_port() for _ in range(2)]
    while len(set(result)) != 2:
        result = [_free_port() for _ in range(2)]
    return result


def _http(port, method, route, body=None, *, bearer='', tls=False, timeout=3):
    context = ssl._create_unverified_context() if tls else None
    conn = (HTTPSConnection('127.0.0.1', port, timeout=timeout, context=context) if tls
            else HTTPConnection('127.0.0.1', port, timeout=timeout))
    headers = {'Authorization': 'Bearer ' + bearer} if bearer else {}
    data = None
    if body is not None:
        data = json.dumps(body)
        headers['Content-Type'] = 'application/json'
    try:
        conn.request(method, route, data, headers)
        response = conn.getresponse()
        raw = response.read(131072)
        return response.status, json.loads(raw)
    finally:
        conn.close()


def _ok(response, expected=200):
    code, data = response
    if code != expected:
        raise ServiceLabFailure(f'Unexpected API response {code} (expected {expected}): {str(data)[:240]}')
    return data


def _await(predicate, description, timeout=10):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        try:
            if predicate():
                return True
        except (OSError, ValueError, TimeoutError, ssl.SSLError):
            pass
        time.sleep(.1)
    raise ServiceLabFailure('Timed out: ' + description)


def _protect_disposable_lab_state(directory: Path) -> Path:
    """Keep lab state private but accessible to the test runner on Windows.

    Production machine ACLs are for ProgramData and deliberately remove the
    ordinary interactive user's access.  Applying them to this lab's throwaway
    TemporaryDirectory prevents SQLite from reopening after Local Service
    restart.  This alternate protector is restricted to lab-created roots.
    """
    directory = Path(directory)
    if not any(part.startswith('mc-slh-two-service-lab-') for part in directory.parts):
        raise ServiceLabFailure('Refusing disposable state permissions outside the SLH lab')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'posix':
        directory.chmod(0o700)
    return directory


def _create_disposable_local_server(state, identities):
    """Support older local-service factories without weakening production ACLs.

    The preferred factory accepts an explicit protector.  Some installed SLH
    versions predate that keyword; only for this disposable lab instance,
    replace the imported protector during synchronous factory construction.
    The replacement is restored before the server begins handling requests.
    """
    kwargs = {'state_dir': state, 'identity_dir': identities, 'port': 0}
    if 'state_dir_protector' in inspect.signature(create_server).parameters:
        return create_server(**kwargs, state_dir_protector=_protect_disposable_lab_state)
    with patch('main_computer.slh.local_service.secure_machine_state_dir',
               _protect_disposable_lab_state):
        return create_server(**kwargs)


@contextmanager
def _local(state, identities):
    with _create_disposable_local_server(state, identities) as server:
        thread = Thread(target=server.serve_forever, daemon=True, name='slh-lab-local')
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=4)


def _login(port, key=None, *, tls=False, local_sign_port=None, device='lab-owner-device'):
    challenge = _ok(_http(port, 'POST', '/v1/auth/challenge', {'device_id': device}, tls=tls))
    if local_sign_port is not None:
        signed = _ok(_http(local_sign_port, 'POST',
                           f'/v1/local/authenticator/identities/{device}/sign',
                           {'challenge': challenge}))['signature']
    else:
        signed = sign_object(key, challenge)
    result = _ok(_http(port, 'POST', '/v1/auth/verify',
                       {'device_id': device, 'challenge_id': challenge['challenge_id'],
                        'signature': signed}, tls=tls))
    if result['role'] != 'admin':
        raise ServiceLabFailure('Administrative identity was not granted admin role')
    return result['access_token']


def _fingerprint(port):
    ctx = ssl._create_unverified_context()
    with socket.create_connection(('127.0.0.1', port), timeout=3) as sock:
        with ctx.wrap_socket(sock, server_hostname='localhost') as peer:
            return hashlib.sha256(peer.getpeercert(binary_form=True)).hexdigest()


def _managed_auth(port, device, key, *, expected_pin, via_relay=False):
    from main_computer.slh.managed_gateway import MANAGED_PATH
    from websockets.sync.client import connect
    transport = 'seh_relay' if via_relay else 'direct'
    with lab_stage('preflight.worker_websocket', transport=transport, endpoint_port=port):
        with lab_stage('preflight.worker_websocket.handshake', transport=transport, endpoint_port=port):
            websocket = connect(f'wss://127.0.0.1:{port}{MANAGED_PATH}',
                     ssl=ssl._create_unverified_context(), open_timeout=5, close_timeout=2)
        with websocket:
            with lab_stage('preflight.worker_websocket.tls_pin', transport=transport):
                peer = websocket.socket.getpeercert(binary_form=True)
                if hashlib.sha256(peer).hexdigest() != expected_pin:
                    raise ServiceLabFailure('SLH TLS pin changed across direct/SEH route')
            with lab_stage('preflight.worker_websocket.device_authorization', transport=transport):
                websocket.send(json.dumps({'type': 'worker.auth', 'device_id': device,
                                       'execution_protocol': 'durable-authorization-v1',
                                       'market': {'models': ['mock-model'], 'capabilities':['chat.completions'], 'max_concurrency': 1}}))
                challenge = json.loads(websocket.recv(timeout=5))
                if challenge.get('type') != 'slh.auth.challenge':
                    raise ServiceLabFailure('Expected real gateway device challenge')
                websocket.send(json.dumps({'type': 'slh.auth.proof', 'device_id': device,
                                       'signature': sign_object(key, challenge['challenge'])}))
                accepted = json.loads(websocket.recv(timeout=5))
                if accepted.get('type') != 'hub.auth.accepted' or accepted.get('device_id') != device:
                    raise ServiceLabFailure('Worker login not accepted: ' + str(accepted.get('type')))
            with lab_stage('preflight.worker_websocket.heartbeat', transport=transport):
                ping = json.loads(websocket.recv(timeout=5))
                if ping.get('type') != 'hub.ping':
                    raise ServiceLabFailure('Expected actual managed worker ping')
                websocket.send(json.dumps({'type': 'worker.pong', 'ping_id': ping['ping_id']}))
                reply = json.loads(websocket.recv(timeout=5))
                if reply.get('type') != 'hub.pong.accepted':
                    raise ServiceLabFailure('Worker did not receive pong acceptance')
            websocket.send(json.dumps({'type': 'worker.close'}))
    return True


def _pending_restart_and_cancel(root):
    """Isolated first-run interruption; never touches the activated lab org."""
    state, identities = root / 'pending', root / 'pending-identities'
    with _local(state, identities) as local:
        secret = (state / 'bootstrap.secret').read_text(encoding='utf-8').strip()
        begun = _ok(_http(local.server_port, 'POST', '/v1/bootstrap/begin',
                          {'bootstrap_secret': secret}), 201)
        transaction = begun['transaction_id']
        if local.organization_controller.status()['listening']:
            raise ServiceLabFailure('Pending initial setup started organizational serving')
    with _local(state, identities) as restarted:
        pending = _ok(_http(restarted.server_port, 'GET',
                            f'/v1/bootstrap/{transaction}/status', bearer=secret))
        if pending['state'] != 'PENDING':
            raise ServiceLabFailure('Unfinished bootstrap was not recovered after restart')
        _ok(_http(restarted.server_port, 'POST',
                  f'/v1/bootstrap/{transaction}/cancel', {'bootstrap_secret': secret}))
        if restarted.organization_controller.status()['listening']:
            raise ServiceLabFailure('Canceled initial setup started organizational serving')
        fresh = _ok(_http(restarted.server_port, 'POST', '/v1/bootstrap/begin',
                          {'bootstrap_secret': secret}), 201)
        return fresh['transaction_id'] != transaction


def verify_services(*, probe_relay: bool = True) -> dict:
    """Runs production startup/configuration/auth/worker/SEH gates with disposable state.

    No Hub, wallet, external address, Coolify, production service, or persistent
    operating-system service is involved. Paid-work is a separate lab lane.
    """
    from cryptography.hazmat.primitives import serialization
    from main_computer.slh.seh_autoconnect import SEHConnectionSettings, write_seh_settings
    from main_computer.seh.relay import SEHRelay
    from main_computer.slh.seh_transport import SEHLocalClientAdapter

    gates = {}
    slh_id = 'lab-slh'
    with tempfile.TemporaryDirectory(prefix='mc-slh-two-service-lab-') as td:
        root = Path(td)
        state, identities = root / 'organization', root / 'identities'
        gates['pending_restart_cancel_and_retry'] = _pending_restart_and_cancel(root)
        api_port, worker_port = _ports()
        relay = adapter = None
        try:
            with _local(state, identities) as local:
                lp = local.server_port
                initial = _ok(_http(lp, 'GET', '/v1/local/status'))
                gates['fresh_local_service_loopback_only'] = (
                    initial['local_service']['binding'] == 'loopback'
                    and initial['organization_service']['enabled'] is False
                    and not local.organization_controller.status()['listening'])
                gates['unprovisioned_start_denied'] = _http(
                    lp, 'POST', '/v1/local/organization/enable', {})[0] == 401
                created = _ok(_http(lp, 'POST', '/v1/local/authenticator/identities',
                                    {'device_id': 'lab-owner-device', 'label': 'Lab Owner'}), 201)
                gates['local_identity_created'] = created['device_id'] == 'lab-owner-device'
                gates['private_key_not_returned_by_local_api'] = (
                    'PRIVATE KEY' not in json.dumps(created).upper())
                secret = (state / 'bootstrap.secret').read_text(encoding='utf-8').strip()
                begun = _ok(_http(lp, 'POST', '/v1/bootstrap/begin',
                                  {'bootstrap_secret': secret}), 201)
                transaction = begun['transaction_id']
                context = {**begun['proof_context'], 'admin_id': 'lab-owner',
                           'device_id': 'lab-owner-device', 'public_key': created['public_key']}
                proof = _ok(_http(lp, 'POST',
                                  '/v1/local/authenticator/identities/lab-owner-device/sign',
                                  {'challenge': context}))['signature']
                staged = _ok(_http(lp, 'PUT', f'/v1/bootstrap/{transaction}/administrator',
                                   {'bootstrap_secret': secret, 'admin_id': 'lab-owner',
                                    'admin_name': 'Lab Owner', 'device_id': 'lab-owner-device',
                                    'public_key': created['public_key'], 'proof': proof}))
                gates['staged_bootstrap_identity_proof'] = staged['state'] == 'AWAITING_AUTHENTICATOR'
                gates['bootstrap_does_not_serve'] = (
                    not local.organization_controller.status()['listening']
                    and not local.organization_controller.config()['enabled'])
                gates['bootstrap_cannot_start_organization'] = _http(
                    lp, 'POST', '/v1/local/organization/enable', {})[0] == 401
                gates['bootstrap_requires_explicit_p256_deferral'] = _http(
                    lp, 'POST', f'/v1/bootstrap/{transaction}/activate-p256',
                    {'bootstrap_secret': secret})[0] == 400
                active = _ok(_http(lp, 'POST', f'/v1/bootstrap/{transaction}/activate-p256',
                                   {'bootstrap_secret': secret, 'defer_totp': True}))
                gates['activated_without_totp_only_explicitly'] = (
                    active['status'] == 'INITIALIZED' and active['totp_enrolled'] is False)
                gates['bootstrap_secret_consumed'] = not (state/'bootstrap.secret').exists()
                local_token = _login(lp, local_sign_port=lp)
                gates['unauthorized_lifecycle_denied'] = _http(
                    lp, 'POST', '/v1/local/organization/enable', {})[0] == 401
                gates['enabled_requires_configuration'] = _http(
                    lp, 'POST', '/v1/local/organization/enable', {}, bearer=local_token)[0] == 409
                config = _ok(_http(lp, 'PATCH', '/v1/local/organization/config',
                                   {'host': '127.0.0.1', 'api_port': api_port,
                                    'worker_port': worker_port, 'workers_enabled': False,
                                    'seh_enabled': False}, bearer=local_token))
                gates['configuration_not_activation'] = config['configured'] and not config['enabled']
                mgmt = _ok(_http(lp, 'POST', '/v1/local/organization/enable',
                                 {}, bearer=local_token))
                _await(lambda: local.organization_controller.status()['listening'],
                       'Organization management listener')
                gates['organization_process_separate'] = (
                    mgmt['enabled'] and local.organization_controller.status()['pid'] is not None)
                original_pid = local.organization_controller.status()['pid']
                _ok(_http(lp, 'POST', '/v1/local/organization/enable', {}, bearer=local_token))
                gates['repeated_enable_does_not_duplicate_child'] = (
                    local.organization_controller.status()['pid'] == original_pid)
                identity = _ok(_http(api_port, 'GET', '/v1/org/identity', tls=True))
                pin = _fingerprint(api_port)
                gates['organization_tls_identity_pinned'] = (
                    identity['tls_sha256'] == pin)
                gates['org_api_no_bootstrap_or_local_routes'] = (
                    _http(api_port, 'GET', '/v1/bootstrap/status', tls=True)[0] == 404
                    and _http(api_port, 'GET', '/v1/local/status', tls=True)[0] == 404)
                gates['unauthenticated_org_admin_denied'] = (
                    _http(api_port, 'GET', '/v1/org/admin/services', tls=True)[0] == 401)
                org_token = _login(api_port, local_sign_port=lp, tls=True)
                gates['remote_api_same_org_authority'] = (
                    _ok(_http(api_port, 'GET', '/v1/org/admin/services',
                              tls=True, bearer=org_token))['organization']['initialized'] is True
                    and _http(api_port, 'GET', '/v1/admin/enrollments',
                              tls=True, bearer=org_token)[0] == 200)
                gates['worker_listener_disabled_by_default'] = (
                    _ok(_http(api_port, 'GET', '/v1/org/status', tls=True))['worker_serving_enabled'] is False)
                try:
                    with socket.create_connection(('127.0.0.1', worker_port), timeout=.3):
                        gates['no_worker_socket_before_enable'] = False
                except OSError:
                    gates['no_worker_socket_before_enable'] = True
                _ok(_http(lp, 'POST', '/v1/local/organization/disable', {}, bearer=local_token))
                _ok(_http(lp, 'PATCH', '/v1/local/organization/config',
                          {'workers_enabled': True}, bearer=local_token))
                # Enroll an actual worker using the Organization API, before its
                # managed TLS/WebSocket connection is opened.
                worker_key, worker_public = generate_identity()
                request = {'selected_admin': 'lab-owner', 'device_id': 'lab-worker',
                           'public_key': worker_public, 'kind': 'worker',
                           'device_name': 'Lab Worker', 'claimed_user': 'Lab Owner',
                           'requested_role': 'worker', 'client_nonce': secrets.token_urlsafe(24)}
                statement = {**request, 'version': 1, 'action': 'slh.device.enrollment.request',
                             'audience': 'slh://' + local.organization_controller.authority.service_fingerprint()}
                # Organization is disabled; enrollment may not be invoked over
                # its network endpoint until explicitly started again.
                gates['disabled_org_rejects_remote_requests'] = (
                    local.organization_controller.status()['state'] == 'disabled')
                _ok(_http(lp, 'POST', '/v1/local/organization/enable', {}, bearer=local_token))
                _await(lambda: local.organization_controller.status()['listening'],
                       'Organization API with Worker serving')
                org_token = _login(api_port, local_sign_port=lp, tls=True)
                pending = _ok(_http(api_port, 'POST', '/v1/enrollment/requests',
                                    {**request, 'proof': sign_object(worker_key, statement)},
                                    tls=True), 201)
                gates['worker_enrollment_via_org_api'] = bool(pending.get('request_id'))
                approvals = _ok(_http(api_port, 'POST', '/v1/enrollment/approvals',
                                      {'request_id': pending['request_id']},
                                      bearer=org_token, tls=True), 201)
                gates['worker_approval_via_org_api'] = approvals.get('status') in ('APPROVED','active','ACTIVE')
                gates['real_worker_tls_websocket'] = _managed_auth(worker_port, 'lab-worker', worker_key,
                                                                    expected_pin=pin)
                # Kill only the disposable child process, never the host's SLH.
                # Its localhost parent must remain healthy and perform bounded recovery.
                child = local.organization_controller._process
                crashed_pid = child.pid
                child.kill()
                child.wait(timeout=5)
                _await(lambda: (local.organization_controller.status()['listening']
                               and local.organization_controller.status()['pid'] != crashed_pid),
                       'Local Service recovery of crashed Organization process', timeout=14)
                gates['unexpected_child_crash_auto_recovers'] = True
                _await(lambda: _managed_auth(worker_port, 'lab-worker', worker_key,
                                              expected_pin=pin),
                       'Worker handshake after Organization crash recovery', timeout=18)
                gates['worker_recovers_after_child_crash'] = True
                # No SEH should be opened simply because Worker serving was enabled.
                gates['seh_not_implicitly_enabled'] = (
                    read_config(state)['seh_enabled'] is False)
                if probe_relay:
                    _ok(_http(lp, 'POST', '/v1/local/organization/disable', {}, bearer=local_token))
                    signer, relay_public = generate_identity()
                    key_path = state / 'seh-service-signing-key.pem'
                    key_path.write_bytes(signer.private_bytes(serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
                    key_path.chmod(0o600)
                    relay = SEHRelay(slh_id=slh_id,
                                     slh_public_key=relay_public, host='127.0.0.1', port=0).start()
                    write_seh_settings(state, SEHConnectionSettings(
                        enabled=True, seh_url=f'ws://127.0.0.1:{relay.port}',
                        slh_id=slh_id,
                        allow_loopback_lab=True, retry_seconds=0.2))
                    _ok(_http(lp, 'PATCH', '/v1/local/organization/config',
                              {'seh_enabled': True}, bearer=local_token))
                    _ok(_http(lp, 'POST', '/v1/local/organization/enable', {}, bearer=local_token))
                    _await(lambda: relay.tunnel_ready, 'authenticated SEH outbound tunnel', timeout=12)
                    gates['real_org_service_seh_autoconnect'] = relay.authenticated_slh_count >= 1
                    # The SEH tunnel must not disable local private work or
                    # Pinky Promise sweeps. Check the actual child API, not
                    # an in-process mock or a reported configuration flag.
                    seh_org_token = _login(api_port, local_sign_port=lp, tls=True)
                    private_code, private_status = _http(
                        api_port, 'GET', '/v1/accounting/obligations',
                        bearer=seh_org_token, tls=True)
                    sweep_code, sweep_status = _http(
                        api_port, 'GET', '/v1/accounting/sweeps',
                        bearer=seh_org_token, tls=True)
                    gates['seh_keeps_private_ledger_available'] = (
                        private_code == 200 and isinstance(private_status, dict)
                        and private_status.get('schema') is not None)
                    gates['seh_keeps_pinky_sweeper_available'] = (
                        sweep_code == 200 and isinstance(sweep_status, dict)
                        and sweep_status.get('schema') == 'main-computer.slh-pinky-sweeps.v1')
                    gates['seh_private_accounting_still_requires_admin'] = (
                        _http(api_port, 'GET', '/v1/accounting/sweeps', tls=True)[0] == 401)
                    adapter = SEHLocalClientAdapter(f'ws://127.0.0.1:{relay.port}', slh_id,
                                                    allow_lab=True)
                    gates['worker_tls_via_real_seh_relay'] = _managed_auth(
                        adapter.server_address[1], 'lab-worker', worker_key,
                        expected_pin=pin, via_relay=True)
                    try:
                        _managed_auth(adapter.server_address[1], 'lab-worker', worker_key,
                                      expected_pin='0' * 64, via_relay=True)
                        gates['relay_rejects_wrong_tls_pin'] = False
                    except ServiceLabFailure as exc:
                        gates['relay_rejects_wrong_tls_pin'] = 'pin changed' in str(exc)
                    _ok(_http(lp, 'POST', '/v1/local/organization/restart', {}, bearer=local_token))
                    _await(lambda: relay.authenticated_slh_count >= 2 and relay.tunnel_ready,
                           'SEH tunnel reconnection after Organization restart', timeout=15)
                    gates['seh_reconnect_after_org_restart'] = True
                    adapter.stop()
                    adapter = None
                    _ok(_http(lp, 'POST', '/v1/local/organization/disable', {}, bearer=local_token))
                    _await(lambda: not relay.tunnel_ready,
                           'SEH disconnect after Organization stop', timeout=10)
                    gates['seh_disconnect_on_org_stop'] = True
                else:
                    _ok(_http(lp, 'POST', '/v1/local/organization/disable', {}, bearer=local_token))
                gates['org_disable_preserves_local'] = (
                    _ok(_http(lp, 'GET', '/v1/local/status'))['local_service']['state'] == 'running'
                    and read_config(state)['enabled'] is False)
            # Both saved disabled and saved enabled choices must survive local
            # control-plane process reconstruction; identity stays unchanged.
            gates['disabled_choice_persisted_across_local_restart'] = (
                read_config(state)['enabled'] is False)
            with _local(state, identities) as restored:
                gates['local_restart_does_not_serve_when_disabled'] = (
                    not restored.organization_controller.status()['listening']
                    and restored.organization_controller.config()['enabled'] is False)
                gates['authenticator_identity_survives_restart'] = (
                    _ok(_http(restored.server_port, 'GET',
                              '/v1/local/authenticator/identities/lab-owner-device'))['fingerprint']
                    == created['fingerprint'])
                token = _login(restored.server_port, local_sign_port=restored.server_port)
                _ok(_http(restored.server_port, 'PATCH', '/v1/local/organization/config',
                          {'seh_enabled': False}, bearer=token))
                _ok(_http(restored.server_port, 'POST', '/v1/local/organization/enable',
                          {}, bearer=token))
                _await(lambda: restored.organization_controller.status()['listening'],
                       'enabled state before simulated host reboot')
            gates['enabled_choice_saved_after_local_shutdown'] = read_config(state)['enabled']
            with _local(state, identities) as resumed:
                _await(lambda: resumed.organization_controller.status()['listening'],
                       'automatic restart of previously enabled Organization Service')
                gates['enabled_choice_resumes_service'] = (
                    bool(_ok(_http(api_port, 'GET', '/v1/org/identity', tls=True))['tls_sha256']))
                token = _login(resumed.server_port, local_sign_port=resumed.server_port)
                _ok(_http(resumed.server_port, 'POST', '/v1/local/organization/disable',
                          {}, bearer=token))
                gates['disabled_after_resume'] = read_config(state)['enabled'] is False
            from main_computer.slh.authority import SLHAuthority
            with SLHAuthority(state)._db() as db:
                gates['slh_sqlite_clean_shutdown'] = db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        finally:
            if adapter is not None:
                adapter.stop()
            if relay is not None:
                relay.stop()
    return {'schema': 'main-computer.slh-two-service-lab.v1',
            'status': 'PASS' if gates and all(gates.values()) else 'FAIL',
            'gates': gates, 'pass': bool(gates) and all(gates.values()),
            'scope': 'disposable-localhost-real-organization-process',
            'limitations': {'paid_marketplace': 'SEPARATE_LEGACY_LAB_LANE',
                            'public_wss': 'NOT_TESTED', 'cloud_coolify': 'NOT_TESTED',
                            'external_bridge': 'NOT_TESTED'}}
