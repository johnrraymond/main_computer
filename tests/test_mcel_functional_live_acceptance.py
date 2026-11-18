"""Acceptance-lane contracts; the optional actual-host test uses MCEL_LIVE_HOST_URL.

A local HTTP fixture validates routing logic, never counts as live production
acceptance. No live test runs implicitly when a developer uses normal pytest.
"""
from __future__ import annotations

import json
import os
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


def test_live_runner_is_explicit_strict_and_records_incomplete(tmp_path, monkeypatch):
    calls = []
    def four(host, report, apps):
        calls.append(('four', host, apps))
        return {'apps': {app:{'status':'PASS_LIVE_READ_ONLY'} for app in apps}}
    def slh_run(host, report, *, protected_reads):
        calls.append(('slh', host, protected_reads))
        return {'status':'INCOMPLETE', 'protected_reads':'NOT_REQUESTED'}
    monkeypatch.setattr(lane, 'four_app_run', four)
    monkeypatch.setattr(lane, 'slh_run', slh_run)
    report = tmp_path / 'report.json'
    result = lane.run(BASE, report, ('calculator','slh'))
    assert result['status'] == 'INCOMPLETE'
    assert result['apps']['calculator']['status'] == 'PASS_LIVE_READ_ONLY'
    assert result['apps']['slh']['status'] == 'INCOMPLETE'
    assert json.loads(report.read_text())['status'] == 'INCOMPLETE'
    assert calls == [('four',BASE,('calculator',)), ('slh',BASE,False)]
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
    def fake_live(host, report, *, protected_reads):
        assert host == BASE and not protected_reads
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


@pytest.mark.skipif(not os.environ.get('MCEL_LIVE_HOST_URL'),
                    reason='Opt-in real host by setting MCEL_LIVE_HOST_URL')
def test_actual_host_five_app_functional_acceptance(tmp_path):
    # No mocked server. Failure of the selected actual host is a FAIL, not skip.
    base = os.environ['MCEL_LIVE_HOST_URL']
    opt_in = os.environ.get('MCEL_LIVE_PROTECTED_READS') == '1'
    result = lane.run(base, tmp_path/'actual-host.json', protected_reads=opt_in)
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
                    'view':'overview','overview':{'organizationState':'running',
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
            return self.reply({'error':'unexpected POST'},404)
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        result = slh.run(f'http://127.0.0.1:{server.server_port}',tmp_path/'slh.json')
    finally:
        server.shutdown();server.server_close();worker.join(timeout=3)
    # An explicit fail is never promoted; print diagnostics to aid a genuine
    # browser/environment failure in CI.
    if 'ERR_BLOCKED_BY_ADMINISTRATOR' in result.get('error',''):
        assert result['status']=='INCOMPLETE'  # never fabricate a live success
        pytest.skip('Browser policy blocks loopback connections in this environment')
    assert result['status']=='PASS_LIVE_READ_ONLY', result
    assert ('GET',slh.SLH_ROUTE) in seen
    assert ('GET','/applications/slh/local') not in seen
    assert any(x[0]=='POST' and x[2]=='inspect' for x in seen)
    assert all(x[2] in ('inspect','operation-state') for x in seen if x[0]=='POST')


def test_live_launch_failure_is_incomplete_not_silently_skipped(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('test-only browser launch failure')
    monkeypatch.setattr(lane, 'four_app_run', fail)
    monkeypatch.setattr(lane, 'slh_run', fail)
    result = lane.run(BASE, tmp_path/'report.json', ('calculator','slh'))
    assert result['status'] == 'INCOMPLETE'
    assert result['apps']['slh']['failure_class'] == 'RuntimeError'
    assert result['apps']['calculator']['status'] == 'INCOMPLETE'
