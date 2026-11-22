"""Actual-handler HTTP target/probe for the disposable MC correction lab.

Only selected handler methods are compiled from baseline/candidate copies. Stub
objects replace production state and side effects. Docker runs a separate probe
container sharing target's network namespace; no Docker ports are published.
This proves an isolated candidate, NOT production integration.
"""
from __future__ import annotations
import argparse
import ast
from dataclasses import asdict, dataclass
import hmac
import hashlib
import http.client
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse, urlsplit

try:
    from mcel_security_session_gateway import make_gateway, make_onlyoffice_status_handler, probe_session
except ImportError:
    from tools.mcel_security_session_gateway import make_gateway, make_onlyoffice_status_handler, probe_session

def capability_for(kind: str) -> str:
    family = kind.removesuffix('-configured')
    return hmac.new(SECRET.encode('ascii'), ('mc-security-'+family).encode('ascii'), hashlib.sha256).hexdigest()

ROOT = Path(os.environ.get('MC_CORRECTION_ROOT', '/lab'))
SECRET = os.environ.get('MC_CORRECTION_CAPABILITY', '')
LOGIN_KEY = os.environ.get('MC_CORRECTION_LOGIN_KEY', '')
BASE = int(os.environ.get('MC_CORRECTION_PORT_BASE', '32410'))

# Deliberate import-free extraction of unchanged actual class and function
# bodies: source modules contain unrelated large MC dependencies, impossible to
# safely spin up in a small isolated Python fixture. No rewriting of code in
# the extracted body occurs. Candidate transformation changes the copied files.
def extract(path: Path, funcs: tuple[str,...]=(), classes: dict[str,tuple[str,...]] | None=None, scope: dict | None=None) -> dict:
    tree=ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
    nodes=[]
    for n in tree.body:
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in funcs:
            nodes.append(n)
        elif isinstance(n,ast.ClassDef) and classes and n.name in classes:
            methods=[m for m in n.body if isinstance(m,ast.FunctionDef) and m.name in classes[n.name]]
            if not methods:
                raise RuntimeError('CLASS_METHODS_MISSING '+n.name)
            nodes.append(ast.ClassDef(name=n.name,bases=[ast.Name(id='BaseHTTPRequestHandler',ctx=ast.Load())],
                                      keywords=[],body=methods,decorator_list=[]))
    found={n.name for n in nodes}
    # The baseline intentionally lacks candidate-only admission helpers.
    optional = {'heartbeat_service_authorized','main_log_authorized'} if path.parent.name == 'baseline' else set()
    required=(set(funcs)-optional)|set(classes or {})
    if found!=required:
        raise RuntimeError('SOURCE_DRIFT_MISSING '+str(required-found)+' '+str(path))
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0), *nodes],type_ignores=[])
    ast.fix_missing_locations(module)
    namespace={'__name__':'mc_security_fixture_'+path.stem, 'BaseHTTPRequestHandler':BaseHTTPRequestHandler,
               'HTTPStatus':HTTPStatus,'json':json,'hmac':hmac,'secrets':secrets,'time':time,
               'ipaddress':ipaddress,'urlsplit':urlsplit,'urlparse':urlparse,'SimpleCookie':SimpleCookie,
               'parse_qs':parse_qs,'re':re,'os':os,'threading':threading,'asdict':asdict,'Any':Any,
               'Path':Path,'SimpleNamespace':SimpleNamespace}
    namespace.update(scope or {})
    exec(compile(module,str(path),'exec'),namespace)
    return namespace


def reply(handler,status:int, data:dict | None=None):
    body=json.dumps(data or {'ok':status==200}).encode()
    handler.send_response(status)
    handler.send_header('Content-Type','application/json')
    handler.send_header('Content-Length',str(len(body)))
    handler.end_headers();handler.wfile.write(body)


class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True


@dataclass
class Message:
    reply:str='ok'


class FakeCatalog:
    def list_projects(self):return []
    def inspect(self,name):return SimpleNamespace(name=name,path=Path('/isolated/example'),markers=[],child_count=0,file_count=0)


class FakeStore:
    root=Path('/private-fixture')
    log_path=Path('/private-fixture/main.log')
    raw_log_path=Path('/private-fixture/raw.log')
    raw_mirror=False
    surprise_path=Path('/private-fixture/surprise.json')
    appended=[]
    def __init__(self):self.appended=[]
    def recent(self,*,limit):return [{'private_marker':'PRIVATE-LOG-SENTINEL'}]
    def append_many(self,events):self.appended.extend(events);return {'ok':True,'count':len(events)}


class FakeBrowser:
    def __init__(self):self.calls=[]
    def call(self,operation,**kwargs):
        self.calls.append((operation,kwargs))
        return {'ok':True,'operation':operation,'count':len(self.calls)}


def viewport_handler(path:Path):
    from_source=extract(path,classes={'ViewportHandler':('do_GET','do_POST','_security_gate')},
        scope={'dispatch_get': lambda h:reply(h,200,{'dispatched':True,'method':'GET'}),
               'dispatch_post':lambda h:reply(h,200,{'dispatched':True,'method':'POST'})})
    class Handler(from_source['ViewportHandler']):
        def log_message(self,*args):pass
    return Handler


def heartbeat_handler(path:Path):
    n=extract(path,funcs=('heartbeat_local_caller','heartbeat_valid_mutation','heartbeat_service_authorized'),
            classes={'HeartbeatHandler':('do_GET','do_POST','_send_json','_cors_headers','do_OPTIONS')},
            scope={'_HEARTBEAT_SESSION_LOCK':threading.RLock(),'_HEARTBEAT_SESSIONS':{},'_HEARTBEAT_TTL':900.0,
                   'status_payload':lambda cfg:{'ok':True,'state':'isolated-fixture'}})
    # Source's heartbeat_valid_mutation looks up a global sessions dictionary
    # directly, so keep scope isolated per baseline/candidate server.
    class Handler(n['HeartbeatHandler']):
        def log_message(self,*args):pass
    return Handler


def mainlog_handler(path:Path):
    n=extract(path,funcs=('_json_response','_coerce_port','main_log_authorized','_now_iso'),
              classes={'MainLogRequestHandler':('do_GET','do_POST','log_message')},
              scope={'MAX_EVENT_BYTES':256*1024,'DEFAULT_RECENT_LIMIT':200,'DEFAULT_MAIN_LOG_PORT':8767,'SERVICE_NAME':'mc-security-isolated-log',
                     'datetime':__import__('datetime').datetime,'timezone':__import__('datetime').timezone})
    return n['MainLogRequestHandler']


def openclaw_handler(path:Path):
    n=extract(path,classes={'OpenClawBridgeHandler':('do_GET','do_POST','_authorize','_send_json',
                    '_health_payload','_capabilities_payload','_projects_payload','_read_json',
                    '_handle_chat','_handle_project_inspect','log_message')})
    return n['OpenClawBridgeHandler']


def browser_handler(path:Path):
    n=extract(path,funcs=('browser_request_authorized','_session_id','_coordinate','_command',
                          'handle_browser_post','_port_base'),scope={
       '_SESSION_RE':re.compile(r'[A-Za-z0-9_-]{24,64}\Z'),
       '_KEY_NAMES':frozenset({'Enter','Escape'}),
       '_OPERATIONS':frozenset({'close','start','navigate','back','forward','reload','click','scroll','key','type','resize'}),
       'BrowserError':type('BrowserError',(Exception,),{}),
       'resolve_browser_address':lambda *args,**kwargs:'about:blank',
    })
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def _send_json(self,data,status=HTTPStatus.OK):reply(self,int(status),data)
        def do_POST(self): n['handle_browser_post'](self)
    return Handler


PORTS=[('viewport',0),('viewport',1),('heartbeat',2),('heartbeat',3),('mainlog',4),('mainlog',5),
       ('openclaw',6),('openclaw',7),('openclaw-configured',8),('browser',9),('browser',10)]
FILES={'viewport':'viewport_server.py','heartbeat':'heartbeat.py','mainlog':'main_log_service.py',
       'openclaw':'openclaw_bridge.py','openclaw-configured':'openclaw_bridge.py','browser':'mcel_backend_browser_route.py'}


def serve() -> int:
    if len(SECRET)<32:raise RuntimeError('MISSING_FIXTURE_CAPABILITY')
    if len(LOGIN_KEY)<32 or LOGIN_KEY==SECRET:raise RuntimeError('MISSING_OR_REUSED_FIXTURE_LOGIN_KEY')
    servers=[]
    for kind,i in PORTS:
        variant='baseline' if i in {0,2,4,6,9} else 'candidate'
        path=ROOT/variant/FILES[kind]
        if kind=='viewport': handler=viewport_handler(path)
        elif kind=='heartbeat':handler=heartbeat_handler(path)
        elif kind=='mainlog':handler=mainlog_handler(path)
        elif kind.startswith('openclaw'):handler=openclaw_handler(path)
        elif kind=='browser':handler=browser_handler(path)
        else:raise RuntimeError(kind)
        server=Server(('0.0.0.0',BASE+i),handler)
        server.ingress_capability=capability_for(kind)
        server.signal=lambda *a,**k:None
        if kind=='heartbeat':
            server.config=SimpleNamespace(actual_heartbeat_port=BASE+i,server_port=BASE+100)
            server.request_shutdown=lambda **k: (_ for _ in ()).throw(AssertionError('UNSAFE_LIFECYCLE_EXECUTION'))
        if kind=='mainlog':server.store=FakeStore()
        if kind.startswith('openclaw'):
            server.config=SimpleNamespace(workspace=Path('/private-fixture'),patch_level='fixture',provider='fixture',model='fixture')
            server.computer=SimpleNamespace(catalog=FakeCatalog(),chat=lambda prompt:Message())
            server.token=capability_for('openclaw') if kind=='openclaw-configured' else None
        if kind=='browser':server.backend_browser=FakeBrowser()
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        servers.append(server)
        print('MC_CORRECTION_TARGET '+kind+' '+variant+' ready port='+str(BASE+i),flush=True)
    # One persistent session store, one authenticated ingress, all services.
    # In the local fixture, localhost listeners are disposable and source-backed.
    onlyoffice_source = ROOT / 'viewport_routes_onlyoffice.py'
    server=Server(('0.0.0.0',BASE+12), make_onlyoffice_status_handler(SECRET, onlyoffice_source))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    servers.append(server)
    print('MC_CORRECTION_TARGET onlyoffice-status candidate ready port='+str(BASE+12),flush=True)
    session_file=Path(os.environ.get('MC_CORRECTION_SESSION_FILE',f'/tmp/mc-unified-session-{BASE}.json'))
    gateway=Server(('0.0.0.0',BASE+11),make_gateway(BASE,SECRET,session_file,LOGIN_KEY))
    threading.Thread(target=gateway.serve_forever,daemon=True).start()
    servers.append(gateway)
    print('MC_UNIFIED_SESSION gateway ready port='+str(BASE+11),flush=True)
    try:
        while True:time.sleep(60)
    except KeyboardInterrupt:
        return 0
    finally:
        for server in servers:server.shutdown();server.server_close()


def get(port:int,path:str,method='GET', body:dict|None=None,headers:dict|None=None):
    h={'Host':f'127.0.0.1:{port}'}
    h.update(headers or {})
    data=json.dumps(body).encode() if body is not None else None
    if body is not None: h['Content-Type']='application/json'
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
    try:
        conn.request(method,path,body=data,headers=h)
        result=conn.getresponse()
        payload=result.read(128*1024)
        try:obj=json.loads(payload)
        except ValueError:obj={'raw':payload[:90].decode('utf-8','replace')}
        return result.status,dict(result.getheaders()),obj
    finally:conn.close()


def probe() -> int:
    ready=False
    for _ in range(80):
        try:
            get(BASE,'/test')
            ready=True;break
        except OSError:time.sleep(0.1)
    if not ready:
        print('MC_CORRECTION_PROBE ERROR fixture not ready',flush=True)
        return 2
    count=0; failures=[]
    def check(name,passed,detail=''):
        nonlocal count
        count+=1
        print('MC_CORRECTION_PROBE '+name+' '+('PASS' if passed else 'FAIL')+(' '+detail if detail else ''),flush=True)
        if not passed:failures.append(name)
    gateway={'X-MC-Gateway-Capability':capability_for('viewport')}
    service={'X-MC-Internal-Authority':capability_for('heartbeat')}
    mainlog_service={'X-MC-Internal-Authority':capability_for('mainlog')}
    browser_gateway={'X-MC-Gateway-Capability':capability_for('browser')}
    bad_gateway={'X-MC-Gateway-Capability':'forged'}
    bad_service={'X-MC-Internal-Authority':'forged'}
    status,_,data=get(BASE+0,'/api/private')
    check('viewport-baseline-bypass-reproduced',status==200 and data.get('dispatched') is True)
    check('viewport-unauthenticated-get-denied',get(BASE+1,'/api/private')[0]==403)
    check('viewport-forged-capability-denied',get(BASE+1,'/api/private',headers=bad_gateway)[0]==403)
    check('viewport-authorized-get-works',get(BASE+1,'/api/private',headers=gateway)[0]==200)
    check('viewport-unauthenticated-post-denied',get(BASE+1,'/api/control',method='POST',body={})[0]==403)
    check('viewport-authorized-post-works',get(BASE+1,'/api/control',method='POST',body={},headers=gateway)[0]==200)
    check('viewport-baseline-post-bypass-reproduced',get(BASE+0,'/api/control',method='POST',body={})[0]==200)
    authority='/api/heartbeat/authority/session'
    check('heartbeat-baseline-issue-reproduced',get(BASE+2,authority)[0]==200)
    check('heartbeat-anonymous-issuance-denied',get(BASE+3,authority)[0]==403)
    check('heartbeat-forged-service-key-denied',get(BASE+3,authority,headers=bad_service)[0]==403)
    status,headers,payload=get(BASE+3,authority,headers=service)
    check('heartbeat-authorized-issue-works',status==200 and bool(payload.get('csrf')) and 'Set-Cookie' in headers)
    for name,route in [('status','/api/heartbeat/status'),('healthz','/healthz'),('control-get','/api/heartbeat/control')]:
        check('heartbeat-'+name+'-anonymous-read-denied',get(BASE+3,route)[0]==403)
        check('heartbeat-'+name+'-service-read-works',get(BASE+3,route,headers=service)[0]==200)
    cookie=headers.get('Set-Cookie','').partition(';')[0]
    mutation={'Cookie':cookie,'X-MCEL-Heartbeat-CSRF':payload.get('csrf','')}
    check('heartbeat-unauthorized-control-denied',get(BASE+3,'/api/heartbeat/control',method='POST',body={'action':'status'})[0]==403)
    check('heartbeat-valid-control-works',get(BASE+3,'/api/heartbeat/control',method='POST',body={'action':'status'},headers=mutation)[0]==200)
    status,_,body=get(BASE+4,'/v1/log/recent?limit=1')
    check('mainlog-baseline-read-reproduced',status==200 and 'PRIVATE-LOG-SENTINEL' in str(body))
    check('mainlog-anonymous-read-denied',get(BASE+5,'/v1/log/recent?limit=1')[0]==403)
    check('mainlog-anonymous-event-injection-denied',get(BASE+5,'/v1/log/events',method='POST',body={'event':'attack'})[0]==403)
    check('mainlog-forged-service-key-denied',get(BASE+5,'/v1/log/recent',headers=bad_service)[0]==403)
    check('mainlog-authorized-read-works',get(BASE+5,'/v1/log/recent?limit=1',headers=mainlog_service)[0]==200)
    check('mainlog-authorized-event-ingest-works',get(BASE+5,'/v1/log/events',method='POST',body={'event':'valid'},headers=mainlog_service)[0]==200)
    check('mainlog-private-health-denied',get(BASE+5,'/health')[0]==403)
    check('mainlog-rejects-heartbeat-capability',get(BASE+5,'/v1/log/recent',headers=service)[0]==403)
    check('viewport-rejects-browser-capability',get(BASE+1,'/api/private',headers=browser_gateway)[0]==403)
    check('openclaw-baseline-anonymous-reproduced',get(BASE+6,'/v1/projects')[0]==200)
    check('openclaw-missing-config-fails-closed',get(BASE+7,'/v1/projects')[0]==401)
    check('openclaw-forged-token-denied',get(BASE+8,'/v1/projects',headers={'Authorization':'Bearer forged'})[0]==401)
    check('openclaw-configured-anonymous-denied',get(BASE+8,'/v1/projects')[0]==401)
    check('openclaw-authorized-list-works',get(BASE+8,'/v1/projects',headers={'Authorization':'Bearer '+capability_for('openclaw')})[0]==200)
    check('openclaw-authorized-chat-works',get(BASE+8,'/v1/chat',method='POST',body={'prompt':'fixture'},headers={'Authorization':'Bearer '+capability_for('openclaw')})[0]==200)
    def browser_headers(port,extra=None):
        return {'Origin':f'http://127.0.0.1:{port}','Sec-Fetch-Site':'same-origin',
                'X-MCEL-Browser-Operation':'browser-control',**(extra or {})}
    action={'operation':'close','session':'X'*24}
    check('browser-baseline-forged-origin-reproduced',get(BASE+9,'/api/applications/browser/control',method='POST',body=action,headers=browser_headers(BASE+9))[0]==200)
    check('browser-anonymous-forged-origin-denied',get(BASE+10,'/api/applications/browser/control',method='POST',body=action,headers=browser_headers(BASE+10))[0]==403)
    check('browser-forged-capability-denied',get(BASE+10,'/api/applications/browser/control',method='POST',body=action,headers=browser_headers(BASE+10,bad_gateway))[0]==403)
    check('browser-authorized-action-works',get(BASE+10,'/api/applications/browser/control',method='POST',body=action,headers=browser_headers(BASE+10,browser_gateway))[0]==200)
    check('browser-rejects-viewport-capability',get(BASE+10,'/api/applications/browser/control',method='POST',body=action,headers=browser_headers(BASE+10,gateway))[0]==403)
    check('browser-cross-origin-denied-even-with-capability',get(BASE+10,'/api/applications/browser/control',method='POST',body=action,headers={**browser_headers(BASE+10,browser_gateway),'Origin':'https://evil.example'})[0]==403)
    if probe_session(BASE, SECRET, get, LOGIN_KEY) != 0:
        failures.append('single-session-gateway-cross-component-acceptance')
    print('MC_CORRECTION_PROBE SUMMARY '+json.dumps({'status':'PASS' if not failures else 'FAIL',
          'tests':count,'failed':failures,'mode':'isolated-real-http-selected-handler-methods',
          'production_modified':False},sort_keys=True),flush=True)
    return 0 if not failures else 1


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['serve','probe'])
    args=parser.parse_args(argv)
    if args.action=='serve':return serve()
    return probe()

if __name__=='__main__':
    raise SystemExit(main())
