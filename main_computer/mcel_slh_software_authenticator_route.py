"""Local same-origin MCEL SLH authenticator RPC; never accepts host/URL/secret."""
from __future__ import annotations

from http import HTTPStatus
import ipaddress
import json

from .mcel_slh_software_authenticator import execute, SoftwareAuthenticatorError


def handle_software_authenticator(self):
    try:
        local=ipaddress.ip_address(self.client_address[0]).is_loopback
    except (IndexError, TypeError, ValueError):
        local=False
    host=(self.headers.get('Host') or '').lower()
    origin=(self.headers.get('Origin') or '').lower()
    port=int(self.server.server_address[1])
    if (not local or host not in (f'127.0.0.1:{port}',f'localhost:{port}') or
        origin!='http://'+host or self.headers.get('Sec-Fetch-Site','same-origin')!='same-origin' or
        self.headers.get('X-MCEL-SLH-Operation')!='software-authenticator' or
        self.headers.get('Content-Type','').split(';')[0].strip()!='application/json'):
        self._send_json({'error':{'code':'LOCAL_ORIGIN_REQUIRED'}},HTTPStatus.FORBIDDEN)
        return
    try:
        length=int(self.headers.get('Content-Length','-1'))
        if not 2<=length<=1024:
            raise SoftwareAuthenticatorError('INVALID_AUTHENTICATOR_REQUEST',400)
        data=json.loads(self.rfile.read(length))
        if not isinstance(data,dict) or set(data)!={'operation','deviceId'}:
            raise SoftwareAuthenticatorError('INVALID_AUTHENTICATOR_REQUEST',400)
        authorization=self.headers.get('Authorization','')
        token=authorization[7:] if authorization.startswith('Bearer ') else ''
        result=execute(data['operation'],data['deviceId'],token)
    except SoftwareAuthenticatorError as exc:
        self._send_json({'error':{'code':exc.code}},HTTPStatus(exc.status))
        return
    except (ValueError,TypeError,UnicodeError):
        self._send_json({'error':{'code':'INVALID_AUTHENTICATOR_REQUEST'}},HTTPStatus.BAD_REQUEST)
        return
    # Sensitive current-code responses must never be cached. A shared page's
    # generic JSON writer does not guarantee no-store, so write explicitly.
    raw=json.dumps({'ok':True,'result':result},separators=(',',':')).encode('utf-8')
    self.send_response(HTTPStatus.OK)
    self.send_header('Content-Type','application/json; charset=utf-8')
    self.send_header('Cache-Control','no-store, max-age=0')
    self.send_header('Pragma','no-cache')
    self.send_header('X-Content-Type-Options','nosniff')
    self.send_header('Content-Length',str(len(raw)))
    self.end_headers()
    self.wfile.write(raw)
