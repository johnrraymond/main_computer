"""Strict same-origin POST boundary for read-only remote SLH identity inspection."""
from __future__ import annotations

from http import HTTPStatus
import ipaddress
import json

from main_computer.mcel_slh_network_probe import NetworkSlhError, inspect_network_target


def handle_network_slh_inspect(self):
    try:
        client=ipaddress.ip_address(self.client_address[0])
        local=client.is_loopback
    except (ValueError, AttributeError, IndexError):
        local=False
    port=int(self.server.server_address[1])
    origin=f"http://127.0.0.1:{port}"
    if (not local or self.headers.get('Host')!=f"127.0.0.1:{port}" or
        self.headers.get('Origin')!=origin or
        self.headers.get('Sec-Fetch-Site', 'same-origin')!='same-origin'):
        self._send_json({'error':{'code':'LOCAL_ORIGIN_REQUIRED'}},HTTPStatus.FORBIDDEN)
        return
    if self.headers.get('Content-Type','').split(';')[0].strip()!='application/json':
        self._send_json({'error':{'code':'JSON_REQUIRED'}},HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        return
    try:
        length=int(self.headers.get('Content-Length','-1'))
    except ValueError:
        length=-1
    if not 2 <= length <= 1024:
        self._send_json({'error':{'code':'REQUEST_SIZE_INVALID'}},HTTPStatus.BAD_REQUEST)
        return
    try:
        data=json.loads(self.rfile.read(length))
    except (UnicodeDecodeError,ValueError):
        self._send_json({'error':{'code':'INVALID_JSON'}},HTTPStatus.BAD_REQUEST)
        return
    if not isinstance(data,dict) or set(data)!={'address','port','tlsSha256'}:
        self._send_json({'error':{'code':'TARGET_FIELDS_INVALID'}},HTTPStatus.BAD_REQUEST)
        return
    try:
        outcome=inspect_network_target(data['address'],data['port'],data['tlsSha256'])
    except NetworkSlhError as exc:
        status=HTTPStatus.FORBIDDEN if exc.code=='TLS_PIN_MISMATCH' else HTTPStatus.BAD_REQUEST
        self._send_json({'error':{'code':exc.code}},status)
        return
    self._send_json(outcome)


def handle_network_slh_connect(self):
    """Loopback/same-origin only, fixed ORG protocol; no arbitrary HTTP proxy."""
    from main_computer.mcel_slh_network_client import NetworkSlhClient, NetworkConnectionError
    try:
        client = ipaddress.ip_address(self.client_address[0])
        local = client.is_loopback
    except (ValueError, AttributeError, IndexError):
        local = False
    port = int(self.server.server_address[1])
    host = self.headers.get('Host', '')
    origin = self.headers.get('Origin', '')
    if (not local or host not in (f'127.0.0.1:{port}', f'localhost:{port}') or
            origin != f'http://{host}' or
            self.headers.get('Sec-Fetch-Site','same-origin') not in ('same-origin','') or
            self.headers.get('X-MCEL-SLH-Operation') != 'network-connect'):
        self._send_json({'error':{'code':'LOCAL_ORIGIN_REQUIRED'}},HTTPStatus.FORBIDDEN)
        return
    if self.headers.get('Content-Type','').split(';')[0].strip() != 'application/json':
        self._send_json({'error':{'code':'JSON_REQUIRED'}},HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        return
    try:
        length = int(self.headers.get('Content-Length','-1'))
    except ValueError:
        length = -1
    if not 2 <= length <= 1024:
        self._send_json({'error':{'code':'REQUEST_SIZE_INVALID'}},HTTPStatus.BAD_REQUEST)
        return
    try:
        data = json.loads(self.rfile.read(length))
    except (UnicodeDecodeError,ValueError):
        self._send_json({'error':{'code':'INVALID_JSON'}},HTTPStatus.BAD_REQUEST)
        return
    if not isinstance(data,dict) or set(data) != {'action','arguments'} or not isinstance(data['arguments'],dict):
        self._send_json({'error':{'code':'INVALID_NETWORK_REQUEST'}},HTTPStatus.BAD_REQUEST)
        return
    action,args = data['action'],data['arguments']
    try:
        client = NetworkSlhClient()
        if action == 'connect' and set(args) in ({'address','port','adminDeviceId'},
                                                  {'address','port','adminDeviceId','confirmPin'}):
            result = client.connect(args['address'],args['port'],args['adminDeviceId'],
                                    confirm_pin=args.get('confirmPin'))
        elif action == 'disconnect' and set(args) == {'sessionTicket'}:
            result = client.disconnect(args['sessionTicket'])
        else:
            raise NetworkConnectionError('INVALID_NETWORK_REQUEST')
    except NetworkConnectionError as exc:
        self._send_json({'error':{'code':exc.code}},exc.status)
        return
    self._send_json(result)
