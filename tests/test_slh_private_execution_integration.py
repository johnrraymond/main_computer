"""P2 network contract: real pinned TLS Org API to a separate pinned TLS Worker."""
import hashlib
import http.client
import json
import ssl
import threading
import unittest

from main_computer.slh.api import SLHAPI
from main_computer.slh.managed_gateway import transport_identity
from main_computer.slh.organization_service import OrganizationHTTPServer, OrganizationHTTPHandler


class TestPrivateExecutionNetwork(unittest.TestCase):
    def setUp(self):
        from tests.test_slh_private_requests import TestPrivateRequests as _Fixture
        helper = _Fixture('test_real_private_worker_success_and_metadata_only_durable_replay')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.fixture = helper
        ctx, fingerprint = transport_identity(helper.root/'state')
        self.fingerprint = fingerprint
        server=OrganizationHTTPServer(('127.0.0.1',0), OrganizationHTTPHandler)
        server.socket=ctx.wrap_socket(server.socket,server_side=True)
        server.authority=helper.auth
        server.fingerprint=fingerprint
        server.workers_enabled=True
        server.seh_enabled=False
        server.gateway=helper.gateway
        server.private_requests=helper.private
        server.api=SLHAPI(helper.auth,private_requests=helper.private)
        server.marketplace_controller=None
        self.server=server
        thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.05},daemon=True)
        thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join(2)))

    def call(self, method, path, token='', body=None):
        ctx=ssl._create_unverified_context()
        c=http.client.HTTPSConnection('127.0.0.1',self.server.server_port,timeout=8,context=ctx)
        headers={'Authorization':'Bearer '+token}
        if body is not None:
            headers['Content-Type']='application/json'
        c.connect()
        actual=hashlib.sha256(c.sock.getpeercert(binary_form=True)).hexdigest()
        self.assertEqual(actual,self.fingerprint,'Organization TLS certificate pin changed')
        try:
            c.request(method,path,body=json.dumps(body) if body is not None else None,headers=headers)
            r=c.getresponse()
            return r.status,json.loads(r.read())
        finally:c.close()

    def test_real_tls_private_request_and_access_control(self):
        f=self.fixture
        token=f.enroll('req-1','requester')
        other=f.enroll('req-2','requester')
        f.worker()
        req=f.req('http-request','non-echo private prompt')
        status,result=self.call('POST','/v1/work',token,req)
        self.assertEqual(status,200,result)
        self.assertEqual(result['status'],'SUCCEEDED')
        self.assertEqual(result['result']['response']['content'],'computed 23')
        st,read=self.call('GET','/v1/work/http-request',token)
        self.assertEqual(st,200)
        self.assertNotIn('result',read)
        self.assertEqual(read['status'],'SUCCEEDED')
        self.assertEqual(self.call('GET','/v1/work/http-request',other)[0],404)
        self.assertEqual(self.call('POST','/v1/work','',req)[0],401)
        self.assertEqual(self.call('POST','/v1/work',token,req)[1]['status'],'SUCCEEDED')
        self.assertEqual(self.call('POST','/v1/work',token,req)[1].get('result'),None)

    def test_network_api_does_not_claim_private_support_without_service(self):
        token=self.fixture.enroll('req-1','requester')
        self.server.api.private_requests=None
        self.assertEqual(self.call('POST','/v1/work',token,self.fixture.req())[0],501)

if __name__=='__main__':unittest.main()
