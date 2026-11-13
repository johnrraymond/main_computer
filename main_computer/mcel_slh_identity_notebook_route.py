"""Loopback and same-origin-only notebook RPC; no arbitrary URLs or tokens in responses."""
from __future__ import annotations
from http import HTTPStatus
import ipaddress
import json
import re
from main_computer.mcel_slh_identity_notebook import execute_notebook, IdentityNotebookError


def handle_identity_notebook(self):
    try: local=ipaddress.ip_address(self.client_address[0]).is_loopback
    except (ValueError,IndexError,TypeError): local=False
    host=self.headers.get('Host','').lower()
    origin=self.headers.get('Origin','').lower()
    port=int(self.server.server_address[1])
    if (not local or host not in (f'127.0.0.1:{port}',f'localhost:{port}') or
        origin!='http://'+host or self.headers.get('Sec-Fetch-Site','same-origin')!='same-origin' or
        self.headers.get('X-MCEL-SLH-Operation')!='identity-notebook' or
        self.headers.get('Content-Type','').split(';')[0].strip()!='application/json'):
        self._send_json({'error':{'code':'LOCAL_ORIGIN_REQUIRED'}},HTTPStatus.FORBIDDEN)
        return
    try:
        length=int(self.headers.get('Content-Length','-1'))
        if not 2<=length<=2048:raise IdentityNotebookError('INVALID_NOTEBOOK_REQUEST')
        data=json.loads(self.rfile.read(length))
        if not isinstance(data,dict) or set(data)!={'target','operation','arguments','sessionTicket'} or not isinstance(data['arguments'],dict):
            raise IdentityNotebookError('INVALID_NOTEBOOK_REQUEST')
        target=data['target']
        if target=='local':
            if data['sessionTicket']!='':raise IdentityNotebookError('INVALID_NOTEBOOK_REQUEST')
            authorization=self.headers.get('Authorization','')
            token=authorization[7:] if authorization.startswith('Bearer ') else ''
            result=execute_notebook(data['operation'],data['arguments'],target='local',local_token=token)
        elif target in ('network','remote-seh'):
            if self.headers.get('Authorization'):raise IdentityNotebookError('INVALID_NOTEBOOK_REQUEST')
            result=execute_notebook(data['operation'],data['arguments'],target=target,remote_ticket=data['sessionTicket'])
        else:raise IdentityNotebookError('INVALID_NOTEBOOK_REQUEST')
    except IdentityNotebookError as exc:
        self._send_json({'error':{'code':exc.code}},exc.status)
        return
    except (ValueError,TypeError,UnicodeError,json.JSONDecodeError):
        self._send_json({'error':{'code':'INVALID_NOTEBOOK_REQUEST'}},HTTPStatus.BAD_REQUEST)
        return
    self._send_json({'ok':True,'result':result})
