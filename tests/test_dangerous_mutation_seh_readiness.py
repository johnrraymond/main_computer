"""Browser-only SEH pre-click readiness and mismatch gates.

All authority responses are injected; no request reaches a real SLH or Coolify.
"""
from __future__ import annotations

import json

import pytest

from main_computer.mcel_slh_application import render_slh_page
from tools import dangerous_mutation as dm
from tools.mcel_phase2b_browser import launch_semantic_browser
from tests.test_mcel_seh_management_oracle_v1 import _browser_mock


class _InDocumentPage:
    """Serve the real compiled SLH document without exposing an HTTP listener."""
    def __init__(self, page):
        self.page = page

    def goto(self, url, **kwargs):
        assert url == 'http://127.0.0.1:8765' + dm.SLH_ROUTE
        return self.page.set_content(render_slh_page(), wait_until='domcontentloaded')

    def __getattr__(self, name):
        return getattr(self.page, name)


def _mocked_page(page, *, uuid: str, phase: str = 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED',
                 reject_inspection: bool = False):
    page.evaluate(_browser_mock())
    page.evaluate('''settings => {
      const original = window.fetch;
      window.sehPreclickRequests = [];
      window.fetch = async (url, options={}) => {
        if(url === '/api/applications/slh/identities')
          await new Promise(resolve => setTimeout(resolve, 800));
        if(url === '/api/applications/slh/seh/deployment') {
          const body = JSON.parse(options.body || '{}');
          window.sehPreclickRequests.push(body.operation);
          if(body.operation === 'inspect') {
            if(settings.reject) return {ok:false,status:503,json:async()=>({ok:false,error:{code:'SERVICE_UNAVAILABLE'}})};
            return {ok:true,status:200,json:async()=>({ok:true,result:{
              generation:33,selectedController:'coolify-c',
              applicationUuid:settings.uuid,deploymentStatus:settings.phase,
              controllers:[{identity:'coolify-c'}]}})};
          }
          if(body.operation === 'operation-state')
            return {ok:true,status:200,json:async()=>({ok:true,result:{
              schema:'main-computer.seh-management-operation-snapshot.v1',
              state:'IDLE',busy:false,operation:null,last_operation:null}})};
          throw new Error('Forbidden operation '+body.operation);
        }
        return original(url,options);
      };
    }''', {'uuid': uuid, 'phase': phase, 'reject': reject_inspection})


def _baseline():
    return {'host':'http://127.0.0.1:8765','application_uuid':'abc12345orig',
            'generation':33,'selected_controller':'coolify-c',
            'deployment_phase':'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED'}


def test_seh_browser_waits_for_local_target_before_verified_read():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser,_ = launch_semantic_browser(pw)
        try:
            page = browser.new_page(viewport={'width':1280,'height':900})
            _mocked_page(page, uuid='abc12345orig')
            verified = dm.browser_open(_InDocumentPage(page), _baseline())
            assert verified['application_uuid'] == 'abc12345orig'
            assert page.evaluate("window.__mcelSlh.app.readState('state:selected-target')") == 'local'
            assert page.evaluate("window.__mcelSlh.app.readState('state:seh-deployment-status').applicationUuid") == 'abc12345orig'
            assert set(page.evaluate('window.sehPreclickRequests')) <= {'inspect','operation-state'}
        finally:
            browser.close()


def test_seh_browser_refuses_true_page_uuid_mismatch_before_mutation():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser,_ = launch_semantic_browser(pw)
        try:
            page = browser.new_page(viewport={'width':1280,'height':900})
            _mocked_page(page, uuid='abc12345different')
            with pytest.raises(dm.MutationRefused,match='PAGE_CANONICAL_UUID_MISMATCH'):
                dm.browser_open(_InDocumentPage(page), _baseline())
            assert set(page.evaluate('window.sehPreclickRequests')) <= {'inspect','operation-state'}
        finally:
            browser.close()


def test_seh_browser_reports_unavailable_read_without_fake_uuid_conflict():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser,_ = launch_semantic_browser(pw)
        try:
            page = browser.new_page(viewport={'width':1280,'height':900})
            _mocked_page(page, uuid='abc12345orig', reject_inspection=True)
            with pytest.raises(dm.MutationRefused,match='PAGE_DEPLOYMENT_READ_UNAVAILABLE'):
                dm.browser_open(_InDocumentPage(page), _baseline())
            assert set(page.evaluate('window.sehPreclickRequests')) <= {'inspect','operation-state'}
        finally:
            browser.close()


def test_seh_page_failure_diagnostic_exports_only_public_fields():
    class Page:
        def evaluate(self, expression):
            return {'mounted': True, 'target': 'local', 'selected': 'local',
                    'deployment': {'applicationUuid': 'abc12345orig',
                                   'generation': 33, 'selectedController': 'coolify-c',
                                   'deploymentStatus': 'PUBLIC_RUNTIME_PRESENT_SLH_CONNECTED',
                                   'apiToken': 'DO-NOT-EXPORT'},
                    'tab': 'true', 'modalText': 'HIDDEN-PRIVATE-LOG'}
    diagnostic = dm._safe_page_observation(Page())
    assert diagnostic['application_uuid'] == 'abc12345orig'
    assert diagnostic['deployment_observed'] is True
    assert 'DO-NOT-EXPORT' not in json.dumps(diagnostic)
    assert 'HIDDEN-PRIVATE-LOG' not in json.dumps(diagnostic)


def test_cli_optional_page_preflight_checks_browser_without_mutations(monkeypatch, tmp_path):
    calls=[]
    monkeypatch.setattr(dm, 'preflight', lambda **kwargs: _baseline() | {'status':'READ_ONLY'})
    monkeypatch.setattr(dm, 'verify_resident_admin', lambda **kwargs:{'status':'VERIFIED','device_id':'admin-device','mutation_submitted':False})
    monkeypatch.setattr(dm, 'verify_seh_page_read_only', lambda **kwargs:
                        (calls.append(kwargs) or {'status':'PASS_READ_ONLY_UI',
                          'page_preflight': {'application_uuid':'abc12345orig'}}))
    report=tmp_path/'report.json'
    code=dm.main(['--mode','preflight','--scope','seh','--verify-page','--report',str(report)])
    assert code==0
    assert len(calls)==1
    data=json.loads(report.read_text())
    assert data['status']=='READ_ONLY'
    assert data['page']['status']=='PASS_READ_ONLY_UI'


def test_cli_page_preflight_refuses_actual_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(dm, 'preflight', lambda **kwargs: _baseline() | {'status':'READ_ONLY'})
    monkeypatch.setattr(dm, 'verify_resident_admin', lambda **kwargs:{'status':'VERIFIED','device_id':'admin-device','mutation_submitted':False})
    monkeypatch.setattr(dm, 'verify_seh_page_read_only', lambda **kwargs:
                        {'status':'INCOMPLETE','failed_gate':'PAGE_CANONICAL_UUID_MISMATCH',
                         'submitted_operations':[]})
    report=tmp_path/'report.json'
    code=dm.main(['--mode','preflight','--scope','seh','--verify-page','--report',str(report)])
    assert code==1
    data=json.loads(report.read_text())
    assert data['status']=='INCOMPLETE'
    assert data['failed_gate']=='PAGE_CANONICAL_UUID_MISMATCH'
    assert data['page']['submitted_operations']==[]
