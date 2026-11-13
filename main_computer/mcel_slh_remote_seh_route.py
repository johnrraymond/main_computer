"""Explicit, pinned Remote SEH administrator session, never SLH lifecycle."""
from __future__ import annotations
from http import HTTPStatus
import ipaddress
import json


def handle_remote_seh_connect(self):
    from main_computer.mcel_seh_remote_client import RemoteSEHClient
    from main_computer.mcel_slh_network_client import NetworkConnectionError
    try: local=ipaddress.ip_address(self.client_address[0]).is_loopback
    except (ValueError, IndexError, TypeError): local=False
    host=self.headers.get('Host','').lower()
    port=int(self.server.server_address[1])
    if (not local or host not in (f'localhost:{port}',f'127.0.0.1:{port}') or
        self.headers.get('Origin','').lower()!='http://'+host or
        self.headers.get('Sec-Fetch-Site','same-origin')!='same-origin' or
        self.headers.get('X-MCEL-SLH-Operation')!='remote-seh-connect' or
        self.headers.get('Content-Type','').split(';')[0].strip()!='application/json'):
        self._send_json({'error':{'code':'LOCAL_ORIGIN_REQUIRED'}},HTTPStatus.FORBIDDEN)
        return
    try:
        length=int(self.headers.get('Content-Length','-1'))
        if not 2<=length<=1024:raise ValueError()
        data=json.loads(self.rfile.read(length))
        if not isinstance(data,dict) or set(data)!={'action','arguments'} or not isinstance(data['arguments'],dict):
            raise ValueError()
        action,args=data['action'],data['arguments']
        client=RemoteSEHClient()
        if action=='connect' and set(args) in ({'sehUrl','adminDeviceId'},
                                                  {'sehUrl','adminDeviceId','confirmPin'}):
            result=client.connect(args['sehUrl'],args['adminDeviceId'],confirm_pin=args.get('confirmPin'))
        elif action=='disconnect' and set(args)=={'sessionTicket'}:
            result=client.disconnect(args['sessionTicket'])
        else: raise ValueError()
    except NetworkConnectionError as exc:
        self._send_json({'error':{'code':exc.code}},exc.status)
        return
    except (ValueError,TypeError,UnicodeError):
        self._send_json({'error':{'code':'INVALID_REMOTE_SEH_REQUEST'}},HTTPStatus.BAD_REQUEST)
        return
    self._send_json(result)
