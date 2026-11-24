"""Exercise the real TLS gateway + signed ingress + native SLH route guards.

Only the final SLH/LMS operation is substituted; no real deployments or writes.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import http.client, json, socket, ssl
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from main_computer.auth_bootstrap import BootstrapAuthority, BootstrapServer, generate_tls_certificate, provision
from main_computer.auth_bootstrap_service_deployment import RegisteredHandler
from main_computer.hardened_ingress import AssertionVerifier, admit
from main_computer.mcel_slh_seh_deployment_route import handle_seh_deployment
from main_computer.mcel_slh_seh_lifecycle_route import handle_seh_lifecycle
from main_computer.mcel_backend_browser_route import browser_request_authorized
from tools.mcel_onlyoffice_cutover import ProbeHandler, running, auth_cookie, DEV

DEPLOY = '/api/applications/slh/seh/deployment'
LIFE = '/api/applications/slh/seh/lifecycle'
BROWSER = '/api/applications/browser/control'
BODY = {'target':'local','operation':'inspect','arguments':{},'sessionTicket':''}
class App(ProbeHandler):
    def do_POST(self):
        if not admit(self, 'POST'): return
        assert not self.headers.get('Cookie')
        if self.path == DEPLOY: return handle_seh_deployment(self)
        if self.path == LIFE: return handle_seh_lifecycle(self)
        if self.path == BROWSER:
            return self._send_json({'allowed':browser_request_authorized(self, write=True)})
        self._send_json({'authorization_present':bool(self.headers.get('Authorization'))})
    def do_GET(self):
        if not admit(self, 'GET'): return
        self._send_json({'origin':self.headers.get('Origin'), 'site':self.headers.get('Sec-Fetch-Site'),
                         'dest':self.headers.get('Sec-Fetch-Dest'), 'cookie':bool(self.headers.get('Cookie'))})

def check(name, ok):
    assert ok, name
    print('MCEL_NATIVE_GATEWAY', name, 'PASS', flush=True)

with TemporaryDirectory() as tmp:
    state=Path(tmp)/'auth'; proof=provision(state)
    cert,key,_=generate_tls_certificate(state); authority=BootstrapAuthority(state)
    with running(ThreadingHTTPServer(('127.0.0.1',0), App)) as app:
        app.mc_gateway_verifier=AssertionVerifier(authority)
        with running(BootstrapServer(('127.0.0.1',0), authority, cert=cert,key=key,isolated_backend_ports={'dev':app.server_port})) as server:
            server.RequestHandlerClass=RegisteredHandler
            port=server.server_port
            cookie=auth_cookie(server,proof,cert)
            origin=f'https://{DEV}:{port}'
            class TLS(http.client.HTTPSConnection):
                def connect(self):
                    raw=socket.create_connection(('127.0.0.1',port),timeout=90)
                    self.sock=self._context.wrap_socket(raw,server_hostname=self.host)
            def request(path, *, body=BODY, authenticated=True, extra=None, method='POST'):
                headers={'Host':f'{DEV}:{port}', 'Origin':origin, 'Sec-Fetch-Site':'same-origin',
                         'Content-Type':'application/json', 'X-MCEL-SLH-Operation':'seh-deployment'}
                if authenticated: headers['Cookie']=cookie
                for name,value in (extra or {}).items():
                    if value is None: headers.pop(name,None)
                    else: headers[name]=value
                conn=TLS(DEV,port,context=ssl.create_default_context(cafile=str(cert)),timeout=90)
                try:
                    conn.request(method,path,body=json.dumps(body).encode() if method=='POST' else None,headers=headers)
                    r=conn.getresponse(); return r.status,json.loads(r.read())
                finally: conn.close()
            with patch('main_computer.mcel_slh_seh_deployment_route.execute_seh_deployment',return_value={'observed':True}) as observe, \
                 patch('main_computer.mcel_slh_seh_lifecycle_route.execute_seh_lifecycle',return_value={'observed':True}) as lifecycle:
                check('anonymous-denied',request(DEPLOY,authenticated=False)[0]==401 and observe.call_count==0)
                check('cross-origin-denied',request(DEPLOY,extra={'Origin':'https://foreign.invalid'})[0]==403 and observe.call_count==0)
                check('cross-site-denied',request(DEPLOY,extra={'Sec-Fetch-Site':'cross-site'})[0]==403 and observe.call_count==0)
                check('missing-origin-denied',request(DEPLOY,extra={'Origin':None})[0]==403 and observe.call_count==0)
                check('wrong-operation-denied',request(DEPLOY,extra={'X-MCEL-SLH-Operation':'wrong'})[0]==403 and observe.call_count==0)
                check('missing-operation-denied',request(DEPLOY,extra={'X-MCEL-SLH-Operation':None})[0]==403 and observe.call_count==0)
                check('missing-content-type-denied',request(DEPLOY,extra={'Content-Type':None})[0]==403 and observe.call_count==0)
                check('unexpected-local-bearer-denied',request(DEPLOY,extra={'Authorization':'Bearer disposable-invalid'})[0]==403 and observe.call_count==0)
                check('local-inspect-through-real-route',request(DEPLOY)==(200,{'ok':True,'result':{'observed':True}}) and observe.call_count==1)
                check('network-ticket-preserved',request(DEPLOY,body={**BODY,'target':'network','sessionTicket':'disposable-ticket'})[0]==200 and observe.call_args.kwargs['remote_ticket']=='disposable-ticket')
                b={'target':'local','operation':'status','sessionTicket':''}
                h={'X-MCEL-SLH-Operation':'seh-lifecycle'}
                check('lifecycle-missing-bearer-denied',request(LIFE,body=b,extra=h)[0]==400 and lifecycle.call_count==0)
                check('lifecycle-bearer-preserved',request(LIFE,body=b,extra={**h,'Authorization':'Bearer disposable-token'})[0]==200 and lifecycle.call_args.kwargs['local_token']=='disposable-token')
                check('browser-operation-preserved',request(BROWSER,extra={'X-MCEL-Browser-Operation':'browser-control'})==(200,{'allowed':True}))
                check('unrelated-route-no-bearer',request('/api/test',extra={'Authorization':'Bearer disposable-token'})==(200,{'authorization_present':False}))
                result=request('/applications/slh',method='GET',extra={'Sec-Fetch-Dest':'iframe'})
                check('frame-navigation-metadata-preserved',result==(200,{'origin':f'http://127.0.0.1:{app.server_port}','site':'same-origin','dest':'iframe','cookie':False}))
                result=request('/applications/slh',method='GET',extra={'Origin':'https://foreign.invalid','Sec-Fetch-Site':'cross-site'})
                check('foreign-origin-get-denied',result[0]==403)
                check('backend-origin-spoof-denied',request('/applications/slh',method='GET',extra={'Origin':f'http://127.0.0.1:{app.server_port}'})[0]==403)
                check('missing-get-origin-not-invented',request('/applications/slh',method='GET',extra={'Origin':None})[1]['origin'] is None)
                conn=http.client.HTTPConnection('127.0.0.1',app.server_port,timeout=10)
                try:
                    conn.request('POST',DEPLOY,body=json.dumps(BODY),headers={'Origin':f'http://127.0.0.1:{app.server_port}','Content-Type':'application/json','X-MCEL-SLH-Operation':'seh-deployment'})
                    check('direct-backend-denied',conn.getresponse().status==401)
                finally: conn.close()
                authority.sessions.revoke(cookie.split('=',1)[1])
                check('revoked-session-denied',request(DEPLOY)[0]==401 and observe.call_count==2)
print('NATIVE_ROUTE_CONTRACT_PASS',flush=True)
'''

class NativeRouteContract(unittest.TestCase):
    def test_real_tls_native_route_contract(self):
        env={**os.environ,'MAIN_COMPUTER_HARDENED_CANDIDATE':'1','MAIN_COMPUTER_PRIMARY_OWNER':'dev','MAIN_COMPUTER_INSTALLATION_MODE':'dev'}
        result=subprocess.run([sys.executable,'-c',SCRIPT],cwd=ROOT,env=env,capture_output=True,text=True,timeout=900 if os.name=='nt' else 180)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('NATIVE_ROUTE_CONTRACT_PASS',result.stdout)

if __name__=='__main__':unittest.main()
