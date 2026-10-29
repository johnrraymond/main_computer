"""SLH managed-worker gateway: enrolled devices, TLS-pinned LAN socket, shared-wallet Hub proxy.

The SLH owns the marketplace MSK. Managed workers never receive it. Only the
public Stable Hub can commit credit holds; local workers execute after an
upstream hub.work.authorized. TLS here uses one pinned leaf, NOT an organization CA.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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

from .authority import SLHAuthority, SLHError
from .ports import DEFAULT_SLH_MANAGED_PORT
from .scheduler_policy import SchedulerPolicyStore, compute_plan

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
    if not key_path.exists():
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

    def send(self, frame: dict):
        with self.send_lock:
            if not self.active:raise ConnectionError('managed worker disconnected')
            self.socket.send(json.dumps(frame,separators=(',',':')))

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
    def __init__(self, is_authorized=None, *, policy_store=None):
        self.is_authorized=is_authorized or (lambda device_id: True)
        self.policy_store=policy_store
        self._advertised:set[str]=set()
        self._reservation_kind:dict[str,str]={}
        self._lock=threading.RLock()
        self._workers:dict[str,ManagedWorker]={}
        self._sessions:dict[str,str]={}

    def register(self, worker: ManagedWorker):
        with self._lock:
            existing=self._workers.get(worker.device_id)
            if existing and existing.active:raise RuntimeError('device already connected')
            self._workers[worker.device_id]=worker

    def unregister(self, worker: ManagedWorker):
        with self._lock:
            if self._workers.get(worker.device_id) is worker:
                self._workers.pop(worker.device_id,None)
                for sid in list(worker.reservations):
                    self._sessions.pop(sid,None)
                    self._reservation_kind.pop(sid,None)
                self._advertised.discard(worker.device_id)
            with worker.condition:
                worker.active=False
                worker.condition.notify_all()

    def snapshot(self):
        with self._lock:
            return [{'device_id':w.device_id,'models':list(w.models),
                     'reserved':len(w.reservations),'available':w.active and not w.reservations,
                     'public_advertised':w.device_id in self._advertised,
                     'reservation_types':sorted(self._reservation_kind.get(s,'unknown') for s in w.reservations)}
                    for w in self._workers.values()]

    def mark_advertised(self, device_id:str, value:bool):
        with self._lock:
            if value:self._advertised.add(device_id)
            else:self._advertised.discard(device_id)

    def _plan_locked(self, *, now=None):
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
        return compute_plan(policy,workers,current,now=now)

    def plan(self, *, now=None):
        with self._lock:return self._plan_locked(now=now)

    def reserve(self, session_id: str, *, model: str = '', device_id: str = '',
                purpose: str = 'public') -> ManagedWorker:
        if purpose not in {'public','private'}:raise ValueError('invalid reservation purpose')
        with self._lock:
            if session_id in self._sessions:raise RuntimeError('duplicate session reservation')
            for w in self._workers.values():
                if (w.active and self.is_authorized(w.device_id)
                    and not w.reservations and (not device_id or w.device_id == device_id)
                    and (not model or model in w.models)):
                    # A private requester cannot take a worker whose public
                    # Stable Hub session is advertised as available.
                    if purpose=='private':
                        if w.device_id in self._advertised:continue
                        if self.policy_store is not None and w.device_id in self.policy_store.get()['policy']['dedicated_public']:
                            continue
                    w.reservations.add(session_id)
                    self._sessions[session_id]=w.device_id
                    self._reservation_kind[session_id]=purpose
                    return w
            raise RuntimeError('no eligible local worker capacity')

    def reserve_public(self, session_id: str, *, device_id: str, now=None) -> ManagedWorker:
        with self._lock:
            if device_id not in self._advertised:
                raise RuntimeError('worker public session not advertised')
            if device_id not in self._plan_locked(now=now).desired:
                raise RuntimeError('worker is draining under SLH policy')
            return self.reserve(session_id,device_id=device_id,purpose='public')

    def reserve_private(self, session_id:str, *, model:str='') -> ManagedWorker:
        return self.reserve(session_id,model=model,purpose='private')

    def release(self, session_id: str):
        with self._lock:
            did=self._sessions.pop(session_id,None)
            self._reservation_kind.pop(session_id,None)
            if did:
                worker=self._workers.get(did)
                if worker:worker.reservations.discard(session_id)

    def worker_for(self, session_id: str) -> ManagedWorker:
        with self._lock:
            did=self._sessions.get(session_id)
            w=self._workers.get(did) if did else None
            if not w or not w.active:raise ConnectionError('reserved local worker disconnected')
            return w

    def execute_authorized(self, offer: dict) -> dict:
        sid=str(offer['session_id']); req=str(offer['request_id']);run=str(offer['run_id'])
        worker=self.worker_for(sid)
        if not self.is_authorized(worker.device_id):
            self.release(sid)
            raise PermissionError('managed device revoked before authorization')
        try:
            # Public Hub withholds the payload until credit/hold is committed.
            worker.pending[sid]=None
            worker.send({'type':'hub.work.offer','session_id':sid,'request_id':req,'run_id':run,
                         'execution_protocol':'durable-authorization-v1',
                         'execution_authorization_required':True,
                         'work':{'execution_authorization_pending':True}})
            accepted=worker.await_message(sid,{'worker.work.accepted'},10)
            if accepted.get('request_id')!=req:raise RuntimeError('local worker offer identity mismatch')
            worker.pending[sid]=None
            worker.send({'type':'hub.work.authorized','session_id':sid,'request_id':req,'run_id':run,
                         'execution_protocol':'durable-authorization-v1','work':offer['work']})
            terminal=worker.await_message(sid,{'worker.work.result','worker.work.failed'},120)
            if terminal.get('request_id')!=req or terminal.get('run_id')!=run:
                raise RuntimeError('local terminal identity mismatch')
            if terminal['type']=='worker.work.failed':raise RuntimeError('local worker failed: '+str(terminal.get('error'))[:350])
            return terminal.get('result') if isinstance(terminal.get('result'),dict) else {'value':terminal.get('result')}
        finally:
            worker.pending.pop(sid,None)
            self.release(sid)


class SLHManagedGateway:
    """Socket server: TLS-pinned clients, existing SLH Known Device verification."""
    def __init__(self, authority: SLHAuthority, *, host='127.0.0.1', port=DEFAULT_MANAGED_PORT,
                 seh_lab_identity=None, seh_lab_id=None):
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
        # Lab-only key injection avoids persisting the real organization's key
        # in disposable lab state; normal gateways load their own state key.
        self._seh_lab_identity=seh_lab_identity
        self._seh_lab_id=seh_lab_id

    def handler(self, socket):
        from websockets.exceptions import ConnectionClosed
        if socket.request.path != MANAGED_PATH:
            socket.close(code=1008,reason='unsupported managed endpoint')
            return
        worker=None
        token=''
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
            models=[m for m in market.get('models',[]) if isinstance(m,str) and m.strip()][:32]
            if not models:raise RuntimeError('worker must advertise at least one model')
            worker=ManagedWorker(did,socket,market,models)
            self.scheduler.register(worker)
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
                elif kind in {'worker.work.accepted','worker.work.result','worker.work.failed'}:
                    worker.receive(frame)
                elif kind=='worker.work.delta':
                    pass  # Streaming telemetry is not yet proxied; terminal results are authoritative.
                elif kind=='worker.close':
                    break
                else:
                    worker.send({'type':'hub.error','error':'unsupported managed worker message'})
        except (ConnectionClosed,EOFError,TimeoutError,OSError,RuntimeError,SLHError,ValueError) as exc:
            try:socket.send(json.dumps({'type':'hub.auth.rejected','ok':False,'reason_code':type(exc).__name__}))
            except Exception:pass
        finally:
            if worker:
                self.scheduler.unregister(worker)
                if callable(self.on_worker_disconnected):self.on_worker_disconnected(worker)
            if token:
                try:self.authority.logout(token)
                except (SLHError,OSError):pass

    def serve(self):
        from websockets.sync.server import serve
        if not self.authority.initialized():raise RuntimeError('SLH administrator bootstrap required before worker listener')
        with serve(self.handler,self.host,self.port,ssl=self.ssl_context,
                   max_size=2*1024*1024,open_timeout=8) as server:
            self._server=server
            self.port=server.socket.getsockname()[1]
            from main_computer.slh.seh_autoconnect import read_seh_settings, SEHAutoConnection
            settings=read_seh_settings(self.authority.directory)
            if settings is not None and settings.enabled:
                if settings.organization_id != self.authority.org_id():
                    raise RuntimeError('Configured SEH organization differs from initialized SLH')
                self.seh_connection=SEHAutoConnection(
                    self.authority.directory, gateway_port=self.port,
                    identity_private_key=self._seh_lab_identity, lab_id=self._seh_lab_id).start()
            try:
                server.serve_forever()
            finally:
                if self.seh_connection is not None:
                    self.seh_connection.stop()

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
    """One upstream worker socket per managed device; all may share one MSK."""
    def __init__(self,gateway:SLHManagedGateway):
        self.gateway=gateway
        self.sessions:dict[str,Any]={}
        self._lock=threading.RLock()

    def connect_device(self, device_id:str, *, hub_url:str, auth:dict):
        from main_computer.viewport_routes_energy import _WorkerHubLiveSessionClient
        scheduler=self.gateway.scheduler
        if not isinstance(auth,dict) or auth.get('kind')!='multisession_key' or not auth.get('multisession_key_id'):
            raise ValueError('SLH requires a genuine Hub-issued MSK authorization')
        if urlsplit(hub_url).scheme not in {'http','https'}:raise ValueError('Invalid Hub URL')
        with scheduler._lock:
            actual=scheduler._workers.get(device_id)
            if not actual or not actual.active:raise RuntimeError('Managed worker not connected')
            market=dict(actual.market)
        with self._lock:
            if device_id in self.sessions:raise RuntimeError('Upstream slot already exists')

        class Slot(_WorkerHubLiveSessionClient):
            def _execute_work_offer(self, offer):
                # An SLH public slot is ONLY a proxy. The inherited Worker
                # client has a debug-echo fast path that can answer a job on
                # the SLH without ever contacting its walletless worker.
                # Never use that path here: every authorized job must cross
                # the managed-device session and return from that device.
                result = scheduler.execute_authorized(offer)
                if not isinstance(result, dict):
                    raise RuntimeError('Managed worker returned invalid result')
                result.setdefault('status', 'success')
                result.setdefault('transport', 'slh-managed-worker')
                return result

            def _handle_work_offer(self, offer):
                sid=str(offer.get('session_id') or '')
                scheduler.reserve_public(sid,device_id=device_id)
                try:super()._handle_work_offer(offer)
                except BaseException:
                    scheduler.release(sid)
                    raise

        message={'type':'worker.auth','execution_protocol':'durable-authorization-v1',
                 'multisession_authorization':auth,'market':market,
                 'model':(market.get('models') or [''])[0],
                 'models':market.get('models',[]),'status':'available','max_concurrency':1}
        client=Slot(hub_url=hub_url,worker_id='slh-'+device_id,auth_message=message,
                    work_executor=scheduler.execute_authorized,timeout_s=8)
        scheduler.mark_advertised(device_id,True)
        try:client.start()
        except BaseException:
            scheduler.mark_advertised(device_id,False)
            client.close(reason='upstream_connect_failed')
            raise
        with self._lock:
            if not actual.active:
                client.close(reason='local_worker_disconnected')
                raise ConnectionError('Local worker disconnected before upstream ready')
            self.sessions[device_id]=client
        return {'device_id':device_id,'connected':True,'shared_wallet':auth.get('wallet_address')}

    def reconcile(self, *, hub_url:str, auth:dict, now:float|None=None) -> dict:
        """Apply policy to public advertisements, never cancel an active job.

        Call periodically as well as on worker join/leave. Slot teardown is
        delayed while the local scheduler owns a work reservation, including
        the offer -> authorization interval.
        """
        scheduler=self.gateway.scheduler
        plan=scheduler.plan(now=now)
        desired=set(plan.desired)
        opened=[];drained=[];blocked=[]
        with self._lock:
            current=dict(self.sessions)
        for did,client in current.items():
            if did in desired and client.is_alive:continue
            with scheduler._lock:
                busy=bool(scheduler._workers.get(did) and
                          scheduler._workers[did].reservations)
                # Do not let a subsequent upstream offer race with drain.
                if did not in desired:scheduler.mark_advertised(did,False)
            if busy or client.has_active_work:
                blocked.append(did)
                continue
            self.disconnect_device(did)
            drained.append(did)
        with self._lock:current=set(self.sessions)
        for did in sorted(desired-current):
            try:
                self.connect_device(did,hub_url=hub_url,auth=auth)
                opened.append(did)
            except (RuntimeError, ConnectionError, ValueError, OSError) as exc:
                blocked.append(did+':'+type(exc).__name__)
        result=scheduler.plan(now=now).as_dict()
        result.update({'opened':opened,'drained':drained,'blocked':blocked,
                       'live_public_sessions':sorted(self.sessions)})
        return result

    def connect(self, *, hub_url:str,auth:dict,max_slots:int=1):
        connected=[]
        for worker in self.gateway.scheduler.snapshot()[:max_slots]:
            connected.append(self.connect_device(worker['device_id'],hub_url=hub_url,auth=auth))
        return {'connected_slots':len(connected),'sessions':connected}

    def disconnect_device(self,device_id:str):
        with self._lock:client=self.sessions.pop(device_id,None)
        self.gateway.scheduler.mark_advertised(device_id,False)
        if client:client.close(reason='local_worker_disconnected')

    def close(self):
        with self._lock:
            ids=list(self.sessions)
        for did in ids:self.disconnect_device(did)
