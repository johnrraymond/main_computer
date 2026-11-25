"""Shared authentication contract. No live MC, real key, or security bypass."""
from types import SimpleNamespace
from pathlib import Path
import base64, json, hmac, hashlib
import pytest
from tools import mcel_functional_auth as auth

class Page:
    def __init__(self, ctx): self.ctx=ctx; self.url=''; self.closed=False
    def route(self, pattern, handler): self.handler=handler
    def set_default_timeout(self, timeout): pass
    def goto(self, url, **kwargs):
        self.url=url;self.ctx.calls.append(('navigate',url));return SimpleNamespace(status=200)
    def evaluate(self, script, arg):
        path=arg.get('path');self.ctx.calls.append(('fetch',path))
        if path==auth.IDENTITIES: return {'http':200 if self.ctx.logged_in else 401}
        if path==auth.CHALLENGE: return {'http':200,'challenge':{'algorithm':'HMAC-SHA256','challenge_id':'abc','nonce':'xyz'}}
        if path==auth.VERIFY:
            assert arg['body']=={'challenge_id':'abc','signature':self.ctx.signature}
            self.ctx.logged_in=True
            return {'http':200,'authenticated':True}
        if path==auth.SESSION:return {'http':200,'authenticated':self.ctx.logged_in}
        if path==auth.LOGOUT:
            self.ctx.logged_in=False
            return {'http':200,'authenticated':False}
        if arg.get('url')==auth.ORIGIN+auth.WORKER:
            return {'status':200,'headers':{'content-type':'text/html'},'body':base64.b64encode(b'<html>exact bytes</html>\r\n').decode()}
        raise AssertionError('unexpected route')
    def close(self):self.closed=True

class Context:
    def __init__(self,signature):self.signature=signature;self.calls=[];self.logged_in=False;self.closed=False;self.pages=[]
    def cookies(self,urls):
        return [{'name':auth.COOKIE,'secure':True,'httpOnly':True,'sameSite':'Strict','path':'/','value':'not-logged'}] if self.logged_in else []
    def new_page(self):
        page=Page(self);self.pages.append(page);return page
    def close(self):self.closed=True

@pytest.fixture
def fixture(tmp_path,monkeypatch):
    key=bytes(range(32));path=tmp_path/'proof.json'
    path.write_text(json.dumps({'type':'mc-proof-v2','key_b64':base64.urlsafe_b64encode(key).decode().rstrip('=')}))
    monkeypatch.setenv('MCEL_FUNCTIONAL_PROOF_FILE',str(path))
    return Context(base64.urlsafe_b64encode(hmac.digest(key,b'MC-AUTH-BOOTSTRAP-V1\nabc\nxyz\n',hashlib.sha256)).decode().rstrip('='))

def test_authentication_exact_sequence_and_revocation(fixture):
    seen={}
    def new(**kwargs):seen.update(kwargs);return fixture
    ctx=auth.new_context(SimpleNamespace(new_context=new),auth.ORIGIN)
    assert seen['ignore_https_errors'] is False and seen['service_workers']=='block'
    assert fixture.logged_in
    assert [v for k,v in fixture.calls if k=='fetch']==[auth.IDENTITIES,auth.CHALLENGE,auth.VERIFY,auth.SESSION]
    r=ctx.request.get(auth.ORIGIN+auth.WORKER,max_redirects=0)
    assert r.status==200 and r.body()==b'<html>exact bytes</html>\r\n'
    with pytest.raises(auth.SmokeFailure):ctx.request.get('https://example.org/')
    with pytest.raises(auth.SmokeFailure):ctx.request.get(auth.ORIGIN+auth.WORKER,headers={'Cookie':'bad'})
    with pytest.raises(auth.SmokeFailure):ctx.request.get(auth.ORIGIN+auth.WORKER,max_redirects=1)
    ctx.close();ctx.close()
    assert fixture.closed and not fixture.logged_in and all(p.closed for p in fixture.pages)
    assert [v for k,v in fixture.calls if k=='fetch'][-2:]==[auth.LOGOUT,auth.IDENTITIES]

def test_existing_session_not_touched(fixture):
    fixture.logged_in=True
    with pytest.raises(auth.SmokeFailure,match='context-not-disposable'):auth.authenticate_context(fixture)
    assert fixture.logged_in and fixture.calls==[]

def test_isolated_context_unchanged(fixture):
    opts={}
    def new(**kwargs):opts.update(kwargs);return fixture
    assert auth.new_context(SimpleNamespace(new_context=new),'http://127.0.0.1:39123',viewport={'width':320,'height':600}) is fixture
    assert not fixture.logged_in and opts=={'viewport':{'width':320,'height':600}}

@pytest.mark.parametrize('options',[{'storage_state':'user.json'},{'ignore_https_errors':True},{'extra_http_headers':{'Cookie':'x'}}])
def test_no_unsafe_context_options(options):
    with pytest.raises(auth.SmokeFailure):auth.new_context(None,auth.ORIGIN,**options)

def test_migration_only_exact_internal_listener():
    assert auth.secure_target('http://127.0.0.1:8765/applications/worker')==auth.ORIGIN+'/applications/worker'
    for u in ('http://127.0.0.1:87650','http://127.0.0.1:9876','http://127.0.0.1:8765.evil.test','https://example.com'):
        assert auth.secure_target(u)==u
    assert not auth.is_secure(auth.ORIGIN+'.evil.test')
    assert not auth.is_secure('https://evil@dev.main-computer.localhost:8875')


def test_polling_pages_close_before_session_revocation(fixture, monkeypatch):
    ctx = auth.new_context(SimpleNamespace(new_context=lambda **kwargs:fixture),auth.ORIGIN)
    application = ctx.new_page()
    original = auth.revoke_context
    def revoke(inner):
        assert application.closed, 'application must stop polling before cookie revocation'
        return original(inner)
    monkeypatch.setattr(auth,'revoke_context',revoke)
    ctx.close()
    assert fixture.closed and not fixture.logged_in
