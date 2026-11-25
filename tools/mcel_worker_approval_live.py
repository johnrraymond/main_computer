"""Real canonical Worker -> SLH Pending -> Accept -> signed login -> revocation.

Explicit opt-in creates one new ordinary LMS identity. Only that identity's
organization records may be changed. No Worker serving, marketplace or SEH
lifecycle changes. LMS has no identity deletion API, so the local key remains.
"""
from __future__ import annotations

import json
import secrets
from urllib.parse import urlsplit

from tools.mcel_functional_auth import ORIGIN, new_context
from tools.mcel_phase2b_browser import launch_semantic_browser
from tools.mcel_slh_live_readonly_frob import SLH_ROUTE, request_decision, _ANONYMOUS_INIT_SCRIPT, _discover_default_identity

ENROLLMENT = '/api/applications/worker/enrollment'
NOTEBOOK = '/api/applications/slh/identity-notebook'


def permitted(url, method, body_text, *, device, label, admin, requests):
    from tools.mcel_worker_identity_live_acceptance import allow_read
    p = urlsplit(url)
    if (p.scheme,p.netloc)!=('https',urlsplit(ORIGIN).netloc) or p.fragment or p.username or p.password:
        return False
    if method=='GET':
        return allow_read(url,method) or request_decision(ORIGIN,url,method,protected_reads=True).startswith('allow-')
    if method!='POST' or p.query:
        return False
    try:
        body=json.loads(body_text or '')
    except (ValueError,TypeError):
        return False
    if p.path==ENROLLMENT:
        return body in ({'action':action,'arguments':{'deviceId':device,'transport':'local','endpoint':''}}
                        for action in ('request','status'))
    if p.path==NOTEBOOK:
        if not isinstance(body,dict) or set(body)!={'target','operation','arguments','sessionTicket'} or body['target']!='local' or body['sessionTicket']!='':
            return False
        action=body['operation']; args=body['arguments']
        if action in ('accept','reject'):
            return isinstance(args,dict) and set(args)=={'recordId'} and isinstance(args['recordId'],str) and args['recordId'] in requests
        if action in ('delete','remove'):
            return args=={'recordId':device}
    if p.path=='/api/applications/slh/setup/action' and isinstance(body,dict) and body.get('action')=='authenticate':
        return admin is not None and body=={'action':'authenticate','arguments':{'adminDeviceId':admin}}
    return request_decision(ORIGIN,url,method,body_text,protected_reads=True).startswith('allow-')


def run_approval():
    from playwright.sync_api import sync_playwright
    from tools.mcel_worker_identity_live_acceptance import ProbeFailure, verify_canonical_surface, verify_enrollment_entry
    from main_computer.mcel_worker_enrollment import RID
    from main_computer.slh.ports import DEFAULT_SLH_API_PORT
    from tools.slh_authenticator_cli import _request as lms_request
    result={'schema':'mcel.worker.identity-live.v1','status':'UNVERIFIED','stage':'gateway-authentication',
            'scope':'canonical-worker-local-slh-disposable-approval','checks':{},'blockedRequests':0,
            'localTestIdentityRetained':False,'organizationCleanup':'NOT_REQUIRED'}
    checks=result['checks']
    device='worker-e2e-'+secrets.token_hex(12)
    label='Disposable Worker approval '+device
    admin=None; requests=set(); request_attempted=False
    notebook='[data-mcel-notebook="surface-node:slh.organization-identity-notebook"]'
    with sync_playwright() as p:
        browser,_=launch_semantic_browser(p)
        ctx=None
        try:
            ctx=new_context(browser,ORIGIN,service_workers='block')
            ctx.add_init_script(_ANONYMOUS_INIT_SCRIPT)
            def guard(route, req):
                if permitted(req.url,req.method,req.post_data,device=device,label=label,admin=admin,requests=requests):
                    route.continue_()
                else:
                    result['blockedRequests']+=1; route.abort()
            ctx.route('**/*',guard)
            ctx.route_web_socket('**/*',lambda ws:ws.close())
            # Establish real administrator access before creating anything.
            slh=ctx.new_page(); slh.set_default_timeout(20000)
            result['stage']='slh-administrator-discovery'
            response=slh.goto(ORIGIN+SLH_ROUTE,wait_until='domcontentloaded')
            if response is None or response.status!=200:raise ProbeFailure('slh-http-not-200')
            slh.wait_for_function("""() => {
              const app=window.__mcelSlh?.app, startup=window.__mcelSlh?.diagnostics?.()?.startup;
              return app?.readState('state:local-identity-status')?.state==='AVAILABLE' &&
                startup?.identityRefresh==='completed' && startup?.identityStatus==='completed' &&
                startup?.identityDirectory==='AVAILABLE' && startup?.lifecycleRefresh==='completed' &&
                app.readState('state:selected-target')==='local' && app.readState('state:organization-lifecycle')?.state==='running';
            }""",timeout=45000)
            admin=_discover_default_identity(slh)
            from tools.mcel_worker_identity_live_acceptance import ID
            if not isinstance(admin,str) or not ID.fullmatch(admin) or admin==device:raise ProbeFailure('lms-default-unverified')
            slh.locator(notebook+' [data-mcel-notebook-tab="pending"]').click()
            selection=slh.evaluate("async id => (await window.__mcelSlh.app.execute('intent:choose-identity',{key:id})).disposition",admin)
            if selection!='selected':raise ProbeFailure('admin-selection-failed')
            slh.wait_for_function("() => Array.isArray(window.__mcelSlh.app.readState('state:pending-enrollments'))",timeout=45000)
            checks['slhAdministratorVerified']=True
            # Accept only the two confirmation dialogs for this disposable run.
            def dialog(d):
                if d.type=='confirm' and (d.message.startswith('Confirm Request Enrollment') or
                    d.message.startswith('Confirm Request enrollment') or
                    (device in d.message and ('Revoke this enrolled identity' in d.message or 'Permanently remove this identity' in d.message))):
                    d.accept()
                else:d.dismiss()
            slh.on('dialog',dialog)
            worker=ctx.new_page(); worker.set_default_timeout(20000); worker.on('dialog',dialog)
            frame=verify_canonical_surface(worker,result)
            verify_enrollment_entry(json.loads(frame.locator('#mcel-generated-data').text_content())['ir'],result)
            result['stage']='disposable-lms-identity-create'
            # Fixture provisioning uses the existing LMS IPC, not the separate
            # viewport user-store API. All browser operations remain on HTTPS.
            result['disposableDeviceId']=device
            result['localTestIdentityRetained']='POSSIBLE'
            out=lms_request(DEFAULT_SLH_API_PORT,'POST','/v1/local/authenticator/identities',{'device_id':device,'label':label})
            if out.get('device_id')!=device or out.get('usable') is not True:raise ProbeFailure('disposable-identity-create-failed')
            result['localTestIdentityRetained']=True
            frame=verify_canonical_surface(worker,result)
            result['stage']='worker-select-disposable-identity'
            frame.wait_for_function("id => window.__mcelWorker?.app.readState('state:local-identities')?.some(r=>r.deviceId===id)",arg=device,timeout=45000)
            frame.locator('[data-mcel-node="surface-node:worker.worker-local-identities"] button[data-select-key="'+device+'"]').click()
            frame.wait_for_function("id => window.__mcelWorker.app.readState('state:selected-local-identity')===id",arg=device)
            checks['disposableIdentitySelected']=True
            def worker_action(action, expected):
                intent='request-enrollment' if action=='request' else 'observe-enrollment'
                with worker.expect_response(lambda r:urlsplit(r.url).path==ENROLLMENT and r.request.method=='POST',timeout=35000) as response:
                    frame.locator('form[data-intent="intent:'+intent+'"] button[type="submit"]').click()
                reply=response.value
                data=reply.json()
                if reply.status!=200 or data.get('ok') is not True:
                    reason=data.get('error','')
                    import re
                    if not isinstance(reason,str) or not re.fullmatch('[A-Z_]{3,80}',reason):reason='WORKER_ENROLLMENT_HTTP_FAILED'
                    raise ProbeFailure(reason)
                row=data.get('enrollment',{})
                if row.get('deviceId')!=device or row.get('status')!=expected or row.get('transport')!='local':raise ProbeFailure('worker-enrollment-state-unexpected')
                frame.wait_for_function("r => {const v=window.__mcelWorker.app.readState('state:worker-enrollment');return v?.deviceId===r.deviceId&&v?.status===r.status&&v?.requestId===r.requestId}",arg=row)
                return row
            def read_tab(tab):
                slh.locator(notebook+' [data-mcel-notebook-tab="'+tab+'"]').click()
                return slh.locator(notebook)
            def record(tab,key,action):
                root=read_tab(tab)
                return root.locator('[data-record-key="'+key+'"] [data-mcel-notebook-action="'+action+'"]')
            def notebook_action(button, operation, key, expected):
                def matching(r):
                    if urlsplit(r.url).path!=NOTEBOOK or r.request.method!='POST':return False
                    try:body=r.request.post_data_json
                    except Exception:return False
                    return body.get('operation')==operation and body.get('arguments')=={'recordId':key}
                with slh.expect_response(matching,timeout=25000) as response:
                    button.click()
                reply=response.value
                body=reply.json()
                if reply.status!=200 or body.get('ok') is not True or body.get('result',{}).get('outcome')!=expected:
                    raise ProbeFailure('slh-'+operation+'-receipt-unverified')
            try:
                result['stage']='worker-unapproved-access'
                worker_action('status','ACCESS_NOT_VERIFIED');checks['unapprovedAccessRefused']=True
                result['stage']='worker-enrollment-request'
                request_attempted=True
                pending=worker_action('request','PENDING_ADMIN')
                rid=pending.get('requestId','')
                if not RID.fullmatch(rid):raise ProbeFailure('invalid-request-reference')
                requests.add(rid);checks['workerSignedRequestPending']=True
                result['stage']='slh-pending-approval'
                accept=record('pending',rid,'Accept');accept.wait_for(state='visible',timeout=25000)
                checks['slhPendingRowObserved']=True
                notebook_action(accept,'accept',rid,'APPROVED')
                result['stage']='worker-approved-authentication'
                # Read the enrolled notebook row before testing applicant access.
                record('enrolled',device,'Delete').wait_for(state='visible',timeout=25000)
                checks['slhApprovalObserved']=True
                auth=worker_action('status','AUTHENTICATED')
                if auth.get('role')!='worker':raise ProbeFailure('approved-role-mismatch')
                checks['workerSignedAuthentication']=True
                result['stage']='slh-revocation'
                notebook_action(record('enrolled',device,'Delete'),'delete',device,'REVOKED')
                record('trash',device,'Remove').wait_for(state='visible',timeout=25000)
                worker_action('status','ACCESS_DENIED');checks['revokedWorkerAccessDenied']=True
                result['stage']='slh-disposable-cleanup'
                notebook_action(record('trash',device,'Remove'),'remove',device,'REMOVED')
                slh.locator(notebook+' [data-record-key="'+device+'"]').wait_for(state='detached',timeout=25000)
                result['organizationCleanup']='REMOVED';checks['organizationRecordRemoved']=True
                result['stage']='complete';result['status']='PASS'
            finally:
                if request_attempted and result['organizationCleanup']!='REMOVED':
                    # Never silently leave an approved test identity active. The
                    # exact ID and unique label are used, no bulk operation.
                    result['organizationCleanup']='NEEDS_VERIFICATION'
                    try:
                        for tab,action,key in [('enrolled','Delete',device),('trash','Remove',device)]:
                            b=record(tab,key,action)
                            try:b.wait_for(state='visible',timeout=2500)
                            except Exception:continue
                            b.click();b.wait_for(state='detached',timeout=10000)
                        root=read_tab('pending')
                        # Discover a request whose response was lost by its unique label.
                        row=root.locator('[data-mcel-record-row]').filter(has_text=label)
                        try:row.wait_for(state='visible',timeout=2500)
                        except Exception:pass
                        else:
                            rid=row.get_attribute('data-record-key')
                            if rid and RID.fullmatch(rid):
                                requests.add(rid);row.locator('[data-mcel-notebook-action="Reject"]').click()
                                row.wait_for(state='detached',timeout=10000)
                        result['organizationCleanup']='ATTEMPTED_REVIEW_REQUIRED'
                    except Exception:pass
        except Exception as exc:
            result['status']='UNVERIFIED';result['errorClass']=type(exc).__name__
            result['errorReason']=str(exc) if isinstance(exc,ProbeFailure) else result['stage']+'-operation-failed'
        finally:
            try:
                if ctx is not None:
                    try:ctx.close()
                    except Exception:
                        result['status']='UNVERIFIED';result['sessionCleanup']='UNVERIFIED'
                        result.setdefault('errorReason','disposable-gateway-session-revocation-unverified')
                    else:result['sessionCleanup']='REVOKED'
            finally:browser.close()
    return result
