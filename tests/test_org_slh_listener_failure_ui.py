"""Incident regression: unavailable configured bind address must reach MCEL health."""
from __future__ import annotations

from collections import deque
import json
import shutil
import threading

from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import observe_slh, render_slh_page
from main_computer.slh.organization_controller import OrganizationController
from main_computer.slh.organization_failure import (
    record_listener_bind_failure, read_listener_bind_failure,
)
from main_computer.slh.organization_state import DEFAULTS, save_config


def config(tmp_path):
    return save_config(tmp_path, {**DEFAULTS, 'configured': True, 'enabled': True,
                                  'activation_confirmed': True, 'host': '10.0.0.15', 'api_port': 8899})


def make_controller(tmp_path, error=True):
    ctl = OrganizationController.__new__(OrganizationController)
    ctl.directory = tmp_path
    ctl._lock = threading.RLock()
    ctl._process = None
    ctl._paused = False
    ctl._last_error = 'Organization Service repeatedly exited; restart limit reached' if error else None
    ctl._restarts = deque([1.0, 2.0, 3.0]) if error else deque()
    return ctl


def test_original_windows_bind_error_is_sanitized_and_durable(tmp_path):
    cfg = config(tmp_path)
    report = record_listener_bind_failure(tmp_path, cfg, OSError(10049, 'secret-value-must-not-be-persisted'))
    assert report['error_code'] == 'ORG_SLH_BIND_ADDRESS_UNAVAILABLE'
    assert report['os_error'] == 10049
    assert report['stage'] == 'LISTENER_BIND'
    persisted = (tmp_path / 'organization-listener-failure.json').read_text()
    assert 'secret-value' not in persisted
    assert read_listener_bind_failure(tmp_path, cfg)['bind_host'] == '10.0.0.15'
    assert read_listener_bind_failure(tmp_path, {**cfg, 'host':'10.126.52.217'}) is None


def test_org_child_records_failed_listener_bind_before_exiting(tmp_path, monkeypatch):
    from main_computer.slh import organization_service as service
    cfg = config(tmp_path)

    class Authority:
        def ensure_local_auth_key(self): pass

    def failed_listener(*args, **kwargs):
        raise OSError(10049, 'never-copy-private-exception-text')

    monkeypatch.setattr(service, 'transport_identity', lambda _: (None, 'unused'))
    monkeypatch.setattr(service, 'OrganizationHTTPServer', failed_listener)
    try:
        service._run_ready_service(tmp_path, 1, 1.0, Authority(), cfg)
        assert False, 'expected original bind error'
    except OSError as exc:
        assert exc.errno == 10049
    observed = read_listener_bind_failure(tmp_path, cfg)
    assert observed['error_code'] == 'ORG_SLH_BIND_ADDRESS_UNAVAILABLE'
    assert 'never-copy' not in json.dumps(observed)


def test_controller_keeps_root_cause_at_restart_limit_and_after_restart(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    record_listener_bind_failure(tmp_path, cfg, OSError(10049, 'private'))
    ctl = make_controller(tmp_path)
    state = ctl.status()
    assert state['state'] == 'failed'
    assert state['last_error'].endswith('restart limit reached')
    assert state['failure']['os_error'] == 10049
    assert state['failure']['restart_count'] == 3
    assert state['failure']['restart_limit_reached'] is True
    # New controller instance, same durable failure evidence.
    fresh = make_controller(tmp_path, error=False)
    assert fresh.status()['failure']['error_code'] == 'ORG_SLH_BIND_ADDRESS_UNAVAILABLE'
    # Only verified listener readiness, not process launch, clears the failure.
    monkeypatch.setattr('main_computer.slh.organization_controller.verified_launched_pid', lambda *a, **k: 23)
    fresh._process = type('Child', (), {'pid': 23, 'poll': lambda self: None})()
    fresh._listening = lambda _: True
    assert fresh.status()['state'] == 'running'
    assert fresh.status()['failure'] is None
    assert not (tmp_path / 'organization-listener-failure.json').exists()


def test_local_observation_projects_safe_cause_without_admin_token(tmp_path):
    cfg = config(tmp_path)
    record_listener_bind_failure(tmp_path, cfg, OSError(10049, 'private'))
    ctl = make_controller(tmp_path)
    observed = observe_slh('overview',probe=lambda:('RUNNING',{
        'schema':'main-computer.slh-local-status.v1',
        'initialization':{'state':'INITIALIZED'},
        'administrator_enrollment':{'state':'ENROLLED'},
        'organization_service': ctl.status(),
    }))
    assert observed['organizationFailure']['errorCode'] == 'ORG_SLH_BIND_ADDRESS_UNAVAILABLE'
    assert observed['organizationFailure']['restartLimitReached'] is True
    assert observed['organizationFailure']['bindAddress'] == '10.0.0.15'
    assert observed['overview']['organizationState'] == 'failed'
    assert 'Check the computer' in observed['overview']['nextStep']
    assert 'private' not in json.dumps(observed)


MOCK = r'''() => {
  const stored={};window.mode='failed'; window.requests=[];window.copies=[];
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async s=>window.copies.push(JSON.parse(s))}});
  Object.defineProperty(window,'sessionStorage',{configurable:true,value:{
    getItem:k=>stored[k]??null,setItem:(k,v)=>{stored[k]=String(v)},removeItem:k=>{delete stored[k]}
  }});
  window.fetch=async(url,options={})=>{
    window.requests.push({url:String(url),method:options.method||'GET'});
    const response=p=>({ok:true,json:async()=>p});
    if(url==='/api/applications/slh/identities')return response({
      schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'one',
      identities:[{deviceId:'one',label:'One',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}],
      explanation:'Available'});
    if(url==='/api/applications/slh/observe?view=overview'){
      const down=window.mode==='failed';
      return response({ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
        overview:{serviceState:'RUNNING',observationState:'OBSERVED',
          organizationState:down?'failed':'running',organizationEndpoint:'10.0.0.15:8899'},
        organizationFailure:down?{errorCode:'ORG_SLH_BIND_ADDRESS_UNAVAILABLE',stage:'LISTENER_BIND',
          bindAddress:'10.0.0.15',port:8899,osError:10049,explanation:'safe',
          restartCount:3,restartLimitReached:true,observedAt:'2026-11-15T21:46:00+00:00'}:null});
    }
    if(url==='/api/applications/slh/seh/deployment')return {ok:false,json:async()=>({error:{code:'UNAVAILABLE'}})};
    return {ok:false,json:async()=>({error:'UNEXPECTED'})};
  };
}'''


def test_browser_service_health_and_ewg_then_recovery():
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True, executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page()
            errors=[];page.on('pageerror',lambda error: errors.append(str(error)))
            page.evaluate(MOCK)
            page.set_content(render_slh_page())
            page.wait_for_function("""() => window.__mcelSlh?.diagnostics()?.organizationService?.failure?.osError === 10049""", timeout=15000)
            assert 'ORG-SLH failed to start' in page.locator('#mcel-workbench-status').inner_text()
            assert 'WinError 10049' in page.locator('#mcel-workbench-status').inner_text()
            details=page.locator('#mcel-slh-org-failure-details')
            assert details.is_visible()
            details.locator('summary').click()
            assert 'restart limit reached' in details.inner_text()
            diag=page.evaluate('window.__mcelSlh.diagnostics()')
            assert diag['organizationService']['failure']['errorCode']=='ORG_SLH_BIND_ADDRESS_UNAVAILABLE'
            assert any(row['code']=='org-slh-bind-address-unavailable' for row in diag['findings'])
            page.locator('[data-mcel-diagnostic-title-control]').click()
            page.wait_for_function('window.copies.length === 1')
            copied=page.evaluate('window.copies[0]')
            assert copied['diagnosticEvidence']['operational']['organizationService']['failure']['osError']==10049
            assert copied['diagnosticEvidence']['operational']['organizationService']['failure']['stage']=='LISTENER_BIND'
            assert copied['counts']['errors'] >= 1
            assert any(issue['code']=='org-slh-bind-address-unavailable' for issue in copied['issues'])
            assert not any(row['method']=='POST' and '/setup/action' in row['url'] for row in page.evaluate('window.requests'))
            page.evaluate("() => {window.mode='running';window.dispatchEvent(new Event('focus'));}")
            page.wait_for_function("""() => window.__mcelSlh.diagnostics().organizationService.failure === null""", timeout=15000)
            assert not details.is_visible()
            assert page.locator('#mcel-workbench-status').get_attribute('data-error')=='false'
            assert any(e['stage']=='organization-service-health' and e['outcome']=='recovered'
                       for e in page.evaluate('window.__mcelSlh.diagnostics().timeline'))
            assert not errors, errors
        finally:
            browser.close()
