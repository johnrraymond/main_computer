"""SLH managed-worker gateway: enrolled devices, TLS-pinned LAN socket, shared-wallet Hub proxy.

The SLH owns the marketplace MSK. Managed workers never receive it. Only the
public Stable Hub can commit credit holds; local workers execute after an
upstream hub.work.authorized. TLS here uses one pinned leaf, NOT an organization CA.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import os
import ssl
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from main_computer.model_execution_contract import (
    advertised_models, advertised_operations, request_contract, market_supports,
    verify_execution_result, ModelContractError,
)
from .authority import SLHAuthority, SLHError
from .ports import DEFAULT_SLH_MANAGED_PORT
from .scheduler_policy import SchedulerPolicyStore, compute_plan
from .storage import secure_private_state_file

MANAGED_PATH = '/v1/managed/workers/live-session'
DEFAULT_MANAGED_PORT = DEFAULT_SLH_MANAGED_PORT


def transport_identity(directory: Path) -> tuple[ssl.SSLContext, str]:
    """Create a persistent TLS transport *leaf*, not a CA or device cert hierarchy."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    key_path = directory / 'managed-tls-key.pem'
    cert_path = directory / 'managed-tls-cert.pem'
    if key_path.exists() != cert_path.exists():
        raise RuntimeError('Incomplete SLH TLS identity; restore the matching key/certificate pair')
    created = not key_path.exists()
    if created:
        key = ec.generate_private_key(ec.SECP256R1())
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Main Computer SLH Managed LAN')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(minutes=5))
                .not_valid_after(now+timedelta(days=365))
                .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
                .sign(key,hashes.SHA256()))
        # Store key only on the SLH host. Keep out of SEH and the worker.
        fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd,'wb') as f:
            f.write(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        fd = os.open(cert_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd,'wb') as f:f.write(cert.public_bytes(serialization.Encoding.PEM))
    # The private TLS leaf must be restricted even on Windows, where 0o600
    # alone does not secure an NTFS ACL. Never start the gateway on failure.
    try:
        secure_private_state_file(key_path)
    except BaseException:
        if created:
            key_path.unlink(missing_ok=True)
            cert_path.unlink(missing_ok=True)
        raise
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    if cert.not_valid_after_utc <= datetime.now(timezone.utc):
        raise RuntimeError('SLH TLS leaf expired; rotate explicitly and redistribute its fingerprint')
    fingerprint = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert_path),str(key_path))
    return ctx, fingerprint


@dataclass
class ManagedWorker:
    device_id: str
    socket: Any
    market: dict
    models: list[str]
    send_lock: threading.RLock = field(default_factory=threading.RLock)
    condition: threading.Condition = field(default_factory=lambda:threading.Condition(threading.RLock()))
    pending: dict[str,dict] = field(default_factory=dict)
    reservations: set[str] = field(default_factory=set)
    active: bool = True
    audit: Any = None
    generation: int = 0

    def send(self, frame: dict):
        with self.send_lock:
            if not self.active:raise ConnectionError('managed worker disconnected')
            try:
                self.socket.send(json.dumps(frame,separators=(',',':')))
                if callable(self.audit) and frame.get('type') in {
                        'hub.work.offer', 'hub.work.authorized'}:
                    self.audit('gateway_paid_offer_sent' if frame['type'] == 'hub.work.offer'
                               else 'gateway_paid_authorization_sent')
            except Exception as exc:
                if callable(self.audit):
                    self.audit('gateway_worker_send_failed', exc=exc)
                raise

    def receive(self, frame: dict):
        session = str(frame.get('session_id') or '')
        with self.condition:
            if session in self.pending:
                self.pending[session] = dict(frame)
                self.condition.notify_all()

    def await_message(self, session: str, kinds: set[str], timeout: float) -> dict:
        deadline=time.monotonic()+timeout
        with self.condition:
            while self.active:
                frame=self.pending.get(session)
                if isinstance(frame,dict):
                    self.pending[session]=None
                    if frame.get('type') not in kinds:
                        raise RuntimeError('unexpected managed worker response')
                    return frame
                remaining=deadline-time.monotonic()
                if remaining <= 0:raise TimeoutError('managed worker response timeout')
                self.condition.wait(remaining)
            raise ConnectionError('managed worker disconnected')


class ManagedScheduler:
    def __init__(self, is_authorized=None, *, policy_store=None, credit_provider=None):
        self.is_authorized=is_authorized or (lambda device_id: True)
        self.policy_store=policy_store
        self.credit_provider=credit_provider
        self._withdrawals:set[str]=set()
        # A local request owns its selected Worker across asynchronous Hub
        # withdrawal until it atomically reserves the now-private Worker.
        self._private_claims:set[str]=set()
        self._generation=0
        self._advertised:set[str]=set()
        self._reservation_kind:dict[str,str]={}
        self._lock=threading.RLock()
        self._workers:dict[str,ManagedWorker]={}
        self._sessions:dict[str,ManagedWorker]={}

    def register(self, worker: ManagedWorker):
        with self._lock:
            existing=self._workers.get(worker.device_id)
            if existing and existing.active:raise RuntimeError('device already connected')
            self._generation+=1
            worker.generation=self._generation
            self._workers[worker.device_id]=worker

    def unregister(self, worker: ManagedWorker):
        with self._lock:
            if self._workers.get(worker.device_id) is worker:
                self._workers.pop(worker.device_id,None)
                for sid in list(worker.reservations):
                    self._sessions.pop(sid,None)
                    self._reservation_kind.pop(sid,None)
                self._advertised.discard(worker.device_id)
                self._withdrawals.discard(worker.device_id)
                self._private_claims.discard(worker.device_id)
            with worker.condition:
                worker.active=False
                worker.condition.notify_all()

    def snapshot(self):
        with self._lock:
            return [{'device_id':w.device_id,'models':list(w.models),
                     'reserved':len(w.reservations),'available':w.active and not w.reservations,
                     'public_advertised':w.device_id in self._advertised,
                     'withdrawal_requested':w.device_id in self._withdrawals,
                     'private_capacity_claimed':w.device_id in self._private_claims,
                     'generation':w.generation,
                     'reservation_types':sorted(self._reservation_kind.get(s,'unknown') for s in w.reservations)}
                    for w in self._workers.values()]

    def mark_advertised(self, device_id:str, value:bool, *, generation:int|None=None):
        with self._lock:
            worker=self._workers.get(device_id)
            if generation is not None and (worker is None or worker.generation!=generation):
                return False
            if value:
                if (worker is None or not worker.active or worker.reservations
                        or device_id in self._private_claims):
                    return False
                self._advertised.add(device_id)
            else:
                self._advertised.discard(device_id)
                self._withdrawals.discard(device_id)
            return True

    def private_quote(self, *, model:str='', operation:str='chat.completions') -> dict|None:
        '''Quote the selected Worker's normal marketplace price in credit wei.

        A quote is advisory until reservation checks its connection generation.
        Public candidates must be withdrawn before any local execution.
        '''
        from main_computer.credit_units import CREDIT_WEI_PER_CREDIT
        with self._lock:
            policy=(self.policy_store.get()['policy'] if self.policy_store is not None
                    else {'dedicated_public':[]})
            matches=[]
            for w in self._workers.values():
                if (not w.active or not self.is_authorized(w.device_id)
                        or not market_supports(w.market, {
                            'model': model, 'operation': operation})):
                    continue
                public=w.device_id in self._advertised
                if w.device_id in policy['dedicated_public']:
                    continue
                if not public and w.reservations:
                    continue
                if w.device_id in self._withdrawals:
                    continue
                matches.append((public,w.device_id,w))
            if not matches:return None
            _,_,w=min(matches,key=lambda item:(item[0],item[1]))
            raw=w.market.get('price') or {'amount':'1','unit':'compute_credit'}
            if not isinstance(raw,dict) or str(raw.get('unit','compute_credit')).lower() not in ('compute_credit','credit'):
                raise RuntimeError('unsupported Worker marketplace credit price')
            try:
                price=Decimal(str(raw.get('amount','1')))
            except (ValueError,TypeError,InvalidOperation):
                raise RuntimeError('invalid Worker marketplace price')
            if not price.is_finite() or price<=0 or price>Decimal('1000000000000000000'):
                raise RuntimeError('invalid Worker marketplace price')
            reference=int((price*CREDIT_WEI_PER_CREDIT).to_integral_value(rounding=ROUND_CEILING))
            assessment=(reference+4)//5  # hardcoded 20%, round up in wei
            return {'device_id':w.device_id,'generation':w.generation,
                    'reference_credit_wei':reference,'assessment_credit_wei':assessment,
                    'public':w.device_id in self._advertised}

    def request_private_capacity(self, *, model:str='', device_id:str='', generation:int|None=None, operation:str='chat.completions') -> str|None:
        '''Select one idle public Worker for a future upstream withdrawal.

        Does not claim it for local work or pretend disconnection happened.
        Subsequent public offers are refused while withdrawal is pending.
        '''
        with self._lock:
            for did in sorted(self._advertised):
                worker=self._workers.get(did)
                if (worker is not None and worker.active and
                    self.is_authorized(did) and
                    not any(self._reservation_kind.get(s)=='private' for s in worker.reservations) and
                    did not in self._withdrawals and
                    market_supports(worker.market, {
                        'model': model, 'operation': operation}) and
                    (not device_id or device_id==did) and
                    (generation is None or worker.generation==generation)):
                    self._withdrawals.add(did)
                    self._private_claims.add(did)
                    return did
        return None

    def withdrawals(self) -> tuple[str,...]:
        with self._lock:return tuple(sorted(self._withdrawals))

    def cancel_private_capacity(self, device_id: str, *, generation: int) -> None:
        """Release a timed-out local claim without clearing Hub close fencing."""
        with self._lock:
            worker = self._workers.get(device_id)
            if worker and worker.generation == generation:
                self._private_claims.discard(device_id)

    def request_public_withdrawal(self, device_id: str, *, generation: int) -> bool:
        """Fence new offers before an upstream retirement; never imply Hub ACK."""
        with self._lock:
            worker = self._workers.get(device_id)
            if (worker is None or not worker.active or worker.generation != generation
                    or device_id not in self._advertised):
                return False
            self._withdrawals.add(device_id)
            return True

    def _plan_locked(self, *, now=None, credit_meter=None):
        current = self._advertised.copy()
        workers=[]
        for w in self._workers.values():
            kinds=[self._reservation_kind.get(s, 'public') for s in w.reservations]
            workers.append({'device_id':w.device_id,'active':w.active,
                            'authorized':self.is_authorized(w.device_id),
                            'busy_public':'public' in kinds,
                            'busy_private':'private' in kinds})
        if self.policy_store is None:
            from .scheduler_policy import DEFAULT_POLICY
            policy=DEFAULT_POLICY
        else:policy=self.policy_store.get()['policy']
        return compute_plan(policy,workers,current,now=now,
                            credit_meter=credit_meter,
                            withdrawal_requested=self._withdrawals | self._private_claims)

    def _credit_meter(self):
        if self.credit_provider is None:return None
        try:
            observation=self.credit_provider()
            if observation.get('state')!='CURRENT' or not observation.get('balance'):
                return None
            return {'state':'CURRENT',
                    'available_credit_wei':observation['balance']['available_credit_wei']}
        except (ValueError, TypeError, KeyError, SLHError, OSError):
            return None

    def plan(self, *, now=None, credit_meter=None):
        meter=self._credit_meter() if credit_meter is None else credit_meter
        with self._lock:return self._plan_locked(now=now,credit_meter=meter)

    def reserve(self, session_id: str, *, model: str = '', device_id: str = '',
                purpose: str = 'public', operation: str = 'chat.completions') -> ManagedWorker:
        if purpose not in {'public','private'}:raise ValueError('invalid reservation purpose')
        with self._lock:
            if session_id in self._sessions:raise RuntimeError('duplicate session reservation')
            for w in self._workers.values():
                if (w.active and self.is_authorized(w.device_id)
                    and not w.reservations and (not device_id or w.device_id == device_id)
                    and (not model or model in w.models)
                    and (purpose != 'private' or market_supports(w.market, {
                        'model': model, 'operation': operation}))):
                    # A private requester cannot take a worker whose public
                    # Stable Hub session is advertised as available.
                    if purpose=='private':
                        if w.device_id in self._advertised or w.device_id in self._withdrawals:continue
                        if self.policy_store is not None and w.device_id in self.policy_store.get()['policy']['dedicated_public']:
                            continue
                    if purpose=='public' and w.device_id in self._withdrawals:continue
                    w.reservations.add(session_id)
                    self._sessions[session_id]=w
                    self._reservation_kind[session_id]=purpose
                    if purpose == 'private':
                        self._private_claims.discard(w.device_id)
                    return w
            raise RuntimeError('no eligible local worker capacity')

    def reserve_public(self, session_id: str, *, device_id: str, work: dict, now=None) -> ManagedWorker:
        meter=self._credit_meter()
        with self._lock:
            if device_id not in self._advertised:
                raise RuntimeError('worker public session not advertised')
            if device_id in self._withdrawals or device_id not in self._plan_locked(now=now,credit_meter=meter).desired:
                raise RuntimeError('worker is draining under SLH policy')
            worker = self._workers.get(device_id)
            if worker is None or not market_supports(worker.market, work):
                raise RuntimeError('WORKER_MODEL_OPERATION_MISMATCH')
            return self.reserve(session_id, model=request_contract(work)[0],
                                device_id=device_id,purpose='public')

    def reserve_private(self, session_id:str, *, model:str='',device_id:str='',generation:int|None=None, operation:str='chat.completions') -> ManagedWorker:
        with self._lock:
            worker=self.reserve(session_id,model=model,device_id=device_id,purpose='private',operation=operation)
            if generation is not None and worker.generation!=generation:
                self.release(session_id)
                raise RuntimeError('stale Worker quote')
            return worker

    def release(self, session_id: str):
        with self._lock:
            worker=self._sessions.pop(session_id,None)
            self._reservation_kind.pop(session_id,None)
            if worker:worker.reservations.discard(session_id)

    def worker_for(self, session_id: str) -> ManagedWorker:
        with self._lock:
            w=self._sessions.get(session_id)
            if not w or not w.active or self._workers.get(w.device_id) is not w:
                raise ConnectionError('reserved local worker disconnected')
            return w

    def execute_private(self, offer: dict, *, timeout: float = 120) -> dict:
        sid=str(offer['session_id']); req=str(offer['request_id']); run=str(offer['run_id'])
        worker=self.worker_for(sid)
        if not market_supports(worker.market, offer.get('work')):
            raise RuntimeError('PRIVATE_MODEL_OPERATION_MISMATCH')
        if not self.is_authorized(worker.device_id):
            raise PermissionError('private worker no longer authorized')
        try:
            worker.pending[sid]=None
            worker.send({'type':'slh.private.work.offer','protocol':'slh-private-v1',
                         'session_id':sid,'request_id':req,'run_id':run})
            accepted=worker.await_message(sid,{'worker.private.work.accepted'},10)
            if accepted.get('request_id')!=req or accepted.get('run_id')!=run:
                raise RuntimeError('private worker offer identity mismatch')
            if not self.is_authorized(worker.device_id):
                raise PermissionError('private worker revoked before execution')
            worker.pending[sid]=None
            worker.send({'type':'slh.private.work.authorized','protocol':'slh-private-v1',
                         'session_id':sid,'request_id':req,'run_id':run,'work':offer['work']})
            terminal=worker.await_message(sid,{'worker.private.work.result','worker.private.work.failed'},timeout)
            if terminal.get('request_id')!=req or terminal.get('run_id')!=run:
                raise RuntimeError('private worker terminal identity mismatch')
            if terminal['type']=='worker.private.work.failed':
                raise RuntimeError('PRIVATE_WORKER_FAILED')
            result=terminal.get('result')
            verify_execution_result(offer.get('work'), result)
            return result
        finally:
            worker.pending.pop(sid,None)

    def execute_authorized(self, offer: dict) -> dict:
        sid=str(offer['session_id']); req=str(offer['request_id']);run=str(offer['run_id'])
        worker=self.worker_for(sid)
        if not market_supports(worker.market, offer.get('work')):
            self.release(sid)
            raise RuntimeError('PUBLIC_MODEL_OPERATION_MISMATCH')
        if not self.is_authorized(worker.device_id):
            self.release(sid)
            raise PermissionError('managed device revoked before authorization')
        try:
            # Public Hub withholds the payload until credit/hold is committed.
            worker.pending[sid]=None
            worker.send({'type':'hub.work.offer','session_id':sid,'request_id':req,'run_id':run,
                         'execution_protocol':'durable-authorization-v1',
                         'execution_authorization_required':True,
                         'work':{'execution_authorization_pending':True,
                                 'model':request_contract(offer['work'])[0],
                                 'operation':request_contract(offer['work'])[1],
                                 'capabilities':list(request_contract(offer['work'])[2])}})
            accepted=worker.await_message(sid,{'worker.work.accepted'},10)
            if accepted.get('request_id')!=req:raise RuntimeError('local worker offer identity mismatch')
            worker.pending[sid]=None
            worker.send({'type':'hub.work.authorized','session_id':sid,'request_id':req,'run_id':run,
                         'execution_protocol':'durable-authorization-v1','work':offer['work']})
            terminal=worker.await_message(sid,{'worker.work.result','worker.work.failed'},120)
            if terminal.get('request_id')!=req or terminal.get('run_id')!=run:
                raise RuntimeError('local terminal identity mismatch')
            if terminal['type']=='worker.work.failed':raise RuntimeError('local worker failed: '+str(terminal.get('error'))[:350])
            result = terminal.get('result')
            verify_execution_result(offer.get('work'), result)
            return result
        finally:
            worker.pending.pop(sid,None)
            self.release(sid)


class SLHManagedGateway:
    """Socket server: TLS-pinned clients, existing SLH Known Device verification."""
    def __init__(self, authority: SLHAuthority, *, host='127.0.0.1', port=DEFAULT_MANAGED_PORT,
                 seh_lab_identity=None, seh_lab_id=None, allow_seh=True,
                 seh_admin_target=None):
        self.authority=authority
        self.scheduler=ManagedScheduler(policy_store=SchedulerPolicyStore(authority),is_authorized=lambda did: any(
            d['id']==did and d['status']=='active' and d['role']=='worker'
            for d in self.authority.devices()))
        self.host=host
        self.port=port
        self.ssl_context,self.fingerprint=transport_identity(authority.directory)
        self._server=None
        self._thread=None
        self.on_worker_registered=None
        self.on_worker_disconnected=None
        self.seh_connection=None
        self._seh_start_lock=threading.RLock()
        self.allow_seh=allow_seh
        self.seh_admin_target=seh_admin_target
        # Lab-only key injection avoids persisting the real organization's key
        # in disposable lab state; normal gateways load their own state key.
        self._seh_lab_identity=seh_lab_identity
        self._seh_lab_id=seh_lab_id
        # Only authenticated organization administrators may fetch this ring.
        # No messages, credentials, peer IP addresses, or exception strings.
        self._connection_events = deque(maxlen=1024)
        self._connection_events_lock = threading.Lock()
        self._connection_events_total = 0

    def _note_connection(self, event: str, *, device_id: str = '', socket=None,
                         phase: str = '', exc=None, peer_port=None) -> None:
        row = {'event': event, 'device_id': device_id,
               'unix_ns': time.time_ns(), 'monotonic_ns': time.monotonic_ns()}
        if phase:
            row['phase'] = phase
        if peer_port is None and socket is not None:
            try:
                remote = getattr(socket, 'remote_address', None)
            except (OSError, RuntimeError):
                remote = None
            if isinstance(remote, tuple) and len(remote) > 1 and isinstance(remote[1], int):
                peer_port = remote[1]
        if isinstance(peer_port, int):
            row['gateway_peer_port'] = peer_port
        if exc is not None:
            row['exception_class'] = type(exc).__name__
            if isinstance(getattr(exc, 'errno', None), int):
                row['errno'] = exc.errno
            for attr, out in (('rcvd', 'received_close_code'), ('sent', 'sent_close_code')):
                frame = getattr(exc, attr, None)
                code = getattr(frame, 'code', None)
                if code is not None:
                    try:
                        row[out] = int(code)
                    except (TypeError, ValueError):
                        pass
            direction = getattr(exc, 'rcvd_then_sent', None)
            if isinstance(direction, bool):
                row['received_close_before_sent'] = direction
        if socket is not None:
            try:
                code = getattr(socket, 'close_code', None)
            except (OSError, RuntimeError):
                code = None
            if code is not None:
                try:
                    row['websocket_close_code'] = int(code)
                except (TypeError, ValueError):
                    pass
            # Graceful websocket iteration returns without raising a
            # ConnectionClosed exception. Preserve the protocol's close
            # direction in that case too, without any close-reason text.
            protocol = getattr(socket, 'protocol', None)
            if protocol is not None:
                for attr, out in (('close_rcvd', 'received_close_code'),
                                  ('close_sent', 'sent_close_code')):
                    frame = getattr(protocol, attr, None)
                    code = getattr(frame, 'code', None)
                    if code is not None:
                        try:
                            row[out] = int(code)
                        except (TypeError, ValueError):
                            pass
                direction = getattr(protocol, 'close_rcvd_then_sent', None)
                if isinstance(direction, bool):
                    row['received_close_before_sent'] = direction
        with self._connection_events_lock:
            self._connection_events_total += 1
            self._connection_events.append(row)

    def connection_diagnostics(self, *, device_ids=None) -> dict:
        # Filter before applying the response cap: failures recorded earlier
        # remain retrievable even if other Workers produce subsequent events.
        with self._connection_events_lock:
            selected = (list(self._connection_events) if device_ids is None else
                        [r for r in self._connection_events if r.get('device_id') in device_ids])
            response = {'schema': 'main-computer.slh-gateway-connection-audit.v1',
                        'total_events': self._connection_events_total,
                        'events_retained': len(self._connection_events),
                        'events': selected[-128:]}
        # The Org Service owns the SEH connector; the test harness must not
        # reach into this separate process to recover its causal evidence.
        ports = {r['gateway_peer_port'] for r in selected
                 if isinstance(r.get('gateway_peer_port'), int)}
        supervisor = getattr(self, 'seh_connection', None)
        if supervisor is not None:
            audit = supervisor.stream_diagnostics_for_gateway_ports(ports)
            response['seh_gateway_stream_events'] = audit['events']
            response['seh_tunnel_lifecycle'] = audit['tunnel_lifecycle']
            response['seh_tunnel_connections'] = audit['connections']
            response['seh_tunnel_failures'] = audit['failures']
            response['seh_tunnel_last_error_class'] = audit['last_error_class']
        return response

    def handler(self, socket):
        from websockets.exceptions import ConnectionClosed
        if socket.request.path != MANAGED_PATH:
            socket.close(code=1008,reason='unsupported managed endpoint')
            return
        worker=None
        token=''
        did=''
        phase='authentication'
        self._note_connection('gateway_connection_accepted', socket=socket, phase=phase)
        try:
            # The identity must be registered in SLH before it is permitted to join.
            first=json.loads(socket.recv(timeout=8))
            if first.get('type')!='worker.auth' or not first.get('device_id'):
                raise RuntimeError('expected managed worker.auth')
            if first.get('execution_protocol')!='durable-authorization-v1':
                raise RuntimeError('durable authorization is mandatory')
            did=str(first['device_id'])
            challenge=self.authority.challenge(did)
            socket.send(json.dumps({'type':'slh.auth.challenge','challenge':challenge}))
            proof=json.loads(socket.recv(timeout=8))
            if proof.get('type')!='slh.auth.proof' or proof.get('device_id')!=did:
                raise RuntimeError('invalid managed device proof')
            verified=self.authority.verify_login({'device_id':did,'challenge_id':challenge['challenge_id'],
                                                   'signature':proof.get('signature')})
            token=verified['access_token']
            ident=self.authority.authenticate(token)
            if ident['role']!='worker':raise RuntimeError('device is not an enrolled worker')
            market=first.get('market') if isinstance(first.get('market'),dict) else {}
            market=dict(market)
            market['max_concurrency']=1
            market['active_sessions']=0
            models=advertised_models(market)
            advertised_operations(market)
            worker=ManagedWorker(did,socket,market,models)
            worker.audit = lambda event, exc=None: self._note_connection(
                event, device_id=did, socket=socket, phase='send', exc=exc)
            self.scheduler.register(worker)
            phase='live'
            supervisor = self.seh_connection
            if supervisor is not None:
                try:
                    peer = socket.remote_address
                    if isinstance(peer, tuple) and len(peer) > 1:
                        supervisor.mark_worker_authenticated(int(peer[1]))
                except (OSError, RuntimeError, ValueError, ConnectionError):
                    # No marker means the relay's absolute pre-auth timeout
                    # still applies. Never grant access because of a marker.
                    pass
            self._note_connection('gateway_worker_registered', device_id=did, socket=socket, phase=phase)
            socket.send(json.dumps({'type':'hub.auth.accepted','ok':True,
                                    'execution_protocol':'durable-authorization-v1',
                                    'device_id':did,'service':'main_computer.slh'}))
            socket.send(json.dumps({'type':'hub.ping','ping_id':'initial'}))
            if callable(self.on_worker_registered):
                threading.Thread(target=self.on_worker_registered,args=(worker,),daemon=True).start()
            for raw in socket:
                frame=json.loads(raw)
                kind=frame.get('type')
                if kind=='worker.pong':
                    worker.send({'type':'hub.pong.accepted','ok':True})
                elif kind in {'worker.private.work.accepted','worker.private.work.result','worker.private.work.failed'}:
                    worker.receive(frame)
                elif kind in {'worker.work.accepted','worker.work.result','worker.work.failed'}:
                    self._note_connection({
                        'worker.work.accepted': 'gateway_paid_worker_accepted',
                        'worker.work.result': 'gateway_paid_worker_result',
                        'worker.work.failed': 'gateway_paid_worker_failed',
                    }[kind], device_id=did, socket=socket, phase='live')
                    worker.receive(frame)
                elif kind=='worker.work.delta':
                    pass  # Streaming telemetry is not yet proxied; terminal results are authoritative.
                elif kind=='worker.close':
                    self._note_connection('gateway_worker_close_requested', device_id=did,
                                          socket=socket, phase=phase)
                    break
                else:
                    worker.send({'type':'hub.error','error':'unsupported managed worker message'})
            self._note_connection('gateway_receive_loop_finished', device_id=did,
                                  socket=socket, phase=phase)
        except (ConnectionClosed,EOFError,TimeoutError,OSError,RuntimeError,SLHError,ValueError) as exc:
            self._note_connection('gateway_handler_exception', device_id=did,
                                  socket=socket, phase=phase, exc=exc)
            try:socket.send(json.dumps({'type':'hub.auth.rejected','ok':False,'reason_code':type(exc).__name__}))
            except Exception:pass
        finally:
            self._note_connection('gateway_handler_cleanup', device_id=did,
                                  socket=socket, phase=phase)
            if worker:
                self.scheduler.unregister(worker)
                self._note_connection('gateway_worker_unregistered', device_id=did,
                                      socket=socket, phase=phase)
                if callable(self.on_worker_disconnected):self.on_worker_disconnected(worker)
            if token:
                try:self.authority.logout(token)
                except (SLHError,OSError):pass

    def serve(self):
        from websockets.sync.server import serve
        if not self.authority.initialized():raise RuntimeError('SLH administrator bootstrap required before worker listener')
        with serve(self.handler,self.host,self.port,ssl=self.ssl_context,
                   max_size=2*1024*1024,max_queue=4,open_timeout=8) as server:
            self._server=server
            self.port=server.socket.getsockname()[1]
            from main_computer.slh.seh_autoconnect import read_seh_settings, SEHAutoConnection
            settings=read_seh_settings(self.authority.directory)
            if self.allow_seh and settings is not None and settings.enabled:
                # Explicit local lab configuration remains authoritative.
                self.seh_connection=SEHAutoConnection(
                    self.authority.directory, gateway_port=self.port,
                    admin_target=self.seh_admin_target,
                    identity_private_key=self._seh_lab_identity, lab_id=self._seh_lab_id).start()
            elif (self.allow_seh and settings is None and
                  (self.authority.directory / 'seh-service-signing-key.pem').is_file()):
                # An enabled resident service automatically resolves the ONE
                # registered Mother deployment. No connector command, SEH
                # selector, copied identity, or local configuration write.
                from main_computer.slh.seh_canonical_connection import resolve_canonical_connection
                self.seh_connection=SEHAutoConnection(
                    self.authority.directory, gateway_port=self.port,
                    admin_target=self.seh_admin_target,
                    settings_resolver=resolve_canonical_connection).start()
            try:
                server.serve_forever()
            finally:
                if self.seh_connection is not None:
                    self.seh_connection.stop()

    def start_canonical_seh(self):
        """Enable the sole canonical outbound tunnel on the running gateway.

        Called only by the authenticated organization-service control endpoint.
        No standalone connector, local Worker listener restart, or SLH key write.
        """
        with self._seh_start_lock:
            if self._server is None or not self.allow_seh:
                raise RuntimeError('Resident managed-worker gateway is not ready for SEH')
            if self.seh_connection is not None:
                return self.seh_connection
            from main_computer.slh.seh_autoconnect import read_seh_settings, SEHAutoConnection
            if read_seh_settings(self.authority.directory) is not None:
                raise RuntimeError('Legacy SEH connection configuration is not canonical')
            from main_computer.slh.seh_canonical_connection import resolve_canonical_connection
            # Validate Mother and remote trust before starting retry supervision.
            resolve_canonical_connection(self.authority.directory)
            self.seh_connection = SEHAutoConnection(
                self.authority.directory, gateway_port=self.port,
                settings_resolver=resolve_canonical_connection).start()
            return self.seh_connection

    def start_background(self):
        thread=threading.Thread(target=self.serve,daemon=True,name='slh-managed-gateway')
        self._thread=thread
        thread.start()
        return thread

    def stop(self):
        if self.seh_connection is not None:
            self.seh_connection.stop()
        server=self._server
        if server is not None:
            server.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)


class SLHMarketplaceProxy:
    """One Hub-acknowledged upstream session per live local Worker generation.

    A requested withdrawal fences new offers immediately. A Worker becomes
    private-eligible ONLY after the Hub confirms its own durable close and
    settlement with hub.worker.closed. Socket EOF is not such evidence.
    """

    def __init__(self, gateway: SLHManagedGateway):
        self.gateway = gateway
        self.sessions: dict[str, Any] = {}
        self._lock = threading.RLock()

    def connect_device(self, device_id: str, *, hub_url: str, auth: dict):
        from main_computer.worker_live_session import _WorkerHubLiveSessionClient
        scheduler = self.gateway.scheduler
        if (not isinstance(auth, dict) or auth.get('kind') != 'multisession_key'
                or not auth.get('multisession_key_id')):
            raise ValueError('SLH requires a genuine Hub-issued MSK authorization')
        from .marketplace_auto import _check_url
        _check_url(hub_url)
        with scheduler._lock:
            actual = scheduler._workers.get(device_id)
            if not actual or not actual.active:
                raise RuntimeError('Managed worker not connected')
            if device_id in scheduler._withdrawals:
                raise RuntimeError('Worker awaiting upstream withdrawal')
            generation = actual.generation
            market = dict(actual.market)
        with self._lock:
            if device_id in self.sessions:
                raise RuntimeError('Upstream slot already exists')

        class Slot(_WorkerHubLiveSessionClient):
            def _handle_hub_message(self, message):
                kind = str(message.get('type') or '')
                if kind == 'hub.worker.closed':
                    # Hub sends this only after its owner/market/hold close
                    # commits. A different connection must not release ours.
                    expected = str(self.accepted.get('connection_id') or '')
                    exp_private_slot = (self.accepted.get('service') == 'main_computer.exp_fdb_hub'
                                        and not expected and 'connection_id' not in message)
                    if (self._slh_close_requested and message.get('ok') is True
                            and (expected and str(message.get('connection_id') or '') == expected
                                 or exp_private_slot)):
                        self._slh_closed_confirmed = True
                    self._slh_close_ack.set()
                    return
                if kind == 'hub.work.denied':
                    scheduler.release(str(message.get('session_id') or ''))
                if kind == 'hub.error' and self._slh_close_requested:
                    self._slh_close_ack.set()  # no success; fail closed
                super()._handle_hub_message(message)

            def _execute_work_offer(self, offer):
                # Never use the upstream client's local debug-echo fast path.
                result = scheduler.execute_authorized(offer)
                if not isinstance(result, dict):
                    raise RuntimeError('Managed worker returned invalid result')
                result.setdefault('status', 'success')
                result.setdefault('transport', 'slh-managed-worker')
                return result

            def _handle_work_offer(self, offer):
                sid = str(offer.get('session_id') or '')
                try:
                    # A mismatched Hub offer must never reserve this Worker.
                    # Closing the upstream session allows the Hub to release
                    # the accepted hold without authorizing wrong-model work.
                    scheduler.reserve_public(sid, device_id=device_id,
                                             work=offer.get('work'))
                except RuntimeError:
                    if not market_supports(market, offer.get('work')):
                        self._request_hub_retirement()
                        return
                    # There is no Hub work-decline frame. Close gracefully so
                    # any Hub-side pending hold is settled by its existing
                    # durable close procedure. Never execute this offer.
                    if device_id in scheduler.withdrawals():
                        self._request_hub_retirement()
                        return
                    raise
                try:
                    super()._handle_work_offer(offer)
                except BaseException:
                    scheduler.release(sid)
                    raise

            def _request_hub_retirement(self):
                with self._state_lock:
                    if self._slh_close_requested:
                        return
                    self._slh_close_requested = True
                try:
                    self._send_json({'type': 'worker.close',
                                     'reason': 'slh_worker_bank_withdrawal'})
                except (OSError, ConnectionError, RuntimeError):
                    # A lost socket is not proof of durable Hub settlement.
                    self._slh_close_ack.set()

            def retire_with_hub_ack(self, timeout: float = 5.0) -> bool:
                self._request_hub_retirement()
                self._slh_close_ack.wait(timeout)
                return self._slh_closed_confirmed

        message = {'type': 'worker.auth', 'execution_protocol': 'durable-authorization-v1',
                   'multisession_authorization': auth, 'market': market,
                   'model': (market.get('models') or [''])[0],
                   'models': market.get('models', []), 'status': 'available',
                   'max_concurrency': 1}
        client = Slot(hub_url=hub_url, worker_id='slh-' + device_id,
                      auth_message=message, work_executor=scheduler.execute_authorized,
                      timeout_s=8)
        client._slh_generation = generation
        client._slh_close_ack = threading.Event()
        client._slh_close_requested = False
        client._slh_closed_confirmed = False
        # Before the first Hub offer arrives, reserve the public allocation.
        # A failed auth always removes this tentative registration.
        if not scheduler.mark_advertised(device_id, True, generation=generation):
            raise RuntimeError('Worker cannot be allocated publicly')
        try:
            client.start()
            if (client.accepted.get('ok') is not True
                    or not (client.accepted.get('connection_id')
                            or client.accepted.get('service') == 'main_computer.exp_fdb_hub')
                    or not client.is_alive):
                raise ConnectionError('Stable Hub did not confirm the live slot')
        except BaseException:
            scheduler.mark_advertised(device_id, False, generation=generation)
            client.close(reason='upstream_connect_failed')
            raise
        with self._lock, scheduler._lock:
            if (scheduler._workers.get(device_id) is not actual or not actual.active
                    or device_id in scheduler._withdrawals):
                # Auth succeeded but connection generation or allocation was
                # superseded. Do NOT pretend its Hub session was retired.
                self.sessions[device_id] = client
                scheduler.request_public_withdrawal(device_id, generation=generation)
                raise RuntimeError('Worker changed during upstream registration')
            self.sessions[device_id] = client
        return {'device_id': device_id, 'connected': True,
                'connection_id': client.accepted.get('connection_id'),
                'generation': generation, 'shared_wallet': auth.get('wallet_address')}

    def disconnect_device(self, device_id: str, *, timeout: float = 5.0) -> bool:
        """Retire one slot; return False without releasing on missing Hub ACK."""
        with self._lock:
            client = self.sessions.get(device_id)
        if client is None:
            return True
        scheduler = self.gateway.scheduler
        generation = client._slh_generation
        with scheduler._lock:
            worker = scheduler._workers.get(device_id)
            busy = bool(worker is not None and worker.generation == generation
                        and worker.reservations)
            if worker is not None and worker.generation == generation:
                scheduler.request_public_withdrawal(device_id, generation=generation)
        if busy or client.has_active_work:
            return False
        if not client.retire_with_hub_ack(timeout=timeout):
            # Do not release this device's public allocation merely because
            # a socket died, a timeout fired, or Hub cleanup was deferred.
            return False
        with self._lock:
            if self.sessions.get(device_id) is not client:
                return False
            self.sessions.pop(device_id, None)
        scheduler.mark_advertised(device_id, False, generation=generation)
        client.close(reason='hub_close_acknowledged')
        return True

    def reconcile(self, *, hub_url: str, auth: dict,
                  now: float | None = None, enabled: bool = True) -> dict:
        """Reconcile actual confirmed upstream sessions to scheduler intent."""
        scheduler = self.gateway.scheduler
        plan = scheduler.plan(now=now)
        desired = set(plan.desired) if enabled else set()
        desired -= set(scheduler.withdrawals())
        opened, drained, blocked = [], [], []
        with self._lock:
            current = dict(self.sessions)
        for did, client in current.items():
            with scheduler._lock:
                worker = scheduler._workers.get(did)
                same_generation = bool(worker and worker.active
                                       and worker.generation == client._slh_generation)
                pending = did in scheduler._withdrawals
            if did in desired and same_generation and not pending and client.is_alive:
                continue
            # Generation changes must not cause a stale session to release
            # the new Worker's capacity. Hub retirement still comes first.
            if same_generation:
                scheduler.request_public_withdrawal(did, generation=client._slh_generation)
            if self.disconnect_device(did):
                drained.append(did)
            else:
                blocked.append(did + ':UPSTREAM_CLOSE_UNCONFIRMED')
        with self._lock:
            current = set(self.sessions)
        # Skip new sessions for a Worker with a pending old Hub connection.
        for did in sorted(desired - current):
            if did in scheduler.withdrawals():
                continue
            try:
                self.connect_device(did, hub_url=hub_url, auth=auth)
                opened.append(did)
            except (RuntimeError, ConnectionError, ValueError, OSError) as exc:
                blocked.append(did + ':' + type(exc).__name__)
        result = scheduler.plan(now=now).as_dict()
        result.update({'opened': opened, 'drained': drained, 'blocked': blocked,
                       'live_public_sessions': sorted(
                           did for did, slot in self.sessions.items()
                           if slot.is_alive and slot.accepted.get('ok') is True),
                       'upstream_connection_ids': {
                           did: slot.accepted.get('connection_id')
                           for did, slot in self.sessions.items()
                           if slot.accepted.get('ok') is True}})
        return result

    def connect(self, *, hub_url: str, auth: dict, max_slots: int = 1):
        connected = []
        for worker in self.gateway.scheduler.snapshot()[:max_slots]:
            connected.append(self.connect_device(worker['device_id'], hub_url=hub_url, auth=auth))
        return {'connected_slots': len(connected), 'sessions': connected}

    def close(self):
        # Best effort for process shutdown; do not claim release without ACK.
        with self._lock:
            ids = list(self.sessions)
        for did in ids:
            self.disconnect_device(did, timeout=2.0)
        with self._lock:
            remaining = list(self.sessions.values())
            self.sessions.clear()
        for client in remaining:
            client.close(reason='slh_proxy_shutdown')
