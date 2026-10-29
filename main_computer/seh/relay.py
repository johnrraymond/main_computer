"""Actual SEH WebSocket relay. Never terminates or possesses the SLH TLS identity.

The SEH accepts one authenticated SLH outbound control tunnel and multiple
untrusted client connections. Each client is a byte-stream tunneled through
its SLH, which terminates the client's pinned SLH TLS handshake. No remote
client identity is asserted by the SEH; Known Device checks run at the SLH.
"""
from __future__ import annotations

import argparse
import base64
from http import HTTPStatus
import json
import os
import secrets
import re
import threading
import time
from typing import Any

from main_computer.slh.authenticator import verify_object, load_public_key
from websockets.exceptions import ConnectionClosed

MAX_FRAME = 1024 * 1024
MAX_CLIENTS = 64
LAB_ID = re.compile(r"[a-f0-9]{32}\Z")
MAX_LAB_TUNNELS = 2


def _frame(obj: dict) -> str:
    return json.dumps(obj, separators=(',', ':'), sort_keys=True)


class SEHRelay:
    def __init__(self, *, organization_id: str, slh_id: str, slh_public_key: str,
                 host: str = '127.0.0.1', port: int = 8080, max_clients: int = MAX_CLIENTS):
        if not organization_id or '/' in organization_id or len(organization_id) > 128:
            raise ValueError('SEH organization ID invalid')
        if not slh_id or '/' in slh_id or len(slh_id) > 128:
            raise ValueError('SEH SLH ID invalid')
        load_public_key(slh_public_key)
        if not 1 <= max_clients <= MAX_CLIENTS:
            raise ValueError('SEH max_clients outside bounded range')
        self.organization_id = organization_id
        self.slh_id = slh_id
        self.slh_public_key = slh_public_key
        self.host = host
        self.port = port
        self.max_clients = max_clients
        self._lock = threading.RLock()
        self._slh = None
        self._send_lock = threading.RLock()
        self._clients: dict[str, Any] = {}
        self._lab_slhs: dict[str, Any] = {}
        self._lab_clients: dict[tuple[str, str], Any] = {}
        self._server = None
        self._thread = None
        self.bytes_forwarded = 0
        self.authenticated_slh_count = 0
        self.closed_streams = 0

    @property
    def tunnel_ready(self):
        with self._lock:
            return self._slh is not None

    def _send_slh(self, payload: dict, *, lab_id: str | None = None):
        with self._send_lock:
            with self._lock:
                target = self._lab_slhs.get(lab_id) if lab_id else self._slh
            if target is None:
                raise ConnectionError('No authenticated SLH tunnel')
            target.send(_frame(payload))

    def _handle_slh(self, ws, *, lab_id: str | None = None):
        nonce = secrets.token_urlsafe(32)
        statement = {'version': 1, 'action': 'seh.slh.connect',
                     'organization_id': self.organization_id,
                     'slh_id': self.slh_id, 'nonce': nonce}
        if lab_id is not None:
            statement['lab_id'] = lab_id
        ws.send(_frame({'type': 'seh.auth.challenge', 'statement': statement}))
        try:
            raw = ws.recv(timeout=6)
            result = json.loads(raw) if isinstance(raw, str) and len(raw) < 8192 else {}
            if result.get('type') != 'seh.auth.proof' or not verify_object(
                    self.slh_public_key, statement, result.get('signature', '')):
                ws.close(code=1008, reason='SLH_IDENTITY_REJECTED')
                return
        except (ValueError, TimeoutError, OSError):
            ws.close(code=1008, reason='SLH_IDENTITY_REJECTED')
            return
        with self._lock:
            if lab_id is None:
                if self._slh is not None:
                    ws.close(code=1008, reason='SLH_ALREADY_CONNECTED')
                    return
                self._slh = ws
            else:
                if lab_id in self._lab_slhs or len(self._lab_slhs) >= MAX_LAB_TUNNELS:
                    ws.close(code=1008, reason='LAB_TUNNEL_UNAVAILABLE')
                    return
                self._lab_slhs[lab_id] = ws
            self.authenticated_slh_count += 1
        ws.send(_frame({'type':'seh.auth.accepted', 'organization_id': self.organization_id}))
        try:
            for raw in ws:
                if not isinstance(raw, str):
                    break
                msg = json.loads(raw)
                sid = msg.get('id')
                if not isinstance(sid, str) or len(sid) > 80:
                    break
                with self._lock:
                    client = self._lab_clients.get((lab_id, sid)) if lab_id else self._clients.get(sid)
                if client is None:
                    continue
                op = msg.get('op')
                if op == 'data':
                    encoded = msg.get('data')
                    if not isinstance(encoded, str) or len(encoded) > MAX_FRAME * 2:
                        break
                    try:
                        value = base64.b64decode(encoded, validate=True)
                        if len(value) > MAX_FRAME:
                            break
                        client.send(value)
                        with self._lock:
                            self.bytes_forwarded += len(value)
                    except (OSError, ValueError, ConnectionClosed):
                        break
                elif op == 'close':
                    try: client.close(code=1000)
                    except OSError: pass
                else:
                    break
        except (OSError, ValueError, ConnectionClosed):
            pass
        finally:
            with self._lock:
                if lab_id is None and self._slh is ws:
                    self._slh = None
                    clients = list(self._clients.values())
                elif lab_id is not None and self._lab_slhs.get(lab_id) is ws:
                    self._lab_slhs.pop(lab_id, None)
                    clients = [client for (channel, _), client in self._lab_clients.items()
                               if channel == lab_id]
                else:
                    clients = []
            for client in clients:
                try:client.close(code=1012, reason='SLH_DISCONNECTED')
                except (OSError, ConnectionClosed):pass

    def _handle_client(self, ws, *, lab_id: str | None = None):
        with self._lock:
            tunnel = self._lab_slhs.get(lab_id) if lab_id else self._slh
            if tunnel is None or len(self._clients) + len(self._lab_clients) >= self.max_clients:
                ws.close(code=1013, reason='SLH_UNAVAILABLE_OR_BUSY')
                return
            sid = secrets.token_urlsafe(18)
            if lab_id:self._lab_clients[(lab_id, sid)] = ws
            else:self._clients[sid] = ws
        try:
            self._send_slh({'op':'open', 'id':sid}, lab_id=lab_id)
            for raw in ws:
                if not isinstance(raw, bytes) or len(raw) > MAX_FRAME:
                    break
                self._send_slh({'op':'data', 'id':sid, 'data':base64.b64encode(raw).decode('ascii')}, lab_id=lab_id)
                with self._lock:self.bytes_forwarded += len(raw)
        except (OSError, ConnectionError, ConnectionClosed):
            pass
        finally:
            with self._lock:
                if lab_id:self._lab_clients.pop((lab_id, sid), None)
                else:self._clients.pop(sid, None)
                self.closed_streams += 1
            try:self._send_slh({'op':'close', 'id':sid}, lab_id=lab_id)
            except (OSError, ConnectionError, ConnectionClosed):pass

    def handler(self, ws):
        path = ws.request.path
        if path == f'/v1/seh/slh/{self.organization_id}':
            return self._handle_slh(ws)
        if path == f'/v1/seh/client/{self.organization_id}':
            return self._handle_client(ws)
        # Separate disposable test channel: uses the SAME enrolled SLH public
        # key but never replaces or routes traffic into the production tunnel.
        parts = path.split('/')
        if (len(parts) == 8 and parts[:4] == ['', 'v1', 'seh', 'lab']
                and parts[4] == self.organization_id and parts[5] in ('slh', 'client')
                and parts[6] == 'id' and LAB_ID.fullmatch(parts[7])):
            if parts[5] == 'slh':
                return self._handle_slh(ws, lab_id=parts[7])
            return self._handle_client(ws, lab_id=parts[7])
        ws.close(code=1008, reason='UNKNOWN_ROUTE')

    def process_request(self, conn, request):
        if request.path == '/lab-capabilities':
            return conn.respond(HTTPStatus.OK, _frame({'service':'main_computer.seh',
                    'isolated_lab_channels':True, 'production_tunnel_untouched':True})+'\n')
        if request.path in ('/healthz', '/readyz'):
            ready = self.tunnel_ready
            body = _frame({'service':'main_computer.seh', 'live':True,
                           'slh_connected':ready, 'ready':ready}) + '\n'
            status = HTTPStatus.OK if request.path == '/healthz' or ready else HTTPStatus.SERVICE_UNAVAILABLE
            return conn.respond(status, body)
        return None

    def serve(self):
        from websockets.sync.server import serve
        with serve(self.handler, self.host, self.port, process_request=self.process_request,
                   compression=None, max_size=MAX_FRAME * 2 + 8192,
                   open_timeout=8, ping_interval=20) as server:
            self._server = server
            self.port = server.socket.getsockname()[1]
            server.serve_forever()

    def start(self, timeout=6):
        self._thread = threading.Thread(target=self.serve, daemon=True, name='seh-relay')
        self._thread.start()
        end = time.monotonic() + timeout
        while self._server is None and time.monotonic() < end:
            time.sleep(.02)
        if self._server is None:
            raise RuntimeError('SEH listener failed to bind')
        return self

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)


def main(argv=None):
    p = argparse.ArgumentParser(description='Main Computer SEH opaque relay service')
    p.add_argument('--host', default=os.getenv('SEH_HOST','0.0.0.0'))
    p.add_argument('--port', type=int, default=int(os.getenv('SEH_PORT','8080')))
    args = p.parse_args(argv)
    org = os.getenv('SEH_ORGANIZATION_ID','')
    slh = os.getenv('SEH_SLH_ID','')
    encoded = os.getenv('SEH_SLH_PUBLIC_KEY_B64','')
    if not org or not slh or not encoded:
        p.error('SEH_ORGANIZATION_ID, SEH_SLH_ID and SEH_SLH_PUBLIC_KEY_B64 are required')
    key = base64.b64decode(encoded, validate=True).decode('ascii')
    relay = SEHRelay(organization_id=org, slh_id=slh, slh_public_key=key,
                     host=args.host, port=args.port)
    print(_frame({'event':'seh_listening', 'port':args.port,'org':org}), flush=True)
    relay.serve()


if __name__ == '__main__':
    main()
