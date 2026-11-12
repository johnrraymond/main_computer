"""V4-B: MCEL consumes LMS-SLH identities; organization still authorizes use."""
from __future__ import annotations

import json
import re
import socket
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from main_computer.mcel_slh_application import (
    IDENTITIES_SCHEMA, compile_slh_application, observe_lms_slh_identities,
    observe_slh, render_slh_page,
)
from main_computer.slh.authenticator_store import LocalIdentityStore
from main_computer.slh.local_service import create_server


def _free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _status(initialized=True, running=True):
    return ('RUNNING', {
        'schema': 'main-computer.slh-local-status.v1',
        'local_service': {'state': 'running', 'binding': 'loopback'},
        'initialization': {'state': 'INITIALIZED' if initialized else 'UNCONFIGURED'},
        'administrator_enrollment': {'state': 'ENROLLED' if initialized else 'NOT_ESTABLISHED'},
        'organization_service': {'state': 'running' if running else 'not_configured',
            'configured': running, 'bind_host': '127.0.0.1' if running else None,
            'api_port': 8899 if running else None, 'listening': running,
            'network_access': 'LOOPBACK_ONLY' if running else 'NOT_CONFIGURED'}
    })


def _identity_response():
    return {
        'schema': IDENTITIES_SCHEMA, 'state': 'AVAILABLE', 'defaultDeviceId': 'identity-default',
        'explanation': 'Local identities discovered; organizational authority not yet verified',
        'identities': [
            {'deviceId': 'identity-default', 'label': 'Default',
             'fingerprint': 'a'*64, 'defaultStatus': 'DEFAULT'},
            {'deviceId': 'identity-second', 'label': 'Second',
             'fingerprint': 'b'*64, 'defaultStatus': 'ADDITIONAL'},
        ],
    }


def test_v4b_real_local_identity_api_recovers_default_without_new_keys(tmp_path):
    identity_dir=tmp_path/'identities'
    state_dir=tmp_path/'state'
    server=create_server(state_dir=state_dir,identity_dir=identity_dir,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        before=LocalIdentityStore(identity_dir).list()
        assert before['default_device_id']
        observed=observe_lms_slh_identities(port=server.server_port)
        assert observed['state']=='AVAILABLE'
        assert observed['defaultDeviceId']==before['default_device_id']
        assert len(observed['identities'])==len(before['identities'])==1
        assert 'private_key' not in json.dumps(observed)
        assert '.key.pem' not in json.dumps(observed)
        assert LocalIdentityStore(identity_dir).list()==before
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_v4b_missing_service_is_not_an_empty_verified_store():
    observed=observe_lms_slh_identities(port=_free_port())
    assert observed['state']=='UNAVAILABLE'
    assert observed['identities']==[]
    assert observed['defaultDeviceId']==''


def test_v4b_rejects_inconsistent_identity_authority():
    class BadResponse:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,n):return json.dumps({
            'identities':[{'device_id':'first','label':'One','fingerprint':'a'*64,'usable':True}],
            'invalid_identities':[], 'default_device_id':'missing',
        }).encode()
    class BadOpener:
        def open(self,*args,**kwargs):return BadResponse()
    with patch('main_computer.mcel_slh_application.build_opener',return_value=BadOpener()):
        assert observe_lms_slh_identities()['state']=='UNVERIFIED'


def test_v4b_semantic_identity_selection_not_manual_device_id():
    ir,composition=compile_slh_application()
    assert composition['policy']=='mcel.semantic-structure.p3'
    assert not ir['layouts']
    declared={intent['id']:intent for intent in ir['intents']}
    assert declared['intent:login-administrator']['risk']=='consequential'
    assert declared['intent:login-administrator']['confirmationRequired'] is False
    assert all(intent.get('confirmationRequired') is not False
               for name,intent in declared.items() if name!='intent:login-administrator')
    assert {'intent:refresh-identities','intent:refresh-identity-status','intent:choose-identity'} <= {
        intent['id'] for intent in ir['intents']}
    page=render_slh_page()
    assert 'available-local-identities' in page
    for node in ir['surfaces']:
        assert 'htmlBindings' not in node or not node['htmlBindings']
    from main_computer.mcel_slh_application import ROOT
    source=(ROOT/'mcel_apps/slh/application.js').read_text()
    assert 'fields: {adminDeviceId: text}' not in source
    assert 'fieldState: {adminDeviceId:adminDevice}' not in source


def test_v4b_local_route_is_bounded(tmp_path):
    from main_computer.config import MainComputerConfig
    with patch('main_computer.game_web_loader.read_game_bundle',return_value='<div></div>'):
        from main_computer.viewport_server import ViewportServer
    server=ViewportServer(('127.0.0.1',0),MainComputerConfig(workspace=Path(__file__).resolve().parents[1]),verbose=False)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with patch('main_computer.mcel_slh_application.observe_lms_slh_identities',return_value=_identity_response()):
            with urlopen(base+'/api/applications/slh/identities') as response:
                result=json.load(response)
            assert result['defaultDeviceId']=='identity-default'
            for headers in ({'Host': 'attacker.example'}, {'Origin': 'http://attacker.example'},
                            {'Sec-Fetch-Site': 'cross-site'}):
                with pytest.raises(HTTPError) as exc:
                    urlopen(Request(base+'/api/applications/slh/identities', headers=headers))
                assert exc.value.code==403
            with pytest.raises(HTTPError) as exc:urlopen(base+'/api/applications/slh/identities?extra=1')
            assert exc.value.code==400
            with patch('main_computer.viewport_route_dispatch._hard_halt_client_is_local',return_value=False):
                with pytest.raises(HTTPError) as exc:urlopen(base+'/api/applications/slh/identities')
                assert exc.value.code==403
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)


def test_v4b_browser_reflects_selected_identity_without_exposing_login():
    from playwright.sync_api import sync_playwright
    import shutil
    identities=_identity_response()
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page()
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.evaluate("""identities=>{
                window.v4bCalls=[];
                window.fetch=async(url,opts={})=>{
                    window.v4bCalls.push({url,method:opts.method,body:opts.body||null});
                    if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>identities};
                    throw Error('No lifecycle operation allowed: '+url);
                };
            }""",identities)
            page.set_content(render_slh_page(),wait_until='load')
            page.wait_for_function("()=>window.__mcelSlh?.app.readState('state:selected-local-identity')==='identity-default'")
            assert not errors,errors
            assert page.locator('input[name="adminDeviceId"]').count()==0
            assert page.locator('input[name="deviceId"]').count()==0
            assert page.locator('[data-record-key="identity-default"]').count()==1
            assert page.locator('[data-record-key="identity-second"]').count()==1
            assert page.locator('[data-mcel-selected-details="identity-default"]').count()==1
            assert not page.locator('button[data-intent="intent:login-administrator"]').count()
            assert page.locator('button[data-mcel-draft-submit]').is_disabled()
            page.locator('[data-record-key="identity-second"] button[data-select-key="identity-second"]').click()
            page.wait_for_function("()=>window.__mcelSlh.app.readState('state:selected-local-identity')==='identity-second'")
            assert page.locator('[data-mcel-selected-details="identity-second"]').count()==1
            result=page.evaluate("""async()=>{
                return (await window.__mcelSlh.app.execute('intent:login-administrator')).disposition;
            }""")
            assert result in {'failed','refused-before-attempt'}
            assert all(x['url']=='/api/applications/slh/identities' and x['method']=='GET'
                       for x in page.evaluate('window.v4bCalls'))
            assert not errors,errors
        finally:browser.close()

def test_login_confirmation_fix_preserves_service_start_confirmation():
    """Only authentication loses the prompt; service operations keep their gate."""
    import shutil
    from playwright.sync_api import sync_playwright
    from main_computer.mcel_slh_application import ROOT

    source=(ROOT/'main_computer/web/applications/scripts/mcel-semantic-slh-ports.js').read_text(encoding='utf-8')
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page()
            prompts=[]
            page.on('dialog',lambda dialog:(prompts.append(dialog.message),dialog.dismiss()))
            page.set_content('<!doctype html><html><body></body></html>')
            page.evaluate(source)
            result=page.evaluate('''async () => {
              window.testCalls=[];
              const port=McelSemanticSlhPorts.create({appId:"slh",fetcher:async url=>{
                window.testCalls.push(url); throw Error("unexpected network call");
              }});
              try {
                await port.invoke({appId:"slh",intentId:"intent:start-local",
                  risk:"consequential",effectId:"effect:start-local.request",
                  capabilityId:"capability:slh-first-run",operation:"start",
                  runtimeMethod:"startSlhLocal",arguments:{}});
                return "accepted";
              } catch(e) { return String(e.message); }
            }''')
            assert result=='MCEL_SLH_PORT:administrator-canceled'
            assert len(prompts)==1 and 'Start the local SLH control service' in prompts[0]
            assert page.evaluate('window.testCalls')==[]
        finally:
            browser.close()
