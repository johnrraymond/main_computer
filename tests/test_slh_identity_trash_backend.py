"""ORG-SLH enrollment notebook backend: persistent, authorized, lifecycle-safe.

Uses disposable organizations only; no resident services or user identities.
"""
import secrets
import threading
import time
from pathlib import Path

import pytest

from main_computer.slh.api import SLHAPI
from main_computer.slh.authority import SLHAuthority
from main_computer.slh.authenticator import generate_identity, sign_object
from main_computer.slh.organization_service import _trash_retention_loop
from tools.slh_seh_connector import service_identity


class Organization:
    def __init__(self, state):
        self.state=Path(state)
        service_identity(self.state, create=True)
        self.authority=SLHAuthority(self.state)
        self.api=SLHAPI(self.authority)
        self.admin_key,self.admin_public=generate_identity()
        secret=(self.state/'bootstrap.secret').read_text().strip()
        body={'admin_id':'admin-1','admin_name':'Administrator','device_id':'admin-device',
              'public_key':self.admin_public,'bootstrap_secret':secret}
        statement={k:body[k] for k in ('admin_id','device_id','public_key')}
        statement.update({'version':1,'action':'slh.admin.bootstrap'})
        body['proof']=sign_object(self.admin_key,statement)
        status,out=self.call('POST','/v1/bootstrap/admin',body,loopback=True)
        assert status==201,out
        self.admin_token=self.login('admin-device',self.admin_key)

    def call(self,method,path,body=None,*,token=None,loopback=False):
        return self.api.call(method,path,{} if body is None else body,
                             token=self.admin_token if token is None and hasattr(self,'admin_token') else token or '',
                             loopback=loopback)

    def login(self,device_id,key):
        status,challenge=self.call('POST','/v1/auth/challenge',{'device_id':device_id},token='')
        assert status==200,challenge
        status,out=self.call('POST','/v1/auth/verify',{
            'device_id':device_id,'challenge_id':challenge['challenge_id'],
            'signature':sign_object(key,challenge)},token='')
        assert status==200,out
        return out['access_token']

    def pending(self,device_id,role='worker'):
        key,pub=generate_identity()
        body={'selected_admin':'admin-1','device_id':device_id,'public_key':pub,
              'kind':'worker','device_name':'Lab worker','claimed_user':'Lab operator',
              'requested_role':role,'client_nonce':secrets.token_urlsafe(24)}
        stmt={**body,'version':1,'action':'slh.device.enrollment.request',
              'audience':'slh://'+self.authority.service_fingerprint()}
        body['proof']=sign_object(key,stmt)
        status,out=self.call('POST','/v1/enrollment/requests',body,token='')
        assert status==201,out
        return out['request_id'],key

    def enroll(self,device_id,role='worker'):
        rid,key=self.pending(device_id,role)
        status,out=self.call('POST','/v1/enrollment/approvals',{'request_id':rid})
        assert status==201,out
        return rid,key

    def error(self,method,path,body=None,token=None):
        status,out=self.call(method,path,body,token=token)
        assert status>=400,(status,out)
        return status,out['error']['code']


def test_three_lists_and_working_row_operations(tmp_path):
    org=Organization(tmp_path/'org')
    rid,key=org.pending('pending-device')
    assert [e['id'] for e in org.call('GET','/v1/admin/enrollments')[1]['enrollments']
            if e['status']=='pending']==[rid]
    assert org.call('GET','/v1/admin/devices/trash')[1]['devices']==[]
    assert org.call('POST','/v1/enrollment/approvals',{'request_id':rid})[0]==201
    token=org.login('pending-device',key)
    active=org.call('GET','/v1/admin/devices')[1]['devices']
    assert {'admin-device','pending-device'} <= {d['id'] for d in active if d['status']=='active'}
    code,revoked=org.call('POST','/v1/admin/devices/pending-device/revoke')
    assert code==200 and revoked['status']=='REVOKED' and isinstance(revoked['trashed_at'],int)
    assert org.call('GET','/v1/auth/session',token=token)[0]==401
    assert org.call('POST','/v1/auth/challenge',{'device_id':'pending-device'},token='')[0]==403
    trash=org.call('GET','/v1/admin/devices/trash')[1]['devices']
    assert len(trash)==1 and trash[0]['id']=='pending-device'
    assert trash[0]['trashed_at']==revoked['trashed_at']
    assert trash[0]['deleted_by']=='admin-1'
    assert org.call('POST','/v1/admin/devices/pending-device/reactivate')[1]['status']=='REACTIVATED'
    assert org.call('GET','/v1/admin/devices/trash')[1]['devices']==[]
    assert org.call('GET','/v1/auth/session',token=token)[0]==401
    org.login('pending-device',key)  # fresh proof, not a restored bearer
    assert org.call('POST','/v1/admin/devices/pending-device/revoke')[0]==200
    assert org.error('POST','/v1/admin/devices/pending-device/remove')==(400,'REMOVE_CONFIRMATION_REQUIRED')
    status,res=org.call('POST','/v1/admin/devices/pending-device/remove',{'confirm':'REMOVE_DEVICE'})
    assert status==200 and res['status']=='REMOVED'
    assert 'pending-device' not in {d['id'] for d in org.call('GET','/v1/admin/devices')[1]['devices']}
    assert org.call('GET','/v1/admin/devices/trash')[1]['devices']==[]
    # Removed device no longer has reusable enrollment material; audit still records actions.
    assert not any(x['device_id']=='pending-device' for x in org.authority.queue())
    actions=[e['action'] for e in org.call('GET','/v1/admin/audit')[1]['events']]
    assert {'enrollment.approved','device.revoked','device.reactivated','device.removed'} <= set(actions)
    org.pending('pending-device')  # fresh enrollment requires fresh administrator approval


def test_deny_stays_out_of_trash_and_last_admin_protected(tmp_path):
    org=Organization(tmp_path/'org')
    rid,_=org.pending('reject-me')
    assert org.call('POST',f'/v1/admin/enrollments/{rid}/deny')[1]['status']=='DENIED'
    assert org.call('GET','/v1/admin/devices/trash')[1]['devices']==[]
    assert org.error('POST','/v1/admin/devices/admin-device/revoke')==(409,'LAST_ADMIN_PROTECTED')
    assert org.error('POST','/v1/admin/devices/admin-device/reactivate')==(409,'DEVICE_NOT_IN_TRASH')


def test_trash_requires_admin_and_destructive_confirmation(tmp_path):
    org=Organization(tmp_path/'org')
    _,key=org.enroll('worker-device')
    worker_token=org.login('worker-device',key)
    paths=[('GET','/v1/admin/devices/trash',None),
           ('POST','/v1/admin/devices/worker-device/reactivate',None),
           ('POST','/v1/admin/devices/worker-device/remove',{'confirm':'REMOVE_DEVICE'}),
           ('POST','/v1/admin/devices/trash/empty',{'confirm':'EMPTY_TRASH'}),
           ('PATCH','/v1/admin/policies',{'remove_trash_after_30_days':True})]
    for method,path,body in paths:
        assert org.call(method,path,body,token='')[0]==401
        assert org.call(method,path,body,token=worker_token)[0]==403
    assert org.error('POST','/v1/admin/devices/trash/empty')==(400,'EMPTY_TRASH_CONFIRMATION_REQUIRED')
    assert org.error('PATCH','/v1/admin/policies',{'remove_trash_after_30_days':'true'})==(400,'INVALID_POLICY')
    assert org.error('PATCH','/v1/admin/policies',{'unexpected':True})==(400,'INVALID_POLICY')
    assert org.error('PATCH','/v1/admin/policies',{'remove_trash_after_30_days':None})==(400,'INVALID_POLICY')
    assert org.call('POST','/v1/admin/devices/worker-device/revoke')[0]==200
    assert org.error('POST','/v1/admin/devices/worker-device/reactivate',{'value':1})==(400,'INVALID_REACTIVATE_REQUEST')
    assert org.error('POST','/v1/admin/devices/worker-device/remove',{'confirm':'foo'})==(400,'REMOVE_CONFIRMATION_REQUIRED')
    assert org.call('POST','/v1/admin/devices/trash/empty',{'confirm':'EMPTY_TRASH'})[1]['removed_count']==1
    assert org.call('POST','/v1/admin/devices/trash/empty',{'confirm':'EMPTY_TRASH'})[1]['removed_count']==0
    assert org.call('GET','/v1/admin/devices/trash')[1]['devices']==[]


def test_retention_durable_30_day_boundary_and_browser_independence(tmp_path):
    org=Organization(tmp_path/'org')
    org.enroll('a');org.enroll('b')
    org.call('POST','/v1/admin/devices/a/revoke')
    org.call('POST','/v1/admin/devices/b/revoke')
    records=org.call('GET','/v1/admin/devices/trash')[1]['devices']
    ts=records[0]['trashed_at']
    assert org.authority.purge_expired_trash(now=ts+40*86400)['removed_count']==0  # default off
    status,policy=org.call('PATCH','/v1/admin/policies',{'remove_trash_after_30_days':True})
    assert status==200 and policy['physical_inspection_required'] is False
    assert policy['remove_trash_after_30_days'] is True
    restarted=SLHAuthority(org.state)
    assert restarted.policy()['remove_trash_after_30_days'] is True
    assert restarted.purge_expired_trash(now=ts+30*86400-1)['removed_count']==0
    # The resident sweeper uses this same durable authority without any page session.
    assert restarted.purge_expired_trash(now=ts+30*86400)['removed_count']==2
    assert restarted.trash()==[]
    assert {'admin-device'} == {d['id'] for d in restarted.devices()}
    assert org.call('GET','/v1/admin/audit')[1]['events'][0]['action']=='device.removed'


def test_expired_sweep_on_service_thread_with_no_browser(tmp_path):
    org=Organization(tmp_path/'org')
    org.enroll('old-device')
    org.call('POST','/v1/admin/devices/old-device/revoke')
    org.call('PATCH','/v1/admin/policies',{'remove_trash_after_30_days':True})
    with org.authority._lock,org.authority._db() as c:
        c.execute('UPDATE device_trash SET trashed_at=? WHERE device_id=?',
                  (int(time.time())-31*86400,'old-device'))
    class Status:
        trash_retention_last_error=None
        trash_retention_last_removed_count=0
    status=Status();stop=threading.Event()
    sweep=threading.Thread(target=_trash_retention_loop,
                            args=(SLHAuthority(org.state),stop,status),kwargs={'interval_seconds':.01})
    sweep.start()
    try:
        for _ in range(100):
            if status.trash_retention_last_removed_count==1:break
            time.sleep(.01)
        assert status.trash_retention_last_removed_count==1
        assert status.trash_retention_last_error is None
        assert org.authority.trash()==[]
    finally:
        stop.set();sweep.join(timeout=2)
    assert not sweep.is_alive()


def test_old_revoked_migration_starts_retention_at_upgrade_not_historical_time(tmp_path):
    org=Organization(tmp_path/'org')
    org.enroll('legacy-device')
    with org.authority._lock,org.authority._db() as c:
        c.execute("UPDATE devices SET status='revoked' WHERE id='legacy-device'")
    assert org.authority.trash()==[]
    migrated=SLHAuthority(org.state)
    old=migrated.trash()
    assert [r['id'] for r in old]==['legacy-device']
    assert old[0]['deleted_by']=='legacy-revocation'
    assert old[0]['trashed_at']>time.time()-10
    assert migrated.policy()['remove_trash_after_30_days'] is False


def test_two_organizations_do_not_share_trash_or_policy(tmp_path):
    first=Organization(tmp_path/'first')
    second=Organization(tmp_path/'second')
    first.enroll('first-only')
    first.call('POST','/v1/admin/devices/first-only/revoke')
    first.call('PATCH','/v1/admin/policies',{'remove_trash_after_30_days':True})
    assert [d['id'] for d in first.authority.trash()]==['first-only']
    assert second.authority.trash()==[]
    assert second.authority.policy()['remove_trash_after_30_days'] is False
    assert second.call('POST','/v1/admin/devices/first-only/reactivate')[0]==409
    # A bearer minted by another authority is never accepted.
    assert second.call('GET','/v1/admin/devices/trash',token=first.admin_token)[0]==401


def test_openapi_exposes_notebook_backend_contract(tmp_path):
    org=Organization(tmp_path/'org')
    paths=org.call('GET','/openapi.json',token='')[1]['paths']
    for method,path in [('get','/v1/admin/devices/trash'),
                        ('post','/v1/admin/devices/trash/empty'),
                        ('post','/v1/admin/devices/{id}/reactivate'),
                        ('post','/v1/admin/devices/{id}/remove'),
                        ('patch','/v1/admin/policies')]:
        assert paths[path][method]['x-required-access']=='admin'
        assert paths[path][method]['x-implementation']=='implemented'
    assert paths['/v1/admin/devices/{id}/remove']['post']['requestBody']['content']['application/json']['schema']['properties']['confirm']['const']=='REMOVE_DEVICE'
