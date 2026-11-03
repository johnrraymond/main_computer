"""SLH outbound SEH tunnel and local Worker TCP adapter.

The real SLH TLS handshake passes end-to-end through opaque SEH WebSocket frames.
Only the SLH controls the pinned private service key and authenticates workers.
"""
from __future__ import annotations

import base64
import hashlib
from collections import deque
import json
import secrets
import socket
import socketserver
import threading
import time
from urllib.parse import urlsplit

from main_computer.slh.authenticator import sign_object
from main_computer.seh.relay import MAX_FRAME, LAB_ID
from main_computer.seh.stream_writer import StreamWriter
from websockets.exceptions import ConnectionClosed


def _endpoint(uri: str, *, allow_lab: bool):
    u = urlsplit(uri)
    if u.username or u.password or u.query or u.fragment or not u.hostname:
        raise ValueError('Unsafe SEH URL')
    if u.scheme == 'ws' and not (allow_lab and u.hostname in {'127.0.0.1','localhost'}):
        raise ValueError('Unencrypted SEH is forbidden outside loopback lab')
    if u.scheme not in ('ws','wss'):
        raise ValueError('SEH URL requires WSS')
    return u


class SLHSEHConnector:
    """Authenticates using a dedicated SLH P-256 key, then tunnels to its local TLS service."""
    def __init__(self, *, seh_url: str, slh_id: str,
                 identity_private_key, gateway_port: int, allow_lab: bool=False,
                 lab_id: str | None = None):
        _endpoint(seh_url,allow_lab=allow_lab)
        if lab_id is not None and not LAB_ID.fullmatch(lab_id):
            raise ValueError('Invalid isolated SEH lab identifier')
        self.lab_id = lab_id
        self.seh_url = seh_url.rstrip('/')
        self.slh_id=slh_id
        self.identity_private_key=identity_private_key
        self.gateway_port=gateway_port
        self._socket=None
        self._thread=None
        self._send_lock=threading.RLock()
        self._streams={}
        self._stream_writers={}
        self._lock=threading.RLock()
        self._running=threading.Event()
        self.last_error=None
        self._stream_events = deque(maxlen=2048)

    def _stream_event(self, event, sid, *, local=None, exc=None, cause=None):
        row = {'event': event, 'stream_tag': hashlib.sha256(sid.encode()).hexdigest()[:16],
               'monotonic_ns': time.monotonic_ns(), 'unix_ns': time.time_ns()}
        if local is not None:
            try:
                row['gateway_peer_port'] = local.getsockname()[1]
            except OSError:
                pass
        if exc is not None:
            row['exception_class'] = type(exc).__name__
            if isinstance(getattr(exc, 'errno', None), int):
                row['errno'] = exc.errno
        if cause in {'local_eof','local_read_error','write_error','write_queue_full',
                     'write_byte_budget', 'seh_close_instruction', 'tunnel_ended',
                     'connector_stop'}:
            row['cause'] = cause
        with self._lock:
            self._stream_events.append(row)

    def diagnostics_for_gateway_ports(self, ports):
        selected = set(ports)
        with self._lock:
            tags={row['stream_tag'] for row in self._stream_events
                  if row.get('gateway_peer_port') in selected}
            return [dict(row) for row in self._stream_events
                    if row['stream_tag'] in tags][-160:]

    def _send(self, payload):
        with self._send_lock:
            if self._socket is None:
                raise ConnectionError('SEH tunnel not connected')
            self._socket.send(json.dumps(payload,separators=(',',':')))

    def _local_reader(self, sid, local):
        cause = 'local_eof'
        try:
            while self._running.is_set():
                data=local.recv(65536)
                if not data:
                    self._stream_event('slh_gateway_tcp_eof', sid, local=local)
                    break
                self._send({'op':'data','id':sid,'data':base64.b64encode(data).decode('ascii')})
        except (OSError, ConnectionError, ConnectionClosed) as exc:
            cause = 'local_read_error'
            self._stream_event('slh_gateway_read_or_tunnel_send_error', sid, local=local, exc=exc)
        finally:
            # Only the first closer emits a close frame. A simultaneous failed
            # write must not be mislabeled as an ordinary local EOF.
            self._stream_event('slh_gateway_stream_cleanup', sid, local=local, cause=cause)
            if self._close_stream(sid, initiator=cause):
                self._stream_event('slh_origin_close_instruction', sid, cause=cause)
                try:self._send({'op':'close','id':sid,'cause':cause})
                except (OSError,ConnectionError,ConnectionClosed):pass

    def _close_stream(self, sid, *, initiator="unspecified"):
        with self._lock:
            local=self._streams.pop(sid,None)
            writer=self._stream_writers.pop(sid,None)
        if writer is not None:
            writer.close()
        if local:
            self._stream_event("slh_local_socket_shutdown_invoked", sid, local=local, cause=initiator)
            try:local.shutdown(socket.SHUT_RDWR)
            except (OSError,ConnectionClosed) as exc:
                self._stream_event("slh_local_socket_shutdown_error", sid, local=local, exc=exc)
            try:local.close()
            except OSError as exc:
                self._stream_event("slh_local_socket_close_error", sid, local=local, exc=exc)
        return local is not None

    def _local_write_failed(self, sid):
        with self._lock:
            writer = self._stream_writers.get(sid)
            cause = (writer.failure_reason if writer is not None else None) or 'write_error'
        self._stream_event('slh_gateway_writer_failure', sid, cause=cause)
        if self._close_stream(sid, initiator=cause):
            self._stream_event("slh_writer_origin_close_instruction", sid, cause=cause)
            try:
                self._send({'op':'close','id':sid,'cause':cause})
            except (OSError, ConnectionError, ConnectionClosed):
                pass

    def _handle(self, payload):
        sid=payload.get('id')
        if not isinstance(sid,str) or len(sid)>80:
            raise RuntimeError('invalid stream ID')
        op=payload.get('op')
        if op=='open':
            with self._lock:
                duplicate = sid in self._streams
                full = len(self._streams) >= 64
            if duplicate:
                # A duplicate is a protocol error, not grounds to replace a
                # legitimate existing stream or tear down all Workers.
                return
            if full:
                # One incoming Worker is over capacity. Refuse only its
                # stream; an exception here previously killed the SLH-SEH
                # control tunnel and disconnected the entire organization.
                self._send({'op':'close','id':sid})
                return
            try:
                local=socket.create_connection(('127.0.0.1',self.gateway_port),timeout=5)
                local.settimeout(None)
            except OSError:
                self._send({'op':'close','id':sid})
                return
            with self._lock:
                self._streams[sid]=local
                self._stream_event('slh_gateway_stream_opened', sid, local=local)
                self._stream_writers[sid]=StreamWriter(
                    local.sendall,
                    lambda: self._local_write_failed(sid),
                    name='slh-local-stream-writer')
            threading.Thread(target=self._local_reader,args=(sid,local),daemon=True).start()
        elif op=='data':
            raw=payload.get('data')
            if not isinstance(raw,str) or len(raw)>MAX_FRAME*2:
                self._close_stream(sid)
                self._send({'op':'close','id':sid})
                return
            try:
                data=base64.b64decode(raw,validate=True)
            except (ValueError, base64.binascii.Error):
                self._close_stream(sid)
                self._send({'op':'close','id':sid})
                return
            if len(data)>MAX_FRAME:
                self._close_stream(sid)
                self._send({'op':'close','id':sid})
                return
            with self._lock:writer=self._stream_writers.get(sid)
            if writer is not None:
                # A rejected offer invokes the writer's stream-scoped failure
                # callback, which sends a classified close exactly once.
                writer.offer(data)
        elif op=='close':
            with self._lock:
                local = self._streams.get(sid)
            self._stream_event('slh_received_seh_close_instruction', sid, local=local)
            self._close_stream(sid, initiator='seh_close_instruction')
        else:
            raise RuntimeError('unsupported relay frame')

    def connect(self):
        from websockets.sync.client import connect
        path=(f'{self.seh_url}/v1/seh/lab/{self.slh_id}/slh/id/{self.lab_id}'
              if self.lab_id else f'{self.seh_url}/v1/seh/slh/{self.slh_id}')
        ws=connect(path,open_timeout=8,max_size=MAX_FRAME*2+8192,compression=None)
        try:
            request=json.loads(ws.recv(timeout=8))
            statement=request.get('statement') or {}
            if (request.get('type')!='seh.auth.challenge' or
                statement.get('slh_id')!=self.slh_id or
                statement.get('lab_id')!=self.lab_id or
                statement.get('action')!='seh.slh.connect' or
                not isinstance(statement.get('nonce'),str) or len(statement['nonce'])<32):
                raise RuntimeError('SEH challenge invalid')
            ws.send(json.dumps({'type':'seh.auth.proof','signature':sign_object(self.identity_private_key,statement)}))
            accepted=json.loads(ws.recv(timeout=8))
            if accepted.get('type')!='seh.auth.accepted':
                raise RuntimeError('SEH did not accept SLH identity')
        except BaseException:
            ws.close()
            raise
        self._socket=ws
        self._running.set()
        self._thread=threading.Thread(target=self._run,daemon=True,name='slh-seh-connector')
        self._thread.start()
        return self

    def _run(self):
        try:
            for raw in self._socket:
                if not isinstance(raw,str):raise RuntimeError('invalid SEH tunnel frame')
                self._handle(json.loads(raw))
        except (OSError,ConnectionError,ConnectionClosed,ValueError,RuntimeError) as exc:
            self.last_error=type(exc).__name__
        finally:
            self._running.clear()
            with self._lock:streams=list(self._streams)
            for sid in streams:self._close_stream(sid, initiator='tunnel_ended')

    def stop(self):
        self._running.clear()
        if self._socket:
            self._socket.close()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:streams=list(self._streams)
        for sid in streams:self._close_stream(sid, initiator='connector_stop')


class _LocalAdapterHandler(socketserver.BaseRequestHandler):
    def handle(self):
        adapter=self.server
        from websockets.sync.client import connect
        uri=(f'{adapter.seh_url}/v1/seh/lab/{adapter.slh_id}/client/id/{adapter.lab_id}'
             if adapter.lab_id else f'{adapter.seh_url}/v1/seh/client/{adapter.slh_id}')
        first_lock=threading.Lock()
        first=[None]
        # Port is the Worker side's ephemeral loopback port, and is NOT a
        # credential or payload identifier. It joins two local timelines.
        peer_port = int(self.request.getpeername()[1])

        def observe(event, *, exc=None, close_code=None):
            row = {'event': event, 'local_port': peer_port,
                   'unix_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns()}
            if exc is not None:
                row['exception_class'] = type(exc).__name__
                if isinstance(getattr(exc, 'errno', None), int):
                    row['errno'] = exc.errno
            if isinstance(close_code, int) and 1000 <= close_code <= 4999:
                row['websocket_close_code'] = close_code
            with adapter.stats_lock:
                adapter.connection_events.append(row)

        observe('adapter_tcp_accepted')
        with adapter.stats_lock:
            adapter.relay_peer_ports.pop(peer_port, None)

        def first_close(cause):
            # Record the first observed boundary event, not the downstream
            # side effects of that event. No Worker credentials or TLS bytes.
            with first_lock:
                if first[0] is not None:
                    return
                first[0]=cause
            observe('adapter_first_close_' + cause)
            with adapter.stats_lock:
                adapter.first_close_causes[cause] = adapter.first_close_causes.get(cause, 0) + 1

        try:
            with connect(uri,max_size=MAX_FRAME,compression=None,open_timeout=7) as remote:
                try:
                    relay_peer_port = int(remote.socket.getsockname()[1])
                except (OSError, AttributeError, ValueError):
                    relay_peer_port = None
                if relay_peer_port is not None:
                    with adapter.stats_lock:
                        adapter.relay_peer_ports[peer_port] = relay_peer_port
                    observe('adapter_relay_websocket_connected')
                stop=threading.Event()
                def upstream():
                    try:
                        while not stop.is_set():
                            chunk=self.request.recv(65536)
                            if not chunk:
                                observe('worker_tcp_recv_eof')
                                first_close('worker_tcp_eof_first')
                                break
                            remote.send(chunk)
                    except (OSError, ConnectionError, ConnectionClosed) as exc:
                        observe('worker_tcp_read_or_relay_send_exception', exc=exc)
                        first_close('worker_tcp_read_or_relay_send_error_first')
                    finally:
                        stop.set()
                        observe('adapter_initiating_websocket_close')
                        try:remote.close()
                        except (OSError,ConnectionClosed):pass
                reader=threading.Thread(target=upstream,daemon=True)
                reader.start()
                try:
                    for blob in remote:
                        if not isinstance(blob,bytes):
                            first_close('relay_nonbinary_first')
                            break
                        try:
                            self.request.sendall(blob)
                        except OSError as exc:
                            observe('worker_tcp_write_exception', exc=exc)
                            first_close('worker_tcp_write_error_first')
                            raise
                        with adapter.stats_lock:adapter.bytes_forwarded += len(blob)
                    code = getattr(remote, 'close_code', None)
                    observe('relay_websocket_ended', close_code=code)
                    first_close('relay_websocket_ended_first')
                except (OSError, ConnectionError, ConnectionClosed) as exc:
                    observe('relay_receive_or_worker_send_exception', exc=exc)
                    first_close('relay_receive_or_worker_send_error_first')
                finally:
                    stop.set()
                    observe('adapter_initiating_worker_tcp_shutdown')
                    try:self.request.shutdown(socket.SHUT_RDWR)
                    except (OSError,ConnectionClosed):pass
                    reader.join(timeout=2)
        except (OSError,ConnectionError,ConnectionClosed) as exc:
            observe('adapter_websocket_connect_exception', exc=exc)
            first_close('adapter_websocket_connect_error_first')
        finally:
            observe('adapter_handler_finished')


class SEHLocalClientAdapter(socketserver.ThreadingTCPServer):
    allow_reuse_address=False
    daemon_threads=True

    def __init__(self, seh_url: str, slh_id: str, *, allow_lab=False, lab_id=None):
        _endpoint(seh_url,allow_lab=allow_lab)
        if lab_id is not None and not LAB_ID.fullmatch(lab_id):
            raise ValueError('Invalid isolated SEH lab identifier')
        self.lab_id=lab_id
        self.slh_id=slh_id
        self.seh_url=seh_url.rstrip('/')
        self.stats_lock=threading.Lock()
        self.bytes_forwarded=0
        # Counts of first close observations, not inferred remote causes.
        self.first_close_causes={}
        self.connection_events=deque(maxlen=2048)
        self.relay_peer_ports={}
        super().__init__(('127.0.0.1',0),_LocalAdapterHandler)
        self.thread=threading.Thread(target=self.serve_forever,daemon=True)
        self.thread.start()

    def relay_ports_for_worker_ports(self, ports):
        selected=set(ports)
        with self.stats_lock:
            return {self.relay_peer_ports[k] for k in selected if k in self.relay_peer_ports}

    def diagnostics_for_ports(self, ports):
        selected = set(ports)
        with self.stats_lock:
            return [dict(row) for row in self.connection_events
                    if row.get('local_port') in selected]

    def stop(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=4)
