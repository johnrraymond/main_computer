"""Permanent negative identity-boundary oracle for Stable Hub and SLH.

Real HTTP handlers; disposable loopback in-memory Hub; no production or real funds.
"""
from __future__ import annotations

import json
import secrets
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from main_computer.stable_hub import create_stable_hub_server, _public_work_session, _public_payout
from main_computer.stable_hub_msk import InMemoryStableMultiSessionKeyStore
from main_computer.stable_hub_topology import load_stable_hub_topology
from main_computer.stable_hub_worker_sessions import InMemoryStableWorkerSessionStore
from main_computer.slh.private_requests import PrivateRequests


class DummyBridge:
    def status(self):
        return {"ok": True}


@pytest.fixture
def live_hub():
    topology = load_stable_hub_topology('deploy/hub-topology/dev-topology.json')
    store = InMemoryStableWorkerSessionStore()
    server = create_stable_hub_server(
        topology=topology, hub_id='dev-hub1', bind_host='127.0.0.1', bind_port=0,
        multisession_key_store=InMemoryStableMultiSessionKeyStore(),
        worker_session_store=store, bridge_completion_service=DummyBridge(),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _request(url, *, method='GET', payload=None, token=''):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=4) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _session(server, base, *, token=None):
    token = token if token is not None else secrets.token_urlsafe(32)
    server.accepted_work_session_directory.record_accepted(
        session_id='sess_identity_oracle', run_id='run_identity', request_id='request_identity',
        requester_msk_id='msk_very_private', requester_account_id='0x'+'1'*40,
        requester_wallet_address='0x'+'1'*40,
        worker_id='conn_PRIVATE_DEVICE_SENTINEL',worker_connection_id='conn_PRIVATE_DEVICE_SENTINEL',
        owner_hub_id='dev-hub1', owner_hub_url=base,
        partition='ring-3', task_queue='private_task_queue',
        work={'model':'nanojev-clef','input':{'private_prompt':'PRIVATE_PROMPT_SENTINEL'}},
        worker_acceptance={'device_id':'PRIVATE_SLH_DEVICE_SENTINEL'},
        continuation_token=token,
        payout={'worker_account_id':'0x'+'2'*40, 'worker_earning_id':'earn_PRIVATE',
                'hold_id':'hold_PRIVATE','charge_id':'chg_public','charged_credits':'1',
                'payment_status':'charged','settlement_status':'earned'},
    )
    return token


def _assert_no_identity(response):
    raw=json.dumps(response,sort_keys=True)
    for sentinel in ('PRIVATE_DEVICE_SENTINEL', 'PRIVATE_SLH_DEVICE_SENTINEL',
                     'msk_very_private','PRIVATE_PROMPT_SENTINEL','earn_PRIVATE',
                     'hold_PRIVATE','private_task_queue','0x'+'1'*40,'0x'+'2'*40):
        assert sentinel not in raw, sentinel
    # Match serialized JSON object keys, not substrings of words like 'worker_result'.
    for key in ('worker_id','worker_connection_id','worker_device_id','worker_market',
                'requester_wallet_address','requester_msk_id','owner','work','worker_acceptance'):
        assert json.dumps(key) + ':' not in raw


def test_anonymous_wrong_and_cross_session_capability_refused(live_hub):
    server, base=live_hub
    token=_session(server,base)
    uri=base+'/api/hub/v1/work/sessions/sess_identity_oracle/stream'
    for supplied in ('', 'wrong_token_from_another_requester', secrets.token_urlsafe(32)):
        status, reply=_request(uri,token=supplied)
        assert status == 403, reply
        assert reply['error']=='session_continuation_forbidden'
        _assert_no_identity(reply)
    status, reply=_request(uri,token=token)
    assert status == 200,reply
    assert reply['accepted_session']['payout']['charged_credits']=='1'
    assert reply['accepted_session']['payout']['charge_id']=='chg_public'
    _assert_no_identity(reply)
    assert 'continuation_token' not in json.dumps(reply)


def test_financial_raw_reads_and_bridge_mutation_refused(live_hub):
    server, base=live_hub
    _session(server,base)
    # Anonymous/unauthorized reads must not become a Worker/account oracle.
    for path in ('/api/hub/v1/credits/charges', '/api/hub/v1/payout/status',
                 '/api/hub/v1/credits/balance?wallet_address=0x'+'1'*40):
        status, reply=_request(base+path)
        assert status == 403,(path,reply)
        _assert_no_identity(reply)
    # Mutators are disabled until an independently authenticated bridge exists.
    for suffix in ('confirm','fail'):
        status, reply=_request(base+'/api/hub/v1/payout/bridge/bridge_private/'+suffix,
                              method='POST',payload={'settlement_reference':'fake','reason':'fake'})
        assert status==403 and reply['error']=='bridge_controller_authorization_required',reply
    assert not server.payout_ledger_directory.store.load().get('payout_bridge_payouts')


def test_internal_session_projection_allowlists_result_and_receipt():
    row={'session_id':'sess_private','request_id':'req_private','run_id':'run_private',
         'status':'succeeded','worker_id':'PRIVATE_DEVICE_SENTINEL',
         'requester_msk_id':'msk_very_private','worker_acceptance':{'device_id':'PRIVATE_SLH_DEVICE_SENTINEL'},
         'work':{'input':{'private_prompt':'PRIVATE_PROMPT_SENTINEL'}},
         'worker_result':{'type':'worker.work.result','worker_id':'PRIVATE_DEVICE_SENTINEL',
             'result':{'status':'success','solution':{'schema':'main-computer.nanojev-solution.v1','result':{'states':[]}},
                       'response':{'content':'completed'}}},
         'payout':{'worker_account_id':'0x'+'2'*40, 'worker_earning_id':'earn_PRIVATE',
                   'hold_id':'hold_PRIVATE','charge_id':'chg_public','charged_credits':'1',
                   'settlement_status':'earned'}}
    reply=_public_work_session(row)
    assert reply['worker_result']['result']['solution']['result']['states']==[]
    assert reply['payout']['charged_credits']=='1'
    _assert_no_identity(reply)
    assert 'worker_account_id' not in _public_payout(row['payout'])


def test_legacy_session_token_upgrade_does_not_duplicate_session_or_charges(live_hub):
    server,base=live_hub
    _session(server,base,token='')
    directory=server.accepted_work_session_directory
    first=directory.ensure_continuation_token('sess_identity_oracle')
    second=directory.ensure_continuation_token('sess_identity_oracle')
    assert first==second and len(first)>35
    status,reply=_request(base+'/api/hub/v1/work/sessions/sess_identity_oracle/stream',token=first)
    assert status==200,reply
    assert len(server.payout_ledger_directory.store.load().get('accepted_sessions',{}))==1
    _assert_no_identity(reply)


def test_slh_requester_projection_excludes_worker_device_identity():
    row={k:None for k in ('request_id','model','status','worker_device_id','run_id',
        'attempt','admitted_at_ns','updated_at_ns','fulfilled_at_ns','result_hash',
        'failure_code','billing_policy_id','pinky_policy_revision')}
    row.update(request_id='req_test',model='nanojev-clef',status='SUCCEEDED',
               worker_device_id='PRIVATE_SLH_DEVICE_SENTINEL')
    public=PrivateRequests._public(row)
    assert public['status']=='SUCCEEDED'
    _assert_no_identity(public)
    assert 'worker_device_id' not in public


def test_client_refuses_missing_continuation_capability():
    from main_computer.nanojev_hub_request import retrieve_solution
    with pytest.raises(RuntimeError,match='continuation token'):
        retrieve_solution({'session_id':'sess_private','continuation_url':
            'https://hub.example/api/hub/v1/work/sessions/sess_private/stream'},
            hub_url='https://hub.example',timeout_s=1)


def test_balance_requires_wallet_bound_valid_msk(live_hub):
    from datetime import datetime, timedelta, timezone
    from main_computer.multisession_key_signing import build_personal_sign_blob, private_key_to_address

    _server, base=live_hub
    key='0x' + '63'*32
    address=private_key_to_address(key)
    now=datetime.now(timezone.utc)
    document={'purpose':'request_multi_session_key','wallet_address':address,
              'chain_id':'42424242', 'request_id':'boundary-balance-test',
              'issued_at':now.isoformat(),
              'expires_at':(now+timedelta(minutes=10)).isoformat(),
              'origin':'identity-boundary-oracle','user_slug':'user_slug_'+'b'*40}
    blob=build_personal_sign_blob(message=document,private_key=key,
                                  wallet_address=address,chain_id='42424242')
    status,issued=_request(base+'/api/hub/v1/credits/multisession-keys/request',
                           method='POST',payload={'signed_request':blob})
    assert status==200,issued
    key_id=issued['multisession_authorization']['key_id']
    from urllib.parse import quote
    own=base+'/api/hub/v1/credits/balance?wallet_address='+quote(address,safe='')
    for token in ('', 'not-an-active-key'):
        status,_=_request(own,token=token)
        assert status==403
    other=base+'/api/hub/v1/credits/balance?wallet_address=0x'+'9'*40
    status,_=_request(other,token=key_id)
    assert status==403
    status,reply=_request(own,token=key_id)
    assert status==200,reply
    assert reply['wallet_address'].lower()==address.lower()


def test_native_requester_passes_token_header_and_returns_sanitized_paid_receipt(monkeypatch):
    from main_computer import nanojev_hub_request as client
    calls=[]
    solution={'schema':'main-computer.nanojev-solution.v1',
              'provider':'nanojev','capability':'nanojev.clef.evaluate.v1',
              'model':{'family':'nanojev-clef','release_manifest_sha256':'a'*64},
              'result':{'states':[{'id':'cli','answers':{'route':{'choice':'safe'}}}]}}
    def fake_json(url, *, timeout_s, continuation_token):
        calls.append((url,continuation_token))
        return {'ok':True,'status':'succeeded','accepted_session':{
            'session_id':'sess_identity','status':'succeeded',
            'worker_result':{'result':{'status':'success',
                'response':{'model':'nanojev-clef'},'solution':solution}},
            'payout':{'charge_id':'chg_public','charged_credits':'1',
                      'payment_status':'charged','settlement_status':'earned'}}}
    monkeypatch.setattr(client,'_hub_json',fake_json)
    accepted={'session_id':'sess_identity','continuation_token':'scoped-opaque-capability',
              'continuation_url':'https://hub.example/api/hub/v1/work/sessions/sess_identity/stream'}
    done=client.retrieve_solution(accepted,hub_url='https://hub.example',timeout_s=1,
                                  expected_model='nanojev-clef',
                                  expected_operation='nanojev.clef.evaluate.v1')
    assert done['status']=='succeeded'
    assert done['payout']['charged_credits']=='1'
    assert done['solution']['result']['states'][0]['answers']['route']['choice']=='safe'
    assert calls==[(accepted['continuation_url'],'scoped-opaque-capability')]
    _assert_no_identity(done)

@pytest.mark.parametrize('path', [
    '/api/hub/v1/admin/bootstrap',
    '/api/hub/v1/metrics',
    '/api/hub/v1/ring-control/feedback-summary',
    '/api/hub/v1/requests',
    '/api/hub/v1/requests/req-unguessable/result',
    '/api/hub/v1/requests/req-unguessable/stream',
    '/api/hub/v1/requests/req-unguessable/charges',
    '/api/hub/v1/credits',
    '/api/hub/v1/credits/accounts',
    '/api/hub/v1/credits/balance?wallet_address=0x'+'1'*40,
    '/api/hub/v1/credits/wallets/0x'+'1'*40+'/balance',
    '/api/hub/v1/credits/worker-earnings',
    '/api/hub/v1/credits/transactions',
    '/api/hub/v1/bridge/audit',
    '/api/hub/v1/payouts',
])
def test_legacy_hub_raw_reads_refuse_anonymous_requesters(path):
    from types import SimpleNamespace
    from main_computer.hub import HubServerHandler
    handler=object.__new__(HubServerHandler)
    handler.path=path
    handler.server=SimpleNamespace()
    responses=[]
    handler._send_json=lambda payload, status=200: responses.append((int(status), payload))
    handler.do_GET()
    assert responses==[(403,{'ok':False,'error':'authenticated_legacy_read_required'})]


@pytest.mark.parametrize('path', ['/api/hub/v1/status', '/api/hub/status'])
def test_legacy_hub_public_status_is_aggregate_only(path):
    from types import SimpleNamespace
    from main_computer.hub import HubServerHandler
    handler=object.__new__(HubServerHandler)
    handler.path=path
    handler.server=SimpleNamespace(
        config=SimpleNamespace(hub_network='testnet',chain_id=42424241),
        registry=SimpleNamespace(status=lambda: {
            'worker_count':1,'available_worker_count':1,'stale_worker_count':0,
            'workers':[{'node_id':'PRIVATE_DEVICE_SENTINEL',
                        'wallet_address':'0x'+'2'*40,
                        'endpoint':'https://private.invalid'}],
            'upstream_hubs':[{'internal_endpoint':'PRIVATE_UPSTREAM_SENTINEL'}]}))
    responses=[]
    handler._send_json=lambda payload, status=200: responses.append((int(status), payload))
    handler.do_GET()
    assert responses[0][0]==200
    assert responses[0][1]['worker_count']==1
    assert 'workers' not in responses[0][1]
    assert 'upstream_hubs' not in responses[0][1]
    _assert_no_identity(responses[0][1])
