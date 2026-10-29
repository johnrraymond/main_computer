"""SLH outbound SEH tunnel and local Worker TCP adapter.

The real SLH TLS handshake passes end-to-end through opaque SEH WebSocket frames.
Only the SLH controls the pinned private service key and authenticates workers.
"""
from __future__ import annotations

import base64
import json
import secrets
import socket
import socketserver
import threading
import time
from urllib.parse import urlsplit

from main_computer.slh.authenticator import sign_object
from main_computer.seh.relay import MAX_FRAME, LAB_ID
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
    def __init__(self, *, seh_url: str, organization_id: str, slh_id: str,
                 identity_private_key, gateway_port: int, allow_lab: bool=False,
                 lab_id: str | None = None):
        _endpoint(seh_url,allow_lab=allow_lab)
        if lab_id is not None and not LAB_ID.fullmatch(lab_id):
            raise ValueError('Invalid isolated SEH lab identifier')
        self.lab_id = lab_id
        self.seh_url = seh_url.rstrip('/')
        self.organization_id=organization_id
        self.slh_id=slh_id
        self.identity_private_key=identity_private_key
        self.gateway_port=gateway_port
        self._socket=None
        self._thread=None
        self._send_lock=threading.RLock()
        self._streams={}
        self._lock=threading.RLock()
        self._running=threading.Event()
        self.last_error=None

    def _send(self, payload):
        with self._send_lock:
            if self._socket is None:
                raise ConnectionError('SEH tunnel not connected')
            self._socket.send(json.dumps(payload,separators=(',',':')))

    def _local_reader(self, sid, local):
        try:
            while self._running.is_set():
                data=local.recv(65536)
                if not data:break
                self._send({'op':'data','id':sid,'data':base64.b64encode(data).decode('ascii')})
        except (OSError, ConnectionError, ConnectionClosed):
            pass
        finally:
            self._close_stream(sid)
            try:self._send({'op':'close','id':sid})
            except (OSError,ConnectionError,ConnectionClosed):pass

    def _close_stream(self, sid):
        with self._lock:
            local=self._streams.pop(sid,None)
        if local:
            try:local.shutdown(socket.SHUT_RDWR)
            except (OSError,ConnectionClosed):pass
            local.close()

    def _handle(self, payload):
        sid=payload.get('id')
        if not isinstance(sid,str) or len(sid)>80:
            raise RuntimeError('invalid stream ID')
        op=payload.get('op')
        if op=='open':
            with self._lock:
                if sid in self._streams or len(self._streams)>=64:
                    raise RuntimeError('SEH stream capacity exhausted')
            local=socket.create_connection(('127.0.0.1',self.gateway_port),timeout=5)
            local.settimeout(None)
            with self._lock:self._streams[sid]=local
            threading.Thread(target=self._local_reader,args=(sid,local),daemon=True).start()
        elif op=='data':
            raw=payload.get('data')
            if not isinstance(raw,str) or len(raw)>MAX_FRAME*2:
                raise RuntimeError('Invalid opaque payload')
            data=base64.b64decode(raw,validate=True)
            if len(data)>MAX_FRAME:raise RuntimeError('Oversized opaque payload')
            with self._lock:local=self._streams.get(sid)
            if local:local.sendall(data)
        elif op=='close':
            self._close_stream(sid)
        else:
            raise RuntimeError('unsupported relay frame')

    def connect(self):
        from websockets.sync.client import connect
        path=(f'{self.seh_url}/v1/seh/lab/{self.organization_id}/slh/id/{self.lab_id}'
              if self.lab_id else f'{self.seh_url}/v1/seh/slh/{self.organization_id}')
        ws=connect(path,open_timeout=8,max_size=MAX_FRAME*2+8192,compression=None)
        try:
            request=json.loads(ws.recv(timeout=8))
            statement=request.get('statement') or {}
            if (request.get('type')!='seh.auth.challenge' or
                statement.get('organization_id')!=self.organization_id or
                statement.get('slh_id')!=self.slh_id or
                statement.get('lab_id')!=self.lab_id or
                statement.get('action')!='seh.slh.connect' or
                not isinstance(statement.get('nonce'),str) or len(statement['nonce'])<32):
                raise RuntimeError('SEH challenge invalid')
            ws.send(json.dumps({'type':'seh.auth.proof','signature':sign_object(self.identity_private_key,statement)}))
            accepted=json.loads(ws.recv(timeout=8))
            if accepted.get('type')!='seh.auth.accepted' or accepted.get('organization_id')!=self.organization_id:
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
            for sid in streams:self._close_stream(sid)

    def stop(self):
        self._running.clear()
        if self._socket:
            self._socket.close()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:streams=list(self._streams)
        for sid in streams:self._close_stream(sid)


class _LocalAdapterHandler(socketserver.BaseRequestHandler):
    def handle(self):
        adapter=self.server
        from websockets.sync.client import connect
        uri=(f'{adapter.seh_url}/v1/seh/lab/{adapter.organization_id}/client/id/{adapter.lab_id}'
             if adapter.lab_id else f'{adapter.seh_url}/v1/seh/client/{adapter.organization_id}')
        try:
            with connect(uri,max_size=MAX_FRAME,compression=None,open_timeout=7) as remote:
                stop=threading.Event()
                def upstream():
                    try:
                        while not stop.is_set():
                            chunk=self.request.recv(65536)
                            if not chunk:break
                            remote.send(chunk)
                    except (OSError,ConnectionError,ConnectionClosed):pass
                    finally:
                        stop.set()
                        try:remote.close()
                        except (OSError,ConnectionClosed):pass
                reader=threading.Thread(target=upstream,daemon=True)
                reader.start()
                for blob in remote:
                    if not isinstance(blob,bytes):break
                    self.request.sendall(blob)
                    with adapter.stats_lock:adapter.bytes_forwarded += len(blob)
                stop.set()
                try:self.request.shutdown(socket.SHUT_RDWR)
                except (OSError,ConnectionClosed):pass
                reader.join(timeout=2)
        except (OSError,ConnectionError,ConnectionClosed):pass


class SEHLocalClientAdapter(socketserver.ThreadingTCPServer):
    allow_reuse_address=False
    daemon_threads=True

    def __init__(self, seh_url: str, organization_id: str, *, allow_lab=False, lab_id=None):
        _endpoint(seh_url,allow_lab=allow_lab)
        if lab_id is not None and not LAB_ID.fullmatch(lab_id):
            raise ValueError('Invalid isolated SEH lab identifier')
        self.lab_id=lab_id
        self.seh_url=seh_url.rstrip('/')
        self.organization_id=organization_id
        self.stats_lock=threading.Lock()
        self.bytes_forwarded=0
        super().__init__(('127.0.0.1',0),_LocalAdapterHandler)
        self.thread=threading.Thread(target=self.serve_forever,daemon=True)
        self.thread.start()

    def stop(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=4)
