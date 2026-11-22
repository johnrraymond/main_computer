"""Independent offline negative-control check for the single-session lab.

    python -m tools.test_mcel_security_unified_session_offline

No Docker, production processes or persistent MC state are accessed.
"""
from __future__ import annotations
import hashlib
import hmac
import http.client
import secrets
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from tools.mcel_security_session_gateway import COOKIE_NAME, Sessions, make_gateway, scoped_capability


def main():
    secret=secrets.token_urlsafe(40)
    login_key=secrets.token_urlsafe(40)
    hits=[]
    expected=scoped_capability(secret,'viewport')
    class Backend(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            supplied=self.headers.get('X-MC-Gateway-Capability','')
            valid=hmac.compare_digest(expected,supplied)
            if valid:hits.append(self.path)
            self.send_response(200 if valid else 403)
            self.send_header('Content-Length','0')
            self.end_headers()
    def serve(srv):
        thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
        return thread
    with tempfile.TemporaryDirectory(prefix='mc-unified-session-selftest-') as temp:
        # Choose base from an ephemeral gateway port; backend will be BASE+1.
        with ThreadingHTTPServer(('127.0.0.1',0),Backend) as reservation:
            base=reservation.server_port-11
        if base < 1024:raise RuntimeError('unexpected port')
        with ThreadingHTTPServer(('127.0.0.1',base+1),Backend) as backend:
            thread=serve(backend)
            with ThreadingHTTPServer(('127.0.0.1',base+11),make_gateway(base,secret,Path(temp)/'sessions.json',login_key)) as gateway:
                gwthread=serve(gateway)
                def call(verb,path,body=None,headers=None):
                    conn=http.client.HTTPConnection('127.0.0.1',base+11,timeout=3)
                    hdr={'Host':f'127.0.0.1:{base+11}', **(headers or {})}
                    try:
                        conn.request(verb,path,body=body,headers=hdr)
                        r=conn.getresponse();return r.status,dict(r.getheaders()),r.read()
                    finally:conn.close()
                assert call('GET','/mc/viewport/private')[0]==401 and not hits
                assert call('POST','/auth/login',b'bogus')[0]==401
                status,headers,_=call('POST','/auth/login',login_key.encode())
                assert status==200
                cookie=headers['Set-Cookie'].split(';',1)[0]
                assert cookie.startswith(COOKIE_NAME+'=') and secret not in cookie
                assert call('GET','/mc/viewport/private',headers={'Cookie':cookie})[0]==200
                assert hits==['/api/private']
                assert call('GET','/auth/check-persisted',headers={'Cookie':cookie})[0]==200
                assert call('POST','/auth/logout',b'',{'Cookie':cookie})[0]==200
                assert call('GET','/mc/viewport/private',headers={'Cookie':cookie})[0]==401
                print('MC_UNIFIED_SELFTEST genuine-session-authority-and-revocation PASS',flush=True)
                prior=len(hits)
                # Intentionally corrupt the exact auth admission decision. The
                # same anonymous test must now become an unauthorized 200;
                # this is a negative control, not a successful attack report.
                with patch.object(Sessions,'valid',lambda self,token:True):
                    unprotected=call('GET','/mc/viewport/private')[0]
                assert unprotected==200 and len(hits)==prior+1, ('negative control inconclusive',unprotected)
                print('MC_UNIFIED_SELFTEST deliberately-removed-session-guard-detected PASS',flush=True)
                assert call('GET','/mc/viewport/private')[0]==401
                gateway.shutdown();gwthread.join(timeout=3)
            backend.shutdown();thread.join(timeout=3)
    print('MC_UNIFIED_SELFTEST SUMMARY {"status":"PASS","production_modified":false}',flush=True)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
