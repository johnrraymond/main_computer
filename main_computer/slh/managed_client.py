"""Existing Worker live-session client adapted to SLH's walletless Known Device mode."""
from __future__ import annotations

from pathlib import Path
from collections import deque
import threading
import time
from main_computer.viewport_routes_energy import _WorkerHubLiveSessionClient
from main_computer.slh.authenticator import sign_object
from main_computer.slh.managed_gateway import MANAGED_PATH
from tools.slh_authenticator_cli import load_identity


class ManagedSLHWorkerClient(_WorkerHubLiveSessionClient):
    endpoint_path = MANAGED_PATH

    def __init__(self, *, device_id:str, identity_dir:Path, tls_fingerprint:str, **kwargs):
        if len(tls_fingerprint)!=64 or any(c not in '0123456789abcdef' for c in tls_fingerprint.lower()):
            raise ValueError('Managed SLH requires a SHA-256 pinned TLS certificate fingerprint')
        if not kwargs.get('hub_url','').startswith('https://'):
            raise ValueError('Managed SLH requires TLS')
        self.managed_slh_fingerprint=tls_fingerprint.lower()
        self.device_id=device_id
        self.identity_dir=Path(identity_dir)
        self.identity_key,self.public_key=load_identity(self.identity_dir,device_id)
        super().__init__(**kwargs)
        self._private_prepared = {}
        self._private_lock = threading.RLock()
        self.local_adapter_port = None
        self._transport_trace_lock = threading.Lock()
        self._transport_trace = deque(maxlen=48)
        # Do not transmit local filesystem paths or transport configuration.
        self.auth_message.pop('managed_slh', None)
        self.fingerprint+='|slh:'+self.managed_slh_fingerprint+'|device:'+device_id

    def _recv_json(self):
        frame=super()._recv_json()
        if frame.get('type')=='slh.auth.challenge':
            challenge=frame.get('challenge')
            if not isinstance(challenge,dict) or challenge.get('device_id')!=self.device_id:
                raise RuntimeError('SLH challenge does not match this managed device')
            self._send_json({'type':'slh.auth.proof','device_id':self.device_id,
                             'signature':sign_object(self.identity_key,challenge)})
            return super()._recv_json()
        return frame

    def _open_socket(self):
        sock = super()._open_socket()
        # The ephemeral loopback port matches getpeername() in the local SEH
        # adapter. It lets the lab correlate both sides without inspecting TLS.
        self.local_adapter_port = int(sock.getsockname()[1])
        self._record_transport_event('socket_connected')
        return sock

    def _record_transport_event(self, event, *, exc=None, close_code=None, **_ignored):
        """Bounded, metadata-only transport evidence; never record exception text.

        Private identities, TLS frames, tokens, messages and socket addresses
        must not enter diagnostics. errno and SSL reason are structured fields.
        """
        row = {'event': str(event), 'unix_ns': time.time_ns(),
               'monotonic_ns': time.monotonic_ns(),
               'local_port': self.local_adapter_port}
        if exc is not None:
            row['exception_class'] = type(exc).__name__
            if isinstance(getattr(exc, 'errno', None), int):
                row['errno'] = exc.errno
            ssl_reason = getattr(exc, 'reason', None)
            if isinstance(ssl_reason, str) and ssl_reason.isupper() and len(ssl_reason) <= 64:
                row['ssl_reason'] = ssl_reason
        if isinstance(close_code, int) and 1000 <= close_code <= 4999:
            row['websocket_close_code'] = close_code
        with self._transport_trace_lock:
            self._transport_trace.append(row)

    def transport_diagnostics(self):
        with self._transport_trace_lock:
            return list(self._transport_trace)

    def _handle_hub_message(self, frame):
        kind = frame.get('type')
        if kind == 'slh.private.work.offer':
            sid = str(frame.get('session_id') or '')
            if not sid or not frame.get('request_id') or not frame.get('run_id') or frame.get('protocol') != 'slh-private-v1':
                raise RuntimeError('Invalid SLH private offer')
            with self._private_lock:
                if sid in self._private_prepared:
                    raise RuntimeError('Duplicate private offer')
                self._private_prepared[sid] = dict(frame)
            self._send_json({'type':'worker.private.work.accepted', 'session_id':sid,
                             'request_id':frame['request_id'],'run_id':frame['run_id']})
            return
        if kind == 'slh.private.work.authorized':
            sid = str(frame.get('session_id') or '')
            with self._private_lock:
                pending = self._private_prepared.pop(sid, None)
            if (pending is None or frame.get('protocol')!='slh-private-v1' or
                    pending.get('request_id')!=frame.get('request_id') or
                    pending.get('run_id')!=frame.get('run_id') or
                    not isinstance(frame.get('work'),dict)):
                raise RuntimeError('Invalid SLH private authorization')
            threading.Thread(target=self._execute_private_work,
                             args=(dict(frame),),daemon=True,
                             name='slh-private-compute').start()
            return
        return super()._handle_hub_message(frame)

    def _execute_private_work(self, frame):
        # No public Hub offer/hold is created. Never publish private work
        # results as public worker.work.result or stream private deltas.
        try:
            result=self._execute_work_offer(frame)
            if not isinstance(result,dict) or result.get('debug_echo'):
                raise RuntimeError('Invalid or debug-only private computation')
            output={'type':'worker.private.work.result','result':result}
        except BaseException:
            output={'type':'worker.private.work.failed','error_code':'PRIVATE_WORKER_FAILED'}
        output.update({'session_id':frame['session_id'],
                       'request_id':frame['request_id'],'run_id':frame['run_id']})
        try:
            self._send_json(output)
        except (OSError,RuntimeError):
            # A disconnected Worker cannot turn a possibly executed request
            # into a retryable failure. SLH retains OUTCOME_UNKNOWN.
            pass
