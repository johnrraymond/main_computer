"""V2 target-selection oracle independent of layout pixels and service mutation."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import ROUTES, render_slh_page
from main_computer.mcel_slh_network_probe import NetworkSlhError, inspect_network_target, validate_target
from main_computer.mcel_slh_network_route import handle_network_slh_inspect
from main_computer.mcel_slh_target_navigation import TARGET_ROUTES, target_tabs

ROOT=Path(__file__).resolve().parents[1]
ORACLE=ROOT/'tests/fixtures/mcel_oracles/target-selection/oracle.v2.json'


def test_v2_contract_is_explicit_and_does_not_weaken_v1():
    data=json.loads(ORACLE.read_text(encoding='utf-8'))
    assert data['schema']=='mcel.slh.target-selection-oracle.v2'
    assert [x['id'] for x in data['targets']]==['local','network']
    assert {x['canonical_url'] for x in data['targets']}==set(TARGET_ROUTES.values())
    assert all(data['requirements'].values())
    assert data['scenarios'][3]['local_status_requests']==0
    assert 'operational-workflows/oracle.v1.json' in data['frozen_v1']


def test_both_target_tabs_are_real_canonical_links_and_old_routes_remain():
    assert ROUTES['/applications/slh']=='overview'
    assert ROUTES['/applications/slh/workers']=='worker-bank'
    assert ROUTES['/applications/slh/local']=='overview'
    assert ROUTES['/applications/slh/local/workers']=='worker-bank'
    assert ROUTES['/applications/slh/network']=='network'
    for view, selected in [('overview','local'),('worker-bank','local'),('network','network')]:
        page=render_slh_page(view)
        for target, route in TARGET_ROUTES.items():
            import re
            assert re.search(r'<a\s+role="tab"\s+href="'+re.escape(route)+r'"',page)
            assert 'href="javascript:' not in page
        assert f'data-slh-target="{selected}" aria-selected="true"' in page
        assert 'onclick=' not in page.split('role="tablist"')[1].split('</nav>')[0]


def test_target_tabs_do_not_make_remote_inspection_local_authority():
    """Historical V2 retains target safety, not distinct MCEL app identity."""
    local=render_slh_page('overview')
    network=render_slh_page('network')
    assert 'data-slh-target="local" aria-selected="true"' in local
    assert 'data-mcel-slh-target="network"' in network
    assert 'role="tablist"' in network
    assert 'href="/applications/slh/local"' in network
    assert 'href="/applications/slh/network"' in local
    # Semantic application identity and post-auth parity are governed by V4.


@pytest.mark.parametrize('address',[
    '127.0.0.1','localhost','169.254.1.1','8.8.8.8','192.0.2.4','0.0.0.0',
    '192.168.1.2.evil.test','http://192.168.1.2','[::1]','::1','fe80::1','2001:db8::1',
])
def test_unsafe_remote_address_never_connects(address):
    with pytest.raises(NetworkSlhError):
        validate_target(address,8899,'a'*64)


def test_private_address_and_strict_pin_type():
    assert validate_target('192.168.1.20',8899,'A'*64)==('192.168.1.20',8899,'a'*64)
    assert validate_target('10.1.2.3',8899,'a'*64)[0]=='10.1.2.3'
    assert validate_target('fd00::8',8899,'b'*64)[0]=='fd00::8'
    for port in (None,'8899',0,65536,True):
        with pytest.raises(NetworkSlhError):validate_target('10.0.0.4',port,'a'*64)
    for fingerprint in (None,'a'*63,'g'*64,''): 
        with pytest.raises(NetworkSlhError):validate_target('10.0.0.4',8899,fingerprint)


class FakeConnection:
    def __init__(self,host,port,timeout,context,*,der=b'network-cert',bad_schema=False):
        self.peer_der=der
        self.sock=SimpleNamespace(getpeercert=lambda binary_form:der)
        self.requests=[]
        self.connected=False
        self.closed=False
        self.bad_schema=bad_schema
    def connect(self):self.connected=True
    def close(self):self.closed=True
    def request(self,method,path,headers=None):
        assert self.connected
        self.requests.append((method,path))
    def getresponse(self):
        path=self.requests[-1][1]
        if path=='/v1/org/identity':
            data={'schema':'main-computer.slh-org-identity.v1',
                  'tls_sha256':hashlib.sha256(self.peer_der).hexdigest()}
        else:
            data={'schema': 'incorrect' if self.bad_schema else 'main-computer.slh-org-status.v1',
                  'operational':True}
        return SimpleNamespace(status=200,read=lambda amount:json.dumps(data).encode('utf-8'),
                               getheader=lambda name,default=None:'application/json')


def test_certificate_pin_checked_before_any_http_request():
    connection=FakeConnection('10.2.3.4',8899,3,None)
    def factory(*args,**kwargs):return connection
    with pytest.raises(NetworkSlhError,match='TLS_PIN_MISMATCH'):
        inspect_network_target('10.2.3.4',8899,'f'*64,connection_factory=factory)
    assert connection.connected
    assert connection.requests==[]
    assert connection.closed


def test_verified_remote_identity_remains_unauthenticated_and_read_only():
    connection=FakeConnection('10.2.3.4',8899,3,None)
    pin=hashlib.sha256(connection.peer_der).hexdigest()
    value=inspect_network_target('10.2.3.4',8899,pin,connection_factory=lambda *a,**k:connection)
    assert value['tlsSha256']==pin
    assert value['organizationState']=='RUNNING'
    assert value['authenticationState']=='NOT_AUTHENTICATED'
    assert connection.requests==[('GET','/v1/org/identity'),('GET','/v1/org/status')]
    assert connection.closed


def test_same_origin_post_is_required_before_any_remote_probe(monkeypatch):
    called=[]
    monkeypatch.setattr('main_computer.mcel_slh_network_route.inspect_network_target',
                        lambda *a:called.append(a))
    class Handler:
        client_address=('127.0.0.1',40405)
        server=SimpleNamespace(server_address=('127.0.0.1',8765))
        headers={'Host':'127.0.0.1:8765','Origin':'https://evil.example',
                 'Sec-Fetch-Site':'cross-site','Content-Type':'application/json',
                 'Content-Length':'90'}
        rfile=io.BytesIO(b'')
        sent=[]
        def _send_json(self,value,status=200):self.sent.append((status,value))
    handler=Handler()
    handle_network_slh_inspect(handler)
    assert not called
    assert handler.sent and handler.sent[0][1]['error']['code']=='LOCAL_ORIGIN_REQUIRED'


def test_unverified_network_tab_cannot_perform_local_setup_or_mutation():
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':900,'height':800})
            errors=[]
            page.on('pageerror',lambda err:errors.append(str(err)))
            page.set_content(render_slh_page('network'))
            page.wait_for_function('''()=>document.querySelector('#mcel-workbench')?.children.length > 0''')
            assert page.get_by_role('tab',name='Network SLH').get_attribute('aria-selected')=='true'
            assert page.get_by_role('tab',name='This Computer').get_attribute('href')=='/applications/slh/local'
            assert page.locator('form[data-intent="intent:connect-network-slh"]').count()==1
            assert page.locator('input[name="tlsSha256"]').count()==0
            assert page.locator('[data-intent="intent:bootstrap-administrator"]').count()==0
            # Login may be declared by the shared semantic app, but must be
            # unavailable until remote trust and authorization are established.
            assert page.locator('button[data-intent="intent:rename-worker"]:visible').count()==0
            assert errors==[],errors
        finally:
            browser.close()


def test_network_connection_browser_uses_only_bounded_network_port():
    # The full paired TLS + signed-device scenario is exercised in the
    # dedicated integration oracle; retain it as part of V2 target acceptance.
    from test_mcel_slh_network_connection_integration import (
        test_network_browser_connect_uses_current_selected_identity_and_inspector,
    )
    test_network_browser_connect_uses_current_selected_identity_and_inspector()


def test_real_viewport_routes_local_and_network_targets_without_cross_target_fallback(tmp_path):
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    from threading import Thread
    if not (ROOT / 'game_projects/webgl-demo/game.json').exists():
        pytest.skip('Export lacks game_projects/webgl-demo required by the existing viewport')
    from main_computer.config import MainComputerConfig
    from main_computer.viewport_server import ViewportServer
    server=ViewportServer(('127.0.0.1',0), MainComputerConfig(workspace=ROOT),verbose=False)
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    try:
        for path,target in [('/applications/slh/local','local'),('/applications/slh/network','network')]:
            with urlopen(origin+path,timeout=15) as response:
                assert response.status==200
                html=response.read().decode('utf-8')
                assert f'data-slh-target="{target}" aria-selected="true"' in html
                assert '/applications/slh/network' in html and '/applications/slh/local' in html
        body=json.dumps({'address':'8.8.8.8','port':8899,'tlsSha256':'a'*64}).encode()
        request=Request(origin+'/api/applications/slh/network/inspect',data=body,method='POST',
                        headers={'Origin':origin,'Content-Type':'application/json','Sec-Fetch-Site':'same-origin'})
        with pytest.raises(HTTPError) as exc:
            urlopen(request,timeout=15)
        assert exc.value.code==400
        assert json.loads(exc.value.read())['error']['code']=='PRIVATE_NETWORK_ADDRESS_REQUIRED'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=4)
