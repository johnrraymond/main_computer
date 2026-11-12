"""Configuration lifecycle: explicit, persistent, signed, and browser-native."""
from __future__ import annotations

import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright
import pytest

from main_computer.mcel_slh_application import compile_slh_application, render_slh_page
from main_computer.mcel_slh_first_run import SlhFirstRunController, FirstRunError


def test_compiled_lifecycle_is_explicit_and_authority_bounded():
    ir, _ = compile_slh_application()
    ids = {x['id']: x for x in ir['intents']}
    assert 'intent:login-administrator' not in ids
    assert set(ids) & {
        'intent:configure-and-enable-organization', 'intent:disable-organization',
    } == {'intent:configure-and-enable-organization', 'intent:disable-organization'}
    assert ids['intent:configure-and-enable-organization']['risk'] == 'consequential'
    assert ids['intent:disable-organization']['risk'] == 'consequential'
    nodes = {x['id']: x for x in ir['surfaces'][0]['nodes']}
    assert nodes['surface-node:slh.configure-org-slh']['intent']['ref'] == 'intent:configure-and-enable-organization'
    assert nodes['surface-node:slh.disable-org-slh']['intent']['ref'] == 'intent:disable-organization'
    assert 'first-administrator-identity' in render_slh_page()


def test_lifecycle_browser_explicit_transitions_preserve_selected_identity():
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1280,'height':820})
            page.on('dialog',lambda d:d.accept())
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.evaluate('''() => {
              const storage={};window.slhCalls=[];window.orgState='not_configured';window.orgEndpoint='0.0.0.0:8899';
              Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
                getItem:k=>storage[k]??null,setItem:(k,v)=>storage[k]=String(v),removeItem:k=>delete storage[k]
              }});
              window.fetch=async(url,options={})=>{
                const req={url:String(url),method:options.method||'GET',
                  action:options.headers?.['X-MCEL-SLH-Operation']||'',
                  body:options.body?JSON.parse(options.body):null};
                window.slhCalls.push(req);
                if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
                  schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'one',
                  identities:[{deviceId:'one',label:'One',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'},
                    {deviceId:'two',label:'Two',fingerprint:'b'.repeat(64),defaultStatus:'ADDITIONAL'}],
                  explanation:'Identity available'})};
                if(url==='/api/applications/slh/observe?view=overview')return {ok:true,json:async()=>({
                  ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
                  overview:{organizationState:window.orgState,organizationEndpoint:window.orgEndpoint}})};
                if(url==='/api/applications/slh/setup/action'){
                  const action=req.body.action;
                  if(action==='configure-and-enable'){
                    window.orgState='running';window.orgEndpoint=req.body.arguments.bindAddress+':'+req.body.arguments.port;return {ok:true,json:async()=>({
                      outcome:'RUNNING',message:'Configured and running',deviceId:req.body.arguments.adminDeviceId})};
                  }
                  if(action==='disable'){
                    window.orgState='disabled';return {ok:true,json:async()=>({
                      outcome:'DISABLED',message:'Stopped and disabled',deviceId:req.body.arguments.adminDeviceId})};
                  }
                }
                throw Error('UNEXPECTED_API:'+url);
              };
            }''')
            page.set_content(render_slh_page())
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:selected-local-identity')==='one'")
            assert not any(c['method']=='POST' for c in page.evaluate('window.slhCalls'))
            form=page.locator('form[data-intent="intent:configure-and-enable-organization"]')
            assert form.count()==1
            page.locator('[data-select-key="two"]').click()
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:selected-local-identity')==='two'")
            form.locator('[name="port"]').fill('9023')
            form.get_by_role('button',name='Configure ORG-SLH').click()
            page.wait_for_function("() => window.slhCalls.some(c=>c.action==='configure-and-enable')")
            outgoing=page.evaluate("window.slhCalls.filter(c=>c.action==='configure-and-enable')")
            assert len(outgoing)==1
            assert outgoing[0]['body']['arguments']=={
                'adminDeviceId':'two','bindAddress':'0.0.0.0','port':9023}
            page.wait_for_function("() => window.__mcelSlh?.app.readState('state:organization-lifecycle')?.state==='running' ")
            assert form.locator('[name="port"]').input_value()=='9023' 
            page.locator('button[data-intent="intent:disable-organization"]').click()
            page.wait_for_function("() => window.slhCalls.some(c=>c.action==='disable')")
            assert page.evaluate("window.slhCalls.filter(c=>c.action==='disable')[0].body.arguments.adminDeviceId")=='two'
            page.wait_for_function("() => window.__mcelSlh.app.readState('state:organization-lifecycle')?.state==='disabled' ")
            assert form.locator('[name="port"]').input_value()=='9023' 
            assert page.evaluate("window.__mcelSlh.app.readState('state:selected-local-identity')")=='two'
            assert errors==[], errors
        finally:
            browser.close()


def test_configure_refuses_invalid_bind_before_touching_service(tmp_path):
    ctl=SlhFirstRunController(state_dir=tmp_path)
    for address in ('8.8.8.8','169.254.3.2','abc','127.0.0.2'):
        with pytest.raises(FirstRunError):
            ctl.execute('configure-and-enable',{'adminDeviceId':'dev','bindAddress':address,'port':8899})
    with pytest.raises(FirstRunError) as err:
        ctl.execute('configure-and-enable',{'adminDeviceId':'dev','bindAddress':'0.0.0.0','port':8897})
    assert err.value.code=='INVALID_PORT'
    for action in ('configure-and-enable','disable'):
        with pytest.raises(FirstRunError) as err:
            ctl.execute(action, {'wrong':'fields'})
        assert err.value.code=='INVALID_SETUP_ARGUMENTS'


def _free_port():
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def test_real_disposable_configure_starts_persists_disable_and_reconfigure(tmp_path, monkeypatch):
    import subprocess
    from main_computer.slh.organization_state import read_config
    import main_computer.mcel_slh_first_run as first_run
    worker_port,api_port,second_port,local_port=(_free_port() for _ in range(4))
    while len({worker_port,api_port,second_port,local_port})!=4:
        worker_port,api_port,second_port,local_port=(_free_port() for _ in range(4))
    monkeypatch.setattr(first_run,'DEFAULT_SLH_MANAGED_PORT',worker_port)
    state=tmp_path/'state';identities=tmp_path/'identities';procs=[]
    def spawn(*args,**kw):
        proc=subprocess.Popen(*args,**kw);procs.append(proc);return proc
    controller=SlhFirstRunController(state_dir=state,identity_dir=identities,local_port=local_port,runner=spawn)
    identity=controller.identities.ensure_default_identity()['device_id']
    try:
        assert controller.execute('start',{})['outcome']=='STARTED'
        assert controller.status()['organization']=='not_configured'
        args={'adminDeviceId':identity,'bindAddress':'127.0.0.1','port':api_port}
        result=controller.execute('configure-and-enable',args)
        assert result['outcome']=='RUNNING',result
        assert controller.status()['organization']=='running'
        cfg=read_config(state)
        assert cfg['enabled'] is True and cfg['configured'] is True and cfg['activation_confirmed'] is True
        assert cfg['host']=='127.0.0.1' and cfg['api_port']==api_port
        # A deliberate Configure directly rebinds a running organization;
        # the user must not first click Disable.
        result=controller.execute('configure-and-enable',{**args,'port':second_port})
        assert result['outcome']=='RUNNING',result
        cfg=read_config(state)
        assert cfg['enabled'] is True and cfg['api_port']==second_port
        assert cfg['worker_port']==worker_port
        assert controller.status()['organization']=='running'
        # Repeating the same listener is idempotent, without a restart.
        result=controller.execute('configure-and-enable',{**args,'port':second_port})
        assert result['outcome']=='ALREADY_RUNNING'
        # Login authenticates only. It did not initiate configuration or start.
        assert controller.execute('login',{'adminDeviceId':identity})['role']=='admin'
        stopped=controller.execute('disable',{'adminDeviceId':identity})
        assert stopped['outcome']=='DISABLED'
        cfg=read_config(state)
        assert cfg['enabled'] is False and cfg['configured'] is True and cfg['api_port']==second_port
        assert controller.status()['organization']=='disabled'
        with pytest.raises(FirstRunError) as err:
            controller.execute('login',{'adminDeviceId':identity})
        assert err.value.code=='ORGANIZATION_NOT_RUNNING'
        result=controller.execute('configure-and-enable',{**args,'port':second_port})
        assert result['outcome']=='RUNNING',result
        cfg=read_config(state)
        assert cfg['api_port']==second_port and cfg['enabled'] is True
        controller.execute('disable',{'adminDeviceId':identity})
    finally:
        for proc in procs:
            try: proc.terminate();proc.wait(timeout=8)
            except (OSError,subprocess.TimeoutExpired):
                proc.kill();proc.wait(timeout=8)


def test_org_autostarts_from_persisted_enable_and_stays_stopped_after_disable(tmp_path):
    from main_computer.slh.organization_state import DEFAULTS, read_config, save_config
    from main_computer.slh.organization_controller import OrganizationController
    class Authority:
        directory=tmp_path
        def initialized(self): return True
    class Process:
        pid=42424
        dead=False
        def poll(self):return 0 if self.dead else None
        def terminate(self):self.dead=True
        def wait(self,timeout=None):return 0
    launched=[]
    def spawn(*args,**kw):
        launched.append(args);return Process()
    save_config(tmp_path,{**DEFAULTS,'configured':True,'enabled':True,
        'activation_confirmed':True,'host':'127.0.0.1'})
    ctl=OrganizationController(Authority(),runner=spawn,automatic_start=True)
    try:
        assert len(launched)==1
        assert read_config(tmp_path)['enabled'] is True
        ctl.disable()
        assert read_config(tmp_path)['enabled'] is False
    finally:
        ctl.close()
    launched.clear()
    ctl2=OrganizationController(Authority(),runner=spawn,automatic_start=True)
    try:
        assert launched==[]
        assert read_config(tmp_path)['configured'] is True
    finally:
        ctl2.close()
