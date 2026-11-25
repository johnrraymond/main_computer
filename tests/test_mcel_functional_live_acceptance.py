"""Acceptance-lane contracts; the optional actual-host test uses MCEL_LIVE_HOST_URL.

A local HTTP fixture validates routing logic, never counts as live production
acceptance. No live test runs implicitly when a developer uses normal pytest.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tools import mcel_functional_live_acceptance as lane
from tools import mcel_slh_live_readonly_frob as slh

BASE = 'http://127.0.0.1:8765'


def test_live_slh_guard_denies_all_mutations_and_foreign_origins():
    decision = lambda url, method, body=None, auth=False: slh.request_decision(
        BASE, BASE + url if url.startswith('/') else url, method,
        json.dumps(body) if body is not None else None, protected_reads=auth)
    inspect = {'target': 'local', 'operation': 'inspect', 'arguments': {}, 'sessionTicket': ''}
    assert decision('/api/applications/slh/seh/deployment', 'POST', inspect) == 'allow-public-read'
    assert decision('/api/applications/slh/identities', 'GET') == 'allow-public-read'
    # The canonical SLH path is now the parent launcher; live observation
    # requires the explicit native child document, with no extra query keys.
    assert slh.SLH_ROUTE == '/applications/slh/local?mcel-shell-child=1'
    assert decision(slh.SLH_ROUTE, 'GET') == 'allow-native-slh-child'
    assert decision('/applications/slh/local?mcel-shell-child=0', 'GET').startswith('deny')
    assert decision('/applications/slh/local?mcel-shell-child=1&unsafe=1', 'GET').startswith('deny')
    assert decision('/applications/slh/local?unsafe=1', 'GET').startswith('deny')
    assert decision('/api/applications/slh/observe?view=overview', 'GET') == 'allow-public-read'
    for op in ('deploy','undeploy','select','register','cancel-operation'):
        assert decision('/api/applications/slh/seh/deployment', 'POST',
                        {**inspect, 'operation': op}, True).startswith('deny')
    for op in ('accept','reject','delete','empty-trash','reactivate','retain'):
        assert decision('/api/applications/slh/identity-notebook', 'POST',
                        {'target':'local','operation':op,'arguments':{},'sessionTicket':''}, True).startswith('deny')
    assert decision('/api/applications/slh/setup/action', 'POST',
                    {'action':'authenticate','arguments':{'adminDeviceId':'operator'}}).startswith('deny')
    assert decision('/api/applications/slh/setup/action', 'POST',
                    {'action':'authenticate','arguments':{'adminDeviceId':'operator'}},True) == 'allow-session-auth'
    assert decision('/api/applications/slh/identity-notebook', 'POST',
                    {'target':'local','operation':'read-pending','arguments':{},'sessionTicket':''},True) == 'allow-protected-read'
    assert decision('/api/applications/slh/identity-notebook', 'POST',
                    {'target':'local','operation':'read-pending','arguments':{'requestId':'x'},'sessionTicket':''},True).startswith('deny')
    assert decision('http://127.0.0.1:9876/api/applications/slh/identities','GET') == 'deny-origin'
    assert decision('https://example.com/applications/slh/local','GET') == 'deny-origin'
    assert decision('/api/applications/slh/observe?view=workers','GET').startswith('deny')
    assert decision('/api/applications/slh/observe?view=workers','GET',auth=True) == 'allow-protected-read'
    assert decision('/api/applications/slh/observe?view=overview&x=1','GET').startswith('deny')
    assert decision('/api/applications/slh/setup/action','POST',{'action':'start','arguments':{}},True).startswith('deny')
    assert decision('/api/applications/slh/worker/rename','POST',{},True).startswith('deny')
    assert decision('/api/applications/slh/network/connect','POST',{},True).startswith('deny')


def test_protected_authentication_failure_is_identified_by_status_only():
    assert slh._failed_authentication_request([
        {'method':'POST','path':'/api/applications/slh/setup/action','status':503}
    ]) == {'method':'POST','path':'/api/applications/slh/setup/action','status':503}
    assert slh._failed_authentication_request([
        {'method':'GET','path':'/api/applications/slh/setup/action','status':503},
        {'method':'POST','path':'/api/applications/slh/seh/deployment','status':503},
        {'method':'POST','path':'/api/applications/slh/setup/action','status':200},
    ]) is None


def test_optional_admin_override_is_prevalidated_without_launching_browser():
    assert slh.validate_admin_id('059-LNF-AL-074-TM-26-11-09', protected_reads=True)
    assert slh.validate_admin_id(None, protected_reads=True) is None
    for invalid in ('', ' ', '../escape', 'admin id', 'a' * 65):
        with pytest.raises(ValueError):
            slh.validate_admin_id(invalid, protected_reads=True)
    with pytest.raises(ValueError):
        slh.validate_admin_id('one', protected_reads=False)
    with pytest.raises(ValueError):
        lane.run(BASE, Path('/tmp/inert.json'), ('calculator',), admin_id='one')
    with pytest.raises(ValueError):
        lane.run(BASE, Path('/tmp/inert.json'), ('slh',), admin_id='one')
    assert slh._protected_request_counts([
        {'method':'POST','path':'/api/applications/slh/setup/action','status':200},
        {'method':'POST','path':'/api/applications/slh/identity-notebook','status':200},
        {'method':'GET','path':'/api/applications/slh/identities','status':200},
    ]) == {'authentication':1, 'notebook':1}
    assert slh._ANONYMOUS_INIT_SCRIPT.startswith('sessionStorage.setItem(')
    assert slh._pending_observation_ready({'pendingLoaded':True, 'pendingCount':0,
                                           'notebookMessage':'No pending records'})
    assert slh._pending_observation_ready({'pendingLoaded':True, 'pendingCount':1,
                                           'notebookMessage':''})
    assert not slh._pending_observation_ready({'pendingLoaded':False, 'pendingCount':0,
                                               'notebookMessage':'No pending records'})
    assert not slh._pending_observation_ready({'pendingLoaded':True, 'pendingCount':1,
                                               'notebookMessage':'authority-denied'})


def test_live_runner_is_explicit_strict_and_records_incomplete(tmp_path, monkeypatch):
    calls = []
    def four(host, report, apps):
        calls.append(('four', host, apps))
        return {'apps': {app:{'status':'PASS_LIVE_READ_ONLY'} for app in apps}}
    def slh_run(host, report, *, protected_reads, admin_id):
        calls.append(('slh', host, protected_reads, admin_id))
        return {'status':'INCOMPLETE', 'protected_reads':'NOT_REQUESTED',
                'failure_stage':'protected-prerequisites', 'failure_code':'ADMIN_ID_REQUIRED'}
    monkeypatch.setattr(lane, 'four_app_run', four)
    monkeypatch.setattr(lane, 'slh_run', slh_run)
    report = tmp_path / 'report.json'
    result = lane.run(BASE, report, ('calculator','slh'))
    assert result['status'] == 'INCOMPLETE'
    assert result['apps']['calculator']['status'] == 'PASS_LIVE_READ_ONLY'
    assert result['apps']['slh']['status'] == 'INCOMPLETE'
    assert result['apps']['slh']['failure_code'] == 'ADMIN_ID_REQUIRED'
    assert json.loads(report.read_text())['status'] == 'INCOMPLETE'
    from tools.mcel_functional_auth import ORIGIN
    assert calls == [('four',ORIGIN,('calculator',)), ('slh',ORIGIN,False,None)]
    with pytest.raises(ValueError):
        lane.run('https://example.com', tmp_path/'x.json', ('slh',))
    with pytest.raises(ValueError):
        lane.run(BASE, tmp_path/'x.json', ('slh','slh'))
    with pytest.raises(ValueError):
        lane.run(BASE, tmp_path/'x.json', ('calculator',), protected_reads=True)


def test_live_runner_does_not_expand_success_beyond_requested_apps(tmp_path, monkeypatch):
    monkeypatch.setattr(lane, 'four_app_run', lambda h, r, a: {'apps': {
        app: {'status': 'PASS_LIVE_READ_ONLY'} for app in a}})
    result=lane.run(BASE, tmp_path/'report.json', ('calculator','task-manager'))
    assert result['status']=='PASS_LIVE_READ_ONLY'
    assert set(result['apps']) == {'calculator','task-manager'}
    assert result['mutating_acceptance']=='NOT_RUN_NEEDS_ISOLATED_FIXTURES'


def test_functional_acid_appends_requested_live_lane_without_promotion(tmp_path, monkeypatch):
    from tools import mcel_functional_acid as acid
    monkeypatch.setattr(acid, '_run_pytest', lambda *a, **kw: acid.result('isolated','PASS'))
    monkeypatch.setattr(acid, '_run_browser', lambda *a, **kw: {
        'status':'PASS','checks':[],'elements':[],'visuals':[]})
    def fake_live(host, report, *, protected_reads, admin_id):
        assert host == BASE and not protected_reads and admin_id is None
        return {'status':'INCOMPLETE','apps':{'slh':{'status':'INCOMPLETE'}}}
    monkeypatch.setattr(lane,'run',fake_live)
    report = acid.run(acid.ROOT, output=tmp_path, mode='core', live_host_url=BASE)
    assert report['live']['status']=='INCOMPLETE'
    assert report['status']=='FAIL'  # requested live failure overrides unrelated gaps
    assert any(s['id']=='real-host-five-app-readonly' and s['status']=='FAIL'
               for s in report['suites'])
    assert json.loads((tmp_path/'report.json').read_text())['live']['status']=='INCOMPLETE'
    with pytest.raises(ValueError):
        acid.run(acid.ROOT, output=tmp_path/'unreachable', mode='inventory', live_host_url=BASE)
    with pytest.raises(ValueError):
        acid.run(acid.ROOT, output=tmp_path/'badadmin', mode='core',
                 live_host_url=BASE, live_admin_id='one')


def test_explicit_admin_forwarded_without_appearing_in_summary(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(lane, 'slh_run', lambda host, report, *, protected_reads, admin_id:
                        (called.append((protected_reads, admin_id)) or
                         {'status':'PASS_LIVE_READ_ONLY','protected_reads':'PASS'}))
    result = lane.run(BASE, tmp_path/'report.json', ('slh',),
                      protected_reads=True, admin_id='one')
    assert called == [(True,'one')]
    assert result['status'] == 'PASS_LIVE_READ_ONLY'
    assert result['admin_id_supplied'] is True
    assert 'one' not in (tmp_path/'report.json').read_text()


@pytest.mark.skipif(not os.environ.get('MCEL_LIVE_HOST_URL'),
                    reason='Opt-in real host by setting MCEL_LIVE_HOST_URL')
def test_actual_host_five_app_functional_acceptance(tmp_path):
    # No mocked server. Failure of the selected actual host is a FAIL, not skip.
    base = os.environ['MCEL_LIVE_HOST_URL']
    opt_in = os.environ.get('MCEL_LIVE_PROTECTED_READS') == '1'
    admin_id = os.environ.get('MCEL_LIVE_ADMIN_ID') if opt_in else None
    result = lane.run(base, tmp_path/'actual-host.json', protected_reads=opt_in,
                      admin_id=admin_id)
    assert result['status'] == 'PASS_LIVE_READ_ONLY', result['apps']


def test_slh_live_browser_lane_uses_http_fixture_not_mocks(tmp_path):
    """Prove the live harness drives a real loopback HTTP interface.

    This is a hermetic harness test, NOT evidence of a running Windows service.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from main_computer.mcel_slh_application import render_slh_page
    html = render_slh_page()
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def reply(self, value, status=200, content_type='application/json'):
            raw = value.encode() if isinstance(value,str) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self):
            seen.append(('GET',self.path))
            if self.path=='/applications/slh/local':
                # Deliberately serve only the launcher at the bare path,
                # matching the real host. The live runner must not use it.
                return self.reply('<html><body><div id="mcel-shell"></div></body></html>',
                                  content_type='text/html; charset=utf-8')
            if self.path==slh.SLH_ROUTE:
                return self.reply(html,content_type='text/html; charset=utf-8')
            if self.path=='/api/applications/slh/identities':
                return self.reply({'schema':'mcel.slh.identities.v4b',
                    'state':'AVAILABLE','defaultDeviceId':'one',
                    'identities':[{'deviceId':'one','label':'One','fingerprint':'a'*64,
                                   'defaultStatus':'DEFAULT'}], 'explanation':''})
            if self.path=='/api/applications/slh/observe?view=overview':
                return self.reply({'ok':True,'schema':'mcel.slh.first-slice-observation.v1',
                    'view':'overview','overview':{'serviceState':'RUNNING',
                    'observationState':'OBSERVED','organizationState':'running',
                    'organizationEndpoint':'127.0.0.1:8899'}})
            return self.reply({'error':'unexpected GET'},404)
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'{}')
            seen.append(('POST',self.path,data.get('operation') or data.get('action')))
            if self.path=='/api/applications/slh/seh/deployment':
                if data.get('operation')=='inspect':
                    return self.reply({'ok':True,'result':{
                        'generation':33,'selectedController':'coolify-c',
                        'applicationUuid':'','deploymentStatus':'UNDEPLOYED',
                        'controllers':[{'identity':'coolify-c'}]}})
                if data.get('operation')=='operation-state':
                    return self.reply({'ok':True,'result':{
                        'schema':'main-computer.seh-management-operation-snapshot.v1',
                        'state':'IDLE','busy':False,'operation':None,'last_operation':None}})
            if self.path=='/api/applications/slh/setup/action':
                if data.get('action')=='authenticate':
                    return self.reply({'access_token':'a'*40,'device_id':'one'})
                if data.get('action')=='logout':
                    return self.reply({'status':'SIGNED_OUT'})
            if self.path=='/api/applications/slh/identity-notebook':
                if data.get('operation')=='read-pending':
                    return self.reply({'ok':True,'result':[
                        {'id':'a'*32,'identity':'Identity fixture','role':'worker',
                         'requestedAt':'Nov 17, 2026'}]})
                if data.get('operation') in ('read-enrolled','read-trash'):
                    return self.reply({'ok':True,'result':[]})
                if data.get('operation')=='read-retention':
                    return self.reply({'ok':True,'result':{'enabled':False}})
            return self.reply({'error':'unexpected POST'},404)
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        result = slh.run(f'http://127.0.0.1:{server.server_port}',tmp_path/'slh.json')
        protected = None
        if result['status']=='PASS_LIVE_READ_ONLY':
            protected = slh.run(f'http://127.0.0.1:{server.server_port}',
                                tmp_path/'protected.json',protected_reads=True)
    finally:
        server.shutdown();server.server_close();worker.join(timeout=3)
    # An explicit fail is never promoted; print diagnostics to aid a genuine
    # browser/environment failure in CI.
    if 'ERR_BLOCKED_BY_ADMINISTRATOR' in result.get('error',''):
        assert result['status']=='INCOMPLETE'  # never fabricate a live success
        pytest.skip('Browser policy blocks loopback connections in this environment')
    assert result['status']=='PASS_LIVE_READ_ONLY', result
    assert result['checks']['deployment_tab_selected'] is True
    assert result['checks']['undeploy_button_mounted'] is True
    assert result['deployment_evidence']['status'] == 'UNDEPLOYED'
    assert result['deployment_evidence']['undeploy_disabled'] is True
    assert ('GET',slh.SLH_ROUTE) in seen
    assert ('GET','/applications/slh/local') not in seen
    assert any(x[0]=='POST' and x[2]=='inspect' for x in seen)
    assert protected is not None and protected['status']=='PASS_LIVE_READ_ONLY', protected
    assert protected['admin_identity_source'] == 'LMS_DEFAULT'
    assert protected['admin_id_supplied'] is False
    assert protected['checks']['anonymous_does_not_authenticate']
    assert protected['checks']['admin_selected_via_mcel']
    assert protected['checks']['pending_loaded_without_tab_click']
    assert protected['checks']['session_cleanup']
    assert protected['protected_reads']=='PASS'
    assert any(x[0]=='POST' and x[2]=='authenticate' for x in seen)
    assert any(x[0]=='POST' and x[2]=='read-pending' for x in seen)
    assert all(x[2] in ('inspect','operation-state','authenticate','logout',
                        'read-pending','read-enrolled','read-trash','read-retention')
               for x in seen if x[0]=='POST')


def test_live_launch_failure_is_incomplete_not_silently_skipped(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('test-only browser launch failure')
    monkeypatch.setattr(lane, 'four_app_run', fail)
    monkeypatch.setattr(lane, 'slh_run', fail)
    result = lane.run(BASE, tmp_path/'report.json', ('calculator','slh'))
    assert result['status'] == 'INCOMPLETE'
    assert result['apps']['slh']['failure_class'] == 'RuntimeError'
    assert result['apps']['calculator']['status'] == 'INCOMPLETE'


def test_anonymous_visible_pending_does_not_authenticate_then_selected_admin_hydrates():
    """The real SLH app owns hydration; the harness must not dispatch a read.

    This is a mocked-service Chromium regression, not live-host evidence.
    """
    from playwright.sync_api import sync_playwright
    from main_computer.mcel_slh_application import render_slh_page
    from tests.test_mcel_identity_selection_authorization_v1 import MOCK

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True,
                                     executable_path=shutil.which('chromium') or None,
                                     args=['--no-sandbox'])
        try:
            page = browser.new_page(viewport={'width':1272,'height':900})
            page.evaluate('''() => {
              Object.defineProperty(window,'sessionStorage',{configurable:true,
                value:{_store:{'mcel.slh.selected-identity.v2.local':'__EXPLICIT_NONE_V3__'},
                       getItem(k){return this._store[k]??null},
                       setItem(k,v){this._store[k]=v}}});
            }''')
            page.evaluate(MOCK)
            # This mock needs a verified lifecycle observation; the legacy
            # identity-selection fixture intentionally omits provenance.
            page.evaluate('''() => {
              const old=window.fetch;
              window.fetch=(url,options) => url==='/api/applications/slh/observe?view=overview'
                ? Promise.resolve({ok:true,json:async()=>({
                    ok:true,schema:'mcel.slh.first-slice-observation.v1',view:'overview',
                    overview:{serviceState:'RUNNING',observationState:'OBSERVED',
                      organizationState:'running',organizationEndpoint:'127.0.0.1:8899'}
                  })}) : old(url,options);
            }''')
            page.set_content(render_slh_page())
            page.wait_for_function('''() => {
              const app=window.__mcelSlh?.app;
              return app?.readState('state:local-identities')?.length===2 &&
                app.readState('state:selected-target')==='local' &&
                app.readState('state:organization-lifecycle')?.state==='running';
            }''', timeout=12000)
            page.locator('[data-mcel-notebook-tab="pending"]').scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            anonymous=slh._state(page,'one')
            assert not anonymous['selectedIdentityPresent'], anonymous
            assert anonymous['identityObservationReady'] and anonymous['lifecycleObservationReady']
            assert anonymous['adminInDirectory'] and anonymous['pendingTabSelected']
            assert not anonymous['pendingLoaded']
            assert not page.evaluate('''() => window.calls.some(c=>
              c.body?.action==='authenticate' ||
              c.url==='/api/applications/slh/identity-notebook')''')
            selection=page.evaluate('''async () => (await window.__mcelSlh.app.execute(
              'intent:choose-identity',{key:'one'})).disposition''')
            assert selection=='selected'
            page.wait_for_function('''() => {
              const app=window.__mcelSlh?.app;
              return Array.isArray(app?.readState('state:pending-enrollments'));
            }''', timeout=12000)
            ready=slh._state(page,'one')
            assert ready['selectedAdminMatches'] and ready['pendingLoaded'], ready
            assert page.evaluate('''() => window.calls.some(c=>
              c.body?.action==='authenticate')''')
            assert page.evaluate('''() => window.calls.some(c=>
              c.url==='/api/applications/slh/identity-notebook' &&
              c.body.operation==='read-pending')''')
        finally:
            browser.close()


def test_lms_default_identity_is_not_inferred_from_order_or_label():
    summary = {'state':'AVAILABLE', 'defaultDeviceId':'two'}
    rows = [{'deviceId':'one','defaultStatus':'ADDITIONAL','label':'Administrator'},
            {'deviceId':'two','defaultStatus':'DEFAULT','label':'Local'}]
    assert slh._default_identity_id(summary, rows) == 'two'
    assert slh._default_identity_id(summary, list(reversed(rows))) == 'two'
    assert slh._default_identity_id({**summary,'state':'UNVERIFIED'}, rows) is None
    assert slh._default_identity_id({**summary,'defaultDeviceId':'one'}, rows) is None
    assert slh._default_identity_id(summary, rows[:1]) is None
    assert slh._default_identity_id(summary, rows + [rows[1]]) is None
    assert slh._default_identity_id(summary, [{**rows[0],'defaultStatus':'DEFAULT'},rows[1]]) is None
    assert slh._default_identity_id({'state':'AVAILABLE','defaultDeviceId':'../bad'}, rows) is None
    assert slh._default_identity_id(summary, None) is None


def test_protected_lane_needs_no_manual_identity_argument(tmp_path, monkeypatch):
    calls = []
    def fake(host, report, *, protected_reads, admin_id):
        calls.append((protected_reads, admin_id))
        return {'status':'PASS_LIVE_READ_ONLY','protected_reads':'PASS'}
    monkeypatch.setattr(lane, 'slh_run', fake)
    assert lane.main(['--app','slh','--protected-reads','--report',str(tmp_path/'report.json')]) == 0
    assert calls == [(True,None)]
    assert json.loads((tmp_path/'report.json').read_text())['admin_id_supplied'] is False
