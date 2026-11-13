"""MCEL bounded SEH *connection* management, never SEH deployment authority.

Only the selected local or directly authenticated Network SLH may operate on
its outbound SEH connection. Remote SEH tickets are not admitted here.
"""
from __future__ import annotations

from main_computer.mcel_slh_worker_bridge import OrganizationWorkerClient, WorkerBridgeError, validate_admin_bearer
from main_computer.mcel_slh_network_client import NetworkSlhClient, NetworkConnectionError


class SEHLifecycleError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def execute_seh_lifecycle(operation, *, target, local_token='', remote_ticket='',
                          local_client=None, network_client=None):
    if target not in ('local', 'network') or operation not in ('status','start','stop','restart'):
        raise SEHLifecycleError('SEH_LIFECYCLE_OPERATION_REFUSED', 403)
    try:
        if target == 'local':
            if remote_ticket:
                raise SEHLifecycleError('SEH_LIFECYCLE_TARGET_MISMATCH', 403)
            token = validate_admin_bearer(local_token)
            result = (local_client or OrganizationWorkerClient()).request(
                'GET' if operation == 'status' else 'POST', '/v1/org/admin/seh/connection',
                token, None if operation == 'status' else {'action': operation})
        else:
            if local_token:
                raise SEHLifecycleError('SEH_LIFECYCLE_TARGET_MISMATCH', 403)
            result = (network_client or NetworkSlhClient()).seh_connection_request(
                remote_ticket, action=None if operation == 'status' else operation)
    except (WorkerBridgeError, NetworkConnectionError) as exc:
        raise SEHLifecycleError(exc.code, exc.status) from exc
    if (not isinstance(result, dict) or result.get('schema') != 'main-computer.slh-seh-connection.v1' or
        result.get('configuration_owner') != 'org-slh' or
        result.get('relay_role') != 'opaque-passthrough' or
        type(result.get('connection_enabled')) is not bool or
        type(result.get('resident_gateway_ready')) is not bool or
        type(result.get('connected')) is not bool or
        not isinstance(result.get('phase'), str) or len(result['phase']) > 100 or
        result.get('last_error_class') is not None and
        (not isinstance(result.get('last_error_class'), str) or len(result['last_error_class']) > 200)):
        raise SEHLifecycleError('SEH_LIFECYCLE_RESPONSE_INVALID', 502)
    return {'phase':result['phase'], 'connectionEnabled':result['connection_enabled'],
            'connected':result['connected'], 'gatewayReady':result['resident_gateway_ready'],
            'configurationOwner':'ORG-SLH', 'relayRole':'Opaque passthrough',
            'lastErrorClass':result.get('last_error_class') or ''}
