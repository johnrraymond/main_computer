"""Consequence-level acceptance for live ORG-SLH configuration changes."""
from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import render_slh_page
from main_computer.mcel_slh_first_run import SlhFirstRunController, FirstRunError
from main_computer.slh.organization_state import read_config, save_config, DEFAULTS
from main_computer.slh.organization_controller import OrganizationController
from main_computer.slh.api import ENDPOINTS, SLHAPI
from main_computer.slh.authority import SLHError


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def test_failed_live_rebind_restores_previous_tls_listener_and_enabled_state(tmp_path, monkeypatch):
    """A bound destination port must not strand a formerly running service."""
    import main_computer.mcel_slh_first_run as first_run
    ports = [_free_port() for _ in range(3)]
    while len(set(ports)) != len(ports):
        ports = [_free_port() for _ in range(3)]
    worker, original_api, local_port = ports
    monkeypatch.setattr(first_run, 'DEFAULT_SLH_MANAGED_PORT', worker)
    state, identities = tmp_path/'state', tmp_path/'identities'
    processes = []
    def spawn(*args, **kwargs):
        proc = subprocess.Popen(*args, **kwargs)
        processes.append(proc)
        return proc
    controller = SlhFirstRunController(state_dir=state, identity_dir=identities,
                                       local_port=local_port, runner=spawn)
    identity = controller.identities.ensure_default_identity()['device_id']
    blocker = socket.socket()
    blocker.bind(('127.0.0.1', 0))
    blocked_port = blocker.getsockname()[1]
    blocker.listen()
    try:
        controller.start()
        args = {'adminDeviceId': identity, 'bindAddress': '127.0.0.1', 'port': original_api}
        assert controller.execute('configure-and-enable', args)['outcome'] == 'RUNNING'
        baseline = read_config(state)
        assert baseline['enabled'] and baseline['api_port'] == original_api
        with pytest.raises(FirstRunError) as failure:
            controller.execute('configure-and-enable', {**args, 'port': blocked_port})
        assert failure.value.code == 'ORGANIZATION_RECONFIGURE_FAILED_RESTORED'
        assert read_config(state) == baseline
        assert controller.status()['organization'] == 'running'
        assert controller.execute('configure-and-enable', args)['outcome'] == 'ALREADY_RUNNING'
        controller.execute('disable', {'adminDeviceId': identity})
    finally:
        blocker.close()
        for proc in processes:
            try:
                proc.terminate()
                proc.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait(timeout=8)


def test_reconfigure_prevalidates_and_preserves_worker_policy(tmp_path, monkeypatch):
    class Authority:
        directory = tmp_path
        def initialized(self): return True
    class Process:
        pid = 4444
        exited = False
        def poll(self): return 0 if self.exited else None
        def terminate(self): self.exited = True
        def wait(self, timeout=None): return 0
    spawned = []
    def spawn(*args, **kwargs):
        proc = Process()
        spawned.append(proc)
        return proc
    old = {**DEFAULTS, 'configured': True, 'enabled': True,
           'activation_confirmed': True, 'host': '127.0.0.1',
           'api_port': 9019, 'worker_port': 9018,
           'workers_enabled': True, 'seh_enabled': True}
    save_config(tmp_path, old)
    controller = OrganizationController(Authority(), runner=spawn, automatic_start=False)
    monkeypatch.setattr(controller, '_listening', lambda config: True)
    controller._process = spawn()
    try:
        with pytest.raises(SLHError) as invalid:
            controller.reconfigure_and_enable({'api_port': 9018})
        assert invalid.value.code == 'INVALID_ORGANIZATION_CONFIG'
        assert read_config(tmp_path) == old
        assert not spawned[0].exited
        result = controller.reconfigure_and_enable({'host': '0.0.0.0', 'api_port': 9029})
        assert result['state'] == 'running'
        updated = read_config(tmp_path)
        assert updated['worker_port'] == old['worker_port']
        assert updated['workers_enabled'] is True and updated['seh_enabled'] is True
        assert updated['enabled'] is True and updated['api_port'] == 9029
        assert spawned[0].exited and len(spawned) == 2
    finally:
        controller.close()


def test_reconfigure_endpoint_is_an_authorized_loopback_operation():
    route = next(r for r in ENDPOINTS if r.path == '/v1/local/organization/configure-and-enable')
    assert (route.method, route.access, route.handler) == ('POST', 'admin', 'local_org_reconfigure_enable')
    class Authority:
        def authenticate(self, token, admin=False):
            raise SLHError('AUTH_REQUIRED', 401)
    class Controller:
        def reconfigure_and_enable(self, changes):
            raise AssertionError('unauthorized mutation')
    api = SLHAPI(Authority(), organization_controller=Controller())
    status, failure = api.call('POST', route.path, {'host': '127.0.0.1', 'api_port': 9001},
                               loopback=True)
    assert status == 401 and failure['error']['code'] == 'AUTH_REQUIRED'
    status, failure = api.call('POST', route.path, {'host': '127.0.0.1', 'api_port': 9001},
                               loopback=False)
    assert status == 403 and failure['error']['code'] == 'LOCAL_TRANSPORT_ONLY'


def test_mcel_shows_actual_backend_error_code_instead_of_generic_refusal():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page = browser.new_page(viewport={'width': 1040, 'height': 768})
            page.on('dialog', lambda d: d.accept())
            page.evaluate('''() => {
              const store={};Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
                getItem:k=>store[k]??null,setItem:(k,v)=>store[k]=String(v),removeItem:k=>delete store[k]}});
              window.fetch=async(url,opts={})=>{
                if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
                  schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'one',
                  identities:[{deviceId:'one',label:'One',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}],
                  explanation:'available'})};
                if(url==='/api/applications/slh/observe?view=overview')return {ok:true,json:async()=>({
                  ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
                  overview:{organizationState:'running',organizationEndpoint:'127.0.0.1:8899'}})};
                if(url==='/api/applications/slh/setup/action')return {ok:false,json:async()=>({
                  ok:false,error:'ORGANIZATION_RECONFIGURE_FAILED_RESTORED'})};
                throw Error('UNEXPECTED_API:'+url);
              };
            }''')
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            page.get_by_role('button', name='Configure ORG-SLH').click()
            # The same refusal is now visible in the task feedback, the inspector's
            # latest-request summary, and retained Activity receipts.
            page.locator('[data-mcel-natural-scroller="inspection"] [data-mcel-request-result]').get_by_text(
                'ORGANIZATION_RECONFIGURE_FAILED_RESTORED', exact=False).wait_for(timeout=10000)
        finally:
            browser.close()
