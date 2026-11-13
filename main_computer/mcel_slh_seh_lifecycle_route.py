"""Same-origin, selected-target-only SEH lifecycle adapter for the MCEL viewport."""
from __future__ import annotations
from http import HTTPStatus
import ipaddress
import json
from main_computer.mcel_slh_seh_lifecycle import execute_seh_lifecycle, SEHLifecycleError


def handle_seh_lifecycle(self):
    try: local = ipaddress.ip_address(self.client_address[0]).is_loopback
    except (ValueError, IndexError, TypeError): local = False
    host = self.headers.get('Host','').lower()
    port = int(self.server.server_address[1])
    if (not local or host not in (f'localhost:{port}',f'127.0.0.1:{port}') or
        self.headers.get('Origin','').lower() != 'http://' + host or
        self.headers.get('Sec-Fetch-Site','same-origin') != 'same-origin' or
        self.headers.get('X-MCEL-SLH-Operation') != 'seh-lifecycle' or
        self.headers.get('Content-Type','').split(';')[0].strip() != 'application/json'):
        self._send_json({'error':{'code':'LOCAL_ORIGIN_REQUIRED'}}, HTTPStatus.FORBIDDEN)
        return
    try:
        length = int(self.headers.get('Content-Length','-1'))
        if not 2 <= length <= 1024: raise ValueError()
        data = json.loads(self.rfile.read(length))
        if (not isinstance(data,dict) or set(data) != {'target','operation','sessionTicket'} or
            not isinstance(data['sessionTicket'],str) or len(data['sessionTicket']) > 128):
            raise ValueError()
        target = data['target']
        authorization = self.headers.get('Authorization','')
        if target == 'local':
            if data['sessionTicket'] or not authorization.startswith('Bearer '):raise ValueError()
            result = execute_seh_lifecycle(data['operation'],target='local',local_token=authorization[7:])
        elif target == 'network':
            if authorization:raise ValueError()
            result = execute_seh_lifecycle(data['operation'],target='network',remote_ticket=data['sessionTicket'])
        else:
            raise SEHLifecycleError('SEH_LIFECYCLE_TARGET_REFUSED',403)
    except SEHLifecycleError as exc:
        self._send_json({'error':{'code':exc.code}},exc.status)
        return
    except (ValueError, TypeError, UnicodeError):
        self._send_json({'error':{'code':'INVALID_SEH_LIFECYCLE_REQUEST'}}, HTTPStatus.BAD_REQUEST)
        return
    self._send_json({'ok':True,'result':result})
