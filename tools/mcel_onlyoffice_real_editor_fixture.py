"""Disposable source-backed MC session / real ONLYOFFICE Document Server bridge.

DO NOT promote this server. This fixture is intentionally mounted only in a
random Docker Compose laboratory; never import a user's real authentication
state, store credentials in a URL, or expose its test-only endpoints publicly.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from main_computer.auth_bootstrap import (BootstrapAuthority, PROOF_NAME,
    PROOF_TYPE, COOKIE, _b64)

DOC = 'real-edit.xlsx'
MODE = 'dev'
FOLDER = Path(os.environ.get('LAB_DOCUMENT_DIR', '/lab/documents'))
TARGET = FOLDER / DOC
AUTH_ROOT = Path(os.environ.get('LAB_AUTH_ROOT', '/tmp/mc-onlyoffice-real-auth'))
KEY_BYTES = base64.urlsafe_b64decode(os.environ['LAB_AUTH_KEY_B64'] + '===')
JWT_SECRET = os.environ['LAB_JWT_SECRET']
BROWSER_PORT = int(os.environ['LAB_BROWSER_PORT'])
PUBLIC_BASE = f'https://dev.main-computer.local:{BROWSER_PORT}'
DOCS_BASE = f'https://onlyoffice.main-computer.local:{BROWSER_PORT}'
REPORT = {'callbacks': [], 'downloads': 0, 'bad_callbacks': 0, 'saved': 0,
          'editor_configs': 0, 'session_issuance': 0, 'force_saves': 0, 'shadow_commits': 0,
          'continuous_commits': 0, 'continuous_denials': 0, 'continuous_seqs': [],
          'final_close_signed_status2': 0, 'final_close_rejected': 0,
          'final_close_has_userdata': 0, 'final_close_committed': 0}
REPORT_LOCK = threading.RLock()
ACTIVE_KEYS = {}  # document key -> server-issued base content SHA-256
SHADOW_REVISION_FENCE = os.environ.get('LAB_SHADOW_REVISION_FENCE') == '1'
CONTINUOUS_SAVE = os.environ.get('LAB_SHADOW_CONTINUOUS_SAVE') == '1'
DURABLE_SAVE = os.environ.get('LAB_DURABLE_SAVE_JOURNAL') == '1'
FINAL_CLOSE_PROBE = os.environ.get('LAB_FINAL_CLOSE_PROBE') == '1'
if FINAL_CLOSE_PROBE and not DURABLE_SAVE:
    raise RuntimeError('final-close probe requires durable journal')
SERVER_BOOT_ID = secrets.token_hex(16)
if DURABLE_SAVE and not CONTINUOUS_SAVE:
    raise RuntimeError('durable journal experiment requires continuous-saves')
if CONTINUOUS_SAVE and not SHADOW_REVISION_FENCE:
    raise RuntimeError('continuous-save test requires a revision fence')
# Lab-only server-issued sequence state. No production authorization state is used.
CONTINUOUS_STATE = {}
PENDING_SAVE_COMMANDS = {}
if SHADOW_REVISION_FENCE:
    from mcel_onlyoffice_shadow_revision import ShadowRevisionFence
    REVISION_FENCE = ShadowRevisionFence(FOLDER)
if DURABLE_SAVE:
    from mcel_onlyoffice_durable_save_journal import DurableSaveJournal, JournalConflict
    JOURNAL = DurableSaveJournal(FOLDER, KEY_BYTES)



def initialize_authority():
    if len(KEY_BYTES) != 32 or len(JWT_SECRET) < 32:
        raise RuntimeError('lab-only keys must be at least 32 bytes')
    AUTH_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    (AUTH_ROOT / 'authority.json').write_text(json.dumps({'version': 1, 'hmac_key': _b64(KEY_BYTES)}), encoding='utf8')
    (AUTH_ROOT / PROOF_NAME).write_text(json.dumps({'type': PROOF_TYPE, 'key_b64': _b64(KEY_BYTES)}), encoding='utf8')
    os.chmod(AUTH_ROOT, 0o700)
    for file in AUTH_ROOT.iterdir(): os.chmod(file, 0o600)
    return BootstrapAuthority(AUTH_ROOT)

AUTH = initialize_authority()


def b64json(obj):
    return base64.urlsafe_b64encode(json.dumps(obj, separators=(',', ':')).encode()).decode().rstrip('=')


def jwt_sign(payload):
    signed = b64json({'typ': 'JWT', 'alg': 'HS256'}) + '.' + b64json(payload)
    sig = hmac.new(JWT_SECRET.encode(), signed.encode(), hashlib.sha256).digest()
    return signed + '.' + base64.urlsafe_b64encode(sig).decode().rstrip('=')


def jwt_verify(token):
    if not token or len(token) > 32768 or token.count('.') != 2:
        return None
    try:
        header, payload, tag = token.split('.')
        signed = (header + '.' + payload).encode()
        actual = base64.urlsafe_b64decode(tag + '===')
        expected = hmac.new(JWT_SECRET.encode(), signed, hashlib.sha256).digest()
        if not hmac.compare_digest(actual, expected): return None
        meta = json.loads(base64.urlsafe_b64decode(header + '==='))
        data = json.loads(base64.urlsafe_b64decode(payload + '==='))
        if meta != {'typ':'JWT','alg':'HS256'} or not isinstance(data,dict): return None
        if data.get('exp') is not None and int(data['exp']) <= time.time(): return None
        return data
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def scoped_token(kind, mode, document, exp):
    body = f'mc-real-onlyoffice-v1|{kind}|{mode}|{document}|{exp}'
    tag = hmac.new(KEY_BYTES, body.encode(), hashlib.sha256).hexdigest()
    return f'{exp}.{tag}'


def valid_token(token, kind, mode, document):
    if not isinstance(token, str) or len(token) > 80: return False
    try:
        exp_s, tag = token.split('.')
        if not exp_s.isdigit() or len(exp_s) > 12 or len(tag) != 64: return False
        exp = int(exp_s)
        now = int(time.time())
        return now <= exp <= now + 3600 and hmac.compare_digest(token, scoped_token(kind, mode, document, exp))
    except (ValueError, TypeError): return False


def issue_save_userdata(key):
    """Mint an expiring, authenticated, one-save command identifier.

    The ONLYOFFICE CommandService echoes userdata in its status=6 callback.
    The real callback JWT additionally authenticates that echoed field.
    This protocol is a *disposable candidate*, not a production implementation.
    """
    if DURABLE_SAVE:
        return JOURNAL.issue(TARGET, mode=MODE, key=key)
    with REPORT_LOCK:
        state = CONTINUOUS_STATE.setdefault(key, {
            'next': 0, 'accepted': 0, 'sha': ACTIVE_KEYS[key]})
        state['next'] += 1
        meta = {'v': 1, 'mode': MODE, 'doc': DOC, 'key': key,
                'seq': state['next'], 'nonce': secrets.token_hex(16),
                'exp': int(time.time()) + 300}
        payload = b64json(meta)
        tag = hmac.new(KEY_BYTES, b'mc-oo-forcesave|' + payload.encode(),
                       hashlib.sha256).hexdigest()
        userdata = payload + '.' + tag
        PENDING_SAVE_COMMANDS[userdata] = (key, meta['seq'])
        return userdata


def save_sequence_fresh(key, seq):
    """Only a strictly newer server-issued force-save may commit."""
    cursor = CONTINUOUS_STATE.get(key)
    return bool(cursor is not None and type(seq) is int and seq > cursor['accepted'])


def parse_save_userdata(userdata, key):
    if not isinstance(userdata, str) or len(userdata) > 600 or userdata.count('.') != 1:
        return None
    try:
        payload, tag = userdata.split('.')
        expected = hmac.new(KEY_BYTES, b'mc-oo-forcesave|' + payload.encode(),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(tag, expected): return None
        data = json.loads(base64.urlsafe_b64decode(payload + '==='))
        if (not isinstance(data, dict) or
                set(data) != {'v','mode','doc','key','seq','nonce','exp'} or
                data['v'] != 1 or data['mode'] != MODE or data['doc'] != DOC or
                data['key'] != key or type(data['seq']) is not int or
                data['seq'] <= 0 or type(data['exp']) is not int or
                not int(time.time()) <= data['exp'] <= int(time.time()) + 300 or
                PENDING_SAVE_COMMANDS.get(userdata) != (key, data['seq'])):
            return None
        return data
    except (ValueError, TypeError, KeyError, UnicodeError):
        return None


def active_document_key(key):
    """Verify the issued document key against persistent journal on restart."""
    if not isinstance(key, str):
        return False
    if not DURABLE_SAVE:
        return key in ACTIVE_KEYS
    try:
        JOURNAL.inspect(TARGET, mode=MODE, key=key)
        return True
    except (JournalConflict, ValueError, OSError):
        return False


def onlyoffice_source_handler():
    """Execute the *actual* MC callback method, not an invented save handler."""
    source = Path(os.environ.get('LAB_SOURCE_PATH', '/lab/viewport_routes_onlyoffice.py'))
    tree = ast.parse(source.read_text(encoding='utf8'))
    cls = next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='ViewportOnlyOfficeRoutesMixin')
    keep = {'_handle_onlyoffice_callback','_onlyoffice_looks_like_xlsx','_onlyoffice_atomic_write'}
    body = [x for x in cls.body if isinstance(x,ast.FunctionDef) and x.name in keep]
    if {x.name for x in body} != keep: raise RuntimeError('original callback methods unavailable')
    out = ast.fix_missing_locations(ast.Module(body=[ast.ClassDef(name='RealMCHandler',bases=[],keywords=[],body=body,decorator_list=[])],type_ignores=[]))
    import tempfile
    scope = {'Request': Request, 'urlopen': safe_urlopen, 'parse_qs':__import__('urllib.parse',fromlist=['parse_qs']).parse_qs,
             'urlsplit':urlsplit, 'HTTPStatus':HTTPStatus,'zipfile':zipfile,'io':io,'os':os,'tempfile':tempfile}
    exec(compile(out,str(source),'exec'),scope)
    return scope['RealMCHandler']

class BlockRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url): return None

class LimitedResponse:
    def __init__(self, response): self.response=response
    def __enter__(self): return self
    def __exit__(self,*a): return self.response.__exit__(*a)
    def read(self):
        data=self.response.read(24*1024*1024+1)
        if len(data)>24*1024*1024: raise ValueError('ONLYOFFICE_RESPONSE_TOO_LARGE')
        return data


def safe_urlopen(request, timeout=10):
    """Allow ONLYOFFICE's private cache only; refuse redirects and host changes."""
    url=urlsplit(request.full_url)
    direct = (url.scheme == 'http' and url.hostname == 'onlyoffice'
              and url.port in (None, 80))
    public = (url.scheme == 'https' and url.hostname == 'onlyoffice.main-computer.local'
              and url.port == BROWSER_PORT)
    if (not (direct or public) or url.username or url.password or url.fragment or
            not url.path.startswith('/cache/files/') or '..' in url.path.split('/')):
        raise ValueError('ONLYOFFICE_CALLBACK_SOURCE_NOT_ALLOWLISTED')
    # Real Document Server versions can report either the private service URL
    # or the public forwarded hostname. Resolve both to the *fixed* isolated
    # Document Server, not to DNS/mDNS or a caller-selected internal address.
    pinned = Request(urlunsplit(('http', 'onlyoffice', url.path, url.query, '')),
                     headers={'User-Agent':'main-computer-onlyoffice'})
    return LimitedResponse(build_opener(BlockRedirect()).open(pinned,timeout=min(timeout,12)))

ActualHandler = onlyoffice_source_handler()

LOGIN_HTML = '''<!doctype html><html><head><meta charset="utf-8"><title>MC disposable login</title></head>
<body><h1>MC disposable login</h1><input id="proof" type="file" accept=".json"/><div id="login-status"></div>
<script>
const getBytes=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/').padEnd(Math.ceil(s.length/4)*4,'=')),c=>c.charCodeAt(0));
const toB64=a=>btoa(String.fromCharCode(...new Uint8Array(a))).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');
document.getElementById('proof').onchange=async()=>{try{
 const x=JSON.parse(await document.getElementById('proof').files[0].text()); if(x.type!=='mc-proof-v2')throw Error('wrong proof type');
 const r=await fetch('/auth/challenge');if(!r.ok)throw Error('challenge failed');const c=await r.json();
 const k=await crypto.subtle.importKey('raw',getBytes(x.key_b64),{name:'HMAC',hash:'SHA-256'},false,['sign']);
 const msg=new TextEncoder().encode('MC-AUTH-BOOTSTRAP-V1\\n'+c.challenge_id+'\\n'+c.nonce+'\\n');
 const signature=toB64(await crypto.subtle.sign('HMAC',k,msg));
 const v=await fetch('/auth/verify',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({challenge_id:c.challenge_id,signature})});
 if(!v.ok)throw Error('authentication rejected');location.replace('/graphical');
 }catch(e){document.getElementById('login-status').textContent='Login failed: '+e.message;}};
</script></body></html>'''

MC_HOME_HTML = '''<!doctype html><html><head><meta charset="utf-8"><title>MC ONLYOFFICE session lab</title>
<meta name="referrer" content="no-referrer"><style>html,body,iframe {width:100%;height:100%;margin:0;border:0}</style></head>
<body><iframe id="mc-docs" title="Isolated ONLYOFFICE editor" referrerpolicy="no-referrer"
 src="__DOCS_FRAME_URL__"></iframe></body></html>'''

EDITOR_HTML = '''<!doctype html><html><head><meta charset="utf-8"><title>MC ONLYOFFICE real edit</title>
<meta name="referrer" content="no-referrer"><style>html,body,#editor {width:100%;height:100%;margin:0;padding:0}</style></head>
<body><div id="editor"></div><script>
window.MC_EDITOR_EVENTS=[];window.MC_DOC_READY=false;
(async()=>{try{
 const ticket=new URL(location.href).searchParams.get('ticket');
 if(!ticket)throw Error('missing scoped editor ticket');
 const r=await fetch('/mc-config?ticket='+encodeURIComponent(ticket),{credentials:'omit'});
 if(!r.ok)throw Error('config HTTP '+r.status);
 const config=await r.json();window.MC_DOCUMENT_KEY=config.document.key;
 history.replaceState({},'', '/mc-editor');
 const s=document.createElement('script');s.src='/web-apps/apps/api/documents/api.js';
 s.onload=()=>{
  config.events={onAppReady:()=>window.MC_EDITOR_EVENTS.push('app-ready'),
    onDocumentReady:()=>{window.MC_DOC_READY=true;window.MC_EDITOR_EVENTS.push('document-ready')},
    onError:e=>window.MC_EDITOR_EVENTS.push('error:'+JSON.stringify(e))};
  window.MC_DOC_EDITOR=new DocsAPI.DocEditor('editor',config);
 };
 s.onerror=()=>window.MC_EDITOR_EVENTS.push('api-js-load-error');document.head.appendChild(s);
 }catch(e){window.MC_EDITOR_EVENTS.push('init-error:'+e.message)}})();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args): pass
    def send(self,status,body=b'',kind='application/json',headers=None):
        if isinstance(body,dict): body=json.dumps(body,separators=(',',':')).encode()
        if isinstance(body,str):body=body.encode()
        self.send_response(status)
        self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Connection','close')
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers()
        self.close_connection=True
        if body:
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass

    def json_body(self):
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0]!='application/json': return None
        lengths=self.headers.get_all('Content-Length',[])
        if len(lengths)!=1 or not lengths[0].isdigit() or not 0<int(lengths[0])<=100000:return None
        try: obj=json.loads(self.rfile.read(int(lengths[0])))
        except (ValueError,UnicodeError):return None
        return obj if isinstance(obj,dict) else None

    def cookie(self):
        headers=self.headers.get_all('Cookie',[])
        if len(headers)!=1:return ''
        parts=[part.strip().partition('=') for part in headers[0].split(';')]
        names=[v for k,sep,v in parts if sep and k==COOKIE]
        return names[0] if len(names)==1 else ''

    def auth(self): return AUTH.sessions.valid(self.cookie())

    def valid_host(self):
        host=self.headers.get('Host','')
        return host in (f'dev.main-computer.local:{BROWSER_PORT}',
                        f'onlyoffice.main-computer.local:{BROWSER_PORT}',
                        'fixture:'+os.environ.get('LAB_PORT','8000'))

    def parameters(self,allowed):
        try:
            params=parse_qsl(urlsplit(self.path).query, keep_blank_values=True, strict_parsing=True)
            if len(params)!=len({k for k,v in params}) or set(k for k,v in params)!=set(allowed):return None
            return dict(params)
        except ValueError:return None

    def do_GET(self):
        path=urlsplit(self.path).path
        if not self.valid_host():return self.send(403,{'error':'host'})
        if path=='/health':return self.send(200,{'ready': True})
        if path=='/auth/challenge':
            if self.headers.get('Host')!=f'dev.main-computer.local:{BROWSER_PORT}':return self.send(403,{'error':'wrong-origin'})
            try:return self.send(200,AUTH.challenge())
            except RuntimeError:return self.send(503,{'error':'unavailable'})
        if path=='/auth/login':
            if self.headers.get('Host')!=f'dev.main-computer.local:{BROWSER_PORT}':return self.send(403,{'error':'wrong-origin'})
            return self.send(200,LOGIN_HTML,'text/html; charset=utf-8')
        if path=='/graphical':
            if self.headers.get('Host')!=f'dev.main-computer.local:{BROWSER_PORT}':return self.send(403,{'error':'wrong-origin'})
            if not self.auth():return self.send(303,b'',headers={'Location':'/auth/login'})
            ticket=scoped_token('launch',MODE,DOC,int(time.time())+120)
            src=DOCS_BASE+'/mc-editor?ticket='+ticket
            return self.send(200,MC_HOME_HTML.replace('__DOCS_FRAME_URL__',src),
                             'text/html; charset=utf-8')
        if path in ('/mc-editor','/mc-config'):
            if self.headers.get('Host')!=f'onlyoffice.main-computer.local:{BROWSER_PORT}':return self.send(403,{'error':'wrong-origin'})
            query=self.parameters({'ticket'})
            if not query or not valid_token(query['ticket'],'launch',MODE,DOC):
                return self.send(403,{'error':'editor-launch-denied'})
            if path=='/mc-editor':return self.send(200,EDITOR_HTML,'text/html; charset=utf-8',
                headers={'Referrer-Policy':'no-referrer'})
            config=make_config()
            with REPORT_LOCK:REPORT['editor_configs']+=1
            config.pop('_browser_api',None)
            return self.send(200,config,headers={'Referrer-Policy':'no-referrer'})
        if path=='/api/onlyoffice/config':
            if not self.auth():return self.send(401,{'error':'auth-required'})
            if not TARGET.is_file():return self.send(404,{'error':'document-absent'})
            config=make_config()
            with REPORT_LOCK: REPORT['editor_configs']+=1
            return self.send(200,config)
        if path=='/api/onlyoffice/file':
            q=self.parameters({'mode','doc','cap'})
            if not q or (q['mode'],q['doc'])!=(MODE,DOC) or not valid_token(q['cap'],'download',MODE,DOC):
                return self.send(403,{'error':'download-denied'})
            with REPORT_LOCK: REPORT['downloads']+=1
            return self.send(200,TARGET.read_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        if path=='/api/onlyoffice/metrics':
            if not self.auth():return self.send(401,{'error':'auth-required'})
            with REPORT_LOCK:
                result={**REPORT,'document_sha256':hashlib.sha256(TARGET.read_bytes()).hexdigest(),
                        'server_boot_id':SERVER_BOOT_ID}
            if DURABLE_SAVE:
                result['durable_journal']=JOURNAL.current(TARGET,mode=MODE)
            return self.send(200,result)
        return self.send(404,{'error':'not-found'})

    def do_POST(self):
        path=urlsplit(self.path).path
        if not self.valid_host():return self.send(403,{'error':'host'})
        if path=='/auth/verify':
            if self.headers.get('Host')!=f'dev.main-computer.local:{BROWSER_PORT}' or self.headers.get('Origin')!=PUBLIC_BASE:
                return self.send(403,{'error':'origin'})
            body=self.json_body()
            if not body or set(body)!={'challenge_id','signature'}:return self.send(400,{'error':'bad-proof'})
            token=AUTH.verify(body['challenge_id'],body['signature'])
            if not token:return self.send(401,{'error':'proof-rejected'})
            with REPORT_LOCK: REPORT['session_issuance']+=1
            cookie=f'{COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=15552000'
            return self.send(200,{'ok':True},headers={'Set-Cookie':cookie})
        if path=='/api/onlyoffice/shadow-stale-probe':
            # Lab-only negative control: replay a base revision that the real
            # Document Server editor has already committed. This endpoint is
            # ABSENT from production and requires the actual disposable user
            # session plus same-origin request.
            if not SHADOW_REVISION_FENCE:
                return self.send(404,{'error':'shadow-not-enabled'})
            if self.headers.get('Origin')!=PUBLIC_BASE or not self.auth():
                return self.send(401,{'error':'auth-required'})
            body=self.json_body()
            if DURABLE_SAVE:
                if (not isinstance(body,dict) or set(body) !=
                    {'action','userdata','key'} or
                    body['action']!='replay-old-editor-revision'):
                    return self.send(400,{'error':'invalid-durable-probe'})
                before=TARGET.read_bytes()
                try:
                    JOURNAL.commit(TARGET,mode=MODE,key=body['key'],
                                   userdata=body['userdata'],data=before)
                except (JournalConflict,ValueError,OSError):
                    return self.send(409,{'stale_rejected':True,
                                          'bytes_unchanged':TARGET.read_bytes()==before})
                return self.send(500,{'error':'durable-old-save-accepted'})
            if body!={'action':'replay-old-editor-revision'}:
                return self.send(400,{'error':'invalid-probe'})
            from mcel_onlyoffice_shadow_revision import RevisionConflict
            before=TARGET.read_bytes()
            old_bases=[v for v in ACTIVE_KEYS.values() if v != hashlib.sha256(before).hexdigest()]
            if not old_bases:
                return self.send(409,{'error':'no-old-issued-grant-to-test'})
            try:
                REVISION_FENCE.commit(TARGET,old_bases[0],before)
            except RevisionConflict:
                return self.send(409,{'stale_rejected':True,
                                      'bytes_unchanged':TARGET.read_bytes()==before})
            return self.send(500,{'error':'stale-replay-was-accepted'})
        if path=='/api/onlyoffice/forcesave':
            if self.headers.get('Origin')!=PUBLIC_BASE or not self.auth():return self.send(401,{'error':'auth-required'})
            body=self.json_body()
            if not isinstance(body,dict) or body.get('action')!='forcesave' or not (
                    body=={'action':'forcesave'} or
                    CONTINUOUS_SAVE and set(body)=={'action','key'} and
                    isinstance(body.get('key'),str) and active_document_key(body['key'])):
                return self.send(400,{'error':'bad-command'})
            from urllib.request import urlopen
            key=body.get('key') if CONTINUOUS_SAVE else document_key()
            cmd={'c':'forcesave','key':key}
            if CONTINUOUS_SAVE:
                cmd['userdata']=issue_save_userdata(key)
            cmd['token']=jwt_sign(cmd.copy())
            request=Request('http://onlyoffice/coauthoring/CommandService.ashx',
                            data=json.dumps(cmd).encode(),headers={'Content-Type':'application/json'},method='POST')
            try:
                with urlopen(request,timeout=15) as resp:answer=json.loads(resp.read(4096))
                with REPORT_LOCK: REPORT['force_saves']+=1
                if DURABLE_SAVE and isinstance(answer,dict):
                    # Disposable test-only replay control; not a production response.
                    answer['_lab_userdata']=cmd['userdata']
                return self.send(200,answer)
            except Exception as exc:return self.send(502,{'error':type(exc).__name__})
        if path=='/api/onlyoffice/callback':
            q=self.parameters({'mode','doc','path','cap'})
            if not q or (q['mode'],q['doc'],q['path'])!=(MODE,DOC,DOC) or not valid_token(q['cap'],'callback',MODE,DOC):
                with REPORT_LOCK:REPORT['bad_callbacks']+=1
                return self.send(403,{'error':'callback-denied'})
            auth=self.headers.get('Authorization','')
            jwt=jwt_verify(auth[7:] if auth.startswith('Bearer ') else '')
            if not jwt:
                with REPORT_LOCK:REPORT['bad_callbacks']+=1
                return self.send(403,{'error':'callback-jwt-required'})
            body=self.json_body()
            signed_body=jwt.get('payload') if isinstance(jwt.get('payload'),dict) else jwt
            if (isinstance(signed_body,dict) and isinstance(body,dict) and
                    any(field in signed_body and signed_body[field]!=body.get(field)
                        for field in ('status','key','url','userdata'))):
                with REPORT_LOCK:REPORT['bad_callbacks']+=1
                return self.send(403,{'error':'callback-jwt-body-mismatch'})
            if not body or not active_document_key(body.get('key')):
                with REPORT_LOCK:REPORT['bad_callbacks']+=1
                return self.send(403,{'error':'callback-key-mismatch'})
            # Observe the *genuine signed* status=2 callback, after validating
            # its scoped URL token, Document Server JWT, and issued editor key.
            # Never record a signed token, callback URL, workbook bytes or user
            # identity in these aggregate counters.
            if FINAL_CLOSE_PROBE and body.get('status') == 2:
                with REPORT_LOCK:
                    REPORT['final_close_signed_status2'] += 1
                    if body.get('userdata') is not None:
                        REPORT['final_close_has_userdata'] += 1
            # Source-backed MC method executes only *after* authorizing the
            # real Document Server callback and its document/installation grant.
            obj=ActualHandler()
            obj.path=self.path
            obj.server=SimpleNamespace(signal=lambda *a,**k:None)
            obj._onlyoffice_safe_path=lambda name,**kw: TARGET if name==DOC else (_ for _ in ()).throw(ValueError('wrong file'))
            obj._read_json=lambda:body
            if DURABLE_SAVE and body.get('status') in (2,6):
                # The SAME real MC callback method executes, but its final
                # write is mediated by the file-backed, cross-process journal.
                # This protects the second callback even after fixture restart.
                if body.get('status')!=6 or not isinstance(body.get('userdata'),str):
                    with REPORT_LOCK:
                        REPORT['continuous_denials']+=1
                        if FINAL_CLOSE_PROBE and body.get('status')==2:
                            REPORT['final_close_rejected']+=1
                    return self.send(403,{'error':'durable-command-required'})
                userdata=body['userdata']
                def guarded_write(path,data):
                    digest=JOURNAL.commit(path,mode=MODE,key=body['key'],
                                          userdata=userdata,data=data)
                    meta=JOURNAL._parse(userdata)
                    with REPORT_LOCK:
                        REPORT['shadow_commits']+=1
                        REPORT['continuous_commits']+=1
                        REPORT['continuous_seqs'].append(meta['seq'])
                obj._onlyoffice_atomic_write=guarded_write
                sent={}
                obj._send_json=lambda payload,status=HTTPStatus.OK: sent.update(payload=payload,status=int(status))
                try:obj._handle_onlyoffice_callback()
                except Exception as exc:
                    with REPORT_LOCK:REPORT['continuous_denials']+=1
                    return self.send(409,{'error':type(exc).__name__})
            elif CONTINUOUS_SAVE and body.get('status') in (2,6):
                # Deliberately require command-service correlation for force
                # saves. An unsolicited timer/UI callback is not evidence of a
                # newer revision. Final close (status=2) requires a distinct
                # protocol and remains outside this experimental gate.
                meta=parse_save_userdata(body.get('userdata'),body['key'])
                if body.get('status') != 6 or meta is None:
                    with REPORT_LOCK:REPORT['continuous_denials'] += 1
                    return self.send(403,{'error':'uncorrelated-save-denied'})
                with REPORT_LOCK:
                    cursor=CONTINUOUS_STATE[body['key']]
                    seq=meta['seq']
                    if not save_sequence_fresh(body['key'], seq):
                        REPORT['continuous_denials'] += 1
                        return self.send(409,{'error':'stale-save-sequence'})
                    # The ordering comparison and the content SHA CAS are
                    # serialized for this one disposable HTTP fixture. A
                    # deployable version needs durable process-shared state.
                    def guarded_write(path, data):
                        digest=REVISION_FENCE.commit(path,cursor['sha'],data)
                        cursor['sha']=digest
                        cursor['accepted']=seq
                        REPORT['shadow_commits'] += 1
                        REPORT['continuous_commits'] += 1
                        REPORT['continuous_seqs'].append(seq)
                    obj._onlyoffice_atomic_write=guarded_write
                    sent={}
                    obj._send_json=lambda payload,status=HTTPStatus.OK: sent.update(payload=payload,status=int(status))
                    try:obj._handle_onlyoffice_callback()
                    except Exception as exc:
                        REPORT['continuous_denials'] += 1
                        return self.send(409,{'error':type(exc).__name__})
            else:
                if SHADOW_REVISION_FENCE:
                    # The original MC callback method remains the source being
                    # exercised. Only its final write is replaced by a shadow
                    # cross-process revision guard. The base revision is selected
                    # from the *server-issued* editor key, never callback input.
                    issued_base = ACTIVE_KEYS[body['key']]
                    def guarded_write(path, data):
                        REVISION_FENCE.commit(path, issued_base, data)
                        with REPORT_LOCK:REPORT['shadow_commits'] += 1
                    obj._onlyoffice_atomic_write = guarded_write
                sent={}
                obj._send_json=lambda payload,status=HTTPStatus.OK: sent.update(payload=payload,status=int(status))
                try:obj._handle_onlyoffice_callback()
                except Exception as exc: return self.send(500,{'error':type(exc).__name__})
            result=sent.get('payload',{'error':'source-handler-no-response'})
            if sent.get('status')==200:
                with REPORT_LOCK:
                    REPORT['callbacks'].append({'status':body.get('status'),'saved':bool(body.get('status') in (2,6)),
                                                'key':body.get('key')})
                    if body.get('status') in (2,6):REPORT['saved']+=1
                    if FINAL_CLOSE_PROBE and body.get('status')==2:
                        REPORT['final_close_committed']+=1
            else:
                with REPORT_LOCK: REPORT['bad_callbacks']+=1
            return self.send(sent.get('status',500),result)
        return self.send(404,{'error':'not-found'})


def document_key():
    # Stable within one edit/reopen round trip until the saved content changes.
    # A real installation would use a per-document revision identifier.
    return hashlib.sha256(TARGET.read_bytes()).hexdigest()[:32]


def make_config():
    key=document_key()
    if DURABLE_SAVE:
        # Explicitly authenticated launch handler calls make_config. When a
        # *new* editor is opened after committed bytes changed, rotate the
        # issued editor key without resetting the monotonic sequence journal.
        current=JOURNAL.current(TARGET,mode=MODE)
        if current is None:
            JOURNAL.register(TARGET,mode=MODE,key=key)
        elif current['key']!=key:
            JOURNAL.rotate_key(TARGET,mode=MODE,
                               old_key=current['key'],new_key=key)
    ACTIVE_KEYS[key] = hashlib.sha256(TARGET.read_bytes()).hexdigest()
    expiry=int(time.time())+1800
    download=scoped_token('download',MODE,DOC,expiry)
    callback=scoped_token('callback',MODE,DOC,expiry)
    source=f'http://fixture:8000/api/onlyoffice/file?mode={MODE}&doc={DOC}&cap={download}'
    sink=f'http://fixture:8000/api/onlyoffice/callback?mode={MODE}&doc={DOC}&path={DOC}&cap={callback}'
    config={'documentType':'cell','type':'desktop','width':'100%','height':'100%',
            'document':{'title':DOC,'fileType':'xlsx','key':key,'url':source,
                        'permissions':{'edit':True,'download':False,'print':False}},
            'editorConfig':{'mode':'edit','lang':'en','callbackUrl':sink,
                'customization':{'autosave':True,'forcesave':True},
                'user':{'id':'disposable-local-user','name':'MC isolated test'}},
            '_browser_api':f'{DOCS_BASE}/web-apps/apps/api/documents/api.js'}
    signing={k:v for k,v in config.items() if not k.startswith('_')}
    config['token']=jwt_sign(signing)
    return config


def main():
    if not TARGET.is_file():raise RuntimeError('disposable workbook missing')
    server=ThreadingHTTPServer((os.environ.get('LAB_BIND','0.0.0.0'), int(os.environ.get('LAB_PORT','8000'))),Handler)
    server.daemon_threads=True
    print('MC_REAL_ONLYOFFICE fixture-ready source-backed-callback=true isolated-state=true',flush=True)
    server.serve_forever()

if __name__=='__main__':main()
