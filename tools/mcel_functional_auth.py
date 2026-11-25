"""Shared disposable authentication for real MC functional test lanes.
No TLS exceptions, persistent browser state, or production security changes.
"""
from __future__ import annotations
import base64, hashlib, hmac, json, re, os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

ORIGIN = 'https://dev.main-computer.localhost:8875'
AUTH = '/api/auth/bootstrap/'
SESSION = AUTH + 'session'
CHALLENGE = AUTH + 'challenge'
VERIFY = AUTH + 'verify'
LOGOUT = AUTH + 'logout'
WORKER = '/applications/worker'
IDENTITIES = '/api/applications/slh/identities'
COOKIE = '__Host-MC-Session'
TOKEN = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


class SmokeFailure(RuntimeError):
    pass


def allowed(url, method):
    # Exact local HTTPS target only. Never forward a proof or cookie elsewhere.
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.netloc != urlsplit(ORIGIN).netloc
                or parsed.query or parsed.fragment):
            return False
        return (method == 'GET' and parsed.path in (SESSION, CHALLENGE, WORKER, IDENTITIES)
                or method == 'POST' and parsed.path in (VERIFY, LOGOUT))
    except ValueError:
        return False


def proof_signature(path, challenge):
    if not isinstance(challenge, dict) or challenge.get('algorithm') != 'HMAC-SHA256':
        raise SmokeFailure('unexpected-challenge-algorithm')
    cid, nonce = challenge.get('challenge_id'), challenge.get('nonce')
    if not all(isinstance(v, str) and TOKEN.fullmatch(v) for v in (cid, nonce)):
        raise SmokeFailure('invalid-challenge')
    with path.open('rb') as f:
        raw = f.read(4097)
    if len(raw) > 4096:
        raise SmokeFailure('invalid-proof-size')
    record = json.loads(raw)
    if not isinstance(record, dict) or record.get('type') != 'mc-proof-v2':
        raise SmokeFailure('invalid-proof-type')
    key = record.get('key_b64')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', key):
        raise SmokeFailure('invalid-proof-encoding')
    secret = base64.urlsafe_b64decode(key + '=')
    if len(secret) != 32:
        raise SmokeFailure('invalid-proof-length')
    message = f'MC-AUTH-BOOTSTRAP-V1\n{cid}\n{nonce}\n'.encode('ascii')
    signature = base64.urlsafe_b64encode(hmac.new(secret, message, hashlib.sha256).digest()).decode('ascii').rstrip('=')
    return {'challenge_id': cid, 'signature': signature}


# Executed in a real browser at the gateway origin. No persistent key, cookie
# value, identity list, or HTML body leaves the browser through this helper.
FETCH = r'''async ({path, body, kind}) => {
    const options = {credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10000)};
    if (body !== null) {
        options.method = 'POST'; options.headers = {'Content-Type': 'application/json'};
        options.body = JSON.stringify(body);
    }
    const r = await fetch(path, options);
    const out = {http: r.status};
    if (!r.ok) return out;
    if (kind === 'session') out.authenticated = (await r.json()).authenticated === true;
    if (kind === 'challenge') {
        const v = await r.json();
        out.challenge = {algorithm: v.algorithm, challenge_id: v.challenge_id, nonce: v.nonce};
    }
    if (kind === 'worker') {
        const d = new DOMParser().parseFromString(await r.text(), 'text/html');
        out.shell = d.body?.dataset.mcelShellCanonicalPromoted === 'true' && !!d.querySelector('#mcel-shell-frame');
    }
    if (kind === 'identities') {
        const v = await r.json();
        out.inventory = v.schema === 'mcel.slh.identities.v4b' && v.state === 'AVAILABLE' && Array.isArray(v.identities);
    }
    return out;
}'''


LEGACY_ORIGIN = 'http://127.0.0.1:8765'

def secure_target(url):
    """Translate only the known retired internal listener, never arbitrary hosts."""
    if isinstance(url,str) and (url == LEGACY_ORIGIN or url.startswith(LEGACY_ORIGIN + '/')):
        return ORIGIN + url[len(LEGACY_ORIGIN):]
    return url


def proof_path():
    return Path(os.environ.get('MCEL_FUNCTIONAL_PROOF_FILE', str(Path.home() / 'AAA_AUTH_MAIN_COMPUTER.json')))


def authenticate_context(ctx, proof=None, check=None):
    """Bootstrap a fresh context using the same browser fetch protocol as the live smoke."""
    def require(stage, condition, http=None):
        if check is not None:
            check(stage, condition, http)
        if not condition:
            raise SmokeFailure(stage + '-failed')
    if any(c['name'] == COOKIE for c in ctx.cookies([ORIGIN])):
        raise SmokeFailure('context-not-disposable')
    page = ctx.new_page()
    def guard(route):
        if allowed(route.request.url, route.request.method):
            route.continue_()
        else:
            route.abort()
    try:
        page.route('**/*', guard)
        page.set_default_timeout(10000)
        response = page.goto(ORIGIN + SESSION, wait_until='domcontentloaded', timeout=10000)
        require('trusted-https', response is not None and response.status == 200 and page.url == ORIGIN + SESSION,
                response.status if response is not None else None)
        def req(path, kind='', body=None):
            return page.evaluate(FETCH, {'path': path, 'kind': kind, 'body': body})
        r = req(IDENTITIES)
        require('anonymous-denied', r['http'] in (401, 403), r['http'])
        r = req(CHALLENGE, 'challenge')
        require('challenge', r['http'] == 200 and isinstance(r.get('challenge'), dict), r['http'])
        signed = proof_signature(proof or proof_path(), r['challenge'])
        r = req(VERIFY, 'session', signed)
        require('authenticate', r['http'] == 200 and r.get('authenticated') is True, r['http'])
        cookies = [c for c in ctx.cookies([ORIGIN]) if c['name'] == COOKIE]
        require('secure-cookie', len(cookies) == 1 and cookies[0]['secure'] and cookies[0]['httpOnly'] and
                cookies[0]['sameSite'] == 'Strict' and cookies[0]['path'] == '/')
        del cookies
        r = req(SESSION, 'session')
        require('authenticated-session', r['http'] == 200 and r.get('authenticated') is True, r['http'])
    finally:
        page.close()


class BrowserResponse:
    def __init__(self, value):
        self.status = value['status']
        self.headers = value['headers']
        self._bytes = base64.b64decode(value['body'])
    def body(self): return self._bytes
    def json(self): return json.loads(self._bytes)


class BrowserRequest:
    """Exact same-origin requests through the proven browser TLS/cookie stack.

    Used only by explicit HTTP preflight lanes, never as an application route
    interceptor. The response bytes are fetched, not navigated or executed.
    """
    def __init__(self, ctx): self.ctx = ctx
    def get(self, url, **kwargs): return self._send('GET', url, **kwargs)
    def post(self, url, **kwargs): return self._send('POST', url, **kwargs)
    def _send(self, method, url, *, data=None, headers=None, max_redirects=0, timeout=10000, negative_origin=None):
        parsed = urlsplit(url)
        if not is_secure(url) or parsed.fragment or parsed.username or parsed.password or max_redirects != 0:
            raise SmokeFailure('invalid-secure-request-target')
        if negative_origin is not None and (negative_origin != 'https://cross-origin.invalid' or method != 'POST'):
            raise SmokeFailure('invalid-negative-origin-probe')
        headers = dict(headers or {})
        if any(k.lower() in ('authorization','cookie','origin','host','referer') for k in headers):
            raise SmokeFailure('caller-credential-or-origin-header-refused')
        page = self.ctx.new_page()
        def guard(route):
            req = route.request
            if ((req.url == ORIGIN + SESSION and req.method == 'GET') or
                    (req.url == url and req.method == method)):
                if negative_origin and req.url == url and req.method == method:
                    route.continue_(headers={**req.all_headers(), 'origin': negative_origin})
                else:
                    route.continue_()
            else:
                route.abort()
        try:
            page.route('**/*', guard)
            r = page.goto(ORIGIN + SESSION, wait_until='domcontentloaded', timeout=timeout)
            if r is None or r.status != 200 or page.url != ORIGIN + SESSION:
                raise SmokeFailure('secure-request-session-unavailable')
            result = page.evaluate("""async ({url,method,data,headers,timeout}) => {
                const options={method,headers,credentials:'same-origin',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(timeout)};
                if(method==='POST') {options.headers['Content-Type']='application/json';options.body=typeof data==='string'?data:JSON.stringify(data);}
                const r=await fetch(url,options);
                const bytes=new Uint8Array(await r.arrayBuffer());
                if(bytes.length>64*1024*1024)throw Error('response-too-large');
                let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
                return {status:r.status,headers:Object.fromEntries(r.headers.entries()),body:btoa(binary)};
            }""", {'url':url,'method':method,'data':data,'headers':headers,'timeout':timeout})
            return BrowserResponse(result)
        finally:
            page.close()


def revoke_context(ctx):
    """Revoke the disposable session with browser fetch, not a second TLS stack."""
    if not any(c['name'] == COOKIE for c in ctx.cookies([ORIGIN])):
        return False
    page = ctx.new_page()
    try:
        page.route('**/*', lambda r: r.continue_() if allowed(r.request.url, r.request.method) else r.abort())
        response = page.goto(ORIGIN + SESSION, wait_until='domcontentloaded', timeout=10000)
        if response is None or response.status != 200 or page.url != ORIGIN + SESSION:
            return False
        r = page.evaluate(FETCH, {'path':LOGOUT,'kind':'session','body':{}})
        denied = page.evaluate(FETCH, {'path':IDENTITIES,'kind':'','body':None})
        return r['http'] == 200 and denied['http'] in (401,403)
    finally:
        page.close()


def is_secure(base):
    if not base:
        return False
    parsed = urlsplit(base)
    return (parsed.scheme, parsed.netloc) == ('https', urlsplit(ORIGIN).netloc)


class _DisposableContext:
    """Local facade: the existing lane's finally: ctx.close() revokes its session."""
    def __init__(self, ctx):
        self._ctx = ctx
        self._closed = False
        self.request = BrowserRequest(ctx)
    def __getattr__(self, name):
        return getattr(self._ctx, name)
    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            # Stop application timers and requests before invalidating the cookie.
            # The revocation probe uses a separate fresh page in this context.
            for page in list(self._ctx.pages):
                page.close()
            if not revoke_context(self._ctx):
                raise SmokeFailure('disposable-session-revocation-unverified')
        finally:
            self._ctx.close()


def new_context(browser, base, **options):
    """Authenticate only the exact gateway; offline/isolated fixtures stay untouched."""
    if not is_secure(base):
        return browser.new_context(**options)
    if options.get('storage_state') is not None:
        raise SmokeFailure('secure-tests-require-fresh-disposable-session')
    if options.get('ignore_https_errors') or options.get('extra_http_headers'):
        raise SmokeFailure('unsafe-auth-context-options')
    options.update(ignore_https_errors=False, service_workers='block')
    ctx = browser.new_context(**options)
    if any(c['name'] == COOKIE for c in ctx.cookies([ORIGIN])):
        ctx.close()
        raise SmokeFailure('context-not-disposable')
    try:
        authenticate_context(ctx)
    except BaseException:
        try:
            revoke_context(ctx)
        except Exception:
            pass
        finally:
            ctx.close()
        raise
    return _DisposableContext(ctx)


@contextmanager
def authenticated_request(base):
    """For live source preflights which must read raw HTTP bytes, not execute HTML."""
    if not is_secure(base):
        raise SmokeFailure('expected-secure-gateway')
    from playwright.sync_api import sync_playwright
    from tools.mcel_phase2b_browser import launch_semantic_browser
    with sync_playwright() as p:
        browser, _ = launch_semantic_browser(p)
        try:
            ctx = new_context(browser, base)
            try:
                yield ctx.request
            finally:
                ctx.close()
        finally:
            browser.close()
