"""Phase 2A isolated cryptographic and API acceptance tests; no production network claims."""
import io
import json
import secrets
import tempfile
import unittest
from pathlib import Path

from main_computer.slh.api import SLHAPI, ENDPOINTS
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.ports import DEFAULT_SLH_API_PORT


class TestSLHPhase2A(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.auth=SLHAuthority(self.path)
        self.api=SLHAPI(self.auth)
        self.admin_key,self.admin_pub=generate_identity()
        self.admin_device='admin-device'
        self.boot_token=(self.path/'bootstrap.secret').read_text().strip()

    def call(self,method,path,body=None,token='',loopback=False):
        return self.api.call(method,path,body or {},token=token,loopback=loopback)

    def bootstrap(self):
        data={'org_id':'org-testing','admin_id':'root-admin','admin_name':'Root Administrator',
              'device_id':self.admin_device,'public_key':self.admin_pub,'bootstrap_secret':self.boot_token}
        statement={k:data[k] for k in ('org_id','admin_id','device_id','public_key')}
        statement.update({'version':1,'action':'slh.admin.bootstrap'})
        data['proof']=sign_object(self.admin_key,statement)
        status,body=self.call('POST','/v1/bootstrap/admin',data,loopback=True)
        self.assertEqual(status,201,body)
        return body

    def login(self,device_id,key):
        status,challenge=self.call('POST','/v1/auth/challenge',{'device_id':device_id})
        self.assertEqual(status,200,challenge)
        status,auth=self.call('POST','/v1/auth/verify',{'device_id':device_id,
                     'challenge_id':challenge['challenge_id'],'signature':sign_object(key,challenge)})
        self.assertEqual(status,200,auth)
        return auth['access_token'],challenge

    def enroll(self,admin='root-admin',device_id='worker-device',kind='worker',role='worker'):
        key,pub=generate_identity()
        data={'selected_admin':admin,'device_id':device_id,'public_key':pub,
              'kind':kind,'device_name':'RTX Box','claimed_user':'Worker Operator',
              'requested_role':role,'client_nonce':secrets.token_urlsafe(32)}
        statement={**data,'version':1,'action':'slh.device.enrollment.request',
                   'org_id':'org-testing'}
        data['proof']=sign_object(key,statement)
        status,response=self.call('POST','/v1/enrollment/requests',data)
        self.assertEqual(status,201,response)
        return response['request_id'],key,pub

    def test_bootstrap_host_local_only_and_immutable(self):
        self.assertEqual(self.call('POST','/v1/bootstrap/admin',{},loopback=False)[1]['error']['code'],'BOOTSTRAP_LOCAL_ONLY')
        self.bootstrap()
        self.assertFalse((self.path/'bootstrap.secret').exists())
        self.assertEqual(self.call('POST','/v1/bootstrap/admin',{},loopback=True)[0],409)
        self.assertEqual(self.auth.status()['organization_id'],'org-testing')

    def test_real_signature_login_and_replay(self):
        self.bootstrap()
        t,challenge=self.login(self.admin_device,self.admin_key)
        status,_=self.call('POST','/v1/auth/verify',{'device_id':self.admin_device,'challenge_id':challenge['challenge_id'],
                                      'signature':sign_object(self.admin_key,challenge)})
        self.assertEqual(status,403)
        self.assertEqual(self.call('GET','/v1/admin/status',token=t)[0],200)
        self.assertEqual(self.call('GET','/v1/admin/status')[0],401)
        status,ch=self.call('POST','/v1/auth/challenge',{'device_id':self.admin_device})
        wrong,_=generate_identity()
        status,out=self.call('POST','/v1/auth/verify',{'device_id':self.admin_device,'challenge_id':ch['challenge_id'],
                                                    'signature':sign_object(wrong,ch)})
        self.assertEqual(status,403)
        self.assertEqual(out['error']['code'],'INVALID_AUTHENTICATOR')

    def test_pending_queue_and_same_approval_engine(self):
        self.bootstrap()
        admin,_=self.login(self.admin_device,self.admin_key)
        first,key,_=self.enroll()
        self.assertEqual(self.call('GET','/v1/admin/enrollments',token=admin)[1]['enrollments'][0]['status'],'pending')
        status,resp=self.call('POST','/v1/enrollment/approvals',{'request_id':first},token=admin)
        self.assertEqual(status,201,resp)
        self.assertEqual(resp['authorized_by'],'root-admin')
        worker_token,_=self.login('worker-device',key)
        self.assertEqual(self.call('GET','/v1/admin/devices',token=worker_token)[0],403)
        second,key2,_=self.enroll(device_id='worker-2')
        status,issued=self.call('POST',f'/v1/admin/enrollments/{second}/keys',{},token=admin)
        self.assertEqual(status,201,issued)
        status,result=self.call('POST','/v1/enrollment/approvals',{'request_id':second,'enrollment_key':issued['enrollment_key']})
        self.assertEqual(status,201,result)
        self.assertEqual(result['authorized_by'],'root-admin')
        self.assertEqual(self.call('POST','/v1/enrollment/approvals',{'request_id':second,'enrollment_key':issued['enrollment_key']})[0],409)
        self.login('worker-2',key2)

    def test_wrong_request_key_and_inspection_policy(self):
        self.bootstrap();admin,_=self.login(self.admin_device,self.admin_key)
        a,_,_=self.enroll()
        b,_,_=self.enroll(device_id='worker-2')
        status,issued=self.call('POST',f'/v1/admin/enrollments/{a}/keys',{},token=admin)
        self.assertEqual(status,201)
        self.assertEqual(self.call('POST','/v1/enrollment/approvals',{'request_id':b,'enrollment_key':issued['enrollment_key']})[0],403)
        self.call('PATCH','/v1/admin/policies',{'physical_inspection_required':True},token=admin)
        self.assertEqual(self.call('POST','/v1/enrollment/approvals',{'request_id':a},token=admin)[1]['error']['code'],'INSPECTION_REQUIRED')
        self.assertEqual(self.call('POST',f'/v1/admin/enrollments/{b}/keys',{},token=admin)[0],403)
        self.assertEqual(self.call('POST',f'/v1/admin/enrollments/{a}/inspect',{},token=admin)[0],200)
        self.assertEqual(self.call('POST','/v1/enrollment/approvals',{'request_id':a,'enrollment_key':issued['enrollment_key']})[0],201)

    def test_revocation_and_restart_preserves_data_not_sessions(self):
        self.bootstrap();admin,_=self.login(self.admin_device,self.admin_key)
        rid,key,_=self.enroll()
        self.call('POST','/v1/enrollment/approvals',{'request_id':rid},token=admin)
        worker,_=self.login('worker-device',key)
        self.assertEqual(self.call('GET','/v1/auth/session',token=worker)[0],200)
        self.assertEqual(self.call('PATCH','/v1/admin/devices/worker-device',{'name':'Lab GPU','notes':'Desk 4'},token=admin)[0],200)
        new_api=SLHAPI(SLHAuthority(self.path))
        self.assertEqual(new_api.call('GET','/v1/admin/devices',token=admin)[0],401)
        fresh,_=self.login(self.admin_device,self.admin_key)
        self.assertEqual(new_api.call('GET','/v1/admin/devices',token=fresh)[0],401)
        token2,newch=self._login_new_api(new_api,'admin-device',self.admin_key)
        status,data=new_api.call('GET','/v1/admin/devices',token=token2)
        self.assertEqual(status,200)
        self.assertEqual(next(x for x in data['devices'] if x['id']=='worker-device')['notes'],'Desk 4')
        self.assertEqual(new_api.call('POST','/v1/admin/devices/worker-device/revoke',token=token2)[0],200)
        self.assertEqual(self.call('GET','/v1/auth/session',token=worker)[0],401)
        self.assertEqual(new_api.call('POST','/v1/auth/challenge',{'device_id':'worker-device'})[0],403)

    def _login_new_api(self,api,device,key):
        _,ch=api.call('POST','/v1/auth/challenge',{'device_id':device})
        _,resp=api.call('POST','/v1/auth/verify',{'device_id':device,'challenge_id':ch['challenge_id'],
                                                'signature':sign_object(key,ch)})
        return resp['access_token'],ch

    def test_stubs_and_openapi_are_honest(self):
        self.bootstrap();admin,_=self.login(self.admin_device,self.admin_key)
        status,body=self.call('POST','/v1/accounting/reconcile',{},token=admin)
        self.assertEqual(status,501)
        self.assertEqual(body['error']['code'],'NOT_IMPLEMENTED')
        self.assertEqual(self.call('POST','/v1/accounting/reconcile')[0],401)
        status,doc=self.call('GET','/openapi.json')
        self.assertEqual(status,200)
        self.assertEqual(len(doc['paths'])>=30,True)
        self.assertEqual(doc['paths']['/v1/accounting/reconcile']['post']['x-implementation'],'unimplemented')
        self.assertEqual(self.call('GET','/v1/connections',token=admin)[0],200)

    def test_transport_parity_and_wsgi_loopback_guard(self):
        self.bootstrap();admin,_=self.login(self.admin_device,self.admin_key)
        self.assertEqual(self.call('GET','/v1/admin/devices',token=admin,loopback=True),
                         self.call('GET','/v1/admin/devices',token=admin,loopback=False))
        def wsgi(remote,host,origin=None):
            state=[]
            env={'REQUEST_METHOD':'GET','PATH_INFO':'/v1/admin/devices','REMOTE_ADDR':remote,
                 'HTTP_HOST':host,'HTTP_AUTHORIZATION':'Bearer '+admin,
                 'wsgi.input':io.BytesIO(b''),'CONTENT_LENGTH':'0'}
            if origin: env['HTTP_ORIGIN']=origin
            raw=b''.join(self.api.wsgi_app(env,lambda status,headers:state.append(status)))
            return int(state[0][:3]),json.loads(raw)
        self.assertEqual(wsgi('10.0.0.5',f'localhost:{DEFAULT_SLH_API_PORT}')[0],403)
        self.assertEqual(wsgi('127.0.0.1','evil.example')[0],403)
        self.assertEqual(wsgi('127.0.0.1',f'localhost:{DEFAULT_SLH_API_PORT}','http://evil.example')[0],403)
        self.assertEqual(wsgi('127.0.0.1',f'localhost:{DEFAULT_SLH_API_PORT}')[0],200)

    def test_cross_org_challenge_and_unauthorized_enrollment(self):
        self.bootstrap();admin,_=self.login(self.admin_device,self.admin_key)
        _,ch=self.call('POST','/v1/auth/challenge',{'device_id':self.admin_device})
        substituted={**ch,'org_id':'different-company'}
        self.assertEqual(self.call('POST','/v1/auth/verify',{'device_id':self.admin_device,'challenge_id':ch['challenge_id'],
                                             'signature':sign_object(self.admin_key,substituted)})[0],403)
        self.assertEqual(self.call('POST','/v1/admin/enrollments/bad-id/keys',{},token='bad')[0],401)
        self.assertGreater(len(self.call('GET','/v1/admin/audit',token=admin)[1]['events']),0)


    def test_last_administrator_and_expired_keys(self):
        self.bootstrap();admin,_=self.login(self.admin_device,self.admin_key)
        status,data=self.call('POST','/v1/admin/devices/admin-device/revoke',{},token=admin)
        self.assertEqual(status,409)
        self.assertEqual(data['error']['code'],'LAST_ADMIN_PROTECTED')
        rid,_,_=self.enroll()
        status,issued=self.call('POST',f'/v1/admin/enrollments/{rid}/keys',{},token=admin)
        self.assertEqual(status,201)
        with self.auth._db() as c:
            c.execute('UPDATE enrollment_keys SET expires_at=0 WHERE enrollment_id=?',(rid,))
        status,data=self.call('POST','/v1/enrollment/approvals',{'request_id':rid,'enrollment_key':issued['enrollment_key']})
        self.assertEqual(status,403)
        self.assertEqual(data['error']['code'],'INVALID_ENROLLMENT_KEY')

    def test_invalid_signature_consumes_challenge(self):
        self.bootstrap()
        _,challenge=self.call('POST','/v1/auth/challenge',{'device_id':self.admin_device})
        wrong,_=generate_identity()
        payload={'device_id':self.admin_device,'challenge_id':challenge['challenge_id'],
                 'signature':sign_object(wrong,challenge)}
        self.assertEqual(self.call('POST','/v1/auth/verify',payload)[0],403)
        payload['signature']=sign_object(self.admin_key,challenge)
        self.assertEqual(self.call('POST','/v1/auth/verify',payload)[1]['error']['code'],'INVALID_OR_EXPIRED_CHALLENGE')


def run_isolated_harness():
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(TestSLHPhase2A)
    result=unittest.TestResult()
    suite.run(result)
    return {'schema':'main-computer.slh-auth-harness.v1','status':'PASS' if result.wasSuccessful() else 'FAIL',
            'tests_run':result.testsRun,'failures':[str(t) + ': '+err[-500:] for t,err in result.failures+result.errors],
            'network':'NOT_TESTED','seh_e2e':'NOT_IMPLEMENTED', 'marketplace_delegation':'NOT_IMPLEMENTED'}

if __name__=='__main__':unittest.main()
