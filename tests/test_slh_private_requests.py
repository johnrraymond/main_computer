"""P2 durability, access control and real managed private Worker execution."""
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest

from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.api import SLHAPI
from main_computer.slh.managed_gateway import SLHManagedGateway
from main_computer.slh.managed_client import ManagedSLHWorkerClient
from main_computer.slh.private_requests import PrivateRequests
from tools.slh_authenticator_cli import save_identity, load_identity


class TestPrivateRequests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.auth=SLHAuthority(self.root/'state')
        self.identity_dir=self.root/'client-keys'
        key,pub=generate_identity()
        self.admin_key=key  # P3 admin policy tests sign real administrator sessions
        statement={'version':1,'action':'slh.admin.bootstrap','org_id':'private-lab',
                   'admin_id':'admin','device_id':'admin-device','public_key':pub}
        self.auth.bootstrap({**statement,'proof':sign_object(key,statement),
                             'admin_name':'admin','bootstrap_secret':(self.root/'state'/'bootstrap.secret').read_text().strip()},loopback=True)
        self.gateway=SLHManagedGateway(self.auth,host='127.0.0.1',port=0)
        thread=self.gateway.start_background()
        self.addCleanup(self.gateway.stop)
        for _ in range(200):
            if self.gateway._server:break
            if not thread.is_alive():raise RuntimeError('gateway did not start')
            time.sleep(0.01)
        self.assertIsNotNone(self.gateway._server)
        self.private=PrivateRequests(self.root/'state',self.auth,self.gateway,execution_timeout=12)
        self.api=SLHAPI(self.auth,private_requests=self.private)

    def enroll(self,did,role):
        save_identity(self.identity_dir,did)
        key,pub=load_identity(self.identity_dir,did)
        statement={'selected_admin':'admin','device_id':did,'public_key':pub,
                   'kind':role,'device_name':did,'claimed_user':did,
                   'requested_role':role,'client_nonce':secrets.token_urlsafe(32)}
        signed={**statement,'version':1,'action':'slh.device.enrollment.request','org_id':'private-lab'}
        req=self.auth.enrollment_request({**statement,'proof':sign_object(key,signed)})
        self.auth.approve(req['request_id'],admin_id='admin')
        challenge=self.auth.challenge(did)
        return self.auth.verify_login({'device_id':did,'challenge_id':challenge['challenge_id'],
                                       'signature':sign_object(key,challenge)})['access_token']

    def worker(self,did='worker-1',executor=None):
        self.enroll(did,'worker')
        market={'rings':['ring-2'],'capabilities':['chat.completions'],
                'models':['mock-model'],'price':{'amount':'1','unit':'compute_credit'}}
        worker=ManagedSLHWorkerClient(device_id=did,identity_dir=self.identity_dir,
                     tls_fingerprint=self.gateway.fingerprint,
                     hub_url='https://127.0.0.1:'+str(self.gateway.port),
                     worker_id=did,auth_message={'type':'worker.auth','device_id':did,'market':market},
                     work_executor=executor or (lambda offer:{'status':'success','response':{'content': 'computed '+str(len(offer['work']['input']['prompt']))}}))
        worker.start()
        self.addCleanup(worker.close)
        return worker

    def req(self,key='x', prompt='five words'):
        return {'request_id':key,'model':'mock-model',
                'work':{'model':'mock-model','input':{'kind':'chat','prompt':prompt}}}

    def submit(self,token,b):return self.api.call('POST','/v1/work',b,token=token,loopback=False)
    def read(self,token,req):return self.api.call('GET','/v1/work/'+req,token=token,loopback=False)

    def test_real_private_worker_success_and_metadata_only_durable_replay(self):
        requester=self.enroll('req-1','requester')
        self.worker()
        status,result=self.submit(requester,self.req())
        self.assertEqual(status,200,result)
        self.assertEqual(result['status'],'SUCCEEDED')
        self.assertEqual(result['result']['response']['content'],'computed 10')
        self.assertEqual(result['worker_device_id'],'worker-1')
        self.assertEqual(len(result['result_hash']),64)
        st,reloaded=self.read(requester,'x')
        self.assertEqual(st,200)
        self.assertNotIn('result',reloaded)
        self.assertNotIn('five words',self.private.db_path.read_bytes().decode('latin1'))
        self.assertNotIn('computed 10',self.private.db_path.read_bytes().decode('latin1'))
        after=PrivateRequests(self.root/'state',self.auth,self.gateway)
        self.assertEqual(after.get({'role':'requester','device_id':'req-1'},'x')['status'],'SUCCEEDED')
        self.assertNotIn('result',self.submit(requester,self.req())[1])
        self.assertEqual(self.submit(requester,self.req(prompt='different'))[1]['error']['code'],'PRIVATE_REQUEST_ID_REUSED')
        self.assertEqual(self.gateway.scheduler.snapshot()[0]['reserved'],0)

    def test_cross_requester_access_denied_and_revoked_role(self):
        a=self.enroll('req-1','requester');b=self.enroll('req-2','requester')
        self.worker()
        self.assertEqual(self.submit(a,self.req())[1]['status'],'SUCCEEDED')
        self.assertEqual(self.read(b,'x')[0],404)
        self.assertEqual(self.read(b,'x')[1]['error']['code'],'PRIVATE_REQUEST_NOT_FOUND')
        self.assertEqual(self.submit(a,{**self.req(), 'device_id':'req-2'})[0],400)
        self.auth.revoke_device('req-1','admin')
        self.assertEqual(self.read(a,'x')[0],401)
        self.assertEqual(self.submit(a,self.req('new'))[0],401)

    def test_no_model_and_public_worker_collision(self):
        a=self.enroll('req-1','requester');self.worker()
        self.gateway.scheduler.mark_advertised('worker-1', True)
        self.assertEqual(self.submit(a,self.req())[1]['failure_code'],'NO_ELIGIBLE_WORKER')
        self.assertEqual(self.submit(a,self.req('different'))[1]['failure_code'],'NO_ELIGIBLE_WORKER')
        self.gateway.scheduler.mark_advertised('worker-1',False)
        bad=self.req('wrong');bad['model']='other-model';bad['work']['model']='other-model'
        self.assertEqual(self.submit(a,bad)[1]['failure_code'],'NO_ELIGIBLE_WORKER')

    def test_uncertain_execution_is_not_replayed(self):
        a=self.enroll('req-1','requester')
        fired=threading.Event();release=threading.Event();completed=threading.Event();count=[0]
        def computation(offer):
            count[0]+=1;fired.set();release.wait(6)
            completed.set()
            return {'status':'success','response':{'content':'done'}}
        self.worker(executor=computation)
        self.private.execution_timeout=.2
        _,response=self.submit(a,self.req())
        self.assertTrue(fired.is_set())
        self.assertEqual(response['status'],'OUTCOME_UNKNOWN')
        self.assertNotIn('result',response)
        self.assertEqual(self.submit(a,self.req())[1]['status'],'OUTCOME_UNKNOWN')
        self.assertEqual(count[0],1)
        release.set()
        self.assertTrue(completed.wait(3))
        self.assertEqual(PrivateRequests(self.root/'state',self.auth,self.gateway).get({'role':'requester','device_id':'req-1'},'x')['status'],'OUTCOME_UNKNOWN')

    def test_worker_terminal_failure_is_not_success(self):
        a=self.enroll('req-1','requester')
        def fail(offer):raise RuntimeError('private prompt should never enter error journal')
        self.worker(executor=fail)
        _,result=self.submit(a,self.req())
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['failure_code'],'WORKER_TERMINAL_FAILED')
        self.assertNotIn('private prompt',self.private.db_path.read_bytes().decode('latin1'))

    def test_role_validation_and_prohibited_echo(self):
        self.worker()
        w=self.enroll('worker-2','worker')
        self.assertEqual(self.submit(w,self.req())[0],403)
        a=self.enroll('req-1','requester')
        echo=self.req();echo['work']['input']={'kind':'echo','prompt':'sensitive'}
        self.assertEqual(self.submit(a,echo)[1]['error']['code'],'PRIVATE_ECHO_NOT_ALLOWED')
        self.assertEqual(self.api.call('POST','/v1/work',self.req(),token='')[0],401)
        self.assertEqual(self.read(a,'missing')[0],404)

    def test_concurrent_same_id_single_execution(self):
        a=self.enroll('req-1','requester');count=[0];lock=threading.Lock()
        def calculate(offer):
            with lock:count[0]+=1
            time.sleep(.1)
            return {'status':'success','response':{'content':'real result'}}
        self.worker(executor=calculate)
        responses=[]
        threads=[threading.Thread(target=lambda:responses.append(self.submit(a,self.req()))) for _ in range(5)]
        for t in threads:t.start()
        for t in threads:t.join(5)
        self.assertEqual(len(responses),5)
        self.assertEqual(count[0],1)
        self.assertEqual(self.read(a,'x')[1]['status'],'SUCCEEDED')

    def test_interrupted_dispatch_recovers_unknown(self):
        a=self.enroll('req-1','requester')
        with self.private._db() as db:
            db.execute('INSERT INTO private_requests (org_id,requester_device_id,request_id,request_digest,model,status,admitted_at_ns,updated_at_ns) VALUES (?,?,?,?,?,?,?,?)',
                       ('private-lab','req-1','lost','x','mock-model','DISPATCHED',1,1))
        recovered=PrivateRequests(self.root/'state',self.auth,self.gateway)
        self.assertEqual(recovered.get({'role':'requester','device_id':'req-1'},'lost')['status'],'OUTCOME_UNKNOWN')

if __name__=='__main__':unittest.main()
