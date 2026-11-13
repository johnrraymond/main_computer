"""Bounded, same-origin MCEL transport for direct SLH-owned SEH deployment."""
from __future__ import annotations
from http import HTTPStatus
import ipaddress
import json

from main_computer.mcel_slh_seh_deployment import execute_seh_deployment, SEHDeploymentUIError


def handle_seh_deployment(self):
    try:
        local = ipaddress.ip_address(self.client_address[0]).is_loopback
    except (ValueError, IndexError, TypeError):
        local = False
    host = self.headers.get('Host', '').lower()
    port = int(self.server.server_address[1])
    if (not local or host not in (f'localhost:{port}', f'127.0.0.1:{port}') or
            self.headers.get('Origin', '').lower() != 'http://' + host or
            self.headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin' or
            self.headers.get('X-MCEL-SLH-Operation') != 'seh-deployment' or
            self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json'):
        self._send_json({'error': {'code': 'LOCAL_ORIGIN_REQUIRED'}}, HTTPStatus.FORBIDDEN)
        return
    try:
        length = int(self.headers.get('Content-Length', '-1'))
        if not 2 <= length <= 8192:
            raise ValueError()
        data = json.loads(self.rfile.read(length))
        if (type(data) is not dict or set(data) != {'target', 'operation', 'arguments', 'sessionTicket'} or
                type(data['arguments']) is not dict or type(data['sessionTicket']) is not str or
                len(data['sessionTicket']) > 128):
            raise ValueError()
        target = data['target']
        authorization = self.headers.get('Authorization','')
        if target == 'local':
            if data['sessionTicket'] or not authorization.startswith('Bearer '):
                raise SEHDeploymentUIError('SEH_DEPLOYMENT_TARGET_MISMATCH', 403)
            result = execute_seh_deployment(data['operation'],data['arguments'],target='local',
                                            local_token=authorization[7:])
        elif target == 'network':
            if authorization:
                raise SEHDeploymentUIError('SEH_DEPLOYMENT_TARGET_MISMATCH', 403)
            result = execute_seh_deployment(data['operation'],data['arguments'],target='network',
                                            remote_ticket=data['sessionTicket'])
        else:
            raise SEHDeploymentUIError('SEH_DEPLOYMENT_TARGET_REFUSED', 403)
    except SEHDeploymentUIError as exc:
        self._send_json({'error':{'code':exc.code}},exc.status)
        return
    except (ValueError, TypeError, UnicodeError):
        self._send_json({'error':{'code':'INVALID_SEH_DEPLOYMENT_REQUEST'}},HTTPStatus.BAD_REQUEST)
        return
    self._send_json({'ok':True,'result':result})
