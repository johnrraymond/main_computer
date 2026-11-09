"""Authenticated MCEL Worker Bank acceptance: real authority, TLS and Worker.

A disposable SLH authority and Organization Service are used; no installed SLH,
Coolify deployment, marketplace credits, or production device is modified.
"""
from __future__ import annotations

import json
import secrets
import shutil
import ssl
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from main_computer.mcel_slh_application import compile_slh_application, observe_slh, render_slh_page
from main_computer.mcel_slh_worker_bridge import (
    OrganizationWorkerClient, WorkerBridgeError, project_inventory,
)
from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.managed_gateway import SLHManagedGateway, transport_identity
from main_computer.slh.managed_client import ManagedSLHWorkerClient
from main_computer.slh.organization_service import OrganizationHTTPServer, OrganizationHTTPHandler
from main_computer.slh.organization_state import save_config, DEFAULTS
from tools.slh_authenticator_cli import save_identity, load_identity
from tools.slh_seh_connector import service_identity


def status_probe():
    return "RUNNING", {"schema": "main-computer.slh-local-status.v1",
        "local_service": {"state": "running", "binding": "loopback"},
        "initialization": {"setup_phase": "INITIALIZED"},
        "administrator_enrollment": {"state": "ENROLLED", "local_credential": "NOT_VERIFIED"},
        "organization_service": {"state": "running", "configured": True,
            "listening": True, "bind_host": "127.0.0.1", "api_port": 8899,
            "network_access": "LOOPBACK_ONLY"}}


@pytest.fixture
def live_worker(tmp_path):
    root = tmp_path / 'state'
    service_identity(root, create=True)
    auth = SLHAuthority(root)
    identities = tmp_path / 'identities'
    save_identity(identities, 'admin-device')
    admin_key, public = load_identity(identities, 'admin-device')
    statement = {"version": 1, "action": "slh.admin.bootstrap", "admin_id": "owner",
                 "device_id": "admin-device", "public_key": public}
    auth.bootstrap({**statement, "admin_name": "Owner", "proof": sign_object(admin_key, statement),
        "bootstrap_secret": (root / 'bootstrap.secret').read_text().strip()}, loopback=True)
    status, challenge = SLHAPI(auth).call("POST", "/v1/auth/challenge", {"device_id": "admin-device"})
    assert status == 200
    status, login = SLHAPI(auth).call("POST", "/v1/auth/verify", {"device_id": "admin-device",
        "challenge_id": challenge["challenge_id"], "signature": sign_object(admin_key, challenge)})
    assert status == 200
    token = login["access_token"]

    save_identity(identities, 'worker-01')
    worker_key, worker_public = load_identity(identities, 'worker-01')
    enrollment = {"selected_admin": "owner", "device_id": "worker-01", "public_key": worker_public,
        "kind": "worker", "device_name": "Compute Node 01", "claimed_user": "Operator",
        "requested_role": "worker", "client_nonce": secrets.token_urlsafe(32)}
    statement = {**enrollment, "version": 1, "action": "slh.device.enrollment.request",
                 "audience": "slh://" + auth.service_fingerprint()}
    request = auth.enrollment_request({**enrollment, "proof": sign_object(worker_key, statement)})
    auth.approve(request["request_id"], admin_id="owner")

    gateway = SLHManagedGateway(auth, host="127.0.0.1", port=0, allow_seh=False)
    gw_thread = gateway.start_background()
    for _ in range(150):
        if gateway._server is not None: break
        time.sleep(.02)
    assert gateway._server is not None
    market = {"rings": ["ring-2"], "capabilities": ["chat.completions"], "models": ["mock-model"],
              "price": {"amount": "1", "unit": "compute_credit"}, "max_concurrency": 1}
    worker = ManagedSLHWorkerClient(device_id="worker-01", identity_dir=identities,
        tls_fingerprint=gateway.fingerprint, hub_url=f"https://127.0.0.1:{gateway.port}",
        worker_id="worker-01", auth_message={"type": "worker.auth", "device_id": "worker-01",
        "market": market},
        work_executor=lambda offer: {"status": "success", "response": {"model": "mock-model", "content": "done"}})
    worker.start()
    for _ in range(100):
        if gateway.scheduler.snapshot():break
        time.sleep(.02)
    assert len(gateway.scheduler.snapshot()) == 1

    tls, fingerprint = transport_identity(root)
    org = OrganizationHTTPServer(("127.0.0.1", 0), OrganizationHTTPHandler)
    port = org.server_port
    org.socket = tls.wrap_socket(org.socket, server_side=True)
    org.authority = auth
    org.state_dir = root
    org.api = SLHAPI(auth)
    org.gateway = gateway
    org.workers_enabled = True
    org.seh_enabled = False
    org.marketplace = SimpleNamespace(_lock=threading.RLock(), sessions={})
    org.marketplace_controller = SimpleNamespace(status=lambda: {"state": "test-disabled"})
    org.private_requests = None
    org.pinky_sweeper = None
    org.fingerprint = fingerprint
    save_config(root, {**DEFAULTS, "configured": True, "enabled": True,
                       "activation_confirmed": True, "host": "127.0.0.1",
                       "api_port": port, "worker_port": gateway.port, "workers_enabled": True})
    thread = threading.Thread(target=org.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(auth=auth, token=token, admin_key=admin_key, client=OrganizationWorkerClient(state_dir=root),
                              org=org, gateway=gateway, identities=identities, root=root)
    finally:
        org.shutdown()
        org.server_close()
        thread.join(timeout=5)
        worker.close()
        gateway.stop()
        gw_thread.join(timeout=5)


def test_authenticated_real_worker_and_verified_backend_rename(live_worker):
    client, token = live_worker.client, live_worker.token
    with pytest.raises(WorkerBridgeError) as exc:
        client.inventory('x' * 40)
    assert exc.value.status == 401
    inventory = client.inventory(token)
    assert inventory["worker_serving_enabled"] is True
    workers, summary = project_inventory(inventory, '2026-11-08T20:00:00Z')
    assert summary["enrolledCount"] == '1' and summary["connectedCount"] == '1'
    assert workers[0]["id"] == 'worker-01'
    assert workers[0]["state"] == 'AVAILABLE' and workers[0]["models"] == 'mock-model'
    before = observe_slh('workers', probe=status_probe, admin_token=token, worker_client=client)
    assert before['workerSummary']['observationState'] == 'AUTHENTICATED'
    assert before['workers'][0]['label'] == 'Compute Node 01'
    with pytest.raises(WorkerBridgeError) as exc:
        client.rename('x' * 40, 'worker-01', 'Falsified')
    assert exc.value.status == 401
    with pytest.raises(WorkerBridgeError):
        client.rename(token, 'admin-device', 'Not a worker')
    receipt = client.rename(token, 'worker-01', 'Compute Node Renamed')
    assert receipt == {"outcome": "UPDATED", "message": "Worker label verified in organization directory",
                       "deviceId": "worker-01"}
    after = observe_slh('workers', probe=status_probe, admin_token=token, worker_client=client)
    assert after['workers'][0]['label'] == 'Compute Node Renamed'
    assert after['workers'][0]['models'] == 'mock-model'
    assert any(entry['action'] == 'device.updated' and entry['target'] == 'worker-01'
               for entry in live_worker.auth.audit())
    assert token not in json.dumps(after)
    assert 'private_key' not in json.dumps(after)


def test_pinned_tls_mismatch_refuses_without_unverified_fallback(live_worker):
    cert_path = live_worker.client.state_dir / 'managed-tls-cert.pem'
    original = cert_path.read_bytes()
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    from datetime import datetime, timezone, timedelta
    # Simulated local pin replacement, never touches production files.
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Impostor')])
    certificate = x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(
        key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(
        datetime.now(timezone.utc)-timedelta(minutes=5)).not_valid_after(
        datetime.now(timezone.utc)+timedelta(days=1)).sign(key,hashes.SHA256())
    try:
        cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        with pytest.raises(WorkerBridgeError) as exc:
            live_worker.client.inventory(live_worker.token)
        assert exc.value.code == 'ORG_TLS_PIN_MISMATCH'
    finally:
        cert_path.write_bytes(original)


def test_semantic_worker_operations_have_no_authored_layout():
    ir, plan = compile_slh_application()
    assert plan['policy'] == 'mcel.semantic-structure.p3'
    assert not ir['layouts']
    assert {i['id'] for i in ir['intents']} >= {'intent:choose-worker', 'intent:rename-worker'}
    assert len([x for x in ir['effects'] if x['risk'] == 'consequential']) == 7
    assert 'intent:login-administrator' in render_slh_page('worker-bank')
    assert 'localStorage.setItem' not in render_slh_page('worker-bank')


def test_generated_browser_renders_real_inventory_and_updated_label(live_worker):
    # Real authority/TLS/worker state is sourced via bridge, while Chromium
    # receives a scoped mocked same-origin fetch: loopback navigation is blocked
    # in this container. Browser composition/intent and adapter are still real.
    from playwright.sync_api import sync_playwright
    token, client = live_worker.token, live_worker.client
    before = observe_slh('workers', probe=status_probe, admin_token=token, worker_client=client)
    client.rename(token, 'worker-01', 'Renamed Through Admin API')
    after = observe_slh('workers', probe=status_probe, admin_token=token, worker_client=client)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('dialog', lambda d: d.accept())
            page.evaluate('''data=>{
              window.__requests=[];
              window.fetch=async(url,opts)=>{
                window.__requests.push({url,method:opts.method,authenticated:!!opts.headers.Authorization});
                if(url==='/api/applications/slh/setup/action')return {ok:true,json:async()=>({access_token:data.token})};
                if(url!=='/api/applications/slh/observe?view=workers')throw Error('unexpected-request');
                return {ok:true,json:async()=>opts.headers.Authorization ? data.after : data.before};
              };
            }''', {"before": before, "after": after, "token": token})
            page.set_content(render_slh_page('worker-bank'))
            page.get_by_text('SLH observation complete', exact=False).wait_for(timeout=15000)
            assert page.get_by_text('Organization session not established').count() == 1
            page.locator('form[data-intent="intent:login-administrator"] input[name="adminDeviceId"]').fill('admin-device')
            page.locator('form[data-intent="intent:login-administrator"] button[type=submit]').click()
            page.get_by_text('Organization administrator authenticated').wait_for(timeout=15000)
            assert page.get_by_text('Renamed Through Admin API').count() >= 1
            assert page.evaluate('window.__mcelSlh.composition.policy') == 'mcel.semantic-structure.p3'
            assert token not in page.locator('body').inner_text()
            assert page.evaluate('window.__requests.some(x=>x.authenticated)')
            assert not errors
        finally:
            browser.close()


def test_explicit_org_login_is_process_local_and_requires_admin(live_worker, tmp_path):
    from main_computer.slh.authenticator_store import LocalIdentityStore
    store = LocalIdentityStore(tmp_path / 'admin-identities')
    # Explicit export of test authority admin key into the *local test* identity
    # store is performed through its own native creation in normal operation.
    # This case verifies unknown device cannot silently authenticate.
    with pytest.raises(WorkerBridgeError) as exc:
        live_worker.client.login_from_local_identity('unrecognized', tmp_path / 'admin-identities')
    assert exc.value.code == 'LOCAL_ADMIN_IDENTITY_UNAVAILABLE'
    with pytest.raises(WorkerBridgeError) as exc:
        live_worker.client.login_from_local_identity('worker-01', live_worker.identities)
    assert exc.value.status == 403 and exc.value.code == 'ADMIN_REQUIRED'
    authenticated = live_worker.client.login_from_local_identity('admin-device', live_worker.identities)
    assert authenticated['role'] == 'admin' and authenticated['device_id'] == 'admin-device'
    assert authenticated['access_token'] != live_worker.token
    assert live_worker.client.inventory(authenticated['access_token'])['devices']


def test_viewport_http_worker_observation_and_authorized_rename(live_worker):
    from main_computer.config import MainComputerConfig
    from main_computer.mcel_slh_application import ROOT
    with patch('main_computer.game_web_loader.read_game_bundle', return_value='<div></div>'):
        from main_computer.viewport_server import ViewportServer
    server = ViewportServer(('127.0.0.1',0), MainComputerConfig(workspace=ROOT), verbose=False)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    host = f'http://127.0.0.1:{server.server_port}'
    from main_computer.mcel_slh_application import observe_slh as original_observe
    client = live_worker.client
    def observed(view, *, admin_token=''):
        return original_observe(view, probe=status_probe, admin_token=admin_token, worker_client=client)
    try:
        with patch('main_computer.mcel_slh_application.observe_slh', side_effect=observed), \
             patch('main_computer.mcel_slh_worker_bridge.OrganizationWorkerClient', return_value=client):
            with urlopen(host+'/api/applications/slh/observe?view=workers') as response:
                unauthenticated = json.loads(response.read())
            assert unauthenticated['workerSummary']['observationState'] == 'AUTH_REQUIRED'
            req = Request(host+'/api/applications/slh/observe?view=workers',
                          headers={'Authorization':'Bearer '+live_worker.token})
            with urlopen(req) as response:
                authenticated = json.loads(response.read())
            assert authenticated['workerSummary']['observationState'] == 'AUTHENTICATED'
            assert authenticated['workers'][0]['state'] == 'AVAILABLE'
            path = host+'/api/applications/slh/worker/rename'
            payload = json.dumps({'device_id':'worker-01','label':'Renamed Via Viewport'}).encode()
            req = Request(path, data=payload, method='POST', headers={
                'Authorization':'Bearer '+live_worker.token,
                'Content-Type':'application/json', 'X-MCEL-SLH-Operation':'rename'})
            with pytest.raises(HTTPError) as exc: urlopen(req)
            assert exc.value.code == 403  # no same-origin Origin
            req.add_header('Origin', host)
            with urlopen(req) as response:
                receipt = json.loads(response.read())
            assert receipt['outcome'] == 'UPDATED'
            with urlopen(Request(host+'/api/applications/slh/observe?view=workers',
                 headers={'Authorization':'Bearer '+live_worker.token})) as response:
                updated = json.loads(response.read())
            assert updated['workers'][0]['label'] == 'Renamed Via Viewport'
            req = Request(path, data=payload, method='POST', headers={
                'Authorization':'Bearer '+'x'*40, 'Content-Type':'application/json',
                'X-MCEL-SLH-Operation':'rename', 'Origin':host})
            with pytest.raises(HTTPError) as exc: urlopen(req)
            assert exc.value.code == 401
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_browser_mutation_executes_against_disposable_real_worker(live_worker):
    from playwright.sync_api import sync_playwright
    token, client = live_worker.token, live_worker.client
    messages = []

    def browser_bridge(url, options):
        supplied = str(options.get('headers', {}).get('Authorization', ''))
        observed = supplied.removeprefix('Bearer ') if supplied.startswith('Bearer ') else ''
        if url == '/api/applications/slh/setup/action' and options.get('method') == 'POST':
            body = json.loads(options.get('body') or '{}')
            assert body['action'] == 'login' and body['arguments'] == {'adminDeviceId': 'admin-device'}
            return {'ok': True, 'data': {'access_token': token}}
        if url == '/api/applications/slh/observe?view=workers' and options.get('method') == 'GET':
            result = observe_slh('workers', probe=status_probe, admin_token=observed, worker_client=client)
            return {'ok': True, 'data': result}
        if url == '/api/applications/slh/worker/rename' and options.get('method') == 'POST':
            try:
                body = json.loads(options.get('body') or '{}')
                result = client.rename(observed, body.get('device_id'), body.get('label'))
                messages.append('renamed')
                return {'ok': True, 'data': result}
            except WorkerBridgeError as exc:
                return {'ok': False, 'data': {'error': exc.code}}
        raise AssertionError('unexpected semantic Worker operation: ' + url)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page()
            page.expose_function('slh_browser_bridge', browser_bridge)
            page.evaluate('''() => {
              window.fetch=async(url,options)=>{
                const response=await window.slh_browser_bridge(url,options);
                return {ok:response.ok,json:async()=>response.data};
              };
            }''')
            page.on('dialog', lambda d: d.accept())
            page.set_content(render_slh_page('worker-bank'))
            page.get_by_text('SLH observation complete', exact=False).wait_for(timeout=15000)
            assert page.get_by_text('Compute Node 01').count() == 0
            page.locator('form[data-intent="intent:login-administrator"] input[name="adminDeviceId"]').fill('admin-device')
            page.locator('form[data-intent="intent:login-administrator"] button[type=submit]').click()
            page.get_by_text('Organization administrator authenticated').wait_for(timeout=15000)
            page.get_by_text('Compute Node 01').wait_for(timeout=15000)
            page.locator('button[data-select-key="worker-01"]').first.click()
            page.locator('form[data-intent="intent:rename-worker"] input[name="label"]').fill('Browser Renamed Worker')
            page.locator('form[data-intent="intent:rename-worker"] button[type=submit]').click()
            page.get_by_text('Worker label verified in organization directory').wait_for(timeout=15000)
            assert messages == ['renamed']
            assert any(d['name'] == 'Browser Renamed Worker' for d in client.inventory(token)['devices'])
            page.locator('button[data-intent="intent:refresh-workers"]').click()
            page.locator('[data-record-field="label"]').filter(has_text='Browser Renamed Worker').wait_for(timeout=15000)
            assert token not in page.locator('body').inner_text()
        finally:
            browser.close()
