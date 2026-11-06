"""Actual NanoJev Workers and requesters through existing Hub/SLH contracts.

No new gateway, wallet, Hub protocol, identity authority or inference server.
SEH is an outbound SLH relay: local Workers connect to SLH even if their
public marketplace sessions later traverse SEH. Never send requester data to
SEH directly or promise selection of a specific Worker.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
from http.client import HTTPConnection, HTTPSConnection
import json
from pathlib import Path
import secrets
import ssl
import time
import uuid
from typing import Any

from main_computer.model_execution_contract import model_id, operation_id, request_contract
from main_computer.providers.nanojev import CLEF_MODEL, CLEF_EVALUATE
from main_computer.providers.nanojev_worker import NanoJevSharedWorkerBackend
from main_computer.slh.organization_state import read_config
from main_computer.slh.storage import slh_state_dir, worker_machine_dir
from main_computer.slh.authenticator_store import (
    IdentityStoreError, default_identity_directory, load_identity, save_identity)
from main_computer.slh.authenticator import sign_object


class OperationalComputeError(RuntimeError):
    pass


def _positive_price(value: str) -> str:
    try:
        price = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise ValueError('A finite positive credit price is required') from exc
    if not price.is_finite() or price <= 0:
        raise ValueError('A finite positive credit price is required')
    return str(price)


def _require_local_organization(*, state_dir: Path | None = None) -> tuple[dict, str]:
    state = Path(state_dir) if state_dir is not None else slh_state_dir()
    config = read_config(state)
    if not (config['enabled'] and config['workers_enabled']):
        raise OperationalComputeError('SLH Organization Service and Worker listener must be enabled first')
    cert = state / 'managed-tls-cert.pem'
    if not cert.is_file():
        raise OperationalComputeError('SLH pinned TLS certificate is unavailable')
    try:
        digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text(encoding='ascii'))).hexdigest()
    except (OSError, ValueError) as exc:
        raise OperationalComputeError('Invalid SLH TLS certificate') from exc
    return config, digest


class LocalOrganizationAPI:
    """Pinned local TLS, including BEFORE sending identities or bearer tokens."""

    def __init__(self, *, port: int, fingerprint: str):
        self.port = int(port)
        self.fingerprint = fingerprint
        if not 1 <= self.port <= 65535 or len(fingerprint) != 64:
            raise ValueError('Invalid organization endpoint or certificate pin')

    def request(self, method: str, path: str, body: dict | None = None,
                *, token: str = '') -> dict:
        if method not in {'GET', 'POST'} or not path.startswith('/v1/'):
            raise ValueError('Unsupported organization API request')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        connection = HTTPSConnection('127.0.0.1', self.port, timeout=15, context=context)
        try:
            connection.connect()
            peer = connection.sock.getpeercert(binary_form=True)
            if not peer or not secrets.compare_digest(hashlib.sha256(peer).hexdigest(), self.fingerprint):
                raise OperationalComputeError('SLH_TLS_PIN_MISMATCH')
            data = json.dumps(body, allow_nan=False).encode('utf-8') if body is not None else None
            headers = {'Accept': 'application/json'}
            if data is not None:
                headers['Content-Type'] = 'application/json'
            if token:
                headers['Authorization'] = 'Bearer ' + token
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise OperationalComputeError('SLH reply too large')
            result = json.loads(raw)
            if response.status >= 300:
                detail = result.get('error', {}).get('code') if isinstance(result, dict) else None
                raise OperationalComputeError(f'SLH {path} rejected HTTP {response.status}: {detail or "UNSPECIFIED"}')
            if not isinstance(result, dict):
                raise OperationalComputeError('Invalid SLH JSON response')
            return result
        finally:
            connection.close()


def _local_bootstrap_status() -> dict:
    """Read SLH's established loopback bootstrap identity, never via a proxy."""
    from main_computer.slh.ports import DEFAULT_SLH_API_HOST, DEFAULT_SLH_API_PORT

    conn = HTTPConnection(DEFAULT_SLH_API_HOST, DEFAULT_SLH_API_PORT, timeout=8)
    try:
        conn.request('GET', '/v1/bootstrap/status')
        response = conn.getresponse()
        raw = response.read(65537)
        if len(raw) > 65536 or response.status != 200:
            raise OperationalComputeError(
                f'SLH bootstrap identity unavailable (HTTP {response.status})')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise OperationalComputeError('SLH bootstrap identity response is invalid')
        return result
    except (OSError, ValueError) as exc:
        raise OperationalComputeError('SLH bootstrap identity is unavailable') from exc
    finally:
        conn.close()


def _enrollment_state_path() -> Path:
    return worker_machine_dir() / 'operational-enrollments.json'


def _read_enrollments() -> dict:
    path = _enrollment_state_path()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def _record_enrollment(device_id: str, request_id: str, audience: str) -> None:
    """Only public enrollment references, never a second identity authority."""
    path = _enrollment_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    records = _read_enrollments()
    records[device_id] = {'request_id': request_id, 'audience': audience}
    temp = path.with_name('.operational-enrollments-' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(records, sort_keys=True), encoding='utf-8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def _assure_enrolled(api: LocalOrganizationAPI, *, device_id: str, role: str,
                     identity_dir: Path) -> bool:
    """Request approval only; NEVER self-approve or invent admin credentials."""
    identity_dir = Path(identity_dir)
    try:
        private_key, public = load_identity(identity_dir, device_id)
    except IdentityStoreError as exc:
        if exc.code != 'IDENTITY_NOT_FOUND':
            raise
        save_identity(identity_dir, device_id)
        private_key, public = load_identity(identity_dir, device_id)
    except FileNotFoundError:
        save_identity(identity_dir, device_id)
        private_key, public = load_identity(identity_dir, device_id)
    # Authorized devices get an SLH challenge. This never logs in or writes a token.
    try:
        challenge = api.request('POST', '/v1/auth/challenge', {'device_id': device_id})
        session = api.request('POST', '/v1/auth/verify', {
            'device_id': device_id, 'challenge_id': challenge['challenge_id'],
            'signature': sign_object(private_key, challenge)})
        try:
            if session.get('role') != role:
                raise OperationalComputeError(f'{device_id} was enrolled with the wrong role')
        finally:
            if session.get('access_token'):
                api.request('POST', '/v1/auth/logout', {}, token=session['access_token'])
        return True
    except OperationalComputeError as exc:
        if 'HTTP 403: UNKNOWN_OR_REVOKED_DEVICE' not in str(exc):
            raise
    status = _local_bootstrap_status()
    audience = 'slh://' + str(status.get('service_fingerprint') or '')
    if len(audience) <= 7:
        raise OperationalComputeError('SLH did not expose its enrollment signing identity')
    pending = _read_enrollments().get(device_id)
    if isinstance(pending, dict) and pending.get('audience') == audience:
        request_id = pending.get('request_id')
        if isinstance(request_id, str) and request_id:
            receipt = api.request('GET', f'/v1/enrollment/requests/{request_id}/status')
            if str(receipt.get('status')).lower() in {'pending_admin', 'pending'}:
                print(f'Approval pending for {device_id}: enrollment {request_id}')
                return False
            if str(receipt.get('status')).lower() == 'approved':
                raise OperationalComputeError(f'{device_id} was approved but is not authorized; check revocation')
            raise OperationalComputeError(f'{device_id} enrollment status {receipt.get("status")!r}; administrator action required')
    admins = api.request('GET', '/v1/enrollment/admins').get('admins', [])
    if not isinstance(admins, list) or len(admins) != 1:
        raise OperationalComputeError('Select an administrator through existing SLH enrollment UI (requires exactly one admin for automatic request)')
    admin_id = admins[0]['id']
    fields = {'selected_admin': admin_id, 'device_id': device_id,
              'public_key': public, 'kind': role, 'device_name': device_id,
              'claimed_user': 'Main Computer operator', 'requested_role': role,
              'client_nonce': secrets.token_urlsafe(24)}
    proof = sign_object(private_key, {**fields, 'version': 1,
                         'action': 'slh.device.enrollment.request', 'audience': audience})
    receipt = api.request('POST', '/v1/enrollment/requests', {**fields, 'proof': proof})
    _record_enrollment(device_id, receipt['request_id'], audience)
    print(f'Enrollment requested for {device_id}: {receipt["request_id"]}')
    print('Approve it using the existing SLH administrator controls, then repeat this command.')
    return False


def _org_context(*, state_dir: Path | None = None) -> tuple[dict, LocalOrganizationAPI, str]:
    config, pin = _require_local_organization(state_dir=state_dir)
    api = LocalOrganizationAPI(port=config['api_port'], fingerprint=pin)
    identity = api.request('GET', '/v1/org/identity')
    if identity.get('tls_sha256') != pin:
        raise OperationalComputeError('Organization TLS identity mismatch')
    return config, api, pin


def _market(*, model: str, advertisement: dict, price: str, ring: int) -> dict:
    if int(ring) not in {1, 2, 3}:
        raise ValueError('Worker ring must be 1, 2 or 3')
    return {'rings': [f'ring-{ring}'], 'models': [model],
            'capabilities': advertisement['capabilities'],
            'checkpoint_sha256': advertisement['checkpoint_sha256'],
            'price': {'amount': _positive_price(price), 'unit': 'compute_credit'},
            'active_sessions': 0, 'max_concurrency': 1}


def _run_clients(clients: list, *, max_requests: int = 0) -> None:
    """Connect all independently; preserve distinct sessions but share one head."""
    try:
        for idx, client in enumerate(clients, 1):
            receipt = client.start()
            if (receipt.get('accepted') or {}).get('ok') is not True:
                raise OperationalComputeError(f'Worker {idx} was not accepted')
            print(f'Worker {idx}/{len(clients)} connected: {client.worker_id}')
        print('Workers are serving real inference. Ctrl+C stops all connections.')
        seen = {}
        completed = 0
        while True:
            for client in clients:
                if not client.is_alive:
                    raise OperationalComputeError(f'Worker {client.worker_id} disconnected: {client.last_error or client.close_reason}')
                result = client.last_result
                session = result.get('session_id') if isinstance(result, dict) else ''
                if session and seen.get(client.worker_id) != session and result.get('type') in {
                        'hub.work.result.accepted', 'hub.work.failed.accepted', 'hub.work.terminal.accepted'}:
                    seen[client.worker_id] = session
                    completed += 1
                    print(f'Work completed: worker={client.worker_id} session={session} outcome={result.get("type")}')
                    if max_requests and completed >= max_requests:
                        return
            time.sleep(0.2)
    except KeyboardInterrupt:
        print('\nStopping Workers.')
    finally:
        for client in clients:
            client.close(reason='operator_stopped')


def run_worker(*, model: str, count: int = 1, via: str = 'slh',
               base_url: str = 'http://127.0.0.1:9765',
               hub_url: str = '', officer: str = 'o0', price: str = '1',
               ring: int = 3, max_requests: int = 0,
               state_dir: Path | None = None, identity_dir: Path | None = None) -> int:
    model_id(model)
    if not 1 <= count <= 32:
        raise ValueError('Worker count must be between 1 and 32')
    if max_requests < 0:
        raise ValueError('Maximum requests cannot be negative')
    if via not in {'hub','slh','seh'}:
        raise ValueError('Worker transport must be hub, slh or seh')
    backend = NanoJevSharedWorkerBackend(model=model, base_url=base_url)
    market = _market(model=model, advertisement=backend.advertisement(),price=price,ring=ring)
    print(f'Model {model}, checkpoint {market["checkpoint_sha256"]}, shared backend {base_url}')
    clients = []
    if via == 'hub':
        from main_computer.hub_cli_live_session import (
            identify_hub_protocol, issue_cli_multisession_authorization, _officer_wallet)
        from main_computer.worker_live_session import _WorkerHubLiveSessionClient
        if not hub_url:
            raise OperationalComputeError('Stable Hub URL is not configured')
        identity = identify_hub_protocol(hub_url)
        if identity.get('service') != 'main_computer.stable_hub':
            raise OperationalComputeError('Paid Worker requires a verified Stable Hub')
        auth = issue_cli_multisession_authorization(
            hub_url, wallet=_officer_wallet(officer), stable_identity=identity)
        for i in range(count):
            label = f'nanojev-{uuid.uuid4().hex[:12]}'
            message = {'type': 'worker.auth', 'execution_protocol': 'durable-authorization-v1',
                       'chain_id': auth['chain_id'], 'model': model, 'models': [model],
                       'status':'available','market': market,
                       'max_concurrency':1,'queue_depth':0,'active_requests':0,
                       'capabilities':{'provider':'nanojev','assigned_ring':ring,
                                       'capabilities':market['capabilities']},
                       'multisession_authorization':auth}
            clients.append(_WorkerHubLiveSessionClient(
                hub_url=hub_url, worker_id=label, auth_message=message,
                work_executor=backend.execute_offer, timeout_s=12))
    else:
        from main_computer.slh.managed_client import ManagedSLHWorkerClient
        config, api, pin = _org_context(state_dir=state_dir)
        if via == 'seh':
            status = api.request('GET', '/v1/org/status')
            if not (status.get('seh_enabled') and status.get('seh_connection_verified')):
                raise OperationalComputeError('SLH outbound SEH relay is not verified; cannot claim SEH availability')
            print('SEH outbound connected; actual paid-route traversal must be confirmed by Hub receipt.')
        keys = Path(identity_dir) if identity_dir else default_identity_directory()
        all_approved = True
        for i in range(1,count+1):
            device_id = f'mc-nanojev-worker-{i:02d}'
            if not _assure_enrolled(api, device_id=device_id, role='worker', identity_dir=keys):
                all_approved = False
        if not all_approved:
            print('Worker enrollment awaits administrator approval. No Worker sessions were opened.')
            return 2
        url = f'https://127.0.0.1:{config["worker_port"]}'
        for i in range(1,count+1):
            device_id = f'mc-nanojev-worker-{i:02d}'
            clients.append(ManagedSLHWorkerClient(
                device_id=device_id, identity_dir=keys, tls_fingerprint=pin,
                hub_url=url, worker_id=device_id,
                auth_message={'type':'worker.auth','device_id':device_id,
                              'model':model,'models':[model], 'market':market},
                work_executor=backend.execute_offer, timeout_s=12))
    _run_clients(clients, max_requests=max_requests)
    return 0


def build_work(*, model: str, operation: str, payload: dict, max_price: str,
               timeout_s: float) -> dict:
    model_id(model)
    operation_id(operation)
    if not isinstance(payload, dict) or not payload:
        raise ValueError('Structured NanoJev request requires a nonempty JSON object')
    if not (0 < timeout_s <= 3600):
        raise ValueError('Request timeout must be between 0 and 3600 seconds')
    work = {'model':model,'operation':operation,'capabilities':[operation],
            'input':{'kind':operation,'payload':payload},
            'timeout_seconds':float(timeout_s),
            'ring':'ring-3',
            'max_price':{'amount':_positive_price(max_price),'unit':'compute_credit'}}
    request_contract(work)
    return work


def _login_requester(api: LocalOrganizationAPI, *, device_id: str, identity_dir: Path) -> str:
    key, _ = load_identity(identity_dir, device_id)
    challenge = api.request('POST','/v1/auth/challenge',{'device_id':device_id})
    session = api.request('POST','/v1/auth/verify',{
        'device_id':device_id, 'challenge_id':challenge['challenge_id'],
        'signature':sign_object(key,challenge)})
    if session.get('role') != 'requester' or not session.get('access_token'):
        raise OperationalComputeError('SLH did not authorize requester role')
    return session['access_token']


def run_requester(*, model: str, operation: str, payload: dict, to: str,
                  hub_url: str = '', officer: str = 'o3',
                  max_price: str = '1', timeout_s: float = 1200,
                  submit: bool = False, request_id: str = '',
                  state_dir: Path | None = None,
                  identity_dir: Path | None = None) -> int:
    work = build_work(model=model, operation=operation, payload=payload,
                      max_price=max_price, timeout_s=timeout_s)
    rid = request_id or 'req_nanojev_' + uuid.uuid4().hex
    request = {'request_id':rid, 'work':work}
    if not submit:
        print(json.dumps({'dry_run': True, 'to': to, 'request': request,
                          'note': 'Add --submit to authorize actual charged work'}, indent=2))
        return 0
    if to == 'hub':
        from main_computer.hub_cli_live_session import (cli_request_authorization, identify_hub_protocol)
        from main_computer.nanojev_hub_request import _hub_json, retrieve_solution
        if not hub_url or identify_hub_protocol(hub_url).get('service') != 'main_computer.stable_hub':
            raise OperationalComputeError('Requester requires a verified Stable Hub URL')
        request['multisession_authorization'] = cli_request_authorization(hub_url, officer=officer)
        accepted = _hub_json(hub_url.rstrip('/') + '/api/hub/v1/work/requests', body=request)
        if accepted.get('accepted') is not True:
            raise OperationalComputeError(f'Hub did not accept request: {str(accepted)[:300]}')
        result = retrieve_solution(accepted, hub_url=hub_url, timeout_s=timeout_s,
                                   expected_model=model, expected_operation=operation)
        print(json.dumps(result, indent=2))
        return 0 if result.get('status') == 'succeeded' else 1
    if to != 'slh':
        raise ValueError('Requester destination must be hub or slh; SEH does not accept requester jobs')
    _config, api, _pin = _org_context(state_dir=state_dir)
    keys = Path(identity_dir) if identity_dir else default_identity_directory()
    device_id = 'mc-nanojev-requester'
    if not _assure_enrolled(api, device_id=device_id, role='requester', identity_dir=keys):
        return 2
    token = _login_requester(api, device_id=device_id, identity_dir=keys)
    # SLH owns the private-job accounting and selects a Worker itself. A
    # requester never names or chooses the specific Worker instance.
    private_work = {'model':model,'operation':operation,'capabilities':[operation],
                    'input':{'kind':operation,'payload':payload}}
    try:
        result = api.request('POST','/v1/work',{'request_id':rid,'model':model,'work':private_work},token=token)
    finally:
        api.request('POST','/v1/auth/logout',{},token=token)
    print(json.dumps(result, indent=2))
    return 0 if result.get('status') == 'SUCCEEDED' else 1
