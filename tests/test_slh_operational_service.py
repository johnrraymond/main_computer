"""P-256-only staged setup and real two-process SLH lifecycle.

No phone/TOTP, no external SEH, no marketplace mutations.
"""
from __future__ import annotations

from http.client import HTTPConnection, HTTPSConnection
import json
import os
from pathlib import Path
import socket
import ssl
import time
from threading import Thread

import pytest

from main_computer.slh.local_service import create_server
from main_computer.slh.organization_state import read_config
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.authority import SLHAuthority



@pytest.fixture
def disposable_slh_state(tmp_path, monkeypatch):
    """Preserve the pytest runner's ability to reopen throwaway state.

    Production secure_machine_state_dir() deliberately limits machine-owned
    Windows state to SYSTEM/Administrators. Reapplying those ACLs to files under
    the ordinary interactive pytest account on restart makes SQLite inaccessible.
    Restrict this replacement to the current test's fresh pytest temp tree.
    Production ACL hardening is never modified or disabled outside this fixture.
    """
    root = tmp_path.resolve()

    def protect(directory):
        directory = Path(directory).resolve()
        if not directory.is_relative_to(root):
            raise PermissionError('Test-only SLH state protector refuses non-pytest directory')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == 'posix':
            directory.chmod(0o700)
        return directory

    monkeypatch.setattr('main_computer.slh.local_service.secure_machine_state_dir', protect)
    return protect

def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def request(portnum, method, path, payload=None, *, token=None, tls=False):
    conn = (HTTPSConnection('127.0.0.1',portnum,timeout=3,context=ssl._create_unverified_context())
            if tls else HTTPConnection('127.0.0.1',portnum,timeout=3))
    try:
        headers = {}
        data = None
        if payload is not None:
            data = json.dumps(payload)
            headers['Content-Type']='application/json'
        if token:
            headers['Authorization']='Bearer '+token
        conn.request(method,path,data,headers)
        response=conn.getresponse()
        return response.status,json.loads(response.read())
    finally:
        conn.close()


def provision(portnum, directory):
    secret=(directory/'bootstrap.secret').read_text().strip()
    code,begun=request(portnum,'POST','/v1/bootstrap/begin',
                       {'bootstrap_secret':secret,'org_id':'our-org'})
    assert code==201, begun
    key,public=generate_identity()
    statement={**begun['proof_context'], 'admin_id':'owner', 'device_id':'owner-device',
               'public_key':public}
    code,staged=request(portnum,'PUT',f"/v1/bootstrap/{begun['transaction_id']}/administrator",
                        {'bootstrap_secret':secret,'admin_id':'owner','admin_name':'Owner',
                         'device_id':'owner-device','public_key':public,
                         'proof':sign_object(key,statement)})
    assert code==200 and staged['state']=='AWAITING_AUTHENTICATOR', staged
    assert request(portnum,'POST',f"/v1/bootstrap/{begun['transaction_id']}/activate-p256",
                   {'bootstrap_secret':secret})[0]==400
    code,active=request(portnum,'POST',f"/v1/bootstrap/{begun['transaction_id']}/activate-p256",
                        {'bootstrap_secret':secret,'defer_totp':True})
    assert code==200 and active['status']=='INITIALIZED', active
    assert active['totp_enrolled'] is False
    assert not (directory/'bootstrap.secret').exists()
    assert read_config(directory)['enabled'] is False
    return key


def login(portnum, key, tls=False):
    code,challenge=request(portnum,'POST','/v1/auth/challenge',
                           {'device_id':'owner-device'},tls=tls)
    assert code==200, challenge
    code,login=request(portnum,'POST','/v1/auth/verify',
                       {'device_id':'owner-device', 'challenge_id':challenge['challenge_id'],
                        'signature':sign_object(key,challenge)},tls=tls)
    assert code==200, login
    assert login['role']=='admin'
    return login['access_token']


def wait_until(callback, timeout=8):
    started=time.monotonic()
    while time.monotonic()-started < timeout:
        if callback():return
        time.sleep(0.08)
    raise AssertionError('Timed out waiting for Organization Service')


def test_fresh_setup_activate_and_launch_separate_tls_organization(tmp_path, disposable_slh_state):
    state=tmp_path/'slh'
    with create_server(state_dir=state,port=0) as local:
        thread=Thread(target=local.serve_forever,daemon=True)
        thread.start()
        try:
            lport=local.server_port
            code,pre=request(lport,'GET','/v1/local/status')
            assert code==200 and pre['organization_service']['enabled'] is False
            assert request(lport,'POST','/v1/local/organization/enable',{})[0]==401
            key=provision(lport,state)
            admin=login(lport,key)
            assert request(lport,'POST','/v1/local/organization/enable',{},token=admin)[0]==409
            api_port, worker_port=port(),port()
            while api_port==worker_port: worker_port=port()
            code,cfg=request(lport,'PATCH','/v1/local/organization/config',
                              {'host':'127.0.0.1','api_port':api_port,
                               'worker_port':worker_port, 'workers_enabled':False,
                               'seh_enabled':False},token=admin)
            assert code==200 and cfg['configured'] and not cfg['enabled'], cfg
            assert request(lport,'GET','/v1/local/organization/config',token=admin)[1]['api_port']==api_port
            code,starting=request(lport,'POST','/v1/local/organization/enable',{},token=admin)
            assert code==200 and starting['enabled'],starting
            wait_until(lambda: local.organization_controller.status()['listening'])
            assert request(api_port,'GET','/v1/org/identity',tls=True)[1]['organization_id']=='our-org'
            assert request(api_port,'GET','/v1/org/status',tls=True)[1]['worker_serving_enabled'] is False
            assert request(api_port,'GET','/v1/local/status',tls=True)[0]==404
            assert request(api_port,'GET','/v1/bootstrap/status',tls=True)[0]==404
            assert request(api_port,'GET','/v1/org/admin/services',tls=True)[0]==401
            org_admin=login(api_port,key,tls=True)
            assert request(api_port,'GET','/v1/org/admin/services',token=org_admin,tls=True)[0]==200
            assert request(api_port,'GET','/v1/admin/enrollments',token=org_admin,tls=True)[0]==200
            assert request(lport,'GET','/v1/local/services',token=admin)[1]['organization_service']['state']=='running'
            code,disabled=request(lport,'POST','/v1/local/organization/disable',{},token=admin)
            assert code==200 and not disabled['enabled'] and not disabled['listening']
            assert request(lport,'GET','/v1/local/status')[1]['local_service']['state']=='running'
            assert request(lport,'PATCH','/v1/local/organization/config',
                           {'workers_enabled':True},token=admin)[0]==200
            assert not read_config(state)['enabled']
            assert not (state/'bootstrap.secret').exists()
        finally:
            local.shutdown()
            thread.join(timeout=3)


def test_config_rejects_unsafe_enablement_and_bad_ports(tmp_path):
    from main_computer.slh.organization_state import save_config, OrganizationConfigError, DEFAULTS
    import pytest
    with pytest.raises(OrganizationConfigError):
        save_config(tmp_path,{**DEFAULTS,'configured':False,'enabled':True})
    with pytest.raises(OrganizationConfigError):
        save_config(tmp_path,{**DEFAULTS,'host':'attacker.example.com'})
    with pytest.raises(OrganizationConfigError):
        save_config(tmp_path,{**DEFAULTS,'api_port':8897})
    with pytest.raises(OrganizationConfigError):
        save_config(tmp_path,{**DEFAULTS,'worker_port':8899})
    assert not (tmp_path/'organization-service.json').exists()


def test_enabled_service_resumes_after_local_service_restart_and_worker_bind(tmp_path, disposable_slh_state):
    state=tmp_path/'state'
    api_port, worker_port=port(),port()
    while api_port==worker_port:worker_port=port()
    with create_server(state_dir=state,port=0) as local:
        thread=Thread(target=local.serve_forever,daemon=True)
        thread.start()
        try:
            key=provision(local.server_port,state)
            token=login(local.server_port,key)
            code,cfg=request(local.server_port,'PATCH','/v1/local/organization/config',
                             {'host':'127.0.0.1','api_port':api_port,
                              'worker_port':worker_port, 'workers_enabled':True,
                              'seh_enabled':False},token=token)
            assert code==200,cfg
            assert request(local.server_port,'POST','/v1/local/organization/enable',
                           {},token=token)[0]==200
            wait_until(lambda:local.organization_controller.status()['listening'])
            # The second independent TLS listener is the real managed-worker
            # gateway, not the localhost management API.
            import websockets.sync.client
            ctx=ssl._create_unverified_context()
            with websockets.sync.client.connect(
                f'wss://127.0.0.1:{worker_port}/v1/managed/workers/live-session',
                ssl=ctx,open_timeout=3,close_timeout=1) as ws:
                ws.send(json.dumps({'type':'worker.auth','device_id':'unknown',
                                    'execution_protocol':'durable-authorization-v1'}))
                reply=json.loads(ws.recv(timeout=3))
                assert reply['type']=='hub.auth.rejected'
            assert request(api_port,'GET','/v1/org/status',tls=True)[1]['worker_serving_enabled']
        finally:
            local.shutdown()
            thread.join(timeout=3)
    # Simulate boot/service restart while the persisted enabled choice remains.
    assert read_config(state)['enabled'] is True
    with create_server(state_dir=state,port=0) as restarted:
        thread=Thread(target=restarted.serve_forever,daemon=True)
        thread.start()
        try:
            wait_until(lambda:restarted.organization_controller.status()['listening'])
            assert request(api_port,'GET','/v1/org/identity',tls=True)[1]['organization_id']=='our-org'
            fresh_token=login(restarted.server_port,key)
            assert request(restarted.server_port,'POST','/v1/local/organization/disable',
                           {},token=fresh_token)[0]==200
        finally:
            restarted.shutdown()
            thread.join(timeout=3)
