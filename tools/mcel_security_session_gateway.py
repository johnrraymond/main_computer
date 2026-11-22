"""Disposable single-session MC ingress fixture.

Uses the *existing* mcel_localhost_cookie_gateway_smoke.Sessions implementation:
one uploaded fixture key -> one opaque revocable cookie -> validated gateway ->
purpose-scoped internal authority at each selected source-backed handler. Neither
Bob nor the browser is given a backend service key. No production auth wiring.

This lab's gateway uses HTTP inside Docker. Browser TLS, Windows ACLs and real
user separation remain independently gated by the Windows cookie acceptance.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import http.client
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import secrets
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

try:
    from mcel_localhost_cookie_gateway_smoke import COOKIE_NAME, Sessions, COOKIE_AGE
except ImportError:
    from tools.mcel_localhost_cookie_gateway_smoke import COOKIE_NAME, Sessions, COOKIE_AGE

MAX_REQUEST = 65536
HOSTNAME = 'mc.test'
ORIGIN = 'https://mc.test:8765'


def scoped_capability(secret: str, service: str) -> str:
    family = service.removesuffix('-configured')
    return hmac.new(secret.encode('ascii'), ('mc-security-'+family).encode('ascii'), hashlib.sha256).hexdigest()


def callback_capability(secret: str, document: str) -> str:
    return hmac.new(secret.encode('ascii'), ('mc-document-callback:'+document).encode(), hashlib.sha256).hexdigest()


# The only supported routing destinations are declared here. No client-provided
# port, hostname, URL or route rewriting reaches the downstream HTTP client.
ROUTES = {
    ('GET', '/mc/viewport/private'): (1, '/api/private', 'viewport'),
    ('POST', '/mc/viewport/control'): (1, '/api/control', 'viewport'),
    ('GET', '/mc/viewport/identities'): (1, '/api/local/authenticator/identities', 'viewport'),
    ('GET', '/mc/viewport/file'): (1, '/api/applications/onlyoffice/file?path=fixture.xlsx', 'viewport'),
    ('GET', '/mc/heartbeat/status'): (3, '/api/heartbeat/status', 'heartbeat'),
    ('GET', '/mc/heartbeat/session'): (3, '/api/heartbeat/authority/session', 'heartbeat'),
    ('POST', '/mc/heartbeat/control'): (3, '/api/heartbeat/control', 'heartbeat'),
    ('GET', '/mc/mainlog/recent'): (5, '/v1/log/recent?limit=1', 'mainlog'),
    ('POST', '/mc/mainlog/events'): (5, '/v1/log/events', 'mainlog'),
    ('GET', '/mc/openclaw/projects'): (8, '/v1/projects', 'openclaw'),
    ('POST', '/mc/openclaw/chat'): (8, '/v1/chat', 'openclaw'),
    ('POST', '/mc/browser/control'): (10, '/api/applications/browser/control', 'browser'),
    ('POST', '/mc/onlyoffice/callback'): (12, '/api/applications/onlyoffice/callback?path=fixture.xlsx', 'onlyoffice'),
}


def make_gateway(base: int, secret: str, session_file: Path, login_key: str):
    sessions = Sessions(session_file)
    key = login_key.encode('ascii')  # distinct simulated Alice file key; never a service key

    class Gateway(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status: int, body: bytes = b'', extra: dict | None = None):
            self.send_response(status)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Type', 'application/json')
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if body: self.wfile.write(body)

        def _cookie(self):
            cookies = self.headers.get_all('Cookie', [])
            if len(cookies) != 1:
                return ''
            try:
                parsed = SimpleCookie()
                parsed.load(cookies[0])
                if COOKIE_NAME not in parsed or len([x for x in cookies[0].split(';') if x.strip().startswith(COOKIE_NAME+'=')]) != 1:
                    return ''
                return parsed[COOKIE_NAME].value
            except Exception:
                return ''

        def _body(self):
            raw_size = self.headers.get('Content-Length', '')
            if not raw_size.isascii() or not raw_size.isdecimal():
                raise ValueError('content-length required')
            size = int(raw_size)
            if size < 0 or size > MAX_REQUEST:
                raise ValueError('invalid length')
            return self.rfile.read(size)

        def _request(self):
            if self.headers.get('Host') != f'127.0.0.1:{base+11}':
                return self.respond(403)
            # Reject hostile web origins even if the local browser has a cookie.
            origin = self.headers.get('Origin')
            if origin is not None and origin != ORIGIN:
                return self.respond(403)
            method = self.command
            route = urlsplit(self.path)
            if route.query or route.fragment:
                return self.respond(404)
            if method == 'POST' and route.path == '/auth/login':
                try: body = self._body()
                except ValueError: return self.respond(400)
                if not hmac.compare_digest(body, key):
                    return self.respond(401)
                token = sessions.issue()
                return self.respond(200, b'{"ok":true}', {
                    'Set-Cookie': f'{COOKIE_NAME}={token}; Max-Age={COOKIE_AGE}; Path=/; Secure; HttpOnly; SameSite=Strict',
                })
            token = self._cookie()
            if not sessions.valid(token):
                return self.respond(401)
            if method == 'POST' and route.path == '/_fixture/expire':
                # Disposable lab only: simulate server-side expiration without
                # manipulating system clocks, browser settings or production.
                with sessions.lock:
                    sessions.data[hashlib.sha256(token.encode()).hexdigest()] = int(time.time()) - 10
                    sessions._save()
                return self.respond(200)
            if method == 'POST' and route.path == '/auth/logout':
                sessions.revoke(token)
                return self.respond(200, b'{"ok":true}', {
                    'Set-Cookie': f'{COOKIE_NAME}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict',
                })
            if method == 'GET' and route.path == '/auth/check':
                return self.respond(200, b'{"ok":true}')
            if method == 'GET' and route.path == '/auth/check-persisted':
                return self.respond(200 if Sessions(session_file).valid(token) else 401)
            spec = ROUTES.get((method, route.path))
            if spec is None:
                return self.respond(404)
            offset, downstream, service = spec
            if method == 'POST':
                try: body = self._body()
                except ValueError: return self.respond(400)
            else:
                body = None
            # All caller-supplied authorization, proxy and identity headers
            # are discarded. Only authority derived by the gateway is sent.
            headers = {'Host': f'127.0.0.1:{base+offset}'}
            if body is not None: headers['Content-Type'] = 'application/json'
            if service in {'viewport', 'browser'}:
                headers['X-MC-Gateway-Capability'] = scoped_capability(secret, service)
            elif service in {'heartbeat', 'mainlog'}:
                headers['X-MC-Internal-Authority'] = scoped_capability(secret, service)
            elif service == 'openclaw':
                headers['Authorization'] = 'Bearer '+scoped_capability(secret, service)
            elif service == 'onlyoffice':
                headers['X-MC-Document-Capability'] = callback_capability(secret, 'fixture.xlsx')
            if service == 'browser':
                headers['Origin'] = f'http://127.0.0.1:{base+offset}'
                headers['Sec-Fetch-Site'] = 'same-origin'
                headers['X-MCEL-Browser-Operation'] = 'browser-control'
            if service == 'heartbeat' and method == 'POST':
                # The legacy Heartbeat CSRF cookie is *never* exposed to the
                # browser. Gateway obtains it through its authorized channel.
                s, h, payload = downstream_request(base+offset, 'GET', '/api/heartbeat/authority/session', None, headers)
                if s != 200: return self.respond(502)
                try: csrf = json.loads(payload)['csrf']
                except Exception: return self.respond(502)
                headers['Cookie'] = h.get('set-cookie','').split(';',1)[0]
                headers['X-MCEL-Heartbeat-CSRF'] = csrf
            status, _, payload = downstream_request(base+offset,method,downstream,body,headers)
            if service == 'heartbeat' and downstream == '/api/heartbeat/authority/session' and status == 200:
                # Do not expose a second Heartbeat CSRF credential to Alice's
                # browser. The gateway consumes it internally when necessary.
                payload = b'{"ok":true}'
            return self.respond(status,payload)

        def do_GET(self): self._request()
        def do_POST(self): self._request()

    Gateway._mc_sessions = sessions
    return Gateway


def downstream_request(port: int, method: str, path: str, body: bytes | None, headers: dict):
    conn = http.client.HTTPConnection('127.0.0.1',port,timeout=4)
    try:
        conn.request(method,path,body=body,headers=headers)
        response = conn.getresponse()
        return response.status, {k.lower(): v for k,v in response.getheaders()}, response.read(128*1024)
    finally: conn.close()


def make_onlyoffice_status_handler(secret: str, source: Path):
    """Real MC callback body, with isolated signed admission and no network I/O.

    This checks status-only callback delegation. Real workbook save, redirects,
    document downloads and native Document Server callbacks stay in the separate
    ONLYOFFICE Docker lab and must not be inferred from this case.
    """
    tree = ast.parse(source.read_text(encoding='utf-8'), filename=str(source))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ViewportOnlyOfficeRoutesMixin')
    callback = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_handle_onlyoffice_callback')
    block = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='SourceCallback',bases=[],keywords=[],body=[callback],decorator_list=[])],type_ignores=[]))
    namespace={'parse_qs':parse_qs,'urlsplit':urlsplit,'Request':Request,'HTTPStatus':HTTPStatus}
    # For status-only tests, any attempt to fetch is an explicit test failure.
    namespace['urlopen'] = lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('unexpected callback URL fetch'))
    exec(compile(block, str(source),'exec'),namespace)
    source_handler = namespace['SourceCallback']

    class OnlyOffice(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            if urlsplit(self.path).path != '/api/applications/onlyoffice/callback':
                return self._reply(404)
            docs = parse_qs(urlsplit(self.path).query)
            if docs != {'path':['fixture.xlsx']}:
                return self._reply(403)
            supplied=self.headers.get('X-MC-Document-Capability','')
            if not hmac.compare_digest(supplied,callback_capability(secret,'fixture.xlsx')):
                return self._reply(403)
            try:
                length=int(self.headers.get('Content-Length','-1'))
                if not 0 < length < MAX_REQUEST: return self._reply(400)
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict) or body.get('status') != 1:
                    return self._reply(403)
            except (ValueError,TypeError):return self._reply(400)
            h=source_handler()
            h.path=self.path
            h.server=SimpleNamespace(signal=lambda *a,**k:None)
            h._onlyoffice_safe_path=lambda *a,**k: Path('/tmp/fixture.xlsx')
            h._read_json=lambda:body
            h._send_json=lambda payload,status=HTTPStatus.OK:self._reply(int(status),json.dumps(payload).encode())
            h._handle_onlyoffice_callback()
        def _reply(self,status:int, data:bytes=b''):
            self.send_response(status)
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            if data:self.wfile.write(data)
    return OnlyOffice


def probe_session(base:int, secret:str, request_fn, login_key:str):
    """Single issued cookie must authorize ALL selected MC operations."""
    failures=[]; checked=0
    def check(name, condition):
        nonlocal checked
        checked+=1
        print('MC_UNIFIED_SESSION '+name+' '+('PASS' if condition else 'FAIL'),flush=True)
        if not condition: failures.append(name)
    port=base+11
    # raw bytes use permanent-key upload, not a JSON password or bearer token
    def call(method,path,body=None,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
        hdr={'Host':f'127.0.0.1:{port}', **(headers or {})}
        try:
            conn.request(method,path,body=body,headers=hdr)
            r=conn.getresponse();return r.status,dict(r.getheaders()),r.read(128*1024)
        finally:conn.close()
    check('bob-forged-login-denied',call('POST','/auth/login',b'wrong-key')[0]==401)
    check('bob-anonymous-gateway-denied',call('GET','/mc/viewport/private')[0]==401)
    status,h,data=call('POST','/auth/login',login_key.encode('ascii'))
    token_cookie=h.get('Set-Cookie','').split(';',1)[0]
    check('alice-one-key-one-session-issued',status==200 and token_cookie.startswith(COOKIE_NAME+'='))
    check('alice-cookie-secure-persistent',all(flag in h.get('Set-Cookie','') for flag in ('Secure','HttpOnly','SameSite=Strict','Max-Age=')) and login_key not in token_cookie and secret not in token_cookie)
    authenticated={'Cookie':token_cookie}
    spoof={'Cookie':token_cookie,'X-MC-Gateway-Capability':'forged', 'X-MC-Internal-Authority':'forged', 'Authorization':'Bearer forged', 'X-Forwarded-For':'127.0.0.1'}
    attack_action=b'{"operation":"close","session":"XXXXXXXXXXXXXXXXXXXXXXXX"}'
    scenarios = (
        ('viewport-read','GET','/mc/viewport/private',None),
        ('viewport-write','POST','/mc/viewport/control',b'{}'),
        ('viewport-identities','GET','/mc/viewport/identities',None),
        ('viewport-onlyoffice-file','GET','/mc/viewport/file',None),
        ('heartbeat-read','GET','/mc/heartbeat/status',None),
        ('heartbeat-session-issued','GET','/mc/heartbeat/session',None),
        ('heartbeat-control','POST','/mc/heartbeat/control',b'{"action":"status"}'),
        ('mainlog-read','GET','/mc/mainlog/recent',None),
        ('mainlog-write','POST','/mc/mainlog/events',b'{"events":[{"message":"alice"}]}'),
        ('openclaw-projects','GET','/mc/openclaw/projects',None),
        ('openclaw-chat','POST','/mc/openclaw/chat',b'{"prompt":"fixture"}'),
        ('browser-control','POST','/mc/browser/control',attack_action),
        ('onlyoffice-delegated-status','POST','/mc/onlyoffice/callback',b'{"status":1}'),
    )
    for label,method,path,body in scenarios:
        check('alice-session-'+label,call(method,path,body,authenticated)[0]==200)
        check('bob-without-session-'+label,call(method,path,body)[0]==401)
    check('alice-client-forged-internal-headers-ignored',call('GET','/mc/mainlog/recent',None,spoof)[0]==200)
    check('bob-forged-session-denied',call('GET','/mc/mainlog/recent',None,{'Cookie':COOKIE_NAME+'=forged'})[0]==401)
    check('alice-duplicate-session-cookie-denied',call('GET','/mc/mainlog/recent',None,{'Cookie':token_cookie+'; '+token_cookie})[0]==401)
    check('alice-host-spoof-denied',call('GET','/mc/mainlog/recent',None,{'Cookie':token_cookie,'Host':'mc.test:8765'})[0]==403)
    check('alice-cross-origin-write-denied',call('POST','/mc/browser/control',attack_action,{'Cookie':token_cookie,'Origin':'https://evil.example'})[0]==403)
    # Direct backend must not accept Alice's gateway cookie as a backend key.
    check('direct-viewport-with-cookie-denied',request_fn(base+1,'/api/private',headers=authenticated)[0]==403)
    check('direct-heartbeat-with-cookie-denied',request_fn(base+3,'/api/heartbeat/status',headers=authenticated)[0]==403)
    check('direct-mainlog-with-cookie-denied',request_fn(base+5,'/v1/log/recent',headers=authenticated)[0]==403)
    check('direct-openclaw-with-cookie-denied',request_fn(base+8,'/v1/projects',headers=authenticated)[0]==401)
    check('direct-onlyoffice-with-cookie-denied',request_fn(base+12,'/api/applications/onlyoffice/callback?path=fixture.xlsx',method='POST',body={'status':1},headers=authenticated)[0]==403)
    check('alice-logout-works',call('POST','/auth/logout',b'',authenticated)[0]==200)
    check('revoked-session-denied-everywhere',all(call(method,path,body,authenticated)[0]==401 for _,method,path,body in scenarios))
    # Session validity is read from the *existing* Sessions persistence file;
    # create a fresh Sessions object to prove the disk record can be recovered.
    status,newh,_=call('POST','/auth/login',login_key.encode('ascii'))
    check('new-session-issued-after-revocation',status==200)
    new_token=newh.get('Set-Cookie','').split(';',1)[0]
    check('existing-session-store-restorable',call('GET','/auth/check-persisted',None,{'Cookie':new_token})[0]==200)
    check('bob-forged-cookie-denied-all-components',all(call(method,path,body,{'Cookie':COOKIE_NAME+'=forged'})[0]==401 for _,method,path,body in scenarios))
    check('fixture-session-expiration-accepted',call('POST','/_fixture/expire',b'',{'Cookie':new_token})[0]==200)
    check('expired-session-denied-all-components',all(call(method,path,body,{'Cookie':new_token})[0]==401 for _,method,path,body in scenarios))
    print('MC_UNIFIED_SESSION SUMMARY '+json.dumps({'status':'PASS' if not failures else 'FAIL', 'tests':checked,'failed':failures,'one_session_across_components':True,'onlyoffice_status_source_handler':True,'real_onlyoffice_save_not_tested_here':True,'tls_not_tested_here':True,'production_modified':False},sort_keys=True),flush=True)
    return 0 if not failures else 1

