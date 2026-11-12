"""Bounded Organization Identity notebook bridge: exact API operations only.

Every call targets either the locally pinned ORG-SLH or the selected, pinned
remote session. The viewport is a transport, never an administrator authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re

from main_computer.mcel_slh_worker_bridge import OrganizationWorkerClient, WorkerBridgeError, validate_admin_bearer
from main_computer.mcel_slh_network_client import NetworkSlhClient, NetworkConnectionError

_DEVICE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
_REQUEST = re.compile(r"[0-9a-f]{32}\Z")
_READS = {
    'read-pending':('GET','/v1/admin/enrollments'),
    'read-enrolled':('GET','/v1/admin/devices'),
    'read-trash':('GET','/v1/admin/devices/trash'),
    'read-retention':('GET','/v1/admin/policies'),
}
_ACTIONS = {
    'accept':('POST','/v1/enrollment/approvals'),
    'reject':('POST','/v1/admin/enrollments/{id}/deny'),
    'delete':('POST','/v1/admin/devices/{id}/revoke'),
    'reactivate':('POST','/v1/admin/devices/{id}/reactivate'),
    'remove':('POST','/v1/admin/devices/{id}/remove'),
    'empty-trash':('POST','/v1/admin/devices/trash/empty'),
    'set-retention':('PATCH','/v1/admin/policies'),
}

class IdentityNotebookError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code,self.status=code,status


def _date(value):
    if type(value) is not int or not 0 <= value <= 4102444800:
        raise IdentityNotebookError('INVALID_ORGANIZATION_RESPONSE',502)
    return datetime.fromtimestamp(value,timezone.utc).strftime('%b %d, %Y')


def _name(value):
    if not isinstance(value,str):
        raise IdentityNotebookError('INVALID_ORGANIZATION_RESPONSE',502)
    return value[:120]


def _records(operation,data):
    key='enrollments' if operation=='read-pending' else 'devices'
    values=data.get(key)
    if not isinstance(values,list) or len(values)>1000:
        raise IdentityNotebookError('INVALID_ORGANIZATION_RESPONSE',502)
    result=[]
    for row in values:
        if not isinstance(row,dict):
            raise IdentityNotebookError('INVALID_ORGANIZATION_RESPONSE',502)
        if operation=='read-pending' and row.get('status')!='pending':
            continue
        if operation=='read-enrolled' and row.get('status')!='active':
            continue
        if operation=='read-trash' and row.get('status')!='revoked':
            continue
        rid=row.get('id')
        if not isinstance(rid,str) or not (_REQUEST.fullmatch(rid) if operation=='read-pending' else _DEVICE.fullmatch(rid)):
            raise IdentityNotebookError('INVALID_ORGANIZATION_RESPONSE',502)
        if operation=='read-pending':
            result.append({'id':rid,'identity':_name(row.get('device_name') or row.get('device_id')),
                'role':_name(row.get('requested_role')),'requestedAt':_date(row.get('created_at'))})
        else:
            result.append({'id':rid,'identity':_name(row.get('name') or rid),
                'role':_name(row.get('role')),
                ('trashedAt' if operation=='read-trash' else 'createdAt'):
                    _date(row.get('trashed_at') if operation=='read-trash' else row.get('created_at'))})
    return result


def execute_notebook(operation, values, *, target, local_token='', remote_ticket='', local_client=None, remote_client=None):
    if not isinstance(values,dict) or target not in ('local','network'):
        raise IdentityNotebookError('INVALID_NOTEBOOK_REQUEST')
    if operation in _READS:
        if values:
            raise IdentityNotebookError('INVALID_NOTEBOOK_ARGUMENTS')
        method,path=_READS[operation]
        body=None
    elif operation in _ACTIONS:
        method,path=_ACTIONS[operation]
        if operation in ('empty-trash',):
            if values:raise IdentityNotebookError('INVALID_NOTEBOOK_ARGUMENTS')
            body={'confirm':'EMPTY_TRASH'}
        elif operation=='set-retention':
            if set(values)!={'enabled'} or type(values['enabled']) is not bool:
                raise IdentityNotebookError('INVALID_NOTEBOOK_ARGUMENTS')
            body={'remove_trash_after_30_days':values['enabled']}
        else:
            if set(values)!={'recordId'}:
                raise IdentityNotebookError('INVALID_NOTEBOOK_ARGUMENTS')
            identity=values['recordId']
            pattern=_REQUEST if operation in ('accept','reject') else _DEVICE
            if not isinstance(identity,str) or not pattern.fullmatch(identity):
                raise IdentityNotebookError('INVALID_RECORD_ID')
            path=path.replace('{id}',identity)
            body={'request_id':identity} if operation=='accept' else {'confirm':'REMOVE_DEVICE'} if operation=='remove' else {}
    else:
        raise IdentityNotebookError('UNKNOWN_NOTEBOOK_OPERATION')
    try:
        if target=='local':
            token=validate_admin_bearer(local_token)
            result=(local_client or OrganizationWorkerClient()).request(method,path,token,body)
        else:
            result=(remote_client or NetworkSlhClient()).admin_request(remote_ticket,method,path,body)
    except (WorkerBridgeError,NetworkConnectionError) as exc:
        raise IdentityNotebookError(exc.code,exc.status) from exc
    if not isinstance(result,dict):
        raise IdentityNotebookError('INVALID_ORGANIZATION_RESPONSE',502)
    if operation.startswith('read-') and operation!='read-retention':
        return _records(operation,result)
    if operation in ('read-retention','set-retention'):
        enabled=result.get('remove_trash_after_30_days')
        if type(enabled) is not bool:
            raise IdentityNotebookError('INVALID_ORGANIZATION_RESPONSE',502)
        return {'enabled':enabled}
    if not isinstance(result.get('status'),str) and operation!='empty-trash':
        raise IdentityNotebookError('INVALID_ORGANIZATION_RESPONSE',502)
    if operation=='empty-trash' and type(result.get('removed_count')) is not int:
        raise IdentityNotebookError('INVALID_ORGANIZATION_RESPONSE',502)
    return {'outcome':result.get('status','EMPTIED'),
            'message':('Permanently removed '+str(result['removed_count'])+' trashed records' if operation=='empty-trash'
                       else result['status'])}
