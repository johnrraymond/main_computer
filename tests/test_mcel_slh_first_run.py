"""SLH application first-run integration; all authority and listeners disposable."""
from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import threading
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from main_computer.mcel_slh_first_run import SlhFirstRunController, FirstRunError
from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from main_computer.slh.ports import DEFAULT_SLH_API_PORT


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_first_run_semantic_source_has_no_authored_layout():
    ir, composition = compile_slh_application()
    assert ir['layouts'] == []
    assert composition['policy'] == 'mcel.semantic-structure.p3'
    intents = {i['id'] for i in ir['intents']}
    assert {'intent:start-local', 'intent:bootstrap-administrator',
            'intent:configure-organization', 'intent:activate-organization',
            'intent:login-administrator'} <= intents
    assert 'mcel-slh-admin-bearer' not in render_slh_page()
    assert 'intent:login-administrator' in render_slh_page()


def test_unavailable_is_not_assumed_unconfigured(tmp_path):
    controller = SlhFirstRunController(state_dir=tmp_path / 'nonexistent',
                                       identity_dir=tmp_path / 'identities', local_port=free_port())
    assert controller.status() == {'local': 'UNREACHABLE', 'initialized': None, 'organization': 'NOT_OBSERVED'}
    with pytest.raises(FirstRunError) as exc:
        controller.bootstrap(admin_id='owner', admin_name='Operator', device_id='admin-pc',
                             acknowledge_p256_only=True)
    assert exc.value.code == 'SLH_LOCAL_UNAVAILABLE'
    with pytest.raises(FirstRunError) as exc:
        controller.bootstrap(admin_id='owner', admin_name='Operator', device_id='admin-pc',
                             acknowledge_p256_only=False)
    assert exc.value.code == 'P256_ONLY_ACKNOWLEDGEMENT_REQUIRED'


def test_invalid_network_binding_and_unlisted_operations_refused(tmp_path):
    ctl = SlhFirstRunController(state_dir=tmp_path)
    for host in ('0.0.0.0', '8.8.8.8', '169.254.1.3', '224.0.0.1', 'my.local', '127.0.0.2'):
        with pytest.raises(FirstRunError):
            ctl.configure(device_id='admin-pc', host=host, api_port='8899', worker_port='8898')
    with pytest.raises(FirstRunError):
        ctl.execute('reset-identity', {})
    with pytest.raises(FirstRunError):
        ctl.execute('start', {'extra': 'ignored'})


def test_first_run_from_stopped_to_org_authenticated(tmp_path):
    state, identities = tmp_path/'authority', tmp_path/'identities'
    local_port, worker_port, api_port = free_port(), free_port(), free_port()
    while len({local_port, worker_port, api_port}) != 3:
        local_port, worker_port, api_port = free_port(), free_port(), free_port()
    launched = []

    def runner(*args, **kwargs):
        proc = subprocess.Popen(*args, **kwargs)
        launched.append(proc)
        return proc

    ctl = SlhFirstRunController(state_dir=state, identity_dir=identities,
                                local_port=local_port, runner=runner)
    ctl.identities.create('admin-pc',label='Test Admin')
    try:
        assert ctl.status()['local'] == 'UNREACHABLE'
        assert ctl.execute('start', {})['outcome'] == 'STARTED'
        assert ctl.status()['local'] == 'RUNNING' and not ctl.status()['initialized']
        result = ctl.execute('bootstrap', {'adminId': 'owner', 'adminName': 'Test Admin',
                              'deviceId': 'admin-pc', 'acknowledgeP256Only': True})
        assert result['outcome'] == 'INITIALIZED'
        assert ctl.status()['initialized']
        with pytest.raises(FirstRunError) as exc:
            ctl.execute('bootstrap', {'adminId':'other','adminName':'Other',
                         'deviceId':'other-pc','acknowledgeP256Only':True})
        assert exc.value.code == 'ALREADY_INITIALIZED'
        result = ctl.execute('configure', {'adminDeviceId':'admin-pc', 'host':'127.0.0.1',
                              'apiPort': str(api_port), 'workerPort': str(worker_port)})
        assert result['outcome'] == 'CONFIGURED'
        assert ctl.status()['organization'] == 'disabled'
        result = ctl.execute('activate', {'adminDeviceId': 'admin-pc'})
        assert result['outcome'] == 'RUNNING'
        assert ctl.status()['organization'] == 'running'
        login = ctl.execute('login', {'adminDeviceId':'admin-pc'})
        assert login['role'] == 'admin'
        assert login['access_token'] and len(login['access_token']) > 24
        from main_computer.mcel_slh_worker_bridge import OrganizationWorkerClient
        inventory = OrganizationWorkerClient(state_dir=state).inventory(login['access_token'])
        assert inventory['devices'] and inventory['devices'][0]['kind'] == 'administrator'
        assert inventory['workers'] == []
        assert login['access_token'] not in json.dumps(ctl.status())
        receipt = ctl.execute('logout', {}, token=login['access_token'])
        assert receipt['outcome'] == 'SIGNED_OUT'
        with pytest.raises(Exception):
            OrganizationWorkerClient(state_dir=state).inventory(login['access_token'])
    finally:
        for proc in launched:
            try:
                proc.terminate(); proc.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill(); proc.wait(timeout=8)


def test_viewport_setup_route_is_exact_same_origin_and_bounded(tmp_path):
    from main_computer.config import MainComputerConfig
    with patch('main_computer.game_web_loader.read_game_bundle', return_value='<div></div>'):
        from main_computer.viewport_server import ViewportServer
    server = ViewportServer(('127.0.0.1', 0), MainComputerConfig(workspace=Path(__file__).resolve().parents[1]), verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    data = json.dumps({'action':'reset-identity', 'arguments':{}}).encode()
    def attempt(headers):
        req = Request(base+'/api/applications/slh/setup/action', data=data, method='POST', headers=headers)
        with pytest.raises(HTTPError) as exc:
            urlopen(req, timeout=10)
        return exc.value.code
    try:
        assert attempt({'Content-Type':'application/json','X-MCEL-SLH-Operation':'reset-identity'}) == 403
        assert attempt({'Content-Type':'application/json','X-MCEL-SLH-Operation':'reset-identity',
                        'Origin':'http://malicious.example'}) == 403
        assert attempt({'Content-Type':'application/json','X-MCEL-SLH-Operation':'reset-identity',
                        'Origin':base}) == 403
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_generated_first_run_browser_completes_authenticated_setup(tmp_path):
    """Every step originates from MCEL-generated controls, reaches real local SLH."""
    from main_computer.config import MainComputerConfig
    from main_computer.mcel_slh_application import observe_slh, _local_status
    from main_computer.mcel_slh_worker_bridge import OrganizationWorkerClient
    from playwright.sync_api import sync_playwright
    import shutil

    state, identities = tmp_path/'state', tmp_path/'identities'
    # Auto-configuration selects the port immediately after the Worker port.
    local_port, worker_port = free_port(), free_port()
    api_port = worker_port + 1
    procs = []
    def spawn(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        procs.append(process)
        return process
    ctl = SlhFirstRunController(state_dir=state, identity_dir=identities,
                                local_port=local_port, runner=spawn)
    bridge = OrganizationWorkerClient(state_dir=state)
    default=ctl.identities.ensure_default_identity()
    device_id=default['device_id']
    def observed_identities():
        from main_computer.mcel_slh_application import IDENTITIES_SCHEMA
        if ctl.status()['local']!='RUNNING':
            return {'schema':IDENTITIES_SCHEMA,'state':'UNAVAILABLE',
                    'defaultDeviceId':'','identities':[],'explanation':'LMS-SLH is not running'}
        return {'schema':IDENTITIES_SCHEMA,'state':'AVAILABLE',
                'defaultDeviceId':device_id,'explanation':'Local identity discovered; role unverified',
                'identities':[{'deviceId':device_id,'label':default['label'],
                               'fingerprint':default['fingerprint'],'defaultStatus':'DEFAULT'}]}
    def observed(view, **kw):
        return observe_slh(view, probe=lambda:_local_status(port=local_port),
                           worker_client=bridge, **kw)
    with patch('main_computer.game_web_loader.read_game_bundle', return_value='<div></div>'):
        from main_computer.viewport_server import ViewportServer
    server = ViewportServer(('127.0.0.1', 0), MainComputerConfig(workspace=Path(__file__).resolve().parents[1]), verbose=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'

    def proxy(url, opts):
        headers = dict(opts.get('headers') or {})
        if opts['method'] == 'POST':
            headers['Origin'] = base
        data = opts.get('body')
        req = Request(base+url, data=data.encode() if data is not None else None,
                      method=opts['method'], headers=headers)
        try:
            with urlopen(req, timeout=20) as response:
                return {'ok': True, 'data': json.loads(response.read())}
        except HTTPError as exc:
            return {'ok': False, 'data': json.loads(exc.read())}
    try:
        with patch('main_computer.mcel_slh_first_run.DEFAULT_SLH_MANAGED_PORT', worker_port), \
             patch('main_computer.mcel_slh_first_run.SlhFirstRunController', return_value=ctl), \
             patch('main_computer.mcel_slh_application.observe_slh', side_effect=observed), \
             patch('main_computer.mcel_slh_application.observe_lms_slh_identities', side_effect=observed_identities):
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
                try:
                    page = browser.new_page()
                    errors=[]
                    page.on('pageerror', lambda e: errors.append(str(e)))
                    page.on('dialog',lambda d:d.accept())
                    page.expose_function('first_run_proxy', proxy)
                    page.evaluate('''() => {window.fetch=async(url,opts)=>{
                      const r=await window.first_run_proxy(url,opts);
                      return {ok:r.ok,json:async()=>r.data};
                    };}''')
                    page.set_content(render_slh_page('overview'))
                    page.get_by_text('SLH observation complete', exact=False).wait_for(timeout=15000)
                    page.locator('button[data-intent="intent:start-local"]').click()
                    page.get_by_text('Loopback SLH control service verified', exact=False).wait_for(timeout=15000)
                    assert page.locator('form[data-intent="intent:bootstrap-administrator"]').count()==0
                    assert page.locator('input[name="adminId"]').count()==0
                    assert page.locator('input[name="adminName"]').count()==0
                    assert page.locator('input[name="acknowledgeP256Only"]').count()==0
                    page.locator('button[data-intent="intent:login-administrator"]').click()
                    page.get_by_text('Organization administrator authenticated').wait_for(timeout=15000)
                    summary = page.evaluate('''() => window.__mcelSlh.app.receipts()
                        .filter(x=>x.intentId&&x.intentId.includes('login-administrator'))''')
                    assert 'access_token' not in json.dumps(summary)
                    page.locator('button[data-intent="intent:logout-administrator"]').click()
                    page.get_by_text('Organization session not established').wait_for(timeout=15000)
                    assert errors == []
                finally:
                    browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        for process in procs:
            try:
                process.terminate(); process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                process.kill(); process.wait(timeout=8)
