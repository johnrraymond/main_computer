"""Real Stable Hub credit/earning acceptance for the disposable SLH/SEH lab.

No live-network funding, public credit bridge or mocked terminal accounting. The
only synthetic funding is explicitly confined to an in-memory Hub or the existing
three-node smoke FoundationDB namespace, which is strictly validated by run_lab.
"""
from __future__ import annotations

import json
import secrets
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from main_computer.hub_cli_live_session import identify_hub_protocol, issue_cli_multisession_authorization
from main_computer.hub_credit_indexer import wallet_account_id
from main_computer.multisession_key_signing import private_key_to_address
from main_computer.stable_hub_worker_sessions import StableHubPayoutLedgerDirectory


def _json(url: str, body: dict | None = None, timeout: float = 20) -> dict:
    if body is None:
        request = Request(url, method='GET')
    else:
        request = Request(url, method='POST', data=json.dumps(body).encode('utf-8'),
                          headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:450]
        raise RuntimeError(f'Stable Hub HTTP {exc.code}: {detail}') from exc


def _eventually(fn, seconds=12):
    deadline=time.monotonic()+seconds
    while time.monotonic() < deadline:
        result=fn()
        if result is not None:return result
        time.sleep(.05)
    raise RuntimeError('Timed out waiting for paid Stable Hub terminal ledger transition')


def verify_paid_cycle(*, hub_url: str, proxy, gateway, ledger: StableHubPayoutLedgerDirectory,
                      worker_account_id: str, lab_mode: str) -> dict:
    """Complete a real paid request via an SLH upstream worker and settle its earning.

    Caller must already have verified all Hub identities/topology and started the
    managed gateway, worker sockets, and SLH public marketplace sessions.
    """
    parsed=urlsplit(hub_url)
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1':
        raise ValueError('Paid lab refuses non-loopback or HTTPS marketplace Hub')
    if lab_mode not in {'in-memory-stable-hub','three-hub-smoke-fdb'}:
        raise ValueError('Paid lab requires disposable in-memory or validated FDB smoke mode')
    identity=identify_hub_protocol(hub_url)
    if identity.get('service')!='main_computer.stable_hub':
        raise ValueError('Paid lab requires a verified Stable Hub')
    if lab_mode=='three-hub-smoke-fdb' and ('smoke' not in str(ledger.cluster_id).lower()
            or str(ledger.topology.storage.get('namespace'))!='main-computer-hub-smoke'):
        raise ValueError('Paid lab refuses funding outside the smoke FDB namespace')
    if lab_mode=='in-memory-stable-hub' and ledger.store.__class__.__name__ != 'InMemoryStableWorkerSessionStore':
        raise ValueError('In-memory lab must use in-memory payout ledger')
    if not proxy.sessions:
        raise RuntimeError('SLH has no public sessions available for paid work')
    actual_accounts={slot.accepted.get('account_id') for slot in proxy.sessions.values()}
    if actual_accounts != {worker_account_id}:
        raise RuntimeError('Public sessions are not bound to expected SLH wallet account')

    from types import SimpleNamespace
    requester_private='0x'+secrets.token_hex(32)
    requester_wallet=private_key_to_address(requester_private)
    requester_account=wallet_account_id(requester_wallet)
    if requester_account==worker_account_id:
        raise RuntimeError('Requester and worker must use distinct wallets')
    requester_auth=issue_cli_multisession_authorization(hub_url,
        wallet=SimpleNamespace(private_key=requester_private,address=requester_wallet),
        stable_identity=identity)
    if not requester_auth.get('multisession_key_id'):
        raise RuntimeError('Requester failed to obtain real Hub MSK')
    # Dedicated disposable account, seeded only in the checked lab namespace.
    ledger.fund_account(account_id=requester_account,wallet_address=requester_wallet,
                        credits='2',replace=True)
    before_request=ledger.get_account(requester_account)
    request_id='slh-paid-'+secrets.token_hex(12)
    marker='slh-actual-authorized-'+secrets.token_hex(10)
    work={'ring':'ring-2','max_price':{'amount':'2','unit':'compute_credit'},
          'capabilities':['chat.completions'], 'model':'mock-model',
          'input':{'kind':'echo','value':marker}}
    payload={'request_id':request_id,
             'multisession_authorization':{'kind':'multisession_key',
                   'multisession_key_id':requester_auth['multisession_key_id']},
             'work':work}
    accepted=_json(hub_url.rstrip('/')+'/api/hub/v1/work/requests',payload,timeout=25)
    if accepted.get('ok') is not True or accepted.get('accepted') is not True:
        raise RuntimeError('Stable Hub refused paid request: '+str(accepted)[:700])
    session_id=str(accepted.get('session_id') or '')
    if not session_id:raise RuntimeError('No accepted paid session id')
    result_worker=str(accepted.get('worker_id') or '')
    connected_slots={str(slot.accepted.get('connection_id')) for slot in proxy.sessions.values()}
    def _terminal():
        state=ledger.store.load()
        record=state.get('accepted_sessions',{}).get(session_id)
        if not isinstance(record,dict):return None
        if record.get('status') in {'succeeded','failed','cancelled'}:return record
        return None
    terminal=_eventually(_terminal, seconds=18)
    payout=dict(terminal.get('payout') or {})
    store_state=ledger.store.load()
    charges=[x for x in store_state.get('payout_charges',{}).values()
             if x.get('session_id')==session_id and x.get('request_id')==request_id]
    earnings=[x for x in store_state.get('worker_earnings',{}).values()
              if x.get('session_id')==session_id and x.get('request_id')==request_id]
    after_request=ledger.get_account(requester_account)
    charge=charges[0] if len(charges)==1 else {}
    earning=earnings[0] if len(earnings)==1 else {}
    amount=int(charge.get('charged_credit_wei') or 0)
    earning_amount=int(earning.get('earned_credit_wei') or 0)
    # Replaying the same signed request must reuse its accepted session and
    # never create a second paid charge or dispatch another worker job.
    replay=_json(hub_url.rstrip('/')+'/api/hub/v1/work/requests',payload,timeout=10)
    replay_state=ledger.store.load()
    replay_charges=[c for c in replay_state.get('payout_charges',{}).values()
                    if c.get('session_id')==session_id and c.get('request_id')==request_id]
    replay_earnings=[e for e in replay_state.get('worker_earnings',{}).values()
                     if e.get('session_id')==session_id and e.get('request_id')==request_id]
    # Real payouts are recorded by Stable Hub. No direct ledger spend call here.
    gates={
       'requester_msk_real': bool(requester_auth.get('multisession_key_id')),
       'request_accepted_by_stable_hub':accepted.get('accepted') is True,
       'idempotent_request_not_double_charged':(replay.get('session_id')==session_id
             and len(replay_charges)==1 and len(replay_earnings)==1),
       'slh_public_connection_selected':result_worker in connected_slots,
       'stable_hub_terminal_succeeded':terminal.get('status')=='succeeded',
       'stable_hub_charge_exactly_once':len(charges)==1 and bool(charge.get('charge_id')) and amount>0,
       'worker_earning_exactly_once':len(earnings)==1 and bool(earning.get('earning_id')) and earning_amount==amount,
       'worker_wallet_owns_earning':earning.get('worker_id')==worker_account_id,
       'requester_credit_decreased':int((after_request or {}).get('spent_credit_wei') or 0)>int((before_request or {}).get('spent_credit_wei') or 0),
       'worker_earning_recorded':earning_amount>0 and len(earnings)==1,
       'no_unsettled_hold':str(payout.get('hold_status'))=='charged',
    }
    claim={}
    settlement={}
    if all(gates.values()):
        # Confirm the economic authority belongs to the wallet, not the worker slot.
        worker_msk=next(iter(proxy.sessions.values())).auth_message['multisession_authorization']['multisession_key_id']
        auth={'kind':'multisession_key','multisession_key_id':worker_msk}
        claim=_json(f'{hub_url.rstrip("/")}/api/hub/v1/workers/{quote(worker_account_id)}/payout/claim',
                  {'multisession_authorization':auth,'earning_ids':[earning['earning_id']]},timeout=10)
        settlement=_json(f'{hub_url.rstrip("/")}/api/hub/v1/workers/{quote(worker_account_id)}/payout/settlements',
                       {'multisession_authorization':auth,
                        'claim_ids':[claim['claim']['claim_id']],
                        'settle':True,'settlement_reference':'smoke-'+request_id},timeout=10)
    gates['worker_result_matches_private_marker']=(earning.get('result') or {}).get('response',{}).get('content')==marker
    gates['wallet_authorized_claim']=claim.get('ok') is True and earning.get('earning_id') in (claim.get('claim') or {}).get('earning_ids',[])
    settlement_record=settlement.get('settlement') or {}
    gates['wallet_authorized_settlement']=settlement.get('ok') is True and settlement_record.get('status')=='settled'
    return {'gates':gates,'pass':all(gates.values()),'request_id':request_id,
            'session_id':session_id,'worker_connection_id':result_worker,
            'worker_account_id':worker_account_id,
            'charge_count':len(charges),'earning_count':len(earnings),
            'charged_credit_wei':str(amount), 'earned_credit_wei':str(earning_amount),
            'terminal_status':terminal.get('status'),
            'funding':'SYNTHETIC_LAB_ONLY', 'settlement':'LOCAL_LEDGER_SETTLED_NOT_BRIDGED'}
