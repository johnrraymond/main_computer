"""Staged MCEL ONLYOFFICE cutover candidate verification.

`gateway`: real isolated TLS gateway and its signed backend admission.
`live`: isolated production ViewportHandler + Edge + real Docker Docs. No
production listener, profile, workbook, Compose project, or trust store is used.
`status`: source-bound receipts and remaining deployment gates.

These experiments do not run the complete installed MC supervisor or authorize
production promotion. They refuse to claim that those gates are verified.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from types import SimpleNamespace
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'runtime' / 'reports' / 'mcel-onlyoffice-cutover'
DEV = 'dev.main-computer.localhost'
DEBUG = 'debug.main-computer.localhost'
DOCS = 'onlyoffice.main-computer.localhost'
API = '/api/applications/onlyoffice/'
CANDIDATE_ENV = {'MAIN_COMPUTER_HARDENED_CANDIDATE': '1',
                 'MAIN_COMPUTER_PRIMARY_OWNER': 'dev',
                 'MAIN_COMPUTER_INSTALLATION_MODE': 'dev'}
FILES = (
    'main_computer/auth_bootstrap.py', 'main_computer/auth_bootstrap_service.py', 'main_computer/auth_hostnames.py',
    'main_computer/hardened_ingress.py', 'main_computer/viewport_server.py',
    'main_computer/viewport_route_dispatch.py', 'main_computer/viewport_http.py',
    'main_computer/viewport_routes_onlyoffice.py', 'main_computer/onlyoffice_secure_runtime.py',
    'main_computer/onlyoffice_durable_journal.py', 'main_computer/onlyoffice_revision_fence.py',
    'main_computer/web/auth_login.js', 'main_computer/web/auth_login.html',
    'main_computer/web/applications/scripts/onlyoffice-app.js',
    'main_computer/mcel_onlyoffice_application.py', 'mcel_apps/onlyoffice/application.js',
    'mcel_apps/onlyoffice/application.html', 'mcel_apps/onlyoffice/mcel.app.json',
    'main_computer/web/applications/scripts/mcel-onlyoffice-port.js',
    'main_computer/web/applications/scripts/mcel-onlyoffice-workspace.js',
    'main_computer/web/applications/styles/mcel-onlyoffice-workspace.css',
    'main_computer/mcel_applications_registry.py',
    'tools/fixtures/mcel_semantic_applications_shell/registry.json',
    'deploy/onlyoffice-hardened-nginx.conf', 'deploy/onlyoffice-editor-host.html',
    'deploy/onlyoffice-editor-host.js', 'docker-compose.onlyoffice.hardened.yml',
    'tools/mcel_onlyoffice_cutover.py', 'tools/mcel_onlyoffice_real_editor_lab.py',
)


def fingerprint():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}


def check(name, result):
    print(f'MC_OO_CUTOVER {name} {"PASS" if result else "FAIL"}', flush=True)
    if not result:
        raise AssertionError(name)


def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@contextmanager
def running(server):
    server.daemon_threads = False
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    try:
        yield server
    finally:
        server.shutdown()
        runner.join(15)
        server.server_close()


def request(host, number, cert, path, *, method='GET', body=None, cookie=None, presented_host=None):
    class TLS(http.client.HTTPSConnection):
        def connect(self):
            raw = socket.create_connection(('127.0.0.1', number), timeout=self.timeout)
            try:
                self.sock = self._context.wrap_socket(raw, server_hostname=host)
            except BaseException:
                raw.close()
                raise
    conn = TLS(host, number, context=ssl.create_default_context(cafile=str(cert)), timeout=90)
    headers = {'Host': f'{presented_host or host}:{number}'}
    if cookie:
        headers['Cookie'] = cookie
    if body is not None:
        headers.update({'Origin': f'https://{host}:{number}', 'Content-Type': 'application/json'})
    try:
        conn.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=headers)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def auth_cookie(server, proof, cert, host=DEV):
    from main_computer.auth_bootstrap import BootstrapAuthority
    number = server.server_port
    status, _, raw = request(host, number, cert, '/api/auth/bootstrap/challenge')
    check('real-auth-challenge', status == 200)
    challenge = json.loads(raw)
    key = base64.urlsafe_b64decode(json.loads(proof.read_text())['key_b64'] + '=')
    message = BootstrapAuthority.message(challenge['challenge_id'], challenge['nonce'])
    signature = base64.urlsafe_b64encode(hmac.new(key, message, hashlib.sha256).digest()).decode().rstrip('=')
    status, headers, _ = request(host, number, cert, '/api/auth/bootstrap/verify', method='POST',
        body={'challenge_id': challenge['challenge_id'], 'signature': signature})
    check('real-auth-session-issued', status == 200 and 'Set-Cookie' in headers)
    return headers['Set-Cookie'].split(';')[0]


class ProbeHandler(BaseHTTPRequestHandler):
    """Harmless echo only. Actual signed ingress code is not mocked."""
    def log_message(self, *_): pass
    def _send_json(self, payload, status=200):
        raw = json.dumps(payload).encode()
        self.send_response(int(status))
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_GET(self):
        from main_computer.hardened_ingress import admit
        if admit(self, 'GET'):
            self._send_json({'lane': self.server.lane})
    def do_POST(self):
        from main_computer.hardened_ingress import admit
        if admit(self, 'POST'):
            raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            self._send_json({'lane': self.server.lane, 'body': json.loads(raw)})


def gateway(work):
    from main_computer.auth_bootstrap import BootstrapAuthority, BootstrapServer, generate_tls_certificate, provision
    from main_computer.hardened_ingress import AssertionVerifier
    state = work / 'auth'
    proof = provision(state)
    cert, key, _ = generate_tls_certificate(state)
    authority = BootstrapAuthority(state)
    with running(ThreadingHTTPServer(('127.0.0.1', 0), ProbeHandler)) as dev, \
         running(ThreadingHTTPServer(('127.0.0.1', 0), ProbeHandler)) as debug:
        for lane, server in [('dev', dev), ('debug', debug)]:
            server.lane = lane
            server.mc_gateway_verifier = AssertionVerifier(authority)
        with running(BootstrapServer(('127.0.0.1', 0), authority, cert=cert, key=key,
                     isolated_backend_ports={'dev': dev.server_port, 'debug': debug.server_port})) as https:
            number = https.server_port
            path = '/api/cutover-probe'
            check('anonymous-gateway-denied', request(DEV, number, cert, path)[0] == 401)
            conn = http.client.HTTPConnection('127.0.0.1', dev.server_port, timeout=10)
            try:
                conn.request('POST', path, body=b'{}', headers={'Content-Type': 'application/json'})
                check('direct-protected-backend-denied', conn.getresponse().status == 401)
            finally:
                conn.close()
            cookie = auth_cookie(https, proof, cert)
            result = request(DEV, number, cert, path, cookie=cookie)
            check('authenticated-real-gateway-get', result[0] == 200 and json.loads(result[2])['lane'] == 'dev')
            result = request(DEV, number, cert, path, method='POST', body={'nonce': 'real-body'}, cookie=cookie)
            check('authenticated-real-gateway-post', result[0] == 200 and json.loads(result[2]) == {'lane':'dev','body':{'nonce':'real-body'}})
            result = request(DEV, number, cert, '/auth/handoff/start?target='+DEBUG, cookie=cookie)
            check('cross-host-handoff-issued', result[0] == 303)
            redeem = urlsplit(result[1]['Location'])
            result = request(DEBUG, number, cert, redeem.path+'?'+redeem.query)
            check('cross-host-handoff-redeemed', result[0] == 303)
            second = result[1]['Set-Cookie'].split(';')[0]
            check('same-session-different-host', second == cookie)
            result = request(DEBUG, number, cert, path, cookie=second)
            check('debug-routes-to-distinct-backend', result[0] == 200 and json.loads(result[2])['lane'] == 'debug')
            check('handoff-replay-rejected', request(DEBUG, number, cert, redeem.path+'?'+redeem.query)[0] == 401)
            check('docs-origin-not-app-origin', request(DOCS, number, cert, path, cookie=cookie)[0] == 403)
            check('unknown-localhost-host-denied', request(DEV, number, cert, path, cookie=cookie, presented_host='unknown.main-computer.localhost')[0] == 403)
            check('retired-local-host-denied', request(DEV, number, cert, path, cookie=cookie, presented_host='dev.main-computer.local')[0] == 403)
            check('inactive-safe-backend-no-fallback', request('safe.main-computer.localhost', number, cert, path, cookie=cookie)[0] == 503)
            authority.sessions.revoke(cookie.split('=',1)[1])
            check('revocation-blocks-backend', request(DEBUG, number, cert, path, cookie=second)[0] == 401)


def backend_child(config_path):
    """Actual ViewportHandler/dispatch in a restricted disposable HTTP listener.

    No ViewportServer constructor: it starts unrelated production services.
    Every document operation below reaches the real production dispatcher.
    """
    from main_computer.auth_bootstrap import BootstrapAuthority
    from main_computer.hardened_ingress import AssertionVerifier, admit
    print("MC_OO_LIVE_BACKEND import-actual-viewport START",flush=True)
    from main_computer.viewport_server import ViewportHandler
    print("MC_OO_LIVE_BACKEND import-actual-viewport PASS",flush=True)
    config_path = Path(config_path).resolve(strict=True)
    work = config_path.parent
    if (config_path.name != 'backend.json' or not work.name.startswith('mc-oo-cutover-')
            or work.parent != Path(tempfile.gettempdir()).resolve()):
        raise RuntimeError('DISPOSABLE_BACKEND_CONFIG_REQUIRED')
    data = json.loads(config_path.read_text())
    if (Path(data['root']).resolve() != work or Path(data['state']).resolve() != work/'auth'
            or Path(data['config']['onlyoffice_storage_root']).resolve() != work/'workbooks'
            or data['backend_port'] in {8765,8766,8767,8875,18085,18086,28865,28866,38865,38866}):
        raise RuntimeError('REFUSE_PRODUCTION_BACKEND_STATE')
    class Handler(ViewportHandler):
        def do_GET(self):
            route = urlsplit(self.path).path
            if route == '/health':
                self._send_json({'probe': data['probe']})
            elif route in (API+'file',):
                super().do_GET()
            elif route == '/cutover-probe':
                if admit(self, 'GET'):
                    self._send_text('<!doctype html><html><head><meta charset="utf-8"></head><body><div id="editor-host" style="width:100vw;height:95vh"></div></body></html>', 'text/html; charset=utf-8')
            elif route == '/cutover-probe-script':
                if admit(self, 'GET'):
                    self._send_text((ROOT/'main_computer/web/applications/scripts/mcel-onlyoffice-port.js').read_text(encoding='utf-8'), 'application/javascript; charset=utf-8')
            else:
                if admit(self, 'GET'):
                    self._send_json({'error':'NOT_A_LAB_ROUTE'}, 404)
        def do_POST(self):
            if urlsplit(self.path).path in {API+'config', API+'force-save', API+'callback', API+'files', API+'status'}:
                super().do_POST()
            else:
                if admit(self, 'POST'):
                    self._send_json({'error':'NOT_A_LAB_ROUTE'}, 404)
    server = ThreadingHTTPServer(('127.0.0.1', data['backend_port']), Handler)
    server.debug_root = Path(data['root'])
    server.config = SimpleNamespace(**data['config'])
    # No request URLs or capability tokens in logs.
    def signal(event, **fields):
        if event in ('api-onlyoffice-callback', 'api-onlyoffice-error'):
            safe = {'event':event, 'status':fields.get('status'), 'saved':fields.get('saved')}
            if 'error' in fields:
                safe['error_class'] = type(fields['error']).__name__
            print('MC_OO_LIVE_BACKEND '+json.dumps(safe), flush=True)
    server.signal = signal
    server.mc_gateway_verifier = AssertionVerifier(BootstrapAuthority(Path(data['state'])))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def wait_until(predicate, seconds, name):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            check(name, True)
            return
        time.sleep(1)
    check(name, False)


def local_health(number, marker):
    conn = http.client.HTTPConnection('127.0.0.1', number, timeout=2)
    try:
        conn.request('GET','/health')
        response=conn.getresponse()
        return response.status == 200 and json.loads(response.read()).get('probe') == marker
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        conn.close()


def docker(args, timeout=300):
    # Never print resolved compose configuration: it contains the JWT secret.
    subprocess.run(['docker', *args], cwd=ROOT, check=True, timeout=timeout)


def live(work, report, tag):
    if os.name != 'nt':
        raise RuntimeError('WINDOWS_EDGE_DOCKER_REQUIRED')
    from main_computer.auth_bootstrap import BootstrapAuthority, BootstrapServer, generate_tls_certificate, provision, _write_new_private, _private_folder
    from main_computer.onlyoffice_secure_runtime import _key
    from main_computer.onlyoffice_durable_journal import DurableSaveJournal
    from tools.mcel_onlyoffice_real_editor_lab import create_workbook, get_cell
    from playwright.sync_api import sync_playwright
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    # Test-only private authority and workbook root; never use real MC state.
    work = _private_folder(work)
    state = work/'auth'; proof = provision(state)
    cert, key, _ = generate_tls_certificate(state)
    storage = work/'workbooks'; storage.mkdir()
    book = storage/'Probe.xlsx'; create_workbook(book, 'MC_ORIGINAL')
    backend_port, ds_port, docs_port = port(), port(), port()
    if len({backend_port,ds_port,docs_port}) != 3:
        raise RuntimeError('EPHEMERAL_PORT_COLLISION')
    os.environ['MAIN_COMPUTER_ONLYOFFICE_HTTPS_PORT'] = str(docs_port)
    secret = secrets.token_hex(32)
    marker = secrets.token_hex(16)
    project = 'mc-oo-cutover-'+secrets.token_hex(6)
    docs_origin = f'https://{DOCS}:{docs_port}'
    config = {'onlyoffice_enabled':True, 'onlyoffice_mode':'docker',
        'onlyoffice_storage_root':str(storage), 'onlyoffice_jwt_enabled':True, 'onlyoffice_jwt_secret':secret,
        'onlyoffice_public_url':docs_origin, 'onlyoffice_browser_public_url':docs_origin,
        'onlyoffice_internal_url':f'http://127.0.0.1:{ds_port}',
        'onlyoffice_callback_base_url':f'http://host.docker.internal:{backend_port}'}
    config_file=work/'backend.json'
    _write_new_private(config_file,json.dumps({'root':str(work),'state':str(state),'backend_port':backend_port,'probe':marker,'config':config}).encode())
    mounts = [(ROOT/'deploy/onlyoffice-hardened-nginx.conf','/etc/nginx/conf.d/default.conf'),
              (ROOT/'deploy/onlyoffice-editor-host.html','/etc/nginx/mc-editor/host.html'),
              (ROOT/'deploy/onlyoffice-editor-host.js','/etc/nginx/mc-editor/host.js'),
              (cert,'/etc/nginx/tls/cert.pem'),(key,'/etc/nginx/tls/key.pem')]
    manifest = {'services':{
      'onlyoffice':{'image':'onlyoffice/documentserver:'+tag,'restart':'no','shm_size':'512m',
         'ports':[f'127.0.0.1:{ds_port}:80'], 'environment':{'JWT_ENABLED':'true','JWT_SECRET':secret,'ALLOW_PRIVATE_IP_ADDRESS':'true','ALLOW_META_IP_ADDRESS':'false'}},
      'docs-https':{'image':'nginx:1.27-alpine','restart':'no','depends_on':['onlyoffice'],
         'ports':[f'127.0.0.1:{docs_port}:443'],'volumes':[{'type':'bind','source':str(src.resolve()),'target':dest,'read_only':True} for src,dest in mounts]}
    }}
    compose_file=work/'compose.json'; _write_new_private(compose_file,json.dumps(manifest).encode())
    compose=['compose','--project-name',project,'-f',str(compose_file)]
    backend=None; stream=None; attempt_started=False
    def start_backend():
        nonlocal backend,stream
        stream=(report/'backend.log').open('a',encoding='utf8')
        backend=subprocess.Popen([sys.executable,'-m','tools.mcel_onlyoffice_cutover','_backend','--config',str(config_file)],cwd=ROOT,stdout=stream,stderr=stream)
        def backend_ready():
            if backend.poll() is not None:
                raise AssertionError('actual-viewport-process-exited-see-backend-log')
            return local_health(backend_port,marker)
        wait_until(backend_ready,180,'actual-viewport-handler-ready')
    def stop_backend():
        nonlocal backend,stream
        if backend is not None and backend.poll() is None:
            backend.terminate()
            try: backend.wait(timeout=20)
            except subprocess.TimeoutExpired:
                backend.kill();backend.wait(timeout=20)
        backend=None
        if stream: stream.close();stream=None
    def doc_ready():
        try: return request(DOCS,docs_port,cert,'/healthcheck')[0]==200
        except (OSError, ValueError, http.client.HTTPException): return False
    def cell_is(value):
        try: return get_cell(book)==value
        except (OSError, ValueError, KeyError, zipfile.BadZipFile): return False
    journal=DurableSaveJournal(storage,_key(secret.encode(),b'journal'))
    try:
        start_backend()
        attempt_started=True
        docker([*compose,'up','-d'],timeout=600)
        wait_until(doc_ready,300,'real-docker-documentserver-https-ready')
        authority=BootstrapAuthority(state)
        with running(BootstrapServer(('127.0.0.1',0),authority,cert=cert,key=key,isolated_backend_ports={'dev':backend_port})) as https:
            base=f'https://{DEV}:{https.server_port}'
            direct=http.client.HTTPConnection('127.0.0.1',backend_port,timeout=15)
            try:
                direct.request('POST',API+'config',body=b'{"path":"Probe.xlsx"}',headers={'Content-Type':'application/json'})
                check('actual-config-direct-anonymous-denied',direct.getresponse().status==401)
            finally: direct.close()
            direct=http.client.HTTPConnection('127.0.0.1',backend_port,timeout=15)
            try:
                direct.request('GET',API+'file?path=Probe.xlsx')
                check('actual-download-direct-anonymous-denied',direct.getresponse().status==403)
            finally: direct.close()
            direct=http.client.HTTPConnection('127.0.0.1',backend_port,timeout=15)
            try:
                direct.request('POST',API+'callback?path=Probe.xlsx',body=b'{"status":6}',headers={'Content-Type':'application/json'})
                check('actual-anonymous-callback-denied',direct.getresponse().status==403 and cell_is('MC_ORIGINAL'))
            finally: direct.close()
            leaf=x509.load_pem_x509_certificate(cert.read_bytes())
            spki=leaf.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
            pin=base64.b64encode(hashlib.sha256(spki).digest()).decode()
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='msedge',headless=True,args=['--ignore-certificate-errors-spki-list='+pin,'--no-proxy-server'])
                try:
                    context=browser.new_context(ignore_https_errors=False,accept_downloads=False)
                    first=context.new_page()
                    first.goto(base+'/auth/login?next=/cutover-probe',wait_until='domcontentloaded',timeout=120000)
                    first.locator('#auth-proof-file-input').set_input_files(str(proof))
                    first.wait_for_url(base+'/cutover-probe',timeout=180000)
                    check('actual-edge-proof-login',True)
                    health=first.evaluate("""async () => {const r=await fetch('/api/applications/onlyoffice/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({probe:true,timeout_s:10})});return {status:r.status,body:await r.json()};}""")
                    check('actual-backend-health-uses-internal-transport',health['status']==200 and health['body'].get('api_reachable') is True and health['body'].get('server_probe',{}).get('url')==config['onlyoffice_internal_url']+'/web-apps/apps/api/documents/api.js' and health['body'].get('public_url')==docs_origin)

                    def open_editor(page):
                        page.goto(base+'/cutover-probe',wait_until='domcontentloaded',timeout=120000)
                        page.add_script_tag(url=base+'/cutover-probe-script')
                        # Real config + real production mount function. No DocsAPI script on MC origin.
                        data=page.evaluate('''async () => { const response=await fetch('/api/applications/onlyoffice/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:'Probe.xlsx'})}); const data=await response.json(); if(!response.ok||!data.hardened_editor_origin)throw Error('hardened mode missing'); window.probeEditor=McelOnlyOfficePort.mountEditor(document.getElementById('editor-host'),data); await window.probeEditor.ready; return {key:data.config.document.key,callback:data.config.editorConfig.callbackUrl,download:data.config.document.url}; }''')
                        check('actual-editor-config-and-isolated-host-ready',bool(data['key']))
                        check('docs-js-not-executed-on-mc-origin',page.evaluate('typeof window.DocsAPI === "undefined"'))
                        return data
                    def edit(page,value):
                        def frames_ready(): return any('spreadsheeteditor' in f.url for f in page.frames)
                        wait_until(frames_ready,90,'real-spreadsheet-frame')
                        frame=[f for f in page.frames if 'spreadsheeteditor' in f.url][-1]
                        canvas=None
                        for sel in ('#editor_sdk','.sdk-canvas','canvas'):
                            loc=frame.locator(sel).first
                            try:
                                if loc.is_visible(timeout=3000): canvas=loc; break
                            except Exception: pass
                        check('real-editor-canvas',canvas is not None)
                        bounds=canvas.bounding_box();check('real-editor-geometry',bounds and bounds['width']>60 and bounds['height']>60)
                        canvas.click(position={'x':min(140,max(10,bounds['width']//3)),'y':min(120,max(10,bounds['height']//3))},timeout=20000)
                        for command in ('Control+Home','F2','Control+A'): page.keyboard.press(command)
                        page.keyboard.type(value,delay=25);page.keyboard.press('Enter');page.wait_for_timeout(3000)
                    def save(page,value):
                        result=page.evaluate('''async () => {const response=await fetch('/api/applications/onlyoffice/force-save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:'Probe.xlsx'})});if(!response.ok)throw Error('force-save refused');return await response.json();}''')
                        check('actual-commandservice-accepted',result.get('command_response',{}).get('error')==0)
                        wait_until(lambda:cell_is(value),180,'genuine-signed-callback-persisted')
                    launch=open_editor(first)
                    callback=urlsplit(launch['callback'])
                    direct=http.client.HTTPConnection('127.0.0.1',backend_port,timeout=15)
                    try:
                        direct.request('POST',callback.path+'?'+callback.query,body=b'{"status":6}',headers={'Content-Type':'application/json'})
                        check('document-capability-without-jwt-denied',direct.getresponse().status==403 and cell_is('MC_ORIGINAL'))
                    finally: direct.close()
                    edit(first,'MC_FIRST'); save(first,'MC_FIRST')
                    old_pid=backend.pid;stop_backend();start_backend()
                    check('actual-handler-process-restarted',backend.pid!=old_pid)
                    second=context.new_page();second_launch=open_editor(second)
                    check('second-editor-shares-live-key',second_launch['key']==launch['key'])
                    edit(second,'MC_SECOND');save(second,'MC_SECOND')
                    second.close();edit(first,'MC_FIRST_AGAIN');save(first,'MC_FIRST_AGAIN')
                    edit(first,'MC_FINAL_UNSAVED');first.close()
                    wait_until(lambda:cell_is('MC_FINAL_UNSAVED') and journal.current(book,mode='dev')['final_closed'],240,'real-last-editor-final-close-persisted')
                    third=context.new_page();new_launch=open_editor(third)
                    check('reopen-rotates-key',new_launch['key']!=launch['key'] and cell_is('MC_FINAL_UNSAVED'))
                    old_url=urlsplit(launch['download']);conn=http.client.HTTPConnection('127.0.0.1',backend_port,timeout=15)
                    try:
                        conn.request('GET',old_url.path+'?'+old_url.query)
                        check('retired-document-capability-denied',conn.getresponse().status==403)
                    finally: conn.close()
                    check('docs-origin-no-mc-cookie',not any(c['name']=='__Host-MC-Session' for c in context.cookies([docs_origin])))
                    audit=subprocess.run(['docker',*compose,'exec','-T','docs-https','cat','/var/log/nginx/mc-docs-cookie-audit.log'],cwd=ROOT,capture_output=True,text=True,timeout=30,check=True)
                    bits=audit.stdout.splitlines()
                    check('actual-docs-ingress-cookie-audit',len(bits)>0 and all(bit.strip()=='0' for bit in bits))
                    third.close()
                finally: browser.close()
    finally:
        stop_backend()
        if attempt_started:
            docker([*compose,'down','--volumes','--remove-orphans'],timeout=180)


def status():
    current=fingerprint()
    print('MC_OO_CUTOVER status: production unchanged; promotion not implemented by this command')
    for action in ('gateway','live'):
        candidates=sorted(REPORTS.glob('*/summary.json'),reverse=True)
        records=[]
        for file in candidates:
            try: row=json.loads(file.read_text())
            except (OSError,ValueError): continue
            if row.get('action')==action:
                records.append((row,file));break
        passed=bool(records and records[0][0].get('verdict')=='PASS_ISOLATED_'+action.upper() and records[0][0].get('fingerprint')==current)
        print(f'MC_OO_CUTOVER {action} {"PASS_SOURCE_MATCHED" if passed else "PENDING_OR_STALE"}')
    print('MC_OO_CUTOVER normal-windows-supervisor-startup UNVERIFIED')
    print('MC_OO_CUTOVER real-multi-installation-and-currentuser-trust UNVERIFIED')
    print('MC_OO_CUTOVER production-promotion-and-rollback UNIMPLEMENTED')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('gateway','live','status','_backend'))
    parser.add_argument('--tag',default='latest')
    parser.add_argument('--config',type=Path)
    opts=parser.parse_args(argv)
    if opts.action=='status': status();return 0
    if opts.action=='_backend':
        if not opts.config or not all(os.environ.get(k)==v for k,v in CANDIDATE_ENV.items()): raise RuntimeError('isolated backend configuration required')
        backend_child(opts.config);return 0
    # auth_bootstrap captures candidate mode at import; use a fresh child to
    # avoid accidental incumbent configuration inherited from the shell.
    if not all(os.environ.get(k)==v for k,v in CANDIDATE_ENV.items()):
        return subprocess.call([sys.executable,'-m','tools.mcel_onlyoffice_cutover',opts.action,'--tag',opts.tag],cwd=ROOT,env={**os.environ,**CANDIDATE_ENV})
    report=REPORTS/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(3))
    report.mkdir(parents=True,exist_ok=False)
    before=fingerprint();verdict='BLOCKED';error=None
    try:
        with tempfile.TemporaryDirectory(prefix='mc-oo-cutover-') as folder:
            if opts.action=='gateway': gateway(Path(folder))
            else: live(Path(folder),report,opts.tag)
        if fingerprint()!=before:raise RuntimeError('SOURCE_CHANGED_DURING_RUN')
        verdict='PASS_ISOLATED_'+opts.action.upper()
    except KeyboardInterrupt:
        error='INTERRUPTED'
    except Exception as exc:
        # Error messages from HTTP or Playwright may contain signed URL query
        # strings. Print only the exception class and safe named assertions.
        error=str(exc) if isinstance(exc,AssertionError) and str(exc).replace('-','').isalnum() else type(exc).__name__
        print('MC_OO_CUTOVER ERROR '+error,flush=True)
        # Keep source location and exception class without leaking signed URLs,
        # request bodies, session tokens, or credentials from exception text.
        stack=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(exc.__traceback__)]
        (report/'failure-location.json').write_text(json.dumps({'error':error,'stack':stack},indent=2)+'\n',encoding='utf8')
    result={'action':opts.action,'verdict':verdict,'error':error,'fingerprint':before,
            'production_cutover_performed':False,'production_routes_promoted':False,
            'full_windows_supervisor_tested':False,'real_windows_multi_lane_tested':False,
            'windows_currentuser_trust_tested':False,'report_dir':str(report)}
    (report/'summary.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
    print('MC_OO_CUTOVER SUMMARY '+json.dumps({k:v for k,v in result.items() if k!='fingerprint'}),flush=True)
    return 0 if verdict.startswith('PASS') else 1


if __name__=='__main__': raise SystemExit(main())
