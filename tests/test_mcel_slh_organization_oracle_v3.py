"""Oracle V3: actual binding and enrollment, never infer an admin from setup."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from main_computer.mcel_slh_application import observe_slh, render_slh_page
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.local_service import local_status
from main_computer.slh.organization_controller import OrganizationController
from main_computer.slh.organization_state import initialize_disabled, save_config, DEFAULTS, organization_status
from main_computer.mcel_slh_target_navigation import TARGET_ROUTES

ROOT=Path(__file__).resolve().parents[1]
ORACLE=ROOT/'tests/fixtures/mcel_oracles/organization-observation/oracle.v3.json'
CONTRACT=json.loads(ORACLE.read_text(encoding='utf-8'))


def case_payload(case):
    org={'configured':case['host'] is not None,'state':case['org'],
         'listening':case['listening'],'bind_host':case['host'],'api_port':case['port']}
    if case['host'] is not None:
        org['network_access']=('LOOPBACK_ONLY' if case['host'].startswith('127.') else
                'BIND_CONFIGURED_REACHABILITY_UNVERIFIED')
    return {'schema':'main-computer.slh-local-status.v1',
            'local_service':{'state':'running','binding':'loopback','host':'127.0.0.1','port':8897},
            'initialization':{'initialized':case['initialized'],
                              'setup_phase':'INITIALIZED' if case['initialized'] else 'UNCONFIGURED'},
            'organization_service':org,
            **({'administrator_enrollment':{'state':case['admin'],'local_credential':'NOT_VERIFIED'}}
               if case['admin'] else {})}


def test_v3_contract_extends_not_replaces_existing_oracles():
    assert CONTRACT['schema']=='mcel.slh.organization-observation-oracle.v3'
    assert all(CONTRACT['requirements'].values())
    assert {x['id'] for x in CONTRACT['scenarios']} >= {'legacy-without-admin',
        'enrolled-loopback','enrolled-wildcard','running-but-tls-unverified','authority-unverified'}
    assert set(TARGET_ROUTES)=={'local','network'}


@pytest.mark.parametrize('case', CONTRACT['scenarios'], ids=lambda c:c['id'])
def test_observation_preserves_authority_distinctions(case):
    result=observe_slh('overview',probe=lambda:('RUNNING',case_payload(case)))
    ov=result['overview']
    assert ov['organizationEstablishedState']==case['established']
    assert ov['administratorEnrollmentState']==(case['admin'] or 'UNVERIFIED')
    assert ov['organizationNetworkState']==case['network']
    assert ov['organizationEndpoint']==case['endpoint']
    assert ov['organizationTlsState']==('VERIFIED_LOCAL' if case['listening'] and
             case['host'] is not None and case['org']=='running' else
             'NOT_LISTENING' if not case['listening'] else 'UNVERIFIED')
    assert ov['managementEndpoint']=='127.0.0.1:8897'
    assert ov['localCredentialState']=='NOT_VERIFIED'
    assert 'private_key' not in json.dumps(result).lower()
    assert 'network reachability' in ov['explanation']
    assert len(result['health'])==5
    assert result['health'][3]['source'].endswith(case['endpoint'])


def test_public_authority_admin_enrollment_does_not_expose_device_ids(tmp_path):
    authority=SLHAuthority(tmp_path)
    assert authority.administrator_enrollment_status()['state']=='NOT_ESTABLISHED'
    with authority._db() as db:
        db.execute('INSERT INTO slh_setup VALUES (1,1234)')
    assert authority.administrator_enrollment_status()['state']=='MISSING'
    with authority._db() as db:
        db.execute("INSERT INTO users VALUES ('person-secret','A Person','admin','active')")
    assert authority.administrator_enrollment_status()['state']=='NO_ACTIVE_DEVICE'
    with authority._db() as db:
        db.execute("""INSERT INTO devices(id,user_id,public_key,fingerprint,kind,name,notes,status,created_at)
          VALUES ('device-secret','person-secret','PUBLIC','FINGERPRINT','administrator','Alice','','active',1234)""")
    actual=authority.administrator_enrollment_status()
    assert actual=={'state':'ENROLLED','local_credential':'NOT_VERIFIED'}
    assert 'secret' not in json.dumps(actual)
    assert 'PUBLIC' not in json.dumps(actual)


def test_backend_status_exposes_exact_binding_without_claiming_reachability(tmp_path,monkeypatch):
    initialize_disabled(tmp_path)
    authority=SLHAuthority(tmp_path)
    with authority._db() as db:
        db.execute('INSERT INTO slh_setup VALUES (1,1234)')
    cfg={**DEFAULTS,'configured':True,'enabled':False,'host':'0.0.0.0',
         'api_port':18899,'worker_port':18898}
    save_config(tmp_path,cfg)
    controller=OrganizationController(authority,automatic_start=False)
    try:
        result=local_status(authority,18897,controller)
        org=result['organization_service']
        assert org['bind_host']=='0.0.0.0'
        assert org['api_port']==18899
        assert org['network_access']=='BIND_CONFIGURED_REACHABILITY_UNVERIFIED'
        assert result['local_service']['host']=='127.0.0.1'
        assert result['administrator_enrollment']['state']=='MISSING'
        assert 'device-secret' not in json.dumps(result)
        # A positive local TLS probe is not evidence of LAN reachability.
        class MockProcess:
            pid=123456
            def poll(self):return None
        controller._process=MockProcess()
        monkeypatch.setattr(controller,'_listening',lambda conf:True)
        actual=controller.status()
        assert actual['state']=='running' and actual['listening'] is True
        assert actual['network_access']=='BIND_CONFIGURED_REACHABILITY_UNVERIFIED'
    finally:
        controller._process=None
        controller.close()


def test_local_unconfigured_and_loopback_status_are_not_network_reachability(tmp_path):
    initialize_disabled(tmp_path)
    status=organization_status(tmp_path)
    assert status['bind_host'] is None
    assert status['network_access']=='NOT_CONFIGURED'
    save_config(tmp_path,{**DEFAULTS,'configured':True,'host':'127.0.0.1'})
    status=organization_status(tmp_path)
    assert status['bind_host']=='127.0.0.1'
    assert status['network_access']=='LOOPBACK_ONLY'


@pytest.mark.parametrize('case', CONTRACT['scenarios'], ids=lambda c:c['id'])
def test_generated_browser_login_entry_is_not_admin_authority(case):
    data=observe_slh('overview',probe=lambda:('RUNNING',case_payload(case)))
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
        try:
            page=browser.new_page(viewport={'width':1100,'height':800})
            errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.evaluate('''data=>{
              window.slhLoginAttempts=[];
              window.fetch=async(url)=>{
                if(url==='/api/applications/slh/identities')return {ok:true,json:async()=>({
                  schema:'mcel.slh.identities.v4b',state:'AVAILABLE',defaultDeviceId:'test-admin',
                  explanation:'Identity discovered; role unverified',identities:[{deviceId:'test-admin',
                    label:'Test',fingerprint:'a'.repeat(64),defaultStatus:'DEFAULT'}]})};
                if(url==='/api/applications/slh/setup/action'){
                  window.slhLoginAttempts.push(url);
                  return {ok:false,status:403,json:async()=>({error:'IDENTITY_NOT_ENROLLED'})};
                }
                return {ok:true,json:async()=>data};
              };
            }''',data)
            page.set_content(render_slh_page('overview'))
            page.get_by_text('SLH observation complete',exact=False).wait_for(timeout=20000)
            login=page.locator('button[data-intent="intent:login-administrator"]')
            # The newer login entry is available before administrator enrollment;
            # the old V3 scenario 'login' flag describes superseded UI gating.
            # Visibility confers no credential or organization authority.
            login.wait_for(state='visible',timeout=10000)
            assert page.evaluate('window.slhLoginAttempts.length') == 0
            login.click()
            page.wait_for_function('window.slhLoginAttempts.length === 1')
            assert page.get_by_text('Organization session not established',exact=True).count()==1
            assert page.get_by_role('tab',name='This Computer').get_attribute('aria-selected')=='true'
            assert page.get_by_role('tab',name='Network SLH').get_attribute('href')=='/applications/slh/network'
            assert not errors,errors
        finally:
            browser.close()


def test_network_tab_remains_separate_and_does_not_inherit_local_admin():
    assert '<a role="tab" href="/applications/slh/network"' in render_slh_page('overview')
    network=render_slh_page('network')
    assert '"appId":"slh-network"' in network
    assert 'intent:login-administrator' not in network
