"""Phase 2A versioned API: fully functional identity core, explicit service stubs.

This HTTP transport is deliberately LOOPBACK ONLY. Do not expose it over LAN,
proxy it through SEH, or rely on a bearer token over unencrypted remote HTTP.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import re
from typing import Any
from .authority import SLHAuthority, SLHError
from .control import SLHControlBackend
from .schemas import AUTH_REQUESTS, KNOWN_RESPONSES
from .ports import DEFAULT_SLH_API_ORIGIN
from .scheduler_policy import SchedulerPolicyStore


@dataclass(frozen=True)
class Endpoint:
    method: str
    path: str
    handler: str
    access: str = 'admin'
    state: str = 'implemented'


ENDPOINTS = (
    Endpoint('GET','/v1/bootstrap/status','bootstrap_status','public'),
    Endpoint('POST','/v1/bootstrap/admin','bootstrap_admin','local_bootstrap'),
    Endpoint('GET','/v1/capabilities','capabilities','public'),
    Endpoint('POST','/v1/bootstrap/recovery','stub','local_bootstrap','unimplemented'),
    Endpoint('GET','/v1/enrollment/admins','admins','public'),
    Endpoint('POST','/v1/enrollment/requests','request_enrollment','public'),
    Endpoint('GET','/v1/enrollment/requests/{id}/status','enrollment_status','public'),
    Endpoint('POST','/v1/enrollment/approvals','approve_enrollment','optional_key'),
    Endpoint('POST','/v1/auth/challenge','challenge','public'),
    Endpoint('POST','/v1/auth/verify','verify','public'),
    Endpoint('GET','/v1/auth/session','session','known'),
    Endpoint('POST','/v1/auth/logout','logout','known'),
    Endpoint('GET','/v1/admin/status','admin_status'),
    Endpoint('GET','/v1/admin/devices','devices'),
    Endpoint('GET','/v1/admin/devices/{id}','device'),
    Endpoint('PATCH','/v1/admin/devices/{id}','edit_device'),
    Endpoint('POST','/v1/admin/devices/{id}/revoke','revoke_device'),
    Endpoint('GET','/v1/admin/users','users'),
    Endpoint('GET','/v1/admin/enrollments','enrollments'),
    Endpoint('POST','/v1/admin/enrollments/{id}/keys','issue_key'),
    Endpoint('POST','/v1/admin/enrollments/{id}/inspect','inspect'),
    Endpoint('POST','/v1/admin/enrollments/{id}/deny','deny'),
    Endpoint('GET','/v1/admin/policies','get_policy'),
    Endpoint('PATCH','/v1/admin/policies','change_policy'),
    Endpoint('GET','/v1/admin/audit','audit'),
    Endpoint('GET','/v1/admin/scheduler/policy','scheduler_policy'),
    Endpoint('PATCH','/v1/admin/scheduler/policy','set_scheduler_policy'),
    Endpoint('GET','/v1/admin/scheduler/status','scheduler_status'),
    Endpoint('GET','/v1/connections','connections'),
    Endpoint('GET','/v1/connections/{id}/status','stub','admin','unimplemented'),
    Endpoint('GET','/v1/admin/seh/plan','stub','admin','unimplemented'),
    Endpoint('POST','/v1/admin/seh/deploy','stub','admin','unimplemented'),
    Endpoint('POST','/v1/admin/seh/start','stub','admin','unimplemented'),
    Endpoint('GET','/v1/seh/status','stub','admin','unimplemented'),
    Endpoint('POST','/v1/seh/pair','stub','known','unimplemented'),
    Endpoint('GET','/v1/seh/tunnels','stub','admin','unimplemented'),
    Endpoint('GET','/v1/workers','stub','known','unimplemented'),
    Endpoint('POST','/v1/workers/register','stub','known','unimplemented'),
    Endpoint('POST','/v1/workers/heartbeat','stub','known','unimplemented'),
    Endpoint('GET','/v1/requesters','stub','known','unimplemented'),
    Endpoint('POST','/v1/work','stub','known','unimplemented'),
    Endpoint('POST','/v1/routes','stub','known','unimplemented'),
    Endpoint('GET','/v1/marketplace','stub','admin','unimplemented'),
    Endpoint('POST','/v1/marketplace/workers','stub','admin','unimplemented'),
    Endpoint('GET','/v1/wallets','stub','admin','unimplemented'),
    Endpoint('GET','/v1/credits','stub','admin','unimplemented'),
    Endpoint('GET','/v1/accounting','stub','admin','unimplemented'),
    Endpoint('POST','/v1/accounting/reconcile','stub','admin','unimplemented'),
    Endpoint('POST','/v1/auth/people/challenge','stub','known','unimplemented'),
    Endpoint('POST','/v1/auth/people/verify','stub','known','unimplemented'),
)


def _match(pattern, path):
    expression='^'+re.sub(r'\{([a-z]+)\}',r'(?P<\1>[^/]+)',pattern)+'$'
    result=re.match(expression,path)
    return result.groupdict() if result else None


class SLHAPI:
    def __init__(self, authority: SLHAuthority, *, control=None):
        self.authority=authority
        self.control=control or SLHControlBackend()

    def openapi(self):
        paths={}
        for e in ENDPOINTS:
            op={
                'operationId':e.handler if e.handler!='stub' else (e.method+' '+e.path).replace('/','_'),
                'summary':e.path,'x-implementation':e.state,'x-required-access':e.access,
                'responses': {'200': {'description':'JSON result', 'content':{'application/json':{
                                     'schema': KNOWN_RESPONSES.get(e.handler,{'type':'object'})}}}
                             if e.state=='implemented' else {'description':'Not yet implemented'},
                              '401': {'description':'Authentication required'},
                              '403': {'description':'Permission refused'},
                              '501': {'description':'Not implemented'}},
            }
            if e.access in ('admin','known'):
                op['security']=[{'DeviceSession':[]}]
            elif e.access=='optional_key':
                op['description']='Either an admin bearer session or a single-use enrollment_key; same approval engine.'
            if e.method in ('POST','PATCH'):
                op['requestBody']={'content':{'application/json':{'schema':AUTH_REQUESTS.get(e.handler,{'type':'object'})}}}
            paths.setdefault(e.path,{})[e.method.lower()]=op
        return {'openapi':'3.1.0','info':{'title':'Main Computer SLH API','version':'2A.1'},
                'servers':[{'url':DEFAULT_SLH_API_ORIGIN,'description':'Loopback development only; no remote listener'}],
                'paths':paths, 'components':{'securitySchemes':{'DeviceSession':{'type':'http','scheme':'bearer'}}}}

    def call(self, method: str, path: str, body: dict | None = None, *, token='', loopback=False):
        if path=='/openapi.json' and method=='GET':
            return 200,self.openapi()
        body={} if body is None else body
        if not isinstance(body,dict):
            return 400,{'error':{'code':'INVALID_JSON_OBJECT'}}
        try:
            found=None
            for endpoint in ENDPOINTS:
                if endpoint.method==method:
                    vars=_match(endpoint.path,path)
                    if vars is not None:
                        found=(endpoint,vars)
                        break
            if not found:raise SLHError('ENDPOINT_NOT_FOUND',404)
            e,params=found
            identity=None
            if e.access=='local_bootstrap' and not loopback:
                raise SLHError('BOOTSTRAP_LOCAL_ONLY',403)
            if e.access=='admin':identity=self.authority.authenticate(token,admin=True)
            elif e.access=='known':identity=self.authority.authenticate(token)
            elif e.access=='optional_key' and not body.get('enrollment_key'):
                identity=self.authority.authenticate(token,admin=True)
            if e.state=='unimplemented':
                return 501,{'error':{'code':'NOT_IMPLEMENTED','operation':e.method+' '+e.path,'available':False}}
            result=self._invoke(e.handler,body,params,identity,token,loopback)
            status = 201 if e.handler in ('bootstrap_admin','request_enrollment','approve_enrollment','issue_key') else 200
            return status,result
        except SLHError as exc:
            return exc.status,{'error':{'code':exc.code}}
        except (ValueError,TypeError,KeyError):
            return 400,{'error':{'code':'INVALID_REQUEST'}}

    def _invoke(self,op,b,p,ident,token,loopback):
        a=self.authority
        actor=ident['user_id'] if ident else None
        if op=='bootstrap_status':return a.status()
        if op=='bootstrap_admin':return a.bootstrap(b,loopback=loopback)
        if op=='capabilities':return {'schema':'main-computer.slh-capabilities.v1','operations':[
            {'method':e.method,'path':e.path,'status':e.state,'access':e.access} for e in ENDPOINTS]}
        if op=='admins':return {'admins':a.admin_directory()}
        if op=='request_enrollment':return a.enrollment_request(b)
        if op=='enrollment_status':
            row=next((r for r in a.queue() if r['id']==p['id']),None)
            if not row:raise SLHError('ENROLLMENT_NOT_FOUND',404)
            return {'request_id':p['id'],'status':row['status']}
        if op=='approve_enrollment':return a.approve(b.get('request_id'),admin_id=actor,
                                                     enrollment_key=b.get('enrollment_key'))
        if op=='challenge':return a.challenge(b.get('device_id'))
        if op=='verify':return a.verify_login(b)
        if op=='session':return {'active':True,'user_id':actor,'device_id':ident['device_id'],'role':ident['role']}
        if op=='logout':return a.logout(token)
        if op=='admin_status':return {'organization':a.status(),'policies':a.policy()}
        if op=='devices':return {'devices':a.devices()}
        if op=='device':
            item=next((d for d in a.devices() if d['id']==p['id']),None)
            if not item:raise SLHError('DEVICE_NOT_FOUND',404)
            return item
        if op=='edit_device':return a.update_device(p['id'],b,actor)
        if op=='revoke_device':return a.revoke_device(p['id'],actor)
        if op=='users':return {'users':a.users()}
        if op=='enrollments':return {'enrollments':a.queue()}
        if op=='issue_key':return a.issue_key(p['id'],actor,ttl=b.get('ttl_seconds',300))
        if op=='inspect':return a.mark_inspected(p['id'],actor)
        if op=='deny':return a.deny(p['id'],actor)
        if op=='get_policy':return a.policy()
        if op=='change_policy':
            if 'physical_inspection_required' not in b:
                raise SLHError('INVALID_POLICY')
            return a.policy(b['physical_inspection_required'],actor)
        if op=='audit':return {'events':a.audit()}
        if op=='scheduler_policy':return SchedulerPolicyStore(a).get()
        if op=='set_scheduler_policy':
            return SchedulerPolicyStore(a).update(b.get('changes'),
                expected_revision=b.get('expected_revision'),actor=actor)
        if op=='scheduler_status':
            store=SchedulerPolicyStore(a)
            return {'policy':store.get(),'runtime':store.runtime_status(),
                    'credit_meter':'NOT_CONNECTED'}
        if op=='connections':return self.control.connection_catalog()
        raise SLHError('ENDPOINT_NOT_FOUND',404)

    def wsgi_app(self, environ, start_response):
        # Loopback-only server, no forwarded-address trust, no wildcard CORS.
        remote=environ.get('REMOTE_ADDR','')
        host=environ.get('HTTP_HOST','')
        if remote not in ('127.0.0.1','::1') or host.split(':')[0] not in ('127.0.0.1','localhost','[::1]'):
            status,result=403,{'error':{'code':'LOCAL_TRANSPORT_ONLY'}}
        elif environ.get('HTTP_ORIGIN'):
            status,result=403,{'error':{'code':'CROSS_ORIGIN_DENIED'}}
        else:
            method=environ.get('REQUEST_METHOD','GET')
            try:
                length=int(environ.get('CONTENT_LENGTH','0') or 0)
            except (ValueError, TypeError):
                length=-1
            if length>131072 or length<0:
                status,result=413,{'error':{'code':'BODY_TOO_LARGE'}}
            elif length and environ.get('CONTENT_TYPE','').split(';')[0].strip()!='application/json':
                status,result=415,{'error':{'code':'JSON_REQUIRED'}}
            else:
                try:
                    body=json.loads(environ['wsgi.input'].read(length)) if length else {}
                except (ValueError,UnicodeDecodeError):
                    body=None
                auth=environ.get('HTTP_AUTHORIZATION','')
                token=auth[7:] if auth.startswith('Bearer ') else ''
                status,result=self.call(method,environ.get('PATH_INFO',''),body,token=token,loopback=True)
        raw=json.dumps(result,sort_keys=True).encode('utf-8')
        reasons={200:'OK',201:'Created',400:'Bad Request',401:'Unauthorized',403:'Forbidden',404:'Not Found',409:'Conflict',413:'Content Too Large',415:'Unsupported Media Type',501:'Not Implemented'}
        start_response(f'{status} {reasons.get(status,"Error")}',
                       [('Content-Type','application/json; charset=utf-8'),('Content-Length',str(len(raw))),
                        ('Cache-Control','no-store'),('X-Content-Type-Options','nosniff')])
        return [raw]
