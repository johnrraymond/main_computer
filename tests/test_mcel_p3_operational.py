"""P3 isolated security and layout-free composition gates. No live effects."""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from main_computer.task_manager import TaskManagerService
from main_computer.mcel_semantic_generated_host import compile_p3_generated_candidate, render_p3_generated_page
from main_computer.mcel_knowledge.semantic_composition import synthesize_composition, verify_composition
from main_computer import task_http_authority as authority


def service(tmp_path):
    svc = TaskManagerService(tmp_path, default_port=18401, control_root=tmp_path)
    svc.schedule_path = tmp_path / "schedules.json"
    return svc


def test_generation_required_in_service_even_without_http_marker(tmp_path, monkeypatch):
    svc = service(tmp_path)
    events=[]
    created=1730000000.0
    class FakeProcess:
        def create_time(self):return created
        def terminate(self):events.append("terminate")
        def kill(self):events.append("kill")
        def wait(self,timeout):pass
    monkeypatch.setattr("main_computer.task_manager.psutil",SimpleNamespace(Process=lambda pid:FakeProcess(),
                       NoSuchProcess=ValueError,AccessDenied=PermissionError,ZombieProcess=RuntimeError))
    for action in ("terminate_pid","kill_pid"):
        with pytest.raises(ValueError,match="generation is required"):
            svc.perform_action(action=action,pid=500,confirm=True)
        with pytest.raises(ValueError,match="generation mismatch"):
            svc.perform_action(action=action,pid=500,confirm=True,expected_created_at="2020-01-01T00:00:00+00:00")
    assert events==[]
    generation=svc._format_epoch(created)
    r=svc.perform_action(action="terminate_pid",pid=500,confirm=True,expected_created_at=generation)
    # The generation is OS-verified and normalized to the millisecond precision
    # used by Windows CIM; raw ISO string spelling is not the identity.
    assert r["verified_created_at"] == datetime.fromtimestamp(
        created, timezone.utc).isoformat(timespec="milliseconds")
    assert events==["terminate"]
    with pytest.raises(ValueError,match="Invalid process generation"):
        svc.perform_action(action="kill_pid",pid=500,confirm=True,expected_created_at="2024-10-27T00:00:00")


def test_schedule_offset_revision_atomicity_and_recorded_action(tmp_path, monkeypatch):
    svc=service(tmp_path)
    with pytest.raises(ValueError,match="explicit UTC offset"):
        svc.create_schedule(action="server_restart",run_at="2026-11-01T01:30")
    with pytest.raises(ValueError,match="server lifecycle"):
        svc.create_schedule(action="terminate_pid",run_at="2026-11-01T01:30-07:00")
    record=svc.create_schedule(action="server_restart",run_at="2026-11-01T01:30-07:00")['schedule']
    assert record['run_at']=="2026-11-01T08:30:00+00:00"
    assert record['revision']==svc.list_schedules()['schedules'][0]['revision']
    assert not list(tmp_path.glob('*.tmp'))
    invoked=[]
    def dispatch(*,action,confirm):
        invoked.append((action,confirm))
        return {"ok":True,"action":action,"deferred":True,"message":"Isolated acceptance"}
    monkeypatch.setattr(svc,'perform_action',dispatch)
    req='manual_schedule_123456'
    with pytest.raises(ValueError,match="changed"):
        svc.run_recorded_schedule(schedule_id=record['id'],expected_revision="stale",expected_action="server_restart",request_id=req,confirm=True)
    assert not invoked
    with pytest.raises(ValueError,match="changed"):
        svc.run_recorded_schedule(schedule_id=record['id'],expected_revision=record['revision'],expected_action="server_shutdown",request_id=req,confirm=True)
    a=svc.run_recorded_schedule(schedule_id=record['id'],expected_revision=record['revision'],expected_action="server_restart",request_id=req,confirm=True)
    b=svc.run_recorded_schedule(schedule_id=record['id'],expected_revision=record['revision'],expected_action="server_restart",request_id=req,confirm=True)
    assert a==b and a['outcome']=='accepted-deferred' and invoked==[("server_restart",True)]
    with pytest.raises(ValueError,match="revision changed"):
        svc.delete_schedule(schedule_id=record['id'],expected_revision="stale")
    deleted=svc.delete_schedule(schedule_id=record['id'],expected_revision=record['revision'])
    assert deleted['schedules']==[]


def test_snapshot_has_no_heartbeat_recovery(tmp_path,monkeypatch):
    svc=service(tmp_path)
    def must_not_start(*a,**kw):
        raise AssertionError("Read path attempted to start a service")
    monkeypatch.setattr("main_computer.task_manager.ensure_heartbeat_service",must_not_start)
    monkeypatch.setattr("main_computer.task_manager.status_payload",lambda config:{"heartbeat":{"running":False,"ready":False}})
    monkeypatch.setattr(svc,"_read_pid_file",lambda:700)
    result=svc._server_summary(processes=[],connections=[])
    assert result['heartbeat_running'] is False
    assert result['running'] is True


class Handler:
    def __init__(self,host='localhost',origin='http://localhost:18401',peer='127.0.0.1'):
        self.client_address=(peer,19321)
        self.server=SimpleNamespace(server_address=('127.0.0.1',18401))
        self.headers={'Host':host+':18401','Origin':origin,'Sec-Fetch-Site':'same-origin',
                      'Content-Type':'application/json','Content-Length':'12'}
        self.calls=[];self.wfile=io.BytesIO();self.refusal=None
    def _send_json(self,body,status):self.refusal=(body,status)
    def send_response(self,status):self.calls.append(('status',status))
    def send_header(self,key,value):self.calls.append((key,value))
    def end_headers(self):pass


def test_local_operator_csrf_guard():
    bad=Handler(host='evil.example');authority.issue(bad)
    assert bad.refusal[0]['error']=='caller-not-authorized'
    remote=Handler(peer='192.0.2.10');authority.issue(remote)
    assert remote.refusal[0]['error']=='caller-not-authorized'
    origin=Handler(origin='http://evil.example:18401');authority.issue(origin)
    assert origin.refusal[0]['error']=='caller-not-authorized'
    local=Handler();authority.issue(local)
    data=json.loads(local.wfile.getvalue())
    cookie=next(v for k,v in local.calls if k=='Set-Cookie')
    assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
    req=Handler();req.headers['Cookie']=cookie.split(';',1)[0]
    req.headers['X-MCEL-Task-CSRF']=data['csrf']
    assert authority.require(req)
    assert not authority.require(Handler())
    req.headers['X-MCEL-Task-CSRF']='forged'
    assert not authority.require(req)


def test_p3_source_derived_from_ir_and_p2_read_only():
    ir,plan=compile_p3_generated_candidate()
    verify_composition(ir,plan)
    assert len(ir['intents'])==19
    assert len(ir['capabilities'])==3
    assert {c['risk'] for c in ir['capabilities']}=={'external-read','consequential'}
    assert plan['policy']=='mcel.semantic-structure.p3'
    assert not ir.get('layouts') and not ir['surfaces'][0].get('htmlBindings')
    html=render_p3_generated_page()
    assert 'window.__mcelP3' in html
    from main_computer.mcel_semantic_generated_host import compile_generated_candidate
    old_ir,old_plan=compile_generated_candidate()
    assert old_plan['policy']=='mcel.semantic-structure.p1'
    assert {c['risk'] for c in old_ir['capabilities']}=={'external-read'}
    with pytest.raises(ValueError,match='P1 cannot'):
        synthesize_composition(ir)


@pytest.fixture(scope='module')
def browser():
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    with sync_playwright() as pw:
        b,_=launch_semantic_browser(pw)
        try:yield b
        finally:b.close()


@pytest.mark.parametrize('width',[320,390,560,1100,1272])
def test_p3_generated_browser_no_implicit_effects(browser,width):
    page=browser.new_page(viewport={'width':width,'height':800})
    try:
        page.evaluate('''() => {
          window.__requests=[];window.confirm=()=>true;
          window.fetch=async(url,opts={})=>{
            const body=opts.body?JSON.parse(opts.body):{}; window.__requests.push({url,body,method:opts.method||'GET'});
            let value={ok:true};
            if(url.endsWith('/authority/session'))value={ok:true,csrf:'unit-session'};
            else if(url.endsWith('/overview')) value={ok:true,server:{running:true,pid:33},processes:[
              {pid:501,name:'Disposable',status:'running',memory_human:'1MB',created_at:'2026-11-01T12:00:00+00:00'}],
              connections:[],hardware:{summary:'CPU 25%',cpu:{available:true,overall_percent:25,per_core:[]},gpu:{available:true,overall_percent:0,devices:[]}}};
            else if(url.endsWith('/action'))value={ok:true,action:body.action,pid:body.pid,
              verified_created_at:body.expected_created_at,deferred:body.action==='server_restart',message:'sandbox-only'};
            else if(url.endsWith('/schedules'))value={ok:true,schedules:[{id:'s1',action:'server_restart',run_at:'2026-11-01T19:00:00+00:00',note:'test',revision:'r1'}]};
            else if(url.endsWith('/ai'))value={ok:true,content:'Isolated model result'};
            return {ok:true,status:200,json:async()=>value};
          };
        }''')
        page.set_content(render_p3_generated_page())
        assert 'mounted' in page.locator('#mcel-workbench-status').inner_text()
        assert page.locator('[data-mcel-mode-tabs] button').count()==4
        assert page.evaluate('window.__requests.length')==0
        page.locator('form[data-intent="intent:refresh"] button[type=submit]').click()
        page.wait_for_function('window.__requests.some(v=>v.url.endsWith("/overview"))')
        page.locator('[data-record-key="501:2026-11-01T12:00:00+00:00"] [data-select-key]').click()
        page.get_by_role('button',name='Terminate Process').click()
        page.wait_for_function('window.__requests.some(v=>v.url.endsWith("/action"))')
        calls=page.evaluate('window.__requests')
        process=next(x for x in calls if x['url'].endswith('/action'))
        assert process['body']['expected_created_at']=='2026-11-01T12:00:00+00:00'
        assert any(x['url'].endswith('/authority/session') for x in calls)
        assert page.locator('[data-mcel-node="surface-node:semantic-task-manager-generated-p3.operation-receipt"]').count()==1
    finally:
        page.close()


def test_heartbeat_control_requires_independent_session(tmp_path, monkeypatch):
    """Real loopback HTTP test: unauthorized lifecycle request never dispatches."""
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    from main_computer.heartbeat import HeartbeatConfig, HeartbeatHandler
    import main_computer.heartbeat as heartbeat
    monkeypatch.setattr(heartbeat, 'status_payload', lambda cfg:{'ok':True,'heartbeat':{'ready':True}})
    monkeypatch.setattr(heartbeat, 'start_viewport', lambda cfg: (_ for _ in ()).throw(AssertionError('unsafe start')))
    monkeypatch.setattr(heartbeat, 'stop_viewport', lambda cfg: (_ for _ in ()).throw(AssertionError('unsafe stop')))
    srv=ThreadingHTTPServer(('127.0.0.1',0),HeartbeatHandler)
    port=srv.server_address[1]
    srv.config=HeartbeatConfig(workspace=tmp_path,server_port=port+1,heartbeat_port=port)
    srv.signal=lambda *args,**kwargs:None
    srv.request_shutdown=lambda **kw:None
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    host=f'127.0.0.1:{port}'
    origin=f'http://127.0.0.1:{port+1}'
    base={'Host':host,'Origin':origin,'Content-Type':'application/json'}
    try:
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        conn.request('POST','/api/heartbeat/control','{"action":"shutdown"}',headers=base)
        response=conn.getresponse();response.read()
        assert response.status==403
        conn.close()
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        conn.request('GET','/api/heartbeat/authority/session',headers={'Host':host,'Origin':origin})
        issued=conn.getresponse()
        assert issued.status==200
        assert issued.getheader('Access-Control-Allow-Origin')==origin
        assert issued.getheader('Access-Control-Allow-Origin')!='*'
        cookie=issued.getheader('Set-Cookie').split(';',1)[0]
        token=json.loads(issued.read())['csrf']
        conn.close()
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        conn.request('POST','/api/heartbeat/control','{"action":"status"}',headers={
            **base,'Cookie':cookie,'X-MCEL-Heartbeat-CSRF':token})
        response=conn.getresponse();reply=json.loads(response.read())
        assert response.status==200 and reply['heartbeat']['ready'] is True
        conn.close()
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        conn.request('POST','/api/heartbeat/control','{"action":"shutdown"}',headers={
            **base,'Cookie':cookie,'X-MCEL-Heartbeat-CSRF':'forged'})
        response=conn.getresponse();response.read()
        assert response.status==403
        conn.close()
    finally:
        srv.shutdown();srv.server_close();thread.join(timeout=5)


def test_direct_server_action_request_id_is_durable(tmp_path, monkeypatch):
    svc=service(tmp_path)
    calls=[]
    monkeypatch.setattr(svc, 'perform_action', lambda **kw: (calls.append(kw) or
                         {'ok':True,'action':kw['action'],'deferred':True}))
    args=dict(action='server_restart',request_id='restart_123456789012',confirm=True)
    first=svc.perform_action_idempotent(**args)
    assert first['outcome']=='accepted-deferred'
    second=service(tmp_path)
    monkeypatch.setattr(second,'perform_action',lambda **kw: (_ for _ in ()).throw(AssertionError('duplicate dispatch')))
    assert second.perform_action_idempotent(**args)==first
    assert len(calls)==1
    with pytest.raises(ValueError,match='different action'):
        second.perform_action_idempotent(action='server_shutdown',request_id=args['request_id'],confirm=True)


def test_bootstrap_shutdown_uses_heartbeat_session(tmp_path, monkeypatch):
    """Installer Python request obtains cookie and CSRF; old direct POST fails."""
    import threading
    from http.server import ThreadingHTTPServer
    from main_computer.heartbeat import HeartbeatConfig, HeartbeatHandler
    from main_computer.bootstrap.cli import _post_json
    import main_computer.heartbeat as hb
    monkeypatch.setattr(hb, 'status_payload', lambda cfg: {'ok': True})
    srv = ThreadingHTTPServer(('127.0.0.1', 0), HeartbeatHandler)
    port = srv.server_address[1]
    srv.config = HeartbeatConfig(workspace=tmp_path, server_port=port+1, heartbeat_port=port)
    srv.signal = lambda *a, **kw: None
    srv.request_shutdown = lambda **kw: None
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        assert _post_json(f'http://127.0.0.1:{port}/api/heartbeat/control',
                          {'action': 'status'}) is True
    finally:
        srv.shutdown(); srv.server_close(); thread.join(timeout=5)
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'main_computer/bootstrap/cli.py'
    script = path.read_text(encoding='utf-8')
    assert 'SessionVariable mcHeartbeatSession' in script
    assert 'X-MCEL-Heartbeat-CSRF' in script

# MCEL interface-replacement acceptance: the real generated JS capability port,
# real viewport Task Manager HTTP route methods and real TaskManagerService are
# connected through loopback HTTP. Only OS process mutation, Windows controller
# execution and the paid AI provider are replaced by explicitly test-owned
# boundaries. This does NOT assert Windows-controller or real-provider fidelity.
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import threading

# Loading viewport_state eagerly pulls in unrelated game-project assets that are
# absent from source-only exports. Load the actual, unmodified route module with
# its common HTTPStatus namespace supplied, without loading the full viewport.
# The task route methods themselves are imported unchanged from the source file.
import importlib.util
import sys
from unittest.mock import patch as _mock_patch
from types import ModuleType


def _load_bounded_task_routes():
    path = Path(__file__).resolve().parents[1] / 'main_computer/viewport_routes_task.py'
    spec = importlib.util.spec_from_file_location('_mcel_bounded_viewport_task_routes', path)
    module = importlib.util.module_from_spec(spec)
    shim = ModuleType('main_computer.viewport_state')
    shim.HTTPStatus = HTTPStatus
    with _mock_patch.dict(sys.modules, {'main_computer.viewport_state': shim}):
        spec.loader.exec_module(module)
    return module.ViewportTaskRoutesMixin


ViewportTaskRoutesMixin = _load_bounded_task_routes()

_INTEGRATION_PID = 64251  # synthetic identity, never passed to the host OS
_INTEGRATION_BIRTH = '2026-11-02T22:50:46.355+00:00'


class _TaskIntegrationHandler(ViewportTaskRoutesMixin, BaseHTTPRequestHandler):
    """Real route mixin on a test-only HTTP transport; no production dispatch."""
    def log_message(self, *args):
        pass

    def _read_json(self):
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 16384:
            raise ValueError('Invalid bounded task request length')
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise ValueError('Task request must be an object')
        # Record non-secret request bodies for deterministic contract assertions.
        self.server.requests.append((self.path, value))
        return value

    @staticmethod
    def _coerce_bool(value, *, default=False):
        if isinstance(value, bool):
            return value
        if value in ('true', '1', 1):
            return True
        if value in ('false', '0', 0):
            return False
        return default

    def _send_json(self, payload, status=HTTPStatus.OK):
        value = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(value)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(value)

    def do_GET(self):
        if self.path == '/applications/mcel-semantic-task-manager-generated-p3':
            value = render_p3_generated_page().encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(value)))
            self.end_headers()
            self.wfile.write(value)
        elif self.path == '/api/applications/task/authority/session':
            authority.issue(self)
        else:
            self.send_error(404)

    def do_POST(self):
        commands = {
            '/api/applications/task/overview': self._handle_task_overview,
            '/api/applications/task/action': self._handle_task_action,
            '/api/applications/task/schedules': self._handle_task_schedules,
            '/api/applications/task/schedule/create': self._handle_task_schedule_create,
            '/api/applications/task/schedule/delete': self._handle_task_schedule_delete,
            '/api/applications/task/schedule/run': self._handle_task_schedule_run,
            '/api/applications/task/ai': self._handle_task_ai,
        }
        action = commands.get(self.path)
        if action is None:
            self.send_error(404)
            return
        # Same authenticated mutation allowlist as viewport_route_dispatch.py.
        if self.path in {
            '/api/applications/task/action',
            '/api/applications/task/schedule/create',
            '/api/applications/task/schedule/delete',
            '/api/applications/task/schedule/run',
            '/api/applications/task/ai',
        } and not authority.require(self):
            return
        action()


@pytest.fixture
def isolated_task_http(tmp_path, monkeypatch):
    """Never spawns a host process, invokes PowerShell or loads prod state."""
    repo = tmp_path / 'disposable-repo'
    repo.mkdir()
    control = tmp_path / 'disposable-controller'
    control.mkdir()
    effects = []
    server = ThreadingHTTPServer(('127.0.0.1', 0), _TaskIntegrationHandler)
    assert server.server_port != 8765
    service = TaskManagerService(repo, default_port=server.server_port,
        control_root=control, isolated_server_executor=lambda verb:
            (effects.append(('controller', verb)) or
             {'ok': True, 'deferred': verb in ('restart', 'shutdown'),
              'message': 'bounded isolated executor'}))
    assert service.control_root == control.resolve()
    assert service.data_root.is_relative_to(tmp_path)
    process_time = datetime.fromisoformat(_INTEGRATION_BIRTH).timestamp()
    generation = {'epoch': process_time}

    class _OnlyTestProcess:
        def __init__(self, pid):
            if pid != _INTEGRATION_PID:
                raise AssertionError('Non-disposable process accessed')
        def create_time(self):
            return generation['epoch']
        def terminate(self):
            effects.append(('process', 'terminate', _INTEGRATION_PID))
        def kill(self):
            effects.append(('process', 'kill', _INTEGRATION_PID))
        def wait(self, timeout):
            return 0

    monkeypatch.setattr('main_computer.task_manager.psutil', SimpleNamespace(
        Process=_OnlyTestProcess, NoSuchProcess=LookupError,
        AccessDenied=PermissionError, ZombieProcess=RuntimeError))
    monkeypatch.setattr(service, '_process_rows', lambda **kw: [{
        'pid': _INTEGRATION_PID, 'name': 'disposable-contract-target',
        'status': 'running', 'memory_human': '1 MB',
        'memory_rss': 1024, 'created_at': _INTEGRATION_BIRTH,
        'is_current_process': False, 'is_main_computer': True,
        'command_preview': 'bounded test only',
    }])
    monkeypatch.setattr(service, '_connection_rows', lambda **kw: [])
    monkeypatch.setattr(service, '_server_summary', lambda **kw: {
        'running': False, 'pid': None, 'is_current_process': False,
    })
    monkeypatch.setattr(service, '_hardware_snapshot', lambda: {
        'summary': 'bounded telemetry',
        'cpu': {'available': False, 'overall_percent': None, 'per_core': []},
        'gpu': {'available': False, 'overall_percent': None, 'devices': []},
    })
    class _Provider:
        fail = False
        def chat(self, prompt):
            effects.append(('ai-provider-contract', prompt))
            if self.fail:
                raise RuntimeError('Isolated model provider deliberately unavailable')
            return SimpleNamespace(content='Bounded AI contract result',
                                   provider='test-boundary', model='non-production')
    server.task_manager = service
    server.computer = _Provider()
    server.signal = lambda *a, **kw: None
    server.requests = []
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    try:
        yield SimpleNamespace(url=f'http://127.0.0.1:{server.server_port}',
                              service=service, requests=server.requests, effects=effects,
                              cookie='', cookie_flags='', generation=generation,
                              ai_provider=server.computer, accept_dialog=True)
    finally:
        server.shutdown()
        server.server_close()
        runner.join(timeout=5)


def _task_http_transport_bridge(lab, uri, options):
    """Proxy fetch across CI's denied browser loopback to real HTTP methods.

    Browser-to-Python is a test transport; Python-to-task-route is genuine HTTP.
    The cookie is held only in the Python transport and never available to JS.
    """
    if not isinstance(uri, str) or not uri.startswith('/api/applications/task/'):
        raise AssertionError('Task transport escaped the isolated HTTP allowlist')
    opts = options or {}
    method = str(opts.get('method') or 'GET').upper()
    if method not in ('GET', 'POST'):
        raise AssertionError('Unexpected task HTTP method')
    from urllib.parse import urlsplit
    address = urlsplit(lab.url)
    conn = http.client.HTTPConnection(address.hostname, address.port, timeout=12)
    headers = {'Host': address.netloc, 'Origin': lab.url,
               'Sec-Fetch-Site': 'same-origin'}
    headers.update(opts.get('headers') or {})
    if lab.cookie:
        headers['Cookie'] = lab.cookie
    if method == 'POST':
        assert opts.get('credentials') == 'same-origin'
    try:
        conn.request(method, uri, body=opts.get('body'), headers=headers)
        response = conn.getresponse()
        data = response.read()
        set_cookie = response.getheader('Set-Cookie')
        if set_cookie:
            assert 'HttpOnly' in set_cookie and 'SameSite=Strict' in set_cookie
            lab.cookie = set_cookie.split(';', 1)[0]
            lab.cookie_flags = set_cookie
        return {'status': response.status,
                'payload': json.loads(data), 'ok': 200 <= response.status < 300}
    finally:
        conn.close()


def _task_page(browser, lab):
    page = browser.new_page(viewport={'width': 1100, 'height': 800})
    page.on('dialog', lambda dialog: dialog.accept() if lab.accept_dialog else dialog.dismiss())
    page.expose_binding('__taskContractHttp',
                        lambda source, uri, opts: _task_http_transport_bridge(lab, uri, opts))
    page.evaluate("""() => { window.fetch=async(uri, opts={}) => {
      const r=await window.__taskContractHttp(String(uri),opts);
      return {ok:r.ok,status:r.status,json:async()=>r.payload};
    }; }""")
    page.set_content(render_p3_generated_page())
    page.locator('#mcel-workbench-status').wait_for(timeout=9000)
    assert 'mounted' in page.locator('#mcel-workbench-status').inner_text()
    return page


def _contract_receipt(page, intent, expected):
    page.locator(f'li[data-intent="intent:{intent}"][data-disposition="{expected}"]').first.wait_for(state="attached", timeout=9000)


def _task_refresh_and_select(page):
    page.locator('form[data-intent="intent:refresh"] button[type=submit]').click()
    key = f'{_INTEGRATION_PID}:{_INTEGRATION_BIRTH}'
    row = page.locator(f'[data-record-key="{key}"]').first
    row.locator('button[data-select-key]').click()
    page.locator(f'[data-mcel-selected-details="{key}"]:visible').first.wait_for(timeout=9000)


def _test_request(url, path, payload, *, cookie='', csrf=''):
    from urllib.parse import urlsplit
    host = urlsplit(url)
    conn = http.client.HTTPConnection(host.hostname, host.port, timeout=5)
    headers = {'Content-Type': 'application/json', 'Origin': url}
    if cookie:
        headers['Cookie'] = cookie
    if csrf:
        headers['X-MCEL-Task-CSRF'] = csrf
    try:
        conn.request('POST', path, json.dumps(payload), headers=headers)
        response = conn.getresponse()
        return response.status, json.loads(response.read())
    finally:
        conn.close()


def test_p3_real_http_process_buttons_bounded(browser, isolated_task_http):
    lab = isolated_task_http
    page = _task_page(browser, lab)
    try:
        _task_refresh_and_select(page)
        for intent, verb in (('terminate-process', 'terminate'), ('kill-process', 'kill')):
            page.locator(f'button[data-intent="intent:{intent}"]').click()
            _contract_receipt(page, intent, 'command-returned')
            action = [body for path, body in lab.requests if path.endswith('/task/action')][-1]
            assert action['action'] == ('terminate_pid' if verb == 'terminate' else 'kill_pid')
            assert action['pid'] == _INTEGRATION_PID
            assert action['expected_created_at'] == _INTEGRATION_BIRTH
            assert action['confirm'] is True and len(action['request_id']) >= 12
            assert ('process', verb, _INTEGRATION_PID) in lab.effects
            if verb == 'terminate':
                _task_refresh_and_select(page)
        assert len([e for e in lab.effects if e[0] == 'process']) == 2
        assert len([v for v in lab.requests if v[0].endswith('/authority/session')]) == 0  # GET, not a mutation
    finally:
        page.close()


def test_p3_real_http_server_buttons_and_cancellation(browser, isolated_task_http):
    lab = isolated_task_http
    page = _task_page(browser, lab)
    try:
        lab.accept_dialog=False
        page.locator('button[data-intent="intent:shutdown-server"]').click()
        page.wait_for_timeout(100)
        assert not any(x[0] == 'controller' for x in lab.effects)
        lab.accept_dialog=True
        for intent, verb in (('start-server', 'start'), ('restart-server', 'restart'),
                             ('shutdown-server', 'shutdown')):
            page.locator(f'button[data-intent="intent:{intent}"]').click()
            _contract_receipt(page, intent, 'accepted-deferred' if verb != 'start' else 'command-returned')
            assert ('controller', verb) in lab.effects
        server_actions = [r[1] for r in lab.requests if r[0].endswith('/task/action')]
        assert [a['action'] for a in server_actions] == ['server_start', 'server_restart', 'server_shutdown']
        assert all(a['confirm'] is True and len(a['request_id']) >= 12 for a in server_actions)
        assert all(a.get('request_id') for a in server_actions)
    finally:
        page.close()


def test_p3_real_http_schedule_crud_and_run(browser, isolated_task_http):
    lab = isolated_task_http
    page = _task_page(browser, lab)
    try:
        form = page.locator('form[data-intent="intent:create-schedule"]')
        form.locator('[name="action"]').fill('server_restart')
        form.locator('[name="runAt"]').fill('2026-11-04T12:00:00+00:00')
        form.locator('[name="note"]').fill('bounded contract')
        form.locator('button[type=submit]').click()
        _contract_receipt(page, 'create-schedule', 'recorded')
        records = lab.service.list_schedules()['schedules']
        assert len(records) == 1 and records[0]['action'] == 'server_restart'
        page.locator('button[data-intent="intent:list-schedules"]').click()
        schedule = page.locator(f'[data-record-key="{records[0]["id"]}"]')
        schedule.locator('button[data-select-key]').click()
        page.locator('button[data-intent="intent:run-schedule"]').click()
        _contract_receipt(page, 'run-schedule', 'accepted-deferred')
        assert ('controller', 'restart') in lab.effects
        run = next(body for path, body in lab.requests if path.endswith('/schedule/run'))
        assert run['expected_revision'] == records[0]['revision']
        assert run['expected_action'] == records[0]['action']
        assert run['request_id'].startswith('manual_') and run['confirm'] is True
        page.locator('button[data-intent="intent:delete-schedule"]').click()
        _contract_receipt(page, 'delete-schedule', 'recorded')
        assert lab.service.list_schedules()['schedules'] == []
        assert not any(path.endswith('/schedule/run') and body['schedule_id'] != records[0]['id']
                       for path, body in lab.requests)
    finally:
        page.close()


def test_p3_real_http_ai_contract_provider_boundary(browser, isolated_task_http):
    lab = isolated_task_http
    page = _task_page(browser, lab)
    try:
        form = page.locator('form[data-intent="intent:task-ai"]')
        form.locator('[name="instruction"]').fill('Summarize the disposable process only')
        form.locator('[name="query"]').fill('disposable')
        form.locator('button[type=submit]').click()
        _contract_receipt(page, 'task-ai', 'completed')
        assert any(e[0] == 'ai-provider-contract' and 'Summarize the disposable process only' in e[1]
                   for e in lab.effects)
        assert 'Bounded AI contract result' in page.locator('body').inner_text()
        request = next(body for path, body in lab.requests if path.endswith('/task/ai'))
        assert request['instruction'] == 'Summarize the disposable process only'
        assert request['query'] == 'disposable'
    finally:
        page.close()


def test_p3_real_http_negative_contracts_and_idempotent_receipts(browser, isolated_task_http):
    lab = isolated_task_http
    page = _task_page(browser, lab)
    try:
        _task_refresh_and_select(page)
        unauthorized, unauthorized_body = _test_request(lab.url, '/api/applications/task/action',
            {'action': 'server_shutdown', 'confirm': True, 'request_id': 'unauthorized_123456'})
        assert unauthorized == 403 and unauthorized_body['error'] == 'task-session-required'
        assert not lab.effects

        # The real CSRF issuer is exercised below. Its HttpOnly cookie stays
        # inside the Python HTTP bridge, never exposed to page JavaScript.
        # A real browser request with an intentionally stale target is refused.
        stale = page.evaluate('''async() => {
          const session=await fetch('/api/applications/task/authority/session').then(r=>r.json());
          const r=await fetch('/api/applications/task/action', {method:'POST', credentials:'same-origin',
            headers:{'Content-Type':'application/json','X-MCEL-Task-CSRF':session.csrf},
            body:JSON.stringify({action:'terminate_pid',pid:64251,confirm:true,
              expected_created_at:'2026-11-01T22:50:46.355+00:00',request_id:'stale_generation_1234'})});
          return {status:r.status,payload:await r.json()};
        }''')
        assert lab.cookie and 'HttpOnly' in lab.cookie_flags
        assert stale['status'] == 400 and 'generation mismatch' in stale['payload']['error'].lower()
        assert not lab.effects

        # A real service and route refuse timezone-naive schedule values.
        invalid = page.evaluate('''async() => {
          const session=await fetch('/api/applications/task/authority/session').then(r=>r.json());
          const r=await fetch('/api/applications/task/schedule/create',{method:'POST',credentials:'same-origin',
            headers:{'Content-Type':'application/json','X-MCEL-Task-CSRF':session.csrf},
            body:JSON.stringify({action:'server_restart',run_at:'2026-11-04T12:00',note:'no-zone',payload:{}})});
          return {status:r.status,payload:await r.json()};
        }''')
        assert invalid['status'] == 400 and 'offset' in invalid['payload']['error'].lower()
        assert lab.service.list_schedules()['schedules'] == []

        # Identical retries return a durable prior receipt without dispatching twice.
        args = {'action':'server_start','confirm':True,'request_id':'replay_contract_123456'}
        replay = page.evaluate('''async args => {
          const session=await fetch('/api/applications/task/authority/session').then(r=>r.json());
          const headers={'Content-Type':'application/json','X-MCEL-Task-CSRF':session.csrf};
          const act=()=>fetch('/api/applications/task/action',{method:'POST',credentials:'same-origin',
            headers,body:JSON.stringify(args)}).then(r=>r.json());
          return [await act(),await act()];
        }''', args)
        assert replay[0] == replay[1] and replay[0]['ok'] is True
        assert lab.effects == [('controller', 'start')]

        # A generated UI click with a stale-but-previously-observed process
        # generation must receive failure; the synthetic OS boundary is never called.
        _task_refresh_and_select(page)
        lab.generation['epoch'] += 3600
        page.locator('button[data-intent="intent:terminate-process"]').click()
        _contract_receipt(page, 'terminate-process', 'failed')
        assert lab.effects == [('controller', 'start')]

        # The client currently accepts Date.parse() on naive local dates, while
        # the real service requires an offset. Verify this mismatch refuses at
        # the backend and the generated activity reports failure, not success.
        form=page.locator('form[data-intent="intent:create-schedule"]')
        form.locator('[name="action"]').fill('server_restart')
        form.locator('[name="runAt"]').fill('2026-11-04T12:00')
        form.locator('[name="note"]').fill('must-refuse-naive-time')
        form.locator('button[type=submit]').click()
        _contract_receipt(page, 'create-schedule', 'failed')
        assert lab.service.list_schedules()['schedules'] == []

        # Provider failures propagate as failed activity, never a fabricated
        # AI brief or a completed receipt.
        lab.ai_provider.fail = True
        ai=page.locator('form[data-intent="intent:task-ai"]')
        ai.locator('[name="instruction"]').fill('Deliberately fail')
        ai.locator('[name="query"]').fill('disposable')
        ai.locator('button[type=submit]').click()
        _contract_receipt(page, 'task-ai', 'failed')
        assert 'Bounded AI contract result' not in page.locator('body').inner_text()

        # On execution uncertainty, the journal preserves an unknown receipt
        # and will not re-dispatch an identical request ID after the exception.
        attempted=[]
        def failed_control(verb):
            attempted.append(verb)
            raise RuntimeError('Deliberate isolated executor interruption')
        lab.service._isolated_server_executor=failed_control
        uncertain={'action':'server_start','confirm':True,'request_id':'unknown_contract_12345'}
        outcomes=page.evaluate('''async args => {
          const session=await fetch('/api/applications/task/authority/session').then(r=>r.json());
          const headers={'Content-Type':'application/json','X-MCEL-Task-CSRF':session.csrf};
          const act=async()=>{const r=await fetch('/api/applications/task/action',
            {method:'POST',credentials:'same-origin',headers,body:JSON.stringify(args)});
            return {status:r.status,payload:await r.json()};};
          return [await act(),await act()];
        }''', uncertain)
        assert outcomes[0]['status']==400
        assert outcomes[1]['status']==400
        assert outcomes[1]['payload']['outcome']=='unknown'
        assert attempted==['start']
        assert [entry for entry in lab.effects if entry[0]=='controller']==[('controller','start')]
        assert len([entry for entry in lab.effects if entry[0]=='ai-provider-contract'])==1
    finally:
        page.close()
