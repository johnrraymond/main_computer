"""Actual SEH WebSocket relay. Never terminates or possesses the SLH TLS identity.

The SEH accepts one authenticated SLH outbound control tunnel and multiple
untrusted client connections. Each client is a byte-stream tunneled through
its SLH, which terminates the client's pinned SLH TLS handshake. No remote
client identity is asserted by the SEH; Known Device checks run at the SLH.
"""
from __future__ import annotations

import argparse
from collections import deque
import hashlib
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
from .stream_writer import StreamWriter, SharedWriteBudget

MAX_FRAME = 1024 * 1024
MAX_CLIENTS = 64
MAX_PENDING_CLIENTS = 16
MAX_ADMIN_CLIENTS = 8
ADMIN_STREAM_DEADLINE_SECONDS = 90
PREAUTH_DEADLINE_SECONDS = 30
LAB_ID = re.compile(r"[a-f0-9]{32}\Z")
MAX_LAB_TUNNELS = 2
# Hard shared cap, not 64 * the independent 2 MiB per-stream allowance.
MAX_AGGREGATE_PENDING_BYTES = 8 * 1024 * 1024
MAX_WS_BUFFERED_MESSAGES = 4


def _frame(obj: dict) -> str:
    return json.dumps(obj, separators=(',', ':'), sort_keys=True)


class SEHRelay:
    def __init__(self, *, slh_public_key: str,
                 host: str = '127.0.0.1', port: int = 8080, max_clients: int = MAX_CLIENTS):
        load_public_key(slh_public_key)
        if not 1 <= max_clients <= MAX_CLIENTS:
            raise ValueError('SEH max_clients outside bounded range')
        self.slh_public_key = slh_public_key
        self.host = host
        self.port = port
        self.max_clients = max_clients
        self._lock = threading.RLock()
        self._slh = None
        self._send_lock = threading.RLock()
        self._clients: dict[str, Any] = {}
        self._admin_streams: set[str] = set()
        self._lab_slhs: dict[str, Any] = {}
        self._lab_clients: dict[tuple[str, str], Any] = {}
        # One bounded, nonrenewable timer per unauthenticated Worker stream.
        self._pending_clients: dict[tuple[str | None, str], threading.Timer] = {}
        self._server = None
        self._thread = None
        self.bytes_forwarded = 0
        self.authenticated_slh_count = 0
        self.closed_streams = 0
        # Close status counts only: no Worker IDs, tokens, or frame bodies.
        # Keep anomalous closes diagnosable when the shared tunnel survives.
        self.client_close_codes: dict[str, int] = {}
        # Classified SLH-origin stream closes; no private Worker identifiers.
        self.slh_close_causes: dict[str, int] = {}
        self._closing_clients: set[int] = set()
        # FIFO writes are isolated per Worker; one blocked send cannot stall
        # the shared authenticated SLH tunnel reader.
        self._client_writers: dict[tuple[str | None, str], StreamWriter] = {}
        self._client_write_budget = SharedWriteBudget(MAX_AGGREGATE_PENDING_BYTES)
        # Secret-free per-stream causal events. Stream IDs are hashed, never logged.
        self._stream_events = deque(maxlen=2048)

    def _stream_event(self, event, sid, *, ws=None, exc=None, cause=None):
        row = {'event': event, 'stream_tag': hashlib.sha256(sid.encode()).hexdigest()[:16],
               'monotonic_ns': time.monotonic_ns(), 'unix_ns': time.time_ns()}
        if ws is not None:
            # websockets.sync.connection.remote_address calls getpeername().
            # On Windows, querying a closed socket raises WinError 10038;
            # forensic telemetry must never crash the handler during cleanup.
            try:
                peer = getattr(ws, 'remote_address', None)
            except OSError:
                peer = None
            if isinstance(peer, tuple) and len(peer) > 1 and isinstance(peer[1], int):
                row['relay_peer_port'] = peer[1]
            protocol = getattr(ws, 'protocol', None)
            if protocol is not None:
                for attr, target in (('close_rcvd', 'received_close_code'),
                                     ('close_sent', 'sent_close_code')):
                    frame = getattr(protocol, attr, None)
                    code = getattr(frame, 'code', None)
                    if isinstance(code, int):
                        row[target] = code
                order = getattr(protocol, 'close_rcvd_then_sent', None)
                if isinstance(order, bool):
                    row['received_close_before_sent'] = order
        if exc is not None:
            row['exception_class'] = type(exc).__name__
            if isinstance(getattr(exc, 'errno', None), int):
                row['errno'] = exc.errno
        if cause in {'local_eof', 'local_read_error', 'write_error', 'write_queue_full',
                     'write_byte_budget', 'write_aggregate_budget', 'unspecified'}:
            row['cause'] = cause
        with self._lock:
            self._stream_events.append(row)

    def diagnostics_for_relay_ports(self, ports):
        selected = set(ports)
        with self._lock:
            tags={row['stream_tag'] for row in self._stream_events
                  if row.get('relay_peer_port') in selected}
            return [dict(row) for row in self._stream_events
                    if row['stream_tag'] in tags][-160:]

    def _schedule_client_close(self, client, *, code: int, reason: str = '') -> None:
        """Do not block the shared SLH tunnel on one WebSocket close handshake.

        websockets.sync.Connection.close() waits for the peer and TCP teardown.
        Closing many departing Workers serially in _handle_slh can stall *all*
        of the multiplexed Workers. There are at most max_clients live clients;
        deduplicate overlapping close requests to keep close workers bounded.
        """
        # A close requested by the relay must be distinguishable from a close
        # frame initiated by the Worker adapter.
        key = id(client)
        with self._lock:
            if key in self._closing_clients:
                return
            self._closing_clients.add(key)

        # Explicit relay close: the code is diagnostic metadata, never log reason text.
        # A socket may already be gone by the time the asynchronous close starts.
        def close_one():
            try:
                client.close(code=code, reason=reason)
            except (OSError, TimeoutError, ConnectionClosed, RuntimeError):
                pass
            finally:
                with self._lock:
                    self._closing_clients.discard(key)

        threading.Thread(target=close_one, name='seh-client-close', daemon=True).start()

    def _retire_pending(self, key):
        with self._lock:
            timer = self._pending_clients.pop(key, None)
        if timer is not None:
            timer.cancel()

    def _expire_pending(self, key):
        lab_id, sid = key
        with self._lock:
            if self._pending_clients.pop(key, None) is None:
                return
            client = (self._lab_clients.get((lab_id, sid)) if lab_id
                      else self._clients.get(sid))
        if client is not None:
            self._stream_event('relay_pre_auth_expired', sid, ws=client)
            self._schedule_client_close(client, code=1008, reason='AUTH_TIMEOUT')

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
        statement = {'version': 2, 'action': 'seh.slh.connect', 'nonce': nonce}
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
        ws.send(_frame({'type':'seh.auth.accepted'}))
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
                if op == 'authenticated':
                    # Only an authenticated SLH tunnel can attest to its own
                    # end-to-end TLS and managed device-proof success.
                    self._retire_pending((lab_id, sid))
                    continue
                if op == 'data':
                    encoded = msg.get('data')
                    if not isinstance(encoded, str) or len(encoded) > MAX_FRAME * 2:
                        # Bad data belongs to one stream, not the authenticated
                        # organization's entire Worker tunnel.
                        self._stream_event('relay_initiated_invalid_frame_close', sid, ws=client)
                        self._schedule_client_close(client, code=1008, reason='INVALID_STREAM_FRAME')
                        continue
                    try:
                        value = base64.b64decode(encoded, validate=True)
                        if len(value) > MAX_FRAME:
                            raise ValueError('oversized stream frame')
                    except (ValueError, base64.binascii.Error):
                        self._schedule_client_close(client, code=1008, reason='INVALID_STREAM_FRAME')
                        continue
                    with self._lock:
                        writer = self._client_writers.get((lab_id, sid))
                    if writer is not None and writer.offer(value):
                        pass
                    else:
                        self._stream_event('relay_initiated_backpressure_close', sid, ws=client)
                        # A queue-budget refusal is backpressure, not a
                        # generic internal failure. Report it as 1013 and
                        # disconnect only that stream.
                        cause = writer.failure_reason if writer is not None else None
                        code = 1013 if cause in {
                            'write_aggregate_budget', 'write_byte_budget', 'write_queue_full'
                        } else 1011
                        self._schedule_client_close(client, code=code, reason='STREAM_BACKPRESSURE')
                elif op == 'close':
                    cause = msg.get('cause')
                    if cause not in ('local_eof', 'local_read_error', 'write_error',
                                     'write_queue_full', 'write_byte_budget', 'write_aggregate_budget'):
                        cause = 'unspecified'
                    with self._lock:
                        self.slh_close_causes[cause] = self.slh_close_causes.get(cause, 0) + 1
                    # Do not report a local stream failure as an ordinary
                    # WebSocket closure. Preserve stream-scoped semantics.
                    code = 1013 if cause in ('write_queue_full', 'write_byte_budget', 'write_aggregate_budget') else (
                        1011 if cause in ('write_error', 'local_read_error') else 1000)
                    self._stream_event('relay_slh_requested_close', sid, ws=client, cause=cause)
                    self._schedule_client_close(client, code=code)
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
            with self._lock:
                keys = [key for key in self._pending_clients if key[0] == lab_id]
            for key in keys:
                self._retire_pending(key)
            for client in clients:
                self._schedule_client_close(client, code=1012, reason='SLH_DISCONNECTED')

    def _handle_client(self, ws, *, lab_id: str | None = None, purpose: str = 'worker'):
        if purpose not in ('worker', 'admin'):
            ws.close(code=1008, reason='UNKNOWN_STREAM_PURPOSE')
            return
        with self._lock:
            tunnel = self._lab_slhs.get(lab_id) if lab_id else self._slh
            if (tunnel is None or len(self._clients) + len(self._lab_clients) >= self.max_clients
                    or len(self._pending_clients) >= min(MAX_PENDING_CLIENTS, self.max_clients)
                    or (purpose == 'admin' and len(self._admin_streams) >= MAX_ADMIN_CLIENTS)):
                ws.close(code=1013, reason='SLH_UNAVAILABLE_OR_BUSY')
                return
            sid = secrets.token_urlsafe(18)
            pending_key = (lab_id, sid)
            # The SEH cannot inspect end-to-end ORG-SLH TLS or attest admin
            # login. Limit these unauthenticated transport streams by time and
            # count instead of pretending that the relay authenticated them.
            deadline = threading.Timer(ADMIN_STREAM_DEADLINE_SECONDS if purpose == 'admin'
                                       else PREAUTH_DEADLINE_SECONDS,
                                       self._expire_pending, args=(pending_key,))
            deadline.daemon = True
            self._pending_clients[pending_key] = deadline
            self._stream_event('relay_client_accepted', sid, ws=ws)
            if lab_id:self._lab_clients[(lab_id, sid)] = ws
            else:self._clients[sid] = ws
            if purpose == 'admin':
                self._admin_streams.add(sid)
            def send_client(data):
                ws.send(data)
                with self._lock:
                    self.bytes_forwarded += len(data)
            def failed_client_writer():
                writer = self._client_writers.get((lab_id, sid))
                cause = writer.failure_reason if writer is not None else None
                pressure = cause in {'write_aggregate_budget', 'write_byte_budget', 'write_queue_full'}
                self._schedule_client_close(ws, code=1013 if pressure else 1011,
                                            reason='STREAM_BACKPRESSURE' if pressure else 'STREAM_WRITE_FAILED')
            self._client_writers[(lab_id, sid)] = StreamWriter(
                send_client, failed_client_writer,
                name='seh-client-stream-writer', shared_budget=self._client_write_budget)
        deadline.start()
        try:
            self._send_slh({'op':'open', 'id':sid, 'purpose':purpose}, lab_id=lab_id)
            for raw in ws:
                if not isinstance(raw, bytes) or len(raw) > MAX_FRAME:
                    break
                self._send_slh({'op':'data', 'id':sid, 'data':base64.b64encode(raw).decode('ascii')}, lab_id=lab_id)
                with self._lock:self.bytes_forwarded += len(raw)
        except (OSError, ConnectionError, ConnectionClosed) as exc:
            self._stream_event('relay_client_receive_exception', sid, ws=ws, exc=exc)
        finally:
            self._stream_event('relay_client_handler_exiting', sid, ws=ws)
            # Client WebSocket close codes distinguish orderly Worker churn
            # from transport errors (1006), without logging credentials.
            try:
                code = ws.close_code
            except (AttributeError, RuntimeError):
                code = None
            label = str(code) if code is not None else 'unknown'
            self._retire_pending((lab_id, sid))
            with self._lock:
                writer = self._client_writers.pop((lab_id, sid), None)
                if writer is not None:
                    writer.close()
                if lab_id:self._lab_clients.pop((lab_id, sid), None)
                else:self._clients.pop(sid, None)
                self._admin_streams.discard(sid)
                self.closed_streams += 1
                self.client_close_codes[label] = self.client_close_codes.get(label, 0) + 1
            self._stream_event('relay_client_close_forwarded_to_slh', sid, ws=ws)
            try:self._send_slh({'op':'close', 'id':sid}, lab_id=lab_id)
            except (OSError, ConnectionError, ConnectionClosed):pass

    def handler(self, ws):
        path = ws.request.path
        if path == '/v1/seh/slh':
            return self._handle_slh(ws)
        if path == '/v1/seh/client':
            return self._handle_client(ws)
        if path == '/v1/seh/admin':
            return self._handle_client(ws, purpose='admin')
        # Separate disposable test channel: uses the SAME enrolled SLH public
        # key but never replaces or routes traffic into the production tunnel.
        parts = path.split('/')
        if (len(parts) == 7 and parts[:4] == ['', 'v1', 'seh', 'lab']
                and parts[4] in ('slh', 'client')
                and parts[5] == 'id' and LAB_ID.fullmatch(parts[6])):
            if parts[4] == 'slh':
                return self._handle_slh(ws, lab_id=parts[6])
            return self._handle_client(ws, lab_id=parts[6])
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
                   max_queue=MAX_WS_BUFFERED_MESSAGES,
                   open_timeout=8, ping_interval=20, close_timeout=2) as server:
            self._server = server
            self.port = server.socket.getsockname()[1]
            server.serve_forever()

    def start(self, timeout=6):
        self._thread = threading.Thread(target=self.serve, daemon=True, name='seh-relay')
        self._thread.start()
        end = time.monotonic() + timeout
        # The server object becomes visible just before the OS-assigned port.
        # Returning while port=0 makes clients dial the default WS port 80.
        while (self._server is None or self.port == 0) and time.monotonic() < end:
            time.sleep(.02)
        if self._server is None or self.port == 0:
            raise RuntimeError('SEH listener failed to bind')
        return self

    def stop(self):
        with self._lock:
            keys = list(self._pending_clients)
        for key in keys:
            self._retire_pending(key)
        if self._server is not None:
            self._server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)


def main(argv=None):
    p = argparse.ArgumentParser(description='Main Computer SEH opaque relay service')
    p.add_argument('--host', default=os.getenv('SEH_HOST','0.0.0.0'))
    p.add_argument('--port', type=int, default=int(os.getenv('SEH_PORT','8080')))
    args = p.parse_args(argv)
    encoded = os.getenv('SEH_SLH_PUBLIC_KEY_B64','')
    if not encoded:
        # Securely deployed but unbound. No SLH/client transport may be
        # accepted until a separate, authorized enrollment configures trust.
        from websockets.sync.server import serve
        def reject(ws):
            ws.close(code=1008, reason='SEH_NOT_BOUND')
        def health(conn, request):
            if request.path in ('/healthz', '/readyz'):
                is_health = request.path == '/healthz'
                return conn.respond(HTTPStatus.OK if is_health else HTTPStatus.SERVICE_UNAVAILABLE,
                                    _frame({'service':'main_computer.seh', 'live':True,
                                            'bound':False, 'ready':False}) + '\n')
            return None
        print(_frame({'event':'seh_listening_unbound', 'port':args.port,
                      'ready':False}), flush=True)
        with serve(reject, args.host, args.port, process_request=health,
                   compression=None, max_size=8192,
                   max_queue=MAX_WS_BUFFERED_MESSAGES) as server:
            server.serve_forever()
        return
    try:
        key = base64.b64decode(encoded, validate=True).decode('ascii')
    except (ValueError, UnicodeDecodeError):
        p.error('Invalid SEH_SLH_PUBLIC_KEY_B64')
    relay = SEHRelay(slh_public_key=key,
                     host=args.host, port=args.port)
    print(_frame({'event':'seh_listening', 'port':args.port}), flush=True)
    relay.serve()


if __name__ == '__main__':
    main()
