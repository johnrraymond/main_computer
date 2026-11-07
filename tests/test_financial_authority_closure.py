"""P0 final Hub financial-authority sweep (isolated loopback, no mainnet).

This is an exhaustive *declared dispatch* oracle: every branch in the two
production Hub POST dispatchers has a disposition. New branches require a
new explicit policy decision before security acceptance can pass.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
from threading import Thread

import pytest

from main_computer.config import MainComputerConfig
from main_computer.hub import HubHttpServer, HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID
from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address
from main_computer.stable_hub import create_stable_hub_server
from main_computer.stable_hub_topology import load_stable_hub_topology

ROOT = Path(__file__).resolve().parents[1]
# Explicit review: exact normalized ast.unparse(test) of every POST dispatcher
# branch. Comments classify the required authority. The inventory is not a
# permissive wildcard registry: unknown branches fail the oracle.
LEGACY_POST_POLICIES = {
    "path == '/api/hub/remote-overflow/safe-chat'": 'wallet-msk-paid-overflow',
    "path == '/api/hub/v1/credits/multisession-keys/request'": 'wallet-personal-sign',
    "path == '/api/hub/v1/credits/multisession-keys/revoke'": 'wallet-personal-sign-revoke-intent-bound',
    "path == '/api/hub/v1/credits/multisession-keys/validate'": 'readiness-only',
    "path in {'/api/hub/workers/connect', '/api/hub/v1/workers/connect'}": 'wallet-msk-worker',
    "path in {'/api/hub/workers/register', '/api/hub/v1/workers/register'}": 'wallet-msk-worker',
    "path in {'/api/hub/v1/workers/heartbeat', '/api/hub/workers/heartbeat'}": 'wallet-msk-worker',
    "path.startswith('/api/hub/v1/workers/') and path.endswith('/heartbeat')": 'wallet-msk-worker',
    "path in {'/api/hub/v1/workers/poll', '/api/hub/workers/poll'}": 'wallet-msk-worker',
    "path in {'/api/hub/v1/workers/stream-events', '/api/hub/workers/stream-events'}": 'wallet-msk-worker',
    "path in {'/api/hub/v1/workers/results', '/api/hub/workers/results'}": 'wallet-msk-worker',
    "path == '/api/hub/sessions/start'": 'nonfinancial-secure-chat',
    "path == '/api/hub/sessions/chat'": 'nonfinancial-secure-chat',
    "path == '/api/hub/v1/credits/wallet-funding/complete'": 'chain-deposit-receipt-verified',
    "path == '/api/hub/v1/requests/quote'": 'nonmutating-quote',
    "path == '/api/hub/v1/requests'": 'wallet-msk-if-worker-pull',
    "path.startswith('/api/hub/v1/requests/') and path.endswith('/feedback')": 'client-feedback-not-money',
    "path == '/api/hub/chat'": 'nonfinancial-chat',
}
STABLE_POST_POLICIES = {
    "path == '/api/hub/v1/credits/multisession-keys/request'": 'wallet-personal-sign',
    "path == '/api/hub/v1/credits/multisession-keys/validate'": 'readiness-only',
    "path == '/api/hub/v1/credits/pinky-reserve/sweep'": 'wallet-signature-recipient-fixed',
    "path == '/api/hub/v1/credits/wallet-funding/complete'": 'chain-deposit-receipt-verified',
    "path == '/api/hub/v1/work/requests'": 'buyer-msk-authorization',
    "path.startswith('/api/hub/v1/workers/') and path.endswith('/payout/claim')": 'worker-msk-earnings-owner',
    "path.startswith('/api/hub/v1/workers/') and path.endswith('/payout/settlements')": 'worker-msk-batch-create-only',
    "path.startswith('/api/hub/v1/workers/') and path.endswith('/payout/bridge')": 'worker-msk-bridge-request-only',
}

# The separately served Worker-side HTTP ingress is nonfinancial, but every
# exposed POST route is still inventoried to prevent mutation aliases here.
WORKER_SIDE_POST_POLICIES = {
    "parsed.path == HUB_WORKER_SESSION_START_PATH": "ephemeral-encrypted-session-nonfinancial",
    "parsed.path == HUB_WORKER_SESSION_CHAT_PATH": "session-bound-encrypted-chat-nonfinancial",
    "parsed.path == HUB_WORKER_CHAT_PATH": "legacy-chat-nonfinancial",
}

# All previous removals are protected in addition to the new P0 deletion set.
PREVIOUS_DELETIONS = {
    '/api/hub/v1/credits/admin/issue',
    '/api/hub/v1/credits/deposits/import',
    '/api/hub/v1/credits/purchases/import',
    '/api/hub/v1/credits/wallet-funding/import',
    '/api/hub/v1/credits/bridge-reconciliation/record',
    '/api/hub/v1/credits/bridge-reconciliation/execute',
    '/api/hub/v1/bridge/payouts',
    '/api/hub/v1/bridge/payouts/confirm',
    '/api/hub/v1/bridge/payouts/fail',
    '/api/hub/v1/workers/claims',
    '/api/hub/v1/credits/worker-claims/record',
    '/api/hub/v1/workers/settlements/batches',
    '/api/hub/v1/credits/worker-settlements/batches',
    '/api/hub/v1/workers/settlements/batches/settle',
    '/api/hub/v1/workers/settlements/settle',
    '/api/hub/v1/credits/worker-settlements/batches/settle',
    '/api/hub/v1/workers/settlements/chain-executions',
    '/api/hub/v1/workers/settlements/batches/chain-execution',
    '/api/hub/v1/credits/worker-settlements/chain-executions',
    '/api/hub/v1/workers/settlements/proofs',
    '/api/hub/v1/workers/settlements/batches/proof',
    '/api/hub/v1/credits/worker-settlements/proofs',
    '/api/hub/v1/bridge/mock-chain/mint',
    '/api/hub/v1/bridge/deposits',
    '/api/hub/v1/bridge/deposits/confirm',
    '/api/hub/payouts/claim',
    '/api/hub/v1/payouts/claim',
    '/api/hub/v1/payout/bridge/example/confirm',
    '/api/hub/v1/payout/bridge/example/fail',
}
NEW_DELETIONS = {
    '/api/hub/upstreams/register',
    '/api/hub/v1/upstreams/register',
    '/api/hub/v1/requests/fake-session/cancel',
}


def _dispatch_conditions(filename: str, klass: str) -> list[str]:
    source = ast.parse((ROOT / 'main_computer' / filename).read_text(encoding='utf-8'))
    cls = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == klass)
    func = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'do_POST')
    return [ast.unparse(node.test) for node in ast.walk(func)
            if isinstance(node, ast.If) and 'path' in ast.unparse(node.test)]


def test_every_hub_post_dispatch_has_a_documented_authority_policy():
    assert set(_dispatch_conditions('hub.py', 'HubServerHandler')) == set(LEGACY_POST_POLICIES)
    assert set(_dispatch_conditions('stable_hub.py', 'StableHubRequestHandler')) == set(STABLE_POST_POLICIES)
    assert set(_dispatch_conditions('hub.py', 'HubWorkerHandler')) == set(WORKER_SIDE_POST_POLICIES)


def test_no_previous_deleted_financial_path_reentered_handler():
    for filename, cls in [('hub.py', 'HubServerHandler'), ('stable_hub.py', 'StableHubRequestHandler')]:
        source = ast.parse((ROOT / 'main_computer' / filename).read_text(encoding='utf-8'))
        c = next(x for x in source.body if isinstance(x, ast.ClassDef) and x.name == cls)
        f = next(x for x in c.body if isinstance(x, ast.FunctionDef) and x.name == 'do_POST')
        literals = {node.value for node in ast.walk(f) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        assert literals.isdisjoint(PREVIOUS_DELETIONS | NEW_DELETIONS)


def _post(port: int, path: str, body: dict | bytes) -> tuple[int, dict | str, bool]:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode('utf-8')
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=6)
    try:
        conn.request('POST', path, body=raw, headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        payload = response.read()
        try:
            result = json.loads(payload)
        except ValueError:
            result = payload.decode('utf-8', errors='replace')
        return response.status, result, response.will_close
    finally:
        conn.close()


@pytest.fixture
def legacy(tmp_path):
    cfg = MainComputerConfig(workspace=tmp_path, model='fake-model', hub_root=tmp_path/'hub-runtime',
                             hub_bridge_backend='mock-chain', hub_require_multisession_auth=False)
    srv = HubHttpServer(('127.0.0.1', 0), cfg, verbose=False)
    thr = Thread(target=srv.serve_forever, daemon=True); thr.start()
    yield srv
    srv.shutdown(); srv.server_close(); thr.join(timeout=5)


@pytest.fixture
def stable():
    topology = load_stable_hub_topology(ROOT / 'deploy/hub-topology/dev-topology.json')
    from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
    from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
    srv = create_stable_hub_server(topology=topology, hub_id='dev-hub1',
                                   bind_host='127.0.0.1', bind_port=0,
                                   multisession_key_store=InMemoryStableMultiSessionKeyStore(),
                                   worker_session_store=InMemoryStableWorkerSessionStore())
    thr = Thread(target=srv.serve_forever, daemon=True); thr.start()
    yield srv
    srv.shutdown(); srv.server_close(); thr.join(timeout=5)


@pytest.mark.parametrize('path', sorted(NEW_DELETIONS))
def test_legacy_unauthenticated_topology_and_cancellation_are_absent(legacy, path):
    before = legacy.registry.status()
    energy_before = json.dumps(legacy.energy_ledger._load(),sort_keys=True)
    status, _, closed = _post(legacy.server_port,path,{
        'node_id':'forged-upstream','endpoint':'http://attacker.invalid',
        'credits_per_request':'1','request_id':'fake-session','account_id':'victim',
    })
    assert status == 404 and closed
    after = legacy.registry.status()
    assert after['worker_count'] == before['worker_count']
    assert after['upstream_count'] == before['upstream_count']
    assert after['upstream_hubs'] == before['upstream_hubs']
    current_energy = legacy.energy_ledger._load()
    initial_energy = json.loads(energy_before)
    for item in ('nodes','transactions','worker_payouts','worker_payout_claims'):
        assert current_energy.get(item) == initial_energy.get(item)


def test_walletless_worker_registration_and_settlement_are_refused_even_with_old_opt_out(legacy):
    before = json.dumps(legacy.credit_ledger.status(),sort_keys=True)
    status, reply, _ = _post(legacy.server_port,'/api/hub/v1/workers/register', {
        'node_id':'untrusted-worker','endpoint':'http://127.0.0.1:9',
        'model':'fake-model', 'models':['fake-model'], 'credits_per_request':3})
    assert status == 403, reply
    assert legacy.registry.status()['worker_count'] == 0
    status, reply, _ = _post(legacy.server_port,'/api/hub/v1/workers/results', {
        'worker_node_id':'untrusted-worker','request_id':'r','lease_id':'l',
        'result':{'status':'success','earnings':9999}})
    assert status == 403, reply
    status, reply, _ = _post(legacy.server_port,'/api/hub/v1/requests',{
        'request_id':'fraud','execution_mode':'worker_pull_v0',
        'model':'fake-model','messages':[{'role':'user','content':'X'}],
        'account_id':'victim','max_credits':10})
    assert status == 403, reply
    assert json.dumps(legacy.credit_ledger.status(),sort_keys=True) == before


def _sign(private_key: str, *, request_id: str, action: str = '', key_id: str = '') -> dict:
    wallet = private_key_to_address(private_key)
    now = datetime.now(timezone.utc)
    msg = {'purpose':'request_multi_session_key', 'wallet_address':wallet,
           'chain_id':HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID,
           'request_id':request_id,'issued_at':now.isoformat(),
           'expires_at':(now+timedelta(minutes=5)).isoformat(),
           'origin':'financial-authority-oracle', 'user_slug':'user_slug_' + 'x'*40}
    if action: msg['action'] = action
    if key_id: msg['key_id'] = key_id
    return build_personal_sign_blob(message=msg, private_key=private_key,
                                    wallet_address=wallet, chain_id=HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID)


def test_legacy_msk_revoke_requires_wallet_signed_key_bound_intent(legacy):
    pkey='0x' + '34'*32
    other='0x' + '35'*32
    status, created, _ = _post(legacy.server_port,'/api/hub/v1/credits/multisession-keys/request',{
        'signed_request':_sign(pkey,request_id='issue-1')})
    assert status == 200, created
    key=created['key']['id']
    wallet=created['key']['wallet_address']
    path='/api/hub/v1/credits/multisession-keys/revoke'
    for forged in (
        {'key_id':key,'wallet_address':wallet},
        {'key_id':key,'signed_request':_sign(pkey, request_id='issue-replay')},
        {'key_id':key,'signed_request':_sign(other,request_id='revoke:'+key,
                    action='revoke_multisession_key',key_id=key)},
        {'key_id':key,'signed_request':_sign(pkey,request_id='revoke:other',
                    action='revoke_multisession_key',key_id=key)},
    ):
        status, response, _=_post(legacy.server_port,path,forged)
        assert status in (400,403), (status,response)
        status, verified, _=_post(legacy.server_port,'/api/hub/v1/credits/multisession-keys/validate',{
            'wallet_address':wallet,'key_id':key})
        assert status==200 and verified.get('reason_code') != 'multisession_key_revoked',verified
    status, done, _=_post(legacy.server_port,path,{
        'key_id':key,'wallet_address':wallet,
        'signed_request':_sign(pkey,request_id='revoke:'+key,
                    action='revoke_multisession_key',key_id=key)})
    assert status==200 and done.get('revoked') is True,done
    status, again, _=_post(legacy.server_port,path,{
        'key_id':key,'wallet_address':wallet,
        'signed_request':_sign(pkey,request_id='revoke:'+key,
                    action='revoke_multisession_key',key_id=key)})
    assert status==200 and again.get('already_inactive') is True


def test_stable_worker_msk_cannot_self_finalize_accounting(stable,monkeypatch):
    from main_computer.hub_credit_indexer import wallet_account_id
    from main_computer.stable_hub import StableHubRequestHandler
    pkey='0x'+'36'*32
    wallet=private_key_to_address(pkey)
    status,issued,_ = _post(stable.server_port,'/api/hub/v1/credits/multisession-keys/request',{
        'signed_request':_sign(pkey,request_id='stable-issuance')})
    assert status==200,issued
    worker=wallet_account_id(wallet)
    path=f'/api/hub/v1/workers/{worker}/payout/settlements'
    called=[]
    def forbidden(*args,**kwargs):
        called.append((args,kwargs));raise AssertionError('Unverified settlement was invoked')
    monkeypatch.setattr(stable.payout_ledger_directory,'settle_worker_settlement_batch',forbidden)
    body={'multisession_authorization':{'kind':'multisession_key',
          'multisession_key_id':issued['key']['id']},
          'idempotency_key':'self-finalization','settle':True,
          'settlement_reference':'invented-chain-payment'}
    status,response,_=_post(stable.server_port,path,body)
    assert status in (400,403),response
    assert 'settlement_finalization_forbidden' in response.get('error','')
    assert not called
    # The legitimate worker operation is only to prepare a pending batch.
    status,pending,_=_post(stable.server_port,path,{k:v for k,v in body.items()
                                                         if k not in {'settle','settlement_reference'}})
    assert status==200,pending
    assert pending.get('settlement',{}).get('status') != 'settled'
    assert not called


@pytest.mark.parametrize('path', sorted(PREVIOUS_DELETIONS))
def test_deleted_financial_urls_stay_404_at_legacy_hub(legacy,path):
    status, _, closed = _post(legacy.server_port,path,b'{"wallet_address":"0x0000000000000000000000000000000000000001","credits":999999}')
    assert status == 404 and closed, (path,status)


@pytest.mark.parametrize('path', sorted(PREVIOUS_DELETIONS))
def test_deleted_financial_urls_stay_404_at_stable_hub(stable,path):
    status, _, closed = _post(stable.server_port,path,b'{"wallet_address":"0x0000000000000000000000000000000000000001","credits":999999}')
    assert status == 404 and closed, (path,status)


def test_live_worker_websocket_message_types_remain_identity_authorized():
    source=(ROOT/'main_computer/stable_hub.py').read_text(encoding='utf-8')
    assert 'if auth_message.get("type") != "worker.auth"' in source
    assert 'self.server.multisession_key_service.validate_key(' in source
    for accepted in ('worker.pong','worker.work.accepted','worker.work.result',
                     'worker.work.failed','worker.close'):
        assert accepted in source
    # An unknown websocket message is refused rather than becoming an
    # implicit financial-mutation command.
    assert 'unsupported_worker_message_type' in source or 'unsupported worker' in source.lower()


@pytest.mark.parametrize('path', [
    '/api/hub/workers/connect', '/api/hub/v1/workers/connect',
    '/api/hub/workers/register', '/api/hub/v1/workers/register',
    '/api/hub/workers/heartbeat', '/api/hub/v1/workers/heartbeat',
    '/api/hub/workers/poll', '/api/hub/v1/workers/poll',
    '/api/hub/workers/results', '/api/hub/v1/workers/results',
    '/api/hub/workers/stream-events', '/api/hub/v1/workers/stream-events',
])
def test_legacy_worker_mutation_routes_refuse_missing_wallet_authorization(legacy,path):
    status,_,_ = _post(legacy.server_port,path,{
        'node_id':'intruder','worker_node_id':'intruder',
        'endpoint':'http://127.0.0.1:9988',
        'model':'fake-model','models':['fake-model'],
        'status':'available','capabilities':{'model':'fake-model'},
        'request_id':'fake-request','lease_id':'fake-lease',
        'result':{'status':'success','credits':999},
        'event':{'type':'worker.work.accepted'},
    })
    assert status in (400,403,404), (path,status)
    assert legacy.registry.status()['worker_count'] == 0
    assert not legacy.credit_ledger.list_charges()


def test_valid_legacy_worker_wallet_msk_still_permits_registration(legacy):
    key='0x' + '37'*32
    status,issued,_=_post(legacy.server_port,'/api/hub/v1/credits/multisession-keys/request',{
        'signed_request':_sign(key,request_id='authorized-worker-test')})
    assert status==200,issued
    authorization={'kind':'multisession_key',
                   'multisession_key_id':issued['key']['id'],
                   'wallet_address':issued['key']['wallet_address'],
                   'chain_id':HUB_MULTISESSION_KEY_EXPECTED_CHAIN_ID}
    status,result,_=_post(legacy.server_port,'/api/hub/v1/workers/register',{
        'node_id':'legitimate-worker', 'endpoint':'http://127.0.0.1:9988',
        'model':'fake-model', 'models':['fake-model'],
        'requested_ring':3,'wallet_address':issued['key']['wallet_address'],
        'multisession_authorization':authorization})
    assert status==200,result
    assert result['ok'] is True
    assert legacy.registry.status()['worker_count'] == 1
