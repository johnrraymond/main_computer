"""P0 ORG MSK and bidirectional marketplace acceptance, no live spending.

Default: actual spawned ORG-SLH, real loopback Stable Hub, disposable keys,
synthetic funding. The test deliberately starts with no pre-issued ORG MSK.
The production acceptance contracts are: POST admin marketplace authorization {"issue": true}, and POST
/v1/marketplace/work/requests (Known Device requester, Hub work without MSK;
GET the same path plus /{request_id} returns terminal status and result).
The existing external-MSK worker lane is a separately labelled control.
--resident: read-only pinned Known Device admin inspection of the actual ORG;
never issues an MSK, binds a wallet, changes policy, or submits paid work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from main_computer.hub_cli_live_session import identify_hub_protocol, issue_cli_multisession_authorization
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.multisession_key_signing import private_key_to_address
from main_computer.slh.authenticator import sign_object
from main_computer.slh.managed_client import ManagedSLHWorkerClient
from main_computer.slh.pinky_sweep import SIGNER_ENV, RESERVE_ENV
from main_computer.worker_live_session import _WorkerHubLiveSessionClient
from .service_lifecycle import _local, _ports, _http, _ok, _await, _login, _fingerprint
from .real_paid_service import RemoteMarketplace
from .pinky_integrated import _enroll
from .paid_cycle import verify_paid_cycle, _await_paid_terminal, PaidCycleDiagnostics
from .run_lab import _in_memory_hub

ISSUE = '/v1/org/admin/marketplace/authorization'
PUBLIC_REQUEST = '/v1/marketplace/work/requests'
REQUIRED = frozenset({
    'org_fresh_msk_signed_and_accepted_by_hub', 'msk_issue_admin_only',
    'org_wallet_bound', 'org_worker_public_result_and_credit',
    'org_requester_role_verified', 'org_requester_public_result_and_debit',
    'org_requester_replay_no_double_charge', 'org_public_request_worker_role_denied',
    'org_public_request_anonymous_denied', 'credentials_not_returned',
    'org_signing_origin_verified', 'org_public_spending_admin_only',
    'org_public_spending_default_denied', 'org_public_price_limit_denied',
    'org_public_budget_exhaustion_denied', 'org_public_forged_msk_denied',
})


def _new_report(scope):
    return {'schema': 'mcel.slh.org-msk-passthrough.v1', 'scope': scope,
            'status': 'UNVERIFIED', 'stage': 'start', 'checks': {}, 'observations': {},
            'limitations': ['synthetic credit and deterministic test execution in isolated mode',
                            'resident mode does not issue MSKs or submit jobs',
                            'direct ORG TLS requester lane; remote SEH requester relay not exercised',
                            'not evidence of live Mainnet/Testnet settlement']}


def _finish(report):
    gates = report['checks']
    report['missing'] = sorted(k for k in REQUIRED if gates.get(k) is not True)
    report['status'] = 'PASS' if not report['missing'] else 'BLOCKED'
    obs = report['observations']
    report['blockers'] = []
    if obs.get('org_issue', {}).get('error_code') == 'INVALID_MARKETPLACE_REQUEST':
        report['blockers'].append('ORG_MSK_ISSUE_CONTRACT_NOT_IMPLEMENTED')
    if obs.get('org_requester_submit', {}).get('error_code') == 'ENDPOINT_NOT_FOUND':
        report['blockers'].append('ORG_PUBLIC_REQUESTER_CONTRACT_NOT_IMPLEMENTED')
    return report


def _keys_for_wallet(store, address):
    return {kid: row for kid, row in store.load().get('keys', {}).items()
            if row.get('wallet_address') == address}


def _fresh_verified_key(store, before, address, chain):
    new = {kid: row for kid, row in _keys_for_wallet(store, address).items() if kid not in before}
    if len(new) != 1:
        return None
    kid, row = next(iter(new.items()))
    verified = row.get('verified') or {}
    if (row.get('status') == 'active' and verified.get('recovered_address') == address
            and verified.get('wallet_address') == address
            and str(verified.get('chain_id')) == str(chain)
            and verified.get('account_id') == wallet_account_id(address)):
        return kid
    return None


def _role_login(port, device, key, role):
    challenge = _ok(_http(port, 'POST', '/v1/auth/challenge', {'device_id': device}, tls=True))
    result = _ok(_http(port, 'POST', '/v1/auth/verify', {
        'device_id': device, 'challenge_id': challenge['challenge_id'],
        'signature': sign_object(key, challenge)}, tls=True))
    if result.get('role') != role:
        raise RuntimeError('unexpected-role')
    return result['access_token']


def _wallet():
    key = '0x' + secrets.token_hex(32)
    return SimpleNamespace(private_key=key, address=private_key_to_address(key))


def _market(capability):
    return {'rings': ['ring-2'], 'capabilities': ['chat.completions', capability],
            'models': ['mock-model'], 'price': {'amount': '1', 'unit': 'compute_credit'},
            'max_concurrency': 1}


def _result(offer):
    value = (offer['work']['input']).get('value', '')
    return {'status': 'success', 'response': {'model': 'mock-model',
        'content': hashlib.sha256(value.encode()).hexdigest()}}


def _http_observation(status, body):
    # Never persist bearer tokens, MSKs, full responses or signed requests.
    error = body.get('error') if isinstance(body, dict) else None
    code = error.get('code') if isinstance(error, dict) else None
    return {'http': status, 'error_code': code if isinstance(code, str) and
            len(code) <= 80 and code.isascii() and code.replace('_','').isalnum() else None}


def _no_secret(value, *secrets_to_check):
    text = json.dumps(value, sort_keys=True)
    return all(not secret or secret not in text for secret in secrets_to_check)


def _outbound_evidence(ledger, reqid, org_account, external_account, external_connection,
                       expected_result, before, after):
    state = ledger.store.load()
    rows = [r for r in state.get('accepted_sessions', {}).values()
            if r.get('request_id') == reqid and r.get('requester_account_id') == org_account]
    charges = [r for r in state.get('payout_charges', {}).values() if r.get('request_id') == reqid]
    earnings = [r for r in state.get('worker_earnings', {}).values() if r.get('request_id') == reqid]
    if len(rows) != 1 or len(charges) != 1 or len(earnings) != 1:
        return False
    row, charge, earning = rows[0], charges[0], earnings[0]
    amount = int(charge.get('charged_credit_wei') or 0)
    spent = int(after.get('spent_credit_wei') or 0) - int(before.get('spent_credit_wei') or 0)
    available = int(before.get('available_credit_wei') or 0) - int(after.get('available_credit_wei') or 0)
    return (row.get('status') == 'succeeded' and amount == spent == available == 10**18
        and int(after.get('held_credit_wei') or 0) == 0
        and charge.get('account_id') == org_account
        and charge.get('worker_account_id') == external_account
        and earning.get('worker_id') == external_account
        and earning.get('worker_node_id') == external_connection
        and int(earning.get('earned_credit_wei') or 0) == amount
        and charge.get('session_id') == earning.get('session_id') == row.get('session_id')
        and (row.get('worker_result') or {}).get('result', {}).get('response', {}).get('content') == expected_result)


def isolated():
    report = _new_report('actual-org-child-real-loopback-hub-disposable-wallets')
    g, obs = report['checks'], report['observations']
    hub, thread = _in_memory_hub()
    workers = []
    try:
        hub_url = f'http://127.0.0.1:{hub.server_port}'
        hub_identity = identify_hub_protocol(hub_url)
        if hub_identity.get('service') != 'main_computer.stable_hub':
            raise RuntimeError('wrong-hub')
        ledger, store = hub.payout_ledger_directory, hub.multisession_key_store
        org = _wallet()
        org_account = wallet_account_id(org.address)
        with tempfile.TemporaryDirectory(prefix='mc-slh-two-service-lab-org-msk-') as td:
            root = Path(td)
            state, identities = root / 'state', root / 'identities'
            signer_file = root / 'org-wallet.private'
            signer_file.write_text(org.private_key, encoding='ascii')
            signer_file.chmod(0o600)
            # Existing SLH-machine-only signer configuration, no reserve sweep.
            with patch.dict(os.environ, {SIGNER_ENV: str(signer_file), RESERVE_ENV: ''}), _local(state, identities) as local:
                report['stage'] = 'bootstrap-org'
                lp = local.server_port
                created = _ok(_http(lp, 'POST', '/v1/local/authenticator/identities',
                    {'device_id': 'lab-owner-device', 'label': 'Lab Owner'}), 201)
                secret = (state / 'bootstrap.secret').read_text(encoding='utf-8').strip()
                begun = _ok(_http(lp, 'POST', '/v1/bootstrap/begin', {'bootstrap_secret': secret}), 201)
                tx = begun['transaction_id']
                statement = {**begun['proof_context'], 'admin_id': 'lab-owner',
                             'device_id': 'lab-owner-device', 'public_key': created['public_key']}
                sig = _ok(_http(lp, 'POST', '/v1/local/authenticator/identities/lab-owner-device/sign',
                               {'challenge': statement}))['signature']
                _ok(_http(lp, 'PUT', f'/v1/bootstrap/{tx}/administrator', {'bootstrap_secret': secret,
                    'admin_id': 'lab-owner', 'admin_name': 'Lab Owner', 'device_id': 'lab-owner-device',
                    'public_key': created['public_key'], 'proof': sig}))
                _ok(_http(lp, 'POST', f'/v1/bootstrap/{tx}/activate-p256', {'bootstrap_secret': secret, 'defer_totp': True}))
                local_token = _login(lp, local_sign_port=lp)
                ap, wp = _ports()
                _ok(_http(lp, 'PATCH', '/v1/local/organization/config', {'host': '127.0.0.1',
                    'api_port': ap, 'worker_port': wp, 'workers_enabled': True, 'seh_enabled': False}, bearer=local_token))
                _ok(_http(lp, 'POST', '/v1/local/organization/enable', {}, bearer=local_token))
                _await(lambda: local.organization_controller.status()['listening'], 'organization listener', timeout=18)
                admin = _login(ap, local_sign_port=lp, tls=True)
                pin = _fingerprint(ap)
                proxy = RemoteMarketplace(ap, admin, {})
                proxy.configure(hub_url=hub_url, interval_seconds=1, enabled=False)
                audience = 'slh://' + local.organization_controller.authority.service_fingerprint()
                requester_key = _enroll(ap, admin, root/'requester', 'lab-org-requester', 'requester', audience=audience)
                worker_key = _enroll(ap, admin, root/'worker', 'lab-org-worker', 'worker', audience=audience)
                requester_token = _role_login(ap, 'lab-org-requester', requester_key, 'requester')
                worker_token = _role_login(ap, 'lab-org-worker', worker_key, 'worker')
                g['org_requester_role_verified'] = True
                report['stage'] = 'resident-msk-issuance'
                before = _keys_for_wallet(store, org.address)
                issue_body = {'issue': True}
                a = _http(ap, 'POST', ISSUE, issue_body, tls=True)[0]
                b = _http(ap, 'POST', ISSUE, issue_body, bearer=requester_token, tls=True)[0]
                c = _http(ap, 'POST', ISSUE, issue_body, bearer=worker_token, tls=True)[0]
                g['msk_issue_admin_only'] = (a == 401 and b == 403 and c == 403 and
                    _keys_for_wallet(store, org.address) == before)
                code, reply = _http(ap, 'POST', ISSUE, issue_body, bearer=admin, tls=True, timeout=20)
                obs['org_issue'] = _http_observation(code, reply)
                kid = _fresh_verified_key(store, before, org.address, hub_identity['network']['chain_id'])
                g['org_fresh_msk_signed_and_accepted_by_hub'] = bool(not before and code in (200,201) and kid)
                record = _keys_for_wallet(store,org.address).get(kid) or {}
                g['org_signing_origin_verified'] = bool(kid and record.get('origin') == 'org-slh'
                    and (record.get('verified') or {}).get('origin') == 'org-slh')
                if not g['org_fresh_msk_signed_and_accepted_by_hub']:
                    # Deliberately separate control. This cannot satisfy signer gate.
                    obs['worker_authorization_origin'] = 'EXTERNAL_TEST_SIGNER_CONTROL_ONLY'
                    upstream = issue_cli_multisession_authorization(hub_url, wallet=org, stable_identity=hub_identity)
                    kid = upstream['multisession_key_id']
                    proxy.authorize({'kind':'multisession_key','multisession_key_id':kid})
                else:
                    obs['worker_authorization_origin'] = 'ORG_SERVICE'
                proxy.upstream_auth = {'kind':'multisession_key','multisession_key_id':kid}
                # Bind before advertising. Hub validates the chosen account/chain.
                bind = _ok(_http(ap,'POST','/v1/org/admin/marketplace/wallet/bind', {
                    'expected_revision':0,'expected_hub_id':hub_identity['hub_id'],
                    'expected_cluster_id':hub_identity['cluster_id'],
                    'expected_chain_id':str(hub_identity['network']['chain_id'])},bearer=admin,tls=True))
                # wallet/bind response may wrap status; read actual authority status.
                wallet_view = proxy.status()['automatic']['organization_wallet']
                g['org_wallet_bound'] = (wallet_view.get('state')=='CURRENT' and
                    (wallet_view.get('binding') or {}).get('account_id')==org_account)
                report['stage'] = 'org-worker-public-passthrough'
                cap = 'slh.lab.org-msk.worker.' + secrets.token_hex(12)
                def worker_execute(offer):
                    # Existing paid-cycle verifier expects exact marker in response;
                    # test actual work callback, never Hub debug-echo path.
                    return {'status':'success','response':{'model':'mock-model','content':offer['work']['input']['value']}}
                worker = ManagedSLHWorkerClient(device_id='lab-org-worker', identity_dir=root/'worker',
                    tls_fingerprint=pin, hub_url=f'https://127.0.0.1:{wp}', worker_id='lab-org-worker',
                    auth_message={'type':'worker.auth','device_id':'lab-org-worker','market':_market(cap)}, work_executor=worker_execute)
                worker.start(); workers.append(worker)
                policy = _ok(_http(ap,'GET','/v1/admin/scheduler/policy',bearer=admin,tls=True))
                _ok(_http(ap,'PATCH','/v1/admin/scheduler/policy',{'expected_revision':policy['revision'],
                    'changes':{'max_public_percent':100,'min_private_idle_percent':0}},bearer=admin,tls=True))
                proxy.configure(hub_url=hub_url,enabled=True)
                _await(lambda: 'lab-org-worker' in proxy.sessions,'public Worker',timeout=20)
                paid = verify_paid_cycle(hub_url=hub_url,proxy=proxy,gateway=None,ledger=ledger,
                    worker_account_id=org_account,lab_mode='in-memory-stable-hub',market_isolation_capability=cap)
                _await(lambda: worker.last_result.get('request_id') == paid['request_id'],'managed result',timeout=10)
                worker_pass = bool(paid.get('pass') and
                    worker.last_result.get('type') == 'worker.work.result' and paid.get('worker_account_id')==org_account and
                    paid.get('charge_count')==paid.get('earning_count')==1)
                if g['org_fresh_msk_signed_and_accepted_by_hub']:
                    g['org_worker_public_result_and_credit'] = worker_pass
                else:
                    g['external_msk_worker_passthrough_control'] = worker_pass
                obs['worker_payout'] = {'charge_count':paid.get('charge_count'),'earning_count':paid.get('earning_count'),
                    'charged_credit_wei':paid.get('charged_credit_wei'),'earned_credit_wei':paid.get('earned_credit_wei')}
                report['stage'] = 'org-requester-public-passthrough'
                # An independent external Worker is necessary: self-buying must not count.
                external = _wallet(); external_account = wallet_account_id(external.address)
                external_auth = issue_cli_multisession_authorization(hub_url,wallet=external,stable_identity=hub_identity)
                cap2 = 'slh.lab.org-msk.requester.' + secrets.token_hex(12)
                executed = []
                def external_execute(offer):
                    executed.append(offer.get('request_id'))
                    return _result(offer)
                external_worker = _WorkerHubLiveSessionClient(hub_url=hub_url,worker_id='external-lab-worker',
                    auth_message={'type':'worker.auth','multisession_authorization':{'kind':'multisession_key','multisession_key_id':external_auth['multisession_key_id']},
                    'market':_market(cap2)},work_executor=external_execute,timeout_s=8)
                external_worker.start(); workers.append(external_worker)
                ledger.fund_account(account_id=org_account,wallet_address=org.address,credits='2',replace=False)
                reqid = 'org-outbound-' + secrets.token_hex(12)
                marker = secrets.token_hex(20)
                payload = {'request_id':reqid,'work':{'ring':'ring-2','max_price':{'amount':'1','unit':'compute_credit'},
                    'capabilities':['chat.completions',cap2],'model':'mock-model','input':{'kind':'chat','value':marker}}}
                balance_before = ledger.get_account(org_account)
                unauthorized = _http(ap,'POST',PUBLIC_REQUEST,payload,tls=True)[0]
                wrong_role = _http(ap,'POST',PUBLIC_REQUEST,payload,bearer=worker_token,tls=True)[0]
                deny_state = ledger.store.load()
                denied_no_side_effect = (ledger.get_account(org_account) == balance_before and not executed and
                    not any(r.get('request_id') == reqid for r in deny_state.get('accepted_sessions', {}).values()))
                g['org_public_request_anonymous_denied'] = unauthorized==401 and denied_no_side_effect
                g['org_public_request_worker_role_denied'] = wrong_role==403 and denied_no_side_effect
                obs['org_requester_role_probe_http'] = {'anonymous':unauthorized,'worker':wrong_role}
                # Prove a requester cannot spend or grant itself authority
                # merely by enrolling a device. No Hub activity may occur.
                policy_path = '/v1/org/admin/marketplace/requester-policy'
                policy = _ok(_http(ap,'GET',policy_path,bearer=admin,tls=True))
                grant = {'expected_revision':policy['revision'],'enabled':True,
                    'max_request_credit_wei':str(10**18),'budget_credit_wei':str(10**18)}
                policy_denied = [_http(ap,'PATCH',policy_path,grant,bearer=token,tls=True)[0]
                                 for token in (None,requester_token,worker_token)]
                g['org_public_spending_admin_only'] = (policy_denied == [401,403,403] and
                    _ok(_http(ap,'GET',policy_path,bearer=admin,tls=True)) == policy)
                disabled_code, disabled_body = _http(ap,'POST',PUBLIC_REQUEST,payload,bearer=requester_token,tls=True)
                g['org_public_spending_default_denied'] = (disabled_code == 403 and
                    _http_observation(disabled_code,disabled_body)['error_code']=='PUBLIC_REQUESTS_DISABLED' and
                    ledger.get_account(org_account)==balance_before and not executed)
                _ok(_http(ap,'PATCH',policy_path,grant,bearer=admin,tls=True))
                overpriced = {**payload,'work':{**payload['work'],'max_price':{'amount':'2','unit':'compute_credit'}}}
                price_code,price_body = _http(ap,'POST',PUBLIC_REQUEST,overpriced,bearer=requester_token,tls=True)
                g['org_public_price_limit_denied'] = (price_code==403 and
                    _http_observation(price_code,price_body)['error_code']=='PUBLIC_REQUEST_PRICE_LIMIT' and
                    ledger.get_account(org_account)==balance_before and not executed)
                forged = {**payload,'multisession_authorization':external_auth}
                forged_code,forged_body = _http(ap,'POST',PUBLIC_REQUEST,forged,bearer=requester_token,tls=True)
                g['org_public_forged_msk_denied'] = (forged_code==400 and
                    _http_observation(forged_code,forged_body)['error_code']=='INVALID_PUBLIC_REQUEST' and
                    ledger.get_account(org_account)==balance_before and not executed)
                code2, reply2 = _http(ap,'POST',PUBLIC_REQUEST,payload,bearer=requester_token,tls=True,timeout=25)
                obs['org_requester_submit'] = _http_observation(code2,reply2)
                result_reply, replay = {}, {}
                if code2 in (200,201):
                    sid = reply2.get('session_id')
                    if isinstance(sid,str) and sid:
                        _await_paid_terminal(ledger.store,sid,PaidCycleDiagnostics(None,hub_url),seconds=20)
                        expected=hashlib.sha256(marker.encode()).hexdigest()
                        after=ledger.get_account(org_account)
                        result_code,result_reply = _http(ap,'GET',PUBLIC_REQUEST+'/'+reqid,bearer=requester_token,tls=True,timeout=15)
                        obs['org_requester_result'] = _http_observation(result_code,result_reply)
                        g['org_requester_public_result_and_debit'] = (result_code == 200 and
                            str(result_reply.get('status','')).lower() == 'succeeded' and
                            (result_reply.get('result') or {}).get('response',{}).get('content') == expected and
                            executed == [reqid] and _outbound_evidence(ledger,reqid,org_account,external_account,
                            external_worker.accepted.get('connection_id'),expected,balance_before,after))
                        replay_code,replay = _http(ap,'POST',PUBLIC_REQUEST,payload,bearer=requester_token,tls=True,timeout=25)
                        g['org_requester_replay_no_double_charge'] = (replay_code in (200,201) and replay.get('session_id')==sid and executed == [reqid] and
                            _outbound_evidence(ledger,reqid,org_account,external_account,external_worker.accepted.get('connection_id'),expected,balance_before,ledger.get_account(org_account)))
                        extra = {**payload,'request_id':reqid+'-over-budget'}
                        budget_code,budget_body = _http(ap,'POST',PUBLIC_REQUEST,extra,bearer=requester_token,tls=True)
                        g['org_public_budget_exhaustion_denied'] = (budget_code==403 and
                            _http_observation(budget_code,budget_body)['error_code']=='PUBLIC_REQUEST_BUDGET_LIMIT' and
                            executed==[reqid] and ledger.get_account(org_account)==after)
                    else:
                        g['org_requester_public_result_and_debit']=False
                else:
                    g['org_requester_public_result_and_debit']=False
                    obs['requester_hub_debit_absent']=(ledger.get_account(org_account)==balance_before)
                g['credentials_not_returned'] = _no_secret([reply,reply2,result_reply,replay,proxy.status(),worker.last_result], kid,org.private_key,admin,requester_token,worker_token)
                report['stage']='complete'
                return _finish(report)
    except Exception as exc:
        report['error_class']=type(exc).__name__
        return _finish(report)
    finally:
        for worker in reversed(workers):
            try: worker.close(reason='org-msk-acceptance-cleanup')
            except Exception: pass
        hub.shutdown(); hub.server_close(); thread.join(timeout=3)


RESIDENT_REQUIRED = frozenset({
    'resident_lms_default_discovered', 'resident_admin_verified',
    'resident_marketplace_status_verified', 'resident_mainnet_wallet_current',
    'resident_authorization_present', 'resident_test_session_revoked',
    'resident_financial_readiness_verified',
})


def _resident_finish(report):
    gates, obs = report['checks'], report['observations']
    report['missing'] = sorted(k for k in RESIDENT_REQUIRED if gates.get(k) is not True)
    report['blockers'] = []
    if report.get('error_code'):
        report['blockers'].append(report['error_code'])
    if gates.get('resident_marketplace_status_verified') is True:
        if not gates.get('resident_mainnet_wallet_current'):
            report['blockers'].append('RESIDENT_MAINNET_WALLET_NOT_CURRENT')
        if not gates.get('resident_authorization_present'):
            report['blockers'].append('RESIDENT_MSK_NOT_PRESENT')
        if not gates.get('resident_financial_readiness_verified'):
            report['blockers'].append('RESIDENT_FINANCIAL_STARTUP_NOT_READY')
        if obs.get('connected_worker_count', 0) and gates.get('resident_worker_accounts_match_binding') is not True:
            report['blockers'].append('RESIDENT_WORKER_ACCOUNT_MISMATCH')
    if gates.get('resident_admin_verified') and not gates.get('resident_test_session_revoked'):
        report['blockers'].append('RESIDENT_SESSION_CLEANUP_UNVERIFIED')
    report['status'] = 'PASS_PREFLIGHT_ONLY' if not report['missing'] and not report['blockers'] else 'BLOCKED'
    return report


def resident(*, restart_evidence=False):
    """Discover and sign through the running LMS, never the CLI key store."""
    from main_computer.mcel_slh_application import observe_lms_slh_identities, IDENTITIES_SCHEMA
    from main_computer.mcel_slh_first_run import SlhFirstRunController
    from main_computer.mcel_slh_worker_bridge import OrganizationWorkerClient
    from main_computer.hub_networks import load_hub_network_registry
    report = _new_report('resident-org-read-only-preflight')
    g, obs = report['checks'], report['observations']
    token = None
    client = None
    process_before = None
    try:
        report['stage'] = 'resident-lms-inventory'
        controller = SlhFirstRunController()
        if restart_evidence:
            from . import resident_restart as restart
            report['stage'] = 'resident-process-identity'
            process_before = restart.process_identity(controller.state_dir)
        inventory = observe_lms_slh_identities(port=controller.local_port)
        state = inventory.get('state')
        obs['lms_inventory_state'] = state if state in ('AVAILABLE', 'UNAVAILABLE', 'UNVERIFIED') else 'UNKNOWN'
        if inventory.get('schema') != IDENTITIES_SCHEMA or state != 'AVAILABLE':
            report['error_code'] = 'RESIDENT_LMS_INVENTORY_UNAVAILABLE'
            return _resident_finish(report)
        did = inventory.get('defaultDeviceId')
        rows = inventory.get('identities')
        if not isinstance(did, str) or not did or not isinstance(rows, list) or sum(
                isinstance(x, dict) and x.get('deviceId') == did and x.get('defaultStatus') == 'DEFAULT'
                for x in rows) != 1:
            report['error_code'] = 'RESIDENT_LMS_DEFAULT_IDENTITY_UNVERIFIED'
            return _resident_finish(report)
        g['resident_lms_default_discovered'] = True
        obs['lms_identity_count'] = len(rows)
        report['stage'] = 'resident-admin-auth'
        # Same login implementation as MCEL's SLH login, using the LMS signer
        # and ORG's pinned TLS challenge. No bootstrap or enable operations.
        client = OrganizationWorkerClient(state_dir=controller.state_dir)
        session = controller.login(device_id=did)
        token = session['access_token']
        if session.get('role') != 'admin' or session.get('device_id') != did:
            report['error_code'] = 'RESIDENT_ADMIN_SESSION_UNVERIFIED'
        else:
            g['resident_admin_verified'] = True
            report['stage'] = 'resident-marketplace-status'
            status = client.request('GET', '/v1/org/admin/marketplace/status', token)
            automatic = status.get('automatic')
            if (status.get('schema') != 'main-computer.slh-org-marketplace.v1' or
                    not isinstance(automatic, dict) or
                    automatic.get('schema') != 'main-computer.slh-marketplace-auto.v1' or
                    not isinstance(automatic.get('configuration'), dict) or
                    not isinstance(automatic.get('organization_wallet'), dict) or
                    automatic['organization_wallet'].get('schema') != 'main-computer.slh-org-wallet.v1' or
                    not isinstance(status.get('sessions'), dict) or
                    not all(isinstance(row, dict) for row in status['sessions'].values())):
                report['error_code'] = 'RESIDENT_MARKETPLACE_STATUS_INVALID'
            else:
                cfg = automatic['configuration']
                wallet = automatic['organization_wallet']
                binding = wallet.get('binding') or {}
                profiles = load_hub_network_registry().networks
                g['resident_marketplace_status_verified'] = True
                obs['wallet_state'] = wallet.get('state', 'UNKNOWN')
                reason = wallet.get('reason')
                if isinstance(reason, str) and len(reason) <= 80 and reason.isascii() and reason.upper() == reason and reason.replace('_', '').isalnum():
                    obs['wallet_reason'] = reason
                obs['marketplace_enabled'] = cfg.get('enabled') is True
                obs['authorization_present'] = automatic.get('authorization_present') is True
                g['resident_authorization_present'] = obs['authorization_present']
                obs['configured_hub_network'] = next((key for key,p in profiles.items() if p.hub_url.rstrip('/') == str(cfg.get('hub_url') or '').rstrip('/')), 'UNKNOWN')
                connected = [row for row in status['sessions'].values() if row.get('connected') is True]
                obs['connected_worker_count'] = len(connected)
                mainnet = profiles['mainnet']
                g['resident_mainnet_wallet_current'] = (wallet.get('state') == 'CURRENT' and
                    str(binding.get('chain_id')) == str(mainnet.chain_id) and
                    str(binding.get('hub_url') or '').rstrip('/') == mainnet.hub_url.rstrip('/') and
                    binding.get('trust_mode') in ('SYSTEM_TLS', 'TLS_PINNED') and
                    bool(binding.get('account_id')) and obs['configured_hub_network'] == 'mainnet')
                readiness = automatic.get('financial_readiness') or {}
                if not isinstance(readiness, dict):
                    readiness = {}
                state = readiness.get('state')
                obs['financial_readiness'] = state if state in ('READY', 'BLOCKED', 'INITIALIZING', 'NOT_MANAGED') else 'UNKNOWN'
                reason = readiness.get('reason')
                if isinstance(reason, str) and reason.isascii() and len(reason) <= 80 and reason.isupper() and reason.replace('_', '').isalnum():
                    obs['financial_readiness_reason'] = reason
                g['resident_financial_readiness_verified'] = (state == 'READY' and readiness.get('network') == 'mainnet'
                    and readiness.get('wallet_address') == binding.get('wallet_address') and bool(binding.get('wallet_address'))
                    and g['resident_mainnet_wallet_current'] and g['resident_authorization_present'])
                obs['wallet_address'] = binding.get('wallet_address')  # public address only
                if connected:
                    g['resident_worker_accounts_match_binding'] = bool(binding.get('account_id') and all(
                        row.get('account_id') == binding['account_id'] for row in connected))
                else:
                    obs['worker_account_check'] = 'NOT_TESTED_NO_CONNECTED_WORKERS'
                # Project only numeric accounting observations, never signing material.
                balance = wallet.get('balance')
                if isinstance(balance, dict):
                    obs['wallet_balance_wei'] = {k: str(balance[k]) for k in (
                        'available_credit_wei', 'held_credit_wei', 'spent_credit_wei',
                        'bridge_completed_credit_wei', 'hub_earned_credit_wei')
                        if type(balance.get(k)) in (str, int) and str(balance[k]).isascii() and str(balance[k]).isdigit()}
                if restart_evidence:
                    report['stage'] = 'resident-restart-evidence'
                    policy = client.request('GET', restart.POLICY_PATH, token)
                    report['restart_evidence'] = {'schema': restart.SCHEMA, 'process': process_before,
                        'binding': restart.binding_evidence(binding),
                        'policy': restart.policy_evidence(cfg, policy),
                        'readiness_checked_at': readiness.get('checked_at'),
                        'signer_acl': restart.signer_acl(controller.state_dir)}
                report['stage'] = 'read-only-complete'
    except Exception as exc:
        report['error_class'] = type(exc).__name__
        code = getattr(exc, 'code', None)
        if isinstance(code, str) and code.isascii() and code.replace('_', '').isalnum() and code.upper() == code and len(code) <= 80:
            report['error_code'] = code
        else:
            report['error_code'] = 'RESIDENT_OPERATION_UNVERIFIED'
    finally:
        if token and client is not None:
            try:
                client.logout(token)
                g['resident_test_session_revoked'] = True
            except Exception:
                g['resident_test_session_revoked'] = False
    if restart_evidence and report.get('restart_evidence'):
        evidence = report['restart_evidence']
        try:
            evidence['process_stable'] = restart.process_identity(controller.state_dir) == process_before
        except Exception:
            evidence['process_stable'] = False
        evidence['captured_at'] = restart.time.time()
    return _resident_finish(report)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--resident',action='store_true',help='Read-only resident Mainnet wallet preflight')
    modes = p.add_mutually_exclusive_group()
    modes.add_argument('--restart-baseline', action='store_true', help='Capture read-only pre-restart evidence')
    modes.add_argument('--verify-restart', action='store_true', help='Compare against saved baseline after normal restart')
    p.add_argument('--report-dir',type=Path,default=Path('runtime/reports/slh-org-msk-passthrough'))
    args=p.parse_args(argv)
    if (args.restart_baseline or args.verify_restart) and not args.resident:
        p.error('restart checks require --resident')
    if args.restart_baseline or args.verify_restart:
        from . import resident_restart as restart
        args.report_dir.mkdir(parents=True, exist_ok=True)
        baseline = args.report_dir / 'resident-restart-before.json'
        report = resident(restart_evidence=True)
        if args.restart_baseline:
            path = baseline if restart.snapshot_valid(report) else args.report_dir / 'resident-restart-baseline-failed.json'
            result = 'BASELINE_READY' if restart.snapshot_valid(report) else 'BLOCKED'
            output = report
            output['restart_checks'] = restart.snapshot_checks(report)
        else:
            try:
                before = json.loads(baseline.read_text(encoding='utf-8'))
                if not isinstance(before, dict): before = {}
            except (OSError, ValueError):
                before = {}
            output = restart.compare(before, report)
            path = args.report_dir / 'resident-restart-after.json'
            result = output['status']
        tmp = path.with_suffix('.partial')
        tmp.write_text(json.dumps(output, indent=2, sort_keys=True)+'\n', encoding='utf-8')
        tmp.replace(path)
        for k,v in output.get('restart_checks', output.get('checks', {})).items(): print('ORG_MSK_RESTART', k, 'PASS' if v is True else 'FAIL')
        for k,v in output.get('current_checks', {}).items(): print('ORG_MSK_RESTART current', k, 'PASS' if v is True else 'FAIL')
        acl = (report.get('restart_evidence') or {}).get('signer_acl') or {}
        for node in ('directory', 'file'):
            for reason in acl.get(node + '_failure_reasons', []):
                print('ORG_MSK_RESTART ACL', node, reason)
            for entry in acl.get(node + '_unexpected_principals', []):
                print('ORG_MSK_RESTART ACL_DETAIL', node, json.dumps(entry, sort_keys=True))
        if report.get('error_code'): print('ORG_MSK_RESTART diagnostic', report['stage'], report['error_code'])
        print('ORG_MSK_RESTART', result, 'report='+str(path))
        return 0 if result in ('BASELINE_READY', 'PASS_RESTART') else 1
    report=resident() if args.resident else isolated()
    args.report_dir.mkdir(parents=True,exist_ok=True)
    path=args.report_dir/('resident.json' if args.resident else 'report.json')
    path.write_text(json.dumps(report,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    for name,value in report['checks'].items(): print('ORG_MSK',name,'PASS' if value else 'FAIL')
    for name,value in report['observations'].items(): print('ORG_MSK',name,json.dumps(value,sort_keys=True))
    for blocker in report.get('blockers', []): print('ORG_MSK BLOCKER', blocker)
    print(f"ORG_MSK {report['status']} stage={report['stage']} report={path}")
    return 0 if report['status'] in ('PASS','PASS_PREFLIGHT_ONLY') else 1


if __name__=='__main__': raise SystemExit(main())
