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
