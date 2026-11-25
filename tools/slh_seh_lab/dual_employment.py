"""One real managed Worker, two employers, one SEH transport.

Default: verified canonical deployment, isolated relay channel, disposable ORG
and in-memory Stable Hub. No deployed service changes or real credits. The
A protocol-conforming mock requester represents Great Library employment.
Direct Great Library application integration is outside this test scope.
--local-relay exercises identical logic against the deployable relay runtime.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import secrets
import tempfile
import threading
from types import SimpleNamespace
from urllib.request import build_opener, HTTPRedirectHandler, Request
from urllib.error import HTTPError, URLError

from .run_lab import _in_memory_hub, _wait

REPORT = Path('runtime/reports/slh-seh-dual-employment/report.json')
UNIT = 10**18
REQUIRED_CHECKS = frozenset({
    'isolated_relay_target_verified', 'worker_pending', 'worker_unapproved_denied',
    'worker_approved', 'requester_pending', 'requester_unapproved_denied',
    'requester_approved', 'managed_listener_ready', 'isolated_tunnel_authenticated',
    'single_worker_registered', 'only_worker_public', 'public_job_and_accounting',
    'public_job_same_worker_over_seh', 'earned_credit_available_for_org',
    'private_computation_correct', 'private_job_same_worker_over_seh',
    'exclusive_private_reservation_after_hub_close', 'private_replay_and_exact_obligation',
    'worker_readvertised_after_org_job', 'public_drained_before_reconnect',
    'old_worker_unregistered', 'tunnel_reconnected', 'same_identity_registered_after_reconnect',
    'revoked_worker_not_available', 'revoked_worker_connection_refused',
    'revoked_identity_challenge_refused',
})


class Refused(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RelayRefused(Refused):
    def __init__(self, reason, *, http_status=None):
        super().__init__(reason)
        self.http_status = http_status


def relay_observation(uri, path, *, evidence=None):
    if not uri.startswith('wss://') or path not in ('/healthz', '/readyz', '/lab-capabilities'):
        raise Refused('canonical-wss-required')
    record = evidence if evidence is not None else {}
    record['path'] = path
    request = Request('https://' + uri[6:].rstrip('/') + path,
                      headers={'Accept': 'application/json', 'User-Agent': 'Mozilla/5.0'},
                      method='GET')
    label = path[1:]
    try:
        with build_opener(NoRedirect()).open(request, timeout=8) as r:
            code = r.status
            record['http_status'] = code
            content = r.read(8193)
    except HTTPError as exc:
        code = exc.code
        record['http_status'] = code
        try:
            content = exc.read(8193) if path == '/readyz' and code == 503 else b''
        finally:
            exc.close()
    except (URLError, OSError, TimeoutError) as exc:
        record['error_class'] = type(exc).__name__
        raise RelayRefused(label + '-transport-failed') from None
    if code != 200 and not (path == '/readyz' and code == 503):
        raise RelayRefused(label + '-http-' + str(code), http_status=code)
    try:
        if len(content) > 8192:
            raise ValueError('oversized response')
        value = json.loads(content)
    except (ValueError, UnicodeError):
        raise RelayRefused(label + '-invalid-json', http_status=code) from None
    if not isinstance(value, dict) or value.get('service') != 'main_computer.seh':
        raise RelayRefused(label + '-seh-identity-unverified', http_status=code)
    record['service_verified'] = True
    for name in ('live', 'ready', 'bound', 'slh_connected', 'isolated_lab_channels', 'production_tunnel_untouched'):
        if name in value:
            record[name] = value[name] is True
    # A 503 is evidence of non-readiness, never a successful readiness receipt.
    if code == 503 and value.get('slh_connected') is True:
        raise RelayRefused(label + '-inconsistent-readiness', http_status=code)
    return value


def canonical_relay(report=None):
    from main_computer.slh.seh_canonical_connection import resolve_canonical_connection
    from tools.slh_seh_connector import default_state, service_identity
    report = report if report is not None else {}
    evidence = report.setdefault('relay_preflight', {})
    report['stage'] = 'canonical-resolution'
    state = default_state()
    settings = resolve_canonical_connection(state)
    uri = settings.seh_url
    evidence['canonical_resolution'] = 'VERIFIED'
    # Collect all three read-only observations before refusing; no fallback,
    # redeploy, new key, or isolated tunnel is attempted on a failed preflight.
    results = {}
    failures = {}
    for path in ('/healthz', '/readyz', '/lab-capabilities'):
        label = path[1:]
        report['stage'] = 'relay-preflight-' + label
        record = evidence.setdefault(label, {})
        try:
            results[path] = relay_observation(uri, path, evidence=record)
        except RelayRefused as exc:
            record['reason'] = str(exc)
            failures[path] = exc
    for path in ('/healthz', '/readyz', '/lab-capabilities'):
        if path in failures:
            report['stage'] = 'relay-preflight-' + path[1:]
            raise failures[path]
    report['stage'] = 'relay-preflight-healthz'
    if results['/healthz'].get('live') is not True:
        raise Refused('canonical-relay-not-live')
    report['stage'] = 'relay-preflight-readyz'
    if results['/readyz'].get('slh_connected') is not True:
        raise Refused('canonical-primary-tunnel-not-connected')
    report['stage'] = 'relay-preflight-lab-capabilities'
    caps = results['/lab-capabilities']
    if caps.get('isolated_lab_channels') is not True or caps.get('production_tunnel_untouched') is not True:
        raise Refused('isolated-channel-not-supported')
    report['stage'] = 'relay-preflight-existing-signing-key'
    key, _ = service_identity(state, create=False)
    evidence['existing_signing_key'] = 'AVAILABLE'
    return uri, key


def answer(challenge):
    # Real CPU work, not an echo and not a claim of real model inference.
    return hashlib.sha256(challenge.encode('utf-8')).hexdigest()


def require(report, name, value):
    report['checks'][name] = value is True
    if value is not True:
        raise Refused(name)


def exercise(report, *, local_relay=False):
    from main_computer.slh.authority import SLHAuthority
    from main_computer.slh.api import SLHAPI
    from main_computer.slh.authenticator import generate_identity, sign_object
    from main_computer.slh.managed_gateway import SLHManagedGateway, SLHMarketplaceProxy
    from main_computer.slh.managed_client import ManagedSLHWorkerClient
    from main_computer.slh.seh_transport import SEHLocalClientAdapter, SLHSEHConnector
    from main_computer.seh.relay import SEHRelay
    from main_computer.slh.organization_wallet import OrganizationWallet
    from main_computer.slh.private_requests import PrivateRequests
    from main_computer.slh.marketplace_auto import AutomaticMarketplace
    from main_computer.slh.scheduler_policy import SchedulerPolicyStore
    from main_computer.hub_cli_live_session import identify_hub_protocol, issue_cli_multisession_authorization
    from main_computer.multisession_key_signing import private_key_to_address
    from main_computer.hub_credit_indexer import wallet_account_id
    from tools.slh_authenticator_cli import save_identity, load_identity
    from tools.slh_seh_connector import service_identity
    from .paid_cycle import verify_paid_cycle
    from .credit_recovery_acceptance import _settle_disposable_recovery_earning, _verified_disposable_private_assignment

    check = lambda n, v: require(report, n, v)
    report['stage'] = 'relay-preflight'
    with ExitStack() as stack:
        if local_relay:
            signer, public = generate_identity()
            relay = SEHRelay(slh_public_key=public, host='127.0.0.1', port=0).start()
            stack.callback(relay.stop)
            uri = f'ws://127.0.0.1:{relay.port}'
        else:
            uri, signer = canonical_relay(report)
        check('isolated_relay_target_verified', True)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='mc-slh-two-service-lab-paid-')))
        state = root/'state'
        keys = root/'keys'
        service_identity(state, create=True)
        authority = SLHAuthority(state)
        owner, owner_public = generate_identity()
        statement = {'version':1,'action':'slh.admin.bootstrap','admin_id':'lab-owner',
                     'device_id':'lab-admin','public_key':owner_public}
        authority.bootstrap({**statement,'proof':sign_object(owner,statement),'admin_name':'Lab Owner',
                             'bootstrap_secret':(state/'bootstrap.secret').read_text().strip()},loopback=True)
        api = SLHAPI(authority)
        def call(method, path, body=None, *, token='', expected=200):
            status, data = api.call(method,path,body,token=token,loopback=True)
            if status != expected:
                error = data.get('error',{}).get('code','unknown')
                raise Refused(f'org-api-{status}-{error}')
            return data
        def login(device, key):
            c = call('POST','/v1/auth/challenge',{'device_id':device})
            return call('POST','/v1/auth/verify',{'device_id':device,'challenge_id':c['challenge_id'],
                                                 'signature':sign_object(key,c)})
        admin = login('lab-admin',owner)['access_token']
        stack.callback(authority.logout,admin)
        device = 'lab-dual-worker-'+secrets.token_hex(8)
        requester = 'lab-org-requester-'+secrets.token_hex(8)
        report['worker_device_id'] = device
        def enroll(did, role):
            save_identity(keys,did)
            key, public = load_identity(keys,did)
            req = {'selected_admin':'lab-owner','device_id':did,'public_key':public,'kind':role,
                   'device_name':did,'claimed_user':'Lab Owner','requested_role':role,
                   'client_nonce':secrets.token_urlsafe(24)}
            proof = sign_object(key,{**req,'version':1,'action':'slh.device.enrollment.request',
                                     'audience':'slh://'+authority.service_fingerprint()})
            pending = call('POST','/v1/enrollment/requests',{**req,'proof':proof},expected=201)
            check(role+'_pending',pending['status']=='PENDING_ADMIN')
            check(role+'_unapproved_denied',api.call('POST','/v1/auth/challenge',{'device_id':did})[0]==403)
            approved = call('POST','/v1/enrollment/approvals',{'request_id':pending['request_id']},token=admin,expected=201)
            check(role+'_approved',approved.get('device_id')==did and approved.get('status')=='APPROVED')
            return key
        report['stage']='signed-enrollment'
        enroll(device,'worker')
        requester_key = enroll(requester,'requester')
        request_token = login(requester,requester_key)['access_token']
        stack.callback(authority.logout,request_token)
        lab_id = secrets.token_hex(16)
        # The resident supervisor refuses injected identities outside loopback.
        # Do not change that guard or copy the canonical service key. Instead
        # use the existing isolated-channel connector in this disposable lab.
        gateway = SLHManagedGateway(authority,host='127.0.0.1',port=0,allow_seh=False)
        stack.callback(gateway.stop)
        gateway.start_background()
        report['stage']='isolated-tunnel'
        check('managed_listener_ready',_wait(lambda: gateway._server is not None,seconds=10))
        def connect_tunnel():
            tunnel = SLHSEHConnector(seh_url=uri,identity_private_key=signer,
                gateway_port=gateway.port,allow_lab=local_relay,lab_id=lab_id).connect()
            # Preserve the existing gateway-to-relay authenticated marker.
            gateway.seh_connection = tunnel
            return tunnel
        tunnel=connect_tunnel()
        check('isolated_tunnel_authenticated',tunnel._running.is_set())
        adapter = SEHLocalClientAdapter(uri,allow_lab=local_relay,lab_id=lab_id)
        stack.callback(adapter.stop)
        server, thread = _in_memory_hub()
        def shutdown_hub():
            server.shutdown(); server.server_close(); thread.join(timeout=5)
        stack.callback(shutdown_hub)
        ledger = server.payout_ledger_directory
        hub_url = f'http://127.0.0.1:{server.server_port}'
        directory = server.worker_session_directory
        proxy = SLHMarketplaceProxy(gateway)
        stack.callback(proxy.close)
        wallet_service = OrganizationWallet(state)
        private = PrivateRequests(state,authority,gateway,wallet_service=wallet_service,execution_timeout=25)
        api.private_requests = private
        controller = AutomaticMarketplace(proxy,state,wallet_service=wallet_service)
        private.marketplace_controller = controller
        gateway.scheduler.credit_provider = private.net_credit_status
        gateway.on_worker_registered = controller.wake
        gateway.on_worker_disconnected = controller.wake
        stack.callback(controller.stop)
        capability = 'slh.lab.dual.'+secrets.token_hex(12)
        observations=[]
        report['observations']=observations
        lock=threading.Lock()
        slot = {'id':None}
        def compute(offer):
            data = offer['work']['input']
            challenge = data.get('value') or data.get('prompt')
            if not isinstance(challenge,str) or not challenge:
                raise ValueError('missing-computation-challenge')
            rows = gateway.scheduler.snapshot()
            row = next(r for r in rows if r['device_id']==device)
            owner_row = directory.get_owner(slot['id']) if slot['id'] else None
            with lock:
                observations.append({'request_id':offer.get('request_id'),'generation':row['generation'],
                                     'reservation_types':row['reservation_types'],'public_advertised':row['public_advertised'],
                                     'prior_hub_state':(owner_row or {}).get('status')})
            return {'status':'success','response':{'model':'mock-model','content':answer(challenge)}}
        def client():
            c = ManagedSLHWorkerClient(device_id=device,identity_dir=keys,tls_fingerprint=gateway.fingerprint,
                hub_url=f'https://127.0.0.1:{adapter.server_address[1]}',worker_id=device,
                auth_message={'type':'worker.auth','device_id':device,'market':{'rings':['ring-2'],
                    'capabilities':['chat.completions',capability],'models':['mock-model'],
                    'price':{'amount':'1','unit':'compute_credit'},'max_concurrency':1}},work_executor=compute)
            stack.callback(c.close,reason='dual-employment-cleanup')
            return c
        report['stage']='worker-bank'
        worker = client(); worker.start()
        check('single_worker_registered',_wait(lambda: len(gateway.scheduler.snapshot())==1 and gateway.scheduler.snapshot()[0]['device_id']==device,seconds=15))
        generation = gateway.scheduler.snapshot()[0]['generation']
        report['worker_generation']=generation
        policy=SchedulerPolicyStore(authority)
        policy.update({'max_public_percent':100,'min_organization_percent':0,'min_private_idle_percent':0,
                       'local_credit_reserve_credits':'2'},expected_revision=0,actor='lab-owner')
        identity=identify_hub_protocol(hub_url)
        wallet_key='0x'+secrets.token_hex(32)
        wallet=SimpleNamespace(private_key=wallet_key,address=private_key_to_address(wallet_key))
        msk=issue_cli_multisession_authorization(hub_url,wallet=wallet,stable_identity=identity)
        controller.authorize({'kind':'multisession_key','multisession_key_id':msk['multisession_key_id']})
        controller.configure({'enabled':True,'hub_url':hub_url,'interval_seconds':1},expected_revision=0)
        controller.start()
        check('only_worker_public',_wait(lambda:set(proxy.sessions)=={device},seconds=25))
        slot['id']=proxy.sessions[device].accepted['connection_id']
        account=wallet_account_id(wallet.address)
        report['stage']='public-employment'
        before=adapter.bytes_forwarded
        paid=verify_paid_cycle(hub_url=hub_url,proxy=proxy,gateway=gateway,ledger=ledger,
            worker_account_id=account,lab_mode='in-memory-stable-hub',market_isolation_capability=capability,
            expected_content=lambda marker: hashlib.new("sha256", marker.encode("utf-8")).hexdigest())
        report['public_marketplace']=paid
        check('public_job_and_accounting',paid['pass'] is True and paid['worker_connection_id']==slot['id'] and
              paid['charged_credit_wei']==str(UNIT) and paid['earned_credit_wei']==str(UNIT))
        check('public_job_same_worker_over_seh',adapter.bytes_forwarded>before and
              len(observations)==1 and observations[0]['request_id']==paid['request_id'] and observations[0]['generation']==generation and
              observations[0]['reservation_types']==['public'] and observations[0]['prior_hub_state']=='live')
        report['stage']='verified_lab_settlement'
        report['lab_settlement']=_settle_disposable_recovery_earning(ledger=ledger,paid=paid,org_wallet_address=wallet.address)
        binding=controller.bind_wallet({'expected_revision':0,'expected_hub_id':identity['hub_id'],
            'expected_cluster_id':identity['cluster_id'],'expected_chain_id':str(ledger.topology.network['chain_id'])})
        check('earned_credit_available_for_org',binding['state']=='CURRENT' and int(binding['balance']['available_credit_wei'])==UNIT)
        report['stage']='org-private-employment'
        challenge='private-'+secrets.token_hex(24)
        rid='dual-private-'+secrets.token_hex(12)
        payload={'request_id':rid,'model':'mock-model','work':{'model':'mock-model','input':{'kind':'chat','prompt':challenge}}}
        before=adapter.bytes_forwarded
        response=call('POST','/v1/work',payload,token=request_token)
        report['private_status']=response.get('status'); report['private_failure_code']=response.get('failure_code')
        check('private_computation_correct',response.get('status')=='SUCCEEDED' and
              response.get('result',{}).get('response',{}).get('content')==hashlib.new('sha256',challenge.encode('utf-8')).hexdigest())
        check('private_job_same_worker_over_seh',adapter.bytes_forwarded>before and
              _verified_disposable_private_assignment(root,requester=requester,request_id=rid,public_result=response)==device and
              len(observations)==2 and observations[1]['request_id']==rid and observations[1]['generation']==generation)
        check('exclusive_private_reservation_after_hub_close',observations[1]['reservation_types']==['private'] and
              observations[1]['public_advertised'] is False and observations[1]['prior_hub_state']=='closed')
        replay=call('POST','/v1/work',payload,token=request_token)
        obligations=call('GET','/v1/accounting/obligations',token=admin)
        report['org_accounting']={'request_id':rid,'run_id':response['run_id'],'result_hash':response['result_hash'],
                                 'obligation_count':obligations['count'],'assessed_credit_wei':obligations['assessed_total_credit_wei']}
        rows=obligations['obligations']
        check('private_replay_and_exact_obligation',replay.get('run_id')==response['run_id'] and 'result' not in replay and len(observations)==2 and
              obligations['count']==1 and obligations['assessed_total_credit_wei']==str(UNIT//5) and len(rows)==1 and
              rows[0]['request_id']==rid and rows[0]['result_hash']==response['result_hash'] and rows[0]['run_id']==response['run_id'])
        check('worker_readvertised_after_org_job',_wait(lambda:device in proxy.sessions and proxy.sessions[device].accepted['connection_id']!=slot['id'],seconds=25))
        report['stage']='reconnect'
        controller.configure({'enabled':False},expected_revision=1)
        check('public_drained_before_reconnect',_wait(lambda:not proxy.sessions,seconds=25))
        tunnel.stop()
        check('old_worker_unregistered',_wait(lambda:not gateway.scheduler.snapshot(),seconds=15))
        worker.close(reason='dual-reconnect')
        # Reconnect the *same isolated channel*, not the primary tunnel. The
        # lab explicitly retries, so this is not automatic Worker recovery.
        renewed=None
        import time
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                renewed=connect_tunnel()
                break
            except (RuntimeError,OSError,TimeoutError,ConnectionError):
                time.sleep(.3)
        check('tunnel_reconnected',renewed is not None and renewed._running.is_set())
        restored=client(); restored.start()
        check('same_identity_registered_after_reconnect',_wait(lambda:len(gateway.scheduler.snapshot())==1 and gateway.scheduler.snapshot()[0]['device_id']==device and gateway.scheduler.snapshot()[0]['generation']!=generation,seconds=15))
        report['stage']='revocation'
        call('POST',f'/v1/admin/devices/{device}/revoke',{},token=admin)
        restored.close(reason='dual-revoked')
        check('revoked_worker_not_available',_wait(lambda:not gateway.scheduler.snapshot(),seconds=15) and not gateway.scheduler.is_authorized(device))
        rejected=client()
        denied=False
        try: rejected.start()
        except RuntimeError as exc: denied='Hub rejected worker.auth' in str(exc) and 'reason_code=SLHError' in str(exc)
        check('revoked_worker_connection_refused',denied and not gateway.scheduler.snapshot())
        check('revoked_identity_challenge_refused',api.call('POST','/v1/auth/challenge',{'device_id':device})[0]==403)
        report['observations']=observations
    if not local_relay:
        report['stage'] = 'primary-tunnel-postflight'
        check('primary_tunnel_still_connected',relay_observation(uri,'/readyz', evidence=report.setdefault('relay_postflight', {})).get('slh_connected') is True)
    if set(report['checks']) != REQUIRED_CHECKS | (set() if local_relay else {'primary_tunnel_still_connected'}):
        raise Refused('required-employment-evidence-missing')
    if not all(value is True for value in report['checks'].values()):
        raise Refused('employment-evidence-failed')
    report['employment_status']='PASS'
    report['stage']='complete'
    report['status']='PASS'
    report['reason']='verified'
    report['great_library_status']='REPRESENTED_BY_PROTOCOL_MOCK'


def run(*, local_relay=False, preflight_only=False):
    report={'schema':'mcel.slh.seh.dual-employment.v1','status':'UNVERIFIED','employment_status':'UNVERIFIED',
            'stage':'initialization','checks':{},'transport':'LOCAL_REAL_RELAY' if local_relay else 'CANONICAL_DEPLOYED_WSS_ISOLATED_CHANNEL',
            'great_library_status':'NOT_TESTED','scope':'disposable-org-real-managed-worker-deterministic-computation-in-memory-hub',
            'limitations':['no canonical Worker UI or production organization enrollment in this isolated channel',
                           'public requester is a protocol-conforming disposable client representing Great Library; direct application integration not tested',
                           'reconnect is explicitly driven by the test, not automatic production Worker recovery',
                           'operator settlement is in-memory only; no chain bridge or Reserve payment',
                           'deterministic CPU computation, not production model inference']}
    try:
        if preflight_only:
            canonical_relay(report)
            report['status'] = 'PASS_PREFLIGHT_ONLY'
            report['stage'] = 'relay-preflight-complete'
            report['reason'] = 'verified'
        else:
            exercise(report,local_relay=local_relay)
    except Exception as exc:
        report['error_class']=type(exc).__name__
        report['reason']=str(exc) if isinstance(exc,Refused) else report['stage']+'-failed'
        if isinstance(exc, HTTPError):
            report['http_status'] = exc.code
            report['reason'] += '-http-' + str(exc.code)
        elif isinstance(exc, RelayRefused) and exc.http_status is not None:
            report['http_status'] = exc.http_status
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--local-relay',action='store_true',help='Use actual local relay runtime instead of canonical deployment; never certify deployed SEH')
    p.add_argument('--preflight-only', action='store_true', help='Read-only canonical SEH discovery and HTTPS probes; no employment')
    args=p.parse_args(argv)
    if args.local_relay and args.preflight_only:
        p.error('--preflight-only requires the canonical deployed relay')
    report=run(local_relay=args.local_relay, preflight_only=args.preflight_only)
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    temp=REPORT.with_suffix('.partial'); temp.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8'); temp.replace(REPORT)
    for name, evidence in report.get('relay_preflight', {}).items():
        if isinstance(evidence, dict):
            print('SEH_EMPLOYMENT_PREFLIGHT', name, 'http='+str(evidence.get('http_status','NOT_AVAILABLE')),
                  'reason='+evidence.get('reason','observed'))
    for key,value in report['checks'].items(): print('SEH_EMPLOYMENT',key,'PASS' if value else 'FAIL')
    print('SEH_EMPLOYMENT',report['status'],'employment='+report['employment_status'],'stage='+report['stage'],
          'reason='+report.get('reason','verified'),'report='+str(REPORT))
    return 0 if report['status'] in ('PASS', 'PASS_PREFLIGHT_ONLY') else 1


if __name__=='__main__':
    raise SystemExit(main())
