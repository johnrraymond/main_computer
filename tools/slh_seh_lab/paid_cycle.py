"""Real Stable Hub credit/earning acceptance for the disposable SLH/SEH lab.

No live-network funding, public credit bridge or mocked terminal accounting. The
only synthetic funding is explicitly confined to an in-memory Hub or the existing
three-node smoke FoundationDB namespace, which is strictly validated by run_lab.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone
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
    except (OSError, TimeoutError) as exc:
        # The real FDB lab has multiple independent network boundaries.
        # A generic "read timed out" gives no evidence of which one failed.
        # Report only the endpoint path (not payloads, wallet keys or tokens).
        raise RuntimeError(f'Stable Hub {request.get_method()} '
                           f'{urlsplit(url).path}: {type(exc).__name__}: {exc}') from exc


def _eventually(fn, seconds=12):
    deadline=time.monotonic()+seconds
    while time.monotonic() < deadline:
        result=fn()
        if result is not None:return result
        time.sleep(.05)
    raise RuntimeError('Timed out waiting for paid Stable Hub terminal ledger transition')


class PaidCycleDiagnostics:
    """Best-effort, append-only stage timeline for real-Hub laboratory failures."""

    def __init__(self, trace_file: Path | None, hub_url: str):
        self.trace_file = trace_file
        self.hub_path = urlsplit(hub_url).path or '/'
        self.request_id = ''

    @contextmanager
    def stage(self, name: str):
        started = time.monotonic()
        self._emit(name, 'begin', 0)
        try:
            yield
        except BaseException as exc:
            self._emit(name, 'error', round((time.monotonic() - started) * 1000, 1),
                       error_type=type(exc).__name__)
            raise
        else:
            self._emit(name, 'ok', round((time.monotonic() - started) * 1000, 1))

    def _emit(self, stage: str, status: str, duration_ms: float, **extra):
        if self.trace_file is None:
            return
        row = {'schema': 'main-computer.slh-paid-cycle-diagnostic.v1',
               'timestamp': datetime.now(timezone.utc).isoformat(),
               'request_id': self.request_id, 'stage': stage, 'status': status,
               'duration_ms': duration_ms, **extra}
        line = json.dumps(row, sort_keys=True) + '\n'
        # Diagnostic failure must never affect paid work or its accounting.
        try:
            with self.trace_file.open('a', encoding='utf-8') as stream:
                stream.write(line)
        except OSError:
            pass
        sys.stderr.write('slh-paid-cycle-diagnostic ' + line)
        sys.stderr.flush()


def verify_paid_cycle(*, hub_url: str, proxy, gateway, ledger: StableHubPayoutLedgerDirectory,
                      worker_account_id: str, lab_mode: str,
                      trace_file: Path | None = None,
                      market_isolation_capability: str | None = None) -> dict:
    """Complete a real paid request via an SLH upstream worker and settle its earning.

    Caller must already have verified all Hub identities/topology and started the
    managed gateway, worker sockets, and SLH public marketplace sessions.
    """
    trace = PaidCycleDiagnostics(trace_file, hub_url)
    if market_isolation_capability is not None:
        from main_computer.stable_hub_worker_sessions import _normalize_market_token
        _normalize_market_token(market_isolation_capability, field_name='lab market isolation capability')
    parsed=urlsplit(hub_url)
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1':
        raise ValueError('Paid lab refuses non-loopback or HTTPS marketplace Hub')
    if lab_mode not in {'in-memory-stable-hub','three-hub-smoke-fdb'}:
        raise ValueError('Paid lab requires disposable in-memory or validated FDB smoke mode')
    with trace.stage('hub_identity'):
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
    initial_sessions = list(proxy.sessions.values())
    worker_before = ledger.get_account(worker_account_id)
    worker_available_before = int((worker_before or {}).get('available_credit_wei') or 0)
    actual_accounts={slot.accepted.get('account_id') for slot in initial_sessions}
    if actual_accounts != {worker_account_id}:
        raise RuntimeError('Public sessions are not bound to expected SLH wallet account')

    from types import SimpleNamespace
    requester_private='0x'+secrets.token_hex(32)
    requester_wallet=private_key_to_address(requester_private)
    requester_account=wallet_account_id(requester_wallet)
    if requester_account==worker_account_id:
        raise RuntimeError('Requester and worker must use distinct wallets')
    with trace.stage('requester_msk'):
        requester_auth=issue_cli_multisession_authorization(hub_url,
            wallet=SimpleNamespace(private_key=requester_private,address=requester_wallet),
            stable_identity=identity)
    if not requester_auth.get('multisession_key_id'):
        raise RuntimeError('Requester failed to obtain real Hub MSK')
    # Dedicated disposable account, seeded only in the checked lab namespace.
    with trace.stage('fund_disposable_requester'):
        ledger.fund_account(account_id=requester_account,wallet_address=requester_wallet,
                            credits='2',replace=True)
    before_request=ledger.get_account(requester_account)
    request_id='slh-paid-'+secrets.token_hex(12)
    trace.request_id = request_id
    marker='slh-actual-authorized-'+secrets.token_hex(10)
    work={'ring':'ring-2','max_price':{'amount':'2','unit':'compute_credit'},
          'capabilities':['chat.completions'] + ([market_isolation_capability] if market_isolation_capability else []), 'model':'mock-model',
          'input':{'kind':'chat','value':marker}}
    payload={'request_id':request_id,
             'multisession_authorization':{'kind':'multisession_key',
                   'multisession_key_id':requester_auth['multisession_key_id']},
             'work':work}
    with trace.stage('paid_work_post'):
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
    with trace.stage('paid_work_terminal'):
        terminal=_eventually(_terminal, seconds=18)
    payout=dict(terminal.get('payout') or {})
    store_state=ledger.store.load()
    charges=[x for x in store_state.get('payout_charges',{}).values()
             if x.get('session_id')==session_id and x.get('request_id')==request_id]
    earnings=[x for x in store_state.get('worker_earnings',{}).values()
              if x.get('session_id')==session_id and x.get('request_id')==request_id]
    after_request=ledger.get_account(requester_account)
    market_matches = [record for record in store_state.get('market_workers', {}).values()
                      if isinstance(record, dict) and str(record.get('connection_id') or '') == result_worker]
    isolation_verified = (market_isolation_capability is None or
        len(market_matches) == 1 and
        market_isolation_capability in (market_matches[0].get('capabilities') or []))
    charge=charges[0] if len(charges)==1 else {}
    earning=earnings[0] if len(earnings)==1 else {}
    amount=int(charge.get('charged_credit_wei') or 0)
    earning_amount=int(earning.get('earned_credit_wei') or 0)
    # Replaying the same signed request must reuse its accepted session and
    # never create a second paid charge or dispatch another worker job.
    with trace.stage('paid_work_replay'):
        replay=_json(hub_url.rstrip('/')+'/api/hub/v1/work/requests',payload,timeout=10)
    replay_state=ledger.store.load()
    replay_charges=[c for c in replay_state.get('payout_charges',{}).values()
                    if c.get('session_id')==session_id and c.get('request_id')==request_id]
    replay_earnings=[e for e in replay_state.get('worker_earnings',{}).values()
                     if e.get('session_id')==session_id and e.get('request_id')==request_id]
    # Real payouts are recorded by Stable Hub. No direct ledger spend call here.
    gates={
       'requester_msk_real': bool(requester_auth.get('multisession_key_id')),
       'market_isolation_capability_verified': isolation_verified,
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
        worker_msk=initial_sessions[0].auth_message['multisession_authorization']['multisession_key_id']
        auth={'kind':'multisession_key','multisession_key_id':worker_msk}
        with trace.stage('worker_earning_claim'):
            claim=_json(f'{hub_url.rstrip("/")}/api/hub/v1/workers/{quote(worker_account_id)}/payout/claim',
                      {'multisession_authorization':auth,'earning_ids':[earning['earning_id']]},timeout=10)
        with trace.stage('worker_earning_settle'):
            settlement=_json(f'{hub_url.rstrip("/")}/api/hub/v1/workers/{quote(worker_account_id)}/payout/settlements',
                           {'multisession_authorization':auth,
                            'claim_ids':[claim['claim']['claim_id']],
                            'settle':True,'settlement_reference':'smoke-'+request_id},timeout=10)
    gates['worker_result_matches_private_marker']=(earning.get('result') or {}).get('response',{}).get('content')==marker
    gates['wallet_authorized_claim']=claim.get('ok') is True and earning.get('earning_id') in (claim.get('claim') or {}).get('earning_ids',[])
    settlement_record=settlement.get('settlement') or {}
    gates['wallet_authorized_settlement']=settlement.get('ok') is True and settlement_record.get('status')=='settled'
    worker_after = ledger.get_account(worker_account_id)
    buyer_spent_before = int((before_request or {}).get('spent_credit_wei') or 0)
    buyer_spent_after = int((after_request or {}).get('spent_credit_wei') or 0)
    worker_available_after = int((worker_after or {}).get('available_credit_wei') or 0)
    # Evidence is read from the authoritative Hub ledger, not inferred from
    # a successful HTTP response or the worker's emulated answer.
    accounting = {
        'buyer_account_id': requester_account,
        'buyer_available_before_wei': str(int((before_request or {}).get('available_credit_wei') or 0)),
        'buyer_available_after_wei': str(int((after_request or {}).get('available_credit_wei') or 0)),
        'buyer_spent_before_wei': str(buyer_spent_before),
        'buyer_spent_after_wei': str(buyer_spent_after),
        'buyer_held_after_wei': str(int((after_request or {}).get('held_credit_wei') or 0)),
        'buyer_spent_delta_wei': str(buyer_spent_after - buyer_spent_before),
        'worker_account_id': worker_account_id,
        'worker_available_before_wei': str(worker_available_before),
        'worker_available_after_settlement_wei': str(worker_available_after),
        'worker_earned_after_settlement_wei': str(int((worker_after or {}).get('earned_credit_wei') or 0)),
        'charge_id': str(charge.get('charge_id') or ''),
        'earning_id': str(earning.get('earning_id') or ''),
        'request_id': request_id, 'session_id': session_id,
    }
    gates['buyer_debit_equals_charge'] = buyer_spent_after - buyer_spent_before == amount
    gates['worker_wallet_credit_equals_earning'] = worker_available_after - worker_available_before == earning_amount
    gates['buyer_and_worker_distinct'] = requester_account != worker_account_id
    return {'gates':gates,'pass':all(gates.values()),'request_id':request_id,
            'market_isolation_capability': market_isolation_capability,
            'accounting': accounting,
            'session_id':session_id,'worker_connection_id':result_worker,
            'worker_account_id':worker_account_id,
            'charge_count':len(charges),'earning_count':len(earnings),
            'charged_credit_wei':str(amount), 'earned_credit_wei':str(earning_amount),
            'terminal_status':terminal.get('status'),
            'funding':'SYNTHETIC_LAB_ONLY', 'settlement':'LOCAL_LEDGER_SETTLED_NOT_BRIDGED'}
