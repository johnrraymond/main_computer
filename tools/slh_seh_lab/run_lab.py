"""Integrated SLH/SEH lab: one command, same three-Hub smoke topology as Stable/Exp.

    python -m tools.slh_seh_lab.run_lab plan
    python -m tools.slh_seh_lab.run_lab verify-local
    python -m tools.slh_seh_lab.run_lab run
    python -m tools.slh_seh_lab.run_lab run --use-existing-hubs
    python -m tools.slh_seh_lab.run_lab deploy-local-coolify
    python -m tools.slh_seh_lab.run_lab verify-deployed

'run' owns its Hub subprocesses; it never stops unrelated processes or wipes
FoundationDB. 'verify-local' uses an isolated in-memory Hub with real HTTP/WS
and the actual SLH gateway. SEH uses the real deployable WebSocket relay runtime, connected
by an authenticated outbound SLH tunnel. Public Coolify deployment is tested separately. Paid credits and earnings are proven against the Stable Hub in the smoke namespace.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit

from main_computer.stable_hub_topology import load_stable_hub_topology

DEFAULT_TOPOLOGY = Path('deploy/hub-topology/smoke-topology.json')
REPORT_ROOT = Path('runtime/reports/slh-seh-lab')
SCHEMA = 'main-computer.slh-seh-lab.v2'


class LabRefused(RuntimeError):
    pass


def validate_topology(path: str | Path) -> dict:
    """Never launch or mutate a nonlocal/production Hub from this harness."""
    t = load_stable_hub_topology(path)
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    network = raw.get('network') or {}
    storage = raw.get('storage') or {}
    if (network.get('network_kind') != 'smoke' or network.get('network_key') != 'dev'
            or 'smoke' not in str(t.cluster_id).lower()):
        raise LabRefused('SLH/SEH lab requires the isolated dev/smoke topology')
    if storage.get('namespace') != 'main-computer-hub-smoke':
        raise LabRefused('Refusing non-smoke FoundationDB namespace')
    if len(t.hubs) != 3 or len(t.entry_urls) != 3:
        raise LabRefused('SLH/SEH lab requires exactly three smoke Hub endpoints')
    for url in [*t.entry_urls, *(h.hub_url for h in t.hubs)]:
        u = urlsplit(url)
        if u.scheme != 'http' or u.hostname != '127.0.0.1' or not u.port:
            raise LabRefused('All smoke Hub endpoints must use http://127.0.0.1:PORT')
    return {'topology': str(path), 'cluster_id': t.cluster_id,
            'namespace': storage['namespace'], 'hub_ids': [h.hub_id for h in t.hubs],
            'hub_urls': [h.hub_url for h in t.hubs], 'network_kind': 'smoke'}


def plan(path: str | Path) -> dict:
    topology = validate_topology(path)
    return {'schema': SCHEMA, 'status': 'PLANNED_ONLY', 'topology': topology,
            'lanes': ['known_device_and_lan', 'seh_production_runtime_local_integration',
                      'stable_hub_shared_wallet_sessions', 'scheduler_reconcile_and_drain',
                      'private_authorized_execution', 'paid_requester_charge_and_wallet_earning',
                      'seh_coolify_contract'],
            'limits': {'production_seh': 'LOCAL_RUNTIME_TESTED_NO_COOLIFY', 'paid_request_settlement': 'LAB_EARNINGS_SETTLEMENT',
                       'remote_coolify': 'NOT_TESTED', 'real_fdb_required_for_run': True},
            'no_external_urls': True, 'production_mutations': False}


def _wait(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.035)
    return False


def _local_demo(hub_url: str, *, topology_label: str, payout_ledger=None,
                deployed: dict | None = None) -> dict:
    """Perform Known Device, WSS relay, scheduler, paid work and wallet earnings."""
    from main_computer.slh.authority import SLHAuthority
    from main_computer.slh.authenticator import generate_identity, sign_object
    from main_computer.slh.managed_gateway import SLHManagedGateway, SLHMarketplaceProxy
    from main_computer.slh.managed_client import ManagedSLHWorkerClient
    from main_computer.slh.scheduler_policy import SchedulerPolicyStore
    from tools.slh_authenticator_cli import save_identity, load_identity
    from main_computer.hub_cli_live_session import identify_hub_protocol, issue_cli_multisession_authorization
    from main_computer.multisession_key_signing import private_key_to_address
    from types import SimpleNamespace
    from main_computer.seh.relay import SEHRelay
    from main_computer.slh.seh_transport import SEHLocalClientAdapter
    from main_computer.slh.seh_autoconnect import SEHConnectionSettings, write_seh_settings
    from .paid_cycle import verify_paid_cycle

    gates: dict[str, bool] = {}
    org_id = deployed['org'] if deployed else 'slh-seh-lab'
    slh_id = deployed['slh'] if deployed else 'lab-slh'
    with tempfile.TemporaryDirectory(prefix='mc-slh-seh-lab-') as directory:
        root = Path(directory)
        auth = SLHAuthority(root / 'state')
        admin_key, admin_public = generate_identity()
        statement = {'version': 1, 'action': 'slh.admin.bootstrap',
                     'org_id': org_id, 'admin_id': 'lab-owner',
                     'device_id': 'lab-admin', 'public_key': admin_public}
        auth.bootstrap({**statement, 'proof': sign_object(admin_key, statement),
                        'admin_name': 'Lab Owner',
                        'bootstrap_secret': (root/'state'/'bootstrap.secret').read_text().strip()},
                       loopback=True)
        gates['admin_initialized'] = auth.status()['initialized'] is True
        identity_dir = root/'authenticators'
        devices = ('lab-worker-a', 'lab-worker-b', 'lab-worker-c', 'lab-worker-d')
        for did in devices:
            save_identity(identity_dir, did)
            private, public = load_identity(identity_dir, did)
            request = {'selected_admin': 'lab-owner', 'device_id': did,
                       'public_key': public, 'kind': 'worker', 'device_name': did,
                       'claimed_user': 'Lab Owner', 'requested_role': 'worker',
                       'client_nonce': secrets.token_urlsafe(24)}
            signed = {**request, 'version': 1, 'action': 'slh.device.enrollment.request',
                      'org_id': org_id}
            pending = auth.enrollment_request({**request, 'proof': sign_object(private, signed)})
            auth.approve(pending['request_id'], admin_id='lab-owner')
        gates['four_approved_walletless_devices'] = len([x for x in auth.devices()
                                                           if x['role']=='worker' and x['status']=='active']) == 4
        # Before the SLH starts, configure its desired SEH route. The managed
        # gateway itself must start and supervise the tunnel; the lab must never
        # call SLHSEHConnector.connect() as a substitute for autoconnection.
        relay = None
        if deployed:
            relay_signer = deployed['identity_key']
            uri = deployed['url']
            lab_id = deployed['lab_id']
        else:
            relay_signer, relay_public = generate_identity()
            relay = SEHRelay(organization_id=org_id, slh_id=slh_id,
                             slh_public_key=relay_public, host='127.0.0.1', port=0).start()
            uri=f'ws://127.0.0.1:{relay.port}'
            lab_id=None
        write_seh_settings(auth.directory, SEHConnectionSettings(
            enabled=True, seh_url=uri, organization_id=org_id, slh_id=slh_id,
            allow_loopback_lab=True, retry_seconds=0.2))
        gateway = SLHManagedGateway(auth, host='127.0.0.1', port=0,
                                    seh_lab_identity=relay_signer, seh_lab_id=lab_id)
        gateway.start_background()
        adapter=None
        try:
            if not _wait(lambda: gateway._server is not None and
                         gateway.seh_connection is not None and gateway.seh_connection.connected,
                         seconds=8):
                raise RuntimeError('Configured SLH did not automatically authenticate its SEH tunnel')
            adapter=SEHLocalClientAdapter(uri, org_id, allow_lab=True, lab_id=lab_id)
            if not _wait(lambda: relay is None or relay.tunnel_ready):
                raise RuntimeError('SEH did not observe the configured SLH tunnel')
        except BaseException:
            if adapter is not None:adapter.stop()
            gateway.stop()
            if relay is not None:relay.stop()
            raise
        gates['seh_configured_autoconnect_on_slh_start'] = gateway.seh_connection.connected
        def relay_traffic():
            return relay.bytes_forwarded if relay is not None else adapter.bytes_forwarded
        def relay_ready():
            return relay.tunnel_ready if relay is not None else gateway.seh_connection.connected
        clients = []
        def execute_work(offer):
            work = offer.get('work') or {}
            payload=work.get('input')
            return {'status':'success','response':{'content':payload.get('value','') if isinstance(payload,dict) else str(payload or '')}}
        proxy = SLHMarketplaceProxy(gateway)
        try:
            market = {'rings': ['ring-2'], 'capabilities':['chat.completions'],
                      'models':['mock-model'], 'price': {'amount':'1', 'unit':'compute_credit'},
                      'max_concurrency':1}
            for index, did in enumerate(devices):
                use_relay = index in (1, 3) if deployed else index == 1
                port = adapter.server_address[1] if use_relay else gateway.port
                client = ManagedSLHWorkerClient(
                    device_id=did, identity_dir=identity_dir,
                    tls_fingerprint=gateway.fingerprint,
                    hub_url=f'https://127.0.0.1:{port}', worker_id=did,
                    auth_message={'type': 'worker.auth', 'device_id':did, 'market':market},
                    work_executor=execute_work)
                client.start()
                clients.append(client)
            if not _wait(lambda: len(gateway.scheduler.snapshot()) == 4):
                raise RuntimeError('workers did not register')
            gates['four_real_managed_ws_sessions'] = len(gateway.scheduler.snapshot()) == 4
            gates['seh_test_relay_keeps_slh_tls_pin'] = relay_traffic() > 0 and adapter.bytes_forwarded > 0
            gates['seh_authenticated_slh_outbound'] = gateway.seh_connection.connected and (
                relay is None or relay.authenticated_slh_count == 1)
            gates['seh_real_runtime_multiplexing'] = relay_ready() and relay_traffic() > 0 and (
                not deployed or len([c for c in clients if c.hub_url.startswith('https://127.0.0.1:' + str(adapter.server_address[1]))]) >= 2)
            # The temporary wallet is strictly local, disposable, and never written into evidence.
            wallet_key = '0x' + secrets.token_hex(32)
            wallet = SimpleNamespace(private_key=wallet_key,
                                     address=private_key_to_address(wallet_key))
            identity = identify_hub_protocol(hub_url)
            if identity.get('service') != 'main_computer.stable_hub':
                raise LabRefused('Hub identity is not a Stable Hub')
            upstream_auth = issue_cli_multisession_authorization(hub_url, wallet=wallet,
                                                                  stable_identity=identity)
            gates['real_hub_msk_issued'] = bool(upstream_auth.get('multisession_key_id'))
            policy = SchedulerPolicyStore(auth)
            policy.update({'max_public_percent':50,'min_private_idle_percent':25},
                          expected_revision=0, actor='lab-owner')
            first = proxy.reconcile(hub_url=hub_url, auth=upstream_auth)
            gates['two_public_slots'] = len(first['live_public_sessions']) == 2
            sessions = tuple(proxy.sessions.values())
            accounts = {c.accepted.get('account_id') for c in sessions}
            connections = {c.accepted.get('connection_id') for c in sessions}
            gates['one_wallet_many_sessions'] = (len(accounts)==1 and None not in accounts
                                                  and len(connections)==2 and None not in connections)
            # Force the paid work through the remote SEH client rather than
            # allowing the Stable Hub to choose a direct-LAN worker by chance.
            policy.update({'max_public_percent':25,
                           'dedicated_private':['lab-worker-a','lab-worker-c','lab-worker-d']},
                          expected_revision=1,actor='lab-owner')
            remote_only=None
            for _ in range(70):
                remote_only=proxy.reconcile(hub_url=hub_url,auth=upstream_auth)
                if set(remote_only['live_public_sessions']) == {'lab-worker-b'}:
                    break
                time.sleep(.05)
            gates['seh_paid_target_only_remote_worker'] = (
                set(remote_only['live_public_sessions']) == {'lab-worker-b'} and
                relay_ready())
            if not gates['seh_paid_target_only_remote_worker']:
                raise RuntimeError('SEH-only public session could not be isolated')
            if payout_ledger is None:
                raise RuntimeError('Paid-cycle lab requires verified Stable Hub payout ledger')
            paid = verify_paid_cycle(hub_url=hub_url, proxy=proxy, gateway=gateway,
                                     ledger=payout_ledger, worker_account_id=next(iter(accounts)),
                                     lab_mode=topology_label)
            gates.update({'paid_'+k:v for k,v in paid['gates'].items()})
            marker_executed = _wait(lambda: any(
                c.last_result.get('request_id') == paid.get('request_id')
                and c.last_result.get('type') == 'worker.work.result'
                for c in clients), seconds=5)
            paid['managed_worker_proof'] = marker_executed
            gates['paid_actual_walletless_worker_execution'] = marker_executed
            gates['seh_paid_worker_executed_through_real_relay'] = (
                paid.get('worker_connection_id') ==
                proxy.sessions['lab-worker-b'].accepted.get('connection_id')
                and clients[1].last_result.get('request_id') == paid.get('request_id')
                and relay_traffic() > 0)
            # Restore both public slots before testing the two-slot drain.
            policy.update({'max_public_percent':50,'dedicated_private':[]},
                          expected_revision=2,actor='lab-owner')
            restored=None
            for _ in range(70):
                restored=proxy.reconcile(hub_url=hub_url,auth=upstream_auth)
                if len(restored['live_public_sessions']) == 2:break
                time.sleep(.05)
            gates['public_slots_restored_after_remote_paid_job'] = len(restored['live_public_sessions']) == 2

            # A reserved private worker cannot be simultaneously advertised publicly.
            sid = 'lab-private-' + secrets.token_hex(8)
            private_worker = gateway.scheduler.reserve_private(sid, model='mock-model')
            result = gateway.scheduler.execute_authorized({
                'session_id':sid, 'request_id':'lab-private-request',
                'run_id':'lab-private-run', 'work':{'model':'mock-model', 'input':'lab'}})
            gates['private_authorized_execution'] = (private_worker.device_id not in proxy.sessions
                                                      and result.get('status') == 'success')
            policy.update({'max_public_percent':0}, expected_revision=3, actor='lab-owner')
            drained=[]
            second=None
            for _ in range(70):
                second=proxy.reconcile(hub_url=hub_url, auth=upstream_auth)
                drained.extend(second['drained'])
                if not second['live_public_sessions']:break
                time.sleep(.05)
            gates['drained_public_slots'] = not second['live_public_sessions'] and len(set(drained))==2
            gates['private_reserve_preserved'] = first.get('reserve_satisfied') is True
            # Prove the session on the test relay was TLS-end-to-end, with a deliberately incorrect pin.
            wrong = ManagedSLHWorkerClient(
                device_id=devices[1],identity_dir=identity_dir,
                tls_fingerprint='0'*64,
                hub_url=f'https://127.0.0.1:{adapter.server_address[1]}', worker_id=devices[1],
                auth_message={'type':'worker.auth','device_id':devices[1],'market':market},
                work_executor=lambda offer:{'status':'success'})
            try:
                wrong.start()
                gates['seh_relay_rejects_wrong_slh_pin'] = False
            except RuntimeError as exc:
                gates['seh_relay_rejects_wrong_slh_pin'] = 'SLH_TLS_PIN_MISMATCH' in str(exc)
            finally:
                wrong.close(reason='expected-pin-rejection')
        finally:
            proxy.close()
            try:
                for client in reversed(clients):
                    client.close(reason='lab_teardown')
                # Known Device sessions disappear only after their WS logout.
                if not _wait(lambda: not auth._sessions, seconds=8):
                    raise RuntimeError('Managed worker sessions did not finish logout')
                if not _wait(lambda: not gateway.scheduler.snapshot(), seconds=8):
                    raise RuntimeError('Managed worker sockets did not cleanly unregister')
            finally:
                # Intentionally close the disposable tunnel and require the
                # SLH-owned supervisor to reconnect it without lab intervention.
                before = gateway.seh_connection.connections
                gateway.seh_connection.drop_for_test()
                gates['seh_configured_autoreconnect_after_drop'] = _wait(
                    lambda: gateway.seh_connection.connections > before and
                    gateway.seh_connection.connected, seconds=8)
                adapter.stop()
                gateway.stop()
                gates['seh_configured_disconnect_on_slh_stop'] = (
                    not gateway.seh_connection.connected)
                if relay is not None:relay.stop()
        with auth._db() as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('SLH authority database integrity failure after shutdown')
        gates['authority_database_clean_shutdown'] = True
        return {'gates': gates, 'pass': all(gates.values()), 'hub':hub_url,
                'paid_marketplace': paid,
                'topology_mode':topology_label,
                'limitations': {'production_seh':'COOLIFY_MANAGED_LOOPBACK' if deployed else 'LOCAL_RUNTIME_TESTED',
                                'coolify_live':'VERIFIED_LOCAL_COOLIFY' if deployed else 'NOT_TESTED',
                                'paid_marketplace_request':'LAB_PAID_SETTLED_NOT_BRIDGED',
                                'seh_transport':'DEPLOYED_SEH_LAB_CHANNEL' if deployed else 'REAL_SEH_RUNTIME_LOCAL'}}


def _mock_coolify_gate() -> dict:
    import base64
    from main_computer.slh.authenticator import generate_identity
    _, relay_public = generate_identity()
    from main_computer.slh.config import SEHTarget
    from tools.seh_harness import _local_verify
    target = SEHTarget.from_dict({
        'target_id':'slh-seh-lab', 'organization_id':'slh-seh-lab', 'slh_id':'lab-slh',
        'public_url':'https://hub.secure.example.com',
        'coolify_url':'http://127.0.0.1:38080', 'project_uuid':'mock-project',
        'environment_uuid':'mock-environment', 'server_uuid':'mock-server',
        'image':'example.invalid/main-computer/seh', 'image_digest':'sha256:'+'0'*64,
        'local_lab':True, 'slh_public_key_b64':base64.b64encode(relay_public.encode('ascii')).decode('ascii')})
    return _local_verify(target)


def _in_memory_hub():
    # Stable Hub's real server with in-memory stores, not an imitation of its API.
    from tests.test_stable_hub_worker_live_session import _start_server
    from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
    return _start_server('dev-hub3', InMemoryStableMultiSessionKeyStore(),
                         InMemoryStableWorkerSessionStore())


def verify_local() -> dict:
    server, thread = _in_memory_hub()
    try:
        trial = _local_demo(f'http://127.0.0.1:{server.server_port}',
                            topology_label='in-memory-stable-hub', payout_ledger=server.payout_ledger_directory)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    seh = _mock_coolify_gate()
    ok = trial['pass'] and seh['status']=='PASS'
    return {'schema':SCHEMA, 'mode':'verify-local', 'status':'PASS' if ok else 'FAIL',
            'slh':trial, 'seh_coolify_contract':seh, 'production_seh':'LOCAL_RUNTIME_TESTED_COOLIFY_NOT_TESTED',
            'paid_work_settlement':'LAB_LEDGER_SETTLED_NOT_BRIDGED'}


def _ports_free(urls):
    busy = []
    for url in urls:
        u = urlsplit(url)
        with socket.socket() as s:
            try:s.bind(('127.0.0.1',u.port))
            except OSError:busy.append(u.port)
    return busy


def _launch_cluster(topology_path, log_dir):
    from tools.stable_hub_lab.run_lab import _child_command, check_cluster, _terminate_children
    topo = load_stable_hub_topology(topology_path)
    busy = _ports_free([h.hub_url for h in topo.hubs])
    if busy:raise LabRefused(f'Smoke Hub ports already occupied: {busy}; use --use-existing-hubs only if they are verified smoke Hubs')
    children=[]
    logs=[]
    try:
        for hub in topo.hubs:
            log_file = open(log_dir/f'{hub.hub_id}.log','w',encoding='utf-8')
            logs.append(log_file)
            children.append(subprocess.Popen(_child_command(topology_path,hub.hub_id),
                                             stdout=log_file,stderr=subprocess.STDOUT,
                                             cwd=Path.cwd(), env={**os.environ,'PYTHONUNBUFFERED':'1'}))
        health = check_cluster(topology_path=topology_path,timeout=30)
        if not health['ok']:
            raise LabRefused('Smoke Hub startup failed: '+str(health['checks'])[:1000])
        return children,logs,health
    except BaseException:
        _terminate_children(children)
        for log in logs:log.close()
        raise


def run_smoke(topology, *, use_existing:bool, report_dir:Path, deployed:dict|None=None) -> dict:
    from tools.stable_hub_lab.run_lab import check_cluster, _terminate_children
    children=[];logs=[]
    try:
        if use_existing:
            health=check_cluster(topology_path=topology,timeout=4)
            if not health['ok']:
                raise LabRefused('Existing smoke cluster does not match configured topology')
        else:
            children,logs,health = _launch_cluster(topology,report_dir)
        # The Hub identity is verified for every node BEFORE any MSK or worker registration.
        identities = health['checks']
        if len(identities)!=3 or not all(x.get('ok') for x in identities):
            raise LabRefused('Hub identity preflight failed')
        from tools.stable_hub_lab.run_lab import require_durable_execution_cluster
        require_durable_execution_cluster(topology_path=topology,timeout=4)
        urls = [h.hub_url for h in load_stable_hub_topology(topology).hubs]
        # Issue the MSK against the entry Hub, open worker slots against the third Hub.
        # Stable Hub cluster uses FDB for multi-node key distribution. For the moment,
        # one Hub handles both issuance and worker sessions, while the identity check
        # covers all three. Cross-Hub owner-handoff remains a separate acceptance gate.
        from main_computer.stable_hub_worker_sessions import (
            stable_worker_session_store_from_topology, StableHubPayoutLedgerDirectory)
        topo=load_stable_hub_topology(topology)
        ledger=StableHubPayoutLedgerDirectory(topology=topo,hub_id=topo.hubs[2].hub_id,
            store=stable_worker_session_store_from_topology(topo))
        trial = _local_demo(urls[2],topology_label='three-hub-smoke-fdb', payout_ledger=ledger,
                            deployed=deployed)
        seh = _mock_coolify_gate() if deployed is None else {'status':'NOT_APPLICABLE_DEPLOYED'}
        ok = trial['pass'] and (deployed is not None or seh['status']=='PASS')
        return {'schema':SCHEMA,'mode':'verify-deployed' if deployed else 'run', 'status':'PASS' if ok else 'FAIL',
                'hub_cluster': {'ok':True,'ids':[x['hub_id'] for x in identities],
                                'cluster_id':health['cluster_id']},
                'slh':trial,'seh_coolify_contract':seh,
                'production_seh':'COOLIFY_MANAGED_LOCAL' if deployed else 'LOCAL_RUNTIME_TESTED_COOLIFY_NOT_TESTED',
                'paid_work_settlement':'LAB_LEDGER_SETTLED_NOT_BRIDGED'}
    finally:
        if children:_terminate_children(children)
        for log in logs:log.close()


def _seh_http_identity(port: int, path: str) -> dict:
    """Read only localhost SEH endpoints; never follow redirects or proxy externally."""
    from http.client import HTTPConnection
    if not 1024 <= port <= 65535 or path not in {'/healthz','/readyz','/lab-capabilities'}:
        raise LabRefused('Invalid local deployed SEH check')
    conn=HTTPConnection('127.0.0.1',port,timeout=3)
    try:
        conn.request('GET',path)
        res=conn.getresponse()
        body=res.read(4096)
        if res.status != 200:
            return {}
        payload=json.loads(body.decode('utf-8'))
        return payload if payload.get('service')=='main_computer.seh' else {}
    except (OSError,UnicodeError,ValueError,TimeoutError):
        return {}
    finally:
        conn.close()


def verify_deployed(topology: Path, *, use_existing:bool, report_dir:Path,
                    org:str, slh:str, port:int, compose_output:Path) -> dict:
    """Redeploy the existing LOCAL Coolify SEH, then prove a paid job through it.

    No in-process relay. A random isolated channel with the existing service key
    carries disposable worker identities and synthetic smoke Hub credits. The
    production SLH tunnel and its real organization/worker state stay untouched.
    """
    from .local_coolify import deploy_local, find_healthy_deployed
    from tools.slh_seh_connector import service_identity, default_state
    # Reuse the Coolify-managed resource untouched once its isolated-channel
    # runtime is installed. Upgrade that SAME service automatically the first
    # time, using Website Builder's local Coolify operator without a user token.
    # A working production tunnel is not a prerequisite for the isolated paid
    # lane. Observe it *before* any optional Coolify upgrade so that a tunnel
    # lost by our deployment is reported, rather than silently ignored.
    primary_before = _seh_http_identity(port, '/readyz').get('slh_connected') is True
    deployment=find_healthy_deployed(org=org,slh=slh,port=port)
    if deployment is None:
        deployment=deploy_local(org=org,slh=slh,port=port,
                                output=compose_output,build=True)
    if deployment.get('status')!='DEPLOYED_VERIFIED' or not deployment.get('service_uuid'):
        raise LabRefused('Existing local Coolify SEH deployment not verified')
    # Do NOT require /readyz here: it is 503 when the organization's optional
    # primary connector is down, even while the isolated lab relay is healthy.
    # The isolated SLH connector below authenticates independently of it.
    if not _seh_http_identity(port,'/healthz').get('live'):
        raise LabRefused('Coolify-managed SEH is not healthy after deployment')
    capabilities=_seh_http_identity(port,'/lab-capabilities')
    if capabilities.get('isolated_lab_channels') is not True:
        raise LabRefused('Coolify-managed SEH does not expose isolated authenticated lab channels')
    identity,_=service_identity(default_state(),create=False)
    from main_computer.slh.seh_transport import SLHSEHConnector
    lab_id=secrets.token_hex(16)
    trial=run_smoke(topology,use_existing=use_existing,report_dir=report_dir,deployed={
        'url':f'ws://127.0.0.1:{port}', 'org':org, 'slh':slh,
        'lab_id':lab_id, 'identity_key':identity})
    # Test recovery of the disposable tunnel after the paid cycle, without
    # disconnecting or restarting the organization's primary SLH connector.
    recovered=False
    for _ in range(30):
        replay=None
        try:
            replay=SLHSEHConnector(seh_url=f'ws://127.0.0.1:{port}',
                    organization_id=org, slh_id=slh, identity_private_key=identity,
                    gateway_port=1, allow_lab=True, lab_id=lab_id).connect()
            recovered=True
            break
        except (OSError,RuntimeError,ConnectionError,TimeoutError,ValueError):
            time.sleep(.1)
        finally:
            if replay is not None:replay.stop()
    # The production tunnel is a separate availability observation, not the
    # identity used by the disposable paid test. If it was connected before an
    # upgrade, losing it is a genuine regression and fails this acceptance run.
    # If absent at entry, do not call it "preserved" or count it as a PASS gate.
    if primary_before:
        primary_after = _wait(lambda: _seh_http_identity(port, '/readyz').get('slh_connected') is True,
                              seconds=15)
        trial['slh']['gates']['deployed_original_slh_tunnel_preserved'] = primary_after
    else:
        primary_after = _seh_http_identity(port, '/readyz').get('slh_connected') is True
    trial['primary_tunnel'] = {
        'connected_before': primary_before,
        'connected_after': primary_after,
        'status': ('PRESERVED' if primary_before and primary_after else
                   'LOST_DURING_VERIFICATION' if primary_before else
                   'NOT_CONNECTED_AT_START'),
        'required_for_isolated_paid_test': False,
    }
    trial['slh']['gates']['deployed_isolated_tunnel_reconnect'] = recovered
    trial['slh']['gates']['deployed_service_capability_verified'] = True
    trial['slh']['pass'] = all(trial['slh']['gates'].values())
    trial['status']='PASS' if trial['slh']['pass'] else 'FAIL'
    trial['coolify_deployment']={'service_uuid':deployment['service_uuid'],
                                 'service_name':deployment['service_name'],
                                 'health_url':deployment['health_url'],
                                 'image_status':deployment['image_status'],
                                 'local_automated':True,
                                 'existing_service_reused':deployment.get('existing_service_reused',False)}
    trial['public_wss']='NOT_TESTED_LOCAL_LOOPBACK_ONLY'
    return trial


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('plan','verify-local','run','verify-deployed','prepare-local-coolify','deploy-local-coolify'))
    p.add_argument('--topology',type=Path,default=DEFAULT_TOPOLOGY)
    p.add_argument('--use-existing-hubs',action='store_true',help='Only run on a verified, already-running smoke cluster; never stop it')
    p.add_argument('--report-dir',type=Path,default=REPORT_ROOT)
    p.add_argument('--json',action='store_true')
    p.add_argument('--build-image',action='store_true')
    p.add_argument('--reuse-image',action='store_true',help='Deploy previously built SEH image without rebuilding')
    p.add_argument('--org-id',default='my-company')
    p.add_argument('--slh-id',default='my-slh')
    p.add_argument('--local-port',type=int,default=18080)
    p.add_argument('--compose-output',type=Path,default=Path('runtime/reports/slh-seh-lab/local-coolify/compose.yml'))
    a=p.parse_args(argv)
    if a.command not in ('run','verify-deployed') and a.use_existing_hubs:
        p.error('--use-existing-hubs requires run or verify-deployed')
    if a.command in ('prepare-local-coolify','deploy-local-coolify'):
        from .local_coolify import prepare, deploy_local, LocalCoolifyError
        try:
            if a.command == 'deploy-local-coolify':
                report=deploy_local(org=a.org_id,slh=a.slh_id,port=a.local_port,
                                    output=a.compose_output,build=not a.reuse_image)
                # Explicit local deployment also configures the SLH service to
                # maintain the tunnel at its next startup. Never change the
                # organization's persisted routing policy in verification mode.
                if report['status']=='DEPLOYED_VERIFIED':
                    from tools.slh_seh_connector import default_state
                    from main_computer.slh.seh_autoconnect import (
                        SEHConnectionSettings, write_seh_settings)
                    write_seh_settings(default_state(), SEHConnectionSettings(
                        enabled=True, seh_url=f'ws://127.0.0.1:{a.local_port}',
                        organization_id=a.org_id, slh_id=a.slh_id,
                        allow_loopback_lab=True))
                    report['slh_autoconnect_configured']=True
            else:
                report=prepare(org=a.org_id,slh=a.slh_id,port=a.local_port,
                               output=a.compose_output,build=a.build_image)
        except (LocalCoolifyError, OSError, ValueError) as exc:
            report={'status':'FAIL','error':str(exc)}
        if a.json:print(json.dumps(report,indent=2,sort_keys=True))
        else:
            print('SEH Coolify local:',report['status'])
            if report['status']=='FAIL':print(' ',report['error'])
            else:
                print('  Image:',report['image_status'])
                print('  Compose:',report['compose'])
                if report['status']=='DEPLOYED_VERIFIED':
                    print('  Coolify service:',report['service_name'],report['service_uuid'])
                    print('  Deployment:',report['deployment_uuid'] or 'Coolify accepted')
                    print('  Health verified:',report['health_url'])
                    print('  Local Coolify authorization: automatic (no user key)')
                    if report.get('slh_autoconnect_configured'):
                        print('  SLH SEH autoconnection: CONFIGURED (active on SLH gateway startup)')
                else:
                    print('  Next: run deploy-local-coolify to deploy automatically')
                    print('  Verify:',report['health_url'])
        return 0 if report['status'] in ('PREPARED_NOT_DEPLOYED','DEPLOYED_VERIFIED') else 1
    if a.build_image or a.reuse_image:
        p.error('--build-image/--reuse-image requires a local Coolify command')
    report = None
    path = None
    try:
        topology=validate_topology(a.topology)
        if a.command=='plan':report=plan(a.topology)
        else:
            run_id = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3)
            path = a.report_dir/run_id
            path.mkdir(parents=True, exist_ok=False)
            # Keep Hub/WS request diagnostics in the run folder, not the operator's console.
            with (path/'lab-runtime.log').open('w', encoding='utf-8') as log:
                with redirect_stdout(log), redirect_stderr(log):
                    if a.command=='verify-local':report=verify_local()
                    elif a.command=='verify-deployed':
                        report=verify_deployed(a.topology,use_existing=a.use_existing_hubs,
                                               report_dir=path,org=a.org_id,slh=a.slh_id,
                                               port=a.local_port,compose_output=a.compose_output)
                    else:report=run_smoke(a.topology,use_existing=a.use_existing_hubs,report_dir=path)
            report['topology']=topology
    except (LabRefused, RuntimeError, OSError, ValueError, TimeoutError, ImportError) as exc:
        report={'schema':SCHEMA,'status':'FAIL', 'mode':a.command,
                'error_type':type(exc).__name__, 'error':str(exc)[:1200]}
    if path:
        report['report_path']=str((path/'report.json').resolve())
        (path/'report.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    if a.json:print(json.dumps(report,indent=2,sort_keys=True))
    else:
        print('SLH / SEH lab:',report['status'])
        if 'slh' in report:
            for k,v in report['slh']['gates'].items():
                print('  ', 'PASS' if v else 'FAIL', k)
            if a.command=='verify-deployed':
                print('  Coolify service:',report.get('coolify_deployment',{}).get('service_uuid','not verified'))
            else:
                print('  SEH deployment mock:',report['seh_coolify_contract']['status'])
        if report['status']=='FAIL':print('  ',report.get('error','See JSON report'), file=sys.stderr)
        if path:print('  Report:',report['report_path'])
        if 'slh' in report and 'paid_marketplace' in report['slh']:
            p=report['slh']['paid_marketplace']
            print('  Paid Stable Hub cycle:', 'PASS' if p.get('pass') else 'FAIL',
                  '(synthetic smoke credits; local ledger settlement only)')
        if a.command=='verify-deployed':
            if 'primary_tunnel' in report:
                print('  Production SLH tunnel:',report['primary_tunnel']['status'],
                      '(separate from isolated paid test)')
            print('  SEH: deployed Coolify loopback tested; public WSS and external bridge payout NOT TESTED')
        else:
            print('  SEH: real runtime local PASS; live Coolify/public WSS NOT TESTED; external bridge payout NOT TESTED')
    return 0 if report['status'] in {'PASS','PLANNED_ONLY'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
